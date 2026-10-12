"""Kreator ``/setup/`` nie przejmuje istniejącego konta (audyt 10.10.2026, uwagi niskie).

``bootstrap_coordinator`` bez ``--reset-password`` nadaje istniejącemu kontu superużytkownika
z niezmienionym hasłem, a kreator od razu je loguje – czyli adres cudzego konta wpisany w kroku 1.
dawał sesję superużytkownika na koncie, którego hasła nikt w kreatorze nie podał. Odmowa adresu,
który ma już konto, jest błędem formularza: konto nie zmienia się w żadnym polu.
"""

from __future__ import annotations

import pytest

# Fikstury kreatora są własnością ``test_setup.py`` – import, a nie kopia, żeby „instalacja świeża”
# znaczyła w obu plikach to samo.
from apps.tenancy.tests.test_setup import SETUP_URL, empty_install, opened, operator_payload  # noqa: F401

pytestmark = pytest.mark.django_db


def test_an_address_with_an_account_is_refused(opened):  # noqa: F811
    from apps.accounts.tests.factories import UserFactory

    existing = UserFactory(email="nauczyciel@example.org")
    password_before = existing.password

    response = opened.post(SETUP_URL, operator_payload(email="Nauczyciel@Example.org"))

    assert response.status_code == 400
    assert "już istnieje" in response.content.decode()
    existing.refresh_from_db()
    assert existing.is_superuser is False
    assert existing.is_staff is False
    assert existing.password == password_before
    assert "_auth_user_id" not in opened.session
