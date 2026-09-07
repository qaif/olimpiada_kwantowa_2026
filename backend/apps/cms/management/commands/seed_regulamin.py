"""``manage.py seed_regulamin`` – „Regulamin Olimpiady Kwantowej” jako strona CMS.

Strona stoi w sekcji dokumentów: ``/dokumenty/regulamin/``. Sekcję zakłada (albo odnajduje)
``apps.cms.site_tree.ensure_document_index``, a stronę stojącą jeszcze pod stroną główną –
w bazie sprzed przeniesienia dokumentów – ta sama komenda przenosi pod sekcję, zachowując
jej identyfikator, rewizje i odnośniki wewnętrzne.

Komenda importuje dwa pliki z ``apps/cms/fixtures/regulamin/``:

- ``regulamin-mammoth.html`` – konwersja pliku .docx (mammoth) na płaską listę ``p``/``h1``/``h2``/
  ``ol``/``ul``/``table``. Z tego powstaje ``DocumentPage.body``,
- ``Regulamin-Olimpiady-Kwantowej.docx`` – oryginał, ładowany do biblioteki dokumentów Wagtaila
  i przypinany do strony jako plik źródłowy. PDF podpisany przez organizatora dokłada
  ``seed_legacy_content``; ta komenda go nie usuwa, jeśli już wisi na stronie – patrz
  ``_attachment_specs``.

**Treść regulaminu jest daną, nie instrukcją.** Komenda nie przeredagowuje ani jednego zdania –
poprawia wyłącznie strukturę HTML tam, gdzie konwerter ją zgubił (patrz ``_SectionBuffer``).

Idempotencja: strona jest rozpoznawana po slugu ``regulamin`` w sekcji dokumentów, dokument – po
tytule. Drugi przebieg nadpisuje treść tą samą treścią i nie tworzy ani drugiej strony, ani
drugiego pliku w buckecie.

Uruchamiać w dev/staging oraz jednorazowo przy wdrożeniu dokumentu; potem tekst redaguje się
w ``/cms/``. Kolejny przebieg komendy skasowałby te poprawki – to świadomy koszt narzędzia
importującego, nie tryb pracy redakcyjnej.

Mapowanie struktury dokumentu (decyzje opisane w README zadania):

===========================================  ==================================================
źródło (mammoth)                             wynik
===========================================  ==================================================
strona tytułowa (3 akapity)                  ``intro``
tabela Wersja/Data/Status                    ``version_label``/``document_date``/``status_label``
akapit „WAŻNY STATUS PRAWNY” + następny       blok ``notice`` (tone ``warning``)
``h1`` „Spis rozdziałów” + lista             pominięte – spis generuje szablon z kotwic
``h1`` „Status dokumentu” + tabela           pominięte – dosłowne powtórzenie ramki wyżej
``h2`` „Organizator” + tabela                ``heading`` (poziom 3) + akapit z listą definicji
``h1`` „Rozdział N. …”                       ``heading`` poziom 2, kotwica ``rozdzial-N``
``h2`` „§ n. …”                              ``heading`` poziom 3, kotwica ``par-n``
``h1`` pozostałe (źródła, zatwierdzenie)     ``heading`` poziom 2 poza spisem rozdziałów
``ol``/``ul``/``p`` sekcji                   jeden blok ``paragraph`` na sekcję
===========================================  ==================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.rich_text import RichText

from apps.cms.attachments import LABEL_PDF, LABEL_SOURCE_DOCX, ensure_document, set_attachments
from apps.cms.legacy_markdown import slugify_anchor
from apps.cms.models import DocumentPage, DocumentPageAttachment, HomePage
from apps.cms.site_tree import ensure_document_index, take_document_page

#: ``…/apps/cms/management/commands/`` → ``…/apps/cms/fixtures/regulamin/``.
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "regulamin"
HTML_SOURCE = FIXTURES / "regulamin-mammoth.html"
DOCX_SOURCE = FIXTURES / "Regulamin-Olimpiady-Kwantowej.docx"

PAGE_SLUG = "regulamin"
PAGE_TITLE = "Regulamin"
DOCUMENT_TITLE = "Regulamin Olimpiady Kwantowej v1.0 (DOCX)"
#: Tytuł PDF-a wgrywanego przez ``seed_legacy_content`` – tu potrzebny tylko po to, żeby
#: przy powtórnym przebiegu tej komendy plik do druku został na stronie i został pierwszy.
PDF_DOCUMENT_TITLE = "Regulamin Olimpiady Kwantowej v1.0 (PDF)"

#: Nagłówki pomijane wraz z zawartością aż do kolejnego nagłówka. „Spis rozdziałów” zastępuje
#: spis generowany z kotwic (papierowa lista bez odnośników byłaby na stronie martwa),
#: „Status dokumentu” jest w źródle dosłownym powtórzeniem ramki „WAŻNY STATUS PRAWNY”.
SKIPPED_SECTIONS = frozenset({"Spis rozdziałów", "Status dokumentu"})

#: Etykiety wiersza tabeli metryki → pole modelu.
META_ROWS = {"Wersja": "version_label", "Data dokumentu": "document_date", "Status": "status_label"}

NOTICE_HEADING = "WAŻNY STATUS PRAWNY"

CHAPTER_RE = re.compile(r"^Rozdział\s+([IVXLC]+)\.")
PARAGRAPH_RE = re.compile(r"^§\s*(\d+)\.")
DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]+)\s+(\d{4})")

MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}

#: Znaczniki, które przepuszczamy do RichText. Zgodne z ``RICH_TEXT_FEATURES`` (whitelist edytora)
#: – gdyby konwerter wypluł cokolwiek spoza listy, blok i tak by tego nie przyjął w edytorze.
ALLOWED_TAGS = {"p", "ol", "ul", "li", "strong", "em", "a"}
ALLOWED_ATTRS = {"a": {"href"}}


def roman_to_int(value: str) -> int:
    total = 0
    previous = 0
    for char in reversed(value):
        current = ROMAN[char]
        total = total - current if current < previous else total + current
        previous = max(previous, current)
    return total


# --- mini-DOM ----------------------------------------------------------------------------------
#
# Zależności projektu nie zawierają BeautifulSoup (patrz ``pyproject.toml``), a wyciąganie
# struktury regexpami z dokumentu prawnego jest proszeniem się o cichy błąd. ``html.parser``
# ze standardowej biblioteki wystarcza: budujemy z niego drzewo węzłów i dopiero po nim chodzimy.


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)  # Node | str

    def text(self) -> str:
        parts = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return "".join(parts).strip()

    def find_all(self, tag: str) -> list[Node]:
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag:
                    found.append(child)
                found.extend(child.find_all(tag))
        return found


class FragmentParser(HTMLParser):
    """Buduje drzewo ``Node`` z fragmentu HTML. Bez ``void``-elementów – konwerter ich nie tworzy."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_fragment(html: str) -> list[Node]:
    parser = FragmentParser()
    parser.feed(html)
    parser.close()
    return [child for child in parser.root.children if isinstance(child, Node)]


