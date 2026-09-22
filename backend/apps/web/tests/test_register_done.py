"""Strona „sprawdź skrzynkę” po udanej rejestracji (``/register/done/``).

Zdanie o folderze ze spamem i instrukcja aktywacji stoją po **poprawnym** wysłaniu formularza,
a nie w liście ani nad formularzem logowania. Adres, na który poszedł list, przychodzi z sesji
i znika po jednym odczycie.
"""

import pytest
from django.core import mail

from apps.web.tests.test_activation import REGISTER_URL, register_payload

pytestmark = pytest.mark.django_db

DONE_URL = "/register/done/"


def test_successful_registration_lands_on_the_check_your_mailbox_page(
    web_client, edition, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(REGISTER_URL, register_payload())

    assert response.status_code == 302
    assert response.url == DONE_URL
    body = web_client.get(DONE_URL).content.decode()
    assert "Konto zostało założone" in body
    assert "aktywacja-web@example.test" in body
    assert "sprawdź folder ze spamem" in body
    assert "24 godziny" in body
    assert 'href="/activate/resend/"' in body
    # Zdanie o spamie zniknęło z treści listu – tam nie miało sensu.
    assert "folder ze spamem" not in mail.outbox[0].body


def test_the_page_forgets_the_address_after_one_view(web_client, edition, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        web_client.post(REGISTER_URL, register_payload())
    assert web_client.get(DONE_URL).status_code == 200

    second = web_client.get(DONE_URL)

    assert second.status_code == 302
    assert second.url == REGISTER_URL


def test_direct_visit_without_a_registration_goes_back_to_the_form(web_client):
    response = web_client.get(DONE_URL)

    assert response.status_code == 302
    assert response.url == REGISTER_URL
