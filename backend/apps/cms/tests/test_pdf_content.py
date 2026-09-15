"""Dokumenty ``/dokumenty/rodo/`` i ``/dokumenty/standardy-ochrony-maloletnich/``
wobec PDF-ów organizatora.

Obie strony są **wersją HTML podpisanego dokumentu**, a nie streszczeniem: czytelnik ma dostać
w przeglądarce to samo, co w pliku, w tej samej kolejności sekcji. Testy pilnują trzech rzeczy,
które przy ręcznym przepisywaniu psują się najciszej:

- **kompletność sekcji.** Zgubienie jednej sekcji nie rzuca się w oczy w tekście na trzy ekrany,
  ale zmienia treść dokumentu prawnego. Sprawdzamy tytuły co do słowa i długość spisu sekcji,
- **kompletność treści.** Sekcja może być na miejscu, a w środku brakować akapitu. Porównanie
  liczby słów strony z liczbą słów PDF-u łapie każdy taki ubytek powyżej dziesiątej części tekstu,
- **status.** Treść pochodzi z dokumentu organizatora, nie ze starego WordPressa, więc na stronach
  nie może zostać ani słowa o „wersji demonstracyjnej”, a ramka na górze ma wskazywać PDF jako
  wersję źródłową.

Wzorcem jest wyciąg tekstu z PDF-ów (``fixtures/legacy/pdf-text/``) – ten sam, z którego przepisano
pliki ``fixtures/legacy/*.md``. Leży obok PDF-ów, bo tylko ``backend/`` jest w kontenerze.
"""

import re
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils.html import strip_tags

from apps.cms.models import DocumentPage

pytestmark = pytest.mark.django_db

PDF_TEXT = Path(__file__).resolve().parents[1] / "fixtures" / "legacy" / "pdf-text"

#: Znacznik strony z ekstrakcji oraz żywa pagina PDF-u – ani jedno, ani drugie nie jest treścią.
PAGE_MARKER_RE = re.compile(r"^=+\s*STRONA\s+\d+\s*=+$")
RUNNING_HEAD_RE = re.compile(r"^FUNDACJA QUANTUM AI\b")
RUNNING_FOOT_RE = re.compile(r"^Olimpiada Kwantowa\s*\|\s*strona\s+\d+$")

#: Ile słów PDF-u musi znaleźć się na stronie. Nie 100 %, bo dwie rzeczy z pliku wychodzą poza
#: treść strony: wiersz „Organizator … Data eksportu” trafia do metryki dokumentu (osobne pola,
#: nie akapit), a nagłówek tabeli „Cel | Podstawa” powtarza się w PDF-ie na każdej stronie.
MIN_WORD_RATIO = 0.90

RODO_SECTIONS = (
    "1. Kogo dotyczy informacja",
    "2. Jakie dane przetwarzamy",
    "3. Cele i podstawy prawne",
    "4. Dane osób małoletnich",
    "5. Skąd mamy dane",
    "6. Odbiorcy danych",
    "7. Przekazywanie poza EOG",
    "8. Jak długo przechowujemy dane",
    "9. Twoje prawa",
    "10. Skarga i decyzje automatyczne",
    "11. Aktualizacje",
)

#: Dziesięć sekcji numerowanych plus nienumerowana „Wersja skrócona dla uczniów”, która w PDF-ie
#: stoi między sekcją 9 a 10 i jest osobnym rozdziałem dokumentu, a nie ramką w środku sekcji.
STANDARDY_SECTIONS = (
    "1. Zakres i najważniejsze zasady",
    "2. Bezpieczne relacje Personelu z małoletnimi",
    "3. Weryfikacja i przygotowanie Personelu",
    "4. Bezpieczne relacje między uczestnikami",
    "5. Internet system zawodów i wizerunek",
    "6. Jak zgłosić zagrożenie lub krzywdzenie",
    "7. Procedura interwencji",
    "8. Poufność i ochrona zgłaszających",
    "9. Wdrożenie dostępność i przegląd",
    "Wersja skrócona dla uczniów",
    "10. Informacja o dokumencie",
)

