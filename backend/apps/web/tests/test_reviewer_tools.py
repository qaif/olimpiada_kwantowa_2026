"""Panel recenzenta: rubryka, wzorcówka, porównanie ocen, serie prac i terminy.

Pięć funkcji, o które prosił organizator, oglądanych od strony ekranu: to tu widać, czy reguła
z ``apps.grading`` faktycznie dociera do recenzenta – i czy po drodze nie wypływa nic, czego
widzieć nie ma (tożsamość drugiego recenzenta, wzorcówka dla uczestnika).
"""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.grading.comparison import add_review_note
from apps.grading.models import Review, ReviewNote, ReviewStatus, RubricCriterion
from apps.grading.rubric import criteria_for
from apps.grading.services import submit_review
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def submission(entry, problems):
    return SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)


@pytest.fixture
def review(submission, reviewer):
    return ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.ASSIGNED)


@pytest.fixture
def criteria(problems):
    """Rubryka 2 + 4 punkty przy pierwszym zadaniu: sumy 0, 2, 5, 6 są osiągalne, 4 – nie."""
    return [
        RubricCriterion.objects.create(problem=problems[0], order=1, title="Pomysł", max_points=2),
        RubricCriterion.objects.create(problem=problems[0], order=2, title="Wykonanie", max_points=4),
    ]


def rubric_post(criteria, *points, **extra):
    data = {
        "comment_internal": "",
        "comment_for_participant": "",
        "annotations": "",
        **extra,
    }
    for criterion, value in zip(criteria, points, strict=True):
        data[f"rubric-{criterion.pk}-points"] = value
        data[f"rubric-{criterion.pk}-comment"] = ""
    return data


# --- rubryka ------------------------------------------------------------------------------------


def test_detail_shows_rubric_fields_instead_of_the_scale(web_client, review, criteria):
    web_client.force_login(review.reviewer.user)

    content = web_client.get(f"/review/{review.pk}/").content.decode()

    assert f'name="rubric-{criteria[0].pk}-points"' in content
    assert f'name="rubric-{criteria[1].pk}-comment"' in content
    assert 'name="score" value="2"' not in content


def test_problem_without_rubric_keeps_the_plain_scale(web_client, review):
    web_client.force_login(review.reviewer.user)

    content = web_client.get(f"/review/{review.pk}/").content.decode()

    assert 'name="score" value="2"' in content
    assert "Rubryka oceniania" not in content


def test_submitting_the_rubric_computes_the_score(web_client, review, criteria):
    web_client.force_login(review.reviewer.user)

    response = web_client.post(f"/review/{review.pk}/submit/", rubric_post(criteria, 2, 3))

    review.refresh_from_db()
    assert response.status_code == 302
    assert review.status == ReviewStatus.SUBMITTED
    assert review.score == 5
    assert [item["points"] for item in review.rubric] == [2, 3]


def test_rubric_sum_outside_the_scale_keeps_the_review_open(web_client, review, criteria):
    web_client.force_login(review.reviewer.user)

    response = web_client.post(f"/review/{review.pk}/submit/", rubric_post(criteria, 0, 4), follow=True)

    review.refresh_from_db()
    assert review.status == ReviewStatus.ASSIGNED
    assert "0, 2, 5, 6" in response.content.decode()


def test_draft_saves_an_unfinished_rubric(web_client, review, criteria):
    web_client.force_login(review.reviewer.user)

    web_client.post(
        f"/review/{review.pk}/draft/",
        {
            "comment_internal": "robocze",
            "comment_for_participant": "",
            "annotations": "",
            f"rubric-{criteria[0].pk}-points": 2,
            f"rubric-{criteria[0].pk}-comment": "pomysł jest",
            f"rubric-{criteria[1].pk}-points": "",
            f"rubric-{criteria[1].pk}-comment": "",
        },
    )

    review.refresh_from_db()
    assert review.status == ReviewStatus.DRAFT
    assert review.score is None
    assert review.rubric[0]["comment"] == "pomysł jest"


# --- wzorcówka i uwagi dla recenzentów ----------------------------------------------------------


def test_detail_links_the_model_solution_and_shows_reviewer_notes(web_client, review, problems):
    problem = problems[0]
    problem.reviewer_notes = "Uznajemy rozwiązanie bez sprawdzenia warunku brzegowego."
    problem.model_solution_pdf.save("wzorcowka.pdf", SimpleUploadedFile("wzorcowka.pdf", PDF_BYTES))
    web_client.force_login(review.reviewer.user)

    content = web_client.get(f"/review/{review.pk}/").content.decode()

    assert "Rozwiązanie wzorcowe i uwagi dla recenzentów" in content
    assert reverse("web:problem-model-solution", kwargs={"pk": problem.pk}) in content
    assert "warunku brzegowego" in content


def test_participant_panel_never_links_the_model_solution(web_client, participant, problems, entry):
    problems[0].model_solution_pdf.save("wzorcowka.pdf", SimpleUploadedFile("w.pdf", PDF_BYTES))
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert reverse("web:problem-model-solution", kwargs={"pk": problems[0].pk}) not in content


# --- porównanie ocen i notatki ------------------------------------------------------------------


@pytest.fixture
def revealed(submission, review):
    """Obie oceny rundy 1 wystawione – rozjazd 2 vs 5, czyli praca idzie do moderacji."""
    second = ReviewFactory(submission=submission, reviewer=ActiveReviewerFactory())
    submit_review(review, 2, comment_for_participant="Brakuje uzasadnienia.")
    submit_review(second, 5, comment_for_participant="Rachunki poprawne.")
    review.refresh_from_db()
    second.refresh_from_db()
    return second


