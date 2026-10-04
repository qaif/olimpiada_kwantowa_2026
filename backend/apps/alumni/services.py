"""Reguły sieci absolwentów: kto może dołączyć, co widać, kto co może zmienić.

Widoki (``apps.alumni.views``, ``apps.alumni.views_coordinator``) nie powtarzają żadnej
z tych reguł – każda funkcja pisząca sprawdza rolę **sama** (``DomainError`` 403/404), ta sama zasada,
co w ``apps.chat.services``. Flaga wyłączona to „tu nic nie stoi” (404), a nie odmowa.

Mentoring stoi w ``apps.alumni.mentoring``, zaproszenia w ``apps.alumni.invitations``, statystyki
w ``apps.alumni.stats``; tutaj – profil, zgoda i RODO.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit

from .achievements import Achievement, achievements_for, best_finished_rank
from .models import (
    ALUMNI_CONSENT_VERSION,
    LEVEL_RANK,
    MAX_MENTOR_CAPACITY,
    MIN_MENTOR_CAPACITY,
    AlumniConsentEvent,
    AlumniProfile,
    AlumniSettings,
    ConsentEventKind,
    Interest,
    Level,
    MentorshipStatus,
    enabled,
)

AUDIT_JOINED = "alumni.joined"
AUDIT_WITHDRAWN = "alumni.withdrawn"
AUDIT_SETTINGS = "alumni.settings_changed"
AUDIT_HIDDEN = "alumni.profile_hidden"
AUDIT_UNHIDDEN = "alumni.profile_unhidden"
AUDIT_CONTENT_APPROVED = "alumni.profile_content_approved"
AUDIT_RENEWED = "alumni.consent_renewed"

#: Kody odmowy dołączenia – tłumaczy je :data:`INELIGIBLE_REASONS` (ekran) i test.
NOT_PARTICIPANT = "not_participant"
MINOR = "minor"
NO_ACHIEVEMENT = "no_achievement"

INELIGIBLE_REASONS = {
    NOT_PARTICIPANT: gettext_lazy("Do sieci absolwentów dołączają uczestnicy tego konkursu."),
    MINOR: gettext_lazy(
        "Do sieci absolwentów dołączają osoby pełnoletnie. Wróć tu po swoich 18. urodzinach – "
        "Twoje osiągnięcia poczekają."
    ),
    NO_ACHIEVEMENT: gettext_lazy(
        "Do sieci absolwentów dołączają osoby z osiągnięciem w zakończonej edycji na poziomie "
        "co najmniej: %(level)s."
    ),
}

#: Treść zgody – wersja :data:`~apps.alumni.models.ALUMNI_CONSENT_VERSION`. Leniwa, bo moduł ładuje
#: się przed aktywacją języka; zmiana brzmienia wymaga podbicia wersji.
CONSENT_TEXT = gettext_lazy(
    "Chcę należeć do sieci absolwentów tego konkursu. Zgadzam się, żeby organizator przetwarzał "
    "moje imię i inicjał nazwiska (pełne imię i nazwisko – tylko jeśli tak wybiorę i mam zgodę na "
    "publikację nazwiska), moje osiągnięcia z ogłoszonych wyników oraz dane, które sam wpiszę do "
    "profilu, w celu: pokazania profilu zalogowanym uczestnikom konkursu (a na publicznej ścianie – "
    "tylko jeśli to zaznaczę), kontaktu mentorskiego przez Wiadomości, wysyłania mi zaproszeń na "
    "warsztaty, webinary i do jury (mogę się wypisać w każdym liście) oraz zagregowanych statystyk "
    "„gdzie są teraz”, w których nikt nie jest rozpoznawalny. Zgodę mogę wycofać w każdej chwili – "
    "profil zniknie od razu."
)


def _forbidden(text=None) -> DomainError:
    return DomainError(
        text or _("Ta część serwisu jest dostępna dla uczestników tego konkursu."),
        "ALUMNI_FORBIDDEN",
        http.HTTP_403_FORBIDDEN,
    )


def not_found(text: str = "Nie ma takiego profilu.") -> DomainError:
    return DomainError(text, "ALUMNI_NOT_FOUND", http.HTTP_404_NOT_FOUND)


def bad_request(text, code: str) -> DomainError:
    return DomainError(text, code, http.HTTP_400_BAD_REQUEST)


# --- bramki -------------------------------------------------------------------------------------------


def ensure_enabled(competition) -> None:
    if not enabled(competition):
        raise DomainError(
            "Sieć absolwentów jest w tym konkursie wyłączona.", "ALUMNI_DISABLED", http.HTTP_404_NOT_FOUND
        )


def member(user, competition):
    """Profil uczestnika z rolą w tym konkursie albo ``None`` – ta sama reguła, co w Wiadomościach."""
    from apps.chat.services import chat_participant

    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return chat_participant(user, competition)


def ensure_member(user, competition):
    participant = member(user, competition)
    if participant is None:
        raise _forbidden()
    return participant


def is_coordinator(user, competition) -> bool:
    from apps.chat.services import is_organizer

    return is_organizer(user, competition)


def ensure_coordinator(user, competition) -> None:
    if not is_coordinator(user, competition):
        raise _forbidden(_("Ta część serwisu jest dostępna dla koordynatora."))


# --- ustawienia -----------------------------------------------------------------------------------------


def settings_for(competition) -> AlumniSettings:
    """Ustawienia konkursu; brak wiersza = domyślne (obiekt niezapisany – odczyt nie zakłada wiersza)."""
    row = AlumniSettings.objects.for_competition(competition).first() if competition is not None else None
    return row if row is not None else AlumniSettings(competition=competition)


@transaction.atomic
def save_settings(
    *, competition, actor, eligibility: str, mentoring_enabled: bool, public_wall: bool, request=None
):
    ensure_enabled(competition)
    ensure_coordinator(actor, competition)
    level = Level(eligibility)
    row, _created = AlumniSettings.objects.get_or_create(competition=competition)
    before = {
        "eligibility": row.eligibility,
        "mentoring_enabled": row.mentoring_enabled,
        "public_wall": row.public_wall,
    }
    row.eligibility = level
    row.mentoring_enabled = bool(mentoring_enabled)
    row.public_wall = bool(public_wall)
    row.updated_at = timezone.now()
    row.save()
    after = {
        "eligibility": row.eligibility,
        "mentoring_enabled": row.mentoring_enabled,
        "public_wall": row.public_wall,
    }
    if before != after:
        audit(actor, AUDIT_SETTINGS, row, {"before": before, "after": after}, request=request)
    return row


# --- kwalifikowalność -----------------------------------------------------------------------------------


@dataclass
class Eligibility:
    eligible: bool
    reason: str = ""
    achievements: list[Achievement] = field(default_factory=list)
    required: Level = Level.FINALIST

    @property
    def message(self) -> str:
        if self.eligible or not self.reason:
            return ""
        text = INELIGIBLE_REASONS[self.reason]
        return (
            str(text) % {"level": Level(self.required).label} if self.reason == NO_ACHIEVEMENT else str(text)
        )


def eligibility(participant, *, row: AlumniSettings | None = None) -> Eligibility:
    """Czy ten uczestnik może **teraz** dołączyć (``docs/tasks/ALUM-01.md`` § 1)."""
    from apps.chat.services import is_adult

    row = row or settings_for(participant.competition)
    required = Level(row.eligibility)
    found = achievements_for([participant]).get(participant.pk, [])
    if not is_adult(participant):
        return Eligibility(False, MINOR, found, required)
    if best_finished_rank(found) < LEVEL_RANK[required]:
        return Eligibility(False, NO_ACHIEVEMENT, found, required)
    return Eligibility(True, "", found, required)


def profile_of(participant) -> AlumniProfile | None:
    if participant is None:
        return None
    return AlumniProfile.objects.filter(participant=participant).first()


# --- zgoda ----------------------------------------------------------------------------------------------


@transaction.atomic
def join(*, user, competition, consent: bool, request=None) -> AlumniProfile:
    """Dołączenie do sieci: wyraźna zgoda, kwalifikowalność, profil i dowód zgody."""
    ensure_enabled(competition)
    participant = ensure_member(user, competition)
    existing = profile_of(participant)
    if existing is not None:
        return existing
    if not consent:
        raise bad_request(_("Zaznacz zgodę, żeby dołączyć do sieci absolwentów."), "ALUMNI_CONSENT_REQUIRED")
    status = eligibility(participant)
    if not status.eligible:
        raise _forbidden(status.message)
    now = timezone.now()
    try:
        with transaction.atomic():
            profile = AlumniProfile.objects.create(
                participant=participant,
                consent_version=ALUMNI_CONSENT_VERSION,
                joined_at=now,
                updated_at=now,
                # Dołączenie wymaga pełnoletności (eligibility) – potwierdzenie zostaje na profilu (M2).
                adult_confirmed_at=now,
            )
    except IntegrityError:
        return profile_of(participant)
    _consent_event(participant, ConsentEventKind.GRANTED, now)
    audit(user, AUDIT_JOINED, profile, {"version": ALUMNI_CONSENT_VERSION}, request=request)
    return profile


def consent_proof() -> tuple[str, str]:
    """``(język, skrót SHA-256)`` treści zgody pokazanej **teraz** – w aktywnym języku (L7)."""
    import hashlib

    from django.utils.translation import get_language

    text = str(CONSENT_TEXT)
    return (get_language() or ""), hashlib.sha256(text.encode()).hexdigest()


def _consent_event(participant, kind, now=None) -> AlumniConsentEvent:
    language, text_hash = consent_proof() if kind == ConsentEventKind.GRANTED else ("", "")
    return AlumniConsentEvent.objects.create(
        participant=participant,
        kind=kind,
        version=ALUMNI_CONSENT_VERSION,
        language=language,
        text_hash=text_hash,
        created_at=now or timezone.now(),
    )


def needs_reconsent(profile) -> bool:
    """Treść zgody zmieniła się od dołączenia – profil śpi, dopóki osoba nie potwierdzi nowej (L7)."""
    return profile is not None and profile.consent_version != ALUMNI_CONSENT_VERSION


@transaction.atomic
def renew_consent(*, user, competition, consent: bool, request=None) -> AlumniProfile:
    """Potwierdzenie **nowej** wersji zgody. Do tego czasu profil nie jest nikomu pokazywany."""
    ensure_enabled(competition)
    participant = ensure_member(user, competition)
    profile = profile_of(participant)
    if profile is None:
        raise not_found(_("Najpierw dołącz do sieci absolwentów."))
    if not consent:
        raise bad_request(_("Zaznacz zgodę, żeby dołączyć do sieci absolwentów."), "ALUMNI_CONSENT_REQUIRED")
    if needs_reconsent(profile):
        profile.consent_version = ALUMNI_CONSENT_VERSION
        profile.save(update_fields=["consent_version"])
        _consent_event(participant, ConsentEventKind.GRANTED)
        audit(user, AUDIT_RENEWED, profile, {"version": ALUMNI_CONSENT_VERSION}, request=request)
    return profile


@transaction.atomic
def withdraw(*, user, competition, request=None) -> None:
    """Wycofanie zgody: profil znika **od razu**, relacje mentorskie mentora się kończą.

    Celowo **bez** bramki flagi (M4): wycofanie zgody musi działać także wtedy, gdy organizator
    wyłączył sieć – inaczej osoba nie miałaby jak zabrać danych, które wciąż leżą w bazie.
    """
    from . import mentoring
    from .models import EndReason

    participant = ensure_member(user, competition)
    profile = profile_of(participant)
    if profile is None:
        return
    mentoring.end_all_for(participant, reason=EndReason.WITHDRAWN, actor=user, as_mentor_only=True)
    pk = profile.pk
    profile.delete()
    _consent_event(participant, ConsentEventKind.WITHDRAWN)
    audit(user, AUDIT_WITHDRAWN, participant, {"profile_id": pk}, request=request)


# --- profil ---------------------------------------------------------------------------------------------

#: Pola, które absolwent edytuje formularzem – i nic poza nimi (``update_profile``).
EDITABLE_FIELDS = (
    "show_full_name",
    "university",
    "field_of_study",
    "city",
    "country",
    "bio",
    "interests",
    "linkedin_url",
    "github_url",
    "mentor_available",
    "mentor_topics",
    "mentor_capacity",
    "listed",
    "public",
    "invitations",
)


@transaction.atomic
def update_profile(*, user, competition, data: dict) -> AlumniProfile:
    """Zapis profilu z danych **już zwalidowanych** formularzem – serwis pilnuje roli i zakresów."""
    from .validators import clean_github, clean_linkedin

    ensure_enabled(competition)
    participant = ensure_member(user, competition)
    profile = profile_of(participant)
    if profile is None:
        raise not_found(_("Najpierw dołącz do sieci absolwentów."))
    allowed = set(Interest.values)
    for name in EDITABLE_FIELDS:
        if name not in data:
            continue
        value = data[name]
        if name in ("interests", "mentor_topics"):
            value = [item for item in dict.fromkeys(value or []) if item in allowed]
        elif name == "linkedin_url":
            value = clean_linkedin(value)
        elif name == "github_url":
            value = clean_github(value)
        elif name == "mentor_capacity":
            value = min(max(int(value or MIN_MENTOR_CAPACITY), MIN_MENTOR_CAPACITY), MAX_MENTOR_CAPACITY)
        elif isinstance(value, str):
            value = value.strip()
        setattr(profile, name, value)
    profile.updated_at = timezone.now()
    profile.full_clean(exclude=["participant", "hidden_by"])
    profile.save()
    return profile


def may_show_full_name(participant) -> bool:
    """Reguła publikacji nazwiska – ta sama, co w tabelach wyników (``_may_show_full_name``):
    zgoda uczestnika i, dla małoletniego, zgoda opiekuna."""
    from apps.chat.services import is_adult

    if not participant.publish_full_name:
        return False
    return bool(participant.guardian_consent or is_adult(participant))


def display_name(profile: AlumniProfile) -> str:
    """Podpis absolwenta: „Imię N.”, a pełne imię i nazwisko – tylko z wyborem **i** zgodą."""
    from apps.forum.models import display_author

    participant = profile.participant
    user = participant.user
    if profile.show_full_name and may_show_full_name(participant):
        full = f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip()
        if full:
            return full
    return display_author(user)


@dataclass
class Card:
    """Profil przygotowany do wyświetlenia – szablon nie sięga do ``participant`` ani do konta."""

    profile: AlumniProfile
    name: str
    achievements: list[Achievement]
    interests: list[str]
    mentor_topics: list[str]
    country_name: str
    free_slots: int = 0
    #: Opis i odnośniki schowane przed widzem małoletnim do akceptacji koordynatora (H1).
    content_hidden: bool = False


def _labels(values) -> list[str]:
    labels = dict(Interest.choices)
    return [str(labels[value]) for value in values or [] if value in labels]


def cards(profiles, *, viewer=None, finished_only: bool = True) -> list[Card]:
    """Karty do wyświetlenia.

    ``viewer`` – profil uczestnika oglądającego: małoletni nie widzi opisu i odnośników mentora,
    dopóki koordynator ich nie zaakceptował (H1). ``finished_only`` – katalog i ściana pokazują
    osiągnięcia wyłącznie z **zakończonych** edycji (M3): wynik etapu trwającej edycji nie jest
    jeszcze tytułem absolwenta.
    """
    from apps.accounts.countries import country_name
    from apps.chat.services import is_adult

    from . import safety

    rows = list(profiles)
    found = achievements_for([row.participant for row in rows])
    minor_viewer = viewer is not None and not is_adult(viewer)
    result = []
    for row in rows:
        active = getattr(row, "active_mentees", 0) or 0
        items = found.get(row.participant_id, [])
        if finished_only:
            items = [item for item in items if item.finished]
        result.append(
            Card(
                profile=row,
                name=display_name(row),
                achievements=items,
                content_hidden=minor_viewer and safety.needs_review(row),
                interests=_labels(row.interests),
                mentor_topics=_labels(row.mentor_topics),
                country_name=country_name(row.country) if row.country else "",
                free_slots=max(row.mentor_capacity - active, 0) if row.mentor_available else 0,
            )
        )
    return result


def _alive(rows):
    """Profile, które w ogóle wolno komuś pokazać: nieukryte, z bieżącą wersją zgody (L7), konto
    aktywne i nie po anonimizacji."""
    from apps.accounts.anonymised import anonymised_q

    return rows.filter(
        hidden_at__isnull=True, participant__user__is_active=True, consent_version=ALUMNI_CONSENT_VERSION
    ).exclude(anonymised_q("participant__user"))


def _with_role(rows, competition):
    from apps.accounts.messaging import _role_filter
    from apps.accounts.models import CompetitionRole, User

    with_role = User.objects.filter(_role_filter(competition, CompetitionRole.PARTICIPANT)).values("pk")
    return rows.filter(participant__user_id__in=with_role)


def annotate_load(rows):
    return rows.annotate(
        active_mentees=Count(
            "participant__mentorships_as_mentor",
            filter=Q(participant__mentorships_as_mentor__status=MentorshipStatus.ACCEPTED),
            distinct=True,
        )
    )


def directory(viewer, *, query: str = "", interest: str = "", mentors_only: bool = False):
    """Katalog dla zalogowanych uczestników tego konkursu (bez siebie). Queryset – stronicuje widok."""
    competition = viewer.competition
    rows = AlumniProfile.objects.for_competition(competition).filter(listed=True).exclude(participant=viewer)
    rows = annotate_load(_with_role(_alive(rows), competition)).select_related("participant__user")
    text = (query or "").strip()
    if text:
        rows = rows.filter(
            Q(participant__user__first_name__icontains=text)
            | Q(university__icontains=text)
            | Q(field_of_study__icontains=text)
        )
    if interest in Interest.values:
        rows = rows.filter(Q(interests__contains=[interest]) | Q(mentor_topics__contains=[interest]))
    if mentors_only:
        from django.db.models import F

        rows = rows.filter(mentor_available=True, active_mentees__lt=F("mentor_capacity"))
    return rows.order_by("participant__user__first_name", "pk")


def wall(competition):
    """Publiczna ściana: tylko profile ``public`` i tylko przy włączonej ścianie konkursu."""
    if not enabled(competition) or not settings_for(competition).public_wall:
        return AlumniProfile.objects.none()
    rows = AlumniProfile.objects.for_competition(competition).filter(public=True)
    return (
        _with_role(_alive(rows), competition).select_related("participant__user").order_by("-joined_at", "pk")
    )


def profile_by_token(competition, token: str) -> AlumniProfile:
    profile = (
        _alive(AlumniProfile.objects.for_competition(competition).filter(token=token, listed=True))
        .select_related("participant__user")
        .first()
    )
    if profile is None:
        raise not_found()
    return profile


# --- koordynator ----------------------------------------------------------------------------------------


def coordinator_list(competition, *, query: str = ""):
    rows = annotate_load(AlumniProfile.objects.for_competition(competition)).select_related(
        "participant__user", "hidden_by"
    )
    text = (query or "").strip()
    if text:
        rows = rows.filter(
            Q(participant__user__first_name__icontains=text)
            | Q(participant__user__last_name__icontains=text)
            | Q(participant__public_code__icontains=text)
            | Q(university__icontains=text)
        )
    return rows.order_by("-joined_at", "pk")


@transaction.atomic
def set_hidden(*, competition, actor, pk: int, hidden: bool, request=None) -> AlumniProfile:
    ensure_enabled(competition)
    ensure_coordinator(actor, competition)
    profile = AlumniProfile.objects.for_competition(competition).filter(pk=pk).first()
    if profile is None:
        raise not_found()
    if hidden and profile.hidden_at is None:
        from . import mentoring
        from .models import EndReason

        profile.hidden_at = timezone.now()
        profile.hidden_by = actor
        profile.save(update_fields=["hidden_at", "hidden_by"])
        # M1: ukryty mentor nie prowadzi dalej relacji – trwające się kończą (rozmowa tylko do
        # odczytu), czekające prośby są odrzucane. Ukrycie bywa reakcją na niestosowny opis albo
        # zgłoszenie, a koordynator nie ma pamiętać, żeby osobno zamknąć każdą relację.
        declined = mentoring.decline_pending_for(profile.participant, reason=EndReason.HIDDEN)
        ended = mentoring.end_all_for(
            profile.participant, reason=EndReason.HIDDEN, actor=actor, as_mentor_only=True
        )
        audit(
            actor,
            AUDIT_HIDDEN,
            profile,
            {"profile_id": profile.pk, "relations_closed": ended, "requests_declined": declined},
            request=request,
        )
    elif not hidden and profile.hidden_at is not None:
        profile.hidden_at = None
        profile.hidden_by = None
        profile.save(update_fields=["hidden_at", "hidden_by"])
        audit(actor, AUDIT_UNHIDDEN, profile, {"profile_id": profile.pk}, request=request)
    return profile


def content_review_queue(competition) -> list:
    """Profile mentorów, których opis albo odnośniki czekają na akceptację dla małoletnich (H1)."""
    from . import safety

    rows = list(
        _alive(
            AlumniProfile.objects.for_competition(competition).filter(mentor_available=True)
        ).select_related("participant__user")
    )
    queue = [row for row in rows if safety.needs_review(row)]
    for row in queue:
        row.contact_hits = safety.contact_hits(*safety.reviewed_text(row))
    return queue


@transaction.atomic
def approve_content(*, competition, actor, pk: int, request=None) -> AlumniProfile:
    """Akceptacja opisu i odnośników mentora dla małoletnich – **tej** treści (skrót, H1)."""
    from . import safety

    ensure_enabled(competition)
    ensure_coordinator(actor, competition)
    profile = AlumniProfile.objects.for_competition(competition).filter(pk=pk).first()
    if profile is None:
        raise not_found()
    profile.reviewed_hash = safety.review_hash(profile)
    profile.reviewed_at = timezone.now()
    profile.reviewed_by = actor
    profile.save(update_fields=["reviewed_hash", "reviewed_at", "reviewed_by"])
    audit(actor, AUDIT_CONTENT_APPROVED, profile, {"profile_id": profile.pk}, request=request)
    return profile


def eligible_not_joined_count(competition) -> int:
    """Ilu uczestników z roli **mogłoby** dołączyć, a nie dołączyło – liczba na ekran koordynatora.

    Liczona dla uczestników z jakimkolwiek wpisem w ogłoszonym etapie (reszta i tak nie ma osiągnięć),
    więc koszt rośnie z liczbą startujących, a nie z liczbą kont.
    """
    from apps.accounts.models import Participant
    from apps.chat.services import is_adult

    row = settings_for(competition)
    candidates = list(
        Participant.objects.for_competition(competition)
        .filter(stage_entries__stage__results_published_at__isnull=False, alumni_profile__isnull=True)
        .exclude_anonymised()
        .distinct()
    )
    found = achievements_for(candidates)
    required = LEVEL_RANK[Level(row.eligibility)]
    return sum(
        1
        for participant in candidates
        if is_adult(participant) and best_finished_rank(found.get(participant.pk, [])) >= required
    )


# --- RODO -----------------------------------------------------------------------------------------------


def retention_hold(participant) -> bool:
    """Czy aktywna zgoda absolwenta wstrzymuje **pełną** anonimizację tego profilu (§ 2, M5).

    Tylko przy włączonej fladze (wyłączenie sieci kończy cel przetwarzania) i tylko dla profilu
    „żywego”: nieukrytego, z aktywnym kontem. Ukryty profil nie służy sieci, więc nie jest powodem,
    żeby trzymać konto.
    """
    from apps.accounts.anonymised import anonymised_q

    if not enabled(participant.competition):
        return False
    return (
        AlumniProfile.objects.filter(
            participant=participant, hidden_at__isnull=True, participant__user__is_active=True
        )
        .exclude(anonymised_q("participant__user"))
        .exists()
    )


#: Pola profilu uczestnika czyszczone przy wstrzymanej retencji (M5) – dane zbierane wyłącznie do
#: zawodów, których sieć absolwentów nie potrzebuje. Imię, nazwisko i adres (konto), kod publiczny,
#: wpisy i dyplomy (z nich liczą się osiągnięcia) zostają.
MINIMISED_TEXT_FIELDS = ("phone", "school", "institution_name", "supervisor_email", "guardian_email")


def minimise_participant(participant) -> bool:
    """Minimalizacja konta trzymanego zgodą absolwenta. Zwraca ``True``, gdy coś się zmieniło.

    Data urodzenia zostaje sprowadzona do rocznika (``birth_year`` liczy się sam z daty, więc
    zostaje ten sam) – a pełnoletność, której potrzebują reguły mentoringu, i tak jest zapisana na
    profilu absolwenta (``adult_confirmed_at``). Idempotentne: drugi przebieg nic nie zmienia.
    """
    from apps.accounts.models import Participant

    changes: dict = {}
    for name in MINIMISED_TEXT_FIELDS:
        if getattr(participant, name):
            changes[name] = ""
    for name in ("school_ref", "custom_institution_ref", "region", "grade", "birth_date"):
        attname = participant._meta.get_field(name).attname
        if getattr(participant, attname) is not None:
            changes[name] = None
    if participant.district:
        changes["district"] = ""
    if not changes:
        return False
    Participant.objects.filter(pk=participant.pk).update(**changes)
    for name, value in changes.items():
        setattr(participant, name, value)
    return True


def erase_for_user(user) -> int:
    """Anonimizacja konta: profile absolwenta znikają, relacje się kończą, notatki są czyszczone."""
    from apps.accounts.models import Participant

    from . import mentoring
    from .models import EndReason, Mentorship, MentorshipFlag

    removed = 0
    for participant in Participant.objects.filter(user=user):
        mentoring.end_all_for(participant, reason=EndReason.ACCOUNT_REMOVED, actor=None)
        removed += AlumniProfile.objects.filter(participant=participant).delete()[0]
    Mentorship.objects.filter(Q(mentee__user=user) | Q(mentor__user=user)).update(
        note="", mentee_birth_date=None, mentee_birth_year=None
    )
    MentorshipFlag.objects.filter(reporter=user).update(reason="", reporter=None)
    return removed


def export_for(user, participant) -> dict:
    """Sekcja ``absolwenci`` eksportu danych konta (``apps.accounts.data_export``)."""
    from apps.forum.models import display_author

    from .models import Mentorship, MentorshipFlag

    if participant is None:
        return {"profil": None, "zgody": [], "mentoring": [], "zgloszenia": []}
    profile = profile_of(participant)
    profile_section = None
    if profile is not None:
        profile_section = {
            "dolaczyl": profile.joined_at.isoformat(),
            "wersja_zgody": profile.consent_version,
            **{name: getattr(profile, name) for name in EDITABLE_FIELDS},
            "ukryty_przez_organizatora": profile.hidden_at is not None,
            "osiagniecia": [
                {"edycja": item.edition_label, "poziom": item.title}
                for item in achievements_for([participant]).get(participant.pk, [])
            ],
        }
    events = [
        {
            "zdarzenie": event.kind,
            "wersja": event.version,
            "jezyk": event.language,
            "skrot_tresci": event.text_hash,
            "chwila": event.created_at.isoformat(),
        }
        for event in AlumniConsentEvent.objects.filter(participant=participant).order_by("created_at")
    ]
    pairs = []
    rows = Mentorship.objects.filter(Q(mentor=participant) | Q(mentee=participant)).select_related(
        "mentor__user", "mentee__user"
    )
    for row in rows.order_by("created_at"):
        as_mentor = row.mentor_id == participant.pk
        other = row.mentee if as_mentor else row.mentor
        pairs.append(
            {
                "rola": "mentor" if as_mentor else "mentee",
                "druga_strona": display_author(other.user),
                "stan": row.status,
                "temat": row.topic,
                "notatka_prosby": row.note if not as_mentor else "",
                "prosba": row.created_at.isoformat(),
                "zakonczona": row.ended_at.isoformat() if row.ended_at else None,
                "powod_zakonczenia": row.end_reason,
            }
        )
    flags = [
        {"powod": flag.reason, "zgloszone": flag.created_at.isoformat()}
        for flag in MentorshipFlag.objects.filter(
            reporter=user, mentorship__competition=participant.competition
        )
    ]
    return {"profil": profile_section, "zgody": events, "mentoring": pairs, "zgloszenia": flags}
