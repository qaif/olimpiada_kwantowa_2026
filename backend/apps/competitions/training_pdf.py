"""Skład PDF-ów zadań treningowych z ``fixtures/training/zadania.md``.

Po co składanie PDF-a mieszka w kodzie, a nie tylko w skrypcie: PDF-y są **artefaktem
wersjonowanym** razem ze źródłem (leżą w tym samym katalogu fixture'ów i wchodzą do repozytorium),
więc ktoś musi umieć je odtworzyć i sprawdzić, że odtworzenie daje to samo. Skrypt
``scripts/build_training_problem_pdfs.py`` jest cienką nakładką na ten moduł – dzięki temu test
importuje generator wprost, zamiast szukać pliku po ścieżce.

Co powstaje (w katalogu ``fixtures/training/``):

- ``zadanie-1.pdf`` … ``zadanie-4.pdf`` – treści zadań **bez odpowiedzi**, po jednym pliku
  (uczestnik dostaje dokładnie to, co ma rozwiązać),
- ``odpowiedzi.pdf`` – szkice rozwiązań **dla recenzentów i koordynatora**. Ten plik nie jest
  nigdzie podpinany publicznie: ``seed_training_problems`` wgrywa wyłącznie treści zadań.

``reportlab`` jest zależnością **dev**, a nie produkcyjną: PDF-y powstają raz, przy zmianie źródła,
i idą do repozytorium. Dlatego import biblioteki jest **leniwy** – moduł wolno zaimportować (i np.
odczytać z niego nazwy plików) tam, gdzie reportlaba nie ma. Bez tej ostrożności nazwa pliku
potrzebna komendzie ``seed_training_problems`` ciągnęłaby za sobą cały silnik składu.

Czcionki: komplet DejaVu leży obok, w ``fixtures/training/fonts/`` (licencja pozwala na
redystrybucję, patrz ``fonts/LICENSE-DejaVu.txt``). Bez tego notacja Diraca rozsypałaby się na
czworokąty: wbudowane czcionki reportlaba to Type 1 z latin-1, więc nie mają ani ⟨ ⟩, ani ⊗, ani Ψ.
``DejaVuMathTeXGyre`` jest **rezerwą znakową**: moduł czyta cmap obu plików i każdy znak, którego
nie ma w DejaVuSans, przełącza w locie na czcionkę matematyczną. Znak, którego nie ma w żadnej,
przerywa generowanie – lepszy błąd niż PDF z dziurą w treści zadania.

Składnia markdowna jest obsłużona **minimalnie i celowo**: nagłówki ``##``/``###``, akapity, pozycje
listy ``- **a)** …`` oraz pogrubienia/kursywy/linki w linii. Cytat blokowy (``>``) jest pomijany –
to nota redakcyjna dla czytelnika strony, a nie część polecenia. To nie jest parser markdowna do
ponownego użycia; parser treści redakcyjnych mieszka w ``apps.cms`` i nie ma z tym pliku nic
wspólnego.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Katalog fixture'ów – jedno źródło prawdy o tym, gdzie leżą markdown, czcionki i wyniki.
TRAINING_DIR = Path(__file__).resolve().parent / "fixtures" / "training"
SOURCE = TRAINING_DIR / "zadania.md"
FONT_DIR = TRAINING_DIR / "fonts"

#: Nazwy czcionek w PDF-ie. ``BODY`` i ``BOLD`` składają tekst, ``MATH`` jest rezerwą znakową.
BODY_FONT = "DejaVuSans"
BOLD_FONT = "DejaVuSans-Bold"
MATH_FONT = "DejaVuMath"
FONT_FILES = {
    BODY_FONT: "DejaVuSans.ttf",
    BOLD_FONT: "DejaVuSans-Bold.ttf",
    MATH_FONT: "DejaVuMathTeXGyre.ttf",
}

#: Nagłówek każdej strony. Jedno zdanie, bo to jego jedyne zadanie: czytelnik z wydrukiem w ręku ma
#: wiedzieć, że trzyma zadania treningowe, a nie arkusz zawodów.
RUNNING_HEAD = "Olimpiada Kwantowa – zadania treningowe"
ANSWERS_TITLE = "Odpowiedzi i szkice rozwiązań"
ANSWERS_FILENAME = "odpowiedzi.pdf"
#: Sekcja markdowna z konwencjami zapisu. Wchodzi do **każdej** treści zadania: wydruk jednego
#: zadania musi być samowystarczalny, a notacja Diraca bez legendy nie jest samowystarczalna.
CONVENTIONS_HEADING = "Jak czytać i jak zapisywać rozwiązanie"
CONVENTIONS_TITLE_IN_PDF = "Oznaczenia i konwencje"
#: Zdania ze źródła, które mówią o **stronie** („odpowiedzi są na końcu strony”) i w wydrukowanej
#: treści zadania byłyby po prostu nieprawdą – w PDF-ie z zadaniem odpowiedzi nie ma i nie będzie.
#: Wycinamy je po początku zdania i zastępujemy jednym własnym, prawdziwym w obu miejscach.
CONVENTIONS_SKIP_PREFIXES = ("Odpowiedzi i szkice rozwiązań",)
SOLUTIONS_NOTE = (
    "Szkice rozwiązań są w osobnym dokumencie, który udostępnia organizator – najpierw spróbuj samodzielnie."
)

#: „## Zadanie 3 (trudne): podsłuch w protokole BB84” → numer, poziom, tytuł.
PROBLEM_HEADING = re.compile(r"^##\s+Zadanie\s+(\d+)\s*\(([^)]+)\)\s*:\s*(.+?)\s*$")
#: „### Zadanie 3” w sekcji odpowiedzi.
ANSWER_HEADING = re.compile(r"^###\s+Zadanie\s+(\d+)\s*$")
#: „- **a)** treść podpunktu”. Etykieta jest częścią źródła, więc nie numerujemy od siebie –
#: odpowiedzi muszą trafiać w te same litery, co polecenia.
LIST_ITEM = re.compile(r"^-\s+(?:\*\*(?P<label>[^*]+)\*\*\s*)?(?P<text>.*)$")

STRONG = re.compile(r"\*\*(.+?)\*\*")
EMPHASIS = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

PAGE_MARGIN_MM = 20
HEAD_BASELINE_MM = 12


class BuildError(RuntimeError):
    """Generowanie się nie udało: brakuje źródła, czcionki albo znaku w obu krojach."""


def statement_filename(number: int) -> str:
    """Nazwa pliku treści zadania – ta sama funkcja, z której czyta ``seed_training_problems``."""
    return f"zadanie-{number}.pdf"


@dataclass
class Block:
    """Kawałek treści gotowy do złożenia: akapit albo pozycja listy z etykietą („a)”)."""

    text: str
    label: str = ""
    kind: str = "paragraph"


@dataclass
class Section:
    """Zadanie albo odpowiedź do zadania: numer, poziom, tytuł i bloki treści."""

    number: int
    level: str
    title: str
    blocks: list[Block] = field(default_factory=list)

    @property
    def heading(self) -> str:
        """Podpis w PDF-ie: „Zadanie 3 (trudne): podsłuch w protokole BB84”."""
        level = f" ({self.level})" if self.level else ""
        return f"Zadanie {self.number}{level}: {self.title}"


@dataclass
class Source:
    """Sparsowane źródło: konwencje zapisu, treści zadań i odpowiedzi (po numerze zadania)."""

    conventions: list[Block]
    problems: list[Section]
    answers: dict[int, Section]


# --- parsowanie źródła --------------------------------------------------------------------------


def _inline(text: str) -> str:
    """Markdown w linii → znaczniki reportlaba. Kolejność: najpierw escape, potem pogrubienia.

    Linki tracą adres i zostaje sam tekst: PDF wychodzi też na papier, a wydrukowany odnośnik
    „/dokumenty/zoz/” nie jest ani klikalny, ani czytelny bez domeny. Nazwa dokumentu wystarcza,
    żeby go znaleźć na stronie.
    """
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = LINK.sub(r"\1", text)
    text = STRONG.sub(r"<b>\1</b>", text)
    text = EMPHASIS.sub(r"<i>\1</i>", text)
    return text.strip()


def _append(blocks: list[Block], line: str) -> None:
    """Dokłada linię do bloków: pozycja listy albo (ewentualnie kontynuowany) akapit."""
    item = LIST_ITEM.match(line) if line.startswith("- ") else None
    if item is not None:
        blocks.append(
            Block(
                text=_inline(item.group("text")),
                label=_inline(item.group("label") or ""),
                kind="item",
            )
        )
        return
    if blocks and blocks[-1].kind == "paragraph":
        # Markdown zwija kolejne linie akapitu w jeden – bez tego łamanie wiersza w źródle
        # stawałoby się łamaniem akapitu w PDF-ie.
        blocks[-1].text = f"{blocks[-1].text} {_inline(line)}".strip()
        return
    blocks.append(Block(text=_inline(line)))


def parse(path: Path = SOURCE) -> Source:
    """Czyta ``zadania.md``. Rzuca, gdy brakuje którejkolwiek z części – cichy brak byłby gorszy."""
    conventions: list[Block] = []
    problems: list[Section] = []
    answers: dict[int, Section] = {}
    target: list[Block] | None = None
    in_answers = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(">"):
            # Pusta linia kończy akapit; cytat blokowy jest notą redakcyjną strony, nie treścią PDF-a.
            if not line and target and target[-1].kind == "paragraph":
                target.append(Block(text="", kind="break"))
            continue
        if line.startswith("# "):
            target = None
            continue
        heading = PROBLEM_HEADING.match(line)
        if heading is not None:
            section = Section(int(heading.group(1)), heading.group(2), heading.group(3))
            problems.append(section)
            target = section.blocks
            continue
        answer = ANSWER_HEADING.match(line)
        if in_answers and answer is not None:
            number = int(answer.group(1))
            section = Section(number, "", "")
            answers[number] = section
            target = section.blocks
            continue
        if line.startswith("## "):
            title = line[3:].strip()
            in_answers = title == ANSWERS_TITLE
            target = conventions if title == CONVENTIONS_HEADING else None
            continue
        if line.startswith("### "):
            target = None
            continue
        if target is None:
            continue
        _append(target, line)

    # Puste bloki-separatory wchodziły tylko po to, żeby rozdzielić akapity; dalej są zbędne.
    conventions = [
        block for block in conventions if block.text and not block.text.startswith(CONVENTIONS_SKIP_PREFIXES)
    ]
    for section in [*problems, *answers.values()]:
        section.blocks = [block for block in section.blocks if block.text]
    if not conventions:
        raise BuildError(f"W {path.name} nie znaleziono sekcji „{CONVENTIONS_HEADING}”.")
    if not problems:
        raise BuildError(f"W {path.name} nie znaleziono nagłówka „## Zadanie N (poziom): …”.")
    missing = [section.number for section in problems if section.number not in answers]
    if missing:
        raise BuildError(f"W {path.name} brakuje odpowiedzi do zadań: {missing}.")
    return Source(conventions=conventions, problems=problems, answers=answers)


# --- czcionki i rezerwa znakowa -----------------------------------------------------------------


def _codepoints(path: Path) -> set[int]:
    """Zbiór kodów znaków, które **naprawdę** są w pliku czcionki (z cmap, nie z domysłu).

    Czytamy przez ``TTFontFile`` reportlaba, a nie przez ``fontTools``: to ta sama biblioteka,
    która potem osadza czcionkę w PDF-ie, więc odpowiada na pytanie „czy ten znak się wyrenderuje”
    bez dokładania zależności tylko na jedno sprawdzenie.
    """
    from reportlab.pdfbase.ttfonts import TTFontFile

    return set(TTFontFile(str(path), validate=0).charToGlyph)


@dataclass
class Fonts:
    """Zarejestrowane kroje i ich repertuar znaków – wynik ``register_fonts``."""

    body: set[int]
    math: set[int]


def register_fonts(font_dir: Path = FONT_DIR) -> Fonts:
    """Rejestruje trzy kroje DejaVu i zwraca repertuary znaków tekstowego i matematycznego."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    missing = [name for name, filename in FONT_FILES.items() if not (font_dir / filename).is_file()]
    if missing:
        raise BuildError(
            f"Brak plików czcionek w {font_dir}: {', '.join(FONT_FILES[name] for name in missing)}."
        )
    for name, filename in FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))
    pdfmetrics.registerFontFamily(BODY_FONT, normal=BODY_FONT, bold=BOLD_FONT)
    return Fonts(
        body=_codepoints(font_dir / FONT_FILES[BODY_FONT]),
        math=_codepoints(font_dir / FONT_FILES[MATH_FONT]),
    )


