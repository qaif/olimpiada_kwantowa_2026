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
"""

from __future__ import annotations

import html
import re

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
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
ITALIC = re.compile(r"(?<![*\w])\*(?=\S)(.+?)(?<=\S)\*(?![*\w])")


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
    text = BOLD.sub(r"<strong>\1</strong>", text)
    return ITALIC.sub(r"<em>\1</em>", text)


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


def render(source: str) -> SafeString:
    """Markdown z formułami → HTML bezpieczny do wstawienia w szablon (patrz docstring modułu)."""
    text = (source or "").replace("\r\n", "\n").replace("\r", "\n").replace(MARK, "")
    stash = _Stash()
    text = FENCE.sub(
        lambda m: "\n" + stash.put(f"<pre><code>{html.escape(m.group(1))}</code></pre>") + "\n", text
    )
    text = INLINE_CODE.sub(lambda m: stash.put(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = DISPLAY_MATH.sub(lambda m: stash.put(_math(m.group(1) or m.group(2), display=True)), text)
    text = INLINE_MATH.sub(lambda m: stash.put(_math(m.group(1) or m.group(2), display=False)), text)
    escaped = html.escape(text, quote=True)
    rendered = "\n".join(_blocks(escaped.split("\n")))
    return mark_safe(stash.restore(rendered))  # noqa: S308 - całe wejście przeszło przez html.escape


def plain_blocks(source: str) -> list[tuple[str, str]]:
    """Ten sam tekst jako lista bloków ``(rodzaj, treść)`` dla składu PDF (``pdf.py``).

    Rodzaje: ``heading``, ``paragraph``, ``math`` (formuła wyświetlana, LaTeX), ``code``. Formuły
    w tekście zostają w akapicie jako ``$…$`` – serwer nie ma silnika TeX-a, a uczciwe źródło formuły
    jest czytelniejsze niż jej zgadywane przybliżenie.
    """
    text = (source or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(("paragraph", " ".join(paragraph)))
            paragraph.clear()

    position = 0
    pieces: list[tuple[str, str]] = []
    for match in re.finditer(r"```[^\n]*\n(.*?)\n```|\$\$(.+?)\$\$|\\\[(.+?)\\\]", text, re.DOTALL):
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
            heading = HEADING.match(stripped)
            if heading:
                flush()
                blocks.append(("heading", heading.group(2)))
                continue
            paragraph.append(stripped)
        flush()
    return blocks
