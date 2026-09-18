"""``RegistrationProfile``: co konkurs pyta przy rejestracji i co dopuszcza jako placówkę (§ 1.3.4).

Przedmiotem są cztery reguły, a nie szczegóły wykonania:

- **brak wiersza znaczy „jak dziś”.** Konkurs #1 profilu nie ma i mieć nie będzie (migracja
  ``accounts.0028`` nie zakłada go nikomu), a mimo to rejestracja ma przyjmować dokładnie to samo,
  co przyjmowała przed etapem 2 – szkołę ponadpodstawową z wykazu albo wpisaną ręcznie, klasę 1–5,
  województwo z listy;
- **przy wyłączonej fladze ``institution_types`` nie pada ani jedno zapytanie.** Rejestracja jest
  ekranem, po którym chodzi każdy uczestnik, a budżet ``/register/`` nie rośnie w żadnym wydaniu
  etapu 2 (§ 5.6). Wiersz w bazie **też** nie wystarczy, żeby cokolwiek zmienić: flaga jest
  jedynym przełącznikiem (§ 0.1);
- **placówka spoza wykazu ma jedną drogę do bazy.** Nazwa wpisana wolnym tekstem kopiuje się do
  ``Participant.school``, tak jak dziś kopiuje się nazwa z rejestru – żeby publikacja wyników
  i próg k-anonimowości nie musiały wiedzieć, którą drogą uczestnik się zarejestrował;
- **podział terytorialny.** Przy włączonej fladze ``custom_regions`` rejestracja zapisuje
  ``Participant.region``, a ``district`` zostaje jego kopią (§ 1.4.2); przy wyłączonej zapis jest
  dokładnie dzisiejszy i nie kosztuje ani jednego zapytania więcej.
"""

import pytest

from apps.accounts.models import (
    DIRECTORY_INSTITUTION_TYPES,
    MAX_GRADE,
    MIN_GRADE,
    Region,
    RegistrationProfile,
)
from apps.accounts.services import (
    CUSTOM_DIRECTORY_FLAG,
    CUSTOM_REGIONS_FLAG,
    REGISTRATION_PROFILE_FLAG,
    allowed_institution_types,
    custom_directory_enabled,
    register_participant,
    registration_profile,
)
from apps.core.api import DomainError
from apps.schools.models import InstitutionType
from apps.schools.tests.factories import SchoolFactory

pytestmark = pytest.mark.django_db


def enable(competition, *flags) -> None:
    """Włącza flagi zapisem do ``feature_flags``, a nie podmianą ``has_feature``.

    Przełącznik ma być sprawdzany tam, gdzie stoi na produkcji – w danych konkursu. Obiekt jest
    tym samym, który wiąże kontekst testu (``conftest._bind_competition``), więc serwis czytający
    ``current_competition()`` widzi zmianę bez ponownego pobrania.
    """
    competition.feature_flags = {**(competition.feature_flags or {}), **{flag: True for flag in flags}}
    competition.save(update_fields=["feature_flags"])


def profile_for(competition, **fields) -> RegistrationProfile:
    return RegistrationProfile.objects.create(competition=competition, **fields)


def kwargs(**overrides) -> dict:
    data = {
        "email": "uczen@example.test",
        "password": "Poprawne-Haslo-2026",
        "first_name": "Anna",
        "last_name": "Nowak",
        "district": "mazowieckie",
        "birth_year": 2008,
        "grade": 2,
        "phone": "600 100 200",
        "school": "LO nr 1",
        "terms_consent": True,
        "gdpr_consent": True,
        "guardian_consent": True,
    }
    data.update(overrides)
    return data


# --- model ---------------------------------------------------------------------------------------


def test_defaults_reproduce_todays_registration():
    """Wiersz z samymi domyślnymi pyta o to, o co pyta dzisiejszy formularz – i mówi to wprost."""
    profile = RegistrationProfile()

    assert profile.institution_types() == (InstitutionType.SECONDARY,)
    assert profile.directory_types() == (InstitutionType.SECONDARY,)
    assert profile.grade_range() == (MIN_GRADE, MAX_GRADE)
    assert profile.is_default() is True


