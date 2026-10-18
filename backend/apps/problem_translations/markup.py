"""Markdown tłumaczeń zadań → bezpieczny HTML (TR-01 § 2).

Dlaczego własny, mały konwerter, a nie biblioteka: tekst piszą opiekunowie drużyn i czytają go
uczniowie, więc to jest **treść od użytkownika renderowana innym użytkownikom**. Biblioteka Markdown
przepuszcza surowy HTML i odnośniki ``javascript:`` – trzeba by ją jeszcze oczyścić drugą biblioteką.
Tutaj kolejność jest odwrotna i nie da się jej obejść: **najpierw** cały tekst jest uciekany
(``html.escape``), a dopiero potem dokładamy znaczniki z zamkniętej listy. Żaden znak wpisany przez
autora nie staje się znacznikiem.

Obsługiwany podzbiór – to, czego potrzebuje treść zadania olimpijskiego:

- nagłówki ``#``–``####``, akapity, listy ``-``/``*`` i ``1.``, cytat ``>``, linia pozioma ``---``,
- ``**pogrubienie**``, ``*kursywa*``, ``kod`` w odwrotnych apostrofach i bloki kodu w potrójnych,
- tabele z kreskami (``| a | b |`` + wiersz ``|---|---|``),
- formuły LaTeX: ``$…$`` i ``\\(…\\)`` w tekście, ``$$…$$`` i ``\\[…\\]`` w osobnym wierszu.

Formuł serwer **nie** składa – wstawia je jako tekst (uciekany) w ``<span class="tr-math">``, a rysuje
je w przeglądarce KaTeX z plików statycznych aplikacji (``static/problem_translations/js/math.js``).
Ten sam tekst trafia do eksportu PDF jako źródło LaTeX-a (``pdf.py``). Obrazków i odnośników nie
ma celowo: obrazek z obcego adresu zdradzałby, kto i kiedy czyta tajne zadanie, a rysunek do zadania
dołącza się jako PDF.

**Koszt liniowy (TR-01b).** Wyrażenia znaczników są leniwe (``**(.+?)**``, ``\\[(.+?)\\]`` z
``DOTALL``, bloki kodu), więc ``re.sub`` przy **niedomkniętym** znaczniku skanuje tekst do końca
wiersza albo całego tekstu – a tysiące takich znaczników to koszt kwadratowy (wiersz 200 KB
z „**a ” ×50 000: ok. 142 s). Dlatego żadnego z nich nie puszczamy przez ``re.sub``: ``_sub`` zbiera
pozycje otwarć i zamknięć (``str.find`` i to samo sprawdzenie otoczenia, co w wyrażeniu) i próbuje
wyrażenia **tylko** tam, gdzie wyszukiwanie binarne znalazło zamknięcie w zasięgu – czyli tam, gdzie
dopasowanie na pewno się uda i zje tekst, który przeskanuje. Samo dopasowanie robi nadal to samo
wyrażenie, więc wynik jest identyczny z ``re.sub`` (test różnicowy w ``tests/test_markup_cost.py``).

**Bramka kosztu.** Niezależnie od tego ``render``/``plain_blocks`` przepuszczają do składu tylko tekst,
którego liniowy szacunek ``markdown_cost`` mieści się w ``settings.MARKDOWN_COST_BUDGET``. To
bezpiecznik na przyszłość: gdyby zmiana konwertera dołożyła nowe wyrażenie leniwe, tekst
o kształcie kwadratowym i tak nie zajmie workera. Tekst ponad budżet jest pokazywany uciekany, bez
znaczników. Wszyscy wywołujący (widoki tłumaczeń, eksport PDF, podgląd materiałów warsztatowych)
wchodzą przez te funkcje.
"""

from __future__ import annotations

import bisect
import html
import re
from collections.abc import Callable, Iterator

from django.conf import settings
from django.utils.safestring import SafeString, mark_safe

#: Znak-znacznik miejsc wyjętych przed ucieczką (formuły, kod). ``\x00`` usuwamy z wejścia, więc
#: autor nie może podrobić znacznika.
MARK = "\x00"

