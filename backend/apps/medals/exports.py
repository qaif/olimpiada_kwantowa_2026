"""Eksport medali na galę: CSV dla biura i PDF z listą wręczeń (MED-01 § 4).

Oba pliki są **wewnętrzne** (koordynator) i niosą imiona i nazwiska – lista na galę musi je mieć,
bo ktoś odczytuje je ze sceny. Strona publiczna tych danych nie ma; tutaj o dostępie rozstrzyga rola,
a każde pobranie jest wpisem w audycie (``services.audit_export``).

Kolejność w PDF-ie jest kolejnością **wręczania**: wyróżnienia, brąz, srebro, złoto – tak, jak
woła się nagrodzonych na scenę – a w grupie po kraju i nazwisku. CSV jest po miejscu w rankingu,
bo czyta go arkusz, a nie konferansjer.
"""

from __future__ import annotations

from io import BytesIO

from apps.core.exports import Dataset

from . import typesetting
from .models import Award

#: Kolejność grup na liście wręczeń.
CEREMONY_ORDER = (Award.HONOURABLE, Award.BRONZE, Award.SILVER, Award.GOLD)

CSV_HEADER = [
    "miejsce",
    "nagroda",
    "nagroda wyliczona",
    "zmiana ręczna",
    "imię",
    "nazwisko",
    "kraj",
    "szkoła",
    "suma punktów",
    "kod uczestnika",
]


def csv_dataset(rows: list[dict], *, filename: str) -> Dataset:
    """Wszyscy z pola (także bez nagrody) po miejscu – biuro sprawdza w arkuszu całość, nie tylko gali."""
    labels = dict(Award.choices)
    return Dataset(
        header=CSV_HEADER,
        rows=(
            [
                row["rank"],
                str(labels.get(row["award"], row["award"])),
                str(labels.get(row["computed"], row["computed"])),
                row["overridden"],
                row["first_name"],
                row["last_name"],
                row["country"],
                row["school"],
                row["total"],
                row["public_code"],
            ]
            for row in rows
        ),
        count=len(rows),
        title="Medale",
        filename=filename,
    )


def ceremony_groups(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Grupy listy wręczeń w kolejności sceny; w grupie po kraju, nazwisku i imieniu."""
    groups = []
    for award in CEREMONY_ORDER:
        members = sorted(
            (row for row in rows if row["award"] == award),
            key=lambda row: (
                row["country"].casefold(),
                row["last_name"].casefold(),
                row["first_name"].casefold(),
            ),
        )
        if members:
            groups.append((str(award), members))
    return groups


def ceremony_pdf(rows: list[dict], *, title: str, subtitle: str) -> bytes:
    """Lista wręczeń A4 pionowo: grupa, a w niej kraj – nazwisko – suma. Nazwiska w każdym piśmie."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    labels = dict(Award.choices)
    buffer = BytesIO()
    width, height = A4
    canvas = pdf_canvas.Canvas(buffer, pagesize=A4)
    canvas.setTitle(title)
    canvas.setSubject(subtitle)
    margin = 50
    y = height - margin

    def new_page():
        nonlocal y
        canvas.showPage()
        y = height - margin

    typesetting.draw_text(canvas, title, x=width / 2, y=y, size=16, bold=True, max_width=width - 2 * margin)
    y -= 20
    typesetting.draw_text(canvas, subtitle, x=width / 2, y=y, size=10, max_width=width - 2 * margin)
    y -= 30
    for award, members in ceremony_groups(rows):
        if y < margin + 60:
            new_page()
        typesetting.draw_text(
            canvas, f"{labels[award]} ({len(members)})", x=margin, y=y, size=13, bold=True, align="left"
        )
        y -= 20
        for index, row in enumerate(members, start=1):
            if y < margin:
                new_page()
            typesetting.draw_text(canvas, f"{index}.", x=margin + 18, y=y, size=10, align="right")
            typesetting.draw_text(
                canvas, row["country"] or "—", x=margin + 26, y=y, size=10, align="left", max_width=150
            )
            typesetting.draw_text(
                canvas,
                row["name"] or row["public_code"],
                x=margin + 185,
                y=y,
                size=10,
                align="left",
                max_width=250,
            )
            typesetting.draw_text(canvas, str(row["total"]), x=width - margin, y=y, size=10, align="right")
            y -= 15
        y -= 12
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()
