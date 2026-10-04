"""Identyfikatory finału (PDF z kodem QR) i odhaczanie obecności przez obsługę (LOG-01 § 4).

**Kod QR nie niesie danych osobowych.** Zawiera wyłącznie adres ekranu obsługi z losowym tokenem
(``DelegationMember.badge_token``) – zgubiony identyfikator znaleziony na ulicy nie mówi niczego
poza tym, co i tak jest na nim wydrukowane. Token otwiera cokolwiek **wyłącznie** zalogowanej
obsłudze z przydziałem (``access.can_check_in``); każdemu innemu adres odpowiada jak każdy ekran
panelu: logowanie albo 403. „Wydaj nowy identyfikator” zmienia token i stara karta przestaje działać.

Skład PDF: A4 pionowo, cztery identyfikatory A6 na stronie (do przecięcia na pół i na pół), kroje
DejaVu i rejestracja z ``apps.results.certificates`` – ta sama droga, co dyplomy, bez nowej
zależności. Napisy na karcie (rola) są w **języku domyślnym konkursu**, bo identyfikator czyta
obsługa i ochrona na miejscu, a nie jego właściciel.
"""

from __future__ import annotations

import logging
from io import BytesIO

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import Http404
from django.urls import reverse
from django.utils import translation
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit

from .models import CheckIn, Checkpoint, DelegationMember, new_badge_token
from .services import photo_bytes

logger = logging.getLogger(__name__)

#: Wymiary karty (A6) w milimetrach i układ na arkuszu A4.
BADGE_WIDTH_MM = 105
BADGE_HEIGHT_MM = 148.5
SEARCH_LIMIT = 20


# --- token i ekran obsługi --------------------------------------------------------------------------


def checkin_path(member: DelegationMember) -> str:
    return reverse("web:onsite-checkin-member", args=[member.badge_token])


def checkin_url(member: DelegationMember, request=None, competition=None) -> str:
    from apps.accounts.activation import absolute_url

    return absolute_url(checkin_path(member), request, competition or member.delegation.competition)


def member_by_token(competition, token: str) -> DelegationMember | None:
    """Członek delegacji **tego konkursu** o tym tokenie albo ``None`` (nieznany, nieważny, cudzy)."""
    token = (token or "").strip()
    if not token or len(token) > 64:
        return None
    return (
        DelegationMember.objects.for_competition(competition)
        .select_related("participant__user", "user", "guest", "delegation__country", "delegation__edition")
        .filter(badge_token=token)
        .first()
    )


@transaction.atomic
def reissue_badge(member: DelegationMember, *, actor, request=None) -> DelegationMember:
    """Nowy token – stary identyfikator (zgubiony, skradziony) przestaje działać przy skanowaniu."""
    member.badge_token = new_badge_token()
    member.save(update_fields=["badge_token"])
    audit(actor, "logistics.badge_reissued", member, {}, request=request)
    return member


# --- punkty kontroli i odhaczanie -----------------------------------------------------------------------


def checkpoints_of(edition):
    return Checkpoint.objects.filter(edition=edition).order_by("position", "id")


def checkpoint_for(competition, pk) -> Checkpoint | None:
    try:
        pk = int(pk)
    except TypeError, ValueError:
        return None
    return Checkpoint.objects.for_competition(competition).filter(pk=pk).first()


def create_checkpoint(competition, edition, *, name: str, actor, request=None) -> Checkpoint:
    name = (name or "").strip()[:120]
    if not name:
        raise DomainError("Podaj nazwę punktu kontroli.", "CHECKPOINT_NAME", status.HTTP_400_BAD_REQUEST)
    position = checkpoints_of(edition).count()
    try:
        with transaction.atomic():
            checkpoint = Checkpoint.objects.create(
                competition=competition, edition=edition, name=name, position=position
            )
    except IntegrityError as exc:
        raise DomainError(
            "Taki punkt kontroli już istnieje.", "CHECKPOINT_DUPLICATE", status.HTTP_400_BAD_REQUEST
        ) from exc
    audit(actor, "logistics.checkpoint_created", checkpoint, {"name": name}, request=request)
    return checkpoint


