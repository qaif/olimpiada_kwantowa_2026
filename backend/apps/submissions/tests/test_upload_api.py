"""Kryteria 1–7 T-04: upload, wersjonowanie, deadline z tolerancją, walidacja treści, izolacja."""

from datetime import timedelta

import pytest
from django.urls import reverse
from freezegun import freeze_time
from rest_framework.test import APIClient

from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.submissions.models import AvStatus, Submission, SubmissionFile, SubmissionStatus
from apps.submissions.tests.factories import ZIP_BYTES, notebook_bytes, pdf_upload, upload

pytestmark = pytest.mark.django_db


def upload_url(stage, number: int) -> str:
    return reverse("submissions:submission-create", kwargs={"stage_id": stage.pk, "number": number})


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def scenario():
    """Otwarty etap, jedno zadanie (tylko PDF) i zarejestrowany uczestnik."""
    stage = StageFactory()
    problem = ProblemFactory(stage=stage, number=1, allowed_formats=["pdf"], max_file_mb=1)
    entry = StageEntryFactory(stage=stage)
    return stage, problem, entry


def post_file(client, stage, number, file_obj):
    return client.post(upload_url(stage, number), {"file": file_obj}, format="multipart")


# --- kryterium 1 -------------------------------------------------------------------------


def test_upload_pdf_creates_first_version(client, scenario, clamd, django_capture_on_commit_callbacks):
    stage, problem, entry = scenario
    client.force_authenticate(entry.participant.user)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = post_file(client, stage, 1, pdf_upload())

    assert response.status_code == 201, response.data
    assert response.data["version"] == 1
    assert response.data["status"] == SubmissionStatus.SUBMITTED
    # Odpowiedź opisuje stan sprzed skanu: uczestnik dostaje 201 natychmiast, skan idzie w tle.
    assert response.data["file"]["av_status"] == AvStatus.PENDING
    assert response.data["file"]["original_name"] == "rozwiazanie.pdf"
    assert len(response.data["file"]["sha256"]) == 64

    submission = Submission.objects.get(pk=response.data["id"])
    assert (submission.entry, submission.problem, submission.is_late) == (entry, problem, False)
    assert len(callbacks) == 1, "skan musi być kolejkowany dokładnie raz, po commicie"

    submission_file = SubmissionFile.objects.get(submission=submission)
    submission_file.refresh_from_db()
    assert submission_file.av_status == AvStatus.CLEAN
    assert clamd.scanned, "zadanie skanu musi przeczytać treść pliku ze storage"


# --- kryterium 2 -------------------------------------------------------------------------


def test_second_upload_creates_second_version(client, scenario):
    stage, problem, entry = scenario
    client.force_authenticate(entry.participant.user)

    first = post_file(client, stage, 1, pdf_upload("pierwsze.pdf"))
    second = post_file(client, stage, 1, pdf_upload("drugie.pdf"))

    assert (first.status_code, second.status_code) == (201, 201)
    assert (first.data["version"], second.data["version"]) == (1, 2)
    versions = list(
        Submission.objects.filter(entry=entry, problem=problem)
        .order_by("version")
        .values_list("version", flat=True)
    )
    assert versions == [1, 2]


# --- kryterium 3 -------------------------------------------------------------------------


def test_upload_after_deadline_and_grace_is_rejected(client, scenario):
    stage, _, entry = scenario
    stage.grace_seconds = 120
    stage.save(update_fields=["grace_seconds"])
    client.force_authenticate(entry.participant.user)

    with freeze_time(stage.deadline_at + timedelta(seconds=121)):
        response = post_file(client, stage, 1, pdf_upload())

    assert response.status_code == 403
    assert response.data["code"] == "DEADLINE_PASSED"
    assert not Submission.objects.exists()


def test_upload_exactly_at_submission_deadline_is_rejected(client, scenario):
    """Okno uploadu jest półotwarte: chwila równa ``submission_deadline`` jest już po terminie."""
    stage, _, entry = scenario
    stage.grace_seconds = 120
    stage.save(update_fields=["grace_seconds"])
    client.force_authenticate(entry.participant.user)

    with freeze_time(stage.submission_deadline):
        response = post_file(client, stage, 1, pdf_upload())

    assert response.status_code == 403
    assert response.data["code"] == "DEADLINE_PASSED"
    assert not Submission.objects.exists()


