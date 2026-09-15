"""API przydziału ręcznego (``/api/grading/``) – komplet endpointów koordynatora.

Testy sprawdzają kontrakt, a nie regułę domenową (ta ma własne testy w ``test_manual_assignment``):
kod odpowiedzi, kształt danych, zakres uprawnień i to, że zadanie z obcego etapu jest niewidoczne.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.competitions.tests.factories import ProblemFactory
from apps.grading.models import FinalGrade, GradeMethod, ProblemReviewerRule, Review, ReviewStatus
from apps.grading.services import assign_reviewer_to_submission

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def test_coordinator_creates_problem_rule(client, stage):
    reviewer = ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    submission = locked_submission(stage, problem=problem)
    client.force_authenticate(CoordinatorFactory())

    response = client.post(
        f"/api/grading/stages/{stage.pk}/problem-rules/",
        {"problem_id": problem.pk, "reviewer_id": reviewer.pk},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["assigned"] == 1
    assert response.data["rule"]["reviewer_email"] == reviewer.user.email
    assert Review.objects.filter(submission=submission, reviewer=reviewer).exists()


def test_problem_from_another_stage_is_invisible(client, stage, district_stage):
    """Zadanie spoza etapu z adresu to 404 – odpowiedź nie potwierdza, że takie zadanie istnieje."""
    foreign = ProblemFactory(stage=district_stage)
    client.force_authenticate(CoordinatorFactory())

    response = client.post(
        f"/api/grading/stages/{stage.pk}/problem-rules/",
        {"problem_id": foreign.pk, "reviewer_id": ActiveReviewerFactory().pk},
        format="json",
    )

    assert response.status_code == 404
    assert not ProblemReviewerRule.objects.exists()


def test_reviewer_cannot_create_problem_rule(client, stage):
    """Reguły to narzędzie koordynatora – recenzent dostaje 403."""
    reviewer = ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    client.force_authenticate(reviewer.user)

    response = client.post(
        f"/api/grading/stages/{stage.pk}/problem-rules/",
        {"problem_id": problem.pk, "reviewer_id": reviewer.pk},
        format="json",
    )

    assert response.status_code == 403
    assert not ProblemReviewerRule.objects.exists()


def test_coordinator_deletes_problem_rule(client, stage):
    reviewer = ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    submission = locked_submission(stage, problem=problem)
    coordinator = CoordinatorFactory()
    client.force_authenticate(coordinator)
    created = client.post(
        f"/api/grading/stages/{stage.pk}/problem-rules/",
        {"problem_id": problem.pk, "reviewer_id": reviewer.pk},
        format="json",
    )
    rule_id = created.data["rule"]["id"]

    response = client.delete(f"/api/grading/stages/{stage.pk}/problem-rules/{rule_id}/")

    assert response.status_code == 204
    assert not ProblemReviewerRule.objects.exists()
    # Recenzja z reguły zostaje – kasowanie reguły nie jest cofaniem przydziałów.
    assert Review.objects.filter(submission=submission, reviewer=reviewer).exists()


def test_coordinator_assigns_single_submission(client, stage):
    reviewer = ActiveReviewerFactory()
    submission = locked_submission(stage)
    client.force_authenticate(CoordinatorFactory())

    response = client.post(
        f"/api/grading/submissions/{submission.pk}/assign/", {"reviewer_id": reviewer.pk}, format="json"
    )

    assert response.status_code == 201
    assert response.data["submission_id"] == submission.pk
    assert response.data["status"] == ReviewStatus.ASSIGNED
    # Ocenianie ślepe: odpowiedź opisuje uczestnika wyłącznie pseudonimem.
    assert response.data["participant_public_code"] == submission.entry.participant.public_code


def test_conflict_of_interest_is_reported_with_machine_code(client, district_stage):
    conflicted = ActiveReviewerFactory(district="mazowieckie")
    submission = locked_submission(district_stage, district="mazowieckie")
    client.force_authenticate(CoordinatorFactory())

    response = client.post(
        f"/api/grading/submissions/{submission.pk}/assign/", {"reviewer_id": conflicted.pk}, format="json"
    )

    assert response.status_code == 409
    assert response.data["code"] == "REVIEWER_CONFLICT_OF_INTEREST"


def test_coordinator_unassigns_review(client, stage):
    review = assign_reviewer_to_submission(locked_submission(stage), ActiveReviewerFactory())
    client.force_authenticate(CoordinatorFactory())

    response = client.post(f"/api/grading/reviews/{review.pk}/unassign/")

    assert response.status_code == 200
    assert response.data["status"] == ReviewStatus.CANCELLED


def test_reviewer_cannot_unassign(client, stage):
    """Recenzent nie zdejmuje sobie przydziałów – to decyzja koordynatora."""
    reviewer = ActiveReviewerFactory()
    review = assign_reviewer_to_submission(locked_submission(stage), reviewer)
    client.force_authenticate(reviewer.user)

    response = client.post(f"/api/grading/reviews/{review.pk}/unassign/")

    review.refresh_from_db()
    assert response.status_code == 403
    assert review.status == ReviewStatus.ASSIGNED


# --- korekta ocen -------------------------------------------------------------------------------


def test_coordinator_sets_review_score(client, stage):
    review = assign_reviewer_to_submission(locked_submission(stage), ActiveReviewerFactory())
    client.force_authenticate(CoordinatorFactory())

    response = client.post(
        f"/api/grading/reviews/{review.pk}/score/",
        {"score": 5, "rationale": "Ustalenie komisji."},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["score"] == 5
    assert response.data["status"] == ReviewStatus.SUBMITTED


def test_score_outside_the_scale_is_a_domain_error(client, stage):
    review = assign_reviewer_to_submission(locked_submission(stage), ActiveReviewerFactory())
    client.force_authenticate(CoordinatorFactory())

    response = client.post(f"/api/grading/reviews/{review.pk}/score/", {"score": 4}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "SCORE_NOT_IN_SCALE"


def test_reviewer_cannot_set_scores_through_the_coordinator_endpoint(client, stage):
    """Recenzent ma własną ścieżkę (``submit/``) z bramką stanu – ta jest wyłącznie dla koordynatora."""
    reviewer = ActiveReviewerFactory()
    review = assign_reviewer_to_submission(locked_submission(stage), reviewer)
    client.force_authenticate(reviewer.user)

    response = client.post(f"/api/grading/reviews/{review.pk}/score/", {"score": 5}, format="json")

    review.refresh_from_db()
    assert response.status_code == 403
    assert review.score is None


def test_coordinator_overrides_final_grade(client, stage):
    submission = locked_submission(stage)
    client.force_authenticate(CoordinatorFactory())

    response = client.post(
        f"/api/grading/submissions/{submission.pk}/final-grade/",
        {"score": 6, "rationale": "Ocena komisji – brak recenzentów."},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["grade"]["score"] == 6
    assert response.data["grade"]["method"] == GradeMethod.COORDINATOR_OVERRIDE
    assert response.data["results_stale"] is False


def test_override_without_rationale_is_rejected(client, stage):
    submission = locked_submission(stage)
    client.force_authenticate(CoordinatorFactory())

    response = client.post(
        f"/api/grading/submissions/{submission.pk}/final-grade/", {"score": 6}, format="json"
    )

    assert response.status_code == 400
    assert not FinalGrade.objects.exists()


def test_reviewer_cannot_override_final_grade(client, stage):
    reviewer = ActiveReviewerFactory()
    submission = locked_submission(stage)
    client.force_authenticate(reviewer.user)

    response = client.post(
        f"/api/grading/submissions/{submission.pk}/final-grade/",
        {"score": 6, "rationale": "Chcę sobie wpisać ocenę."},
        format="json",
    )

    assert response.status_code == 403
    assert not FinalGrade.objects.exists()
