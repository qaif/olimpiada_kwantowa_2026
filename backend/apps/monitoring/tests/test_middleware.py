"""Warstwa tagu konkursu (OPS-02 § 2) – bez działającego klienta nie robi nic i nic nie importuje."""

from __future__ import annotations

from django.http import HttpResponse
from django.test import RequestFactory

from apps.monitoring import sentry
from apps.monitoring.middleware import ErrorTrackingTagMiddleware


def test_without_a_client_the_layer_is_a_pass_through(monkeypatch):
    monkeypatch.setattr(sentry, "_initialized", False)
    request = RequestFactory().get("/")
    request.competition = type("C", (), {"slug": "iqo"})()

    response = ErrorTrackingTagMiddleware(lambda r: HttpResponse("ok"))(request)

    assert response.content == b"ok"


def test_with_a_client_the_slug_is_passed_on(monkeypatch):
    seen = []
    monkeypatch.setattr("apps.monitoring.middleware.set_competition_tag", seen.append)
    request = RequestFactory().get("/")
    request.competition = type("C", (), {"slug": "iqo"})()

    ErrorTrackingTagMiddleware(lambda r: HttpResponse())(request)
    request_without = RequestFactory().get("/")
    ErrorTrackingTagMiddleware(lambda r: HttpResponse())(request_without)

    assert seen == ["iqo", None]
