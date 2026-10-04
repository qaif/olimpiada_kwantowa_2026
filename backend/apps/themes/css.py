"""Sanityzacja arkusza motywu (``theme.css``) i stylów osadzonych w SVG.

Arkusz jest **parsowany** (``tinycss2``), a nie przeszukiwany wyrażeniami regularnymi: ucieczki
CSS (``@\\69mport``, ``exp\\72 ession(``), komentarze w środku słowa i cudzysłowy w ``url()`` rozbijają
każdy filtr tekstowy, a parser zgodny ze specyfikacją widzi to samo, co przeglądarka. Reguły
(THEME-01 § 2):

- ``@import`` – odrzucone (dociągnięcie arkusza spoza paczki omija całą walidację),
- ``url()``/``src()``/napisy w ``image-set()`` – wyłącznie ścieżki względne do plików paczki pod
  ``assets/``; adres bezwzględny, schemat (``http:``, ``data:``, ``javascript:``), ``//host``
  i wyjście ``..`` poza katalog – błąd. Dobre adresy są **przepisywane** na postać kanoniczną
  ``url("assets/…")``: arkusz leży w storage obok katalogu ``assets/``, więc adres względny trafia
  do pliku paczki pod tym samym niezmiennym prefiksem, a zmiana domeny bucketu (dziś ``:9000``,
  docelowo własny host S3) nie wymaga przepisywania wgranych motywów,
- ``expression()``, ``behavior``, ``-moz-binding``, ``javascript:``/``vbscript:`` w dowolnym
  napisie lub identyfikatorze, ``-moz-element()``/``element()`` – błąd,
- nieznane at-reguły – błąd (lista dozwolonych jest zamknięta, bo nowa at-reguła przeglądarki
  może kiedyś znaczyć „pobierz coś”).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

ALLOWED_AT_RULES = frozenset(
    {
        "media",
        "supports",
        "font-face",
        "keyframes",
        "-webkit-keyframes",
        "layer",
        "container",
        "page",
        "font-feature-values",
        "property",
        "counter-style",
        "charset",
        "scope",
        "starting-style",
    }
)
FORBIDDEN_FUNCTIONS = frozenset({"expression", "element", "-moz-element", "-webkit-element"})
URL_FUNCTIONS = frozenset({"url", "src"})
IMAGE_SET_FUNCTIONS = frozenset({"image-set", "-webkit-image-set"})
FORBIDDEN_PROPERTIES = frozenset({"behavior", "-ms-behavior", "-moz-binding", "binding"})
FORBIDDEN_SCHEMES = ("javascript:", "vbscript:", "livescript:")

MAX_CSS_BYTES = 1024 * 1024


@dataclass
class CssResult:
    css: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _contains_script_scheme(text: str) -> bool:
    lowered = "".join((text or "").split()).lower()
    return any(scheme in lowered for scheme in FORBIDDEN_SCHEMES)


def normalize_asset_url(raw: str, *, base_dir: str, assets: set[str]) -> tuple[str | None, str | None]:
    """``(ścieżka_kanoniczna, None)`` dla pliku paczki pod ``assets/`` albo ``(None, błąd)``."""
    value = (raw or "").strip()
    if not value:
        return None, "pusty adres w url()"
    lowered = value.lower()
    if lowered.startswith(("/", "\\")) or "//" in value or "\\" in value:
        return None, f"adres bezwzględny w url(): {value[:80]!r}"
    if ":" in value:
        return None, f"adres ze schematem w url(): {value[:80]!r}"
    path = value.split("#", 1)[0].split("?", 1)[0]
    joined = posixpath.normpath(posixpath.join(base_dir, path)) if base_dir else posixpath.normpath(path)
    if joined.startswith("../") or joined == ".." or not joined.startswith("assets/"):
        return None, f"url() spoza katalogu assets/ paczki: {value[:80]!r}"
    if joined not in assets:
        return None, f"url() wskazuje plik, którego nie ma w paczce: {joined!r}"
    return joined, None


def _url_token(value: str):
    """``url("…")`` z adresem, który przeszedł walidację (znaki ścieżki paczki albo ``#id``)."""
    from tinycss2 import ast

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return ast.URLToken(0, 0, value, f'url("{escaped}")')


class _Walker:
    def __init__(self, *, assets: set[str], base_dir: str, rewrite_prefix: str, svg_mode: bool):
        self.assets = assets
        self.base_dir = base_dir
        self.rewrite_prefix = rewrite_prefix
        self.svg_mode = svg_mode
        self.errors: list[str] = []

    def url(self, raw: str):
        if self.svg_mode:
            # W SVG dopuszczamy wyłącznie odwołania do elementów tego samego dokumentu (gradienty,
            # filtry, maski): ``url(#id)``. Plik zewnętrzny z SVG to droga do śledzenia wyświetleń.
            if raw.strip().startswith("#") and not _contains_script_scheme(raw):
                return _url_token(raw.strip())
            self.errors.append(f"url() w stylu SVG może wskazywać wyłącznie #element: {raw[:80]!r}")
            return None
        path, error = normalize_asset_url(raw, base_dir=self.base_dir, assets=self.assets)
        if error:
            self.errors.append(error)
            return None
        return _url_token(self.rewrite_prefix + path)

    def walk(self, nodes: list) -> list:
        out = []
        items = list(nodes or [])
        for index, node in enumerate(items):
            kind = node.type
            if kind == "error":
                self.errors.append(f"błąd składni CSS (wiersz {node.source_line}): {node.message}")
                continue
            if kind == "at-rule":
                keyword = node.lower_at_keyword
                if keyword == "import":
                    self.errors.append("@import jest niedozwolone – cały styl motywu musi być w theme.css.")
                    continue
                if keyword not in ALLOWED_AT_RULES:
                    self.errors.append(f"niedozwolona at-reguła @{keyword}.")
                    continue
                node.prelude = self.walk(node.prelude)
                if node.content is not None:
                    node.content = self.walk(node.content)
                out.append(node)
                continue
            if kind == "qualified-rule":
                node.prelude = self.walk(node.prelude)
                node.content = self.walk(node.content)
                out.append(node)
                continue
            if kind == "url":
                replacement = self.url(node.value)
                if replacement is not None:
                    out.append(replacement)
                continue
            if kind == "function":
                name = node.lower_name
                if name in FORBIDDEN_FUNCTIONS:
                    self.errors.append(f"funkcja {name}() jest niedozwolona.")
                    continue
                if name in URL_FUNCTIONS:
                    strings = [a for a in node.arguments if a.type == "string"]
                    others = [a for a in node.arguments if a.type not in ("string", "whitespace", "comment")]
                    if len(strings) != 1 or others:
                        self.errors.append(f"{name}() musi zawierać dokładnie jeden adres w cudzysłowie.")
                        continue
                    replacement = self.url(strings[0].value)
                    if replacement is not None:
                        out.append(replacement)
                    continue
                if name in IMAGE_SET_FUNCTIONS:
                    args = []
                    for arg in node.arguments:
                        if arg.type == "string":
                            replacement = self.url(arg.value)
                            if replacement is None:
                                continue
                            args.append(replacement)
                        else:
                            args.append(arg)
                    node.arguments = self.walk(args)
                    out.append(node)
                    continue
                node.arguments = self.walk(node.arguments)
                out.append(node)
                continue
            if kind in ("() block", "[] block", "{} block"):
                node.content = self.walk(node.content)
                out.append(node)
                continue
            if kind in ("string", "ident") and _contains_script_scheme(node.value):
                self.errors.append(f"niedozwolony schemat skryptu w CSS: {node.value[:60]!r}")
                continue
            if kind == "ident" and node.lower_value in FORBIDDEN_PROPERTIES:
                following = next(
                    (n for n in items[index + 1 :] if n.type not in ("whitespace", "comment")), None
                )
                if following is not None and following.type == "literal" and following.value == ":":
                    self.errors.append(f"właściwość {node.lower_value} jest niedozwolona.")
                    continue
            if kind == "comment":
                # Komentarze nie trafiają do wyniku: nie mają wpływu na wygląd, a bywają nośnikiem
                # sztuczek z rozbijaniem słów przy naiwnych filtrach dalej w łańcuchu.
                continue
            out.append(node)
        return out


def sanitize_css(
    text: str,
    *,
    assets: set[str] | None = None,
    base_dir: str = "",
    rewrite_prefix: str = "",
    svg_mode: bool = False,
) -> CssResult:
    """Sprawdza i przepisuje arkusz. ``errors`` niepuste = arkusz odrzucony (``css`` pusty)."""
    import tinycss2

    result = CssResult()
    if len(text.encode("utf-8")) > MAX_CSS_BYTES:
        result.errors.append(f"arkusz większy niż {MAX_CSS_BYTES // 1024} KB.")
        return result
    walker = _Walker(
        assets=assets or set(), base_dir=base_dir, rewrite_prefix=rewrite_prefix, svg_mode=svg_mode
    )
    rules = tinycss2.parse_stylesheet(text, skip_comments=False, skip_whitespace=False)
    cleaned = walker.walk(rules)
    result.errors = walker.errors
    if not result.errors:
        result.css = tinycss2.serialize(cleaned)
    return result


def sanitize_declarations(text: str, *, svg_mode: bool = True) -> CssResult:
    """To samo dla treści atrybutu ``style`` (lista deklaracji, bez selektorów)."""
    import tinycss2

    result = CssResult()
    walker = _Walker(assets=set(), base_dir="", rewrite_prefix="", svg_mode=svg_mode)
    tokens = tinycss2.parse_component_value_list(text, skip_comments=False)
    cleaned = walker.walk(tokens)
    result.errors = walker.errors
    if not result.errors:
        result.css = tinycss2.serialize(cleaned)
    return result
