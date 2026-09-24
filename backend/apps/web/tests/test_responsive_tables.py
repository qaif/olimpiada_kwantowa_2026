"""Responsywne tabele: ramka przewijania, przyklejona kolumna, macierz i karty na telefonie.

Prośba organizatora z 25.09.2026: „Popraw responsywność, szczególnie w panelu koordynatora, jeśli
chodzi o tabele”. Audyt w przeglądarce (360–1280 px) pokazał trzy rodzaje usterek:

- tabela **wystawała** poza stronę – bez ramki (``/coordinator/support/``, komunikaty), przez
  ``.visually-hidden`` w komórce, liczone od początku dokumentu (przydziały, karta uczestnika,
  zadanie), przez niejawną kolumnę siatki (``/warsztaty/``) albo przez długi e-mail w ``<h1>``,
- tabela była **ściśnięta**: ``overflow-wrap: anywhere`` zerował minimalną szerokość kolumn, więc
  lista kont na 1024 px miała wiersze po 360 px (tekst łamany co kilka liter),
- macierz obecności na warsztatach miała 9500 px szerokości, bo nagłówki-tematy nie łamały się.

Wygląd sprawdzają oczy i ``e2e/check_mobile.py``; tu pilnujemy **umowy** (``docs/UI.md`` § 2.10):
kluczowe tabele stoją w ramce z nazwą regionu i przystankiem klawiatury, macierz i listy mają swoje
modyfikatory, karty mają etykiety w ``data-label``, a arkusz i skrypt zawierają to, na czym to stoi.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders

from apps.accounts.tests.factories import CoordinatorFactory
from apps.cms.tests.factories import AnnouncementFactory
from apps.core.models import audit
from apps.support.tests.factories import SupportTicketFactory
from apps.web.tests.test_coordinator_certificates import workshops_page  # noqa: F401 - fikstura harmonogramu
from apps.web.tests.test_ui_system import app_css, has_selector, media_block, occurrences, print_block

pytestmark = pytest.mark.django_db

#: Otwierający znacznik ramki razem z tabelą, która w niej stoi.
WRAPPER = re.compile(
    r'<div class="(?P<box>(?:scroll|table-scroll)[^"]*)"(?P<attrs>[^>]*)>\s*<table class="(?P<table>[^"]*)"'
)


def wrappers(html: str) -> list[dict[str, str]]:
    return [match.groupdict() for match in WRAPPER.finditer(html)]


def region(html: str, label: str) -> dict[str, str]:
    """Ramka tabeli o tej nazwie dostępnej – z kompletem atrybutów dla klawiatury i czytnika."""
    found = [item for item in wrappers(html) if f'aria-label="{label}"' in item["attrs"]]
    assert found, f"brak ramki tabeli „{label}”; są: {[item['attrs'] for item in wrappers(html)]}"
    item = found[0]
    assert 'role="region"' in item["attrs"], item
    assert 'tabindex="0"' in item["attrs"], item
    return item


def tbody_cells(html: str, table_class: str) -> list[str]:
    """Otwierające znaczniki ``<td>`` z ``tbody`` pierwszej tabeli o tej klasie."""
    start = html.index(f'<table class="{table_class}"')
    body = html[html.index("<tbody>", start) : html.index("</tbody>", start)]
    return re.findall(r"<td\b[^>]*>", body)


@pytest.fixture
def logged(web_client):
    web_client.force_login(CoordinatorFactory())
    return web_client


# --- szablony: kluczowe tabele panelu koordynatora -------------------------------------------------


def test_account_lists_scroll_with_a_sticky_first_column(logged, participant):
    everyone = logged.get("/coordinator/accounts/").content.decode()
    participants = logged.get("/coordinator/accounts/", {"role": "participant"}).content.decode()

    accounts = region(everyone, "Konta")
    listing = region(participants, "Uczestnicy – lista kont")

    for item in (accounts, listing):
        assert "table--sticky-first" in item["table"]
        # Długa lista: nagłówek kolumn z sortowaniem zostaje nad wierszami.
        assert "scroll--tall" in item["box"]


@pytest.mark.usefixtures("workshops_page")
def test_workshop_attendance_is_a_matrix_with_a_sticky_participant_column(logged, entry):
    html = logged.get("/coordinator/workshops/attendance/").content.decode()

    item = region(html, "Obecność uczestników na warsztatach")

    assert "table--matrix" in item["table"]
    assert "table--sticky-first" in item["table"]


def test_audit_log_turns_into_cards_on_a_phone(logged, participant):
    actor = CoordinatorFactory()
    audit(actor, "account.update", participant.user, diff={"email": ["a@example.test", "b@example.test"]})

    html = logged.get("/coordinator/audit/").content.decode()

    item = region(html, "Dziennik zdarzeń")
    assert "table--stack" in item["table"]
    cells = tbody_cells(html, item["table"])
    assert cells, "dziennik bez wierszy – test nic nie sprawdza"
    # Każda komórka karty ma etykietę; nazwa wiersza (akcja) jest ``th`` i tytułem karty.
    unlabelled = [cell for cell in cells if "data-label=" not in cell and 'class="empty"' not in cell]
    assert not unlabelled, unlabelled


def test_support_list_is_in_a_frame_and_stacks(logged):
    SupportTicketFactory(subject="Nie da się wgrać pliku PDF z telefonu")

    html = logged.get("/coordinator/support/").content.decode()

    item = region(html, "Zgłoszenia problemów")
    assert "table--stack" in item["table"]
    assert '<th scope="row"><a href="/coordinator/support/' in html
    unlabelled = [cell for cell in tbody_cells(html, item["table"]) if "data-label=" not in cell]
    assert not unlabelled, unlabelled


def test_announcements_list_is_in_a_frame_and_stacks(logged):
    AnnouncementFactory(text="Przerwa techniczna w sobotę od 22:00")

    html = logged.get("/coordinator/announcements/").content.decode()

    item = region(html, "Komunikaty w serwisie")
    assert "table--stack" in item["table"]
    unlabelled = [cell for cell in tbody_cells(html, item["table"]) if "data-label=" not in cell]
    assert not unlabelled, unlabelled


def test_broadcast_history_is_in_a_labelled_frame(logged):
    html = logged.get("/coordinator/messages/").content.decode()

    assert "table--stack" in region(html, "Wysłane komunikaty")["table"]


def test_committee_list_scrolls_with_a_sticky_first_column(logged):
    html = logged.get("/coordinator/members/").content.decode()

    assert "table--sticky-first" in region(html, "Członkowie komitetu")["table"]


def test_assignments_tables_are_labelled_regions(logged, entry):
    html = logged.get(f"/coordinator/stages/{entry.stage.pk}/assignments/").content.decode()

    region(html, "Zadania etapu i reguły przydziału")
    assert "table--wide" in region(html, "Prace etapu, recenzenci i oceny")["table"]


def test_no_coordinator_table_outside_a_frame(logged, participant):
    """Tabela bez ramki to poziomy suwak całej strony na telefonie (UI.md § 4)."""
    SupportTicketFactory()
    AnnouncementFactory()
    for url in ("/coordinator/support/", "/coordinator/announcements/", "/coordinator/accounts/"):
        html = logged.get(url).content.decode()
        tables = len(re.findall(r"<table\b", html))
        framed = len(wrappers(html))
        assert tables == framed, f"{url}: tabel {tables}, w ramce {framed}"


# --- skrypt ramki -------------------------------------------------------------------------------


def test_table_scroll_script_is_loaded_with_a_nonce(web_client):
    html = web_client.get("/login/").content.decode()

    tag = re.search(r"<script[^>]*js/table-scroll[^>]*>", html)
    assert tag, "base.html nie ładuje js/table-scroll.js"
    assert "nonce=" in tag.group(0)
    assert "defer" in tag.group(0)


def test_table_scroll_script_only_adds_a_tab_stop_where_it_scrolls():
    path = finders.find("js/table-scroll.js")
    assert path, "staticfiles nie znajduje js/table-scroll.js"
    source = Path(path).read_text(encoding="utf-8")

    # Przystanek klawiatury tylko przy przewijaniu i zdejmowany wyłącznie wtedy, gdy dał go skrypt.
    assert "scrollWidth - el.clientWidth" in source
    assert "data-scroll-tabindex" in source
    assert 'setAttribute("role", "region")' in source
    assert 'setAttribute("data-fit", "wide")' in source
    assert "htmx:afterSettle" in source


# --- arkusz -------------------------------------------------------------------------------------

TABLE_COMPONENTS = [
    ".table-scroll",
    ".table--sticky-first",
    ".table--sticky-rank",
    ".table--matrix",
    ".table--stack",
    ".table--wide",
    ".nowrap",
]


@pytest.mark.parametrize("selector", TABLE_COMPONENTS)
def test_table_component_is_defined(selector):
    assert has_selector(app_css(), selector), f"{selector} nie jest zdefiniowany w app.css"


def test_frame_is_the_containing_block_of_hidden_labels():
    """``.visually-hidden`` w komórce liczy się od ramki, a nie od dokumentu – inaczej poziomy suwak."""
    css = app_css()
    rule = css.split(".scroll,\n.table-scroll {", 1)[1].split("}", 1)[0]

    assert "position: relative;" in rule
    assert "overflow-x: auto;" in rule


def test_cells_in_a_frame_do_not_collapse_to_single_letters():
    """W ramce kolumna nie schodzi poniżej najdłuższego słowa (``break-word``), a ciąg bez spacji ma sufit."""
    css = app_css()
    rule = css.split(".table-scroll > .table tbody th {", 1)[1].split("}", 1)[0]

    assert "overflow-wrap: break-word;" in rule
    assert "max-width: var(--cell-max" in rule


def test_row_headers_are_not_sticky_column_headers():
    """``position: sticky; top: 0`` dotyczy nagłówka kolumn, a nie ``th scope="row"`` w treści."""
    css = app_css()
    sticky = css.split(".table thead th,\n.grid thead th {", 1)[1].split("}", 1)[0]
    assert "position: sticky;" in sticky
    base = css.split(".table th,\n.grid th {", 1)[1].split("}", 1)[0]
    assert "position: sticky" not in base


def test_stacked_cards_take_labels_from_data_label():
    css = app_css()
    blocks = [media_block(css, start) for start in occurrences(css, "@media (max-width: 639.98px)")]
    stack = [block for block in blocks if ".table--stack tbody tr {" in block]

    assert stack, "brak układu kart dla .table--stack poniżej 640 px"
    assert "content: attr(data-label);" in stack[0]
    # Nagłówek kolumn znika z oczu, ale nie z drzewa dostępności.
    assert "clip: rect(0 0 0 0);" in stack[0]


def test_scroll_hint_fades_edges_by_state():
    css = app_css()
    for state in ("start", "middle", "end"):
        assert f'[data-scroll="{state}"]' in css, f"brak podpowiedzi przewijania dla {state}"
    assert "mask-image" in css


def test_print_drops_the_scroll_hint_and_sticky_columns():
    block = print_block()

    assert "mask-image: none !important;" in block
    assert ".table--sticky-first tr > :first-child" in block
    assert "width: 100% !important;" in block


def test_high_contrast_keeps_the_frame_focus_ring_inside():
    css = app_css()
    marker = ':root[data-contrast="high"] :is(.scroll, .table-scroll):is(:focus, :focus-visible)'

    assert marker in css
    assert "outline-offset: -4px;" in css.split(marker, 1)[1].split("}", 1)[0]