#: Pary „Cel | Podstawa” z sekcji 3 polityki RODO. Sprawdzamy tekst, a nie znacznik: to podstawa
#: prawna przetwarzania, więc liczy się, czy stoi przy właściwym celu i w brzmieniu z dokumentu.
RODO_PURPOSES = (
    (
        "rejestracja, prowadzenie konta, przyjmowanie i anonimowe ocenianie prac, "
        "kwalifikacja do etapów oraz obsługa odwołań",
        "art. 6 ust. 1 lit. b i e RODO oraz przepisy dotyczące organizacji konkursów i olimpiad; "
        "w odpowiednim zakresie także zgoda - art. 6 ust. 1 lit. a",
    ),
    (
        "weryfikacja samodzielności prac, zapewnienie bezpieczeństwa, zapobieganie nadużyciom "
        "i obrona przed roszczeniami",
        "art. 6 ust. 1 lit. f RODO - prawnie uzasadniony interes Organizatora",
    ),
    (
        "realizacja obowiązków prawnych, podatkowych, rachunkowych, sprawozdawczych i archiwizacyjnych",
        "art. 6 ust. 1 lit. c RODO",
    ),
    (
        "publikacja zdjęć, nagrań i materiałów promocyjnych wykraczających poza relację "
        "z wydarzenia publicznego",
        "dobrowolna zgoda - art. 6 ust. 1 lit. a RODO",
    ),
    # Jedyny wiersz, który **nie** pochodzi z PDF-a organizatora: dopisany razem z Google
    # Analytics 4, bo statystyka odwiedzin jest przetwarzaniem, o którym art. 13 RODO każe
    # poinformować. Patrz README 4.5.
    (
        "statystyka odwiedzin serwisu (Google Analytics 4)",
        "dobrowolna zgoda - art. 6 ust. 1 lit. a RODO",
    ),
)

DOCUMENTS = {
    "rodo": "Polityka-RODO-Olimpiada-Kwantowa.txt",
    "standardy-ochrony-maloletnich": "Standardy-ochrony-maloletnich-Olimpiada-Kwantowa.txt",
}


@pytest.fixture
def legacy_content():
    call_command("seed_legacy_content", verbosity=0)


def pdf_words(name: str) -> list[str]:
    """Słowa dokumentu bez żywej paginy i znaczników stron z ekstrakcji."""
    lines = (PDF_TEXT / name).read_text(encoding="utf-8").splitlines()
    kept = [
        line
        for line in lines
        if not (
            PAGE_MARKER_RE.match(line.strip())
            or RUNNING_HEAD_RE.match(line.strip())
            or RUNNING_FOOT_RE.match(line.strip())
        )
    ]
    # „•” to znak wypunktowania z PDF-u, a nie słowo; na stronie robi to znacznik ``<li>``.
    return " ".join(kept).replace("•", " ").split()


def page_words(page) -> list[str]:
    """Słowa treści strony: tytuł, wprowadzenie i bloki. Bez ramy serwisu (menu, stopka).

    Znacznik zamieniamy na spację, a nie na pustkę: ``</p><p>`` bez tego sklejałoby ostatnie słowo
    akapitu z pierwszym słowem następnego i każde takie miejsce gubiłoby jedno słowo z licznika.
    """
    html = " ".join([page.title, str(page.intro), str(page.body)])
    return strip_tags(html.replace("<", " <")).split()


# --- kompletność sekcji -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "sections"),
    [("rodo", RODO_SECTIONS), ("standardy-ochrony-maloletnich", STANDARDY_SECTIONS)],
)
def test_page_has_every_section_from_pdf(web_client, legacy_content, slug, sections):
    response = web_client.get(f"/dokumenty/{slug}/")
    content = response.content.decode()

    assert response.status_code == 200
    for section in sections:
        assert section in content, f"brak sekcji „{section}” na /{slug}/"


@pytest.mark.parametrize(
    ("slug", "sections"),
    [("rodo", RODO_SECTIONS), ("standardy-ochrony-maloletnich", STANDARDY_SECTIONS)],
)
def test_chapter_list_has_one_entry_per_section(web_client, legacy_content, slug, sections):
    """Spis rozdziałów jest nawigacją po dokumencie – ma wymieniać wszystkie sekcje i tylko je."""
    page = DocumentPage.objects.get(slug=slug)
    chapters = page.chapters()

    assert [chapter["text"] for chapter in chapters] == list(sections)
    assert '<nav class="doc-toc"' in web_client.get(f"/dokumenty/{slug}/").content.decode()


# --- tabela „Cel | Podstawa” --------------------------------------------------------------------


