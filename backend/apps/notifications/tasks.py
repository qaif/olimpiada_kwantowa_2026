"""Zadania Celery powiadomień: rozesłanie ogłoszenia, przebieg ogłoszeń zaplanowanych, retencja."""

from __future__ import annotations

import logging

from celery import shared_task

from . import announcements, services

logger = logging.getLogger(__name__)


@shared_task
def deliver_announcement(announcement_id: int) -> int:
    """Rozesłanie jednego ogłoszenia tuż po publikacji (``hooks.announcement_published``)."""
    from apps.chat.models import OrganizerAnnouncement

    announcement = OrganizerAnnouncement.objects.filter(pk=announcement_id).first()
    if announcement is None:
        return 0
    return announcements.deliver(announcement)


@shared_task
def deliver_due_announcements() -> int:
    """Beat co 10 minut: ogłoszenia, które weszły w okno „widoczne od”, i zgubione rozesłania."""
    sent = announcements.deliver_due()
    if sent:
        logger.info("Rozesłano powiadomienia o ogłoszeniach: %s.", sent)
    return sent


@shared_task
def purge_expired() -> int:
    """Beat raz na dobę: retencja powiadomień (``NOTIFICATION_RETENTION_DAYS``, domyślnie 90 dni)."""
    deleted = services.purge_expired()
    if deleted:
        logger.info("Usunięto %s powiadomień po terminie retencji.", deleted)
    return deleted
