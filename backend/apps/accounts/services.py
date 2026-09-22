"""Logika domenowa kont: rejestracja, kody zaproszeń, zatwierdzanie komitetu.

Widoki nie tworzą obiektów samodzielnie – cała logika i wszystkie błędy domenowe są tutaj.
"""

import re
import secrets
from datetime import date, datetime, timedelta

from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import EmailValidator
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy import branding
from apps.tenancy.context import current_competition

from .activation import absolute_url, queue_mail, send_activation_email
from .consents import (
    BY_KIND,
    ConsentKind,
    ConsentSource,
    consent_set,
    given_from_fields,
    required_kinds,
)
from .models import (
    DIRECTORY_INSTITUTION_TYPES,
    MAX_GRADE,
    MIN_BIRTH_DATE,
    MIN_GRADE,
    UNKNOWN_BIRTH_YEAR,
    CommitteeMember,
    CommitteeStatus,
    CompetitionRole,
    ConsentRecord,
    InvitationCode,
    InvitationGrantsStatus,
    Membership,
    Participant,
    RegistrationProfile,
    User,
    Voivodeship,
    generate_public_code,
    hash_invitation_code,
    normalize_voivodeship,
    region_for_district,
)
from .phones import normalize_phone

INVITATION_CODE_BYTES = 24
PUBLIC_CODE_MAX_ATTEMPTS = 20

#: Najkrótsza sensowna nazwa szkoły wpisana ręcznie. „LO” samo w sobie nie identyfikuje niczego,
#: a puste pole po ``strip()`` zostawiłoby uczestnika bez szkoły w tabeli wyników.
MIN_SCHOOL_NAME_LENGTH = 3

#: Flaga, za którą stoi cały ``RegistrationProfile`` (§ 0.6, § 2.3). Jedna flaga na obszar, a nie
#: jedna na pole: profil jest **jedną** decyzją organizatora („co konkurs pyta przy rejestracji”),
#: a piętnaście przełączników na piętnaście pól byłoby piętnastoma sposobami na rozjazd formularza
#: z serwisem.
REGISTRATION_PROFILE_FLAG = "institution_types"

#: Flaga słownika własnego organizatora. Czyta ją :func:`custom_directory_enabled` i wyłącznie ona.
CUSTOM_DIRECTORY_FLAG = "custom_school_directory"

#: Flaga podziału terytorialnego z ``Region`` zamiast z ``Voivodeship`` (§ 1.4.2).
CUSTOM_REGIONS_FLAG = "custom_regions"


def registration_profile(competition=None) -> RegistrationProfile:
    """Profil rejestracji **tego** konkursu – jedyne wejście do reguł formularza (§ 1.3.4).

    Odwrót jest dwustopniowy i oba stopnie dają **to samo**, czyli dzisiejszy formularz:

    - **flaga wyłączona albo konkurs nierozstrzygnięty** – oddajemy niezapisany wiersz z samymi
      wartościami domyślnymi i **nie pytamy bazy ani razu**. To jest warunek z § 5.6: budżet
      zapytań ``/register/`` nie rośnie w żadnym wydaniu etapu 2, a rejestracja jest ekranem,
      po którym chodzi każdy uczestnik;
    - **flaga włączona, ale wiersza nie ma** – tak samo, bo brak wiersza znaczy „jak dziś”
      (§ 1.3.4). Migracja ``accounts.0028`` nie zakłada go nikomu.

    Odwrót przy pustym kontekście jest **miękki**, tak samo jak przy zgodach
    (``apps.accounts.consents._competition_or_none``) i z tego samego powodu: twarde
    ``require_competition`` zamieniłoby żądanie pod nierozstrzygniętym hostem w błąd 500 na
    formularzu rejestracji, a to jest zmiana widoczna dla uczestnika Konkursu #1 (§ 0.1) – i to
    zmiana na gorsze, bo reguły domyślne są poprawne.

    Wynik jest **do czytania**, a nie do zapisu: wiersz zwrócony przy wyłączonej fladze nie ma
    klucza głównego i ``save()`` na nim założyłby konfigurację, której nikt nie zamawiał.
    """
    competition = _competition_or_none(competition)
    if competition is None or not competition.has_feature(REGISTRATION_PROFILE_FLAG):
        return RegistrationProfile()
    return _stored_profile(competition)


def _competition_or_none(competition=None):
    """Konkurs dla odczytu reguł rejestracji albo ``None`` – odwrót **miękki**, jak przy zgodach."""
    from apps.competitions.scoping import resolve_competition

    try:
        return resolve_competition(competition)
    except Exception:  # noqa: BLE001 - brak kontekstu nie może zablokować rejestracji
        return None


def _stored_profile(competition) -> RegistrationProfile:
    """Wiersz profilu tego konkursu albo niezapisany wiersz z domyślnymi (brak = „jak dziś”).

    Odwrotna relacja ``OneToOne`` podnosi ``RelatedObjectDoesNotExist``, który dziedziczy po
    ``AttributeError`` – ``getattr`` z wartością domyślną jest więc tu pełnym odczytem, a nie
    obejściem; Django zapamiętuje wynik przy obiekcie, więc drugi odczyt w tym samym żądaniu
    nie kosztuje już nic.
    """
    return getattr(competition, "registration_profile", None) or RegistrationProfile(competition=competition)


def allowed_institution_types(competition=None) -> tuple[str, ...]:
    """Rodzaje placówek dopuszczone w konkursie – dla wyszukiwarki (T25) i importu (T24).

    Zawsze niepusta krotka wartości ``schools.InstitutionType``; przy wyłączonej fladze
    ``institution_types`` jest to dokładnie ``("SECONDARY",)``, czyli dzisiejszy zakres
    podpowiedzi. Wyszukiwarka pyta **tę** funkcję, a nie profil wprost: dzięki temu zawężenie
    listy i zawężenie walidacji w serwisie mają jedno źródło, a wiersz, którego nie wolno wybrać,
    nie ma jak trafić do podpowiedzi.
    """
    return registration_profile(competition).institution_types()


def custom_directory_enabled(competition=None) -> bool:
    """Czy wolno pytać o słownik własny organizatora (``schools.CustomInstitution``, T24).

    Koniunkcja **flagi platformy** i **decyzji konkursu**: flaga mówi, czy ta zdolność w ogóle
    istnieje w tym wydaniu, a pole profilu – czy ten konkurs jej używa. Przy wyłączonej fladze
    odpowiedź jest ``False`` **bez zapytania do bazy**, bo od tego zależy budżet ``/register/``
    (§ 5.6: „bez flagi zapytanie do ``CustomInstitution`` nie pada ani razu”).

    Wiersz czytamy wprost, a nie przez :func:`registration_profile`: słownik własny jest osobną
    zdolnością z osobną flagą, więc konkurs, który chce wyłącznie własnego wykazu placówek, nie
    ma powodu włączać przy okazji rodzajów placówek.
    """
    competition = _competition_or_none(competition)
    if competition is None or not competition.has_feature(CUSTOM_DIRECTORY_FLAG):
        return False
    return _stored_profile(competition).allow_custom_directory


