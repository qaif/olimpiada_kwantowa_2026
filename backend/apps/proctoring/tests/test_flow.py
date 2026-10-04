"""Przebieg nadzoru: start sprawdzany w LiveKit, wiadomości, incydenty i raport, webhooki, nagrania,
retencja i RODO (PROC-01 § 5–8)."""

from __future__ import annotations

import itertools
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.proctoring import services
from apps.proctoring.models import (
    EventKind,
    ProctoringRecording,
    ProctoringSession,
    ProctorKind,
    RecordingStatus,
)
from apps.webinars.livekit import LiveKitUnavailable
from apps.webinars.tests.fake_livekit import sign_webhook

from .conftest import ready_session
from .fake_livekit import MemoryStorage

pytestmark = pytest.mark.django_db


# --- start ---------------------------------------------------------------------------------------


def test_start_is_confirmed_by_livekit_not_by_the_browser(
    proctoring_on, fake_livekit, config, stage, student
):
    session = ready_session(stage, student, started=False)
    with pytest.raises(DomainError) as error:
        services.confirm_started(session, config, user=student.user)
    assert error.value.machine_code == "PROCTORING_NOT_LIVE"
    fake_livekit.publish(config.room_name("m"), session.identity, "MICROPHONE")
    with pytest.raises(DomainError):
        services.confirm_started(session, config, user=student.user)
    fake_livekit.publish(config.room_name("m"), session.identity, "CAMERA")
    services.confirm_started(session, config, user=student.user)
    session.refresh_from_db()
    assert session.started_at is not None and session.camera_live
    assert session.events.filter(kind=EventKind.STARTED).count() == 1


def test_start_requires_screen_when_stage_requires_it(proctoring_on, fake_livekit, config, stage, student):
    config.require_screen_share = True
    config.save()
    session = ready_session(stage, student, started=False)
    fake_livekit.publish(config.room_name("m"), session.identity, "CAMERA")
    with pytest.raises(DomainError):
        services.confirm_started(session, config, user=student.user)
    fake_livekit.publish(config.room_name("m"), session.identity, "CAMERA", "SCREEN_SHARE")
    services.confirm_started(session, config, user=student.user)


def test_start_with_livekit_down_is_502(proctoring_on, fake_livekit, config, stage, student):
    session = ready_session(stage, student, started=False)
    fake_livekit.fail_with = LiveKitUnavailable("down")
    with pytest.raises(DomainError) as error:
        services.confirm_started(session, config, user=student.user)
    assert error.value.status_code == 502


def test_check_keeps_only_booleans_and_browser_family(proctoring_on, config, stage, student, logged):
    services.give_consent(services.session_for(stage, student), user=student.user)
    response = logged(student.user).post(
        f"/me/proctoring/{stage.pk}/do/check/",
        data=json.dumps(
            {"webrtc": True, "camera": True, "browser": "Chrome", "userAgent": "Mozilla/5.0 …", "gpu": "RTX"}
        ),
        content_type="application/json",
    )
    assert response.json()["passed"] is True
    session = ProctoringSession.objects.get(participant=student)
    assert session.check_result == {
        "webrtc": True,
        "camera": True,
        "microphone": False,
        "screen": False,
        "browser": "chrome",
    }


def test_id_photo_must_be_jpeg(proctoring_on, config, stage, student, logged):
    config.id_photo = "required"
    config.save()
    ready_session(stage, student, started=False)
    client = logged(student.user)
    bad = client.post(f"/me/proctoring/{stage.pk}/do/photo/", data=b"<svg/>", content_type="image/jpeg")
    assert bad.status_code == 400
    good = client.post(
        f"/me/proctoring/{stage.pk}/do/photo/", data=b"\xff\xd8\xff\xe0JFIF", content_type="image/jpeg"
    )
    assert good.status_code == 200
    session = ProctoringSession.objects.get(participant=student)
    assert session.id_photo_key.startswith("proctoring/") and session.id_photo_key in MemoryStorage.objects


# --- czynności nadzorującego ----------------------------------------------------------------------


