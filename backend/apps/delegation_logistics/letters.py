"""Listy zapraszające do wizy: wystawienie z numerem, rejestr i PDF z migawki (LOG-01 § 4).

Tekst listu jest konfiguracją konkursu (``apps.tenancy.documents``, rodzaj ``VISA_INVITATION``):
przy fladze ``document_templates`` organizator pisze tytuł, zdanie główne, linię podpisu i dopisek
na ekranie „Szablony dokumentów”, a bez niej obowiązuje angielski tekst odwrotu z tego modułu – list
idzie do konsulatu kraju delegacji, a językiem olimpiady międzynarodowej jest angielski.

Podpis i pieczęć: bloki podpisu (obraz, imię i nazwisko, funkcja) z szablonu graficznego dyplomów
(``results.CertificateTemplate`` – wspólny dla wszystkich rodzajów albo dla edycji) i pieczęć
elektroniczna organizatora (``apps.results.signing``), gdy jest skonfigurowana. Bez nich list
wychodzi z linią do odręcznego podpisu – tak samo, jak dyplom bez pieczęci.

**Numer** ``PREFIKS/ROK/NNNN`` nadawany pod blokadą wiersza finału: dwa listy wystawione w tej samej
sekundzie dostają kolejne numery, a nie ten sam (więz unikalności i tak by to odrzucił – blokada
zamienia odrzucenie w kolejkę).

**PDF z migawki**, a nie z bieżących danych – powód w docstringu ``InvitationLetter``.
"""

from __future__ import annotations

import json
import logging
import re
from html import escape
from io import BytesIO

from django.db import connection, transaction
from django.db.models import Max
from django.http import Http404
from django.utils import timezone, translation
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy.branding import competition_name
from apps.tenancy.documents import DocumentKind, current_version, render_document, substitute

from .models import DelegationMember, FieldGroup, FinalEvent, InvitationLetter, LetterScope
from .services import REQUIRED_FIELDS, event_for, members_of

logger = logging.getLogger(__name__)

FALLBACK_TITLE = "Letter of invitation"
FALLBACK_STATEMENT = (
    "On behalf of {organizer}, we have the pleasure of inviting the person(s) listed below, members "
    "of the national delegation of {country}, to take part in {event}, which will take place in "
    "{city} on {event_dates}. This letter is issued at the request of the national delegation for "
    "the purpose of a visa application."
)
FALLBACK_SIGNATURE_LINE = "for the organizer"
FALLBACK_FOOTER_NOTE = "Letter no. {number} of {date}. To verify this letter, please contact {organizer}."

PAGE_MARGIN_MM = 20
#: Przesunięcie klucza blokady doradczej numeracji listów – odróżnia ją od innych blokad w bazie.
LOCK_NAMESPACE = 0x4C4F4700  # „LOG\0”
PREFIX_RE = re.compile(r"[^A-Za-z0-9-]")


def letter_prefix(event: FinalEvent | None, competition) -> str:
    raw = (event.letter_prefix if event is not None else "") or competition.slug
    return PREFIX_RE.sub("", raw).upper()[:20] or "LTR"


def _identity_complete(member: DelegationMember) -> bool:
    return all(getattr(member, field) for field in REQUIRED_FIELDS[FieldGroup.IDENTITY])


def _person(member: DelegationMember) -> dict:
    """Wiersz migawki: dane z dokumentu podróży w chwili wystawienia listu."""
    return {
        "member_id": member.pk,
        "name": member.passport_name,
        "nationality": member.nationality,
        "birth_date": member.date_of_birth,
        "passport": member.passport_number,
        "expiry": member.passport_expiry,
        "role": str(member.role_label),
    }


def _require_event(event: FinalEvent | None) -> FinalEvent:
    if event is None or not (event.name and event.city and event.starts_on and event.ends_on):
        raise DomainError(
            "Uzupełnij ustawienia finału (nazwa, miasto, daty) – list zapraszający je cytuje.",
            "EVENT_INCOMPLETE",
            status.HTTP_409_CONFLICT,
        )
    if event.purged_at is not None:
        raise DomainError(
            "Dane tego finału zostały już usunięte.", "LOGISTICS_PURGED", status.HTTP_409_CONFLICT
        )
    return event