def _require_voivodeship(district: str | None, *, required: bool) -> str | None:
    """Sprowadza województwo do wartości z listy albo podnosi błąd domenowy.

    Walidacja jest tutaj, a nie tylko w formularzu i serializerze, bo do serwisów wchodzą też
    seed, komendy CLI i logowanie społecznościowe – gdyby każda z tych ścieżek pilnowała listy
    osobno, do bazy trafiłby prędzej czy później zapis spoza słownika i reguła konfliktu
    interesów (porównanie województw) przestałaby być rozstrzygalna.
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


def _resolve_school(
    school: str,
    school_id: int | None,
    *,
    allow_free_text: bool = True,
    allowed_types: tuple[str, ...] | None = None,
):
    """Zwraca ``(nazwa_do_pokazania, obiekt_School_albo_None)`` dla pary pól z rejestracji.

    Dwa argumenty nazwane dołożył etap 2 i oba mają **dzisiejszą wartość domyślną**, więc wołający,
    który o profilu rejestracji nie wie (``apps.accounts.profile``, import grupowy, seed), dostaje
    to samo zachowanie i te same komunikaty, co przed tą zmianą: ``allow_free_text=False`` zamyka
    furtkę wolnego tekstu, a ``allowed_types`` zawęża dopuszczone rodzaje placówek z wykazu.

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
        # Rodzaj placówki sprawdzamy na **już pobranym** wierszu, więc nie kosztuje to zapytania.
        # Przy domyślnym profilu warunek jest spełniony z definicji: dzisiejszy wykaz to 8118
        # szkół ponadpodstawowych i ani jednego wiersza innego rodzaju (§ 1.3.2).
        if allowed_types is not None and chosen.institution_type not in allowed_types:
            raise DomainError(
                "Ten rodzaj placówki nie jest dopuszczony w tym konkursie.",
                "INSTITUTION_TYPE_NOT_ALLOWED",
                status.HTTP_400_BAD_REQUEST,
            )
        return chosen.name, chosen
    if not allow_free_text:
        # Konkurs, który wyłączył wolny tekst, nie ma innej drogi niż wykaz – i musi to powiedzieć
        # osobnym zdaniem, bo dzisiejszy komunikat („albo wpisz jej nazwę”) odesłałby uczestnika
        # do pola, którego u niego nie ma.
        raise DomainError(
            "Wybierz placówkę z listy.",
            "SCHOOL_REQUIRED",
            status.HTTP_400_BAD_REQUEST,
        )
    if len(text) < MIN_SCHOOL_NAME_LENGTH:
        raise DomainError(
            "Wybierz szkołę z listy albo wpisz jej nazwę.",
            "SCHOOL_REQUIRED",
            status.HTTP_400_BAD_REQUEST,
        )
    return text, None


def _resolve_institution(profile: RegistrationProfile, fields: dict, *, competition=None) -> dict:
    """Sprowadza blok „placówka” do kolumn ``Participant`` – niezależnie od rodzaju placówki.

    Wejściem jest to, co przysłał formularz albo API (``institution_type``, ``school``,
    ``school_id``, ``custom_institution_id``, ``institution_name``, ``country``), wyjściem –
    słownik pól profilu. Trzy gałęzie, bo pytania są różne:

    - **placówka ze słownika organizatora** (``custom_institution_id``, § 1.3.3) – dowiązanie do
      ``schools.CustomInstitution`` obok pustego ``school_ref``. Gałąź jest pierwsza, bo wybór
      z wykazu jest wyborem z wykazu niezależnie od rodzaju placówki – uczelnia partnerska
      organizatora i jego ośrodek zagraniczny idą tą samą drogą,
    - **placówka z wykazu publicznego** (``PRIMARY``, ``SECONDARY``, ``UNIVERSITY``) – dokładnie
      dzisiejsza droga przez :func:`_resolve_school`, razem z furtką na wolny tekst,
    - **placówka spoza wykazu** (``FOREIGN``, ``NONE``, ``OTHER``) – nazwa jest wolnym tekstem
      i **kopiuje się do ``school``**, tak jak dziś kopiuje się nazwa z rejestru. Dzięki temu
      publikacja wyników, próg k-anonimowości i podgląd koordynatora nie muszą wiedzieć, którą
      drogą uczestnik się zarejestrował (§ 1.3.2). „Bez szkoły” zostawia ``school`` pustym – to
      jest odpowiedź na pytanie o szkołę, a nie brak odpowiedzi.

    Przy profilu domyślnym (Konkurs #1) gałąź jest jedna, ``institution_type`` nie przychodzi
    z żadnego formularza, a wynik jest identyczny z tym, co serwis zapisywał przed etapem 2.
    Wynik ma zawsze ten sam **komplet kluczy**, także ``custom_institution_ref`` – dzięki temu
    przejście z jednego wykazu na drugi czyści dowiązanie, zamiast zostawiać dwa naraz.
    """
    allowed = profile.institution_types()
    chosen = (fields.get("institution_type") or "").strip().upper()
    if not chosen:
        if len(allowed) > 1:
            raise DomainError(
                "Wybierz rodzaj placówki.", "INSTITUTION_TYPE_REQUIRED", status.HTTP_400_BAD_REQUEST
            )
        chosen = allowed[0]
    if chosen not in allowed:
        raise DomainError(
            "Ten rodzaj placówki nie jest dopuszczony w tym konkursie.",
            "INSTITUTION_TYPE_NOT_ALLOWED",
            status.HTTP_400_BAD_REQUEST,
        )
    country = _require_country(fields.get("country"), profile=profile, institution_type=chosen)
    custom_id = fields.get("custom_institution_id")
    # Kolejność warunków jest tu **całą** treścią budżetu zapytań z § 5.6: przy wyłączonej fladze
    # ``custom_school_directory`` ``custom_directory_enabled`` odpowiada bez dotknięcia bazy, a bez
    # przysłanego identyfikatora nie pytamy nawet o flagę. Rejestracja Konkursu #1 przechodzi więc
    # tędy dokładnie tyloma zapytaniami, co przed etapem 2 – i to jest jedyny warunek, pod którym
    # ta gałąź ma prawo tu stać.
    if custom_id and custom_directory_enabled(competition):
        chosen_row = _resolve_custom_institution(competition, custom_id, allowed=allowed)
        return {
            "school": chosen_row.name,
            "school_ref": None,
            "custom_institution_ref": chosen_row,
            "institution_name": "",
            "country": country,
        }
    if chosen in DIRECTORY_INSTITUTION_TYPES:
        name, school_obj = _resolve_school(
            fields.get("school", ""),
            fields.get("school_id"),
            allow_free_text=profile.allow_free_text_school,
            allowed_types=None if profile.is_default() else profile.directory_types(),
        )
        return {
            "school": name,
            "school_ref": school_obj,
            "custom_institution_ref": None,
            "institution_name": "",
            "country": country,
        }
    text = (fields.get("institution_name") or fields.get("school") or "").strip()
    if chosen == "NONE":
        return {
            "school": "",
            "school_ref": None,
            "custom_institution_ref": None,
            "institution_name": "",
            "country": country,
        }
    if len(text) < MIN_SCHOOL_NAME_LENGTH:
        raise DomainError("Podaj nazwę placówki.", "INSTITUTION_NAME_REQUIRED", status.HTTP_400_BAD_REQUEST)
    return {
        "school": text,
        "school_ref": None,
        "custom_institution_ref": None,
        "institution_name": text,
        "country": country,
    }


def _resolve_custom_institution(competition, custom_id, *, allowed: tuple[str, ...]):
    """Wiersz słownika organizatora wskazany przez formularz – albo błąd domenowy.

    Trzy warunki, wszystkie na **jednym** pobranym wierszu, więc kosztuje to jedno zapytanie:

    - wiersz należy do **tego** konkursu (``for_competition``) – identyfikator z cudzego wykazu ma
      być nie do odróżnienia od nieistniejącego, bo lista placówek organizatora jest jego listą
      kontrahentów i nawet „ten numer istnieje” jest o niej zdaniem,
    - wiersz jest aktywny – wygaszona placówka zostaje przy profilach sprzed wygaszenia, ale nowego
      zgłoszenia już nie przyjmuje (tak samo, jak wygaszona szkoła z wykazu SIO),
    - rodzaj placówki jest dopuszczony w konkursie – ta sama reguła i **ten sam komunikat**, co
      przy wykazie publicznym (``_resolve_school``); dwa zdania o tej samej odmowie znaczyłyby, że
      uczestnik widzi inny komunikat w zależności od tego, z której listy wybrał.
    """
    from apps.schools.custom import CustomInstitution

    try:
        chosen = CustomInstitution.objects.for_competition(competition).get(pk=custom_id, is_active=True)
    except (CustomInstitution.DoesNotExist, ValueError, TypeError) as exc:
        raise DomainError(
            "Wybrana placówka nie istnieje w wykazie organizatora.",
            "CUSTOM_INSTITUTION_NOT_FOUND",
            status.HTTP_400_BAD_REQUEST,
        ) from exc
    if chosen.institution_type not in allowed:
        raise DomainError(
            "Ten rodzaj placówki nie jest dopuszczony w tym konkursie.",
            "INSTITUTION_TYPE_NOT_ALLOWED",
            status.HTTP_400_BAD_REQUEST,
        )
    return chosen


