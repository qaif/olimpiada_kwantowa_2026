"""Kryteria 1–2 T-05 oraz reguła konfliktu interesów oparta na województwie recenzenta.

Województwo członka komitetu jest opcjonalne (decyzja organizatora): konfliktem jest wyłącznie
województwo **równe** województwu uczestnika na etapie wojewódzkim, a ``district_verified``
niczego nie bramkuje.
"""

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

    # ``due_at`` jest w wyniku od czasu terminów recenzji – porównujemy pozycje, które opisują
    # sam przydział, a termin sprawdza ``test_deadlines.py``.
    assert result["submissions"] == 2
    assert result["assignments"] == 4
    assert result["skipped"] == []
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

    assert (again["submissions"], again["assignments"], again["skipped"]) == (0, 0, [])
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
    """2a. Na etapie wojewódzkim recenzent z województwa uczestnika nie dostaje przydziału."""
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


def test_unverified_but_equal_district_is_still_a_conflict(district_stage):
    """Niepotwierdzone województwo **równe** województwu uczestnika nadal wyklucza.

    To bezpieczniejszy kierunek błędu: samodeklaracja nie jest dowodem, że konfliktu nie ma,
    więc dopóki recenzent deklaruje województwo uczestnika, prac stamtąd nie dostaje.
    """
    conflicted = ActiveReviewerFactory(district="mazowieckie", district_verified=False)
    first = ActiveReviewerFactory(district="malopolskie")
    second = ActiveReviewerFactory(district="lubelskie")
    submission = locked_submission(district_stage, district="mazowieckie")

    assign_reviewers(district_stage)

    reviewers = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert reviewers == {first.pk, second.pk}
    assert conflicted.pk not in reviewers


def test_member_without_district_is_assignable_on_district_stage(district_stage):
    """Województwo członka komitetu jest opcjonalne: jego brak nie jest konfliktem z niczym.

    Pula to dokładnie dwie osoby, w tym jedna bez województwa, więc przydział musi po nią sięgnąć –
    asercja mówi wtedy coś o regule, a nie tylko o rozmiarze zbioru.
    """
    without_district = ActiveReviewerFactory(district=None, district_verified=False)
    other = ActiveReviewerFactory(district="malopolskie")
    submission = locked_submission(district_stage, district="mazowieckie")

    assign_reviewers(district_stage)

    reviewers = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert reviewers == {without_district.pk, other.pk}


def test_unverified_reviewer_is_assignable_outside_district_stage(stage):
    """Poza etapem wojewódzkim województwo nie wyklucza nikogo – nawet zgodne z uczestnikiem."""
    unverified = ActiveReviewerFactory(district="pomorskie", district_verified=False)
    verified = ActiveReviewerFactory(district="malopolskie", district_verified=True)
    submission = locked_submission(stage, district="pomorskie")

    assign_reviewers(stage)

    reviewers = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert unverified.pk in reviewers
    assert reviewers == {unverified.pk, verified.pk}


def test_verify_district_endpoint_moves_reviewer_out_of_the_conflict(client, district_stage):
    """Zmiana województwa przez koordynatora wypuszcza recenzenta z konfliktu na etapie wojewódzkim."""
    conflicted = ActiveReviewerFactory(district="mazowieckie")
    other = ActiveReviewerFactory(district="malopolskie")
    submission = locked_submission(district_stage, district="mazowieckie")

    # Przed zmianą pula ma jedną osobę – nie ma z czego złożyć dwóch recenzji.
    with pytest.raises(DomainError) as exc:
        assign_reviewers(district_stage)
    assert exc.value.machine_code == "NOT_ENOUGH_REVIEWERS"

    client.force_authenticate(CoordinatorFactory())
    response = client.post(
        f"/api/auth/committee/{conflicted.pk}/verify-district/", {"district": "pomorskie"}, format="json"
    )
    assert response.status_code == 200

    assign_reviewers(district_stage)

    reviewers = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert reviewers == {conflicted.pk, other.pk}


def test_clearing_the_district_makes_the_reviewer_assignable_everywhere(client, district_stage):
    """Usunięcie województwa („— brak —”) też zdejmuje konflikt: pole jest opcjonalne."""
    conflicted = ActiveReviewerFactory(district="mazowieckie")
    other = ActiveReviewerFactory(district="malopolskie")
    submission = locked_submission(district_stage, district="mazowieckie")

    client.force_authenticate(CoordinatorFactory())
    response = client.post(
        f"/api/auth/committee/{conflicted.pk}/verify-district/", {"district": ""}, format="json"
    )
    assert response.status_code == 200
    assert response.data["district"] is None

    assign_reviewers(district_stage)

    reviewers = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert reviewers == {conflicted.pk, other.pk}


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
