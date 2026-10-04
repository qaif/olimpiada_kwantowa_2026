"""Dostosowanie wersji motywu w konkursie (THEME-02 § 2): schemat, kolory, logo, kroje.

Wszystko jest **danymi**, które przechodzą ten sam generator, co ``tokens.css`` przy wgraniu:
nadpisane kolory scalamy z paletą z ``tokens.json`` wersji, a pochodne (skale, „soft”, kolory „na …”,
składowe RGB) liczy jeszcze raz ``apps.themes.tokens.build_tokens_css``. Arkusz wynikowy idzie
z własnej domeny (``/_theme/custom.css?s=<podpis>``, CSP ``'self'``) – bez stylu inline.

Dlaczego podpis, a nie identyfikator w adresie: ten sam arkusz obsługuje stronę aktywnego motywu
(gość, pełnostronicowy cache) i podgląd koordynatora (opcje jeszcze niezapisane). Podpisany zestaw
opcji (``django.core.signing.Signer`` – deterministyczny, bez znacznika czasu) daje adres, który
nie zmienia się, dopóki nie zmienią się opcje, więc arkusz może mieć ``Cache-Control: immutable``,
a nikt spoza serwera nie wygeneruje arkusza z dowolnymi kolorami. Widok i tak oczyszcza opcje
jeszcze raz (obrona w głąb) – podpis chroni przed nadużyciem adresu, nie zastępuje walidacji.
"""

from __future__ import annotations

import json
import re

from django.core import signing
from django.utils.translation import gettext as _

from . import tokens as tk

SIGN_SALT = "apps.themes.custom"
#: Wersja generatora arkusza ``custom.css``. Arkusz ma ``Cache-Control: immutable``, więc zmiana
#: tego, **jak** liczymy tokeny z tych samych opcji (nowe pochodne, poprawka reguły), musi zmienić
#: adres – podnieś tę liczbę razem ze zmianą generatora (przegląd THEME-02, L2).
GENERATOR_VERSION = 2
SCHEMES = ("light", "dark", "auto")
HEX6 = re.compile(r"^#[0-9a-f]{6}$")
OPTION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
#: Promień zaokrąglenia (THEME-02, np. ``radius-leaf`` IQO): liczba w ``px``/``rem`` z ograniczonym
#: zakresem – wartość trafia do ``--t-radius-*`` w arkuszu, więc kształt jest zamknięty.
RADIUS_VALUE = re.compile(r"^(\d{1,2}(?:\.\d{1,3})?)(px|rem)$")
RADIUS_LIMITS = {"px": 48.0, "rem": 3.0}

#: Próg dla elementów nietekstowych (obwódka fokusu na tle) – WCAG 2.1 SC 1.4.11.
MIN_NON_TEXT = 3.0
FOCUS_TOKENS = ("focus", "ring")


class CustomizationError(ValueError):
    """Opcje odrzucone – lista komunikatów dla koordynatora w ``messages``."""

    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


# --- palety ---------------------------------------------------------------------------------------


def has_both_palettes(runtime) -> bool:
    data = runtime.tokens or {}
    return bool(data.get("light")) and bool(data.get("dark"))


def schemes_for(runtime) -> tuple[str, ...]:
    """Schematy do wyboru: wszystkie trzy, gdy motyw ma obie palety; inaczej tylko ten z manifestu."""
    return SCHEMES if has_both_palettes(runtime) else (runtime.color_scheme,)


def palette_modes(runtime, scheme: str) -> tuple[str, ...]:
    """Palety ``tokens.json`` (``light`` = grupa ``colors``, ``dark``) używane przez schemat.

    Odwzorowuje dokładnie ``tokens.build_tokens_css``: schemat ``dark`` bez palety ``dark`` bierze
    ``colors``, ``auto`` bez ``dark`` jest zawsze jasny.
    """
    data = runtime.tokens or {}
    has_dark = bool(data.get("dark"))
    if scheme == "dark":
        return ("dark",) if has_dark else ("light",)
    if scheme == "auto" and has_dark:
        return ("light", "dark")
    return ("light",)


def editable_colors(runtime, mode: str) -> dict[str, str]:
    """Kolory palety ``mode`` z ``tokens.json`` – klucz → wartość domyślna (w kolejności pliku).

    Nazwy kanoniczne dopisane przez synonim (``accent-contrast`` → ``on-accent``) nie są osobnymi
    polami: zmiana synonimu zmienia obie (patrz :func:`merged_palette`).
    """
    palette = dict((runtime.tokens or {}).get(mode) or {})
    for alias, canonical in tk.ALIASES.items():
        if alias in palette and canonical in palette and palette[alias] == palette[canonical]:
            palette.pop(canonical)
    return {key: value for key, value in palette.items() if tk.is_color(value)}


