"""Wyszukiwarka pytająca o **dwa** wykazy: SIO i słownik organizatora (§ 1.3.3, zadanie T25).

Plik odpowiada na cztery pytania i tylko na nie – reguły dopasowania (tokeny, diakrytyki,
dzielnice, stronicowanie) ma ``test_api.py`` i one się nie zmieniły:

- **co widzi Konkurs #1.** Odpowiedź co do klucza i co do kolejności taka, jak przed etapem 2,
  i ani jedno zapytanie do ``CustomInstitution`` (§ 5.6). To jest tu test najważniejszy: oba
  endpointy są publiczne i chodzi po nich każdy rejestrujący się uczestnik,
- **co widzi konkurs z własnym słownikiem.** Wiersz mówi, z którego wykazu jest (``source``)
  i do której kolumny zapisać dowiązanie (``school_id`` albo ``custom_institution_id``),
- **czego nie widzi nikt.** Rodzaj placówki niedopuszczony w konkursie nie trafia do podpowiedzi –
  ta sama reguła, co w ``apps.accounts.services._resolve_institution``, bo inaczej okno wyboru
  proponowałoby wiersze, które rejestracja i tak odrzuci,
- **czego nie widzi konkurs obok.** Słownik organizatora jest listą jednego konkursu (§ 5.7).
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import RegistrationProfile
from apps.accounts.services import CUSTOM_DIRECTORY_FLAG, REGISTRATION_PROFILE_FLAG
from apps.schools.custom import CustomInstitution
from apps.schools.models import InstitutionType, SchoolKind

from .factories import SchoolFactory

pytestmark = pytest.mark.django_db

URL = "/api/schools/"
CITIES_URL = "/api/schools/cities/"

#: Tabela, o którą bez flagi nie wolno zapytać ani razu.
CUSTOM_TABLE = "schools_custominstitution"

#: Klucze wiersza sprzed etapu 2. Powtórzone tutaj z ``test_api.py`` **świadomie**: tamten test
#: pilnuje kształtu odpowiedzi w ogóle, ten – że dołożenie drugiego wykazu go nie ruszyło.
TODAYS_KEYS = {
    "id",
    "rspo",
    "name",
    "kind",
    "kind_label",
    "city",
    "city_parent",
    "city_label",
    "voivodeship",
}


def enable(competition, *flags) -> None:
    """Włącza flagi zapisem do ``feature_flags`` – tak jak na produkcji, a nie podmianą metody."""
    competition.feature_flags = {**(competition.feature_flags or {}), **{flag: True for flag in flags}}
    competition.save(update_fields=["feature_flags"])


def with_directory(competition, *types) -> RegistrationProfile:
    """Konkurs ze **słownikiem organizatora** i podanymi rodzajami placówek.

    Oba warunki naraz, bo słownik własny wymaga koniunkcji flagi platformy i decyzji konkursu
    (``custom_directory_enabled``), a lista rodzajów placówek chodzi za osobną flagą.
    """
    profile = RegistrationProfile.objects.create(
        competition=competition,
        allow_custom_directory=True,
        allowed_institution_types=[str(value) for value in types],
    )
    enable(competition, CUSTOM_DIRECTORY_FLAG, REGISTRATION_PROFILE_FLAG)
    return profile


def institution(competition, **fields) -> CustomInstitution:
    data = {
        "name": "UNIWERSYTET JAGIELLOŃSKI",
        "city": "Kraków",
        "institution_type": InstitutionType.UNIVERSITY,
    }
    data.update(fields)
    return CustomInstitution.objects.create(competition=competition, **data)


# --- Konkurs #1: bez flagi nic się nie zmienia ---------------------------------------------------


def test_without_the_flag_a_row_keeps_exactly_its_keys(client_for, competition):
    """Punkt 1 listy zamrożonych rzeczy: kształt odpowiedzi publicznego endpointu (§ 0.2)."""
    SchoolFactory(name="XIV LICEUM OGÓLNOKSZTAŁCĄCE", city="Warszawa", kind=SchoolKind.LO)

    row = client_for(competition).get(URL, {"q": "liceum"}).json()["results"][0]

    assert set(row) == TODAYS_KEYS
    assert "source" not in row


def test_without_the_flag_a_city_keeps_exactly_its_keys(client_for, competition):
    SchoolFactory(city="Kraków")

    row = client_for(competition).get(CITIES_URL, {"q": "kra"}).json()["results"][0]

    assert set(row) == {"city", "voivodeship", "voivodeship_label"}


def test_without_the_flag_the_custom_table_is_not_queried_at_all(client_for, competition):
    """Zdanie z § 5.6 dosłownie: zapytanie do ``CustomInstitution`` **nie pada ani razu**.

    Sprawdzamy treść zapytań, a nie ich liczbę: próg liczby przechodziłby także wtedy, gdyby
    zapytanie do drugiej tabeli zastąpiło jakieś inne, a tu chodzi o to, że tej tabeli się nie
    dotyka – wyszukiwarka jest publiczna i chodzi po niej każdy rejestrujący się uczestnik.
    """
    SchoolFactory(name="I LICEUM", city="Kraków")
    institution(competition, name="I LICEUM PARTNERSKIE", city="Kraków")
    client = client_for(competition)

    with CaptureQueriesContext(connection) as queries:
        client.get(URL, {"q": "liceum"})
        client.get(CITIES_URL, {"q": "kra"})

    assert not [entry for entry in queries.captured_queries if CUSTOM_TABLE in entry["sql"]]


def test_without_the_flag_the_organiser_rows_are_invisible(client_for, competition):
    """Wiersz w bazie bez flagi jest niewidoczny – przełącznik jest jeden (§ 0.1)."""
    institution(competition, name="UNIWERSYTET TESTOWY", city="Kraków")

    body = client_for(competition).get(URL, {"q": "uniwersytet"}).json()

    assert body["results"] == []
    assert body["has_more"] is False


# --- konkurs z dwoma wykazami --------------------------------------------------------------------


def test_each_row_says_where_it_comes_from(client_for, competition):
    """Wiersz niesie źródło **i** nazwę kolumny dowiązania – po to, żeby front nie zgadywał."""
    with_directory(competition, InstitutionType.SECONDARY, InstitutionType.UNIVERSITY)
    school = SchoolFactory(name="LICEUM MIEJSKIE", city="Kraków")
    row = institution(competition, name="LICEUM PARTNERSKIE", city="Kraków")

    results = client_for(competition).get(URL, {"q": "liceum"}).json()["results"]

    # Najpierw wykaz publiczny, potem słownik organizatora – tam trafia większość pytających.
    assert [entry["source"] for entry in results] == ["sio", "custom"]
    assert results[0]["school_id"] == school.pk
    assert "custom_institution_id" not in results[0]
    assert results[1]["custom_institution_id"] == row.pk
    assert results[1]["institution_type_label"] == "uczelnia wyższa"
    assert "school_id" not in results[1]


def test_the_public_row_keeps_every_key_it_had(client_for, competition):
    """Dopisek jest **dopiskiem**: żaden dzisiejszy klucz nie znika i nie zmienia znaczenia."""
    with_directory(competition, InstitutionType.SECONDARY)
    SchoolFactory(name="LICEUM MIEJSKIE", city="Warszawa")

    row = client_for(competition).get(URL, {"q": "liceum"}).json()["results"][0]

    assert TODAYS_KEYS < set(row)
    assert set(row) - TODAYS_KEYS == {"school_id", "source"}


def test_the_organiser_row_does_not_pretend_to_have_an_rspo(client_for, competition):
    """Numeru z rejestru ministerialnego ta placówka nie ma i nie udaje, że ma."""
    with_directory(competition, InstitutionType.UNIVERSITY)
    institution(competition, name="UNIWERSYTET PARTNERSKI", city="Kraków")

    row = client_for(competition).get(URL, {"q": "uniwersytet"}).json()["results"][0]

    assert "rspo" not in row
    assert "kind" not in row
    assert row["institution_type"] == InstitutionType.UNIVERSITY


def test_the_search_rules_are_the_same_in_both_directories(client_for, competition):
    """„lodz” znajduje „Łódź” w obu wykazach – jedna normalizacja, jedno zachowanie okna."""
    with_directory(competition, InstitutionType.SECONDARY, InstitutionType.UNIVERSITY)
    SchoolFactory(name="LICEUM PIERWSZE", city="Łódź")
    institution(competition, name="OŚRODEK ŁÓDZKI", city="Łódź", institution_type=InstitutionType.UNIVERSITY)

    results = client_for(competition).get(URL, {"q": "lodz"}).json()["results"]

    assert [row["name"] for row in results] == ["LICEUM PIERWSZE", "OŚRODEK ŁÓDZKI"]


def test_the_city_narrows_both_directories(client_for, competition):
    with_directory(competition, InstitutionType.SECONDARY, InstitutionType.UNIVERSITY)
    institution(competition, name="OŚRODEK KRAKOWSKI", city="Kraków")
    institution(competition, name="OŚRODEK GDAŃSKI", city="Gdańsk")

    results = client_for(competition).get(URL, {"q": "", "city": "Kraków"}).json()["results"]

    assert [row["name"] for row in results] == ["OŚRODEK KRAKOWSKI"]


# --- rodzaje placówek ----------------------------------------------------------------------------


def test_a_type_the_competition_does_not_allow_is_not_suggested(client_for, competition):
    """Podpowiedź, której rejestracja by nie przyjęła, jest zaproszeniem do odmowy.

    ``_resolve_custom_institution`` odrzuca wiersz o rodzaju spoza dopuszczonych – wyszukiwarka
    ma odrzucać go **wcześniej**, czyli w ogóle go nie pokazywać.
    """
    with_directory(competition, InstitutionType.SECONDARY)
    institution(competition, name="UNIWERSYTET PARTNERSKI", city="Kraków")

    results = client_for(competition).get(URL, {"q": "uniwersytet"}).json()["results"]

    assert results == []


def test_the_public_registry_is_narrowed_by_the_allowed_types(client_for, competition):
    with_directory(competition, InstitutionType.SECONDARY)
    SchoolFactory(name="SZKOŁA PODSTAWOWA NR 1", city="Kraków", institution_type=InstitutionType.PRIMARY)
    SchoolFactory(name="SZKOŁA PONADPODSTAWOWA NR 1", city="Kraków")

    results = client_for(competition).get(URL, {"q": "szkola"}).json()["results"]

    assert [row["name"] for row in results] == ["SZKOŁA PONADPODSTAWOWA NR 1"]


def test_the_institution_type_parameter_narrows_the_list(client_for, competition):
    """Parametr z listy wyboru w formularzu – zawęża w granicach tego, co konkurs dopuszcza."""
    with_directory(competition, InstitutionType.PRIMARY, InstitutionType.SECONDARY)
    SchoolFactory(name="SZKOŁA PODSTAWOWA NR 1", city="Kraków", institution_type=InstitutionType.PRIMARY)
    SchoolFactory(name="SZKOŁA PONADPODSTAWOWA NR 1", city="Kraków")

    body = client_for(competition).get(URL, {"q": "szkola", "institution_type": "PRIMARY"}).json()

    assert [row["name"] for row in body["results"]] == ["SZKOŁA PODSTAWOWA NR 1"]


def test_a_type_outside_the_competition_is_ignored_rather_than_fatal(client_for, competition):
    """Śmieć w adresie daje listę domyślną, a nie 400 – tak samo jak przy ``voivodeship``."""
    with_directory(competition, InstitutionType.SECONDARY)
    SchoolFactory(name="LICEUM MIEJSKIE", city="Kraków")

    body = client_for(competition).get(URL, {"q": "liceum", "institution_type": "UNIVERSITY"}).json()

    assert [row["name"] for row in body["results"]] == ["LICEUM MIEJSKIE"]


def test_a_type_without_rows_in_the_registry_gives_nothing_from_it(client_for, competition):
    """„Placówka poza Polską” nie jest pozycją wykazu SIO – lista z niego ma być pusta.

    Pusta, a nie „cała”: warunek ``institution_type IN ()`` jest tu treścią, bo brak zawężenia
    znaczyłby, że wybór „poza Polską” pokazuje wszystkie polskie szkoły.
    """
    with_directory(competition, InstitutionType.SECONDARY, InstitutionType.FOREIGN)
    SchoolFactory(name="LICEUM MIEJSKIE", city="Kraków")
    institution(
        competition, name="LICEUM W BERLINIE", city="Berlin", institution_type=InstitutionType.FOREIGN
    )

    results = (
        client_for(competition).get(URL, {"q": "liceum", "institution_type": "FOREIGN"}).json()["results"]
    )

    assert [row["name"] for row in results] == ["LICEUM W BERLINIE"]


# --- stronicowanie -------------------------------------------------------------------------------


def test_the_organiser_rows_ride_on_the_first_page_only(client_for, competition):
    """``offset`` przewija **wykaz publiczny**; słownik organizatora jedzie raz i w całości.

    Inaczej doczytana strona dokładałaby te same wiersze po raz drugi – klient skleja strony,
    a nie porównuje ich zawartości.
    """
    with_directory(competition, InstitutionType.SECONDARY, InstitutionType.UNIVERSITY)
    SchoolFactory(name="LICEUM PIERWSZE", city="Kraków")
    SchoolFactory(name="LICEUM DRUGIE", city="Kraków")
    institution(competition, name="LICEUM PARTNERSKIE", city="Kraków")

    client = client_for(competition)
    first = client.get(URL, {"q": "liceum", "limit": 1}).json()
    second = client.get(URL, {"q": "liceum", "limit": 1, "offset": 1}).json()

    assert [row["source"] for row in first["results"]] == ["sio", "custom"]
    assert first["has_more"] is True
    assert [row["source"] for row in second["results"]] == ["sio"]


# --- miejscowości --------------------------------------------------------------------------------


def test_cities_merge_both_directories(client_for, competition):
    with_directory(competition, InstitutionType.SECONDARY, InstitutionType.UNIVERSITY)
    SchoolFactory(city="Kraków")
    institution(competition, name="OŚRODEK", city="Krasnystaw", institution_type=InstitutionType.UNIVERSITY)

    results = client_for(competition).get(CITIES_URL, {"q": "kra"}).json()["results"]

    assert [(row["city"], row["source"]) for row in results] == [
        ("Kraków", "sio"),
        ("Krasnystaw", "custom"),
    ]


def test_a_city_known_to_both_directories_is_one_suggestion(client_for, competition):
    """Dwa razy „Kraków” na liście byłoby dla człowieka tą samą pozycją dwa razy."""
    with_directory(competition, InstitutionType.SECONDARY, InstitutionType.UNIVERSITY)
    SchoolFactory(city="Kraków")
    institution(competition, name="OŚRODEK", city="KRAKÓW", institution_type=InstitutionType.UNIVERSITY)

    results = client_for(competition).get(CITIES_URL, {"q": "kra"}).json()["results"]

    assert [row["city"] for row in results] == ["Kraków"]


def test_an_organiser_city_has_no_voivodeship_and_that_is_not_an_error(client_for, competition):
    """Ta tabela opisuje położenie kodem regionu konkursu, a nie listą szesnastu (§ 1.3.3)."""
    with_directory(competition, InstitutionType.UNIVERSITY)
    institution(competition, name="OŚRODEK", city="Krasnystaw")

    row = client_for(competition).get(CITIES_URL, {"q": "kras"}).json()["results"][0]

    assert row["voivodeship"] == ""
    assert row["voivodeship_label"] == ""


# --- izolacja (§ 5.7) ----------------------------------------------------------------------------


def test_the_directory_of_the_other_competition_is_invisible(client_for, competition, other_competition):
    """Lista placówek organizatora jest listą jego kontrahentów – z cudzej domeny nie ma jej wcale."""
    with_directory(competition, InstitutionType.SECONDARY, InstitutionType.UNIVERSITY)
    with_directory(other_competition, InstitutionType.SECONDARY, InstitutionType.UNIVERSITY)
    institution(other_competition, name="UNIWERSYTET CUDZY", city="Kraków")

    results = client_for(competition).get(URL, {"q": "uniwersytet"}).json()["results"]

    assert results == []
