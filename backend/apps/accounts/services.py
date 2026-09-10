"""Logika domenowa kont: rejestracja, kody zaproszeń, zatwierdzanie komitetu.

Widoki nie tworzą obiektów samodzielnie – cała logika i wszystkie błędy domenowe są tutaj.
"""

import secrets
from datetime import timedelta

from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit

from .models import (
    GROUP_APPEALS,
    GROUP_COORDINATOR,
    GROUP_PARTICIPANT,
    GROUP_REVIEWER,
    CommitteeMember,
    CommitteeStatus,
    InvitationCode,
    InvitationGrantsStatus,
    Participant,
    User,
    generate_public_code,
    hash_invitation_code,
    normalize_voivodeship,
)

INVITATION_CODE_BYTES = 24
PUBLIC_CODE_MAX_ATTEMPTS = 20


def _require_voivodeship(district: str | None, *, required: bool) -> str | None:
    """Sprowadza województwo do wartości z listy albo podnosi błąd domenowy.

    Walidacja jest tutaj, a nie tylko w formularzu i serializerze, bo do serwisów wchodzą też
    seed, komendy CLI i logowanie społecznościowe – gdyby każda z tych ścieżek pilnowała listy
    osobno, do bazy trafiłby prędzej czy później zapis spoza słownika i reguła konfliktu
    interesów (porównanie okręgów) przestałaby być rozstrzygalna.
    """
    normalized = normalize_voivodeship(district)
    if normalized is not None:
        return normalized
    if (district or "").strip():
        raise DomainError(
            "Nieznane województwo – wybierz jedno z listy.",
            "DISTRICT_INVALID",
            status.HTTP_400_BAD_REQUEST,
        )
    if required:
        raise DomainError("Województwo jest wymagane.", "DISTRICT_REQUIRED", status.HTTP_400_BAD_REQUEST)
    return None