def _require_country(country, *, profile: RegistrationProfile, institution_type: str) -> str:
    """Kraj w zapisie ISO 3166-1 alpha-2. **Pusty znaczy Polska**, a nie „nie podano”.

    Konkurs, który nie dopuszcza uczestników spoza Polski, o kraj nie pyta i przysłanej wartości
    nie zapisuje – pole, którego nie ma w formularzu, nie może wejść do bazy okrężną drogą przez
    API. Przy placówce poza Polską kraj jest **wymagany**: bez niego „poza Polską” nie niesie
    żadnej informacji ponad to, co i tak stoi w rodzaju placówki.
    """
    text = (country or "").strip().upper()
    if not profile.allow_foreign:
        return ""
    if not text:
        if institution_type == "FOREIGN":
            raise DomainError("Podaj kraj.", "COUNTRY_REQUIRED", status.HTTP_400_BAD_REQUEST)
        return ""
    if len(text) != 2 or not text.isascii() or not text.isalpha():
        raise DomainError(
            "Kraj podaj dwuliterowym kodem (ISO 3166-1), na przykład „DE”.",
            "COUNTRY_INVALID",
            status.HTTP_400_BAD_REQUEST,
        )
    return text


def _resolve_region(competition, district, region, *, profile: RegistrationProfile) -> tuple[str, object]:
    """Zwraca parę ``(district, region)`` do zapisania w profilu uczestnika (§ 1.4.2).

    Przy **wyłączonej** fladze ``custom_regions`` odpowiada wyłącznie ``district`` i nie pada ani
    jedno dodatkowe zapytanie – dokładnie jak dziś. Przy włączonej źródłem prawdy jest region,
    a ``district`` jest jego **denormalizowaną kopią** (``region.code``): to on zostaje dla
    kilkudziesięciu odczytów, których etap 2 nie dotyka – filtrów panelu, eksportów i tabel
    wyników. Jedno miejsce tego zapisu jest tutaj, więc kopia nie ma jak się rozjechać.

    Region rozpoznajemy po kodzie – najpierw po wartości przysłanej wprost (``region``), potem po
    województwie (``district``); robi to :func:`apps.accounts.models.region_for_district` jednym
    zapytaniem. Kod nierozpoznany schodzi do dzisiejszej reguły, czyli do listy województw:
    konkurs z włączoną flagą, ale bez własnego podziału, ma zachowywać się jak przed nią.
    """
    if competition is None or not competition.has_feature(CUSTOM_REGIONS_FLAG):
        return _require_voivodeship(district, required=profile.require_region) or "", None
    found = region_for_district(competition, (region or "").strip() or district)
    if found is None:
        return _require_voivodeship(district, required=profile.require_region) or "", None
    return found.code, found


def _require_grade(grade, *, profile: RegistrationProfile | None = None) -> int | None:
    """Klasa z przedziału konkursu. Domyślnie 1–5 i wymagana – czyli dokładnie jak dziś.

    Przedział i wymagalność bierze się z ``RegistrationProfile`` (§ 1.3.4); brak profilu znaczy
    dzisiejsze granice, więc wołający sprzed etapu 2 (``apps.accounts.profile``, import grupowy,
    API) nie zmienia zachowania ani komunikatu. Konkurs, który o klasę nie pyta, zapisuje
    ``None`` – kolumna jest nullowalna od zawsze, bo profile sprzed jej wprowadzenia też jej
    nie mają.
    """
    low, high = (MIN_GRADE, MAX_GRADE) if profile is None else profile.grade_range()
    required = True if profile is None else profile.require_grade
    if not required and (grade is None or grade == ""):
        return None
    try:
        number = int(grade)
    except (TypeError, ValueError) as exc:
        raise DomainError(
            f"Podaj klasę ({low}–{high}).", "GRADE_INVALID", status.HTTP_400_BAD_REQUEST
        ) from exc
    if not low <= number <= high:
        raise DomainError(f"Podaj klasę ({low}–{high}).", "GRADE_INVALID", status.HTTP_400_BAD_REQUEST)
    return number


def _require_birth_date(value) -> date | None:
    """Data urodzenia w postaci gotowej do zapisu – albo ``None``, gdy wolno jej nie znać.

    Jedna reguła dla wszystkich dróg zapisu (formularz, API, import, ekran koordynatora), bo od
    wieku zależy podstawa prawna zapisu i to nie jest miejsce na cztery interpretacje tego samego
    pola. Przyjmujemy ``date`` (formularz i serializer już ją zbudowały) oraz napis w zapisie ISO
    ``RRRR-MM-DD`` i polskim ``DD.MM.RRRR`` – drugi po to, żeby plik z importu nie wymagał od
    nauczyciela przestawiania formatu kolumny w arkuszu.

    Granice są sitem na literówki, a nie regułą wieku: data z przyszłości i data sprzed
    :data:`~apps.accounts.models.MIN_BIRTH_DATE` są odrzucane, a górnego ograniczenia wieku nie ma.

    Pusta wartość przechodzi jako ``None`` i **nie** jest tu błędem: o tym, czy wolno jej nie
    podać, rozstrzyga profil rejestracji jedno piętro wyżej (:func:`_resolve_birth`). Pusta data
    nie znaczy „pełnoletni” – ``consents.is_minor`` bez daty i bez rocznika odpowiada „małoletni”.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        parsed = None
        for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(str(value).strip(), pattern).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise DomainError(
                "Podaj datę urodzenia w zapisie RRRR-MM-DD albo DD.MM.RRRR.",
                "BIRTH_DATE_INVALID",
                status.HTTP_400_BAD_REQUEST,
            )
        value = parsed
    if value > timezone.localdate():
        raise DomainError(
            "Data urodzenia nie może być z przyszłości.",
            "BIRTH_DATE_INVALID",
            status.HTTP_400_BAD_REQUEST,
        )
    if value < MIN_BIRTH_DATE:
        raise DomainError(
            f"Data urodzenia nie może być wcześniejsza niż {MIN_BIRTH_DATE:%d.%m.%Y}.",
            "BIRTH_DATE_INVALID",
            status.HTTP_400_BAD_REQUEST,
        )
    return value


def _resolve_birth(birth_date, birth_year, *, profile: RegistrationProfile | None = None):
    """Para (data urodzenia, rocznik) dla zapisu – **jedno** miejsce godzenia obu kolumn.

    Trzy przypadki i każdy jest realny:

    - **data** (rejestracja od wydania 0.30.0) – rocznik bierze się z niej i przysłany obok
      rocznik jest ignorowany. Nie ma tu błędu „rok nie zgadza się z datą”: klient, który przysłał
      jedno i drugie, ma dostać zapis zgodny z tym, co dokładniejsze, a nie odmowę,
    - **sam rocznik** (klient API sprzed tej zmiany, import z kolumną „rok urodzenia”) – data
      zostaje pusta, a reguła wieku spada na starą, rocznikową,
    - **nic** – dopuszczalne wyłącznie w konkursie, którego profil o wiek nie pyta. Rocznik jest
      wtedy :data:`~apps.accounts.models.UNKNOWN_BIRTH_YEAR`, czyli „nie wiem”, a nie zero lat.
    """
    birth_date = _require_birth_date(birth_date)
    if birth_date is not None:
        return birth_date, birth_date.year
    if birth_year in (None, ""):
        if profile is None or profile.require_birth_year:
            raise DomainError("Podaj datę urodzenia.", "BIRTH_DATE_REQUIRED", status.HTTP_400_BAD_REQUEST)
        return None, UNKNOWN_BIRTH_YEAR
    try:
        year = int(birth_year)
    except (TypeError, ValueError) as exc:
        raise DomainError("Podaj datę urodzenia.", "BIRTH_DATE_INVALID", status.HTTP_400_BAD_REQUEST) from exc
    if not MIN_BIRTH_DATE.year <= year <= timezone.localdate().year:
        raise DomainError("Podaj datę urodzenia.", "BIRTH_DATE_INVALID", status.HTTP_400_BAD_REQUEST)
    return None, year


def active_reviewer_profile(user, competition=None) -> CommitteeMember | None:
    """Profil recenzenta użytkownika, o ile wolno mu recenzować: ACTIVE **i** rola ``reviewer``.

    Jedna definicja dla całego systemu: używa jej i uprawnienie ``IsActiveReviewer`` (przez
    ``apps.accounts.permissions``), i widoczność plików (``Submission.objects.for_user``), i serwisy
    oceniania. Rozjazd między nimi oznaczałby, że ktoś widzi pracę, której nie ma prawa recenzować.

    ``competition=None`` znaczy „weź konkurs z kontekstu” (``current_competition()``), a nie „bez
    konkursu”: funkcję woła kilkudziesięciu klientów w czterech aplikacjach, z których większość
    ma konkurs w żądaniu i nie ma po co go tu przepisywać. Wskazanie wprost jest dla zadań Celery
    i dla kodu, który chodzi po wielu konkursach po kolei.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return None
    member = getattr(user, "committee_member", None)
    if member is None or member.status != CommitteeStatus.ACTIVE:
        return None
    if not has_role(user, competition or current_competition(), CompetitionRole.REVIEWER):
        return None
    return member


