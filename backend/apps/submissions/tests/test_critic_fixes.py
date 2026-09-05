"""Testy regresyjne do findings Critica z T-04.

Jeden test (albo mała grupa) na finding, w kolejności zgłoszeń:
1. presigned URL podpisywany publicznym endpointem MinIO,
2. throttling wyłączony także dla widoków z jawnym ``throttle_classes`` (kolizja z freezegun),
3. notatnik nbformat 3 nie omija limitu outputów,
4. brak obiektu w storage kończy skan błędem trwałym, a nie wiszącym SCANNING,
5. ``close_stage`` widzi wersje w SCANNING i pomija REJECTED_INFECTED,
6. plik ponad ``StreamMaxLength`` clamd nie generuje retry; ``Problem.max_file_mb`` ma górną granicę,
7. drobiazgi: ``latest_file``, sanityzacja ``original_name``.
"""

import io
import json
import time
from datetime import timedelta
from urllib.parse import urlparse

import pytest
import rest_framework.throttling
from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from rest_framework.test import APIClient

from apps.competitions.models import MAX_FILE_MB_LIMIT, Problem
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.core.api import DomainError
from apps.submissions.antivirus import (
    VERDICT_INFECTED,
    ClamAVStreamTooLarge,
    scan_stream,
)
from apps.submissions.models import AvStatus, Submission, SubmissionStatus
from apps.submissions.services import (
    close_due_stages,
    close_stage,
    sanitize_original_name,
)
from apps.submissions.storage import get_submission_storage
from apps.submissions.tasks import MissingStorageObject, _open_object, scan_submission_file
from apps.submissions.tests.factories import (
    PDF_BYTES,
    SubmissionFactory,
    SubmissionFileFactory,
    legacy_notebook_bytes,
    upload,
)
from apps.submissions.validators import _notebook_outputs_bytes, validate_upload

S3_BACKEND = "apps.submissions.storage.S3SubmissionStorage"
OBJECT_KEY = f"1/2/OLM-AB2345/2f4a1b6c-0000-4000-8000-000000000001/{'a' * 64}.pdf"


def store(submission_file):
    """Kładzie treść pliku w storage – bez tego skan trafiłby na brak obiektu."""
    get_submission_storage().put(submission_file.object_key, io.BytesIO(PDF_BYTES), "application/pdf")
    return submission_file


def past_stage(**kwargs):
    """Etap, któremu deadline minął minutę temu – gotowy dla ``close_due_stages``."""
    now = timezone.now()
    return StageFactory(opens_at=now - timedelta(days=30), deadline_at=now - timedelta(minutes=1), **kwargs)


def scenario_in(stage):
    """Wpis i zadanie w tym samym etapie (fabryki domyślnie tworzą dwa różne)."""
    return StageEntryFactory(stage=stage), ProblemFactory(stage=stage, number=1)


# --- finding 1: presigned URL pod publicznym hostem ---------------------------------------


@override_settings(
    SUBMISSION_STORAGE_BACKEND=S3_BACKEND,
    S3_ENDPOINT_URL="http://minio:9000",
    S3_PUBLIC_ENDPOINT_URL="https://s3.example.test",
    S3_ACCESS_KEY="dummy-access-key",
    S3_SECRET_KEY="dummy-secret-key",
)
def test_presigned_url_is_signed_with_the_public_endpoint():
    # generate_presigned_url liczy podpis lokalnie, więc test nie dotyka sieci.
    url = get_submission_storage().presigned_get_url(OBJECT_KEY)

    assert urlparse(url).netloc == "s3.example.test"
    assert "minio:9000" not in url
    assert "X-Amz-Signature" in url


@override_settings(
    SUBMISSION_STORAGE_BACKEND=S3_BACKEND,
    S3_ENDPOINT_URL="http://minio:9000",
    S3_PUBLIC_ENDPOINT_URL="",
    S3_ACCESS_KEY="dummy-access-key",
    S3_SECRET_KEY="dummy-secret-key",
)
def test_presigned_url_falls_back_to_the_internal_endpoint():
    storage = get_submission_storage()

    assert urlparse(storage.presigned_get_url(OBJECT_KEY)).netloc == "minio:9000"
    # Brak publicznego adresu nie może mnożyć klientów boto3 – to ma być ten sam obiekt.
    assert storage.presign_client is storage.client


