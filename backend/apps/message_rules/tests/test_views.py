"""Ekran „Komunikacja → Automatyczne przypomnienia” – ``/coordinator/messages/rules/``."""

from __future__ import annotations

import datetime as dt

import pytest
from django.test import Client

from apps.accounts.models import BroadcastStatus, MessageBroadcast
from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.core.models import AuditLog
from apps.message_rules.models import MessageRule, RuleKind
from apps.message_rules.tests.helpers import MEET, row, today, workshop_rule, workshops_page_for

pytestmark = pytest.mark.django_db

LIST = "/coordinator/messages/rules/"
NEW = "/coordinator/messages/rules/new/"

FORM = {
    "name": "Sobota 6:00",
    "enabled": "on",
    "group": "ALL_PARTICIPANTS_AND_TEACHERS",
    "timing": "DAY_AT",
    "days_before": "0",
    "send_time": "06:00",
    "link": MEET,
    "subject": "Dziś warsztat: {temat}",
    "body": "{temat} – {data}, {godziny}. Link: {link}",
}


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def client(coordinator):
    client = Client()
    client.force_login(coordinator)
    return client


@pytest.fixture
def page(competition):
    return workshops_page_for(competition, [row("Algebra liniowa", today() + dt.timedelta(days=2))])


def test_saving_a_workshop_rule_schedules_its_reminder(client, coordinator, page):
    response = client.post(f"{NEW}?kind=workshop", {**FORM, "action": "save"})

    assert response.status_code == 302
    rule = MessageRule.objects.get()
    assert rule.kind == RuleKind.WORKSHOP
    assert rule.created_by == coordinator
    assert rule.parameters == {"include_past_editions": False}
    broadcast = MessageBroadcast.objects.get(status=BroadcastStatus.SCHEDULED)
    assert broadcast.subject == "Dziś warsztat: Algebra liniowa"
    assert AuditLog.objects.filter(action="message_rule.created").count() == 1

    edit = client.get(response["Location"]).content.decode()
    assert "Dziś warsztat: Algebra liniowa" in edit
    assert "zaplanowany" in edit


def test_unknown_placeholder_is_a_form_error_and_saves_nothing(client, page):
    response = client.post(
        f"{NEW}?kind=workshop", {**FORM, "body": "Prowadzi {prowadzacz}", "action": "save"}
    )

    assert response.status_code == 400
    assert "{prowadzacz}" in response.content.decode()
    assert not MessageRule.objects.exists()


def test_link_placeholder_needs_a_link(client, page):
    response = client.post(f"{NEW}?kind=workshop", {**FORM, "link": "", "action": "save"})

    assert response.status_code == 400
    assert not MessageRule.objects.exists()


def test_preview_fills_the_template_without_saving(client, page):
    response = client.post(f"{NEW}?kind=workshop", {**FORM, "action": "preview"})

    content = response.content.decode()
    assert response.status_code == 200
    assert "Dziś warsztat: Algebra liniowa" in content
    assert MEET in content
    assert not MessageRule.objects.exists()
    assert not MessageBroadcast.objects.exists()


def test_list_shows_only_rules_of_this_competition(client, competition, coordinator, other_competition, page):
    workshop_rule(competition, coordinator, name="Nasza reguła")
    MessageRule.objects.create(
        competition=other_competition,
        name="Cudza reguła",
        kind=RuleKind.WORKSHOP,
        group="ALL_PARTICIPANTS",
        subject_template="x",
        body_template="y",
        days_before=[0],
    )

    content = client.get(LIST).content.decode()

    assert "Nasza reguła" in content
    assert "Cudza reguła" not in content


def test_rule_of_another_competition_is_404(client, other_competition):
    foreign = MessageRule.objects.create(
        competition=other_competition,
        name="Cudza",
        kind=RuleKind.WORKSHOP,
        group="ALL_PARTICIPANTS",
        subject_template="x",
        body_template="y",
        days_before=[0],
    )

    assert client.get(f"{LIST}{foreign.pk}/").status_code == 404
    assert client.post(f"{LIST}{foreign.pk}/toggle/").status_code == 404
    assert client.post(f"{LIST}{foreign.pk}/delete/").status_code == 404
    assert MessageRule.objects.filter(pk=foreign.pk, enabled=True).exists()


def test_reviewer_is_forbidden_and_actions_need_post(client, competition, coordinator, page):
    rule, _ = workshop_rule(competition, coordinator)
    reviewer = Client()
    reviewer.force_login(ActiveReviewerFactory().user)

    assert reviewer.get(LIST).status_code == 403
    assert reviewer.post(f"{LIST}{rule.pk}/toggle/").status_code == 403
    assert client.get(f"{LIST}{rule.pk}/toggle/").status_code == 405
    assert client.get(f"{LIST}{rule.pk}/delete/").status_code == 405
    assert MessageRule.objects.filter(pk=rule.pk, enabled=True).exists()


def test_toggle_and_delete(client, competition, coordinator, page):
    rule, _ = workshop_rule(competition, coordinator)

    assert client.post(f"{LIST}{rule.pk}/toggle/").status_code == 302
    rule.refresh_from_db()
    assert not rule.enabled
    assert not MessageBroadcast.objects.filter(status=BroadcastStatus.SCHEDULED).exists()

    assert client.post(f"{LIST}{rule.pk}/delete/").status_code == 302
    assert not MessageRule.objects.exists()
    assert AuditLog.objects.filter(action="message_rule.deleted").exists()


def test_stage_rule_form_requires_the_stage(client, page):
    response = client.post(
        f"{NEW}?kind=stage",
        {**FORM, "group": "ALL_PARTICIPANTS", "subject": "{etap}", "body": "{termin}", "action": "save"},
    )

    assert response.status_code == 400
    assert not MessageRule.objects.exists()


def test_unknown_kind_is_404(client):
    assert client.get(f"{NEW}?kind=cokolwiek").status_code == 404
