"""Kryteria 3, 4, 8, 9, 10 T-05: ślepa lista przydziałów, uprawnienia, pobranie pliku, wyścig ocen."""

from io import BytesIO

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    PendingReviewerFactory,
)
from apps.grading.models import ROUND_BLIND, ReviewStatus
from apps.grading.services import submit_review
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFileFactory

from .conftest import locked_submission

pytestmark = pytest.mark.django_db

REVIEWS_URL = "/api/grading/reviews/"

# Dane, które w ocenianiu ślepym nie mogą wypłynąć do recenzenta w żadnej postaci.
SECRET_FIRST_NAME = "Zenobia"
SECRET_LAST_NAME = "Nieujawniona"
SECRET_SCHOOL = "Tajne Liceum nr 42"


def in_review(stage, **participant_kwargs):
    submission = locked_submission(stage, **participant_kwargs)
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status"])
    return submission


def test_reviews_list_shows_only_own_assignments_without_personal_data(client, stage):
    """3. GET reviews/ zwraca tylko własne przydziały i nie ujawnia danych osobowych uczestnika."""
    submission = in_review(
        stage,
        school=SECRET_SCHOOL,
        user__first_name=SECRET_FIRST_NAME,
        user__last_name=SECRET_LAST_NAME,
        user__email="tajny.uczestnik@example.test",
    )
    mine = ReviewFactory(submission=submission)
    other_submission = in_review(stage)
    ReviewFactory(submission=other_submission)
    client.force_authenticate(mine.reviewer.user)

    response = client.get(REVIEWS_URL)

    assert response.status_code == 200
    assert [item["id"] for item in response.data] == [mine.pk]
    item = response.data[0]
    assert item["participant_public_code"] == submission.entry.participant.public_code
    assert item["problem_number"] == submission.problem.number
    assert item["download_url"] == reverse("submissions:submission-download", kwargs={"pk": submission.pk})

    raw = response.content.decode()
    for forbidden in (
        "first_name",
        "last_name",
        "email",
        "school",
        SECRET_FIRST_NAME,
        SECRET_LAST_NAME,
        SECRET_SCHOOL,
        "tajny.uczestnik@example.test",
    ):
        assert forbidden not in raw, forbidden


def test_pending_reviewer_and_participant_are_rejected(client, stage):
    """4. Recenzent PENDING → 403, uczestnik → 403 (sama grupa nie wystarcza)."""
    client.force_authenticate(PendingReviewerFactory().user)
    assert client.get(REVIEWS_URL).status_code == 403

    client.force_authenticate(ParticipantFactory().user)
    assert client.get(REVIEWS_URL).status_code == 403

    client.force_authenticate(None)
    assert client.get(REVIEWS_URL).status_code == 401


def test_reviewer_cannot_open_someone_elses_review(client, stage):
    """IDOR: cudza recenzja to 404, nie 403 – odpowiedź nie potwierdza jej istnienia."""
    foreign = ReviewFactory(submission=in_review(stage))
    client.force_authenticate(ActiveReviewerFactory().user)

    assert client.get(f"{REVIEWS_URL}{foreign.pk}/").status_code == 404
    assert client.post(f"{REVIEWS_URL}{foreign.pk}/submit/", {"score": 6}, format="json").status_code == 404
    assert client.patch(f"{REVIEWS_URL}{foreign.pk}/", {"score": 6}, format="json").status_code == 404


def test_reviewer_does_not_see_other_score_in_round_one(client, stage):
    """9. Recenzent A nie widzi oceny recenzenta B przed zakończeniem rundy 1."""
    submission = in_review(stage)
    first = ReviewFactory(submission=submission)
    second = ReviewFactory(submission=submission)
    submit_review(first, 5, "Tajny komentarz recenzenta A", "Dla uczestnika A", [])

    client.force_authenticate(second.reviewer.user)
    listing = client.get(REVIEWS_URL)
    detail = client.get(f"{REVIEWS_URL}{second.pk}/")

    assert listing.status_code == detail.status_code == 200
    assert [item["id"] for item in listing.data] == [second.pk]
    assert detail.data["score"] is None
    assert detail.data["status"] == ReviewStatus.ASSIGNED
    for response in (listing, detail):
        raw = response.content.decode()
        assert "Tajny komentarz recenzenta A" not in raw
        assert "Dla uczestnika A" not in raw
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.IN_REVIEW


