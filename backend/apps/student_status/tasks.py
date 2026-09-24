"""Zadania Celery: skan antywirusowy skanu zaświadczenia i dobowa retencja plików.

``scan_certificate_file`` idzie na kolejkę ``scan`` (``CELERY_TASK_ROUTES``) – tę samą, co skan
rozwiązań, bo to ta sama praca na tym samym kliencie clamd. Kształt ponowień jest skopiowany ze
``apps.submissions.tasks.scan_submission_file``: niedostępny ClamAV to ponowienie z rosnącym
odstępem, brak obiektu w storage to błąd trwały bez ponowień.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.submissions.antivirus import ClamAVStreamTooLarge, ClamAVUnavailable, scan_stream
from apps.submissions.storage import get_submission_storage
from apps.submissions.tasks import (
    MAX_SCAN_RETRIES,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    SCAN_TIMEOUT_SECONDS,
    MissingStorageObject,
    _open_object,
)

from .models import ScanStatus, StudentStatusCertificate
from .services import apply_scan_error, apply_scan_verdict, purge_expired_scans

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=MAX_SCAN_RETRIES)
def scan_certificate_file(self, certificate_id: int) -> str:
    """Skanuje plik zaświadczenia przez ClamAV i zapisuje werdykt (``services.apply_scan_verdict``)."""
    certificate = StudentStatusCertificate.objects.filter(pk=certificate_id).first()
    if certificate is None:
        logger.warning("Skan pominięty: zaświadczenie %s już nie istnieje.", certificate_id)
        return "MISSING"
    if certificate.scan_status != ScanStatus.PENDING or not certificate.object_key:
        # Plik zastąpiony nowszym zanim worker do niego doszedł – nie ma czego skanować.
        return certificate.scan_status
    try:
        stream = _open_object(get_submission_storage(), certificate.object_key)
        try:
            verdict, signature = scan_stream(
                stream, timeout=SCAN_TIMEOUT_SECONDS, size=certificate.size_bytes
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
    except (MissingStorageObject, ClamAVStreamTooLarge) as exc:
        logger.error("Skan zaświadczenia %s zamknięty błędem trwałym: %s", certificate_id, exc)
        apply_scan_error(certificate_id)
        return ScanStatus.ERROR
    except ClamAVUnavailable as exc:
        countdown = min(RETRY_BASE_SECONDS * (2**self.request.retries), RETRY_MAX_SECONDS)
        logger.warning("ClamAV niedostępny przy skanie zaświadczenia %s: %s", certificate_id, exc)
        raise self.retry(exc=exc, countdown=countdown) from exc
    apply_scan_verdict(certificate_id, verdict)
    if verdict == ScanStatus.INFECTED:
        # Sygnatura tylko do logu workera – uczestnik dostaje zdanie, co zrobić, a nie nazwę wirusa.
        logger.warning("Zaświadczenie %s zainfekowane (%s) – odrzucone.", certificate_id, signature)
    return verdict


@shared_task(name="apps.student_status.tasks.purge_expired_scans")
def purge_expired_scans_task() -> int:
    """Beat raz na dobę: pliki zaświadczeń edycji po terminie retencji (``services.purge_expired_scans``).

    Raz na dobę z tego samego powodu, co anonimizacja kont: termin jest liczony w miesiącach.
    """
    return purge_expired_scans()
