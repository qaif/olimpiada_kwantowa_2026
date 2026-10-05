"""Zadania okresowe MAIL-02: skrzynka odbić relaya (co 5 min) i retencja stanów adresów (raz na dobę).

Oba bez ponowień: następny przebieg i tak przyjdzie, a plik, którego nie dało się przetworzyć, leży
w ``cur/`` (``bounces.process_maildir``). Kolejka domyślna – czytanie kilku plików nie potrzebuje
kolejki ``mail``, a nie może jej blokować.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(name="apps.email_delivery.tasks.process_bounce_mailbox")
def process_bounce_mailbox() -> dict:
    """Czyta ``MAIL_BOUNCE_MAILDIR/new``: zawiadomienia o niedoręczeniu → ``DeliveryStatus``."""
    from .bounces import process_maildir
    from .models import Source
    from .services import record_bounce, tracking_enabled

    if not tracking_enabled():
        return {}
    stats = process_maildir(
        settings.MAIL_BOUNCE_MAILDIR, record=lambda bounce: record_bounce(bounce, source=Source.DSN)
    )
    if stats.get("files"):
        logger.info("Skrzynka odbić: %s", stats)
    return stats


@shared_task(name="apps.email_delivery.tasks.purge_delivery_statuses")
def purge_delivery_statuses() -> int:
    """Kasuje stany adresów bez zdarzenia od roku (``services.RETENTION_DAYS``)."""
    from .services import purge_expired

    deleted = purge_expired()
    if deleted:
        logger.info("Retencja stanów doręczalności: skasowano %s wierszy.", deleted)
    return deleted