def test_the_directory_types_are_exactly_the_ones_with_rows_in_the_registry():
    """Trzy rodzaje mają wiersze w ``School``, trzy są sytuacjami uczestnika (§ 1.3.2).

    Test pilnuje krotki przepisanej do ``apps.accounts.models``: gdyby ktoś dołożył rodzaj
    placówki, ma tu stanąć decyzja, czy wchodzi on do wykazu, czy do wolnego tekstu – a nie ciche
    „nie ma go w żadnej z list”.
    """
    assert set(DIRECTORY_INSTITUTION_TYPES) < set(InstitutionType.values)
    assert set(InstitutionType.values) - set(DIRECTORY_INSTITUTION_TYPES) == {
        InstitutionType.FOREIGN,
        InstitutionType.NONE,
        InstitutionType.OTHER,
    }


def test_the_order_of_types_comes_from_the_declaration_not_from_the_column():
    profile = RegistrationProfile(
        allowed_institution_types=[InstitutionType.OTHER, "NIE-MA-TAKIEGO", InstitutionType.PRIMARY]
    )

    assert profile.institution_types() == (InstitutionType.PRIMARY, InstitutionType.OTHER)
    assert profile.directory_types() == (InstitutionType.PRIMARY,)


def test_an_empty_list_of_types_means_secondary_only():
    """Pusty zbiór dopuszczonych placówek zamknąłby rejestrację – to byłaby awaria, nie konfiguracja."""
    assert RegistrationProfile(allowed_institution_types=[]).institution_types() == (
        InstitutionType.SECONDARY,
    )


# --- odczyt profilu ------------------------------------------------------------------------------


def test_without_the_flag_the_profile_is_the_default_and_costs_no_query(
    competition, django_assert_num_queries
):
    """Wiersz w bazie **jest**, a mimo to nie zmienia niczego i nie jest czytany.

    To jest cała reguła § 0.1: przełącznik jest jeden i jest flagą konkursu. Zapytanie liczy się
    tu podwójnie – ``/register/`` ma dziś swój budżet i etap 2 go nie podnosi (§ 5.6).
    """
    profile_for(competition, allowed_institution_types=[InstitutionType.UNIVERSITY])

    with django_assert_num_queries(0):
        profile = registration_profile(competition)

    assert profile.pk is None
    assert profile.is_default() is True
    assert allowed_institution_types(competition) == (InstitutionType.SECONDARY,)


def test_with_the_flag_the_row_decides(competition):
    profile_for(
        competition,
        allowed_institution_types=[InstitutionType.UNIVERSITY, InstitutionType.FOREIGN],
        allow_foreign=True,
        grade_min=1,
        grade_max=8,
    )
    enable(competition, REGISTRATION_PROFILE_FLAG)

    profile = registration_profile(competition)

    assert profile.pk is not None
    assert profile.is_default() is False
    assert allowed_institution_types(competition) == (InstitutionType.UNIVERSITY, InstitutionType.FOREIGN)
    assert profile.grade_range() == (1, 8)


def test_the_flag_without_a_row_still_means_todays_rules(competition):
    """Migracja nie zakłada wiersza nikomu – brak wiersza ma znaczyć „jak dziś”, a nie „pustka”."""
    enable(competition, REGISTRATION_PROFILE_FLAG)

    assert registration_profile(competition).is_default() is True


def test_the_profile_of_one_competition_is_invisible_to_the_other(competition, other_competition):
    profile_for(competition, allow_foreign=True)

    assert not RegistrationProfile.objects.for_competition(other_competition).exists()
    assert RegistrationProfile.objects.for_competition(competition).count() == 1


def test_the_custom_directory_needs_both_the_flag_and_the_row(competition, django_assert_num_queries):
    """Koniunkcja, a nie suma: flaga mówi „ta zdolność istnieje”, wiersz – „ten konkurs jej używa”."""
    profile_for(competition, allow_custom_directory=True)

    with django_assert_num_queries(0):
        assert custom_directory_enabled(competition) is False

    # Sama flaga słownika wystarczy: to osobna zdolność i konkurs, który chce wyłącznie własnego
    # wykazu placówek, nie musi przy okazji włączać rodzajów placówek.
    enable(competition, CUSTOM_DIRECTORY_FLAG)

    assert custom_directory_enabled(competition) is True
    assert registration_profile(competition).is_default() is True


