"""Zgoda opiekuna prawnego: alias ``+tag`` i adres opiekuna szkolnego (audyt 10.10.2026, niskie).

Uczeń, który chce „potwierdzić” zgodę sam, podawał własny adres z aliasem (``jan+mama@…`` trafia
do jego skrzynki) albo adres nauczyciela, który klikał link w przekonaniu, że potwierdza udział.
Oba warianty kończą się odmową, zanim cokolwiek zostanie zapisane albo wysłane.
"""

import pytest
from django.utils import timezone

from apps.accounts.guardian import mailbox_key, request_consent
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.core.api import DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def minor():
    return ParticipantFactory(
        user=UserFactory(email="jan.kowalski@example.test", groups=["participant"]),
        birth_year=timezone.localdate().year - 16,
        supervisor_email="nauczyciel@szkola.test",
    )


def test_postac_porownawcza_skrzynki_zdejmuje_alias_i_wielkosc_liter():
    assert mailbox_key(" Jan.Kowalski+Mama@Example.TEST ") == "jan.kowalski@example.test"
    # Kropki zostają – to reguła jednego dostawcy, u innych to różne osoby.
    assert mailbox_key("jankowalski@example.test") != mailbox_key("jan.kowalski@example.test")


@pytest.mark.parametrize("address", ["jan.kowalski+mama@example.test", "JAN.KOWALSKI+x@example.test"])
def test_wlasny_adres_z_aliasem_jest_odmowa(minor, address):
    with pytest.raises(DomainError) as exc:
        request_consent(minor, address, actor=minor.user)
    assert exc.value.machine_code == "GUARDIAN_EMAIL_IS_OWN"
    minor.refresh_from_db()
    assert minor.guardian_email == ""


@pytest.mark.parametrize("address", ["nauczyciel@szkola.test", "nauczyciel+rodzic@szkola.test"])
def test_adres_opiekuna_szkolnego_jest_odmowa(minor, address):
    with pytest.raises(DomainError) as exc:
        request_consent(minor, address, actor=minor.user)
    assert exc.value.machine_code == "GUARDIAN_EMAIL_IS_SUPERVISOR"


def test_inny_adres_przechodzi(minor, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        assert request_consent(minor, "rodzic@example.test", actor=minor.user) == "rodzic@example.test"
