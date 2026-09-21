"""``manage.py build_guardian_consent_pdf`` – wzór zgody opiekuna jako plik do wydruku.

Organizator poprosił o **szablon** zgody rodzica lub opiekuna prawnego: coś, co się pobiera,
drukuje, wypełnia długopisem i podpisuje. Sama strona ``/dokumenty/zgoda-opiekuna/`` tego nie
załatwia – wydruk z przeglądarki wychodzi z paginą przeglądarki, łamie się tam, gdzie akurat
skończy się okno, i wygląda jak wydruk strony WWW, a nie jak formularz do podpisania.

Trzy decyzje, na których ta komenda stoi:

- **źródłem jest markdown, a nie drugi plik z treścią.** Formularz powstaje z tego samego
  ``fixtures/legacy/zgoda-opiekuna.md``, z którego ``seed_legacy_content`` buduje stronę. Gdyby
  treść PDF-a była osobnym plikiem, to po pierwszej poprawce brzmienia istniałyby dwa różne
  oświadczenia o tej samej nazwie – a oświadczenie ma jedno brzmienie albo nie jest dowodem,
- **plik jest generowany komendą i trzymany w repozytorium** (``fixtures/documents/``), a nie
  składany przy każdym pobraniu. Do biblioteki Wagtaila wgrywa go ``seed_legacy_content`` tak
  samo, jak PDF-y organizatora: dzięki temu na produkcji nie ma różnicy między „plikiem
  organizatora” a „plikiem naszym” – oba są wersjami do pobrania przypiętymi do strony,
  a wdrożenie nie potrzebuje reportlaba w ścieżce żądania,
- **ramka „wersja robocza do akceptacji” nie trafia na formularz.** Zostaje na stronie (tam jest
  informacją dla organizatora i dla nas, kto ma ten tekst jeszcze zatwierdzić), ale kartka, którą
  opiekun podpisuje przy dziecku, ma być formularzem, a nie projektem dokumentu ze stemplem
  „nie obowiązuje”. To jedyne miejsce, w którym PDF różni się od strony, i jest to różnica
  **świadoma** – patrz ``SKIP_LEADING_NOTICE``.

Składnia markdowna jest tym samym zamkniętym podzbiorem, co w ``apps.cms.legacy_markdown``
(nagłówki, akapity, listy, tabela dwukolumnowa, ramka ``>``, ``**pogrubienie**``, ``[link](adres)``).
Parser jest tu jednak **osobny i własny**: tamten produkuje bloki StreamFielda z ``RichText``,
czyli HTML dla Wagtaila, a reportlab potrzebuje flowables. Przejście „markdown → HTML → flowables”
znaczyłoby pisanie parsera HTML-a, więc jest o jeden format dłuższe niż potrzeba.

Adresy względne (``/dokumenty/rodo/``) zostają w PDF-ie **samym tekstem**, bez odnośnika: poza
przeglądarką ścieżka bez domeny nie prowadzi donikąd, a martwy odnośnik w dokumencie do podpisu
jest gorszy niż jego brak. Adresy bezwzględne (``mailto:``, ``https://``) zostają klikalne.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

#: ``…/apps/cms/management/commands/`` → ``…/apps/cms/fixtures/``.
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SOURCE = FIXTURES / "legacy" / "zgoda-opiekuna.md"

#: Pliki **wytworzone w repozytorium**, w odróżnieniu od ``fixtures/legacy/pdf/`` z podpisanymi
#: dokumentami organizatora. Katalogi są dwa, bo to dwa różne rodzaje plików: tamte przyszły
#: z zewnątrz i nie wolno ich odtworzyć, ten powstaje z markdowna jedną komendą.
OUTPUT = FIXTURES / "documents" / "zgoda-opiekuna.pdf"

#: Ramka otwierająca plik źródłowy („wersja robocza do akceptacji organizatora”) nie wchodzi do
#: formularza – uzasadnienie w docstringu modułu. Pomijamy **wyłącznie** ramkę stojącą przed
#: pierwszym śródtytułem: ramki w treści (gdyby doszły) są częścią oświadczenia.
SKIP_LEADING_NOTICE = True

TITLE_RE = re.compile(r"^#\s+(.*)$")
HEADING_RE = re.compile(r"^(#{2,3})\s+(.*)$")
UNORDERED_RE = re.compile(r"^[-*]\s+(.*)$")
ORDERED_RE = re.compile(r"^(\d+)\.\s+(.*)$")
QUOTE_RE = re.compile(r"^>\s?(.*)$")
TABLE_SEPARATOR_RE = re.compile(r"^\|[\s|:-]+\|$")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")

#: Adresy, które w pliku do wydruku mają zostać klikalne. Reszta (ścieżki serwisu) zostaje tekstem.
ABSOLUTE_SCHEMES = ("http://", "https://", "mailto:", "tel:")

#: Stopka każdej strony. Formularz krąży luzem – kartka wyrwana z kompletu ma powiedzieć, czyj to
#: dokument i ile stron liczy całość, bo inaczej nie widać, że czegoś brakuje.
FOOTER_LEFT = "Olimpiada Kwantowa – zgoda rodzica lub opiekuna prawnego"
PDF_TITLE = "Zgoda rodzica lub opiekuna prawnego – Olimpiada Kwantowa"
PDF_SUBJECT = "Formularz zgody rodzica lub opiekuna prawnego na udział w Olimpiadzie Kwantowej"

#: Dokumenty składane tą komendą: źródło (markdown strony), plik wynikowy i metadane PDF-a.
#: Drugi wpis doszedł 21.09.2026: organizator podał nowy skład komitetów (tytuły, afiliacje,
#: dwie nowe osoby), a przy stronie wisiał podpisany PDF ze składem z 7 września – plik do
#: pobrania przeczyłby stronie, przy której stoi. Nowy plik powstaje z **tego samego**
#: ``komitety.md``, co strona, więc nie da się ich rozjechać, i zajmuje miejsce starego pod tą
#: samą nazwą (``fixtures/legacy/pdf/``), żeby ``seed_legacy_content`` podmienił treść dokumentu
#: w bibliotece, a nie dołożył drugiego.
DOCUMENTS = {
    "zgoda-opiekuna": {
        "source": SOURCE,
        "output": OUTPUT,
        "title": PDF_TITLE,
        "subject": PDF_SUBJECT,
        "footer_left": FOOTER_LEFT,
    },
    "komitety": {
        "source": FIXTURES / "legacy" / "komitety.md",
        "output": FIXTURES / "legacy" / "pdf" / "Sklad-komitetow-Olimpiady-Kwantowej.pdf",
        "title": "Skład komitetów Olimpiady Kwantowej",
        "subject": "Komitet Merytoryczny i Komitet Organizacyjny Olimpiady Kwantowej",
        "footer_left": "Olimpiada Kwantowa – skład komitetów",
    },
}

PAGE_MARGIN_MM = 18
BOTTOM_MARGIN_MM = 20


def inline(text: str) -> str:
    """Formatowanie liniowe markdowna → znaczniki akapitu reportlaba.

    Kolejność jest ta sama, co w ``apps.cms.legacy_markdown.inline``: escape, potem link, potem
    pogrubienie, potem kursywa. Escape musi być pierwszy, bo w tekście stoją ``&`` i ``<``,
    a reportlab czyta zawartość ``Paragraph`` jako mini-HTML i przewróciłby się na nich.
    """
    html = escape(text.strip(), quote=False)

    def link(match: re.Match) -> str:
        label, href = match.group(1), match.group(2)
        if href.startswith(ABSOLUTE_SCHEMES):
            return f'<a href="{href}" color="#1d4ed8">{label}</a>'
        return label

    html = LINK_RE.sub(link, html)
    html = BOLD_RE.sub(r"<b>\1</b>", html)
    return ITALIC_RE.sub(r"<i>\1</i>", html)


def _styles():
    """Arkusz stylów formularza. Osobny, bo ``getSampleStyleSheet`` stoi na Helvetice bez ogonków."""
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle

    from apps.results.certificates import FONT_BOLD, FONT_REGULAR

    body = ParagraphStyle(
        "body",
        fontName=FONT_REGULAR,
        fontSize=9,
        leading=12.5,
        spaceAfter=5,
        alignment=TA_JUSTIFY,
    )
    return {
        "title": ParagraphStyle(
            "title",
            parent=body,
            fontName=FONT_BOLD,
            fontSize=15,
            leading=19,
            spaceAfter=10,
            alignment=0,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=body,
            fontName=FONT_BOLD,
            fontSize=11,
            leading=14,
            spaceBefore=11,
            spaceAfter=4,
            alignment=0,
            keepWithNext=1,
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=body,
            fontName=FONT_BOLD,
            fontSize=9.5,
            leading=13,
            spaceBefore=7,
            spaceAfter=3,
            alignment=0,
            keepWithNext=1,
        ),
        "body": body,
        "item": ParagraphStyle("item", parent=body, spaceAfter=2, alignment=0),
        "cell": ParagraphStyle("cell", parent=body, fontSize=8, leading=10.5, spaceAfter=0),
        "footer": ParagraphStyle("footer", parent=body, fontSize=7.5, leading=9, alignment=0),
    }


class _Story:
    """Zbiera flowables i domyka bufory (akapit, lista, tabela) zanim zacznie się następny blok.

    Bufory są trzy, bo trzy konstrukcje markdowna ciągną się przez wiele wierszy i kończą się
    dopiero pustym wierszem albo blokiem innego rodzaju. Domknięcie „samo z siebie” (przy każdej
    zmianie rodzaju wiersza) jest jedyną regułą, która nie wymaga patrzenia w przód.
    """

    def __init__(self, styles: dict) -> None:
        self.styles = styles
        self.flowables: list = []
        self.paragraph: list[str] = []
        self.items: list[tuple[str, str]] = []
        self.table: list[str] = []
        self.seen_heading = False

    def flush(self) -> None:
        self._flush_paragraph()
        self._flush_items()
        self._flush_table()

    def _flush_paragraph(self) -> None:
        from reportlab.platypus import Paragraph

        if self.paragraph:
            self.flowables.append(Paragraph(inline(" ".join(self.paragraph)), self.styles["body"]))
            self.paragraph.clear()

    def _flush_items(self) -> None:
        from reportlab.platypus import ListFlowable, ListItem, Paragraph

        if not self.items:
            return
        ordered = self.items[0][0] == "ordered"
        entries = [
            ListItem(Paragraph(inline(text), self.styles["item"]), leftIndent=14) for _, text in self.items
        ]
        self.flowables.append(
            ListFlowable(
                entries,
                bulletType="1" if ordered else "bullet",
                bulletFontName=self.styles["item"].fontName,
                bulletFontSize=8,
                leftIndent=14,
                spaceAfter=5,
            )
        )
        self.items.clear()

    def _flush_table(self) -> None:
        """Tabela dwukolumnowa → prawdziwa ``Table``.

        Na stronie ta sama tabela jest listą definicji (czytnik ekranu, telefon), ale na papierze
        para „cel → podstawa prawna” czyta się w dwóch kolumnach: oko przebiega po lewej i sięga
        w prawo tylko tam, gdzie zatrzyma je treść. Nagłówek powtarzamy na każdej stronie
        (``repeatRows``), bo tabela bywa dłuższa niż kartka.
        """
        from reportlab.lib import colors
        from reportlab.platypus import Paragraph, Table, TableStyle

        if not self.table:
            return
        rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in self.table
            if not TABLE_SEPARATOR_RE.match(line.strip())
        ]
        self.table.clear()
        if not rows:
            return
        data = [[Paragraph(inline(cell), self.styles["cell"]) for cell in row] for row in rows]
        table = Table(data, colWidths=["45%", "55%"], repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9ca3af")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        self.flowables.append(table)

    def add_heading(self, text: str, level: int) -> None:
        from reportlab.platypus import Paragraph

        self.flush()
        self.seen_heading = True
        style = self.styles["h2" if level == 2 else "h3"]
        self.flowables.append(Paragraph(inline(text), style))

    def add_title(self, text: str) -> None:
        from reportlab.platypus import Paragraph

        self.flush()
        self.flowables.append(Paragraph(inline(text), self.styles["title"]))


def build_story(markdown: str, styles: dict) -> list:
    """Markdown → lista flowables. Jedno przejście po wierszach, bez patrzenia w przód."""
    story = _Story(styles)
    quote: list[str] = []

    def flush_quote() -> None:
        from reportlab.platypus import Paragraph

        if not quote:
            return
        # Ramka przed pierwszym śródtytułem to metryka projektu, a nie treść oświadczenia.
        if not (SKIP_LEADING_NOTICE and not story.seen_heading):
            story.flowables.append(Paragraph(inline(" ".join(quote)), styles["body"]))
        quote.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_quote()
            story.flush()
            continue

        if (match := QUOTE_RE.match(stripped)) is not None:
            story.flush()
            quote.append(match.group(1))
            continue
        flush_quote()

        if (match := TITLE_RE.match(stripped)) is not None:
            story.add_title(match.group(1))
            continue
        if (match := HEADING_RE.match(stripped)) is not None:
            story.add_heading(match.group(2), len(match.group(1)))
            continue
        if stripped.startswith("|"):
            story._flush_paragraph()
            story._flush_items()
            story.table.append(stripped)
            continue
        if (match := UNORDERED_RE.match(stripped)) is not None:
            story._flush_paragraph()
            story._flush_table()
            story.items.append(("bullet", match.group(1)))
            continue
        if (match := ORDERED_RE.match(stripped)) is not None:
            story._flush_paragraph()
            story._flush_table()
            story.items.append(("ordered", match.group(2)))
            continue

        story._flush_items()
        story._flush_table()
        story.paragraph.append(stripped)

    flush_quote()
    story.flush()
    return story.flowables


def _draw_footer(canvas, doc) -> None:
    """Stopka z numerem strony. Rysowana na płótnie, bo flowable nie wie, na której jest stronie.

    Napis po lewej bierze się z dokumentu (``doc.footer_left``), bo tym samym składem powstaje
    więcej niż jeden plik – patrz ``DOCUMENTS``.
    """
    from reportlab.lib.units import mm

    from apps.results.certificates import FONT_REGULAR

    baseline = 12 * mm
    canvas.saveState()
    canvas.setFont(FONT_REGULAR, 7.5)
    canvas.setFillGray(0.35)
    canvas.drawString(doc.leftMargin, baseline, getattr(doc, "footer_left", FOOTER_LEFT))
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, baseline, f"strona {canvas.getPageNumber()}")
    canvas.restoreState()


def render_pdf(
    markdown: str,
    *,
    title: str = PDF_TITLE,
    subject: str = PDF_SUBJECT,
    footer_left: str = FOOTER_LEFT,
) -> bytes:
    """Składa dokument i zwraca bajty PDF-a.

    Wartości domyślne opisują formularz zgody opiekuna – wywołanie bez argumentów daje ten sam
    plik co do bajtu, co przed dodaniem drugiego dokumentu (pilnuje tego
    ``test_guardian_consent_pdf_is_rebuilt_byte_for_byte``).
    """
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    from apps.results.certificates import register_fonts

    register_fonts()
    styles = _styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=PAGE_MARGIN_MM * mm,
        rightMargin=PAGE_MARGIN_MM * mm,
        topMargin=PAGE_MARGIN_MM * mm,
        bottomMargin=BOTTOM_MARGIN_MM * mm,
        title=title,
        author="Fundacja Quantum AI",
        subject=subject,
        # Plik leży w repozytorium, więc musi być **powtarzalny**: bez tego reportlab wpisuje
        # do środka datę złożenia i identyfikator z losowości, czyli ta sama treść dawałaby za
        # każdym przebiegiem inne bajty. Skutek byłby podwójny – szum w diffie i ponowne wgranie
        # pliku do biblioteki Wagtaila przy każdym seedzie (``ensure_document`` porównuje SHA-256).
        invariant=1,
    )
    document.footer_left = footer_left
    document.build(build_story(markdown, styles), onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buffer.getvalue()


class Command(BaseCommand):
    help = "Składa PDF z markdowna strony: formularz zgody opiekuna (domyślnie) albo skład komitetów."

    def add_arguments(self, parser):
        parser.add_argument(
            "--document",
            choices=sorted(DOCUMENTS),
            default="zgoda-opiekuna",
            help="Który dokument złożyć (domyślnie formularz zgody opiekuna).",
        )
        parser.add_argument("--output", default="", help="Plik wynikowy (domyślnie właściwy dla dokumentu).")

    def handle(self, *args, **options):
        spec = DOCUMENTS[options["document"]]
        source = spec["source"]
        if not source.exists():  # pragma: no cover - brak pliku znaczyłby zepsute repozytorium
            raise CommandError(f"Brak pliku źródłowego {source}.")
        target = Path(options["output"] or spec["output"])
        target.parent.mkdir(parents=True, exist_ok=True)
        data = render_pdf(
            source.read_text(encoding="utf-8"),
            title=spec["title"],
            subject=spec["subject"],
            footer_left=spec["footer_left"],
        )
        target.write_bytes(data)
        self.stdout.write(f"zapisano: {target} ({len(data)} bajtów)")
