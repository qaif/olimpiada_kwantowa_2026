"""Kryterium 9: ``close_stage`` blokuje wyłącznie najnowszą wersję i jest idempotentny."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.competitions.models import Stage
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.services import close_due_stages, close_stage, due_stages
from apps.submissions.tasks import close_due_stages as close_due_stages_task
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def three_versions():
    stage = StageFactory()
    problem = ProblemFactory(stage=stage, number=1)
    entry = StageEntryFactory(stage=stage)
    versions = [
        SubmissionFactory(entry=entry, problem=problem, version=number, status=SubmissionStatus.SUBMITTED)
        for number in (1, 2, 3)
    ]
    return stage, entry, problem, versions


def statuses(entry, problem) -> list[str]:
    return list(
        Submission.objects.filter(entry=entry, problem=problem)
        .order_by("version")
        .values_list("status", flat=True)
    )


def test_close_stage_locks_only_latest_version(three_versions):
    stage, entry, problem, _ = three_versions

    locked = close_stage(stage)

    assert locked == 1
    assert statuses(entry, problem) == [
        SubmissionStatus.SUBMITTED,
        SubmissionStatus.SUBMITTED,
        SubmissionStatus.LOCKED,
    ]


def test_close_stage_is_idempotent(three_versions):
    stage, entry, problem, _ = three_versions
    close_stage(stage)

    assert close_stage(stage) == 0
    assert statuses(entry, problem) == [
        SubmissionStatus.SUBMITTED,
        SubmissionStatus.SUBMITTED,
        SubmissionStatus.LOCKED,
    ]


def test_close_stage_handles_many_entries_and_problems():
    stage = StageFactory()
    problems = [ProblemFactory(stage=stage, number=number) for number in (1, 2)]
    entries = [StageEntryFactory(stage=stage) for _ in range(2)]
    for entry in entries:
        for problem in problems:
            SubmissionFactory(entry=entry, problem=problem, version=1)
            SubmissionFactory(entry=entry, problem=problem, version=2)

    assert close_stage(stage) == 4
    assert Submission.objects.filter(status=SubmissionStatus.LOCKED, version=2).count() == 4
    assert Submission.objects.filter(status=SubmissionStatus.SUBMITTED, version=1).count() == 4


def test_due_stages_skips_stages_before_deadline():
    open_stage = StageFactory()
    past = StageFactory(
        edition=open_stage.edition,
        kind="DISTRICT",
        opens_at=timezone.now() - timedelta(days=30),
        deadline_at=timezone.now() - timedelta(minutes=5),
        grace_seconds=0,
        review_deadline_at=timezone.now() + timedelta(days=1),
        appeal_window_opens_at=timezone.now() + timedelta(days=2),
        appeal_window_closes_at=timezone.now() + timedelta(days=3),
    )

    assert [stage.pk for stage in due_stages()] == [past.pk]
    assert open_stage.pk not in [stage.pk for stage in due_stages()]


def test_due_stages_respects_grace_seconds():
    stage = StageFactory(
        opens_at=timezone.now() - timedelta(days=30),
        deadline_at=timezone.now() - timedelta(seconds=30),
        grace_seconds=600,
        review_deadline_at=timezone.now() + timedelta(days=1),
        appeal_window_opens_at=timezone.now() + timedelta(days=2),
        appeal_window_closes_at=timezone.now() + timedelta(days=3),
    )

    # Deadline minął, ale tolerancja jeszcze trwa – etapu nie wolno zamknąć.
    assert stage.pk not in [item.pk for item in due_stages()]


def test_close_due_stages_marks_stage_closed_once(three_versions):
    stage, entry, problem, _ = three_versions
    stage.deadline_at = timezone.now() - timedelta(minutes=1)
    stage.opens_at = timezone.now() - timedelta(days=30)
    stage.save(update_fields=["deadline_at", "opens_at"])

    assert close_due_stages() == [stage.pk]
    stage.refresh_from_db()
    assert stage.closed_at is not None
    assert statuses(entry, problem)[-1] == SubmissionStatus.LOCKED

    # Drugi przebieg nie ma już co zamykać – closed_at działa jak bezpiecznik idempotencji.
    assert close_due_stages() == []


def test_beat_task_delegates_to_service(three_versions):
    stage, _, _, _ = three_versions
    Stage.objects.filter(pk=stage.pk).update(
        opens_at=timezone.now() - timedelta(days=30), deadline_at=timezone.now() - timedelta(minutes=1)
    )

    assert close_due_stages_task.apply().get() == [stage.pk]
