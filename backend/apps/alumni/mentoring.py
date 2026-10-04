"""Mentoring: prośba → akceptacja → rozmowa w Wiadomościach → zakończenie (ALUM-01 § 5).

**Rozmowa jest rozmową czatu**, a nie drugim komunikatorem. Mentoring zakłada przy akceptacji
zwykłą rozmowę P2P (``apps.chat.services.ensure_peer_conversation``) i rejestruje w czacie
**politykę** tej rozmowy (:func:`chat_policy`, punkt rozszerzenia ``register_peer_policy``). Treść,
moderacja, zgłoszenia, blokady i listy „masz wiadomość” zostają w czacie – tu nie ma ani jednej
linijki o wiadomościach.

**Kanał** (tabela w ``docs/tasks/ALUM-01.md`` § 5.2), liczony przy każdej wiadomości, bo wiek
mentee zmienia się w trakcie relacji:

- strony w różnych grupach wiekowych (mentor zawsze dorosły – § 1) i czat konkursu w polityce
  ``SAME_GROUP`` → ``SUPERVISED``: każdą wiadomość czyta organizator **przed** doręczeniem (tryb
  ``PRE`` wymuszony), a reguła grupy wiekowej czatu tej jednej rozmowy nie zamyka – zakaz dotyczy
  rozmowy dorosłego z dzieckiem **bez świadków**, a tu świadek jest przed doręczeniem,
- czat konkursu z wyłączonymi rozmowami uczestników (``OFF``) → ``SUPERVISED`` dla każdej pary:
  organizator zamknął rozmowy bez moderacji, a mentoring włączył świadomie osobnym przełącznikiem,
- różne grupy wiekowe przy ``ANY`` → zasady czatu z podłogą ``POST``: organizator dopuścił rozmowy
  międzygrupowe, ale mentora **poleca** dziecku platforma, więc organizator ma widzieć treść,
- ta sama grupa → zasady czatu bez zmian (z wymuszeniem ``PRE`` w trakcie etapu – reguła czatu).
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.models import audit

from . import notifications
from .models import (
    MAX_NOTE_LENGTH,
    MAX_OPEN_REQUESTS_PER_MENTEE,
    MAX_REASON_LENGTH,
    OPEN_STATUSES,
    AlumniProfile,
    Channel,
    EndReason,
    Interest,
    Mentorship,
    MentorshipFlag,
    MentorshipStatus,
    enabled,
)
from .services import bad_request, ensure_coordinator, ensure_enabled, ensure_member, not_found, settings_for

AUDIT_ENDED_BY_COORDINATOR = "alumni.mentorship_ended"
AUDIT_FLAG_RESOLVED = "alumni.flag_resolved"

#: Notki nad wątkiem i odmowy w czacie (``PeerPolicy``). Leniwe – rozstrzygają się w języku widza.
SUPERVISED_NOTICE = gettext_lazy(
    "Rozmowa mentorska. Każdą wiadomość czyta i akceptuje organizator, zanim trafi do drugiej osoby."
)
PEER_NOTICE = gettext_lazy("Rozmowa mentorska – obowiązują zasady Wiadomości tego konkursu.")
REVIEWED_NOTICE = gettext_lazy(
    "Rozmowa mentorska. Organizator przegląda wiadomości tej rozmowy (moderacja następcza)."
)
ENDED_REFUSAL = gettext_lazy("Mentoring zakończył się – ta rozmowa jest teraz tylko do odczytu.")
PAUSED_REFUSAL = gettext_lazy("Organizator wstrzymał mentoring – ta rozmowa jest teraz tylko do odczytu.")


# --- kanał i polityka rozmowy ----------------------------------------------------------------------------


def mixed_ages(mentorship: Mentorship, today=None) -> bool:
    from apps.chat.services import is_adult

    return is_adult(mentorship.mentor, today) != is_adult(mentorship.mentee, today)


def channel_for(mentorship: Mentorship, *, row=None) -> Channel:
    """Kanał tej relacji **teraz** – opis w docstringu modułu."""
    from apps.chat import services as chat
    from apps.chat.models import AgePolicy, PeerMode

    row = row or chat.settings_for(mentorship.competition)
    if PeerMode(row.peer_mode) == PeerMode.OFF:
        return Channel.SUPERVISED
    if mixed_ages(mentorship) and row.age_policy == AgePolicy.SAME_GROUP:
        return Channel.SUPERVISED
    return Channel.PEER


def chat_policy(conversation):
    """Dostawca ``PeerPolicy`` dla czatu: reguła rozmowy mentorskiej albo ``None`` (zwykła rozmowa)."""
    from apps.chat.models import PeerMode
    from apps.chat.services import PeerPolicy

    rows = list(
        Mentorship.objects.filter(conversation_id=conversation.pk)
        .exclude(status=MentorshipStatus.REQUESTED)
        .select_related("competition", "mentor", "mentee")
        .order_by("-created_at", "-id")
    )
    if not rows:
        return None
    active = next((row for row in rows if row.status == MentorshipStatus.ACCEPTED), None)
    if active is None:
        # Rozmowa założona przez mentoring zamyka się razem z nim; rozmowa sprzed mentoringu wraca
        # do zwykłych reguł czatu – nie mentoring ją założył, więc nie on ją zamyka.
        if any(not row.conversation_preexisting for row in rows):
            return PeerPolicy(refusal=str(ENDED_REFUSAL))
        return None
    if not enabled(active.competition) or not settings_for(active.competition).mentoring_enabled:
        return PeerPolicy(refusal=str(PAUSED_REFUSAL))
    if channel_for(active) == Channel.SUPERVISED:
        return PeerPolicy(mode=PeerMode.PRE, skip_age_policy=True, notice=str(SUPERVISED_NOTICE))
    if mixed_ages(active):
        return PeerPolicy(at_least=PeerMode.POST, notice=str(REVIEWED_NOTICE))
    return PeerPolicy(notice=str(PEER_NOTICE))


# --- kto może prosić ------------------------------------------------------------------------------------


def is_current_participant(participant) -> bool:
    """Mentee = uczestnik bieżącej edycji (wpis w niej) albo świeżo zapisany (bez żadnego wpisu)."""
    from apps.competitions.models import StageEntry

    entries = StageEntry.objects.filter(participant=participant)
    if not entries.exists():
        return True
    return entries.filter(stage__edition__is_current=True).exists()


def ensure_mentoring(competition) -> None:
    from apps.chat.services import is_enabled as chat_enabled

    ensure_enabled(competition)
    if not settings_for(competition).mentoring_enabled:
        raise not_found(_("Mentoring jest w tym konkursie wyłączony."))
    if not chat_enabled(competition):
        raise bad_request(
            _("Mentoring wymaga modułu Wiadomości, który organizator wyłączył."), "ALUMNI_CHAT_DISABLED"
        )


def mentor_profile(competition, token: str) -> AlumniProfile:
    from .services import _alive

    profile = (
        _alive(AlumniProfile.objects.for_competition(competition).filter(token=token, listed=True))
        .select_related("participant__user")
        .first()
    )
    if profile is None or not profile.mentor_available:
        raise not_found(_("Ta osoba nie przyjmuje teraz próśb o mentoring."))
    return profile


def active_count(mentor) -> int:
    return Mentorship.objects.filter(mentor=mentor, status=MentorshipStatus.ACCEPTED).count()


def _clean_text(value: str, limit: int, *, required: bool = False, label: str = "") -> str:
    text = (value or "").strip()
    if required and not text:
        raise bad_request(_("%(label)s nie może być puste.") % {"label": label}, "ALUMNI_TEXT_REQUIRED")
    if len(text) > limit:
        raise bad_request(
            _("%(label)s jest za długie (limit %(limit)s znaków).") % {"label": label, "limit": limit},
            "ALUMNI_TEXT_TOO_LONG",
        )
    return text


@transaction.atomic
def request_mentor(
    *, user, competition, token: str, topic: str = "", note: str = "", request=None
) -> Mentorship:
    ensure_mentoring(competition)
    mentee = ensure_member(user, competition)
    profile = mentor_profile(competition, token)
    mentor = profile.participant
    if mentor.pk == mentee.pk:
        raise bad_request(_("Nie możesz poprosić o mentoring samego siebie."), "ALUMNI_SELF")
    if not is_current_participant(mentee):
        raise bad_request(_("O mentora proszą uczestnicy bieżącej edycji."), "ALUMNI_NOT_CURRENT")
    if topic and topic not in Interest.values:
        raise bad_request(_("Wybierz temat z listy."), "ALUMNI_TOPIC")
    text = _clean_text(note, MAX_NOTE_LENGTH, label=_("Notatka"))
    open_rows = Mentorship.objects.filter(mentee=mentee, status__in=OPEN_STATUSES)
    if open_rows.filter(mentor=mentor).exists():
        raise bad_request(_("Masz już otwartą prośbę albo relację z tą osobą."), "ALUMNI_DUPLICATE")
    if open_rows.count() >= MAX_OPEN_REQUESTS_PER_MENTEE:
        raise bad_request(
            _("Możesz mieć naraz najwyżej %(limit)s otwarte prośby i relacje mentorskie.")
            % {"limit": MAX_OPEN_REQUESTS_PER_MENTEE},
            "ALUMNI_TOO_MANY",
        )
    if active_count(mentor) >= profile.mentor_capacity:
        raise bad_request(_("Ta osoba nie ma teraz wolnego miejsca na mentoring."), "ALUMNI_FULL")
    try:
        with transaction.atomic():
            row = Mentorship.objects.create(
                competition=competition, mentor=mentor, mentee=mentee, topic=topic, note=text
            )
    except IntegrityError as exc:
        raise bad_request(_("Masz już otwartą prośbę albo relację z tą osobą."), "ALUMNI_DUPLICATE") from exc
    notifications.mentorship_requested(row)
    return row


def _own(competition, pk, participant, *, as_mentor: bool | None = None) -> Mentorship:
    rows = Mentorship.objects.for_competition(competition).filter(pk=pk)
    if as_mentor is True:
        rows = rows.filter(mentor=participant)
    elif as_mentor is False:
        rows = rows.filter(mentee=participant)
    else:
        rows = rows.filter(Q(mentor=participant) | Q(mentee=participant))
    row = rows.select_related("mentor__user", "mentee__user", "competition").first()
    if row is None:
        raise not_found(_("Nie ma takiej relacji mentorskiej."))
    return row


@transaction.atomic
def accept(*, user, competition, pk: int, request=None) -> Mentorship:
    """Akceptacja: pojemność, rozmowa w Wiadomościach, list do mentee."""
    from apps.chat.services import ensure_peer_conversation

    ensure_mentoring(competition)
    mentor = ensure_member(user, competition)
    row = _own(competition, pk, mentor, as_mentor=True)
    row = Mentorship.objects.select_for_update().get(pk=row.pk)
    if row.status != MentorshipStatus.REQUESTED:
        raise bad_request(_("Na tę prośbę już odpowiedziano."), "ALUMNI_NOT_PENDING")
    profile = AlumniProfile.objects.filter(participant=mentor).first()
    if profile is None:
        raise not_found(_("Najpierw dołącz do sieci absolwentów."))
    if active_count(mentor) >= profile.mentor_capacity:
        raise bad_request(
            _("Masz już komplet mentee. Zwiększ liczbę miejsc w profilu albo zakończ inną relację."),
            "ALUMNI_FULL",
        )
    conversation, existed = ensure_peer_conversation(row.mentor, row.mentee)
    started_here_before = Mentorship.objects.filter(
        conversation=conversation, conversation_preexisting=False
    ).exists()
    row.status = MentorshipStatus.ACCEPTED
    row.responded_at = timezone.now()
    row.conversation = conversation
    row.conversation_preexisting = existed and not started_here_before
    row.save(update_fields=["status", "responded_at", "conversation", "conversation_preexisting"])
    notifications.mentorship_decided(row)
    return row


@transaction.atomic
def decline(*, user, competition, pk: int, request=None) -> Mentorship:
    ensure_enabled(competition)
    mentor = ensure_member(user, competition)
    row = _own(competition, pk, mentor, as_mentor=True)
    if row.status != MentorshipStatus.REQUESTED:
        raise bad_request(_("Na tę prośbę już odpowiedziano."), "ALUMNI_NOT_PENDING")
    row.status = MentorshipStatus.DECLINED
    row.responded_at = timezone.now()
    row.save(update_fields=["status", "responded_at"])
    notifications.mentorship_decided(row)
    return row


@transaction.atomic
def cancel(*, user, competition, pk: int, request=None) -> Mentorship:
    ensure_enabled(competition)
    mentee = ensure_member(user, competition)
    row = _own(competition, pk, mentee, as_mentor=False)
    if row.status != MentorshipStatus.REQUESTED:
        raise bad_request(_("Tę prośbę można już tylko zakończyć."), "ALUMNI_NOT_PENDING")
    row.status = MentorshipStatus.CANCELLED
    row.ended_at = timezone.now()
    row.save(update_fields=["status", "ended_at"])
    return row


def _end(row: Mentorship, *, reason: str, actor, note: str = "") -> Mentorship:
    row.status = MentorshipStatus.ENDED
    row.ended_at = timezone.now()
    row.ended_by = actor if getattr(actor, "is_authenticated", False) else None
    row.end_reason = reason
    if note:
        row.coordinator_note = note
    row.save(update_fields=["status", "ended_at", "ended_by", "end_reason", "coordinator_note"])
    return row


@transaction.atomic
def end(*, user, competition, pk: int, request=None) -> Mentorship:
    """Zakończenie przez jedną ze stron – prośba czekająca kończy się tak samo (bez rozmowy)."""
    ensure_enabled(competition)
    participant = ensure_member(user, competition)
    row = _own(competition, pk, participant)
    if row.status not in OPEN_STATUSES:
        raise bad_request(_("Ta relacja jest już zakończona."), "ALUMNI_NOT_OPEN")
    reason = EndReason.MENTOR if row.mentor_id == participant.pk else EndReason.MENTEE
    _end(row, reason=reason, actor=user)
    notifications.mentorship_ended(row, ended_by=participant)
    return row


def end_all_for(participant, *, reason: str, actor, as_mentor_only: bool = False) -> int:
    """Kończy otwarte relacje tej osoby (wycofanie zgody mentora, usunięcie konta)."""
    rows = Mentorship.objects.filter(status__in=OPEN_STATUSES)
    rows = (
        rows.filter(mentor=participant)
        if as_mentor_only
        else rows.filter(Q(mentor=participant) | Q(mentee=participant))
    )
    count = 0
    for row in rows.select_related("mentor__user", "mentee__user", "competition"):
        _end(row, reason=reason, actor=actor)
        if reason != EndReason.ACCOUNT_REMOVED:
            notifications.mentorship_ended(row, ended_by=participant)
        count += 1
    return count


@transaction.atomic
def flag(*, user, competition, pk: int, reason: str, request=None) -> MentorshipFlag:
    """Zgłoszenie problemu z relacją – list do koordynatorów od razu (bezpieczeństwo, nie kolejka)."""
    ensure_enabled(competition)
    participant = ensure_member(user, competition)
    row = _own(competition, pk, participant)
    text = _clean_text(reason, MAX_REASON_LENGTH, required=True, label=_("Powód"))
    item = MentorshipFlag.objects.create(mentorship=row, reporter=user, reason=text)
    notifications.flag_raised(item)
    return item


def my_mentorships(participant):
    """Relacje tej osoby (obie role), najnowsze pierwsze – do panelu ``/me/alumni/``."""
    return (
        Mentorship.objects.for_competition(participant.competition)
        .filter(Q(mentor=participant) | Q(mentee=participant))
        .select_related("mentor__user", "mentee__user", "conversation")
        .order_by("-created_at", "-id")
    )


# --- koordynator ----------------------------------------------------------------------------------------


def oversight(competition, *, status: str = ""):
    """Relacje konkursu do nadzoru: z liczbą otwartych zgłoszeń relacji i zgłoszeń wiadomości."""
    rows = Mentorship.objects.for_competition(competition)
    if status in MentorshipStatus.values:
        rows = rows.filter(status=status)
    return (
        rows.select_related("mentor__user", "mentee__user", "competition")
        .annotate(
            open_flags=Count("flags", filter=Q(flags__resolved_at__isnull=True), distinct=True),
            open_reports=Count(
                "conversation__messages__reports",
                filter=Q(conversation__messages__reports__resolved_at__isnull=True),
                distinct=True,
            ),
        )
        .order_by("-created_at", "-id")
    )


def open_flags(competition):
    return (
        MentorshipFlag.objects.for_competition(competition)
        .filter(resolved_at__isnull=True)
        .select_related("mentorship__mentor__user", "mentorship__mentee__user", "reporter")
        .order_by("created_at", "id")
    )


@transaction.atomic
def coordinator_end(*, competition, actor, pk: int, note: str, request=None) -> Mentorship:
    ensure_enabled(competition)
    ensure_coordinator(actor, competition)
    row = (
        Mentorship.objects.for_competition(competition)
        .filter(pk=pk)
        .select_related("mentor__user", "mentee__user", "competition")
        .first()
    )
    if row is None:
        raise not_found(_("Nie ma takiej relacji mentorskiej."))
    if row.status not in OPEN_STATUSES:
        raise bad_request(_("Ta relacja jest już zakończona."), "ALUMNI_NOT_OPEN")
    text = _clean_text(note, MAX_REASON_LENGTH, required=True, label=_("Notatka"))
    _end(row, reason=EndReason.COORDINATOR, actor=actor, note=text)
    audit(actor, AUDIT_ENDED_BY_COORDINATOR, row, {"mentorship_id": row.pk}, request=request)
    notifications.mentorship_ended(row, ended_by=None)
    return row


@transaction.atomic
def resolve_flag(*, competition, actor, pk: int, request=None) -> MentorshipFlag:
    ensure_enabled(competition)
    ensure_coordinator(actor, competition)
    item = MentorshipFlag.objects.for_competition(competition).filter(pk=pk).first()
    if item is None:
        raise not_found(_("Nie ma takiego zgłoszenia."))
    if item.resolved_at is None:
        item.resolved_at = timezone.now()
        item.resolved_by = actor
        item.save(update_fields=["resolved_at", "resolved_by"])
        audit(actor, AUDIT_FLAG_RESOLVED, item, {"mentorship_id": item.mentorship_id}, request=request)
    return item
