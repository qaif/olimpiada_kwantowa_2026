"""Mentoring i integracja z Wiadomościami: prośby, limity, kanały, małoletni, zakończenie, nadzór."""

from __future__ import annotations

import pytest
from django.core import mail

from apps.alumni import mentoring
from apps.alumni.models import Channel, EndReason, Mentorship, MentorshipFlag, MentorshipStatus
from apps.alumni.tests.helpers import (
    alumnus,
    chat,
    coordinator_of,
    current_stage,
    enable,
    joined,
    mentee,
    mentor,
    participant_of,
)
from apps.chat import services as chat_services
from apps.chat.models import AgePolicy, ConversationKind, MessageStatus, PeerMode
from apps.core.api import DomainError
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def ask(person, profile, **kwargs):
    return mentoring.request_mentor(
        user=person.user, competition=person.competition, token=profile.token, **kwargs
    )


def accept(profile, row):
    return mentoring.accept(user=profile.participant.user, competition=row.competition, pk=row.pk)


def write(person, conversation, body="Dzień dobry"):
    return chat_services.send_participant_message(
        user=person.user, competition=person.competition, conversation=conversation, body=body
    )


# --- prośby ---------------------------------------------------------------------------------------------


def test_request_and_accept_opens_plain_peer_conversation(competition, django_capture_on_commit_callbacks):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition, minor=False)

    with django_capture_on_commit_callbacks(execute=True):
        row = ask(student, teacher, topic="physics", note="Pomóż z fizyką")
    assert row.status == MentorshipStatus.REQUESTED
    assert [m.to for m in mail.outbox] == [[teacher.participant.user.email]]
    assert "Pomóż z fizyką" not in mail.outbox[0].body

    row = accept(teacher, row)

    conversation = row.conversation
    assert row.status == MentorshipStatus.ACCEPTED
    assert conversation.kind == ConversationKind.PEER
    assert conversation.is_encrypted is False
    assert conversation.started_by_id is None
    assert row.conversation_preexisting is False


def test_mentor_must_be_available_listed_and_in_competition(competition, other_competition):
    enable(competition)
    enable(other_competition)
    chat(competition)
    student = mentee(competition)
    not_mentor = joined(alumnus(competition, "Nie"))
    foreign = mentor(other_competition, "Obca")

    with pytest.raises(DomainError) as exc:
        ask(student, not_mentor)
    assert exc.value.status_code == 404
    with pytest.raises(DomainError) as exc:
        ask(student, foreign)
    assert exc.value.status_code == 404


def test_duplicate_and_open_request_limit(competition):
    enable(competition)
    chat(competition)
    student = mentee(competition)
    mentors = [mentor(competition, f"M{i}") for i in range(4)]

    ask(student, mentors[0])
    with pytest.raises(DomainError) as exc:
        ask(student, mentors[0])
    assert exc.value.machine_code == "ALUMNI_DUPLICATE"
    ask(student, mentors[1])
    ask(student, mentors[2])
    with pytest.raises(DomainError) as exc:
        ask(student, mentors[3])
    assert exc.value.machine_code == "ALUMNI_TOO_MANY"


def test_capacity_is_enforced_on_request_and_accept(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition, mentor_capacity=1)
    first, second = mentee(competition, "A"), mentee(competition, "B")
    row_a = ask(first, teacher)
    row_b = ask(second, teacher)

    accept(teacher, row_a)
    with pytest.raises(DomainError) as exc:
        accept(teacher, row_b)
    assert exc.value.machine_code == "ALUMNI_FULL"
    third = mentee(competition, "C")
    with pytest.raises(DomainError):
        ask(third, teacher)


def test_only_current_participants_request(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    old_timer = alumnus(competition, "Dawny")

    with pytest.raises(DomainError) as exc:
        ask(old_timer, teacher)
    assert exc.value.machine_code == "ALUMNI_NOT_CURRENT"


def test_mentoring_disabled_is_404_and_requires_chat(competition):
    enable(competition, mentoring_enabled=False)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)

    with pytest.raises(DomainError) as exc:
        ask(student, teacher)
    assert exc.value.status_code == 404

    enable(competition, mentoring_enabled=True)
    chat(competition, enabled=False)
    with pytest.raises(DomainError) as exc:
        ask(student, teacher)
    assert exc.value.machine_code == "ALUMNI_CHAT_DISABLED"


