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

from .activation import send_activation_email
from .consents import (
    BY_KIND,
    CONSENTS,
    ConsentKind,
    ConsentSource,
    given_from_fields,
    required_kinds,
)
from .models import (
    GROUP_APPEALS,
    GROUP_COORDINATOR,
    GROUP_PARTICIPANT,
    GROUP_REVIEWER,
    MAX_GRADE,
    MIN_GRADE,
    CommitteeMember,
    CommitteeStatus,
    ConsentRecord,
    InvitationCode,
    InvitationGrantsStatus,
    Participant,
    User,
    generate_public_code,
    hash_invitation_code,
    normalize_voivodeship,
)
from .phones import normalize_phone

INVITATION_CODE_BYTES = 24
PUBLIC_CODE_MAX_ATTEMPTS = 20

#: Najkrótsza sensowna nazwa szkoły wpisana ręcznie. „LO” samo w sobie nie identyfikuje niczego,
#: a puste pole po ``strip()`` zostawiłoby uczestnika bez szkoły w tabeli wyników.
MIN_SCHOOL_NAME_LENGTH = 3


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


def _resolve_school(school: str, school_id: int | None):
    """Zwraca ``(nazwa_do_pokazania, obiekt_School_albo_None)`` dla pary pól z rejestracji.

    Dwie drogi, dokładnie jedna obowiązkowa:

    - **wybór ze słownika** (``school_id``) – nazwa jest przepisywana z rejestru, więc wszyscy
      uczniowie tej samej szkoły mają w bazie ten sam napis. Dopiero to sprawia, że grupowanie po
      szkole (próg k-anonimowości w publikacji wyników) cokolwiek znaczy,
    - **wolny tekst** (``school``) – dla szkół, których w wykazie nie ma: zagranicznych, świeżo
      założonych, przekształconych po dacie wykazu. Bez tej furtki rejestracja byłaby zamknięta
      dla ludzi, których jedyną winą jest nieaktualność cudzego rejestru.

    Pierwszeństwo ma ``school_id``: kiedy przyjdą oba, wolny tekst jest ignorowany, bo nazwa
    i tak zostaje przepisana z rejestru. Kiedy ``school_id`` nie ma, **wystarczy sam tekst** –
    serwis nigdy nie widział kratki „mojej szkoły nie ma na liście” i widzieć jej nie ma. To pole
    interfejsu, które odsłania wolny tekst; warunkowanie nim rejestracji oznaczało (zgłoszenie
    z produkcji), że uczestnik z zablokowanym skryptem wpisywał nazwę szkoły i dostawał odmowę.

    Sprawdzenie siedzi w serwisie, a nie tylko w formularzu i serializerze, bo tych wejść jest
    kilka (WWW, API, logowanie społecznościowe, seed) – reguła powtórzona w każdym z nich
    rozjechałaby się przy pierwszej zmianie.
    """
    # Import lokalny: ``apps.schools`` zna ``apps.accounts`` (lista województw), więc import
    # w drugą stronę na poziomie modułu zamknąłby pętlę zależności między aplikacjami.
    from apps.schools.models import School

    text = (school or "").strip()
    if school_id is not None:
        try:
            chosen = School.objects.get(pk=school_id, is_active=True)
        except School.DoesNotExist as exc:
            raise DomainError(
                "Wybrana szkoła nie istnieje w rejestrze.",
                "SCHOOL_NOT_FOUND",
                status.HTTP_400_BAD_REQUEST,
            ) from exc
        return chosen.name, chosen
    if len(text) < MIN_SCHOOL_NAME_LENGTH:
        raise DomainError(
            "Wybierz szkołę z listy albo wpisz jej nazwę.",
            "SCHOOL_REQUIRED",
            status.HTTP_400_BAD_REQUEST,
        )
    return text, None