def test_upload_inside_grace_window_is_accepted(client, scenario):
    stage, _, entry = scenario
    stage.grace_seconds = 120
    stage.save(update_fields=["grace_seconds"])
    client.force_authenticate(entry.participant.user)

    with freeze_time(stage.deadline_at + timedelta(seconds=119)):
        response = post_file(client, stage, 1, pdf_upload())

    assert response.status_code == 201, response.data
    assert Submission.objects.get(pk=response.data["id"]).is_late is True


# --- kryterium 4 -------------------------------------------------------------------------


def test_upload_before_opens_at_is_rejected(client, scenario):
    stage, _, entry = scenario
    client.force_authenticate(entry.participant.user)

    with freeze_time(stage.opens_at - timedelta(seconds=1)):
        response = post_file(client, stage, 1, pdf_upload())

    assert response.status_code == 403
    assert response.data["code"] == "STAGE_NOT_OPEN"
    assert not Submission.objects.exists()


# --- kryterium 5 -------------------------------------------------------------------------


def test_pdf_extension_with_zip_content_is_rejected(client, scenario):
    stage, _, entry = scenario
    client.force_authenticate(entry.participant.user)

    response = post_file(client, stage, 1, upload("rozwiazanie.pdf", ZIP_BYTES, "application/pdf"))

    assert response.status_code == 400
    assert response.data["code"] == "INVALID_FILE_TYPE"
    assert not Submission.objects.exists()


def test_file_over_problem_limit_is_rejected(client, scenario):
    stage, problem, entry = scenario
    client.force_authenticate(entry.participant.user)
    oversized = b"%PDF-1.7\n" + b"0" * (problem.max_file_mb * 1024 * 1024)

    response = post_file(client, stage, 1, upload("duze.pdf", oversized, "application/pdf"))

    assert response.status_code == 400
    assert response.data["code"] == "FILE_TOO_LARGE"
    assert not Submission.objects.exists()


# --- kryterium 6 -------------------------------------------------------------------------


def test_valid_notebook_is_accepted_when_allowed(client, scenario):
    stage, problem, entry = scenario
    problem.allowed_formats = ["pdf", "ipynb"]
    problem.save(update_fields=["allowed_formats"])
    client.force_authenticate(entry.participant.user)

    response = post_file(client, stage, 1, upload("praca.ipynb", notebook_bytes(), "application/json"))

    assert response.status_code == 201, response.data
    assert response.data["file"]["original_name"] == "praca.ipynb"


def test_notebook_with_huge_outputs_is_rejected(client, scenario):
    stage, problem, entry = scenario
    problem.allowed_formats = ["ipynb"]
    problem.max_file_mb = 20
    problem.save(update_fields=["allowed_formats", "max_file_mb"])
    client.force_authenticate(entry.participant.user)
    fat_notebook = notebook_bytes("x" * (2 * 1024 * 1024 + 1024))

    response = post_file(client, stage, 1, upload("praca.ipynb", fat_notebook, "application/json"))

    assert response.status_code == 400
    assert response.data["code"] == "INVALID_FILE_TYPE"


def test_notebook_rejected_when_format_not_allowed(client, scenario):
    stage, problem, entry = scenario
    assert problem.allowed_formats == ["pdf"]
    client.force_authenticate(entry.participant.user)

    response = post_file(client, stage, 1, upload("praca.ipynb", notebook_bytes(), "application/json"))

    assert response.status_code == 400
    assert response.data["code"] == "FORMAT_NOT_ALLOWED"


# --- kryterium 7 -------------------------------------------------------------------------


def test_participant_without_entry_gets_not_registered(client, scenario):
    stage, _, _ = scenario
    outsider = StageEntryFactory(stage=StageFactory()).participant
    client.force_authenticate(outsider.user)

    response = post_file(client, stage, 1, pdf_upload())

    assert response.status_code == 403
    assert response.data["code"] == "NOT_REGISTERED"


def test_foreign_entry_id_in_body_is_ignored(client, scenario):
    stage, problem, entry = scenario
    other_entry = StageEntryFactory(stage=stage)
    client.force_authenticate(entry.participant.user)

    response = client.post(
        upload_url(stage, 1),
        {"file": pdf_upload(), "entry": other_entry.pk, "entry_id": other_entry.pk},
        format="multipart",
    )

    assert response.status_code == 201, response.data
    # Wpis pochodzi wyłącznie z request.user – pole z body nie ma żadnego wpływu.
    assert Submission.objects.get(pk=response.data["id"]).entry_id == entry.pk
    assert not Submission.objects.filter(entry=other_entry).exists()
