"""Zdjęcie rozwiązania (JPEG): walidacja treści, normalizacja ``.jpeg`` i droga przez upload.

Prośba organizatora: „dodaj jeszcze obsługę plików jpeg”. Uczestnik bez skanera fotografuje kartkę
telefonem, a telefon nazywa plik raz ``.jpg``, raz ``.jpeg`` – dlatego kanoniczna nazwa formatu jest
jedna i to ona trafia do dozwolonych formatów zadania, do klucza w storage i do paczki ZIP.
"""

import zipfile

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.core.api import DomainError
from apps.submissions.models import AvStatus, Submission
from apps.submissions.packaging import README_NAME, zip_entry_name
from apps.submissions.services import build_stage_zip
from apps.submissions.tests.factories import (
    JPEG_BYTES,
    PDF_BYTES,
    jpeg_upload,
    upload,
)
from apps.submissions.validators import declared_extension, validate_upload


def assert_code(excinfo, code: str) -> None:
    assert excinfo.value.machine_code == code
    assert excinfo.value.status_code == 400


# --- walidator ---------------------------------------------------------------------------------


def test_jpeg_jest_rozpoznawany_po_sygnaturze():
    ext, mime = validate_upload(upload("a.jpg", JPEG_BYTES, "text/plain"), ["jpg"], 20)

    assert (ext, mime) == ("jpg", "image/jpeg")


def test_pdf_przemianowany_na_jpg_odpada():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.jpg", PDF_BYTES, "image/jpeg"), ["jpg"], 20)

    assert_code(excinfo, "INVALID_FILE_TYPE")


def test_rozszerzenie_jpeg_sprowadza_sie_do_jpg():
    assert declared_extension("Zdjecie.JPEG") == "jpg"
    assert declared_extension("zdjecie.jpg") == "jpg"

    ext, mime = validate_upload(upload("zdjecie.JPEG", JPEG_BYTES, "image/jpeg"), ["jpg"], 20)

    # Zadanie ma w ``allowed_formats`` wyłącznie ``jpg`` – bez normalizacji ten plik by odpadł.
    assert (ext, mime) == ("jpg", "image/jpeg")


def test_zdjecie_do_zadania_bez_jpg_jest_odrzucane():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(jpeg_upload(), ["pdf"], 20)

    assert_code(excinfo, "FORMAT_NOT_ALLOWED")


def test_komunikat_o_nieznanym_rozszerzeniu_wymienia_jpg():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.exe", JPEG_BYTES, "image/jpeg"), ["jpg"], 20)

    assert ".jpg" in str(excinfo.value.detail)


# --- upload i paczka ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_upload_zdjecia_tworzy_wersje_z_typem_image_jpeg(clamd, django_capture_on_commit_callbacks):
    stage = StageFactory()
    ProblemFactory(stage=stage, number=1, allowed_formats=["jpg"], max_file_mb=5)
    entry = StageEntryFactory(stage=stage)
    client = APIClient()
    client.force_authenticate(entry.participant.user)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("submissions:submission-create", kwargs={"stage_id": stage.pk, "number": 1}),
            {"file": jpeg_upload("Zdjecie.JPEG")},
            format="multipart",
        )

    assert response.status_code == 201, response.data
    submission_file = Submission.objects.get(pk=response.data["id"]).latest_file
    assert submission_file.mime == "image/jpeg"
    # Klucz obiektu powstaje z wyliczonego rozszerzenia, nie z nazwy od uczestnika.
    assert submission_file.object_key.endswith(".jpg")


@pytest.mark.django_db
def test_paczka_zip_zachowuje_rozszerzenie_jpg(clamd, django_capture_on_commit_callbacks):
    stage = StageFactory()
    ProblemFactory(stage=stage, number=1, allowed_formats=["jpg"], max_file_mb=5)
    entry = StageEntryFactory(stage=stage)
    client = APIClient()
    client.force_authenticate(entry.participant.user)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("submissions:submission-create", kwargs={"stage_id": stage.pk, "number": 1}),
            {"file": jpeg_upload()},
            format="multipart",
        )
    submission = Submission.objects.get(pk=response.data["id"])
    submission_file = submission.latest_file
    submission_file.av_status = AvStatus.CLEAN
    submission_file.save(update_fields=["av_status"])

    package = build_stage_zip(stage)

    expected = f"{entry.participant.public_code}_zad1_v1.jpg"
    assert zip_entry_name(submission, submission_file) == expected
    with zipfile.ZipFile(package.stream) as archive:
        assert sorted(archive.namelist()) == sorted([README_NAME, expected])