def _require_grade(grade) -> int:
    """Klasa 1–5. Wymagana od każdego nowego uczestnika (stare profile mają ``None``)."""
    try:
        number = int(grade)
    except (TypeError, ValueError) as exc:
        raise DomainError(
            f"Podaj klasę ({MIN_GRADE}–{MAX_GRADE}).", "GRADE_INVALID", status.HTTP_400_BAD_REQUEST
        ) from exc
    if not MIN_GRADE <= number <= MAX_GRADE:
        raise DomainError(
            f"Podaj klasę ({MIN_GRADE}–{MAX_GRADE}).", "GRADE_INVALID", status.HTTP_400_BAD_REQUEST
        )
    return number


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
def _create_user(
    *, email: str, password: str, first_name: str, last_name: str, is_active: bool = True
) -> User:
    """Tworzy konto hasłowe. ``is_active=False`` znaczy „czeka na link aktywacyjny”.

    Aktywność jest argumentem, a nie stałą, bo obie drogi rejestracji (uczestnik, komitet na kod)
    wymagają potwierdzenia adresu, a ``bootstrap_coordinator`` i seed – nie. Konto nieaktywne nie
    przechodzi przez ``ModelBackend`` (ogólny komunikat „nieprawidłowy e-mail lub hasło”), więc
    logowanie jest zamknięte samym tym polem, bez drugiej reguły w widoku logowania.
    """
    email = _normalize_email(email)
    if User.objects.filter(email=email).exists():
        raise DomainError(
            "Konto z tym adresem e-mail już istnieje.", "EMAIL_TAKEN", status.HTTP_400_BAD_REQUEST
        )
    unsaved = User(email=email, first_name=first_name, last_name=last_name)
    _validate_password_or_raise(password, unsaved)
    return User.objects.create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        is_active=is_active,
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
    district: str,
    birth_year: int,
    grade: int,
    gdpr_consent: bool,
    phone: str = "",
    school: str = "",
    school_id: int | None = None,
    guardian_consent: bool = False,
    terms_consent: bool = False,
    publish_name_consent: bool = False,
    source: str = ConsentSource.API,
    request=None,
) -> Participant:
    """Rejestracja otwarta uczestnika: User w grupie ``participant`` + profil ``Participant``.

    „Otwarta” znaczy „bez zaproszenia”, a nie „zawsze”: okno rejestracji ustawia koordynator
    w panelu i pilnuje go ``ensure_registration_open`` (patrz niżej).

    Szkoła przychodzi jedną z dwóch dróg – ``school_id`` (wybór ze słownika ``apps.schools``) albo
    ``school`` (wolny tekst dla szkół spoza wykazu); szczegóły w ``_resolve_school``. Kolejność
    argumentów jest zachowana wstecznie: klient API, który zna wyłącznie tekstowe ``school``,
    działa dalej bez zmian.

    Zgody wchodzą osobnymi argumentami (``terms_consent``, ``gdpr_consent``, ``guardian_consent``,
    ``publish_name_consent``), a nie słownikiem, bo są zwykłymi polami formularza i serializera –
    na słownik zamienia je ``given_from_fields`` w jednym miejscu. Nazwy dwóch starych argumentów
    zostają nietknięte, więc klient sprzed wprowadzenia zestawu zgód nadal trafia w te same pola;
    zmienia się tylko to, że sam regulamin też trzeba zaakceptować.

    **Konto powstaje nieaktywne** i czeka na kliknięcie linku z listu
    (``apps.accounts.activation``). Bez tego adres e-mail – który jest u nas loginem i jedyną drogą
    odzyskania konta – byłby przyjmowany na słowo: literówka dawałaby konto bez powrotu, a cudzy
    adres dałby się zająć kontem-widmem.
    """
    _require_registration_open()
    given = given_from_fields(
        {
            "terms_consent": terms_consent,
            "gdpr_consent": gdpr_consent,
            "guardian_consent": guardian_consent,
            "publish_name_consent": publish_name_consent,
        }
    )
    # Zgody sprawdzamy przed zapisem czegokolwiek – konto bez kompletu zgód nie ma prawa powstać
    # nawet na chwilę wewnątrz transakcji.
    validate_consents(given, birth_year=birth_year)
    district = _require_voivodeship(district, required=True)
    school_name, school_obj = _resolve_school(school, school_id)
    grade = _require_grade(grade)
    phone = normalize_phone(phone)
    user = _create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        is_active=False,
    )
    _add_to_group(user, GROUP_PARTICIPANT)
    participant = create_participant_with_public_code(
        user=user,
        school=school_name,
        school_ref=school_obj,
        grade=grade,
        district=district,
        birth_year=birth_year,
        phone=phone,
        gdpr_consent_at=timezone.now(),
        guardian_consent=guardian_consent,
    )
    record_consents(participant, given, source=source, request=request)
    send_activation_email(user, request=request)
    return participant


