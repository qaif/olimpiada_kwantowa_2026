"""Parser podzbioru Markdowna używanego przez import treści starej strony.

Projekt nie ma w zależnościach biblioteki markdown (``pyproject.toml``) i nie ma powodu jej
dokładać: pliki w ``apps/cms/fixtures/legacy/`` pisze ten sam zespół, który pisze ten parser,
a zakres składni jest zamknięty i wypisany niżej. Dokładanie zależności, żeby odczytać dziewięć
własnych plików, kosztowałoby więcej (aktualizacje, audyt, rozmiar obrazu) niż sto linijek tutaj.

Obsługiwana składnia — i tylko ona:

===================================  =======================================================
źródło                               wynik
===================================  =======================================================
``# Tytuł``                          pomijane (tytuł strony jest polem, nie treścią)
``## Tekst``                         blok ``heading`` poziom 2, kotwica ze slugifikacji, w spisie
``### Tekst``                        blok ``heading`` poziom 3, poza spisem
akapit                               blok ``paragraph`` (``<p>``)
``- pozycja``                        blok ``paragraph`` z ``<ul>``
``1. pozycja``                       blok ``paragraph`` z ``<ol>``
``| a | b |``                        blok ``definitions`` (``<dl>``) albo – w treściach bez tego
                                     bloku – ``paragraph``: ``<p><strong>a</strong> — b</p>``
``| a | b | c |``                    blok ``schedule`` (``<table>``) – patrz niżej
``> tekst``                          blok ``notice`` (ton ``info``); wiersz ``>`` dzieli akapity
``**pogrubienie**``, ``*kursywa*``   ``<strong>`` / ``<em>``
``[tekst](adres)``                   ``<a href="adres">``
===================================  =======================================================

Dwie decyzje warte uzasadnienia:

- **tabela dwukolumnowa zostaje listą definicji, a nie ``<table>``.** Tabele w tych treściach są
  układem strony (etykieta → wartość), nie danymi tabelarycznymi; lista czyta się w czytniku
  ekranu i na telefonie, a ``<table>`` i tak nie przeszłoby przez whitelistę ``RICH_TEXT_FEATURES``.
  Postacie są dwie i zależą od tego, jak długie są komórki:

  - ``parse_markdown(..., definition_lists=True)`` – blok ``definitions`` (``<dl>``, etykieta nad
    wartością, nagłówki kolumn przy każdej parze). Tego używają dokumenty urzędowe, w których
    komórka bywa całym zdaniem („rejestracja, prowadzenie konta…” obok „art. 6 ust. 1 lit. b i e
    RODO oraz…”); zlepienie ich w jeden akapit gubi granicę, która jest tam całą treścią,
  - domyślnie – akapit ``<p><strong>etykieta</strong> — wartość</p>``. Wystarcza tabelom
    z krótkimi komórkami (terminarz: „Rejestracja — 1 września – 15 października 2026”), gdzie
    karta z nagłówkami kolumn nad każdą wartością byłaby cięższa od samej treści. Łącznikiem jest
    półpauza, nie dwukropek: etykietą bywa całe zdanie, któremu dopisanie dwukropka zmieniałoby
    interpunkcję tekstu organizatora,

- **tabela trzykolumnowa zostaje ``<table>`` (blok ``schedule``).** Obie postacie wyżej sklejają
  wszystko od drugiej kolumny w jedną wartość rozdzieloną „·”, bo obie opisują parę etykieta–wartość.
  Przy harmonogramie warsztatów (temat, termin, godziny) to sklejenie gubi granicę, po której
  czytelnik przebiega wzrokiem, a czytnik ekranu traci nagłówek kolumny. Liczba kolumn jest tu dobrym
  kryterium, bo wynika z treści: tabela, która ma trzecią kolumnę, nie jest już listą definicji,

- **cały tekst jest escapowany**, zanim dołożymy znaczniki. Do ``RichText`` trafia dokładnie to,
  co było w pliku, i nic, czego nie zna whitelist edytora. Komórki bloku ``definitions`` idą tam
  jako czysty tekst (escapuje je szablon), więc formatowanie liniowe w tabeli nie zadziała –
  w tabelach organizatora go nie ma.
"""

from __future__ import annotations

import re
from html import escape

from wagtail.rich_text import RichText

#: Slugifikacja kotwic: „§ 3. Zakres” → „3-zakres”.
TRANSLIT = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
ORDERED_RE = re.compile(r"^\d+\.\s+(.*)$")
UNORDERED_RE = re.compile(r"^[-*]\s+(.*)$")
HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
TABLE_SEPARATOR_RE = re.compile(r"^\|[\s|:-]+\|$")

