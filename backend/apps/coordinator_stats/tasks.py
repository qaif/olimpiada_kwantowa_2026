"""Retencja odbić przypisanych komunikatom (STATS-02 § 4) – raz na dobę, bez ponowień."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.coordinator_stats.tasks.purge_broadcast_bounces")
def purge_broadcast_bounces() -> int:
    """Kasuje adresy odbić starsze niż retencja MAIL-02; liczniki komunikatów zostają."""
    from .broadcasts import purge_expired

    deleted = purge_expired()
    if deleted:
        logger.info("Retencja odbić komunikatów: skasowano %s wierszy.", deleted)
    return deleted
