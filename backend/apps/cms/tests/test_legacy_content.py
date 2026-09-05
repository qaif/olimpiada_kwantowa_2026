"""Import treści starej strony: ``seed_legacy_content`` i ``seed_edition_kwantowa``.

Testy pilnują trzech rzeczy, na których ten import stoi:

- **co jest publiczne, a co nie.** „Komitety” to szesnaście nazwisk niepotwierdzonych przez
  organizatora, „Partnerzy” sugerują patronaty, których może nie być – obie strony mają zostać
  szkicem, czyli odpowiadać 404 pod publicznym adresem,
- **kolejność menu.** Menu wynika z kolejności rodzeństwa w drzewie, a nie z pola sortującego,
  więc pomyłka w komendzie objawia się dopiero w nagłówku strony,
- **oś czasu edycji.** Stara strona ma po jednej dacie na etap; reguły uzupełniające resztę są
  zapisane w komendzie i tu sprawdzane, żeby ich cicha zmiana nie przeszła bez śladu.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command

from apps.cms.models import ContentPage, DocumentPage, HomePage, NewsPage
from apps.competitions.management.commands.seed_edition_kwantowa import EDITION_LABEL, MIN_POINTS
from apps.competitions.models import Edition, QualificationMode, Stage, StageKind
from apps.competitions.tests.factories import CurrentEditionFactory

pytestmark = pytest.mark.django_db

PUBLISHED_CONTENT = ("o-olimpiadzie", "jak-zaczac", "harmonogram", "kontakt", "dla-nauczycieli")
DRAFT_CONTENT = ("komitety", "partnerzy")
DOCUMENTS = ("rodo", "standardy-ochrony-maloletnich")
DEMO_NOTICE_FRAGMENT = "Wersja demonstracyjna"

MENU_WITHOUT_REGULAMIN = [
    "O Olimpiadzie",
    "Jak zacząć?",
    "Aktualności",
    "Zadania",
    "Terminarz i harmonogram",
    "Archiwum",
    "Wyniki",
    "Kontakt",
]


@pytest.fixture
def legacy_content():
    call_command("seed_legacy_content", verbosity=0)


# --- strony -----------------------------------------------------------------------------------


def test_seed_creates_published_pages_and_drafts(legacy_content):
    published = ContentPage.objects.filter(slug__in=PUBLISHED_CONTENT)
    drafts = ContentPage.objects.filter(slug__in=DRAFT_CONTENT)

    assert set(published.values_list("slug", flat=True)) == set(PUBLISHED_CONTENT)
    assert all(page.live for page in published)
    assert set(drafts.values_list("slug", flat=True)) == set(DRAFT_CONTENT)
    assert not any(page.live for page in drafts)
    assert set(DocumentPage.objects.values_list("slug", flat=True)) >= set(DOCUMENTS)
    assert NewsPage.objects.live().count() == 3


def test_seed_marks_only_menu_pages(legacy_content):
    in_menu = ContentPage.objects.filter(show_in_menu=True).values_list("slug", flat=True)

    assert set(in_menu) == {"o-olimpiadzie", "jak-zaczac", "harmonogram", "kontakt"}
    # Dokumenty demonstracyjne zostają poza paskiem nawigacji.
    assert not DocumentPage.objects.filter(show_in_menus=True).exists()


def test_seed_is_idempotent(legacy_content):
    before = (ContentPage.objects.count(), DocumentPage.objects.count(), NewsPage.objects.count())

    call_command("seed_legacy_content", verbosity=0)

    assert (ContentPage.objects.count(), DocumentPage.objects.count(), NewsPage.objects.count()) == before


def test_content_page_body_keeps_structure(legacy_content):
    page = ContentPage.objects.get(slug="harmonogram")
    kinds = [block.block_type for block in page.body]

    # Tabela terminów zostaje listą definicji w akapicie, nagłówki – blokami ``heading``.
    assert "heading" in kinds
    assert "paragraph" in kinds
    assert "1 września – 15 października 2026" in str(page.body)


# --- adresy publiczne -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("/o-olimpiadzie/", "Fundacja Quantum AI"),
        ("/kontakt/", "contact@qaif.org"),
        ("/rodo/", DEMO_NOTICE_FRAGMENT),
        ("/harmonogram/", "7 listopada 2026"),
        ("/standardy-ochrony-maloletnich/", DEMO_NOTICE_FRAGMENT),
    ],
)
def test_published_pages_render(web_client, legacy_content, path, fragment):
    response = web_client.get(path)

    assert response.status_code == 200
    assert fragment in response.content.decode()


@pytest.mark.parametrize("path", ["/komitety/", "/partnerzy/"])
def test_draft_pages_are_not_public(web_client, legacy_content, path):
    assert web_client.get(path).status_code == 404


def test_rodo_keeps_document_metadata(legacy_content):
    page = DocumentPage.objects.get(slug="rodo")

    assert page.version_label == "1.0"
    assert page.document_date.isoformat() == "2026-07-22"
    assert "demonstracyjna" in page.status_label
    # Ostrzeżenie stoi nad treścią, nie gdzieś w środku dokumentu.
    assert page.body[0].block_type == "notice"
    assert page.body[0].value["tone"] == "warning"


# --- rama serwisu -----------------------------------------------------------------------------


def test_menu_has_declared_order(web_client, legacy_content):
    response = web_client.get("/")

    assert [item["title"] for item in response.context["cms_menu"]] == MENU_WITHOUT_REGULAMIN


def test_header_and_hero_show_branding(web_client, legacy_content):
    content = web_client.get("/").content.decode()

    assert '<span class="brand__mark">Olimpiada Kwantowa</span>' in content
    assert "<title>Olimpiada Kwantowa</title>" in content
    assert "Przyszłość ma naturę kwantową." in content
    assert "Jak zacząć w 3 krokach" in content


def test_footer_shows_organizer_from_settings(web_client, legacy_content):
    content = web_client.get("/").content.decode()

    assert "Fundacja Quantum AI" in content
    assert "KRS 0000808359" in content
    assert 'href="mailto:contact@qaif.org"' in content


def test_home_page_keeps_steps(legacy_content):
    home = HomePage.objects.get()

    assert home.steps_title == "Jak zacząć w 3 krokach"
    assert [block.value["title"] for block in home.steps] == [
        "Załóż konto",
        "Rozwiąż zadania",
        "Sprawdź wynik",
    ]


# --- edycja I 2026/2027 -----------------------------------------------------------------------


def test_seed_edition_creates_three_stages_with_full_timeline():
    call_command("seed_edition_kwantowa", verbosity=0)

    edition = Edition.objects.get(year_label=EDITION_LABEL)
    stages = {stage.kind: stage for stage in edition.stages.all()}

    assert set(stages) == {StageKind.ELIM, StageKind.DISTRICT, StageKind.FINAL}
    for stage in stages.values():
        assert stage.opens_at < stage.deadline_at
        assert stage.review_deadline_at == stage.deadline_at + timedelta(days=14)
        assert stage.appeal_window_opens_at == stage.review_deadline_at + timedelta(days=2)
        assert stage.appeal_window_closes_at == stage.review_deadline_at + timedelta(days=9)
        assert stage.scoring_scale.values == [
            {"value": 0, "label": "brak istotnego postępu"},
            {"value": 2, "label": "istotny postęp, rozwiązanie niepełne"},
            {"value": 5, "label": "rozwiązanie pełne z drobnymi usterkami"},
            {"value": 6, "label": "rozwiązanie pełne i poprawne"},
        ]
        assert stage.qualification_rule.mode == QualificationMode.MIN_POINTS
        assert stage.qualification_rule.min_points == MIN_POINTS


def test_seed_edition_uses_dates_from_old_site():
    call_command("seed_edition_kwantowa", verbosity=0)

    stages = {stage.kind: stage for stage in Stage.objects.all()}
    # Daty czytamy w strefie organizatora – w UTC „7 listopada 23:59” wypada 22:59.
    deadlines = {
        kind: stage.deadline_at.astimezone(stage.deadline_at.tzinfo).date() for kind, stage in stages.items()
    }

    assert deadlines[StageKind.ELIM].isoformat() == "2026-11-07"
    assert deadlines[StageKind.DISTRICT].isoformat() == "2027-01-16"
    assert deadlines[StageKind.FINAL].isoformat() == "2027-04-10"


def test_seed_edition_does_not_steal_current_flag_without_flag():
    demo = CurrentEditionFactory()

    call_command("seed_edition_kwantowa", verbosity=0)

    demo.refresh_from_db()
    assert demo.is_current is True
    assert Edition.objects.get(year_label=EDITION_LABEL).is_current is False


def test_seed_edition_make_current_switches_edition():
    demo = CurrentEditionFactory()

    call_command("seed_edition_kwantowa", "--make-current", verbosity=0)

    demo.refresh_from_db()
    assert demo.is_current is False
    assert Edition.objects.get(year_label=EDITION_LABEL).is_current is True


def test_seed_edition_is_idempotent():
    call_command("seed_edition_kwantowa", verbosity=0)
    call_command("seed_edition_kwantowa", verbosity=0)

    assert Edition.objects.filter(year_label=EDITION_LABEL).count() == 1
    assert Stage.objects.count() == 3
