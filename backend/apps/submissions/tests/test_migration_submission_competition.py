"""Migracja danych ``submissions.0006``: praca dostaje konkurs **swojego etapu**.

Różnica wobec ``competitions.0020`` jest tu przedmiotem testu, a nie szczegółem. Tamta migracja
przypisuje edycje do jedynego konkursu w bazie, bo edycja jest korzeniem własności. Ta wypełnia
kolumnę **denormalizacyjną**, więc jej wartość nie jest decyzją – jest funkcją
``entry.stage.edition.competition``. Przepisanie jej z „jedynego konkursu” dałoby ten sam wynik na
dzisiejszej produkcji i błędny dzień po dołożeniu drugiego konkursu; stąd test, który uruchamia
backfill na bazie **dwukonkursowej** i sprawdza, że nikt nie przejął cudzych prac.

Bazę przewija raz na moduł fikstura w transakcji wycofywanej na końcu modułu
(``apps/core/tests/migration_helpers.py``).
"""

import importlib

import pytest
from django.apps import apps as django_apps
from django.db import connection

from apps.competitions.tests.factories import StageFactory
from apps.core.tests.migration_helpers import MIGRATION_TESTS, migrate_to, rewound_database
from apps.submissions.models import Submission

from .factories import SubmissionFactory

pytestmark = MIGRATION_TESTS

BEFORE = ("submissions", "0005_submission_competition")
AFTER = ("submissions", "0006_backfill_submission_competition")

#: Nazwa modułu zaczyna się od cyfry, więc ``from … import …`` jest tu składniowo niemożliwe.
backfill = importlib.import_module(f"apps.submissions.migrations.{AFTER[1]}")


@pytest.fixture(scope="module")
def before_backfill(django_db_setup, django_db_blocker):
    """Baza cofnięta do stanu sprzed backfillu prac: kolumna jest, właścicieli nie ma."""
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db.competition


def _orphan(**kwargs) -> Submission:
    """Praca bez właściciela – czyli taka, jaką zastaje wdrożenie.

    ``update`` po utworzeniu, a nie ``competition=None`` w fabryce: kolumnę wypełnia sam
    ``Submission.save()`` (i ma wypełniać), więc stan „zastany” trzeba w teście odtworzyć wprost.
    """
    submission = SubmissionFactory(**kwargs)
    Submission.objects.filter(pk=submission.pk).update(competition=None)
    return submission


def test_the_backfill_copies_the_owner_from_the_stage(before_backfill):
    competition = before_backfill
    submission = _orphan(competition=competition)

    migrate_to(AFTER)

    assert Submission.objects.get(pk=submission.pk).competition_id == competition.pk


def test_the_backfill_never_moves_work_between_competitions(before_backfill, other_competition):
    """Dwa konkursy naraz: każda praca ma dostać właściciela **swojego** etapu.

    To jest przypadek, którego nie wyłapałby backfill „wszystko do jedynego konkursu”: przeszedłby
    na produkcji i przypisał cudze prace przy pierwszym ponownym uruchomieniu.
    """
    competition = before_backfill
    mine = _orphan(competition=competition)
    theirs = _orphan(competition=other_competition, entry__stage=StageFactory(competition=other_competition))

    backfill.forwards(django_apps, connection.schema_editor())

    assert Submission.objects.get(pk=mine.pk).competition_id == competition.pk
    assert Submission.objects.get(pk=theirs.pk).competition_id == other_competition.pk


def test_running_the_backfill_twice_changes_nothing(before_backfill):
    """Idempotencja: wydania B i C stoją obok siebie, więc drugi przebieg jest normalną sytuacją."""
    competition = before_backfill
    submission = _orphan(competition=competition)

    migrate_to(AFTER)
    backfill.forwards(django_apps, connection.schema_editor())

    assert Submission.objects.filter(competition=competition).count() == 1
    assert Submission.objects.get(pk=submission.pk).competition_id == competition.pk


def test_the_backfill_is_reversible(before_backfill):
    """Cofnięcie zdejmuje właścicieli, bo kolumna zostaje – ``noop`` zostawiłby stan pośredni."""
    competition = before_backfill
    submission = _orphan(competition=competition)

    migrate_to(AFTER)
    migrate_to(BEFORE)

    assert Submission.objects.get(pk=submission.pk).competition_id is None


def test_the_backfill_on_an_empty_database_does_nothing(before_backfill):  # noqa: ARG001
    """Świeża instalacja: brak prac i brak konkursu to nie jest błąd, tylko pusta baza."""
    from apps.tenancy.models import Competition

    Submission.objects.all().delete()
    Competition.objects.all().delete()

    migrate_to(AFTER)

    assert Submission.objects.count() == 0
