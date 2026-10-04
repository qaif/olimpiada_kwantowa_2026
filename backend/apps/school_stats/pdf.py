"""Raport szkoły w PDF – jedna kartka A4 dla dyrektora (STAT-01 § 6).

Skład tym samym zestawem co reszta dokumentów serwisu (reportlab, kroje DejaVu z polskimi
znakami, rejestracja ``apps.results.certificates.register_fonts``) – nowej biblioteki do PDF-ów
nie dokładamy.

Treść to **wyłącznie agregaty** z ``apps.school_stats.services.school_report``: ani jednego
nazwiska, kodu uczestnika czy wyniku pojedynczej osoby. Raport z założenia wychodzi poza serwis
(sekretariat, rada pedagogiczna, gazetka szkolna), więc nie może nieść niczego, na co zgody
uczniów by nie wystarczyły – a na agregat od pięciu osób zgoda nie jest potrzebna. Pliku nie
przechowujemy: powstaje przy każdym pobraniu z danych, które i tak są w bazie.
"""

from __future__ import annotations

from html import escape
from io import BytesIO

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.points import format_points
from apps.tenancy.branding import competition_name

from .services import K_ANONYMITY

PAGE_MARGIN_MM = 18


def _para(text: str, style):
    """Akapit z tekstem zescapowanym – nazwa szkoły jest danymi, nie znacznikami reportlaba."""
    from reportlab.platypus import Paragraph

    return Paragraph(escape(str(text or ""), quote=False), style)


def _styles():
    from reportlab.lib.styles import ParagraphStyle

    from apps.results.certificates import FONT_BOLD, FONT_REGULAR

    body = ParagraphStyle("ss-body", fontName=FONT_REGULAR, fontSize=9.5, leading=13)
    return {
        "body": body,
        "small": ParagraphStyle("ss-small", parent=body, fontSize=8, leading=10.5),
        "title": ParagraphStyle("ss-title", parent=body, fontName=FONT_BOLD, fontSize=16, leading=20),
        "heading": ParagraphStyle("ss-heading", parent=body, fontName=FONT_BOLD, fontSize=11, leading=15),
        "cell": ParagraphStyle("ss-cell", parent=body, fontSize=8.5, leading=10.5),
        "head": ParagraphStyle("ss-head", parent=body, fontName=FONT_BOLD, fontSize=8.5, leading=10.5),
    }


def _number(value) -> str:
    return "—" if value is None else str(value)


def _line_cells(stage, line, styles) -> list:
    """Komórki jednego wiersza porównania – albo jedna informacja „mniej niż 5” na całą szerokość."""
    aggregate = line["aggregate"]
    cells = [_para(line["label"], styles["cell"])]
    if not line["visible"]:
        hidden = _("mniej niż %(k)s uczestników – dane ukryte") % {"k": K_ANONYMITY}
        return [*cells, _para(hidden, styles["cell"]), "", "", "", ""]
    submitted = _number(aggregate.submitted) if stage.has_submissions else "—"
    on_time = _number(aggregate.on_time) if stage.has_submissions else "—"
    qualified = _number(aggregate.qualified) if stage.published else "—"
    mean = format_points(line["mean"]) if line.get("mean") is not None else "—"
    return [
        *cells,
        _para(str(aggregate.entries), styles["cell"]),
        _para(submitted, styles["cell"]),
        _para(on_time, styles["cell"]),
        _para(qualified, styles["cell"]),
        _para(mean, styles["cell"]),
    ]


def compose_pdf(report: dict, competition) -> bytes:
    """Bajty raportu szkoły. ``report`` – wynik ``services.school_report``."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    from apps.results.certificates import FONT_BOLD, register_fonts

    register_fonts()
    styles = _styles()
    school = report["school"]
    edition = report["edition"]
    brand = competition_name(competition) if competition is not None else ""
    title = _("Raport szkoły")
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=PAGE_MARGIN_MM * mm,
        rightMargin=PAGE_MARGIN_MM * mm,
        topMargin=PAGE_MARGIN_MM * mm,
        bottomMargin=PAGE_MARGIN_MM * mm,
        title=f"{title} – {school.name}",
        author=brand,
        subject=f"{title}, {edition.year_label}",
    )
    width = A4[0] - 2 * PAGE_MARGIN_MM * mm
    story = [
        _para(brand, styles["small"]),
        Spacer(1, 3 * mm),
        _para(title, styles["title"]),
        Spacer(1, 2 * mm),
        _para(f"{school.name}, {school.city}", styles["heading"]),
        _para(
            _("Edycja: %(edition)s · stan na %(date)s")
            % {
                "edition": edition.year_label,
                "date": timezone.localdate().strftime("%d.%m.%Y"),
            },
            styles["body"],
        ),
        Spacer(1, 5 * mm),
    ]
    header = [
        _para(_("Grupa"), styles["head"]),
        _para(_("Uczestnicy"), styles["head"]),
        _para(_("Oddane prace"), styles["head"]),
        _para(_("W terminie"), styles["head"]),
        _para(_("Zakwalifikowani"), styles["head"]),
        _para(_("Średnia pkt"), styles["head"]),
    ]
    columns = [width * 0.34, *([width * 0.132] * 5)]
    for item in report["stages"]:
        stage = item["stage"]
        status = _("wyniki ogłoszone") if stage.published else _("wyniki jeszcze nieogłoszone")
        story.append(_para(f"{stage.name} – {status}", styles["heading"]))
        rows = [header]
        spans = []
        for index, line in enumerate(item["lines"], start=1):
            rows.append(_line_cells(stage, line, styles))
            if not line["visible"]:
                spans.append(("SPAN", (1, index), (-1, index)))
        table = Table(rows, colWidths=columns, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.grey),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    *spans,
                ]
            )
        )
        story += [table, Spacer(1, 4 * mm)]
    story.append(_para(_("Udział szkoły w kolejnych edycjach"), styles["heading"]))
    history = [[_para(_("Edycja"), styles["head"]), _para(_("Uczestnicy ze szkoły"), styles["head"])]]
    for item in report["history"]:
        # Liczba osób tylko wtedy, gdy nie da się z niej odjąć znanych uczniów (L2, ``school_report``).
        count = str(item["participants"]) if item["visible"] else _("mniej niż %(k)s") % {"k": K_ANONYMITY}
        history.append([_para(item["edition"].year_label, styles["cell"]), _para(count, styles["cell"])])
    table = Table(history, colWidths=[width * 0.6, width * 0.4])
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.grey),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.lightgrey),
            ]
        )
    )
    story += [table, Spacer(1, 6 * mm)]
    story.append(
        _para(
            _(
                "Raport zawiera wyłącznie dane zbiorcze. Grupy mniejsze niż %(k)s osób są ukryte, żeby "
                "z liczb nie dało się odczytać wyniku konkretnego ucznia. Punkty i kwalifikacja pochodzą "
                "z oficjalnie ogłoszonych wyników etapu; etap bez ogłoszonych wyników ma tylko liczbę "
                "uczestników i oddanych prac."
            )
            % {"k": K_ANONYMITY},
            styles["small"],
        )
    )
    doc.build(story)
    return buffer.getvalue()


def pdf_filename(school, edition) -> str:
    """Nazwa pliku z numeru RSPO, a nie z nazwy szkoły – nazwa bywa długa i pełna znaków."""
    return f"raport-szkoly-{school.rspo}-{edition.pk}.pdf"
