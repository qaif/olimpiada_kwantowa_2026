"""Import treści starej strony: ``seed_legacy_content`` i ``seed_edition_kwantowa``.

Testy pilnują czterech rzeczy, na których ten import stoi:

- **co jest publiczne, a co nie.** „Komitety”: skład komitetów jest podpisanym PDF-em
  organizatora, więc strona ma być publiczna i w menu. „Partnerzy” są publiczni, ale z **pustą**
  listą – nazwy ze starej strony (w tym nieistniejący „Uniwersytet Kwantowy”) nie mogą wrócić
  na serwis ani przez import, ani przez sekcję na stronie głównej,
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

from apps.cms.models import (
    ContentPage,
    DocumentIndexPage,
    DocumentPage,
    HomePage,
    NewsPage,
    PartnersPage,
)
from apps.competitions.management.commands.seed_edition_kwantowa import EDITION_LABEL, MIN_POINTS
from apps.competitions.models import Edition, QualificationMode, Stage, StageKind
from apps.competitions.tests.factories import CurrentEditionFactory

pytestmark = pytest.mark.django_db

PUBLISHED_CONTENT = (
    "o-olimpiadzie",
    "jak-zaczac",
    "harmonogram",
    "kontakt",
    "dla-nauczycieli",
)
DOCUMENTS = ("rodo", "standardy-ochrony-maloletnich", "komitety")

#: Nazwy z kafli starej strony. Jedna z nich („Uniwersytet Kwantowy”) to instytucja nieistniejąca,
#: pozostałe dwie nie mają potwierdzonego patronatu – żadna nie może wrócić na serwis.
INVENTED_PARTNERS = ("Ministerstwo Edukacji", "Uniwersytet Kwantowy", "Polskie Towarzystwo Fizyczne")
PARTNERS_EMPTY_STATE = "Lista partnerów I edycji zostanie opublikowana wkrótce."

#: Dokumenty pod ``/dokumenty/`` w kolejności z drzewa – ta sama w menu, w spisie i na stronie głównej.
DOCUMENT_ORDER = ("regulamin", "rodo", "standardy-ochrony-maloletnich", "komitety")
DOCUMENT_TITLES = [
    "Regulamin",
    "Polityka RODO Olimpiady Kwantowej",
    "Standardy ochrony małoletnich Olimpiady Kwantowej",
    "Skład komitetów",
]
#: Ramka nad treścią obu dokumentów: skąd jest treść i który plik jest wersją źródłową.
SOURCE_NOTICE_FRAGMENT = "Wersja do pobrania (PDF) jest wersją źródłową."

#: Tytuły PDF-ów organizatora wgrywanych przez tę komendę – tożsamość pliku w bibliotece Wagtaila.
#: PDF-u regulaminu tu nie ma: wgrywa go ``seed_regulamin`` razem z .docx i treścią strony, bo
#: wszystkie trzy są tą samą wersją dokumentu (patrz apps/cms/tests/test_document_page.py).
PDF_TITLES = {
    "Polityka RODO Olimpiady Kwantowej (PDF)",
    "Standardy ochrony małoletnich (PDF)",
    "Skład komitetów Olimpiady Kwantowej (PDF)",
}

#: Pasek nawigacji po imporcie. Dokumenty mają **jedną** pozycję („Dokumenty”) z listą rozwijaną –
#: regulamin i skład komitetów nie stoją już osobno między pozostałymi stronami.
MENU_TITLES = [
    "O Olimpiadzie",
    "Jak zacząć?",
    "Aktualności",
    "Zadania",
    "Harmonogram",
    "Dokumenty",
    "Archiwum",
    "Wyniki",
    "Partnerzy",
    "Kontakt",
]


@pytest.fixture
def legacy_content():
    call_command("seed_legacy_content", verbosity=0)


@pytest.fixture
def full_content():
    """Komplet treści: regulamin (osobna komenda) plus reszta importu – jak przy wdrożeniu."""
    call_command("seed_regulamin", verbosity=0)
    call_command("seed_legacy_content", verbosity=0)


# --- strony -----------------------------------------------------------------------------------


def test_seed_creates_published_pages(legacy_content):
    published = ContentPage.objects.filter(slug__in=PUBLISHED_CONTENT)

    assert set(published.values_list("slug", flat=True)) == set(PUBLISHED_CONTENT)
    assert all(page.live for page in published)
    assert set(DocumentPage.objects.values_list("slug", flat=True)) >= set(DOCUMENTS)
    assert NewsPage.objects.live().count() == 3


def test_seed_marks_only_menu_pages(legacy_content):
    in_menu = ContentPage.objects.filter(show_in_menu=True).values_list("slug", flat=True)

    assert set(in_menu) == {"o-olimpiadzie", "jak-zaczac", "harmonogram", "kontakt"}
    # Żaden dokument nie jest osobną pozycją paska: prowadzi do nich rozwijana sekcja „Dokumenty”,
    # która czyta dzieci sekcji, a nie znacznik ``show_in_menus``.
    assert not DocumentPage.objects.filter(show_in_menus=True).exists()
    assert DocumentIndexPage.objects.get(slug="dokumenty").show_in_menus is True


def test_seed_is_idempotent(legacy_content):
    before = (ContentPage.objects.count(), DocumentPage.objects.count(), NewsPage.objects.count())

    call_command("seed_legacy_content", verbosity=0)

    assert (ContentPage.objects.count(), DocumentPage.objects.count(), NewsPage.objects.count()) == before


# --- pliki organizatora -------------------------------------------------------------------------


def test_seed_uploads_the_official_pdfs(legacy_content):
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
    komitety = DocumentPage.objects.get(slug="komitety").attachments.get()

    assert rodo.document.title == "Polityka RODO Olimpiady Kwantowej (PDF)"
    assert standardy.document.title == "Standardy ochrony małoletnich (PDF)"
    assert komitety.document.title == "Skład komitetów Olimpiady Kwantowej (PDF)"
    for item in (rodo, standardy, komitety):
        assert item.label == "PDF do druku"
        assert item.is_pdf is True
        assert item.document.filename.endswith(".pdf")


@pytest.mark.parametrize("reversed_order", [False, True])
def test_regulamin_keeps_both_files_whatever_the_seed_order(reversed_order):
    """Regulamin ma dwa pliki: podpisany PDF (pierwszy) i plik źródłowy .docx (drugi).

    Obie komendy dotykają tej samej strony, a w skrypcie wdrożeniowym mogą stanąć w dowolnej
    kolejności – wynik ma być ten sam. Wcześniej pliki wgrywały dwie różne komendy z dwóch różnych
    katalogów i przy aktualizacji dokumentu strona dostawała nowy tekst ze starym PDF-em.
    """
    commands = ["seed_legacy_content", "seed_regulamin"]
    for name in reversed(commands) if reversed_order else commands:
        call_command(name, verbosity=0)

    page = DocumentPage.objects.get(slug="regulamin")
    assert [(item.label, item.document.file_extension) for item in page.attachments.all()] == [
        ("PDF do druku", "pdf"),
        ("Wersja źródłowa (DOCX)", "docx"),
    ]


def test_seed_does_not_duplicate_documents_on_second_run(legacy_content):
    Document = get_document_model()
    before = Document.objects.count()

    call_command("seed_legacy_content", verbosity=0)

    assert Document.objects.count() == before
    for title in PDF_TITLES:
        assert Document.objects.filter(title=title).count() == 1


@pytest.mark.parametrize(
    ("path", "title"),
    [
        ("/dokumenty/rodo/", "Polityka RODO Olimpiady Kwantowej (PDF)"),
        ("/dokumenty/standardy-ochrony-maloletnich/", "Standardy ochrony małoletnich (PDF)"),
        ("/dokumenty/komitety/", "Skład komitetów Olimpiady Kwantowej (PDF)"),
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
        ("/dokumenty/rodo/", SOURCE_NOTICE_FRAGMENT),
        ("/harmonogram/", "7 listopada 2026"),
        ("/dokumenty/standardy-ochrony-maloletnich/", SOURCE_NOTICE_FRAGMENT),
    ],
)
def test_published_pages_render(web_client, legacy_content, path, fragment):
    response = web_client.get(path)

    assert response.status_code == 200
    assert fragment in response.content.decode()


# --- partnerzy ----------------------------------------------------------------------------------


def test_partners_page_is_public_and_empty(web_client, legacy_content):
    """``/partnerzy/`` żyje, ale bez ani jednego partnera – lista czeka na podpisane umowy."""
    page = PartnersPage.objects.get(slug="partnerzy")
    response = web_client.get("/partnerzy/")
    content = response.content.decode()

    assert response.status_code == 200
    assert page.live is True
    assert page.partners_empty() is True
    assert page.groups() == []
    assert PARTNERS_EMPTY_STATE in content
    assert "Partnerzy instytucjonalni, naukowi oraz sponsorzy" in content


def test_partners_page_invites_cooperation(web_client, legacy_content):
    """Sekcja „Zostań partnerem” jest powodem, dla którego strona nie może być szkicem."""
    page = PartnersPage.objects.get(slug="partnerzy")
    content = web_client.get("/partnerzy/").content.decode()

    assert "Zostań partnerem" in content
    assert "patronat, wsparcie merytoryczne, nagrody dla laureatów i finansowanie finału" in content
    # Ostatnie zdanie powtarza § 22 ust. 3 Regulaminu – to nie jest ozdobnik, tylko odpowiedź
    # na pytanie, które przy stronie partnerów zadaje sobie uczestnik.
    assert "nie mają wpływu na treść zadań, ocenę prac ani wyniki" in content
    # Adres przychodzi z ustawień serwisu, więc jego zmiana jest jedną poprawką w ``/cms/``.
    assert page.contact_email == "contact@qaif.org"
    assert 'href="mailto:contact@qaif.org"' in content


@pytest.mark.parametrize("name", INVENTED_PARTNERS)
def test_seed_does_not_publish_invented_partners(web_client, legacy_content, name):
    """Nazw z kafli starej strony nie ma ani na ``/partnerzy/``, ani na stronie głównej."""
    assert name not in web_client.get("/partnerzy/").content.decode()
    assert name not in web_client.get("/").content.decode()
    assert name not in str(PartnersPage.objects.get(slug="partnerzy").partners)


def test_seed_replaces_the_old_partners_draft(web_client, home_page):
    """Baza sprzed zmiany ma pod tym slugiem szkic ``ContentPage`` – komenda go zastępuje.

    Typu strony nie da się zmienić w miejscu (dwie tabele), więc szkic jest kasowany, a strona
    powstaje na nowo. Sprawdzamy też, że nie zostają dwie strony o tym samym slugu.
    """
    draft = ContentPage(title="Partnerzy i sponsorzy", slug="partnerzy", live=False)
    home_page.add_child(instance=draft)

    call_command("seed_legacy_content", verbosity=0)

    assert not ContentPage.objects.filter(slug="partnerzy").exists()
    assert PartnersPage.objects.filter(slug="partnerzy").count() == 1
    assert web_client.get("/partnerzy/").status_code == 200


def test_home_page_hides_partners_section_until_there_is_one(web_client, legacy_content):
    """Pas logotypów na stronie głównej pojawia się dopiero z pierwszym wpisem.

    Nagłówek „Partnerzy” nad pustym pasem czytałby się jak awaria szablonu – a wcześniej stały
    tam kafle z nazwą instytucji, która nie istnieje.
    """
    response = web_client.get("/")

    assert response.context["partners_page"] is None
    assert "partner-strip" not in response.content.decode()

    page = PartnersPage.objects.get(slug="partnerzy")
    page.partners = [
        ("partner", {"name": "Instytut Fizyki PAN", "level": "partner-naukowy", "description": ""})
    ]
    page.save()
    page.save_revision().publish()

    response = web_client.get("/")
    content = response.content.decode()
    assert response.context["partners_page"] is not None
    assert "partner-strip" in content
    assert "Instytut Fizyki PAN" in content


def test_partners_page_groups_entries_by_level(web_client, legacy_content):
    """Grupy stoją w kolejności ``PARTNER_LEVELS``, a nie w kolejności dodawania wpisów."""
    page = PartnersPage.objects.get(slug="partnerzy")
    page.partners = [
        ("partner", {"name": "Firma Kwantowa", "level": "sponsor-zloty", "description": "Nagrody."}),
        ("partner", {"name": "Instytut Fizyki PAN", "level": "partner-naukowy", "description": ""}),
        ("partner", {"name": "Uniwersytet Warszawski", "level": "partner-naukowy", "description": ""}),
    ]
    page.save()
    page.save_revision().publish()

    groups = PartnersPage.objects.get(slug="partnerzy").groups()
    content = web_client.get("/partnerzy/").content.decode()

    assert [group["label"] for group in groups] == ["partner naukowy", "sponsor złoty"]
    assert [len(group["partners"]) for group in groups] == [2, 1]
    assert PARTNERS_EMPTY_STATE not in content
    assert content.index("Instytut Fizyki PAN") < content.index("Firma Kwantowa")
    # Bez logotypu karta pokazuje inicjały nazwy, a nie pusty kadr obrazu.
    assert ">IF<" in content
    assert ">UW<" in content


def test_komitety_is_public_with_scope_from_pdf(web_client, legacy_content):
    """Strona składu komitetów jest publiczna i powtarza zakresy odpowiedzialności z PDF-u."""
    response = web_client.get("/dokumenty/komitety/")
    content = response.content.decode()

    assert response.status_code == 200
    assert DocumentPage.objects.get(slug="komitety").live is True
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

    assert [item["title"] for item in response.context["cms_menu"]] == MENU_TITLES


def test_header_and_hero_show_branding(web_client, legacy_content):
    content = web_client.get("/").content.decode()

    assert 'class="brand__logo"' in content  # logotyp graficzny w nagłówku
    assert '<span class="brand__mark visually-hidden">Olimpiada Kwantowa</span>' in content
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
    assert [row["page"].slug for row in rows] == list(DOCUMENT_ORDER)
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


# --- sekcja /dokumenty/ -------------------------------------------------------------------------


def test_seed_moves_documents_under_the_documents_section(full_content):
    """Wszystkie dokumenty są dziećmi ``/dokumenty/``, w zadeklarowanej kolejności."""
    index = DocumentIndexPage.objects.get(slug="dokumenty")
    documents = DocumentPage.objects.child_of(index).order_by("path")

    assert list(documents.values_list("slug", flat=True)) == list(DOCUMENT_ORDER)
    assert index.get_parent().specific_class is HomePage


def test_document_index_lists_every_document(web_client, full_content):
    response = web_client.get("/dokumenty/")
    content = response.content.decode()

    assert response.status_code == 200
    assert [page.slug for page in response.context["documents"]] == list(DOCUMENT_ORDER)
    assert content.count('class="card doc-card"') == len(DOCUMENT_ORDER)
    for slug, title in zip(DOCUMENT_ORDER, DOCUMENT_TITLES, strict=True):
        assert f'href="/dokumenty/{slug}/"' in content
        assert title in content


def test_document_index_links_every_file_directly(web_client, full_content):
    """Karta ma odnośnik do strony i wprost do plików – czytelnik nie musi wchodzić po PDF."""
    content = web_client.get("/dokumenty/").content.decode()

    files = [item for page in DocumentPage.objects.all() for item in page.attachments.all()]
    assert len(files) == len(DOCUMENT_ORDER) + 1  # regulamin ma dwa pliki: PDF i źródłowy .docx
    for item in files:
        assert f'href="{item.document.url}"' in content


def test_document_index_shows_summaries_not_the_documents_themselves(web_client, full_content):
    """Spis pokazuje zajawkę wprowadzenia – nazwiska i telefony zostają na stronach dokumentów."""
    content = web_client.get("/dokumenty/").content.decode()

    for name in ("Rafał Demkowicz-Dobrzański", "Michał Kutwin", "Tomasz Ćwik", "Tomasz Sowiński"):
        assert name not in content
    # Numer telefonu zaufania z „Standardów ochrony małoletnich” też jest treścią dokumentu.
    assert "800 12 12 12" not in content
    assert "§ 24" not in content
    assert "Skład komitetów" in content


def test_document_summary_reads_as_a_sentence(legacy_content):
    """Zajawka powstaje ze zdjęcia znaczników z ``intro`` – bez sklejania sąsiednich akapitów."""
    summary = DocumentPage.objects.get(slug="komitety").summary()

    assert summary.startswith("Członkowie i zakres odpowiedzialności Za przygotowanie zadań")
    assert "odpowiedzialnościZa" not in summary
    assert "<" not in summary


def test_komitety_is_a_document_with_metadata_and_chapters(web_client, legacy_content):
    """Skład komitetów ma układ dokumentu: metrykę, ramkę ze źródłem, spis sekcji i PDF."""
    page = DocumentPage.objects.get(slug="komitety")
    content = web_client.get("/dokumenty/komitety/").content.decode()

    assert [chapter["text"] for chapter in page.chapters()] == [
        "Komitet Merytoryczny",
        "Komitet Organizacyjny",
        "Kontakt z Organizatorem",
    ]
    assert '<nav class="doc-toc"' in content
    assert 'href="#komitet-merytoryczny"' in content
    assert 'href="#komitet-organizacyjny"' in content
    # Metryka opisuje eksport PDF-u organizatora – dokładnie tak, jak przy RODO.
    assert page.version_label == ""
    assert page.document_date.isoformat() == "2026-09-07"
    assert page.status_label == DocumentPage.objects.get(slug="rodo").status_label
    assert page.body[0].block_type == "notice"
    assert SOURCE_NOTICE_FRAGMENT in content
    assert page.attachments.get().document.title == "Skład komitetów Olimpiady Kwantowej (PDF)"


# --- menu z listą rozwijaną ---------------------------------------------------------------------


def test_menu_documents_item_has_every_document_as_child(web_client, full_content):
    response = web_client.get("/")
    item = next(entry for entry in response.context["cms_menu"] if entry["title"] == "Dokumenty")

    assert item["url"] == "/dokumenty/"
    assert [child["title"] for child in item["children"]] == DOCUMENT_TITLES
    assert [child["url"] for child in item["children"]] == [f"/dokumenty/{s}/" for s in DOCUMENT_ORDER]


def test_menu_renders_documents_as_a_details_element(web_client, full_content):
    """Rozwijacz działa bez JavaScriptu: ``<details>`` + ``<summary>``, plus link do całej sekcji."""
    content = web_client.get("/").content.decode()
    menu = content.split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]

    assert '<details class="nav-menu">' in menu
    assert ">Dokumenty</summary>" in menu
    assert 'href="/dokumenty/"' in menu
    assert menu.index("/dokumenty/regulamin/") < menu.index("/dokumenty/komitety/")
    # Żaden dokument nie jest już osobną pozycją najwyższego poziomu.
    assert '<a class="nav__link" href="/dokumenty/regulamin/"' not in menu


def test_menu_marks_current_document_and_its_section(web_client, full_content):
    content = web_client.get("/dokumenty/rodo/").content.decode()
    menu = content.split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]
    link = menu.split('href="/dokumenty/rodo/"', 1)[1].split(">", 1)[0]

    assert 'aria-current="page"' in link
    # Rodzic jest podświetlony, choć czytelnik nie stoi na ``/dokumenty/``.
    assert "nav-menu__summary--active" in menu


def test_menu_reads_document_children_without_a_query_per_document(
    django_assert_max_num_queries, rf, full_content
):
    """Lista rozwijana kosztuje jedno zapytanie na całe menu, nie jedno na dokument."""
    from apps.cms.context_processors import cms_menu

    with django_assert_max_num_queries(6):
        menu = cms_menu(rf.get("/"))["cms_menu"]

    assert [len(item["children"]) for item in menu if item["children"]] == [len(DOCUMENT_ORDER)]


# --- przekierowania ze starych adresów ----------------------------------------------------------


@pytest.mark.parametrize("slug", DOCUMENT_ORDER)
def test_old_document_address_redirects_permanently(web_client, full_content, slug):
    response = web_client.get(f"/{slug}/")

    assert response.status_code == 301
    assert response["Location"] == f"/dokumenty/{slug}/"
    assert web_client.get(f"/dokumenty/{slug}/").status_code == 200


def test_seed_leaves_exactly_one_redirect_per_old_address(full_content):
    """Wagtail dokłada własne przekierowanie przy każdym przeniesieniu strony – wpis ma być jeden.

    Bez sprzątania każde wdrożenie zostawiałoby w ``/cms/`` kolejną kopię wiersza dla tego samego
    adresu (unikalność w bazie obejmuje parę ``old_path`` + witryna), a redaktor nie wiedziałby,
    który z nich obowiązuje.
    """
    from wagtail.contrib.redirects.models import Redirect

    call_command("seed_legacy_content", verbosity=0)

    for slug in DOCUMENT_ORDER:
        redirects = Redirect.objects.filter(old_path=f"/{slug}")
        assert redirects.count() == 1, f"/{slug}/ ma {redirects.count()} przekierowań"
        assert redirects.get().link == f"/dokumenty/{slug}/"


def test_seed_migrates_the_old_flat_layout_without_duplicates(web_client, home_page):
    """Baza sprzed wydzielenia sekcji: dokumenty pod stroną główną, komitety jako strona treści.

    To jest stan produkcji w chwili wdrożenia tej zmiany. Komenda ma przenieść istniejące strony
    (zachowując ich identyfikatory, a więc rewizje i odnośniki wewnętrzne), przerobić komitety
    na dokument i nie zostawić ani jednej strony w dwóch egzemplarzach.
    """
    rodo = DocumentPage(title="Polityka RODO", slug="rodo")
    home_page.add_child(instance=rodo)
    standardy = DocumentPage(title="Standardy", slug="standardy-ochrony-maloletnich")
    home_page.add_child(instance=standardy)
    komitety = ContentPage(title="Komitety", slug="komitety", show_in_menu=True)
    home_page.add_child(instance=komitety)
    old_pks = {"rodo": rodo.pk, "standardy-ochrony-maloletnich": standardy.pk}

    call_command("seed_regulamin", verbosity=0)
    call_command("seed_legacy_content", verbosity=0)

    index = DocumentIndexPage.objects.get(slug="dokumenty")
    slugs = DocumentPage.objects.child_of(index).order_by("path").values_list("slug", flat=True)
    assert list(slugs) == list(DOCUMENT_ORDER)
    for slug, pk in old_pks.items():
        # Przeniesiona, nie utworzona na nowo – inaczej zerwałyby się rewizje i odnośniki.
        assert DocumentPage.objects.get(slug=slug).pk == pk
    assert DocumentPage.objects.filter(slug__in=DOCUMENT_ORDER).count() == len(DOCUMENT_ORDER)
    assert not ContentPage.objects.filter(slug="komitety").exists()
    assert web_client.get("/dokumenty/komitety/").status_code == 200
    assert web_client.get("/komitety/").status_code == 301


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
