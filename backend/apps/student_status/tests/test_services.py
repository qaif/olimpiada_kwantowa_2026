"""Serwis zaświadczeń o statusie ucznia: walidacja pliku, wersje, skan, decyzje, RODO.

Przedmiotem są reguły, których nie widać po samym ekranie:

- **o formacie decyduje treść**, nie rozszerzenie ani ``Content-Type`` – HTML przemianowany na
  ``.pdf`` nie przejdzie, a PDF z rozszerzeniem ``.png`` przejdzie jako PDF,
- **nowe wgranie zastępuje** nierozpatrzone albo odrzucone, a plik poprzedniej wersji znika ze
  storage; zaakceptowanego nie da się podmienić,
- **skan antywirusowy** zainfekowany plik odrzuca i usuwa, a akceptacja przed czystym skanem
  jest odmową,
- **RODO**: usunięcie konta zabiera wiersze i pliki, retencja edycji zabiera pliki.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.tests.factories import CurrentEditionFactory, EditionFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.student_status import services
from apps.student_status.models import (
    STATE_MISSING,
    CertificateStatus,
    ScanStatus,
    StudentStatusCertificate,
)
from apps.student_status.pdf import compose_pdf, school_year
from apps.student_status.validators import MAX_FILE_MB, MEGABYTE

from .helpers import HTML_BYTES, JPEG_BYTES, PDF_BYTES, PNG_BYTES, enable, make_certificate, stored, upload

pytestmark = pytest.mark.django_db


# --- walidacja pliku ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "mime"),
    [
        ("skan.pdf", PDF_BYTES, "application/pdf"),
        ("zdjecie.JPG", JPEG_BYTES, "image/jpeg"),
        ("zdjecie.png", PNG_BYTES, "image/png"),
        # Rozszerzenie kłamie, treść nie – typ bierze się z treści.
        ("skan.png", PDF_BYTES, "application/pdf"),
    ],
)
def test_format_is_recognised_by_content(participant, edition, name, content, mime):
    certificate = services.upload_scan(participant, edition, upload(name, content, "text/html"))

    assert certificate.mime == mime
    assert certificate.status == CertificateStatus.PENDING
    assert certificate.scan_status == ScanStatus.PENDING
    assert certificate.is_current
    assert stored(certificate.object_key)


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (HTML_BYTES, "INVALID_FILE_TYPE"),
        (b"", "EMPTY_FILE"),
        (b"PK\x03\x04" + b"\x00" * 32, "INVALID_FILE_TYPE"),
    ],
)
def test_files_that_are_not_scans_are_refused(participant, edition, content, code):
    with pytest.raises(DomainError) as caught:
        services.upload_scan(participant, edition, upload("zaswiadczenie.pdf", content))

    assert caught.value.machine_code == code
    assert not StudentStatusCertificate.objects.exists()


def test_a_file_over_the_limit_is_refused(participant, edition):
    big = PDF_BYTES + b"0" * (MAX_FILE_MB * MEGABYTE)

    with pytest.raises(DomainError) as caught:
        services.upload_scan(participant, edition, upload("duzy.pdf", big))

    assert caught.value.machine_code == "FILE_TOO_LARGE"


def test_the_object_key_never_contains_the_uploaded_name(participant, edition):
    certificate = services.upload_scan(participant, edition, upload("../../Śniadecka_LO.pdf", PDF_BYTES))

    assert certificate.object_key.startswith(f"student-status/{edition.competition_id}/{edition.pk}/")
    assert "Śniadecka" not in certificate.object_key
    assert ".." not in certificate.object_key
    assert certificate.object_key.endswith(".pdf")


def test_edition_of_another_competition_is_refused(participant, other_competition):
    foreign = CurrentEditionFactory(competition=other_competition)

    with pytest.raises(DomainError) as caught:
        services.upload_scan(participant, foreign, upload("skan.pdf", PDF_BYTES))

    assert caught.value.machine_code == "EDITION_MISMATCH"


def test_upload_is_audited_without_the_file_name(participant, edition):
    services.upload_scan(participant, edition, upload("Łucja_Śniadecka.pdf", PDF_BYTES))

    entry = AuditLog.objects.get(action="student_status.uploaded")
    assert "Śniadecka" not in str(entry.diff)
    assert entry.diff["version"] == 1


# --- wersje -------------------------------------------------------------------------------------


def test_reupload_replaces_a_pending_version_and_removes_its_file(
    participant, edition, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        first = services.upload_scan(participant, edition, upload("a.pdf", PDF_BYTES))
    first_key = first.object_key
    with django_capture_on_commit_callbacks(execute=True):
        second = services.upload_scan(participant, edition, upload("b.jpg", JPEG_BYTES))

    first.refresh_from_db()
    assert second.version == 2 and second.is_current
    assert first.is_current is False
    assert first.status == CertificateStatus.SUPERSEDED
    assert first.object_key == "" and first.file_removed_at is not None
    assert not stored(first_key)
    assert stored(second.object_key)


def test_reupload_after_rejection_keeps_the_rejection_in_history(participant, edition):
    rejected = make_certificate(participant, edition, status=CertificateStatus.REJECTED)
    rejected.rejection_reason = "brak pieczątki"
    rejected.save()

    services.upload_scan(participant, edition, upload("b.pdf", PDF_BYTES))

    rejected.refresh_from_db()
    assert rejected.status == CertificateStatus.REJECTED
    assert rejected.rejection_reason == "brak pieczątki"
    assert [row.version for row in services.history(participant, edition)] == [2, 1]


def test_accepted_certificate_cannot_be_replaced(participant, edition):
    make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)

    with pytest.raises(DomainError) as caught:
        services.upload_scan(participant, edition, upload("b.pdf", PDF_BYTES))

    assert caught.value.machine_code == "STUDENT_STATUS_ALREADY_ACCEPTED"
    assert StudentStatusCertificate.objects.count() == 1


def test_status_is_per_edition(participant, edition, competition):
    """Zeszłoroczne zaakceptowane zaświadczenie nie jest statusem w tym roku."""
    last_year = EditionFactory(competition=competition, year_label="2025/2026")
    make_certificate(participant, last_year, status=CertificateStatus.ACCEPTED)

    assert services.status_summary(participant, edition)["state"] == STATE_MISSING
    assert services.status_summary(participant, last_year)["accepted"] is True
    assert services.accepted_participant_ids(edition) == set()


# --- skan antywirusowy ------------------------------------------------------------------------------


def test_clean_scan_keeps_the_certificate_pending(participant, edition):
    certificate = make_certificate(participant, edition, scan=ScanStatus.PENDING)

    services.apply_scan_verdict(certificate.pk, ScanStatus.CLEAN)

    certificate.refresh_from_db()
    assert certificate.scan_status == ScanStatus.CLEAN
    assert certificate.status == CertificateStatus.PENDING
    assert mail.outbox == []


def test_infected_file_is_rejected_removed_and_reported(
    participant, edition, django_capture_on_commit_callbacks
):
    certificate = make_certificate(participant, edition, scan=ScanStatus.PENDING)
    key = certificate.object_key

    with django_capture_on_commit_callbacks(execute=True):
        services.apply_scan_verdict(certificate.pk, ScanStatus.INFECTED)

    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.REJECTED
    assert certificate.rejection_reason == services.INFECTED_REASON
    assert certificate.object_key == ""
    assert not stored(key)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [participant.user.email]
    assert "odrzucone" in mail.outbox[0].subject


def test_missing_object_closes_the_scan_with_a_request_to_reupload(participant, edition):
    certificate = make_certificate(participant, edition, scan=ScanStatus.PENDING)

    services.apply_scan_error(certificate.pk)

    certificate.refresh_from_db()
    assert certificate.scan_status == ScanStatus.ERROR
    assert certificate.status == CertificateStatus.REJECTED
    assert certificate.rejection_reason == services.SCAN_ERROR_REASON


def test_scan_task_runs_clamav_and_stores_the_verdict(participant, edition, monkeypatch):
    from apps.student_status import tasks

    certificate = make_certificate(participant, edition, scan=ScanStatus.PENDING)
    seen = {}

    def fake_scan(stream, **kwargs):
        seen["bytes"] = stream.read()
        return "CLEAN", ""

    monkeypatch.setattr(tasks, "scan_stream", fake_scan)
    assert tasks.scan_certificate_file.apply(args=[certificate.pk]).get() == "CLEAN"

    certificate.refresh_from_db()
    assert certificate.scan_status == ScanStatus.CLEAN
    assert seen["bytes"] == PDF_BYTES


# --- decyzje koordynatora --------------------------------------------------------------------------


def test_accept_requires_a_clean_scan(participant, edition):
    certificate = make_certificate(participant, edition, scan=ScanStatus.PENDING)

    with pytest.raises(DomainError) as caught:
        services.accept(certificate, actor=CoordinatorFactory())

    assert caught.value.machine_code == "STUDENT_STATUS_NOT_SCANNED"


def test_accept_informs_the_participant_and_audits(participant, edition, django_capture_on_commit_callbacks):
    coordinator = CoordinatorFactory()
    certificate = make_certificate(participant, edition)

    with django_capture_on_commit_callbacks(execute=True):
        services.accept(certificate, actor=coordinator)

    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.ACCEPTED
    assert certificate.decided_by == coordinator
    assert services.accepted_participant_ids(edition) == {participant.pk}
    assert AuditLog.objects.filter(action="student_status.accepted", actor=coordinator).exists()
    assert len(mail.outbox) == 1
    assert "zaakceptowane" in mail.outbox[0].subject


def test_reject_needs_a_reason_and_keeps_it_out_of_the_audit(
    participant, edition, django_capture_on_commit_callbacks
):
    coordinator = CoordinatorFactory()
    certificate = make_certificate(participant, edition)

    with pytest.raises(DomainError):
        services.reject(certificate, "   ", actor=coordinator)
    with django_capture_on_commit_callbacks(execute=True):
        services.reject(certificate, "Nazwisko na pieczątce się nie zgadza", actor=coordinator)

    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.REJECTED
    entry = AuditLog.objects.get(action="student_status.rejected")
    assert "pieczątce" not in str(entry.diff)
    assert "Nazwisko na pieczątce się nie zgadza" in mail.outbox[0].body


def test_accepted_can_be_rejected_and_then_reuploaded(participant, edition):
    certificate = make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)

    services.reject(certificate, "pomyłka – to zaświadczenie z innej szkoły", actor=CoordinatorFactory())
    replacement = services.upload_scan(participant, edition, upload("nowe.pdf", PDF_BYTES))

    assert replacement.version == 2


def test_decision_on_a_superseded_version_is_refused(participant, edition):
    old = make_certificate(participant, edition)
    services.upload_scan(participant, edition, upload("nowe.pdf", PDF_BYTES))

    with pytest.raises(DomainError) as caught:
        services.accept(old, actor=CoordinatorFactory())

    assert caught.value.machine_code == "STUDENT_STATUS_NOT_CURRENT"


# --- lista koordynatora ---------------------------------------------------------------------------


def test_coordinator_rows_cover_the_edition_and_count_every_state(participant, edition, competition):
    stage = StageFactory(competition=competition, edition=edition)
    entered = ParticipantFactory(competition=competition)
    from apps.competitions.tests.factories import StageEntryFactory

    StageEntryFactory(participant=entered, stage=stage)
    ParticipantFactory(competition=competition)  # konto bez udziału w edycji – nie jest na liście
    make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)

    rows, counts = services.coordinator_rows(edition)

    assert {row.participant.pk for row in rows} == {participant.pk, entered.pk}
    assert counts == {"oczekujace": 0, "zaakceptowane": 1, "odrzucone": 0, "brak": 1}
    missing, _ = services.coordinator_rows(edition, state="brak")
    assert [row.participant.pk for row in missing] == [entered.pk]


# --- RODO -----------------------------------------------------------------------------------------


def test_erase_for_user_removes_rows_and_files(participant, edition, django_capture_on_commit_callbacks):
    certificate = make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)
    key = certificate.object_key

    with django_capture_on_commit_callbacks(execute=True):
        services.erase_for_user(participant.user)

    assert not StudentStatusCertificate.objects.exists()
    assert not stored(key)


def test_retention_removes_files_of_expired_editions_only(
    participant, edition, competition, django_capture_on_commit_callbacks
):
    old_edition = EditionFactory(competition=competition, data_retention_months=24)
    past = timezone.now() - timedelta(days=3 * 365)
    StageFactory(
        competition=competition,
        edition=old_edition,
        opens_at=past - timedelta(days=10),
        deadline_at=past,
        review_deadline_at=past + timedelta(days=5),
        appeal_window_opens_at=past + timedelta(days=6),
        appeal_window_closes_at=past + timedelta(days=7),
    )
    expired = make_certificate(participant, old_edition, status=CertificateStatus.ACCEPTED)
    current = make_certificate(participant, edition)
    expired_key = expired.object_key

    with django_capture_on_commit_callbacks(execute=True):
        assert services.purge_expired_scans() == 1

    expired.refresh_from_db()
    current.refresh_from_db()
    assert expired.object_key == "" and not stored(expired_key)
    # Decyzja zostaje, plik nie.
    assert expired.status == CertificateStatus.ACCEPTED
    assert current.has_file and stored(current.object_key)


def test_flag_gates_only_the_zip_scope_parameter(competition):
    assert services.wants_verified_only(None, competition) is False
    assert services.wants_verified_only("all", competition) is False
    with pytest.raises(DomainError):
        services.wants_verified_only("verified", competition)
    enable(competition)
    assert services.wants_verified_only("verified", competition) is True
    with pytest.raises(DomainError):
        services.wants_verified_only("wszystko-i-jeszcze-wiecej", competition)


# --- wzór PDF -------------------------------------------------------------------------------------


def test_template_pdf_is_personalised_and_escapes_markup(participant, edition):
    from pypdf import PdfReader

    participant.school = "Liceum <b>&</b> Technikum"
    participant.save()

    data = compose_pdf(participant, edition, panel_url="https://example.test/me/status-ucznia/")

    assert data.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(data))
    assert len(reader.pages) == 1
    # Łamanie wierszy zależy od długości nazwisk – porównujemy tekst ze spłaszczonymi odstępami.
    text = " ".join(reader.pages[0].extract_text().split())
    assert "Zaświadczenie o statusie ucznia" in text
    assert "Łucja Śniadecka" in text
    assert "2026/2027" in text
    assert participant.birth_date.strftime("%d.%m.%Y") in text
    assert "Liceum <b>&</b> Technikum" in text
    assert "pieczątka szkoły" in text


def test_template_pdf_leaves_a_blank_for_an_unknown_birth_date(participant, edition):
    from pypdf import PdfReader

    participant.birth_date = None
    participant.save(update_fields=["birth_date"])

    text = PdfReader(BytesIO(compose_pdf(participant, edition))).pages[0].extract_text()

    assert "urodzony(-a) …" in text


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("I edycja 2026/2027", "2026/2027"),
        ("XV (2026/2027)", "2026/2027"),
        ("Edycja zimowa", "Edycja zimowa"),
    ],
)
def test_school_year_is_taken_from_the_edition_label(label, expected):
    class Edition:
        year_label = label

    assert school_year(Edition()) == expected


def test_the_scan_goes_to_the_scan_queue_and_retention_runs_daily(settings):
    """Skan zaświadczenia idzie tą samą kolejką, co skan prac, a retencja plików ma wpis w beat."""
    assert settings.CELERY_TASK_ROUTES["apps.student_status.tasks.scan_certificate_file"] == {"queue": "scan"}
    entry = settings.CELERY_BEAT_SCHEDULE["student-status-purge-expired-scans"]
    assert entry["task"] == "apps.student_status.tasks.purge_expired_scans"
    assert entry["schedule"] == 86400.0
