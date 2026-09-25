"""Migracja danych ``accounts.0010``: istniejące, działające konta dostają ``email_verified_at``.

To nie jest kosmetyka. Od tej zmiany logowanie jest zamknięte dla kont bez potwierdzonego adresu,
a listę „konta oczekujące na aktywację” w panelu koordynatora buduje ten sam warunek. Gdyby pole
zostało puste dla kont, które już działają, cała dotychczasowa baza uczestników i recenzentów
zostałaby w jednej chwili odcięta od panelu – bez niczyjej winy i bez linku w skrzynce, bo listy
aktywacyjne nigdy do nich nie poszły.

Bazę przewija fikstura :func:`rewound_apps` w transakcji wycofywanej na końcu modułu – wycofanie
przywraca czoło migracji (``apps/core/tests/migration_helpers.py``).
"""

import pytest
from django.utils import timezone

from apps.core.tests.migration_helpers import MIGRATION_TESTS, migrate_to, rewound_database

pytestmark = MIGRATION_TESTS

BEFORE = ("accounts", "0009_participant_consents")
AFTER = ("accounts", "0010_activation_and_phone")


@pytest.fixture(scope="module")
def rewound_apps(django_db_setup, django_db_blocker):
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db.apps


def test_existing_accounts_keep_access_after_the_migration(rewound_apps):
    User = rewound_apps.get_model("accounts", "User")
    joined = timezone.now().replace(microsecond=0)
    working = User.objects.create(email="dziala@example.test", is_active=True, date_joined=joined)
    # Konto zablokowane przez organizatora: ``is_active=False`` znaczy tu „zablokowane”, a nie
    # „niepotwierdzone”, i migracja nie ma prawa zrobić z niego konta z potwierdzonym adresem.
    blocked = User.objects.create(email="zablokowane@example.test", is_active=False, date_joined=joined)

    new_apps = migrate_to(AFTER)
    MigratedUser = new_apps.get_model("accounts", "User")

    assert MigratedUser.objects.get(pk=working.pk).email_verified_at == joined
    assert MigratedUser.objects.get(pk=blocked.pk).email_verified_at is None
    # Telefon dokładamy jako puste pole – wpisanie wartości zastępczej byłoby wpisaniem
    # uczestnikowi danych, których nie podał.
    assert MigratedUser.objects.get(pk=working.pk).email == "dziala@example.test"
