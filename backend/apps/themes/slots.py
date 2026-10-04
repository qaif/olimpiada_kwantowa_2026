"""Sloty szablonów, które motyw może nadpisać (THEME-01 § 3), i reguły szablonów paczki.

``base.html`` aplikacji zostaje właścicielem ``<head>`` (CSP nonce, meta, skrypty, ciasteczka,
skip-link, landmarki) – motyw dostaje wyłącznie **fragmenty** wewnątrz. Każdy slot ma domyślną
treść w szablonie aplikacji ``templates/theme/<slot>.html`` (= wygląd ``classic``); paczka nadpisuje
go plikiem ``templates/theme/<slot>.html`` o tej samej nazwie. Panele i formularze nie mają slotów.

Lint szablonu paczki (wołany przy wgraniu **i** przy każdym załadowaniu szablonu przez loader):

- ``{% load %}`` wyłącznie bibliotek z ``ALLOWED_LIBRARIES``,
- bez ``{% extends %}`` (slot jest fragmentem), ``{% debug %}`` (wypisuje cały kontekst),
  ``{% autoescape off %}``, filtrów ``safe``/``safeseq`` (także przez ``{% filter %}``),
- ``{% include %}`` wyłącznie z nazwą w cudzysłowie: inny szablon paczki (``theme/…``), domyślny
  slot aplikacji pod aliasem ``classic/<slot>.html`` albo fragment aplikacji z listy prefiksów;
  zmienna w ``include`` jest niedozwolona (nie da się jej sprawdzić przy wgraniu),
- bez odwołań do sesji, haseł, nonce CSP, nagłówków i ciasteczek w wyrażeniach,
- HTML bez ``<script>``, ``<style>``, ``<link>``, ``<meta>``, ``<base>``, ramek i osadzeń,
  atrybutów ``on*``/``style``/``srcdoc``/``formaction`` i adresów ``javascript:``/``data:``.
  Motyw v1 nie dokłada JavaScriptu, a styl ma w jednym, sprawdzonym ``theme.css``. Formularze są
  dozwolone (nagłówek motywu rysuje przycisk „Wyloguj” – POST z ``{% csrf_token %}``): dokąd
  formularz może wysłać dane, rozstrzyga ``form-action`` w CSP, a nie szablon.
- tekst wewnątrz ``{% comment %}`` nie podlega regułom HTML (nie jest renderowany).
"""

from __future__ import annotations

import re

#: Sloty v1: nazwa → opis (panel, dokumentacja). Kolejność = kolejność na stronie.
SLOTS: dict[str, str] = {
    "header": "oba paski nagłówka: pasek konta (logo, język, konto) i menu serwisu",
    "brand": "logotyp i nazwa serwisu w pasku konta (wewnątrz domyślnego nagłówka)",
    "home_hero": "plansza powitalna strony głównej (slider)",
    "page_header": "nagłówek strony CMS (tytuł strony treści)",
    "news_card": "karta aktualności na liście aktualności",
    "page_wrapper": "opakowanie treści strony wewnątrz <main> (zmienna slot_content)",
    "footer": "stopka serwisu",
}

SLOT_TEMPLATES = frozenset(f"theme/{name}.html" for name in SLOTS)

#: Szablony pomocnicze paczki – dołączane przez sloty ``{% include "theme/partials/…" %}``.
PARTIAL_RE = re.compile(r"^theme/partials/[a-z0-9][a-z0-9_-]{0,40}\.html$")

ALLOWED_LIBRARIES = frozenset(
    {"static", "i18n", "wagtailcore_tags", "wagtailimages_tags", "cms_extras", "web_extras"}
)

#: Fragmenty aplikacji, które slot motywu może dołączyć (menu języków, link wsparcia, slider
#: sponsorów, ikony społecznościowe…). Prefiksy, a nie lista plików: nowy fragment ``web/_x.html``
#: nie wymaga zmiany tej listy, a panele (``web/coordinator/``) i formularze zostają poza zasięgiem.
ALLOWED_APP_INCLUDE_PREFIXES = ("cms/_", "web/_", "classic/")

FORBIDDEN_TAGS = frozenset({"extends", "debug", "ssi", "load_theme"})
FORBIDDEN_LOOKUPS = re.compile(
    r"\b(password|session|csp_nonce|META|COOKIES|headers|environ|secret\w*|_\w+|auth_token|token_key)\b"
)
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
SAFE_FILTER = re.compile(r"\|\s*(safe|safeseq)\b")
FORBIDDEN_HTML = re.compile(
    r"<\s*(script|style|link|meta|base|iframe|frame|frameset|object|embed|applet|foreignobject|portal)\b"
    r"|\son[a-z]+\s*="
    r"|\s(style|srcdoc|formaction)\s*="
    r"|javascript\s*:"
    r"|vbscript\s*:"
    r"|=\s*[\"']?\s*data\s*:",
    re.I,
)
MAX_TEMPLATE_BYTES = 256 * 1024


def is_package_template(name: str) -> bool:
    return name in SLOT_TEMPLATES or bool(PARTIAL_RE.match(name))


