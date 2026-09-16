"""Rubryka oceniania: kryteria zadania, punkty cząstkowe i suma jako ocena recenzji.

Przedmiotem testów jest reguła, o którą prosił organizator: recenzent dzieli punkty według
kryteriów, a system liczy sumę **sam** i nie zaokrągla jej do skali. Zaokrąglenie byłoby zmianą
decyzji recenzenta, więc niedopuszczalna suma musi kończyć się błędem z listą wartości.
"""

import pytest

from apps.core.api import DomainError
from apps.grading.models import ReviewStatus, RubricCriterion
from apps.grading.rubric import (
    format_criteria_lines,
    parse_criteria_lines,
    rubric_rows,
    set_criteria,
    validate_rubric,
)
from apps.grading.services import save_draft, submit_review
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


@pytest.fixture
def submission(stage):
    created = locked_submission(stage)
    created.status = SubmissionStatus.IN_REVIEW
    created.save(update_fields=["status"])
    return created


@pytest.fixture
def criteria(submission):
    """Rubryka 2 + 4 punkty: sumy 0, 2, 5, 6 dają się złożyć, a 4 nie należy do skali etapu."""
    return [
        RubricCriterion.objects.create(problem=submission.problem, order=1, title="Pomysł", max_points=2),
        RubricCriterion.objects.create(problem=submission.problem, order=2, title="Wykonanie", max_points=4),
    ]


def rubric_payload(criteria, *points):
    return [
        {"criterion_id": criterion.pk, "points": value, "comment": ""}
        for criterion, value in zip(criteria, points, strict=True)
    ]


def test_validate_rubric_returns_items_in_criteria_order_with_total(submission, criteria):
    """Kolejność wyniku bierze się z kryteriów zadania, nie z kolejności pól w żądaniu."""
    raw = list(reversed(rubric_payload(criteria, 2, 4)))

    items, total = validate_rubric(submission.problem, raw)

    assert [item["criterion_id"] for item in items] == [criteria[0].pk, criteria[1].pk]
    assert total == 6


def test_validate_rubric_requires_every_criterion_when_not_partial(submission, criteria):
    raw = [{"criterion_id": criteria[0].pk, "points": 2}]

    with pytest.raises(DomainError) as exc:
        validate_rubric(submission.problem, raw)

    assert exc.value.machine_code == "RUBRIC_INCOMPLETE"
    assert criteria[1].title in str(exc.value.detail)


def test_validate_rubric_accepts_gaps_in_draft_mode(submission, criteria):
    items, total = validate_rubric(
        submission.problem, [{"criterion_id": criteria[0].pk, "points": 2}], partial=True
    )

    assert [item["points"] for item in items] == [2, None]
    assert total == 2


def test_points_above_criterion_maximum_are_rejected(submission, criteria):
    with pytest.raises(DomainError) as exc:
        validate_rubric(submission.problem, rubric_payload(criteria, 3, 0))

    assert exc.value.machine_code == "RUBRIC_POINTS_OUT_OF_RANGE"


def test_criterion_from_another_problem_is_rejected(stage, submission, criteria):
    other = locked_submission(stage)
    foreign = RubricCriterion.objects.create(problem=other.problem, order=1, title="Obce", max_points=1)

    with pytest.raises(DomainError) as exc:
        validate_rubric(submission.problem, [{"criterion_id": foreign.pk, "points": 1}])

    assert exc.value.machine_code == "RUBRIC_UNKNOWN_CRITERION"


def test_submit_review_takes_the_score_from_the_rubric(submission, criteria):
    review = ReviewFactory(submission=submission)

    # Ocena przysłana w żądaniu jest ignorowana: liczy się suma z kryteriów (2 + 3 = 5).
    submit_review(review, 0, rubric=rubric_payload(criteria, 2, 3))

    review.refresh_from_db()
    assert review.score == 5
    assert review.status == ReviewStatus.SUBMITTED
    assert [item["points"] for item in review.rubric] == [2, 3]


def test_sum_outside_the_scale_is_refused_with_the_allowed_values(submission, criteria):
    """4 punkty nie należą do skali 0/2/5/6 – system nie zaokrągla, tylko odmawia z listą."""
    review = ReviewFactory(submission=submission)

    with pytest.raises(DomainError) as exc:
        submit_review(review, 0, rubric=rubric_payload(criteria, 0, 4))

    assert exc.value.machine_code == "RUBRIC_TOTAL_NOT_IN_SCALE"
    assert "0, 2, 5, 6" in str(exc.value.detail)
    review.refresh_from_db()
    assert review.status == ReviewStatus.ASSIGNED
    assert review.score is None


def test_draft_stores_partial_rubric_and_sets_score_only_when_complete(submission, criteria):
    review = ReviewFactory(submission=submission)

    save_draft(review, rubric=[{"criterion_id": criteria[0].pk, "points": 2}])
    review.refresh_from_db()
    assert review.score is None
    assert review.rubric[0]["points"] == 2

    save_draft(review, rubric=rubric_payload(criteria, 2, 3))
    review.refresh_from_db()
    assert review.score == 5


def test_rubric_rows_show_criteria_added_after_the_draft(submission, criteria):
    review = ReviewFactory(submission=submission)
    save_draft(review, rubric=[{"criterion_id": criteria[0].pk, "points": 1}])
    RubricCriterion.objects.create(problem=submission.problem, order=3, title="Zapis", max_points=1)

    rows = rubric_rows(review)

    assert [row["criterion"].title for row in rows] == ["Pomysł", "Wykonanie", "Zapis"]
    assert [row["points"] for row in rows] == [1, None, None]


def test_set_criteria_updates_in_place_so_saved_points_keep_their_criterion(submission, criteria):
    """Poprawienie tytułu nie może zerwać powiązania z punktami zapisanymi w recenzjach."""
    ids_before = [criterion.pk for criterion in criteria]

    set_criteria(
        submission.problem,
        [
            {"max_points": 2, "title": "Pomysł (poprawiony)", "description": ""},
            {"max_points": 4, "title": "Wykonanie", "description": "staranność rachunków"},
        ],
    )

    after = list(RubricCriterion.objects.filter(problem=submission.problem).order_by("order"))
    assert [criterion.pk for criterion in after] == ids_before
    assert after[0].title == "Pomysł (poprawiony)"


def test_set_criteria_removes_the_surplus_and_adds_the_missing(submission, criteria):
    set_criteria(submission.problem, [{"max_points": 6, "title": "Całość", "description": ""}])

    assert RubricCriterion.objects.filter(problem=submission.problem).count() == 1


def test_criteria_lines_round_trip():
    text = "2;Pomysł;jak uczestnik podszedł do zadania\n4;Wykonanie"
    items = parse_criteria_lines(text)

    assert items == [
        {"max_points": 2, "title": "Pomysł", "description": "jak uczestnik podszedł do zadania"},
        {"max_points": 4, "title": "Wykonanie", "description": ""},
    ]
    saved = [
        RubricCriterion(max_points=item["max_points"], title=item["title"], description=item["description"])
        for item in items
    ]
    assert format_criteria_lines(saved) == text


@pytest.mark.parametrize(
    "text",
    ["2 Pomysł", "x;Pomysł", "0;Pomysł", "2;"],
)
def test_criteria_lines_reject_malformed_input(text):
    with pytest.raises(ValueError):
        parse_criteria_lines(text)
