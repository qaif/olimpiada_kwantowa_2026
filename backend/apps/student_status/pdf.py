"""Imienny wzór „Zaświadczenia o statusie ucznia” – PDF do wydrukowania i podstemplowania w szkole.

Wzór jest **imienny**, bo o to poprosił organizator i bo to jest różnica, która ma znaczenie
w sekretariacie: kartka z wpisanym imieniem, nazwiskiem, datą urodzenia i szkołą wymaga od szkoły
wyłącznie sprawdzenia, wpisania klasy, pieczątki i podpisu. Pusty formularz do wypełnienia ręcznie
wraca z nazwiskiem przepisanym z błędem albo z „Liceum nr 5” zamiast pełnej nazwy – i wtedy
koordynator nie ma jak dopasować skanu do uczestnika.

Dlaczego skład tu, a nie w ``apps.results.certificates``: tamten moduł składa **dyplom** – poziomą
kartę z winietą, numerem, kodem weryfikacyjnym i rejestrem wystawionych dokumentów. Ten papier nie
ma numeru ani rejestru (to nie organizator go wystawia, tylko szkoła), jest pionowym A4 i ma puste
pola do wypełnienia długopisem. Wspólne są **kroje** (DejaVu z polskimi znakami, ta sama rejestracja
``register_fonts``) i **źródło napisów** (``apps.tenancy.documents.render_document`` z rodzajem
``STUDENT_STATUS``) – czyli dokładnie to, co wspólne być powinno.

Tekst jest konfiguracją konkursu tak samo, jak zdanie na dyplomie: przy włączonej fladze
``document_templates`` organizator zmienia tytuł, zdanie główne, linię podpisu i dopisek na ekranie
„Szablony dokumentów”, a bez niej obowiązują napisy z tego modułu. Napisy odwrotu **same** mają
znaczniki (``{recipient}``, ``{school_year}``…) i przechodzą przez to samo podstawienie, co wiersz
z bazy – inaczej odwrót musiałby składać zdanie drugą drogą.

PDF-a nie przechowujemy (jak dyplomu): powstaje przy każdym pobraniu z danych, które i tak są
w profilu. Kopia z imieniem i datą urodzenia leżąca w storage byłaby drugim miejscem tych samych
danych osobowych bez żadnego pożytku.
"""

from __future__ import annotations

import re
from html import escape
from io import BytesIO

from django.utils import timezone

from apps.tenancy.branding import competition_name
from apps.tenancy.documents import DocumentKind, render_document, substitute

#: Napisy odwrotu – obowiązują, dopóki konkurs nie ma własnego szablonu rodzaju ``STUDENT_STATUS``.
FALLBACK_TITLE = "Zaświadczenie o statusie ucznia"
FALLBACK_STATEMENT = (
    "Zaświadcza się, że {recipient}, urodzony(-a) {birth_date}, jest w roku szkolnym {school_year} "
    "uczniem (uczennicą) klasy {class_blank} w szkole: {school}."
)
FALLBACK_SIGNATURE_LINE = "podpis dyrektora lub sekretarza szkoły"
FALLBACK_FOOTER_NOTE = (
    "Zaświadczenie wydaje się na potrzeby udziału w {competition_locative} ({edition}), "
    "organizowanej przez: {organizer}."
)

#: Miejsce do wypełnienia długopisem. Kropki, a nie podkreślenie: podkreślenie w PDF-ie jest
#: kreską pod pustym tekstem, której drukarka w trybie oszczędnym potrafi nie wydrukować.
BLANK = "…………………………"
SHORT_BLANK = "…………"

#: Rok szkolny w oznaczeniu edycji („I edycja 2026/2027”, „XV (2026/2027)”).
SCHOOL_YEAR_RE = re.compile(r"(\d{4})\s*[/–-]\s*(\d{4})")

PAGE_MARGIN_MM = 20


def school_year(edition) -> str:
    """Rok szkolny z oznaczenia edycji albo całe oznaczenie, gdy roku w nim nie ma."""
    label = getattr(edition, "year_label", "") or ""
    match = SCHOOL_YEAR_RE.search(label)
    return f"{match.group(1)}/{match.group(2)}" if match else label


def recipient_name(participant) -> str:
    """Imię i nazwisko z konta – w tej postaci, w jakiej uczestnik podał je przy rejestracji."""
    user = participant.user
    return (user.get_full_name() or "").strip()


def document_context(participant, edition, *, today=None) -> dict[str, str]:
    """Wartości podstawień jednego zaświadczenia. Puste pole to miejsce do wypełnienia ręcznie.

    Data urodzenia bywa nieznana (profile sprzed wydania 0.30.0 mają sam rocznik) – wtedy na papierze
    zostaje miejsce do wpisania, a nie rocznik udający datę. Klasa jest **zawsze** pusta, choć profil
    ją zna: klasa w profilu jest deklaracją ucznia, a zaświadczenie ma ją potwierdzić ręką szkoły.
    """
    today = today or timezone.localdate()
    return {
        "recipient": recipient_name(participant) or BLANK,
        "birth_date": participant.birth_date.strftime("%d.%m.%Y") if participant.birth_date else BLANK,
        "school": (participant.school or "").strip() or BLANK,
        "edition": edition.year_label,
        "school_year": school_year(edition) or BLANK,
        "participant_code": participant.public_code,
        "date": today.strftime("%d.%m.%Y"),
    }


