"""Pro forma i faktura: numeracja ciągła, migawka treści i skład PDF (PAY-01 § 1, § 3).

**Numeracja bez luk.** Numer ``PREFIKS/PF|FV/ROK/NNNN`` bierze się z :class:`DocumentCounter`
zablokowanego ``SELECT … FOR UPDATE`` w **tej samej transakcji**, w której powstaje dokument. Wycofana
transakcja wycofuje też licznik, więc numer nie przepada; dwie równoległe wpłaty szeregują się na
wierszu licznika. Licznik jest per konkurs, rodzaj i rok – IQO i konkurs polski mają własne serie,
a pro formy nie zabierają numerów fakturom.

**Migawka, nie odczyt.** Dokument zapisuje dane sprzedawcy, nabywcy, pozycje i sumy z chwili
wystawienia, a PDF składa się z migawki przy każdym pobraniu (pliku nie przechowujemy – jak dyplom).
Zmiana adresu organizatora albo nabywcy po fakcie nie zmienia faktury, którą ktoś zaksięgował.

**Granice (D15 po zmianie z 4.10.2026).** Kwoty są brutto, podatku system nie liczy – adnotacja VAT
jest tekstem organizatora. Faktur korygujących nie ma: zwrot jest zapisany w rejestrze i w audycie,
a korektę wystawia księgowość organizatora (OPERACJE § 29).
"""

from __future__ import annotations

from html import escape
from io import BytesIO

from django.db import IntegrityError, transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from .models import (
    DOCUMENT_CODES,
    BillingDocument,
    DocumentCounter,
    DocumentKind,
    Order,
    Provider,
)

#: Języki, w których składamy dokument. Kroje DejaVu (``apps.results.certificates``) mają alfabet
#: łaciński i cyrylicę, ale nie chińskie, dewanagari, arabskie ani bengalskie znaki – dokument w tych
#: językach byłby rzędem prostokątów. Płacący z takim językiem dostaje dokument po angielsku.
DOCUMENT_LANGUAGES = frozenset({"pl", "en", "es", "fr", "pt", "ru", "id"})


def document_language(language: str) -> str:
    return language if language in DOCUMENT_LANGUAGES else "en"


def _next_number(competition, kind: str, year: int) -> int:
    """Kolejny numer serii – pod blokadą wiersza licznika (patrz docstring modułu)."""
    try:
        with transaction.atomic():
            DocumentCounter.objects.get_or_create(competition=competition, kind=kind, year=year)
    except IntegrityError:
        pass  # równoległe założenie wiersza – i tak go za chwilę zablokujemy
    counter = DocumentCounter.objects.select_for_update().get(competition=competition, kind=kind, year=year)
    counter.last += 1
    counter.save(update_fields=["last"])
    return counter.last


def money(amount) -> str:
    return f"{amount:,.2f}".replace(",", " ")


def _seller(competition) -> dict:
    from .services import settings_for

    settings_row = settings_for(competition)
    return {
        "name": competition.organizer_name,
        "address": competition.organizer_address,
        "registry": competition.organizer_registry,
        "tax_id": settings_row.seller_tax_id,
        "email": competition.contact_email,
        "bank_account": settings_row.bank_account,
        "bank_swift": settings_row.bank_swift,
        "bank_name": settings_row.bank_name,
        "vat_note": settings_row.vat_note,
        "note": settings_row.invoice_note,
    }


def _snapshot(order: Order, kind: str, now) -> dict:
    payment = None
    if kind == DocumentKind.INVOICE:
        payment = order.payments.filter(status="SUCCEEDED").order_by("-succeeded_at", "-id").first()
    return {
        "seller": _seller(order.competition),
        "buyer": {
            "type": order.buyer_type,
            "name": order.buyer_name,
            "address": order.buyer_address,
            "country": order.buyer_country,
            "vat_id": order.buyer_vat_id,
            "email": order.buyer_email,
        },
        "lines": [
            {
                "description": line.description,
                "quantity": line.quantity,
                "unit_price": str(line.unit_price),
                "amount": str(line.amount),
            }
            for line in order.lines.all()
        ],
        "currency": order.currency,
        "total": str(order.total),
        "issue_date": timezone.localdate(now).isoformat(),
        "due_date": order.due_on.isoformat() if order.due_on else "",
        "sale_date": timezone.localdate(order.paid_at).isoformat() if order.paid_at else "",
        "order_reference": order.reference,
        "competition": order.competition.name,
        "edition": order.edition.year_label,
        "payment_method": payment.provider if payment else "",
        "paid_at": timezone.localdate(payment.succeeded_at).isoformat()
        if payment and payment.succeeded_at
        else "",
    }


def issue_document(order: Order, kind: str) -> BillingDocument:
    """Wystawia dokument zamówienia albo oddaje istniejący (jeden rodzaj na zamówienie – więz).

    Woła się wewnątrz transakcji serwisu (wystawienie zamówienia, zapis wpłaty): numer i dokument
    powstają razem albo wcale.
    """
    existing = BillingDocument.objects.filter(order=order, kind=kind).first()
    if existing is not None:
        return existing
    from .services import settings_for

    now = timezone.now()
    year = timezone.localdate(now).year
    with transaction.atomic():
        sequence = _next_number(order.competition, kind, year)
        prefix = settings_for(order.competition).prefix
        return BillingDocument.objects.create(
            order=order,
            competition=order.competition,
            kind=kind,
            year=year,
            sequence=sequence,
            number=f"{prefix}/{DOCUMENT_CODES[kind]}/{year}/{sequence:04d}",
            issued_at=now,
            language=document_language(order.language),
            snapshot=_snapshot(order, kind, now),
        )


