"""Wspólne dane testów integracji: edycja z etapem, klucz API i klient z nagłówkiem klucza.

Klucze powstają **serwisem**, a nie fabryką modelu: postać jawna klucza istnieje wyłącznie
w chwili wystawienia, więc test, który chce nią uwierzytelnić żądanie, musi przejść tą samą
drogą, co koordynator. Fabryka modelu musiałaby znać skrót i podstawiać sekret obok reguły –
czyli sprawdzać coś innego niż to, co działa na produkcji.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.models import StageEntryStatus, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.integrations.models import SCOPES
from apps.integrations.services import create_api_key, create_endpoint

#: Etap „domknięty”: otwarty 90 dni temu, więc okno reklamacji dawno się zamknęło i wolno
#: publikować wyniki. Ta sama stała, co w testach wyników – i z tego samego powodu.
CLOSED_STAGE_AGE = timedelta(days=90)

WEBHOOK_URL = "https://partner.example.test/hooks/olimpiada"


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def edition():
    return CurrentEditionFactory()


@pytest.fixture
def stage(edition):
    """Etap eliminacyjny ze skalą, progiem i dwoma zadaniami."""
    built = StageFactory(edition=edition, kind=StageKind.ELIM, opens_at=timezone.now() - CLOSED_STAGE_AGE)
    ScoringScaleFactory(stage=built)
    QualificationRuleFactory(stage=built, min_points=0)
    for number in (1, 2):
        ProblemFactory(stage=built, number=number, title=f"Zadanie {number}")
    return built


@pytest.fixture
def entry(stage):
    """Wpis uczestnika do etapu – z rozpoznawalnymi danymi osobowymi, łatwo sprawdzić wyciek."""
    participant = ParticipantFactory(
        user__first_name="Łucja",
        user__last_name="Śniadecka",
        user__email="lucja.sniadecka@example.test",
        school="XIV LO Warszawa",
        district="mazowieckie",
        grade=3,
    )
    return StageEntryFactory(stage=stage, participant=participant, status=StageEntryStatus.REGISTERED)


@pytest.fixture
def make_key(coordinator):
    """Fabryka kluczy: zwraca parę (wiersz, klucz jawny). Domyślnie komplet zakresów."""

    def _make(**kwargs):
        kwargs.setdefault("name", "Partner testowy")
        kwargs.setdefault("scopes", list(SCOPES))
        kwargs.setdefault("pii_allowed", True)
        kwargs.setdefault("actor", coordinator)
        return create_api_key(**kwargs)

    return _make


@pytest.fixture
def authed(api_client, make_key):
    """Klient z nagłówkiem ``Authorization`` dla świeżo wystawionego klucza.

    Zwraca funkcję, bo część testów potrzebuje klucza o zawężonych uprawnieniach – wtedy
    przekazuje własne argumenty i dostaje klienta razem z wierszem klucza.
    """

    def _authed(**kwargs):
        key, token = make_key(**kwargs)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return api_client, key

    return _authed


@pytest.fixture
def endpoint(coordinator):
    """Aktywny odbiorca webhooków zapisany na wszystkie zdarzenia, we wszystkich edycjach."""
    from apps.integrations.models import WEBHOOK_EVENTS

    return create_endpoint(url=WEBHOOK_URL, events=list(WEBHOOK_EVENTS), actor=coordinator)
