"""``GET /api/schools/`` i ``GET /api/schools/cities/`` – wyszukiwarka słownika dla rejestracji."""

import pytest
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from apps.accounts.models import Voivodeship
from apps.schools.api import MAX_RESULTS, CitySearchView, SchoolSearchView
from apps.schools.models import SchoolKind

from .factories import SchoolFactory

URL = "/api/schools/"
CITIES_URL = "/api/schools/cities/"


@pytest.fixture
def api():
    return APIClient()


@pytest.mark.django_db
def test_search_is_accent_insensitive(api):
    SchoolFactory(name="XXI LICEUM OGÓLNOKSZTAŁCĄCE", city="Łódź", voivodeship=Voivodeship.LODZKIE)

    body = api.get(URL, {"q": "lodz"}).json()

    assert [row["city"] for row in body["results"]] == ["Łódź"]


@pytest.mark.django_db
def test_every_token_must_match(api):
    SchoolFactory(name="LICEUM IM. MICKIEWICZA", city="Kraków")
    SchoolFactory(name="LICEUM IM. MICKIEWICZA", city="Gdańsk")

    body = api.get(URL, {"q": "mickiewicza krakow"}).json()

    assert [row["city"] for row in body["results"]] == ["Kraków"]


@pytest.mark.django_db
def test_search_is_narrowed_by_voivodeship(api):
    SchoolFactory(name="LICEUM NAD RZEKĄ", city="Płock", voivodeship=Voivodeship.MAZOWIECKIE)
    SchoolFactory(name="LICEUM NAD RZEKĄ", city="Opole", voivodeship=Voivodeship.OPOLSKIE)

    body = api.get(URL, {"q": "liceum nad", "voivodeship": Voivodeship.OPOLSKIE}).json()

    assert [row["city"] for row in body["results"]] == ["Opole"]


@pytest.mark.django_db
def test_unknown_voivodeship_is_ignored_rather_than_fatal(api):
    """Wartość spoza listy nie może wywracać podpowiedzi – to parametr wygody, nie autoryzacja."""
    SchoolFactory(name="LICEUM NAD RZEKĄ", city="Płock")

    body = api.get(URL, {"q": "liceum nad", "voivodeship": "mazowsze"}).json()

    assert len(body["results"]) == 1


@pytest.mark.django_db
def test_inactive_schools_are_not_suggested(api):
    SchoolFactory(name="LICEUM ZLIKWIDOWANE", city="Kielce", is_active=False)

    assert api.get(URL, {"q": "liceum zlikwidowane"}).json()["results"] == []


@pytest.mark.django_db
def test_too_short_query_returns_nothing(api):
    SchoolFactory(name="LICEUM", city="Kielce")

    assert api.get(URL, {"q": "l"}).json()["results"] == []
    assert api.get(URL, {}).json()["results"] == []


@pytest.mark.django_db
def test_results_are_capped_even_when_a_bigger_limit_is_asked_for(api):
    SchoolFactory.create_batch(MAX_RESULTS + 5, city="Kielce")

    body = api.get(URL, {"q": "liceum kielce", "limit": "500"}).json()

    assert len(body["results"]) == MAX_RESULTS


@pytest.mark.django_db
def test_limit_narrows_the_list(api):
    SchoolFactory.create_batch(5, city="Kielce")

    body = api.get(URL, {"q": "liceum kielce", "limit": "2"}).json()

    assert len(body["results"]) == 2


@pytest.mark.django_db
def test_broken_limit_falls_back_to_the_default(api):
    SchoolFactory.create_batch(3, city="Kielce")

    assert len(api.get(URL, {"q": "liceum kielce", "limit": "dużo"}).json()["results"]) == 3


