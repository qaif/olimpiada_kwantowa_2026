"""Tokeny motywu: rejestr wartości ``classic``, walidacja ``tokens.json`` i generator ``tokens.css``.

Arkusze aplikacji czytają kolory, kroje, promienie i odstępy wyłącznie przez zmienne ``--t-*``
(``static/css/app.css`` § 2). Motyw podaje ich wartości w ``tokens.json``, a ten moduł zamienia je
w arkusz ``tokens.css`` dołączany **po** ``app.css``. Dwie decyzje, które warto znać:

- **Arkusz jest kompletny dla wybranego schematu.** Motyw jasny-tylko (``color_scheme: light``)
  dostaje w ``tokens.css`` *wszystkie* tokeny kolorów, także te, których nie podał (uzupełnione
  pochodnymi albo wartościami ``classic`` z wariantu jasnego). Bez tego w systemie z trybem ciemnym
  tokeny niepodane przez motyw brałyby ciemne wartości ``classic`` z ``@media`` w ``app.css``
  i jasny motyw miałby ciemne plamy (np. zielony status na jasnym tle dobrany pod granat).
- **Pochodne liczy serwer, nie przeglądarka.** Motyw podający sam ``primary`` i ``accent`` dostaje
  skale, odcienie „soft”, kolory „na akcencie” i składowe RGB policzone tutaj (mieszanie kanałów
  sRGB, wybór bieli lub czerni po kontraście). ``color-mix()`` w arkuszu dałoby to samo, ale
  zmieniłoby postać wartości wyliczonej i nie dałoby się sprawdzić kontrastu przy wgraniu.

Rejestr ``CLASSIC_*`` jest kopią wartości z ``app.css`` – test ``test_tokens_registry_matches_app_css``
pilnuje, żeby się nie rozjechały.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# --- rejestr classic ---------------------------------------------------------------------------

#: Tokeny kolorów wariantu jasnego (``:root`` w ``app.css``). Kolejność = kolejność w arkuszu.
CLASSIC_LIGHT: dict[str, str] = {
    "primary-900": "#0b1729",
    "primary-800": "#12233f",
    "primary-700": "#1b3153",
    "primary-600": "#274677",
    "accent-300": "#ef8a82",
    "accent-500": "#d43228",
    "accent-600": "#a10f0f",
    "accent-700": "#8b0404",
    "accent-800": "#6d0303",
    "bg": "#f6f4ee",
    "bg-muted": "#efece3",
    "surface": "#fffefb",
    "surface-2": "#faf8f2",
    "surface-inset": "#f1eee5",
    "text": "#171d27",
    "text-soft": "#3c4453",
    "muted": "#5d6472",
    "border": "#dcd6c8",
    "border-strong": "#c4bcaa",
    "primary": "#12233f",
    "primary-contrast": "#f4f1e8",
    "primary-soft": "#e6e9f0",
    "accent": "#a10f0f",
    "accent-ink": "#6d0303",
    "accent-soft": "#f8e5e3",
    "accent-on-primary": "#ef8a82",
    "accent-rule": "#d43228",
    "success": "#2e7d5b",
    "success-ink": "#1f5c42",
    "success-soft": "#e0efe7",
    "warning": "#a06a17",
    "warning-ink": "#7a4f0c",
    "warning-soft": "#f8eed9",
    "danger": "#bd3b26",
    "danger-ink": "#8f2c16",
    "danger-soft": "#fbe8e0",
    "info": "#274677",
    "info-ink": "#1b3153",
    "info-soft": "#e4eaf4",
    "neutral-ink": "#4a515e",
    "neutral-soft": "#e9e6dd",
    "link": "#1d4a86",
    "link-hover": "#12233f",
    "focus": "#a10f0f",
    "on-accent": "#fff",
    "on-primary-strong": "#fff",
    "on-status": "#fff",
    "logo-plaque": "#f6f4ee",
}

#: Wariant ciemny (``@media (prefers-color-scheme: dark)`` w ``app.css``) – wyłącznie tokeny, które
#: ten wariant przestawia. Pozostałe (skale, „na granacie”, plakietka) są wspólne dla obu trybów.
CLASSIC_DARK_OVERRIDES: dict[str, str] = {
    "bg": "#0f1726",
    "bg-muted": "#0b1120",
    "surface": "#162032",
    "surface-2": "#1b2740",
    "surface-inset": "#111a2b",
    "text": "#e9edf5",
    "text-soft": "#cbd3e0",
    "muted": "#9fabbf",
    "border": "#27334c",
    "border-strong": "#374561",
    "primary": "#17243d",
    "primary-contrast": "#eef1f7",
    "primary-soft": "#1d2942",
    "accent": "#ef8a82",
    "accent-ink": "#f6b3ad",
    "accent-soft": "#33100f",
    "accent-on-primary": "#ef8a82",
    "accent-rule": "#d9483c",
    "success": "#4cae83",
    "success-ink": "#7fd0aa",
    "success-soft": "#10281f",
    "warning": "#dda44a",
    "warning-ink": "#edc078",
    "warning-soft": "#2c2210",
    "danger": "#ec8f6e",
    "danger-ink": "#f3b294",
    "danger-soft": "#2f1a11",
    "info": "#7ea6e0",
    "info-ink": "#a6c4ee",
    "info-soft": "#142239",
    "neutral-ink": "#b3bdcd",
    "neutral-soft": "#1c2740",
    "link": "#8fb6e8",
    "link-hover": "#c3d8f5",
    "focus": "#ef8a82",
}

CLASSIC_DARK: dict[str, str] = {**CLASSIC_LIGHT, **CLASSIC_DARK_OVERRIDES}

#: Składowe RGB, z których arkusz składa półprzezroczyste odcienie (``rgba(var(--t-x-rgb), 0.7)``).
#: Klucz = token wynikowy, wartość = token koloru, z którego go liczymy. W ``classic`` są stałe
#: dla obu trybów (patrz komentarz w ``app.css``), więc generator liczy je z palety **jasnej**
#: motywu, a przy motywie ciemnym – z jego palety głównej.
RGB_TOKENS: dict[str, str] = {
    "primary-rgb": "primary-800",
    "primary-900-rgb": "primary-900",
    "primary-contrast-rgb": "primary-contrast",
    "accent-rgb": "accent-600",
    "accent-rule-rgb": "accent-500",
    "accent-on-primary-rgb": "accent-300",
    "danger-rgb": "danger",
}

#: Wartości ``classic`` pozostałych tokenów (typografia, odstępy, promienie, cienie, układ).
#: ``font-mono`` nie ma definicji w ``classic`` (każde miejsce w arkuszach ma własny stos), więc
#: tu stoi tylko jako dokumentacja dla autorów motywów.
CLASSIC_OTHER: dict[str, str] = {
    "space-1": "0.25rem",
    "space-2": "0.5rem",
    "space-3": "0.75rem",
    "space-4": "1rem",
    "space-6": "1.5rem",
    "space-8": "2rem",
    "space-12": "3rem",
    "radius-sm": "4px",
    "radius": "8px",
    "radius-lg": "14px",
    "shadow-sm": "0 1px 2px rgba(18, 35, 63, 0.06), 0 1px 3px rgba(18, 35, 63, 0.05)",
    "shadow": "0 2px 4px rgba(18, 35, 63, 0.06), 0 8px 24px rgba(18, 35, 63, 0.07)",
    "shadow-inset": "inset 0 1px 0 rgba(255, 255, 255, 0.6)",
    "max-width": "1100px",
    "reading": "68ch",
    "font-size-base": "1rem",
    "font-body": '"Inter", system-ui, "Segoe UI", Roboto, sans-serif',
    "font-display": '"Source Serif 4", Georgia, "Times New Roman", serif',
}

CLASSIC_DARK_OTHER: dict[str, str] = {
    "shadow-sm": "0 1px 2px rgba(0, 0, 0, 0.4)",
    "shadow": "0 2px 6px rgba(0, 0, 0, 0.45), 0 12px 32px rgba(0, 0, 0, 0.35)",
    "shadow-inset": "inset 0 1px 0 rgba(255, 255, 255, 0.04)",
}

COLOR_TOKENS = frozenset(CLASSIC_LIGHT)

#: Grupy ``tokens.json``. ``colors``/``dark`` – kolory (``dark`` może też nieść ``shadow-*``),
#: pozostałe – wartości dowolne, ale bezpieczne (``SAFE_VALUE``). Klucz w grupie ``radius``,
#: ``space`` i ``shadow`` może być krótki (``"sm"`` → ``radius-sm``) albo pełny (``"radius-sm"``).
COLOR_GROUPS = ("colors", "dark")
VALUE_GROUPS = ("tokens", "typography", "radius", "space", "shadow", "layout", "extra")

#: Synonimy spotykane w paczkach (np. ``iqo-quantum``) → nazwa kanoniczna rejestru. Token pod
#: synonimem trafia do ``tokens.css`` **pod obiema nazwami**: arkusz motywu czyta swoją, arkusze
#: aplikacji – kanoniczną. Synonim nie nadpisuje nazwy kanonicznej podanej wprost.
ALIASES: dict[str, str] = {
    "accent-contrast": "on-accent",
    "primary-hover": "primary-700",
    "radius-md": "radius",
    "shadow-md": "shadow",
    "size-md": "font-size-base",
}
PREFIXED_GROUPS = {"radius": "radius", "space": "space", "shadow": "shadow"}

#: Nazwa tokenu: małe litery, cyfry i myślniki. Motyw może definiować własne tokeny (grupa
#: ``extra`` albo dowolna nazwa w grupach) – trafiają do ``tokens.css`` jako ``--t-<nazwa>``
#: i są do użycia w jego ``theme.css``.
TOKEN_NAME = re.compile(r"^[a-z][a-z0-9-]{0,47}$")

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
RGB = re.compile(r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(0|1|0?\.\d+|1\.0+))?\s*\)$")

#: Wartość niekolorowa: kroje w cudzysłowach, liczby z jednostkami, ``calc()``, ``rgba()`` cieni.
#: Bez średników, nawiasów klamrowych, ``<``/``>``, ``\``, ``@`` i ``!`` – czyli bez możliwości
#: wyjścia z deklaracji ``--t-x: …;`` w generowanym arkuszu.
SAFE_VALUE = re.compile(r"""^[A-Za-z0-9\s#%.,()"'+\-/*]{1,300}$""")
FORBIDDEN_IN_VALUE = re.compile(r"url\s*\(|expression|javascript|image-set|attr\s*\(|src\s*\(", re.I)

MAX_TOKENS_BYTES = 64 * 1024

#: Próg kontrastu tekstu do tła (WCAG 2.x AA dla zwykłego tekstu). Poniżej – ostrzeżenie, nie błąd:
#: motyw ma prawo do decyzji projektowej, a raport ma ją pokazać operatorowi przed aktywacją.
MIN_CONTRAST = 4.5

#: Pary sprawdzane przy wgraniu: (pierwszy plan, tło, opis po polsku).
CONTRAST_PAIRS = (
    ("text", "bg", "tekst na tle strony"),
    ("text", "surface", "tekst na karcie"),
    ("muted", "surface", "tekst pomocniczy na karcie"),
    ("primary-contrast", "primary", "napis w pasku marki"),
    ("on-accent", "accent", "napis na przycisku akcentu"),
    ("link", "bg", "odnośnik na tle strony"),
)


# --- kolory ------------------------------------------------------------------------------------


def parse_color(value: str) -> tuple[int, int, int] | None:
    """``#rgb``/``#rrggbb``/``#rrggbbaa``/``rgb()``/``rgba()`` → ``(r, g, b)``; inne → ``None``."""
    value = (value or "").strip()
    if HEX.match(value):
        digits = value[1:]
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits[:3])
        return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)
    match = RGB.match(value)
    if match:
        channels = tuple(int(match.group(i)) for i in (1, 2, 3))
        if all(0 <= c <= 255 for c in channels):
            return channels  # type: ignore[return-value]
    return None


