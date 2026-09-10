"""Kryteria 1–2 T-05 oraz wymóg z długu technicznego T-02 (weryfikacja okręgu recenzenta)."""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, PendingReviewerFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ROUND_BLIND, Review, ReviewStatus
from apps.grading.services import assign_reviewers
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def test_assign_gives_two_distinct_reviewers_and_is_idempotent(stage):
    """1. Każde LOCKED rozwiązanie dostaje dokładnie 2 różnych recenzentów; powtórka nic nie dodaje."""
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    first = locked_submission(stage)
    second = locked_submission(stage)

    result = assign_reviewers(stage)

    assert result == {"submissions": 2, "assignments": 4, "skipped": []}
    for submission in (first, second):
        reviewers = set(
            Review.objects.filter(submission=submission, round=ROUND_BLIND).values_list(
                "reviewer_id", flat=True
            )
        )
        assert len(reviewers) == 2
        submission.refresh_from_db()
        assert submission.status == SubmissionStatus.IN_REVIEW
    assert Review.objects.filter(status=ReviewStatus.ASSIGNED).count() == 4

    again = assign_reviewers(stage)

    assert again == {"submissions": 0, "assignments": 0, "skipped": []}
    assert Review.objects.count() == 4


def test_assignment_is_balanced_between_reviewers(stage):
    """Równoważenie: przy 2 recenzentach i 3 rozwiązaniach każdy ma po 3 przydziały."""
    first, second = ActiveReviewerFactory(), ActiveReviewerFactory()
    for _ in range(3):
        locked_submission(stage)

    assign_reviewers(stage)

    assert Review.objects.filter(reviewer=first).count() == 3
    assert Review.objects.filter(reviewer=second).count() == 3


def test_district_stage_excludes_reviewer_from_participant_district(district_stage):
    """2a. Na etapie okręgowym recenzent z okręgu uczestnika nie dostaje przydziału."""
    conflicted = ActiveReviewerFactory(district="mazowieckie")
    ActiveReviewerFactory(district="malopolskie")
    ActiveReviewerFactory(district="pomorskie")
    submission = locked_submission(district_stage, district="mazowieckie")

    assign_reviewers(district_stage)

    reviewers = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert conflicted.pk not in reviewers
    assert len(reviewers) == 2


def test_district_stage_without_enough_reviewers_raises_conflict(district_stage):
    """2b. Gdy po wykluczeniu konfliktów zostaje < 2 recenzentów → NOT_ENOUGH_REVIEWERS (409)."""
    ActiveReviewerFactory(district="mazowieckie")
    ActiveReviewerFactory(district="mazowieckie")
    ActiveReviewerFactory(district="malopolskie")
    locked_submission(district_stage, district="mazowieckie")

    with pytest.raises(DomainError) as exc:
        assign_reviewers(district_stage)

    assert exc.value.machine_code == "NOT_ENOUGH_REVIEWERS"
    assert exc.value.status_code == 409
    assert Review.objects.count() == 0


def test_unverified_reviewer_is_skipped_on_district(district_stage):
    """T-02: niezweryfikowany okręg = konflikt z każdym okręgiem na etapie okręgowym."""
    unverified = ActiveReviewerFactory(district="pomorskie", district_verified=False)
    verified_a = ActiveReviewerFactory(district="malopolskie", district_verified=True)
    verified_b = ActiveReviewerFactory(district="lubelskie", district_verified=True)
    submission = locked_submission(district_stage, district="mazowieckie")

    assign_reviewers(district_stage)

    reviewers = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert reviewers == {verified_a.pk, verified_b.pk}
    assert unverified.pk not in reviewers


def test_unverified_reviewer_is_assignable_outside_district_stage(stage):
    """T-02: poza etapem okręgowym brak potwierdzenia okręgu nie wyklucza recenzenta.

    Pula to dokładnie dwie osoby, więc przydział musi sięgnąć po tę niezweryfikowaną – asercja
    mówi wtedy coś o regule, a nie tylko o rozmiarze zbioru.
    """
    unverified = ActiveReviewerFactory(district="pomorskie", district_verified=False)
    verified = ActiveReviewerFactory(district="malopolskie", district_verified=True)
    submission = locked_submission(stage, district="pomorskie")

    assign_reviewers(stage)

    reviewers = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert unverified.pk in reviewers
    assert reviewers == {unverified.pk, verified.pk}


def test_verify_district_endpoint_makes_reviewer_assignable_on_district(client, district_stage):
    """Po potwierdzeniu okręgu przez koordynatora recenzent wchodzi do puli etapu okręgowego."""
    unverified = ActiveReviewerFactory(district="pomorskie", district_verified=False)
    verified = ActiveReviewerFactory(district="malopolskie", district_verified=True)
    submission = locked_submission(district_stage, district="mazowieckie")

    # Przed potwierdzeniem pula ma jedną osobę – nie ma z czego złożyć dwóch recenzji.
    with pytest.raises(DomainError) as exc:
        assign_reviewers(district_stage)
    assert exc.value.machine_code == "NOT_ENOUGH_REVIEWERS"

    client.force_authenticate(CoordinatorFactory())
    response = client.post(
        f"/api/auth/committee/{unverified.pk}/verify-district/", {"district": "pomorskie"}, format="json"
    )
    assert response.status_code == 200

    assign_reviewers(district_stage)

    reviewers = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert reviewers == {unverified.pk, verified.pk}


def test_pending_reviewer_is_not_in_pool(stage):
    """Recenzent PENDING nie jest przydzielany – pula to wyłącznie profile ACTIVE."""
    PendingReviewerFactory()
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    submission = locked_submission(stage)

    assign_reviewers(stage)

    assert Review.objects.filter(submission=submission).count() == 2
    assert not Review.objects.filter(reviewer__status="PENDING").exists()


def test_assign_endpoint_requires_coordinator(client, stage):
    """Przydział wywołuje wyłącznie koordynator; recenzent dostaje 403."""
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    locked_submission(stage)
    url = f"/api/grading/stages/{stage.pk}/assign/"

    client.force_authenticate(ActiveReviewerFactory().user)
    assert client.post(url, {}, format="json").status_code == 403

    coordinator = CoordinatorFactory()
    client.force_authenticate(coordinator)
    response = client.post(url, {}, format="json")

    assert response.status_code == 200
    assert response.data["assignments"] == 2
    assert AuditLog.objects.filter(action="review.assigned", actor=coordinator).exists()