def test_message_goes_through_server_to_one_student_and_is_logged(
    proctoring_on, fake_livekit, config, stage, student, coordinator, logged
):
    session = ready_session(stage, student)
    response = logged(coordinator).post(
        f"/proctoring/{stage.pk}/s/{session.pk}/action/", {"action": "message", "body": "Popraw kamerę"}
    )
    assert response.status_code == 200
    payload = fake_livekit.payload("SendData")
    assert payload["destination_identities"] == [session.identity]
    assert payload["topic"] == "proctoring"
    assert payload["decoded"]["b"] == "Popraw kamerę"
    message = session.messages.get()
    assert message.via_data_channel
    assert session.events.filter(kind=EventKind.MESSAGE).exists()
    # Audyt bez treści wiadomości.
    entry = AuditLog.objects.get(action="proctoring.message_sent")
    assert "Popraw" not in json.dumps(entry.diff)
    # Uczeń odbiera także odpytaniem i potwierdza.
    client = logged(student.user)
    data = client.get(f"/me/proctoring/{stage.pk}/messages/").json()
    assert data["messages"][0]["body"] == "Popraw kamerę"
    client.post(f"/me/proctoring/{stage.pk}/do/ack/", {"id": message.pk})
    message.refresh_from_db()
    assert message.seen_at is not None


def test_message_survives_livekit_outage(proctoring_on, fake_livekit, config, stage, student, coordinator):
    session = ready_session(stage, student)
    fake_livekit.fail_with = LiveKitUnavailable("down")
    scope = services.proctor_scope(coordinator, stage)
    message = services.send_message(scope, session, "show_room", "")
    assert not message.via_data_channel
    assert services.messages_for_student(session)[0]["kind"] == "show_room"


def test_incident_flow_report_and_export(
    proctoring_on, config, stage, student, coordinator, reviewer, appeals_member, logged
):
    session = ready_session(stage, student)
    services.add_assignment(stage, coordinator, reviewer, ProctorKind.COMMITTEE)
    ProctoringSession.objects.filter(pk=session.pk).update(
        proctor=services.proctor_scope(reviewer, stage).assignment
    )
    response = logged(reviewer).post(
        f"/proctoring/{stage.pk}/s/{session.pk}/action/",
        {
            "action": "incident",
            "category": "other_person",
            "severity": "serious",
            "note": "=SUM(A1) druga osoba",
        },
    )
    assert response.status_code == 200
    incident = session.incidents.get()
    assert incident.reported_by == reviewer and incident.category == "other_person"
    logged(reviewer).post(f"/proctoring/{stage.pk}/s/{session.pk}/action/", {"action": "absent"})
    session.refresh_from_db()
    assert session.attendance == "absent"
    # Raport i eksport: komisja odwoławcza i koordynator tak, nadzorujący z komisji – nie.
    assert logged(reviewer).get(f"/proctoring/{stage.pk}/s/{session.pk}/").status_code == 404
    report = logged(appeals_member).get(f"/proctoring/{stage.pk}/s/{session.pk}/")
    assert report.status_code == 200 and "druga osoba" in report.content.decode()
    assert AuditLog.objects.filter(action="proctoring.report_viewed", actor=appeals_member).exists()
    export = logged(coordinator).get(f"/proctoring/{stage.pk}/export.csv")
    body = export.content.decode("utf-8-sig")
    assert student.public_code in body and "'=SUM(A1)" in body
    assert AuditLog.objects.filter(action="proctoring.exported").exists()


def test_invalid_incident_is_refused(proctoring_on, config, stage, student, coordinator):
    session = ready_session(stage, student)
    scope = services.proctor_scope(coordinator, stage)
    with pytest.raises(DomainError):
        services.create_incident(scope, session, category="bogus", severity="info", note="")


def test_proctor_json_throttle_answers_json(proctoring_on, config, stage, coordinator, logged, settings):
    from django.core.cache import cache

    rates = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    rates["proctoring_coordinator_token"] = "1/hour"
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": rates}
    cache.clear()
    client = logged(coordinator)
    client.post(f"/proctoring/{stage.pk}/token/", {"group": "m"})
    response = client.post(f"/proctoring/{stage.pk}/token/", {"group": "m"})
    assert response.status_code == 429
    assert response.json()["code"] == "THROTTLED"


# --- webhooki i nagrania --------------------------------------------------------------------------


EVENT_IDS = itertools.count(1)


def webhook(client, event: dict):
    stamp = int(timezone.now().timestamp())
    body = json.dumps({"id": f"EV_{next(EVENT_IDS)}", "createdAt": stamp, **event}).encode()
    return client.post(
        "/integrations/livekit/webhook/",
        data=body,
        content_type="application/webhook+json",
        HTTP_AUTHORIZATION=sign_webhook(body),
    )


