"""``DocumentPage`` + ``manage.py seed_regulamin`` – regulamin jako strona serwisu.

Testy pilnują czterech rzeczy, które przy imporcie dokumentu prawnego psują się najciszej:

1. **kompletność struktury** – 10 rozdziałów i wszystkie 24 paragrafy, w kolejności z dokumentu.
   Zgubiony ``§`` na stronie regulaminu to nie literówka, tylko brakujący przepis,
2. **kompletność treści** – liczba słów strony wobec liczby słów PDF-u. Sekcja może stać na
   miejscu, a w środku brakować akapitu albo pozycji listy,
3. **idempotencja** – komenda bywa uruchamiana przy każdym wdrożeniu dev-a; drugi przebieg nie
   może dołożyć ani drugiej strony ``/dokumenty/regulamin/``, ani drugiej kopii plików
   w buckecie,
4. **brak surowego HTML-a** – treść idzie do ``RichText``, więc znaczniki mają się renderować,
   a nie wyświetlać jako tekst. Odwrotny błąd (podwójne escapowanie w imporcie) jest widoczny
   dopiero w przeglądarce.

Wzorcem jest wersja 1.0 z 2 września 2026 r.: trzyetapowa (§ 11–13), bez akapitu „WAŻNY STATUS
PRAWNY” na stronie tytułowej – zastrzeżenie o statusie prawnym stoi wyłącznie w sekcji „Status
dokumentu”, z której import robi ramkę.
"""

import re
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils.html import strip_tags
from wagtail.documents import get_document_model

from apps.cms.management.commands.seed_regulamin import DOCUMENT_TITLE, PDF_DOCUMENT_TITLE
from apps.cms.models import DocumentIndexPage, DocumentPage

pytestmark = pytest.mark.django_db

CHAPTERS = 10
PARAGRAPHS = 24

#: Metryka wersji 1.0 z 2 września 2026 r. – wersja, data i status prosto z tabeli tytułowej.
VERSION = "1.0"
DOCUMENT_DATE = "2026-09-02"
STATUS = "Projekt do zatwierdzenia uchwałą Zarządu"

#: Zdanie z sekcji „Status dokumentu”. W tej wersji dokumentu stoi ono **wyłącznie** tam (akapit
#: „WAŻNY STATUS PRAWNY” ze strony tytułowej zniknął), więc pominięcie sekcji zdjęłoby z serwisu
#: jedyną informację o tym, że Olimpiada nie nadaje ustawowych uprawnień laureata.
LEGAL_STATUS = "nie nadaje ustawowych uprawnień laureata lub finalisty"

#: Wyciąg tekstu z PDF-u (``pypdf``) – wzorzec kompletności treści, tak samo jak w
#: ``test_pdf_content.py`` dla RODO i standardów ochrony małoletnich. Leży obok pliku, bo do
#: kontenera trafia wyłącznie ``backend/``.
PDF_TEXT = Path(__file__).resolve().parents[1] / "fixtures" / "regulamin"
PDF_TEXT_FILE = "Regulamin-Olimpiady-Kwantowej.txt"

#: Znacznik strony z ekstrakcji oraz żywa pagina PDF-u – ani jedno, ani drugie nie jest treścią.
PAGE_MARKER_RE = re.compile(r"^=+\s*STRONA\s+\d+\s*=+$")
RUNNING_HEAD_RE = re.compile(r"^OLIMPIADA KWANTOWA REGULAMIN")
RUNNING_FOOT_RE = re.compile(r"^Fundacja Quantum AI\s*•\s*strona \d+ z \d+$")

#: Ile słów PDF-u musi znaleźć się na stronie. Nie 100 %, bo trzy rzeczy z pliku wychodzą poza
#: treść bloków: numery ustępów (rysuje je ``<ol>``, w PDF-ie są tekstem), papierowy spis
#: rozdziałów oraz wiersze metryki, które są polami modelu, a nie akapitem.
MIN_WORD_RATIO = 0.90

#: Adres strony po wydzieleniu sekcji dokumentów. Stary ``/regulamin/`` żyje jako 301 –
#: patrz ``apps/cms/tests/test_legacy_content.py``.
PAGE_PATH = "/dokumenty/regulamin/"


@pytest.fixture
def regulamin() -> DocumentPage:
    call_command("seed_regulamin", verbosity=0)
    return DocumentPage.objects.get(slug="regulamin")


def headings(page: DocumentPage, prefix: str) -> list[str]:
    return [
        block.value["text"]
        for block in page.body
        if block.block_type == "heading" and block.value["anchor"].startswith(prefix)
    ]


# --- import treści ------------------------------------------------------------------------------


def test_seed_imports_all_chapters_and_paragraphs(regulamin):
    chapter_texts = headings(regulamin, "rozdzial-")
    paragraph_texts = headings(regulamin, "par-")

    assert len(chapter_texts) == CHAPTERS
    assert chapter_texts[0].startswith("Rozdział I.")
    assert chapter_texts[-1].startswith("Rozdział X.")
    # Wszystkie paragrafy, w kolejności dokumentu – bez luk i bez przestawienia.
    assert [text.split(".")[0] for text in paragraph_texts] == [f"§ {n}" for n in range(1, PARAGRAPHS + 1)]