# --- finding 2: throttling nie zależy od kolejności testów --------------------------------


def test_throttle_scopes_are_disabled_in_test_settings():
    rates = django_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]

    assert {"upload", "register", "login", "anon"} <= set(rates)
    assert all(rates[scope] is None for scope in ("upload", "register", "login", "anon"))


def test_throttling_module_keeps_the_real_clock():
    """conftest importuje ``rest_framework.throttling`` zanim jakikolwiek test zamrozi czas."""
    assert rest_framework.throttling.SimpleRateThrottle.timer is time.time


@pytest.mark.django_db
def test_upload_under_frozen_time_passes_the_throttle_check():
    """Widok ma jawne ``throttle_classes`` – pod freezegun nie może wysypać się na zegarze."""
    stage = StageFactory()
    entry, _ = scenario_in(stage)
    client = APIClient()
    client.force_authenticate(entry.participant.user)
    url = reverse("submissions:submission-create", kwargs={"stage_id": stage.pk, "number": 1})

    with freeze_time(stage.opens_at + timedelta(days=1)):
        response = client.post(url, {"file": upload("a.pdf", PDF_BYTES)}, format="multipart")

    assert response.status_code == 201, response.data


# --- finding 3: notatnik nbformat 3 --------------------------------------------------------


def test_legacy_notebook_with_huge_outputs_is_rejected():
    fat = legacy_notebook_bytes("x" * (2 * 1024 * 1024 + 1024))

    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.ipynb", fat, "application/json"), ["ipynb"], 20)

    assert excinfo.value.machine_code == "INVALID_FILE_TYPE"
    assert excinfo.value.status_code == 400


def test_legacy_notebook_is_rejected_even_when_small():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.ipynb", legacy_notebook_bytes(), "application/json"), ["ipynb"], 20)

    assert excinfo.value.machine_code == "INVALID_FILE_TYPE"


def test_outputs_are_counted_on_the_converted_notebook_not_on_raw_json():
    import nbformat

    text = legacy_notebook_bytes("x" * 4096).decode("utf-8")

    # Surowy dokument v3 nie ma klucza "cells" – licznik czytający go widziałby zero.
    assert _notebook_outputs_bytes(json.loads(text)) == 0
    assert _notebook_outputs_bytes(nbformat.reads(text, as_version=4)) > 4096


@pytest.mark.django_db
def test_legacy_notebook_upload_returns_400():
    stage = StageFactory()
    entry, problem = scenario_in(stage)
    problem.allowed_formats = ["ipynb"]
    problem.save(update_fields=["allowed_formats"])
    client = APIClient()
    client.force_authenticate(entry.participant.user)

    response = client.post(
        reverse("submissions:submission-create", kwargs={"stage_id": stage.pk, "number": 1}),
        {"file": upload("stary.ipynb", legacy_notebook_bytes("x" * (2 * 1024 * 1024)), "application/json")},
        format="multipart",
    )

    assert response.status_code == 400
    assert response.data["code"] == "INVALID_FILE_TYPE"


# --- finding 4: brak obiektu w storage -----------------------------------------------------


@pytest.mark.django_db
def test_scan_of_object_missing_in_storage_ends_with_permanent_error(clamd):
    stage = StageFactory()
    entry, problem = scenario_in(stage)
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.SUBMITTED)
    submission_file = SubmissionFileFactory(submission=submission)  # celowo nic nie kładziemy w storage

    assert scan_submission_file.apply(args=[submission_file.pk]).get() == AvStatus.ERROR

    submission_file.refresh_from_db()
    submission.refresh_from_db()
    assert submission_file.av_status == AvStatus.ERROR
    assert submission_file.scanned_at is not None
    # Zgłoszenie nie może zostać w SCANNING – wraca do stanu sprzed skanu.
    assert submission.status == SubmissionStatus.SUBMITTED
    assert clamd.scanned == [], "bez obiektu nie ma czego wysyłać do clamd"


