"""Ekrany webinarów: bramki, koordynator, odbiorca, pokój i token, gość, webhook, CSP, izolacja."""

from __future__ import annotations

import json
from datetime import timedelta

import jwt
import pytest
from django.test import Client
from django.utils import timezone

from apps.core.models import AuditLog
from apps.webinars.models import Audience, RecordingStatus, Webinar, WebinarRecording

from .conftest import make_webinar
from .fake_livekit import API_SECRET, sign_webhook

pytestmark = pytest.mark.django_db

LIST = "/coordinator/webinars/"
MINE = "/webinars/"
WEBHOOK = "/integrations/livekit/webhook/"


def detail(webinar) -> str:
    return f"/coordinator/webinars/{webinar.pk}/"


def room(webinar) -> str:
    return f"/webinars/{webinar.pk}/room/"


def token_url(webinar) -> str:
    return f"/webinars/{webinar.pk}/token/"


def claims(response) -> dict:
    assert response.status_code == 200, response.content
    assert "no-store" in response["Cache-Control"]
    return jwt.decode(response.json()["token"], API_SECRET, algorithms=["HS256"])


# --- bramki ---------------------------------------------------------------------------------------


def test_without_flag_nothing_exists(competition, fake_livekit, logged, coordinator, participant):
    webinar = make_webinar(competition)
    client = logged(coordinator)

    assert client.get(LIST).status_code == 404
    assert client.get(detail(webinar)).status_code == 404
    assert "/coordinator/webinars/" not in client.get("/coordinator/").content.decode()
    participant_client = logged(participant.user)
    assert participant_client.get(MINE).status_code == 404
    assert participant_client.get(room(webinar)).status_code == 404
    assert 'href="/webinars/"' not in participant_client.get("/me/").content.decode()


def test_flag_without_server_says_so(webinars_on, settings, logged, coordinator, participant):
    settings.LIVEKIT_URL = ""
    client = logged(coordinator)

    content = client.get(LIST).content.decode()
    assert "Serwer LiveKit nie jest skonfigurowany" in content
    assert "Zapisz webinar" not in content
    assert 'href="/coordinator/webinars/"' in client.get("/coordinator/").content.decode()
    assert logged(participant.user).get(MINE).status_code == 404


def test_participant_never_reaches_coordinator_screens(webinars_on, fake_livekit, logged, participant):
    webinar = make_webinar(webinars_on)
    client = logged(participant.user)

    assert client.get(LIST).status_code == 403
    assert client.post(f"{detail(webinar)}start/").status_code == 403


# --- koordynator ----------------------------------------------------------------------------------