def test_webhooks_update_session_and_start_recording_only_when_enabled(
    proctoring_on,
    fake_livekit,
    config,
    stage,
    student,
    client_for,
    competition,
    django_capture_on_commit_callbacks,
):
    session = ready_session(stage, student)
    client = client_for(competition)
    room = {"name": config.room_name("m")}
    who = {"identity": session.identity}
    assert (
        webhook(client, {"event": "participant_joined", "room": room, "participant": who}).status_code == 200
    )
    session.refresh_from_db()
    assert session.connected
    with django_capture_on_commit_callbacks(execute=True):
        webhook(
            client,
            {
                "event": "track_published",
                "room": room,
                "participant": who,
                "track": {"sid": "TR_1", "source": "CAMERA"},
            },
        )
    assert "StartTrackEgress" not in fake_livekit.names()  # nagrywanie wyłączone (domyślnie)
    config.record = True
    config.save()
    with django_capture_on_commit_callbacks(execute=True):
        webhook(
            client,
            {
                "event": "track_published",
                "room": room,
                "participant": who,
                "track": {"sid": "TR_2", "source": "CAMERA"},
            },
        )
    egress = fake_livekit.payload("StartTrackEgress")
    assert egress["track_id"] == "TR_2"
    assert egress["file"]["filepath"].startswith(
        f"proctoring/{competition.slug}/{config.room_key}/{session.identity}/"
    )
    recording = ProctoringRecording.objects.get()
    webhook(
        client,
        {
            "event": "egress_ended",
            "egressInfo": {
                "egressId": recording.egress_id,
                "status": "EGRESS_COMPLETE",
                "fileResults": [
                    {"filename": recording.storage_key, "size": "1234", "duration": "5000000000"}
                ],
            },
        },
    )
    recording.refresh_from_db()
    assert recording.status == RecordingStatus.COMPLETE and recording.size == 1234
    webhook(client, {"event": "participant_left", "room": room, "participant": who})
    session.refresh_from_db()
    assert not session.connected and not session.camera_live
    kinds = list(session.events.values_list("kind", flat=True))
    assert {"connected", "camera_on", "disconnected", "recording"} <= set(kinds)


def test_forged_room_name_does_not_touch_other_stage(
    proctoring_on, fake_livekit, config, stage, student, client_for, competition
):
    session = ready_session(stage, student)
    client = client_for(competition)
    forged = {"name": f"proc-inny-{config.room_key}-m"}
    webhook(
        client, {"event": "participant_joined", "room": forged, "participant": {"identity": session.identity}}
    )
    session.refresh_from_db()
    assert not session.connected


def test_recording_access_only_for_review_roles_with_audit(
    proctoring_on, config, stage, student, coordinator, reviewer, logged
):
    session = ready_session(stage, student)
    recording = ProctoringRecording.objects.create(
        session=session,
        egress_id="EG_9",
        track_sid="TR",
        storage_key="proctoring/k/r/p/x.webm",
        status=RecordingStatus.COMPLETE,
    )
    url = f"/proctoring/{stage.pk}/media/rec/{recording.pk}/"
    assert logged(reviewer).get(url).status_code == 404
    response = logged(coordinator).get(url)
    assert response.status_code == 302 and "X-Amz-Expires=900" in response["Location"]
    assert AuditLog.objects.filter(action="proctoring.recording_viewed", actor=coordinator).exists()


# --- retencja i RODO ------------------------------------------------------------------------------


def published_long_ago(stage, days: int):
    when = timezone.now() - timedelta(days=days)
    stage.results_published_at = when
    stage.appeal_window_opens_at = when - timedelta(days=2)
    stage.appeal_window_closes_at = when - timedelta(days=1)
    stage.review_deadline_at = when - timedelta(days=3)
    stage.deadline_at = when - timedelta(days=4)
    stage.opens_at = when - timedelta(days=5)
    stage.save()


def media_session(stage, student, coordinator):
    session = ready_session(stage, student)
    session.id_photo_key = "proctoring/k/r/p/id.jpg"
    session.save()
    ProctoringRecording.objects.create(
        session=session,
        egress_id="EG_1",
        track_sid="TR",
        storage_key="proctoring/k/r/p/a.webm",
        status=RecordingStatus.COMPLETE,
    )
    scope = services.proctor_scope(coordinator, stage)
    services.send_message(scope, session, "text", "hej")
    services.create_incident(scope, session, category="device", severity="info", note="telefon")
    return session


def test_purge_removes_media_after_retention_and_keeps_incidents(
    proctoring_on, config, stage, student, coordinator
):
    session = media_session(stage, student, coordinator)
    published_long_ago(stage, days=10)
    assert services.purge_expired() == 0  # przed terminem
    published_long_ago(stage, days=40)
    assert services.purge_expired() == 1
    session.refresh_from_db()
    assert session.purged_at is not None and session.id_photo_key == ""
    assert set(MemoryStorage.deleted) == {"proctoring/k/r/p/id.jpg", "proctoring/k/r/p/a.webm"}
    assert not session.messages.exists()
    assert set(session.events.values_list("kind", flat=True)) == {"incident"}
    assert session.incidents.count() == 1
    assert session.consents.exists()
    assert AuditLog.objects.filter(action="proctoring.purged").exists()