# --- konkurs i role w konkursie -------------------------------------------------------------------
#
# Cała autoryzacja „kto czym jest” przechodzi przez ``has_role``. Jedno miejsce, bo dwa znaczyłyby
# dwie definicje roli, a pierwsza rozbieżność między nimi jest wyciekiem albo 403 na własnym panelu.


def default_competition():
    """Konkurs „na teraz”, a gdy kontekst jest pusty – jedyny konkurs w instalacji.

    Po co odwrót, skoro jest ``current_competition()``: bo znaczna część kodu kont chodzi **poza
    żądaniem** – zadania Celery (kosiarka kont, wysyłka listów), komendy (``bootstrap_coordinator``,
    seedy), importy i testy jednostkowe, które nigdy nie widziały nagłówka ``Host``. W bazie
    jednokonkursowej „nie wiadomo który” ma dokładnie jedną poprawną odpowiedź i udawanie, że jej
    nie ma, kończyłoby się wierszami bez właściciela.

    Przy **dwóch** konkursach odwrót celowo nie działa: zwracamy ``None``, czyli „powiedz wprost,
    o który chodzi”. Zgadywanie („weź pierwszy”) przypisałoby uczestnika cudzemu organizatorowi –
    i to po cichu. ``None`` u wołającego zostawia kolumnę pustą, co jest widoczne w kontroli przed
    ``NOT NULL`` (``docs/UNIWERSALNY-ETAP-1.md`` § 4.4), a nie w cudzej tabeli wyników.

    Miejsce docelowe tej funkcji to ``apps.tenancy`` – stoi tutaj, bo zadanie T1 jej nie zbudowało,
    a ``apps/accounts/`` jest jedynym katalogiem, który wolno ruszyć w T2. Przeniesienie jest
    zmianą jednej linii importu (patrz raport T2).
    """
    from apps.tenancy.models import Competition

    current = current_competition()
    if current is not None:
        return current
    # ``[:2]`` zamiast ``count()``: jedno zapytanie odpowiada i „ile ich jest”, i „który to”.
    rows = list(Competition.objects.order_by("pk")[:2])
    return rows[0] if len(rows) == 1 else None


def memberships_enforced(competition) -> bool:
    """Czy o roli rozstrzyga ``Membership``, czy jeszcze globalna grupa Django.

    To jest **przełącznik migracji**, nie docelowa opcja konfiguracji (§ 3.8). Do backfillu
    członkostw jedyną zapisaną w bazie odpowiedzią na pytanie „czy ta osoba jest recenzentem” są
    grupy; po backfillu flaga ``memberships_enforced`` przechodzi na ``True`` i zostaje w kodzie
    jeden sezon, na wypadek gdyby trzeba było wrócić bez wdrożenia.

    Brak konkursu znaczy „nie ma czego egzekwować”: reguła schodzi wtedy do grup, czyli do
    zachowania sprzed tej zmiany. To jest świadome i jest warunkiem § 0 – żądanie pod hostem,
    którego nikt nie przypisał do konkursu, ma odpowiadać dokładnie tak, jak odpowiadało wczoraj.
    """
    return competition is not None and competition.has_feature("memberships_enforced")


def has_role(user, competition, role: str) -> bool:
    """Czy ``user`` ma rolę ``role`` w konkursie ``competition``. Jedyne miejsce tej reguły.

    Przy wyłączonej fladze ``memberships_enforced`` odpowiada **grupa Django** – bajt w bajt to,
    co robi serwis dziś; przy włączonej – wiersz ``Membership``. Dzięki temu przełączenie jest
    jedną wartością w ``feature_flags``, a nie wdrożeniem, i da się je cofnąć w minutę.

    Czego ta funkcja **nie** robi: nie eskaluje superużytkownika. ``is_superuser`` opisuje
    operatora platformy, a nie koordynatora konkursu; cicha eskalacja zrobiłaby z każdego konta
    serwisowego konto z wglądem w dane uczestników. Ta sama reguła obowiązuje dziś w
    ``IsCoordinator`` i nie zmienia się tutaj.

    Konto nieaktywne (zablokowane albo z niepotwierdzonym adresem) nie ma żadnej roli, niezależnie
    od tego, co stoi w bazie – blokada konta ma zamykać dostęp od razu, bez sprzątania członkostw.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    # ``CompetitionRole(...)`` sprawdza wartość: literówka w nazwie roli ma podnieść ``ValueError``
    # w miejscu wywołania, a nie po cichu oddać „nie ma takiej roli, czyli nie masz uprawnień”.
    role = CompetitionRole(role).value
    if memberships_enforced(competition):
        return Membership.objects.filter(user=user, competition=competition, role=role).exists()
    return user.groups.filter(name=role).exists()


def roles_for(user, competition) -> set[str]:
    """Komplet ról tej osoby w tym konkursie – do nawigacji i do odpowiedzi API, nie do bramek.

    Bramkuje zawsze ``has_role`` (jedna rola, jedno pytanie). Ta funkcja odpowiada na pytanie
    „co ta osoba tu w ogóle robi”, zadawane raz na żądanie przy składaniu menu – i dlatego jest
    jednym zapytaniem, a nie pięcioma wywołaniami ``has_role``.

    Zbiór, a nie lista: kolejność ról nie niesie informacji, a porównanie zbiorów jest tym, czego
    chce wołający („czy jest tu kimkolwiek”, „czy jest tu wyłącznie uczestnikiem”).
    """
    if not user or not user.is_authenticated or not user.is_active:
        return set()
    known = set(CompetitionRole.values)
    if memberships_enforced(competition):
        rows = Membership.objects.filter(user=user, competition=competition)
        return set(rows.values_list("role", flat=True)) & known
    return set(user.groups.values_list("name", flat=True)) & known


def participant_for(user, competition) -> Participant | None:
    """Profil uczestnika tej osoby **w tym konkursie** albo ``None``.

    **Jedyna** droga do profilu. ``user.participant`` już nie istnieje: relacja odwrotna nazywa
    się ``participations`` i jest wielokrotna, więc każde przeoczone miejsce podnosi
    ``AttributeError`` zamiast oddawać profil z przypadkowego konkursu (§ 3.3, § 6 T2).

    ``competition=None`` znaczy „nie wiadomo, o który konkurs chodzi” i oddaje pierwszy profil bez
    zawężania. Wyciekiem to nie jest: pytamy o profil **tej** osoby, a nie o cudzy – a wołający
    poza żądaniem (komenda, zadanie Celery na bazie jednokonkursowej) ma wtedy jedną poprawną
    odpowiedź. Tam, gdzie konkurs jest znany, podaje się go wprost.

    Zawężenie jest **ścisłe**: wiersz innego konkursu nie jest odpowiedzią, a wierszy bez konkursu
    nie ma – kolumnę domknęła na ``NOT NULL`` migracja ``accounts.0022_competition_not_null``.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None
    rows = Participant.objects.filter(user=user)
    if competition is not None:
        rows = rows.filter(competition=competition)
    return rows.first()


