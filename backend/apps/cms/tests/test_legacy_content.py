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

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from wagtail.documents import get_document_model
from wagtail.rich_text import RichText

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
    "harmonogram",
    "warsztaty",
    "kontakt",
    "dla-nauczycieli",
)

#: Strony wycofane z serwisu i adres, na który prowadzi ich stary link. „Jak zacząć?” dublowało
#: sekcję kroków na stronie głównej, a „O Olimpiadzie” wróciło tam jako sekcja ``#o-olimpiadzie``.
#: Oba adresy wiszą w pismach i w wyszukiwarkach, więc muszą odpowiadać przekierowaniem, a nie 404.
OBSOLETE_PAGES = {"jak-zaczac": "/", "o-olimpiadzie": "/#o-olimpiadzie"}
DOCUMENTS = ("rodo", "zgoda-opiekuna", "standardy-ochrony-maloletnich", "komitety")

#: Nazwy z kafli starej strony. Jedna z nich („Uniwersytet Kwantowy”) to instytucja nieistniejąca,
#: pozostałe dwie nie mają potwierdzonego patronatu – żadna nie może wrócić na serwis.
INVENTED_PARTNERS = ("Ministerstwo Edukacji", "Uniwersytet Kwantowy", "Polskie Towarzystwo Fizyczne")
PARTNERS_EMPTY_STATE = "Lista partnerów I edycji zostanie opublikowana wkrótce."

#: Dokumenty pod ``/dokumenty/`` w kolejności z drzewa – ta sama w menu, w spisie i na stronie głównej.
DOCUMENT_ORDER = (
    "regulamin",
    "zoz",
    "rodo",
    "zgoda-opiekuna",
    "standardy-ochrony-maloletnich",
    "komitety",
    "cookies",
)
DOCUMENT_TITLES = [
    "Regulamin",
    "Zasady Organizacji Zawodów (ZOZ)",
    "Polityka RODO Olimpiady Kwantowej",
    "Zgoda rodzica lub opiekuna prawnego",
    "Standardy ochrony małoletnich Olimpiady Kwantowej",
    "Skład komitetów",
    "Polityka plików cookie",
]
#: Dokumenty, które miały jednosegmentowy adres przed wydzieleniem sekcji ``/dokumenty/`` – tylko
#: one mają przekierowanie 301. Wzór zgody opiekuna powstał już w sekcji, więc nie ma skąd
#: przekierowywać i wpis dla niego byłby wymyślonym adresem.
REDIRECTED_DOCUMENTS = ("regulamin", "rodo", "standardy-ochrony-maloletnich", "komitety")

#: Dokumenty, przy których wisi plik organizatora. Wzoru zgody opiekuna tu nie ma: organizator
#: nie przekazał żadnego pliku, bo dokument jest naszym projektem – do wydruku służy sama strona
#: (przycisk „Drukuj” plus arkusz ``@media print``).
DOWNLOADABLE_DOCUMENTS = ("regulamin", "rodo", "standardy-ochrony-maloletnich", "komitety")

#: Ramka nad treścią obu dokumentów: skąd jest treść i który plik jest wersją źródłową.
SOURCE_NOTICE_FRAGMENT = "Wersja do pobrania (PDF) jest wersją źródłową."

#: Liczba warsztatów przygotowawczych z harmonogramu organizatora (październik 2026 – luty 2027).
WORKSHOP_COUNT = 16