def lint_template(
    name: str, source: str, *, package_templates: set[str] | frozenset[str] = frozenset()
) -> list[str]:
    """Lista błędów szablonu paczki ``name`` (pusta = szablon dopuszczony)."""
    from django.template.base import Lexer, TokenType

    errors: list[str] = []
    if len(source.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        return [f"{name}: szablon większy niż {MAX_TEMPLATE_BYTES // 1024} KB."]
    in_comment = False
    for token in Lexer(source).tokenize():
        line = token.lineno
        if token.token_type == TokenType.BLOCK and token.contents.strip() in ("comment", "endcomment"):
            in_comment = token.contents.strip() == "comment"
            continue
        if in_comment:
            continue
        if token.token_type == TokenType.TEXT:
            match = FORBIDDEN_HTML.search(token.contents)
            if match:
                errors.append(f"{name}:{line}: niedozwolony fragment HTML {match.group(0).strip()!r}.")
            continue
        if token.token_type == TokenType.COMMENT:
            continue
        contents = token.contents.strip()
        # Napisy w cudzysłowach (``{% translate "…" %}``) nie są wyrażeniami – sprawdzamy resztę.
        expression = QUOTED.sub('""', contents)
        if SAFE_FILTER.search(expression):
            errors.append(f"{name}:{line}: filtr safe/safeseq jest niedozwolony.")
        if FORBIDDEN_LOOKUPS.search(expression):
            errors.append(f"{name}:{line}: odwołanie do danych niedostępnych dla motywu ({contents[:60]!r}).")
        if token.token_type != TokenType.BLOCK:
            continue
        bits = token.split_contents()
        if not bits:
            continue
        tag = bits[0]
        if tag in FORBIDDEN_TAGS:
            errors.append(f"{name}:{line}: znacznik {{% {tag} %}} jest niedozwolony w szablonie motywu.")
        elif tag == "load":
            libraries = bits[bits.index("from") + 1 :] if "from" in bits else bits[1:]
            for library in libraries:
                if library not in ALLOWED_LIBRARIES:
                    errors.append(f"{name}:{line}: biblioteka {library!r} spoza listy dozwolonej.")
        elif tag == "autoescape" and len(bits) > 1 and bits[1] == "off":
            errors.append(f"{name}:{line}: {{% autoescape off %}} jest niedozwolone.")
        elif tag == "filter" and any(re.search(r"\b(safe|safeseq)\b", bit) for bit in bits[1:]):
            errors.append(f"{name}:{line}: {{% filter safe %}} jest niedozwolone.")
        elif tag == "include":
            errors += _check_include(name, line, bits, package_templates)
    return errors


def _check_include(name: str, line: int, bits: list[str], package_templates) -> list[str]:
    if len(bits) < 2 or bits[1][:1] not in ("'", '"') or bits[1][-1:] != bits[1][:1]:
        return [f"{name}:{line}: {{% include %}} wymaga nazwy szablonu w cudzysłowie."]
    target = bits[1][1:-1]
    if ".." in target or target.startswith(("/", "\\")) or "\\" in target or ":" in target:
        return [f"{name}:{line}: niedozwolona nazwa szablonu w include: {target!r}."]
    if target.startswith("theme/"):
        if target not in package_templates:
            return [f"{name}:{line}: include {target!r} – brak takiego szablonu w paczce."]
        return []
    if not target.startswith(ALLOWED_APP_INCLUDE_PREFIXES):
        return [f"{name}:{line}: include {target!r} spoza szablonów motywu i fragmentów aplikacji."]
    from django.template import TemplateDoesNotExist, engines

    lookup = "theme/" + target[len("classic/") :] if target.startswith("classic/") else target
    try:
        engines["django"].engine.find_template(lookup)
    except TemplateDoesNotExist:
        return [f"{name}:{line}: include {target!r} – aplikacja nie ma takiego szablonu."]
    return []


def compile_check(name: str, source: str) -> list[str]:
    """Kompilacja silnikiem z **ograniczonym** zestawem bibliotek – składnia i ``{% load %}``."""
    from django.template import Engine, TemplateSyntaxError, engines

    main = engines["django"].engine
    restricted = Engine(
        libraries={lib: main.libraries[lib] for lib in ALLOWED_LIBRARIES if lib in main.libraries},
        builtins=None,
        autoescape=True,
        debug=False,
    )
    try:
        restricted.from_string(source)
    except TemplateSyntaxError as exc:
        return [f"{name}: błąd składni szablonu – {exc}"]
    except Exception as exc:  # noqa: BLE001 - każdy wyjątek kompilacji to odrzucony szablon
        return [f"{name}: szablon nie kompiluje się – {exc.__class__.__name__}: {exc}"]
    return []


def include_cycles(templates: dict[str, str]) -> list[str]:
    """Cykl ``include`` między szablonami paczki (slot dołącza sam siebie) – błąd przy wgraniu."""
    graph = {
        name: set(re.findall(r"\{%\s*include\s+[\"'](theme/[^\"']+)[\"']", source)) & set(templates)
        for name, source in templates.items()
    }
    errors: list[str] = []
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            errors.append("cykl include w szablonach motywu: " + " → ".join([*path, node]))
            return
        state[node] = 1
        for target in sorted(graph.get(node, ())):
            visit(target, [*path, node])
        state[node] = 2

    for node in sorted(graph):
        visit(node, [])
    return errors
