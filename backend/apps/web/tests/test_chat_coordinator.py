"""Wiadomości w panelu koordynatora: skrzynka, wątek, „Napisz wiadomość”, moderacja i ustawienia.

Dwie rzeczy, o które chodzi tu najbardziej:

- **koordynator nie ma drogi do treści rozmów uczestników poza kolejką** – wątek pod
  ``/coordinator/chat/<id>/`` otwiera wyłącznie rozmowę organizatorską, a w trybie „bez moderacji”
  niezgłoszona wiadomość nie pojawia się nigdzie w panelu,
- **każda decyzja zostawia ślad w audycie – bez treści wiadomości.**
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import UserFactory
from apps.chat import services
from apps.chat.models import ChatSettings, Message, MessageStatus, PeerMode
from apps.chat.tests.helpers import (
    configure,
    coordinator_of,
    fake_ciphertext,
    give_key,
    in_directory,
    participant_of,
    start,
)
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

INBOX = "/coordinator/chat/"
MODERATION = "/coordinator/chat/moderation/"
SETTINGS = "/coordinator/chat/settings/"


def thread_url(conversation) -> str:
    return f"/coordinator/chat/{conversation.pk}/"


def as_coordinator(client, competition):
    user = coordinator_of(competition)
    client.force_login(user)
    return user


# --- bramki ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [
        CompetitionRole.PARTICIPANT,
        CompetitionRole.REVIEWER,
        CompetitionRole.SUPERVISOR,
        CompetitionRole.APPEALS,
    ],
)
@pytest.mark.parametrize(
    "url", [INBOX, MODERATION, SETTINGS, "/coordinator/chat/1/", "/coordinator/chat/new/1/"]
)
def test_everyone_but_the_coordinator_gets_403(web_client, competition, role, url):
    user = UserFactory(groups=[role])
    grant_membership(user, competition, role)
    web_client.force_login(user)

    assert web_client.get(url).status_code == 403
    assert web_client.post(url, {"action": "approve"}).status_code == 403


def test_disabled_module_leads_the_coordinator_to_the_settings(web_client, competition):
    configure(competition, enabled=False)
    as_coordinator(web_client, competition)

    response = web_client.get(INBOX)

    assert response.status_code == 302
    assert response["Location"] == SETTINGS
    assert web_client.get(SETTINGS).status_code == 200


# --- skrzynka i wątek --------------------------------------------------------------------------------


def test_inbox_shows_full_participant_data_and_the_unread_filter(web_client, competition):
    ala = participant_of(competition, "Alicja", "Pełnonazwiskowa")
    services.write_to_organizer(user=ala.user, competition=competition, body="Pytanie o salę")
    as_coordinator(web_client, competition)

    body = web_client.get(INBOX).content.decode()
    unread = web_client.get(f"{INBOX}?filter=unread").content.decode()

    assert "Alicja Pełnonazwiskowa" in body
    assert ala.public_code in body
    assert "Pytanie o salę" in unread


def test_opening_the_thread_clears_the_badge_for_the_whole_team(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation
    as_coordinator(web_client, competition)

    before = web_client.get("/coordinator/").content.decode()
    web_client.get(thread_url(conversation))
    after_count = services.coordinator_attention(competition)

    assert "Wiadomości" in before
    assert services.organizer_unread_count(competition) == 0
    assert after_count == 0


def test_reply_by_htmx_returns_the_fragment_signed_by_the_organizer(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation
    boss = as_coordinator(web_client, competition)

    response = web_client.post(thread_url(conversation), {"body": "Sala 101"}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert "Sala 101" in response.content.decode()
    body = response.content.decode()
    # Od pakietu 5 rola jest odznaką, a imię – osobnym elementem obok (``chat.organizer_author``).
    assert 'data-sender-role="organizer">Organizator</span>' in body
    assert f'<span class="chat-msg__author">{boss.first_name}' in body


def test_peer_conversation_is_not_reachable_from_the_panel(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    conversation = start(ala, ola, competition, body="Prywatne").conversation
    as_coordinator(web_client, competition)

    assert web_client.get(thread_url(conversation)).status_code == 404
    assert web_client.post(thread_url(conversation), {"body": "x"}).status_code == 404


def test_conversation_of_another_competition_is_404(web_client, competition, other_competition):
    stranger = participant_of(other_competition, "Obca", "Osoba")
    foreign = services.write_to_organizer(
        user=stranger.user, competition=other_competition, body="x"
    ).conversation
    as_coordinator(web_client, competition)

    assert web_client.get(thread_url(foreign)).status_code == 404
    assert web_client.get(f"/coordinator/chat/new/{stranger.pk}/").status_code == 404


def test_write_from_the_participant_card(web_client, competition):
    ala = participant_of(competition)
    as_coordinator(web_client, competition)
    new_url = f"/coordinator/chat/new/{ala.pk}/"

    card = web_client.get(f"/coordinator/participants/{ala.pk}/").content.decode()
    assert web_client.get(new_url).status_code == 200
    response = web_client.post(new_url, {"body": "Dzień dobry, mamy pytanie."})

    message = Message.objects.get()
    assert f'href="{new_url}"' in card
    assert response["Location"] == thread_url(message.conversation)
    assert web_client.get(new_url)["Location"] == thread_url(message.conversation)


# --- moderacja -----------------------------------------------------------------------------------------


def test_pre_queue_approve_and_reject(web_client, competition):
    configure(competition, peer_mode=PeerMode.PRE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    first = start(ala, ola, competition, body="PIERWSZA-CZEKA")
    second = services.send_participant_message(
        user=ala.user, competition=competition, conversation=first.conversation, body="DRUGA-CZEKA"
    )
    as_coordinator(web_client, competition)

    page = web_client.get(MODERATION).content.decode()
    web_client.post(MODERATION, {"action": "approve", "message": first.pk})
    web_client.post(MODERATION, {"action": "reject", "message": second.pk, "note": ""})
    second.refresh_from_db()
    assert second.status == MessageStatus.PENDING
    web_client.post(MODERATION, {"action": "reject", "message": second.pk, "note": "Bez rozwiązań."})

    first.refresh_from_db()
    second.refresh_from_db()
    assert "PIERWSZA-CZEKA" in page and "DRUGA-CZEKA" in page
    assert first.status == MessageStatus.PUBLISHED
    assert second.status == MessageStatus.REJECTED
    for entry in AuditLog.objects.filter(action__startswith="chat.message_"):
        assert "CZEKA" not in str(entry.diff)


def test_bulk_approve(web_client, competition):
    configure(competition, peer_mode=PeerMode.PRE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    first = start(ala, ola, competition)
    second = services.send_participant_message(
        user=ala.user, competition=competition, conversation=first.conversation, body="2"
    )
    as_coordinator(web_client, competition)

    web_client.post(MODERATION, {"action": "bulk-approve", "message": [first.pk, second.pk]})

    assert set(Message.objects.values_list("status", flat=True)) == {MessageStatus.PUBLISHED}


def test_post_queue_review_and_hide(web_client, competition):
    configure(competition, peer_mode=PeerMode.POST)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    first = start(ala, ola, competition, body="DO-PRZEJRZENIA")
    second = services.send_participant_message(
        user=ala.user, competition=competition, conversation=first.conversation, body="DO-UKRYCIA"
    )
    as_coordinator(web_client, competition)

    assert "DO-PRZEJRZENIA" in web_client.get(MODERATION).content.decode()
    web_client.post(MODERATION, {"action": "reviewed", "message": first.pk})
    web_client.post(MODERATION, {"action": "hide", "message": second.pk})

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.reviewed_at is not None
    assert second.status == MessageStatus.HIDDEN
    assert "Kolejka jest pusta" in web_client.get(MODERATION).content.decode()


def test_no_moderation_mode_shows_only_reported_messages(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    quiet = start(ala, ola, competition, body="NIEZGLOSZONA-TRESC")
    loud = services.send_participant_message(
        user=ala.user, competition=competition, conversation=quiet.conversation, body="ZGLOSZONA-TRESC"
    )
    as_coordinator(web_client, competition)

    empty = web_client.get(MODERATION).content.decode()
    services.report_message(user=ola.user, competition=competition, message=loud, reason="Spam")
    with_report = web_client.get(MODERATION).content.decode()
    hidden_attempt = web_client.post(MODERATION, {"action": "hide", "message": quiet.pk})

    assert "NIEZGLOSZONA-TRESC" not in empty
    assert "Kolejka jest pusta" in empty
    assert "ZGLOSZONA-TRESC" in with_report
    assert "NIEZGLOSZONA-TRESC" not in with_report
    assert hidden_attempt.status_code == 404


def test_encrypted_report_shows_the_forwarded_copy_with_a_caveat(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    give_key(ala)
    give_key(ola)
    in_directory(ola)
    conversation = services.open_encrypted_conversation(
        user=ala.user, competition=competition, token=services.profile_for(ola).token
    )
    message = services.send_participant_message(
        user=ala.user,
        competition=competition,
        conversation=conversation,
        sender_fingerprint=services.key_for(ala).fingerprint,
        recipient_fingerprint=services.key_for(ola).fingerprint,
        **fake_ciphertext(),
    )
    services.report_message(
        user=ola.user,
        competition=competition,
        message=message,
        reason="Groźby",
        reported_plaintext="KOPIA-JAWNA",
    )
    as_coordinator(web_client, competition)

    body = web_client.get(MODERATION).content.decode()

    assert "KOPIA-JAWNA" in body
    assert "serwer nie może potwierdzić jej autentyczności" in body
    assert message.ciphertext not in body


# --- ustawienia ------------------------------------------------------------------------------------------


def test_settings_are_saved_and_audited(web_client, competition):
    as_coordinator(web_client, competition)

    response = web_client.post(SETTINGS, {"enabled": "on", "peer_mode": "POST"})

    row = ChatSettings.objects.get(competition=competition)
    assert response.status_code == 302
    assert (row.enabled, row.peer_mode, row.e2e_enabled) == (True, PeerMode.POST, False)
    assert AuditLog.objects.filter(action=services.AUDIT_SETTINGS_CHANGED).exists()


def test_e2e_with_moderation_is_refused_on_the_screen(web_client, competition):
    as_coordinator(web_client, competition)

    response = web_client.post(SETTINGS, {"enabled": "on", "peer_mode": "PRE", "e2e_enabled": "on"})

    assert response.status_code == 400
    assert "tylko w trybie" in response.content.decode()
    assert not ChatSettings.objects.filter(competition=competition, e2e_enabled=True).exists()


def test_settings_screen_explains_the_forced_pre_moderation(web_client, competition):
    from apps.chat.tests.helpers import open_stage

    configure(competition, peer_mode=PeerMode.NONE)
    stage = open_stage(competition)
    as_coordinator(web_client, competition)

    body = web_client.get(SETTINGS).content.decode()

    assert stage.display_name in body
    assert "premoderacja" in body
