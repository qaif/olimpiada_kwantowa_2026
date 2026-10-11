"""Poprawki po przeglądzie WEB-01: usunięcie z pokoju, nagrania bez webhooka, limity, retencja, webhooki."""

from __future__ import annotations

import json
import time
from datetime import timedelta

import jwt
import pytest
from django.conf import settings as django_settings
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ParticipantFactory
from apps.core.api import DomainError
from apps.tenancy.tests.factories import grant_membership
from apps.webinars import livekit, services
from apps.webinars.models import (
    Audience,
    RecordingStatus,
    Webinar,
    WebinarAttendee,
    WebinarNotificationSettings,
    WebinarRecording,
    WebinarWebhookEvent,
)

from .conftest import make_webinar
from .fake_livekit import API_KEY, API_SECRET, MemoryRecordingStorage, sign_webhook

pytestmark = pytest.mark.django_db


def viewer(user, competition):
    return services.Viewer.of(user, competition)


def started(webinar, user):
    services.start(webinar, user)
    webinar.refresh_from_db()
    return webinar


def rates(settings, **values):
    config = dict(django_settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **values}
    settings.REST_FRAMEWORK = config


# --- M1: usunięty nie wraca z nowym tokenem ------------------------------------------------------------


def test_removed_participant_gets_no_new_token_until_readmitted(
    webinars_on, fake_livekit, participant, coordinator
):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION), coordinator)
    person = viewer(participant.user, webinars_on)
    identity = services.join_token(webinar, person)["identity"]
    fake_livekit.join(webinar.room_name, identity)

    services.remove_from_room(webinar, coordinator, identity)
    assert identity not in fake_livekit.rooms[webinar.room_name]
    with pytest.raises(DomainError) as refused:
        services.join_token(webinar, person)
    assert refused.value.machine_code == "WEBINAR_REMOVED"

    services.readmit(webinar, coordinator, identity)
    assert services.join_token(webinar, person)["identity"] == identity


def test_presenter_cannot_be_removed(webinars_on, fake_livekit, coordinator):
    webinar = make_webinar(webinars_on)
    identity = services.join_token(webinar, viewer(coordinator, webinars_on))["identity"]
    with pytest.raises(DomainError):
        services.remove_from_room(webinar, coordinator, identity)


def test_removed_guest_is_refused_and_link_can_be_rotated(
    webinars_on, fake_livekit, coordinator, logged, client_for
):
    webinar = started(make_webinar(webinars_on, public_link=True), coordinator)
    identity = services.new_guest_identity()
    services.guest_token(webinar, identity=identity, name="Gość")
    services.remove_from_room(webinar, coordinator, identity)
    with pytest.raises(DomainError):
        services.guest_token(webinar, identity=identity, name="Gość")

    old_key = webinar.public_key
    response = logged(coordinator).post(f"/coordinator/webinars/{webinar.pk}/guest-link/")
    assert response.status_code == 302
    webinar.refresh_from_db()
    assert webinar.public_key != old_key
    assert client_for(webinars_on).get(f"/zaproszenie/webinar/{old_key}/").status_code == 404


def test_reenabling_guest_link_gives_a_new_key(webinars_on, fake_livekit, coordinator):
    webinar = make_webinar(webinars_on, public_link=True)
    old_key = webinar.public_key
    base = {
        "title": webinar.title,
        "starts_at": webinar.starts_at,
        "duration_minutes": 60,
        "audience": webinar.audience,
    }
    services.update_webinar(webinar, coordinator, {**base, "public_link": False}, [])
    services.update_webinar(webinar, coordinator, {**base, "public_link": True}, [])
    assert webinar.public_key != old_key


def test_readmit_from_the_attendance_list(webinars_on, fake_livekit, participant, coordinator, logged):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION), coordinator)
    identity = services.join_token(webinar, viewer(participant.user, webinars_on))["identity"]
    services.remove_from_room(webinar, coordinator, identity)
    client = logged(coordinator)

    assert "Wpuść ponownie" in client.get(f"/coordinator/webinars/{webinar.pk}/").content.decode()
    client.post(f"/coordinator/webinars/{webinar.pk}/attendees/", {"action": "readmit", "identity": identity})
    assert WebinarAttendee.objects.get(identity=identity).removed_at is None


# --- L3, L4, L5 ---------------------------------------------------------------------------------------


