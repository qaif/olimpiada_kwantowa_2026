"""Poprawki po przeglądzie CZ-01: historia szyfrowana po usunięciu konta, odczyt tylko z widocznej
karty, akceptacja nie omija blokady, kolejka bez zapytania na pozycję, bramka ustawień, eksport
zgłoszeń i brak listu do kogoś, kto właśnie czyta rozmowę."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import UserFactory
from apps.chat import services
from apps.chat.models import (
    ChatBlock,
    ConversationMember,
    Message,
    MessageStatus,
    PeerMode,
)
from apps.chat.tests.helpers import (
    configure,
    coordinator_of,
    fake_ciphertext,
    give_key,
    in_directory,
    participant_of,
    start,
)
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

SEEN = {"HTTP_X_CHAT_SEEN": "1", "HTTP_HX_REQUEST": "true"}
BACKGROUND = {"HTTP_HX_REQUEST": "true"}


def peers(competition, mode=PeerMode.NONE, **settings):
    configure(competition, peer_mode=mode, **settings)
    return participant_of(competition), participant_of(competition, "Ola", "Nowak")


# --- H1: historia szyfrowana po twardym usunięciu konta nadawcy -------------------------------------


def test_encrypted_history_survives_hard_delete_of_the_sender(web_client, competition):
    ala, ola = peers(competition, e2e_enabled=True)
    give_key(ala)
    give_key(ola)
    in_directory(ola)
    conversation = services.open_encrypted_conversation(
        user=ala.user, competition=competition, token=services.profile_for(ola).token
    )
    sender_key = services.key_for(ala).public_key
    message = services.send_participant_message(
        user=ala.user,
        competition=competition,
        conversation=conversation,
        sender_fingerprint=services.key_for(ala).fingerprint,
        recipient_fingerprint=services.key_for(ola).fingerprint,
        **fake_ciphertext(),
    )

    ala.user.delete()

    message.refresh_from_db()
    web_client.force_login(ola.user)
    body = web_client.get(f"/me/messages/{conversation.pk}/").content.decode()
    assert message.sender_id is None
    # Wszystko, czego przeglądarka potrzebuje do AAD i klucza rozmowy, stoi nadal na wiadomości.
    assert f'data-sender-key="{sender_key}"' in body
    assert f'data-ciphertext="{message.ciphertext}"' in body
    assert "data-sender-id" not in body
    assert "Użytkownik usunięty" in body


# --- M1: odczyt tylko z widocznej karty --------------------------------------------------------------


def test_background_polling_does_not_mark_the_thread_read(web_client, competition):
    ala = participant_of(competition)
    boss = coordinator_of(competition)
    conversation = services.organizer_writes_to(
        user=boss, competition=competition, participant=ala, body="Odpowiedź"
    ).conversation
    web_client.force_login(ala.user)
    url = f"/me/messages/{conversation.pk}/?fragment=messages"

    web_client.get(url, **BACKGROUND)
    assert services.participant_unread_count(ala) == 1

    web_client.get(url, **SEEN)
    assert services.participant_unread_count(ala) == 0


def test_full_page_visit_still_marks_read(web_client, competition):
    ala = participant_of(competition)
    boss = coordinator_of(competition)
    conversation = services.organizer_writes_to(
        user=boss, competition=competition, participant=ala, body="Odpowiedź"
    ).conversation
    web_client.force_login(ala.user)

    web_client.get(f"/me/messages/{conversation.pk}/")

    assert services.participant_unread_count(ala) == 0


def test_coordinator_background_tab_does_not_clear_the_team_badge(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation
    web_client.force_login(coordinator_of(competition))
    url = f"/coordinator/chat/{conversation.pk}/?fragment=messages"

    web_client.get(url, **BACKGROUND)
    assert services.organizer_unread_count(competition) == 1

    web_client.get(url, **SEEN)
    assert services.organizer_unread_count(competition) == 0


def test_the_messages_fragment_polls_again_when_the_tab_comes_back(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(user=ala.user, competition=competition, body="x").conversation
    web_client.force_login(ala.user)

    body = web_client.get(f"/me/messages/{conversation.pk}/").content.decode()

    assert 'hx-trigger="every 15s, chat-visible from:document"' in body
    assert "js/chat.js" in body


# --- M2: akceptacja nie omija blokady ------------------------------------------------------------------


def test_blocking_withdraws_pending_messages_of_the_blocked_person(competition):
    ala, ola = peers(competition, PeerMode.PRE)
    pending = start(ala, ola, competition, body="Czekam")

    services.block(participant=ola, conversation=pending.conversation)

    pending.refresh_from_db()
    assert pending.status == MessageStatus.REJECTED
    assert pending.moderation_note == services.UNDELIVERABLE_NOTE
    assert "zablok" not in pending.moderation_note.lower()


def test_approval_after_a_block_does_not_deliver(competition):
    ala, ola = peers(competition, PeerMode.PRE)
    boss = coordinator_of(competition)
    pending = start(ala, ola, competition, body="Czekam")
    ChatBlock.objects.create(blocker=ola, blocked=ala)

    result = services.approve(message=pending, actor=boss)

    assert result.status == MessageStatus.REJECTED
    assert list(services.visible_messages(pending.conversation, ola)) == []
    assert services.participant_unread_count(ola) == 0
    assert services.bulk_approve(competition=competition, actor=boss, ids=[pending.pk]) == 0


def test_approval_after_switching_the_channel_off_does_not_deliver(competition):
    ala, ola = peers(competition, PeerMode.PRE)
    boss = coordinator_of(competition)
    pending = start(ala, ola, competition)
    configure(competition, peer_mode=PeerMode.OFF)

    assert services.approve(message=pending, actor=boss).status == MessageStatus.REJECTED


def test_the_screen_says_the_message_could_not_be_delivered(web_client, competition):
    ala, ola = peers(competition, PeerMode.PRE)
    pending = start(ala, ola, competition)
    ChatBlock.objects.create(blocker=ola, blocked=ala)
    web_client.force_login(coordinator_of(competition))

    response = web_client.post(
        "/coordinator/chat/moderation/", {"action": "approve", "message": pending.pk}, follow=True
    )

    assert "nie można już doręczyć" in response.content.decode()


# --- M3: kolejka moderacji bez zapytania na pozycję -----------------------------------------------------


def queue_of(competition, conversations: int):
    configure(competition, peer_mode=PeerMode.PRE)
    for index in range(conversations):
        sender = participant_of(competition, f"Nadawca{index}", "Test")
        recipient = participant_of(competition, f"Odbiorca{index}", "Test")
        first = start(sender, recipient, competition, body=f"pierwsza {index}")
        services.send_participant_message(
            user=sender.user, competition=competition, conversation=first.conversation, body=f"druga {index}"
        )


def measure(client) -> int:
    client.get("/coordinator/chat/moderation/")
    with CaptureQueriesContext(connection) as queries:
        assert client.get("/coordinator/chat/moderation/").status_code == 200
    return len(queries)


def test_moderation_queue_cost_does_not_grow_with_the_queue(web_client, competition):
    boss = coordinator_of(competition)
    web_client.force_login(boss)
    queue_of(competition, 1)
    small = measure(web_client)

    queue_of(competition, 4)
    large = measure(web_client)

    assert Message.objects.filter(status=MessageStatus.PENDING).count() == 10
    assert large == small


def test_batched_context_equals_the_single_one(competition):
    queue_of(competition, 2)
    queue = list(services.pending_messages(competition))

    batched = services.moderation_contexts(queue)

    for message in queue:
        assert batched[message.pk] == services.moderation_context(message)
    assert any(batched[message.pk] for message in queue)


# --- L3: bramka ustawień -----------------------------------------------------------------------------


def test_preferences_endpoint_refuses_other_roles(web_client, competition):
    reviewer = UserFactory(groups=[CompetitionRole.REVIEWER])
    grant_membership(reviewer, competition, CompetitionRole.REVIEWER)
    web_client.force_login(reviewer)

    assert web_client.post("/account/chat-settings/", {"email_on_message": "on"}).status_code == 403


# --- L5: eksport zgłoszeń -------------------------------------------------------------------------------


def test_export_lists_own_reports_without_the_reported_content(web_client, competition):
    from apps.web.tests.test_account_export import payload

    ala, ola = peers(competition)
    message = start(ala, ola, competition, body="TRESC-ZGLOSZONA")
    services.report_message(user=ola.user, competition=competition, message=message, reason="Obraża mnie")
    web_client.force_login(ola.user)

    data = payload(web_client.get("/account/export/"))

    [report] = data["zgloszenia_wiadomosci"]
    assert report["powod"] == "Obraża mnie"
    assert report["stan"] == "czeka na organizatora"
    assert "TRESC-ZGLOSZONA" not in str(data)


# --- L6: brak listu do kogoś, kto właśnie czyta ---------------------------------------------------------


def test_no_mail_to_someone_who_just_read_the_thread(competition, django_capture_on_commit_callbacks):
    ala, ola = peers(competition)
    conversation = start(ala, ola, competition).conversation
    member = ConversationMember.objects.get(conversation=conversation, participant=ola)
    member.last_read_at = timezone.now() - timedelta(minutes=5)
    member.notified_at = None
    member.save(update_fields=["last_read_at", "notified_at"])
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        services.send_participant_message(
            user=ala.user, competition=competition, conversation=conversation, body="2"
        )

    assert [letter for letter in mail.outbox if ola.user.email in letter.to] == []
