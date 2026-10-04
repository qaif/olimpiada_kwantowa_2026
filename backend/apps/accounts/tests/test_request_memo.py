"""Pamięć profilu uczestnika na czas żądania (PERF-01, ``apps.accounts.request_memo``).

Cztery własności, każda z osobnym testem, bo każda chroni przed innym błędem:

- poza żądaniem (komenda, zadanie Celery, test wołający serwis wprost) nic się nie zmienia –
  każde wywołanie ``participant_for`` pyta bazę,
- w żądaniu drugie pytanie o ten sam profil nie dotyka bazy (to jest cały zysk),
- „brak profilu” nie jest zapamiętywany – profil założony w tym samym żądaniu ma być widoczny,
- zapis i skasowanie profilu czyszczą pamięć – żądanie, które zmienia profil, nie czyta stanu sprzed.

Na końcu budżet zapytań prawdziwego ``/me/``: wiersz ``accounts_participant`` czytany **raz**
(test obciążenia z 4.10.2026 naliczył dziewięć odczytów na jedno wejście).
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts import request_memo
from apps.accounts.services import participant_for
from apps.accounts.tests.factories import ParticipantFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def scope():
    token = request_memo.open_scope()
    yield
    request_memo.close_scope(token)


def _participant_queries(captured) -> int:
    """Odczyty **wiersza profilu** (``participant_for``) – nie podzapytania liczników, które tylko
    zaczepiają się o tabelę profili (licznik nieprzeczytanych wiadomości w pasku konta)."""
    return sum(
        1
        for query in captured.captured_queries
        if query["sql"].startswith('SELECT "accounts_participant"."id"')
    )


def test_outside_a_request_every_call_reads_the_database(competition):
    participant = ParticipantFactory(competition=competition)

    with CaptureQueriesContext(connection) as captured:
        assert participant_for(participant.user, competition) == participant
        assert participant_for(participant.user, competition) == participant

    assert _participant_queries(captured) == 2


def test_inside_a_request_the_second_call_is_free(competition, scope):
    participant = ParticipantFactory(competition=competition)

    first = participant_for(participant.user, competition)
    with CaptureQueriesContext(connection) as captured:
        second = participant_for(participant.user, competition)

    assert second is first
    assert _participant_queries(captured) == 0


def test_memo_is_keyed_by_competition(competition, other_competition, scope):
    participant = ParticipantFactory(competition=competition)

    assert participant_for(participant.user, competition) == participant
    # Ten sam człowiek w innym konkursie nie ma profilu – pamięć pierwszego konkursu nie może tego zasłonić.
    assert participant_for(participant.user, other_competition) is None


def test_missing_profile_is_not_remembered(competition, scope):
    user = UserFactory()

    assert participant_for(user, competition) is None
    created = ParticipantFactory(competition=competition, user=user)

    assert participant_for(user, competition) == created


def test_saving_a_profile_clears_the_memo(competition, scope):
    participant = ParticipantFactory(competition=competition)
    participant_for(participant.user, competition)

    participant.school = "XIV LO"
    participant.save()
    with CaptureQueriesContext(connection) as captured:
        fresh = participant_for(participant.user, competition)

    assert _participant_queries(captured) == 1
    assert fresh.school == "XIV LO"


def test_deleting_a_profile_clears_the_memo(competition, scope):
    participant = ParticipantFactory(competition=competition)
    user = participant.user
    participant_for(user, competition)

    participant.delete()

    assert participant_for(user, competition) is None


def test_scope_is_closed_after_the_request(client_for, competition):
    participant = ParticipantFactory(competition=competition)
    client = client_for(competition)
    client.force_login(participant.user)

    client.get("/me/")

    # Po odpowiedzi pamięci już nie ma – kolejne żądanie w tym samym wątku zaczyna od zera.
    assert request_memo._participants.get() is None


def test_participant_panel_reads_the_profile_once(client_for, competition):
    participant = ParticipantFactory(competition=competition)
    client = client_for(competition)
    client.force_login(participant.user)
    client.get("/me/")  # rozgrzanie pamięci procesu (menu, ustawienia witryny)

    with CaptureQueriesContext(connection) as captured:
        response = client.get("/me/")

    assert response.status_code == 200
    assert _participant_queries(captured) == 1