def is_color(value: str) -> bool:
    return parse_color(value) is not None


def to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    """Kolor ``a`` przesunięty o ułamek ``t`` w stronę ``b`` (liniowo w sRGB, jak ``color-mix``)."""
    ca, cb = parse_color(a), parse_color(b)
    if ca is None or cb is None:
        return a
    return to_hex(tuple(x * (1 - t) + y * t for x, y in zip(ca, cb, strict=True)))


def luminance(value: str) -> float:
    """Względna luminancja WCAG 2.x."""
    rgb = parse_color(value) or (0, 0, 0)

    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def readable_on(background: str) -> str:
    """Biel albo prawie-czerń – co da większy kontrast na ``background``."""
    return "#ffffff" if contrast("#ffffff", background) >= contrast("#111111", background) else "#111111"


def rgb_triplet(value: str) -> str | None:
    rgb = parse_color(value)
    return None if rgb is None else f"{rgb[0]}, {rgb[1]}, {rgb[2]}"


# --- walidacja tokens.json --------------------------------------------------------------------


@dataclass
class TokenSet:
    """Wynik odczytu ``tokens.json``: palety i pozostałe tokeny, plus komunikaty walidacji."""

    light: dict[str, str] = field(default_factory=dict)
    dark: dict[str, str] = field(default_factory=dict)
    other: dict[str, str] = field(default_factory=dict)
    dark_other: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"light": self.light, "dark": self.dark, "other": self.other, "dark_other": self.dark_other}

    @classmethod
    def from_json(cls, data: dict) -> TokenSet:
        return cls(
            light=dict(data.get("light") or {}),
            dark=dict(data.get("dark") or {}),
            other=dict(data.get("other") or {}),
            dark_other=dict(data.get("dark_other") or {}),
        )


