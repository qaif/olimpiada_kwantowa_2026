"""Nazwa pobieranego pliku jako dana osobowa (przegląd Critica T-05, finding 1).

``SubmissionFile.original_name`` pochodzi od uczestnika i w praktyce zawiera nazwisko albo szkołę
(„Jan_Kowalski_LO5.pdf”). Ocenianie jest ślepe, więc każdy, kto nie jest autorem pracy, dostaje
nazwę zbudowaną z samego pseudonimu: ``{public_code}-z{numer zadania}-v{wersja}.{rozszerzenie}``.
Reguła obowiązuje w obu backendach storage: w ``Content-Disposition`` odpowiedzi lokalnej i w
podpisanym parametrze ``response-content-disposition`` presigned URL-a S3.
"""

from io import BytesIO
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.grading.models import ROUND_BLIND
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFileFactory

pytestmark = pytest.mark.django_db

S3_BACKEND = "apps.submissions.storage.S3SubmissionStorage"
#: Nazwa nadana przez uczestnika – dokładnie taka, jakiej ocenianie ślepe nie może pokazać dalej.
LEAKY_NAME = "Jan_Kowalski_LO5.pdf"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def stored_file():
    """Zgłoszenie w ocenie z czystym plikiem o „gadającej” nazwie od uczestnika."""
    stage = StageFactory()
    entry = StageEntryFactory(stage=stage)
    submission_file = SubmissionFileFactory(
        submission__entry=entry,
        submission__problem=ProblemFactory(stage=stage, number=3),
        submission__status=SubmissionStatus.IN_REVIEW,
        original_name=LEAKY_NAME,
        av_status=AvStatus.CLEAN,
    )
    get_submission_storage().put(submission_file.object_key, BytesIO(PDF_BYTES), "application/pdf")
    return submission_file


def download_url(submission) -> str:
    return reverse("submissions:submission-download", kwargs={"pk": submission.pk})


def expected_name(submission) -> str:
    return (
        f"{submission.entry.participant.public_code}-z{submission.problem.number}-v{submission.version}.pdf"
    )


def test_reviewer_download_header_hides_participant_name(client, stored_file):
    """Recenzent dostaje nazwę z pseudonimu – bez nazwiska i bez szkoły z ``original_name``."""
    submission = stored_file.submission
    reviewer = ActiveReviewerFactory()
    ReviewFactory(submission=submission, reviewer=reviewer, round=ROUND_BLIND)
    client.force_authenticate(reviewer.user)

    response = client.get(download_url(submission))

    assert response.status_code == 200
    disposition = response.headers["Content-Disposition"]
    assert "Kowalski" not in disposition
    assert "LO5" not in disposition
    assert submission.entry.participant.public_code in disposition
    assert expected_name(submission) in disposition


def test_coordinator_download_header_is_anonymous_too(client, stored_file):
    """Koordynator też nie jest autorem pracy – nazwa pliku zostaje pseudonimowa."""
    client.force_authenticate(CoordinatorFactory())

    disposition = client.get(download_url(stored_file.submission)).headers["Content-Disposition"]

    assert "Kowalski" not in disposition
    assert expected_name(stored_file.submission) in disposition


def test_owner_still_gets_own_file_name(client, stored_file):
    """Właściciel pobiera swoją pracę pod swoją nazwą – anonimizacja chroni go przed innymi, nie przed nim."""
    client.force_authenticate(stored_file.submission.entry.participant.user)

    disposition = client.get(download_url(stored_file.submission)).headers["Content-Disposition"]

    assert LEAKY_NAME in disposition


@override_settings(
    SUBMISSION_STORAGE_BACKEND=S3_BACKEND,
    S3_ENDPOINT_URL="http://minio:9000",
    S3_PUBLIC_ENDPOINT_URL="https://s3.example.test",
    S3_ACCESS_KEY="dummy-access-key",
    S3_SECRET_KEY="dummy-secret-key",
)
def test_presigned_url_forces_the_anonymous_file_name(client, stored_file):
    """Na S3 nazwę narzuca podpisany ``response-content-disposition``, nie metadane obiektu."""
    submission = stored_file.submission
    reviewer = ActiveReviewerFactory()
    ReviewFactory(submission=submission, reviewer=reviewer, round=ROUND_BLIND)
    client.force_authenticate(reviewer.user)

    response = client.get(download_url(submission))

    assert response.status_code == 302
    query = parse_qs(urlparse(response["Location"]).query)
    disposition = unquote(query["response-content-disposition"][0])
    assert submission.entry.participant.public_code in disposition
    assert expected_name(submission) in disposition
    assert "Kowalski" not in disposition
    # URL jest podpisany razem z tym parametrem – pobierający nie podmieni sobie nazwy w locie.
    assert "X-Amz-Signature" in query
