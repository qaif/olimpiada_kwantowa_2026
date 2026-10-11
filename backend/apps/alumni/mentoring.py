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

Po przeglądzie krytyka (ALUM-01, 04.10.2026):

- wiek mentee jest liczony ostrożnie – z daty zapisanej przy akceptacji **i** z bieżącej
  (``apps.alumni.safety.mentee_is_minor``), a pełnoletność mentora – z potwierdzenia przy dołączeniu
  **albo** z bieżącej daty (M2): poprawka daty urodzenia nie zdejmuje nadzoru,
- pierwsze :data:`~apps.alumni.models.FIRST_MESSAGES_PRE` wiadomości nowej pary dorosły–małoletni
  czekają na organizatora także przy zasadzie „bez ograniczeń” (L9),
- polityka nie pyta bazy w konkursie, który sieci absolwentów nigdy nie włączał (L4): rozmowa
  i tak niesie już załadowany konkurs, a klucz flagi w ``feature_flags`` mówi, czy kiedykolwiek
  ją włączono. Konkurs, który flagę **wyłączył**, płaci jedno zapytanie – jego rozmowy mentorskie
  muszą zostać tylko do odczytu.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.models import audit

from . import notifications, safety
from .models import (
    ALUMNI_FLAG,
    FIRST_MESSAGES_PRE,
    MAX_NOTE_LENGTH,
    MAX_OPEN_REQUESTS_PER_MENTEE,
    MAX_REASON_LENGTH,
    MAX_REQUESTS_PER_DAY,
    MAX_REQUESTS_PER_PAIR_PER_WEEK,
    OPEN_STATUSES,
    AlumniProfile,
    Channel,
    EndReason,
    Interest,
    Mentorship,
    MentorshipFlag,
    MentorshipStatus,
    NoteStatus,
    enabled,
)
from .services import bad_request, ensure_coordinator, ensure_enabled, ensure_member, not_found, settings_for

AUDIT_ENDED_BY_COORDINATOR = "alumni.mentorship_ended"
AUDIT_FLAG_RESOLVED = "alumni.flag_resolved"
AUDIT_NOTE_DECIDED = "alumni.note_decided"
AUDIT_BIRTH_DATE_CHANGED = "alumni.birth_date_changed"

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
FIRST_MESSAGES_NOTICE = gettext_lazy(
    "Nowa rozmowa mentorska. Pierwsze wiadomości czyta i akceptuje organizator, "
    "zanim trafią do drugiej osoby."
)
#: Powody zgłoszeń automatycznych – po polsku w bazie, jak zgłoszenia ludzi (czyta je koordynator).
AUTO_CONTACT = "Automatycznie: notatka prośby może zawierać dane kontaktowe ({hits})."
AUTO_ENCRYPTED = (
    "Automatycznie: para ma rozmowę szyfrowaną, a mentoring wymaga moderacji – akceptacja wstrzymana."
)
AUTO_BIRTH_DATE = "Automatycznie: zmieniono datę urodzenia osoby w otwartej relacji mentorskiej."


# --- kanał i polityka rozmowy ----------------------------------------------------------------------------


def mixed_ages(mentorship: Mentorship, today=None) -> bool:
    """Dorosły mentor i małoletni mentee – każda z odpowiedzi liczona ostrożnie (M2)."""
    return safety.mentor_is_adult(mentorship, today=today) and safety.mentee_is_minor(mentorship, today)


def channel_for(mentorship: Mentorship, *, row=None, mixed: bool | None = None) -> Channel:
    """Kanał tej relacji **teraz** – opis w docstringu modułu.

    ``row`` (ustawienia czatu) i ``mixed`` podaje wołający, który liczy kanał dla listy relacji
    jednego konkursu – inaczej każdy wiersz listy kosztowałby zapytanie o te same ustawienia (L4).
    """
    from apps.chat import services as chat
    from apps.chat.models import AgePolicy, PeerMode

    row = row or chat.settings_for(mentorship.competition)
    if PeerMode(row.peer_mode) == PeerMode.OFF:
        return Channel.SUPERVISED
    mixed = mixed_ages(mentorship) if mixed is None else mixed
    if mixed and row.age_policy == AgePolicy.SAME_GROUP:
        return Channel.SUPERVISED
    return Channel.PEER


