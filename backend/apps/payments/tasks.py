"""Skan antywirusowy dowodu wpłaty przelewem (kolejka ``scan``, ten sam klient clamd, co prace).

Kształt ponowień skopiowany z ``apps.student_status.tasks.scan_certificate_file``: niedostępny ClamAV
– ponowienie z rosnącym odstępem; brak obiektu albo plik ponad limit strumienia – błąd trwały.
Dowód otwiera koordynator w przeglądarce panelu, więc do czasu werdyktu „czysty” nie da się go pobrać.
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

from .models import Payment, ScanStatus
from .services import apply_proof_error, apply_proof_verdict

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=MAX_SCAN_RETRIES)
def scan_payment_proof(self, payment_id: int) -> str:
    payment = Payment.objects.filter(pk=payment_id).first()
    if payment is None or payment.proof_scan_status != ScanStatus.PENDING or not payment.proof_key:
        return getattr(payment, "proof_scan_status", "MISSING") or "MISSING"
    try:
        stream = _open_object(get_submission_storage(), payment.proof_key)
        try:
            verdict, signature = scan_stream(stream, timeout=SCAN_TIMEOUT_SECONDS, size=payment.proof_size)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
    except (MissingStorageObject, ClamAVStreamTooLarge) as exc:
        logger.error("Skan dowodu wpłaty %s zamknięty błędem trwałym: %s", payment_id, exc)
        apply_proof_error(payment_id)
        return ScanStatus.ERROR
    except ClamAVUnavailable as exc:
        countdown = min(RETRY_BASE_SECONDS * (2**self.request.retries), RETRY_MAX_SECONDS)
        logger.warning("ClamAV niedostępny przy skanie dowodu wpłaty %s: %s", payment_id, exc)
        raise self.retry(exc=exc, countdown=countdown) from exc
    apply_proof_verdict(payment_id, verdict)
    if verdict == ScanStatus.INFECTED:
        logger.warning("Dowód wpłaty %s zainfekowany (%s) – usunięty.", payment_id, signature)
    return verdict
