"""Wysyłka komunikatów (``apps.accounts.messaging``) – warstwa danych, bez ekranu.

Testy ekranu leżą w ``apps/web/tests/test_coordinator_messages.py``. Tutaj sprawdzamy reguły,
które decydują o tym, do kogo naprawdę pójdzie list: kto wchodzi do grupy, kto z niej wypada
i jak wysyłka dzieli się na porcje.
"""

import pytest
from django.core import mail

from apps.accounts.messaging import (
    RECIPIENT_CHUNK,
    parse_address_list,
    resolve_recipients,
    send_broadcast,
)
from apps.accounts.models import BroadcastGroup, BroadcastStatus, MessageBroadcast, Voivodeship
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.models import StageEntryStatus, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def stage():
    return StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM)


def _participant(stage, email: str, *, status=StageEntryStatus.REGISTERED, **user_kwargs):
    participant = ParticipantFactory(user=UserFactory(email=email, **user_kwargs))
    StageEntryFactory(participant=participant, stage=stage, status=status)
    return participant


def test_address_list_accepts_commas_semicolons_and_newlines():
    text = " a@example.test ,b@example.test;\nc@example.test\n\n"

    assert parse_address_list(text) == ["a@example.test", "b@example.test", "c@example.test"]


def test_address_list_drops_duplicates_regardless_of_case():
    assert parse_address_list("A@example.test, a@example.test") == ["a@example.test"]


def test_edition_group_takes_participants_of_every_stage(stage):
    first = _participant(stage, "pierwszy@example.test")
    other_stage = StageFactory(edition=stage.edition, kind=StageKind.DISTRICT)
    second = _participant(other_stage, "drugi@example.test")

    recipients = resolve_recipients(BroadcastGroup.EDITION_PARTICIPANTS, edition=stage.edition)

    assert recipients == sorted([first.user.email, second.user.email])


def test_blocked_and_unverified_accounts_are_excluded(stage):
    active = _participant(stage, "aktywny@example.test")
    _participant(stage, "zablokowany@example.test", is_active=False)
    _participant(stage, "niepotwierdzony@example.test", email_verified_at=None)

    recipients = resolve_recipients(BroadcastGroup.EDITION_PARTICIPANTS, edition=stage.edition)

    assert recipients == [active.user.email]


def test_stage_group_ignores_other_stages(stage):
    here = _participant(stage, "tu@example.test")
    _participant(StageFactory(edition=stage.edition, kind=StageKind.FINAL), "gdzie-indziej@example.test")

    assert resolve_recipients(BroadcastGroup.STAGE_REGISTERED, stage=stage) == [here.user.email]


def test_qualified_group_takes_only_qualified_entries(stage):
    winner = _participant(stage, "awans@example.test", status=StageEntryStatus.QUALIFIED)
    _participant(stage, "bez-awansu@example.test", status=StageEntryStatus.NOT_QUALIFIED)

    assert resolve_recipients(BroadcastGroup.STAGE_QUALIFIED, stage=stage) == [winner.user.email]


def test_committee_group_skips_members_awaiting_approval():
    active = ActiveReviewerFactory()
    CommitteeMemberFactory()

    assert resolve_recipients(BroadcastGroup.COMMITTEE) == [active.user.email]


def test_committee_district_group_needs_a_voivodeship():
    ActiveReviewerFactory(district=Voivodeship.MAZOWIECKIE)

    assert resolve_recipients(BroadcastGroup.COMMITTEE_DISTRICT) == []


def test_group_without_its_context_resolves_to_nobody(stage):
    _participant(stage, "ktos@example.test")

    assert resolve_recipients(BroadcastGroup.STAGE_REGISTERED) == []
    assert resolve_recipients(BroadcastGroup.EDITION_PARTICIPANTS) == []


def test_unknown_group_resolves_to_nobody():
    assert resolve_recipients("NIE_MA_TAKIEJ") == []


def test_broadcast_is_split_into_chunks_and_every_letter_goes_out(django_capture_on_commit_callbacks):
    coordinator = CoordinatorFactory()
    recipients = [f"odbiorca{index}@example.test" for index in range(RECIPIENT_CHUNK + 5)]
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        broadcast = send_broadcast(
            group=BroadcastGroup.CUSTOM,
            subject="Temat",
            body="Treść",
            recipients=recipients,
            actor=coordinator,
        )

    broadcast.refresh_from_db()
    assert len(mail.outbox) == len(recipients)
    assert broadcast.sent_count == len(recipients)
    assert broadcast.status == BroadcastStatus.SENT
    log = AuditLog.objects.get(action="broadcast.sent")
    assert log.diff["chunks"] == 2


def test_register_keeps_the_content_but_never_the_addresses(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        send_broadcast(
            group=BroadcastGroup.CUSTOM,
            subject="Zmiana terminu",
            body="Etap rusza tydzień później.",
            recipients=["ktos@example.test"],
        )

    broadcast = MessageBroadcast.objects.get()
    log = AuditLog.objects.get(action="broadcast.sent")
    assert broadcast.body == "Etap rusza tydzień później."
    # Rejestr i audyt niosą liczniki, nigdy listy adresów.
    assert "ktos@example.test" not in str(log.diff)
    assert not hasattr(broadcast, "recipients")
