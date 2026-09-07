"""Import treści starej strony: ``seed_legacy_content`` i ``seed_edition_kwantowa``.

Testy pilnują czterech rzeczy, na których ten import stoi:

- **co jest publiczne, a co nie.** „Partnerzy” sugerują patronaty, których może nie być – strona
  ma zostać szkicem, czyli odpowiadać 404 pod publicznym adresem. „Komitety” odwrotnie: skład
  komitetów jest podpisanym PDF-em organizatora, więc strona ma być publiczna i w menu,
- **kolejność menu.** Menu wynika z kolejności rodzeństwa w drzewie, a nie z pola sortującego,
  więc pomyłka w komendzie objawia się dopiero w nagłówku strony,
- **pliki organizatora.** Cztery PDF-y mają wisieć przy właściwych stronach, dać się pobrać
  i nie mnożyć kopii w bibliotece przy powtórnym przebiegu komendy,
- **oś czasu edycji.** Stara strona ma po jednej dacie na etap; reguły uzupełniające resztę są
  zapisane w komendzie i tu sprawdzane, żeby ich cicha zmiana nie przeszła bez śladu.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from wagtail.documents import get_document_model

from apps.cms.models import ContentPage, DocumentPage, HomePage, NewsPage
from apps.competitions.management.commands.seed_edition_kwantowa import EDITION_LABEL, MIN_POINTS
from apps.competitions.models import Edition, QualificationMode, Stage, StageKind
from apps.competitions.tests.factories import CurrentEditionFactory

pytestmark = pytest.mark.django_db

PUBLISHED_CONTENT = (
    "o-olimpiadzie",
    "komitety",
    "jak-zaczac",
    "harmonogram",
    "kontakt",
    "dla-nauczycieli",
)
DRAFT_CONTENT = ("partnerzy",)
DOCUMENTS = ("rodo", "standardy-ochrony-maloletnich")
#: Ramka nad treścią obu dokumentów: skąd jest treść i który plik jest wersją źródłową.
SOURCE_NOTICE_FRAGMENT = "Wersja do pobrania (PDF) jest wersją źródłową."

#: Tytuły PDF-ów organizatora wgrywanych przez komendę – tożsamość pliku w bibliotece Wagtaila.
PDF_TITLES = {
    "Polityka RODO Olimpiady Kwantowej (PDF)",
    "Standardy ochrony małoletnich (PDF)",
    "Skład komitetów Olimpiady Kwantowej (PDF)",
    "Regulamin Olimpiady Kwantowej v1.0 (PDF)",
}

MENU_WITHOUT_REGULAMIN = [
    "O Olimpiadzie",
    "Komitety",
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

    assert set(in_menu) == {"o-olimpiadzie", "komitety", "jak-zaczac", "harmonogram", "kontakt"}
    # Dokumenty prawne zostają poza paskiem nawigacji – prowadzi do nich stopka i strona główna.
    assert not DocumentPage.objects.filter(show_in_menus=True).exists()


def test_seed_is_idempotent(legacy_content):
    before = (ContentPage.objects.count(), DocumentPage.objects.count(), NewsPage.objects.count())

    call_command("seed_legacy_content", verbosity=0)

    assert (ContentPage.objects.count(), DocumentPage.objects.count(), NewsPage.objects.count()) == before


# --- pliki organizatora -------------------------------------------------------------------------


def test_seed_uploads_four_official_pdfs(legacy_content):
    documents = get_document_model().objects.filter(title__in=PDF_TITLES)

    assert set(documents.values_list("title", flat=True)) == PDF_TITLES
    for document in documents:
        # Rozmiar i skrót są policzone od razu: pierwszy trafia na kartę „Do pobrania”
        # (bez niego widać „0 bajtów”), drugi – do nagłówka ``ETag`` widoku serwującego.
        assert document.file_size > 0
        assert document.file_hash


def test_seed_attaches_pdf_to_matching_pages(legacy_content):
    rodo = DocumentPage.objects.get(slug="rodo").attachments.get()
    standardy = DocumentPage.objects.get(slug="standardy-ochrony-maloletnich").attachments.get()
    komitety = ContentPage.objects.get(slug="komitety").attachments.get()

    assert rodo.document.title == "Polityka RODO Olimpiady Kwantowej (PDF)"
    assert standardy.document.title == "Standardy ochrony małoletnich (PDF)"
    assert komitety.document.title == "Skład komitetów Olimpiady Kwantowej (PDF)"
    for item in (rodo, standardy, komitety):
        assert item.label == "PDF do druku"
        assert item.is_pdf is True
        assert item.document.filename.endswith(".pdf")


def test_seed_puts_pdf_before_docx_on_regulamin():
    """Regulamin ma dwa pliki: podpisany PDF (pierwszy) i plik źródłowy .docx (drugi).

    Kolejność komend jest tu odwrotna do naturalnej („najpierw regulamin, potem reszta”) –
    obie muszą dać ten sam wynik, bo w skrypcie wdrożeniowym mogą stanąć w dowolnym porządku.
    """
    call_command("seed_regulamin", verbosity=0)
    call_command("seed_legacy_content", verbosity=0)

    page = DocumentPage.objects.get(slug="regulamin")
    assert [(item.label, item.document.file_extension) for item in page.attachments.all()] == [
        ("PDF do druku", "pdf"),
        ("Wersja źródłowa (DOCX)", "docx"),
    ]

    # Powtórny przebieg ``seed_regulamin`` nie może zdjąć PDF-a dołożonego przez drugą komendę.
    call_command("seed_regulamin", verbosity=0)
    page = DocumentPage.objects.get(slug="regulamin")
    assert [item.document.file_extension for item in page.attachments.all()] == ["pdf", "docx"]


def test_seed_does_not_duplicate_documents_on_second_run(legacy_content):
    Document = get_document_model()
    before = Document.objects.count()

    call_command("seed_legacy_content", verbosity=0)

    assert Document.objects.count() == before
    for title in PDF_TITLES - {"Regulamin Olimpiady Kwantowej v1.0 (PDF)"}:
        assert Document.objects.filter(title=title).count() == 1


@pytest.mark.parametrize(
    ("path", "title"),
    [
        ("/rodo/", "Polityka RODO Olimpiady Kwantowej (PDF)"),
        ("/standardy-ochrony-maloletnich/", "Standardy ochrony małoletnich (PDF)"),
        ("/komitety/", "Skład komitetów Olimpiady Kwantowej (PDF)"),
    ],
)
def test_pages_link_and_serve_their_pdf(web_client, legacy_content, path, title):
    document = get_document_model().objects.get(title=title)

    page = web_client.get(path)
    assert page.status_code == 200
    assert f'href="{document.url}"' in page.content.decode()

    download = web_client.get(document.url)
    assert download.status_code == 200
    assert download["Content-Type"] == "application/pdf"


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
        ("/rodo/", SOURCE_NOTICE_FRAGMENT),
        ("/harmonogram/", "7 listopada 2026"),
        ("/standardy-ochrony-maloletnich/", SOURCE_NOTICE_FRAGMENT),
    ],
)
def test_published_pages_render(web_client, legacy_content, path, fragment):
    response = web_client.get(path)

    assert response.status_code == 200
    assert fragment in response.content.decode()


def test_draft_pages_are_not_public(web_client, legacy_content):
    assert web_client.get("/partnerzy/").status_code == 404


def test_komitety_is_public_with_scope_from_pdf(web_client, legacy_content):
    """Strona składu komitetów jest publiczna i powtarza zakresy odpowiedzialności z PDF-u."""
    response = web_client.get("/komitety/")
    content = response.content.decode()

    assert response.status_code == 200
    assert ContentPage.objects.get(slug="komitety").live is True
    assert "Zadania, kryteria oceniania, anonimowa ocena prac, kwalifikacja i rozstrzygnięcia Jury" in content
    assert "Rejestracja, komunikacja, obsługa systemu, logistyka, miejsce finału i dokumentacja" in content
    assert "pełni również funkcję Jury" in content
    # Wszystkie szesnaście wpisów z PDF-u (dziesięć + sześć, Paweł Gora i Grzegorz Czelusta w obu).
    for name in ("Rafał Demkowicz-Dobrzański", "Tomasz Sowiński", "Michał Kutwin", "Tomasz Ćwik"):
        assert name in content


def test_rodo_keeps_document_metadata(legacy_content):
    """Metryka opisuje eksport PDF-u organizatora, a nie numer wersji z ostatniej sekcji treści."""
    page = DocumentPage.objects.get(slug="rodo")

    assert page.version_label == ""
    assert "eksport z 7 września 2026" in page.status_label
    assert page.document_date.isoformat() == "2026-09-07"
    assert page.status_label.startswith("Dokument organizatora (Fundacja Quantum AI)")
    # Ramka stoi nad treścią, nie gdzieś w środku dokumentu, i jest informacją, nie ostrzeżeniem.
    assert page.body[0].block_type == "notice"
    assert page.body[0].value["tone"] == "info"


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


def test_home_page_lists_four_documents_to_download(web_client, legacy_content):
    """Sekcja „Dokumenty do pobrania”: regulamin, RODO, standardy i skład komitetów."""
    call_command("seed_regulamin", verbosity=0)
    call_command("seed_legacy_content", verbosity=0)

    response = web_client.get("/")
    rows = response.context["downloads"]
    content = response.content.decode()

    # Kolejność jest kolejnością z drzewa (ta sama, co w menu), a nie kolejnością wgrywania.
    assert [row["page"].slug for row in rows] == [
        "komitety",
        "regulamin",
        "rodo",
        "standardy-ochrony-maloletnich",
    ]
    assert "Dokumenty do pobrania" in content
    for row in rows:
        # Tytuł prowadzi do strony, przycisk – wprost do pliku.
        assert f'href="{row["page"].url}"' in content
        assert f'href="{row["attachment"].document.url}"' in content
        assert row["attachment"].is_pdf is True


def test_home_page_downloads_do_not_query_per_document(django_assert_max_num_queries, legacy_content):
    """Sekcja rośnie o wiersze, nie o zapytania – ``prefetch_related`` w ``_download_rows``.

    Cztery zapytania na strony i ich pliki (dwa typy stron × strona + załączniki) plus zapas
    na dociągnięcie samych dokumentów. Bez ``prefetch_related`` samo czytanie tytułów w pętli
    dokładałoby po dwa zapytania na każdy dokument.
    """
    from apps.cms.models import _download_rows

    home = HomePage.objects.get()
    with django_assert_max_num_queries(6):
        rows = _download_rows(home)
        for row in rows:
            assert row["attachment"].document.title

    assert len(rows) >= 3


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
