"""Ocenianie przed zamknięciem etapu: blokada prac do oceny przy otwartym oknie uploadu.

Reguła wyboru wersji jest wspólna z ``close_stage`` (``lockable_submission_ids``), więc testy
pilnują przede wszystkim tego, co odróżnia obie drogi: brak ``closed_at``, jawne odmowy przy
blokadzie pojedynczej pracy i ślad w audycie.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import CoordinatorFactory, UserFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.services import (
    lock_for_review,
    lock_submission_for_review,
    review_counters,
)
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def stage():
    return StageFactory()


@pytest.fixture
def problem(stage):
    return ProblemFactory(stage=stage, number=1)


@pytest.fixture
def entry(stage):
    return StageEntryFactory(stage=stage)


def statuses(entry, problem) -> list[str]:
    return list(
        Submission.objects.filter(entry=entry, problem=problem)
        .order_by("version")
        .values_list("status", flat=True)
    )


def test_locks_latest_version_and_leaves_stage_open(stage, entry, problem):
    """Sedno prośby organizatora: prace wchodzą do oceny, a uczestnik nadal może wysyłać wersje."""
    for version in (1, 2):
        SubmissionFactory(entry=entry, problem=problem, version=version)

    locked = lock_for_review(stage)

    stage.refresh_from_db()
    assert locked == 1
    assert stage.closed_at is None
    assert statuses(entry, problem) == [SubmissionStatus.SUBMITTED, SubmissionStatus.LOCKED]


def test_leaves_scanning_versions_to_the_scanner(stage, entry, problem):
    """Wersja w skanie należy do ``apply_scan_verdict`` – blokada jej nie dotyka."""
    SubmissionFactory(entry=entry, problem=problem, version=1, status=SubmissionStatus.SCANNING)

    assert lock_for_review(stage) == 0
    assert statuses(entry, problem) == [SubmissionStatus.SCANNING]


def test_infected_version_falls_back_to_the_previous_one(stage, entry, problem):
    """Wirus w najnowszej wersji nie zjada etapu: do oceny wchodzi ostatnia wcześniejsza."""
    SubmissionFactory(entry=entry, problem=problem, version=1)
    SubmissionFactory(entry=entry, problem=problem, version=2, status=SubmissionStatus.REJECTED_INFECTED)

    assert lock_for_review(stage) == 1
    assert statuses(entry, problem) == [SubmissionStatus.LOCKED, SubmissionStatus.REJECTED_INFECTED]


def test_is_idempotent(stage, entry, problem):
    SubmissionFactory(entry=entry, problem=problem, version=1)
    lock_for_review(stage)

    assert lock_for_review(stage) == 0
    assert statuses(entry, problem) == [SubmissionStatus.LOCKED]


def test_closed_stage_returns_zero_instead_of_refusing(stage, entry, problem):
    """Na zamkniętym etapie wszystko jest już zablokowane – to nie jest błąd, tylko brak roboty."""
    SubmissionFactory(entry=entry, problem=problem, version=1, status=SubmissionStatus.LOCKED)
    stage.closed_at = stage.deadline_at
    stage.save(update_fields=["closed_at"])

    assert lock_for_review(stage) == 0


def test_writes_audit_entry_with_the_count(stage, entry, problem):
    SubmissionFactory(entry=entry, problem=problem, version=1)
    coordinator = CoordinatorFactory()

    lock_for_review(stage, actor=coordinator)

    entry_log = AuditLog.objects.get(action="stage.locked_for_review")
    assert entry_log.diff["locked"] == 1
    assert entry_log.actor == coordinator


def test_counters_count_pairs_not_rows(stage, entry, problem):
    """„Oddane” liczy prace do zablokowania, a nie wszystkie wiersze ze statusem SUBMITTED."""
    SubmissionFactory(entry=entry, problem=problem, version=1)
    SubmissionFactory(entry=entry, problem=problem, version=2)
    other = ProblemFactory(stage=stage, number=2)
    SubmissionFactory(entry=entry, problem=other, status=SubmissionStatus.IN_REVIEW)

    assert review_counters(stage) == {"submitted": 1, "under_review": 1}


# --- blokada pojedynczej pracy ------------------------------------------------------------


def test_single_submission_lock(entry, problem):
    submission = SubmissionFactory(entry=entry, problem=problem, version=1)

    lock_submission_for_review(submission)

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.LOCKED
    assert AuditLog.objects.filter(action="submission.locked_for_review").count() == 1


def test_single_submission_refuses_older_version(entry, problem):
    older = SubmissionFactory(entry=entry, problem=problem, version=1)
    SubmissionFactory(entry=entry, problem=problem, version=2)

    with pytest.raises(DomainError) as exc:
        lock_submission_for_review(older)

    older.refresh_from_db()
    assert exc.value.machine_code == "NOT_LATEST_VERSION"
    assert older.status == SubmissionStatus.SUBMITTED


def test_single_submission_ignores_newer_infected_version(entry, problem):
    """Nowsza wersja odrzucona przez antywirusa nie blokuje starszej – tak samo jak w etapie."""
    older = SubmissionFactory(entry=entry, problem=problem, version=1)
    SubmissionFactory(entry=entry, problem=problem, version=2, status=SubmissionStatus.REJECTED_INFECTED)

    lock_submission_for_review(older)

    older.refresh_from_db()
    assert older.status == SubmissionStatus.LOCKED


@pytest.mark.parametrize(
    "status", [SubmissionStatus.SCANNING, SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW]
)
def test_single_submission_refuses_other_statuses(entry, problem, status):
    submission = SubmissionFactory(entry=entry, problem=problem, version=1, status=status)

    with pytest.raises(DomainError) as exc:
        lock_submission_for_review(submission)

    assert exc.value.machine_code == "SUBMISSION_NOT_LOCKABLE"


# --- API ----------------------------------------------------------------------------------


def stage_url(stage) -> str:
    return reverse("submissions:stage-lock-for-review", kwargs={"stage_id": stage.pk})


def submission_url(submission) -> str:
    return reverse("submissions:submission-lock-for-review", kwargs={"pk": submission.pk})


def test_api_stage_lock_returns_count(api_client, stage, entry, problem):
    SubmissionFactory(entry=entry, problem=problem, version=1)
    api_client.force_authenticate(CoordinatorFactory())

    response = api_client.post(stage_url(stage))

    stage.refresh_from_db()
    assert response.status_code == 200, response.data
    assert response.data["locked"] == 1
    assert stage.closed_at is None


def test_api_submission_lock_returns_submission(api_client, entry, problem):
    submission = SubmissionFactory(entry=entry, problem=problem, version=1)
    api_client.force_authenticate(CoordinatorFactory())

    response = api_client.post(submission_url(submission))

    assert response.status_code == 200, response.data
    assert response.data["status"] == SubmissionStatus.LOCKED


def test_api_submission_lock_reports_domain_refusal(api_client, entry, problem):
    submission = SubmissionFactory(entry=entry, problem=problem, version=1, status=SubmissionStatus.LOCKED)
    api_client.force_authenticate(CoordinatorFactory())

    response = api_client.post(submission_url(submission))

    assert response.status_code == 409
    assert response.data["code"] == "SUBMISSION_NOT_LOCKABLE"


@pytest.mark.parametrize("anonymous", [True, False])
def test_api_is_closed_to_everyone_but_the_coordinator(api_client, stage, anonymous):
    if not anonymous:
        api_client.force_authenticate(UserFactory(groups=["participant"]))

    response = api_client.post(stage_url(stage))

    assert response.status_code in (401, 403)
