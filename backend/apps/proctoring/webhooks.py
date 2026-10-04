"""Zdarzenia LiveKit dla pokoi nadzoru – przekazywane z webhooka WEB-01.

Webhook jest jeden na instalację (``/integrations/livekit/webhook/``): podpis, powtórki i wiek
zdarzenia sprawdza ``apps.webinars`` (``livekit.verify_webhook``, ``services.handle_webhook``), a tam,
gdzie zdarzenie dotyczy pokoju ``proc-…`` albo egressu nagrania nadzoru, oddaje je tutaj. Odpowiedź
``None`` znaczy „to nie nasze” – webinar obsługuje zdarzenie dalej po swojemu.

Co zapisujemy: połączenie i rozłączenie ucznia, start i koniec nadawania kamery i ekranu (stan sesji
do siatki + zdarzenie w dzienniku), koniec nagrania. Start nagrania (Track Egress) idzie zadaniem
Celery po commicie – webhook odpowiada od razu, a LiveKit nie czeka na nasze polecenie do egressu.
"""

from __future__ import annotations

from django.db import transaction

from . import livekit_api
from .models import (
    EventKind,
    EventSource,
    ProctoringConfig,
    ProctoringRecording,
    ProctoringSession,
    RecordingStatus,
)
from .services import log_event
from .storage import KEY_PREFIX


def config_for_room(name: str) -> tuple[ProctoringConfig, str] | None:
    """``(ustawienia, grupa)`` z nazwy pokoju ``proc-<konkurs>-<końcówka>-<grupa>`` albo ``None``.

    Slug konkursu bywa z łącznikiem, więc nazwę tniemy **od końca**; o trafieniu rozstrzyga pełne
    porównanie z nazwą złożoną przez model – podrobiona nazwa nie trafi do cudzego etapu.
    """
    name = (name or "").strip()
    if not name.startswith("proc-"):
        return None
    parts = name.rsplit("-", 2)
    if len(parts) != 3:
        return None
    _prefix, key, group = parts
    config = (
        ProctoringConfig.objects.select_related("stage__edition__competition").filter(room_key=key).first()
    )
    if config is None or config.room_name(group) != name:
        return None
    return config, group


def _session(config, identity: str) -> ProctoringSession | None:
    if not identity.startswith("p-"):
        return None
    return ProctoringSession.objects.filter(stage=config.stage, identity=identity[:32]).first()


def _handle_egress(egress: dict, event_name: str, at) -> str | None:
    egress_id = str(egress.get("egressId") or egress.get("egress_id") or "")
    recording = ProctoringRecording.objects.filter(egress_id=egress_id).first() if egress_id else None
    if recording is None:
        return None
    if event_name != "egress_ended":
        return "ok"
    status_value = str(egress.get("status") or "")
    failed = status_value in ("EGRESS_FAILED", "EGRESS_ABORTED", "EGRESS_LIMIT_REACHED") or bool(
        egress.get("error")
    )
    file_info = egress.get("file") or {}
    results = egress.get("fileResults") or egress.get("file_results") or ([file_info] if file_info else [])
    first = results[0] if results else {}
    filename = str(first.get("filename") or "")
    prefix = recording.storage_key.rsplit("/", 1)[0] + "/"
    if filename.startswith(prefix) and filename.startswith(KEY_PREFIX):
        recording.storage_key = filename
    recording.status = RecordingStatus.FAILED if failed else RecordingStatus.COMPLETE
    recording.error = str(egress.get("error") or "")[:200]
    try:
        recording.size = int(first.get("size")) if first.get("size") is not None else None
    except TypeError, ValueError:
        recording.size = None
    try:
        recording.duration_seconds = (
            int(int(first.get("duration")) / 1_000_000_000) if first.get("duration") else None
        )
    except TypeError, ValueError:
        recording.duration_seconds = None
    recording.ended_at = at
    recording.save()
    log_event(
        recording.session,
        EventKind.RECORDING,
        EventSource.WEBHOOK,
        detail={"status": recording.status},
        at=at,
    )
    return "ok"


