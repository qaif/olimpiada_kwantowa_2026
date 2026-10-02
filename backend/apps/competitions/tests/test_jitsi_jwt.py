"""Przepustki do własnego Jitsi (v0.39.0): kształt tokenu, podpis, warunek włączenia, listy.

Co tu jest pilnowane i dlaczego:

- **claimy** – dokładnie te, które czyta Prosody stable-11031 (``aud``, ``iss``, ``sub``, ``room``,
  ``nbf``, ``exp``, ``context.user``), bez e-maila i awatara, ``room`` nigdy ``*``, moderator
  wyłącznie wtedy, gdy wołający o niego prosi,
- **podpis** – HMAC-SHA256 liczony niezależnie w teście (i PyJWT, jeżeli jest w środowisku):
  implementacja ze standardowej biblioteki nie może się różnić od tego, co weryfikuje Jitsi,
- **wyłączenie** – bez sekretu (albo z obcym hostem pokoju) nic się nie zmienia: listy niosą
  adres pokoju jak dotąd, a przepustek nie ma,
- **listy** – przy włączonych przepustkach list niesie adres widoku wejścia w panelu i nigdy
  ``jwt=``; kontrola ``competitions.W001``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.checks import check_jitsi_jwt_secret
from apps.competitions.interviews import book_slot
from apps.competitions.jitsi_jwt import (
    CLOCK_SKEW_SECONDS,
    claims_for,
    encode_hs256,
    interview_window,
    is_platform_room,
    issue,
    join_url,
    jwt_enabled,
    room_of,
    short_name,
)
from apps.competitions.models import InterviewSlot, StageEntryStatus
from apps.competitions.tasks import remind_interviews
from apps.competitions.video import VideoProvider

from .factories import CurrentEditionFactory, InterviewStageFactory, StageEntryFactory

pytestmark = pytest.mark.django_db

SECRET = "Q7xk2LmN9pRt4VwY8zA3bC6dE1fG5hJ0KsTuVxYz2a4b"  # 44 znaki [A-Za-z0-9]
HOST = "meet.olimpiada.test"


@pytest.fixture
def jitsi(settings):
    """Przepustki włączone: sekret, host i stałe wartości claimów."""
    settings.JITSI_JWT_APP_SECRET = SECRET
    settings.JITSI_JWT_HOST = HOST
    settings.JITSI_JWT_APP_ID = "olimpiada"
    settings.JITSI_JWT_AUDIENCE = "jitsi"
    settings.JITSI_JWT_SUBJECT = "meet.jitsi"
    settings.JITSI_JWT_LEAD_MINUTES = 15
    settings.JITSI_JWT_GRACE_MINUTES = 60
    return settings


def decode(token: str) -> tuple[dict, dict]:
    """Nagłówek i treść tokenu – po sprawdzeniu podpisu **niezależną** implementacją."""
    header_b64, body_b64, signature_b64 = token.split(".")
    expected = hmac.new(SECRET.encode(), f"{header_b64}.{body_b64}".encode(), hashlib.sha256).digest()
    padded = signature_b64 + "=" * (-len(signature_b64) % 4)
    assert hmac.compare_digest(base64.urlsafe_b64decode(padded), expected), "podpis się nie zgadza"

    def part(value: str) -> dict:
        return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))

    return part(header_b64), part(body_b64)


@pytest.fixture
def stage():
    return InterviewStageFactory(
        edition=CurrentEditionFactory(year_label="XV (2026/2027)"),
        video_provider=VideoProvider.CUSTOM,
        video_base_url=f"https://{HOST}/",
    )


@pytest.fixture
def participant(stage):
    person = ParticipantFactory(
        user=UserFactory(
            email="uczestnik@example.test", first_name="Jan", last_name="Kowalski", groups=["participant"]
        )
    )
    StageEntryFactory(participant=person, stage=stage, status=StageEntryStatus.QUALIFIED)
    return person


def make_slot(stage, *, minutes_ahead: int = 60) -> InterviewSlot:
    start = timezone.now() + timedelta(minutes=minutes_ahead)
    return InterviewSlot.objects.create(stage=stage, starts_at=start, ends_at=start + timedelta(minutes=20))


# --- token ------------------------------------------------------------------------------------------


def test_participant_token_claims_and_signature(jitsi):
    starts = timezone.now()
    ends = starts + timedelta(minutes=30)

    header, claims = decode(
        issue("olimpiada-xv-abc", not_before=starts, expires_at=ends, display_name="Jan K.")
    )

    assert header == {"alg": "HS256", "typ": "JWT"}
    assert claims["aud"] == "jitsi"
    assert claims["iss"] == "olimpiada"
    assert claims["sub"] == "meet.jitsi"
    assert claims["room"] == "olimpiada-xv-abc"
    assert claims["nbf"] == int(starts.timestamp()) - CLOCK_SKEW_SECONDS
    assert claims["exp"] == int(ends.timestamp())
    # Żadnych danych ponad nazwę wyświetlaną – i żadnego moderatora, nawet jako ``false``.
    assert claims["context"] == {"user": {"name": "Jan K."}}
    assert set(claims) == {"aud", "iss", "sub", "room", "iat", "nbf", "exp", "context"}


def test_moderator_token_has_the_moderator_flag(jitsi):
    now = timezone.now()

    _header, claims = decode(
        issue("pokoj", not_before=now, expires_at=now + timedelta(minutes=5), moderator=True)
    )

    assert claims["context"]["user"]["moderator"] is True


def test_signature_matches_pyjwt_when_available(jitsi):
    """Druga, niezależna weryfikacja – ta sama, którą zrobiłaby biblioteka po stronie Jitsi."""
    jwt = pytest.importorskip("jwt")
    now = timezone.now()
    token = issue("pokoj", not_before=now, expires_at=now + timedelta(minutes=5))

    claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience="jitsi", issuer="olimpiada")

    assert claims["room"] == "pokoj"


@pytest.mark.parametrize("room", ["*", "", "tenant/pokoj", "Pokój z spacją"])
def test_room_claim_is_never_a_wildcard_or_a_path(jitsi, room):
    now = timezone.now()
    with pytest.raises(ValueError):
        claims_for(room, not_before=now, expires_at=now + timedelta(minutes=1))


def test_token_must_expire_after_it_starts(jitsi):
    now = timezone.now()
    with pytest.raises(ValueError):
        claims_for("pokoj", not_before=now, expires_at=now)


def test_without_a_secret_nothing_is_issued(settings):
    settings.JITSI_JWT_APP_SECRET = ""
    now = timezone.now()

    assert not jwt_enabled()
    with pytest.raises(RuntimeError):
        issue("pokoj", not_before=now, expires_at=now + timedelta(minutes=1))


def test_short_secret_works_like_no_secret(settings):
    settings.JITSI_JWT_APP_SECRET = "za-krotki"

    assert not jwt_enabled()


def test_join_url_carries_the_token_in_the_fragment():
    """Fragment, nie zapytanie: nie jedzie do serwera, więc nie trafia do logów ani ``Referer``."""
    url = join_url("https://meet.olimpiada.test/pokoj", "aaa.bbb.ccc")

    assert url == "https://meet.olimpiada.test/pokoj#jwt=%22aaa.bbb.ccc%22"
    assert "?" not in url


def test_encode_is_deterministic_for_the_same_claims():
    claims = {"room": "x", "exp": 1}

    assert encode_hs256(claims, SECRET) == encode_hs256(claims, SECRET)


# --- który pokój dostaje przepustkę -------------------------------------------------------------------


def test_platform_room_needs_our_host_and_a_secret(jitsi):
    assert is_platform_room(f"https://{HOST}/olimpiada-xv-1234-abcdef")
    assert is_platform_room(f"https://{HOST.upper()}:443/olimpiada-xv-1234-abcdef")
    assert not is_platform_room("https://meet.jit.si/olimpiada-xv-1234-abcdef")
    assert not is_platform_room("https://bbb.uczelnia.test/b/abc-def")
    assert not is_platform_room(f"https://{HOST}/")
    assert not is_platform_room(f"https://{HOST}/tenant/pokoj")
    assert not is_platform_room("")


def test_platform_room_is_off_without_a_secret(settings):
    settings.JITSI_JWT_APP_SECRET = ""
    settings.JITSI_JWT_HOST = HOST

    assert not is_platform_room(f"https://{HOST}/olimpiada-xv-1234-abcdef")


def test_room_of_lowercases_and_unquotes():
    assert room_of("https://meet.olimpiada.test/Komisja-A") == "komisja-a"
    assert room_of("https://meet.olimpiada.test/olimpiada%2Dx") == "olimpiada-x"


def test_short_name_is_first_name_and_initial():
    assert short_name(UserFactory.build(first_name="Jan", last_name="Kowalski")) == "Jan K."
    assert short_name(UserFactory.build(first_name="Jan", last_name="")) == "Jan"
    assert short_name(UserFactory.build(first_name="", last_name="Kowalski")) == ""


def test_interview_window_is_derived_from_the_slot(jitsi, stage):
    slot = make_slot(stage)

    opens_at, closes_at = interview_window(slot)

    assert opens_at == slot.starts_at - timedelta(minutes=15)
    assert closes_at == slot.ends_at + timedelta(minutes=60)


# --- listy ------------------------------------------------------------------------------------------


def test_confirmation_letter_links_the_panel_and_carries_no_token(
    jitsi, stage, participant, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        booking = book_slot(participant, make_slot(stage))

    body = mail.outbox[-1].body
    assert booking.meeting_url.startswith(f"https://{HOST}/olimpiada-")
    assert f"/me/stages/{stage.pk}/interview/join/" in body
    assert f"/me/stages/{stage.pk}/interview/precheck/" in body
    assert booking.meeting_url not in body
    assert "jwt" not in body


def test_reminder_links_the_panel_and_carries_no_token(
    jitsi, stage, participant, django_capture_on_commit_callbacks
):
    booking = book_slot(participant, make_slot(stage, minutes_ahead=20 * 60))
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        assert remind_interviews() == 1

    body = mail.outbox[-1].body
    assert f"/me/stages/{stage.pk}/interview/join/" in body
    assert booking.meeting_url not in body
    assert "jwt" not in body


def test_without_a_secret_the_letter_is_unchanged(
    settings, stage, participant, django_capture_on_commit_callbacks
):
    """Funkcja wyłączona: list niesie adres pokoju i link próby – dokładnie jak przed v0.39.0."""
    settings.JITSI_JWT_APP_SECRET = ""
    settings.JITSI_JWT_HOST = HOST

    with django_capture_on_commit_callbacks(execute=True):
        booking = book_slot(participant, make_slot(stage))

    body = mail.outbox[-1].body
    assert f"Link do rozmowy: {booking.meeting_url}" in body
    assert f"{booking.meeting_url}-test" in body
    assert "/interview/join/" not in body


def test_foreign_video_host_keeps_the_raw_link(jitsi, stage, participant, django_capture_on_commit_callbacks):
    """Etap z publicznym Jitsi: przepustki są skonfigurowane, ale ten pokój ich nie potrzebuje."""
    stage.video_provider = VideoProvider.JITSI
    stage.video_base_url = "https://meet.jit.si/"
    stage.save(update_fields=["video_provider", "video_base_url"])

    with django_capture_on_commit_callbacks(execute=True):
        booking = book_slot(participant, make_slot(stage))

    body = mail.outbox[-1].body
    assert booking.meeting_url.startswith("https://meet.jit.si/")
    assert f"Link do rozmowy: {booking.meeting_url}" in body
    assert "/interview/join/" not in body


# --- kontrola konfiguracji ---------------------------------------------------------------------------


def test_check_is_silent_without_a_secret(settings):
    settings.JITSI_JWT_APP_SECRET = ""

    assert check_jitsi_jwt_secret() == []


def test_check_warns_about_a_short_secret(settings):
    settings.JITSI_JWT_APP_SECRET = "krotki"

    messages = check_jitsi_jwt_secret()

    assert [message.id for message in messages] == ["competitions.W001"]


def test_check_warns_when_the_secret_equals_another_secret(settings):
    settings.JITSI_JWT_APP_SECRET = SECRET
    settings.SECRET_KEY = SECRET

    messages = check_jitsi_jwt_secret()

    assert [message.id for message in messages] == ["competitions.W001"]
    assert "SECRET_KEY" in messages[0].msg


def test_check_is_silent_for_a_good_secret(settings):
    settings.JITSI_JWT_APP_SECRET = SECRET

    assert check_jitsi_jwt_secret() == []