def delete_checkpoint(checkpoint: Checkpoint, *, actor, request=None) -> None:
    audit(
        actor,
        "logistics.checkpoint_deleted",
        checkpoint,
        {"check_ins": checkpoint.check_ins.count()},
        request=request,
    )
    checkpoint.delete()


def check_in(member: DelegationMember, checkpoint: Checkpoint, *, actor, request=None) -> CheckIn:
    """Odhacza osobę w punkcie. Powtórne zeskanowanie nie dubluje wpisu i nie przesuwa godziny.

    Godzina pierwszego odhaczenia jest tą, o którą pyta się przy sporze („kiedy dotarł”) – drugi
    skan tej samej karty przy wyjściu z autobusu nie może jej nadpisać.
    """
    if checkpoint.edition_id != member.delegation.edition_id:
        raise Http404("Punkt kontroli należy do innej edycji.")
    record, created = CheckIn.objects.get_or_create(
        checkpoint=checkpoint,
        member=member,
        defaults={"recorded_by": actor if getattr(actor, "is_authenticated", False) else None},
    )
    if created:
        audit(actor, "logistics.checked_in", member, {"checkpoint": checkpoint.pk}, request=request)
    return record


def undo_check_in(member: DelegationMember, checkpoint: Checkpoint, *, actor, request=None) -> None:
    deleted, _rows = CheckIn.objects.filter(checkpoint=checkpoint, member=member).delete()
    if deleted:
        audit(actor, "logistics.check_in_undone", member, {"checkpoint": checkpoint.pk}, request=request)


def status_of(member: DelegationMember) -> dict[int, CheckIn]:
    return {row.checkpoint_id: row for row in CheckIn.objects.filter(member=member)}


def search(members, query: str) -> list[DelegationMember]:
    """Wyszukiwarka obsługi po imieniu, nazwisku albo kraju – gdy QR się nie skanuje."""
    needle = (query or "").strip().lower()
    if len(needle) < 2:
        return []
    found = [
        member
        for member in members
        if needle in member.full_name.lower() or needle in member.delegation.country.name.lower()
    ]
    return found[:SEARCH_LIMIT]


def attendance(edition, members) -> list[dict]:
    """Ile osób odhaczono w każdym punkcie – liczba z ekranu obsługi i oficera."""
    total = len(members)
    counts = {
        row["checkpoint_id"]: row["n"]
        for row in CheckIn.objects.filter(checkpoint__edition=edition)
        .values("checkpoint_id")
        .annotate(n=Count("id"))
    }
    return [
        {"checkpoint": checkpoint, "checked": counts.get(checkpoint.pk, 0), "total": total}
        for checkpoint in checkpoints_of(edition)
    ]


# --- PDF identyfikatorów --------------------------------------------------------------------------------