def _fallback_runs(text: str, fonts: Fonts) -> str:
    """Owija znaki nieobecne w DejaVuSans w ``<font name="DejaVuMath">``.

    reportlab nie ma automatycznej rezerwy czcionkowej: znak, którego nie ma w bieżącym kroju,
    wychodzi jako czarny prostokąt i **nikt się o tym nie dowie** – PDF powstanie bez ostrzeżenia.
    Dlatego przełączamy krój sami, znak po znaku, a znak nieobecny w obu krojach przerywa
    generowanie (lepiej nie wydać pliku, niż wydać zadanie z dziurą w treści).

    Znaczników XML (``<b>``, ``<font>``) nie ruszamy – idą do reportlaba dosłownie.
    """
    out: list[str] = []
    run: list[str] = []
    inside_tag = False

    def flush() -> None:
        if run:
            out.append(f'<font name="{MATH_FONT}">{"".join(run)}</font>')
            run.clear()

    for char in text:
        if char == "<":
            inside_tag = True
        if inside_tag:
            flush()
            out.append(char)
            if char == ">":
                inside_tag = False
            continue
        code = ord(char)
        if code in fonts.body or char in "\n\t":
            flush()
            out.append(char)
            continue
        if code not in fonts.math:
            raise BuildError(
                f"Znak {char!r} (U+{code:04X}) nie występuje w żadnej z czcionek DejaVu – "
                "popraw źródło albo dołóż krój, w którym ten znak jest."
            )
        run.append(char)
    flush()
    return "".join(out)


