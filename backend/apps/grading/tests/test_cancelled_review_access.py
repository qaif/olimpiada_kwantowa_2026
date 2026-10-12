"""Odebrana praca (``Review.CANCELLED``) odbiera dostęp – audyt bezpieczeństwa 10.10.2026, S1.

Po ``POST /api/grading/reviews/<id>/unassign/`` (np. konflikt interesów) recenzent pobierał dalej
plik, czytał porównanie z cudzymi ocenami i materiał rozjemczy. Testy pilnują każdej z tych dróg
po stronie API i serwisów; ekrany panelu sprawdza ``apps/web/tests/test_reviewer_cancelled_access.py``.

Przy okazji: neutralny stan pracy dla recenzenta rundy 1 (S4) i bramki ``set_review_score``.
"""

from io import BytesIO

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.core.api import DomainError
from apps.grading.comparison import comparison_context
from apps.grading.models import ROUND_TIEBREAK, Review, ReviewStatus
from apps.grading.serializers import NEUTRAL_SUBMITTED_STATUS
from apps.grading.services import (
    assign_reviewer_to_submission,
    assign_third_reviewer,
    dispute_context,
    reviews_for_reviewer,
    set_review_score,
    submit_review,
    unassign_reviewer,
)
from apps.results.models import ResultsPublication
from apps.submissions.models import AvStatus, Submission, SubmissionStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFileFactory

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def _with_clean_file(submission):
    """Prawdziwy, czysty plik w magazynie – inaczej pobranie odmówiłoby z innego powodu."""
    stored = SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN)
    get_submission_storage().put(stored.object_key, BytesIO(PDF_BYTES), "application/pdf")
    return stored


def _download(submission) -> str:
    return reverse("submissions:submission-download", kwargs={"pk": submission.pk})


def _disputed(stage):
    """Praca z dwiema wystawionymi, różnymi ocenami rundy 1 – w moderacji."""
    submission = locked_submission(stage)
    one = assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    two = assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    submit_review(one, 5, "wewnętrzny jeden", "dla ucznia jeden")
    submit_review(two, 2, "wewnętrzny dwa", "dla ucznia dwa")
    submission.refresh_from_db()
    return submission, one, two


def _withdrawn_pair(stage):
    """Praca z plikiem i dwiema ocenami rundy 1; przydział pierwszego recenzenta odebrany.

    Odebrać da się w każdym stanie poza rozstrzygniętym – tu jest to przydział jeszcze przed
    wystawieniem oceny, czyli zwykła sytuacja „konflikt interesów wyszedł w trakcie”.
    """
    submission = locked_submission(stage)
    _with_clean_file(submission)
    one = assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    two = assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    submit_review(two, 2, "wewnętrzny dwa", "dla ucznia dwa")
    unassign_reviewer(Review.objects.get(pk=one.pk), actor=CoordinatorFactory())
    one.refresh_from_db()
    submission.refresh_from_db()
    assert one.status == ReviewStatus.CANCELLED
    return submission, one, two


# --- pobranie pliku -------------------------------------------------------------------------------


def test_reviewer_downloads_the_file_while_assigned(client, stage):
    submission = locked_submission(stage)
    _with_clean_file(submission)
    review = assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    client.force_authenticate(review.reviewer.user)

    assert client.get(_download(submission)).status_code == 200


def test_reviewer_loses_the_file_after_unassign(client, stage):
    submission, one, _ = _withdrawn_pair(stage)
    client.force_authenticate(one.reviewer.user)

    response = client.get(_download(submission))

    # 404, nie 403 – jak dla każdej cudzej pracy: odpowiedź nie potwierdza, że praca istnieje.
    assert response.status_code == 404
    assert not Submission.objects.for_user(one.reviewer.user).filter(pk=submission.pk).exists()


def test_other_live_assignment_of_the_same_reviewer_keeps_the_file(client, stage):
    """Wykluczenie dotyczy **tej** recenzji – druga, żywa recenzja tej pracy dalej daje dostęp."""
    submission, one, _ = _withdrawn_pair(stage)
    Review.objects.create(submission=submission, reviewer=one.reviewer, round=ROUND_TIEBREAK)
    client.force_authenticate(one.reviewer.user)

    assert client.get(_download(submission)).status_code == 200


# --- API recenzji ---------------------------------------------------------------------------------


def test_api_detail_of_a_withdrawn_review_is_404(client, stage):
    _, one, _ = _withdrawn_pair(stage)
    client.force_authenticate(one.reviewer.user)

    assert client.get(f"/api/grading/reviews/{one.pk}/").status_code == 404
    assert client.patch(f"/api/grading/reviews/{one.pk}/", {"score": 2}, format="json").status_code == 404