# --- rejestracja ---------------------------------------------------------------------------------


def test_registration_without_a_profile_is_unchanged(competition, open_registration):
    """Konkurs #1: bez wiersza i bez flagi rejestracja zapisuje dokładnie to, co dziś."""
    school = SchoolFactory(name="XIV LICEUM OGÓLNOKSZTAŁCĄCE")

    participant = register_participant(**kwargs(school_id=school.id, school=""))

    assert participant.school == school.name
    assert participant.school_ref_id == school.id
    assert participant.institution_name == ""
    assert participant.country == ""
    assert participant.region_id is None
    assert participant.district == "mazowieckie"


def test_a_foreign_participant_gets_the_name_copied_into_school(competition, open_registration):
    """Nazwa placówki idzie **także** do ``school`` – tak jak dziś idzie nazwa z rejestru."""
    profile_for(
        competition,
        allowed_institution_types=[InstitutionType.SECONDARY, InstitutionType.FOREIGN],
        allow_foreign=True,
    )
    enable(competition, REGISTRATION_PROFILE_FLAG)

    participant = register_participant(
        **kwargs(
            school="",
            institution_type=InstitutionType.FOREIGN,
            institution_name="Gymnasium Berlin",
            country="de",
        )
    )

    assert participant.institution_name == "Gymnasium Berlin"
    assert participant.school == "Gymnasium Berlin"
    assert participant.school_ref_id is None
    # Kod kraju normalizujemy do wielkich liter – ISO 3166-1 zna jeden zapis, a filtry i eksporty
    # porównują ten napis wprost.
    assert participant.country == "DE"


def test_a_foreign_participant_must_say_which_country(competition, open_registration):
    profile_for(
        competition,
        allowed_institution_types=[InstitutionType.FOREIGN],
        allow_foreign=True,
    )
    enable(competition, REGISTRATION_PROFILE_FLAG)

    with pytest.raises(DomainError) as error:
        register_participant(**kwargs(school="", institution_name="Gymnasium Berlin"))

    assert error.value.machine_code == "COUNTRY_REQUIRED"


def test_no_school_at_all_is_an_answer_not_a_gap(competition, open_registration):
    profile_for(competition, allowed_institution_types=[InstitutionType.NONE])
    enable(competition, REGISTRATION_PROFILE_FLAG)

    participant = register_participant(**kwargs(school=""))

    assert participant.school == ""
    assert participant.school_ref_id is None
    assert participant.institution_name == ""


def test_a_type_outside_the_profile_is_refused(competition, open_registration):
    profile_for(competition, allowed_institution_types=[InstitutionType.SECONDARY])
    enable(competition, REGISTRATION_PROFILE_FLAG)

    with pytest.raises(DomainError) as error:
        register_participant(**kwargs(institution_type=InstitutionType.UNIVERSITY))

    assert error.value.machine_code == "INSTITUTION_TYPE_NOT_ALLOWED"


def test_a_school_of_a_type_the_competition_does_not_allow_is_refused(competition, open_registration):
    """Rodzaj sprawdzamy na **już pobranym** wierszu – zawężenie podpowiedzi to za mało.

    Wyszukiwarka zawęża listę, ale ``school_id`` przychodzi z formularza i da się je podmienić.
    Bramka jest w serwisie, bo tam kończą się wszystkie cztery drogi rejestracji.
    """
    profile_for(competition, allowed_institution_types=[InstitutionType.UNIVERSITY])
    enable(competition, REGISTRATION_PROFILE_FLAG)
    school = SchoolFactory(institution_type=InstitutionType.SECONDARY)

    with pytest.raises(DomainError) as error:
        register_participant(**kwargs(school="", school_id=school.id))

    assert error.value.machine_code == "INSTITUTION_TYPE_NOT_ALLOWED"