def _require_registration_open() -> None:
    """Okno rejestracji uczestników – bramka wspólna dla formularza, API i logowania społecznościowego.

    Import jest lokalny, żeby zależność kont od zawodów nie powstawała przy starcie: to
    ``apps.competitions.models`` importuje ``apps.accounts.models``, a nie odwrotnie. Sprowadzenie
    tego importu na poziom modułu zamieniłoby jednokierunkową zależność w pętlę czekającą na
    pierwszą zmianę kolejności ładowania aplikacji.

    Rejestracji **komitetu** ta bramka nie dotyczy i dotyczyć nie ma: tam wstępem jest kod
    zaproszenia, a recenzentów kompletuje się właśnie wtedy, gdy rejestracja uczestników jeszcze
    nie ruszyła albo już się zamknęła.
    """
    from apps.competitions.registration import ensure_registration_open

    ensure_registration_open()


def validate_consents(given: dict[str, bool], *, birth_year: int | None) -> None:
    """Sprawdza komplet zgód wymaganych od uczestnika o tym roczniku.

    Reguła siedzi **w serwisie**, a nie w formularzu i serializerze, bo dróg rejestracji są trzy
    (WWW, API, dostawca zewnętrzny) i każda z nich jest równie dobrym wejściem. Formularz
    powtarza sprawdzenie zgody opiekuna wyłącznie po to, żeby błąd stanął pod właściwym polem –
    rozstrzyga to sprawdzenie.

    Woła się je **przed** utworzeniem czegokolwiek: bez kompletu zgód nie powstaje ani ``User``,
    ani ``Participant``, ani powiązanie ``SocialAccount``.
    """
    for kind in required_kinds(birth_year):
        if not given.get(kind):
            raise DomainError(
                BY_KIND[kind].missing_message,
                "CONSENT_REQUIRED",
                status.HTTP_400_BAD_REQUEST,
            )


@transaction.atomic
def record_consents(
    participant: Participant,
    given: dict[str, bool],
    *,
    source: str,
    request=None,
) -> list[ConsentRecord]:
    """Zapisuje dowody zgód, przepisuje projekcje na profil i zostawia jeden wpis audytowy.

    Trzy rzeczy w jednej transakcji, bo rozejście się którejkolwiek z nich znaczy dowód niezgodny
    ze stanem:

    1. ``ConsentRecord`` na każdą **wyrażoną** zgodę – z wersją dokumentu obowiązującą teraz
       (``apps.accounts.consents``) i z drogą, którą wpłynęła. Zgoda niewyrażona nie tworzy
       wiersza: brak dowodu jest tu poprawnym stanem, a wiersz „nie zgodził się” niczego nie
       dowodzi i tylko rozmywałby znaczenie tabeli,
    2. projekcje na ``Participant`` (``terms_accepted_at``, ``gdpr_consent_at``,
       ``guardian_consent``, ``publish_full_name``) – to po nich pyta reszta systemu,
    3. **jeden** wpis audytowy na całą operację. Cztery osobne wpisy opisywałyby cztery zdarzenia,
       a zdarzeniem jest jedno: wypełnienie formularza.
    """
    now = timezone.now()
    validate_consents(given, birth_year=participant.birth_year)

    records = [
        ConsentRecord(
            participant=participant,
            kind=consent.kind,
            document_version=consent.version,
            given_at=now,
            source=source,
        )
        for consent in CONSENTS
        if given.get(consent.kind)
    ]
    ConsentRecord.objects.bulk_create(records)

    if given.get(ConsentKind.TERMS):
        participant.terms_accepted_at = now
    if given.get(ConsentKind.PRIVACY):
        participant.gdpr_consent_at = now
    participant.guardian_consent = bool(given.get(ConsentKind.GUARDIAN))
    participant.publish_full_name = bool(given.get(ConsentKind.PUBLISH_NAME))
    participant.save(
        update_fields=["terms_accepted_at", "gdpr_consent_at", "guardian_consent", "publish_full_name"]
    )

    audit(
        participant.user,
        "participant.consents_recorded",
        participant,
        {
            "source": source,
            **{
                consent.kind: {"given": bool(given.get(consent.kind)), "version": consent.version}
                for consent in CONSENTS
            },
        },
        request=request,
    )
    return records


