"""Wspólne fixture'y testów djcms.

Klient API aplikacji głównej jest zamockowany w **każdym** teście (``main_api`` niżej, autouse).

Strony powstają przez publiczne API django CMS (``cms.api.create_page``) i są publikowane przez
djangocms-versioning (``Version.publish``) – tak samo, jak zrobi to importer (DJ-01g). Testy nie
piszą do tabel CMS-a bezpośrednio: zmiana schematu w kolejnym wydaniu django CMS ma wywrócić
test na API, a nie przejść niezauważona przez ręcznie sklejony wiersz.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest
from django.core.cache import caches

# --- API aplikacji głównej: zawsze zamockowane ----------------------------------------------------
#
# W kontenerze dev ``web:8000`` jest osiągalny, więc test, który wyrenderuje ramę bez mocka,
# zapytałby **prawdziwe** API – wynik zależałby od stanu bazy deweloperskiej. Dlatego fixture
# ``main_api`` jest autouse: domyślnie każde żądanie kończy się błędem połączenia (rama degraduje),
# a test, który potrzebuje danych, ustawia je przez ``main_api.set("chrome", {...})``.


class FakeResponse(io.BytesIO):
    """Odpowiedź ``urllib`` na tyle, na ile czyta ją ``apps.live.client``: ``status``, ``read``, ``with``."""

    def __init__(self, body: bytes, status: int = 200, on_read=None):
        super().__init__(body)
        self.status = status
        self._on_read = on_read

    def read(self, size=-1):
        if self._on_read is not None:
            self._on_read()
        return super().read(size)


class FakeOpener:
    """Otwieracz podstawiany za ``apps.live.client._build_opener``. Zapisuje każde żądanie."""

    def __init__(self):
        self.responses: dict[str, object] = {}
        self.requests: list = []
        self.timeouts: list[float] = []

    def set(
        self,
        endpoint: str,
        payload: dict | None = None,
        *,
        status: int = 200,
        raw: bytes | None = None,
        on_read=None,
    ):
        body = raw if raw is not None else json.dumps({"api_version": 1, **(payload or {})}).encode()
        self.responses[endpoint] = lambda: FakeResponse(body, status, on_read)

    def fail(self, endpoint: str, exc: BaseException):
        def _raise():
            raise exc

        self.responses[endpoint] = _raise

    def calls(self, endpoint: str) -> int:
        return sum(1 for request in self.requests if request.full_url.endswith("/" + endpoint))

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        endpoint = request.full_url.split("/internal/djcms/v1/", 1)[-1]
        factory = self.responses.get(endpoint)
        if factory is None:
            raise urllib.error.URLError(ConnectionRefusedError("testy: brak API"))
        return factory()


@pytest.fixture(autouse=True)
def main_api(monkeypatch):
    opener = FakeOpener()
    monkeypatch.setattr("apps.live.client._build_opener", lambda: opener)
    return opener


@pytest.fixture(autouse=True)
def _clear_caches():
    """Bufory (także licznik blokady logowania) nie przenoszą stanu między testami."""
    for alias in ("default", "throttle"):
        caches[alias].clear()
    yield
    for alias in ("default", "throttle"):
        caches[alias].clear()


@pytest.fixture
def editor(django_user_model):
    """Konto personelu (``is_staff``) – dostaje luźną politykę CSP i pasek narzędzi."""
    return django_user_model.objects.create_user(
        username="redaktor@example.com",
        email="redaktor@example.com",
        password="haslo-redaktora-123",
        is_staff=True,
    )


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="admin@example.com", email="admin@example.com", password="haslo-admina-123456"
    )


@pytest.fixture
def make_page(superuser):
    """Tworzy stronę w korzeniu drzewa; ``publish=True`` publikuje jej wersję (versioning)."""
    from cms.api import create_page
    from cms.models import PageContent
    from djangocms_versioning.models import Version

    def _make(
        title: str,
        slug: str,
        *,
        publish: bool = True,
        template: str = "dj/pages/content.html",
        parent=None,
        menu_title: str | None = None,
        in_navigation: bool = True,
        home: bool = False,
    ):
        page = create_page(
            title,
            template,
            "pl",
            slug=slug,
            created_by=superuser,
            in_navigation=in_navigation,
            parent=parent,
            menu_title=menu_title,
        )
        if publish:
            content = PageContent.admin_manager.get(page=page, language="pl")
            Version.objects.get_for_content(content).publish(superuser)
        if home:
            # ``set_as_homepage`` wymaga transakcji (blokada korzeni drzewa) – test ``django_db``
            # już w niej jest; importer (DJ-01g) musi o nią zadbać sam.
            page.set_as_homepage(superuser)
        return page

    return _make


def _chrome_payload(**overrides) -> dict:
    """Odpowiedź ``chrome`` w kształcie z ``backend/apps/cms/djcms_api/views.py::chrome`` (pełna rama)."""
    item = {
        "index": 0,
        "kind": "stage",
        "title": "Etap I",
        "start": "2026-10-01",
        "end": "2026-10-31",
        "status": "current",
        "dates": "1–31 paź",
        "url": "/harmonogram/",
        "note": "",
    }
    payload = {
        "api_version": 1,
        "generated_at": "2026-09-26T12:00:00+02:00",
        "competition": {"slug": "kwantowa", "name": "Olimpiada Kwantowa"},
        "site": {
            "site_name": "Olimpiada Testowa",
            "tagline": "Hasło testowe",
            "organizer_name": "Fundacja Testowa",
            "organizer_logo": {"src": "https://s3.olimpiada.example/logo.png", "width": 240, "height": 80},
            "organizer_address": "ul. Testowa 1",
            "organizer_registry": "KRS 0000000000",
            "contact_email": "kontakt@olimpiada.example",
            "contact_phone": "+48 500 600 700",
            "contact_url": "https://olimpiada.example/kontakt",
            "social_links": [{"url": "https://facebook.com/x", "label": "Facebook", "icon": "facebook"}],
            "registration_note": "",
        },
        "edition": {
            "id": 3,
            "year_label": "I 2026/2027",
            "title": "I edycja 2026/2027",
            "title_cap": "I Edycja 2026/2027",
        },
        "registration": {
            "is_open": True,
            "reason": "open",
            "opens_at": None,
            "opens_at_display": "",
            "closes_at": None,
            "message": "",
        },
        "supervisor_registration": {"enabled": True, "url": "https://olimpiada.example/register/supervisor/"},
        "links": {
            "login": "https://olimpiada.example/login/",
            "register": "https://olimpiada.example/register/",
            "support": "https://olimpiada.example/support/new/",
            "posters": "https://olimpiada.example/plakaty/",
            "main_home": "https://olimpiada.example/",
        },
        "announcements": [
            {
                "id": 4,
                "text": "Uwaga <b>ważne</b>",
                "level": "warning",
                "link_url": "https://olimpiada.example/x",
                "link_label": "Więcej",
                "has_link": True,
                "dismissible": True,
            },
        ],
        "sponsor_slider": {
            "seconds": 5,
            "entries": [
                {
                    "name": "Sponsor A",
                    "url": "https://sponsor.example",
                    "src": "https://s3.olimpiada.example/a.png",
                    "width": 160,
                    "height": 48,
                },
            ],
        },
        "timeline_strip": {
            "edition": "I 2026/2027",
            "header_lines": ["rok_szkolny(2026, 2027)"],
            "items": [item],
            "axis_start": "2026-09-01",
            "axis_end": "2027-06-30",
            "progress": 0.1,
            "head": 3,
            "size": 4,
            "lead": item,
            "cells": [
                {
                    "index": 0,
                    "char": "=",
                    "status": "past",
                    "is_head": False,
                    "css_class": "tl__cell",
                    "items": [],
                    "label": "",
                    "url": "",
                    "indexes": "",
                },
                {
                    "index": 1,
                    "char": "|",
                    "status": "current",
                    "is_head": False,
                    "css_class": "tl__cell tl__mark",
                    "items": [item],
                    "label": "Etap I, 1–31 paź",
                    "url": "/harmonogram/",
                    "indexes": "0",
                },
                {
                    "index": 2,
                    "char": "|",
                    "status": "current",
                    "is_head": False,
                    "css_class": "tl__cell tl__mark",
                    "items": [item, item],
                    "label": "Dwa",
                    "url": "",
                    "indexes": "0,0",
                },
                {
                    "index": 3,
                    "char": ">",
                    "status": "head",
                    "is_head": True,
                    "css_class": "tl__cell tl__head",
                    "items": [],
                    "label": "",
                    "url": "",
                    "indexes": "",
                },
            ],
        },
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def chrome_payload():
    """Fabryka odpowiedzi ``chrome``: ``chrome_payload(registration={...})`` nadpisuje klucze główne."""
    return _chrome_payload