def test_coordinator_creates_webinar(webinars_on, fake_livekit, logged, coordinator, stage):
    starts = timezone.localtime(timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    response = logged(coordinator).post(
        LIST,
        {
            "title": "Omówienie etapu I",
            "description": "Zadania 1–3.",
            "starts_at": starts,
            "duration_minutes": 90,
            "audience": Audience.STAGE,
            "stage": stage.pk,
            "record": "on",
        },
    )

    webinar = Webinar.objects.get()
    assert response.status_code == 302
    assert response["Location"] == detail(webinar)
    assert webinar.stage == stage and webinar.record and not webinar.public_link
    assert AuditLog.objects.filter(action="webinar.created", target_id=str(webinar.pk)).exists()


def test_stage_audience_requires_stage(webinars_on, fake_livekit, logged, coordinator):
    response = logged(coordinator).post(
        LIST,
        {"title": "x", "starts_at": "2030-01-01T10:00", "duration_minutes": 60, "audience": Audience.STAGE},
    )
    assert response.status_code == 400
    assert not Webinar.objects.exists()


def test_start_leads_to_the_room_and_pages_carry_no_credentials(
    webinars_on, fake_livekit, logged, coordinator
):
    webinar = make_webinar(webinars_on, public_link=True, record=True)
    client = logged(coordinator)

    response = client.post(f"{detail(webinar)}start/")
    assert response.status_code == 302 and response["Location"] == room(webinar)
    for page in (client.get(detail(webinar)), client.get(room(webinar))):
        content = page.content.decode()
        assert page.status_code == 200
        assert API_SECRET not in content
        assert "eyJ" not in content  # żaden JWT w HTML-u
        assert "no-store" in page["Cache-Control"]
    room_page = client.get(room(webinar)).content.decode()
    assert f'data-token-url="/webinars/{webinar.pk}/token/"' in room_page
    assert 'data-role="presenter"' in room_page
    assert "webinar-room-strings" in room_page
    assert f"/zaproszenie/webinar/{webinar.public_key}/" in client.get(detail(webinar)).content.decode()


def test_start_get_is_not_allowed(webinars_on, fake_livekit, logged, coordinator):
    webinar = make_webinar(webinars_on)
    assert logged(coordinator).get(f"{detail(webinar)}start/").status_code == 405
    assert Webinar.objects.get(pk=webinar.pk).started_at is None


def test_end_and_cancel(webinars_on, fake_livekit, logged, coordinator):
    webinar = make_webinar(webinars_on)
    client = logged(coordinator)
    client.post(f"{detail(webinar)}start/")

    assert client.post(f"{detail(webinar)}end/").status_code == 302
    webinar.refresh_from_db()
    assert webinar.ended_at is not None and "DeleteRoom" in fake_livekit.names()
    assert client.post(f"{detail(webinar)}cancel/").status_code == 302
    assert Webinar.objects.get(pk=webinar.pk).cancelled_at is not None


def test_recording_buttons_and_delete_confirmation(webinars_on, fake_livekit, logged, coordinator):
    webinar = make_webinar(webinars_on, record=True)
    client = logged(coordinator)
    client.post(f"{detail(webinar)}start/")
    url = f"{detail(webinar)}recordings/"

    client.post(url, {"action": "start"})
    recording = WebinarRecording.objects.get()
    client.post(url, {"action": "stop"})
    assert fake_livekit.egresses[recording.egress_id]["active"] is False
    WebinarRecording.objects.filter(pk=recording.pk).update(status=RecordingStatus.COMPLETE)
    client.post(url, {"action": "delete", "recording": recording.pk})
    assert WebinarRecording.objects.exists()
    client.post(url, {"action": "delete", "recording": recording.pk, "confirm": "1"})
    assert not WebinarRecording.objects.exists()
    assert client.post(url, {"action": "publish", "recording": 999}).status_code == 404


def test_edit_changes_fields(webinars_on, fake_livekit, logged, coordinator):
    webinar = make_webinar(webinars_on)
    client = logged(coordinator)
    assert client.get(f"{detail(webinar)}edit/").status_code == 200
    response = client.post(
        f"{detail(webinar)}edit/",
        {
            "title": "Nowy tytuł",
            "starts_at": "2030-01-01T10:00",
            "duration_minutes": 45,
            "audience": Audience.COMMITTEE,
        },
    )
    assert response.status_code == 302
    webinar.refresh_from_db()
    assert webinar.title == "Nowy tytuł" and webinar.audience == Audience.COMMITTEE
    assert "title" in AuditLog.objects.get(action="webinar.updated").diff["fields"]


# --- izolacja konkursów ---------------------------------------------------------------------------


def test_webinar_of_another_competition_is_404(
    webinars_on, other_competition, fake_livekit, logged, coordinator, participant
):
    foreign = make_webinar(other_competition, audience=Audience.COMPETITION)
    client = logged(coordinator)

    assert client.get(detail(foreign)).status_code == 404
    assert client.post(f"{detail(foreign)}start/").status_code == 404
    assert client.get(room(foreign)).status_code == 404
    assert client.post(token_url(foreign)).status_code == 404
    assert logged(participant.user).post(token_url(foreign)).status_code == 404


# --- odbiorca i token -----------------------------------------------------------------------------


def test_participant_sees_room_and_gets_viewer_token(
    webinars_on, fake_livekit, logged, coordinator, participant
):
    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION)
    hidden = make_webinar(webinars_on, title="Tylko dla komisji", audience=Audience.COMMITTEE)
    client = logged(participant.user)

    content = client.get(MINE).content.decode()
    assert webinar.title in content and hidden.title not in content
    assert 'href="/webinars/"' in client.get("/me/").content.decode()
    assert client.get(room(hidden)).status_code == 404

    not_started = client.post(token_url(webinar))
    assert not_started.status_code == 409
    assert "jeszcze nie rozpoczął" in not_started.json()["detail"]

    logged(coordinator).post(f"{detail(webinar)}start/")
    grant = claims(client.post(token_url(webinar)))["video"]
    assert grant["canPublish"] is False and grant["room"] == webinar.room_name
    assert client.get(token_url(webinar)).status_code == 405


def test_viewer_cannot_use_presenter_controls(webinars_on, fake_livekit, logged, coordinator, participant):
    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION)
    logged(coordinator).post(f"{detail(webinar)}start/")
    client = logged(participant.user)

    response = client.post(f"/webinars/{webinar.pk}/control/", {"action": "speaker", "identity": "u-x"})
    assert response.status_code == 404
    assert fake_livekit.calls == []


def test_presenter_promotes_from_the_room(webinars_on, fake_livekit, logged, coordinator, participant):
    from apps.webinars.services import pseudonym

    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION)
    host = logged(coordinator)
    host.post(token_url(webinar))
    logged(participant.user).post(token_url(webinar))
    identity = pseudonym(participant.user)
    fake_livekit.join(webinar.room_name, identity)

    response = host.post(f"/webinars/{webinar.pk}/control/", {"action": "speaker", "identity": identity})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert fake_livekit.rooms[webinar.room_name][identity]["can_publish"] is True


def test_co_moderator_from_committee_gets_presenter_token(webinars_on, fake_livekit, logged, reviewer):
    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION)
    webinar.co_moderators.set([reviewer.user])

    grant = claims(logged(reviewer.user).post(token_url(webinar)))["video"]
    assert grant["canPublish"] is True and grant["roomAdmin"] is True


