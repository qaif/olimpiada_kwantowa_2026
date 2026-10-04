"""Punkt rozszerzenia „polityka rozmowy” (ALUM-01 § 5.3) – czat bez wiedzy o mentoringu.

Dostawcę polityki podstawiamy w teście (``monkeypatch`` na rejestrze), żeby sprawdzić **czat**,
a nie ``apps.alumni``: rozmowa bez polityki ma się zachowywać dokładnie jak dotąd, a polityka
może wymusić tryb, podnieść podłogę, zdjąć regułę grupy wiekowej (tylko z ``PRE``) albo zamknąć
rozmowę do odczytu.
"""

from __future__ import annotations

import pytest

from apps.chat import services
from apps.chat.models import AgePolicy, ChatSettings, Conversation, MessageStatus, PeerMode
from apps.chat.tests.helpers import configure, coordinator_of, open_stage, participant_of, start
from apps.core.api import DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def policy(monkeypatch):
    """``policy(conversation_pk, PeerPolicy)`` – rejestr z jednym dostawcą tylko dla tej rozmowy."""
    rules: dict[int, services.PeerPolicy] = {}
    monkeypatch.setattr(services, "_PEER_POLICIES", [lambda conversation: rules.get(conversation.pk)])

    def set_rule(pk: int, rule: services.PeerPolicy):
        rules[pk] = rule

    return set_rule


def adult_and_minor(competition):
    adult = participant_of(competition, "Ola", "Dorosła", birth_year=1990)
    minor = participant_of(competition, "Tymek", "Młody", birth_year=2012)
    return adult, minor


def test_conversation_without_policy_follows_competition_mode(competition, policy):  # noqa: ARG001
    configure(competition, peer_mode=PeerMode.NONE)
    a = participant_of(competition, "Ala")
    b = participant_of(competition, "Bartek")

    message = start(a, b, competition)

    assert message.status == MessageStatus.PUBLISHED
    assert services.peer_policy(message.conversation) is None


def test_forced_pre_holds_every_message_for_the_organizer(competition, policy):
    configure(competition, peer_mode=PeerMode.NONE)
    a = participant_of(competition, "Ala")
    b = participant_of(competition, "Bartek")
    conversation, existed = services.ensure_peer_conversation(a, b)
    policy(conversation.pk, services.PeerPolicy(mode=PeerMode.PRE))

    message = services.send_participant_message(
        user=a.user, competition=competition, conversation=conversation, body="Cześć"
    )

    assert existed is False
    assert message.status == MessageStatus.PENDING
    assert message.moderation_mode == PeerMode.PRE


def test_forced_pre_opens_even_when_peer_chat_is_off(competition, policy):
    configure(competition, peer_mode=PeerMode.OFF)
    a = participant_of(competition, "Ala")
    b = participant_of(competition, "Bartek")
    conversation, _existed = services.ensure_peer_conversation(a, b)

    with pytest.raises(DomainError):
        services.send_participant_message(
            user=a.user, competition=competition, conversation=conversation, body="x"
        )

    policy(conversation.pk, services.PeerPolicy(mode=PeerMode.PRE))
    message = services.send_participant_message(
        user=a.user, competition=competition, conversation=conversation, body="Cześć"
    )
    assert message.status == MessageStatus.PENDING