#: Poziom śródtytułu, który trafia do spisu sekcji (``in_toc``).
HEADING_LEVEL_2 = 2
#: Wiersz tabeli poniżej dwóch komórek to zwykły akapit, nie para etykieta–wartość.
MIN_TABLE_CELLS = 2
#: Od trzech kolumn tabela przestaje być parą etykieta–wartość i zostaje blokiem ``schedule``.
SCHEDULE_CELLS = 3


def slugify_anchor(text: str) -> str:
    """Kotwica z tekstu nagłówka. Puste wejście daje ``sekcja`` – ``id=""`` byłoby nieklikalne."""
    ascii_text = text.translate(TRANSLIT).lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_text)).strip("-") or "sekcja"


def inline(text: str) -> str:
    """Formatowanie liniowe. Kolejność: escape → link → pogrubienie → kursywa."""
    html = escape(text.strip(), quote=False)
    html = LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', html)
    html = BOLD_RE.sub(r"<strong>\1</strong>", html)
    return ITALIC_RE.sub(r"<em>\1</em>", html)


def _split_table(rows: list[str]) -> tuple[list[str], list[list[str]]]:
    """Wiersze tabeli → ``(komórki nagłówka, wiersze danych)``.

    Nagłówkiem jest wiersz stojący bezpośrednio przed separatorem ``|---|---|``; wszystko sprzed
    separatora przestaje być danymi. Tabela bez separatora nie ma nagłówka – każdy wiersz to dane.
    """
    header: list[str] = []
    parsed: list[list[str]] = []
    for row in rows:
        if TABLE_SEPARATOR_RE.match(row):
            header = parsed[-1] if parsed else []
            parsed = []
            continue
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if any(cells):
            parsed.append(cells)
    return header, parsed


def _rest_of_row(cells: list[str]) -> str:
    """Kolumny od drugiej w jednej wartości. Trzecia kolumna zdarza się tylko przez pomyłkę."""
    return " · ".join(cell for cell in cells[1:] if cell)


def _table_html(parsed: list[list[str]]) -> str:
    """Wiersze danych → akapity „**lewa kolumna** — prawa kolumna”."""
    parts = []
    for cells in parsed:
        if len(cells) < MIN_TABLE_CELLS:
            parts.append(f"<p>{inline(cells[0])}</p>")
            continue
        rest = " · ".join(inline(cell) for cell in cells[1:] if cell)
        parts.append(f"<p><strong>{inline(cells[0])}</strong> — {rest}</p>")
    return "".join(parts)


def _definitions_value(header: list[str], parsed: list[list[str]]) -> dict:
    """Wiersze danych → wartość bloku ``definitions``. Nagłówki kolumn są opcjonalne."""
    return {
        "term_label": header[0] if header else "",
        "description_label": _rest_of_row(header) if len(header) >= MIN_TABLE_CELLS else "",
        "rows": [{"term": cells[0], "description": _rest_of_row(cells)} for cells in parsed],
    }


def _schedule_value(header: list[str], parsed: list[list[str]]) -> dict:
    """Wiersze danych → wartość bloku ``schedule`` (temat, termin, godziny)."""

    def cell(cells: list[str], index: int) -> str:
        return cells[index] if index < len(cells) else ""

    return {
        "caption": "",
        "topic_label": cell(header, 0),
        "date_label": cell(header, 1),
        "time_label": cell(header, 2),
        "rows": [{"topic": cells[0], "date": cell(cells, 1), "time": cell(cells, 2)} for cells in parsed],
    }


def _quote_html(lines: list[str]) -> str:
    """Treść ramki. Każdy wiersz to osobny akapit – w źródle jeden wiersz to jeden fakt."""
    parts: list[str] = []
    items: list[str] = []

    def flush_items() -> None:
        if items:
            parts.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_items()
            continue
        match = UNORDERED_RE.match(stripped)
        if match is not None:
            items.append(inline(match.group(1)))
            continue
        flush_items()
        parts.append(f"<p>{inline(stripped)}</p>")
    flush_items()
    return "".join(parts)


def _list_html(items: list[str], *, ordered: bool) -> str:
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join(f"<li>{inline(item)}</li>" for item in items) + f"</{tag}>"