def participations_of(user):
    """Wszystkie profile uczestnika tej osoby, po jednym na konkurs, razem z konkursem.

    Do pytań, które dotyczą **konta**, a nie konkursu: „w czym ta osoba w ogóle startuje”
    (panel konta), „co znika przy usunięciu konta” i „co trzeba wytrzeć przy anonimizacji”.
    Bramek na tym nie stawiamy i stawiać nie wolno – do tego jest ``participant_for``, które pyta
    o konkurs.

    Queryset, a nie lista: wołający dokłada własne ``filter`` i ``count`` bez pobierania wierszy,
    a konto bez profilu oddaje pusty queryset, nie ``None`` – pętla po nim wykonuje się zero razy
    i nie potrzebuje warunku.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return Participant.objects.none()
    return Participant.objects.filter(user=user).select_related("competition")


def _add_to_group(user: User, name: str) -> None:
    group, _ = Group.objects.get_or_create(name=name)
    user.groups.add(group)


def grant_role(user: User, role: str, *, competition=None, granted_by: User | None = None):
    """Nadaje rolę: wiersz ``Membership`` **i** przynależność do grupy Django. Oba, zawsze.

    Dlaczego oba, a nie samo członkostwo: od uprawnień grupy ``coordinator`` zależy dostęp do
    ``/cms/`` (migracja ``cms.0003_coordinator_permissions``), a te uprawnienia są własnością
    Wagtaila. Grupa jest więc dziś **uprawnieniem do panelu redakcyjnego**, a członkostwo – rolą
    w konkursie; rozdzielenie jednego zapisu na dwa serwisy skończyłoby się kontem, które ma rolę,
    ale nie ma panelu (albo odwrotnie).

    Brak konkursu (świeża instalacja przed ``tenancy.0002``, dwa konkursy bez wskazania) zapisuje
    **samą grupę** i nie podnosi wyjątku: zachowanie jest wtedy identyczne z tym sprzed T2, a
    pustą kolumnę widać w kontroli przed ``NOT NULL`` (§ 4.4). Wyjątek w tym miejscu przewracałby
    rejestrację uczestnika na bazie, na której konkurs jeszcze nie powstał.

    ``get_or_create``, bo nadanie roli jest **idempotentne**: ponowna rejestracja tą samą drogą,
    powtórzony import i powtórzony backfill mają skończyć się jednym wierszem, a nie błędem.
    """
    role = CompetitionRole(role).value
    _add_to_group(user, role)
    if competition is None:
        return None
    membership, _ = Membership.objects.get_or_create(
        user=user,
        competition=competition,
        role=role,
        defaults={"granted_by": granted_by},
    )
    return membership


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

    Prefiks kodu bierzemy z **konkursu tego profilu**, a nie ze stałej modułu: to ten sam konkurs,
    który zaraz trafi do kolumny, więc kod i jego właściciel nie mają jak się rozjechać. Unikalność
    jest odtąd parą (konkurs, kod), a jej nazwa zawiera ``public_code`` – ponawianie po kolizji
    rozpoznaje ją tak samo, jak dawny więz globalny.
    """
    competition = fields.get("competition")
    for _ in range(PUBLIC_CODE_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                return Participant.objects.create(public_code=generate_public_code(competition), **fields)
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
    grade: int,
    birth_date: date | str | None = None,
    birth_year: int | None = None,
    gdpr_consent: bool,
    phone: str = "",
    school: str = "",
    school_id: int | None = None,
    custom_institution_id: int | None = None,
    institution_type: str = "",
    institution_name: str = "",
    country: str = "",
    region: str = "",
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

    Pięć argumentów dołożył etap 2 i **wszystkie mają puste wartości domyślne**, więc klient
    sprzed tej zmiany trafia dokładnie tam, gdzie trafiał: ``institution_type`` (rodzaj placówki,
    pusty znaczy „jedyny dopuszczony”), ``institution_name`` (nazwa placówki spoza wykazu),
    ``country`` (ISO 3166-1 alpha-2, pusty znaczy Polska), ``region`` (kod regionu z podziału
    konkursu) i ``custom_institution_id`` (wybór ze słownika organizatora, § 1.3.3 – czytany
    wyłącznie przy włączonej fladze ``custom_school_directory``). To, czy o którekolwiek z nich
    wolno zapytać, rozstrzyga ``RegistrationProfile`` (§ 1.3.4) – a jego brak znaczy „jak dziś”.

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
    # Konkurs rejestracji: ten z żądania (ustawia go ``apps.tenancy.middleware``), a poza żądaniem
    # jedyny w instalacji. Jedna wartość dla profilu, dla członkostwa i dla reguł formularza –
    # gdyby były dwa odczyty, dałoby się zarejestrować uczestnika w jednym konkursie, regułami
    # drugiego, a rolę nadać mu w trzecim.
    competition = default_competition()
    profile = registration_profile(competition)
    # Wiek godzimy **przed** sprawdzeniem zgód, bo od niego zależy, które zgody są wymagane:
    # rozstrzygnięcie „małoletni” liczone z surowego napisu daty byłoby zgadywaniem.
    birth_date, birth_year = _resolve_birth(birth_date, birth_year, profile=profile)
    validate_consents(given, birth_date=birth_date, birth_year=birth_year)
    district, region_obj = _resolve_region(competition, district, region, profile=profile)
    institution = _resolve_institution(
        profile,
        {
            "institution_type": institution_type,
            "school": school,
            "school_id": school_id,
            "custom_institution_id": custom_institution_id,
            "institution_name": institution_name,
            "country": country,
        },
        competition=competition,
    )
    grade = _require_grade(grade, profile=profile)
    phone = normalize_phone(phone)
    user = _create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        is_active=False,
    )
    grant_role(user, CompetitionRole.PARTICIPANT, competition=competition)
    participant = create_participant_with_public_code(
        user=user,
        competition=competition,
        grade=grade,
        district=district,
        region=region_obj,
        birth_date=birth_date,
        birth_year=birth_year,
        phone=phone,
        gdpr_consent_at=timezone.now(),
        guardian_consent=guardian_consent,
        **institution,
    )
    record_consents(participant, given, source=source, request=request)
    send_activation_email(user, request=request)
    # Zdarzenie dla systemów zewnętrznych (``apps.integrations``). Wychodzi z niego kod publiczny,
    # województwo i klasa – nigdy imię, nazwisko ani adres. Edycją zdarzenia jest edycja bieżąca:
    # konto zakłada się **do olimpiady**, a nie do etapu, i to ona wyznacza rocznik zgłoszenia.
    from apps.competitions.services import current_edition
    from apps.integrations.events import registration_created

    registration_created(participant, current_edition())
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