# --- PDF ------------------------------------------------------------------------------------------


def _method_label(code: str) -> str:
    return dict(Provider.choices).get(code, code) if code else ""


def render_pdf(document: BillingDocument) -> bytes:
    """PDF z migawki, w języku dokumentu. Te same kroje i ta sama ścieżka ReportLab, co dyplom."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from apps.results.certificates import FONT_BOLD, FONT_REGULAR, register_fonts

    register_fonts()
    data = document.snapshot
    seller, buyer = data["seller"], data["buyer"]
    currency = data["currency"]
    title_style = ParagraphStyle("doc-title", fontName=FONT_BOLD, fontSize=16, leading=20, spaceAfter=4)
    body = ParagraphStyle("doc-body", fontName=FONT_REGULAR, fontSize=9.5, leading=13)
    bold = ParagraphStyle("doc-bold", parent=body, fontName=FONT_BOLD)
    note = ParagraphStyle("doc-note", fontName=FONT_REGULAR, fontSize=8, leading=11)

    def para(text, style=body):
        # Wartości od płacącego (nazwa, adres) są tekstem, nie znacznikami ReportLaba.
        return Paragraph(escape(str(text or ""), quote=False).replace("\n", "<br/>"), style)

    with translation.override(document.language):
        is_invoice = document.kind == DocumentKind.INVOICE
        title = _("Faktura") if is_invoice else _("Faktura pro forma")
        story = [
            para(f"{title} {document.number}", title_style),
            para(f"{data['competition']} – {data['edition']}"),
            Spacer(1, 4 * mm),
        ]
        dates = [[para(_("Data wystawienia"), bold), para(data["issue_date"])]]
        if is_invoice:
            dates.append([para(_("Data sprzedaży"), bold), para(data["sale_date"] or data["issue_date"])])
            dates.append(
                [
                    para(_("Zapłacono"), bold),
                    para(f"{data['paid_at']} · {_method_label(data['payment_method'])}"),
                ]
            )
        else:
            dates.append([para(_("Termin płatności"), bold), para(data["due_date"] or "—")])
        dates.append([para(_("Kod referencyjny"), bold), para(data["order_reference"])])
        story += [Table(dates, colWidths=[45 * mm, 129 * mm]), Spacer(1, 5 * mm)]

        seller_lines = [seller["name"], seller["address"], seller["registry"]]
        if seller["tax_id"]:
            seller_lines.append(f"{_('NIP / VAT ID')}: {seller['tax_id']}")
        buyer_lines = [buyer["name"], buyer["address"], buyer["country"]]
        if buyer["vat_id"]:
            buyer_lines.append(f"{_('NIP / VAT ID')}: {buyer['vat_id']}")
        parties = Table(
            [
                [para(_("Sprzedawca"), bold), para(_("Nabywca"), bold)],
                [para("\n".join(x for x in seller_lines if x)), para("\n".join(x for x in buyer_lines if x))],
            ],
            colWidths=[87 * mm, 87 * mm],
        )
        parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [parties, Spacer(1, 6 * mm)]

        rows = [
            [para(_("Pozycja"), bold), para(_("Ilość"), bold), para(_("Cena"), bold), para(_("Kwota"), bold)]
        ]
        for line in data["lines"]:
            rows.append(
                [
                    para(line["description"]),
                    para(line["quantity"]),
                    para(f"{money(_decimal(line['unit_price']))} {currency}"),
                    para(f"{money(_decimal(line['amount']))} {currency}"),
                ]
            )
        rows.append(
            [
                "",
                "",
                para(_("Razem do zapłaty") if not is_invoice else _("Razem"), bold),
                para(f"{money(_decimal(data['total']))} {currency}", bold),
            ]
        )
        lines_table = Table(rows, colWidths=[86 * mm, 18 * mm, 35 * mm, 35 * mm], repeatRows=1)
        lines_table.setStyle(
            TableStyle(
                [
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
                    ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story += [lines_table, Spacer(1, 6 * mm)]

        if not is_invoice and seller["bank_account"]:
            bank = [
                f"{_('Odbiorca')}: {seller['name']}",
                f"IBAN: {seller['bank_account']}",
            ]
            if seller["bank_swift"]:
                bank.append(f"SWIFT/BIC: {seller['bank_swift']}")
            if seller["bank_name"]:
                bank.append(f"{_('Bank')}: {seller['bank_name']}")
            bank.append(f"{_('Tytuł przelewu')}: {data['order_reference']}")
            story += [para(_("Dane do przelewu"), bold), para("\n".join(bank)), Spacer(1, 5 * mm)]
        if seller["vat_note"]:
            story.append(para(seller["vat_note"], note))
        if seller["note"]:
            story.append(para(seller["note"], note))
        if not is_invoice:
            story.append(
                para(
                    _(
                        "Faktura pro forma nie jest dokumentem księgowym. "
                        "Fakturę wystawimy po zaksięgowaniu wpłaty."
                    ),
                    note,
                )
            )

    buffer = BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=document.number,
        author=seller["name"],
    ).build(story)
    return buffer.getvalue()


def _decimal(value):
    from decimal import Decimal

    return Decimal(str(value))


def filename(document: BillingDocument) -> str:
    """Nazwa pliku z numeru – bez nazwy nabywcy (plik krąży po skrzynkach i dyskach)."""
    return document.number.replace("/", "-") + ".pdf"