def test_hold_stops_the_purge(proctoring_on, config, stage, student, coordinator):
    session = media_session(stage, student, coordinator)
    services.set_hold(session, coordinator, "odwołanie w toku")
    published_long_ago(stage, days=40)
    assert services.purge_expired() == 0
    assert MemoryStorage.deleted == []


def test_unpublished_stage_is_purged_by_the_safety_net(
    proctoring_on, config, stage, student, coordinator, settings
):
    media_session(stage, student, coordinator)
    stage.opens_at = timezone.now() - timedelta(days=200)
    stage.deadline_at = timezone.now() - timedelta(days=190)
    stage.save()
    assert services.purge_expired() == 1


def test_export_and_anonymisation(proctoring_on, config, stage, student, coordinator):
    from apps.accounts.data_export import export_payload
    from apps.accounts.profile import anonymise_account

    session = media_session(stage, student, coordinator)
    section = export_payload(student.user)["nadzor_zdalny"]
    assert section[0]["zgoda"]["wersja"] == services.CONSENT_VERSION
    assert section[0]["incydenty"][0]["notatka"] == "telefon"
    assert section[0]["nagrania"] == 1 and section[0]["zdjecie_dokumentu"] is True
    anonymise_account(student.user)
    session.refresh_from_db()
    assert session.purged_at is not None
    assert "proctoring/k/r/p/a.webm" in MemoryStorage.deleted
    assert not session.consents.filter(withdrawn_at__isnull=True).exists()
    assert session.incidents.count() == 1


def test_deleting_session_deletes_files(
    proctoring_on, config, stage, student, coordinator, django_capture_on_commit_callbacks
):
    session = media_session(stage, student, coordinator)
    with django_capture_on_commit_callbacks(execute=True):
        session.delete()
    assert {"proctoring/k/r/p/id.jpg", "proctoring/k/r/p/a.webm"} <= set(MemoryStorage.deleted)


# --- ekrany ----------------------------------------------------------------------------------------


def test_screens_render(proctoring_on, fake_livekit, config, stage, student, coordinator, logged):
    session = ready_session(stage, student)
    assert logged(student.user).get(f"/me/proctoring/{stage.pk}/").status_code == 200
    assert logged(coordinator).get("/proctoring/").status_code == 200
    assert logged(coordinator).get(f"/proctoring/{stage.pk}/").status_code == 200
    roster = logged(coordinator).get(f"/proctoring/{stage.pk}/roster/?group=m&size=16").json()
    assert roster["items"][0]["id"] == session.pk and roster["size"] == 16
    assert logged(coordinator).get("/coordinator/proctoring/").status_code == 200
    assert logged(coordinator).get(f"/coordinator/proctoring/{stage.pk}/").status_code == 200
    dashboard = logged(student.user).get("/me/")
    assert dashboard.status_code == 200 and f"/me/proctoring/{stage.pk}/" in dashboard.content.decode()


def test_coordinator_saves_config_with_record_off_by_default(proctoring_on, stage, coordinator, logged):
    from apps.proctoring.models import ProctoringConfig

    response = logged(coordinator).post(
        f"/coordinator/proctoring/{stage.pk}/",
        {
            "action": "config",
            "enabled": "on",
            "id_photo": "off",
            "on_unavailable": "allow",
            "instructions": "",
        },
    )
    assert response.status_code == 302
    config = ProctoringConfig.objects.get(stage=stage)
    assert config.enabled and not config.record
    assert AuditLog.objects.filter(action="proctoring.config_updated").exists()


def test_other_competition_stage_is_404(
    proctoring_on, config, stage, coordinator, other_competition, client_for
):
    from apps.accounts.models import CompetitionRole
    from apps.tenancy.tests.factories import grant_membership

    other_competition.feature_flags = {**(other_competition.feature_flags or {}), "proctoring": True}
    other_competition.save(update_fields=["feature_flags"])
    grant_membership(coordinator, other_competition, CompetitionRole.COORDINATOR)
    client = client_for(other_competition)
    client.force_login(coordinator)
    assert client.get(f"/proctoring/{stage.pk}/").status_code == 404
    assert client.get(f"/coordinator/proctoring/{stage.pk}/").status_code == 404
