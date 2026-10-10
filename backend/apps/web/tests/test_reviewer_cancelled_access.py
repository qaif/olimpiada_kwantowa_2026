"""Panel recenzenta po odebraniu pracy – audyt bezpieczeństwa 10.10.2026, S1.

Strona odebranej recenzji zostaje (komunikat zamiast 404, które wyglądałoby na awarię), ale bez
niczego, do czego dawał dostęp przydział: pliku, porównania z cudzymi ocenami, materiału
rozjemczego i narzędzi przy pracy. Osobne adresy – ``/compare/``, notatki, zgłoszenia – są 404.
"""

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import CoordinatorFactory
from apps.grading.models import ROUND_TIEBREAK, Review, ReviewStatus
from apps.grading.services import unassign_reviewer
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def submission(entry, problems):
    created = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.MODERATION)
    SubmissionFileFactory(submission=created, av_status=AvStatus.CLEAN)
    return created


@pytest.fixture
def pair(submission, reviewer):
    """Dwie wystawione, różne oceny rundy 1 – porównanie jest już odsłonięte."""
    own = ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.SUBMITTED, score=5)
    other = ReviewFactory(
        submission=submission,
        status=ReviewStatus.SUBMITTED,
        score=2,
        comment_for_participant="TAJNY KOMENTARZ DRUGIEJ OCENY",
    )
    return own, other


def _download(submission_id) -> str:
    return reverse("submissions:submission-download", kwargs={"pk": submission_id})


def _withdraw(review):
    Review.objects.filter(pk=review.pk).update(status=ReviewStatus.CANCELLED)
    review.refresh_from_db()


def test_detail_shows_the_comparison_while_assigned(web_client, pair):
    own, _ = pair
    web_client.force_login(own.reviewer.user)

    content = web_client.get(f"/review/{own.pk}/").content.decode()

    assert "TAJNY KOMENTARZ DRUGIEJ OCENY" in content
    assert _download(own.submission_id) in content


def test_detail_of_a_withdrawn_review_has_no_file_and_no_comparison(web_client, pair):
    own, _ = pair
    _withdraw(own)
    web_client.force_login(own.reviewer.user)

    response = web_client.get(f"/review/{own.pk}/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Ta praca nie jest już Ci przydzielona" in content
    assert "TAJNY KOMENTARZ DRUGIEJ OCENY" not in content
    assert _download(own.submission_id) not in content
    assert "data-pdf-url" not in content


def test_compare_page_of_a_withdrawn_review_is_404(web_client, pair):
    own, _ = pair
    web_client.force_login(own.reviewer.user)
    assert web_client.get(f"/review/{own.pk}/compare/").status_code == 200

    _withdraw(own)

    assert web_client.get(f"/review/{own.pk}/compare/").status_code == 404


def test_withdrawn_tiebreak_detail_has_no_dispute_material(web_client, submission, pair, reviewer):
    """Rozjemca odsunięty od sprawy nie czyta już argumentacji rundy 1 (``/dispute/`` w panelu)."""
    from apps.accounts.tests.factories import ActiveReviewerFactory

    third = ActiveReviewerFactory()
    tiebreak = ReviewFactory(submission=submission, reviewer=third, round=ROUND_TIEBREAK)
    web_client.force_login(third.user)
    assert "Materiał rozjemczy" in web_client.get(f"/review/{tiebreak.pk}/").content.decode()

    unassign_reviewer(Review.objects.get(pk=tiebreak.pk), actor=CoordinatorFactory())

    content = web_client.get(f"/review/{tiebreak.pk}/").content.decode()
    assert "Materiał rozjemczy" not in content
    assert _download(submission.pk) not in content


def test_actions_on_a_withdrawn_review_are_404(web_client, pair):
    own, _ = pair
    _withdraw(own)
    web_client.force_login(own.reviewer.user)

    assert web_client.post(f"/review/{own.pk}/notes/", {"text": "hej"}).status_code == 404
    assert web_client.post(f"/review/{own.pk}/heartbeat/").status_code == 404
    assert web_client.post(f"/review/{own.pk}/draft/", {"score": 2}).status_code == 404
