"""Słownik własny organizatora: model, izolacja między konkursami i wyszukiwarka (§ 1.3.3).

Import CSV ma własny plik (``test_custom_institutions_import.py``) – tutaj chodzi o cztery
zdania, które muszą być prawdziwe niezależnie od tego, skąd wiersz się wziął:

- **wykaz publiczny zostaje nietknięty.** ``CustomInstitution`` jest osobną tabelą i żadna droga
  nie dopisuje wiersza do ``School`` (decyzja D2 etapu 1),
- **wiersze konkursu A są niewidoczne z konkursu B** – reguła § 5.7 dla nowego modelu,
- **kolumny wyliczane liczy ta sama funkcja, co dla ``School``** (``normalise.derived_fields``),
  więc „lodz” znajduje „Łódź” w obu wykazach tak samo,
- **bez flagi nie pada ani jedno zapytanie** do tej tabeli (§ 5.6).
"""

import pytest

from apps.accounts.models import Region, RegistrationProfile
from apps.accounts.services import CUSTOM_DIRECTORY_FLAG
from apps.schools.custom import CustomInstitution, search_custom_institutions
from apps.schools.models import InstitutionType, School

pytestmark = pytest.mark.django_db


def enable(competition, *flags) -> None:
    """Włącza flagi zapisem do ``feature_flags`` – tak jak na produkcji, a nie podmianą metody."""
    competition.feature_flags = {**(competition.feature_flags or {}), **{flag: True for flag in flags}}
    competition.save(update_fields=["feature_flags"])


def with_directory(competition):
    """Konkurs z **oboma** warunkami słownika własnego: flagą platformy i decyzją konkursu."""
    RegistrationProfile.objects.create(competition=competition, allow_custom_directory=True)
    enable(competition, CUSTOM_DIRECTORY_FLAG)
    return competition


def institution(competition, **fields) -> CustomInstitution:
    data = {
        "name": "UNIWERSYTET JAGIELLOŃSKI",
        "city": "Kraków",
        "institution_type": InstitutionType.UNIVERSITY,
    }
    data.update(fields)
    return CustomInstitution.objects.create(competition=competition, **data)


# --- model ---------------------------------------------------------------------------------------


def test_the_public_register_is_not_touched(competition):
    """Wiersz słownika własnego nie pojawia się w ``School`` – to są dwie tabele, nie jedna."""
    institution(competition)
    assert School.objects.count() == 0


def test_derived_columns_are_computed_on_save(competition):
    """``search_text`` i ``city_search`` liczy ``save()``, tą samą funkcją, co dla ``School``."""
    row = institution(competition, name="Liceum Świętej Łucji", city="Łódź")
    assert row.search_text == "liceum swietej lucji lodz"
    assert row.city_search == "lodz"


def test_the_external_id_is_unique_within_the_competition_only(competition, other_competition):
    """Dwaj organizatorzy mogą numerować swoje wykazy od jedynki i nie jest to ten sam wiersz."""
    institution(competition, external_id="1")
    institution(other_competition, external_id="1")
    assert CustomInstitution.objects.filter(external_id="1").count() == 2


def test_rows_without_an_external_id_do_not_collide(competition):
    """Pusty identyfikator znaczy „nie mam”, a nie „ten sam” – więz ma warunek ``~Q('')``."""
    institution(competition, name="Ośrodek A")
    institution(competition, name="Ośrodek B")
    assert CustomInstitution.objects.count() == 2


def test_the_region_is_a_code_not_a_foreign_key(competition):
    """Kod regionu spoza podziału konkursu zostaje przy wierszu – kolumna jest napisem (§ 1.3.3).

    Wiersz z wykazu organizatora bywa opisany podziałem, którego konkurs jeszcze nie ma; klucz
    obcy kazałby go odrzucić albo zgubić tę informację.
    """
    row = institution(competition, region_code="okreg-poludniowy")
    assert row.region_code == "okreg-poludniowy"
    assert not hasattr(row, "region_id")


def test_a_region_of_the_competition_is_written_by_its_code(competition):
    """Regiony konkursu zakłada migracja ``accounts.0026`` – w wierszu stoi ich ``code``."""
    region = Region.objects.for_competition(competition).get(code="malopolskie")
    row = institution(competition, region_code=region.code)
    assert row.region_code == "malopolskie"


