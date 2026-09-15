"""Regresje z przeglądu Critica taska T-05.

Jeden test na jedno finding – nazwy mówią, co dokładnie się zepsuło i dlaczego to była wada,
a nie tylko „coś nie działa”.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ROUND_BLIND, ROUND_TIEBREAK, FinalGrade, GradeMethod, Review, ReviewStatus
from apps.grading.services import (
    assign_reviewers,
    assign_third_reviewer,
    resolve_moderation,
    save_draft,
    submit_review,
)
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, Submission, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFileFactory

from .conftest import locked_submission

pytestmark = pytest.mark.django_db

REVIEWS_URL = "/api/grading/reviews/"


def in_review(stage, **kwargs):
    submission = locked_submission(stage, **kwargs)
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status"])
    return submission


def moderated(stage):
    """Rozwiązanie po rozjeździe ocen rundy 1 – czeka na rozstrzygnięcie.

    Oba przydziały powstają przed pierwszym wystawieniem oceny, dokładnie tak jak robi to
    ``assign_reviewers`` (jedna transakcja). Runda 1 domyka się po komplecie **istniejących**
    recenzji, więc kolejność „przydziel wszystkich, potem oceniaj” jest częścią kontraktu.
    """
    submission = in_review(stage)
    first = ReviewFactory(submission=submission)
    second = ReviewFactory(submission=submission)
    submit_review(first, 6, "", "", [])
    submit_review(second, 2, "", "", [])
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.MODERATION
    return submission


# --- finding 2: N+1 na liście przydziałów -------------------------------------------------


def assignments_for(reviewer, stage, count: int) -> None:
    """``count`` przydziałów jednego recenzenta, każdy z własnym plikiem rozwiązania."""
    for _ in range(count):
        submission = in_review(stage)
        SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN)
        ReviewFactory(submission=submission, reviewer=reviewer, round=ROUND_BLIND)


def queries_for_review_list(client, reviewer) -> int:
    client.force_authenticate(reviewer.user)
    with CaptureQueriesContext(connection) as captured:
        response = client.get(REVIEWS_URL)
    assert response.status_code == 200
    return len(captured)


def test_review_list_query_count_does_not_grow_with_assignments(client, stage):
    """``latest_file`` musi czytać z cache'u prefetchu, inaczej każdy wiersz listy dokłada zapytanie."""
    small = ActiveReviewerFactory()
    assignments_for(small, stage, 2)
    baseline = queries_for_review_list(client, small)

    large = ActiveReviewerFactory()
    assignments_for(large, stage, 10)
    with_ten = queries_for_review_list(client, large)

    assert with_ten == baseline
    assert with_ten <= 6, f"{with_ten} zapytań na listę przydziałów to już N+1"


# --- finding 3: rozstrzygnięcie rundy 1 przy jednym recenzencie ---------------------------


def test_single_round_one_review_settles_as_consensus(stage):
    """``per_submission=1``: jedyna wystawiona ocena domyka sprawę, praca nie wisi w IN_REVIEW."""
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    submission = locked_submission(stage)

    assign_reviewers(stage, per_submission=1)

    review = Review.objects.get(submission=submission, round=ROUND_BLIND)
    submit_review(review, 5, "", "", [])

    submission.refresh_from_db()
    grade = FinalGrade.objects.get(submission=submission)
    assert grade.method == GradeMethod.CONSENSUS
    assert grade.score == 5
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_assign_endpoint_refuses_a_single_reviewer_per_submission(client, stage):
    """API nie oferuje przydziału jednoosobowego – bez drugiej oceny nie ma czego uzgadniać."""
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    locked_submission(stage)
    client.force_authenticate(CoordinatorFactory())

    response = client.post(f"/api/grading/stages/{stage.pk}/assign/", {"per_submission": 1}, format="json")

    assert response.status_code == 400
    assert not Review.objects.exists()


# --- finding 5: nieprzydzielalne zgłoszenie nie wywraca całego etapu ----------------------


def test_unassignable_submission_is_skipped_and_the_rest_is_assigned(client, district_stage):
    """Jedna praca bez wolnych recenzentów trafia na listę ``skipped``, druga dostaje komplet."""
    ActiveReviewerFactory(district="malopolskie", district_verified=True)
    ActiveReviewerFactory(district="malopolskie", district_verified=True)
    ActiveReviewerFactory(district="mazowieckie", district_verified=True)
    # Uczestnik z województwa dwóch z trzech recenzentów – po wykluczeniu zostaje jeden, a trzeba dwóch.
    blocked = locked_submission(district_stage, district="malopolskie")
    # Ten sam etap, uczestnik z województwa, w którym nie ma żadnego recenzenta – pula pełna.
    assignable = locked_submission(district_stage, district="pomorskie")

    client.force_authenticate(CoordinatorFactory())
    response = client.post(f"/api/grading/stages/{district_stage.pk}/assign/", {}, format="json")

    assert response.status_code == 200
    assert Review.objects.filter(submission=assignable).count() == 2
    assert Review.objects.filter(submission=blocked).count() == 0
    assert [item["submission_id"] for item in response.data["skipped"]] == [blocked.pk]
    skipped = response.data["skipped"][0]
    assert skipped["reason"] == "NOT_ENOUGH_REVIEWERS"
    assert skipped["public_code"] == blocked.entry.participant.public_code
    # Praca przydzielona jest zacommitowana mimo pominięcia tamtej.
    assignable.refresh_from_db()
    assert assignable.status == SubmissionStatus.IN_REVIEW
    blocked.refresh_from_db()
    assert blocked.status == SubmissionStatus.LOCKED


