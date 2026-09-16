"""Ekran ``/coordinator/messages/`` – wysyłka komunikatów organizatora.

Reguły doboru odbiorców i podziału na porcje mają własne testy w
``apps/accounts/tests/test_messaging.py``. Tutaj sprawdzamy sam ekran, a w nim przede wszystkim
jedyny bezpiecznik przed omyłkową wysyłką do kilku tysięcy osób: **podgląd nic nie wysyła**.
"""

import pytest
from django.core import mail

from apps.accounts.models import BroadcastGroup, BroadcastStatus, MessageBroadcast
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

BASE = {"subject": "Zmiana terminu", "body": "Etap rusza tydzień później."}


def _post(client, data, *, action="preview"):
    return client.post("/coordinator/messages/", {**data, "action": action})


def test_preview_shows_the_count_and_sends_nothing(web_client, coordinator, elim_stage, entry):
    web_client.force_login(coordinator)
    mail.outbox.clear()

    response = _post(web_client, {**BASE, "group": BroadcastGroup.EDITION_PARTICIPANTS})
    content = response.content.decode()

    assert response.status_code == 200
    assert "1 odbiorców" in content
    # Treść widać dokładnie tak, jak pójdzie w liście – to jedyny moment, w którym pomyłkę
    # da się jeszcze cofnąć.
    assert BASE["body"] in content
    assert not mail.outbox
    assert not MessageBroadcast.objects.exists()


def test_send_queues_one_letter_per_recipient_and_records_the_broadcast(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    second = ParticipantFactory(user=UserFactory(email="druga@example.test"))
    StageEntryFactory(participant=second, stage=elim_stage)
    web_client.force_login(coordinator)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = _post(web_client, {**BASE, "group": BroadcastGroup.EDITION_PARTICIPANTS}, action="send")

    assert response.status_code == 302
    # Jeden list na odbiorcę – nigdy jedna koperta z listą adresów w nagłówku.
    assert len(mail.outbox) == 2
    assert all(len(message.to) == 1 for message in mail.outbox)
    broadcast = MessageBroadcast.objects.get()
    assert broadcast.recipient_count == 2
    assert broadcast.status == BroadcastStatus.SENT
    assert broadcast.created_by == coordinator


def test_audit_records_counters_but_no_addresses(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        _post(web_client, {**BASE, "group": BroadcastGroup.EDITION_PARTICIPANTS}, action="send")

    log = AuditLog.objects.get(action="broadcast.sent")
    assert log.diff["recipients"] == 1
    assert entry.participant.user.email not in str(log.diff)


def test_custom_list_requires_at_least_one_address(web_client, coordinator):
    web_client.force_login(coordinator)

    response = _post(web_client, {**BASE, "group": BroadcastGroup.CUSTOM, "addresses": "  "})

    assert response.status_code == 200
    assert "Wklej przynajmniej jeden adres." in response.content.decode()


def test_stage_group_requires_a_stage(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = _post(web_client, {**BASE, "group": BroadcastGroup.STAGE_REGISTERED})

    assert response.status_code == 200
    assert "wymaga wskazania etapu" in response.content.decode()


def test_sending_to_an_empty_group_records_nothing(
    web_client, coordinator, elim_stage, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = _post(
            web_client,
            {**BASE, "group": BroadcastGroup.STAGE_REGISTERED, "stage": elim_stage.pk},
            action="send",
        )

    assert response.status_code == 200
    assert not mail.outbox
    assert not MessageBroadcast.objects.exists()


def test_history_lists_past_broadcasts(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)
    with django_capture_on_commit_callbacks(execute=True):
        _post(web_client, {**BASE, "group": BroadcastGroup.EDITION_PARTICIPANTS}, action="send")

    response = web_client.get("/coordinator/messages/")

    assert BASE["subject"] in response.content.decode()


def test_page_is_forbidden_for_a_reviewer(web_client, reviewer):
    web_client.force_login(reviewer.user)

    assert web_client.get("/coordinator/messages/").status_code == 403


def test_dashboard_links_to_the_messages_screen(web_client, coordinator):
    web_client.force_login(coordinator)

    assert "/coordinator/messages/" in web_client.get("/coordinator/").content.decode()
