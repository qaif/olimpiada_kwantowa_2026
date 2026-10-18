"""Reguły przypomnień z bazą: generowanie komunikatów planowanych, idempotencja, wycofywanie.

Komunikaty powstają przez ``schedule_broadcast`` z prawdziwym zegarem (granica 5 minut liczy się
od ``timezone.now()``), więc warsztaty w testach stoją kilka dni od dziś – w oknie generowania
(7 dni) albo celowo poza nim.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.messaging import cancel_scheduled_broadcast, resolve_scheduled_recipients
from apps.accounts.models import BroadcastStatus, MessageBroadcast
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import CurrentEditionFactory, StageEntryFactory, StageFactory
from apps.core.models import AuditLog
from apps.message_rules import services
from apps.message_rules.models import MessageRule, MessageRuleOccurrence, RuleKind
from apps.message_rules.tests.helpers import MEET, rewrite_rows, row, today, workshop_rule, workshops_page_for

pytestmark = pytest.mark.django_db


def _scheduled():
    return MessageBroadcast.objects.filter(status=BroadcastStatus.SCHEDULED).order_by("scheduled_for")


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def schedule(competition):
    rows = [
        row("Liczby zespolone (prowadzący: Tomasz Sowiński)", today() + dt.timedelta(days=2)),
        row("Algebra liniowa", today() + dt.timedelta(days=4), lecturer="Grzegorz Czelusta"),
        row("Mechanika kwantowa", today() + dt.timedelta(days=20)),  # poza oknem 7 dni
        row("Termin do ustalenia", None),
    ]
    return workshops_page_for(competition, rows), rows


def test_rule_schedules_one_broadcast_per_workshop_in_the_window(competition, coordinator, schedule):
    rule, result = workshop_rule(competition, coordinator)

    assert result.created == 2
    first, second = _scheduled()
    assert first.subject == "Dziś warsztat: Liczby zespolone"
    assert "Prowadzi Tomasz Sowiński. Link: " + MEET in first.body
    assert "Prowadzi Grzegorz Czelusta" in second.body
    local = timezone.localtime(first.scheduled_for)
    assert (local.date(), local.time()) == (today() + dt.timedelta(days=2), dt.time(6, 0))
    assert first.competition == competition
    assert first.created_by == coordinator
    assert first.group == "ALL_PARTICIPANTS_AND_TEACHERS"
    assert first.target["message_rule"] == rule.pk
    assert "przypomnienie automatyczne" in first.target["label"]
    # Wygenerowany komunikat ma ten sam audyt, co zaplanowany ręcznie – z autorem reguły jako aktorem.
    logs = AuditLog.objects.filter(action="broadcast.scheduled")
    assert logs.count() == 2
    assert {log.actor for log in logs} == {coordinator}
    assert AuditLog.objects.get(action="message_rule.created").actor == coordinator


def test_generation_is_idempotent(competition, coordinator, schedule):
    rule, _ = workshop_rule(competition, coordinator)

    again = services.sync_rule(rule.pk)
    services.generate_all()

    assert again.created == 0
    assert _scheduled().count() == 2
    assert MessageRuleOccurrence.objects.filter(rule=rule).count() == 2


def test_cancelled_reminder_is_not_generated_again(competition, coordinator, schedule):
    rule, _ = workshop_rule(competition, coordinator)
    first = _scheduled().first()

    assert cancel_scheduled_broadcast(first, actor=coordinator)
    services.sync_rule(rule.pk)
    services.generate_all()

    first.refresh_from_db()
    assert first.status == BroadcastStatus.CANCELLED
    assert MessageBroadcast.objects.count() == 2
    assert _scheduled().count() == 1


def test_disabled_rule_creates_nothing(competition, coordinator, schedule):
    rule, result = workshop_rule(competition, coordinator, enabled=False)

    assert result.created == 0
    assert services.generate_all() == 0
    assert not MessageBroadcast.objects.exists()
    assert services.upcoming(rule)["items"]  # podgląd i tak pokazuje, co by wyszło


def test_disabling_withdraws_pending_reminders_and_enabling_brings_them_back(
    competition, coordinator, schedule
):
    rule, _ = workshop_rule(competition, coordinator)

    result = services.set_enabled(rule, False, actor=coordinator)

    assert result.withdrawn == 2
    assert not _scheduled().exists()
    assert MessageBroadcast.objects.filter(status=BroadcastStatus.CANCELLED).count() == 2
    log = AuditLog.objects.filter(action="broadcast.cancelled").first()
    assert log.actor is None
    assert log.diff["reason"] == "rule_disabled"
    assert AuditLog.objects.filter(action="message_rule.disabled").exists()

    services.set_enabled(rule, True, actor=coordinator)

    assert _scheduled().count() == 2


def test_cancelled_or_undated_workshop_withdraws_its_pending_reminder(competition, coordinator, schedule):
    page, rows = schedule
    rule, _ = workshop_rule(competition, coordinator)
    withdrawn_subject = _scheduled().first().subject

    # Pierwszy warsztat odwołany (dopisek w kolumnie godzin, klucz bez zmian), drugi bez zmian.
    rewrite_rows(page, [{**rows[0], "time": "ODWOŁANE"}, *rows[1:]])
    result = services.sync_rule(rule.pk)

    assert result.withdrawn == 1
    assert result.created == 0
    assert [b.subject for b in _scheduled()] == ["Dziś warsztat: Algebra liniowa"]
    assert MessageBroadcast.objects.get(subject=withdrawn_subject).status == BroadcastStatus.CANCELLED
    assert AuditLog.objects.get(action="broadcast.cancelled").diff["reason"] == "source_gone"


def test_editing_the_rule_replaces_pending_reminders(competition, coordinator, schedule):
    rule, _ = workshop_rule(competition, coordinator)
    old = set(_scheduled().values_list("pk", flat=True))

    changed, result = services.update_rule(rule, actor=coordinator, subject_template="Przypomnienie: {temat}")

    assert changed == ["subject_template"]
    assert (result.withdrawn, result.created) == (2, 2)
    assert {b.subject for b in _scheduled()} == {
        "Przypomnienie: Liczby zespolone",
        "Przypomnienie: Algebra liniowa",
    }
    assert not old & set(_scheduled().values_list("pk", flat=True))
    log = AuditLog.objects.get(action="message_rule.updated")
    assert log.diff["changed"] == ["subject_template"]
    assert "Przypomnienie" not in str(log.diff)  # szablonów audyt nie niesie


def test_stage_rule_targets_entries_without_a_submission(competition, coordinator):
    from apps.submissions.tests.factories import SubmissionFactory

    stage = StageFactory(
        edition=CurrentEditionFactory(),
        kind=StageKind.ELIM,
        deadline_at=timezone.now() + dt.timedelta(days=3),
    )
    idle = ParticipantFactory(user=UserFactory(email="bez-pracy@example.test"))
    StageEntryFactory(participant=idle, stage=stage)
    done = ParticipantFactory(user=UserFactory(email="wyslal@example.test"))
    SubmissionFactory(entry=StageEntryFactory(participant=done, stage=stage), problem__stage=stage)

    rule, result = services.create_rule(
        competition=competition,
        actor=coordinator,
        kind=RuleKind.STAGE_DEADLINE,
        name="Etap I – przypomnienie",
        enabled=True,
        group="STAGE_NO_SUBMISSION",
        parameters={"stage": stage.pk},
        target={"stage": stage.pk, "label": str(stage)},
        stage=stage,
        subject_template="{etap}: termin za {dni}",
        body_template="Termin: {termin}.",
        link="",
        timing="DAY_AT",
        days_before=[30, 1],  # 30 dni – już minęło; 1 dzień – w oknie
        send_time=dt.time(9, 0),
    )

    assert result.created == 1
    broadcast = _scheduled().get()
    assert broadcast.subject.endswith("termin za 1 dzień")
    assert broadcast.parameters == {"stage": stage.pk}
    assert resolve_scheduled_recipients(broadcast) == ["bez-pracy@example.test"]
    assert MessageRuleOccurrence.objects.get(rule=rule).key == f"s:{stage.pk}:d1"


def test_author_without_the_coordinator_role_generates_nothing_and_sees_why(
    competition, coordinator, schedule
):
    rule, _ = workshop_rule(competition, coordinator, enabled=False)
    coordinator.groups.clear()
    MessageRule.objects.filter(pk=rule.pk).update(enabled=True)

    result = services.sync_rule(rule.pk)

    assert result.created == 0
    assert result.error
    rule.refresh_from_db()
    assert rule.last_error
    assert not MessageBroadcast.objects.exists()


def test_only_a_coordinator_saves_rules_and_bad_templates_are_rejected(competition, coordinator, schedule):
    with pytest.raises(PermissionDenied):
        workshop_rule(competition, UserFactory())
    with pytest.raises(ValidationError):
        workshop_rule(competition, coordinator, body_template="{temat} {etap}")
    with pytest.raises(ValidationError):
        workshop_rule(competition, coordinator, link="")
    with pytest.raises(ValidationError):
        workshop_rule(competition, coordinator, group="CUSTOM")

    assert not MessageRule.objects.exists()


def test_deleting_a_rule_withdraws_pending_and_keeps_history(competition, coordinator, schedule):
    rule, _ = workshop_rule(competition, coordinator)

    assert services.delete_rule(rule, actor=coordinator) == 2

    assert not MessageRule.objects.exists()
    assert MessageBroadcast.objects.filter(status=BroadcastStatus.CANCELLED).count() == 2
    assert AuditLog.objects.get(action="message_rule.deleted").diff["withdrawn"] == 2


def test_preview_lists_the_next_three_with_their_state(competition, coordinator, schedule):
    page, rows = schedule
    rule, _ = workshop_rule(competition, coordinator)
    cancel_scheduled_broadcast(_scheduled().first(), actor=coordinator)

    preview = services.upcoming(rule)

    states = [item["state"] for item in preview["items"]]
    assert len(states) == 3
    assert "anulowany" in states[0]
    assert "zaplanowany" in states[1]
    assert "zostanie zaplanowany" in states[2]
    assert preview["skipped"]  # wiersz bez daty
