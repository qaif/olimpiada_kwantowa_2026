"""Powiadomienia w panelu uczestnika (UX-PANEL-01): zapis, scalanie, izolacja, widoki, haki, RODO."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.profile import anonymise_account
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.chat import announcements as chat_announcements
from apps.chat import services as chat
from apps.chat.tests.helpers import configure, coordinator_of, participant_of
from apps.competitions.tests.factories import CurrentEditionFactory, StageEntryFactory, StageFactory
from apps.notifications import announcements, hooks, services
from apps.notifications.kinds import NotificationKind, text_for
from apps.notifications.models import AnnouncementDelivery, Notification
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

LIST = "/me/notifications/"


def person(competition, **kwargs):
    return participant_of(competition, **kwargs)


def logged(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


# --- zapis i scalanie ------------------------------------------------------------------------------


def test_notify_creates_one_unread_row(competition):
    alice = person(competition)

    services.notify(
        alice.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me", suffix="?tab=reklamacje"
    )

    row = Notification.objects.get()
    assert (row.user_id, row.competition_id, row.read_at) == (alice.user_id, competition.pk, None)
    assert services.unread_count_for(alice.user, competition) == 1
    assert services.target_url(row) == "/me/?tab=reklamacje"


def test_same_key_bumps_the_unread_row_instead_of_adding_one(competition):
    alice = person(competition)
    first = timezone.now() - timedelta(hours=2)

    services.notify(
        alice.user_id, competition, NotificationKind.CHAT_PEER, "web:chat", key="chat:1", now=first
    )
    services.notify(alice.user_id, competition, NotificationKind.CHAT_PEER, "web:chat", key="chat:1")

    row = Notification.objects.get()
    assert row.created_at > first


def test_after_reading_the_same_key_starts_a_new_row(competition):
    alice = person(competition)
    services.notify(alice.user_id, competition, NotificationKind.CHAT_PEER, "web:chat", key="chat:1")
    services.mark_all_read(alice.user, competition)

    services.notify(alice.user_id, competition, NotificationKind.CHAT_PEER, "web:chat", key="chat:1")

    assert Notification.objects.count() == 2
    assert services.unread_count_for(alice.user, competition) == 1


def test_bulk_notify_costs_constant_queries(competition, django_assert_max_num_queries):
    people = [person(competition, first_name=f"Osoba{n}") for n in range(12)]

    with django_assert_max_num_queries(4):
        services.notify_many(
            [p.user_id for p in people],
            competition,
            NotificationKind.RESULTS_PUBLISHED,
            "web:me",
            key="results:1",
        )

    assert Notification.objects.count() == 12


def test_text_is_rendered_with_params_and_survives_missing_ones():
    assert text_for(NotificationKind.RESULTS_PUBLISHED, {"stage": "Etap I"}) == "Ogłoszono wyniki: Etap I"
    assert text_for(NotificationKind.RESULTS_PUBLISHED, {}) == "Nowe powiadomienie"
    assert text_for("removed_kind", {}) == "Nowe powiadomienie"


# --- izolacja ---------------------------------------------------------------------------------------


def test_counter_and_list_are_scoped_to_the_account_and_the_competition(competition, other_competition):
    alice = person(competition)
    bob = person(competition, first_name="Bob")
    services.notify(alice.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me")
    services.notify(alice.user_id, other_competition, NotificationKind.APPEAL_DECIDED, "web:me")
    services.notify(bob.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me")

    assert services.unread_count_for(alice.user, competition) == 1
    assert services.unread_count_for(alice.user, other_competition) == 1
    assert list(
        services.page_for(alice.user, competition, 1).object_list.values_list("user_id", flat=True)
    ) == [alice.user_id]


def test_opening_someone_elses_notification_is_404(client_for, competition, other_competition):
    alice = person(competition)
    bob = person(competition, first_name="Bob")
    services.notify(bob.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me")
    services.notify(alice.user_id, other_competition, NotificationKind.APPEAL_DECIDED, "web:me")
    client = logged(client_for, competition, alice.user)

    for row in Notification.objects.all():
        assert client.get(reverse("web:participant-notification-open", args=[row.pk])).status_code == 404
    assert not Notification.objects.filter(read_at__isnull=False).exists()


# --- widoki -----------------------------------------------------------------------------------------


def test_list_shows_text_and_unread_marker(client_for, competition):
    alice = person(competition)
    services.notify(
        alice.user_id, competition, NotificationKind.RESULTS_PUBLISHED, "web:me", params={"stage": "Finał"}
    )

    body = logged(client_for, competition, alice.user).get(LIST).content.decode()

    assert "Ogłoszono wyniki: Finał" in body
    assert "pp-note--unread" in body
    assert reverse("web:participant-notifications-read-all") in body


def test_open_marks_read_and_redirects_to_the_target(client_for, competition):
    alice = person(competition)
    services.notify(
        alice.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me", suffix="?tab=reklamacje"
    )
    row = Notification.objects.get()

    response = logged(client_for, competition, alice.user).get(
        reverse("web:participant-notification-open", args=[row.pk])
    )

    assert response.status_code == 302
    assert response["Location"] == "/me/?tab=reklamacje"
    row.refresh_from_db()
    assert row.read_at is not None


def test_target_that_no_longer_exists_falls_back_to_the_list(client_for, competition):
    alice = person(competition)
    services.notify(alice.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:no-such-screen")
    row = Notification.objects.get()

    response = logged(client_for, competition, alice.user).get(
        reverse("web:participant-notification-open", args=[row.pk])
    )

    assert response["Location"] == LIST


def test_mark_all_read_is_a_post_and_touches_only_own_rows(client_for, competition):
    alice = person(competition)
    bob = person(competition, first_name="Bob")
    for user_id in (alice.user_id, alice.user_id, bob.user_id):
        services.notify(user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me")
    client = logged(client_for, competition, alice.user)

    assert client.get(reverse("web:participant-notifications-read-all")).status_code == 405
    response = client.post(reverse("web:participant-notifications-read-all"))

    assert response.status_code == 302
    assert services.unread_count_for(alice.user, competition) == 0
    assert services.unread_count_for(bob.user, competition) == 1


def test_list_is_for_participants_only(client_for, competition):
    coordinator = coordinator_of(competition)
    assert client_for(competition).get(LIST).status_code == 302
    assert logged(client_for, competition, coordinator).get(LIST).status_code == 403


# --- haki źródeł ------------------------------------------------------------------------------------


def test_message_from_the_organizer_notifies_the_participant_and_reading_clears_it(competition):
    configure(competition)
    alice = person(competition)
    coordinator = coordinator_of(competition)

    message = chat.organizer_writes_to(
        user=coordinator, competition=competition, participant=alice, body="Dzień dobry"
    )

    row = Notification.objects.get(user=alice.user)
    assert row.kind == NotificationKind.CHAT_ORGANIZER
    assert row.dedupe_key == hooks.chat_key(message.conversation_id)
    assert "Dzień dobry" not in str(row.params)

    member = chat.membership(alice, message.conversation_id)
    chat.mark_read(member)
    row.refresh_from_db()
    assert row.read_at is not None


def test_participant_writing_to_the_organizer_notifies_nobody(competition):
    configure(competition)
    alice = person(competition)
    coordinator_of(competition)

    chat.write_to_organizer(user=alice.user, competition=competition, body="Pytanie o termin")

    assert not Notification.objects.exists()


def test_student_status_decision_notifies_the_participant(competition):
    from apps.student_status import services as student_status
    from apps.student_status.tests.helpers import enable, make_certificate

    enable(competition)
    edition = CurrentEditionFactory(competition=competition)
    alice = person(competition)
    certificate = make_certificate(alice, edition)

    student_status.reject(certificate, "Brak pieczątki", actor=CoordinatorFactory())

    row = Notification.objects.get(user=alice.user)
    assert row.kind == NotificationKind.STUDENT_STATUS_REJECTED
    assert "pieczątki" not in str(row.params)
    assert services.target_url(row) == reverse("web:student-status")


def test_results_published_notifies_every_entry_of_the_stage(competition):
    from apps.results.models import ResultsPublication
    from apps.submissions.notifications import notify_results_published

    stage = StageFactory(
        competition=competition, edition=CurrentEditionFactory(competition=competition), name="Etap I"
    )
    entries = [
        StageEntryFactory(stage=stage, participant=person(competition, first_name=f"U{n}")) for n in range(3)
    ]
    publication, _ = ResultsPublication.objects.get_or_create(stage=stage)

    notify_results_published(publication)

    rows = Notification.objects.filter(kind=NotificationKind.RESULTS_PUBLISHED)
    assert {row.user_id for row in rows} == {entry.participant.user_id for entry in entries}
    assert rows[0].params == {"stage": "Etap I"}


def test_appeal_decision_hook(competition):
    alice = person(competition)

    hooks.appeal_decided(SimpleNamespace(pk=7, filed_by=alice))

    row = Notification.objects.get()
    assert services.target_url(row) == "/me/?tab=reklamacje#sekcja"


def test_forum_reply_notifies_participant_subscribers_but_not_the_author_or_staff(competition):
    from apps.forum.models import ForumSubscription
    from apps.forum.notifications import post_published
    from apps.forum.tests.factories import ForumPostFactory, ForumThreadFactory

    alice = person(competition)
    author = person(competition, first_name="Autor")
    coordinator = coordinator_of(competition)
    thread = ForumThreadFactory(competition=competition)
    for user in (alice.user, author.user, coordinator):
        ForumSubscription.objects.create(competition=competition, thread=thread, user=user)
    post = ForumPostFactory(competition=competition, thread=thread, author=author.user)

    post_published(post)
    post_published(post)

    rows = list(Notification.objects.all())
    assert [(row.user_id, row.kind) for row in rows] == [(alice.user_id, NotificationKind.FORUM_REPLY)]
    assert services.target_url(rows[0]) == reverse("web:forum-thread", args=[thread.pk])


def test_a_failing_hook_does_not_break_the_source(competition, monkeypatch):
    configure(competition)
    alice = person(competition)
    coordinator = coordinator_of(competition)

    def boom(*args, **kwargs):
        raise RuntimeError("baza powiadomień leży")

    monkeypatch.setattr(services, "notify_many", boom)
    message = chat.organizer_writes_to(
        user=coordinator, competition=competition, participant=alice, body="Hej"
    )

    assert message.pk is not None
    assert not Notification.objects.exists()


# --- ogłoszenia organizatora -----------------------------------------------------------------------


def test_publishing_an_announcement_notifies_current_participants_once(
    competition, other_competition, django_capture_on_commit_callbacks
):
    alice = person(competition)
    outsider = ParticipantFactory(competition=other_competition)
    coordinator = coordinator_of(competition)

    with django_capture_on_commit_callbacks(execute=True):
        announcement = chat_announcements.create_announcement(
            competition=competition,
            title="Zmiana sali",
            body="Finał w sali 101.",
            actor=coordinator,
            publish=True,
        )
    announcements.deliver_due()

    rows = list(Notification.objects.all())
    assert [(row.user_id, row.params) for row in rows] == [(alice.user_id, {"title": "Zmiana sali"})]
    assert outsider.user_id not in {row.user_id for row in rows}
    assert AnnouncementDelivery.objects.get(announcement=announcement).recipients == 1


def test_scheduled_announcement_is_delivered_when_its_window_opens(competition):
    alice = person(competition)
    coordinator = coordinator_of(competition)
    start = timezone.now() + timedelta(hours=1)
    announcement = chat_announcements.create_announcement(
        competition=competition, title="Jutro", body="Treść", actor=coordinator, published_from=start
    )
    chat_announcements.set_published(announcement=announcement, actor=coordinator, published=True)

    assert announcements.deliver_due() == 0
    assert announcements.deliver_due(now=start + timedelta(minutes=5)) == 1
    assert announcements.deliver_due(now=start + timedelta(minutes=15)) == 0
    assert Notification.objects.filter(user=alice.user).count() == 1


# --- RODO -------------------------------------------------------------------------------------------


def test_retention_purges_rows_older_than_the_limit(competition, settings):
    settings.NOTIFICATION_RETENTION_DAYS = 90
    alice = person(competition)
    now = timezone.now()
    services.notify(
        alice.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me", now=now - timedelta(days=91)
    )
    services.notify(
        alice.user_id, competition, NotificationKind.RESULTS_PUBLISHED, "web:me", now=now - timedelta(days=89)
    )

    assert services.purge_expired(now) == 1
    assert list(Notification.objects.values_list("kind", flat=True)) == [NotificationKind.RESULTS_PUBLISHED]


def test_anonymisation_removes_the_notifications(competition):
    alice = person(competition)
    services.notify(alice.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me")

    anonymise_account(alice.user)

    assert not Notification.objects.exists()


def test_export_lists_own_notifications_without_content(competition):
    from apps.accounts.data_export import export_payload

    alice = person(competition)
    services.notify(alice.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me")

    section = export_payload(alice.user)["powiadomienia_w_panelu"]

    assert [row["rodzaj"] for row in section] == [NotificationKind.APPEAL_DECIDED]


def test_hooks_skip_accounts_without_a_participant_profile(competition):
    user = UserFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)

    assert services.participant_user_ids(competition, [user.pk]) == set()
