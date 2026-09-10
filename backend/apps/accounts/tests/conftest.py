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