class _Builder:
    """Zbiera bloki StreamField oraz wprowadzenie (wszystko przed pierwszym śródtytułem)."""

    def __init__(self, *, definition_lists: bool = False) -> None:
        self.intro_parts: list[str] = []
        self.blocks: list[tuple[str, object]] = []
        self.seen_heading = False
        self.definition_lists = definition_lists

    def add_html(self, html: str) -> None:
        if not html:
            return
        if self.seen_heading:
            self.blocks.append(("paragraph", RichText(html)))
        else:
            self.intro_parts.append(html)

    def add_notice(self, html: str) -> None:
        """Ramka zawsze jest blokiem – także przed pierwszym śródtytułem (``intro`` to zwykły tekst)."""
        if html:
            self.blocks.append(("notice", {"tone": "info", "text": RichText(html)}))

    def add_table(self, rows: list[str]) -> None:
        """Tabela: blok ``schedule``, blok ``definitions``, a gdy ich nie ma – akapity.

        O postaci decyduje **liczba kolumn**, bo to ona mówi, czy tabela jest parą etykieta–wartość,
        czy danymi:

        - **trzy kolumny i więcej → blok ``schedule``** (prawdziwa ``<table>``). Trzeciej kolumny nie
          da się spłaszczyć: ``_table_html`` i lista definicji sklejają wszystko od drugiej kolumny
          w jedną wartość rozdzieloną „·”, więc „14 listopada 2026 · 11:00–15:00” traci granicę
          między terminem a godzinami, a czytnik ekranu – nagłówek kolumny,
        - **dwie kolumny → lista definicji** (``definition_lists``) albo akapity „etykieta — wartość”.

        Przed pierwszym śródtytułem zostają akapity niezależnie od liczby kolumn: wszystko sprzed
        nagłówka trafia do ``intro``, a to zwykłe pole ``RichTextField`` – blok StreamFielda nie ma
        tam gdzie stanąć. Tabel we wprowadzeniu w treściach organizatora zresztą nie ma.
        """
        header, parsed = _split_table(rows)
        usable = parsed and all(len(cells) >= MIN_TABLE_CELLS for cells in parsed)
        if self.seen_heading and usable and max(len(cells) for cells in parsed) >= SCHEDULE_CELLS:
            self.blocks.append(("schedule", _schedule_value(header, parsed)))
            return
        if self.definition_lists and self.seen_heading and usable:
            self.blocks.append(("definitions", _definitions_value(header, parsed)))
            return
        self.add_html(_table_html(parsed))

    def add_heading(self, text: str, level: int) -> None:
        self.seen_heading = True
        self.blocks.append(
            (
                "heading",
                {
                    "text": text,
                    "level": str(level),
                    "anchor": slugify_anchor(text),
                    "in_toc": level == HEADING_LEVEL_2,
                },
            )
        )


def parse_markdown(text: str, *, definition_lists: bool = False) -> tuple[str, list[tuple[str, object]]]:
    """Zwraca ``(intro_html, bloki_streamfield)``.

    ``definition_lists`` włącza blok ``definitions`` dla tabel – patrz docstring modułu. Decyzja
    należy do wywołującego, bo zależy od treści (długie komórki), a nie od typu strony.
    """
    builder = _Builder(definition_lists=definition_lists)
    lines = text.replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        heading = HEADING_RE.match(stripped)
        if heading is not None:
            level = len(heading.group(1))
            if level > 1:
                builder.add_heading(heading.group(2).strip(), level)
            index += 1
            continue

        if stripped.startswith(">"):
            quote: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quote.append(lines[index].lstrip()[1:])
                index += 1
            builder.add_notice(_quote_html(quote))
            continue

        if stripped.startswith("|"):
            rows: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index].strip())
                index += 1
            builder.add_table(rows)
            continue

        ordered = ORDERED_RE.match(stripped)
        unordered = UNORDERED_RE.match(stripped)
        if ordered is not None or unordered is not None:
            pattern = ORDERED_RE if ordered is not None else UNORDERED_RE
            items: list[str] = []
            while index < len(lines):
                match = pattern.match(lines[index].strip())
                if match is None:
                    break
                items.append(match.group(1))
                index += 1
            builder.add_html(_list_html(items, ordered=ordered is not None))
            continue

        paragraph: list[str] = []
        while index < len(lines):
            current = lines[index].strip()
            if not current or current.startswith(("#", ">", "|", "- ", "* ")) or ORDERED_RE.match(current):
                break
            paragraph.append(current)
            index += 1
        builder.add_html(f"<p>{inline(' '.join(paragraph))}</p>")

    return "".join(builder.intro_parts), builder.blocks