def test_committee_panel_card_lists_webinars(webinars_on, fake_livekit, logged, reviewer):
    make_webinar(webinars_on, title="Narada komisji", audience=Audience.COMMITTEE)
    assert "Narada komisji" in logged(reviewer.user).get("/review/").content.decode()


def test_published_recording_redirects_to_short_lived_url(webinars_on, fake_livekit, logged, participant):
    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION, record=True, starts_in_minutes=-300)
    published = WebinarRecording.objects.create(
        webinar=webinar,
        egress_id="EG_1",
        storage_key=f"webinars/{webinars_on.slug}/{webinar.room_key}/a.mp4",
        status=RecordingStatus.COMPLETE,
        published=True,
    )
    hidden = WebinarRecording.objects.create(
        webinar=webinar,
        egress_id="EG_2",
        storage_key=f"webinars/{webinars_on.slug}/{webinar.room_key}/b.mp4",
        status=RecordingStatus.COMPLETE,
    )
    client = logged(participant.user)

    content = client.get(MINE).content.decode()
    assert f"/recordings/{published.pk}/" in content and f"/recordings/{hidden.pk}/" not in content
    response = client.get(f"/webinars/{webinar.pk}/recordings/{published.pk}/")
    assert response.status_code == 302 and "X-Amz-Expires=7200" in response["Location"]
    assert client.get(f"/webinars/{webinar.pk}/recordings/{hidden.pk}/").status_code == 404


def test_notification_toggle(webinars_on, fake_livekit, logged, participant):
    from apps.webinars.services import notifications_enabled

    client = logged(participant.user)
    client.post("/webinars/notifications/", {"email_on_webinar": "0"})
    assert notifications_enabled(participant.user) is False
    client.post("/webinars/notifications/", {"email_on_webinar": "1"})
    assert notifications_enabled(participant.user) is True


# --- gość -----------------------------------------------------------------------------------------


def test_guest_link_flow(webinars_on, fake_livekit, client_for, logged, coordinator):
    webinar = make_webinar(webinars_on, public_link=True)
    guest = client_for(webinars_on)
    url = f"/zaproszenie/webinar/{webinar.public_key}/"

    page = guest.get(url)
    assert page.status_code == 200 and webinar.title in page.content.decode()
    assert guest.get(f"{url}room/")["Location"] == url
    assert guest.post(url, {"name": ""}).status_code == 400
    assert guest.post(url, {"name": "Prof. Gość"})["Location"] == f"{url}room/"
    assert guest.get(f"{url}room/").status_code == 200
    assert guest.post(f"{url}token/").status_code == 409  # jeszcze nie rozpoczęty

    logged(coordinator).post(f"{detail(webinar)}start/")
    token = guest.post(f"{url}token/")
    guest_claims = claims(token)
    assert guest_claims["name"] == "Prof. Gość" and guest_claims["sub"].startswith("g-")
    assert guest_claims["video"]["canPublish"] is False

    webinar.public_link = False
    webinar.save()
    assert guest.get(url).status_code == 404
    assert guest.post(f"{url}token/").status_code == 404


# --- webhook --------------------------------------------------------------------------------------


def test_webhook_requires_signature(webinars_on, fake_livekit, coordinator):
    webinar = make_webinar(webinars_on)
    body = json.dumps(
        {
            "event": "room_started",
            "id": "EV_w1",
            "createdAt": int(timezone.now().timestamp()),
            "room": {"name": webinar.room_name},
        }
    ).encode()
    server = Client()

    unsigned = server.post(WEBHOOK, body, content_type="application/webhook+json")
    assert unsigned.status_code == 401
    forged = server.post(
        WEBHOOK, body, content_type="application/webhook+json", HTTP_AUTHORIZATION=sign_webhook(b"{}")
    )
    assert forged.status_code == 401
    assert Webinar.objects.get(pk=webinar.pk).live_started_at is None

    ok = server.post(
        WEBHOOK, body, content_type="application/webhook+json", HTTP_AUTHORIZATION=sign_webhook(body)
    )
    assert ok.status_code == 200 and ok.json() == {"result": "ok"}
    assert Webinar.objects.get(pk=webinar.pk).live_started_at is not None
    replay = server.post(
        WEBHOOK, body, content_type="application/webhook+json", HTTP_AUTHORIZATION=sign_webhook(body)
    )
    assert replay.json() == {"result": "duplicate"}


def test_webhook_without_configuration_is_404(settings):
    settings.LIVEKIT_URL = ""
    assert Client().post(WEBHOOK, b"{}", content_type="application/json").status_code == 404


# --- CSP ------------------------------------------------------------------------------------------


def test_csp_connect_src_has_livekit_only_when_configured(
    webinars_on, fake_livekit, settings, logged, coordinator
):
    client = logged(coordinator)
    policy = client.get(LIST)["Content-Security-Policy"]
    connect = next(part for part in policy.split(";") if part.strip().startswith("connect-src"))
    assert "wss://live.example.test" in connect and "https://live.example.test" in connect
    assert "live.example.test" not in policy.replace(connect, "")

    settings.LIVEKIT_URL = ""
    assert "live.example.test" not in client.get(LIST)["Content-Security-Policy"]