#: Wierszy w tabeli „Cel | Podstawa” jest siedem (sześć z PDF-a organizatora plus statystyka
#: odwiedzin dopisana razem z GA4); ``RODO_PURPOSES`` wymienia pięć z nich.
RODO_PURPOSE_ROWS = 7


def test_rodo_renders_purposes_as_definition_list(web_client, legacy_content):
    """Sekcja 3 to tabela dwukolumnowa: na stronie ``<dl>`` z parą znaczników na każdy wiersz."""
    body = str(DocumentPage.objects.get(slug="rodo").body)

    assert '<dl class="definitions">' in body
    assert body.count("<dt") == body.count("<dd") == RODO_PURPOSE_ROWS
    # Nagłówki kolumn z dokumentu stoją przy każdej parze – bez nich kolejność nic nie mówi.
    assert body.count('<span class="definitions__label">Cel</span>') == RODO_PURPOSE_ROWS
    assert body.count('<span class="definitions__label">Podstawa</span>') == RODO_PURPOSE_ROWS
    assert '<dl class="definitions">' in web_client.get("/dokumenty/rodo/").content.decode()


@pytest.mark.parametrize(("purpose", "basis"), RODO_PURPOSES)
def test_rodo_keeps_purpose_next_to_its_legal_basis(web_client, legacy_content, purpose, basis):
    page = DocumentPage.objects.get(slug="rodo")
    pairs = [
        (row["term"], row["description"])
        for block in page.body
        if block.block_type == "definitions"
        for row in block.value["rows"]
    ]

    assert (purpose, basis) in pairs
    assert basis in web_client.get("/dokumenty/rodo/").content.decode()


# --- status treści ------------------------------------------------------------------------------


@pytest.mark.parametrize("slug", list(DOCUMENTS))
def test_document_points_at_pdf_instead_of_demo_disclaimer(web_client, legacy_content, slug):
    content = web_client.get(f"/dokumenty/{slug}/").content.decode()

    assert "demonstracyjn" not in content.lower()
    assert '<aside class="notice notice--info">' in content
    assert "Wersja do pobrania (PDF) jest wersją źródłową." in content
    assert "Dokument organizatora (Fundacja Quantum AI)" in content


def test_standardy_keeps_short_version_for_students(web_client, legacy_content):
    """Wersja skrócona jest ramką, a nie kolejnym akapitem: to jedyna część pisana do ucznia."""
    page = DocumentPage.objects.get(slug="standardy-ochrony-maloletnich")
    notices = [block.value for block in page.body if block.block_type == "notice"]
    content = web_client.get("/dokumenty/standardy-ochrony-maloletnich/").content.decode()

    assert all(notice["tone"] == "info" for notice in notices)
    assert "Masz prawo czuć się bezpiecznie." in content
    assert "Dziecięcy Telefon Zaufania Rzecznika Praw Dziecka:" in content
    assert "800 12 12 12" in content


# --- kompletność treści -------------------------------------------------------------------------


@pytest.mark.parametrize(("slug", "source"), sorted(DOCUMENTS.items()))
def test_page_keeps_the_words_of_the_pdf(legacy_content, slug, source):
    page = DocumentPage.objects.get(slug=slug)
    expected = pdf_words(source)
    actual = page_words(page)

    ratio = len(actual) / len(expected)
    assert ratio >= MIN_WORD_RATIO, (
        f"/{slug}/ ma {len(actual)} słów wobec {len(expected)} w PDF-ie ({ratio:.0%}) – "
        "sprawdź, czy przy przepisywaniu nie wypadł akapit"
    )


def test_komitety_repeats_the_pdf_including_contact(web_client, legacy_content):
    """Skład komitetów jest jednostronicowym PDF-em – strona ma powtarzać także jego stopkę.

    Bez porównania liczby słów: numery pozycji w listach nazwisk rysuje ``<ol>``, a nie tekst,
    więc licznik słów strony i PDF-u nie może się zgodzić. Nazwiska i zakresy odpowiedzialności
    sprawdza ``test_legacy_content``.
    """
    content = web_client.get("/dokumenty/komitety/").content.decode()
    page_text = " ".join(page_words(DocumentPage.objects.get(slug="komitety")))

    assert "Członkowie i zakres odpowiedzialności" in content
    assert "Kontakt z Organizatorem" in content
    assert "Fundacja Quantum AI | ul. Sanocka 9/103, 02-110 Warszawa" in page_text
    assert "contact@qaif.org" in content
    assert "+48 507 982 292" in content