#: Terminy czytamy w strefie organizatora – w UTC „7 listopada 23:59” wypada dzień wcześniej o 22:59.
WARSAW = ZoneInfo("Europe/Warsaw")

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
#: „O Olimpiadzie” i „Jak zacząć?” nie są już pozycjami paska – ich treść stoi na stronie głównej.
#: „Warsztaty” stoją zaraz za „Harmonogramem”: obie pozycje odpowiadają na pytanie „kiedy”.
MENU_TITLES = [
    "Aktualności",
    "Zadania",
    "Harmonogram",
    "Warsztaty",
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

    assert set(in_menu) == {"harmonogram", "warsztaty", "kontakt"}
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

    # Nagłówki zostają blokami ``heading``, a terminy etapów – blokiem czytanym z bazy zawodów.
    # Żadnej daty etapu nie ma już w treści strony.
    assert "heading" in kinds
    assert "paragraph" in kinds
    assert "stage_timeline" in kinds
    # Tabela warsztatów wyprowadziła się na własną stronę: ``/harmonogram/`` jest o terminach
    # zawodów i zostawia po niej jeden odnośnik, a nie szesnaście wierszy.
    assert "schedule" not in kinds


def test_workshops_moved_out_of_harmonogram(web_client, legacy_content):
    """Harmonogram warsztatów jest w serwisie dokładnie raz – na ``/warsztaty/``.

    Dopóki tabela stała w środku ``/harmonogram/``, warsztatów nie widział nikt, kto nie przewinął
    tej podstrony do końca – a są bezpłatne i otwarte. Rozdzielenie ma sens tylko wtedy, gdy
    tabela naprawdę się **przenosi**, a nie kopiuje: dwie kopie rozjadą się przy pierwszej zmianie
    terminu i uczestnik zobaczy inny na każdej z nich.
    """
    harmonogram = web_client.get("/harmonogram/").content.decode()

    assert "Liczby zespolone" not in harmonogram
    assert 'class="table schedule"' not in harmonogram
    # Zostaje odnośnik – czytelnik szukający warsztatów na stronie z terminami ma gdzie kliknąć.
    assert 'href="/warsztaty/"' in harmonogram


# --- harmonogram: terminy z systemu i warsztaty --------------------------------------------------


def test_harmonogram_has_no_stage_dates_written_into_the_page(legacy_content):
    """Plik źródłowy strony nie zawiera ani jednej daty etapu – są w ``competitions.Stage``.

    To jest cała treść tej zmiany: dopóki terminarz był tabelą w treści, każde przesunięcie
    terminu wymagało poprawki w dwóch miejscach, a rozjazd między nimi objawiał się dopiero
    wtedy, gdy uczestnik oddawał pracę „na czas” według strony i po terminie według serwera.
    """
    body = str(ContentPage.objects.get(slug="harmonogram").body)

    # „16 stycznia 2027” świadomie poza listą: to termin **warsztatu**, czyli treść redakcyjna,
    # której system zawodów nie zna i której ten blok nie zastępuje.
    for fragment in ("7 listopada 2026", "4–7 czerwca 2027", "10 kwietnia 2027"):
        assert fragment not in body


def test_harmonogram_shows_the_final_in_krakow_from_the_database(web_client, legacy_content):
    """Finał: 4–7 czerwca 2027 w Krakowie – z etapów edycji, nie z akapitu w treści.

    Etap stacjonarny ma **jeden** termin, podany jako zakres dni: na finał się przyjeżdża, a nie
    „otwiera się go” i „oddaje w nim plik o 18:00”.
    """
    call_command("seed_edition_kwantowa", "--make-current", verbosity=0)

    content = web_client.get("/harmonogram/").content.decode()

    assert "Kraków" in content
    assert "<dt>Termin</dt><dd>4–7 czerwca 2027</dd>" in content
    assert "10 kwietnia 2027" not in content
    # Terminy dwóch pierwszych etapów zostają bez zmian – to jedyna zmiana w terminarzu.
    assert "7 listopada 2026" in content
    assert "16 stycznia 2027" in content


def test_warsztaty_lists_every_workshop_as_a_table(web_client, legacy_content):
    """Szesnaście warsztatów jako ``<table>``, nie jako lista definicji ani sklejone akapity."""
    page = ContentPage.objects.get(slug="warsztaty")
    schedule = next(block for block in page.body if block.block_type == "schedule")
    content = web_client.get("/warsztaty/").content.decode()

    assert len(schedule.value["rows"]) == WORKSHOP_COUNT
    assert schedule.value.has_time is True
    assert [schedule.value["topic_label"], schedule.value["date_label"]] == ["Temat", "Termin"]
    assert content.count('<th scope="row" class="schedule__topic">') == WORKSHOP_COUNT
    assert "Harmonogram warsztatów" in content
    for fragment in ("Liczby zespolone", "10 października 2026", "11:00–14:00", "13 lutego 2027"):
        assert fragment in content
    # Data przekazana jako 09/01/2027 jest czytana jako 9 stycznia; godziny potwierdził organizator.
    assert "9 stycznia 2027" in content
    assert "czekają na potwierdzenie" not in content
    # Prowadzący stoją w rubryce tematu – tak wpisał ich organizator w /cms/ i tak zostało w pliku.
    assert "prowadzący: Rafał Demkowicz-Dobrzański" in content
    # Zdanie wprowadzające i odnośnik z powrotem do terminów zawodów zostają na nowej stronie.
    assert "udział jest bezpłatny" in content
    assert 'href="/harmonogram/"' in content


def test_warsztaty_is_in_the_menu_right_after_harmonogram(web_client, legacy_content):
    """Kolejność menu to kolejność rodzeństwa w drzewie – „Warsztaty” muszą stać za „Harmonogramem”."""
    titles = [item["title"] for item in web_client.get("/").context["cms_menu"]]

    assert titles.index("Warsztaty") == titles.index("Harmonogram") + 1


def test_workshop_rows_carry_a_machine_readable_date(legacy_content):
    """Termin trafia do bloku dwa razy: jako tekst organizatora i jako data do porównania z zegarem.

    Bez drugiej postaci zapowiedź „najbliższe warsztaty” na stronie głównej musiałaby parsować
    polszczyznę przy każdym żądaniu – i zamilkłaby przy pierwszej literówce redaktora.
    """
    page = ContentPage.objects.get(slug="warsztaty")
    schedule = next(block for block in page.body if block.block_type == "schedule")
    rows = list(schedule.value["rows"])

    assert rows[0]["date"] == "10 października 2026"
    assert rows[0]["date_value"] == date(2026, 10, 10)
    metrologia = next(row for row in rows if row["topic"].startswith("Podstawy metrologii kwantowej"))
    assert metrologia["date_value"] == date(2027, 1, 9)
    assert metrologia["time"] == "11:00–14:00"
    assert all(row["date_value"] is not None for row in rows)


# --- adresy publiczne -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("/kontakt/", "contact@qaif.org"),
        ("/warsztaty/", "Liczby zespolone"),
        ("/dokumenty/rodo/", SOURCE_NOTICE_FRAGMENT),
        # Bez bieżącej edycji terminarz pokazuje pusty stan, a nie pustą tabelę – strona nadal
        # ma się otworzyć i powiedzieć czytelnikowi, czego jeszcze nie ma.
        ("/harmonogram/", "Terminy zostaną ogłoszone"),
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


# --- wzór zgody opiekuna ------------------------------------------------------------------------


def test_guardian_consent_page_is_published_under_documents(web_client, legacy_content):
    """Wzór zgody opiekuna ma adres, pod który prowadzi etykieta zgody w formularzu rejestracji."""
    page = DocumentPage.objects.get(slug="zgoda-opiekuna")

    assert page.live is True
    assert page.url == "/dokumenty/zgoda-opiekuna/"
    assert web_client.get("/dokumenty/zgoda-opiekuna/").status_code == 200


def test_guardian_consent_page_is_marked_as_a_draft_for_the_organiser(web_client, legacy_content):
    """Ostrzeżenie stoi i w metryce, i w ramce nad treścią – ta druga zostaje także na wydruku."""
    page = DocumentPage.objects.get(slug="zgoda-opiekuna")

    assert page.status_label == "Wersja robocza do akceptacji organizatora"
    assert page.version_label == "0.1 (projekt)"
    assert page.body[0].block_type == "notice"

    content = web_client.get("/dokumenty/zgoda-opiekuna/").content.decode()
    assert "Wersja robocza do akceptacji organizatora" in content


def test_guardian_consent_version_matches_the_consent_set(legacy_content):
    """Metryka strony i wersja w dowodzie zgody muszą mówić o tym samym dokumencie.

    ``ConsentRecord.document_version`` bierze się z ``apps.accounts.consents``; gdyby rozjechała
    się ze stroną, dowód wskazywałby wersję, której nigdy nie opublikowano.
    """
    from apps.accounts.consents import GUARDIAN_VERSION

    page = DocumentPage.objects.get(slug="zgoda-opiekuna")

    assert GUARDIAN_VERSION.startswith(page.version_label)


def test_guardian_consent_page_covers_the_form_sections(web_client, legacy_content):
    content = web_client.get("/dokumenty/zgoda-opiekuna/").content.decode()

    for fragment in (
        "Jak dostarczyć podpisany dokument",
        "Dane uczestnika",
        "Dane rodzica albo opiekuna prawnego",
        "Oświadczenie o zgodzie na udział",
        "Zgoda na przetwarzanie danych osobowych",
        "Dobrowolność i prawo wycofania zgody",
        "publikacja imienia i nazwiska",
        "Miejscowość, data i podpis",
    ):
        assert fragment in content, fragment
    # Kanał dostarczenia i adres organizatora – bez nich formularz jest nie do odesłania.
    assert "contact@qaif.org" in content


def test_guardian_consent_page_is_printable(web_client, legacy_content):
    """Przycisk „Drukuj” bez skryptu inline – CSP nie potrzebuje ``'unsafe-inline'``."""
    content = web_client.get("/dokumenty/zgoda-opiekuna/").content.decode()

    assert "data-print" in content
    assert ">Drukuj<" in content
    assert "js/print.js" in content
    assert "onclick=" not in content


def test_print_stylesheet_hides_navigation_and_footer():
    """Na kartce zostaje treść dokumentu, a nie pasek nawigacji i stopka."""
    from pathlib import Path

    from django.conf import settings

    css = Path(settings.BASE_DIR, "static", "css", "app.css").read_text(encoding="utf-8")
    assert "@media print {" in css
    # Reguła jest zapisana jako jedna lista selektorów zakończona ``display: none !important``;
    # sprawdzamy właśnie ten fragment, żeby test nie przechodził na samej obecności selektora
    # gdzieś indziej w arkuszu.
    hidden = css.split("@media print {", 1)[1].split("display: none !important;", 1)[0]
    for selector in (".topbar", ".footer", ".nav", "[data-print]"):
        assert selector in hidden, selector


def test_guardian_consent_page_is_not_a_separate_menu_item(web_client, legacy_content):
    """Dokument mieszka w rozwijanej sekcji „Dokumenty”, a nie jako kolejna pozycja paska."""
    page = DocumentPage.objects.get(slug="zgoda-opiekuna")

    assert page.show_in_menus is False


def test_seed_can_add_a_single_document_without_touching_the_rest(web_client, full_content):
    """``--only`` istnieje dla produkcji: dokłada jedną stronę i nie cofa redakcyjnych poprawek."""
    edited = DocumentPage.objects.get(slug="rodo")
    edited.title = "Polityka RODO (poprawiona w /cms/)"
    edited.save()
    edited.save_revision().publish()
    DocumentPage.objects.filter(slug="zgoda-opiekuna").delete()

    call_command("seed_legacy_content", only=["zgoda-opiekuna"], verbosity=0)

    assert DocumentPage.objects.get(slug="zgoda-opiekuna").live is True
    assert DocumentPage.objects.get(slug="rodo").title == "Polityka RODO (poprawiona w /cms/)"
    index = DocumentIndexPage.objects.get(slug="dokumenty")
    slugs = list(DocumentPage.objects.child_of(index).order_by("path").values_list("slug", flat=True))
    assert slugs == list(DOCUMENT_ORDER)


def test_seed_only_refuses_an_unknown_slug(legacy_content):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("seed_legacy_content", only=["nie-ma-takiej-strony"], verbosity=0)


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
    assert [row["page"].slug for row in rows] == list(DOWNLOADABLE_DOCUMENTS)
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


# --- strony wycofane --------------------------------------------------------------------------


@pytest.mark.parametrize("slug", sorted(OBSOLETE_PAGES))
def test_seed_removes_the_pages_whose_content_moved_to_the_home_page(legacy_content, slug):
    assert not ContentPage.objects.filter(slug=slug).exists()


@pytest.mark.parametrize(("slug", "target"), sorted(OBSOLETE_PAGES.items()))
def test_old_addresses_of_removed_pages_redirect(web_client, legacy_content, slug, target):
    """Stary adres nie może odpowiadać 404: wisi w pismach do szkół i w wynikach wyszukiwarek."""
    response = web_client.get(f"/{slug}/")

    assert response.status_code == 301
    assert response["Location"] == target


def test_seed_deletes_a_page_left_over_from_an_earlier_import(web_client, home_page):
    """Tak wygląda produkcja: strona stoi jeszcze w drzewie z poprzedniego importu.

    Komenda ma ją skasować, a nie schować jako szkic – szkic zostaje w liście stron redakcji jako
    pozycja bez wyjaśnienia, dlaczego nie jest opublikowana, choć jej treść **jest** w serwisie.
    """
    leftover = ContentPage(title="O Olimpiadzie", slug="o-olimpiadzie", live=True)
    home_page.add_child(instance=leftover)

    call_command("seed_legacy_content", verbosity=0)

    assert not ContentPage.objects.filter(slug="o-olimpiadzie").exists()
    assert web_client.get("/o-olimpiadzie/").status_code == 301


def test_redirects_of_removed_pages_survive_a_second_run(legacy_content):
    """Powtórny przebieg nie mnoży wpisów na ten sam adres – jeden 301, nie dwa."""
    from wagtail.contrib.redirects.models import Redirect

    call_command("seed_legacy_content", verbosity=0)

    for slug in OBSOLETE_PAGES:
        assert Redirect.objects.filter(old_path=f"/{slug}").count() == 1


# --- sekcja „O Olimpiadzie” na stronie głównej -------------------------------------------------


def test_about_section_is_seeded_from_the_fixture(web_client, legacy_content):
    """Treść wycofanej podstrony wraca jako sekcja strony głównej – z tego samego pliku."""
    home = HomePage.objects.get()
    content = web_client.get("/").content.decode()

    assert home.about_title == "O Olimpiadzie"
    assert [block.block_type for block in home.about_body][0] == "heading"
    assert 'id="o-olimpiadzie"' in content
    assert "Fundacja Quantum AI" in content
    assert "Dla uczniów szkół ponadpodstawowych w Polsce" in content


def test_about_section_is_not_overwritten_by_a_second_run(legacy_content):
    """Po pierwszym imporcie sekcja należy do redakcji – tak samo jak lista partnerów.

    Plik w repozytorium jest punktem startowym, nie stanem docelowym: nadpisywanie przy każdym
    przebiegu kasowałoby poprawki z ``/cms/``, a w zamian przywracało tekst sprzed roku.
    """
    home = HomePage.objects.get()
    home.about_body = [("paragraph", RichText("<p>Tekst dopisany przez redakcję.</p>"))]
    home.save()
    home.save_revision().publish()

    call_command("seed_legacy_content", verbosity=0)

    home = HomePage.objects.get()
    assert "Tekst dopisany przez redakcję." in str(home.about_body)
    assert "Fundacja Quantum AI" not in str(home.about_body)


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
    # Regulamin ma dwa pliki (PDF i źródłowy .docx); wzór zgody opiekuna – żadnego.
    assert len(files) == len(DOWNLOADABLE_DOCUMENTS) + 1
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


@pytest.mark.parametrize("slug", REDIRECTED_DOCUMENTS)
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

    for slug in REDIRECTED_DOCUMENTS:
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


def test_seed_edition_names_stages_like_regulamin():
    """Regulamin numeruje etapy – portal podpisuje je tak samo, nie słownikiem rodzajów."""
    call_command("seed_edition_kwantowa", verbosity=0)

    names = {s.kind: s.display_name for s in Edition.objects.get(year_label=EDITION_LABEL).stages.all()}
    assert names == {StageKind.ELIM: "Etap I", StageKind.DISTRICT: "Etap II", StageKind.FINAL: "Etap III"}


def test_seed_edition_keeps_coordinator_stage_name_on_rerun():
    """Nazwa po utworzeniu należy do koordynatora – ponowny seed (także --sync-dates) jej nie cofa."""
    call_command("seed_edition_kwantowa", verbosity=0)
    stage = Edition.objects.get(year_label=EDITION_LABEL).stages.get(kind=StageKind.ELIM)
    stage.name = "Etap I – zawody zdalne"
    stage.save(update_fields=["name"])

    call_command("seed_edition_kwantowa", "--sync-dates", verbosity=0)

    stage.refresh_from_db()
    assert stage.name == "Etap I – zawody zdalne"


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


def test_seed_edition_uses_dates_from_the_organiser():
    call_command("seed_edition_kwantowa", verbosity=0)

    stages = {stage.kind: stage for stage in Stage.objects.all()}
    deadlines = {kind: stage.deadline_at.astimezone(WARSAW) for kind, stage in stages.items()}

    assert deadlines[StageKind.ELIM].date().isoformat() == "2026-11-07"
    assert deadlines[StageKind.DISTRICT].date().isoformat() == "2027-01-16"
    # III etap: zawody stacjonarne 4–7 czerwca 2027 w Krakowie (dawniej 10 kwietnia w Warszawie).
    final = stages[StageKind.FINAL]
    assert final.opens_at.astimezone(WARSAW).isoformat() == "2027-06-04T09:00:00+02:00"
    assert deadlines[StageKind.FINAL].isoformat() == "2027-06-07T18:00:00+02:00"
    assert final.location == "Kraków"
    # Etapy zdalne nie mają miejsca – rubryka zostaje pusta, a nie wypełniona słowem „online”.
    assert stages[StageKind.ELIM].location == ""


def test_seed_edition_does_not_touch_existing_dates_without_the_flag():
    """Domyślnie oś czasu istniejącego etapu należy do koordynatora, a nie do skryptu."""
    call_command("seed_edition_kwantowa", verbosity=0)
    final = Stage.objects.get(kind=StageKind.FINAL)
    final.deadline_at = final.deadline_at + timedelta(days=3)
    final.review_deadline_at = final.review_deadline_at + timedelta(days=3)
    final.appeal_window_opens_at = final.appeal_window_opens_at + timedelta(days=3)
    final.appeal_window_closes_at = final.appeal_window_closes_at + timedelta(days=3)
    final.location = "Gdańsk"
    final.save()

    call_command("seed_edition_kwantowa", verbosity=0)

    final.refresh_from_db()
    assert final.deadline_at.astimezone(WARSAW).date().isoformat() == "2027-06-10"
    assert final.location == "Gdańsk"


def test_seed_edition_sync_dates_moves_existing_stages():
    """``--sync-dates`` przestawia terminy i miejsce istniejącego etapu na wartości z planu.

    To jest ścieżka, którą finał trafia na produkcję: edycja powstała z terminem 10 kwietnia 2027,
    a organizator zmienił go już po jej utworzeniu.
    """
    call_command("seed_edition_kwantowa", verbosity=0)
    final = Stage.objects.get(kind=StageKind.FINAL)
    final.opens_at = datetime(2027, 1, 17, 0, 0, tzinfo=WARSAW)
    final.deadline_at = datetime(2027, 4, 10, 23, 59, tzinfo=WARSAW)
    final.review_deadline_at = final.deadline_at + timedelta(days=14)
    final.appeal_window_opens_at = final.review_deadline_at + timedelta(days=2)
    final.appeal_window_closes_at = final.review_deadline_at + timedelta(days=9)
    final.location = ""
    final.save()

    call_command("seed_edition_kwantowa", "--sync-dates", verbosity=0)

    final.refresh_from_db()
    assert final.opens_at.astimezone(WARSAW).isoformat() == "2027-06-04T09:00:00+02:00"
    assert final.deadline_at.astimezone(WARSAW).isoformat() == "2027-06-07T18:00:00+02:00"
    assert final.location == "Kraków"
    # Reszta osi czasu przelicza się z tej samej reguły, co przy tworzeniu etapu.
    assert final.review_deadline_at == final.deadline_at + timedelta(days=14)
    assert final.appeal_window_opens_at == final.review_deadline_at + timedelta(days=2)
    assert final.appeal_window_closes_at == final.review_deadline_at + timedelta(days=9)
    # Etapy, których plan nie rusza, zostają na swoich terminach.
    assert Stage.objects.get(kind=StageKind.ELIM).deadline_at.astimezone(WARSAW).date().isoformat() == (
        "2026-11-07"
    )


def test_home_page_shows_the_stage_location(web_client, legacy_content):
    """Miejsce zawodów stoi na osi czasu strony głównej – ale tylko tam, gdzie jest w bazie."""
    call_command("seed_edition_kwantowa", "--make-current", verbosity=0)

    content = web_client.get("/").content.decode()

    assert "<dt>Miejsce</dt>" in content
    assert "Kraków" in content
    assert content.count("<dt>Miejsce</dt>") == 1  # etapy zdalne rubryki nie mają


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


# --- ochrona treści zredagowanej w /cms/ ---------------------------------------------------------


def _edit_in_cms(page, *, intro: str):
    """Symuluje poprawkę redaktora: rewizja z autorem, tak jak zapisuje ją panel Wagtaila."""
    from django.contrib.auth import get_user_model

    editor, _ = get_user_model().objects.get_or_create(
        email="redakcja@example.org", defaults={"is_staff": True}
    )
    page.intro = intro
    page.save_revision(user=editor).publish()


def test_full_seed_leaves_pages_edited_in_cms_alone(web_client, legacy_content):
    """Rewizja z autorem chroni stronę: pełny przebieg nie cofa poprawki organizatora."""
    page = ContentPage.objects.get(slug="harmonogram")
    _edit_in_cms(page, intro="<p>Poprawka organizatora.</p>")

    call_command("seed_legacy_content", verbosity=0)

    content = web_client.get("/harmonogram/").content.decode()
    assert "Poprawka organizatora." in content


def test_force_restores_pages_from_files(web_client, legacy_content):
    """``--force`` to świadome przywrócenie treści z repozytorium."""
    page = ContentPage.objects.get(slug="harmonogram")
    _edit_in_cms(page, intro="<p>Poprawka organizatora.</p>")

    call_command("seed_legacy_content", "--force", verbosity=0)

    content = web_client.get("/harmonogram/").content.decode()
    assert "Poprawka organizatora." not in content
    assert "Poniżej terminy I edycji" in content


def test_seed_revisions_carry_no_author_so_they_do_not_lock_pages(legacy_content):
    """Rewizje komendy nie mają autora – inaczej pierwszy przebieg blokowałby każdy następny."""
    page = ContentPage.objects.get(slug="harmonogram")

    assert page.revisions.exists()
    assert not page.revisions.filter(user__isnull=False).exists()


def test_home_hero_edited_in_cms_survives_a_full_seed(web_client, legacy_content):
    """Hasło strony głównej należy do redakcji; sekcja „O Olimpiadzie” nadal dopełnia się, gdy pusta."""
    home = HomePage.objects.get()
    home.hero_title = "Hasło redakcji"
    home.about_body = []
    from django.contrib.auth import get_user_model

    editor, _ = get_user_model().objects.get_or_create(
        email="redakcja@example.org", defaults={"is_staff": True}
    )
    home.save_revision(user=editor).publish()

    call_command("seed_legacy_content", verbosity=0)

    content = web_client.get("/").content.decode()
    assert "Hasło redakcji" in content
    assert 'id="o-olimpiadzie"' in content
    assert "Po co powstała Olimpiada?" in content


def test_partners_intro_edited_in_cms_survives_a_full_seed(web_client, legacy_content):
    """Wstęp strony partnerów po redakcji należy do organizatora – tak jak lista partnerów."""
    from apps.cms.models import PartnersPage

    page = PartnersPage.objects.get(slug="partnerzy")
    _edit_in_cms(page, intro="<p>Wstęp redakcji o partnerach.</p>")

    call_command("seed_legacy_content", verbosity=0)

    assert "Wstęp redakcji o partnerach." in web_client.get("/partnerzy/").content.decode()
