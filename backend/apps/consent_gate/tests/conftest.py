"""Wspólne fikstury testów bramki zgód (CONS-01).

Bramka jest w ``config/settings/test.py`` wyłączona (fabryka uczestnika nie zakłada wpisów
``ConsentRecord``) – tutaj włączamy ją dla **każdego** testu tej aplikacji, a wpisy zakładamy tak,
jak robi to rejestracja: rodzaj i wersja z zestawu konkursu.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.consents import ConsentSource, consent_set, required_kinds
from apps.accounts.models import ConsentRecord
from apps.accounts.tests.factories import ParticipantFactory, UserFactory


@pytest.fixture(autouse=True)
def gate_on(settings):
    settings.CONSENT_GATE_ENABLED = True


def give(participant, kinds=None, *, version: str | None = None, **fields) -> list[ConsentRecord]:
    """Wpisy dowodowe zgód – domyślnie komplet wymaganych od tej osoby, w bieżącej wersji."""
    wanted = (
        set(kinds)
        if kinds is not None
        else set(
            required_kinds(
                participant.birth_date, participant.birth_year, competition=participant.competition
            )
        )
    )
    return [
        ConsentRecord.objects.create(
            participant=participant,
            kind=consent.kind,
            document_version=version if version is not None else consent.version,
            source=ConsentSource.WEB,
            **fields,
        )
        for consent in consent_set(participant.competition)
        if consent.kind in wanted
    ]


def make_adult(**kwargs):
    return ParticipantFactory(birth_year=timezone.localdate().year - 25, **kwargs)


def make_minor(**kwargs):
    return ParticipantFactory(birth_year=timezone.localdate().year - 16, **kwargs)


@pytest.fixture
def adult(competition):
    return make_adult(
        competition=competition, user=UserFactory(email="dorosla@example.test", groups=["participant"])
    )


@pytest.fixture
def minor(competition):
    return make_minor(
        competition=competition,
        user=UserFactory(email="uczen@example.test", first_name="Jan", groups=["participant"]),
    )


@pytest.fixture
def web(client_for, competition):
    return client_for(competition)