def validate_consents(
    given: dict[str, bool],
    *,
    birth_date: date | None = None,
    birth_year: int | None = None,
    competition=None,
) -> None:
    """Sprawdza komplet zgód wymaganych od uczestnika w tym wieku.

    Reguła siedzi **w serwisie**, a nie w formularzu i serializerze, bo dróg rejestracji są trzy
    (WWW, API, dostawca zewnętrzny) i każda z nich jest równie dobrym wejściem. Formularz
    powtarza sprawdzenie zgody opiekuna wyłącznie po to, żeby błąd stanął pod właściwym polem –
    rozstrzyga to sprawdzenie.

    Woła się je **przed** utworzeniem czegokolwiek: bez kompletu zgód nie powstaje ani ``User``,
    ani ``Participant``, ani powiązanie ``SocialAccount``.
    """
    # Zestaw i komunikaty zgód **tego** konkursu; bez wskazania – konkurs z kontekstu żądania,
    # a przy wyłączonej fladze dzisiejsza stała (``consent_set``).
    by_kind = {consent.kind: consent for consent in consent_set(competition)}
    for kind in required_kinds(birth_date, birth_year, competition=competition):
        if not given.get(kind):
            raise DomainError(
                by_kind[kind].missing_message,
                "CONSENT_REQUIRED",
                status.HTTP_400_BAD_REQUEST,
            )


