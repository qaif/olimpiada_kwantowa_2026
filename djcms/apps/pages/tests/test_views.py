"""``/healthz/`` i ``/robots.txt`` (reguły 5 i 10 z § 7 docs/tasks/DJ-01.md)."""

from unittest import mock

import pytest
from django.db import OperationalError


@pytest.mark.django_db
def test_healthz_ok_without_touching_main_api(client):
    # Healthcheck NIE pyta API aplikacji głównej: każda próba wyjścia do sieci wywraca test.
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("healthz nie może pytać API")):
        response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True}
    assert "no-cache" in response["Cache-Control"]


@pytest.mark.django_db
def test_healthz_503_when_database_fails(client):
    with mock.patch("apps.pages.views._ping_database", side_effect=OperationalError("db down")):
        response = client.get("/healthz/")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": False}


@pytest.mark.django_db
def test_healthz_rejects_post(client):
    assert client.post("/healthz/").status_code == 405


@pytest.mark.django_db
def test_robots_txt_disallows_everything(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    assert response.content.decode() == "User-agent: *\nDisallow: /\n"
