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

VISA-01 dokłada tu trzy rzeczy: **język listu** (teksty w ``letter_texts``), **kod weryfikacyjny**
z kodem QR i adresem strony ``/visa/verify/<kod>/`` w stopce oraz **unieważnienie** – list unieważniony
nie daje się pobrać, a strona weryfikacji mówi „unieważniony”. Wnioski opiekunów i decyzje oficera są
w ``letter_requests``.
"""

from __future__ import annotations

import json
import logging
import re
from html import escape
from io import BytesIO

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import Http404
from django.urls import reverse
from django.utils import formats, timezone, translation
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy.branding import competition_name
from apps.tenancy.documents import DocumentKind, current_version, render_document, substitute

from .letter_texts import DEFAULT_LANGUAGE, LETTER_TEXTS, texts_for
from .models import (
    DelegationMember,
    FieldGroup,
    FinalEvent,
    InvitationLetter,
    LetterScope,
    new_verification_code,
)
from .services import REQUIRED_FIELDS, event_for, members_of

logger = logging.getLogger(__name__)

#: Napisy odwrotu (tekst listu bez szablonu z bazy) są od VISA-01 w ``letter_texts`` – po jednym
#: bloku na język; angielski jest dawnym tekstem odwrotu LOG-01.

#: Ile razy próbować nadać kod weryfikacyjny, gdy los trafi w istniejący (59 bitów – w praktyce nigdy).
CODE_ATTEMPTS = 3

PAGE_MARGIN_MM = 20
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


def require_language(language: str) -> str:
    """Kod języka listu albo odmowa – wybór spoza ``LETTER_TEXTS`` to spreparowany formularz."""
    if language not in LETTER_TEXTS:
        raise DomainError(
            "Nieobsługiwany język listu.", "LETTER_LANGUAGE_INVALID", status.HTTP_400_BAD_REQUEST
        )
    return language


def issue_letter(
    competition,
    delegation,
    *,
    member: DelegationMember | None = None,
    actor,
    request=None,
    language: str = DEFAULT_LANGUAGE,
):
    """Wystawia list: imienny (``member``) albo dla całej delegacji (osoby z kompletnym dokumentem).

    Osoby bez kompletnego dokumentu podróży nie trafiają na list delegacji – list z pustą rubryką
    paszportu konsulat odrzuci, a nowy list z nowym numerem i tak trzeba będzie wystawić. Ekran mówi,
    kogo pominięto. List imienny dla osoby bez danych jest odmową.

    VISA-01: list dostaje losowy kod weryfikacyjny, język i migawkę wydarzenia (nazwa, miasto, daty),
    a rola osoby w migawce jest zapisana **w języku listu** – nie w języku ekranu oficera.
    """
    require_language(language)
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
    with translation.override(language):
        snapshot = json.dumps([_person(p) for p in people], ensure_ascii=False)
    for attempt in range(CODE_ATTEMPTS):
        try:
            letter = _create_letter(
                competition, delegation, edition, member, scope, people, snapshot, language, actor
            )
            break
        except IntegrityError:
            # Kolizja kodu weryfikacyjnego (numer i tak jest pod blokadą finału) – nowy los.
            if attempt == CODE_ATTEMPTS - 1:
                raise
    audit(
        actor,
        "logistics.letter_issued",
        letter,
        {
            "number": letter.number,
            "delegation": delegation.pk,
            "people": len(people),
            "scope": scope,
            "language": language,
        },
        request=request,
    )
    return letter


def _create_letter(competition, delegation, edition, member, scope, people, snapshot, language, actor):
    """Wiersz rejestru pod blokadą finału – numer kolejny w roku, kod losowy, migawka wydarzenia."""
    with transaction.atomic():
        event = _require_event(FinalEvent.objects.select_for_update().filter(edition=edition).first())
        now = timezone.now()
        year = timezone.localdate(now).year
        top = (
            InvitationLetter.objects.filter(competition=competition, year=year).aggregate(
                top=Max("sequence")
            )["top"]
            or 0
        )
        sequence = top + 1
        return InvitationLetter.objects.create(
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
            content=snapshot,
            template_version=current_version(competition, DocumentKind.VISA_INVITATION),
            issued_at=now,
            issued_by=actor if getattr(actor, "is_authenticated", False) else None,
            verification_code=new_verification_code(),
            language=language,
            event_name=event.name,
            event_city=event.city,
            event_starts_on=event.starts_on,
            event_ends_on=event.ends_on,
        )


def revoke_letter(letter: InvitationLetter, *, reason: str, actor, request=None) -> InvitationLetter:
    """Unieważnia list (VISA-01 § 3). Odwracalne nie jest – poprawiony list to nowy numer i nowy kod.

    Powód jest obowiązkowy: opiekun i drugi oficer mają się z ekranu dowiedzieć, dlaczego list, który
    ktoś trzyma w ręku, przestał być ważny. Do audytu idzie numer, nie powód (wolny tekst bywa opisem
    osoby – „odmowa wizy”), a strona weryfikacji powodu nie pokazuje w ogóle.
    """
    reason = (reason or "").strip()
    if not reason:
        raise DomainError("Podaj powód unieważnienia.", "REVOKE_REASON_REQUIRED", status.HTTP_400_BAD_REQUEST)
    with transaction.atomic():
        locked = InvitationLetter.objects.select_for_update().get(pk=letter.pk)
        if locked.revoked_at is not None:
            raise DomainError("Ten list jest już unieważniony.", "LETTER_REVOKED", status.HTTP_409_CONFLICT)
        locked.revoked_at = timezone.now()
        locked.revoked_by = actor if getattr(actor, "is_authenticated", False) else None
        locked.revoke_reason = reason[:300]
        locked.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
    audit(actor, "logistics.letter_revoked", locked, {"number": locked.number}, request=request)
    return locked


def verification_url(letter: InvitationLetter) -> str:
    """Bezwzględny adres strony weryfikacji listu – na domenie konkursu, który list wystawił."""
    from apps.accounts.activation import absolute_url

    return absolute_url(
        reverse("web:visa-verify-code", args=[letter.verification_code or ""]),
        competition=letter.competition,
    )


def verification_entry_url(competition) -> str:
    """Adres formularza „wpisz kod” – drukowany na liście obok kodu, krótszy niż adres z kodem."""
    from apps.accounts.activation import absolute_url

    return absolute_url(reverse("web:visa-verify"), competition=competition)


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


def drop_person(letter: InvitationLetter, member_id: int) -> None:
    """Wymazuje jedną osobę z migawki (usunięcie konta) – reszta listu delegacji zostaje."""
    before = people_of(letter)
    people = [person for person in before if person.get("member_id") != member_id]
    if len(people) == len(before):
        return
    letter.content = json.dumps(people, ensure_ascii=False) if people else ""
    if not people:
        letter.content_purged_at = timezone.now()
    letter.save(update_fields=["content", "content_purged_at"])


def _day(value) -> str:
    """Dzień w języku aktywnym (``date_format``, a nie ``strftime``): nazwa miesiąca po polsku, rosyjsku…

    ``strftime("%B")`` bierze nazwę miesiąca z locale systemu kontenera (C – zawsze angielska), więc list
    po francusku miałby „October” w środku francuskiego zdania. Format ``E`` daje dopełniacz tam, gdzie
    język go ma („4 października”), a angielski wychodzi jak dotąd („4 October 2026”).
    """
    return formats.date_format(value, "j E Y")


def _date_range(start, end) -> str:
    if start is None or end is None:
        return ""
    if start == end:
        return _day(start)
    return f"{_day(start)} – {_day(end)}"


def _event_snapshot(letter: InvitationLetter, event: FinalEvent | None) -> dict:
    """Wydarzenie z migawki listu, a dla listów sprzed VISA-01 (bez migawki) – z ustawień finału."""
    if letter.event_name or letter.event_starts_on:
        return {
            "name": letter.event_name,
            "city": letter.event_city,
            "starts_on": letter.event_starts_on,
            "ends_on": letter.event_ends_on,
        }
    return {
        "name": event.name if event else "",
        "city": event.city if event else "",
        "starts_on": event.starts_on if event else None,
        "ends_on": event.ends_on if event else None,
    }


def _qr_drawing(url: str, size: float):
    """Kod QR z adresem weryfikacji – ``reportlab.graphics``, ten sam zabieg, co na dyplomie."""
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing

    widget = qr.QrCodeWidget(url)
    bounds = widget.getBounds()
    drawing = Drawing(
        size,
        size,
        transform=[size / (bounds[2] - bounds[0]), 0, 0, size / (bounds[3] - bounds[1]), 0, 0],
    )
    drawing.add(widget)
    return drawing


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

    if letter.revoked_at is not None:
        # Unieważniony list nie wychodzi ponownie z serwisu – kopia pobrana „na pamiątkę” trafiłaby
        # do konsulatu tak samo, jak ważna. Rejestr i strona weryfikacji mówią, że był i że nie jest.
        raise DomainError(
            "Ten list został unieważniony – nie można go pobrać.", "LETTER_REVOKED", status.HTTP_410_GONE
        )
    people = people_of(letter)
    if not people:
        raise DomainError(
            "Dane osób z tego listu zostały usunięte (retencja) – list nie może być ponownie pobrany.",
            "LETTER_PURGED",
            status.HTTP_410_GONE,
        )
    competition = letter.competition
    event = event_for(letter.edition)
    texts = texts_for(letter.language)
    register_fonts()
    names = ", ".join(person["name"] for person in people)
    issued = timezone.localtime(letter.issued_at)
    snapshot = _event_snapshot(letter, event)
    with translation.override(letter.language or DEFAULT_LANGUAGE):
        event_dates = _date_range(snapshot["starts_on"], snapshot["ends_on"])
    context = {
        "recipient": names,
        "number": letter.number,
        "date": issued.strftime("%d.%m.%Y"),
        "edition": letter.edition.year_label,
        "event": snapshot["name"],
        "event_dates": event_dates,
        "city": snapshot["city"],
        "venue": event.venue if event else "",
        "country": letter.country_name,
        "code": letter.display_code,
    }
    with translation.override(letter.language or DEFAULT_LANGUAGE):
        rendered = render_document(
            competition,
            DocumentKind.VISA_INVITATION,
            version=letter.template_version,
            fallback={
                "title": texts["title"],
                "statement": substitute(texts["statement"], competition, **context),
                "signature_line": texts["signature_line"],
                "footer_note": substitute(texts["footer_note"], competition, **context),
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
        [[left, [_para(f"{texts['number_label']} {letter.number}", right), _para(context["date"], right)]]],
        colWidths=[width * 0.6, width * 0.4],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    table_rows = [[_para(label, head) for label in texts["columns"]]]
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
    if letter.verification_code:
        story += [Spacer(1, 6 * mm), _verification_block(letter, texts, small, head, width)]
    doc.build(story)
    return sign_document(buffer.getvalue()).data


def _verification_block(letter: InvitationLetter, texts: dict, small, head, width: float):
    """Ramka „Weryfikacja”: kod QR z pełnym adresem, krótki adres formularza i kod do przepisania.

    Dwie drogi, bo list bywa oglądany w dwóch postaciach: wydruk w okienku konsulatu (urzędnik
    skanuje QR albo przepisuje kod) i skan w systemie wizowym (wtedy liczy się kod w tekście). Kod
    w grupach po cztery znaki – tak się go dyktuje przez telefon.
    """
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    code = letter.display_code
    text = texts["verify_text"].format(url=verification_entry_url(letter.competition), code=code)
    qr_size = 26 * mm
    block = Table(
        [
            [
                _qr_drawing(verification_url(letter), qr_size),
                [_para(texts["verify_title"], head), _para(text, small), _para(code, head)],
            ]
        ],
        colWidths=[qr_size + 4 * mm, width - qr_size - 4 * mm],
    )
    block.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return block


def pdf_filename(letter: InvitationLetter) -> str:
    return "invitation-" + re.sub(r"[^A-Za-z0-9-]", "-", letter.number) + ".pdf"