def _fit(canvas, text: str, font: str, size: float, width: float, minimum: float = 9) -> float:
    """Największy rozmiar kroju ≤ ``size``, przy którym napis mieści się w szerokości karty."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    while size > minimum and stringWidth(text, font, size) > width:
        size -= 0.5
    return size


def _draw_qr(canvas, data: str, x: float, y: float, size: float) -> None:
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing

    widget = qr.QrCodeWidget(data)
    bounds = widget.getBounds()
    drawing = Drawing(
        size,
        size,
        transform=[size / (bounds[2] - bounds[0]), 0, 0, size / (bounds[3] - bounds[1]), 0, 0],
    )
    drawing.add(widget)
    renderPDF.draw(drawing, canvas, x, y)


def _draw_badge(canvas, member, *, x: float, y: float, brand: str, event_name: str, url: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader

    from apps.results.certificates import FONT_BOLD, FONT_REGULAR

    width, height = BADGE_WIDTH_MM * mm, BADGE_HEIGHT_MM * mm
    inner = width - 16 * mm
    canvas.setStrokeColor(colors.lightgrey)
    canvas.setLineWidth(0.5)
    canvas.rect(x, y, width, height)
    canvas.setFillColor(colors.HexColor("#1f3a5f"))
    canvas.rect(x, y + height - 22 * mm, width, 22 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    size = _fit(canvas, brand, FONT_BOLD, 14, inner)
    canvas.setFont(FONT_BOLD, size)
    canvas.drawCentredString(x + width / 2, y + height - 11 * mm, brand)
    if event_name:
        size = _fit(canvas, event_name, FONT_REGULAR, 9, inner)
        canvas.setFont(FONT_REGULAR, size)
        canvas.drawCentredString(x + width / 2, y + height - 17 * mm, event_name)
    canvas.setFillColor(colors.black)

    photo = photo_bytes(member)
    top = y + height - 26 * mm
    if photo:
        try:
            canvas.drawImage(
                ImageReader(BytesIO(photo)),
                x + (width - 30 * mm) / 2,
                top - 40 * mm,
                width=30 * mm,
                height=40 * mm,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        except Exception:  # noqa: BLE001 - uszkodzone zdjęcie nie może zabrać całej paczki kart
            logger.warning("Nie udało się narysować zdjęcia na identyfikatorze członka %s.", member.pk)
    name_y = top - 50 * mm
    for line, font, base in (
        (member.first_name, FONT_BOLD, 20),
        (member.last_name.upper(), FONT_BOLD, 20),
    ):
        size = _fit(canvas, line, font, base, inner)
        canvas.setFont(font, size)
        canvas.drawCentredString(x + width / 2, name_y, line)
        name_y -= size + 4
    country = member.delegation.country.name
    canvas.setFont(FONT_REGULAR, _fit(canvas, country, FONT_REGULAR, 13, inner))
    canvas.drawCentredString(x + width / 2, name_y - 4, country)
    role = member.role_label.upper()
    canvas.setFillColor(colors.HexColor("#1f3a5f"))
    canvas.rect(x, y, width, 14 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont(FONT_BOLD, _fit(canvas, role, FONT_BOLD, 14, width - 40 * mm))
    canvas.drawString(x + 8 * mm, y + 5 * mm, role)
    canvas.setFillColor(colors.white)
    canvas.rect(x + width - 30 * mm, y + 2 * mm, 26 * mm, 26 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.black)
    _draw_qr(canvas, url, x + width - 29 * mm, y + 3 * mm, 24 * mm)


def badges_pdf(members, *, competition, event=None, request=None) -> bytes:
    """Arkusze A4 z identyfikatorami podanych osób, cztery na stronę."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas

    from apps.results.certificates import register_fonts
    from apps.tenancy.branding import competition_name

    register_fonts()
    buffer = BytesIO()
    canvas = pdf_canvas.Canvas(buffer, pagesize=A4)
    brand = competition_name(competition)
    canvas.setTitle(f"{brand} – identyfikatory")
    canvas.setAuthor(brand)
    event_name = event.name if event is not None else ""
    width, height = BADGE_WIDTH_MM * mm, BADGE_HEIGHT_MM * mm
    positions = [(0, height), (width, height), (0, 0), (width, 0)]
    with translation.override(competition.default_language or "en"):
        for index, member in enumerate(members):
            slot = index % 4
            if index and slot == 0:
                canvas.showPage()
            x, y = positions[slot]
            _draw_badge(
                canvas,
                member,
                x=x,
                y=y,
                brand=brand,
                event_name=event_name,
                url=checkin_url(member, request, competition),
            )
    if not members:
        canvas.setFont("Helvetica", 12)
        canvas.drawString(20 * mm, A4[1] - 30 * mm, "-")
    canvas.save()
    return buffer.getvalue()