def _update(session, **fields) -> None:
    ProctoringSession.objects.filter(pk=session.pk).update(**fields)


def interview_room_session(room_name: str, identity: str):
    """``(ustawienia nadzoru, sesja ucznia)`` dla pokoju rozmowy LiveKit etapu z nadzorem – albo ``None``.

    ``None`` dla pokoju, który nie jest pokojem rozmowy, i dla etapu bez nadzoru (wtedy zdarzenie nie
    jest nasze). Sesja ``None`` – w pokoju jest ktoś, kto nie jest uczniem tego etapu (komisja).
    Uczeń rozpoznawany po pseudonimie konta (``u-…``, WEB-01) wśród zapisów **tego** etapu.
    """
    from apps.competitions.models import InterviewBooking
    from apps.webinars.services import pseudonym

    from .services import config_for, enabled, session_for
    from .stage_rooms import stage_for_room

    if not room_name.startswith("olimpiada-"):
        return None
    stage = stage_for_room(room_name)
    if stage is None or not enabled(stage.edition.competition):
        return None
    config = config_for(stage)
    if config is None:
        return None
    if not identity.startswith("u-"):
        return config, None
    bookings = InterviewBooking._base_manager.filter(slot__stage=stage).select_related(
        "entry__participant__user"
    )
    for booking in bookings:
        participant = booking.entry.participant
        if participant is not None and pseudonym(participant.user) == identity:
            return config, session_for(stage, participant)
    return config, None


def handle_event(name: str, event: dict, at) -> str | None:
    """Zdarzenie zweryfikowanego webhooka. ``None`` – nie dotyczy nadzoru."""
    if name.startswith("egress_"):
        return _handle_egress(event.get("egressInfo") or event.get("egress_info") or {}, name, at)
    room = event.get("room") or {}
    room_name = str(room.get("name") or "")
    participant = event.get("participant") or {}
    identity = str(participant.get("identity") or "")
    found = config_for_room(room_name)
    if found is not None:
        config, _group = found
        session = _session(config, identity)
        recording_room = ""
    else:
        # Pokój rozmowy etapu w LiveKit (STAGE-LK-01) z nadzorem – zdarzenia ucznia do jego sesji.
        found = interview_room_session(room_name, identity)
        if found is None:
            return None
        config, session = found
        recording_room = room_name
    if session is None:
        # Pokój nadzoru, ale nie uczeń (nadzorujący ``x-…``, start/koniec pokoju) – nasze, bez zmian.
        return "ignored"
    track = event.get("track") or {}
    if name == "participant_joined":
        _update(session, connected=True, last_seen_at=at)
        log_event(session, EventKind.CONNECTED, EventSource.WEBHOOK, at=at)
    elif name == "participant_left":
        _update(session, connected=False, camera_live=False, screen_live=False)
        log_event(session, EventKind.DISCONNECTED, EventSource.WEBHOOK, at=at)
    elif name == "track_published" and livekit_api.is_camera(track):
        _update(session, camera_live=True, last_seen_at=at)
        log_event(session, EventKind.CAMERA_ON, EventSource.WEBHOOK, at=at)
        if config.enabled and config.record:
            from .tasks import start_track_recording

            session_pk, track_sid = session.pk, str(track.get("sid") or "")[:64]
            transaction.on_commit(lambda: start_track_recording.delay(session_pk, track_sid, recording_room))
    elif name == "track_unpublished" and livekit_api.is_camera(track):
        _update(session, camera_live=False)
        log_event(session, EventKind.CAMERA_OFF, EventSource.WEBHOOK, at=at)
    elif name == "track_published" and livekit_api.is_screen(track):
        _update(session, screen_live=True)
        log_event(session, EventKind.SCREEN_ON, EventSource.WEBHOOK, at=at)
    elif name == "track_unpublished" and livekit_api.is_screen(track):
        _update(session, screen_live=False)
        log_event(session, EventKind.SCREEN_OFF, EventSource.WEBHOOK, at=at)
    else:
        return "ignored"
    return "ok"
