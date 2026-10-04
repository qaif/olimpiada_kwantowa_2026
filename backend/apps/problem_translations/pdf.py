"""PDF-y tłumaczeń: znak wodny dla opiekunów i pakiet do druku na finał (TR-01 §§ 2–3).

Dwie czynności, obie na bibliotekach, które platforma już ma (reportlab składa, pypdf łączy):

- **znak wodny** – każda strona PDF-u pobranego przez opiekuna drużyny przed otwarciem etapu
  dostaje ukośny napis z kodem delegacji, „CONFIDENTIAL” i datą pobrania. Wyciek arkusza przed
  zawodami wskazuje wtedy kraj, z którego wyszedł. Nakładka powstaje raz na rozmiar strony
  (arkusz ma zwykle jeden rozmiar), a łączenie robi pypdf (``merge_page``),
- **pakiet do druku** – zatwierdzone wersje zadań etapu w jednym języku, po kolei: PDF tłumaczenia
  wchodzi stronami, a tłumaczenie tekstowe składa reportlab krojem DejaVu (łacinka, cyrylica,
  greka). Formuły zostają jako źródło LaTeX-a – serwer nie ma silnika TeX-a. Dla tekstu z formułami
  albo w piśmie spoza DejaVu (chińskie, dewanagari, bengalskie, arabskie z łączeniem liter) właściwą
  drogą jest **widok do druku** w przeglądarce (KaTeX + kroje systemu → „Zapisz jako PDF”);
  ekran eksportu mówi to wprost.

Importy reportlaba i pypdf są leniwe: moduł czytają widoki, a biblioteki potrzebne są dopiero przy
pobraniu.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass

#: Jasnoszary, półprzezroczysty – czytelny na wydruku i nie zasłaniający treści.
WATERMARK_GRAY = 0.55
WATERMARK_ALPHA = 0.28

#: Najwięcej stron przetłumaczonego PDF-u. Zadanie ma kilka stron; limit odcina wgranie przypadkowego
#: podręcznika, który potem szedłby przez znak wodny przy każdym pobraniu.
MAX_PAGES = 60


class PdfUnreadable(ValueError):
    """pypdf nie umie otworzyć pliku albo plik ma za dużo stron."""


def page_count(data: bytes) -> int:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise PdfUnreadable("encrypted")
        return len(reader.pages)
    except (PyPdfError, ValueError, KeyError, TypeError, OSError) as exc:
        raise PdfUnreadable(str(exc)) from exc


def _overlay(width: float, height: float, lines: list[str]) -> bytes:
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    from apps.results.certificates import FONT_BOLD, register_fonts

    register_fonts()
    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer, pagesize=(width, height))
    sheet.setFillColor(Color(WATERMARK_GRAY, WATERMARK_GRAY, WATERMARK_GRAY, alpha=WATERMARK_ALPHA))
    size = max(18.0, min(width, height) / 12)
    sheet.saveState()
    sheet.translate(width / 2, height / 2)
    sheet.rotate(35)
    for offset, line in enumerate(lines):
        sheet.setFont(FONT_BOLD, size if offset == 0 else size * 0.55)
        sheet.drawCentredString(0, -offset * size * 0.9, line)
    sheet.restoreState()
    # Druga, mała linia przy dolnej krawędzi – zostaje na kadrze nawet po przycięciu skanu do treści.
    sheet.setFont(FONT_BOLD, 7)
    sheet.drawString(18, 12, " · ".join(lines))
    sheet.showPage()
    sheet.save()
    return buffer.getvalue()


def watermark(data: bytes, lines: list[str]) -> bytes:
    """PDF z napisem ``lines`` na każdej stronie (pierwsza linia duża, kolejne mniejsze)."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(data), strict=False)
    writer = PdfWriter()
    overlays: dict[tuple[float, float], object] = {}
    for page in reader.pages:
        box = page.mediabox
        key = (float(box.width), float(box.height))
        if key not in overlays:
            overlays[key] = PdfReader(io.BytesIO(_overlay(*key, lines))).pages[0]
        page.merge_page(overlays[key])
        writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@dataclass(frozen=True)
class BundleItem:
    """Jedno zadanie w pakiecie: nagłówek i albo bajty PDF-u, albo tekst Markdown."""

    heading: str
    pdf: bytes | None = None
    body_md: str = ""


def _text_pages(item: BundleItem, footer: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer
    from reportlab.platypus.flowables import KeepTogether

    from apps.results.certificates import FONT_BOLD, FONT_REGULAR, register_fonts

    from .markup import plain_blocks

    register_fonts()
    base = ParagraphStyle("tr-body", fontName=FONT_REGULAR, fontSize=11, leading=15, spaceAfter=6)
    head = ParagraphStyle("tr-head", parent=base, fontName=FONT_BOLD, fontSize=15, leading=19, spaceAfter=10)
    sub = ParagraphStyle("tr-sub", parent=base, fontName=FONT_BOLD, fontSize=12, leading=16)
    math = ParagraphStyle("tr-math", parent=base, fontName=FONT_REGULAR, fontSize=10, leftIndent=12 * mm)

    def escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story: list = [Paragraph(escape(item.heading), head)]
    for kind, value in plain_blocks(item.body_md):
        if kind == "heading":
            story.append(Paragraph(escape(value), sub))
        elif kind == "paragraph":
            story.append(Paragraph(escape(value), base))
        else:
            story.append(KeepTogether([Preformatted(value, math), Spacer(1, 4)]))

    def on_page(canvas, document):
        canvas.saveState()
        canvas.setFont(FONT_REGULAR, 8)
        canvas.drawString(20 * mm, 10 * mm, f"{footer} · {document.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=18 * mm
    )
    document.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()


def bundle(items: Iterable[BundleItem], footer: str) -> bytes:
    """Jeden PDF z zadań w kolejności ``items`` – patrz docstring modułu."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for item in items:
        data = item.pdf if item.pdf is not None else _text_pages(item, footer)
        for page in PdfReader(io.BytesIO(data), strict=False).pages:
            writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