def _token_name(group: str, key: str) -> str:
    prefix = PREFIXED_GROUPS.get(group)
    if prefix and key != prefix and not key.startswith(prefix + "-"):
        return "radius" if (prefix == "radius" and key in ("md", "default")) else f"{prefix}-{key}"
    return key


def _check_value(name: str, value, errors: list[str]) -> str | None:
    if not isinstance(value, str):
        errors.append(f"tokens.json: wartość tokenu „{name}” musi być napisem.")
        return None
    value = value.strip()
    if not SAFE_VALUE.match(value) or FORBIDDEN_IN_VALUE.search(value):
        errors.append(f"tokens.json: niedozwolona wartość tokenu „{name}” ({value[:60]!r}).")
        return None
    return value


def _apply_aliases(values: dict[str, str]) -> None:
    for alias, canonical in ALIASES.items():
        if alias in values and canonical not in values:
            values[canonical] = values[alias]


def parse_tokens(raw: bytes) -> TokenSet:
    """Czyta i waliduje ``tokens.json``. Błąd = token odrzucony (i wpis w ``errors``)."""
    result = TokenSet()
    if len(raw) > MAX_TOKENS_BYTES:
        result.errors.append(f"tokens.json: plik większy niż {MAX_TOKENS_BYTES // 1024} KB.")
        return result
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result.errors.append(f"tokens.json: niepoprawny JSON ({exc}).")
        return result
    if not isinstance(data, dict):
        result.errors.append("tokens.json: oczekiwany obiekt JSON.")
        return result
    for group, entries in data.items():
        if group in ("schema", "$schema", "comment", "_comment"):
            continue
        if group not in COLOR_GROUPS + VALUE_GROUPS:
            result.warnings.append(f"tokens.json: nieznana grupa „{group}” – pominięta.")
            continue
        if not isinstance(entries, dict):
            result.errors.append(f"tokens.json: grupa „{group}” musi być obiektem.")
            continue
        for key, value in entries.items():
            name = _token_name(group, str(key))
            if not TOKEN_NAME.match(name):
                result.errors.append(f"tokens.json: niedozwolona nazwa tokenu „{key}”.")
                continue
            if group in COLOR_GROUPS and not name.startswith("shadow"):
                if not isinstance(value, str) or not is_color(value):
                    result.errors.append(
                        f"tokens.json: „{group}.{key}” musi być kolorem hex albo rgb()/rgba() "
                        f"(jest {value!r})."
                    )
                    continue
                (result.light if group == "colors" else result.dark)[name] = value.strip()
                continue
            checked = _check_value(name, value, result.errors)
            if checked is None:
                continue
            (result.dark_other if group == "dark" else result.other)[name] = checked
    for values in (result.light, result.dark, result.other, result.dark_other):
        _apply_aliases(values)
    return result


