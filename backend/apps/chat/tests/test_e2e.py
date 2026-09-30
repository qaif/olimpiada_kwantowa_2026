"""Szyfrowanie end-to-end rozmów między uczestnikami (§ 11 zadania) – reguły serwera.

Serwer nie widzi treści, więc sprawdza to, co może: kiedy szyfrowanie wolno włączyć, kiedy rozmowa
szyfrowana przyjmuje wiadomości, jaki kształt ma klucz i szyfrogram, i że jawna treść nie wejdzie
tam, gdzie ma być szyfrogram (ani odwrotnie). Kryptografię sprawdza ``backend/js_tests``.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from django.db import IntegrityError, transaction

from apps.chat import services
from apps.chat.models import ChatKey, Conversation, ConversationKind, MessageStatus, PeerMode
from apps.chat.tests.helpers import (
    configure,
    coordinator_of,
    fake_ciphertext,
    give_key,
    in_directory,
    key_fields,
    open_stage,
    participant_of,
)
from apps.core.api import DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def pair(competition):
    """Dwoje uczestników z kluczami, w katalogu, przy włączonym szyfrowaniu."""
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    give_key(ala)
    give_key(ola)
    in_directory(ola)
    return ala, ola


def open_conversation(ala, ola, competition) -> Conversation:
    return services.open_encrypted_conversation(
        user=ala.user, competition=competition, token=services.profile_for(ola).token
    )


def send_encrypted(sender, other, conversation, competition, **overrides):
    payload = {
        **fake_ciphertext(),
        "sender_fingerprint": services.key_for(sender).fingerprint,
        "recipient_fingerprint": services.key_for(other).fingerprint,
        **overrides,
    }
    return services.send_participant_message(
        user=sender.user, competition=competition, conversation=conversation, **payload
    )


# --- włączanie ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [PeerMode.OFF, PeerMode.PRE, PeerMode.POST])
def test_e2e_only_with_no_moderation(competition, mode):
    boss = coordinator_of(competition)

    with pytest.raises(DomainError) as exc:
        services.save_settings(
            competition=competition, actor=boss, enabled=True, peer_mode=mode, e2e_enabled=True
        )

    assert exc.value.machine_code == "CHAT_E2E_NEEDS_NONE"


def test_changing_the_mode_with_e2e_on_asks_to_switch_it_off_first(competition):
    boss = coordinator_of(competition)
    services.save_settings(
        competition=competition, actor=boss, enabled=True, peer_mode="NONE", e2e_enabled=True
    )

    with pytest.raises(DomainError) as exc:
        services.save_settings(
            competition=competition, actor=boss, enabled=True, peer_mode="PRE", e2e_enabled=True
        )
    assert exc.value.machine_code == "CHAT_E2E_BLOCKS_MODE"
    assert "Najpierw wyłącz szyfrowanie" in str(exc.value.detail)

    row = services.save_settings(
        competition=competition, actor=boss, enabled=True, peer_mode="PRE", e2e_enabled=False
    )
    assert (row.peer_mode, row.e2e_enabled) == (PeerMode.PRE, False)


# --- klucz -----------------------------------------------------------------------------------------------


def test_valid_key_is_stored_with_a_server_side_fingerprint(competition):
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    ala = participant_of(competition)
    fields = key_fields()

    row = services.save_key(user=ala.user, competition=competition, **fields)

    import hashlib

    assert row.fingerprint == hashlib.sha256(base64.b64decode(fields["public_key"])).hexdigest()


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"public_key": "nie-base64!"}, "CHAT_E2E_BAD_FIELD"),
        ({"wrap_iv": base64.b64encode(b"x" * 16).decode()}, "CHAT_E2E_BAD_SIZE"),
        ({"kdf_salt": base64.b64encode(b"x" * 4).decode()}, "CHAT_E2E_BAD_SIZE"),
        ({"kdf_iterations": 100_000}, "CHAT_E2E_WEAK_KDF"),
        ({"wrapped_private_key": base64.b64encode(b"x" * 10).decode()}, "CHAT_E2E_BAD_SIZE"),
    ],
)
def test_malformed_key_fields_are_rejected(competition, override, code):
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    ala = participant_of(competition)

    with pytest.raises(DomainError) as exc:
        services.save_key(user=ala.user, competition=competition, **{**key_fields(), **override})

    assert exc.value.machine_code == code
    assert not ChatKey.objects.exists()


def test_key_on_another_curve_is_rejected(competition):
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    ala = participant_of(competition)

    with pytest.raises(DomainError) as exc:
        services.save_key(user=ala.user, competition=competition, **key_fields(ec.SECP384R1()))

    assert exc.value.machine_code in {"CHAT_E2E_BAD_KEY", "CHAT_E2E_BAD_SIZE"}


def test_replacing_a_key_needs_the_explicit_flag(competition):
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    ala = participant_of(competition)
    first = give_key(ala)

    with pytest.raises(DomainError):
        services.save_key(user=ala.user, competition=competition, **key_fields())
    second = services.save_key(user=ala.user, competition=competition, replace=True, **key_fields())

    assert second.pk == first.pk
    assert second.fingerprint != first.fingerprint


# --- rozmowy szyfrowane -----------------------------------------------------------------------------------


def test_directory_with_e2e_lists_only_people_with_a_key(competition):
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    ala = participant_of(competition)
    keyed, keyless = participant_of(competition, "Ola", "Nowak"), participant_of(competition, "Iza", "Bez")
    give_key(keyed)
    in_directory(keyed)
    in_directory(keyless)

    assert [profile.participant for profile in services.directory(ala)] == [keyed]


def test_new_conversation_is_encrypted_and_starts_empty(competition, pair):
    ala, ola = pair

    conversation = open_conversation(ala, ola, competition)

    assert conversation.is_encrypted
    assert services.membership(ola, conversation.pk).conversation == conversation
    assert list(services.participant_inbox(ola)) == []


def test_plain_first_message_is_refused_when_e2e_is_on(competition, pair):
    ala, ola = pair

    with pytest.raises(DomainError) as exc:
        services.start_peer_conversation(
            user=ala.user, competition=competition, token=services.profile_for(ola).token, body="jawnie"
        )

    assert exc.value.machine_code == "CHAT_E2E_REQUIRED"


def test_starting_needs_my_own_key(competition):
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    give_key(ola)
    in_directory(ola)

    with pytest.raises(DomainError) as exc:
        open_conversation(ala, ola, competition)

    assert exc.value.machine_code == "CHAT_E2E_NO_KEY"


def test_ciphertext_is_stored_with_both_public_keys(competition, pair):
    ala, ola = pair
    conversation = open_conversation(ala, ola, competition)

    message = send_encrypted(ala, ola, conversation, competition)

    assert message.body == ""
    assert message.status == MessageStatus.PUBLISHED
    assert message.sender_public_key == services.key_for(ala).public_key
    assert message.recipient_public_key == services.key_for(ola).public_key
    assert services.participant_unread_count(ola) == 1


def test_plaintext_is_refused_in_an_encrypted_conversation(competition, pair):
    ala, ola = pair
    conversation = open_conversation(ala, ola, competition)

    with pytest.raises(DomainError) as exc:
        send_encrypted(ala, ola, conversation, competition, body="jawna treść")

    assert exc.value.machine_code == "CHAT_PLAINTEXT_REFUSED"


def test_ciphertext_is_refused_in_a_plain_conversation(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    in_directory(ola)
    conversation = services.start_peer_conversation(
        user=ala.user, competition=competition, token=services.profile_for(ola).token, body="Hej"
    ).conversation

    with pytest.raises(DomainError) as exc:
        services.send_participant_message(
            user=ala.user, competition=competition, conversation=conversation, **fake_ciphertext()
        )

    assert exc.value.machine_code == "CHAT_CIPHERTEXT_REFUSED"


def test_stale_key_fingerprint_is_refused(competition, pair):
    ala, ola = pair
    conversation = open_conversation(ala, ola, competition)
    old = services.key_for(ola).fingerprint
    services.save_key(user=ola.user, competition=competition, replace=True, **key_fields())

    with pytest.raises(DomainError) as exc:
        send_encrypted(ala, ola, conversation, competition, recipient_fingerprint=old)

    assert exc.value.machine_code == "CHAT_E2E_KEY_STALE"


def test_oversized_ciphertext_is_refused(competition, pair):
    ala, ola = pair
    conversation = open_conversation(ala, ola, competition)

    with pytest.raises(DomainError) as exc:
        send_encrypted(ala, ola, conversation, competition, **fake_ciphertext(20_000))

    assert exc.value.machine_code == "CHAT_BODY_TOO_LONG"


def test_encrypted_conversation_is_read_only_during_an_open_stage(competition, pair):
    ala, ola = pair
    conversation = open_conversation(ala, ola, competition)
    stage = open_stage(competition)

    with pytest.raises(DomainError) as exc:
        send_encrypted(ala, ola, conversation, competition)

    assert stage.display_name in str(exc.value.detail)
    assert "wstrzymane" in str(exc.value.detail)


def test_encrypted_conversation_is_read_only_after_switching_e2e_off(competition, pair):
    ala, ola = pair
    conversation = open_conversation(ala, ola, competition)
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=False)

    with pytest.raises(DomainError) as exc:
        send_encrypted(ala, ola, conversation, competition)

    assert exc.value.machine_code == "CHAT_CANNOT_WRITE"


def test_report_of_an_encrypted_message_carries_the_plaintext_copy(competition, pair):
    ala, ola = pair
    conversation = open_conversation(ala, ola, competition)
    message = send_encrypted(ala, ola, conversation, competition)

    with pytest.raises(DomainError) as exc:
        services.report_message(user=ola.user, competition=competition, message=message, reason="Groźby")
    assert exc.value.machine_code == "CHAT_REPORT_PLAINTEXT_REQUIRED"

    report = services.report_message(
        user=ola.user,
        competition=competition,
        message=message,
        reason="Groźby",
        reported_plaintext="odszyfrowane",
    )
    assert report.reported_plaintext == "odszyfrowane"
    assert services.peer_message(competition, message.pk) == message


# --- kanał organizatora ----------------------------------------------------------------------------------


def test_organizer_channel_is_never_encrypted(competition, pair):
    ala, ola = pair

    message = services.write_to_organizer(user=ala.user, competition=competition, body="Pytanie")

    assert message.conversation.is_encrypted is False
    assert message.body == "Pytanie"
    with pytest.raises(IntegrityError), transaction.atomic():
        Conversation.objects.create(
            competition=competition, kind=ConversationKind.ORGANIZER, participant=ola, is_encrypted=True
        )