def test_second_submit_returns_409(client, stage):
    """10. Ponowne wystawienie tej samej oceny → 409 REVIEW_ALREADY_SUBMITTED."""
    review = ReviewFactory(submission=in_review(stage))
    client.force_authenticate(review.reviewer.user)
    url = f"{REVIEWS_URL}{review.pk}/submit/"

    first = client.post(url, {"score": 5}, format="json")
    second = client.post(url, {"score": 6}, format="json")

    assert first.status_code == 200
    assert first.data["status"] == ReviewStatus.SUBMITTED
    assert second.status_code == 409
    assert second.data["code"] == "REVIEW_ALREADY_SUBMITTED"


def test_draft_patch_accepts_partial_data_without_final_validation(client, stage):
    """PATCH zapisuje szkic: ocena spoza skali jest dopuszczalna, dopóki recenzja nie jest wystawiona."""
    review = ReviewFactory(submission=in_review(stage))
    client.force_authenticate(review.reviewer.user)

    response = client.patch(
        f"{REVIEWS_URL}{review.pk}/",
        {
            "score": 3,
            "comment_internal": "roboczo",
            "annotations": [{"page": 2, "rect": [1, 2, 3, 4], "text": "tu", "public": False}],
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == ReviewStatus.DRAFT
    assert response.data["score"] == 3
    assert response.data["annotations"] == [
        {"page": 2, "rect": [1.0, 2.0, 3.0, 4.0], "text": "tu", "public": False}
    ]
    # Ta sama ocena przy wystawieniu jest już odrzucana (kryterium 5 na poziomie API).
    submitted = client.post(f"{REVIEWS_URL}{review.pk}/submit/", {"score": 3}, format="json")
    assert submitted.status_code == 400
    assert submitted.data["code"] == "SCORE_NOT_IN_SCALE"


def test_malformed_annotations_are_rejected(client, stage):
    """Adnotacje są walidowane strukturalnie – JSON od recenzenta nie idzie do bazy „jak leci”."""
    review = ReviewFactory(submission=in_review(stage))
    client.force_authenticate(review.reviewer.user)

    response = client.patch(
        f"{REVIEWS_URL}{review.pk}/", {"annotations": [{"page": 0, "rect": [1, 2]}]}, format="json"
    )

    assert response.status_code == 400
    assert response.data["code"] == "INVALID_ANNOTATIONS"


@pytest.fixture
def stored_file(stage):
    """Zgłoszenie w ocenie z realnym, czystym plikiem w storage testowym."""
    submission = in_review(stage)
    submission_file = SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN)
    get_submission_storage().put(submission_file.object_key, BytesIO(PDF_BYTES), "application/pdf")
    return submission_file


def download_url(submission) -> str:
    return reverse("submissions:submission-download", kwargs={"pk": submission.pk})


def test_reviewer_downloads_only_assigned_submission(client, stored_file):
    """8. Bez przydziału 404; z przydziałem plik po czystym skanie."""
    stranger = ActiveReviewerFactory()
    client.force_authenticate(stranger.user)
    assert client.get(download_url(stored_file.submission)).status_code == 404

    review = ReviewFactory(submission=stored_file.submission, reviewer=stranger, round=ROUND_BLIND)
    assert review.pk is not None

    response = client.get(download_url(stored_file.submission))
    assert response.status_code in (200, 302)
    if response.status_code == 200:
        assert b"".join(response.streaming_content) == PDF_BYTES


def test_reviewer_cannot_download_file_before_clean_scan(client, stored_file):
    """Recenzent dostaje plik dopiero po ``av_status == CLEAN`` – wcześniej 403 FILE_NOT_CLEAN."""
    stored_file.av_status = AvStatus.PENDING
    stored_file.save(update_fields=["av_status"])
    review = ReviewFactory(submission=stored_file.submission)
    client.force_authenticate(review.reviewer.user)

    response = client.get(download_url(stored_file.submission))

    assert response.status_code == 403
    assert response.data["code"] == "FILE_NOT_CLEAN"


def test_coordinator_sees_moderation_queue_with_both_scores(client, stage):
    """GET moderation/ pokazuje koordynatorowi rozjazd wraz z obiema ocenami rundy 1."""
    submission = in_review(stage)
    first = ReviewFactory(submission=submission)
    second = ReviewFactory(submission=submission)
    submit_review(first, 5, "a", "", [])
    submit_review(second, 2, "b", "", [])

    client.force_authenticate(ActiveReviewerFactory().user)
    assert client.get("/api/grading/moderation/").status_code == 403

    client.force_authenticate(CoordinatorFactory())
    response = client.get("/api/grading/moderation/")

    assert response.status_code == 200
    assert len(response.data) == 1
    assert sorted(item["score"] for item in response.data[0]["reviews"]) == [2, 5]