# --- skład PDF ----------------------------------------------------------------------------------


def _styles() -> dict:
    """Style akapitów. Budowane w funkcji, bo ``ParagraphStyle`` wymaga zaimportowanego reportlaba."""
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm

    return {
        "title": ParagraphStyle(
            "title", fontName=BOLD_FONT, fontSize=15, leading=19, spaceAfter=6, textColor="#101828"
        ),
        "heading": ParagraphStyle("heading", fontName=BOLD_FONT, fontSize=11.5, leading=15, spaceAfter=4),
        "body": ParagraphStyle(
            "body", fontName=BODY_FONT, fontSize=10.5, leading=15, spaceAfter=6, alignment=TA_JUSTIFY
        ),
        # Wisząca linia: etykieta („a)”) stoi w lewym marginesie akapitu, więc podpunkty czytają się
        # jak lista także wtedy, gdy treść zajmuje cztery wiersze.
        "item": ParagraphStyle(
            "item",
            fontName=BODY_FONT,
            fontSize=10.5,
            leading=15,
            spaceAfter=5,
            leftIndent=12 * mm,
            firstLineIndent=-7 * mm,
            alignment=TA_JUSTIFY,
        ),
        "note": ParagraphStyle("note", fontName=BODY_FONT, fontSize=9, leading=13, textColor="#475467"),
    }


