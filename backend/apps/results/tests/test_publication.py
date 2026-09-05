"""Kryterium 4 T-07: anonimizacja snapshotu (RODO) i ponowna publikacja."""

import json

import pytest

from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.models import QualificationMode, StageEntryStatus
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.results.models import Anonymization, ResultsPublication
from apps.results.services import publish_results

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db

FIRST_NAME = "Zenobia"
LAST_NAME = "Niepublikowalna"
EMAIL = "zenobia.tajna@example.test"
SCHOOL = "XIV LO Warszawa"


def consenting_participant(*, publish_full_name: bool):
    """Uczestnik o rozpoznawalnych danych – łatwo sprawdzić, czy nie wyciekły do snapshotu."""
    user = UserFactory(email=EMAIL, first_name=FIRST_NAME, last_name=LAST_NAME)
    return ParticipantFactory(
        user=user,
        school=SCHOOL,
        district="mazowiecki",
        birth_year=2009,
        publish_full_name=publish_full_name,
    )


def publish(stage, anonymization, actor=None):
    return publish_results(stage, actor, anonymization)


def test_code_snapshot_has_no_personal_data():
    """4. Snapshot CODE: wyłącznie pseudonim – bez imienia, nazwiska, e-maila, szkoły i id."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))

    publication = publish(stage, Anonymization.CODE)

    raw = json.dumps(publication.snapshot, ensure_ascii=False)
    for secret in (FIRST_NAME, LAST_NAME, EMAIL, SCHOOL, "2009"):
        assert secret not in raw
    assert publication.snapshot[0]["display"] == entry.participant.public_code
    assert set(publication.snapshot[0]) == {
        "rank",
        "display",
        "district",
        "points",
        "total",
        "qualified",
    }


def test_full_snapshot_needs_participant_consent():
    """4. FULL: nazwisko tylko przy ``publish_full_name=True``; bez zgody zostaje pseudonim."""
    stage = make_stage(problems=1)
    consenting = graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))
    refusing = graded_entry(stage, [5], participant=ParticipantFactory(publish_full_name=False))

    publication = publish(stage, Anonymization.FULL)

    displays = {row["rank"]: row["display"] for row in publication.snapshot}
    assert displays[1] == f"{FIRST_NAME} {LAST_NAME}"
    assert displays[2] == refusing.participant.public_code
    assert consenting.participant.public_code not in json.dumps(publication.snapshot)
    assert EMAIL not in json.dumps(publication.snapshot, ensure_ascii=False)


def test_initials_school_snapshot():
    """4. INITIALS_SCHOOL: „Z.N., XIV LO Warszawa” – bez pełnego imienia i nazwiska."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))

    publication = publish(stage, Anonymization.INITIALS_SCHOOL)

    assert publication.snapshot[0]["display"] == f"Z.N., {SCHOOL}"
    raw = json.dumps(publication.snapshot, ensure_ascii=False)
    assert FIRST_NAME not in raw
    assert LAST_NAME not in raw
    assert EMAIL not in raw


def test_snapshot_carries_points_ranking_and_qualification():
    """Snapshot ma komplet danych tabeli: miejsce, punkty per zadanie, sumę i kwalifikację."""
    stage = make_stage(problems=2, mode=QualificationMode.MIN_POINTS, min_points=8)
    graded_entry(stage, [6, 5])
    graded_entry(stage, [2, 2])

    publication = publish(stage, Anonymization.CODE)

    first, second = publication.snapshot
    assert first["rank"] == 1
    assert first["points"] == {"1": 6, "2": 5}
    assert first["total"] == 11
    assert first["qualified"] is True
    assert second["qualified"] is False


def test_publishing_sets_stage_marker_and_qualification_statuses():
    """Publikacja przelicza, kwalifikuje i stawia znacznik ``Stage.results_published_at``."""
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=5)
    entry = graded_entry(stage, [6])

    publication = publish(stage, Anonymization.CODE)

    stage.refresh_from_db()
    entry.refresh_from_db()
    assert stage.results_published_at == publication.published_at
    assert entry.status == StageEntryStatus.QUALIFIED
    assert entry.total_points == 6


def test_republish_overwrites_snapshot_and_leaves_audit_without_personal_data():
    """Ponowna publikacja nadpisuje ten sam rekord i zostawia ślad w audycie bez danych osobowych."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))

    publish(stage, Anonymization.CODE)
    publication = publish(stage, Anonymization.INITIALS_SCHOOL)

    assert ResultsPublication.objects.count() == 1
    assert publication.anonymization == Anonymization.INITIALS_SCHOOL
    assert publication.snapshot[0]["display"] == f"Z.N., {SCHOOL}"
    entries = AuditLog.objects.filter(action="results.published").order_by("id")
    assert entries.count() == 2
    assert entries.last().diff["republished"] is True
    raw = json.dumps([entry.diff for entry in entries], ensure_ascii=False)
    for secret in (FIRST_NAME, LAST_NAME, EMAIL, SCHOOL):
        assert secret not in raw


def test_unknown_anonymization_is_rejected():
    """Tryb anonimizacji spoza listy → 400, zanim cokolwiek zostanie opublikowane."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])

    with pytest.raises(DomainError) as error:
        publish(stage, "PESEL")

    assert error.value.machine_code == "INVALID_ANONYMIZATION"
    assert error.value.status_code == 400
    assert not ResultsPublication.objects.exists()
