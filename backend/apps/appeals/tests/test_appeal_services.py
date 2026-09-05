"""Kryteria 7, 8, 10 T-06 na poziomie serwisów: audyt decyzji, odrzucenie, finalizacja po oknie."""

from datetime import timedelta

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.appeals.models import AppealStatus
from apps.appeals.services import decide_appeal, file_appeal, finalize_unappealed
from apps.appeals.tasks import finalize_closed_appeal_windows
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ROUND_TIEBREAK, FinalGrade, GradeMethod
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import Submission, SubmissionStatus

from .conftest import graded_submission
from .factories import VALID_ARGUMENT, AppealsCommitteeMemberFactory

pytestmark = pytest.mark.django_db


def filed(submission):
    return file_appeal(submission.entry.participant.user, submission, VALID_ARGUMENT)


def test_accepted_decision_writes_audit_with_score_diff(open_stage):
    """7. AuditLog decyzji zawiera diff {old_score, new_score} i nie zawiera danych osobowych."""
    submission = graded_submission(open_stage, score=2)
    appeal = filed(submission)
    member = AppealsCommitteeMemberFactory()

    decision = decide_appeal(appeal, member, AppealStatus.ACCEPTED, 6, "Pełne rozwiązanie.")

    assert decision.new_score == 6
    assert list(decision.committee.all()) == [member]
    entry = AuditLog.objects.get(action="appeal.decided")
    assert entry.diff["old_score"] == 2
    assert entry.diff["new_score"] == 6
    assert entry.diff["submission_id"] == submission.pk
    assert entry.actor_id == member.user_id
    grade = FinalGrade.objects.get(submission=submission)
    assert (grade.score, grade.method, grade.decided_by_id) == (6, GradeMethod.APPEAL, member.user_id)
    assert grade.rationale == "Pełne rozwiązanie."


def test_rejected_decision_keeps_grade_and_finalizes(open_stage):
    """8. REJECTED → FinalGrade bez zmian, Submission FINAL."""
    submission = graded_submission(open_stage, score=5)
    appeal = filed(submission)
    before = FinalGrade.objects.get(submission=submission)

    decide_appeal(appeal, AppealsCommitteeMemberFactory(), AppealStatus.REJECTED, None, "Zarzut chybiony.")

    grade = FinalGrade.objects.get(submission=submission)
    assert (grade.score, grade.method) == (before.score, before.method)
    assert grade.decided_at == before.decided_at
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.FINAL
    appeal.refresh_from_db()
    assert appeal.status == AppealStatus.REJECTED
    assert AuditLog.objects.get(action="appeal.decided").diff["new_score"] is None


def test_rejected_decision_cannot_carry_new_score(open_stage):
    """REJECTED z ``new_score`` → 400: odrzucenie nie zmienia punktacji."""
    appeal = filed(graded_submission(open_stage, score=5))

    with pytest.raises(DomainError) as excinfo:
        decide_appeal(appeal, AppealsCommitteeMemberFactory(), AppealStatus.REJECTED, 6, "Niespójne.")

    assert excinfo.value.machine_code == "UNEXPECTED_NEW_SCORE"


def test_accepted_decision_requires_changed_score(open_stage):
    """ACCEPTED z tą samą punktacją → 400: to nie jest zmiana oceny."""
    appeal = filed(graded_submission(open_stage, score=5))

    with pytest.raises(DomainError) as excinfo:
        decide_appeal(appeal, AppealsCommitteeMemberFactory(), AppealStatus.ACCEPTED, 5, "Bez zmiany.")

    assert excinfo.value.machine_code == "SCORE_UNCHANGED"


def test_tiebreak_reviewer_is_also_in_conflict(open_stage):
    """Konflikt interesów obejmuje także autora recenzji rundy 2 (rozjemczej)."""
    submission = graded_submission(open_stage, score=2)
    appeal = filed(submission)
    member = AppealsCommitteeMemberFactory()
    ReviewFactory(submission=submission, reviewer=member, round=ROUND_TIEBREAK, score=2)

    with pytest.raises(DomainError) as excinfo:
        decide_appeal(appeal, member, AppealStatus.ACCEPTED, 6, "Zmiana oceny.")

    assert excinfo.value.machine_code == "CONFLICT_OF_INTEREST"


def test_partially_accepted_decision_updates_grade(open_stage):
    """PARTIALLY_ACCEPTED działa tak samo jak ACCEPTED: nowa punktacja z metodą APPEAL."""
    submission = graded_submission(open_stage, score=0)
    appeal = filed(submission)

    decide_appeal(appeal, AppealsCommitteeMemberFactory(), AppealStatus.PARTIALLY_ACCEPTED, 2, "Postęp.")

    grade = FinalGrade.objects.get(submission=submission)
    assert (grade.score, grade.method) == (2, GradeMethod.APPEAL)
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.FINAL


def test_finalize_unappealed_after_window_leaves_appealed_untouched(open_stage):
    """10. Po zamknięciu okna GRADED_PROVISIONAL → FINAL, APPEALED bez zmian."""
    untouched = graded_submission(open_stage, score=2)
    appealed = graded_submission(open_stage, score=2)
    filed(appealed)

    # Przed zamknięciem okna serwis nie robi niczego.
    assert finalize_unappealed(open_stage) == 0
    assert Submission.objects.get(pk=untouched.pk).status == SubmissionStatus.GRADED_PROVISIONAL

    with freeze_time(open_stage.appeal_window_closes_at + timedelta(seconds=1)):
        finalized = finalize_unappealed(open_stage)

    assert finalized == 1
    assert Submission.objects.get(pk=untouched.pk).status == SubmissionStatus.FINAL
    assert Submission.objects.get(pk=appealed.pk).status == SubmissionStatus.APPEALED


def test_finalize_beat_task_is_idempotent(open_stage, stage_after_window):
    """Beat ``finalize_closed_appeal_windows`` obsługuje tylko etapy po oknie i jest idempotentny."""
    still_open = graded_submission(open_stage, score=2)
    closed = graded_submission(stage_after_window, score=2)

    first = finalize_closed_appeal_windows()

    assert first == {str(stage_after_window.pk): 1}
    assert Submission.objects.get(pk=closed.pk).status == SubmissionStatus.FINAL
    assert Submission.objects.get(pk=still_open.pk).status == SubmissionStatus.GRADED_PROVISIONAL
    assert finalize_closed_appeal_windows() == {}


def test_appeal_outside_graded_state_is_rejected(open_stage):
    """Reklamacja ma sens tylko na ocenie wstępnej – rozwiązanie w ocenie daje 409."""
    submission = graded_submission(open_stage, score=2)
    Submission.objects.filter(pk=submission.pk).update(status=SubmissionStatus.IN_REVIEW)
    submission.refresh_from_db()

    with pytest.raises(DomainError) as excinfo:
        filed(submission)

    assert excinfo.value.machine_code == "SUBMISSION_NOT_GRADED"


def test_filed_appeal_writes_audit_without_personal_data(open_stage):
    """Ślad audytowy złożenia reklamacji zawiera wyłącznie identyfikatory i długość uzasadnienia."""
    submission = graded_submission(open_stage, score=2)

    appeal = filed(submission)

    entry = AuditLog.objects.get(action="appeal.filed")
    assert entry.target_id == str(appeal.pk)
    assert set(entry.diff) == {"submission_id", "stage_id", "argument_length"}
    assert entry.at <= timezone.now()