def default_hex(value: str) -> str:
    """Wartość tokenu jako ``#rrggbb`` (pole ``<input type="color">`` nie zna innej postaci)."""
    rgb = tk.parse_color(value)
    return tk.to_hex(rgb) if rgb is not None else "#000000"


def clean_colors(runtime, raw) -> dict[str, dict[str, str]]:
    """Nadpisania kolorów: wyłącznie klucze palet wersji i wartości ``#rrggbb`` różne od domyślnych."""
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for mode in ("light", "dark"):
        given = raw.get(mode)
        if not isinstance(given, dict):
            continue
        allowed = editable_colors(runtime, mode)
        chosen = {}
        for key in sorted(given):
            value = given[key]
            if key not in allowed or not isinstance(value, str):
                continue
            value = value.strip().lower()
            if not HEX6.match(value) or value == default_hex(allowed[key]).lower():
                continue
            chosen[key] = value
        if chosen:
            out[mode] = chosen
    return out


def editable_radii(runtime) -> dict[str, str]:
    """Promienie ``radius-*`` z ``tokens.json`` wersji (grupy ``tokens``/``radius``) – nazwa → wartość."""
    other = (runtime.tokens or {}).get("other") or {}
    return {
        name: value
        for name, value in other.items()
        if name.startswith("radius-") and RADIUS_VALUE.match(value)
    }


def clean_radii(runtime, raw) -> dict[str, str]:
    """Nadpisania promieni: wyłącznie promienie wersji, ``0–48px`` albo ``0–3rem``, różne od domyślnych."""
    if not isinstance(raw, dict):
        return {}
    allowed = editable_radii(runtime)
    out = {}
    for name in sorted(raw):
        value = raw[name]
        if name not in allowed or not isinstance(value, str):
            continue
        value = value.strip()
        match = RADIUS_VALUE.match(value)
        if not match or float(match.group(1)) > RADIUS_LIMITS[match.group(2)] or value == allowed[name]:
            continue
        out[name] = value
    return out


def option_ids(entries) -> tuple[str, ...]:
    return tuple(entry["id"] for entry in entries or ())


def merged_palette(runtime, mode: str, overrides: dict[str, str]) -> dict[str, str]:
    """Paleta ``mode`` z ``tokens.json`` z nałożonymi nadpisaniami (przed uzupełnieniem pochodnych)."""
    palette = dict((runtime.tokens or {}).get(mode) or {})
    palette.update(overrides)
    for alias, canonical in tk.ALIASES.items():
        if alias in overrides:
            palette[canonical] = overrides[alias]
    return palette


# --- kontrast -------------------------------------------------------------------------------------


def _pairs(palette: dict[str, str], declared=()) -> list[tuple[str, str, str, float]]:
    """Pary (pierwszy plan, tło, opis, próg) do sprawdzenia w pełnej palecie.

    Źródła: ``CONTRAST_PAIRS`` platformy, pary zadeklarowane w ``tokens.json`` motywu (grupa
    ``contrast`` – to, czego nie widać z nazw: biały napis na ``primary-fill`` w arkuszu) i reguły
    nazw: ``X-contrast``/``X-text``/``X-ink``/``X-accent`` na ``X``, ``on-X`` i ``on-X-…`` na ``X``,
    ``A-on-X`` na ``X``.
    """
    pairs = [(fg, bg, label, tk.MIN_CONTRAST) for fg, bg, label in tk.CONTRAST_PAIRS]
    pairs += [(str(fg), str(bg), "", float(threshold)) for fg, bg, threshold in declared]
    known = {(fg, bg) for fg, bg, *_ in pairs}
    for name in sorted(palette):
        bases = []
        if "-on-" in name:
            bases.append(name.split("-on-", 1)[1])
        if name.startswith("on-"):
            rest = name[3:]
            bases += [base for base in palette if rest.startswith(base + "-")]
        for base in bases:
            if base in palette and (name, base) not in known:
                pairs.append((name, base, f"{name} na {base}", tk.MIN_CONTRAST))
                known.add((name, base))
    for name in sorted(palette):
        # ``-accent`` (np. ``cover-accent`` IQO) – wyróżnienie pisane na powierzchni bazowej;
        # ``accent-*`` z rejestru ``classic`` mają inne znaczenie i nie kończą się tak.
        for prefix, suffix in (("", "-contrast"), ("", "-text"), ("on-", ""), ("", "-ink"), ("", "-accent")):
            if prefix and name.startswith(prefix):
                base = name[len(prefix) :]
            elif suffix and name.endswith(suffix):
                base = name[: -len(suffix)]
            else:
                continue
            # ``success-ink`` itp. z rejestru ``classic`` to tekst na tle „soft”, nie na kolorze
            # bazowym – para z nazwy dotyczy wyłącznie tokenów własnych motywu (``cta-ink``).
            if suffix == "-ink" and name in tk.COLOR_TOKENS:
                continue
            if base in palette and (name, base) not in known:
                pairs.append((name, base, f"{name} na {base}", tk.MIN_CONTRAST))
                known.add((name, base))
    for token in FOCUS_TOKENS:
        if token in palette:
            pairs.append((token, "bg", f"obwódka fokusu ({token}) na tle strony", MIN_NON_TEXT))
    return pairs


