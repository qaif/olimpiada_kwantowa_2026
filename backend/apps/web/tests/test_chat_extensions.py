"""Ekrany rozszerzeń § 12 zadania CZ-01: szablony odpowiedzi, przypisanie i stan w skrzynce
koordynatora, zasada wieku i limit w ustawieniach."""

from __future__ import annotations

import pytest

from apps.chat import services
from apps.chat.models import AgePolicy, ChatSettings, ConversationStatus, ReplyTemplate
from apps.chat.tests.helpers import coordinator_of, participant_of

pytestmark = pytest.mark.django_db

TEMPLATES = "/coordinator/chat/templates/"
INBOX = "/coordinator/chat/"


def thread(conversation) -> str:
    return f"/coordinator/chat/{conversation.pk}/"


def test_template_crud_on_one_screen(web_client, competition):
    web_client.force_login(coordinator_of(competition))

    web_client.post(TEMPLATES, {"title": "Terminy", "body": "Cześć {imie}, terminy są na stronie."})
    template = ReplyTemplate.objects.get()
    edit_page = web_client.get(f"{TEMPLATES}?edit={template.pk}").content.decode()
    web_client.post(TEMPLATES, {"template": template.pk, "title": "Terminy etapów", "body": "Nowa {imie}"})
    template.refresh_from_db()
    web_client.post(TEMPLATES, {"template": template.pk, "action": "delete"})

    assert "Edytuj szablon" in edit_page
    assert template.title == "Terminy etapów"
    assert not ReplyTemplate.objects.exists()


def test_thread_offers_templates_with_the_participant_first_name(web_client, competition):
    ala = participant_of(competition, "Alicja", "Kowalska")
    boss = coordinator_of(competition)
    services.save_template(competition=competition, actor=boss, title="Powitanie", body="Cześć {imie}!")
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation
    web_client.force_login(boss)

    body = web_client.get(thread(conversation)).content.decode()

    assert 'data-chat-template="Cześć {imie}!"' in body
    assert 'data-first-name="Alicja"' in body
    assert "chat-templates.js" in body
    assert "Odpowiedz i zamknij" in body


def test_reply_and_close_by_htmx(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation
    web_client.force_login(coordinator_of(competition))

    web_client.post(thread(conversation), {"body": "Załatwione", "close": "1"}, HTTP_HX_REQUEST="true")

    conversation.refresh_from_db()
    assert conversation.status == ConversationStatus.CLOSED


def test_assign_to_me_and_status_from_the_thread(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation
    boss = coordinator_of(competition)
    web_client.force_login(boss)

    web_client.post(thread(conversation), {"action": "assign-me"})
    web_client.post(thread(conversation), {"action": "status", "status": "WAITING"})

    conversation.refresh_from_db()
    assert conversation.assigned_to == boss
    assert conversation.status == ConversationStatus.WAITING
    assert not conversation.messages.filter(body="").exists()


def test_inbox_filters_show_counts_and_default_to_open(web_client, competition):
    boss = coordinator_of(competition)
    open_one = services.write_to_organizer(
        user=participant_of(competition, "Otwarta", "Sprawa").user, competition=competition, body="OTWARTA"
    ).conversation
    closed_one = services.write_to_organizer(
        user=participant_of(competition, "Zamknieta", "Sprawa").user,
        competition=competition,
        body="ZAMKNIETA",
    ).conversation
    services.set_status(conversation=closed_one, actor=boss, competition=competition, status="CLOSED")
    services.assign(conversation=open_one, actor=boss, competition=competition, assignee=boss)
    web_client.force_login(boss)

    default = web_client.get(INBOX).content.decode()
    mine = web_client.get(f"{INBOX}?filter=mine&status=OPEN").content.decode()
    closed = web_client.get(f"{INBOX}?status=CLOSED").content.decode()

    assert "Otwarta Sprawa" in default and "Zamknieta Sprawa" not in default
    assert "otwarta (1)" in default and "zamknięta (1)" in default
    assert "Moje (1)" in mine and "Otwarta Sprawa" in mine
    assert "Zamknieta Sprawa" in closed


def test_participant_never_sees_assignment_or_status(web_client, competition):
    ala = participant_of(competition)
    boss = coordinator_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation
    services.assign(conversation=conversation, actor=boss, competition=competition, assignee=boss)
    web_client.force_login(ala.user)

    body = web_client.get(f"/me/messages/{conversation.pk}/").content.decode()

    assert "przypisana" not in body
    assert "czeka na uczestnika" not in body
    assert "Przypisz" not in body
    assert "data-chat-template" not in body


def test_settings_save_age_policy_and_daily_limit(web_client, competition):
    web_client.force_login(coordinator_of(competition))

    page = web_client.get("/coordinator/chat/settings/").content.decode()
    web_client.post(
        "/coordinator/chat/settings/",
        {"enabled": "on", "peer_mode": "NONE", "age_policy": "ANY", "daily_new_conversations": "7"},
    )

    row = ChatSettings.objects.get(competition=competition)
    assert "dorośli uczestnicy będą mogli rozmawiać" in page
    assert (row.age_policy, row.daily_new_conversations) == (AgePolicy.ANY, 7)
