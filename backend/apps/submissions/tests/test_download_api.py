"""Kryterium 7 (część o pobieraniu) oraz widoczność rozwiązań per rola."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.submissions.models import AvStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFileFactory

pytestmark = pytest.mark.django_db


def download_url(submission) -> str:
    return reverse("submissions:submission-download", kwargs={"pk": submission.pk})


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def stored_submission():
    """Zgłoszenie z realnym plikiem w storage lokalnym (backend testowy)."""
    stage = StageFactory()
    problem = ProblemFactory(stage=stage, number=1)
    entry = StageEntryFactory(stage=stage)
    submission_file = SubmissionFileFactory(
        submission__entry=entry, submission__problem=problem, av_status=AvStatus.CLEAN
    )
    from io import BytesIO

    get_submission_storage().put(submission_file.object_key, BytesIO(PDF_BYTES), "application/pdf")
    return submission_file


def test_owner_downloads_own_clean_file(client, stored_submission):
    client.force_authenticate(stored_submission.submission.entry.participant.user)

    response = client.get(download_url(stored_submission.submission))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF_BYTES


def test_other_participant_gets_404_not_403(client, stored_submission):
    stranger = StageEntryFactory(stage=stored_submission.submission.entry.stage).participant
    client.force_authenticate(stranger.user)

    response = client.get(download_url(stored_submission.submission))

    # 404, nie 403: odpowiedź nie może potwierdzać, że cudze zgłoszenie w ogóle istnieje.
    assert response.status_code == 404


def test_coordinator_may_download_clean_file(client, stored_submission):
    client.force_authenticate(CoordinatorFactory())

    assert client.get(download_url(stored_submission.submission)).status_code == 200


def test_unscanned_file_is_available_only_to_owner(client, stored_submission):
    stored_submission.av_status = AvStatus.PENDING
    stored_submission.save(update_fields=["av_status"])

    client.force_authenticate(CoordinatorFactory())
    coordinator_response = client.get(download_url(stored_submission.submission))
    assert coordinator_response.status_code == 403
    assert coordinator_response.data["code"] == "FILE_NOT_CLEAN"

    client.force_authenticate(stored_submission.submission.entry.participant.user)
    assert client.get(download_url(stored_submission.submission)).status_code == 200


def test_reviewer_sees_nothing_until_assignments_exist(client, stored_submission):
    """Przydziały recenzentów dochodzą w T-05 – do tego czasu recenzent nie widzi rozwiązań."""
    client.force_authenticate(ActiveReviewerFactory().user)

    assert client.get(download_url(stored_submission.submission)).status_code in (403, 404)


def test_my_submissions_lists_latest_and_history(client, stored_submission):
    from apps.submissions.tests.factories import SubmissionFactory

    submission = stored_submission.submission
    SubmissionFactory(entry=submission.entry, problem=submission.problem, version=2)
    client.force_authenticate(submission.entry.participant.user)

    response = client.get(reverse("submissions:my-submissions"))

    assert response.status_code == 200
    assert len(response.data) == 1
    group = response.data[0]
    assert group["latest"]["version"] == 2
    assert [item["version"] for item in group["history"]] == [1]
