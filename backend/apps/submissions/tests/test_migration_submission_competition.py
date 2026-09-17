"""Migracja danych ``submissions.0006``: praca dostaje konkurs **swojego etapu**.

Różnica wobec ``competitions.0020`` jest tu przedmiotem testu, a nie szczegółem. Tamta migracja
przypisuje edycje do jedynego konkursu w bazie, bo edycja jest korzeniem własności. Ta wypełnia
kolumnę **denormalizacyjną**, więc jej wartość nie jest decyzją – jest funkcją
``entry.stage.edition.competition``. Przepisanie jej z „jedynego konkursu” dałoby ten sam wynik na
dzisiejszej produkcji i błędny dzień po dołożeniu drugiego konkursu; stąd test, który uruchamia
backfill na bazie **dwukonkursowej** i sprawdza, że nikt nie przejął cudzych prac.
"""

import importlib

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.competitions.tests.factories import StageFactory
from apps.submissions.models import Submission

from .factories import SubmissionFactory

BEFORE = ("submissions", "0005_submission_competition")
AFTER = ("submissions", "0006_backfill_submission_competition")

#: Nazwa modułu zaczyna się od cyfry, więc ``from … import …`` jest tu składniowo niemożliwe.
backfill = importlib.import_module(f"apps.submissions.migrations.{AFTER[1]}")


def migrate_to(target) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    executor.loader.build_graph()


def migrate_to_head() -> None:
    """Przywraca czoło migracji **wszystkich** aplikacji – patrz bliźniaczy test w ``competitions``."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor.loader.build_graph()


@pytest.fixture
def before_backfill(transactional_db, competition):  # noqa: ARG001 - baza, używana przez efekt uboczny
    """Baza cofnięta do stanu sprzed backfillu prac: kolumna jest, właścicieli nie ma."""
    migrate_to(BEFORE)
    yield competition
    migrate_to_head()


def _orphan(**kwargs) -> Submission:
    """Praca bez właściciela – czyli taka, jaką zastaje wdrożenie.

    ``update`` po utworzeniu, a nie ``competition=None`` w fabryce: kolumnę wypełnia sam
    ``Submission.save()`` (i ma wypełniać), więc stan „zastany” trzeba w teście odtworzyć wprost.
    """
    submission = SubmissionFactory(**kwargs)
    Submission.objects.filter(pk=submission.pk).update(competition=None)
    return submission


@pytest.mark.django_db(transaction=True)
def test_the_backfill_copies_the_owner_from_the_stage(before_backfill):
    competition = before_backfill
    submission = _orphan(competition=competition)

    migrate_to(AFTER)

    assert Submission.objects.get(pk=submission.pk).competition_id == competition.pk


@pytest.mark.django_db(transaction=True)
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


@pytest.mark.django_db(transaction=True)
def test_running_the_backfill_twice_changes_nothing(before_backfill):
    """Idempotencja: wydania B i C stoją obok siebie, więc drugi przebieg jest normalną sytuacją."""
    competition = before_backfill
    submission = _orphan(competition=competition)

    migrate_to(AFTER)
    backfill.forwards(django_apps, connection.schema_editor())

    assert Submission.objects.filter(competition=competition).count() == 1
    assert Submission.objects.get(pk=submission.pk).competition_id == competition.pk


@pytest.mark.django_db(transaction=True)
def test_the_backfill_is_reversible(before_backfill):
    """Cofnięcie zdejmuje właścicieli, bo kolumna zostaje – ``noop`` zostawiłby stan pośredni."""
    competition = before_backfill
    submission = _orphan(competition=competition)

    migrate_to(AFTER)
    migrate_to(BEFORE)

    assert Submission.objects.get(pk=submission.pk).competition_id is None


@pytest.mark.django_db(transaction=True)
def test_the_backfill_on_an_empty_database_does_nothing(before_backfill):  # noqa: ARG001
    """Świeża instalacja: brak prac i brak konkursu to nie jest błąd, tylko pusta baza."""
    from apps.tenancy.models import Competition

    Submission.objects.all().delete()
    Competition.objects.all().delete()

    migrate_to(AFTER)

    assert Submission.objects.count() == 0