# --- izolacja (§ 5.7) ----------------------------------------------------------------------------


def test_rows_of_a_are_invisible_to_b(competition, other_competition):
    """Reguła krzyżowa dla nowego modelu: wiersz konkursu A nie istnieje w zapytaniu konkursu B."""
    institution(competition, name="Ośrodek Konkursu A")
    institution(other_competition, name="Ośrodek Konkursu B")
    visible = CustomInstitution.objects.for_competition(competition)
    assert [row.name for row in visible] == ["Ośrodek Konkursu A"]
    assert not visible.filter(competition=other_competition).exists()


def test_the_search_of_b_never_returns_rows_of_a(competition, other_competition):
    """Wyciek listy kontrahentów organizatora – to jest ta odpowiedź, której nie wolno oddać."""
    institution(competition, name="OŚRODEK BADAWCZY", city="Kraków")
    with_directory(other_competition)
    assert search_custom_institutions(other_competition, "osrodek") == []


# --- wyszukiwarka --------------------------------------------------------------------------------


def test_the_directory_is_not_queried_without_the_flag(competition, django_assert_num_queries):
    """Bez ``custom_school_directory`` zapytanie do ``CustomInstitution`` nie pada ani razu (§ 5.6)."""
    institution(competition)
    with django_assert_num_queries(0):
        assert search_custom_institutions(competition, "uniwersytet") == []


def test_the_row_in_the_database_is_not_enough_without_the_flag(competition):
    """Sam wiersz profilu niczego nie włącza – flaga jest jedynym przełącznikiem (§ 0.1)."""
    institution(competition)
    RegistrationProfile.objects.create(competition=competition, allow_custom_directory=True)
    assert search_custom_institutions(competition, "uniwersytet") == []


def test_the_search_folds_diacritics_like_the_public_register(competition):
    """„lodz” znajduje „Łódź” – ta sama normalizacja, co w ``apps.schools.api.search_schools``."""
    with_directory(competition)
    institution(competition, name="Ośrodek Fizyki", city="Łódź")
    assert [row.city for row in search_custom_institutions(competition, "lodz")] == ["Łódź"]


def test_the_tokens_of_the_query_are_a_conjunction(competition):
    """„fizyki krakow” trafia w wiersz, w którym jeden wyraz jest w nazwie, a drugi w mieście."""
    with_directory(competition)
    institution(competition, name="Ośrodek Fizyki", city="Kraków")
    institution(competition, name="Ośrodek Fizyki", city="Gdańsk")
    assert [row.city for row in search_custom_institutions(competition, "fizyki krakow")] == ["Kraków"]


def test_a_query_shorter_than_the_threshold_returns_nothing(competition):
    """Ten sam próg, co w wykazie publicznym: dwie litery nie zawężają niczego."""
    with_directory(competition)
    institution(competition, name="Ośrodek Fizyki")
    assert search_custom_institutions(competition, "o") == []


def test_inactive_rows_are_not_suggested(competition):
    """Wygaszona placówka zostaje przy profilach sprzed wygaszenia, ale nie wraca do podpowiedzi."""
    with_directory(competition)
    institution(competition, name="Ośrodek Zamknięty", is_active=False)
    assert search_custom_institutions(competition, "osrodek") == []


def test_the_search_narrows_to_the_allowed_institution_types(competition):
    """Wiersz rodzaju, którego konkurs nie dopuszcza, nie ma jak trafić do podpowiedzi."""
    with_directory(competition)
    institution(competition, name="Uczelnia Testowa", institution_type=InstitutionType.UNIVERSITY)
    institution(competition, name="Uczelnia Inna", institution_type=InstitutionType.OTHER)
    found = search_custom_institutions(
        competition, "uczelnia", institution_types=(InstitutionType.UNIVERSITY,)
    )
    assert [row.name for row in found] == ["Uczelnia Testowa"]


def test_the_limit_never_exceeds_the_ceiling_of_the_public_search(competition):
    """Sufit jest ten sam, co w ``apps.schools.api`` – jedno okno, jedna maksymalna lista."""
    with_directory(competition)
    for number in range(25):
        institution(competition, name=f"Ośrodek numer {number:02d}")
    assert len(search_custom_institutions(competition, "osrodek", limit=100)) == 20