@pytest.mark.django_db
def test_scan_error_in_a_closed_stage_locks_the_submission(clamd):
    stage = past_stage(closed_at=timezone.now())
    entry, problem = scenario_in(stage)
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.SUBMITTED)
    submission_file = SubmissionFileFactory(submission=submission)

    scan_submission_file.apply(args=[submission_file.pk]).get()

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.LOCKED


def test_open_object_maps_nosuchkey_to_missing_storage_object():
    from botocore.exceptions import ClientError

    class _Storage:
        def open(self, key):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    with pytest.raises(MissingStorageObject):
        _open_object(_Storage(), "klucz")


def test_open_object_reraises_other_s3_errors():
    from botocore.exceptions import ClientError

    class _Storage:
        def open(self, key):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    # Brak uprawnień to problem konfiguracji, nie brakujący plik – tego nie wolno uznać za trwałe.
    with pytest.raises(ClientError):
        _open_object(_Storage(), "klucz")


# --- finding 5: close_stage a skanowanie i wersje zainfekowane -----------------------------


@pytest.mark.django_db
def test_scan_finishing_after_stage_close_locks_the_submission(clamd):
    stage = past_stage()
    entry, problem = scenario_in(stage)
    submission = SubmissionFactory(entry=entry, problem=problem, version=1)
    submission_file = store(SubmissionFileFactory(submission=submission))
    # W chwili zamykania etapu skan jeszcze trwa.
    Submission.objects.filter(pk=submission.pk).update(status=SubmissionStatus.SCANNING)

    assert close_due_stages() == [stage.pk]
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.SCANNING, "skanowanej wersji close_stage nie rusza"

    scan_submission_file.apply(args=[submission_file.pk]).get()

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.LOCKED


@pytest.mark.django_db
def test_close_stage_locks_the_previous_version_when_latest_is_infected():
    stage = StageFactory()
    entry, problem = scenario_in(stage)
    clean = SubmissionFactory(entry=entry, problem=problem, version=1, status=SubmissionStatus.SUBMITTED)
    infected = SubmissionFactory(
        entry=entry, problem=problem, version=2, status=SubmissionStatus.REJECTED_INFECTED
    )

    assert close_stage(stage) == 1

    clean.refresh_from_db()
    infected.refresh_from_db()
    assert clean.status == SubmissionStatus.LOCKED
    assert infected.status == SubmissionStatus.REJECTED_INFECTED
    # Idempotencja: drugi przebieg nie schodzi głębiej w historię.
    assert close_stage(stage) == 0


@pytest.mark.django_db
def test_close_stage_does_not_walk_past_an_already_locked_version():
    stage = StageFactory()
    entry, problem = scenario_in(stage)
    older = SubmissionFactory(entry=entry, problem=problem, version=1, status=SubmissionStatus.SUBMITTED)
    SubmissionFactory(entry=entry, problem=problem, version=2, status=SubmissionStatus.LOCKED)

    assert close_stage(stage) == 0

    older.refresh_from_db()
    assert older.status == SubmissionStatus.SUBMITTED


@pytest.mark.django_db
def test_infection_found_after_close_locks_the_previous_clean_version(clamd):
    clamd.verdict = (VERDICT_INFECTED, "Eicar-Test-Signature")
    stage = past_stage()
    entry, problem = scenario_in(stage)
    clean = SubmissionFactory(entry=entry, problem=problem, version=1, status=SubmissionStatus.SUBMITTED)
    scanning = SubmissionFactory(entry=entry, problem=problem, version=2)
    scanning_file = store(SubmissionFileFactory(submission=scanning))
    Submission.objects.filter(pk=scanning.pk).update(status=SubmissionStatus.SCANNING)

    close_due_stages()
    scan_submission_file.apply(args=[scanning_file.pk]).get()

    clean.refresh_from_db()
    scanning.refresh_from_db()
    assert scanning.status == SubmissionStatus.REJECTED_INFECTED
    assert clean.status == SubmissionStatus.LOCKED