def test_api_list_keeps_a_withdrawn_review_without_the_file(client, stage):
    """Lista nie gubi odebranej pracy (``cancel_reason``), ale nie podaje do niej adresu pliku."""
    _, one, _ = _withdrawn_pair(stage)
    client.force_authenticate(one.reviewer.user)

    response = client.get("/api/grading/reviews/")

    assert response.status_code == 200
    (row,) = [item for item in response.data if item["id"] == one.pk]
    assert row["status"] == ReviewStatus.CANCELLED
    assert row["download_url"] is None
    assert row["file_available"] is False


def test_api_dispute_of_a_withdrawn_tiebreak_is_404(client, stage):
    submission, _, _ = _disputed(stage)
    third = assign_third_reviewer(submission, ActiveReviewerFactory(), actor=CoordinatorFactory())
    client.force_authenticate(third.reviewer.user)
    assert client.get(f"/api/grading/reviews/{third.pk}/dispute/").status_code == 200

    unassign_reviewer(Review.objects.get(pk=third.pk), actor=CoordinatorFactory())

    assert client.get(f"/api/grading/reviews/{third.pk}/dispute/").status_code == 404


def test_services_refuse_a_withdrawn_review(stage):
    """Serwisy odmawiają same z siebie – nie tylko dlatego, że widok nie znalazł recenzji."""
    submission, one, two = _disputed(stage)
    Review.objects.filter(pk=one.pk).update(status=ReviewStatus.CANCELLED)
    one.refresh_from_db()
    two.refresh_from_db()
    # Druga recenzja widzi porównanie (oceny odsłonięte), odebrana – nie.
    assert comparison_context(two) is not None
    assert comparison_context(one) is None
    assert not reviews_for_reviewer(one.reviewer).filter(pk=one.pk).exists()
    assert reviews_for_reviewer(one.reviewer, include_cancelled=True).filter(pk=one.pk).exists()

    tiebreak = Review(submission=submission, round=ROUND_TIEBREAK, status=ReviewStatus.CANCELLED)
    with pytest.raises(DomainError) as exc:
        dispute_context(tiebreak)
    assert exc.value.status_code == 404


# --- neutralny stan pracy dla rundy 1 (S4) -------------------------------------------------------


def test_round_one_reviewer_sees_a_neutral_submission_status(client, stage):
    """MODERATION kontra GRADED_PROVISIONAL mówiło recenzentowi, czy zgodził się z drugą oceną."""
    submission, one, _ = _disputed(stage)
    assert submission.status == SubmissionStatus.MODERATION
    client.force_authenticate(one.reviewer.user)

    detail = client.get(f"/api/grading/reviews/{one.pk}/")
    listing = client.get("/api/grading/reviews/")

    assert detail.data["submission_status"] == NEUTRAL_SUBMITTED_STATUS
    assert [row["submission_status"] for row in listing.data] == [NEUTRAL_SUBMITTED_STATUS]


def test_round_one_revision_in_moderation_is_409_over_the_api(client, stage):
    _, one, _ = _disputed(stage)
    client.force_authenticate(one.reviewer.user)

    response = client.post(f"/api/grading/reviews/{one.pk}/revise/", {"score": 2}, format="json")

    assert response.status_code == 409
    assert response.data["code"] == "IN_MODERATION"


def test_coordinator_response_keeps_the_real_submission_status(client, stage):
    submission, _, _ = _disputed(stage)
    client.force_authenticate(CoordinatorFactory())

    response = client.post(
        reverse("grading:moderation-assign-third", kwargs={"submission_id": submission.pk}),
        {"reviewer_id": ActiveReviewerFactory().pk},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["submission_status"] == SubmissionStatus.MODERATION


# --- set_review_score (niskie) --------------------------------------------------------------------


def test_coordinator_cannot_score_a_withdrawn_review(stage):
    """Wpisanie punktów wskrzeszało odebraną recenzję (status → SUBMITTED) i wracało do konsensusu."""
    _, one, _ = _withdrawn_pair(stage)

    with pytest.raises(DomainError) as exc:
        set_review_score(one, 2, actor=CoordinatorFactory())

    assert exc.value.machine_code == "REVIEW_CANCELLED"
    one.refresh_from_db()
    assert one.status == ReviewStatus.CANCELLED
    assert one.score is None


def test_coordinator_cannot_score_a_review_after_results_are_published(stage):
    submission = locked_submission(stage)
    review = assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    ResultsPublication.objects.create(stage=stage)

    with pytest.raises(DomainError) as exc:
        set_review_score(review, 5, actor=CoordinatorFactory())

    assert exc.value.machine_code == "RESULTS_PUBLISHED"