@transaction.atomic
def set_publish_name_consent(
    participant: Participant, *, given: bool, source: str = ConsentSource.PANEL, request=None
) -> ConsentRecord | None:
    """Wyrażenie albo wycofanie zgody na publikację nazwiska – jedyna zgoda odwracalna w portalu.

    Pozostałe trzy są warunkiem udziału albo oświadczeniem o zapoznaniu się z dokumentem: ich
    „wycofanie” znaczy rezygnację z Olimpiady i jest sprawą do organizatora, a nie przełącznikiem
    w panelu. Ta jedna dotyczy wyłącznie tego, jak uczestnik jest podpisany w publikowanej tabeli,
    więc musi dać się cofnąć – i to bez utraty śladu, że kiedyś była wyrażona.

    Wycofanie **znaczy wiersze**, a nie ich brak: aktywne wpisy dostają ``withdrawn_at``, więc
    z historii dalej widać, kiedy zgoda obowiązywała. Ponowne wyrażenie tworzy nowy wpis
    z aktualną wersją oświadczenia.
    """
    now = timezone.now()
    active = ConsentRecord.objects.filter(
        participant=participant, kind=ConsentKind.PUBLISH_NAME, withdrawn_at__isnull=True
    )
    record = None
    if given:
        # Bez podwójnego wpisu: druga zgoda „na to samo” nie jest nowym zdarzeniem, tylko
        # kliknięciem w przycisk, który i tak był już zaznaczony.
        record = active.order_by("-given_at", "-id").first()
        if record is None:
            record = ConsentRecord.objects.create(
                participant=participant,
                kind=ConsentKind.PUBLISH_NAME,
                document_version=BY_KIND[ConsentKind.PUBLISH_NAME].version,
                given_at=now,
                source=source,
            )
    else:
        active.update(withdrawn_at=now)

    if participant.publish_full_name != given:
        participant.publish_full_name = given
        participant.save(update_fields=["publish_full_name"])

    audit(
        participant.user,
        "participant.consent_publish_name",
        participant,
        {"given": given, "source": source, "version": BY_KIND[ConsentKind.PUBLISH_NAME].version},
        request=request,
    )
    return record


def consents_for_participant(participant: Participant) -> list[ConsentRecord]:
    """Historia zgód uczestnika, najnowsza pierwsza – panel ``/me/`` i profil w API."""
    return list(ConsentRecord.objects.filter(participant=participant))