# --- finding 6: limit strumienia clamd i górna granica max_file_mb -------------------------


def test_scan_stream_rejects_a_declared_size_over_the_limit():
    # Port celowo nieistniejący: limit musi zadziałać, zanim padnie próba połączenia.
    with pytest.raises(ClamAVStreamTooLarge):
        scan_stream(io.BytesIO(b"x"), host="127.0.0.1", port=1, size=200 * 1024 * 1024, max_bytes=1024)


@override_settings(CLAMAV_STREAM_MAX_BYTES=1024)
def test_scan_stream_takes_the_limit_from_settings():
    with pytest.raises(ClamAVStreamTooLarge):
        scan_stream(io.BytesIO(b"x"), host="127.0.0.1", port=1, size=2048)


@pytest.mark.django_db
def test_oversized_file_ends_scan_with_error_instead_of_retrying(clamd):
    clamd.error = ClamAVStreamTooLarge("Plik ponad StreamMaxLength.")
    stage = StageFactory()
    entry, problem = scenario_in(stage)
    submission = SubmissionFactory(entry=entry, problem=problem)
    submission_file = store(SubmissionFileFactory(submission=submission))

    assert scan_submission_file.apply(args=[submission_file.pk]).get() == AvStatus.ERROR

    submission_file.refresh_from_db()
    submission.refresh_from_db()
    assert submission_file.av_status == AvStatus.ERROR
    assert submission.status == SubmissionStatus.SUBMITTED


@pytest.mark.django_db
def test_problem_max_file_mb_over_the_limit_is_invalid():
    problem = ProblemFactory.build(stage=StageFactory(), max_file_mb=MAX_FILE_MB_LIMIT + 1)

    with pytest.raises(ValidationError) as excinfo:
        problem.clean()

    assert "max_file_mb" in excinfo.value.message_dict


@pytest.mark.django_db
def test_problem_max_file_mb_at_the_limit_is_valid():
    ProblemFactory.build(stage=StageFactory(), max_file_mb=MAX_FILE_MB_LIMIT).clean()


@pytest.mark.django_db
def test_database_constraint_blocks_max_file_mb_over_the_limit():
    problem = ProblemFactory()

    with pytest.raises(IntegrityError), transaction.atomic():
        Problem.objects.filter(pk=problem.pk).update(max_file_mb=MAX_FILE_MB_LIMIT + 1)


# --- finding 7: latest_file i sanityzacja original_name -----------------------------------


@pytest.mark.django_db
def test_latest_file_returns_the_newest_file():
    submission = SubmissionFactory()
    SubmissionFileFactory(submission=submission)
    newest = SubmissionFileFactory(submission=submission)

    assert submission.latest_file == newest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../evil.pdf", "evil.pdf"),
        ("C:\\Users\\x\\praca.pdf", "praca.pdf"),
        ("na\x00zw\x7fa.pdf", "nazwa.pdf"),
        ("linia\r\ndruga.pdf", "liniadruga.pdf"),
        ("  spacje.pdf  ", "spacje.pdf"),
        ("", "plik"),
        (None, "plik"),
        ("\r\n\t", "plik"),
    ],
)
def test_sanitize_original_name(raw, expected):
    assert sanitize_original_name(raw) == expected


def test_sanitize_original_name_truncates_to_255_chars():
    assert sanitize_original_name("a" * 300 + ".pdf") == "a" * 255


@pytest.mark.django_db
def test_uploaded_original_name_is_stored_without_path():
    stage = StageFactory()
    entry, _ = scenario_in(stage)
    client = APIClient()
    client.force_authenticate(entry.participant.user)

    response = client.post(
        reverse("submissions:submission-create", kwargs={"stage_id": stage.pk, "number": 1}),
        {"file": upload("podkatalog/rozwiazanie.pdf", PDF_BYTES, "application/pdf")},
        format="multipart",
    )

    assert response.status_code == 201, response.data
    assert response.data["file"]["original_name"] == "rozwiazanie.pdf"
