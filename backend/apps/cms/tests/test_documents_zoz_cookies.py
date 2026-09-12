"""Dwa dokumenty powstałe w repozytorium: ZOZ (projekt) i polityka plików cookie (obowiązuje).

Testy pilnują czterech rzeczy, na których oba dokumenty stoją:

- **są opublikowane w sekcji** ``/dokumenty/`` i mają metrykę, która mówi, co czytelnik czyta.
  Przy ZOZ to nie kosmetyka: dokument jest projektem, więc status i ramka nad treścią są jedyną
  różnicą między „projektem do akceptacji” a zapisem, którym ktoś zacznie się zasłaniać,
- **stoją na swoich miejscach w spisie.** Kolejność bierze się z drzewa stron, a nie z pola
  sortującego, więc pomyłka w ``DOCUMENT_ORDER`` objawia się dopiero w menu i na ``/dokumenty/``.
  ZOZ musi być zaraz za Regulaminem (Regulamin odsyła do niego szczegóły edycji), a polityka
  cookie – ostatnia, bo dotyczy serwisu, nie zawodów,
- **ZOZ nie powtarza terminów etapów.** Terminy trzyma ``competitions.Stage`` i pokazuje
  ``/harmonogram/``; data wpisana w treść dokumentu byłaby drugim źródłem prawdy, które rozjedzie
  się z serwerem przy pierwszym przesunięciu terminu (na produkcji już się przesunął),
- **polityka cookie wymienia pliki, które serwis naprawdę zapisuje.** Lista bez nazw jest
  bezużyteczna, a lista z nazwami wymyślonymi – gorsza niż brak polityki.

``--only`` sprawdzamy osobno, bo to tym trybem oba dokumenty trafiają na produkcję: pełny przebieg
komendy nadpisałby treść wszystkich stron plikami z repozytorium.
"""

from datetime import date

import pytest
from django.core.management import call_command

from apps.cms.models import DocumentIndexPage, DocumentPage

pytestmark = pytest.mark.django_db

INDEX_SLUG = "dokumenty"
ZOZ_SLUG = "zoz"
COOKIES_SLUG = "cookies"

ZOZ_TITLE = "Zasady Organizacji Zawodów (ZOZ)"
COOKIES_TITLE = "Polityka plików cookie"

#: Pierwsze zdanie ramki nad treścią ZOZ. Ma zostać także w wydruku, więc jest w pliku źródłowym,
#: a nie dokładane przez komendę (jak ramka „wersją źródłową jest PDF” przy dokumentach organizatora).
ZOZ_NOTICE = "Wersja robocza (0.1) do akceptacji organizatora"
#: Sekcja z listą decyzji organizatora – bez niej dokument udawałby, że rozstrzyga progi i koszty.
ZOZ_DECISIONS_HEADING = "Do rozstrzygnięcia przez organizatora"

#: Terminy etapów I edycji z ``seed_edition_kwantowa``. **Żaden z nich nie może stać w treści ZOZ**:
#: na produkcji termin Etapu I jest już inny niż w seedzie, a dokument ma odsyłać do harmonogramu.
STAGE_DATES_THAT_MUST_NOT_APPEAR = ("7 listopada 2026", "16 stycznia 2027", "1 września 2026")

#: Nazwy, które polityka cookie musi wymienić – dokładnie te, które serwis ustawia.
COOKIE_NAMES = ("sessionid", "csrftoken", "wagtail_sidebar_collapsed")
#: Klucz pamięci lokalnej ustawiany przez pasek informujący o ciasteczkach. Musi być dosłownie ten,
#: który ustawia ``static/js/cookie-notice.js`` – polityka wymieniająca klucz, którego nie ma
#: w kodzie, jest gorsza niż brak polityki.
NOTICE_STORAGE_KEY = "cookie-notice-ack"


@pytest.fixture
def documents() -> DocumentIndexPage:
    """Komplet dokumentów: regulamin (osobna komenda) plus import – jak przy wdrożeniu."""
    call_command("seed_regulamin", verbosity=0)
    call_command("seed_legacy_content", verbosity=0)
    return DocumentIndexPage.objects.get(slug=INDEX_SLUG)