def contrast_report(runtime, scheme: str, colors: dict) -> tuple[list[str], list[str]]:
    """``(błędy, ostrzeżenia)`` dla schematu i nadpisań.

    Błąd = para poniżej progu, w której którykolwiek kolor pochodzi z nadpisania koordynatora
    (bezpośrednio albo jako pochodna nadpisanego) – zapis jest wtedy blokowany. Para słaba już
    w samym motywie to ostrzeżenie: koordynator jej nie zepsuł i nie może jej zablokować.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for mode in palette_modes(runtime, scheme):
        overrides = (colors or {}).get(mode) or {}
        # Ta sama reguła, co w ``tokens.build_tokens_css``: brakujące tokeny z ``classic`` trybu ciemnego.
        dark = mode == "dark" or scheme == "dark"
        base = tk.complete_palette(merged_palette(runtime, mode, {}), dark=dark)
        full = tk.complete_palette(merged_palette(runtime, mode, overrides), dark=dark)
        label = _("ciemna") if mode == "dark" else _("jasna")
        declared = (runtime.tokens or {}).get("contrast") or ()
        for fg, bg, _description, threshold in _pairs(full, declared):
            # Element pary zadeklarowanej w motywie może być stałym kolorem (``#ffffff``).
            a = fg if tk.HEX.match(fg) else full.get(fg)
            b = bg if tk.HEX.match(bg) else full.get(bg)
            if not (a and b and tk.is_color(a) and tk.is_color(b)):
                continue
            ratio = tk.contrast(a, b)
            if ratio >= threshold:
                continue
            # Nazwy tokenów zamiast opisu słownego: koordynator widzi je w tabeli kolorów obok.
            message = _(
                "Paleta %(palette)s: %(fg)s (%(a)s) na %(bg)s (%(b)s) – kontrast %(ratio)s:1, "
                "wymagane %(minimum)s:1 (WCAG AA)."
            ) % {
                "palette": label,
                "fg": fg,
                "a": a,
                "bg": bg,
                "b": b,
                "ratio": f"{ratio:.2f}",
                "minimum": f"{threshold:g}",
            }
            touched = (base.get(fg, fg) != a) or (base.get(bg, bg) != b)
            (errors if touched else warnings).append(message)
    return errors, warnings


# --- arkusz ---------------------------------------------------------------------------------------


def needs_css(runtime, options: dict) -> bool:
    """Czy opcje odbiegają od wersji na tyle, że potrzebny jest arkusz ``custom.css``."""
    scheme = options.get("scheme") or runtime.color_scheme
    fonts = option_ids(runtime.fonts)
    font = options.get("font") or ""
    colors = options.get("colors") or {}
    active_colors = any(colors.get(mode) for mode in palette_modes(runtime, scheme))
    return (
        scheme != runtime.color_scheme
        or active_colors
        or bool(fonts and font and font != fonts[0])
        or bool(options.get("radius"))
    )


def _font_tokens(runtime, font_id: str) -> dict[str, str]:
    fonts = {entry["id"]: entry for entry in runtime.fonts or ()}
    entry = fonts.get(font_id)
    if entry is None or (runtime.fonts and runtime.fonts[0]["id"] == font_id):
        return {}
    return {f"font-{role}": entry[role] for role in ("body", "display", "mono") if entry.get(role)}


def build_css(runtime, options: dict) -> str:
    """Arkusz nadpisań dla oczyszczonych ``options`` (pusty, gdy niczego nie zmieniają)."""
    if not needs_css(runtime, options):
        return ""
    scheme = options.get("scheme") or runtime.color_scheme
    colors = options.get("colors") or {}
    parts = ["/* custom.css – dostosowanie motywu przez koordynatora (apps/themes/customize.py). */"]
    modes = palette_modes(runtime, scheme)
    if scheme != runtime.color_scheme or any(colors.get(mode) for mode in modes):
        data = runtime.tokens or {}
        token_set = tk.TokenSet(
            light=merged_palette(runtime, "light", colors.get("light") or {}),
            dark=merged_palette(runtime, "dark", colors.get("dark") or {}) if data.get("dark") else {},
            other=dict(data.get("other") or {}),
            dark_other=dict(data.get("dark_other") or {}),
        )
        generated = tk.build_tokens_css(token_set, scheme)
        css = generated.css.split("\n", 1)[1]  # bez komentarza nagłówka tokens.css
        if scheme != "dark" and token_set.dark_other:
            # ``tokens.css`` motywu ciemnego ma w ``:root`` cienie z grupy ``dark``; schemat jasny
            # musi je cofnąć do wartości jasnych (motywu albo ``classic``).
            reset = {
                name: token_set.other.get(name) or tk.CLASSIC_OTHER.get(name)
                for name in token_set.dark_other
                if token_set.other.get(name) or tk.CLASSIC_OTHER.get(name)
            }
            if reset:
                css = css.replace(
                    ":root {\n", ":root {\n" + "".join(f"  --t-{k}: {v};\n" for k, v in reset.items()), 1
                )
        parts.append(css.rstrip("\n"))
    radius = dict(options.get("radius") or {})
    # Synonim (``radius-md``) zapisujemy też pod nazwą kanoniczną (``radius``), jak ``tokens.css``.
    radius.update({tk.ALIASES[name]: value for name, value in list(radius.items()) if name in tk.ALIASES})
    extra = {**_font_tokens(runtime, options.get("font") or ""), **radius}
    if extra:
        parts.append(":root {\n" + "".join(f"  --t-{k}: {v};\n" for k, v in extra.items()) + "}")
    return "\n".join(parts) + "\n"


_CSS_CACHE: dict[tuple[int, str], str] = {}
_CSS_CACHE_MAX = 256


def css_for(runtime, options: dict) -> str:
    """:func:`build_css` z pamięcią procesu (wersja jest niezmienna, opcje – w kluczu).

    Pamięć jest ograniczona: klucz zależy od opcji, a te (choć podpisane) mogą być różne dla
    każdego podglądu – po przekroczeniu limitu słownik zaczyna się od nowa.
    """
    key = (runtime.pk, json.dumps(options, sort_keys=True))
    cached = _CSS_CACHE.get(key)
    if cached is None:
        cached = build_css(runtime, options)
        if len(_CSS_CACHE) >= _CSS_CACHE_MAX:
            _CSS_CACHE.clear()
        _CSS_CACHE[key] = cached
    return cached


def effective_palette(runtime, options: dict) -> dict[str, str]:
    """Paleta główna (pierwszy tryb schematu) po dostosowaniu – podstawa arkusza akcentu marki."""
    scheme = options.get("scheme") or runtime.color_scheme
    colors = options.get("colors") or {}
    mode = palette_modes(runtime, scheme)[0]
    if scheme == runtime.color_scheme and not colors.get(mode):
        return dict(runtime.palette)
    dark = mode == "dark" or scheme == "dark"
    return tk.complete_palette(merged_palette(runtime, mode, colors.get(mode) or {}), dark=dark)


def options_digest(options: dict) -> str:
    """Krótki skrót opcji dostosowania – część adresu arkusza akcentu (nowe kolory = nowy adres)."""
    import hashlib

    keys = ("scheme", "colors")
    payload = json.dumps({key: options.get(key) for key in keys if options.get(key)}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8] if payload != "{}" else ""


def meta_color(runtime, options: dict) -> str:
    """Kolor paska przeglądarki (``<meta name="theme-color">``) po nadpisaniach – ``primary``."""
    scheme = options.get("scheme") or runtime.color_scheme
    colors = options.get("colors") or {}
    mode = palette_modes(runtime, scheme)[0]
    if scheme == runtime.color_scheme and not colors.get(mode):
        return runtime.meta_color
    palette = merged_palette(runtime, mode, colors.get(mode) or {})
    return tk.complete_palette(palette, dark=scheme == "dark").get("primary", runtime.meta_color)


# --- podpis ---------------------------------------------------------------------------------------


def _payload(competition_id: int, runtime, options: dict) -> dict:
    return {
        "g": GENERATOR_VERSION,
        "c": competition_id,
        "v": runtime.pk,
        "s": options.get("scheme") or runtime.color_scheme,
        "f": options.get("font") or "",
        "k": {
            mode: dict(sorted(values.items()))
            for mode, values in sorted((options.get("colors") or {}).items())
        },
        "r": dict(sorted((options.get("radius") or {}).items())),
    }


def sign(competition_id: int, runtime, options: dict) -> str:
    return signing.Signer(salt=SIGN_SALT).sign_object(
        _payload(competition_id, runtime, options), compress=True
    )


def unsign(value: str) -> dict | None:
    try:
        payload = signing.Signer(salt=SIGN_SALT).unsign_object(value)
    except signing.BadSignature, ValueError, TypeError:
        return None
    return payload if isinstance(payload, dict) else None