def render_inner(node: Node) -> str:
    """Zawartość węzła jako HTML ograniczony do ``ALLOWED_TAGS``.

    Tekst jest escapowany ponownie (parser rozwinął encje), więc do RichText trafia dokładnie to,
    co było w dokumencie – i nic, czego nie zna whitelist edytora.
    """
    out = []
    for child in node.children:
        if isinstance(child, str):
            out.append(escape(child, quote=False))
            continue
        inner = render_inner(child)
        if child.tag not in ALLOWED_TAGS:
            # Znacznik nieznany whitelistcie znika, jego treść zostaje – nie gubimy zdania.
            out.append(inner)
            continue
        allowed = ALLOWED_ATTRS.get(child.tag, set())
        attrs = "".join(
            f' {name}="{escape(value, quote=True)}"' for name, value in child.attrs.items() if name in allowed
        )
        out.append(f"<{child.tag}{attrs}>{inner}</{child.tag}>")
    return "".join(out).strip()


def table_rows(node: Node) -> list[list[Node]]:
    return [row.find_all("td") for row in node.find_all("tr")]


# --- składanie treści --------------------------------------------------------------------------


class SectionBuffer:
    """Zbiera akapity i listy jednej sekcji (od nagłówka do nagłówka) w jeden blok ``paragraph``.

    Naprawia przy okazji jedyny błąd strukturalny konwersji: mammoth przerywa listę numerowaną
    tam, gdzie w dokumencie wtrącono wyliczenie punktowane, i otwiera po nim **nową** ``<ol>``.
    W przeglądarce dawało to numerację „1., ●●●, 1.” zamiast „1., ●●●, 2.” – czyli inne numery
    ustępów niż w podpisanym dokumencie. Wyliczenie punktowane wpinamy więc do ostatniego ``li``
    (tam należy merytorycznie: jest rozwinięciem zdania „… w szczególności:”), a kolejne ``ol``
    dokładamy jako dalsze pozycje tej samej listy.
    """

    def __init__(self):
        self.chunks: list[str] = []
        self.items: list[str] | None = None

    def add_paragraph(self, html: str) -> None:
        self.close_list()
        if html:
            self.chunks.append(f"<p>{html}</p>")

    def add_ordered(self, items: list[str]) -> None:
        if self.items is None:
            self.items = []
        self.items.extend(items)

    def add_unordered(self, items: list[str]) -> None:
        markup = "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
        if self.items:
            self.items[-1] += markup
        else:
            self.close_list()
            self.chunks.append(markup)

    def close_list(self) -> None:
        if self.items:
            self.chunks.append("<ol>" + "".join(f"<li>{item}</li>" for item in self.items) + "</ol>")
        self.items = None

    def flush(self) -> str:
        self.close_list()
        html = "".join(self.chunks)
        self.chunks = []
        return html