def _slugs(index: DocumentIndexPage) -> list[str]:
    """Slugi dokumentów w kolejności z drzewa – tej samej, którą pokazują menu i spis."""
    return list(DocumentPage.objects.child_of(index).order_by("path").values_list("slug", flat=True))


# --- powstanie i metryka ------------------------------------------------------------------------


def test_seed_publishes_both_documents(documents):
    zoz = DocumentPage.objects.get(slug=ZOZ_SLUG)
    cookies = DocumentPage.objects.get(slug=COOKIES_SLUG)

    assert (zoz.title, cookies.title) == (ZOZ_TITLE, COOKIES_TITLE)
    assert zoz.live is True
    assert cookies.live is True
    assert (zoz.url, cookies.url) == (f"/{INDEX_SLUG}/{ZOZ_SLUG}/", f"/{INDEX_SLUG}/{COOKIES_SLUG}/")
    # Dokument nie jest osobną pozycją paska nawigacji – sekcja „Dokumenty” czyta swoje dzieci.
    assert zoz.show_in_menus is False
    assert cookies.show_in_menus is False


def test_metadata_says_what_the_reader_is_reading(documents):
    """ZOZ jest projektem, polityka cookie obowiązuje – i to musi być widać w metryce."""
    zoz = DocumentPage.objects.get(slug=ZOZ_SLUG)
    cookies = DocumentPage.objects.get(slug=COOKIES_SLUG)

    assert zoz.version_label == "0.1 (projekt)"
    assert zoz.document_date == date(2026, 9, 12)
    assert zoz.status_label == "projekt do akceptacji organizatora"

    assert cookies.version_label == "1.0"
    assert cookies.document_date == date(2026, 9, 12)
    assert cookies.status_label == "obowiązuje"


def test_neither_document_has_a_file_to_download(documents):
    """Oba powstały w repozytorium, więc wersją źródłową jest strona, a nie plik organizatora."""
    for slug in (ZOZ_SLUG, COOKIES_SLUG):
        assert DocumentPage.objects.get(slug=slug).attachments.count() == 0


# --- kolejność w sekcji ------------------------------------------------------------------------


def test_zoz_stands_right_after_the_regulamin(documents):
    slugs = _slugs(documents)

    assert slugs.index(ZOZ_SLUG) == slugs.index("regulamin") + 1


def test_cookie_policy_closes_the_section(documents):
    assert _slugs(documents)[-1] == COOKIES_SLUG


def test_document_index_lists_both_in_tree_order(web_client, documents):
    response = web_client.get(f"/{INDEX_SLUG}/")
    content = response.content.decode()

    assert response.status_code == 200
    assert [page.slug for page in response.context["documents"]] == _slugs(documents)
    for slug, title in ((ZOZ_SLUG, ZOZ_TITLE), (COOKIES_SLUG, COOKIES_TITLE)):
        assert f'href="/{INDEX_SLUG}/{slug}/"' in content
        assert title in content


# --- treść ZOZ ---------------------------------------------------------------------------------


def test_zoz_opens_with_the_draft_notice(web_client, documents):
    """Ramka stoi **nad** treścią, a nie tylko w metryce: czytelnik ma wiedzieć, co czyta, zanim
    zacznie czytać zapisy – i ma to zostać także w wydruku."""
    zoz = DocumentPage.objects.get(slug=ZOZ_SLUG)

    assert zoz.body[0].block_type == "notice"
    assert ZOZ_NOTICE in zoz.body[0].value["text"].source

    content = web_client.get(zoz.url).content.decode()
    assert ZOZ_NOTICE in content


def test_zoz_ends_with_the_list_of_decisions_for_the_organiser(web_client, documents):
    content = web_client.get(f"/{INDEX_SLUG}/{ZOZ_SLUG}/").content.decode()

    assert ZOZ_DECISIONS_HEADING in content
    # Trzy zapisy, których brak jest dziś najbardziej dotkliwy – Regulamin odsyła je do ZOZ wprost.
    assert "Maksymalna liczba finalistów" in content
    assert "Wykaz literatury" in content
    assert "Koszty przejazdu" in content


