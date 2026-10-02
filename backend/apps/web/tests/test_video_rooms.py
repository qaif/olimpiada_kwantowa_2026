"""Pokoje wideo bez terminu (v0.39.0): koordynator, komisja z uprawnieniem, bramka linku-zaproszenia.

Trzy pytania, na które odpowiada ten plik:

1. **kto może** – koordynator tego konkursu zawsze; członek komisji tylko z uprawnieniem, tylko
   aktywny, tylko w swoim konkursie i tylko dla własnych pokoi; uczestnik nigdy,
2. **co działa od razu** – zamknięcie pokoju, wygaśnięcie, wymiana linku, odebranie uprawnienia
   (wejście z panelu do pokoi autora),
3. **co nie wycieka** – GET bramki nie wystawia przepustki, linki widać tylko po jawnej czynności
   (``no-store``, audyt), w audycie nie ma ani kluczy, ani tokenów, ani nazw gości.
"""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from urllib.parse import unquote, urlsplit

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.models import GROUP_APPEALS, CommitteeStatus, CompetitionRole
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.video_rooms import RoomCreator, VideoRoom
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

SECRET = "Q7xk2LmN9pRt4VwY8zA3bC6dE1fG5hJ0KsTuVxYz2a4b"
HOST = "meet.olimpiada.test"
ROOMS = "/coordinator/video-rooms/"
MY_ROOMS = "/review/video-rooms/"


@pytest.fixture
def jitsi(settings):
    settings.JITSI_JWT_APP_SECRET = SECRET
    settings.JITSI_JWT_HOST = HOST
    settings.JITSI_JWT_ROOM_MAX_DAYS = 60
    settings.JITSI_JWT_COMMITTEE_ROOM_MAX_DAYS = 30
    settings.JITSI_JWT_GATEWAY_MINUTES = 10
    settings.JITSI_JWT_SESSION_MINUTES = 180
    return settings


def rest_framework_with(**rates) -> dict:
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