@pytest.mark.django_db
def test_suggestion_carries_the_label_of_the_kind(api):
    SchoolFactory(name="TECHNIKUM MECHANICZNE", city="Radom", kind=SchoolKind.TECHNIKUM, rspo=51234)

    row = api.get(URL, {"q": "technikum mechaniczne"}).json()["results"][0]

    assert row["rspo"] == 51234
    assert row["kind"] == SchoolKind.TECHNIKUM
    assert row["kind_label"] == "technikum"
    assert set(row) == {
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


@pytest.mark.django_db
def test_endpoint_is_open_and_throttled_by_its_own_scope(api):
    """Formularz rejestracji woła je przed założeniem konta – uwierzytelnienia być nie może."""
    assert api.get(URL, {"q": "cokolwiek"}).status_code == 200
    assert SchoolSearchView.throttle_scope == "schools"
    assert SchoolSearchView.permission_classes[0].__name__ == "AllowAny"
    # Scope musi być znany konfiguracji DRF – ``ScopedRateThrottle`` bez stawki wywraca żądanie.
    # W ``settings/test.py`` stawka jest ``None`` (limit wyłączony), ale klucz istnieje.
    assert "schools" in api_settings.DEFAULT_THROTTLE_RATES


@pytest.mark.django_db
def test_results_put_general_secondary_schools_first(api):
    """Uwaga organizatora: po „wrocław” nie było widać liceów ogólnokształcących.

    Przy porządku wyłącznie alfabetycznym pierwsze trafienia dużego miasta to szkoły branżowe
    i technika przy zespołach szkół – nazwy na literę wcześniejszą niż „L”. Porządek zaczyna się
    więc od typu: licea, technika, reszta; dopiero w obrębie typu decyduje nazwa.
    """
    SchoolFactory(name="BRANŻOWA SZKOŁA I STOPNIA NR 1", city="Wrocław", kind=SchoolKind.BRANZOWA_1)
    SchoolFactory(name="ZESPÓŁ SZKÓŁ – TECHNIKUM NR 3", city="Wrocław", kind=SchoolKind.TECHNIKUM)
    SchoolFactory(name="III LICEUM OGÓLNOKSZTAŁCĄCE", city="Wrocław", kind=SchoolKind.LO)

    body = api.get(URL, {"q": "wroclaw"}).json()

    assert [row["kind"] for row in body["results"]] == [
        SchoolKind.LO,
        SchoolKind.TECHNIKUM,
        SchoolKind.BRANZOWA_1,
    ]


# --- zawężenie do miejscowości -----------------------------------------------------------------


@pytest.mark.django_db
def test_city_narrows_the_search_to_that_city_only(api):
    SchoolFactory(name="LICEUM NAD RZEKĄ", city="Płock")
    SchoolFactory(name="LICEUM NAD RZEKĄ", city="Opole")

    body = api.get(URL, {"q": "liceum", "city": "Opole"}).json()

    assert [row["city"] for row in body["results"]] == ["Opole"]


@pytest.mark.django_db
def test_city_is_matched_without_diacritics(api):
    SchoolFactory(name="XXI LICEUM OGÓLNOKSZTAŁCĄCE", city="Łódź")

    body = api.get(URL, {"city": "lodz"}).json()

    assert [row["city"] for row in body["results"]] == ["Łódź"]


@pytest.mark.django_db
def test_empty_query_with_a_city_lists_every_school_of_that_city(api):
    """Sedno poprawki: kto nie wie, jak jego szkoła nazywa się w wykazie, ma ją przewinąć.

    Bez miejscowości pusty tekst nadal nie zwraca niczego – lista „wszystkich szkół w Polsce”
    nie jest listą, z której da się wybrać.
    """
    SchoolFactory(name="I LICEUM OGÓLNOKSZTAŁCĄCE", city="Kielce")
    SchoolFactory(name="TECHNIKUM NR 2", city="Kielce", kind=SchoolKind.TECHNIKUM)
    SchoolFactory(name="I LICEUM OGÓLNOKSZTAŁCĄCE", city="Radom")

    body = api.get(URL, {"q": "", "city": "Kielce"}).json()

    assert [row["name"] for row in body["results"]] == ["I LICEUM OGÓLNOKSZTAŁCĄCE", "TECHNIKUM NR 2"]
    assert body["has_more"] is False
    assert api.get(URL, {"q": ""}).json()["results"] == []


@pytest.mark.django_db
def test_the_full_city_list_is_paged_by_offset(api):
    """Pełna lista dużego miasta nie mieści się w jednej odpowiedzi – klient doczytuje ją stronami."""
    SchoolFactory.create_batch(MAX_RESULTS + 3, city="Kielce")

    first = api.get(URL, {"city": "Kielce"}).json()
    second = api.get(URL, {"city": "Kielce", "offset": str(MAX_RESULTS)}).json()

    assert len(first["results"]) == MAX_RESULTS
    assert first["has_more"] is True
    assert len(second["results"]) == 3
    assert second["has_more"] is False
    # Strony nie zachodzą na siebie: porządek jest deterministyczny (typ, nazwa, identyfikator).
    assert not {row["id"] for row in first["results"]} & {row["id"] for row in second["results"]}


@pytest.mark.django_db
def test_inactive_schools_are_absent_from_the_full_city_list(api):
    SchoolFactory(name="LICEUM ŻYWE", city="Kielce")
    SchoolFactory(name="LICEUM ZLIKWIDOWANE", city="Kielce", is_active=False)

    body = api.get(URL, {"city": "Kielce"}).json()

    assert [row["name"] for row in body["results"]] == ["LICEUM ŻYWE"]


# --- podpowiedzi miejscowości ------------------------------------------------------------------


@pytest.mark.django_db
def test_cities_are_suggested_by_prefix_without_diacritics(api):
    SchoolFactory(city="Łódź", voivodeship=Voivodeship.LODZKIE)
    SchoolFactory(city="Legnica", voivodeship=Voivodeship.DOLNOSLASKIE)

    body = api.get(CITIES_URL, {"q": "lod"}).json()

    assert [row["city"] for row in body["results"]] == ["Łódź"]


@pytest.mark.django_db
def test_cities_are_distinct(api):
    """Trzy szkoły w jednym mieście to jedna podpowiedź – inaczej lista miast byłaby listą szkół."""
    SchoolFactory.create_batch(3, city="Wrocław", voivodeship=Voivodeship.DOLNOSLASKIE)

    body = api.get(CITIES_URL, {"q": "wroc"}).json()

    assert [row["city"] for row in body["results"]] == ["Wrocław"]


@pytest.mark.django_db
def test_cities_carry_the_voivodeship_with_a_label(api):
    """Nazwy miast się powtarzają – bez województwa nie da się wskazać swojego."""
    SchoolFactory(city="Brzeg", voivodeship=Voivodeship.OPOLSKIE)
    SchoolFactory(city="Brzeg", voivodeship=Voivodeship.DOLNOSLASKIE)

    rows = api.get(CITIES_URL, {"q": "brzeg"}).json()["results"]

    assert len(rows) == 2
    assert {row["voivodeship"] for row in rows} == {Voivodeship.OPOLSKIE, Voivodeship.DOLNOSLASKIE}
    assert {row["voivodeship_label"] for row in rows} == {"opolskie", "dolnośląskie"}
    assert set(rows[0]) == {"city", "voivodeship", "voivodeship_label"}


@pytest.mark.django_db
def test_city_suggestions_match_the_beginning_not_the_middle(api):
    """Prefiks, a nie fragment: „law” w środku „Wrocław” dałby listę, w której nie widać pytania."""
    SchoolFactory(city="Wrocław")

    assert api.get(CITIES_URL, {"q": "law"}).json()["results"] == []


@pytest.mark.django_db
def test_city_suggestions_can_be_narrowed_by_voivodeship(api):
    SchoolFactory(city="Brzeg", voivodeship=Voivodeship.OPOLSKIE)
    SchoolFactory(city="Brzeg", voivodeship=Voivodeship.DOLNOSLASKIE)

    rows = api.get(CITIES_URL, {"q": "brzeg", "voivodeship": Voivodeship.OPOLSKIE}).json()["results"]

    assert [row["voivodeship"] for row in rows] == [Voivodeship.OPOLSKIE]


@pytest.mark.django_db
def test_city_suggestions_need_two_characters_and_are_capped(api):
    for number in range(MAX_RESULTS + 5):
        SchoolFactory(city=f"Miastko {number:02d}")

    assert api.get(CITIES_URL, {"q": "m"}).json()["results"] == []
    assert len(api.get(CITIES_URL, {"q": "miastko"}).json()["results"]) == MAX_RESULTS


@pytest.mark.django_db
def test_city_endpoint_is_open_and_shares_the_schools_throttle_scope(api):
    """Ten sam formularz, ten sam koszt, ten sam limit – osobny scope byłby drugą definicją."""
    assert api.get(CITIES_URL, {"q": "war"}).status_code == 200
    assert CitySearchView.throttle_scope == "schools"
    assert CitySearchView.permission_classes[0].__name__ == "AllowAny"


# --- miasta rozbite w wykazie na dzielnice -------------------------------------------------------


def wroclaw_districts() -> None:
    """Wrocław tak, jak zapisuje go wykaz SIO: pięć „miejscowości”, żadnej o nazwie „Wrocław”."""
    SchoolFactory(
        name="III LICEUM OGÓLNOKSZTAŁCĄCE",
        city="Wrocław-Krzyki",
        voivodeship=Voivodeship.DOLNOSLASKIE,
        postal_code="53-001",
    )
    SchoolFactory(
        name="IX LICEUM OGÓLNOKSZTAŁCĄCE",
        city="Wrocław-Fabryczna",
        voivodeship=Voivodeship.DOLNOSLASKIE,
        postal_code="54-001",
    )


@pytest.mark.django_db
def test_districts_of_one_city_are_a_single_suggestion(api):
    """„wro” ma dać jedno miasto, a nie pięć jego dzielnic.

    Wykaz zapisuje Wrocław jako „Wrocław-Fabryczna”, „Wrocław-Krzyki”… – uczestnik dostawał więc
    pięć pozycji, z których każda zawężała listę szkół do jednej piątej miasta, bez śladu,
    że reszta gdzieś jest.
    """
    wroclaw_districts()

    rows = api.get(CITIES_URL, {"q": "wro"}).json()["results"]

    assert [row["city"] for row in rows] == ["Wrocław"]


@pytest.mark.django_db
def test_the_capital_is_found_although_the_directory_never_writes_its_name(api):
    """Sedno zgłoszenia: w wykazie nie ma napisu „Warszawa” – są nazwy osiemnastu dzielnic."""
    SchoolFactory(city="Śródmieście", voivodeship=Voivodeship.MAZOWIECKIE, postal_code="00-001")
    SchoolFactory(city="Mokotów", voivodeship=Voivodeship.MAZOWIECKIE, postal_code="02-001")

    rows = api.get(CITIES_URL, {"q": "warszawa"}).json()["results"]

    assert [row["city"] for row in rows] == ["Warszawa"]


@pytest.mark.django_db
def test_a_district_name_still_suggests_its_city(api):
    """Kto myśli o sobie „z Krzyków”, ma dostać Wrocław, a nie pustą listę."""
    wroclaw_districts()

    rows = api.get(CITIES_URL, {"q": "krzyki"}).json()["results"]

    assert [row["city"] for row in rows] == ["Wrocław"]


@pytest.mark.django_db
def test_city_suggestions_still_match_the_beginning_of_a_word_not_its_middle(api):
    """Dołożenie dzielnic nie zamienia podpowiedzi w wyszukiwanie fragmentów."""
    wroclaw_districts()

    assert api.get(CITIES_URL, {"q": "rzyki"}).json()["results"] == []
    assert api.get(CITIES_URL, {"q": "law"}).json()["results"] == []


@pytest.mark.django_db
def test_choosing_the_city_covers_every_district(api):
    """„Wrocław” ma dać wszystkie szkoły miasta – to jest cała zmiana widoczna dla uczestnika."""
    wroclaw_districts()
    SchoolFactory(name="I LICEUM OGÓLNOKSZTAŁCĄCE", city="Oleśnica", voivodeship=Voivodeship.DOLNOSLASKIE)

    body = api.get(URL, {"city": "Wrocław"}).json()

    assert [row["name"] for row in body["results"]] == [
        "III LICEUM OGÓLNOKSZTAŁCĄCE",
        "IX LICEUM OGÓLNOKSZTAŁCĄCE",
    ]


@pytest.mark.django_db
def test_choosing_a_city_does_not_drag_in_a_city_whose_name_starts_the_same_way(api):
    """W wykazie są 43 pary gmin, w których jedna nazwa zaczyna nazwę drugiej – to jedna z nich."""
    SchoolFactory(name="I LICEUM OGÓLNOKSZTAŁCĄCE", city="Opole", voivodeship=Voivodeship.OPOLSKIE)
    SchoolFactory(
        name="II LICEUM OGÓLNOKSZTAŁCĄCE", city="Opole Lubelskie", voivodeship=Voivodeship.LUBELSKIE
    )

    body = api.get(URL, {"city": "Opole"}).json()

    assert [row["city"] for row in body["results"]] == ["Opole"]


@pytest.mark.django_db
def test_a_result_row_shows_the_city_with_the_district_in_brackets(api):
    """Etykieta odpowiada na „gdzie”, a nawias odróżnia dwie szkoły o podobnej nazwie w mieście."""
    wroclaw_districts()

    rows = api.get(URL, {"city": "Wrocław"}).json()["results"]

    assert [row["city_label"] for row in rows] == ["Wrocław (Krzyki)", "Wrocław (Fabryczna)"]
    # ``city`` zostaje adresem z rejestru – etykieta jedzie **obok** niego, a nie zamiast.
    assert [row["city"] for row in rows] == ["Wrocław-Krzyki", "Wrocław-Fabryczna"]
    assert {row["city_parent"] for row in rows} == {"Wrocław"}


@pytest.mark.django_db
def test_a_school_in_a_plain_city_has_the_same_label_as_its_city(api):
    SchoolFactory(name="I LICEUM OGÓLNOKSZTAŁCĄCE", city="Kielce", voivodeship=Voivodeship.SWIETOKRZYSKIE)

    row = api.get(URL, {"city": "Kielce"}).json()["results"][0]

    assert row["city_label"] == "Kielce"
    assert row["city_parent"] == "Kielce"


@pytest.mark.django_db
def test_typing_the_city_and_the_district_narrows_to_that_district(api):
    """Odwrotna droga: kto pisze „wrocław krzyki”, zawęża do dzielnicy bez wybierania miasta."""
    wroclaw_districts()

    body = api.get(URL, {"q": "wroclaw krzyki"}).json()

    assert [row["name"] for row in body["results"]] == ["III LICEUM OGÓLNOKSZTAŁCĄCE"]


@pytest.mark.django_db
def test_typing_the_capital_finds_its_schools_although_the_directory_writes_districts(api):
    """„warszawa śródmieście” i samo „warszawa” – jedno i drugie ma działać w polu „Szkoła”."""
    SchoolFactory(
        name="XIV LICEUM OGÓLNOKSZTAŁCĄCE",
        city="Śródmieście",
        voivodeship=Voivodeship.MAZOWIECKIE,
        postal_code="00-001",
    )
    SchoolFactory(
        name="VI LICEUM OGÓLNOKSZTAŁCĄCE",
        city="Mokotów",
        voivodeship=Voivodeship.MAZOWIECKIE,
        postal_code="02-001",
    )

    every = api.get(URL, {"q": "warszawa liceum"}).json()["results"]
    one = api.get(URL, {"q": "warszawa śródmieście"}).json()["results"]

    assert len(every) == 2
    assert [row["name"] for row in one] == ["XIV LICEUM OGÓLNOKSZTAŁCĄCE"]


@pytest.mark.django_db
def test_the_district_narrows_the_list_of_a_chosen_city(api):
    """Po wybraniu miasta wpisane obok „krzyki” zawęża pełną listę, zamiast ją zerować."""
    wroclaw_districts()

    body = api.get(URL, {"city": "Wrocław", "q": "krzyki"}).json()

    assert [row["name"] for row in body["results"]] == ["III LICEUM OGÓLNOKSZTAŁCĄCE"]


@pytest.mark.django_db
def test_general_secondary_schools_come_first_across_districts(api):
    """Porządek nie zmienia się przez zlanie dzielnic: najpierw licea, potem technika, potem reszta."""
    SchoolFactory(
        name="TECHNIKUM NR 1",
        city="Wrocław-Krzyki",
        kind=SchoolKind.TECHNIKUM,
        voivodeship=Voivodeship.DOLNOSLASKIE,
    )
    SchoolFactory(
        name="BRANŻOWA SZKOŁA I STOPNIA NR 1",
        city="Wrocław-Fabryczna",
        kind=SchoolKind.BRANZOWA_1,
        voivodeship=Voivodeship.DOLNOSLASKIE,
    )
    SchoolFactory(
        name="III LICEUM OGÓLNOKSZTAŁCĄCE",
        city="Wrocław-Psie Pole",
        kind=SchoolKind.LO,
        voivodeship=Voivodeship.DOLNOSLASKIE,
    )

    body = api.get(URL, {"city": "Wrocław"}).json()

    assert [row["kind"] for row in body["results"]] == [
        SchoolKind.LO,
        SchoolKind.TECHNIKUM,
        SchoolKind.BRANZOWA_1,
    ]