def test_speaker_only_for_someone_still_in_the_audience(webinars_on, fake_livekit, participant, coordinator):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION), coordinator)
    identity = services.join_token(webinar, viewer(participant.user, webinars_on))["identity"]
    fake_livekit.join(webinar.room_name, identity)
    Webinar.objects.filter(pk=webinar.pk).update(audience=Audience.COMMITTEE)
    webinar.refresh_from_db()

    with pytest.raises(DomainError) as error:
        services.set_speaker(webinar, coordinator, identity, True)
    assert error.value.machine_code == "WEBINAR_NOT_AUDIENCE"
    services.set_speaker(webinar, coordinator, identity, False)  # odebrać głos wolno zawsze


def test_guest_speaker_requires_enabled_link(webinars_on, fake_livekit, coordinator):
    webinar = started(make_webinar(webinars_on, public_link=True), coordinator)
    identity = services.new_guest_identity()
    token = services.guest_token(webinar, identity=identity, name="Gość")["token"]
    assert jwt.decode(token, API_SECRET, algorithms=["HS256"])["name"] == "Gość (gość)"
    fake_livekit.join(webinar.room_name, identity)
    Webinar.objects.filter(pk=webinar.pk).update(public_link=False)
    webinar.refresh_from_db()
    with pytest.raises(DomainError):
        services.set_speaker(webinar, coordinator, identity, True)


def test_room_is_created_by_the_platform_before_a_token(webinars_on, fake_livekit, coordinator):
    webinar = make_webinar(webinars_on)
    services.join_token(webinar, viewer(coordinator, webinars_on))

    assert fake_livekit.names()[0] == "CreateRoom"
    assert fake_livekit.payload("CreateRoom")["name"] == webinar.room_name


# --- M2: nagranie nie wisi w stanie „nagrywa” ------------------------------------------------------------


def test_stop_reconciles_when_egress_already_ended(webinars_on, fake_livekit, coordinator):
    webinar = started(make_webinar(webinars_on, record=True), coordinator)
    recording = services.start_recording(webinar, coordinator)
    egress = fake_livekit.egresses[recording.egress_id]
    egress.update(
        active=False,
        status="EGRESS_COMPLETE",
        file_results=[{"filename": recording.storage_key, "size": "10"}],
    )

    services.stop_recording(webinar, coordinator)  # serwer: failed_precondition → ListEgress

    recording.refresh_from_db()
    assert recording.status == RecordingStatus.COMPLETE and recording.size == 10


def test_beat_reconciles_stale_recordings_and_unknown_egress_fails(webinars_on, fake_livekit, coordinator):
    webinar = started(make_webinar(webinars_on, record=True), coordinator)
    lost = WebinarRecording.objects.create(
        webinar=webinar,
        egress_id="EG_lost",
        storage_key=f"webinars/{webinars_on.slug}/{webinar.room_key}/x.mp4",
        started_at=timezone.now() - timedelta(minutes=30),
    )
    fresh_id = livekit.start_room_recording(webinar.room_name, "webinars/fresh.mp4")
    fresh = WebinarRecording.objects.create(
        webinar=webinar, egress_id=fresh_id, storage_key="webinars/fresh.mp4"
    )

    assert services.reconcile_stale_recordings() == 1
    lost.refresh_from_db()
    fresh.refresh_from_db()
    assert lost.status == RecordingStatus.FAILED and fresh.status == RecordingStatus.ACTIVE


def test_coordinator_marks_hanging_recording(webinars_on, fake_livekit, coordinator, logged):
    webinar = started(make_webinar(webinars_on, record=True), coordinator)
    hanging = WebinarRecording.objects.create(
        webinar=webinar,
        egress_id="EG_gone",
        storage_key=f"webinars/{webinars_on.slug}/{webinar.room_key}/y.mp4",
    )
    logged(coordinator).post(
        f"/coordinator/webinars/{webinar.pk}/recordings/", {"action": "mark_failed", "recording": hanging.pk}
    )
    hanging.refresh_from_db()
    assert hanging.status == RecordingStatus.FAILED


# --- M3: osobne limity i 429 jako JSON ---------------------------------------------------------------


def test_token_limit_answers_json_and_does_not_block_presenter_control(
    webinars_on, fake_livekit, coordinator, participant, logged, settings
):
    rates(settings, webinar_join="1/hour", webinar_control="600/hour")
    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION)
    host = logged(coordinator)
    assert host.post(f"/webinars/{webinar.pk}/token/").status_code == 200

    limited = host.post(f"/webinars/{webinar.pk}/token/")
    assert limited.status_code == 429
    assert limited["Content-Type"].startswith("application/json")
    assert limited.json()["code"] == "THROTTLED" and limited["Retry-After"]

    webinar.refresh_from_db()
    identity = services.join_token(webinar, viewer(participant.user, webinars_on))["identity"]
    fake_livekit.join(webinar.room_name, identity)
    control = host.post(f"/webinars/{webinar.pk}/control/", {"action": "speaker", "identity": identity})
    assert control.status_code == 200