def _ever_enabled(competition) -> bool:
    """Czy konkurs kiedykolwiek zapisał flagę ``alumni`` – bez zapytania (pole wiersza konkursu)."""
    return competition is not None and ALUMNI_FLAG in (competition.feature_flags or {})


def chat_policy(conversation):
    """Dostawca ``PeerPolicy`` dla czatu: reguła rozmowy mentorskiej albo ``None`` (zwykła rozmowa)."""
    from apps.chat.models import MessageStatus, PeerMode
    from apps.chat.services import PeerPolicy

    competition = conversation.competition
    if not _ever_enabled(competition):
        return None
    rows = list(
        Mentorship.objects.filter(conversation_id=conversation.pk)
        .exclude(status=MentorshipStatus.REQUESTED)
        .select_related("mentor", "mentee")
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
    if not enabled(competition) or not settings_for(competition).mentoring_enabled:
        return PeerPolicy(refusal=str(PAUSED_REFUSAL))
    mixed = mixed_ages(active)
    if channel_for(active, mixed=mixed) == Channel.SUPERVISED:
        return PeerPolicy(mode=PeerMode.PRE, skip_age_policy=True, notice=str(SUPERVISED_NOTICE))
    if mixed:
        # Pierwszy kontakt dorosłego z dzieckiem czyta organizator **przed** doręczeniem także przy
        # „bez ograniczeń” (L9); dalej – przegląd po doręczeniu.
        sent = (
            conversation.messages.filter(created_at__gte=active.responded_at or active.created_at)
            .exclude(status=MessageStatus.REJECTED)
            .count()
        )
        if sent < FIRST_MESSAGES_PRE:
            return PeerPolicy(mode=PeerMode.PRE, skip_age_policy=True, notice=str(FIRST_MESSAGES_NOTICE))
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
    _check_request_limits(mentee, mentor)
    from apps.chat.services import is_adult

    # Notatka małoletniego czeka na organizatora, zanim przeczyta ją dorosły mentor (H1).
    note_status = NoteStatus.PENDING if text and not is_adult(mentee) else NoteStatus.NONE
    try:
        with transaction.atomic():
            row = Mentorship.objects.create(
                competition=competition,
                mentor=mentor,
                mentee=mentee,
                topic=topic,
                note=text,
                note_status=note_status,
            )
    except IntegrityError as exc:
        raise bad_request(_("Masz już otwartą prośbę albo relację z tą osobą."), "ALUMNI_DUPLICATE") from exc
    hits = safety.contact_hits(text)
    if hits:
        _auto_flag(row, AUTO_CONTACT.format(hits=", ".join(hits)))
    notifications.mentorship_requested(row)
    return row


def _check_request_limits(mentee, mentor, now=None) -> None:
    """Pętla „poproś → wycofaj → poproś” nie może zasypywać mentora listami (L11).

    Liczymy **prośby**, a nie otwarte relacje: wycofana prośba też wysłała list.
    """
    from datetime import timedelta

    now = now or timezone.now()
    mine = Mentorship.objects.filter(mentee=mentee)
    if mine.filter(created_at__gte=now - timedelta(days=1)).count() >= MAX_REQUESTS_PER_DAY:
        raise bad_request(
            _("Wysłano już dziś dużo próśb o mentoring – spróbuj jutro."), "ALUMNI_REQUEST_LIMIT"
        )
    pair = mine.filter(mentor=mentor, created_at__gte=now - timedelta(days=7)).count()
    if pair >= MAX_REQUESTS_PER_PAIR_PER_WEEK:
        raise bad_request(
            _("Do tej osoby wysłano już w tym tygodniu prośbę – spróbuj za kilka dni."),
            "ALUMNI_REQUEST_LIMIT",
        )


def _auto_flag(row: Mentorship, reason: str) -> MentorshipFlag:
    item = MentorshipFlag.objects.create(mentorship=row, reporter=None, reason=reason[:500], automatic=True)
    notifications.flag_raised(item)
    return item


def note_visible_to_mentor(row: Mentorship) -> bool:
    """Notatkę prośby mentor czyta tylko wtedy, gdy nie czeka na organizatora i nie została odrzucona."""
    return bool(row.note) and row.note_status in (NoteStatus.NONE, NoteStatus.APPROVED)


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


def accept(*, user, competition, pk: int, request=None) -> Mentorship:
    """Akceptacja: pojemność, rozmowa w Wiadomościach, list do mentee.

    Sprawdzenie rozmowy szyfrowanej (L2) stoi **przed** transakcją akceptacji: odmowa ma zostawić
    zgłoszenie dla organizatora, a wyjątek w środku transakcji wycofałby je razem z resztą.
    Istniejąca rozmowa szyfrowana nie da się moderować, a para dorosły–małoletni (albo czat
    z wyłączonymi rozmowami) moderacji wymaga – więc nie akceptujemy i nie wysyłamy „rozmowa czeka”.
    """
    from apps.chat.services import find_peer_conversation

    ensure_mentoring(competition)
    mentor = ensure_member(user, competition)
    row = _own(competition, pk, mentor, as_mentor=True)
    existing = find_peer_conversation(row.mentor, row.mentee)
    if (
        row.status == MentorshipStatus.REQUESTED
        and existing is not None
        and existing.is_encrypted
        and (mixed_ages(row) or channel_for(row) == Channel.SUPERVISED)
    ):
        if not row.flags.filter(automatic=True, reason=AUTO_ENCRYPTED).exists():
            _auto_flag(row, AUTO_ENCRYPTED)
        raise bad_request(
            _(
                "Macie już rozmowę szyfrowaną, a ta relacja wymaga moderacji organizatora. "
                "Organizator dostał zgłoszenie i skontaktuje się z Wami."
            ),
            "ALUMNI_ENCRYPTED_CONVERSATION",
        )
    return _accept(user=user, competition=competition, pk=pk)


@transaction.atomic
def _accept(*, user, competition, pk: int) -> Mentorship:
    from apps.chat.services import chat_participant, ensure_peer_conversation

    ensure_mentoring(competition)
    mentor = ensure_member(user, competition)
    row = _own(competition, pk, mentor, as_mentor=True)
    # Blokada profilu mentora **przed** liczeniem miejsc (L5): dwie akceptacje naraz w dwóch kartach
    # przeglądarki inaczej obie zobaczyłyby wolne miejsce.
    profile = AlumniProfile.objects.select_for_update().filter(participant=mentor).first()
    row = Mentorship.objects.select_for_update().get(pk=row.pk)
    if row.status != MentorshipStatus.REQUESTED:
        raise bad_request(_("Na tę prośbę już odpowiedziano."), "ALUMNI_NOT_PENDING")
    if profile is None:
        raise not_found(_("Najpierw dołącz do sieci absolwentów."))
    # M1: ukryty profil i wyłączona gotowość do mentoringu nie przyjmują nowych relacji, a mentee
    # musi wciąż być uczestnikiem tego konkursu (rola odebrana, konto zablokowane – nie).
    if profile.hidden_at is not None or not profile.mentor_available:
        raise bad_request(
            _("Twój profil nie przyjmuje teraz nowych relacji mentorskich."), "ALUMNI_NOT_AVAILABLE"
        )
    mentee_user = row.mentee.user
    if not mentee_user.is_active or chat_participant(mentee_user, competition) is None:
        raise bad_request(_("Ta osoba nie jest już uczestnikiem konkursu."), "ALUMNI_MENTEE_GONE")
    if active_count(mentor) >= profile.mentor_capacity:
        raise bad_request(
            _("Masz już komplet mentee. Zwiększ liczbę miejsc w profilu albo zakończ inną relację."),
            "ALUMNI_FULL",
        )
    row.mentee_birth_date = row.mentee.birth_date
    row.mentee_birth_year = row.mentee.birth_year or None
    conversation, existed = ensure_peer_conversation(row.mentor, row.mentee)
    started_here_before = Mentorship.objects.filter(
        conversation=conversation, conversation_preexisting=False
    ).exists()
    row.status = MentorshipStatus.ACCEPTED
    row.responded_at = timezone.now()
    row.conversation = conversation
    row.conversation_preexisting = existed and not started_here_before
    row.save(
        update_fields=[
            "status",
            "responded_at",
            "conversation",
            "conversation_preexisting",
            "mentee_birth_date",
            "mentee_birth_year",
        ]
    )
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


def decline_pending_for(mentor, *, reason: str) -> int:
    """Odrzuca czekające prośby do tego mentora (ukrycie profilu, M1) – mentee dostaje list."""
    rows = list(
        Mentorship.objects.filter(mentor=mentor, status=MentorshipStatus.REQUESTED).select_related(
            "mentor__user", "mentee__user", "competition"
        )
    )
    now = timezone.now()
    for row in rows:
        row.status = MentorshipStatus.DECLINED
        row.responded_at = now
        row.end_reason = reason
        row.save(update_fields=["status", "responded_at", "end_reason"])
        notifications.mentorship_decided(row)
    return len(rows)


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


def pending_notes(competition):
    """Notatki próśb małoletnich czekające na organizatora (H1) – ze znacznikiem wzorców kontaktowych."""
    rows = list(
        Mentorship.objects.for_competition(competition)
        .filter(note_status=NoteStatus.PENDING, status__in=OPEN_STATUSES)
        .select_related("mentor__user", "mentee__user")
        .order_by("created_at", "id")
    )
    for row in rows:
        row.contact_hits = safety.contact_hits(row.note)
    return rows


@transaction.atomic
def decide_note(*, competition, actor, pk: int, approve: bool, request=None) -> Mentorship:
    ensure_enabled(competition)
    ensure_coordinator(actor, competition)
    row = (
        Mentorship.objects.for_competition(competition).filter(pk=pk, note_status=NoteStatus.PENDING).first()
    )
    if row is None:
        raise not_found(_("Nie ma takiej notatki do akceptacji."))
    row.note_status = NoteStatus.APPROVED if approve else NoteStatus.REJECTED
    row.save(update_fields=["note_status"])
    audit(actor, AUDIT_NOTE_DECIDED, row, {"mentorship_id": row.pk, "approved": approve}, request=request)
    return row


def channels(rows, competition) -> dict[int, Channel]:
    """Kanał każdej trwającej relacji listy – ustawienia czatu raz, a nie raz na wiersz (L4)."""
    from apps.chat import services as chat

    row = chat.settings_for(competition)
    return {item.pk: channel_for(item, row=row) for item in rows if item.status == MentorshipStatus.ACCEPTED}


def birth_date_changed(participant) -> int:
    """Zmiana daty urodzenia osoby w otwartej relacji: audyt i zgłoszenie do koordynatorów (M2).

    Woła to sygnał zapisu profilu (``apps.alumni.signals``) tylko wtedy, gdy data **naprawdę** się
    zmieniła. Kanał rozmowy i tak się nie złagodzi (liczy się data z chwili akceptacji), ale ktoś,
    kto „odmładza” albo „postarza” się w trakcie relacji z dzieckiem, ma być widoczny dla człowieka.
    """
    rows = list(
        Mentorship.objects.filter(status__in=OPEN_STATUSES)
        .filter(Q(mentor=participant) | Q(mentee=participant))
        .select_related("competition")
    )
    for row in rows:
        audit(
            None,
            AUDIT_BIRTH_DATE_CHANGED,
            row,
            {"mentorship_id": row.pk, "participant_id": participant.pk},
        )
        _auto_flag(row, AUTO_BIRTH_DATE)
    return len(rows)


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
