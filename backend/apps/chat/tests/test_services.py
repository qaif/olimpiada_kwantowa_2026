"""Reguły Wiadomości w serwisie: kto może zacząć i pisać, tryby moderacji, blokady, katalog, izolacja.

Ekrany mają własne testy (``apps/web/tests/test_chat*.py``); tutaj sprawdzamy to, czego widok nie
może obejść – odmowy stoją w ``apps.chat.services``, a nie w szablonie, który chowa przycisk.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, UserFactory
from apps.chat import services
from apps.chat.models import (
    MAX_BODY_LENGTH,
    ChatBlock,
    ChatKey,
    ChatNotificationSettings,
    ChatProfile,
    Conversation,
    ConversationKind,
    Message,
    MessageStatus,
    PeerMode,
)
from apps.chat.tests.helpers import (
    closed_stage,
    configure,
    coordinator_of,
    in_directory,
    open_stage,
    participant_of,
    start,
)
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.forum.models import ANONYMISED_AUTHOR_LABEL
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db


def codes(exc_info) -> str:
    return exc_info.value.machine_code


# --- ustawienia domyślne ---------------------------------------------------------------------------


def test_defaults_organizer_channel_on_peer_channel_off(competition):
    row = services.settings_for(competition)

    assert row.pk is None
    assert row.enabled is True
    assert row.peer_mode == PeerMode.OFF
    assert row.e2e_enabled is False


# --- kanał organizatora ----------------------------------------------------------------------------


def test_participant_writes_to_the_organizer_and_the_team_sees_it_unread(competition):
    ala = participant_of(competition)

    message = services.write_to_organizer(user=ala.user, competition=competition, body="  Pytanie o salę  ")

    conversation = message.conversation
    assert conversation.kind == ConversationKind.ORGANIZER
    assert message.status == MessageStatus.PUBLISHED
    assert message.body == "Pytanie o salę"
    conversation.refresh_from_db()
    assert conversation.organizer_unread_since is not None
    assert services.organizer_unread_count(competition) == 1


def test_one_organizer_conversation_per_participant(competition):
    ala = participant_of(competition)

    first = services.write_to_organizer(user=ala.user, competition=competition, body="Raz")
    second = services.write_to_organizer(user=ala.user, competition=competition, body="Dwa")

    assert first.conversation_id == second.conversation_id
    assert Conversation.objects.filter(kind=ConversationKind.ORGANIZER).count() == 1


def test_coordinator_reply_marks_the_participant_side_unread(competition):
    ala = participant_of(competition)
    boss = coordinator_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Hej"
    ).conversation

    services.send_organizer_message(
        user=boss, competition=competition, conversation=conversation, body="Dzień dobry"
    )

    assert services.participant_unread_count(ala) == 1
    conversation.refresh_from_db()
    assert conversation.organizer_unread_since is None


def test_coordinator_can_start_a_conversation_from_the_participant_card(competition):
    ala = participant_of(competition)
    boss = coordinator_of(competition)

    message = services.organizer_writes_to(user=boss, competition=competition, participant=ala, body="Witaj")

    assert message.conversation.participant == ala
    assert services.participant_unread_count(ala) == 1


def test_the_organizer_channel_does_not_depend_on_peer_mode(competition):
    configure(competition, peer_mode=PeerMode.OFF)
    ala = participant_of(competition)

    message = services.write_to_organizer(user=ala.user, competition=competition, body="Pytanie")

    assert message.status == MessageStatus.PUBLISHED


def test_disabled_module_refuses_everything(competition):
    configure(competition, enabled=False)
    ala = participant_of(competition)

    with pytest.raises(DomainError) as exc:
        services.write_to_organizer(user=ala.user, competition=competition, body="Pytanie")

    assert exc.value.status_code == 404


# --- role ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role", [CompetitionRole.REVIEWER, CompetitionRole.SUPERVISOR, CompetitionRole.APPEALS]
)
def test_other_roles_can_neither_write_nor_be_organizer(competition, role):
    user = UserFactory(groups=[role])
    grant_membership(user, competition, role)

    with pytest.raises(DomainError) as exc:
        services.write_to_organizer(user=user, competition=competition, body="Pytanie")
    assert exc.value.status_code == 403

    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Hej"
    ).conversation
    with pytest.raises(DomainError) as exc:
        services.send_organizer_message(
            user=user, competition=competition, conversation=conversation, body="x"
        )
    assert exc.value.status_code == 403


def test_active_reviewer_is_not_a_participant(competition):
    reviewer = ActiveReviewerFactory().user

    assert services.chat_participant(reviewer, competition) is None


# --- tryby rozmów między uczestnikami --------------------------------------------------------------


def test_off_mode_forbids_new_peer_conversations(competition):
    configure(competition, peer_mode=PeerMode.OFF)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    in_directory(ola)

    with pytest.raises(DomainError) as exc:
        start(ala, ola, competition)

    assert codes(exc) == "CHAT_PEER_OFF"


def test_existing_conversations_are_read_only_after_switching_off(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    conversation = start(ala, ola, competition).conversation
    configure(competition, peer_mode=PeerMode.OFF)

    with pytest.raises(DomainError) as exc:
        services.send_participant_message(
            user=ola.user, competition=competition, conversation=conversation, body="x"
        )

    assert exc.value.detail == services.PEER_OFF


def test_pre_moderation_hides_pending_messages_from_the_recipient(competition):
    configure(competition, peer_mode=PeerMode.PRE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")

    message = start(ala, ola, competition, body="Czekam na akceptację")

    assert message.status == MessageStatus.PENDING
    assert list(services.visible_messages(message.conversation, ala)) == [message]
    assert list(services.visible_messages(message.conversation, ola)) == []
    assert list(services.participant_inbox(ola)) == []
    assert services.participant_unread_count(ola) == 0
    with pytest.raises(DomainError):
        services.membership(ola, message.conversation_id)


def test_approval_delivers_the_message(competition):
    configure(competition, peer_mode=PeerMode.PRE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    boss = coordinator_of(competition)
    message = start(ala, ola, competition)

    services.approve(message=message, actor=boss)

    assert list(services.visible_messages(message.conversation, ola)) == [message]
    assert services.participant_unread_count(ola) == 1
    assert services.participant_unread_count(ala) == 0
    assert AuditLog.objects.filter(action=services.AUDIT_MESSAGE_APPROVED).exists()


def test_rejection_needs_a_note_that_the_sender_sees(competition):
    configure(competition, peer_mode=PeerMode.PRE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    boss = coordinator_of(competition)
    message = start(ala, ola, competition)

    with pytest.raises(DomainError):
        services.reject(message=message, actor=boss, note="  ")
    services.reject(message=message, actor=boss, note="Nie omawiamy zadań.")

    message.refresh_from_db()
    assert message.status == MessageStatus.REJECTED
    assert message.moderation_note == "Nie omawiamy zadań."
    assert list(services.visible_messages(message.conversation, ala)) == [message]
    assert list(services.visible_messages(message.conversation, ola)) == []
    audit = AuditLog.objects.get(action=services.AUDIT_MESSAGE_REJECTED)
    assert "Nie omawiamy" not in str(audit.diff)


def test_post_moderation_delivers_at_once_and_queues_for_review(competition):
    configure(competition, peer_mode=PeerMode.POST)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    boss = coordinator_of(competition)
    message = start(ala, ola, competition)

    assert message.status == MessageStatus.PUBLISHED
    assert list(services.review_messages(competition)) == [message]
    assert services.moderation_count(competition) == 1

    services.mark_reviewed(message=message, actor=boss)
    assert list(services.review_messages(competition)) == []


def test_hiding_leaves_a_placeholder_for_the_recipient(competition):
    configure(competition, peer_mode=PeerMode.POST)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    boss = coordinator_of(competition)
    message = start(ala, ola, competition)

    services.hide(message=message, actor=boss)

    message.refresh_from_db()
    assert message.status == MessageStatus.HIDDEN
    assert list(services.visible_messages(message.conversation, ola)) == [message]
    assert list(services.review_messages(competition)) == []


def test_no_moderation_mode_keeps_content_away_from_the_coordinator(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    message = start(ala, ola, competition, body="prywatne")
    services.send_participant_message(
        user=ola.user, competition=competition, conversation=message.conversation, body="też prywatne"
    )

    assert message.status == MessageStatus.PUBLISHED
    assert services.moderation_count(competition) == 0
    assert list(services.review_messages(competition)) == []
    with pytest.raises(DomainError) as exc:
        services.peer_message(competition, message.pk)
    assert exc.value.status_code == 404


def test_a_report_opens_exactly_the_reported_message_to_the_coordinator(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    first = start(ala, ola, competition, body="niezgłoszona")
    second = services.send_participant_message(
        user=ala.user, competition=competition, conversation=first.conversation, body="zgłoszona"
    )

    services.report_message(user=ola.user, competition=competition, message=second, reason="Obraża mnie")

    assert services.peer_message(competition, second.pk) == second
    # Kontekst zgłoszenia nie otwiera wcześniejszej, niezgłoszonej wiadomości wysłanej bez moderacji.
    assert services.moderation_context(second) == []
    assert services.moderation_count(competition) == 1


def test_reports_work_in_every_mode_including_read_only(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    message = start(ala, ola, competition)
    configure(competition, peer_mode=PeerMode.OFF)

    report = services.report_message(user=ola.user, competition=competition, message=message, reason="Spam")

    assert report.pk is not None


def test_only_the_other_side_can_report_and_only_once(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    message = start(ala, ola, competition)

    with pytest.raises(DomainError):
        services.report_message(user=ala.user, competition=competition, message=message, reason="Moja")
    services.report_message(user=ola.user, competition=competition, message=message, reason="Raz")
    with pytest.raises(DomainError) as exc:
        services.report_message(user=ola.user, competition=competition, message=message, reason="Dwa")
    assert codes(exc) == "CHAT_ALREADY_REPORTED"


def test_hiding_a_reported_message_closes_its_reports(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    boss = coordinator_of(competition)
    message = start(ala, ola, competition)
    report = services.report_message(user=ola.user, competition=competition, message=message, reason="Spam")

    services.hide(message=message, actor=boss)

    report.refresh_from_db()
    assert report.resolved_at is not None
    assert services.moderation_count(competition) == 0


def test_bulk_approve_goes_through_single_approval(competition):
    configure(competition, peer_mode=PeerMode.PRE)
    ala, ola, eva = (
        participant_of(competition),
        participant_of(competition, "Ola", "Nowak"),
        participant_of(competition, "Ewa", "Zych"),
    )
    boss = coordinator_of(competition)
    first = start(ala, ola, competition)
    second = start(eva, ola, competition)

    count = services.bulk_approve(competition=competition, actor=boss, ids=[first.pk, second.pk, 999999])

    assert count == 2
    assert AuditLog.objects.filter(action=services.AUDIT_MESSAGE_APPROVED).count() == 2


# --- wymuszenie premoderacji w trakcie etapu ---------------------------------------------------------


@pytest.mark.parametrize("mode", [PeerMode.POST, PeerMode.NONE])
def test_open_stage_forces_pre_moderation(competition, mode):
    configure(competition, peer_mode=mode)
    stage = open_stage(competition)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")

    assert services.effective_peer_mode(competition) == PeerMode.PRE
    assert services.mode_and_stage(competition)[1] == stage
    message = start(ala, ola, competition)
    assert message.status == MessageStatus.PENDING
    assert message.moderation_mode == PeerMode.PRE


def test_open_stage_does_not_open_a_switched_off_channel(competition):
    configure(competition, peer_mode=PeerMode.OFF)
    open_stage(competition)

    assert services.effective_peer_mode(competition) == PeerMode.OFF


def test_closed_stage_restores_the_setting(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    closed_stage(competition)

    assert services.effective_peer_mode(competition) == PeerMode.NONE


# --- blokady i katalog ------------------------------------------------------------------------------


def test_blocked_person_cannot_write_and_gets_a_neutral_refusal(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    conversation = start(ala, ola, competition).conversation

    services.block(participant=ola, conversation=conversation)

    with pytest.raises(DomainError) as exc:
        services.send_participant_message(
            user=ala.user, competition=competition, conversation=conversation, body="x"
        )
    assert exc.value.detail == services.CANNOT_SEND
    with pytest.raises(DomainError) as exc:
        services.send_participant_message(
            user=ola.user, competition=competition, conversation=conversation, body="x"
        )
    assert exc.value.detail == services.BLOCKED_BY_ME

    services.unblock(participant=ola, conversation=conversation)
    services.send_participant_message(
        user=ala.user, competition=competition, conversation=conversation, body="ok"
    )


def test_blocked_person_cannot_start_and_does_not_see_the_blocker(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    in_directory(ola)
    ChatBlock.objects.create(blocker=ola, blocked=ala)

    assert list(services.directory(ala)) == []
    with pytest.raises(DomainError) as exc:
        start(ala, ola, competition)
    assert exc.value.detail == services.CANNOT_SEND


def test_directory_is_opt_in_and_excludes_self_inactive_and_other_competitions(
    competition, other_competition
):
    configure(competition, peer_mode=PeerMode.NONE)
    ala = participant_of(competition)
    visible = participant_of(competition, "Ola", "Nowak")
    hidden = participant_of(competition, "Iza", "Hidden")
    inactive = participant_of(competition, "Ina", "Nieaktywna")
    stranger = participant_of(other_competition, "Obca", "Osoba")
    for person in (ala, visible, inactive, stranger):
        in_directory(person)
    ChatProfile.objects.get_or_create(participant=hidden)
    inactive.user.is_active = False
    inactive.user.save(update_fields=["is_active"])

    names = [profile.participant.pk for profile in services.directory(ala)]

    assert names == [visible.pk]


def test_directory_searches_by_first_name_only(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala = participant_of(competition)
    ola = participant_of(competition, "Ola", "Szukana")
    in_directory(ola)

    assert list(services.directory(ala, "ol")) == [ola.chat_profile]
    assert list(services.directory(ala, "Szukana")) == []


def test_new_conversation_only_with_a_discoverable_person(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    profile, _ = ChatProfile.objects.get_or_create(participant=ola)

    with pytest.raises(DomainError) as exc:
        services.start_peer_conversation(
            user=ala.user, competition=competition, token=profile.token, body="Hej"
        )

    assert exc.value.detail == services.CANNOT_SEND


def test_after_the_conversation_exists_leaving_the_directory_does_not_stop_replies(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    conversation = start(ala, ola, competition).conversation
    services.set_discoverable(ola, False)

    message = services.send_participant_message(
        user=ala.user, competition=competition, conversation=conversation, body="Nadal piszę"
    )

    assert message.status == MessageStatus.PUBLISHED


def test_one_peer_conversation_per_pair_whoever_starts(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    in_directory(ala)

    first = start(ala, ola, competition).conversation
    second = start(ola, ala, competition).conversation

    assert first.pk == second.pk
    with pytest.raises(IntegrityError), transaction.atomic():
        low, high = sorted([ala, ola], key=lambda p: p.pk)
        Conversation.objects.create(
            competition=competition, kind=ConversationKind.PEER, participant_low=low, participant_high=high
        )


# --- treść -----------------------------------------------------------------------------------------


def test_body_length_limit_rejects_instead_of_truncating(competition):
    ala = participant_of(competition)

    with pytest.raises(DomainError) as exc:
        services.write_to_organizer(user=ala.user, competition=competition, body="x" * (MAX_BODY_LENGTH + 1))
    assert codes(exc) == "CHAT_BODY_TOO_LONG"
    message = services.write_to_organizer(user=ala.user, competition=competition, body="x" * MAX_BODY_LENGTH)
    assert len(message.body) == MAX_BODY_LENGTH


def test_blank_body_is_rejected(competition):
    ala = participant_of(competition)

    with pytest.raises(DomainError) as exc:
        services.write_to_organizer(user=ala.user, competition=competition, body="   \n  ")

    assert codes(exc) == "CHAT_BODY_REQUIRED"


# --- izolacja konkursów ------------------------------------------------------------------------------


def test_conversations_of_another_competition_do_not_exist_here(competition, other_competition):
    ala = participant_of(competition)
    stranger = participant_of(other_competition, "Obca", "Osoba")
    foreign = services.write_to_organizer(
        user=stranger.user, competition=other_competition, body="Nie Twoje"
    ).conversation

    with pytest.raises(DomainError) as exc:
        services.membership(ala, foreign.pk)
    assert exc.value.status_code == 404
    with pytest.raises(DomainError) as exc:
        services.organizer_conversation(competition, foreign.pk)
    assert exc.value.status_code == 404
    boss = coordinator_of(competition)
    with pytest.raises(DomainError):
        services.send_organizer_message(user=boss, competition=competition, conversation=foreign, body="x")
    assert list(services.organizer_inbox(competition)) == []


def test_coordinator_cannot_write_to_a_participant_of_another_competition(competition, other_competition):
    boss = coordinator_of(competition)
    stranger = participant_of(other_competition, "Obca", "Osoba")

    with pytest.raises(DomainError) as exc:
        services.organizer_writes_to(user=boss, competition=competition, participant=stranger, body="x")

    assert exc.value.status_code == 404


# --- liczniki -----------------------------------------------------------------------------------------


def test_nav_state_counts_conversations_with_unread_messages(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    boss = coordinator_of(competition)
    in_directory(ala)
    start(ola, ala, competition)
    services.organizer_writes_to(user=boss, competition=competition, participant=ala, body="Hej")

    assert services.nav_state(ala) == (True, 2)
    member = services.membership(ala, services.organizer_conversation_of(ala).pk)
    services.mark_read(member)
    assert services.nav_state(ala) == (True, 1)
    configure(competition, peer_mode=PeerMode.NONE, enabled=False)
    assert services.nav_state(ala) == (False, 0)


def test_coordinator_attention_counts_unread_and_queue_in_one_number(competition, django_assert_num_queries):
    configure(competition, peer_mode=PeerMode.PRE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    services.write_to_organizer(user=ala.user, competition=competition, body="Pytanie")
    start(ala, ola, competition)

    with django_assert_num_queries(1):
        assert services.coordinator_attention(competition) == 2
    configure(competition, peer_mode=PeerMode.PRE, enabled=False)
    assert services.coordinator_attention(competition) == 0


# --- dane konta ------------------------------------------------------------------------------------


def test_erase_for_user_keeps_messages_and_drops_personal_state(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    in_directory(ala)
    message = start(ala, ola, competition)
    ChatBlock.objects.create(blocker=ala, blocked=ola)
    ChatNotificationSettings.objects.create(user=ala.user, email_on_message=False)
    from apps.chat.tests.helpers import give_key

    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    give_key(ala)

    services.erase_for_user(ala.user)

    assert Message.objects.filter(pk=message.pk).exists()
    assert not ChatProfile.objects.filter(participant=ala).exists()
    assert not ChatKey.objects.filter(participant=ala).exists()
    assert not ChatBlock.objects.filter(blocker=ala).exists()
    assert not ChatNotificationSettings.objects.filter(user=ala.user).exists()


def test_anonymised_sender_is_signed_as_a_deleted_user(competition):
    from apps.accounts.profile import anonymise_account
    from apps.forum.models import display_author

    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    message = start(ala, ola, competition)

    anonymise_account(ala.user)

    message.refresh_from_db()
    assert display_author(message.sender) == ANONYMISED_AUTHOR_LABEL
    assert list(services.visible_messages(message.conversation, ola)) == [message]