@transaction.atomic
def register_social_participant(
    *,
    email: str,
    first_name: str,
    last_name: str,
    district: str,
    birth_year: int,
    grade: int,
    gdpr_consent: bool,
    phone: str = "",
    school: str = "",
    school_id: int | None = None,
    guardian_consent: bool = False,
    terms_consent: bool = False,
    publish_name_consent: bool = False,
    email_verified: bool = False,
    source: str = ConsentSource.SOCIAL,
    request=None,
) -> Participant:
    """Rejestracja uczestnika po zalogowaniu przez dostawcę zewnętrznego (Google/Facebook).

    Różnice wobec ``register_participant`` są dwie i obie są zamierzone:

    - **konto nie ma użytecznego hasła** (``set_unusable_password``). Poświadczeniem jest konto
      u dostawcy; gdyby uczestnik chciał logować się także hasłem, ustawi je przez „Nie pamiętasz
      hasła?” – ta ścieżka potwierdza dostęp do skrzynki i podlega walidatorom haseł,
    - **adres e-mail nie pochodzi z formularza**, tylko z odpowiedzi dostawcy. Wpisywalne pole
      pozwalałoby zarejestrować konto na cudzy adres i tą drogą przejąć je resetem hasła.

    Komplet zgód jest sprawdzany **przed** zapisem czegokolwiek – bez niego nie powstaje ani
    ``User``, ani ``Participant``, ani powiązanie ``SocialAccount`` (to ostatnie zapisuje dopiero
    widok). Tak samo okno rejestracji: udane logowanie u dostawcy nie jest obejściem zamkniętej
    rejestracji.

    ``email_verified`` rozstrzyga, czy konto jest aktywne od razu. Nie jest to konfiguracja, tylko
    **odpowiedź dostawcy o konkretnym adresie**: Google podaje ``email_verified`` i wtedy drugie
    potwierdzenie tego samego adresu naszym listem byłoby pytaniem o coś, co już wiemy. Facebook
    nie potwierdza adresu wcale (``VERIFIED_EMAIL: False``, patrz ``config/settings/base.py``),
    więc konto zakładane tą drogą przechodzi przez zwykłą aktywację linkiem – inaczej wystarczyłoby
    wpisać cudzy adres w profilu Facebooka, żeby dostać konto podpisane tym adresem.
    """
    _require_registration_open()
    given = given_from_fields(
        {
            "terms_consent": terms_consent,
            "gdpr_consent": gdpr_consent,
            "guardian_consent": guardian_consent,
            "publish_name_consent": publish_name_consent,
        }
    )
    validate_consents(given, birth_year=birth_year)
    district = _require_voivodeship(district, required=True)
    school_name, school_obj = _resolve_school(school, school_id)
    grade = _require_grade(grade)
    email = _normalize_email(email)
    if not email:
        raise DomainError(
            "Dostawca nie przekazał adresu e-mail.", "EMAIL_REQUIRED", status.HTTP_400_BAD_REQUEST
        )
    if User.objects.filter(email=email).exists():
        raise DomainError(
            "Konto z tym adresem e-mail już istnieje.", "EMAIL_TAKEN", status.HTTP_400_BAD_REQUEST
        )
    phone = normalize_phone(phone)
    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_active=bool(email_verified),
        email_verified_at=timezone.now() if email_verified else None,
    )
    user.set_unusable_password()
    user.save()
    _add_to_group(user, GROUP_PARTICIPANT)
    participant = create_participant_with_public_code(
        user=user,
        school=school_name,
        school_ref=school_obj,
        grade=grade,
        district=district,
        birth_year=birth_year,
        phone=phone,
        gdpr_consent_at=timezone.now(),
        guardian_consent=guardian_consent,
    )
    record_consents(participant, given, source=source, request=request)
    if not email_verified:
        send_activation_email(user, request=request)
    return participant


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
    request=None,
) -> CommitteeMember:
    """Rejestracja członka komitetu na podstawie kodu zaproszenia.

    Okręg z kodu zaproszenia jest nadrzędny wobec deklaracji z formularza: jeśli koordynator
    przypisał kodowi okręg, pole ``district`` z payloadu jest ignorowane, a profil dostaje
    ``district_verified=True``. Kod bez okręgu daje profil samodeklarowany i niezweryfikowany –
    taki recenzent nie jest przydzielany na etapie okręgowym (reguła konfliktu interesów).

    Aktywacja adresu obowiązuje tu **tak samo**, jak przy rejestracji otwartej: kod zaproszenia
    dowodzi, że koordynator kogoś zaprosił, a nie że wpisany adres należy do tej osoby. Status
    ``ACTIVE`` z kodu i aktywacja konta to dwie różne rzeczy – pierwsza daje uprawnienia
    recenzenta, druga wpuszcza do logowania.
    """
    # Okręg z payloadu sprawdzamy przed zużyciem kodu: nieprawidłowa deklaracja nie ma prawa
    # skasować jednorazowego zaproszenia (``redeem_invitation`` podnosi ``used_count``).
    declared = _require_voivodeship(district, required=False)
    invitation = redeem_invitation(invitation_code)
    user = _create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        is_active=False,
    )
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
    send_activation_email(user, request=request)
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
