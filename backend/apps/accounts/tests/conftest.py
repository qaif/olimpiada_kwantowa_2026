"""Wspólny stan testów kont: bieżąca edycja z otwartą rejestracją uczestników.

Rejestracja uczestnika przechodzi od tej zmiany przez bramkę okna rejestracji
(``apps.competitions.registration.ensure_registration_open``), a brak bieżącej edycji znaczy dla
niej „nie ma do czego się rejestrować”. Test rejestracji bez edycji sprawdzałby więc bramkę,
a nie rejestrację – dlatego tam, gdzie przedmiotem testu jest zakładanie konta, świat ma
bieżącą edycję z domyślnym (otwartym) oknem.

Fixture jest jawna, a nie ``autouse``: testy, których przedmiotem jest **sama bramka**, mają
świadomie zaczynać od pustej bazy (patrz ``apps/competitions/tests/test_registration_window.py``).
"""

import pytest

from apps.competitions.tests.factories import CurrentEditionFactory


@pytest.fixture
def open_registration(db):
    """Bieżąca edycja bez ograniczeń okna – rejestracja uczestników jest otwarta."""
    return CurrentEditionFactory()


#: Odpowiedź, którą ``django-simple-captcha`` przyjmuje przy ``CAPTCHA_TEST_MODE`` (settings/test.py).
API_CAPTCHA_TEST_RESPONSE = "PASSED"


def api_captcha_fields() -> dict:
    """Para CAPTCHY dla JSON-owej rejestracji (``POST /api/auth/register/…``, pakiet 5).

    Ten sam skrót, co ``apps/web/tests/conftest.py::captcha_fields`` dla formularza: tryb testowy
    pakietu przyjmuje odpowiedź ``PASSED`` przy dowolnym, **niepustym** kluczu. Prawdziwą drogę
    (wyzwanie z ``CaptchaStore``) sprawdza ``test_registration.py`` z wyłączonym trybem testowym.
    """
    return {"captcha_key": "klucz-nieistotny-w-trybie-testowym", "captcha_value": API_CAPTCHA_TEST_RESPONSE}