def _render(path: Path, subtitle: str, story: list) -> None:
    """Składa ``story`` na A4 z żywą paginą i numeracją „Strona N z M”.

    Obie klasy powstają wewnątrz funkcji, bo dziedziczą po typach reportlaba – a tych nie wolno
    zaimportować na poziomie modułu (reportlab jest zależnością dev, patrz docstring modułu).

    Dlaczego stopkę rysuje kanwa, a nie ``onPage``: „z M” wymaga liczby stron, której w trakcie
    składania **jeszcze nie ma** – ``onPage`` woła się na początku strony, więc ostatnia strona
    nie jest wtedy znana. Dlatego kanwa odkłada gotowe strony na bok i dopisuje numerację dopiero
    przy zapisie, kiedy wie, ile ich wyszło. Wariant „złóż dwa razy” (``multiBuild``) tu nie
    działa: bez flowable'ów indeksujących drugiego przebiegu po prostu nie ma. Sama informacja
    „z ilu” jest potrzebna, bo wydruk zadania rozsypuje się na biurku i uczestnik musi wiedzieć,
    czy trzyma komplet.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    margin = PAGE_MARGIN_MM * mm
    head_baseline = HEAD_BASELINE_MM * mm

    def head(canvas, doc) -> None:
        """Żywa pagina: podpis „zadania treningowe” i linijka pod nim. Bez numeru strony."""
        canvas.saveState()
        canvas.setFont(BODY_FONT, 8.5)
        canvas.setFillColor("#667085")
        canvas.drawString(doc.leftMargin, A4[1] - head_baseline, RUNNING_HEAD)
        canvas.setStrokeColor("#e4e7ec")
        rule_y = A4[1] - head_baseline - 3 * mm
        canvas.line(doc.leftMargin, rule_y, A4[0] - doc.rightMargin, rule_y)
        canvas.restoreState()

    class NumberedCanvas(Canvas):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._pages: list[dict] = []

        def showPage(self) -> None:  # noqa: N802 - nazwa z API reportlaba
            # Strona idzie „do poczekalni” zamiast od razu do pliku: wrócimy do niej w ``save``,
            # kiedy będzie wiadomo, ile stron ma dokument.
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.saveState()
                self.setFont(BODY_FONT, 8.5)
                self.setFillColor("#667085")
                self.drawRightString(
                    A4[0] - margin,
                    head_baseline - 4 * mm,
                    f"Strona {self._pageNumber} z {total}",
                )
                self.restoreState()
                super().showPage()
            super().save()

    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin + 6 * mm,
        bottomMargin=margin,
        title=f"{RUNNING_HEAD} – {subtitle}",
        author="Olimpiada Kwantowa",
        subject=subtitle,
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        id="body",
    )
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=head)])
    doc.build(story, canvasmaker=NumberedCanvas)


def _flow(blocks: list[Block], fonts: Fonts, styles: dict) -> list:
    from reportlab.platypus import Paragraph

    story = []
    for block in blocks:
        text = _fallback_runs(block.text, fonts)
        if block.kind == "item":
            # Pozycja bez etykiety to zwykły punkt wyliczenia (legenda notacji) – dostaje kropkę,
            # żeby wisząca linia stylu „item” miała co powiesić w marginesie.
            label = _fallback_runs(block.label, fonts) or "•"
            story.append(Paragraph(f"<b>{label}</b> {text}", styles["item"]))
        else:
            story.append(Paragraph(text, styles["body"]))
    return story


def build_problem_pdf(section: Section, conventions: list[Block], fonts: Fonts, out: Path) -> None:
    """Treść jednego zadania: tytuł, polecenie, podpunkty i legenda notacji. Bez odpowiedzi."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    styles = _styles()
    story: list = [Paragraph(_fallback_runs(section.heading, fonts), styles["title"])]
    story += _flow(section.blocks, fonts, styles)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(CONVENTIONS_TITLE_IN_PDF, styles["heading"]))
    story += _flow(conventions, fonts, styles)
    story.append(Paragraph(SOLUTIONS_NOTE, styles["note"]))
    _render(out, section.heading, story)


