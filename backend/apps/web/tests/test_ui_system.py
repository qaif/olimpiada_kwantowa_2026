"""Kontrakt systemu interfejsu: szkielet dostępności w ``base.html`` i komponenty w ``app.css``.

Testy nie sprawdzają wyglądu – od tego są oczy i ``e2e/check_mobile.py``. Sprawdzają **umowę**,
którą opisuje ``docs/UI.md`` i na której opiera się sześć paneli pisanych osobno:

  - strona bazowa daje skip link, landmarki i nazwane nawigacje,
  - arkusz jest serwowany i zawiera komponenty, do których odsyłają szablony paneli,
  - tryb wysokiego kontrastu obejmuje **także** komponenty dopisane po jego powstaniu.

Ostatni punkt jest tu najważniejszy i jest jedynym powodem, dla którego ten plik w ogóle istnieje.
Tryb kontrastu nadpisuje tokeny, więc nowy komponent zbudowany na tokenach obsługuje się sam –
ale stan „wybrany” (czip filtru, aktywna zakładka, bieżąca pozycja nawigacji panelu) nie jest
tokenem, tylko różnicą dwóch odcieni. Po sprowadzeniu palety do czerni i bieli taka różnica znika
bez śladu i **nic tego nie zauważy**: strona się renderuje, testy widoków przechodzą, a człowiek
z tym trybem po prostu nie wie, który filtr jest włączony. Stąd asercje na treść arkusza.

Arkusz czytamy przez ``finders`` (źródło w ``static/``), a nie przez ``staticfiles_storage``:
w konfiguracji testowej nie ma zebranego ``STATIC_ROOT``, a i tak interesuje nas to, co napisał
człowiek, a nie wynik hashowania nazw przez WhiteNoise.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders

pytestmark = pytest.mark.django_db


@lru_cache(maxsize=1)
def app_css() -> str:
    """Treść ``static/css/app.css``. Cache, bo plik ma ponad 5000 wierszy, a testów jest kilkanaście."""
    path = finders.find("css/app.css")
    assert path, "staticfiles nie znajduje css/app.css"
    return Path(path).read_text(encoding="utf-8")


def has_selector(css: str, selector: str) -> bool:
    """Czy arkusz **definiuje** ten selektor (a nie tylko wspomina o nim w komentarzu).

    Szukamy nazwy klasy w pozycji, w której zaczyna się selektor: na początku wiersza albo po
    przecinku/spacji w liście selektorów, zakończonej ``{``, ``,`` albo dalszą częścią selektora.
    Nazwa w komentarzu (``/* … .chip … */``) stoi w wierszu zaczynającym się od gwiazdki lub
    ukośnika i przez to nie pasuje.
    """
    pattern = re.compile(
        r"^[^/*\n]*(?<![\w-])" + re.escape(selector) + r"(?![\w-])[^{}\n]*[{,]$",
        re.MULTILINE,
    )
    return bool(pattern.search(css))


def occurrences(css: str, needle: str) -> list[int]:
    """Pozycje wszystkich wystąpień (``str.index`` daje tylko pierwsze, a bloków bywa kilka)."""
    found, start = [], 0
    while (position := css.find(needle, start)) != -1:
        found.append(position)
        start = position + 1
    return found


def media_block(css: str, start: int) -> str:
    """Treść bloku ``@media`` od podanej pozycji, wycięta po nawiasach klamrowych.

    Wycinanie „na sztywno” N znaków było w pierwszej wersji tego pliku źródłem fałszywej
    porażki: blok wydruku urósł i asercja przestała widzieć jego drugą połowę. Licznik klamr
    nie ma tego problemu, a arkusz nie zawiera klamr w łańcuchach znaków ani w ``content``
    z nawiasem, więc prosty licznik wystarczy.
    """
    depth, index = 0, css.index("{", start)
    opening = index
    while index < len(css):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[opening : index + 1]
        index += 1
    raise AssertionError("niedomknięty blok @media w app.css")


# --- szkielet dostępności w base.html ----------------------------------------------------------


def test_base_renders_skip_link_first(web_client):
    """Skip link jest pierwszym elementem ``<body>`` i prowadzi do identyfikatora treści."""
    html = web_client.get("/login/").content.decode()
    assert '<a class="skip-link" href="#tresc">' in html
    body_start = html.index("<body")
    body_open_end = html.index(">", body_start)
    # Między znacznikiem ``<body>`` a skip linkiem nie ma żadnej innej treści: skip link musi być
    # pierwszym przystankiem klawisza Tab, inaczej nie jest skrótem tylko ozdobą.
    assert html[body_open_end + 1 : body_open_end + 40].strip().startswith('<a class="skip-link"')


def test_base_has_landmarks(web_client):
    """Nagłówek, stopka i treść są landmarkami, a cel skip linku istnieje."""
    html = web_client.get("/login/").content.decode()
    assert "<header class=" in html
    assert "<footer class=" in html
    assert 'id="tresc"' in html
    assert "<main " in html
    # Cel skip linku to ``<main>``, a nie przypadkowy ``<div>`` o tym samym identyfikatorze.
    main_tag = html[html.index("<main ") : html.index(">", html.index("<main ")) + 1]
    assert 'id="tresc"' in main_tag


def test_every_nav_is_named(web_client):
    """Każda ``<nav>`` ma nazwę – na stronie są cztery i lista landmarków bez nazw jest bezużyteczna."""
    html = web_client.get("/login/").content.decode()
    navs = re.findall(r"<nav\b[^>]*>", html)
    assert navs, "strona bazowa nie ma zadnej nawigacji"
    unnamed = [tag for tag in navs if "aria-label" not in tag and "aria-labelledby" not in tag]
    assert not unnamed, f"nawigacje bez nazwy: {unnamed}"


def test_page_has_exactly_one_h1(web_client):
    html = web_client.get("/login/").content.decode()
    assert len(re.findall(r"<h1\b", html)) == 1


# --- stopka --------------------------------------------------------------------------------------


def test_footer_says_the_version_and_nothing_else(web_client):
    """Ostatni wiersz stopki to sama wersja wydania – „wersja <tag>”, bez zdania o strefie czasu.

    Numer musi zostać **dynamiczny**: to po niego sięga zgłaszający błąd, a stopka z wpisanym na
    sztywno numerem kłamałaby przy pierwszym wdrożeniu. Zdanie „wszystkie godziny podajemy
    w czasie polskim” zdjął organizator 15.09 – godziny opisuje harmonogram, a każdą datę panel
    i tak renderuje w strefie organizatora.
    """
    from apps.web import context_processors

    html = web_client.get("/login/").content.decode()

    assert f"wersja {context_processors.APP_VERSION}" in html
    assert "Wersja aplikacji" not in html
    assert "czasie polskim" not in html


def test_footer_separator_does_not_leak_into_the_button_underline():
    """Kropka rozdzielająca przed „Ustawieniami cookies” jest poza podkreśleniem przycisku.

    ``.footer__link-button`` jest podkreślony, a jego ``::before`` stoi **wewnątrz** przycisku –
    bez własnego ``display: inline-block`` podkreślenie obejmowało kropkę razem z jej marginesem
    i rysowało przed tekstem kreskę, którą organizator zgłosił 15.09 jako zabłąkany znak „_”.
    """
    css = app_css()
    rule = css.split(".footer__links a + a::before,", 1)[1].split("}", 1)[0]

    assert "display: inline-block;" in rule
    assert "text-decoration: none;" in rule


def test_stylesheet_is_linked(web_client):
    html = web_client.get("/login/").content.decode()
    assert "css/app.css" in html


def test_stylesheet_is_served(client):
    """Arkusz jest serwowany pod swoim adresem (WhiteNoise/staticfiles w trybie deweloperskim)."""
    response = client.get("/static/css/app.css")
    assert response.status_code == 200


# --- komponenty w arkuszu ----------------------------------------------------------------------

#: Komponenty opisane w ``docs/UI.md``. Lista jest kontraktem z autorami szablonów paneli: jeżeli
#: coś stąd zniknie z arkusza, przestanie działać w trzech panelach naraz i nikt się nie dowie,
#: dopóki ktoś nie otworzy strony.
COMPONENTS = [
    ".btn",
    ".btn--primary",
    ".btn--secondary",
    ".btn--danger",
    ".btn--small",
    ".badge",
    ".badge--ok",
    ".badge--warn",
    ".badge--danger",
    ".badge--neutral",
    ".badge--accent",
    ".card",
    ".card--quiet",
    ".section",
    ".section__head",
    ".page-head",
    ".eyebrow",
    ".lead",
    ".hint",
    ".actions",
    ".table",
    ".scroll",
    ".scroll--tall",
    ".empty",
    ".empty__title",
    ".empty__text",
    ".empty__actions",
    ".stack",
    ".chip",
    ".chips",
    ".chip__count",
    ".tabs",
    ".tab",
    ".tab--active",
    ".kpi",
    ".kpi__value",
    ".kpi__label",
    ".details",
    ".details__summary",
    ".details__body",
    ".form",
    ".field",
    ".field__error",
    ".field__hint",
    ".layout-2col",
    ".layout-2col__main",
    ".layout-2col__aside",
    ".panel",
    ".panel__nav",
    ".panel__main",
    ".panel__nav-link",
    ".bulk-bar",
    ".bulk-bar__count",
    ".bulk-bar__actions",
    ".num",
    ".visually-hidden",
    ".skip-link",
    ".print-only",
]


@pytest.mark.parametrize("selector", COMPONENTS)
def test_component_is_defined(selector):
    assert has_selector(app_css(), selector), f"{selector} nie jest zdefiniowany w app.css"


def test_tokens_cover_the_scale():
    """Skala odstępów i role kolorów są tokenami – arkusze panelowe nie mają czego wpisywać z ręki."""
    css = app_css()
    for token in ("--sp-1", "--sp-2", "--sp-3", "--sp-4", "--sp-6", "--sp-8"):
        assert f"{token}:" in css, f"brak tokenu odstepu {token}"
    for token in (
        "--ink",
        "--muted",
        "--surface",
        "--line",
        "--accent",
        "--ok",
        "--warn",
        "--danger",
        "--info",
        "--focus",
    ):
        assert f"{token}:" in css, f"brak tokenu koloru {token}"


def test_hidden_attribute_reset_is_kept():
    """``[hidden]`` musi chować mimo reguł układu – na tym stoi ukryty pasek zaznaczenia."""
    css = app_css()
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", css)


def test_focus_visible_ring_is_global():
    css = app_css()
    assert re.search(r"^:focus-visible\s*\{", css, re.MULTILINE)
    assert "outline: 3px solid var(--focus)" in css


def test_reduced_motion_is_respected():
    """Globalne wyciszenie animacji. Nowa animacja nie potrzebuje wtedy własnej reguły.

    Blok z ``prefers-reduced-motion`` jest w arkuszu kilka razy (linia czasu i pasek przyklejony
    mają własne), więc szukamy tego jednego, który celuje w ``*`` – tylko on obejmuje komponenty,
    których jeszcze nie ma.
    """
    css = app_css()
    assert "@media (prefers-reduced-motion: reduce)" in css
    globals_ = [
        media_block(css, start) for start in occurrences(css, "@media (prefers-reduced-motion: reduce)")
    ]
    universal = [block for block in globals_ if "animation-duration: 0.01ms !important" in block]
    assert universal, "brak globalnego wyciszenia animacji"
    assert "transition-duration: 0.01ms !important" in universal[0]


# --- tryb wysokiego kontrastu ------------------------------------------------------------------

#: Komponenty, których stan „wybrany” tryb kontrastu musi obsłużyć osobno – tokeny tego nie
#: załatwiają, bo różnicą stanu nie jest rola koloru, tylko odcień.
HIGH_CONTRAST_COMPONENTS = [".chip", ".tab", ".panel__nav-link", ".details", ".bulk-bar", ".kpi__item"]


def high_contrast_block() -> str:
    css = app_css()
    marker = ':root[data-contrast="high"]'
    assert marker in css, "brak bloku trybu wysokiego kontrastu"
    return css[css.index(marker) :]


@pytest.mark.parametrize("selector", HIGH_CONTRAST_COMPONENTS)
def test_high_contrast_covers_new_components(selector):
    block = high_contrast_block()
    assert selector in block, f"tryb wysokiego kontrastu nie obejmuje {selector}"


def test_high_contrast_marks_selected_state():
    """Stan wybrany musi być odróżnialny bez koloru – w tej palecie przez odwrócenie kontrastu."""
    block = high_contrast_block()
    assert ".chip[aria-current]" in block
    assert ".panel__nav-link[aria-current]" in block
    assert '.tab[aria-selected="true"]' in block or ".tab--active" in block


def test_high_contrast_overrides_tokens_not_components():
    """Tryb nadpisuje tokeny – dzięki temu obejmuje też arkusze dokładane per ekran."""
    block = high_contrast_block()
    for token in ("--bg:", "--ink:", "--line:", "--accent:", "--focus:"):
        assert token in block, f"tryb kontrastu nie nadpisuje {token}"


def test_high_contrast_invalid_field_is_marked():
    block = high_contrast_block()
    assert 'aria-invalid="true"' in block


# --- wydruk ------------------------------------------------------------------------------------


def print_block() -> str:
    """Wszystko, co arkusz robi na papierze – sklejone bloki ``@media print``.

    Bloków jest w arkuszu więcej niż jeden (komponenty dopisywane osobno mają własne), a wzmianka
    „patrz sekcja @media print” w komentarzu blokiem nie jest – stąd wyszukiwanie po ``{``
    i sklejanie wszystkich trafień zamiast brania pierwszego.
    """
    css = app_css()
    starts = [match.start() for match in re.finditer(r"@media print\s*\{", css)]
    assert starts, "brak sekcji @media print"
    return "\n".join(media_block(css, start) for start in starts)


def test_print_unfreezes_scrollers():
    """Na papierze ramka przewijalna obcina wiersze, a przyklejony nagłówek gubi kolejne strony."""
    block = print_block()
    assert ".scroll" in block
    assert "overflow: visible !important" in block
    assert "display: table-header-group" in block


def test_print_hides_panel_controls():
    """Filtry, zakładki i pasek zaznaczenia na wydruku są kłamstwem: wyglądają jak treść."""
    block = print_block()
    for selector in (".chips", ".tabs", ".panel__nav", ".bulk-bar", ".no-print"):
        assert selector in block, f"{selector} nie jest chowany na wydruku"


def test_print_keeps_badge_meaning_without_background():
    block = print_block()
    assert ".badge" in block
    assert "border: 1px solid #000 !important" in block


def test_print_only_helper_is_hidden_on_screen():
    css = app_css()
    assert re.search(r"^\.print-only\s*\{\s*display:\s*none;\s*\}", css, re.MULTILINE)
