"""Wspólne fikstury edytora LaTeX: konkurs z flagą, etap, zadanie, uczestnik i podstawiony webTEX.

Sieci nie ma: ``client.send`` zastępuje :class:`FakeWebtex`, który zapisuje żądania (do sprawdzenia
podpisu) i oddaje przygotowane odpowiedzi.
"""

from __future__ import annotations

import itertools
import json

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.submissions.tests.factories import PDF_BYTES
from apps.tenancy.tests.factories import grant_membership

_seq = itertools.count(1)

SHARED_KEY = "webtex-test-key-" + "x" * 32
WEBTEX_URL = "https://tex.example.test"
INTERNAL_URL = "http://webtex:3000"


@pytest.fixture(autouse=True)
def webtex_settings(settings):
    settings.WEBTEX_SHARED_KEY = SHARED_KEY
    settings.WEBTEX_URL = WEBTEX_URL
    settings.WEBTEX_INTERNAL_URL = INTERNAL_URL
    settings.WEBTEX_ISSUER = "olimpiada"
    return settings


def enable(competition, on: bool = True):
    competition.feature_flags = {**(competition.feature_flags or {}), "latex_editor": on}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def stage(competition):
    edition = CurrentEditionFactory(competition=competition)
    return StageFactory(competition=competition, edition=edition)


@pytest.fixture
def problem(stage):
    return ProblemFactory(stage=stage, number=3, title="Splątanie", allowed_formats=["pdf"])


@pytest.fixture
def coordinator(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


def make_participant(competition, stage=None, **user_fields):
    item = ParticipantFactory(
        user=UserFactory(
            email=f"uczen{next(_seq)}@example.test",
            first_name=user_fields.get("first_name", "Ada"),
            last_name=user_fields.get("last_name", "Lovelace"),
            groups=["participant"],
        )
    )
    grant_membership(item.user, competition, CompetitionRole.PARTICIPANT)
    if stage is not None:
        StageEntryFactory(participant=item, stage=stage)
    return item


@pytest.fixture
def participant(competition, stage):
    return make_participant(competition, stage)


class FakeResponse:
    def __init__(self, status: int, *, body: bytes = b"", content_type: str = "application/json"):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self._body = body
        self.closed = False

    def json(self):
        return json.loads(self._body.decode())

    def iter_content(self, chunk_size=1024):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self):
        self.closed = True


def pdf_response(content: bytes = PDF_BYTES) -> FakeResponse:
    return FakeResponse(200, body=content, content_type="application/pdf")


def json_response(status: int, payload: dict) -> FakeResponse:
    return FakeResponse(status, body=json.dumps(payload).encode())


class FakeWebtex:
    """Podstawiony transport: kolejka odpowiedzi (albo wyjątków) i lista wysłanych żądań."""

    def __init__(self):
        self.responses: list = []
        self.calls: list[dict] = []

    def __call__(self, method, url, *, data, headers, timeout, stream=False):
        self.calls.append(
            {"method": method, "url": url, "data": data, "headers": headers, "timeout": timeout}
        )
        item = self.responses.pop(0) if self.responses else pdf_response()
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def webtex(monkeypatch):
    fake = FakeWebtex()
    monkeypatch.setattr("apps.webtex.client.send", fake)
    return fake
