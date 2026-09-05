"""Kryteria 5–6 z T-08 plus test XSS: panel recenzenta, skala, ślepa ocena, adnotacje."""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.grading.models import ROUND_TIEBREAK, Review, ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import PARTICIPANT_LAST_NAME

pytestmark = pytest.mark.django_db

XSS_PAYLOAD = "<script>alert('xss')</script>"


@pytest.fixture
def submission(entry, problems):
    return SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)


@pytest.fixture
def review(submission, reviewer):
    return ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.ASSIGNED)


def test_review_list_shows_public_code_not_personal_data(web_client, review, participant):
    web_client.force_login(review.reviewer.user)
    content = web_client.get("/review/").content.decode()

    assert participant.public_code in content
    assert PARTICIPANT_LAST_NAME not in content
    assert participant.user.email not in content


def test_review_detail_has_full_scale_and_no_participant_name(web_client, review, participant):
    web_client.force_login(review.reviewer.user)
    response = web_client.get(f"/review/{review.pk}/")
    content = response.content.decode()

    assert response.status_code == 200
    for value in (0, 2, 5, 6):
        assert f'name="score" value="{value}"' in content
    assert "rozwiązanie pełne i poprawne" in content
    assert PARTICIPANT_LAST_NAME not in content
    assert participant.user.email not in content
    assert participant.school not in content


def test_review_detail_is_404_for_other_reviewer(web_client, review):
    other = ActiveReviewerFactory()
    web_client.force_login(other.user)
    assert web_client.get(f"/review/{review.pk}/").status_code == 404


def test_draft_is_saved_through_htmx(web_client, review):
    web_client.force_login(review.reviewer.user)

    response = web_client.post(
        f"/review/{review.pk}/draft/",
        {
            "score": 2,
            "comment_internal": "wstępne uwagi",
            "comment_for_participant": "",
            "annotations": '[{"page": 1, "rect": [0.1, 0.1, 0.2, 0.2], "text": "tu", "public": false}]',
        },
        HTTP_HX_REQUEST="true",
    )

    review.refresh_from_db()
    assert response.status_code == 200
    assert "Szkic zapisany" in response.content.decode()
    assert review.status == ReviewStatus.DRAFT
    assert review.score == 2
    assert review.annotations[0]["text"] == "tu"


def test_submit_through_ui_marks_review_submitted(web_client, review, submission, reviewer):
    # Drugi przydział rundy 1 zostaje otwarty, więc runda się nie domyka i sprawdzamy
    # dokładnie to, co mówi kryterium 6: stan recenzji po wysłaniu z UI.
    ReviewFactory(submission=submission, reviewer=ActiveReviewerFactory())
    web_client.force_login(reviewer.user)

    response = web_client.post(
        f"/review/{review.pk}/submit/",
        {
            "score": 5,
            "comment_internal": "pełne",
            "comment_for_participant": "dobra praca",
            "annotations": "",
        },
    )

    review.refresh_from_db()
    assert response.status_code == 302
    assert response.headers["Location"] == "/review/"
    assert review.status == ReviewStatus.SUBMITTED
    assert review.score == 5


def test_annotation_with_script_is_rendered_as_text(web_client, review):
    """Adnotacja z ``<script>`` nie może opuścić szablonu jako kod – ani w liście, ani w atrybucie."""
    review.annotations = [{"page": 1, "rect": [0.1, 0.1, 0.2, 0.2], "text": XSS_PAYLOAD, "public": True}]
    review.comment_for_participant = XSS_PAYLOAD
    review.save(update_fields=["annotations", "comment_for_participant"])
    web_client.force_login(review.reviewer.user)

    content = web_client.get(f"/review/{review.pk}/").content.decode()

    assert XSS_PAYLOAD not in content
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in content
    assert "<script>alert" not in content


def test_dispute_panel_is_visible_for_tiebreak_review(web_client, submission, reviewer):
    first = ReviewFactory(
        submission=submission,
        reviewer=ActiveReviewerFactory(),
        status=ReviewStatus.SUBMITTED,
        score=2,
        comment_internal="brakuje kluczowego kroku",
    )
    ReviewFactory(
        submission=submission,
        reviewer=ActiveReviewerFactory(),
        status=ReviewStatus.SUBMITTED,
        score=6,
        comment_internal="rozumowanie pełne",
    )
    submission.status = SubmissionStatus.MODERATION
    submission.save(update_fields=["status"])
    tiebreak = ReviewFactory(submission=submission, reviewer=reviewer, round=ROUND_TIEBREAK)
    web_client.force_login(reviewer.user)

    content = web_client.get(f"/review/{tiebreak.pk}/").content.decode()

    assert "Materiał rozjemczy" in content
    assert "brakuje kluczowego kroku" in content
    assert "rozumowanie pełne" in content
    assert first.reviewer.user.email not in content


def test_review_detail_of_round_one_has_no_dispute_panel(web_client, review):
    web_client.force_login(review.reviewer.user)
    assert "Materiał rozjemczy" not in web_client.get(f"/review/{review.pk}/").content.decode()
    assert Review.objects.filter(pk=review.pk, round=1).exists()


def test_submit_without_annotations_field_keeps_existing_ones(web_client, review, submission, reviewer):
    """Puste pole adnotacji nie może wyczyścić tego, co zapisała warstwa pdf.js."""
    review.annotations = [{"page": 1, "rect": [0.1, 0.1, 0.2, 0.2], "text": "luka", "public": False}]
    review.save(update_fields=["annotations"])
    ReviewFactory(submission=submission, reviewer=ActiveReviewerFactory())
    web_client.force_login(reviewer.user)

    web_client.post(f"/review/{review.pk}/submit/", {"score": 5, "annotations": ""})

    review.refresh_from_db()
    assert review.status == ReviewStatus.SUBMITTED
    assert review.annotations[0]["text"] == "luka"
