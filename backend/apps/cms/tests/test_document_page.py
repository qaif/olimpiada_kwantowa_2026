"""``DocumentPage`` + ``manage.py seed_regulamin`` – regulamin jako strona serwisu.

Testy pilnują trzech rzeczy, które przy imporcie dokumentu prawnego psują się najciszej:

1. **kompletność** – 10 rozdziałów i wszystkie 24 paragrafy, w kolejności z dokumentu.
   Zgubiony ``§`` na stronie regulaminu to nie literówka, tylko brakujący przepis,
2. **idempotencja** – komenda bywa uruchamiana przy każdym wdrożeniu dev-a; drugi przebieg nie
   może dołożyć ani drugiej strony ``/regulamin/``, ani drugiej kopii .docx w buckecie,
3. **brak surowego HTML-a** – treść idzie do ``RichText``, więc znaczniki mają się renderować,
   a nie wyświetlać jako tekst. Odwrotny błąd (podwójne escapowanie w imporcie) jest widoczny
   dopiero w przeglądarce.
"""

import pytest
from django.core.management import call_command
from wagtail.documents import get_document_model

from apps.cms.management.commands.seed_regulamin import DOCUMENT_TITLE
from apps.cms.models import DocumentPage

pytestmark = pytest.mark.django_db

CHAPTERS = 10
PARAGRAPHS = 24


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


def test_seed_fills_document_metadata_and_attachment(regulamin):
    assert regulamin.version_label == "1.0"
    assert regulamin.document_date.isoformat() == "2026-08-18"
    assert "Projekt do zatwierdzenia uchwałą Zarządu" in regulamin.status_label
    # Sama ``seed_regulamin`` przypina wyłącznie plik źródłowy; PDF do druku dokłada
    # ``seed_legacy_content`` – patrz apps/cms/tests/test_legacy_content.py.
    attachment = regulamin.attachments.get()
    assert attachment.document.title == DOCUMENT_TITLE
    assert attachment.document.filename.endswith(".docx")
    assert attachment.label == "Wersja źródłowa (DOCX)"
    assert attachment.is_pdf is False


def test_seed_keeps_list_numbering_in_one_ordered_list(regulamin):
    """Wtrącone wyliczenie punktowane nie może restartować numeracji ustępów.

    Konwerter zamykał ``<ol>`` przed każdym ``<ul>`` i otwierał nowy po nim, przez co ustęp 2.
    renderował się jako „1.”. Import wpina ``<ul>`` do ostatniego ``<li>``.
    """
    blocks = list(regulamin.body)
    index = next(
        i for i, b in enumerate(blocks) if b.block_type == "heading" and b.value["anchor"] == "par-2"
    )
    html = blocks[index + 1].value.source

    assert html.count("<ol>") == 1
    assert "<li>Celami Olimpiady są w szczególności:<ul>" in html


def test_seed_skips_printed_table_of_contents(regulamin):
    """„Spis rozdziałów” z pliku pomijamy – stronę obsługuje spis generowany z kotwic."""
    texts = [block.value["text"] for block in regulamin.body if block.block_type == "heading"]

    assert "Spis rozdziałów" not in texts
    assert "Organizator" in texts


def test_seed_is_idempotent(regulamin):
    Document = get_document_model()

    call_command("seed_regulamin", verbosity=0)

    assert DocumentPage.objects.filter(slug="regulamin").count() == 1
    assert Document.objects.filter(title=DOCUMENT_TITLE).count() == 1
    assert len(DocumentPage.objects.get(slug="regulamin").body) == len(regulamin.body)


# --- widok publiczny ----------------------------------------------------------------------------


def test_regulamin_page_renders_content_and_download_link(web_client, regulamin):
    response = web_client.get("/regulamin/")
    content = response.content.decode()

    assert response.status_code == 200
    assert regulamin.title in content
    # Metryka dokumentu: wersja, data i status.
    assert "Wersja 1.0" in content
    assert "2026" in content
    assert "Projekt do zatwierdzenia uchwałą Zarządu" in content
    # Załącznik: link do widoku dokumentów Wagtaila, nie do adresu obiektu w buckecie.
    assert f'href="{regulamin.attachments.get().document.url}"' in content
    assert "§ 24" in content


def test_regulamin_page_renders_markup_not_escaped_source(web_client, regulamin):
    content = web_client.get("/regulamin/").content.decode()

    # Podwójne escapowanie w imporcie objawiłoby się „<p>” jako tekstem na stronie.
    assert "&lt;p&gt;" not in content
    assert "&lt;li&gt;" not in content
    assert "&lt;strong&gt;" not in content
    # Listy numerowane regulaminu renderują się jako listy.
    assert "<ol>" in content


def test_regulamin_page_has_anchored_table_of_contents(web_client, regulamin):
    content = web_client.get("/regulamin/").content.decode()

    assert len(regulamin.chapters()) == CHAPTERS
    for number in range(1, CHAPTERS + 1):
        assert f'href="#rozdzial-{number}"' in content
        assert f'id="rozdzial-{number}"' in content
    # Paragrafy też mają kotwice – pismo do komisji może odesłać wprost do „#par-16”.
    assert 'id="par-16"' in content


def test_regulamin_appears_in_menu_between_zadania_and_archiwum(web_client, regulamin):
    content = web_client.get("/").content.decode()
    menu = content.split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]

    assert 'href="/regulamin/"' in menu
    assert menu.index("/zadania/") < menu.index("/regulamin/") < menu.index("/archiwum/")
