"""Migracja danych ``accounts.0039``: konta nieaktywne, które kiedyś się logowały, są zablokowane.

Konto czekające na aktywację nie mogło się nigdy zalogować, więc ``is_active=False`` przy
``last_login`` wypełnionym i bez potwierdzonego adresu znaczy blokadę sprzed ``accounts.0010``
(audyt 10.10.2026, S5). Rejestracja w toku i zaproszenie z importu mają zostać nietknięte – inaczej
ich właściciele straciliby link, na który czekają.
"""

import pytest
from django.utils import timezone

from apps.core.tests.migration_helpers import MIGRATION_TESTS, migrate_to, rewound_database

pytestmark = MIGRATION_TESTS

BEFORE = ("accounts", "0038_delegations_review")
AFTER = ("accounts", "0039_user_blocked_at")


@pytest.fixture(scope="module")
def rewound_apps(django_db_setup, django_db_blocker):
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db.apps


def test_tylko_nieaktywne_konta_po_logowaniu_dostaja_blokade(rewound_apps):
    User = rewound_apps.get_model("accounts", "User")
    now = timezone.now()
    blocked = User.objects.create(email="dawniej-zablokowane@example.test", is_active=False, last_login=now)
    pending = User.objects.create(email="czeka@example.test", is_active=False)
    verified_blocked = User.objects.create(
        email="zablokowane-z-adresem@example.test", is_active=False, last_login=now, email_verified_at=now
    )
    active = User.objects.create(
        email="dziala@example.test", is_active=True, last_login=now, email_verified_at=now
    )

    new_apps = migrate_to(AFTER)
    MigratedUser = new_apps.get_model("accounts", "User")

    assert MigratedUser.objects.get(pk=blocked.pk).blocked_at is not None
    assert MigratedUser.objects.get(pk=pending.pk).blocked_at is None
    # Konto z potwierdzonym adresem i tak nie dostanie linku aktywacyjnego – migracja go nie dotyka.
    assert MigratedUser.objects.get(pk=verified_blocked.pk).blocked_at is None
    assert MigratedUser.objects.get(pk=active.pk).blocked_at is None