def test_skip_age_policy_only_with_pre(competition, policy):
    """Reguła grupy wiekowej ustępuje wyłącznie świadkowi przed doręczeniem (tryb ``PRE``)."""
    configure(competition, peer_mode=PeerMode.NONE)
    ChatSettings.objects.filter(competition=competition).update(age_policy=AgePolicy.SAME_GROUP)
    adult, minor = adult_and_minor(competition)
    conversation, _existed = services.ensure_peer_conversation(adult, minor)

    # Bez polityki – rozmowa dorosły–małoletni jest zamknięta.
    assert services.peer_write_refusal(conversation, adult, mode=PeerMode.NONE) == services.AGE_CLOSED

    # Zdjęcie reguły bez wymuszenia PRE niczego nie otwiera.
    policy(conversation.pk, services.PeerPolicy(skip_age_policy=True))
    with pytest.raises(DomainError):
        services.send_participant_message(
            user=adult.user, competition=competition, conversation=conversation, body="x"
        )

    policy(conversation.pk, services.PeerPolicy(mode=PeerMode.PRE, skip_age_policy=True))
    message = services.send_participant_message(
        user=adult.user, competition=competition, conversation=conversation, body="Dzień dobry"
    )
    assert message.status == MessageStatus.PENDING

    # Akceptacja w kolejce przechodzi tę samą regułę – wiadomość dochodzi do małoletniego.
    coordinator = coordinator_of(competition)
    approved = services.approve(message=message, actor=coordinator)
    assert approved.status == MessageStatus.PUBLISHED


def test_floor_raises_none_to_post_and_keeps_stricter_modes(competition, policy):
    configure(competition, peer_mode=PeerMode.NONE)
    a = participant_of(competition, "Ala")
    b = participant_of(competition, "Bartek")
    conversation, _existed = services.ensure_peer_conversation(a, b)
    policy(conversation.pk, services.PeerPolicy(at_least=PeerMode.POST))

    assert services.conversation_mode_and_stage(conversation)[0] == PeerMode.POST
    message = services.send_participant_message(
        user=a.user, competition=competition, conversation=conversation, body="Hej"
    )
    assert message.status == MessageStatus.PUBLISHED
    assert message.moderation_mode == PeerMode.POST
    assert services.review_messages(competition).filter(pk=message.pk).exists()

    # Trwa etap przyjmujący rozwiązania – PRE z etapem zostaje (podłoga nie łagodzi).
    stage = open_stage(competition)
    mode, forcing = services.conversation_mode_and_stage(conversation)
    assert mode == PeerMode.PRE
    assert forcing == stage


def test_refusal_makes_conversation_read_only_and_withdraws_pending(competition, policy):
    configure(competition, peer_mode=PeerMode.PRE)
    a = participant_of(competition, "Ala")
    b = participant_of(competition, "Bartek")
    conversation, _existed = services.ensure_peer_conversation(a, b)
    pending = services.send_participant_message(
        user=a.user, competition=competition, conversation=conversation, body="Czekam"
    )
    policy(conversation.pk, services.PeerPolicy(refusal="Relacja zakończona."))

    with pytest.raises(DomainError) as exc:
        services.send_participant_message(
            user=a.user, competition=competition, conversation=conversation, body="x"
        )
    assert str(exc.value.detail) == "Relacja zakończona."

    result = services.approve(message=pending, actor=coordinator_of(competition))
    assert result.status == MessageStatus.REJECTED


def test_ensure_peer_conversation_skips_directory_and_daily_limit(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ChatSettings.objects.filter(competition=competition).update(daily_new_conversations=1)
    a = participant_of(competition, "Ala")
    others = [participant_of(competition, f"Osoba{i}") for i in range(3)]

    for other in others:
        conversation, existed = services.ensure_peer_conversation(a, other)
        assert existed is False
        assert conversation.started_by_id is None
        assert conversation.is_encrypted is False

    again, existed = services.ensure_peer_conversation(others[0], a)
    assert existed is True
    assert Conversation.objects.filter(participant_low__in=[a, *others]).count() == 3
    # Rozmowy założone spoza katalogu nie zjadają dziennego limitu – nadal można zacząć jedną z katalogu.
    fresh = participant_of(competition, "Nowy")
    assert start(a, fresh, competition).status == MessageStatus.PUBLISHED


def test_ensure_peer_conversation_refuses_self_and_cross_competition(competition, other_competition):
    a = participant_of(competition, "Ala")
    stranger = participant_of(other_competition, "Obcy")

    with pytest.raises(DomainError):
        services.ensure_peer_conversation(a, a)
    with pytest.raises(DomainError):
        services.ensure_peer_conversation(a, stranger)
