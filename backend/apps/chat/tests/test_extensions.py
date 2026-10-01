"""Rozszerzenia z § 12 zadania CZ-01: szablony odpowiedzi, przypisanie i stan rozmów organizatora,
grupa wiekowa w rozmowach uczestników i dzienny limit nowych rozmów."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import UserFactory
from apps.chat import services
from apps.chat.models import (
    AgePolicy,
    ChatSettings,
    Conversation,
    ConversationStatus,
    PeerMode,
    ReplyTemplate,
)
from apps.chat.tests.helpers import configure, coordinator_of, in_directory, participant_of, start
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 1)


class Person:
    """Lekki zastępnik profilu do testów granicznych ``is_adult`` – bez bazy."""

    def __init__(self, birth_date=None, birth_year=0):
        self.birth_date = birth_date
        self.birth_year = birth_year


# --- 12.3 is_adult: przypadki graniczne ------------------------------------------------------------


@pytest.mark.parametrize(
    ("birth_date", "expected"),
    [
        (date(2008, 10, 1), True),  # 18. urodziny dziś
        (date(2008, 10, 2), False),  # jutro
        (date(2008, 9, 30), True),
        (date(2010, 1, 1), False),
    ],
)
def test_is_adult_from_the_full_date(birth_date, expected):
    assert services.is_adult(Person(birth_date=birth_date), TODAY) is expected


def test_is_adult_on_a_leap_day_birthday():
    born = date(2008, 2, 29)

    assert services.is_adult(Person(birth_date=born), date(2026, 2, 28)) is False
    assert services.is_adult(Person(birth_date=born), date(2026, 3, 1)) is True


@pytest.mark.parametrize(
    ("birth_year", "expected"),
    [
        (2008, False),  # 2026 − 2008 = 18: może jeszcze nie mieć urodzin – ostrożnie niepełnoletni
        (2007, True),  # 19 > 18
        (0, False),  # rocznik nieznany
        (1900, True),
    ],
)
def test_is_adult_from_the_year_alone_is_conservative(birth_year, expected):
    assert services.is_adult(Person(birth_year=birth_year), TODAY) is expected


@freeze_time("2026-10-01 12:00:00")
def test_sql_rule_matches_the_python_rule(competition):
    from apps.accounts.models import Participant

    people = [
        participant_of(competition, "A", "Data", birth_date=date(2008, 10, 1), birth_year=2008),
        participant_of(competition, "B", "Data", birth_date=date(2008, 10, 2), birth_year=2008),
        participant_of(competition, "C", "Rocznik", birth_date=None, birth_year=2008),
        participant_of(competition, "D", "Rocznik", birth_date=None, birth_year=2007),
    ]
    adults = set(Participant.objects.filter(services.adult_q()).values_list("pk", flat=True))

    assert adults == {person.pk for person in people if services.is_adult(person)}
    assert adults == {people[0].pk, people[3].pk}


# --- 12.3 egzekwowanie -----------------------------------------------------------------------------------


def adult(competition, name="Dorota"):
    return participant_of(competition, name, "Dorosła", birth_date=date(2000, 1, 1), birth_year=2000)


def test_directory_keeps_age_groups_apart(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    minor = participant_of(competition)
    grown = adult(competition)
    other_minor = participant_of(competition, "Ola", "Nowak")
    for person in (grown, other_minor, minor):
        in_directory(person)

    assert [p.participant for p in services.directory(minor)] == [other_minor]
    assert [p.participant for p in services.directory(grown)] == []
    ChatSettings.objects.filter(competition=competition).update(age_policy=AgePolicy.ANY)
    assert {p.participant for p in services.directory(minor)} == {grown, other_minor}


def test_starting_across_age_groups_is_refused(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    minor, grown = participant_of(competition), adult(competition)

    with pytest.raises(DomainError) as exc:
        start(minor, grown, competition)

    assert exc.value.detail == services.CANNOT_SEND


def test_conversation_closes_when_someone_turns_18(competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    conversation = start(ala, ola, competition).conversation
    ola.birth_date = date(2000, 1, 1)
    ola.save()
    conversation = Conversation.objects.get(pk=conversation.pk)  # tak, jak wczyta ją następne żądanie

    with pytest.raises(DomainError) as exc:
        services.send_participant_message(
            user=ala.user, competition=competition, conversation=conversation, body="x"
        )

    assert exc.value.detail == services.AGE_CLOSED
    assert "18" not in services.AGE_CLOSED and "pełnolet" not in services.AGE_CLOSED


def test_any_policy_allows_mixed_conversations(competition):
    row = configure(competition, peer_mode=PeerMode.NONE)
    row.age_policy = AgePolicy.ANY
    row.save()
    minor, grown = participant_of(competition), adult(competition)

    message = start(minor, grown, competition)

    assert message.pk is not None


# --- 12.4 dzienny limit nowych rozmów ---------------------------------------------------------------------


def test_daily_limit_of_new_conversations(competition):
    row = configure(competition, peer_mode=PeerMode.NONE)
    row.daily_new_conversations = 2
    row.save()
    ala = participant_of(competition)
    others = [participant_of(competition, f"Osoba{index}", "Test") for index in range(3)]
    now = timezone.now()

    with freeze_time(now):
        first = start(ala, others[0], competition).conversation
        start(ala, others[1], competition)
        with pytest.raises(DomainError) as exc:
            start(ala, others[2], competition)
        assert exc.value.detail == services.DAILY_LIMIT
        # Odpowiedzi w istniejących rozmowach limit nie dotyczy.
        services.send_participant_message(
            user=ala.user, competition=competition, conversation=first, body="dalej"
        )
        # Ani rozmowy z organizatorem.
        services.write_to_organizer(user=ala.user, competition=competition, body="pytanie")

    with freeze_time(now + timedelta(hours=24, minutes=1)):
        assert start(ala, others[2], competition).pk is not None


def test_limit_counts_only_conversations_i_started(competition):
    row = configure(competition, peer_mode=PeerMode.NONE)
    row.daily_new_conversations = 1
    row.save()
    ala, ola, eva = (
        participant_of(competition),
        participant_of(competition, "Ola", "Nowak"),
        participant_of(competition, "Ewa", "Zych"),
    )

    start(ola, ala, competition)
    start(ala, eva, competition)

    assert services.find_peer_conversation(ala, eva).started_by == ala


@pytest.mark.parametrize("value", [0, 51])
def test_daily_limit_range_is_validated(competition, value):
    with pytest.raises(DomainError):
        services.save_settings(
            competition=competition,
            actor=coordinator_of(competition),
            enabled=True,
            peer_mode="NONE",
            daily_new_conversations=value,
        )


# --- 12.2 stan i przypisanie ------------------------------------------------------------------------------


def test_status_transitions(competition):
    ala = participant_of(competition)
    boss = coordinator_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation
    conversation.refresh_from_db()
    assert conversation.status == ConversationStatus.OPEN

    services.send_organizer_message(user=boss, competition=competition, conversation=conversation, body="Już")
    conversation.refresh_from_db()
    assert conversation.status == ConversationStatus.WAITING

    services.send_organizer_message(
        user=boss, competition=competition, conversation=conversation, body="Zamykam", close=True
    )
    conversation.refresh_from_db()
    assert conversation.status == ConversationStatus.CLOSED

    services.write_to_organizer(user=ala.user, competition=competition, body="Jeszcze jedno")
    conversation.refresh_from_db()
    assert conversation.status == ConversationStatus.OPEN
    assert conversation.status_changed_at is not None


def test_assignment_only_to_a_coordinator_of_this_competition(competition):
    ala = participant_of(competition)
    boss, colleague = coordinator_of(competition), coordinator_of(competition)
    reviewer = UserFactory(groups=[CompetitionRole.REVIEWER])
    grant_membership(reviewer, competition, CompetitionRole.REVIEWER)
    conversation = services.write_to_organizer(user=ala.user, competition=competition, body="x").conversation

    services.assign(conversation=conversation, actor=boss, competition=competition, assignee=colleague)
    conversation.refresh_from_db()
    assert conversation.assigned_to == colleague
    for wrong in (reviewer, ala.user):
        with pytest.raises(DomainError):
            services.assign(conversation=conversation, actor=boss, competition=competition, assignee=wrong)
    assert set(services.team_members(competition)) >= {boss, colleague}
    assert reviewer not in services.team_members(competition)
    assert AuditLog.objects.filter(action=services.AUDIT_ASSIGNED).count() == 1


def test_manual_status_change_is_audited(competition):
    ala = participant_of(competition)
    boss = coordinator_of(competition)
    conversation = services.write_to_organizer(user=ala.user, competition=competition, body="x").conversation

    services.set_status(conversation=conversation, actor=boss, competition=competition, status="CLOSED")

    assert AuditLog.objects.filter(action=services.AUDIT_STATUS_CHANGED).exists()
    with pytest.raises(DomainError):
        services.set_status(conversation=conversation, actor=boss, competition=competition, status="BOGUS")


def test_inbox_filters_and_counts(competition):
    boss, colleague = coordinator_of(competition), coordinator_of(competition)
    mine = services.write_to_organizer(
        user=participant_of(competition, "Moja", "Sprawa").user, competition=competition, body="1"
    ).conversation
    unassigned = services.write_to_organizer(
        user=participant_of(competition, "Wolna", "Sprawa").user, competition=competition, body="2"
    ).conversation
    waiting = services.write_to_organizer(
        user=participant_of(competition, "Czeka", "Sprawa").user, competition=competition, body="3"
    ).conversation
    services.assign(conversation=mine, actor=boss, competition=competition, assignee=boss)
    services.send_organizer_message(user=colleague, competition=competition, conversation=waiting, body="odp")

    counts = services.organizer_inbox_counts(competition, user=boss)

    assert (counts["open"], counts["waiting"], counts["closed"], counts["total"]) == (2, 1, 0, 3)
    assert (counts["mine"], counts["unassigned"]) == (1, 1)
    assert list(services.organizer_inbox(competition, user=boss, who="mine", status="OPEN")) == [mine]
    assert list(services.organizer_inbox(competition, user=boss, who="unassigned", status="OPEN")) == [
        unassigned
    ]
    assert list(services.organizer_inbox(competition, user=boss, status="WAITING")) == [waiting]


def test_badge_counts_only_open_unread(competition):
    ala = participant_of(competition)
    boss = coordinator_of(competition)
    conversation = services.write_to_organizer(user=ala.user, competition=competition, body="x").conversation
    assert services.coordinator_attention(competition) == 1

    services.set_status(conversation=conversation, actor=boss, competition=competition, status="CLOSED")

    assert services.coordinator_attention(competition) == 0
    assert services.organizer_unread_count(competition) == 0


# --- 12.1 szablony ------------------------------------------------------------------------------------------


def test_templates_are_saved_and_deleted_with_audit(competition, other_competition):
    boss = coordinator_of(competition)

    template = services.save_template(
        competition=competition, actor=boss, title="Terminy", body="Cześć {imie}!"
    )
    services.save_template(
        competition=competition, actor=boss, title="Terminy 2", body="x", template=template
    )
    template.refresh_from_db()
    assert template.title == "Terminy 2"
    assert list(services.reply_templates(other_competition)) == []

    services.delete_template(competition=competition, actor=boss, template=template)

    assert not ReplyTemplate.objects.exists()
    assert AuditLog.objects.filter(action=services.AUDIT_TEMPLATE_SAVED).count() == 2
    assert AuditLog.objects.filter(action=services.AUDIT_TEMPLATE_DELETED).count() == 1


def test_only_a_coordinator_manages_templates(competition):
    ala = participant_of(competition)

    with pytest.raises(DomainError) as exc:
        services.save_template(competition=competition, actor=ala.user, title="x", body="y")

    assert exc.value.status_code == 403