def build_answers_pdf(source: Source, fonts: Fonts, out: Path) -> None:
    """Szkice rozwiązań wszystkich zadań w jednym pliku – materiał dla komisji, nie dla uczestnika."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    styles = _styles()
    story: list = [
        Paragraph(_fallback_runs(ANSWERS_TITLE, fonts), styles["title"]),
        Paragraph(
            "Dokument dla recenzentów i koordynatora. Nie jest publikowany razem z treściami zadań.",
            styles["note"],
        ),
        Spacer(1, 5 * mm),
    ]
    for section in source.problems:
        answer = source.answers[section.number]
        story.append(Paragraph(_fallback_runs(section.heading, fonts), styles["heading"]))
        story += _flow(answer.blocks, fonts, styles)
        story.append(Spacer(1, 3 * mm))
    _render(out, ANSWERS_TITLE, story)


def build_all(source_path: Path = SOURCE, out_dir: Path = TRAINING_DIR) -> list[Path]:
    """Składa cztery treści zadań i plik odpowiedzi. Zwraca ścieżki w kolejności powstawania."""
    if not source_path.is_file():
        raise BuildError(f"Nie ma pliku źródłowego: {source_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    fonts = register_fonts()
    source = parse(source_path)
    written: list[Path] = []
    for section in source.problems:
        target = out_dir / statement_filename(section.number)
        build_problem_pdf(section, source.conventions, fonts, target)
        written.append(target)
    answers = out_dir / ANSWERS_FILENAME
    build_answers_pdf(source, fonts, answers)
    written.append(answers)
    return written