FENCE = re.compile(r"^```[^\n]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)
INLINE_MATH = re.compile(r"(?<![\\$])\$(?!\s)([^$\n]+?)(?<![\\\s])\$(?!\d)|\\\((.+?)\\\)")
PLACEHOLDER = re.compile(MARK + r"(\d+)" + MARK)
HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
ORDERED = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
QUOTE = re.compile(r"^&gt;\s?(.*)$")
RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
#: Ten sam język, co ``\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$`` z TR-01, ale bez dwóch
#: sąsiednich ``\s*`` – tamte dzieliły ciąg spacji na dwa sposoby i przy nieudanym dopasowaniu
#: dawały koszt kwadratowy w jego długości (TR-01b).
TABLE_SEPARATOR = re.compile(r"^\s*(?:\|\s*)?:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*(?:\|\s*)?$")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
ITALIC = re.compile(r"(?<![*\w])\*(?=\S)(.+?)(?<=\S)\*(?![*\w])")
PDF_BLOCK = re.compile(r"```[^\n]*\n(.*?)\n```|\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)

# Otwarcia i zamknięcia znaczników – dokładnie te warunki otoczenia, które sprawdza wyrażenie
# w miejscu znacznika (patrz ``_sub``).
_BOLD_OPEN = re.compile(r"\*\*(?=\S)")
_BOLD_CLOSE = re.compile(r"(?<=\S)\*\*")
_ITALIC_OPEN = re.compile(r"(?<![*\w])\*(?=\S)")
_ITALIC_CLOSE = re.compile(r"(?<=\S)\*(?![*\w])")
_FENCE_OPEN = re.compile(r"^```", re.MULTILINE)
_FENCE_CLOSE = re.compile(r"^```[ \t]*$", re.MULTILINE)

#: Domyślny budżet ``markdown_cost`` (ustawienie ``MARKDOWN_COST_BUDGET``): ok. 0,35 s najgorszego
#: przypadku naiwnego konwertera (ok. 7 ns na jednostkę na laptopie deweloperskim, pomiar WM-FMT-01);
#: zwykły tekst – także megabajtowy – ma koszt rzędu swojej długości.
DEFAULT_COST_BUDGET = 50_000_000


# --- dopasowania bez skanowania niedomkniętych znaczników (TR-01b) ---------------------------------


def _occurrences(text: str, token: str, check: re.Pattern | None = None) -> list[int]:
    """Wszystkie wystąpienia ``token`` (także zachodzące na siebie), rosnąco; z ``check`` – tylko te,
    w których to wyrażenie pasuje (``check.match(text, pozycja)`` widzi znaki przed pozycją)."""
    found: list[int] = []
    index = text.find(token)
    while index >= 0:
        if check is None or check.match(text, index):
            found.append(index)
        index = text.find(token, index + 1)
    return found


def _any_between(positions: list[int], low: int, high: int) -> bool:
    """Czy w posortowanej liście jest pozycja ``low <= p < high``."""
    index = bisect.bisect_left(positions, low)
    return index < len(positions) and positions[index] < high


class _Lines:
    """Koniec wiersza dla pozycji – zasięg wyrażeń bez ``DOTALL`` (``.`` nie przechodzi przez ``\\n``)."""

    def __init__(self, text: str) -> None:
        self.newlines = _occurrences(text, "\n")
        self.length = len(text)

    def end(self, position: int) -> int:
        index = bisect.bisect_left(self.newlines, position)
        return self.newlines[index] if index < len(self.newlines) else self.length


def _matches(
    pattern: re.Pattern, text: str, starts: list[int], can_match: Callable[[int], bool]
) -> Iterator[re.Match]:
    """Te same dopasowania, co ``pattern.finditer(text)``, w czasie liniowym.

    ``starts`` – rosnąco wszystkie pozycje, w których dopasowanie może się zacząć (pierwszy znak
    znacznika z warunkami otoczenia); ``can_match(p)`` – czy dopasowanie od ``p`` się uda (jest
    zamknięcie w zasięgu). Musi być dokładne w stronę „tak”: pominięte ``p`` z prawdziwym
    dopasowaniem zmieniłoby wynik. Wyrażenie próbujemy tylko tam, gdzie się uda, więc jego praca
    nie przekracza długości zjedzonego tekstu.
    """
    position = 0
    for start in starts:
        if start < position or not can_match(start):
            continue
        match = pattern.match(text, start)
        if match is None:  # pragma: no cover - ``can_match`` jest dokładne; ostrożnie pomijamy
            continue
        yield match
        position = match.end()


def _sub(
    pattern: re.Pattern,
    text: str,
    repl: str | Callable[[re.Match], str],
    starts: list[int],
    can_match: Callable[[int], bool],
) -> str:
    """``pattern.sub(repl, text)`` przez ``_matches`` – ``repl`` jak w ``re.sub`` (wzorzec albo funkcja)."""
    parts: list[str] = []
    position = 0
    for match in _matches(pattern, text, starts, can_match):
        parts.append(text[position : match.start()])
        parts.append(repl(match) if callable(repl) else match.expand(repl))
        position = match.end()
    parts.append(text[position:])
    return "".join(parts)


def _sub_bold(text: str, repl: str | Callable[[re.Match], str] = r"<strong>\1</strong>") -> str:
    # ``**`` + co najmniej jeden znak + ``**`` w tym samym wierszu → zamknięcie od ``start + 3``.
    closers = _occurrences(text, "**", _BOLD_CLOSE)
    lines = _Lines(text)
    return _sub(
        BOLD,
        text,
        repl,
        _occurrences(text, "**", _BOLD_OPEN),
        lambda start: _any_between(closers, start + 3, lines.end(start)),
    )


def _sub_italic(text: str, repl: str | Callable[[re.Match], str] = r"<em>\1</em>") -> str:
    closers = _occurrences(text, "*", _ITALIC_CLOSE)
    lines = _Lines(text)
    return _sub(
        ITALIC,
        text,
        repl,
        _occurrences(text, "*", _ITALIC_OPEN),
        lambda start: _any_between(closers, start + 2, lines.end(start)),
    )


def _sub_fence(text: str, repl: str | Callable[[re.Match], str]) -> str:
    # Otwarcie: wiersz od `````; treść od następnego wiersza (bez ``\n`` – brak dopasowania); zamknięcie:
    # dowolny późniejszy wiersz z samym ````` i spacjami (``DOTALL`` – przez wiele wierszy).
    closers = _occurrences(text, "```", _FENCE_CLOSE)
    lines = _Lines(text)
    return _sub(
        FENCE,
        text,
        repl,
        _occurrences(text, "```", _FENCE_OPEN),
        lambda start: _any_between(closers, lines.end(start) + 1, len(text) + 1),
    )


def _sub_display_math(text: str, repl: str | Callable[[re.Match], str]) -> str:
    dollars = _occurrences(text, "$$")
    brackets = _occurrences(text, "\\]")
    starts = sorted(dollars + _occurrences(text, "\\["))
    return _sub(
        DISPLAY_MATH,
        text,
        repl,
        starts,
        lambda start: _any_between(dollars if text[start] == "$" else brackets, start + 3, len(text) + 1),
    )


def _sub_inline_math(text: str, repl: str | Callable[[re.Match], str]) -> str:
    # ``$…$`` sięga najwyżej do następnego ``$`` albo końca wiersza (``[^$\n]``) – to próbujemy zawsze;
    # ``\(…\)`` sięga do końca wiersza, więc tylko z zamknięciem w tym wierszu.
    closers = _occurrences(text, "\\)")
    lines = _Lines(text)
    starts = sorted(_occurrences(text, "$") + _occurrences(text, "\\("))
    return _sub(
        INLINE_MATH,
        text,
        repl,
        starts,
        lambda start: text[start] == "$" or _any_between(closers, start + 3, lines.end(start)),
    )


def _pdf_matches(text: str) -> Iterator[re.Match]:
    """``PDF_BLOCK.finditer(text)`` w czasie liniowym (blok kodu tu nie musi zaczynać wiersza)."""
    fence_ends = _occurrences(text, "\n```")
    dollars = _occurrences(text, "$$")
    brackets = _occurrences(text, "\\]")
    lines = _Lines(text)

    def can_match(start: int) -> bool:
        if text[start] == "`":
            return _any_between(fence_ends, lines.end(start) + 1, len(text) + 1)
        return _any_between(dollars if text[start] == "$" else brackets, start + 3, len(text) + 1)

    starts = sorted(_occurrences(text, "```") + dollars + _occurrences(text, "\\["))
    return _matches(PDF_BLOCK, text, starts, can_match)


# --- bramka kosztu ---------------------------------------------------------------------------------


def _span_cost(text: str, openers: list[int], closers: list[int]) -> int:
    """Suma odległości od każdego otwarcia do najbliższego zamknięcia za nim (albo do końca tekstu)."""
    cost = 0
    for start in openers:
        index = bisect.bisect_right(closers, start)
        end = closers[index] if index < len(closers) else len(text)
        cost += end - start
    return cost


def _positions(text: str, token: str) -> list[int]:
    return [match.start() for match in re.finditer(re.escape(token), text)]


def markdown_cost(text: str) -> int:
    r"""Szacunek z góry pracy naiwnego (leniwe ``re.sub``) konwertera – liniowy w długości tekstu.

    Wzór z WM-FMT-01, bez zmian (od niego zależy, które pliki podgląd materiałów składa):

    - pogrubienie, kursywa i ``\(…\)`` działają w obrębie wiersza: każdy ``*`` i ``\(``
      może przeskanować resztę wiersza → liczba takich znaków razy długość wiersza,
    - bloki kodu, ``$$…$$`` i ``\[…\]`` działają na całym tekście: każde otwarcie skanuje do
      najbliższego zamknięcia za nim, a bez zamknięcia – do końca pliku.
    """
    cost = 0
    for line in text.split("\n"):
        risky = line.count("*") + line.count("\\(")
        if risky:
            cost += risky * len(line)
    fence_close = [match.start() for match in _FENCE_CLOSE.finditer(text)]
    cost += _span_cost(text, [match.start() for match in _FENCE_OPEN.finditer(text)], fence_close)
    dollars = _positions(text, "$$")
    cost += _span_cost(text, dollars, dollars)
    cost += _span_cost(text, _positions(text, "\\["), _positions(text, "\\]"))
    return cost


def cost_budget() -> int:
    return int(getattr(settings, "MARKDOWN_COST_BUDGET", DEFAULT_COST_BUDGET))


def within_budget(source: str) -> bool:
    return markdown_cost(source or "") <= cost_budget()


# --- skład -----------------------------------------------------------------------------------------


class _Stash:
    """Wyjęte fragmenty (formuły, kod) – wracają na swoje miejsca po ucieczce i składaniu bloków."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def put(self, rendered: str) -> str:
        self.items.append(rendered)
        return f"{MARK}{len(self.items) - 1}{MARK}"

    def restore(self, text: str) -> str:
        return PLACEHOLDER.sub(lambda match: self.items[int(match.group(1))], text)


def _math(latex: str, *, display: bool) -> str:
    css = "tr-math tr-math--display" if display else "tr-math"
    return f'<span class="{css}" data-display="{1 if display else 0}">{html.escape(latex.strip())}</span>'


def _inline(text: str) -> str:
    return _sub_italic(_sub_bold(text))


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _is_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines) and "|" in lines[index] and TABLE_SEPARATOR.match(lines[index + 1]) is not None
    )


def _blocks(lines: list[str]) -> list[str]:
    out: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + _inline("\n".join(paragraph)) + "</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            index += 1
            continue
        heading = HEADING.match(stripped)
        if heading:
            flush_paragraph()
            # ``#`` w tekście zadania to rozdział zadania – o dwa poziomy niżej niż tytuł strony.
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if RULE.match(stripped):
            flush_paragraph()
            out.append("<hr>")
            index += 1
            continue
        if _is_table_start(lines, index):
            flush_paragraph()
            header = _split_row(lines[index])
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_split_row(lines[index]))
                index += 1
            head = "".join(f"<th>{_inline(cell)}</th>" for cell in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>" for row in rows
            )
            out.append(f'<table class="tr-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')
            continue
        for pattern, tag in ((BULLET, "ul"), (ORDERED, "ol")):
            if pattern.match(line):
                flush_paragraph()
                items: list[str] = []
                while index < len(lines) and pattern.match(lines[index]):
                    items.append(f"<li>{_inline(pattern.match(lines[index]).group(1))}</li>")
                    index += 1
                out.append(f"<{tag}>{''.join(items)}</{tag}>")
                break
        else:
            if QUOTE.match(stripped):
                flush_paragraph()
                quoted: list[str] = []
                while index < len(lines) and QUOTE.match(lines[index].strip()):
                    quoted.append(QUOTE.match(lines[index].strip()).group(1))
                    index += 1
                out.append("<blockquote><p>" + _inline("\n".join(quoted)) + "</p></blockquote>")
                continue
            paragraph.append(stripped)
            index += 1
    flush_paragraph()
    return out


def _normalise(source: str) -> str:
    return (source or "").replace("\r\n", "\n").replace("\r", "\n").replace(MARK, "")


def _convert(text: str) -> str:
    """Skład bez bramki (tekst po ``_normalise``) – liniowy, patrz docstring modułu."""
    stash = _Stash()
    text = _sub_fence(
        text, lambda m: "\n" + stash.put(f"<pre><code>{html.escape(m.group(1))}</code></pre>") + "\n"
    )
    text = INLINE_CODE.sub(lambda m: stash.put(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = _sub_display_math(text, lambda m: stash.put(_math(m.group(1) or m.group(2), display=True)))
    text = _sub_inline_math(text, lambda m: stash.put(_math(m.group(1) or m.group(2), display=False)))
    escaped = html.escape(text, quote=True)
    rendered = "\n".join(_blocks(escaped.split("\n")))
    return stash.restore(rendered)


def render_within_budget(source: str) -> SafeString | None:
    """Markdown → HTML jak ``render`` albo ``None``, gdy tekst przekracza budżet kosztu.

    Dla wywołujących, którzy tekst ponad budżet pokazują po swojemu (podgląd materiałów: ``<pre>``
    z adnotacją). Szacunek liczymy na tekście w postaci, w jakiej przyszedł.
    """
    if not within_budget(source):
        return None
    return mark_safe(_convert(_normalise(source)))  # noqa: S308 - całe wejście przeszło przez html.escape


def plain_html(source: str) -> SafeString:
    """Tekst bez składu: uciekany, w ``<pre class="tr-plain">`` (zawijany, ``translations.css``)."""
    return mark_safe(f'<pre class="tr-plain">{html.escape(_normalise(source))}</pre>')  # noqa: S308


def render(source: str) -> SafeString:
    """Markdown z formułami → HTML bezpieczny do wstawienia w szablon (patrz docstring modułu).

    Tekst ponad budżet kosztu (``MARKDOWN_COST_BUDGET``) – uciekany, bez znaczników (``plain_html``).
    """
    rendered = render_within_budget(source)
    return plain_html(source) if rendered is None else rendered


def plain_blocks(source: str) -> list[tuple[str, str]]:
    """Ten sam tekst jako lista bloków ``(rodzaj, treść)`` dla składu PDF (``pdf.py``).

    Rodzaje: ``heading``, ``paragraph``, ``math`` (formuła wyświetlana, LaTeX), ``code``. Formuły
    w tekście zostają w akapicie jako ``$…$`` – serwer nie ma silnika TeX-a, a uczciwe źródło formuły
    jest czytelniejsze niż jej zgadywane przybliżenie. Tekst ponad budżet kosztu – same akapity
    (bez nagłówków, formuł i bloków kodu).
    """
    text = (source or "").replace("\r\n", "\n").replace("\r", "\n")
    structured = within_budget(source)
    blocks: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(("paragraph", " ".join(paragraph)))
            paragraph.clear()

    position = 0
    pieces: list[tuple[str, str]] = []
    for match in _pdf_matches(text) if structured else ():
        pieces.append(("text", text[position : match.start()]))
        if match.group(1) is not None:
            pieces.append(("code", match.group(1)))
        else:
            pieces.append(("math", (match.group(2) or match.group(3)).strip()))
        position = match.end()
    pieces.append(("text", text[position:]))
    for kind, value in pieces:
        if kind != "text":
            flush()
            blocks.append((kind, value))
            continue
        for line in value.split("\n"):
            stripped = line.strip()
            if not stripped:
                flush()
                continue
            heading = HEADING.match(stripped) if structured else None
            if heading:
                flush()
                blocks.append(("heading", heading.group(2)))
                continue
            paragraph.append(stripped)
        flush()
    return blocks
