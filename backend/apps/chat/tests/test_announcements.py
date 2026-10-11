"""Ogłoszenia organizatora (CZ-ANN-01) – reguła widoczności, zapisy, rola i audyt.

Ekrany (skrzynka, pulpit, panel koordynatora) mają własny plik
``apps/web/tests/test_chat_announcements.py``; tutaj to, co serwis gwarantuje niezależnie od ekranu.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chat import announcements
from apps.chat.models import OrganizerAnnouncement
from apps.chat.tests.helpers import coordinator_of, participant_of
from apps.core.api import DomainError
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def actions(announcement_pk=None) -> list[str]:
    rows = AuditLog.objects.filter(action__startswith="chat.announcement.")
    if announcement_pk is not None:
        rows = rows.filter(target_id=str(announcement_pk))
    return list(rows.order_by("id").values_list("action", flat=True))


def draft(competition, actor, **kwargs) -> OrganizerAnnouncement:
    return announcements.create_announcement(
        competition=competition,
        title=kwargs.pop("title", "Warsztaty"),
        body=kwargs.pop("body", "Link: https://meet.google.com/abc-defg-hij"),
        actor=actor,
        **kwargs,
    )


# --- widoczność ----------------------------------------------------------------------------------------


def test_published_announcement_is_visible_and_a_draft_is_not(competition):
    coordinator = coordinator_of(competition)
    published = announcements.publish_announcement(
        competition=competition, title="Warsztaty", body="https://meet.google.com/x", actor=coordinator
    )
    draft(competition, coordinator, title="Szkic")

    assert announcements.visible_announcements(competition) == [published]


def test_window_hides_scheduled_and_expired_announcements(competition):
    coordinator = coordinator_of(competition)
    now = timezone.now()
    scheduled = draft(competition, coordinator, title="Jutro", published_from=now + timedelta(days=1))
    expired = draft(
        competition,
        coordinator,
        title="Wczoraj",
        published_from=now - timedelta(days=2),
        published_until=now - timedelta(days=1),
    )
    current = draft(competition, coordinator, title="Teraz", published_until=now + timedelta(days=1))
    for item in (scheduled, expired, current):
        announcements.set_published(announcement=item, actor=coordinator, published=True)

    assert announcements.visible_announcements(competition, now) == [current]
    assert announcements.visible_announcements(competition, now + timedelta(days=1, hours=1)) == [scheduled]
    assert scheduled.state(now) == "scheduled"
    assert expired.state(now) == "expired"
    assert current.state(now) == "live"


def test_announcement_of_another_competition_is_not_visible(competition, other_competition):
    announcements.publish_announcement(competition=other_competition, title="Obce", body="x", actor=None)

    assert announcements.visible_announcements(competition) == []
    assert announcements.visible_announcements(None) == []


def test_newest_first_and_republishing_moves_to_the_top(competition):
    coordinator = coordinator_of(competition)
    first = announcements.publish_announcement(
        competition=competition, title="Pierwsze", body="a", actor=coordinator
    )
    second = announcements.publish_announcement(
        competition=competition, title="Drugie", body="b", actor=coordinator
    )
    assert announcements.visible_announcements(competition) == [second, first]

    announcements.set_published(announcement=first, actor=coordinator, published=False)
    announcements.set_published(announcement=first, actor=coordinator, published=True)

    assert announcements.visible_announcements(competition) == [first, second]


def test_visible_list_is_capped(competition):
    for index in range(announcements.VISIBLE_LIMIT + 2):
        announcements.publish_announcement(competition=competition, title=f"N{index}", body="x", actor=None)

    assert len(announcements.visible_announcements(competition)) == announcements.VISIBLE_LIMIT


# --- zapisy, rola i audyt ------------------------------------------------------------------------------


def test_publish_announcement_from_the_shell_without_actor(competition):
    item = announcements.publish_announcement(
        competition=competition, title="Warsztaty online", body="https://meet.google.com/x", actor=None
    )

    assert item.is_published and item.published_at is not None and item.created_by is None
    assert actions(item.pk) == ["chat.announcement.created", "chat.announcement.published"]
    entry = AuditLog.objects.get(action="chat.announcement.published")
    # Konkurs wpisu jest konkursem ogłoszenia także poza żądaniem; treści w ``diff`` nie ma.
    assert entry.competition_id == competition.pk
    assert entry.actor_id is None
    assert "body" not in entry.diff and entry.diff["title"] == "Warsztaty online"


def test_only_a_coordinator_may_write(competition):
    """Uczestnik nie publikuje, nie wyłącza i nie usuwa – serwis odmawia, nie tylko ekran.

    Koordynatora **innego** konkursu sprawdza ekran (404 na cudzym ogłoszeniu,
    ``apps/web/tests/test_chat_announcements.py``): w Konkursie #1 bez wymuszonych członkostw rola
    pochodzi z grupy Django, więc test serwisu na samym koncie nie rozróżniłby konkursów.
    """
    participant = participant_of(competition)

    with pytest.raises(DomainError) as exc:
        announcements.publish_announcement(
            competition=competition, title="x", body="y", actor=participant.user
        )
    assert exc.value.status_code == 403
    item = announcements.publish_announcement(competition=competition, title="x", body="y", actor=None)
    with pytest.raises(DomainError):
        announcements.set_published(announcement=item, actor=participant.user, published=False)
    with pytest.raises(DomainError):
        announcements.delete_announcement(announcement=item, actor=participant.user)
    assert OrganizerAnnouncement.objects.filter(pk=item.pk, is_published=True).exists()


def test_unpublish_update_and_delete_are_audited_and_repeats_are_not(competition):
    coordinator = coordinator_of(competition)
    item = draft(competition, coordinator)
    announcements.set_published(announcement=item, actor=coordinator, published=True)
    announcements.set_published(announcement=item, actor=coordinator, published=True)
    announcements.set_published(announcement=item, actor=coordinator, published=False)
    announcements.update_announcement(announcement=item, actor=coordinator, title="Nowy tytuł", body="Treść")
    pk = item.pk
    announcements.delete_announcement(announcement=item, actor=coordinator)

    assert actions(pk) == [
        "chat.announcement.created",
        "chat.announcement.published",
        "chat.announcement.unpublished",
        "chat.announcement.updated",
        "chat.announcement.deleted",
    ]
    assert not OrganizerAnnouncement.objects.filter(pk=pk).exists()


@pytest.mark.parametrize(
    ("title", "body"),
    [("", "treść"), ("tytuł", "   "), ("t" * 201, "x"), ("t", "x" * 4001)],
)
def test_empty_or_too_long_announcement_is_refused(competition, title, body):
    with pytest.raises(DomainError) as exc:
        announcements.publish_announcement(competition=competition, title=title, body=body, actor=None)

    assert exc.value.status_code == 400
    assert not OrganizerAnnouncement.objects.exists()


def test_window_must_have_positive_length_in_the_service_and_in_the_database(competition):
    now = timezone.now()
    with pytest.raises(DomainError):
        announcements.create_announcement(
            competition=competition, title="x", body="y", actor=None, published_from=now, published_until=now
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizerAnnouncement.objects.create(
            competition=competition, title="x", body="y", published_from=now, published_until=now
        )