def test_zoz_points_at_the_schedule_instead_of_repeating_stage_dates(web_client, documents):
    """Terminy trzyma ``Stage``; dokument odsyła do ``/harmonogram/`` i nie wpisuje ich w treść."""
    content = web_client.get(f"/{INDEX_SLUG}/{ZOZ_SLUG}/").content.decode()

    assert 'href="/harmonogram/"' in content
    for stage_date in STAGE_DATES_THAT_MUST_NOT_APPEAR:
        assert stage_date not in content


def test_zoz_links_the_documents_it_must_be_read_with(web_client, documents):
    content = web_client.get(f"/{INDEX_SLUG}/{ZOZ_SLUG}/").content.decode()

    for path in (
        f"/{INDEX_SLUG}/regulamin/",
        f"/{INDEX_SLUG}/rodo/",
        f"/{INDEX_SLUG}/standardy-ochrony-maloletnich/",
        "/warsztaty/",
    ):
        assert f'href="{path}"' in content


# --- treść polityki cookie ---------------------------------------------------------------------


def test_cookie_policy_names_every_cookie_the_service_sets(web_client, documents):
    content = web_client.get(f"/{INDEX_SLUG}/{COOKIES_SLUG}/").content.decode()

    for name in COOKIE_NAMES:
        assert name in content
    assert NOTICE_STORAGE_KEY in content


def test_cookie_policy_is_linked_from_the_rodo_policy(web_client, documents):
    """Czytelnik polityki RODO szukający ciasteczek ma je znaleźć bez wracania do spisu."""
    content = web_client.get(f"/{INDEX_SLUG}/rodo/").content.decode()

    assert f'href="/{INDEX_SLUG}/{COOKIES_SLUG}/"' in content


# --- tryb produkcyjny (--only) -----------------------------------------------------------------


def test_seed_only_zoz_adds_the_document_without_touching_the_rest(documents):
    """Tak ZOZ trafia na produkcję: jedna strona dołożona, redakcyjne poprawki nietknięte."""
    edited = DocumentPage.objects.get(slug="rodo")
    edited.title = "Polityka RODO (poprawiona w /cms/)"
    edited.save()
    edited.save_revision().publish()
    DocumentPage.objects.filter(slug=ZOZ_SLUG).delete()

    call_command("seed_legacy_content", only=[ZOZ_SLUG], verbosity=0)

    assert DocumentPage.objects.get(slug=ZOZ_SLUG).live is True
    assert DocumentPage.objects.get(slug="rodo").title == "Polityka RODO (poprawiona w /cms/)"
    # Kolejność przestawia się także w tym trybie – nowa strona musi trafić na swoje miejsce.
    index = DocumentIndexPage.objects.get(slug=INDEX_SLUG)
    slugs = _slugs(index)
    assert slugs.index(ZOZ_SLUG) == slugs.index("regulamin") + 1


def test_seed_only_accepts_both_slugs_at_once(documents):
    """Komenda wdrożeniowa: ``--only zoz --only cookies`` dokłada oba dokumenty jednym przebiegiem."""
    DocumentPage.objects.filter(slug__in=(ZOZ_SLUG, COOKIES_SLUG)).delete()

    call_command("seed_legacy_content", only=[ZOZ_SLUG, COOKIES_SLUG], verbosity=0)

    index = DocumentIndexPage.objects.get(slug=INDEX_SLUG)
    slugs = _slugs(index)
    assert slugs.index(ZOZ_SLUG) == slugs.index("regulamin") + 1
    assert slugs[-1] == COOKIES_SLUG
    assert DocumentPage.objects.get(slug=COOKIES_SLUG).live is True


def test_second_run_does_not_duplicate_either_document(documents):
    call_command("seed_legacy_content", verbosity=0)

    for slug in (ZOZ_SLUG, COOKIES_SLUG):
        assert DocumentPage.objects.filter(slug=slug).count() == 1
