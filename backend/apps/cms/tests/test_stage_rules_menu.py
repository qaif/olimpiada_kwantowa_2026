"""„Zasady Organizacji Zawodów” etapów w menu „Dokumenty” (ZOZ-02).

ZOZ nie jest stroną Wagtaila, tylko dokumentem etapu (``apps.competitions.stage_rules``), więc
lista rozwijana „Dokumenty” dokłada go poza drzewem stron (``apps.cms.context_processors``).
Pilnujemy tego, co przy takim doklejaniu psuje się najciszej:

- **pozycja istnieje wyłącznie przy wersji opublikowanej** – projekt jej nie daje, wycofanie ją
  zdejmuje (także z zapisanej strony w ``apps.web.page_cache``),
- **kolejność**: zaraz za Regulaminem (§ 1 ust. 4 odsyła do ZOZ), etapy w kolejności terminów,
- **izolacja konkursów** – ZOZ jednego konkursu nie trafia do menu drugiego, także przez pamięć
  podręczną,
- **koszt**: na zimno jedno zapytanie, na ciepło żadnego (menu stoi na każdej stronie),
- **wyróżnienie**: lista „Dokumenty” i pozycja etapu są aktywne na stronach ``/dokumenty/zasady/…``.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation
from wagtail.models import Page

from apps.cms.context_processors import stage_rules_entries
from apps.cms.models import DocumentIndexPage, DocumentPage, HomePage
from apps.competitions.models import StageKind
from apps.competitions.stage_rules import create_rules, publish_rules, withdraw_rules
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory

pytestmark = pytest.mark.django_db

BODY = '<h2 id="par-1">§ 1. Harmonogram</h2><p>Terminy.</p>'
DOCUMENTS = [("Regulamin", "regulamin"), ("Polityka RODO", "rodo"), ("Zgoda opiekuna", "zgoda-opiekuna")]


def _documents_section(home) -> DocumentIndexPage:
    """Sekcja ``/dokumenty/`` z trzema dokumentami w menu – Regulamin pierwszy, jak na produkcji."""
    index = DocumentIndexPage.objects.child_of(home).first()
    if index is None:
        index = home.add_child(
            instance=DocumentIndexPage(title="Dokumenty", slug="dokumenty", show_in_menus=True)
        )
    for title, slug in DOCUMENTS:
        if not Page.objects.child_of(index).filter(slug=slug).exists():
            index.add_child(instance=DocumentPage(title=title, slug=slug))
    return index


@pytest.fixture
def documents(competition) -> DocumentIndexPage:
    return _documents_section(HomePage.objects.get(pk=competition.site.root_page_id))


@pytest.fixture
def stages(competition):
    """Dwa etapy bieżącej edycji – drugi założony **pierwszy**, żeby kolejność szła po terminach."""
    edition = CurrentEditionFactory(competition=competition)
    now = timezone.now()
    second = StageFactory(
        edition=edition, kind=StageKind.DISTRICT, name="Etap II", opens_at=now + timedelta(days=60)
    )
    first = StageFactory(
        edition=edition, kind=StageKind.ELIM, name="Etap I", opens_at=now - timedelta(days=1)
    )
    return first, second


@pytest.fixture
def other_home(other_competition) -> HomePage:
    """Drzewo stron drugiego konkursu (jak ``test_competition_scope.other_home``) z sekcją dokumentów."""
    tree_root = Page.objects.filter(depth=1).order_by("path").first()
    home = tree_root.add_child(instance=HomePage(title="Druga olimpiada", slug="druga-olimpiada"))
    site = other_competition.site
    site.root_page = home
    site.save()
    _documents_section(home)
    return home


def _draft(stage, version="1.0"):
    return create_rules(stage, None, version=version, document_date=date(2026, 11, 12), body_html=BODY)


def _published(stage, version="1.0"):
    return publish_rules(_draft(stage, version), None)


def _documents_item(menu) -> dict:
    return next(item for item in menu if item["slug"] == "dokumenty")


def _children(client, path="/") -> list[tuple[str, str]]:
    item = _documents_item(client.get(path).context["cms_menu"])
    return [(child["title"], child["url"]) for child in item["children"]]


def test_published_rules_appear_right_after_the_regulations_in_stage_order(
    client_for, competition, documents, stages
):
    first, second = stages
    _published(second)
    _published(first)

    assert _children(client_for(competition)) == [
        ("Regulamin", "/dokumenty/regulamin/"),
        ("Zasady Organizacji Zawodów – Etap I", f"/dokumenty/zasady/{first.pk}/"),
        ("Zasady Organizacji Zawodów – Etap II", f"/dokumenty/zasady/{second.pk}/"),
        ("Polityka RODO", "/dokumenty/rodo/"),
        ("Zgoda opiekuna", "/dokumenty/zgoda-opiekuna/"),
    ]


def test_draft_gives_no_item_and_withdrawal_removes_it(client_for, competition, documents, stages):
    first, _second = stages
    client = client_for(competition)
    draft = _draft(first)
    assert not [title for title, _url in _children(client) if title.startswith("Zasady")]

    publish_rules(draft, None)
    assert ("Zasady Organizacji Zawodów – Etap I", f"/dokumenty/zasady/{first.pk}/") in _children(client)

    withdraw_rules(draft, None)
    assert not [title for title, _url in _children(client) if title.startswith("Zasady")]


def test_training_stage_and_previous_edition_have_no_item(competition, stages):
    first, _second = stages
    training = StageFactory(edition=first.edition, kind=StageKind.TRAINING, name="Trening")
    _published(training)
    old_edition_stage = StageFactory(competition=competition, kind=StageKind.ELIM, name="Etap I 2025")
    old_edition_stage.edition.is_current = False
    old_edition_stage.edition.save()
    _published(old_edition_stage)

    assert stage_rules_entries(competition) == []


def test_stage_without_a_name_is_labelled_by_its_kind(competition):
    stage = StageFactory(
        edition=CurrentEditionFactory(competition=competition), kind=StageKind.FINAL, name=""
    )
    _published(stage)

    assert [entry["title"] for entry in stage_rules_entries(competition)] == [
        "Zasady Organizacji Zawodów – Finał"
    ]


def test_the_label_follows_the_interface_language(competition, stages):
    _published(stages[0])

    with translation.override("en"):
        assert stage_rules_entries(competition)[0]["title"] == "Competition Rules – Etap I"


def test_section_without_the_regulations_gets_the_rules_at_the_end(
    client_for, competition, documents, stages
):
    Page.objects.child_of(documents).filter(slug="regulamin").delete()
    _published(stages[0])

    titles = [title for title, _url in _children(client_for(competition))]
    assert titles == ["Polityka RODO", "Zgoda opiekuna", "Zasady Organizacji Zawodów – Etap I"]


def test_rules_page_highlights_the_documents_menu_and_its_own_item(
    client_for, competition, documents, stages
):
    first, second = stages
    _published(first, "1.0")
    _published(first, "1.1")
    _published(second)
    client = client_for(competition)

    for path in (f"/dokumenty/zasady/{first.pk}/", f"/dokumenty/zasady/{first.pk}/wersja/1.0/"):
        response = client.get(path)
        assert response.status_code == 200
        item = _documents_item(response.context["cms_menu"])
        assert item["active"] is True
        assert [child["title"] for child in item["children"] if child["active"]] == [
            "Zasady Organizacji Zawodów – Etap I"
        ]
        nav = response.content.decode().split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]
        assert "nav-menu__summary nav-menu__summary--active" in nav
        assert re.search(rf'href="/dokumenty/zasady/{first.pk}/"\s+aria-current="page"', nav)
        assert not re.search(rf'href="/dokumenty/zasady/{second.pk}/"\s+aria-current', nav)


def test_rules_of_one_competition_never_reach_the_menu_of_another(
    client_for, competition, other_competition, documents, other_home, stages
):
    _published(stages[0])
    other_stage = StageFactory(
        edition=CurrentEditionFactory(competition=other_competition), name="Runda obca", kind=StageKind.ELIM
    )
    _published(other_stage)

    own = _children(client_for(competition))
    other = _children(client_for(other_competition))

    assert ("Zasady Organizacji Zawodów – Etap I", f"/dokumenty/zasady/{stages[0].pk}/") in own
    assert not [title for title, _url in own if "Runda obca" in title]
    assert ("Zasady Organizacji Zawodów – Runda obca", f"/dokumenty/zasady/{other_stage.pk}/") in other
    assert not [title for title, _url in other if "Etap I" in title]


def test_one_query_when_cold_and_none_when_warm(competition, stages):
    _published(stages[0])

    with CaptureQueriesContext(connection) as cold:
        stage_rules_entries(competition)
    with CaptureQueriesContext(connection) as warm:
        entries = stage_rules_entries(competition)

    assert len(cold.captured_queries) == 1
    assert len(warm.captured_queries) == 0
    assert [entry["title"] for entry in entries] == ["Zasady Organizacji Zawodów – Etap I"]


def test_renaming_a_stage_and_switching_the_edition_refresh_the_cached_items(competition, stages):
    first, _second = stages
    _published(first)
    assert [entry["title"] for entry in stage_rules_entries(competition)] == [
        "Zasady Organizacji Zawodów – Etap I"
    ]

    first.name = "Etap pierwszy"
    first.save()
    assert [entry["title"] for entry in stage_rules_entries(competition)] == [
        "Zasady Organizacji Zawodów – Etap pierwszy"
    ]

    first.edition.is_current = False
    first.edition.save()
    assert stage_rules_entries(competition) == []


def test_publication_and_withdrawal_invalidate_the_saved_page(
    client_for, competition, documents, stages, settings
):
    """Strona zapisana w ``apps.web.page_cache`` nie może trzymać menu sprzed publikacji/wycofania."""
    settings.PAGE_CACHE_ENABLED = True
    client = client_for(competition)
    rules = _draft(stages[0])

    assert client.get("/")["X-Page-Cache"] == "MISS"
    assert client.get("/")["X-Page-Cache"] == "HIT"

    publish_rules(rules, None)
    response = client.get("/")
    assert response["X-Page-Cache"] == "MISS"
    assert "Zasady Organizacji Zawodów – Etap I" in response.content.decode()
    assert client.get("/")["X-Page-Cache"] == "HIT"

    withdraw_rules(rules, None)
    response = client.get("/")
    assert response["X-Page-Cache"] == "MISS"
    assert "Zasady Organizacji Zawodów – Etap I" not in response.content.decode()
