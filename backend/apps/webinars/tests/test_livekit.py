"""Klient LiveKit: tokeny (claimy i uprawnienia wg dokumentacji), API serwera, podpis webhooków."""

from __future__ import annotations

import json
import time

import jwt
import pytest
import requests

from apps.webinars import livekit

from .fake_livekit import API_KEY, API_SECRET, sign_webhook

pytestmark = pytest.mark.django_db


def decode(token: str) -> dict:
    """Niezależna weryfikacja (PyJWT) – tak, jak token przeczyta serwer LiveKit."""
    return jwt.decode(token, API_SECRET, algorithms=["HS256"], options={"verify_aud": False})


# --- konfiguracja ---------------------------------------------------------------------------------


def test_configuration_requires_wss_key_and_secret(settings):
    settings.DEBUG = False
    settings.LIVEKIT_API_KEY = API_KEY
    settings.LIVEKIT_API_SECRET = API_SECRET
    settings.LIVEKIT_API_URL = ""
    settings.LIVEKIT_URL = ""
    assert not livekit.configured()
    settings.LIVEKIT_URL = "ws://live.example.test"
    assert not livekit.configured()
    settings.LIVEKIT_URL = "wss://live.example.test/"
    assert livekit.configured()
    assert livekit.ws_url() == "wss://live.example.test"
    assert livekit.api_url() == "https://live.example.test"
    assert livekit.csp_origins() == ("wss://live.example.test", "https://live.example.test")
    settings.LIVEKIT_API_URL = "http://livekit:7880"
    assert livekit.api_url() == "http://livekit:7880"
    settings.LIVEKIT_API_SECRET = ""
    assert not livekit.configured()
    assert livekit.csp_origins() == ()


# --- tokeny ---------------------------------------------------------------------------------------


def test_viewer_token_claims_follow_the_documented_format(fake_livekit):
    token = livekit.access_token(
        identity="u-0123456789abcdef0123", name="Ala N.", room="olimp-k-1", presenter=False
    )
    claims = decode(token)

    assert claims["iss"] == API_KEY
    assert claims["sub"] == "u-0123456789abcdef0123"
    assert claims["name"] == "Ala N."
    assert claims["exp"] - claims["nbf"] <= 600 + livekit.CLOCK_SKEW_SECONDS
    assert claims["video"] == {
        "room": "olimp-k-1",
        "roomJoin": True,
        "canSubscribe": True,
        "canPublishData": True,
        "canPublish": False,
    }
    assert "@" not in json.dumps(claims)


def test_presenter_token_can_publish_and_administer(fake_livekit):
    claims = decode(livekit.access_token(identity="u-x", name="Ola K.", room="olimp-k-1", presenter=True))

    assert claims["video"]["canPublish"] is True
    assert claims["video"]["roomAdmin"] is True
    assert claims["video"]["canPublishData"] is True


def test_token_is_short_lived(fake_livekit, settings):
    settings.LIVEKIT_TOKEN_TTL_SECONDS = 600
    claims = decode(livekit.access_token(identity="u-x", name="x", room="r", presenter=False))
    assert claims["exp"] - int(time.time()) <= 600


# --- API serwera ----------------------------------------------------------------------------------


def test_update_participant_with_room_admin_token(fake_livekit):
    fake_livekit.join("olimp-k-1", "u-a")

    livekit.set_can_publish("olimp-k-1", "u-a", True)

    assert fake_livekit.rooms["olimp-k-1"]["u-a"]["can_publish"] is True
    payload = fake_livekit.payload("UpdateParticipant")
    assert payload["permission"] == {"can_subscribe": True, "can_publish": True, "can_publish_data": True}


def test_unknown_participant_is_a_livekit_error(fake_livekit):
    with pytest.raises(livekit.LiveKitError) as error:
        livekit.set_can_publish("olimp-k-1", "u-nobody", True)
    assert error.value.code == "not_found"


def test_egress_requests_carry_only_the_file_path(fake_livekit):
    egress_id = livekit.start_room_recording("olimp-k-1", "webinars/k/abc/1.mp4")
    payload = fake_livekit.egresses[egress_id]["payload"]

    assert payload["room_name"] == "olimp-k-1"
    assert payload["file_outputs"] == [{"file_type": "MP4", "filepath": "webinars/k/abc/1.mp4"}]
    assert "s3" not in json.dumps(payload)
    livekit.stop_egress(egress_id)
    assert fake_livekit.egresses[egress_id]["active"] is False


def test_network_failure_is_unavailable(fake_livekit):
    fake_livekit.fail_with = requests.ConnectTimeout("x")
    with pytest.raises(livekit.LiveKitUnavailable):
        livekit.delete_room("olimp-k-1")


def test_wrong_secret_is_refused_by_the_server(fake_livekit, settings):
    settings.LIVEKIT_API_SECRET = "zly-sekret-0123456789abcdef0123456789"
    with pytest.raises(livekit.LiveKitError) as error:
        livekit.delete_room("olimp-k-1")
    assert error.value.code == "unauthenticated"


def test_no_call_without_configuration(settings):
    settings.LIVEKIT_URL = ""
    with pytest.raises(livekit.LiveKitUnavailable):
        livekit.delete_room("r")


# --- webhook --------------------------------------------------------------------------------------


BODY = json.dumps(
    {"event": "room_started", "id": "EV_1", "createdAt": int(time.time()), "room": {"name": "olimp-k-1"}}
).encode()


def test_webhook_with_valid_signature_is_accepted(fake_livekit):
    event = livekit.verify_webhook(BODY, sign_webhook(BODY))
    assert event["event"] == "room_started"
    assert livekit.verify_webhook(BODY, "Bearer " + sign_webhook(BODY))["id"] == "EV_1"


@pytest.mark.parametrize(
    "header",
    [
        "",
        "nie-token",
        sign_webhook(BODY, secret="inny-sekret-0123456789abcdef0123456789"),
        sign_webhook(BODY, key="INNY_KLUCZ"),
        sign_webhook(BODY, ttl=-600),
        sign_webhook(b"{}"),
    ],
    ids=["brak", "smieci", "inny-sekret", "inny-klucz", "przeterminowany", "inna-tresc"],
)
def test_webhook_with_invalid_signature_is_refused(fake_livekit, header):
    with pytest.raises(livekit.WebhookInvalid):
        livekit.verify_webhook(BODY, header)


def test_webhook_alg_none_is_refused(fake_livekit):
    token = jwt.encode({"iss": API_KEY, "sha256": "x"}, key=None, algorithm="none")
    with pytest.raises(livekit.WebhookInvalid):
        livekit.verify_webhook(BODY, token)
