"""Zapis obecności → jedno zadanie na transakcję (WS-CERT-02 § 2).

Najważniejszy test pliku to import trzystu osób naraz: w żądaniu mają paść zapytania zapisu i jedno
zapytanie po commicie, a do brokera – **jedno** zadanie z kompletem identyfikatorów.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.db import transaction

from apps.accounts.tests.factories import ParticipantFactory
from apps.cms.models import WorkshopAttendance
from apps.cms.workshops import save_attendance
from apps.results.models import Certificate
from apps.workshop_certificates import tasks

from .helpers import enable, keys, participant_in

pytestmark = pytest.mark.django_db


@pytest.fixture
def delay():
    with mock.patch.object(tasks.issue_for_participants, "delay") as patched:
        yield patched


def test_mass_attendance_save_queues_exactly_one_task(
    stage, delay, django_capture_on_commit_callbacks, django_assert_max_num_queries
):
    enable(stage.edition, threshold=1)
    participants = ParticipantFactory.create_batch(300)
    ids = [participant.pk for participant in participants]
    marked = {(pk, keys()[0]) for pk in ids}

    # Odczyt istniejących, wstawienie hurtem i – po commicie – jedno „czy reguła jest włączona”.
    with django_assert_max_num_queries(4):
        with django_capture_on_commit_callbacks(execute=True):
            result = save_attendance(ids, keys(), marked)

    assert result["added"] == 300
    delay.assert_called_once()
    assert sorted(delay.call_args.args[0]) == sorted(ids)


def test_several_writes_in_one_transaction_queue_one_task(stage, delay, django_capture_on_commit_callbacks):
    enable(stage.edition, threshold=1)
    first, second = ParticipantFactory.create_batch(2)

    with django_capture_on_commit_callbacks(execute=True):
        WorkshopAttendance.objects.bulk_create(
            [WorkshopAttendance(participant=first, workshop_key=keys()[0])]
        )
        WorkshopAttendance.objects.bulk_create(
            [WorkshopAttendance(participant=second, workshop_key=keys()[0])]
        )
        WorkshopAttendance.objects.create(participant=second, workshop_key=keys()[1])

    delay.assert_called_once_with([first.pk, second.pk])


def test_rolled_back_write_queues_nothing_and_does_not_block_the_next(
    stage, delay, django_capture_on_commit_callbacks
):
    enable(stage.edition, threshold=1)
    rolled_back, kept = ParticipantFactory.create_batch(2)

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            WorkshopAttendance.objects.bulk_create(
                [WorkshopAttendance(participant=rolled_back, workshop_key=keys()[0])]
            )
            raise RuntimeError("wycofaj")
    delay.assert_not_called()

    with django_capture_on_commit_callbacks(execute=True):
        WorkshopAttendance.objects.bulk_create([WorkshopAttendance(participant=kept, workshop_key=keys()[0])])
    delay.assert_called_once_with([kept.pk])


def test_no_enabled_rule_queues_nothing(stage, delay, django_capture_on_commit_callbacks):
    participant = ParticipantFactory()

    with django_capture_on_commit_callbacks(execute=True):
        WorkshopAttendance.objects.create(participant=participant, workshop_key=keys()[0])

    delay.assert_not_called()


def test_broker_failure_after_commit_does_not_raise(stage, django_capture_on_commit_callbacks):
    enable(stage.edition, threshold=1)
    participant = ParticipantFactory()

    with mock.patch.object(tasks.issue_for_participants, "delay", side_effect=ConnectionError("broker")):
        with django_capture_on_commit_callbacks(execute=True):
            WorkshopAttendance.objects.create(participant=participant, workshop_key=keys()[0])

    assert WorkshopAttendance.objects.filter(participant=participant).exists()


def test_attendance_save_issues_the_certificate_end_to_end(stage, django_capture_on_commit_callbacks):
    """Bez atrapy: Celery w testach jest synchroniczny, więc po commicie dokument już jest."""
    enable(stage.edition, threshold=2)
    participant = participant_in(stage)

    with django_capture_on_commit_callbacks(execute=True):
        save_attendance([participant.pk], keys(), {(participant.pk, keys()[0])})
    assert not Certificate.objects.exists()

    with django_capture_on_commit_callbacks(execute=True):
        save_attendance([participant.pk], keys(), {(participant.pk, key) for key in keys()[:2]})
    assert Certificate.objects.filter(entry__participant=participant).count() == 1


def test_task_ignores_participants_of_a_competition_without_a_rule(stage, other_competition):
    from .helpers import current_stage, workshops_page_for

    enable(stage.edition, threshold=1)
    workshops_page_for(other_competition)
    other_stage = current_stage(other_competition)
    stranger = participant_in(other_stage)
    for key in keys():
        WorkshopAttendance.objects.create(participant=stranger, workshop_key=key)

    assert tasks.issue_for_participants([stranger.pk]) == 0
    assert not Certificate.objects.exists()
