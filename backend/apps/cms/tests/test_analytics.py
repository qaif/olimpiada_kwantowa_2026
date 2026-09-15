"""Identyfikator Google Analytics 4 w ustawieniach serwisu i pamięć podręczna dla middleware'u CSP.

Dwie rzeczy, których nie widać w przeglądarce, a od których zależy cała funkcja:

- **walidator identyfikatora.** Literówka w ``G-…`` nie objawia się niczym: skrypt Google'a
  wczytuje się i milczy, a organizator przez tydzień patrzy na pusty raport. Jedynym momentem,
  w którym da się ją złapać, jest zapis ustawienia,
- **pamięć podręczna odpowiedzi „czy analityka jest włączona”.** Czyta ją middleware CSP, czyli
  kod odpalany przy **każdej** odpowiedzi, także przy plikach statycznych. Test pilnuje obu stron
  kontraktu: że wynik jest pamiętany i że zapis ustawienia w ``/cms/`` go unieważnia.
"""

import pytest
from django.core.exceptions import ValidationError

from apps.cms import analytics
from apps.cms.models import SiteSettings

pytestmark = pytest.mark.django_db

VALID_ID = "G-TESTTEST1"


@pytest.fixture(autouse=True)
def clean_analytics_cache():
    """Pamięć podręczna jest stanem procesu, a nie bazy – wycofanie transakcji jej nie czyści.

    Bez tego fixture'u test, który włączył analitykę, zostawiałby „włączona” kolejnym testom
    (także w innych modułach) aż do upływu TTL.
    """
    analytics.reset_cache()
    yield
    analytics.reset_cache()


def settings_row() -> SiteSettings:
    return SiteSettings.objects.get()


@pytest.mark.parametrize("value", ["G-TESTTEST1", "G-ABC123", "G-0123456789"])
def test_validator_accepts_a_measurement_id(value):
    row = settings_row()
    row.ga_measurement_id = value

    row.full_clean()  # nie rzuca


@pytest.mark.parametrize(
    "value",
    [
        "UA-12345-1",  # Universal Analytics – usługa, która nie zbiera już danych
        "g-testtest1",  # małe litery
        "G-TEST",  # za krótki
        "G-TEST TEST",  # spacja
        "GTM-ABCDEF",  # kontener Tag Managera, nie strumień GA4
        "https://analytics.google.com/G-TESTTEST1",
    ],
)
def test_validator_rejects_anything_that_is_not_a_ga4_stream(value):
    row = settings_row()
    row.ga_measurement_id = value

    with pytest.raises(ValidationError) as error:
        row.full_clean()

    assert "ga_measurement_id" in error.value.message_dict


def test_empty_value_is_allowed_and_means_analytics_off():
    row = settings_row()
    row.ga_measurement_id = ""

    row.full_clean()

    assert analytics.analytics_enabled() is False


def test_enabled_flag_follows_the_setting():
    row = settings_row()
    row.ga_measurement_id = VALID_ID
    row.save()

    assert analytics.analytics_enabled() is True


def test_answer_is_remembered_between_calls(django_assert_num_queries):
    analytics.analytics_enabled()

    with django_assert_num_queries(0):
        # Drugie pytanie nie może kosztować zapytania: middleware CSP woła je przy każdej
        # odpowiedzi, także przy każdym pliku statycznym oddanym przez WhiteNoise.
        analytics.analytics_enabled()


def test_saving_the_setting_invalidates_the_memory():
    row = settings_row()
    assert analytics.analytics_enabled() is False

    row.ga_measurement_id = VALID_ID
    row.save()

    # Bez sygnału ``post_save`` redaktor patrzyłby na politykę bez hostów Google'a przez cały TTL.
    assert analytics.analytics_enabled() is True
