"""Adres kontaktowy na stronie 500 jest ustawieniem instalacji, a nie napisem w szablonie.

Decyzja organizatora **D14** (``docs/UNIWERSALNY-ETAP-2.md`` § 6): strona błędu zostaje przy
dzisiejszym brzmieniu i nadal podaje adres, ale sam adres wychodzi z kodu do środowiska
(``ERROR_PAGE_CONTACT_EMAIL``). Operator, który stawia platformę dla swojego konkursu, wpisuje
własny adres w ``.env`` zamiast poprawiać plik w repozytorium; instalacja bez wpisu wygląda
dokładnie tak, jak przed etapem 2 – i tego pilnuje ``test_error_page_strings_unchanged``
(``apps/tenancy/tests/test_invariants.py``).

Czego te testy pilnują ponad to: że strona 500 nadal renderuje się **bez bazy**. To jest cały sens
tego szablonu – oglądamy go dokładnie wtedy, gdy coś się już wywróciło, więc zapytanie do bazy
albo procesor kontekstu byłyby drugą awarią w środku obsługi pierwszej.
"""

import pytest
from django.test import RequestFactory
from django.urls import get_resolver

from apps.web.views.errors import server_error

# Baza dla **całego** pliku, choć żaden z tych testów jej nie używa: autouse'owa fikstura tego
# katalogu (``_reset_panel_counters``) sięga do bazy sama, więc test bez ``django_db`` wywraca się
# w przygotowaniu. Zerowy licznik zapytań niżej jest przez to asercją mocniejszą, a nie słabszą.
pytestmark = pytest.mark.django_db

TODAYS_CONTACT = "contact@qaif.org"
OTHER_CONTACT = "biuro@olimpiadajuniorow.pl"


@pytest.fixture
def request_():
    return RequestFactory().get("/cokolwiek/")


def test_handler500_points_at_our_view():
    """Bez tego wpisu Django renderowałoby ``500.html`` **bez kontekstu**, czyli bez adresu."""
    assert get_resolver().resolve_error_handler(500) is server_error


def test_page_shows_todays_address_by_default(request_):
    response = server_error(request_)
    body = response.content.decode()

    assert response.status_code == 500
    assert f'href="mailto:{TODAYS_CONTACT}"' in body
    assert f">{TODAYS_CONTACT}</a>" in body


def test_page_shows_the_configured_address(request_, settings):
    settings.ERROR_PAGE_CONTACT_EMAIL = OTHER_CONTACT

    body = server_error(request_).content.decode()

    assert f'href="mailto:{OTHER_CONTACT}"' in body
    assert TODAYS_CONTACT not in body


def test_wording_and_layout_do_not_change(request_, settings):
    """Zmianą jest **wyłącznie** adres: tytuł, nagłówek i odnośnik do strony głównej zostają."""
    settings.ERROR_PAGE_CONTACT_EMAIL = OTHER_CONTACT

    body = server_error(request_).content.decode()

    assert "<title>Błąd serwera – Olimpiada Kwantowa</title>" in body
    assert "Coś poszło nie tak po naszej stronie" in body
    assert "napisz do organizatora" in body
    assert 'href="/">Wróć na stronę główną' in body


def test_page_renders_without_touching_the_database(request_, django_assert_num_queries):
    """Zero zapytań – to jest warunek, dla którego adres nie może pochodzić z ``Competition``."""
    with django_assert_num_queries(0):
        server_error(request_)