def test_comparison_appears_only_after_both_reviews(web_client, review, submission):
    web_client.force_login(review.reviewer.user)
    assert "Porównanie ocen" not in web_client.get(f"/review/{review.pk}/").content.decode()


def test_comparison_shows_the_other_score_without_the_identity(web_client, review, revealed):
    web_client.force_login(review.reviewer.user)

    content = web_client.get(f"/review/{review.pk}/").content.decode()

    assert "Porównanie ocen" in content
    assert "Rachunki poprawne." in content
    assert "Recenzent B" in content
    assert revealed.reviewer.user.email not in content


def test_reviewer_posts_a_note_from_the_panel(web_client, review, revealed, submission):
    web_client.force_login(review.reviewer.user)

    response = web_client.post(
        f"/review/{review.pk}/notes/", {"text": "Proponuję 5 – krok 3 da się obronić."}, follow=True
    )

    assert ReviewNote.objects.filter(submission=submission).count() == 1
    assert "Proponuję 5" in response.content.decode()


def test_note_is_refused_before_the_scores_are_revealed(web_client, review, submission):
    web_client.force_login(review.reviewer.user)

    response = web_client.post(f"/review/{review.pk}/notes/", {"text": "Za wcześnie."}, follow=True)

    assert not ReviewNote.objects.exists()
    assert "Notatki otwierają się dopiero" in response.content.decode()


def test_compare_page_says_when_it_is_too_early(web_client, review):
    web_client.force_login(review.reviewer.user)

    content = web_client.get(f"/review/{review.pk}/compare/").content.decode()

    assert "nie są jeszcze odsłonięte" in content


def test_compare_page_is_404_for_another_reviewer(web_client, review):
    web_client.force_login(ActiveReviewerFactory().user)

    assert web_client.get(f"/review/{review.pk}/compare/").status_code == 404


def test_coordinator_sees_the_notes_on_the_moderation_queue(web_client, coordinator, review, revealed):
    add_review_note(review.submission, review.reviewer, "Proponuję 5.")
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert "Notatki recenzentów" in content
    assert "Proponuję 5." in content


# --- serie prac i terminy -----------------------------------------------------------------------


def test_detail_links_the_neighbouring_submissions_of_the_same_problem(
    web_client, review, reviewer, problems, elim_stage
):
    from apps.accounts.tests.factories import ParticipantFactory
    from apps.competitions.tests.factories import StageEntryFactory

    others = []
    for _ in range(2):
        entry = StageEntryFactory(stage=elim_stage, participant=ParticipantFactory())
        submission = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)
        others.append(ReviewFactory(submission=submission, reviewer=reviewer))
    web_client.force_login(reviewer.user)

    content = web_client.get(f"/review/{others[0].pk}/").content.decode()

    assert "2 z 3 w tym zadaniu" in content
    assert f'href="/review/{review.pk}/"' in content
    assert f'href="/review/{others[1].pk}/"' in content


def test_list_groups_by_problem_and_marks_overdue_rows(web_client, review, reviewer):
    Review.objects.filter(pk=review.pk).update(due_at=timezone.now() - timedelta(days=1))
    web_client.force_login(reviewer.user)

    content = web_client.get("/review/").content.decode()

    assert "zadanie 1" in content
    assert "1 z 1 do zrobienia" in content
    assert "po terminie" in content


def test_list_shows_the_due_date_when_the_review_is_on_time(web_client, review, reviewer):
    Review.objects.filter(pk=review.pk).update(due_at=timezone.now() + timedelta(days=5))
    web_client.force_login(reviewer.user)

    content = web_client.get("/review/").content.decode()

    assert "po terminie" not in content


# --- ekran koordynatora -------------------------------------------------------------------------


def test_coordinator_saves_rubric_model_solution_and_notes(web_client, coordinator, problems):
    problem = problems[0]
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/problems/{problem.pk}/edit/",
        {
            "number": problem.number,
            "title": problem.title,
            "allowed_formats": ["pdf"],
            "max_file_mb": 20,
            "scoring_values": "",
            "max_points": "",
            "reviewer_notes": "Bez sprawdzenia warunku brzegowego – najwyżej 5 punktów.",
            "rubric": "2;Pomysł;jak uczestnik podszedł\n4;Wykonanie",
            "model_solution_pdf": SimpleUploadedFile("wzorcowka.pdf", PDF_BYTES),
        },
    )

    problem.refresh_from_db()
    assert response.status_code == 302
    assert problem.model_solution_pdf.read() == PDF_BYTES
    assert "warunku brzegowego" in problem.reviewer_notes
    assert [(item.max_points, item.title) for item in criteria_for(problem)] == [
        (2, "Pomysł"),
        (4, "Wykonanie"),
    ]


def test_coordinator_form_reports_a_malformed_rubric_line(web_client, coordinator, problems):
    problem = problems[0]
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/problems/{problem.pk}/edit/",
        {
            "number": problem.number,
            "title": problem.title,
            "allowed_formats": ["pdf"],
            "max_file_mb": 20,
            "scoring_values": "",
            "max_points": "",
            "reviewer_notes": "",
            "rubric": "Pomysł bez punktów",
        },
    )

    assert response.status_code == 400
    assert "brakuje średnika" in response.content.decode()
    assert criteria_for(problem) == []


def test_assignment_message_mentions_the_due_date(web_client, coordinator, submission, elim_stage):
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    submission.status = SubmissionStatus.LOCKED
    submission.save(update_fields=["status"])
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/assign/", {"per_submission": 2}, follow=True
    )

    assert "Termin recenzji:" in response.content.decode()
