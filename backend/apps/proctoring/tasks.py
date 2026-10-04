"""Zadania Celery nadzoru: start nagrania kamery (po webhooku) i retencja nośników (beat, raz dziennie)."""

from __future__ import annotations

from celery import shared_task


@shared_task
def start_track_recording(session_pk: int, track_sid: str, room: str = "") -> bool:
    """Track Egress kamery – kolejkowane przez webhook ``track_published`` przy ``record=True``."""
    from .services import start_recording

    return start_recording(session_pk, track_sid, room) is not None


@shared_task
def purge_expired() -> int:
    """Nagrania, zdjęcia dokumentu, dziennik i wiadomości po terminie retencji (``PROC-01`` § 8)."""
    from .services import purge_expired as purge

    return purge()
