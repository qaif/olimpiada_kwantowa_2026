"""T3: ocenianie należy do konkursu – przez pracę, nigdy przez własną kolumnę.

Żaden model tej aplikacji nie dostaje klucza do konkursu i to jest decyzja, a nie zaniechanie
(§ 3.1): recenzja, ocena końcowa, notatka i zgłoszenie problemu dochodzą do właściciela przez
pracę, a reguła przydziału i rubryka – przez zadanie. Druga droga do tej samej prawdy byłaby drugą
okazją do rozjazdu, a rozjazd w tabeli izolacji znaczy wyciek.

Czego ten plik **nie** dotyka: przepływu ocen. ``supersede_earlier_versions``, ``lock_for_review``,
``revise_review``, ``unassign_reviewer`` i reguły skali mają swoje testy i żaden z nich nie zmienia
się przez zakresowanie – zmienia się wyłącznie **zbiór**, na którym te reguły działają.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.competitions.tests.factories import ProblemFactory, StageFactory
from apps.competitions.tests.scope_helpers import api_client_factory
from apps.grading.models import (
    CommentSnippet,
    FinalGrade,
    ProblemReviewerRule,
    Review,
    ReviewNote,
    ReviewStatus,
    ReviewWorkLog,
    RubricCriterion,
    WorkIssue,
)
from apps.grading.services import moderation_queue, reviews_for_reviewer
from apps.grading.tasks import remind_overdue_reviews
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .factories import FinalGradeFactory, ReviewFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_for(settings):
    """Klient DRF pod domeną wskazanego konkursu – reguła stoi w ``scope_helpers``."""
    return api_client_factory(settings)


@pytest.mark.parametrize(
    ("model", "path"),
    [
        (Review, "submission__competition"),
        (FinalGrade, "submission__competition"),
        (ReviewNote, "submission__competition"),
        (WorkIssue, "submission__competition"),
        (ReviewWorkLog, "review__submission__competition"),
        (ProblemReviewerRule, "problem__stage__edition__competition"),
        (RubricCriterion, "problem__stage__edition__competition"),
        (CommentSnippet, "problem__stage__edition__competition"),
    ],
)
def test_every_model_declares_its_way_to_the_competition(model, path):
    """Ścieżka jest deklaracją modelu (§ 3.5) i ma zgadzać się z tabelą dróg z dokumentu."""
    assert model.objects.all().competition_path == path


def test_reviews_and_grades_of_another_competition_are_invisible(competition, other_competition):
    review_b = ReviewFactory(competition=other_competition)
    grade_b = FinalGradeFactory(competition=other_competition)

    assert list(Review.objects.for_competition(competition)) == []
    assert list(FinalGrade.objects.for_competition(competition)) == []
    assert review_b in Review.objects.for_competition(other_competition)
    assert grade_b in FinalGrade.objects.for_competition(other_competition)


def test_problem_reviewer_rule_reaches_the_competition_through_the_problem(competition, other_competition):
    """Reguła przydziału idzie przez zadanie – cztery ogniwa, ale **jedna** droga."""
    rule_b = ProblemReviewerRule.objects.create(
        problem=ProblemFactory(competition=other_competition),
        reviewer=ActiveReviewerFactory(competition=other_competition),
    )

    assert list(ProblemReviewerRule.objects.for_competition(competition)) == []
    assert list(ProblemReviewerRule.objects.for_competition(other_competition)) == [rule_b]


def test_reviewer_queue_is_scoped_to_the_competition(competition, other_competition):
    """Kolejka recenzenta to zakres **i** przydział, w tej kolejności (§ 3.5)."""
    reviewer = ActiveReviewerFactory(competition=competition)
    mine = ReviewFactory(competition=competition, reviewer=reviewer)
    theirs = ReviewFactory(competition=other_competition, reviewer=reviewer)

    queue = reviews_for_reviewer(reviewer, competition)

    assert list(queue) == [mine]
    assert theirs not in queue


def test_moderation_queue_is_scoped_to_the_competition(competition, other_competition):
    """„Widok wyłącznie dla koordynatora” znaczy odtąd „dla koordynatora **tego** konkursu”."""
    mine = SubmissionFactory(competition=competition, status=SubmissionStatus.MODERATION)
    theirs = SubmissionFactory(competition=other_competition, status=SubmissionStatus.MODERATION)

    assert list(moderation_queue(competition)) == [mine]
    assert theirs not in moderation_queue(competition)


# --- reguła krzyżowa w API ----------------------------------------------------------------------


def test_unassigning_a_review_of_another_competition_is_not_found(api_for, competition, other_competition):
    """Odebranie pracy recenzentowi to zapis w aktach oceniania – na cudzych aktach 404."""
    review_b = ReviewFactory(competition=other_competition)

    response = api_for(competition, CoordinatorFactory()).post(
        f"/api/grading/reviews/{review_b.pk}/unassign/"
    )

    assert response.status_code == 404
    review_b.refresh_from_db()
    assert review_b.status == ReviewStatus.ASSIGNED


def test_final_grade_on_a_submission_of_another_competition_is_not_found(
    api_for, competition, other_competition
):
    submission_b = SubmissionFactory(competition=other_competition)

    response = api_for(competition, CoordinatorFactory()).post(
        f"/api/grading/submissions/{submission_b.pk}/final-grade/",
        {"score": 6, "rationale": "korekta"},
        format="json",
    )

    assert response.status_code == 404
    assert not FinalGrade.objects.filter(submission=submission_b).exists()


def test_assigning_reviewers_to_a_stage_of_another_competition_is_not_found(
    api_for, competition, other_competition
):
    stage_b = StageFactory(competition=other_competition)

    response = api_for(competition, CoordinatorFactory()).post(
        f"/api/grading/stages/{stage_b.pk}/assign/", {"per_submission": 2}, format="json"
    )

    assert response.status_code == 404


def test_moderation_list_does_not_leak_work_of_another_competition(api_for, competition, other_competition):
    """Kolejka rozjazdów niesie prace razem z ich pseudonimami – cudzych nie pokazuje wcale."""
    theirs = SubmissionFactory(competition=other_competition, status=SubmissionStatus.MODERATION)

    response = api_for(competition, CoordinatorFactory()).get("/api/grading/moderation/")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == []
    assert theirs.pk not in [row["id"] for row in response.json()]


# --- przebieg wsadowy ---------------------------------------------------------------------------


def test_reminders_are_sent_per_competition(competition, other_competition):
    """Recenzent w dwóch komitetach dostaje **dwa** listy, każdy z pracami jednego konkursu.

    Jeden list zbiorczy prowadziłby pod jedną domenę, a połowa jego pozycji byłaby tam
    niedostępna – dlatego grupowanie po recenzencie zostaje wewnątrz konkursu.
    """
    overdue = timezone.now() - timedelta(days=1)
    for owner in (competition, other_competition):
        ReviewFactory(
            competition=owner,
            reviewer=ActiveReviewerFactory(competition=owner),
            due_at=overdue,
            submission__status=SubmissionStatus.IN_REVIEW,
        )

    result = remind_overdue_reviews()

    assert result == {"reviewers": 2, "reviews": 2}