def test_only_the_mentor_can_accept(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    row = ask(student, teacher)

    with pytest.raises(DomainError) as exc:
        mentoring.accept(user=student.user, competition=competition, pk=row.pk)
    assert exc.value.status_code == 404


# --- kanały (ALUM-01 § 5.2) --------------------------------------------------------------------------


def test_minor_with_same_group_policy_goes_through_supervised_pre(
    competition, django_capture_on_commit_callbacks
):
    """Dorosły mentor + małoletni przy SAME_GROUP: każda wiadomość czeka na organizatora."""
    enable(competition)
    chat(competition, peer_mode=PeerMode.NONE, age_policy=AgePolicy.SAME_GROUP)
    teacher = mentor(competition)
    student = mentee(competition, minor=True)
    row = accept(teacher, ask(student, teacher))

    assert mentoring.channel_for(row) == Channel.SUPERVISED
    message = write(teacher.participant, row.conversation)
    assert message.status == MessageStatus.PENDING

    # Małoletni nie widzi wiadomości przed akceptacją.
    assert not chat_services.visible_messages(row.conversation, student).filter(pk=message.pk).exists()

    coordinator = coordinator_of(competition)
    with django_capture_on_commit_callbacks(execute=True):
        chat_services.approve(message=message, actor=coordinator)
    assert chat_services.visible_messages(row.conversation, student).filter(pk=message.pk).exists()

    reply = write(student, row.conversation, "Dziękuję")
    assert reply.status == MessageStatus.PENDING


def test_minor_with_any_policy_gets_post_floor(competition):
    enable(competition)
    chat(competition, peer_mode=PeerMode.NONE, age_policy=AgePolicy.ANY)
    teacher = mentor(competition)
    student = mentee(competition, minor=True)
    row = accept(teacher, ask(student, teacher))

    assert mentoring.channel_for(row) == Channel.PEER
    message = write(teacher.participant, row.conversation)
    assert message.status == MessageStatus.PUBLISHED
    assert message.moderation_mode == PeerMode.POST
    assert chat_services.review_messages(competition).filter(pk=message.pk).exists()


def test_adults_follow_plain_chat_rules(competition):
    enable(competition)
    chat(competition, peer_mode=PeerMode.NONE, age_policy=AgePolicy.SAME_GROUP)
    teacher = mentor(competition)
    student = mentee(competition, minor=False)
    row = accept(teacher, ask(student, teacher))

    message = write(teacher.participant, row.conversation)
    assert message.status == MessageStatus.PUBLISHED
    assert message.moderation_mode == PeerMode.NONE

    # Etap przyjmujący rozwiązania wymusza PRE – reguła czatu działa także w rozmowie mentorskiej.
    from apps.competitions.models import StageKind
    from apps.competitions.tests.factories import StageFactory

    StageFactory(competition=competition, edition=current_stage(competition).edition, kind=StageKind.DISTRICT)
    assert write(student, row.conversation).status == MessageStatus.PENDING


def test_peer_chat_off_means_supervised_for_everyone(competition):
    enable(competition)
    chat(competition, peer_mode=PeerMode.OFF)
    teacher = mentor(competition)
    student = mentee(competition, minor=False)
    row = accept(teacher, ask(student, teacher))

    assert mentoring.channel_for(row) == Channel.SUPERVISED
    assert write(student, row.conversation).status == MessageStatus.PENDING


def test_ending_makes_conversation_read_only(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition, minor=False)
    row = accept(teacher, ask(student, teacher))

    mentoring.end(user=student.user, competition=competition, pk=row.pk)

    row.refresh_from_db()
    assert row.status == MentorshipStatus.ENDED
    assert row.end_reason == EndReason.MENTEE
    with pytest.raises(DomainError) as exc:
        write(teacher.participant, row.conversation)
    assert str(exc.value.detail) == str(mentoring.ENDED_REFUSAL)


def test_preexisting_conversation_returns_to_chat_rules_after_end(competition):
    enable(competition)
    chat(competition, peer_mode=PeerMode.NONE)
    teacher = mentor(competition)
    student = mentee(competition, minor=False)
    conversation, _ = chat_services.ensure_peer_conversation(teacher.participant, student)
    row = accept(teacher, ask(student, teacher))
    assert row.conversation_preexisting is True
    assert row.conversation == conversation

    mentoring.end(user=teacher.participant.user, competition=competition, pk=row.pk)

    assert chat_services.peer_policy(conversation) is None
    assert write(student, conversation).status == MessageStatus.PUBLISHED


def test_pausing_mentoring_freezes_conversations(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition, minor=False)
    row = accept(teacher, ask(student, teacher))

    enable(competition, mentoring_enabled=False)

    with pytest.raises(DomainError) as exc:
        write(student, row.conversation)
    assert str(exc.value.detail) == str(mentoring.PAUSED_REFUSAL)


def test_withdrawing_mentor_consent_ends_relations(competition):
    from apps.alumni import services

    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    row = accept(teacher, ask(student, teacher))

    services.withdraw(user=teacher.participant.user, competition=competition)

    row.refresh_from_db()
    assert row.status == MentorshipStatus.ENDED
    assert row.end_reason == EndReason.WITHDRAWN


def test_block_in_chat_still_applies(competition):
    enable(competition)
    chat(competition, peer_mode=PeerMode.NONE)
    teacher = mentor(competition)
    student = mentee(competition, minor=False)
    row = accept(teacher, ask(student, teacher))
    chat_services.block(participant=student, conversation=row.conversation)

    with pytest.raises(DomainError):
        write(teacher.participant, row.conversation)


# --- nadzór koordynatora ------------------------------------------------------------------------------


def test_coordinator_ends_pair_with_note_and_audit(competition, django_capture_on_commit_callbacks):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    row = accept(teacher, ask(student, teacher))
    coordinator = coordinator_of(competition)

    with pytest.raises(DomainError):
        mentoring.coordinator_end(competition=competition, actor=coordinator, pk=row.pk, note="  ")
    with pytest.raises(DomainError):
        mentoring.coordinator_end(competition=competition, actor=student.user, pk=row.pk, note="x")

    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        mentoring.coordinator_end(
            competition=competition, actor=coordinator, pk=row.pk, note="Naruszenie zasad"
        )

    row.refresh_from_db()
    assert row.end_reason == EndReason.COORDINATOR
    assert row.coordinator_note == "Naruszenie zasad"
    assert AuditLog.objects.filter(action=mentoring.AUDIT_ENDED_BY_COORDINATOR).exists()
    assert sorted(m.to[0] for m in mail.outbox) == sorted(
        [teacher.participant.user.email, student.user.email]
    )


def test_coordinator_of_other_competition_gets_404(competition, other_competition):
    enable(competition)
    enable(other_competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    row = ask(student, teacher)
    foreign = coordinator_of(other_competition)

    with pytest.raises(DomainError) as exc:
        mentoring.coordinator_end(competition=other_competition, actor=foreign, pk=row.pk, note="x")
    assert exc.value.status_code == 404


def test_flag_mails_coordinators_without_reason(competition, django_capture_on_commit_callbacks):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    row = ask(student, teacher)
    coordinator = coordinator_of(competition)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        mentoring.flag(
            user=student.user, competition=competition, pk=row.pk, reason="Dziwne prośby o telefon"
        )

    assert [m.to for m in mail.outbox] == [[coordinator.email]]
    assert "telefon" not in mail.outbox[0].body
    flag = MentorshipFlag.objects.get()
    [item] = mentoring.oversight(competition)
    assert item.open_flags == 1
    mentoring.resolve_flag(competition=competition, actor=coordinator, pk=flag.pk)
    assert not mentoring.open_flags(competition).exists()


def test_stranger_cannot_flag_or_end(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    row = ask(student, teacher)
    stranger = participant_of(competition, "Obcy")

    with pytest.raises(DomainError) as exc:
        mentoring.flag(user=stranger.user, competition=competition, pk=row.pk, reason="x")
    assert exc.value.status_code == 404
    with pytest.raises(DomainError):
        mentoring.end(user=stranger.user, competition=competition, pk=row.pk)
    assert Mentorship.objects.get(pk=row.pk).status == MentorshipStatus.REQUESTED