def _consent_by_kind(competition, kind: str):
    """Definicja zgody danego rodzaju w zestawie **tego** konkursu (``consent_set``)."""
    for consent in consent_set(competition):
        if consent.kind == kind:
            return consent
    return BY_KIND[kind]


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
    validate_consents(
        given,
        birth_date=participant.birth_date,
        birth_year=participant.birth_year,
        competition=participant.competition,
    )
    # Zestaw zgód **tego** konkursu (przy wyłączonej fladze – dzisiejsza stała, bez zapytania):
    # dowód ma nieść wersję dokumentu, pod którym uczestnik naprawdę się podpisał.
    consents = consent_set(participant.competition)

    records = [
        ConsentRecord(
            participant=participant,
            kind=consent.kind,
            document_version=consent.version,
            given_at=now,
            source=source,
        )
        for consent in consents
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
                for consent in consents
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
                document_version=_consent_by_kind(participant.competition, ConsentKind.PUBLISH_NAME).version,
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
    grade: int,
    birth_date: date | str | None = None,
    birth_year: int | None = None,
    gdpr_consent: bool,
    phone: str = "",
    school: str = "",
    school_id: int | None = None,
    custom_institution_id: int | None = None,
    institution_type: str = "",
    institution_name: str = "",
    country: str = "",
    region: str = "",
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
    competition = default_competition()
    profile = registration_profile(competition)
    birth_date, birth_year = _resolve_birth(birth_date, birth_year, profile=profile)
    validate_consents(given, birth_date=birth_date, birth_year=birth_year)
    district, region_obj = _resolve_region(competition, district, region, profile=profile)
    institution = _resolve_institution(
        profile,
        {
            "institution_type": institution_type,
            "school": school,
            "school_id": school_id,
            "custom_institution_id": custom_institution_id,
            "institution_name": institution_name,
            "country": country,
        },
        competition=competition,
    )
    grade = _require_grade(grade, profile=profile)
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
    grant_role(user, CompetitionRole.PARTICIPANT, competition=competition)
    participant = create_participant_with_public_code(
        user=user,
        competition=competition,
        grade=grade,
        district=district,
        region=region_obj,
        birth_date=birth_date,
        birth_year=birth_year,
        phone=phone,
        gdpr_consent_at=timezone.now(),
        guardian_consent=guardian_consent,
        **institution,
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
    ``district`` (o ile podany) narzuca województwo rejestrowanego recenzenta i oznacza je jako
    pochodzące od organizatora (``district_verified``).
    """
    district = _require_voivodeship(district, required=False)
    if expires_at is None:
        expires_at = timezone.now() + (valid_for or timedelta(days=14))
    if max_uses < 1:
        raise DomainError("Limit użyć musi być dodatni.", "INVALID_MAX_USES", status.HTTP_400_BAD_REQUEST)
    plain_code = secrets.token_urlsafe(INVITATION_CODE_BYTES)
    invitation = InvitationCode.objects.create(
        code_hash=hash_invitation_code(plain_code),
        # Kod wpuszcza do komitetu **tego** konkursu, w którym go wystawiono.
        competition=default_competition(),
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
    # ``is_usable`` rozstrzyga tu wszystkie trzy powody odmowy naraz: minął termin, wyczerpał się
    # limit użyć albo koordynator kod **unieważnił** (``revoked_at``). Ostatni jest tu istotny:
    # zaproszenie wysłane pod zły adres musi dać się odebrać, zanim ktoś obcy założy na nie konto
    # komitetu – a jedynym momentem, w którym da się to jeszcze zatrzymać, jest właśnie ta funkcja.
    if not invitation.is_usable():
        raise _invalid_invitation()
    invitation.used_count += 1
    invitation.save(update_fields=["used_count"])
    return invitation


# --- zaproszenia wysyłane e-mailem -------------------------------------------------------------

#: Ile adresów przyjmujemy w jednym wklejeniu. Limit jest po to, żeby przypadkowe wklejenie całej
#: książki adresowej nie zamieniło jednego kliknięcia w kilka tysięcy listów wysłanych z naszej
#: domeny w jednej minucie – dostawcy poczty czytają taki ruch jako spam i obniżają reputację
#: nadawcy, przez co przestają dochodzić także listy aktywacyjne uczestników.
MAX_INVITATION_EMAILS = 200

#: Separatory listy adresów: koordynator wkleja ją z arkusza (nowe wiersze), z pola „Do:”
#: (przecinki, średniki) albo przepisuje ręcznie (spacje). Rozdzielanie po każdym z nich naraz
#: jest tańsze niż tłumaczenie człowiekowi, w jakim formacie ma tę listę przygotować.
EMAIL_SEPARATORS = re.compile(r"[\s,;]+")

INVITATION_SUBJECT = "Zaproszenie do komitetu Olimpiady Kwantowej"

#: Ten sam temat jako wzorzec z nazwą konkursu (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.1). Jedyny
#: z szesnastu, który odmienia nazwę: „komitetu **Olimpiady Kwantowej**” jest dopełniaczem, więc
#: wzorzec sięga po ``%(competition_genitive)s`` (``Competition.genitive_name``). Stała wyżej
#: zostaje odwrotem – wybiera między nimi ``apps.tenancy.branding.subject``.
INVITATION_SUBJECT_TEMPLATE = "Zaproszenie do komitetu %(competition_genitive)s"

#: Górna długość osobistej dopiski koordynatora. Pole jest dla jednego zdania („piszemy po
#: rozmowie na konferencji”), a nie dla okólnika – długi tekst i tak zginie pod kodem.
MAX_INVITATION_NOTE_LENGTH = 500


def parse_email_list(raw: str) -> tuple[list[str], list[str]]:
    """Rozbija wklejoną listę adresów na ``(poprawne, odrzucone)`` – bez powtórzeń.

    Parser stoi w serwisie, a nie w formularzu, bo tę samą listę przyjmuje formularz panelu
    i (docelowo) każdy inny wołający ``send_invitations``; gdyby każdy rozbijał ją po swojemu,
    „Jan@Example.test” raz byłby duplikatem „jan@example.test”, a raz drugim zaproszeniem.

    Adres sprowadzamy do małych liter tą samą funkcją, co rejestracja (``_normalize_email``):
    porównujemy go zaraz potem z ``User.email``, który ma indeks bez rozróżniania wielkości liter.
    Odrzucone zwracamy w postaci **wpisanej przez człowieka**, żeby w komunikacie o błędzie dało
    się odnaleźć wiersz do poprawienia.
    """
    validator = EmailValidator()
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for token in EMAIL_SEPARATORS.split(raw or ""):
        candidate = token.strip()
        if not candidate:
            continue
        normalized = _normalize_email(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            validator(normalized)
        except DjangoValidationError:
            invalid.append(candidate)
        else:
            valid.append(normalized)
    return valid, invalid


@sensitive_variables("plain_code")
def invitation_message(
    plain_code: str,
    *,
    link: str,
    expires_at,
    district: str | None = None,
    is_appeals: bool = False,
    note: str = "",
    competition=None,
) -> str:
    """Treść zaproszenia. Poza adresem odbiorcy (i tak w nagłówku ``To:``) zero danych osobowych.

    Kod jawny jest **wyłącznie tutaj**: w bazie zostaje sha256, w audycie nie ma go wcale, a list
    jest jedynym egzemplarzem. Stąd zdanie o jednorazowości i o tym, że kodu nie wolno przekazywać
    dalej – z drugiej strony nie ma nikogo, kto mógłby odtworzyć kod komuś, kto go zgubił.

    Termin ważności podajemy w czasie polskim, tak jak potwierdzenie terminu rozmowy
    (``apps.competitions.interviews._confirmation_message``): odbiorca ma go przeczytać, a nie
    przeliczać z UTC.
    """
    expires_local = timezone.localtime(expires_at)
    lines = [
        "Komitet Olimpiady Kwantowej zaprasza Cię do prac komitetu zawodów.",
        "",
    ]
    if note:
        lines += [note, ""]
    lines += [
        f"Twój kod zaproszenia: {plain_code}",
        "",
        "Kod wpisuje się przy zakładaniu konta pod adresem:",
        link,
        "",
        f"Kod jest ważny do {expires_local:%d.%m.%Y, %H:%M} (czas polski).",
    ]
    if district:
        lines.append(f"Województwo przypisane do kodu: {Voivodeship(district).label}.")
    if is_appeals:
        lines.append("Kod uprawnia do prac komisji odwoławczej.")
    lines += [
        "",
        "Kod jest jednorazowy i osobisty – działa dla jednego konta i został wysłany wyłącznie "
        "na ten adres. Prosimy go nie przekazywać dalej; osobie, która także ma dołączyć do "
        "komitetu, koordynator wyśle własne zaproszenie.",
        "",
        "Jeśli nie spodziewasz się tego zaproszenia – zignoruj tę wiadomość. Bez wpisania kodu "
        "nic się nie wydarzy.",
        "",
        # Podpis idzie przez moduł marki, ale jego odwrotem jest **nietłumaczony** literał – i to
        # jest stan zastany, a nie przeoczenie: ten list jako jedyny z pięciu nie ma ani jednego
        # ``gettext`` (zaproszenie do komitetu pisze się po polsku, § 1.6.4).
        "--",
        branding.signature(competition),
        "Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać.",
    ]
    return "\n".join(lines)


@sensitive_variables("plain_code")
def _issue_invitation(
    *,
    created_by: User,
    email: str,
    expires_at,
    grants_status: str,
    is_appeals: bool,
    district: str | None,
    note: str,
    link: str,
    action: str,
    actor: User,
    request=None,
) -> InvitationCode:
    """Jeden kod dla jednego adresu: zapis, wpis audytowy i list zakolejkowany po commicie.

    Wspólny środek wysyłki masowej i ponowienia – obie drogi muszą zapisać dokładnie ten sam
    komplet pól, bo inaczej kod z ponowienia nie miałby ``sent_at`` i wypadłby z listy w panelu.

    W audycie jest adres (to własne pole wiersza, więc nie jest to żaden dodatkowy wyciek),
    termin i parametry kodu – **nigdy** kod jawny ani treść listu: log audytowy czyta później
    więcej osób niż skrzynka odbiorcy, a kod jest poświadczeniem dostępu do komitetu.
    """
    invitation, plain_code = create_invitation(
        created_by,
        expires_at=expires_at,
        max_uses=1,
        grants_status=grants_status,
        is_appeals=is_appeals,
        district=district,
    )
    invitation.email = email
    invitation.sent_at = timezone.now()
    invitation.save(update_fields=["email", "sent_at"])
    audit(
        actor,
        action,
        invitation,
        {
            "email": email,
            "district": district,
            "is_appeals": is_appeals,
            "grants_status": grants_status,
            "expires_at": invitation.expires_at.isoformat(),
        },
        request=request,
    )
    # Konkurs bierze się z **kodu**, a nie z kontekstu: zaproszenie nadaje status w komitecie tego
    # konkursu, którego kolumnę ma wiersz (``InvitationCode.competition``), i to jego marka ma stać
    # w temacie listu, który ten status obiecuje.
    competition = invitation.competition
    queue_mail(
        branding.subject(INVITATION_SUBJECT_TEMPLATE, INVITATION_SUBJECT, competition),
        invitation_message(
            plain_code,
            link=link,
            expires_at=invitation.expires_at,
            district=district,
            is_appeals=is_appeals,
            note=note,
            competition=competition,
        ),
        email,
        competition=competition,
    )
    return invitation


def _committee_registration_link(request=None) -> str:
    """Bezwzględny adres formularza „załóż konto komitetu” – ten sam helper, co listy aktywacyjne."""
    return absolute_url(reverse("web:register-committee"), request)


def _emails_with_committee_account(emails: list[str]) -> set[str]:
    """Adresy, które mają już konto z profilem komitetu – jednym zapytaniem, nie po jednym."""
    return set(
        User.objects.filter(email__in=emails, committee_member__isnull=False).values_list("email", flat=True)
    )


@transaction.atomic
def send_invitations(
    created_by: User,
    emails,
    *,
    district: str | None = None,
    valid_for=None,
    grants_status: str = InvitationGrantsStatus.ACTIVE,
    is_appeals: bool = False,
    note: str = "",
    request=None,
) -> dict:
    """Wysyła **osobny, jednorazowy** kod na każdy z podanych adresów.

    Osobny kod na adres, a nie jeden kod o ``max_uses=N``, jest tu całą różnicą: kod wspólny
    krąży potem po korespondencji i każdy, kto go zobaczy, zakłada sobie konto komitetu, dopóki
    limit się nie wyczerpie. Kod indywidualny da się unieważnić pojedynczo, a po fakcie wiadomo,
    kto z zaproszenia skorzystał – bez zaglądania komukolwiek do skrzynki.

    Termin ważności liczymy **raz** dla całej wysyłki: adresaci z jednej listy dostają zaproszenia
    z tą samą datą, więc koordynator ma jeden termin do zapamiętania, a nie dwieście.

    Adresy, które mają już konto z profilem komitetu, pomijamy. Kod nic by im nie dał –
    rejestracja odbiłaby się o „konto z tym adresem e-mail już istnieje” – a wysłany list
    wyglądałby jak zaproszenie do serwisu, w którym ta osoba od dawna pracuje.

    Transakcja obejmuje całą wysyłkę, a listy idą przez ``transaction.on_commit``: jeśli
    którykolwiek zapis się nie powiedzie, nie wyjdzie **żaden** list z kodem do bazy, której
    ostatecznie nie ma.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in emails or []:
        email = _normalize_email(str(raw))
        if email and email not in seen:
            seen.add(email)
            normalized.append(email)
    if not normalized:
        raise DomainError(
            "Podaj co najmniej jeden adres e-mail.", "NO_RECIPIENTS", status.HTTP_400_BAD_REQUEST
        )
    if len(normalized) > MAX_INVITATION_EMAILS:
        raise DomainError(
            f"Na raz można wysłać najwyżej {MAX_INVITATION_EMAILS} zaproszeń "
            f"(podano {len(normalized)}). Podziel listę na części.",
            "TOO_MANY_RECIPIENTS",
            status.HTTP_400_BAD_REQUEST,
        )
    district = _require_voivodeship(district, required=False)
    expires_at = timezone.now() + (valid_for or timedelta(days=14))
    link = _committee_registration_link(request)
    taken = _emails_with_committee_account(normalized)

    sent: list[str] = []
    skipped: list[dict] = []
    for email in normalized:
        if email in taken:
            skipped.append({"email": email, "reason": "ma już konto komisji"})
            continue
        _issue_invitation(
            created_by=created_by,
            email=email,
            expires_at=expires_at,
            grants_status=grants_status,
            is_appeals=is_appeals,
            district=district,
            note=note,
            link=link,
            action="invitation.sent",
            actor=created_by,
            request=request,
        )
        sent.append(email)
    return {
        "sent": sent,
        "skipped": skipped,
        "sent_count": len(sent),
        "skipped_count": len(skipped),
        "expires_at": expires_at,
    }


def _invitation_already_used() -> DomainError:
    """Kod, na który ktoś już założył konto, jest faktem – nie ma czego cofać ani powtarzać."""
    return DomainError(
        "To zaproszenie zostało już wykorzystane – konto na tym kodzie istnieje.",
        "INVITATION_USED",
        status.HTTP_409_CONFLICT,
    )


@transaction.atomic
def revoke_invitation(code: InvitationCode, *, actor: User, request=None) -> InvitationCode:
    """Unieważnia kod: od tej chwili rejestracja go nie przyjmie (``InvitationCode.is_usable``).

    Wiersz zostaje, znika tylko możliwość użycia kodu. Skasowanie zaproszenia zabrałoby jedyną
    odpowiedź na pytanie „dlaczego ten adres dostał od nas list” – a to pytanie pada właśnie
    wtedy, gdy zaproszenie poszło pod zły adres.
    """
    locked = InvitationCode.objects.select_for_update().get(pk=code.pk)
    if locked.used_count:
        raise _invitation_already_used()
    if locked.revoked_at is not None:
        raise DomainError(
            "To zaproszenie jest już unieważnione.", "ALREADY_REVOKED", status.HTTP_409_CONFLICT
        )
    locked.revoked_at = timezone.now()
    locked.save(update_fields=["revoked_at"])
    audit(actor, "invitation.revoked", locked, {"email": locked.email, "revoked": True}, request=request)
    return locked


@transaction.atomic
def resend_invitation(code: InvitationCode, *, actor: User, request=None) -> InvitationCode:
    """Unieważnia stary kod i wysyła pod ten sam adres **nowy**, z tymi samymi parametrami.

    Powtórzenie tego samego kodu nie jest możliwe – w bazie jest wyłącznie sha256 – więc „wyślij
    ponownie” zawsze znaczy „wystaw nowy”. Stary od razu przestaje działać: gdyby żył dalej,
    jedna osoba miałaby dwa ważne zaproszenia, a wysłany wcześniej list (ten, który zaginął
    w spamie i bywa, że jednak dojdzie) pozostałby ważnym poświadczeniem.

    Ważność liczymy od nowa, ale **tyle samo**, ile dostał kod pierwotny: ponowienie ma naprawić
    niedostarczony list, a nie po cichu skracać albo wydłużać termin ustalony przez koordynatora.
    """
    locked = InvitationCode.objects.select_for_update().get(pk=code.pk)
    if locked.used_count:
        raise _invitation_already_used()
    if not locked.email:
        raise DomainError(
            "Ten kod nie był wysyłany listem – nie wiadomo, pod jaki adres go powtórzyć.",
            "INVITATION_WITHOUT_EMAIL",
            status.HTTP_400_BAD_REQUEST,
        )
    if _emails_with_committee_account([locked.email]):
        raise DomainError(
            "Ten adres ma już konto komisji – nowy kod nie byłby do niczego potrzebny.",
            "ALREADY_COMMITTEE",
            status.HTTP_409_CONFLICT,
        )
    if locked.revoked_at is None:
        locked.revoked_at = timezone.now()
        locked.save(update_fields=["revoked_at"])
    audit(
        actor,
        "invitation.revoked",
        locked,
        {"email": locked.email, "revoked": True, "reason": "resend"},
        request=request,
    )
    return _issue_invitation(
        created_by=locked.created_by,
        email=locked.email,
        expires_at=timezone.now() + (locked.expires_at - locked.created_at),
        grants_status=locked.grants_status,
        is_appeals=locked.is_appeals,
        district=locked.district,
        note="",
        link=_committee_registration_link(request),
        action="invitation.resent",
        actor=actor,
        request=request,
    )


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

    Województwo z kodu zaproszenia jest nadrzędne wobec deklaracji z formularza: jeśli koordynator
    przypisał kodowi województwo, pole ``district`` z payloadu jest ignorowane, a profil dostaje
    ``district_verified=True``. Kod bez województwa daje profil samodeklarowany – w niczym to
    recenzenta nie ogranicza, bo województwo członka komitetu jest opcjonalne i decyduje wyłącznie
    o konflikcie interesów na etapie wojewódzkim.

    Aktywacja adresu obowiązuje tu **tak samo**, jak przy rejestracji otwartej: kod zaproszenia
    dowodzi, że koordynator kogoś zaprosił, a nie że wpisany adres należy do tej osoby. Status
    ``ACTIVE`` z kodu i aktywacja konta to dwie różne rzeczy – pierwsza daje uprawnienia
    recenzenta, druga wpuszcza do logowania.
    """
    # Województwo z payloadu sprawdzamy przed zużyciem kodu: nieprawidłowa deklaracja nie ma prawa
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
        # Konkurs bierzemy z **kodu**, a nie z kontekstu żądania: to zaproszenie rozstrzyga,
        # do czyjego komitetu ktoś wchodzi. Kod sprzed wydania B konkursu nie ma – wtedy, i tylko
        # wtedy, schodzimy do konkursu bieżącego.
        competition=invitation.competition or default_competition(),
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
    """Role recenzenta (i ewentualnie komisji odwoławczej) w konkursie **tego profilu**.

    Konkurs bierzemy z profilu komitetu, a nie z kontekstu żądania: zatwierdza koordynator, więc
    kontekst jest jego, ale rola dotyczy konkursu, w którym ten recenzent się zgłosił.
    """
    competition = member.competition or default_competition()
    grant_role(member.user, CompetitionRole.REVIEWER, competition=competition)
    if member.is_appeals_committee:
        grant_role(member.user, CompetitionRole.APPEALS, competition=competition)


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
    member: CommitteeMember, *, district: str | None, actor: User, request=None
) -> CommitteeMember:
    """Koordynator ustala województwo członka komitetu – albo je usuwa.

    Województwo członka komitetu jest opcjonalne i służy wyłącznie regule konfliktu interesów na
    etapie wojewódzkim (recenzent nie ocenia prac ze swojego województwa). Dlatego pusta wartość
    jest tu poprawnym wejściem: czyści pole i zdejmuje ``district_verified``, bo nie ma już czego
    potwierdzać – bez tego koordynator nie miałby jak cofnąć województwa wpisanego pomyłkowo.
    """
    district = _require_voivodeship(district, required=False)
    member = CommitteeMember.objects.select_for_update().get(pk=member.pk)
    if member.status != CommitteeStatus.ACTIVE:
        # Województwo ma znaczenie tylko dla kogoś, kto realnie ocenia prace. Ustawianie go
        # profilowi oczekującemu albo zawieszonemu sugerowałoby, że jest on już w puli recenzentów.
        raise DomainError(
            "Województwo ustala się wyłącznie aktywnemu członkowi komitetu.",
            "MEMBER_NOT_ACTIVE",
            status.HTTP_400_BAD_REQUEST,
        )
    previous = member.district
    previously_verified = member.district_verified
    member.district = district
    member.district_verified = district is not None
    member.save(update_fields=["district", "district_verified"])
    audit(
        actor,
        "committee.district_verified",
        member,
        {
            "district": {"from": previous, "to": district},
            "district_verified": {"from": previously_verified, "to": member.district_verified},
        },
        request=request,
    )
    return member


def make_coordinator(user: User, competition=None) -> User:
    """Nadaje rolę koordynatora (używane przez seed/administrację, nie przez API).

    ``competition`` jest opcjonalny, bo wołają stąd komendy startowe (``bootstrap_coordinator``,
    seedy), które chodzą na bazie z jednym konkursem i nie mają po co go wskazywać. Wskazanie
    wprost jest potrzebne dopiero wtedy, gdy konkursów jest więcej niż jeden.
    """
    grant_role(user, CompetitionRole.COORDINATOR, competition=competition or default_competition())
    return user
