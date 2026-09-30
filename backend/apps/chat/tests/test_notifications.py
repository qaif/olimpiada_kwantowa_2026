"""List „masz nową wiadomość”: domyślnie włączony, bez treści, zbijany, tylko do adresów dostarczalnych."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone
from freezegun import freeze_time

from apps.chat import notifications, services
from apps.chat.models import ChatNotificationSettings, ConversationMember, PeerMode
from apps.chat.tests.helpers import configure, coordinator_of, participant_of, start

pytestmark = pytest.mark.django_db

SECRET = "Tajna treść wiadomości 42"


def inbox(user) -> list:
    return [message for message in mail.outbox if user.email in message.to]


@pytest.fixture
def peers(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    return participant_of(competition), participant_of(competition, "Ola", "Nowak")


def test_recipient_gets_a_mail_without_the_message_body(
    competition, peers, django_capture_on_commit_callbacks
):
    ala, ola = peers

    with django_capture_on_commit_callbacks(execute=True):
        start(ala, ola, competition, body=SECRET)

    [letter] = inbox(ola.user)
    assert SECRET not in letter.body
    assert SECRET not in letter.subject
    assert "Ala K." in letter.body
    assert "/me/messages/" in letter.body
    assert "#wiadomosci" in letter.body
    assert "Nowa wiadomość" in letter.subject
    assert inbox(ala.user) == []


def test_mail_can_be_switched_off(competition, peers, django_capture_on_commit_callbacks):
    ala, ola = peers
    notifications.save_preferences(ola.user, email_on_message=False)

    with django_capture_on_commit_callbacks(execute=True):
        start(ala, ola, competition)

    assert inbox(ola.user) == []


def test_default_is_on_without_a_row(competition, peers):
    _ala, ola = peers

    assert not ChatNotificationSettings.objects.filter(user=ola.user).exists()
    assert notifications.preferences_for(ola.user).email_on_message is True


def test_undeliverable_address_gets_nothing(competition, peers, django_capture_on_commit_callbacks):
    ala, ola = peers
    ola.user.email_verified_at = None
    ola.user.save(update_fields=["email_verified_at"])

    with django_capture_on_commit_callbacks(execute=True):
        start(ala, ola, competition)

    assert inbox(ola.user) == []


def test_three_hour_batching_per_conversation(competition, peers, django_capture_on_commit_callbacks):
    ala, ola = peers
    now = timezone.now()
    with freeze_time(now), django_capture_on_commit_callbacks(execute=True):
        conversation = start(ala, ola, competition).conversation
        services.send_participant_message(
            user=ala.user, competition=competition, conversation=conversation, body="2"
        )
    assert len(inbox(ola.user)) == 1

    # Odbiorca otworzył wątek, kolejna wiadomość – ale w oknie trzech godzin od listu: nadal jeden.
    with freeze_time(now + timedelta(hours=1)):
        services.mark_read(ConversationMember.objects.get(conversation=conversation, participant=ola))
    with freeze_time(now + timedelta(hours=2)), django_capture_on_commit_callbacks(execute=True):
        services.send_participant_message(
            user=ala.user, competition=competition, conversation=conversation, body="3"
        )
    assert len(inbox(ola.user)) == 1

    # Po oknie, a wątek był otwarty po poprzednim liście – nowy list.
    with freeze_time(now + timedelta(hours=4)), django_capture_on_commit_callbacks(execute=True):
        services.send_participant_message(
            user=ala.user, competition=competition, conversation=conversation, body="4"
        )
    assert len(inbox(ola.user)) == 2

    # Po kolejnym oknie, ale odbiorca **nie** otworzył wątku po ostatnim liście: ten list nadal czeka
    # w jego skrzynce, więc drugiego o tej samej rozmowie nie wysyłamy.
    with freeze_time(now + timedelta(hours=9)), django_capture_on_commit_callbacks(execute=True):
        services.send_participant_message(
            user=ala.user, competition=competition, conversation=conversation, body="5"
        )
    assert len(inbox(ola.user)) == 2


def test_pre_moderation_mails_only_after_approval(competition, django_capture_on_commit_callbacks):
    configure(competition, peer_mode=PeerMode.PRE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    boss = coordinator_of(competition)

    with django_capture_on_commit_callbacks(execute=True):
        message = start(ala, ola, competition)
    assert inbox(ola.user) == []

    with django_capture_on_commit_callbacks(execute=True):
        services.approve(message=message, actor=boss)
    assert len(inbox(ola.user)) == 1


def test_message_to_the_organizer_mails_every_coordinator_by_own_setting(
    competition, django_capture_on_commit_callbacks
):
    ala = participant_of(competition)
    boss, quiet = coordinator_of(competition), coordinator_of(competition)
    notifications.save_preferences(quiet, email_on_message=False)

    with django_capture_on_commit_callbacks(execute=True):
        services.write_to_organizer(user=ala.user, competition=competition, body=SECRET)

    [letter] = inbox(boss)
    assert SECRET not in letter.body
    assert "/coordinator/chat/" in letter.body
    assert inbox(quiet) == []
    assert inbox(ala.user) == []


def test_organizer_reply_is_signed_as_the_organizer(competition, django_capture_on_commit_callbacks):
    ala = participant_of(competition)
    boss = coordinator_of(competition)

    with django_capture_on_commit_callbacks(execute=True):
        services.organizer_writes_to(user=boss, competition=competition, participant=ala, body=SECRET)

    [letter] = inbox(ala.user)
    assert "od Organizator" in letter.body
    assert SECRET not in letter.body
    assert inbox(boss) == []