def active_reviewer_profile(user) -> CommitteeMember | None:
    """Profil recenzenta użytkownika, o ile wolno mu recenzować: ACTIVE **i** grupa ``reviewer``.

    Jedna definicja dla całego systemu: używa jej i uprawnienie ``IsActiveReviewer`` (przez
    ``apps.accounts.permissions``), i widoczność plików (``Submission.objects.for_user``), i serwisy
    oceniania. Rozjazd między nimi oznaczałby, że ktoś widzi pracę, której nie ma prawa recenzować.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return None
    member = getattr(user, "committee_member", None)
    if member is None or member.status != CommitteeStatus.ACTIVE:
        return None
    if not user.groups.filter(name=GROUP_REVIEWER).exists():
        return None
    return member


def _add_to_group(user: User, name: str) -> None:
    group, _ = Group.objects.get_or_create(name=name)
    user.groups.add(group)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


@sensitive_variables()
def _validate_password_or_raise(password: str, user: User) -> None:
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        # Komunikaty walidatorów są bezpieczne (nie zawierają hasła).
        raise DomainError(" ".join(exc.messages), "WEAK_PASSWORD", status.HTTP_400_BAD_REQUEST) from exc


@sensitive_variables()
def _create_user(*, email: str, password: str, first_name: str, last_name: str) -> User:
    email = _normalize_email(email)
    if User.objects.filter(email=email).exists():
        raise DomainError(
            "Konto z tym adresem e-mail już istnieje.", "EMAIL_TAKEN", status.HTTP_400_BAD_REQUEST
        )
    unsaved = User(email=email, first_name=first_name, last_name=last_name)
    _validate_password_or_raise(password, unsaved)
    return User.objects.create_user(
        email=email, password=password, first_name=first_name, last_name=last_name
    )


def _violates_constraint(exc: IntegrityError, fragment: str) -> bool:
    """Czy IntegrityError dotyczy constraintu o nazwie zawierającej ``fragment``.

    Na psycopg nazwa constraintu jest w ``diag.constraint_name`` (pewne źródło); dopasowanie po
    komunikacie zostaje tylko jako fallback dla backendów bez diagnostyki (np. SQLite).
    """
    diag = getattr(exc.__cause__, "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name:
        return fragment in name
    return fragment in str(exc)


def create_participant_with_public_code(**fields) -> Participant:
    """Tworzy profil uczestnika z losowym kodem publicznym, ponawiając próbę po kolizji.

    Rozstrzygającą instancją jest unikalność w bazie, nie wcześniejszy ``SELECT`` – sprawdzenie
    ``exists()`` przed zapisem było podatne na TOCTOU (inny proces mógł zająć kod w międzyczasie).
    Każda próba idzie w osobnym savepoincie, więc IntegrityError nie unieważnia transakcji żądania.
    """
    for _ in range(PUBLIC_CODE_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                return Participant.objects.create(public_code=generate_public_code(), **fields)
        except IntegrityError as exc:
            if not _violates_constraint(exc, "public_code"):
                raise
    raise DomainError(
        "Nie udało się wygenerować kodu uczestnika.",
        "PUBLIC_CODE_UNAVAILABLE",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@sensitive_variables()
@transaction.atomic
def register_participant(
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    school: str,
    district: str,
    birth_year: int,
    gdpr_consent: bool,
    guardian_consent: bool = False,
) -> Participant:
    """Rejestracja otwarta uczestnika: User w grupie ``participant`` + profil ``Participant``."""
    _require_gdpr_consent(gdpr_consent)
    district = _require_voivodeship(district, required=True)
    user = _create_user(email=email, password=password, first_name=first_name, last_name=last_name)
    _add_to_group(user, GROUP_PARTICIPANT)
    return create_participant_with_public_code(
        user=user,
        school=school,
        district=district,
        birth_year=birth_year,
        gdpr_consent_at=timezone.now(),
        guardian_consent=guardian_consent,
    )


def _require_gdpr_consent(gdpr_consent: bool) -> None:
    if not gdpr_consent:
        raise DomainError(
            "Zgoda na przetwarzanie danych osobowych jest wymagana.",
            "GDPR_CONSENT_REQUIRED",
            status.HTTP_400_BAD_REQUEST,
        )


@transaction.atomic
def register_social_participant(
    *,
    email: str,
    first_name: str,
    last_name: str,
    school: str,
    district: str,
    birth_year: int,
    gdpr_consent: bool,
    guardian_consent: bool = False,
) -> Participant:
    """Rejestracja uczestnika po zalogowaniu przez dostawcę zewnętrznego (Google/Facebook).

    Różnice wobec ``register_participant`` są dwie i obie są zamierzone:

    - **konto nie ma użytecznego hasła** (``set_unusable_password``). Poświadczeniem jest konto
      u dostawcy; gdyby uczestnik chciał logować się także hasłem, ustawi je przez „Nie pamiętasz
      hasła?” – ta ścieżka potwierdza dostęp do skrzynki i podlega walidatorom haseł,
    - **adres e-mail nie pochodzi z formularza**, tylko z odpowiedzi dostawcy. Wpisywalne pole
      pozwalałoby zarejestrować konto na cudzy adres i tą drogą przejąć je resetem hasła.

    Zgoda RODO jest sprawdzana **przed** zapisem czegokolwiek – bez niej nie powstaje ani ``User``,
    ani ``Participant``, ani powiązanie ``SocialAccount`` (to ostatnie zapisuje dopiero widok).
    """
    _require_gdpr_consent(gdpr_consent)
    district = _require_voivodeship(district, required=True)
    email = _normalize_email(email)
    if not email:
        raise DomainError(
            "Dostawca nie przekazał adresu e-mail.", "EMAIL_REQUIRED", status.HTTP_400_BAD_REQUEST
        )
    if User.objects.filter(email=email).exists():
        raise DomainError(
            "Konto z tym adresem e-mail już istnieje.", "EMAIL_TAKEN", status.HTTP_400_BAD_REQUEST
        )
    user = User(email=email, first_name=first_name, last_name=last_name)
    user.set_unusable_password()
    user.save()
    _add_to_group(user, GROUP_PARTICIPANT)
    return create_participant_with_public_code(
        user=user,
        school=school,
        district=district,
        birth_year=birth_year,
        gdpr_consent_at=timezone.now(),
        guardian_consent=guardian_consent,
    )


@sensitive_variables("plain_code", "invitation_code")
def create_invitation(
    created_by: User,
    *,
    expires_at=None,
    valid_for=None,
    max_uses: int = 1,
    grants_status: str = InvitationGrantsStatus.ACTIVE,
    is_appeals: bool = False,
    district: str | None = None,
) -> tuple[InvitationCode, str]:
    """Tworzy kod zaproszenia i zwraca ``(obiekt, kod_jawny)``.

    Kod jawny jest zwracany wyłącznie wywołującemu (komenda CLI) i nigdzie nie jest zapisywany.
    ``district`` (o ile podany) narzuca okręg rejestrowanego recenzenta i czyni go zweryfikowanym.
    """
    district = _require_voivodeship(district, required=False)
    if expires_at is None:
        expires_at = timezone.now() + (valid_for or timedelta(days=14))
    if max_uses < 1:
        raise DomainError("Limit użyć musi być dodatni.", "INVALID_MAX_USES", status.HTTP_400_BAD_REQUEST)
    plain_code = secrets.token_urlsafe(INVITATION_CODE_BYTES)
    invitation = InvitationCode.objects.create(
        code_hash=hash_invitation_code(plain_code),
        created_by=created_by,
        expires_at=expires_at,
        max_uses=max_uses,
        grants_status=grants_status,
        is_appeals=is_appeals,
        district=district,
    )
    return invitation, plain_code


def _invalid_invitation() -> DomainError:
    # Jeden komunikat dla każdego powodu odrzucenia – brak wycieku informacji o istnieniu kodu.
    return DomainError(
        "Kod zaproszenia jest nieprawidłowy, wygasł lub został już wykorzystany.",
        "INVALID_INVITATION",
        status.HTTP_400_BAD_REQUEST,
    )


@sensitive_variables("plain_code", "invitation_code")
@transaction.atomic
def redeem_invitation(plain_code: str) -> InvitationCode:
    """Zużywa kod zaproszenia pod blokadą wiersza. Przy każdym błędzie ``used_count`` nie rośnie."""
    if not plain_code:
        raise _invalid_invitation()
    try:
        invitation = InvitationCode.objects.select_for_update().get(
            code_hash=hash_invitation_code(plain_code)
        )
    except InvitationCode.DoesNotExist as exc:
        raise _invalid_invitation() from exc
    if not invitation.is_usable():
        raise _invalid_invitation()
    invitation.used_count += 1
    invitation.save(update_fields=["used_count"])
    return invitation


@sensitive_variables()
@transaction.atomic
def register_committee(
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    invitation_code: str,
    district: str | None = None,
) -> CommitteeMember:
    """Rejestracja członka komitetu na podstawie kodu zaproszenia.

    Okręg z kodu zaproszenia jest nadrzędny wobec deklaracji z formularza: jeśli koordynator
    przypisał kodowi okręg, pole ``district`` z payloadu jest ignorowane, a profil dostaje
    ``district_verified=True``. Kod bez okręgu daje profil samodeklarowany i niezweryfikowany –
    taki recenzent nie jest przydzielany na etapie okręgowym (reguła konfliktu interesów).
    """
    # Okręg z payloadu sprawdzamy przed zużyciem kodu: nieprawidłowa deklaracja nie ma prawa
    # skasować jednorazowego zaproszenia (``redeem_invitation`` podnosi ``used_count``).
    declared = _require_voivodeship(district, required=False)
    invitation = redeem_invitation(invitation_code)
    user = _create_user(email=email, password=password, first_name=first_name, last_name=last_name)
    from_code = normalize_voivodeship(invitation.district)
    member = CommitteeMember.objects.create(
        user=user,
        district=from_code or declared,
        district_verified=bool(from_code),
        status=invitation.grants_status,
        is_appeals_committee=invitation.is_appeals,
    )
    if member.status == CommitteeStatus.ACTIVE:
        _grant_reviewer_groups(member)
        member.approved_at = timezone.now()
        member.save(update_fields=["approved_at"])
    return member


def _grant_reviewer_groups(member: CommitteeMember) -> None:
    _add_to_group(member.user, GROUP_REVIEWER)
    if member.is_appeals_committee:
        _add_to_group(member.user, GROUP_APPEALS)


@transaction.atomic
def approve_committee_member(member: CommitteeMember, *, actor: User) -> CommitteeMember:
    """Koordynator zatwierdza członka komitetu: status ACTIVE + grupy ``reviewer``/``appeals``."""
    member = CommitteeMember.objects.select_for_update().get(pk=member.pk)
    if member.status != CommitteeStatus.PENDING:
        # Zawieszonego nie odwiesza się ścieżką "approve" – to osobna, świadoma decyzja z własnym audytem.
        raise DomainError(
            "Zatwierdzić można tylko członka oczekującego.", "NOT_PENDING", status.HTTP_400_BAD_REQUEST
        )
    member.status = CommitteeStatus.ACTIVE
    member.approved_at = timezone.now()
    member.approved_by = actor
    member.save(update_fields=["status", "approved_at", "approved_by"])
    _grant_reviewer_groups(member)
    return member


@transaction.atomic
def verify_committee_district(
    member: CommitteeMember, *, district: str, actor: User, request=None
) -> CommitteeMember:
    """Koordynator potwierdza okręg członka komitetu (dług techniczny T-02).

    Dopóki okręg jest samodeklarowany, reguła konfliktu interesów nie ma na czym się oprzeć –
    dlatego przydział na etapie okręgowym pomija profile z ``district_verified=False``.
    """
    if not (district or "").strip():
        raise DomainError(
            "Podaj województwo do potwierdzenia.", "DISTRICT_REQUIRED", status.HTTP_400_BAD_REQUEST
        )
    district = _require_voivodeship(district, required=True)
    member = CommitteeMember.objects.select_for_update().get(pk=member.pk)
    if member.status != CommitteeStatus.ACTIVE:
        # Potwierdzony okręg wpuszcza do przydziału na etapie okręgowym. Nadawanie go profilowi
        # oczekującemu albo zawieszonemu byłoby cichym omijaniem ścieżki zatwierdzania.
        raise DomainError(
            "Województwo potwierdza się wyłącznie aktywnemu członkowi komitetu.",
            "MEMBER_NOT_ACTIVE",
            status.HTTP_400_BAD_REQUEST,
        )
    previous = member.district
    member.district = district
    member.district_verified = True
    member.save(update_fields=["district", "district_verified"])
    audit(
        actor,
        "committee.district_verified",
        member,
        {"district": {"from": previous, "to": district}, "district_verified": {"from": False, "to": True}},
        request=request,
    )
    return member


def make_coordinator(user: User) -> User:
    """Nadaje rolę koordynatora (używane przez seed/administrację, nie przez API)."""
    _add_to_group(user, GROUP_COORDINATOR)
    return user