# --- pochodne i generator --------------------------------------------------------------------


def _is_dark_palette(palette: dict[str, str], fallback_dark: bool) -> bool:
    bg = palette.get("bg")
    return luminance(bg) < 0.2 if bg and is_color(bg) else fallback_dark


def complete_palette(given: dict[str, str], *, dark: bool) -> dict[str, str]:
    """Pełna paleta: podane wartości + pochodne liczone z podanych + reszta z ``classic``.

    Pochodna powstaje **wyłącznie** z wartości podanej przez motyw – token, którego bazy motyw
    nie podał, zostaje przy ``classic`` danego trybu (motyw zmieniający sam akcent nie ma
    przemalowanego tła).
    """
    base = CLASSIC_DARK if dark else CLASSIC_LIGHT
    p = dict(given)
    is_dark = _is_dark_palette(p, dark)

    def put(name: str, value) -> None:
        if name not in p and value:
            p[name] = value

    bg = p.get("bg")
    text = p.get("text")
    if bg:
        put("surface", bg)
    surface = p.get("surface") or bg
    if bg and text:
        put("bg-muted", mix(bg, text, 0.04))
        put("surface-inset", mix(bg, text, 0.06))
        put("text-soft", mix(text, bg, 0.18))
        put("muted", mix(text, bg, 0.38))
        put("border", mix(bg, text, 0.16))
        put("border-strong", mix(bg, text, 0.28))
        put("neutral-ink", p.get("text-soft"))
    if surface and bg:
        put("surface-2", mix(surface, bg, 0.5))
        put("neutral-soft", p.get("surface-inset"))
    primary = p.get("primary")
    if primary:
        put("primary-800", primary)
        put("primary-900", mix(primary, "#000000", 0.35))
        put("primary-700", mix(primary, "#ffffff", 0.08))
        put("primary-600", mix(primary, "#ffffff", 0.2))
        put("primary-contrast", readable_on(primary))
        put("on-primary-strong", readable_on(primary))
        if surface:
            put("primary-soft", mix(surface, primary, 0.12))
        put("info", primary if not is_dark else mix(primary, "#ffffff", 0.45))
    accent = p.get("accent")
    if accent:
        put("accent-600", accent)
        put("accent-700", mix(accent, "#000000", 0.14))
        put("accent-800", mix(accent, "#000000", 0.32))
        put("accent-500", mix(accent, "#ffffff", 0.18))
        put("accent-300", mix(accent, "#ffffff", 0.45))
        put("accent-ink", mix(accent, "#ffffff" if is_dark else "#000000", 0.3))
        put("accent-rule", accent)
        put("on-accent", readable_on(accent))
        put("focus", accent)
        if surface:
            put("accent-soft", mix(surface, accent, 0.12))
        if primary:
            on_primary = accent if contrast(accent, primary) >= MIN_CONTRAST else p.get("accent-300")
            if on_primary and contrast(on_primary, primary) < MIN_CONTRAST:
                on_primary = p.get("primary-contrast")
            put("accent-on-primary", on_primary)
        if bg:
            put("link", accent if contrast(accent, bg) >= MIN_CONTRAST else (primary or None))
    if text:
        put("link-hover", text)
    if p.get("danger"):
        put("on-status", readable_on(p["danger"]))
    for status in ("success", "warning", "danger", "info"):
        colour = p.get(status)
        if not colour:
            continue
        if text:
            put(f"{status}-ink", mix(colour, text, 0.35))
        if surface:
            put(f"{status}-soft", mix(surface, colour, 0.14))
    for name, value in base.items():
        put(name, value)
    return {name: p[name] for name in [*base, *sorted(set(p) - set(base))]}