def list_items(node: Node) -> list[str]:
    return [html for html in (render_inner(li) for li in node.find_all("li")) if html]


def definition_paragraph(node: Node) -> str:
    """Tabela dwukolumnowa → akapity „**Etykieta:** wartość”.

    Tabela w dokumencie jest tu układem strony, nie danymi tabelarycznymi (dwie kolumny, brak
    nagłówków, jeden fakt w wierszu). Lista definicji czyta się w czytniku ekranu i na telefonie,
    a ``<table>`` w bloku RichText i tak nie przeszłaby przez whitelistę.
    """
    parts = []
    for cells in table_rows(node):
        if len(cells) != 2:
            continue
        label = cells[0].text()
        value = render_inner(cells[1].find_all("p")[0]) if cells[1].find_all("p") else cells[1].text()
        if label and value:
            parts.append(f"<p><strong>{escape(label, quote=False)}:</strong> {value}</p>")
    return "".join(parts)


def parse_document_date(raw: str) -> date | None:
    match = DATE_RE.match(raw.strip())
    if match is None:
        return None
    month = MONTHS.get(match.group(2).lower())
    return date(int(match.group(3)), month, int(match.group(1))) if month else None


def heading_block(text: str, level: str, anchor: str, *, in_toc: bool) -> tuple[str, dict]:
    return ("heading", {"text": text, "level": level, "anchor": anchor, "in_toc": in_toc})


def build_content(nodes: list[Node]) -> tuple[dict, list[tuple[str, object]]]:
    """Zwraca (pola metryki + intro, lista bloków StreamField)."""
    meta: dict = {"intro": "", "version_label": "", "document_date": None, "status_label": ""}
    blocks: list[tuple[str, object]] = []
    buffer = SectionBuffer()

    def flush() -> None:
        html = buffer.flush()
        if html:
            blocks.append(("paragraph", RichText(html)))

    index = 0
    # --- strona tytułowa: wszystko przed pierwszym nagłówkiem ---------------------------------
    intro_parts: list[str] = []
    seen_meta_table = False
    pending_notice: list[str] = []
    while index < len(nodes) and nodes[index].tag not in {"h1", "h2"}:
        node = nodes[index]
        index += 1
        if node.tag == "table":
            for cells in table_rows(node):
                if len(cells) == 2 and cells[0].text() in META_ROWS:
                    field_name = META_ROWS[cells[0].text()]
                    raw = cells[1].text()
                    meta[field_name] = parse_document_date(raw) if field_name == "document_date" else raw
                    seen_meta_table = True
            continue
        if node.tag != "p":
            continue
        html = render_inner(node)
        if not html:
            continue
        if not seen_meta_table:
            intro_parts.append(f"<p>{html}</p>")
        elif node.text() == NOTICE_HEADING or pending_notice:
            # Nagłówek ramki i następujący po nim akapit tworzą jedno zastrzeżenie.
            pending_notice.append(f"<p>{html}</p>")
            if len(pending_notice) == 2:
                blocks.append(("notice", {"tone": "warning", "text": RichText("".join(pending_notice))}))
                pending_notice = []
        else:
            buffer.add_paragraph(html)
    flush()
    meta["intro"] = "".join(intro_parts)

    # --- korpus dokumentu ----------------------------------------------------------------------
    skipping = False
    for node in nodes[index:]:
        if node.tag in {"h1", "h2"}:
            flush()
            text = node.text()
            skipping = text in SKIPPED_SECTIONS
            if skipping:
                continue
            chapter = CHAPTER_RE.match(text)
            paragraph = PARAGRAPH_RE.match(text)
            if chapter is not None:
                anchor = f"rozdzial-{roman_to_int(chapter.group(1))}"
                blocks.append(heading_block(text, "2", anchor, in_toc=True))
            elif paragraph is not None:
                blocks.append(heading_block(text, "3", f"par-{paragraph.group(1)}", in_toc=False))
            elif node.tag == "h1":
                blocks.append(heading_block(text, "2", slugify_anchor(text), in_toc=False))
            else:
                blocks.append(heading_block(text, "3", slugify_anchor(text), in_toc=False))
            continue
        if skipping:
            continue
        if node.tag == "p":
            buffer.add_paragraph(render_inner(node))
        elif node.tag == "ol":
            buffer.add_ordered(list_items(node))
        elif node.tag == "ul":
            buffer.add_unordered(list_items(node))
        elif node.tag == "table":
            flush()
            html = definition_paragraph(node)
            if html:
                blocks.append(("paragraph", RichText(html)))
    flush()
    return meta, blocks