def test_nothing_assignable_still_returns_conflict(district_stage):
    """Gdy nie da się przydzielić niczego, zostaje 409 – nie ma czego commitować."""
    ActiveReviewerFactory(district="mazowieckie", district_verified=True)
    ActiveReviewerFactory(district="mazowieckie", district_verified=True)
    locked_submission(district_stage, district="mazowieckie")

    with pytest.raises(DomainError) as exc:
        assign_reviewers(district_stage)

    assert exc.value.machine_code == "NOT_ENOUGH_REVIEWERS"
    assert exc.value.status_code == 409


# --- finding 7: wisząca runda 2 i zapis do zamkniętej pracy -------------------------------


def test_coordinator_resolution_cancels_the_pending_third_review(stage):
    """Po rozstrzygnięciu przez koordynatora przydział rundy 2 traci przedmiot – ma być CANCELLED."""
    submission = moderated(stage)
    third = assign_third_reviewer(submission, ActiveReviewerFactory())
    coordinator = CoordinatorFactory()

    resolve_moderation(submission, coordinator, 5, None, "Posiedzenie komisji")

    third.refresh_from_db()
    assert third.status == ReviewStatus.CANCELLED
    entry = AuditLog.objects.get(action="review.cancelled", target_id=str(third.pk))
    assert entry.actor == coordinator
    assert entry.diff["submission_id"] == submission.pk


def test_cancelled_third_reviewer_cannot_write_anything(client, stage):
    """Anulowany przydział nie przyjmuje ani szkicu, ani oceny – 409, nie cichy zapis."""
    submission = moderated(stage)
    third = assign_third_reviewer(submission, ActiveReviewerFactory())
    resolve_moderation(submission, CoordinatorFactory(), 5, None, "Posiedzenie komisji")

    client.force_authenticate(third.reviewer.user)
    draft = client.patch(f"{REVIEWS_URL}{third.pk}/", {"score": 2}, format="json")
    submitted = client.post(f"{REVIEWS_URL}{third.pk}/submit/", {"score": 2}, format="json")

    assert draft.status_code == 409
    assert draft.data["code"] == "REVIEW_CANCELLED"
    assert submitted.status_code == 409
    assert submitted.data["code"] == "REVIEW_CANCELLED"
    assert FinalGrade.objects.get(submission=submission).method == GradeMethod.MODERATION


def test_draft_is_rejected_when_submission_left_review(stage):
    """Szkic zapisuje się tylko do pracy w IN_REVIEW/MODERATION – nie do zamkniętej."""
    submission = in_review(stage)
    review = ReviewFactory(submission=submission)
    submission.status = SubmissionStatus.FINAL
    submission.save(update_fields=["status"])

    with pytest.raises(DomainError) as exc:
        save_draft(review, score=5)

    assert exc.value.machine_code == "SUBMISSION_NOT_REVIEWABLE"
    assert exc.value.status_code == 409
    review.refresh_from_db()
    assert review.status == ReviewStatus.ASSIGNED
    assert review.score is None


# --- finding 9: rozstrzygnięcie trzeciego recenzenta idzie przez submit_review ------------


def test_third_reviewer_resolution_leaves_the_same_audit_trail(stage):
    """``resolve`` wołane przez trzeciego recenzenta to jego ocena – ma zostawić ``review.submitted``."""
    submission = moderated(stage)
    third = assign_third_reviewer(submission, ActiveReviewerFactory())

    grade = resolve_moderation(submission, third.reviewer.user, 5, None, "Rozjemczo")

    third.refresh_from_db()
    assert third.status == ReviewStatus.SUBMITTED
    assert third.score == 5
    assert grade.method == GradeMethod.THIRD_REVIEW
    assert grade.decided_by == third.reviewer.user
    entry = AuditLog.objects.get(action="review.submitted", target_id=str(third.pk))
    assert entry.diff == {"submission_id": submission.pk, "round": ROUND_TIEBREAK, "score": 5}
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_reviewer_without_the_group_does_not_see_the_submission(client, stage):
    """Widoczność pliku ma tę samą definicję recenzenta co uprawnienie: status ACTIVE **i** grupa."""
    submission = in_review(stage)
    SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN)
    review = ReviewFactory(submission=submission)
    review.reviewer.user.groups.clear()

    assert not Submission.objects.for_user(review.reviewer.user).filter(pk=submission.pk).exists()