def rgb_tokens(palette: dict[str, str]) -> dict[str, str]:
    out = {}
    for name, source in RGB_TOKENS.items():
        triplet = rgb_triplet(palette.get(source, ""))
        if triplet:
            out[name] = triplet
    return out


def contrast_warnings(palette: dict[str, str], label: str) -> list[str]:
    warnings = []
    for fg, bg, description in CONTRAST_PAIRS:
        a, b = palette.get(fg), palette.get(bg)
        if a and b and is_color(a) and is_color(b):
            ratio = contrast(a, b)
            if ratio < MIN_CONTRAST:
                warnings.append(
                    f"Kontrast {description} ({fg} {a} na {bg} {b}) w palecie {label} wynosi "
                    f"{ratio:.2f}:1 – poniżej {MIN_CONTRAST}:1 (WCAG AA)."
                )
    return warnings


@dataclass
class GeneratedTokens:
    css: str
    main: dict[str, str]
    dark: dict[str, str] | None
    warnings: list[str]


def _block(selector_indent: str, values: dict[str, str]) -> list[str]:
    return [f"{selector_indent}  --t-{name}: {value};" for name, value in values.items()]


def build_tokens_css(tokens: TokenSet, color_scheme: str) -> GeneratedTokens:
    """``tokens.css`` motywu dla schematu ``light`` | ``dark`` | ``auto``.

    - ``light``: jedna paleta (``colors``) w ``:root``; ``color-scheme: light`` wyłącza ciemne
      formularze przeglądarki,
    - ``dark``: jedna paleta (``dark``, a bez niej ``colors``) w ``:root``,
    - ``auto``: ``colors`` w ``:root`` i ``dark`` w ``@media (prefers-color-scheme: dark)``;
      bez zestawu ``dark`` motyw jest zawsze jasny (ostrzeżenie w raporcie).
    """
    warnings: list[str] = []
    dark_palette = None
    if color_scheme == "dark":
        main = complete_palette(tokens.dark or tokens.light, dark=True)
        scheme = "dark"
        other = {**tokens.other, **tokens.dark_other}
    else:
        main = complete_palette(tokens.light, dark=False)
        scheme = "light" if color_scheme == "light" or not tokens.dark else "light dark"
        other = dict(tokens.other)
        if color_scheme == "auto":
            if tokens.dark:
                dark_palette = complete_palette(tokens.dark, dark=True)
            else:
                warnings.append(
                    "color_scheme „auto” bez zestawu „dark” w tokens.json – motyw będzie zawsze jasny."
                )
    main_values = {**main, **rgb_tokens(main), **other}
    warnings += contrast_warnings(main, "główna")
    lines = [
        "/* tokens.css – wygenerowane przez platformę z tokens.json motywu (apps/themes/tokens.py). */",
        ":root {",
        f"  color-scheme: {scheme};",
        *_block("", main_values),
        "}",
    ]
    if dark_palette is not None:
        warnings += contrast_warnings(dark_palette, "ciemna")
        dark_values = {**dark_palette, **rgb_tokens(dark_palette), **tokens.dark_other}
        lines += [
            "@media (prefers-color-scheme: dark) {",
            "  :root {",
            *_block("  ", dark_values),
            "  }",
            "}",
        ]
    return GeneratedTokens(css="\n".join(lines) + "\n", main=main, dark=dark_palette, warnings=warnings)