# --- komenda -----------------------------------------------------------------------------------


class Command(BaseCommand):
    help = "Publikuje „Regulamin Olimpiady Kwantowej” pod /dokumenty/regulamin/. Idempotentne."

    @transaction.atomic
    def handle(self, *args, **options):
        home = HomePage.objects.first()
        if home is None:
            raise CommandError("Brak drzewa stron – uruchom najpierw `manage.py migrate`.")
        if not HTML_SOURCE.exists() or not DOCX_SOURCE.exists():
            raise CommandError(f"Brak plików źródłowych w {FIXTURES}.")

        meta, blocks = build_content(parse_fragment(HTML_SOURCE.read_text(encoding="utf-8")))
        document, action = ensure_document(DOCUMENT_TITLE, DOCX_SOURCE)

        index, index_created = ensure_document_index(home)
        page, moved = take_document_page(DocumentPage, index, home, PAGE_SLUG)
        created = page is None
        if created:
            page = DocumentPage(title=PAGE_TITLE, slug=PAGE_SLUG)
            index.add_child(instance=page)

        page.title = PAGE_TITLE
        # Dokument nie jest osobną pozycją paska nawigacji – prowadzi do niego rozwijana sekcja
        # „Dokumenty”, która czyta dzieci sekcji, a nie znacznik ``show_in_menus``.
        page.show_in_menus = False
        page.body = blocks
        for name, value in meta.items():
            setattr(page, name, value)
        page.save()
        set_attachments(page, DocumentPageAttachment, self._attachment_specs(document))

        # Świeży obiekt z bazy: rewizja serializuje także wiersze załączników, a te dopisaliśmy
        # przez ORM już po ``page.save()``. Publikacja rewizji zbudowanej ze starego obiektu
        # skasowałaby je ze strony opublikowanej.
        page = DocumentPage.objects.get(pk=page.pk)
        page.save_revision().publish()

        note = ""
        if index_created:
            note = ", utworzono sekcję /dokumenty/"
        elif moved:
            note = ", przeniesiono spod strony głównej"
        self.stdout.write(
            self.style.SUCCESS(
                f"seed_regulamin: {'utworzono' if created else 'zaktualizowano'} {page.url} "
                f"– {len(page.body)} bloków, {len(page.chapters())} rozdziałów, "
                f"dokument #{document.pk} ({document.url}, {action}){note}"
            )
        )

    def _attachment_specs(self, docx) -> list[tuple[object, str]]:
        """Pliki strony: podpisany PDF (jeśli już wgrany) przed plikiem źródłowym .docx.

        PDF wgrywa ``seed_legacy_content`` i to ona rozstrzyga o jego treści – tutaj interesuje
        nas wyłącznie to, żeby powtórne uruchomienie tej komendy go nie zdjęło ze strony.
        """
        Document = docx.__class__
        pdf = Document.objects.filter(title=PDF_DOCUMENT_TITLE).first()
        specs = [(docx, LABEL_SOURCE_DOCX)]
        if pdf is not None:
            specs.insert(0, (pdf, LABEL_PDF))
        return specs
