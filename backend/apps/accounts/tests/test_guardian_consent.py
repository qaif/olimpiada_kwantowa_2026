"""Zgoda opiekuna zbierana online: prośba, podpisany link, potwierdzenie i stan w panelu.

Sprawdzamy trzy rzeczy, których nie da się sprawdzić osobno:

- **dowód**: po potwierdzeniu w bazie stoi ``ConsentRecord`` rodzaju ``GUARDIAN`` z adresem
  potwierdzającego i adresem IP – bez tego zostałoby oświadczenie dziecka o cudzej woli,
- **uprawnienie**: jedynym kluczem do formularza jest podpisany token. Zmiana adresu opiekuna
  albo podrobiony podpis mają zamknąć drogę, a nie dać „prawie działający” link,
- **stan**: panel uczestnika ma odpowiadać „brak / oczekuje / potwierdzona <data>”, bo to jest
  jedyne miejsce, w którym uczestnik dowie się, że czegoś jeszcze brakuje.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.consents import ConsentKind
from apps.accounts.guardian import (
    GUARDIAN_CONFIRMED_SUBJECT,
    GUARDIAN_SUBJECT,
    confirm_consent,
    guardian_status,
    make_token,
    read_token,
    request_consent,
)
from apps.accounts.models import ConsentRecord
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.core.api import DomainError

pytestmark = pytest.mark.django_db

GUARDIAN_EMAIL = "rodzic@example.test"


@pytest.fixture
def minor():
    """Uczestnik niepełnoletni – rocznik liczony od „teraz”, żeby test nie starzał się z kalendarzem."""
    year = timezone.localdate().year - 16
    return ParticipantFactory(
        user=UserFactory(email="uczen@example.test", first_name="Jan", groups=["participant"]),
        birth_year=year,
        school="I LO w Krakowie",
    )


@pytest.fixture
def adult():
    year = timezone.localdate().year - 25
    return ParticipantFactory(
        user=UserFactory(email="dorosly@example.test", groups=["participant"]), birth_year=year
    )


def confirm_url(participant) -> str:
    return f"/zgoda/{make_token(participant)}/"


# --- (a) prośba o zgodę --------------------------------------------------------------------------


def test_request_stores_the_address_and_sends_a_link(minor, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        request_consent(minor, GUARDIAN_EMAIL, actor=minor.user)

    minor.refresh_from_db()
    assert minor.guardian_email == GUARDIAN_EMAIL
    message = next(item for item in mail.outbox if item.subject == GUARDIAN_SUBJECT)
    assert message.to == [GUARDIAN_EMAIL]
    # Imię i szkoła – tyle, żeby opiekun rozpoznał, czego dotyczy prośba. Nazwiska w liście nie ma.
    assert "Jan" in message.body
    assert "I LO w Krakowie" in message.body
    assert "/zgoda/" in message.body


def test_request_refuses_an_empty_address(minor):
    with pytest.raises(DomainError) as exc:
        request_consent(minor, "", actor=minor.user)

    assert exc.value.machine_code == "GUARDIAN_EMAIL_REQUIRED"


def test_participant_cannot_be_their_own_guardian(minor):
    with pytest.raises(DomainError) as exc:
        request_consent(minor, minor.user.email, actor=minor.user)

    assert exc.value.machine_code == "GUARDIAN_EMAIL_IS_OWN"


def test_adult_does_not_need_guardian_consent(adult):
    assert guardian_status(adult)["state"] == "not_required"

    with pytest.raises(DomainError) as exc:
        request_consent(adult, GUARDIAN_EMAIL, actor=adult.user)

    assert exc.value.machine_code == "GUARDIAN_NOT_REQUIRED"


# --- (b) token ------------------------------------------------------------------------------------


def test_token_stops_working_after_the_address_changes(minor):
    request_consent(minor, GUARDIAN_EMAIL, actor=minor.user)
    token = make_token(minor)

    request_consent(minor, "inny.rodzic@example.test", actor=minor.user)

    with pytest.raises(DomainError):
        read_token(token)


def test_expired_token_is_refused(minor):
    """Ważność liczy podpis, nie baza – więc jedynym sposobem sprawdzenia jej jest zegar."""
    request_consent(minor, GUARDIAN_EMAIL, actor=minor.user)
    token = make_token(minor)

    # Piętnaście dni: link żyje czternaście, więc to pierwszy dzień, w którym ma nie działać.
    with freeze_time(timezone.now() + timedelta(days=15)), pytest.raises(DomainError):
        read_token(token)


def test_forged_token_is_refused(minor):
    request_consent(minor, GUARDIAN_EMAIL, actor=minor.user)

    with pytest.raises(DomainError):
        read_token("nie-jest-podpisem")


# --- (c) potwierdzenie ----------------------------------------------------------------------------


def test_confirmation_records_the_evidence(minor, django_capture_on_commit_callbacks):
    request_consent(minor, GUARDIAN_EMAIL, actor=minor.user)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        record = confirm_consent(minor)

    minor.refresh_from_db()
    assert record.kind == ConsentKind.GUARDIAN
    assert record.given_by_email == GUARDIAN_EMAIL
    assert minor.guardian_consent is True
    notice = next(item for item in mail.outbox if item.subject == GUARDIAN_CONFIRMED_SUBJECT)
    assert notice.to == [minor.user.email]


def test_second_confirmation_does_not_add_a_second_record(minor):
    request_consent(minor, GUARDIAN_EMAIL, actor=minor.user)

    first = confirm_consent(minor)
    second = confirm_consent(minor)

    assert first.pk == second.pk
    assert ConsentRecord.objects.filter(participant=minor, kind=ConsentKind.GUARDIAN).count() == 1


# --- (d) strona opiekuna (bez logowania) ----------------------------------------------------------


def test_guardian_page_shows_the_consent_and_saves_it(minor, django_capture_on_commit_callbacks):
    request_consent(minor, GUARDIAN_EMAIL, actor=minor.user)
    client = Client()

    page = client.get(confirm_url(minor))
    content = page.content.decode()
    assert page.status_code == 200
    assert "Jan" in content
    assert "I LO w Krakowie" in content

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(confirm_url(minor), {"agree": "1"}, REMOTE_ADDR="198.51.100.7")

    assert response.status_code == 302
    record = ConsentRecord.objects.get(participant=minor, kind=ConsentKind.GUARDIAN)
    assert record.ip_address == "198.51.100.7"


def test_guardian_page_refuses_an_unticked_box(minor):
    request_consent(minor, GUARDIAN_EMAIL, actor=minor.user)
    client = Client()

    response = client.post(confirm_url(minor), {})

    assert response.status_code == 400
    assert not ConsentRecord.objects.filter(participant=minor, kind=ConsentKind.GUARDIAN).exists()


def test_bad_token_gets_one_message_and_no_details(minor):
    response = Client().get("/zgoda/cokolwiek/")

    assert response.status_code == 400
    assert "nieprawidłowy albo wygasł" in response.content.decode()


# --- (e) stan w panelu uczestnika -----------------------------------------------------------------


def test_panel_walks_through_missing_pending_confirmed(client, minor):
    assert guardian_status(minor)["state"] == "missing"

    client.force_login(minor.user)
    content = client.get("/me/").content.decode()
    assert "Zgoda rodzica lub opiekuna prawnego" in content
    assert "Wyślij prośbę o zgodę" in content

    client.post("/me/guardian/", {"guardian_email": GUARDIAN_EMAIL})
    minor.refresh_from_db()
    assert guardian_status(minor)["state"] == "pending"
    assert "Wyślij ponownie" in client.get("/me/").content.decode()

    confirm_consent(minor)
    minor.refresh_from_db()
    state = guardian_status(minor)
    assert state["state"] == "confirmed"
    assert state["email"] == GUARDIAN_EMAIL
    assert "potwierdzona" in client.get("/me/").content.decode()


def test_adult_panel_has_no_guardian_section(client, adult):
    client.force_login(adult.user)

    assert "Wyślij prośbę o zgodę" not in client.get("/me/").content.decode()
