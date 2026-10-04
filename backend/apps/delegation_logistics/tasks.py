"""Zadania Celery logistyki finału: skan zdjęcia do identyfikatora i dobowa retencja.

``scan_badge_photo`` idzie na kolejkę ``scan`` (``CELERY_TASK_ROUTES``) – ten sam klient clamd i ten
sam kształt ponowień, co skan zaświadczeń o statusie ucznia (``apps.student_status.tasks``):
niedostępny ClamAV to ponowienie z rosnącym odstępem, brak obiektu w storage – błąd trwały.
Zadanie dostaje **klucz** zdjęcia obok identyfikatora wiersza: opiekun, który w międzyczasie wgrał
nowe zdjęcie, nie może dostać werdyktu starego pliku przypisanego do nowego.
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

from .models import DelegationMember, ScanStatus

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=MAX_SCAN_RETRIES)
def scan_badge_photo(self, member_id: int, key: str) -> str:
    from .services import apply_photo_scan

    member = DelegationMember.objects.filter(pk=member_id, photo_key=key).first()
    if member is None or member.photo_scan != ScanStatus.PENDING:
        return "SKIPPED"
    try:
        stream = _open_object(get_submission_storage(), key)
        try:
            verdict, signature = scan_stream(stream, timeout=SCAN_TIMEOUT_SECONDS, size=member.photo_size)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
    except (MissingStorageObject, ClamAVStreamTooLarge) as exc:
        logger.error("Skan zdjęcia identyfikatora %s zamknięty błędem trwałym: %s", member_id, exc)
        apply_photo_scan(member_id, key, ScanStatus.ERROR)
        return ScanStatus.ERROR
    except ClamAVUnavailable as exc:
        countdown = min(RETRY_BASE_SECONDS * (2**self.request.retries), RETRY_MAX_SECONDS)
        logger.warning("ClamAV niedostępny przy skanie zdjęcia identyfikatora %s: %s", member_id, exc)
        raise self.retry(exc=exc, countdown=countdown) from exc
    apply_photo_scan(member_id, key, verdict)
    if verdict == ScanStatus.INFECTED:
        logger.warning("Zdjęcie identyfikatora %s zainfekowane (%s) – usunięte.", member_id, signature)
    return verdict


@shared_task(name="apps.delegation_logistics.tasks.purge_expired")
def purge_expired_task() -> int:
    """Beat raz na dobę: dane członków delegacji po terminie retencji finału (``privacy.purge_expired``)."""
    from .privacy import purge_expired

    return purge_expired()