def test_seed_fills_document_metadata_and_attachments(regulamin):
    assert regulamin.version_label == VERSION
    assert regulamin.document_date.isoformat() == DOCUMENT_DATE
    assert STATUS in regulamin.status_label
    # Obie postacie dokumentu wgrywa ta sama komenda, bo obie są tą samą wersją: podpisany PDF
    # (wersja do druku i do cytowania) przed plikiem źródłowym .docx.
    files = [(item.label, item.document.title, item.is_pdf) for item in regulamin.attachments.all()]
    assert files == [
        ("PDF do druku", PDF_DOCUMENT_TITLE, True),
        ("Wersja źródłowa (DOCX)", DOCUMENT_TITLE, False),
    ]


def test_seed_keeps_the_legal_status_notice(regulamin):
    """Sekcja „Status dokumentu” zamienia się w ramkę, a nie znika razem z nagłówkiem.

    W wersji z sierpnia to samo zdanie stało dwa razy (ramka „WAŻNY STATUS PRAWNY” na stronie
    tytułowej i powtórzenie w sekcji), więc import pomijał sekcję. Wersja 1.0 z 2 września ma je
    tylko raz – pominięcie sekcji skasowałoby zastrzeżenie o statusie prawnym z całego serwisu.
    """
    notices = [block.value for block in regulamin.body if block.block_type == "notice"]
    headings = [block.value["text"] for block in regulamin.body if block.block_type == "heading"]

    assert len(notices) == 1
    assert notices[0]["tone"] == "warning"
    assert LEGAL_STATUS in notices[0]["text"].source
    # Nagłówek sekcji się nie renderuje – ramka sama jest wyróżnieniem.
    assert "Status dokumentu" not in headings
    # Ramka stoi nad treścią dokumentu, a nie gdzieś w środku.
    assert regulamin.body[0].block_type == "notice"


def test_seed_reads_the_new_subtitle_into_the_intro(regulamin):
    """Podtytuł ze strony tytułowej trafia do wprowadzenia – to on nazywa model zawodów."""
    assert "trzyetapowy konkurs edukacyjny" in regulamin.intro
    assert "FUNDACJA QUANTUM AI" in regulamin.intro


def test_seed_keeps_nested_enumeration_inside_its_own_item(regulamin):
    """Wyliczenie zagnieżdżone zostaje w swojej pozycji – i nie powtarza się obok niej.

    Konwerter zwraca je jako ``<ol>`` wewnątrz ``<li>``, a ``Node.find_all`` chodzi po całym
    poddrzewie: policzone rekurencyjnie pozycje wewnętrzne trafiłyby na stronę dwa razy – raz
    w treści ustępu 1., raz jako kolejne ustępy. Wyglądałoby to jak dopisane do regulaminu
    punkty, których w dokumencie nie ma, więc import bierze wyłącznie ``<li>`` pierwszego poziomu.
    """
    blocks = list(regulamin.body)
    index = next(
        i for i, b in enumerate(blocks) if b.block_type == "heading" and b.value["anchor"] == "par-2"
    )
    html = blocks[index + 1].value.source

    # § 2 ma dwa ustępy; pierwszy zawiera pięciopunktowe wyliczenie celów.
    assert "<li>Celami Olimpiady są w szczególności:<ol>" in html
    assert html.count("<li>") == 2 + 5
    assert html.count("rozbudzanie zainteresowania fizyką kwantową") == 1
    # Lista ustępów jest jedna – numeracja na stronie to numeracja z dokumentu.
    assert html.startswith("<ol>")
    assert html.endswith("</ol>")


def test_seed_skips_printed_table_of_contents(regulamin):
    """„Spis rozdziałów” z pliku pomijamy – stronę obsługuje spis generowany z kotwic."""
    texts = [block.value["text"] for block in regulamin.body if block.block_type == "heading"]
    body = str(regulamin.body)

    assert "Spis rozdziałów" not in texts
    assert "Organizator" in texts
    # Wraz z nagłówkiem znika cała zawartość sekcji: papierowa lista rozdziałów i zdanie
    # o numerach stron w stopce, które na stronie internetowej nie znaczy nic.
    assert "Numery stron są widoczne w stopce" not in body


