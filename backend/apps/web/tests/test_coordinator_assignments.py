"""Ekran „Przydziały i oceny” (``/coordinator/stages/<id>/assignments/``).

Sprawdzamy to, czego nie widać w testach serwisów: że strona pokazuje wszystkie trzy narzędzia
(reguły zadań, przydział pojedynczej pracy, korekta punktów), że każda akcja wraca na ten sam
ekran (a nie na pulpit) i że nikt poza koordynatorem tu nie wejdzie.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.grading.models import (
    FinalGrade,
    GradeMethod,
    ProblemReviewerRule,
    Review,
    ReviewStatus,
)
from apps.grading.services import add_problem_reviewer_rule, assign_reviewer_to_submission
from apps.results.models import ResultsPublication
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import PARTICIPANT_LAST_NAME

pytestmark = pytest.mark.django_db


@pytest.fixture
def locked(entry, problems):
    """Praca gotowa do przydziału – dokładnie taki stan zostawia zamknięcie etapu."""
    return SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.LOCKED)


def assignments_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/assignments/"


def test_page_lists_rules_and_submissions(web_client, coordinator, elim_stage, problems, locked):
    reviewer = ActiveReviewerFactory()
    add_problem_reviewer_rule(problems[0], reviewer)
    web_client.force_login(coordinator)

    response = web_client.get(assignments_url(elim_stage))
    content = response.content.decode()

    assert response.status_code == 200
    assert problems[0].title in content
    assert reviewer.user.email in content
    assert locked.entry.participant.public_code in content
    # Koordynator jest jedyną rolą, która widzi nazwisko obok kodu publicznego.
    assert PARTICIPANT_LAST_NAME in content


def test_search_filters_by_public_code_and_surname(web_client, coordinator, elim_stage, locked):
    web_client.force_login(coordinator)

    hit = web_client.get(assignments_url(elim_stage), {"q": PARTICIPANT_LAST_NAME.lower()})
    miss = web_client.get(assignments_url(elim_stage), {"q": "Kowalski-nie-ma"})

    assert locked.entry.participant.public_code in hit.content.decode()
    assert locked.entry.participant.public_code not in miss.content.decode()


def test_non_coordinator_gets_403(web_client, reviewer, elim_stage):
    web_client.force_login(reviewer.user)

    response = web_client.get(assignments_url(elim_stage))

    assert response.status_code == 403


def test_anonymous_is_redirected_to_login(web_client, elim_stage):
    response = web_client.get(assignments_url(elim_stage))

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_add_rule_assigns_locked_submission(web_client, coordinator, elim_stage, problems, locked):
    member = ActiveReviewerFactory()
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/problems/{problems[0].pk}/reviewer-rules/", {"reviewer_id": member.pk}
    )

    assert response.status_code == 302
    assert response["Location"] == assignments_url(elim_stage)
    assert ProblemReviewerRule.objects.filter(problem=problems[0], reviewer=member).exists()
    assert Review.objects.filter(submission=locked, reviewer=member).exists()


def test_remove_rule_keeps_review(web_client, coordinator, elim_stage, problems, locked):
    member = ActiveReviewerFactory()
    rule = add_problem_reviewer_rule(problems[0], member)["rule"]
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/reviewer-rules/{rule.pk}/delete/")

    assert response["Location"] == assignments_url(elim_stage)
    assert not ProblemReviewerRule.objects.exists()
    assert Review.objects.filter(submission=locked, reviewer=member).exists()


def test_assign_single_submission_from_panel(web_client, coordinator, elim_stage, locked):
    member = ActiveReviewerFactory()
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/submissions/{locked.pk}/assign-reviewer/", {"reviewer_id": member.pk}
    )

    locked.refresh_from_db()
    assert response["Location"] == assignments_url(elim_stage)
    assert Review.objects.filter(submission=locked, reviewer=member).count() == 1
    assert locked.status == SubmissionStatus.IN_REVIEW


def test_refusal_shows_message_and_returns_to_the_page(web_client, coordinator, elim_stage, locked):
    """Druga próba tego samego przydziału: komunikat błędu, powrót na ekran, bez drugiej recenzji."""
    member = ActiveReviewerFactory()
    assign_reviewer_to_submission(locked, member)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/submissions/{locked.pk}/assign-reviewer/", {"reviewer_id": member.pk}, follow=True
    )

    assert "przydzieloną" in response.content.decode()
    assert Review.objects.filter(submission=locked, reviewer=member).count() == 1


def test_unassign_from_panel(web_client, coordinator, elim_stage, locked):
    review = assign_reviewer_to_submission(locked, ActiveReviewerFactory())
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/reviews/{review.pk}/unassign/")

    review.refresh_from_db()
    assert response["Location"] == assignments_url(elim_stage)
    assert review.status == ReviewStatus.CANCELLED


def test_dashboard_links_to_the_assignments_page(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get("/coordinator/")

    assert assignments_url(elim_stage) in response.content.decode()


# --- korekta ocen -------------------------------------------------------------------------------


def test_page_shows_scale_and_final_grade_forms(web_client, coordinator, elim_stage, locked):
    assign_reviewer_to_submission(locked, ActiveReviewerFactory())
    web_client.force_login(coordinator)

    content = web_client.get(assignments_url(elim_stage)).content.decode()

    assert "Przydziały i oceny" in content
    assert f"/coordinator/submissions/{locked.pk}/final-grade/" in content
    assert "Zapisz korektę" in content


def test_set_review_score_from_panel(web_client, coordinator, elim_stage, locked):
    review = assign_reviewer_to_submission(locked, ActiveReviewerFactory())
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/reviews/{review.pk}/score/", {"score": 5, "rationale": ""})

    review.refresh_from_db()
    assert response["Location"] == assignments_url(elim_stage)
    assert (review.score, review.status) == (5, ReviewStatus.SUBMITTED)


def test_override_final_grade_from_panel(web_client, coordinator, elim_stage, locked):
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/submissions/{locked.pk}/final-grade/",
        {"score": 6, "rationale": "Praca bez recenzentów – ocena komisji."},
    )

    locked.refresh_from_db()
    assert response["Location"] == assignments_url(elim_stage)
    grade = FinalGrade.objects.get(submission=locked)
    assert (grade.score, grade.method) == (6, GradeMethod.COORDINATOR_OVERRIDE)
    assert locked.status == SubmissionStatus.GRADED_PROVISIONAL


def test_override_without_rationale_shows_an_error(web_client, coordinator, elim_stage, locked):
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/submissions/{locked.pk}/final-grade/", {"score": 6, "rationale": ""}, follow=True
    )

    assert "uzasadnienie" in response.content.decode().casefold()
    assert not FinalGrade.objects.exists()


def test_published_results_warning_is_shown(web_client, coordinator, elim_stage, locked):
    """Korekta po ogłoszeniu wyników przechodzi, ale koordynator dostaje ostrzeżenie."""
    ResultsPublication.objects.create(stage=elim_stage)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/submissions/{locked.pk}/final-grade/",
        {"score": 6, "rationale": "Uwzględniona reklamacja uczestnika."},
        follow=True,
    )

    assert "Wyniki tego etapu są już ogłoszone" in response.content.decode()
    assert FinalGrade.objects.get(submission=locked).score == 6


def test_reviewer_cannot_correct_grades(web_client, reviewer, elim_stage, locked):
    review = assign_reviewer_to_submission(locked, ActiveReviewerFactory())
    web_client.force_login(reviewer.user)

    score_response = web_client.post(f"/coordinator/reviews/{review.pk}/score/", {"score": 5})
    grade_response = web_client.post(
        f"/coordinator/submissions/{locked.pk}/final-grade/",
        {"score": 5, "rationale": "Chcę sobie wpisać ocenę."},
    )

    assert score_response.status_code == 403
    assert grade_response.status_code == 403
    assert not FinalGrade.objects.exists()
