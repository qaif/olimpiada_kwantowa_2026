"""``GET /api/schools/`` – wyszukiwarka słownika dla formularza rejestracji."""

import pytest
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from apps.accounts.models import Voivodeship
from apps.schools.api import MAX_RESULTS, SchoolSearchView
from apps.schools.models import SchoolKind

from .factories import SchoolFactory

URL = "/api/schools/"


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
    assert set(row) == {"id", "rspo", "name", "kind", "kind_label", "city", "voivodeship"}


@pytest.mark.django_db
def test_endpoint_is_open_and_throttled_by_its_own_scope(api):
    """Formularz rejestracji woła je przed założeniem konta – uwierzytelnienia być nie może."""
    assert api.get(URL, {"q": "cokolwiek"}).status_code == 200
    assert SchoolSearchView.throttle_scope == "schools"
    assert SchoolSearchView.permission_classes[0].__name__ == "AllowAny"
    # Scope musi być znany konfiguracji DRF – ``ScopedRateThrottle`` bez stawki wywraca żądanie.
    # W ``settings/test.py`` stawka jest ``None`` (limit wyłączony), ale klucz istnieje.
    assert "schools" in api_settings.DEFAULT_THROTTLE_RATES