def logged_in(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def claims_from(location: str) -> dict:
    fragment = urlsplit(location).fragment
    assert fragment.startswith("jwt="), location
    token = json.loads(unquote(fragment[len("jwt=") :]))
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def issuer(**kwargs):
    """Aktywny recenzent **z** uprawnieniem do pokoi."""
    member = ActiveReviewerFactory(video_room_issuer=True, **kwargs)
    member.user.first_name, member.user.last_name = "Ewa", "Recenzentka"
    member.user.save(update_fields=["first_name", "last_name"])
    return member


def create(client, url: str = ROOMS, **data):
    payload = {"label": "Zebranie komisji", "validity_days": 7}
    payload.update(data)
    return client.post(url, payload)


def gateway_path(link: str) -> str:
    return urlsplit(link).path


def audit_text() -> str:
    return " ".join(json.dumps(entry.diff) for entry in AuditLog.objects.all())


# --- bramki dostępu ---------------------------------------------------------------------------------


def test_without_a_secret_the_screens_do_not_exist(settings):
    settings.JITSI_JWT_APP_SECRET = ""
    coordinator = logged_in(CoordinatorFactory())

    assert coordinator.get(ROOMS).status_code == 404
    assert "Pokoje wideo" not in coordinator.get("/coordinator/").content.decode()
    assert logged_in(issuer().user).get(MY_ROOMS).status_code == 404


def test_coordinator_menu_has_the_screen_when_configured(jitsi):
    content = logged_in(CoordinatorFactory()).get("/coordinator/").content.decode()

    assert "Pokoje wideo" in content


def test_participant_never_reaches_room_screens(jitsi):
    person = ParticipantFactory(user=UserFactory(groups=["participant"]))
    client = logged_in(person.user)

    assert client.get(ROOMS).status_code == 403
    assert client.get(MY_ROOMS).status_code == 403
    assert create(client).status_code == 403


# --- koordynator --------------------------------------------------------------------------------------


def test_coordinator_creates_a_room_and_sees_two_gateway_links(jitsi):
    client = logged_in(CoordinatorFactory())

    response = create(client, validity_days=60, committee_access="on")

    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    room = VideoRoom.objects.get()
    assert room.room_name.startswith("olimpiada-zebranie-komisji-")
    assert room.expires_at - room.created_at == timedelta(days=60)
    assert room.created_as == RoomCreator.COORDINATOR
    content = response.content.decode()
    assert f"/zaproszenie/wideo/{room.host_key}/" in content
    assert f"/zaproszenie/wideo/{room.guest_key}/" in content
    # Żadnej przepustki w linkach – to są adresy platformy, nie Jitsi.
    assert "jwt" not in content
    assert AuditLog.objects.filter(action="video.room_created").exists()
    assert AuditLog.objects.filter(action="video.room_links_viewed").exists()
    assert room.host_key not in audit_text() and room.guest_key not in audit_text()


def test_validity_outside_the_list_is_refused(jitsi):
    client = logged_in(CoordinatorFactory())

    assert create(client, validity_days=90).status_code == 400
    assert create(client, validity_days=3).status_code == 400
    assert not VideoRoom.objects.exists()


def test_list_does_not_embed_links_until_asked(jitsi):
    client = logged_in(CoordinatorFactory())
    create(client)
    room = VideoRoom.objects.get()

    listing = client.get(ROOMS).content.decode()

    assert room.label in listing
    assert room.guest_key not in listing and room.host_key not in listing


def test_coordinator_sees_links_of_a_committee_room_equal_to_the_creators(jitsi):
    member = issuer()
    create(logged_in(member.user), MY_ROOMS)
    room = VideoRoom.objects.get()
    coordinator = logged_in(CoordinatorFactory())

    listing = coordinator.get(ROOMS).content.decode()
    shown_to_coordinator = coordinator.post(f"{ROOMS}{room.pk}/links/")
    shown_to_creator = logged_in(member.user).post(f"{MY_ROOMS}{room.pk}/links/")

    assert "Ewa Recenzentka" in listing
    assert shown_to_coordinator.status_code == shown_to_creator.status_code == 200
    assert "no-store" in shown_to_coordinator["Cache-Control"]
    for key in (room.host_key, room.guest_key):
        assert f"/zaproszenie/wideo/{key}/" in shown_to_coordinator.content.decode()
        assert f"/zaproszenie/wideo/{key}/" in shown_to_creator.content.decode()
    assert AuditLog.objects.filter(action="video.room_links_viewed").count() == 3


def test_coordinator_of_another_competition_gets_404(jitsi, client_for, other_competition):
    from apps.tenancy.tests.factories import grant_membership

    create(logged_in(CoordinatorFactory()))
    room = VideoRoom.objects.get()
    stranger = CoordinatorFactory()
    grant_membership(stranger, other_competition, CompetitionRole.COORDINATOR)
    client = client_for(other_competition)
    client.force_login(stranger)

    assert client.post(f"{ROOMS}{room.pk}/links/").status_code == 404
    assert client.post(f"{ROOMS}{room.pk}/close/").status_code == 404
    assert client.get(f"{ROOMS}{room.pk}/join/").status_code == 404
    assert client.get(f"/zaproszenie/wideo/{room.guest_key}/").status_code == 404


def test_closed_room_shows_no_links_and_the_gateway_refuses(jitsi):
    client = logged_in(CoordinatorFactory())
    create(client, label="Konsultacja z dr X")
    room = VideoRoom.objects.get()
    guest_path = f"/zaproszenie/wideo/{room.guest_key}/"

    assert client.post(f"{ROOMS}{room.pk}/close/").status_code == 302
    room.refresh_from_db()
    shown = client.post(f"{ROOMS}{room.pk}/links/")
    gateway = Client().get(guest_path)
    joined = Client().post(guest_path, {"name": "Gość"})

    assert room.closed_at is not None
    assert shown.status_code == 302
    assert gateway.status_code == 410
    # Etykieta nie jest informacją dla kogoś, kto już nie ma wstępu.
    assert "Konsultacja z dr X" not in gateway.content.decode()
    assert joined.status_code == 410
    assert "Location" not in joined
    assert AuditLog.objects.filter(action="video.room_closed").exists()


def test_expired_room_refuses_at_the_gateway(jitsi):
    create(logged_in(CoordinatorFactory()), validity_days=1)
    room = VideoRoom.objects.get()
    VideoRoom.objects.filter(pk=room.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

    assert Client().post(f"/zaproszenie/wideo/{room.guest_key}/", {"name": "Gość"}).status_code == 410


def test_rotated_link_dies_and_the_new_one_works(jitsi):
    client = logged_in(CoordinatorFactory())
    create(client)
    room = VideoRoom.objects.get()
    old_guest = room.guest_key

    assert client.post(f"{ROOMS}{room.pk}/rotate/", {"link": "guest"}).status_code == 302
    room.refresh_from_db()

    assert room.guest_key != old_guest
    assert Client().get(f"/zaproszenie/wideo/{old_guest}/").status_code == 404
    assert Client().post(f"/zaproszenie/wideo/{room.guest_key}/", {"name": "Gość"}).status_code == 302
    assert old_guest not in audit_text()


def test_coordinator_joins_any_room_as_host(jitsi):
    client = logged_in(CoordinatorFactory(first_name="Anna", last_name="Nowak"))
    create(client)
    room = VideoRoom.objects.get()

    response = client.get(f"{ROOMS}{room.pk}/join/")

    claims = claims_from(response["Location"])
    assert claims["room"] == room.room_name
    assert claims["context"]["user"] == {"name": "Anna N.", "moderator": True}


# --- bramka linku-zaproszenia ---------------------------------------------------------------------------


@pytest.fixture
def room(jitsi):
    create(logged_in(CoordinatorFactory()))
    return VideoRoom.objects.get()


def test_gateway_get_issues_nothing(room):
    response = Client().get(f"/zaproszenie/wideo/{room.guest_key}/")

    assert response.status_code == 200
    assert room.label in response.content.decode()
    assert "jwt" not in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert response["Referrer-Policy"] == "no-referrer"
    assert not AuditLog.objects.filter(action="video.room_joined").exists()


def test_guest_link_gives_a_short_participant_pass(room):
    response = Client().post(f"/zaproszenie/wideo/{room.guest_key}/", {"name": "Jan Gość"})

    assert response.status_code == 302
    assert response["Referrer-Policy"] == "no-referrer"
    claims = claims_from(response["Location"])
    assert response["Location"].startswith(f"https://{HOST}/{room.room_name}#jwt=")
    assert claims["room"] == room.room_name
    assert claims["exp"] - claims["iat"] == 10 * 60
    assert claims["context"]["user"] == {"name": "Jan Gość"}
    entry = AuditLog.objects.get(action="video.room_joined")
    assert entry.diff["role"] == "guest_link" and entry.diff["moderator"] is False
    assert "Jan Gość" not in audit_text()


def test_host_link_gives_moderator(room):
    response = Client().post(f"/zaproszenie/wideo/{room.host_key}/", {"name": "Prowadzący"})

    assert claims_from(response["Location"])["context"]["user"]["moderator"] is True


def test_guest_name_is_cleaned_and_limited(room):
    raw = "‮Admin\x00\x07   Komisji " + "x" * 80

    response = Client().post(f"/zaproszenie/wideo/{room.guest_key}/", {"name": raw})

    name = claims_from(response["Location"])["context"]["user"]["name"]
    assert name.startswith("Admin Komisji ")
    assert len(name) <= 40
    assert "‮" not in name and "\x00" not in name


def test_guest_without_a_name_is_asked_again(room):
    response = Client().post(f"/zaproszenie/wideo/{room.guest_key}/", {"name": "  \x00 "})

    assert response.status_code == 400
    assert "Location" not in response


def test_unknown_key_is_404(room):
    assert Client().get("/zaproszenie/wideo/nie-ma-takiego-klucza/").status_code == 404


@override_settings(REST_FRAMEWORK=rest_framework_with(video_gateway="2/hour"))
def test_gateway_post_is_throttled_per_address(room):
    path = f"/zaproszenie/wideo/{room.guest_key}/"
    client = Client()

    statuses = [client.post(path, {"name": "Gość"}).status_code for _attempt in range(3)]

    assert statuses == [302, 302, 429]


# --- komisja: uprawnienie i własne pokoje ---------------------------------------------------------------


def test_committee_member_without_the_grant_gets_404(jitsi):
    member = ActiveReviewerFactory()

    assert logged_in(member.user).get(MY_ROOMS).status_code == 404
    assert create(logged_in(member.user), MY_ROOMS).status_code == 404


def test_member_with_the_grant_creates_rooms_capped_at_30_days(jitsi):
    client = logged_in(issuer().user)

    assert client.get(MY_ROOMS).status_code == 200
    assert create(client, MY_ROOMS, validity_days=60).status_code == 400
    assert create(client, MY_ROOMS, validity_days=30).status_code == 200
    room = VideoRoom.objects.get()
    assert room.created_as == RoomCreator.COMMITTEE
    assert room.expires_at - room.created_at == timedelta(days=30)


def test_member_sees_and_closes_only_own_rooms(jitsi):
    mine, theirs = issuer(), issuer()
    create(logged_in(theirs.user), MY_ROOMS, label="Cudzy pokoj")
    foreign = VideoRoom.objects.get()
    create(logged_in(CoordinatorFactory()), label="Pokoj koordynatora")
    client = logged_in(mine.user)

    listing = client.get(MY_ROOMS).content.decode()

    assert "Cudzy pokoj" not in listing and "Pokoj koordynatora" not in listing
    assert client.post(f"{MY_ROOMS}{foreign.pk}/links/").status_code == 404
    assert client.post(f"{MY_ROOMS}{foreign.pk}/close/").status_code == 404
    assert client.post(f"{MY_ROOMS}{foreign.pk}/rotate/", {"link": "guest"}).status_code == 404
    foreign.refresh_from_db()
    assert foreign.closed_at is None


def test_member_of_another_competition_cannot_issue_here(jitsi, other_competition):
    stranger = issuer(competition=other_competition)

    # Pod domeną tego konkursu nie jest członkiem komisji – rola odmawia (403), nic nie powstaje.
    assert create(logged_in(stranger.user), MY_ROOMS).status_code == 403
    assert not VideoRoom.objects.exists()


def test_revoking_the_grant_closes_the_screen(jitsi):
    member = issuer()
    client = logged_in(member.user)
    assert client.get(MY_ROOMS).status_code == 200

    response = logged_in(CoordinatorFactory()).post(f"{ROOMS}issuers/{member.pk}/", {"granted": "0"})

    assert response.status_code == 302
    member.refresh_from_db()
    assert member.video_room_issuer is False
    assert client.get(MY_ROOMS).status_code == 404
    assert AuditLog.objects.filter(action="video.issuer_revoked", target_id=str(member.pk)).get().diff == {}


def test_granting_is_audited(jitsi):
    member = ActiveReviewerFactory()

    logged_in(CoordinatorFactory()).post(f"{ROOMS}issuers/{member.pk}/", {"granted": "1"})

    member.refresh_from_db()
    assert member.video_room_issuer is True
    assert AuditLog.objects.filter(action="video.issuer_granted").get().diff == {}


def test_suspended_member_with_the_grant_gets_nothing(jitsi):
    member = issuer()
    member.status = CommitteeStatus.SUSPENDED
    member.save(update_fields=["status"])

    # Zawieszony nie jest już członkiem komisji – bramka roli odmawia, zanim zapyta o uprawnienie.
    assert logged_in(member.user).get(MY_ROOMS).status_code == 403


# --- komisja: pokoje udostępnione, wejście z panelu ------------------------------------------------------


def test_shared_room_is_listed_and_joinable_from_the_reviewer_panel(jitsi):
    create(
        logged_in(CoordinatorFactory()), label="Narada", committee_access="on", committee_as_moderator="on"
    )
    room = VideoRoom.objects.get()
    member = ActiveReviewerFactory()
    client = logged_in(member.user)

    panel = client.get("/review/").content.decode()
    response = client.get(f"{MY_ROOMS}{room.pk}/join/")

    assert "Narada" in panel
    assert f"{MY_ROOMS}{room.pk}/join/" in panel
    assert response.status_code == 302
    assert "no-store" in response["Cache-Control"]
    claims = claims_from(response["Location"])
    assert claims["room"] == room.room_name
    assert claims["context"]["user"]["moderator"] is True
    assert claims["exp"] - claims["iat"] == 180 * 60


def test_shared_room_without_moderator_flag_gives_participant(jitsi):
    create(logged_in(CoordinatorFactory()), committee_access="on")
    room = VideoRoom.objects.get()

    response = logged_in(ActiveReviewerFactory().user).get(f"{MY_ROOMS}{room.pk}/join/")

    assert "moderator" not in claims_from(response["Location"])["context"]["user"]


def test_appeals_member_sees_shared_rooms_too(jitsi):
    create(logged_in(CoordinatorFactory()), label="Narada odwoławcza", committee_access="on")
    member = CommitteeMemberFactory(
        user=UserFactory(groups=[GROUP_APPEALS]), status=CommitteeStatus.ACTIVE, is_appeals_committee=True
    )

    content = logged_in(member.user).get("/appeals/").content.decode()

    assert "Narada odwoławcza" in content


def test_room_not_shared_with_the_committee_is_404(jitsi):
    create(logged_in(CoordinatorFactory()))
    room = VideoRoom.objects.get()

    assert logged_in(ActiveReviewerFactory().user).get(f"{MY_ROOMS}{room.pk}/join/").status_code == 404


def test_member_of_another_competition_gets_404_on_join(jitsi, client_for, other_competition):
    create(logged_in(CoordinatorFactory()), committee_access="on")
    room = VideoRoom.objects.get()
    stranger = ActiveReviewerFactory(competition=other_competition)
    client = client_for(other_competition)
    client.force_login(stranger.user)

    assert client.get(f"{MY_ROOMS}{room.pk}/join/").status_code == 404


def test_participant_cannot_join_committee_rooms(jitsi):
    create(logged_in(CoordinatorFactory()), committee_access="on")
    room = VideoRoom.objects.get()
    person = ParticipantFactory(user=UserFactory(groups=["participant"]))

    assert logged_in(person.user).get(f"{MY_ROOMS}{room.pk}/join/").status_code == 403


def test_closed_shared_room_gives_no_pass(jitsi):
    coordinator = logged_in(CoordinatorFactory())
    create(coordinator, committee_access="on")
    room = VideoRoom.objects.get()
    coordinator.post(f"{ROOMS}{room.pk}/close/")

    response = logged_in(ActiveReviewerFactory().user).get(f"{MY_ROOMS}{room.pk}/join/")

    assert response.status_code == 302
    assert "#jwt=" not in response["Location"]


def test_revoked_creator_stops_panel_joins_but_not_gateway_until_closed(jitsi):
    member = issuer()
    create(logged_in(member.user), MY_ROOMS, committee_access="on")
    room = VideoRoom.objects.get()
    coordinator = logged_in(CoordinatorFactory())
    coordinator.post(f"{ROOMS}issuers/{member.pk}/", {"granted": "0"})
    other_member = logged_in(ActiveReviewerFactory().user)

    panel_join = other_member.get(f"{MY_ROOMS}{room.pk}/join/")
    gateway_before = Client().post(f"/zaproszenie/wideo/{room.guest_key}/", {"name": "Gość"})
    coordinator.post(f"{ROOMS}issuers/{member.pk}/close-rooms/")
    gateway_after = Client().post(f"/zaproszenie/wideo/{room.guest_key}/", {"name": "Gość"})

    assert "#jwt=" not in panel_join["Location"]
    assert gateway_before.status_code == 302
    assert gateway_after.status_code == 410