def accent_override_css(accent: str, palette: dict[str, str]) -> str:
    """Arkusz nadpisania akcentu motywu kolorem marki konkursu („Ustawienia konkursu”).

    Wyłącznie tokeny, które od akcentu zależą wprost. **Bez** ``focus``: obwódka fokusu jest
    dobrana przez motyw pod jego tło (kontrast ≥ 3:1), a kolor marki z formularza nie przeszedł
    żadnej kontroli kontrastu – fokus niewidoczny na tle to bariera, nie kwestia gustu.
    Kolor spoza ``HEX`` daje pusty arkusz (pole w modelu ma walidator, to jest druga linia obrony
    przed wstrzyknięciem w CSS).
    """
    if not HEX.match(accent or ""):
        return ""
    surface = palette.get("surface") or palette.get("bg") or "#ffffff"
    values = {
        "accent": accent,
        "accent-600": accent,
        "accent-rule": accent,
        "on-accent": readable_on(accent),
        "accent-soft": mix(surface, accent, 0.12),
        "accent-rgb": rgb_triplet(accent),
        "accent-rule-rgb": rgb_triplet(accent),
    }
    body = "\n".join(f"  --t-{k}: {v};" for k, v in values.items() if v)
    return f"/* Akcent marki konkursu (theme_options.brand_accent). */\n:root {{\n{body}\n}}\n"


def classic_tokens_json() -> dict:
    """``tokens.json`` motywu ``classic`` – dokumentacja API tokenów dla autorów motywów."""
    return {
        "schema": 1,
        "colors": dict(CLASSIC_LIGHT),
        "dark": {**CLASSIC_DARK_OVERRIDES, **CLASSIC_DARK_OTHER},
        "typography": {
            "font-body": CLASSIC_OTHER["font-body"],
            "font-display": CLASSIC_OTHER["font-display"],
            "font-mono": 'ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace',
            "font-size-base": CLASSIC_OTHER["font-size-base"],
        },
        "radius": {k: v for k, v in CLASSIC_OTHER.items() if k.startswith("radius")},
        "space": {k: v for k, v in CLASSIC_OTHER.items() if k.startswith("space")},
        "shadow": {k: v for k, v in CLASSIC_OTHER.items() if k.startswith("shadow")},
        "layout": {"max-width": CLASSIC_OTHER["max-width"], "reading": CLASSIC_OTHER["reading"]},
    }
