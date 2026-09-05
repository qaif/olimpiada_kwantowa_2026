"""Zadanie skanu: werdykty CLEAN/INFECTED, retry przy niedostępnym ClamAV, parsowanie protokołu."""

import io

import pytest
from celery.exceptions import Retry

from apps.submissions.antivirus import (
    VERDICT_CLEAN,
    VERDICT_INFECTED,
    ClamAVError,
    ClamAVUnavailable,
    parse_response,
)
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tasks import scan_submission_file
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFileFactory


def store(submission_file):
    get_submission_storage().put(submission_file.object_key, io.BytesIO(PDF_BYTES), "application/pdf")
    return submission_file


# --- protokół clamd ----------------------------------------------------------------------


def test_parse_response_ok():
    assert parse_response("stream: OK") == (VERDICT_CLEAN, "")


def test_parse_response_found_extracts_signature():
    assert parse_response("stream: Win.Test.EICAR_HDB-1 FOUND") == (
        VERDICT_INFECTED,
        "Win.Test.EICAR_HDB-1",
    )


def test_parse_response_error_raises():
    with pytest.raises(ClamAVError):
        parse_response("INSTREAM size limit exceeded. ERROR")


def test_parse_response_empty_is_unavailable():
    with pytest.raises(ClamAVUnavailable):
        parse_response("")


# --- zadanie Celery ----------------------------------------------------------------------


@pytest.mark.django_db
def test_scan_marks_file_clean_and_keeps_submission_submitted(clamd):
    submission_file = store(SubmissionFileFactory())

    assert scan_submission_file.apply(args=[submission_file.pk]).get() == VERDICT_CLEAN

    submission_file.refresh_from_db()
    assert submission_file.av_status == AvStatus.CLEAN
    assert submission_file.scanned_at is not None
    assert submission_file.submission.status == SubmissionStatus.SUBMITTED


@pytest.mark.django_db
def test_infected_file_rejects_the_submission(clamd):
    clamd.verdict = (VERDICT_INFECTED, "Eicar-Test-Signature")
    submission_file = store(SubmissionFileFactory())

    scan_submission_file.apply(args=[submission_file.pk]).get()

    submission_file.refresh_from_db()
    assert submission_file.av_status == AvStatus.INFECTED
    assert submission_file.av_signature == "Eicar-Test-Signature"
    assert submission_file.submission.status == SubmissionStatus.REJECTED_INFECTED


@pytest.mark.django_db
def test_unavailable_clamav_triggers_retry(clamd):
    clamd.error = ClamAVUnavailable("clamd padł")
    submission_file = store(SubmissionFileFactory())

    # W trybie eager Celery sygnalizuje ponowienie wyjątkiem Retry (albo propaguje pierwotny błąd).
    with pytest.raises((Retry, ClamAVUnavailable)):
        scan_submission_file.apply(args=[submission_file.pk], throw=True).get()

    submission_file.refresh_from_db()
    # Bez werdyktu plik zostaje PENDING – nigdy nie zakładamy, że jest czysty.
    assert submission_file.av_status == AvStatus.PENDING


@pytest.mark.django_db
def test_scan_of_missing_file_is_a_no_op(clamd):
    assert scan_submission_file.apply(args=[10**9]).get() == "MISSING"


@pytest.mark.django_db
def test_already_scanned_file_is_not_rescanned(clamd):
    submission_file = store(SubmissionFileFactory(av_status=AvStatus.CLEAN))

    assert scan_submission_file.apply(args=[submission_file.pk]).get() == AvStatus.CLEAN
    assert clamd.scanned == []
