"""Kryterium 10: klucz obiektu nigdy nie pochodzi od nazwy pliku podanej przez użytkownika."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.submissions.models import SubmissionFile
from apps.submissions.storage import build_object_key, get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, upload

EVIL_NAME = "../../evil.pdf"


def test_build_object_key_uses_only_technical_identifiers():
    key = build_object_key(
        edition_id=7,
        stage_id=11,
        participant_code="OLM-AB2345",
        submission_uuid="2f4a1b6c-0000-4000-8000-000000000001",
        sha256="b" * 64,
        ext="pdf",
    )

    assert key == f"7/11/OLM-AB2345/2f4a1b6c-0000-4000-8000-000000000001/{'b' * 64}.pdf"


@pytest.mark.parametrize("participant_code", ["../../evil", "a/b", "..", "OLM-../x"])
def test_object_key_segments_cannot_escape_the_prefix(participant_code):
    key = build_object_key(
        edition_id=1,
        stage_id=1,
        participant_code=participant_code,
        submission_uuid="0" * 32,
        sha256="c" * 64,
        ext="pdf",
    )

    assert ".." not in key
    assert key.count("/") == 4


@pytest.mark.django_db
def test_uploaded_file_key_ignores_original_filename():
    stage = StageFactory()
    ProblemFactory(stage=stage, number=1, allowed_formats=["pdf"])
    entry = StageEntryFactory(stage=stage)
    client = APIClient()
    client.force_authenticate(entry.participant.user)

    response = client.post(
        reverse("submissions:submission-create", kwargs={"stage_id": stage.pk, "number": 1}),
        {"file": upload(EVIL_NAME, PDF_BYTES, "application/pdf")},
        format="multipart",
    )

    assert response.status_code == 201, response.data
    submission_file = SubmissionFile.objects.get(submission_id=response.data["id"])
    assert "evil" not in submission_file.object_key
    assert ".." not in submission_file.object_key
    assert submission_file.object_key.endswith(f"{submission_file.sha256}.pdf")
    # Oryginalna nazwa zostaje wyłącznie jako metadana (Django obcina ścieżkę w nazwie uploadu).
    assert submission_file.original_name.endswith("evil.pdf")
    assert get_submission_storage().exists(submission_file.object_key)
