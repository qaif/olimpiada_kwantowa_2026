"""Korekta ocen przez koordynatora: punkty pojedynczej recenzji i ocena końcowa pracy.

Tryb ostatniej instancji, więc testy pilnują przede wszystkim granic: skali punktacji, ponownego
rozstrzygnięcia rundy 1 po zmianie oceny, obowiązkowego uzasadnienia korekty i tego, że ogłoszona
tabela wyników nie zmienia się po cichu.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import (
    ROUND_BLIND,
    FinalGrade,
    GradeMethod,
    Review,
    ReviewStatus,
)
from apps.grading.services import (
    assign_reviewer_to_submission,
    override_final_grade,
    set_review_score,
)
from apps.grading.tests.factories import ReviewFactory
from apps.results.models import ResultsPublication
from apps.submissions.models import Submission, SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def two_assigned_reviews(submission):
    """Dwie recenzje rundy 1 w stanie ASSIGNED – tak wygląda praca zaraz po przydziale."""
    return [
        assign_reviewer_to_submission(submission, ActiveReviewerFactory()),
        assign_reviewer_to_submission(submission, ActiveReviewerFactory()),
    ]


# --- punkty pojedynczej recenzji ---------------------------------------------------------------


def test_score_outside_the_scale_is_refused(stage):
    """Skala etapu obowiązuje także koordynatora – inaczej rozjeżdża się tabela wyników."""
    review = assign_reviewer_to_submission(locked_submission(stage), ActiveReviewerFactory())

    with pytest.raises(DomainError) as exc:
        set_review_score(review, 4, actor=CoordinatorFactory())

    assert exc.value.machine_code == "SCORE_NOT_IN_SCALE"
    review.refresh_from_db()
    assert review.score is None


def test_score_on_assigned_review_marks_it_submitted_with_a_note(stage):
    """Recenzja, której nikt nie oddał: koordynator wpisuje punkty, a ślad zostaje w komentarzu."""
    review = assign_reviewer_to_submission(locked_submission(stage), ActiveReviewerFactory())
    coordinator = CoordinatorFactory()

    set_review_score(review, 5, actor=coordinator, rationale="Ustalenie komisji z 12.05.")

    review.refresh_from_db()
    assert review.status == ReviewStatus.SUBMITTED
    assert review.submitted_at is not None
    assert review.score == 5
    assert "[koordynator]" in review.comment_internal
    assert "Ustalenie komisji" in review.comment_internal
    entry = AuditLog.objects.get(action="review.score_set_by_coordinator")
    assert entry.diff["from"] is None
    assert entry.diff["to"] == 5


def test_agreeing_scores_after_the_edit_produce_a_consensus_grade(stage):
    """Po korekcie system ponownie rozstrzyga rundę 1: zgodne oceny → ocena uzgodniona."""
    submission = locked_submission(stage)
    first, second = two_assigned_reviews(submission)
    coordinator = CoordinatorFactory()

    set_review_score(first, 5, actor=coordinator)
    assert not FinalGrade.objects.filter(submission=submission).exists()
    set_review_score(second, 5, actor=coordinator)

    grade = FinalGrade.objects.get(submission=submission)
    assert (grade.score, grade.method) == (5, GradeMethod.CONSENSUS)
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_differing_scores_after_the_edit_send_the_work_to_moderation(stage):
    """Rozjazd po korekcie kieruje pracę do moderacji, tak samo jak rozjazd dwóch recenzentów."""
    submission = locked_submission(stage)
    first, second = two_assigned_reviews(submission)
    coordinator = CoordinatorFactory()

    set_review_score(first, 2, actor=coordinator)
    set_review_score(second, 6, actor=coordinator)

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.MODERATION
    assert not FinalGrade.objects.filter(submission=submission).exists()


def test_editing_a_submitted_review_keeps_its_status_and_records_the_previous_score(stage):
    """Korekta oceny już wystawionej: status bez zmian, audyt z wartością przed i po."""
    submission = locked_submission(stage)
    review = ReviewFactory(submission=submission, round=ROUND_BLIND, status=ReviewStatus.SUBMITTED, score=2)

    set_review_score(review, 6, actor=CoordinatorFactory())

    review.refresh_from_db()
    assert (review.score, review.status) == (6, ReviewStatus.SUBMITTED)
    assert "[koordynator]" not in review.comment_internal
    entry = AuditLog.objects.get(action="review.score_set_by_coordinator")
    assert (entry.diff["from"], entry.diff["to"]) == (2, 6)


def test_editing_a_review_of_a_graded_work_does_not_touch_the_final_grade(stage):
    """Praca z oceną uzgodnioną: korekta recenzji nie przelicza oceny końcowej po cichu."""
    submission = locked_submission(stage)
    first, second = two_assigned_reviews(submission)
    coordinator = CoordinatorFactory()
    set_review_score(first, 5, actor=coordinator)
    set_review_score(second, 5, actor=coordinator)

    set_review_score(first, 2, actor=coordinator)

    grade = FinalGrade.objects.get(submission=submission)
    assert (grade.score, grade.method) == (5, GradeMethod.CONSENSUS)


# --- ocena końcowa -----------------------------------------------------------------------------


def test_override_replaces_a_consensus_grade_and_audits_the_change(stage):
    submission = locked_submission(stage)
    first, second = two_assigned_reviews(submission)
    coordinator = CoordinatorFactory()
    set_review_score(first, 5, actor=coordinator)
    set_review_score(second, 5, actor=coordinator)

    result = override_final_grade(
        submission, 6, rationale="Komisja uznała rozwiązanie za pełne.", actor=coordinator
    )

    assert result["results_stale"] is False
    grade = FinalGrade.objects.get(submission=submission)
    assert (grade.score, grade.method) == (6, GradeMethod.COORDINATOR_OVERRIDE)
    assert grade.decided_by == coordinator
    entry = AuditLog.objects.get(action="grade.overridden")
    assert (entry.diff["from"], entry.diff["to"]) == (5, 6)
    assert entry.diff["from_method"] == GradeMethod.CONSENSUS


def test_override_requires_a_rationale(stage):
    submission = locked_submission(stage)

    with pytest.raises(DomainError) as exc:
        override_final_grade(submission, 5, rationale="krótko", actor=CoordinatorFactory())

    assert exc.value.machine_code == "RATIONALE_REQUIRED"
    assert not FinalGrade.objects.exists()


def test_override_rejects_a_score_outside_the_scale(stage):
    submission = locked_submission(stage)

    with pytest.raises(DomainError) as exc:
        override_final_grade(submission, 4, rationale="Decyzja komisji z posiedzenia.", actor=None)

    assert exc.value.machine_code == "SCORE_NOT_IN_SCALE"


def test_override_works_for_a_submission_without_any_review(stage):
    """Praca, której nikt nie recenzował: koordynator wpisuje ocenę wprost."""
    submission = locked_submission(stage)

    result = override_final_grade(
        submission, 2, rationale="Brak chętnych recenzentów, ocena komisji.", actor=CoordinatorFactory()
    )

    assert result["cancelled_reviews"] == 0
    grade = FinalGrade.objects.get(submission=submission)
    assert (grade.score, grade.method) == (2, GradeMethod.COORDINATOR_OVERRIDE)
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_override_cancels_pending_reviews(stage):
    """Praca z wpisaną oceną końcową znika z kolejek recenzentów; wystawione oceny zostają."""
    submission = locked_submission(stage)
    pending, started = two_assigned_reviews(submission)
    started.status = ReviewStatus.DRAFT
    started.save(update_fields=["status"])
    done = ReviewFactory(submission=submission, status=ReviewStatus.SUBMITTED, score=2, round=2)

    result = override_final_grade(
        submission, 6, rationale="Rozstrzygnięcie komisji na posiedzeniu.", actor=CoordinatorFactory()
    )

    assert result["cancelled_reviews"] == 2
    pending.refresh_from_db()
    started.refresh_from_db()
    done.refresh_from_db()
    assert pending.status == ReviewStatus.CANCELLED
    assert started.status == ReviewStatus.CANCELLED
    assert done.status == ReviewStatus.SUBMITTED


def test_override_refuses_a_work_still_open_for_uploads(stage):
    """Przed zamknięciem etapu uczestnik może podmienić plik – ocena dotyczyłaby nie tej wersji."""
    submission = locked_submission(stage)
    submission.status = SubmissionStatus.SUBMITTED
    submission.save(update_fields=["status"])

    with pytest.raises(DomainError) as exc:
        override_final_grade(submission, 5, rationale="Decyzja komisji z posiedzenia.", actor=None)

    assert exc.value.machine_code == "SUBMISSION_NOT_GRADABLE"


def test_override_on_published_stage_reports_stale_results_without_touching_the_snapshot(stage):
    """Ogłoszona tabela jest dokumentem: korekta wchodzi, ale snapshot zostaje nietknięty."""
    submission = locked_submission(stage)
    publication = ResultsPublication.objects.create(stage=stage, snapshot=[{"place": 1}])
    snapshot_before = publication.snapshot

    result = override_final_grade(
        submission, 6, rationale="Uwzględniona reklamacja z posiedzenia.", actor=CoordinatorFactory()
    )

    assert result["results_stale"] is True
    publication.refresh_from_db()
    assert publication.snapshot == snapshot_before
    assert AuditLog.objects.get(action="grade.overridden").diff["results_stale"] is True


def test_override_of_a_final_work_keeps_it_final(stage):
    """Praca po zamknięciu etapu zostaje finalna – korekta nie otwiera z powrotem reklamacji."""
    submission = locked_submission(stage)
    submission.status = SubmissionStatus.FINAL
    submission.save(update_fields=["status"])

    override_final_grade(submission, 6, rationale="Korekta po odwołaniu do komisji.", actor=None)

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.FINAL
    assert FinalGrade.objects.get(submission=submission).score == 6


def test_override_grade_counts_in_results(stage):
    """Ocena wpisana przez koordynatora wchodzi do przeliczenia wyników jak każda inna."""
    from apps.results.services import compute_stage_results

    submission = locked_submission(stage)
    override_final_grade(submission, 6, rationale="Ocena komisji, brak recenzentów.", actor=None)
    # Tabelę wyników wolno policzyć dopiero dla prac domkniętych – ten krok normalnie robi ``beat``.
    Submission.objects.filter(pk=submission.pk).update(status=SubmissionStatus.FINAL)

    rows = compute_stage_results(stage)

    assert any(row["total"] == 6 for row in rows)
    assert not Review.objects.exists()