def _fallback(competition, context: dict[str, str]) -> dict[str, str]:
    """Napisy odwrotu po podstawieniu – tym samym, które dostaje wiersz szablonu z bazy."""
    values = {**context, "class_blank": SHORT_BLANK}
    return {
        "title": FALLBACK_TITLE,
        "statement": substitute(FALLBACK_STATEMENT, competition, **values),
        "signature_line": FALLBACK_SIGNATURE_LINE,
        "footer_note": substitute(FALLBACK_FOOTER_NOTE, competition, **values),
        "author": competition_name(competition) if competition is not None else "Olimpiada Kwantowa",
    }


def _styles():
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle

    from apps.results.certificates import FONT_BOLD, FONT_REGULAR

    body = ParagraphStyle("body", fontName=FONT_REGULAR, fontSize=11, leading=17, alignment=TA_JUSTIFY)
    return {
        "body": body,
        "brand": ParagraphStyle(
            "brand", parent=body, fontName=FONT_BOLD, fontSize=10, leading=13, alignment=0
        ),
        "small": ParagraphStyle("small", parent=body, fontSize=8.5, leading=11, alignment=0),
        "caption": ParagraphStyle("caption", parent=body, fontSize=8, leading=10, alignment=TA_CENTER),
        "title": ParagraphStyle(
            "title", parent=body, fontName=FONT_BOLD, fontSize=17, leading=22, alignment=TA_CENTER
        ),
    }


def _para(text: str, style):
    """Akapit z tekstem **zescapowanym**: imię, szkoła i tekst szablonu nie są znacznikami reportlaba.

    ``Paragraph`` czyta treść jak mini-HTML, więc szkoła nazwana „<b>LO</b>” albo imię z ``&`` bez
    escape'u zmieniałyby skład albo wywracały go wyjątkiem w chwili pobrania.
    """
    from reportlab.platypus import Paragraph

    return Paragraph(escape(text or "", quote=False), style)


def compose_pdf(participant, edition, *, competition=None, panel_url: str = "", today=None) -> bytes:
    """Bajty imiennego wzoru zaświadczenia – A4 pionowo, jedna strona.

    ``competition`` domyślnie bierze się z edycji (dokument należy do konkursu, którego edycji
    dotyczy). ``panel_url`` to adres, pod który uczestnik ma wgrać skan – drukowany w ramce „dla
    ucznia”, bo kartka wraca ze szkoły po kilku dniach, a adres panelu nie jest czymś, co się pamięta.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    from apps.results.certificates import register_fonts

    register_fonts()
    competition = competition if competition is not None else edition.competition
    context = document_context(participant, edition, today=today)
    rendered = render_document(
        competition,
        DocumentKind.STUDENT_STATUS,
        fallback=_fallback(competition, context),
        **{**context, "class_blank": SHORT_BLANK},
    )
    styles = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=PAGE_MARGIN_MM * mm,
        rightMargin=PAGE_MARGIN_MM * mm,
        topMargin=PAGE_MARGIN_MM * mm,
        bottomMargin=PAGE_MARGIN_MM * mm,
        title=f"{rendered.title} – {context['recipient']}",
        author=rendered.author,
        subject=f"{rendered.title}, {edition.year_label}",
    )
    width = A4[0] - 2 * PAGE_MARGIN_MM * mm

    # Nagłówek: po lewej pieczątka szkoły (pusta ramka), po prawej miejscowość i data.
    stamp = Table(
        [[""], [_para("pieczątka szkoły", styles["caption"])]],
        colWidths=[65 * mm],
        rowHeights=[32 * mm, 6 * mm],
    )
    stamp.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (0, 0), 0.6, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    place = _para(f"{BLANK}, dnia {BLANK}", styles["small"])
    place_caption = _para("miejscowość i data", styles["caption"])
    header = Table(
        [[stamp, [Spacer(1, 8 * mm), place, place_caption]]],
        colWidths=[width * 0.5, width * 0.5],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    signature = Table(
        [
            ["", _para(BLANK + BLANK, styles["caption"])],
            ["", _para(rendered.signature_line, styles["caption"])],
        ],
        colWidths=[width * 0.45, width * 0.55],
    )
    signature.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    note_lines = [
        "Informacja dla ucznia: po podpisaniu i podstemplowaniu w szkole zeskanuj albo sfotografuj "
        "całą kartkę (PDF, JPG albo PNG, do 10 MB) i wgraj ją w panelu uczestnika, w sekcji "
        "„Status ucznia”.",
    ]
    if panel_url:
        note_lines.append(f"Adres: {panel_url}")
    note_lines.append(f"Kod uczestnika: {context['participant_code']} · wzór wygenerowano {context['date']}.")
    note = Table([[[_para(line, styles["small"]) for line in note_lines]]], colWidths=[width])
    note.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    brand = competition_name(competition) if competition is not None else rendered.author
    story = [
        _para(brand, styles["brand"]),
        Spacer(1, 6 * mm),
        header,
        Spacer(1, 16 * mm),
        _para(rendered.title, styles["title"]),
        Spacer(1, 12 * mm),
        _para(rendered.statement, styles["body"]),
        Spacer(1, 6 * mm),
    ]
    if rendered.footer_note:
        story += [_para(rendered.footer_note, styles["body"]), Spacer(1, 6 * mm)]
    story += [Spacer(1, 22 * mm), signature, Spacer(1, 30 * mm), note]
    doc.build(story)
    return buffer.getvalue()


def pdf_filename(participant) -> str:
    """Nazwa pobieranego pliku: kod uczestnika, a nie nazwisko – plik bywa przesyłany dalej."""
    return f"zaswiadczenie-status-ucznia-{participant.public_code}.pdf"