# --- L1, L2: webhooki ----------------------------------------------------------------------------------


def test_webhook_failure_rolls_back_the_dedupe_row(webinars_on, fake_livekit, coordinator, monkeypatch):
    webinar = started(make_webinar(webinars_on), coordinator)
    event = {
        "event": "room_started",
        "id": "EV_boom",
        "createdAt": int(time.time()),
        "room": {"name": webinar.room_name},
    }

    def boom(*args):
        raise RuntimeError("awaria w trakcie")

    monkeypatch.setattr(services, "_apply_event", boom)
    with pytest.raises(RuntimeError):
        services.handle_webhook(event)
    assert not WebinarWebhookEvent.objects.filter(event_id="EV_boom").exists()
    monkeypatch.undo()
    assert services.handle_webhook(event) == "ok"


@pytest.mark.parametrize("drop", ["id", "createdAt"])
def test_webhook_without_id_or_time_is_refused(fake_livekit, drop):
    event = {"event": "room_started", "id": "EV_x", "createdAt": int(time.time())}
    event.pop(drop)
    body = json.dumps(event).encode()
    with pytest.raises(livekit.WebhookInvalid):
        livekit.verify_webhook(body, sign_webhook(body))


def test_webhook_token_without_expiry_is_refused(fake_livekit):
    import base64
    import hashlib

    body = json.dumps({"event": "room_started", "id": "EV_y", "createdAt": int(time.time())}).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    token = jwt.encode({"iss": API_KEY, "sha256": digest}, API_SECRET, algorithm="HS256")
    with pytest.raises(livekit.WebhookInvalid):
        livekit.verify_webhook(body, token)


# --- M6: informacja o nagrywaniu, retencja, dane osobowe ----------------------------------------------


def test_room_page_announces_recording(webinars_on, fake_livekit, participant, coordinator, logged):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION, record=True), coordinator)
    content = logged(participant.user).get(f"/webinars/{webinar.pk}/room/").content.decode()

    assert "Ten webinar może być nagrywany" in content
    assert "data-recording-badge" in content and "data-start-audio" in content


def test_retention_purges_recordings_and_attendance(webinars_on, fake_livekit, coordinator, settings):
    settings.WEBINAR_RETENTION_DAYS = 30
    old = make_webinar(webinars_on, starts_in_minutes=-60 * 24 * 40, record=True)
    recent = make_webinar(webinars_on, starts_in_minutes=-60 * 24 * 5, record=True)
    for webinar in (old, recent):
        WebinarRecording.objects.create(
            webinar=webinar,
            egress_id=f"EG_{webinar.pk}",
            storage_key=f"webinars/{webinars_on.slug}/{webinar.room_key}/r.mp4",
            status=RecordingStatus.COMPLETE,
        )
        WebinarAttendee.objects.create(webinar=webinar, identity=f"u-{webinar.pk:020d}", user=coordinator)

    assert services.purge_expired() == {"recordings": 1, "attendees": 1}
    assert MemoryRecordingStorage.deleted == [f"webinars/{webinars_on.slug}/{old.room_key}/r.mp4"]
    assert WebinarRecording.objects.get().webinar == recent
    assert Webinar.objects.filter(pk=old.pk).exists()


def test_anonymisation_and_export_cover_webinars(webinars_on, fake_livekit, coordinator):
    from apps.accounts.data_export import export_payload
    from apps.accounts.profile import anonymise_account

    person = ParticipantFactory(competition=webinars_on)
    grant_membership(person.user, webinars_on, CompetitionRole.PARTICIPANT)
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION), coordinator)
    services.join_token(webinar, viewer(person.user, webinars_on))
    WebinarNotificationSettings.objects.create(user=person.user, email_on_webinar=False)

    exported = export_payload(person.user)["webinary"]
    assert exported["listy_o_webinarach"] is False
    assert exported["obecnosc"][0]["webinar"] == webinar.title

    anonymise_account(person.user)
    assert not WebinarAttendee.objects.filter(user=person.user).exists()
    assert not WebinarNotificationSettings.objects.filter(user=person.user).exists()
