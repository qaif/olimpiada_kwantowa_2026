"""Strony błędów po polsku: odrzucenie CSRF, brak dostępu, brak strony."""

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def test_csrf_failure_explains_the_stale_form_and_offers_a_retry_link(edition):
    """Scenariusz organizatora: formularz otwarty w innej karcie przed zalogowaniem na inne konto."""
    strict = Client(enforce_csrf_checks=True)
    strict.get("/login/")

    payload = {"username": "ktos@example.test", "password": "x", "csrfmiddlewaretoken": "zly"}
    response = strict.post("/login/", payload)

    assert response.status_code == 403
    body = response.content.decode()
    assert "Formularz wymaga odświeżenia" in body
    assert "zalogowano się lub wylogowano w innej karcie" in body
    assert 'href="/login/"' in body
    assert "Weryfikacja CSRF nie powiodła się" not in body


def test_csrf_failure_without_the_cookie_points_at_cookie_settings(edition):
    strict = Client(enforce_csrf_checks=True)

    response = strict.post("/login/", {"username": "ktos@example.test", "password": "x"})

    assert response.status_code == 403
    assert "nie przyjęła pliku cookie" in response.content.decode()


def test_forbidden_page_is_polish(web_client, participant, edition):
    web_client.force_login(participant.user)

    response = web_client.get("/coordinator/")

    assert response.status_code == 403
    assert "Brak dostępu" in response.content.decode()


def test_not_found_page_is_polish(web_client):
    response = web_client.get("/nie-ma-takiej-strony/")

    assert response.status_code == 404
    assert "Nie znaleziono strony" in response.content.decode()