def issue_letter(competition, delegation, *, member: DelegationMember | None = None, actor, request=None):
    """Wystawia list: imienny (``member``) albo dla całej delegacji (osoby z kompletnym dokumentem).

    Osoby bez kompletnego dokumentu podróży nie trafiają na list delegacji – list z pustą rubryką
    paszportu konsulat odrzuci, a nowy list z nowym numerem i tak trzeba będzie wystawić. Ekran mówi,
    kogo pominięto. List imienny dla osoby bez danych jest odmową.
    """
    if delegation.competition_id != competition.pk:
        raise Http404("Delegacja należy do innego konkursu.")
    edition = delegation.edition
    if member is not None:
        # Świeży odczyt: list cytuje dane z bazy, a nie z obiektu, który wołający trzyma od chwili.
        member = DelegationMember.objects.filter(pk=member.pk).first()
        if member is None or member.delegation_id != delegation.pk:
            raise Http404("Osoba należy do innej delegacji.")
        if not _identity_complete(member):
            raise DomainError(
                "Ta osoba nie ma kompletnych danych dokumentu podróży.",
                "IDENTITY_INCOMPLETE",
                status.HTTP_409_CONFLICT,
            )
        people = [member]
        scope = LetterScope.PERSON
    else:
        people = [m for m in members_of(delegation) if _identity_complete(m)]
        scope = LetterScope.DELEGATION
        if not people:
            raise DomainError(
                "Nikt w tej delegacji nie ma jeszcze kompletnych danych dokumentu podróży.",
                "IDENTITY_INCOMPLETE",
                status.HTTP_409_CONFLICT,
            )
    with transaction.atomic():
        event = _require_event(FinalEvent.objects.select_for_update().filter(edition=edition).first())
        now = timezone.now()
        year = timezone.localdate(now).year
        # Numeracja jest per (konkurs, rok), a nie per finał: dwie edycje w jednym roku kalendarzowym
        # dzielą licznik. Blokada doradcza PostgreSQL-a na tę parę szereguje wystawienia dokładnie
        # tam, gdzie powstaje wyścig, i nie dotyka żadnego wiersza (L8).
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [LOCK_NAMESPACE + competition.pk, year])
        top = (
            InvitationLetter.objects.filter(competition=competition, year=year).aggregate(
                top=Max("sequence")
            )["top"]
            or 0
        )
        sequence = top + 1
        letter = InvitationLetter.objects.create(
            competition=competition,
            edition=edition,
            number=f"{letter_prefix(event, competition)}/{year}/{sequence:04d}",
            year=year,
            sequence=sequence,
            scope=scope,
            delegation=delegation,
            member=member,
            country_name=delegation.country.name,
            people_count=len(people),
            content=json.dumps([_person(p) for p in people], ensure_ascii=False),
            template_version=current_version(competition, DocumentKind.VISA_INVITATION),
            issued_at=now,
            issued_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
    audit(
        actor,
        "logistics.letter_issued",
        letter,
        {"number": letter.number, "delegation": delegation.pk, "people": len(people), "scope": scope},
        request=request,
    )
    return letter


def letters_of(competition, edition=None, delegation=None):
    rows = InvitationLetter.objects.for_competition(competition).select_related("issued_by", "member")
    if edition is not None:
        rows = rows.filter(edition=edition)
    if delegation is not None:
        rows = rows.filter(delegation=delegation)
    return rows.order_by("-year", "-sequence")


def letter_for(competition, pk: int, *, delegation=None) -> InvitationLetter:
    rows = InvitationLetter.objects.for_competition(competition)
    if delegation is not None:
        rows = rows.filter(delegation=delegation)
    letter = rows.select_related("edition", "delegation").filter(pk=pk).first()
    if letter is None:
        raise Http404("Nie ma takiego listu.")
    return letter


def people_of(letter: InvitationLetter) -> list[dict]:
    if not letter.content:
        return []
    try:
        return json.loads(letter.content)
    except ValueError:
        return []


def drop_person(letter: InvitationLetter, member_ids) -> None:
    """Wymazuje osoby z migawki (usunięcie członka, konta, gościa) – reszta listu delegacji zostaje."""
    if isinstance(member_ids, int):
        member_ids = [member_ids]
    wanted = set(member_ids)
    before = people_of(letter)
    people = [person for person in before if person.get("member_id") not in wanted]
    if len(people) == len(before):
        return
    letter.content = json.dumps(people, ensure_ascii=False) if people else ""
    if not people:
        letter.content_purged_at = timezone.now()
    letter.save(update_fields=["content", "content_purged_at"])


def _dates(event: FinalEvent) -> str:
    start, end = event.starts_on, event.ends_on
    if start == end:
        return start.strftime("%d %B %Y")
    return f"{start.strftime('%d %B %Y')} – {end.strftime('%d %B %Y')}"


def _para(text: str, style):
    from reportlab.platypus import Paragraph

    return Paragraph(escape(text or "", quote=False), style)


def letter_pdf(letter: InvitationLetter) -> bytes:
    """PDF listu z migawki. Brak migawki (po retencji) – odmowa, a nie pusty list z numerem."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, SimpleDocTemplate, Spacer, Table, TableStyle

    from apps.results.certificates import (
        FONT_BOLD,
        FONT_REGULAR,
        _image_bytes,
        competition_logo,
        register_fonts,
        resolve_template,
    )
    from apps.results.signing import sign_document

    people = people_of(letter)
    if not people:
        raise DomainError(
            "Dane osób z tego listu zostały usunięte (retencja) – list nie może być ponownie pobrany.",
            "LETTER_PURGED",
            status.HTTP_410_GONE,
        )
    competition = letter.competition
    event = event_for(letter.edition)
    register_fonts()
    names = ", ".join(person["name"] for person in people)
    issued = timezone.localtime(letter.issued_at)
    context = {
        "recipient": names,
        "number": letter.number,
        "date": issued.strftime("%d.%m.%Y"),
        "edition": letter.edition.year_label,
        "event": event.name if event else "",
        "event_dates": _dates(event) if event and event.starts_on and event.ends_on else "",
        "city": event.city if event else "",
        "venue": event.venue if event else "",
        "country": letter.country_name,
    }
    with translation.override("en"):
        rendered = render_document(
            competition,
            DocumentKind.VISA_INVITATION,
            version=letter.template_version,
            fallback={
                "title": FALLBACK_TITLE,
                "statement": substitute(FALLBACK_STATEMENT, competition, **context),
                "signature_line": FALLBACK_SIGNATURE_LINE,
                "footer_note": substitute(FALLBACK_FOOTER_NOTE, competition, **context),
                "author": competition_name(competition),
            },
            **context,
        )
    body = ParagraphStyle("body", fontName=FONT_REGULAR, fontSize=10.5, leading=15, alignment=TA_JUSTIFY)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11, alignment=0)
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=10.5, alignment=0)
    head = ParagraphStyle("head", parent=cell, fontName=FONT_BOLD)
    right = ParagraphStyle("right", parent=small, alignment=TA_RIGHT)
    title = ParagraphStyle(
        "title", parent=body, fontName=FONT_BOLD, fontSize=16, leading=21, alignment=TA_CENTER
    )
    brand = ParagraphStyle("brand", parent=body, fontName=FONT_BOLD, fontSize=11, leading=14, alignment=0)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=PAGE_MARGIN_MM * mm,
        rightMargin=PAGE_MARGIN_MM * mm,
        topMargin=PAGE_MARGIN_MM * mm,
        bottomMargin=PAGE_MARGIN_MM * mm,
        title=f"{rendered.title} {letter.number}",
        author=rendered.author,
    )
    width = A4[0] - 2 * PAGE_MARGIN_MM * mm
    logo = competition_logo(competition)
    left = [_para(competition_name(competition), brand), _para(competition.organizer_name, small)]
    if logo:
        try:
            left.insert(0, Image(BytesIO(logo), width=22 * mm, height=22 * mm, kind="proportional"))
        except Exception:  # noqa: BLE001 - uszkodzony znak nie może zatrzymać listu do konsulatu
            logger.warning("Nie udało się wstawić logo do listu %s.", letter.number)
    header = Table(
        [[left, [_para(f"No. {letter.number}", right), _para(context["date"], right)]]],
        colWidths=[width * 0.6, width * 0.4],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    table_rows = [
        [
            _para(label, head)
            for label in (
                "Full name (as in passport)",
                "Nationality",
                "Date of birth",
                "Passport no.",
                "Valid until",
                "Role",
            )
        ]
    ]
    for person in people:
        table_rows.append(
            [
                _para(person.get("name", ""), cell),
                _para(person.get("nationality", ""), cell),
                _para(person.get("birth_date", ""), cell),
                _para(person.get("passport", ""), cell),
                _para(person.get("expiry", ""), cell),
                _para(person.get("role", ""), cell),
            ]
        )
    persons = Table(
        table_rows, colWidths=[w * width for w in (0.30, 0.11, 0.14, 0.17, 0.14, 0.14)], repeatRows=1
    )
    persons.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    template = resolve_template("", letter.edition, competition)
    signatures = template.signatures() if template is not None else []
    signature_cells = []
    for block in signatures or [{"image": None, "name": "", "title": rendered.signature_line}]:
        parts = []
        data = _image_bytes(block["image"]) if block.get("image") else None
        if data:
            try:
                parts.append(Image(BytesIO(data), width=40 * mm, height=16 * mm, kind="proportional"))
            except Exception:  # noqa: BLE001 - jak wyżej
                logger.warning("Nie udało się wstawić podpisu do listu %s.", letter.number)
        else:
            parts.append(Spacer(1, 16 * mm))
        parts.append(_para("…………………………………………", small))
        for line in (block.get("name", ""), block.get("title", "")):
            if line:
                parts.append(_para(line, small))
        signature_cells.append(parts)
    signature_table = Table(
        [signature_cells], colWidths=[width / max(1, len(signature_cells))] * len(signature_cells)
    )

    story = [
        header,
        Spacer(1, 14 * mm),
        _para(rendered.title, title),
        Spacer(1, 8 * mm),
        _para(rendered.statement, body),
        Spacer(1, 6 * mm),
        persons,
        Spacer(1, 12 * mm),
        signature_table,
    ]
    if rendered.footer_note:
        story += [Spacer(1, 10 * mm), _para(rendered.footer_note, small)]
    doc.build(story)
    return sign_document(buffer.getvalue()).data


def pdf_filename(letter: InvitationLetter) -> str:
    return "invitation-" + re.sub(r"[^A-Za-z0-9-]", "-", letter.number) + ".pdf"
