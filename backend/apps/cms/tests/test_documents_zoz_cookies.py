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
ZOZ_NOTICE = "Wersja robocza (0.2) do akceptacji organizatora"
#: Sekcja z listą decyzji organizatora – bez niej dokument udawałby, że rozstrzyga progi i koszty.
ZOZ_DECISIONS_HEADING = "Do rozstrzygnięcia przez organizatora"

#: Terminy etapów I edycji z ``seed_edition_kwantowa``. **Żaden z nich nie może stać w treści ZOZ**:
#: na produkcji termin Etapu I jest już inny niż w seedzie, a dokument ma odsyłać do harmonogramu.
STAGE_DATES_THAT_MUST_NOT_APPEAR = ("7 listopada 2026", "16 stycznia 2027", "1 września 2026")

#: Nazwy, które polityka cookie musi wymienić – dokładnie te, które serwis ustawia.
COOKIE_NAMES = ("sessionid", "csrftoken", "wagtail_sidebar_collapsed")
#: Klucze pamięci lokalnej ustawiane przez pasek cookie. Muszą być dosłownie te, które ustawia
#: ``static/js/consent.js`` – polityka wymieniająca klucz, którego nie ma w kodzie, jest gorsza
#: niż brak polityki.
NOTICE_STORAGE_KEY = "cookie-notice-ack"
CONSENT_STORAGE_KEYS = ("cookie-consent", "cookie-consent-at")

#: Nazwy plików cookie GA4 i elementy sekcji analitycznej, bez których dokument nie spełnia
#: obowiązku informacyjnego: kto jest dostawcą, jak długo żyją pliki i jak wycofać zgodę.
ANALYTICS_HEADING = "Cookies analityczne (Google Analytics 4)"
ANALYTICS_FACTS = (
    "_ga",
    "2 lata",
    "Google Ireland Limited",
    "Ustawienia cookies",
    "art. 6 ust. 1 lit. a RODO",
)


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

    assert zoz.version_label == "0.2 (projekt)"
    assert zoz.document_date == date(2026, 9, 17)
    assert zoz.status_label == "projekt do akceptacji organizatora"

    assert cookies.version_label == "1.1"
    assert cookies.document_date == date(2026, 9, 15)
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


def test_zoz_metadata_agrees_with_the_version_section_of_the_document(web_client, documents):
    """Numer wersji stoi w dwóch miejscach: w metryce strony i w ostatniej sekcji treści.

    Oba biorą się z różnych plików (komenda kontra ``zoz.md``), więc rozjazd jest tu możliwy
    i przy każdej poprawce dokumentu realny – a czytelnik dostałby wtedy dwie odpowiedzi na
    pytanie „którą wersję czytam”.
    """
    zoz = DocumentPage.objects.get(slug=ZOZ_SLUG)
    content = web_client.get(zoz.url).content.decode()

    assert f"Wersja: {zoz.version_label}" in content


def test_zoz_carries_the_organisers_september_corrections(web_client, documents):
    """Poprawki organizatora z 15.09 – każda zmieniała zapis, który mówił coś innego niż praktyka.

    Czytamy **treść dokumentu**, a nie całą odpowiedź: dane rejestrowe Fundacji stoją w stopce
    każdej strony serwisu, więc asercja „nie ma ich w dokumencie” postawiona na HTML-u całej
    strony sprawdzałaby stopkę, a nie ZOZ.
    """
    page = DocumentPage.objects.get(slug=ZOZ_SLUG)
    body = " ".join([str(page.intro), str(page.body)])

    # Rozjazd ocen rozstrzyga organ Olimpiady, a nie funkcja techniczna w panelu.
    assert "Rozjazd rozstrzyga Przewodniczący Jury" in body
    assert "Pozostałe progi wymagają decyzji Jury." in body
    # Link do rozmowy widzi też Jury – inaczej zapis zabraniałby komisji wejść na własną rozmowę.
    assert "osoba zapisana na dany termin oraz członkowie Jury" in body
    # § 1 jest jednym zdaniem odsyłającym do harmonogramu, a nie wykładem o zegarze serwera.
    assert "Terminów zawodów" not in body
    # Dane rejestrowe organizatora stoją w stopce serwisu i w Regulaminie, nie w środku ZOZ.
    assert "REGON 384899425" not in body
    # Zakres warsztatów ma jedno miejsce – stronę „Warsztaty”.
    assert "narzędzia matematyczne: liczby zespolone" not in body
    assert 'href="/dokumenty/regulamin/"' in body


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
    for key in CONSENT_STORAGE_KEYS:
        assert key in content


def test_cookie_policy_describes_the_analytics_cookies(web_client, documents):
    """Cookie analityczne wolno zapisać dopiero po zgodzie – i dopiero po jej opisaniu.

    Zapowiedź „gdyby serwis zaczął używać plików innych niż niezbędne, poprosimy o zgodę”
    z wersji 1.0 przestała wystarczać w chwili, w której organizator dostał pole na identyfikator
    GA4. Dokument musi więc nazwać pliki, dostawcę, czas życia i drogę wycofania zgody.
    """
    content = web_client.get(f"/{INDEX_SLUG}/{COOKIES_SLUG}/").content.decode()

    assert ANALYTICS_HEADING in content
    for fact in ANALYTICS_FACTS:
        assert fact in content
    # Zapewnienia z wersji 1.0, które przestały być prawdziwe.
    assert "Nie prowadzimy analityki" not in content
    assert "nie używamy Google Analytics" not in content


def test_rodo_policy_names_the_purpose_basis_and_recipient_of_the_statistics(web_client, documents):
    """Polityka RODO wymienia cel, podstawę i odbiorcę – cookie to zapis, RODO to przetwarzanie."""
    content = web_client.get(f"/{INDEX_SLUG}/rodo/").content.decode()

    assert "statystyka odwiedzin serwisu" in content
    assert "art. 6 ust. 1 lit. a RODO" in content
    assert "Google Ireland Limited" in content
    assert "Data Privacy Framework" in content


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