def test_a_competition_can_close_the_free_text_door(competition, open_registration):
    profile_for(competition, allow_free_text_school=False)
    enable(competition, REGISTRATION_PROFILE_FLAG)

    with pytest.raises(DomainError) as error:
        register_participant(**kwargs(school="Szkoła spoza wykazu"))

    assert error.value.machine_code == "SCHOOL_REQUIRED"
    assert str(error.value.detail) == "Wybierz placówkę z listy."


def test_the_grade_range_comes_from_the_profile(competition, open_registration):
    profile_for(competition, grade_min=1, grade_max=8)
    enable(competition, REGISTRATION_PROFILE_FLAG)

    participant = register_participant(**kwargs(grade=7))

    assert participant.grade == 7


def test_the_grade_message_names_the_range_of_this_competition(competition, open_registration):
    profile_for(competition, grade_min=1, grade_max=8)
    enable(competition, REGISTRATION_PROFILE_FLAG)

    with pytest.raises(DomainError) as error:
        register_participant(**kwargs(grade=9))

    assert str(error.value.detail) == "Podaj klasę (1–8)."


def test_todays_grade_message_is_unchanged(competition, open_registration):
    """Konkurs #1 słyszy dokładnie to zdanie, co przed etapem 2 – razem z zakresem 1–5."""
    with pytest.raises(DomainError) as error:
        register_participant(**kwargs(grade=9))

    assert str(error.value.detail) == f"Podaj klasę ({MIN_GRADE}–{MAX_GRADE})."


def test_a_competition_can_stop_asking_for_the_grade(competition, open_registration):
    profile_for(competition, require_grade=False)
    enable(competition, REGISTRATION_PROFILE_FLAG)

    participant = register_participant(**kwargs(grade=None))

    assert participant.grade is None


# --- region (T18/T19: zapis regionu przy rejestracji) ---------------------------------------------


def test_without_the_regions_flag_registration_writes_no_region(
    competition, open_registration, django_assert_max_num_queries
):
    """Dzisiejszy zapis co do joty: ``district`` z listy województw, ``region`` puste.

    Próg zapytań jest tu po to, żeby odczyt regionu nie wszedł „przy okazji”: bez flagi żadne
    dodatkowe zapytanie paść nie ma prawa, a rejestracja jest ścieżką, którą przechodzi każdy
    uczestnik.
    """
    with django_assert_max_num_queries(40):
        participant = register_participant(**kwargs())

    assert participant.region_id is None
    assert participant.district == "mazowieckie"


def test_with_the_regions_flag_the_voivodeship_also_fills_the_region(competition, open_registration):
    """Region bierze się z województwa przez ``region_for_district`` – jednym złączeniem po kodzie."""
    enable(competition, CUSTOM_REGIONS_FLAG)

    participant = register_participant(**kwargs(district="mazowieckie"))

    assert participant.region is not None
    assert participant.region.code == "mazowieckie"
    # ``district`` zostaje **kopią** kodu regionu: to z niej czytają filtry, eksporty i tabele
    # wyników, których etap 2 nie dotyka (§ 1.4.2).
    assert participant.district == "mazowieckie"


def test_a_region_code_outside_the_voivodeships_fills_the_district_from_the_region(
    competition, open_registration
):
    """Konkurs z własnym podziałem podaje kod regionu, a ``district`` jest jego kopią."""
    enable(competition, CUSTOM_REGIONS_FLAG)
    Region.objects.create(competition=competition, code="okreg-poludniowy", name="Okręg południowy")

    participant = register_participant(**kwargs(district="", region="okreg-poludniowy"))

    assert participant.region.code == "okreg-poludniowy"
    assert participant.district == "okreg-poludniowy"


def test_an_unknown_region_falls_back_to_the_voivodeship_rule(competition, open_registration):
    """Flaga włączona, ale kod nierozpoznany – zachowanie schodzi do dzisiejszej listy województw."""
    enable(competition, CUSTOM_REGIONS_FLAG)

    with pytest.raises(DomainError) as error:
        register_participant(**kwargs(district="atlantyda"))

    assert error.value.machine_code == "DISTRICT_INVALID"