def test_page_keeps_the_words_of_the_pdf(regulamin):
    """Strona ma nieść treść pliku, a nie jej większość.

    Liczba paragrafów i rozdziałów łapie zgubioną sekcję; ten test łapie zgubiony akapit
    **w środku** sekcji – najcichszy błąd importu dokumentu prawnego.
    """
    lines = (PDF_TEXT / PDF_TEXT_FILE).read_text(encoding="utf-8").splitlines()
    kept = [
        line
        for line in lines
        if not (
            PAGE_MARKER_RE.match(line.strip())
            or RUNNING_HEAD_RE.match(line.strip())
            or RUNNING_FOOT_RE.match(line.strip())
        )
    ]
    expected = " ".join(kept).split()
    # Znacznik zamieniamy na spację: ``</p><p>`` bez tego sklejałoby ostatnie słowo akapitu
    # z pierwszym słowem następnego i każde takie miejsce gubiłoby jedno słowo z licznika.
    html = " ".join([regulamin.title, str(regulamin.intro), str(regulamin.body)])
    actual = strip_tags(html.replace("<", " <")).split()

    ratio = len(actual) / len(expected)
    assert ratio >= MIN_WORD_RATIO, (
        f"regulamin ma {len(actual)} słów wobec {len(expected)} w PDF-ie ({ratio:.0%}) – "
        "sprawdź, czy parser nie zgubił akapitu albo pozycji listy"
    )


def test_seed_is_idempotent(regulamin):
    Document = get_document_model()

    call_command("seed_regulamin", verbosity=0)

    page = DocumentPage.objects.get(slug="regulamin")
    assert DocumentPage.objects.filter(slug="regulamin").count() == 1
    assert Document.objects.filter(title=DOCUMENT_TITLE).count() == 1
    assert Document.objects.filter(title=PDF_DOCUMENT_TITLE).count() == 1
    assert len(page.body) == len(regulamin.body)
    assert page.attachments.count() == 2


# --- widok publiczny ----------------------------------------------------------------------------


def test_regulamin_page_renders_content_and_download_link(web_client, regulamin):
    response = web_client.get(PAGE_PATH)
    content = response.content.decode()

    assert response.status_code == 200
    assert regulamin.title in content
    # Metryka dokumentu: wersja, data i status.
    assert f"Wersja {VERSION}" in content
    assert f'datetime="{DOCUMENT_DATE}"' in content
    assert STATUS in content
    # Załączniki: link do widoku dokumentów Wagtaila, nie do adresu obiektu w buckecie.
    for item in regulamin.attachments.all():
        assert f'href="{item.document.url}"' in content
    assert "§ 24" in content
    # Zastrzeżenie o statusie prawnym jest na stronie, nie tylko w pliku.
    assert LEGAL_STATUS in content


def test_regulamin_page_offers_the_pdf_above_the_content(web_client, regulamin):
    """Uwaga organizatora z 16.09: dokument ma być plikiem, a nie tylko podstroną.

    Karta „Do pobrania” z kompletem plików zostaje niżej, ale sam PDF dostaje przycisk **nad**
    treścią: czytelnik, który wszedł tu z etykiety zgody przy rejestracji albo z pisma, przychodzi
    po dokument i nie ma go szukać pod dwudziestoma czterema paragrafami.
    """
    content = web_client.get(PAGE_PATH).content.decode()
    pdf = next(item for item in regulamin.attachments.all() if item.is_pdf)

    assert "Pobierz PDF" in content
    button = content.index("Pobierz PDF")
    assert button < content.index('class="doc-body')
    assert f'href="{pdf.document.url}" download' in content


def test_a_document_without_a_pdf_has_no_download_button(web_client, regulamin):
    """Bez pliku nie ma czego obiecywać – przycisk prowadzący donikąd byłby gorszy od jego braku."""
    regulamin.attachments.all().delete()

    content = web_client.get(PAGE_PATH).content.decode()

    assert "Pobierz PDF" not in content
    assert "Do pobrania" not in content


def test_regulamin_page_renders_markup_not_escaped_source(web_client, regulamin):
    content = web_client.get(PAGE_PATH).content.decode()

    # Podwójne escapowanie w imporcie objawiłoby się „<p>” jako tekstem na stronie.
    assert "&lt;p&gt;" not in content
    assert "&lt;li&gt;" not in content
    assert "&lt;strong&gt;" not in content
    # Listy numerowane regulaminu renderują się jako listy.
    assert "<ol>" in content


def test_regulamin_page_has_anchored_table_of_contents(web_client, regulamin):
    content = web_client.get(PAGE_PATH).content.decode()

    assert len(regulamin.chapters()) == CHAPTERS
    for number in range(1, CHAPTERS + 1):
        assert f'href="#rozdzial-{number}"' in content
        assert f'id="rozdzial-{number}"' in content
    # Paragrafy też mają kotwice – pismo do komisji może odesłać wprost do „#par-16”.
    assert 'id="par-16"' in content


def test_regulamin_lives_in_the_documents_section_of_the_menu(web_client, regulamin):
    """Regulamin nie jest już osobną pozycją paska – prowadzi do niego rozwijana „Dokumenty”."""
    content = web_client.get("/").content.decode()
    menu = content.split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]

    assert regulamin.get_parent().specific_class is DocumentIndexPage
    assert '<details class="nav-menu">' in menu
    assert f'href="{PAGE_PATH}"' in menu
    assert '<a class="nav__link" href="/regulamin/"' not in menu
    assert menu.index("/zadania/") < menu.index("/dokumenty/") < menu.index("/archiwum/")
