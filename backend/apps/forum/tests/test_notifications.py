"""Powiadomienia e-mail z forum: zbiorczość, limity, obserwacje, wypis i to, czego list nie niesie.

Suita pilnuje **reguł wysyłki** (``apps.forum.notifications``), a nie ekranów – przyciski
„Obserwuj wątek”, blok ustawień na profilu i strona wypisu mają własne testy w
``apps/web/tests/test_forum_notifications.py``.

Najważniejsze są tu trzy rzeczy, bo są regułami bezpieczeństwa, a nie funkcjami:

- **nic niepublikowanego nie wychodzi pocztą** – ani treść, ani temat wątku czekającego na
  moderację, ani wpis zatwierdzony i zaraz ukryty (regulamin § 10 ust. 2 i § 17: w czasie
  otwartego etapu moderacja wstępna obowiązuje zawsze),
- **izolacja konkursów** – koordynator olimpiady B nie dostaje listu o kolejce olimpiady A,
- **konta, do których nie wolno pisać**, nie dostają nic: nieaktywne, niepotwierdzone,
  zanonimizowane.

Czas jest zamrożony (``freezegun``), bo reguły są czasem: opóźnienie pierwszego listu o kolejce,
limit listów o wątku i przebieg dzienny.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.core import mail, signing
from freezegun import freeze_time

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.forum import notifications, services, tasks
from apps.forum.models import (
    DecisionKind,
    ForumDecisionNotice,
    ForumNotificationSettings,
    ForumSubscription,
    ModerationMode,
    ModerationStatus,
    NotificationFrequency,
)
from apps.forum.tests.factories import ForumCategoryFactory, ForumSettingsFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

#: Chwila startowa. Środek dnia w UTC, żeby „+ kilka godzin” nie przechodziło przez północ.
START = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)

SECRET_BODY = "TREŚĆ-WPISU-KTÓREJ-NIE-MA-W-LIŚCIE"
PENDING_TITLE = "TEMAT-CZEKAJĄCY-NA-MODERACJĘ"


# --- pomocnicze -----------------------------------------------------------------------------------


def forum_on(competition, *, mode=ModerationMode.POST, **flags):
    """Konkurs z włączonym forum – **zapisany**, bo przebieg zadania czyta konkurs z bazy."""
    competition.feature_flags = {**(competition.feature_flags or {}), "participant_forum": True, **flags}
    competition.save(update_fields=["feature_flags"])
    ForumSettingsFactory(competition=competition, mode=mode)
    return competition


def participant_of(competition, **kwargs):
    profile = ParticipantFactory(competition=competition, **kwargs)
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    return profile.user


def coordinator_of(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


def start_thread(competition, author, title="Pytanie o salę", body="Gdzie jest sala 101?"):
    category = ForumCategoryFactory(competition=competition)
    return services.create_thread(
        user=author, competition=competition, category=category, title=title, body=body
    )


def run(django_capture_on_commit_callbacks, *, daily=False):
    """Jeden przebieg zadania – z wykonaniem kolejkowania „po commicie”, jak na produkcji."""
    with django_capture_on_commit_callbacks(execute=True):
        if daily:
            return tasks.send_daily_forum_digest()
        return tasks.send_forum_notifications()


def outbox_for(user):
    return [message for message in mail.outbox if user.email in message.to]


# --- ustawienia domyślne ----------------------------------------------------------------------------


def test_defaults_without_a_row_are_immediate_and_moderation_on(competition):
    """Brak wiersza znaczy „domyślne” – i odczyt niczego w bazie nie zakłada."""
    user = participant_of(competition)

    current = notifications.preferences_for(user)

    assert current.frequency == NotificationFrequency.IMMEDIATE
    assert current.moderation_digest is True
    assert current.pk is None
    assert not ForumNotificationSettings.objects.filter(user=user).exists()


# --- obserwacje ------------------------------------------------------------------------------------


def test_starting_a_thread_and_replying_follows_it(competition):
    forum_on(competition)
    author, other = participant_of(competition), participant_of(competition)

    thread = start_thread(competition, author)
    services.reply(user=other, competition=competition, thread=thread, body="Na parterze.")

    assert notifications.is_following(author, thread)
    assert notifications.is_following(other, thread)


def test_an_explicit_unfollow_survives_the_next_reply(competition):
    """„Przestań obserwować” jest jawnym wyborem – automat przy kolejnym wpisie go nie odwraca."""
    forum_on(competition)
    author = participant_of(competition)
    thread = start_thread(competition, author)

    notifications.set_following(author, thread, False)
    services.reply(user=author, competition=competition, thread=thread, body="Jeszcze jedno.")

    assert not notifications.is_following(author, thread)


# --- listy o odpowiedziach ----------------------------------------------------------------------------


def test_a_published_reply_notifies_the_thread_author_but_not_the_replier(
    competition, django_capture_on_commit_callbacks
):
    forum_on(competition)
    author, replier = participant_of(competition), participant_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author, title="Sala na finale")
        services.reply(user=replier, competition=competition, thread=thread, body=SECRET_BODY)
        run(django_capture_on_commit_callbacks)

    [message] = outbox_for(author)
    assert message.subject == "[Olimpiada Kwantowa] Forum: nowe odpowiedzi w wątku „Sala na finale”"
    assert f"/forum/t/{thread.pk}/" in message.body
    assert "nowe wpisy: 1" in message.body
    # Treść wpisu nie jedzie pocztą – nawet opublikowana (docstring ``apps.forum.notifications``).
    assert SECRET_BODY not in message.body
    assert outbox_for(replier) == []


def test_one_mail_per_thread_per_interval(competition, django_capture_on_commit_callbacks, settings):
    settings.FORUM_THREAD_NOTIFY_INTERVAL_HOURS = 4
    forum_on(competition)
    author, replier = participant_of(competition), participant_of(competition)
    with freeze_time(START) as frozen:
        thread = start_thread(competition, author)
        services.reply(user=replier, competition=competition, thread=thread, body="Pierwsza.")
        run(django_capture_on_commit_callbacks)

        frozen.move_to(START + timedelta(hours=1))
        services.reply(user=replier, competition=competition, thread=thread, body="Druga.")
        services.reply(user=replier, competition=competition, thread=thread, body="Trzecia.")
        run(django_capture_on_commit_callbacks)
        assert len(outbox_for(author)) == 1

        frozen.move_to(START + timedelta(hours=4, minutes=1))
        run(django_capture_on_commit_callbacks)

    first, second = outbox_for(author)
    assert "nowe wpisy: 2" in second.body


def test_a_coordinator_answer_has_its_own_subject(competition, django_capture_on_commit_callbacks):
    forum_on(competition)
    author, coordinator = participant_of(competition), coordinator_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author, title="Czy kalkulator jest dozwolony")
        services.reply(user=coordinator, competition=competition, thread=thread, body="Tak, prosty.")
        run(django_capture_on_commit_callbacks)

    [message] = outbox_for(author)
    assert "organizator odpowiedział w wątku „Czy kalkulator jest dozwolony”" in message.subject
    assert "odpowiedział organizator" in message.body


def test_a_committee_answer_has_its_own_subject(competition, django_capture_on_commit_callbacks):
    forum_on(competition)
    author = participant_of(competition)
    reviewer = ActiveReviewerFactory(competition=competition).user
    grant_membership(reviewer, competition, CompetitionRole.REVIEWER)
    with freeze_time(START):
        thread = start_thread(competition, author, title="Zadanie 3")
        services.reply(user=reviewer, competition=competition, thread=thread, body="Doprecyzowanie.")
        run(django_capture_on_commit_callbacks)

    [message] = outbox_for(author)
    assert "komitet odpowiedział w wątku „Zadanie 3”" in message.subject


def test_a_pending_reply_sends_nothing_until_it_is_approved(competition, django_capture_on_commit_callbacks):
    """Tryb ``PRE``: wpis czekający na moderację nie istnieje dla poczty."""
    forum_on(competition, mode=ModerationMode.PRE)
    author, replier = participant_of(competition), participant_of(competition)
    coordinator = coordinator_of(competition)
    with freeze_time(START) as frozen:
        thread = start_thread(competition, author)
        services.moderate_thread(thread=thread, actor=coordinator, status=ModerationStatus.PUBLISHED)
        mail.outbox.clear()
        post = services.reply(user=replier, competition=competition, thread=thread, body=SECRET_BODY)
        assert post.status == ModerationStatus.PENDING
        run(django_capture_on_commit_callbacks)
        assert [m for m in outbox_for(author) if "nowe odpowiedzi" in m.subject] == []

        frozen.move_to(START + timedelta(minutes=30))
        services.moderate_post(post=post, actor=coordinator, status=ModerationStatus.PUBLISHED)
        run(django_capture_on_commit_callbacks)

    assert any("nowe odpowiedzi" in message.subject for message in outbox_for(author))


def test_a_reply_hidden_before_the_run_is_not_mailed(competition, django_capture_on_commit_callbacks):
    forum_on(competition)
    author, replier = participant_of(competition), participant_of(competition)
    coordinator = coordinator_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author)
        post = services.reply(user=replier, competition=competition, thread=thread, body=SECRET_BODY)
        services.moderate_post(post=post, actor=coordinator, status=ModerationStatus.HIDDEN)
        run(django_capture_on_commit_callbacks)

    assert outbox_for(author) == []
    assert ForumSubscription.objects.get(thread=thread, user=author).pending_since is None


def test_a_thread_rejected_after_a_reply_is_not_mailed(competition, django_capture_on_commit_callbacks):
    forum_on(competition)
    author, replier = participant_of(competition), participant_of(competition)
    coordinator = coordinator_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author, title="TEMAT-ZDJĘTY")
        services.reply(user=replier, competition=competition, thread=thread, body="Odpowiedź.")
        services.moderate_thread(thread=thread, actor=coordinator, status=ModerationStatus.HIDDEN)
        run(django_capture_on_commit_callbacks)

    assert all("TEMAT-ZDJĘTY" not in m.subject + m.body for m in mail.outbox)


# --- decyzje moderatora ----------------------------------------------------------------------------


def test_bulk_approval_ends_in_one_mail_per_author(competition, django_capture_on_commit_callbacks):
    forum_on(competition, mode=ModerationMode.PRE)
    author = participant_of(competition)
    coordinator = coordinator_of(competition)
    with freeze_time(START):
        threads = [start_thread(competition, author, title=f"Wątek {n}") for n in range(3)]
        services.bulk_approve(
            competition=competition, actor=coordinator, thread_ids=[thread.pk for thread in threads]
        )
        run(django_capture_on_commit_callbacks)

    [message] = outbox_for(author)
    assert message.subject == "[Olimpiada Kwantowa] Forum: decyzja organizatora w sprawie Twojego wpisu"
    for n in range(3):
        assert f"Twój wątek „Wątek {n}” został zatwierdzony" in message.body


def test_a_rejection_carries_the_reason_but_not_the_rejected_title_or_body(
    competition, django_capture_on_commit_callbacks
):
    forum_on(competition, mode=ModerationMode.PRE)
    author = participant_of(competition)
    coordinator = coordinator_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author, title=PENDING_TITLE, body=SECRET_BODY)
        services.moderate_thread(
            thread=thread, actor=coordinator, status=ModerationStatus.REJECTED, note="Dotyczy zadania etapu."
        )
        run(django_capture_on_commit_callbacks)

    [message] = outbox_for(author)
    assert "Dotyczy zadania etapu." in message.body
    assert "/forum/mine/" in message.body
    assert PENDING_TITLE not in message.subject + message.body
    assert SECRET_BODY not in message.body


def test_an_approval_reverted_before_the_run_is_not_mailed(competition, django_capture_on_commit_callbacks):
    forum_on(competition, mode=ModerationMode.PRE)
    author, replier = participant_of(competition), participant_of(competition)
    coordinator = coordinator_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author)
        services.moderate_thread(thread=thread, actor=coordinator, status=ModerationStatus.PUBLISHED)
        post = services.reply(user=replier, competition=competition, thread=thread, body="x")
        services.moderate_post(post=post, actor=coordinator, status=ModerationStatus.PUBLISHED)
        services.moderate_post(post=post, actor=coordinator, status=ModerationStatus.HIDDEN)
        mail.outbox.clear()
        run(django_capture_on_commit_callbacks)

    assert outbox_for(replier) == []
    assert not ForumDecisionNotice.objects.filter(handled_at__isnull=True).exists()


def test_a_coordinator_approving_own_thread_gets_no_decision_mail(competition):
    forum_on(competition, mode=ModerationMode.PRE)
    coordinator = coordinator_of(competition)
    thread = start_thread(competition, coordinator)

    services.moderate_thread(thread=thread, actor=coordinator, status=ModerationStatus.PUBLISHED)

    assert not ForumDecisionNotice.objects.exists()


def test_approving_a_thread_records_one_decision_not_two(competition):
    """Pierwszy wpis zatwierdzany razem z wątkiem nie dokłada drugiej pozycji do listu."""
    forum_on(competition, mode=ModerationMode.PRE)
    author, coordinator = participant_of(competition), coordinator_of(competition)
    thread = start_thread(competition, author)

    services.moderate_thread(thread=thread, actor=coordinator, status=ModerationStatus.PUBLISHED)

    assert list(ForumDecisionNotice.objects.values_list("kind", flat=True)) == [DecisionKind.THREAD_APPROVED]


# --- częstotliwość ---------------------------------------------------------------------------------


def test_never_sends_nothing_and_drops_the_backlog(competition, django_capture_on_commit_callbacks):
    forum_on(competition)
    author, replier = participant_of(competition), participant_of(competition)
    notifications.save_preferences(author, frequency=NotificationFrequency.NEVER, moderation_digest=True)
    with freeze_time(START):
        thread = start_thread(competition, author)
        services.reply(user=replier, competition=competition, thread=thread, body="x")
        run(django_capture_on_commit_callbacks)
        run(django_capture_on_commit_callbacks, daily=True)

    assert outbox_for(author) == []
    assert ForumSubscription.objects.get(thread=thread, user=author).pending_since is None


def test_daily_waits_for_the_daily_run_and_sends_one_summary(competition, django_capture_on_commit_callbacks):
    forum_on(competition, mode=ModerationMode.PRE)
    author, replier = participant_of(competition), participant_of(competition)
    coordinator = coordinator_of(competition)
    notifications.save_preferences(author, frequency=NotificationFrequency.DAILY, moderation_digest=True)
    with freeze_time(START):
        first = start_thread(competition, author, title="Pierwszy")
        second = start_thread(competition, author, title="Drugi")
        services.bulk_approve(competition=competition, actor=coordinator, thread_ids=[first.pk, second.pk])
        for thread in (first, second):
            thread.refresh_from_db()
            post = services.reply(user=replier, competition=competition, thread=thread, body="x")
            services.moderate_post(post=post, actor=coordinator, status=ModerationStatus.PUBLISHED)
        run(django_capture_on_commit_callbacks)
        assert outbox_for(author) == []

        run(django_capture_on_commit_callbacks, daily=True)

    [message] = outbox_for(author)
    assert message.subject == "[Olimpiada Kwantowa] Forum: podsumowanie dnia"
    assert "„Pierwszy”" in message.body and "„Drugi”" in message.body
    assert "zatwierdzony" in message.body


# --- list o kolejce moderacji ----------------------------------------------------------------------


def test_moderation_digest_waits_for_the_delay_then_throttles(
    competition, django_capture_on_commit_callbacks, settings
):
    settings.FORUM_MODERATION_DIGEST_DELAY_MINUTES = 10
    settings.FORUM_MODERATION_DIGEST_INTERVAL_HOURS = 3
    forum_on(competition, mode=ModerationMode.PRE)
    author, coordinator = participant_of(competition), coordinator_of(competition)
    with freeze_time(START) as frozen:
        start_thread(competition, author, title=PENDING_TITLE, body=SECRET_BODY)
        frozen.move_to(START + timedelta(minutes=5))
        run(django_capture_on_commit_callbacks)
        assert outbox_for(coordinator) == []

        frozen.move_to(START + timedelta(minutes=11))
        run(django_capture_on_commit_callbacks)
        assert len(outbox_for(coordinator)) == 1

        start_thread(competition, author, title="Drugi")
        frozen.move_to(START + timedelta(hours=2))
        run(django_capture_on_commit_callbacks)
        assert len(outbox_for(coordinator)) == 1

        frozen.move_to(START + timedelta(hours=3, minutes=12))
        run(django_capture_on_commit_callbacks)

    first, second = outbox_for(coordinator)
    assert first.subject == "[Olimpiada Kwantowa] Forum: wpisy czekają na moderację (1)"
    assert second.subject == "[Olimpiada Kwantowa] Forum: wpisy czekają na moderację (2)"
    assert "/coordinator/forum/" in first.body
    # Kolejka to rzeczy niepublikowane: ani tematu, ani treści w liście.
    assert PENDING_TITLE not in first.subject + first.body
    assert SECRET_BODY not in first.body
    assert "List-Unsubscribe" in first.extra_headers


def test_moderation_digest_stops_when_the_queue_is_empty(competition, django_capture_on_commit_callbacks):
    forum_on(competition, mode=ModerationMode.PRE)
    author, coordinator = participant_of(competition), coordinator_of(competition)
    with freeze_time(START) as frozen:
        thread = start_thread(competition, author)
        services.moderate_thread(thread=thread, actor=coordinator, status=ModerationStatus.PUBLISHED)
        frozen.move_to(START + timedelta(hours=1))
        run(django_capture_on_commit_callbacks)

    assert [m for m in outbox_for(coordinator) if "moderację" in m.subject] == []


def test_moderation_digest_goes_only_to_opted_in_coordinators(
    competition, django_capture_on_commit_callbacks
):
    """Komitet nie moderuje, więc listu o kolejce nie dostaje; koordynator może go wyłączyć."""
    forum_on(competition, mode=ModerationMode.PRE)
    author = participant_of(competition)
    wants, refuses = coordinator_of(competition), coordinator_of(competition)
    reviewer = ActiveReviewerFactory(competition=competition).user
    grant_membership(reviewer, competition, CompetitionRole.REVIEWER)
    notifications.save_preferences(
        refuses, frequency=NotificationFrequency.IMMEDIATE, moderation_digest=False
    )
    with freeze_time(START) as frozen:
        start_thread(competition, author)
        frozen.move_to(START + timedelta(minutes=20))
        run(django_capture_on_commit_callbacks)

    assert len(outbox_for(wants)) == 1
    assert outbox_for(refuses) == []
    assert outbox_for(reviewer) == []


# --- izolacja konkursów ----------------------------------------------------------------------------


def test_a_coordinator_of_another_competition_gets_nothing(
    competition, other_competition, django_capture_on_commit_callbacks
):
    """Z członkostwami rolą jest ``Membership`` konkursu – koordynator B nie widzi kolejki A."""
    forum_on(competition, mode=ModerationMode.PRE, memberships_enforced=True)
    forum_on(other_competition, mode=ModerationMode.PRE, memberships_enforced=True)
    ours, theirs = coordinator_of(competition), coordinator_of(other_competition)
    author = participant_of(competition)
    with freeze_time(START) as frozen:
        start_thread(competition, author)
        frozen.move_to(START + timedelta(minutes=20))
        run(django_capture_on_commit_callbacks)

    assert len(outbox_for(ours)) == 1
    assert outbox_for(theirs) == []


def test_mail_links_point_at_the_competition_of_the_thread(
    competition, other_competition, django_capture_on_commit_callbacks
):
    forum_on(other_competition)
    author, replier = participant_of(other_competition), participant_of(other_competition)
    with freeze_time(START):
        thread = start_thread(other_competition, author)
        services.reply(user=replier, competition=other_competition, thread=thread, body="x")
        run(django_capture_on_commit_callbacks)

    [message] = outbox_for(author)
    assert other_competition.site.hostname in message.body
    assert competition.site.hostname not in message.body


# --- komu nie wolno pisać --------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["inactive", "unverified", "anonymised"])
def test_undeliverable_accounts_get_nothing(competition, django_capture_on_commit_callbacks, state):
    from apps.accounts.profile import anonymise_account

    forum_on(competition)
    author, replier = participant_of(competition), participant_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author)
        services.reply(user=replier, competition=competition, thread=thread, body="x")
        email = author.email
        if state == "inactive":
            author.is_active = False
            author.save(update_fields=["is_active"])
        elif state == "unverified":
            author.email_verified_at = None
            author.save(update_fields=["email_verified_at"])
        else:
            anonymise_account(author)
        mail.outbox.clear()
        run(django_capture_on_commit_callbacks)

    assert [m for m in mail.outbox if email in m.to] == []
    subscription = ForumSubscription.objects.filter(thread=thread, user=author).first()
    if state == "anonymised":
        # Anonimizacja kasuje stan powiadomień konta (``apps.forum.notifications.erase_for_user``).
        assert subscription is None
    else:
        assert subscription.pending_since is None


def test_a_person_who_lost_the_role_gets_nothing(competition, django_capture_on_commit_callbacks):
    """Obserwacja nie jest przepustką: kto nie może już czytać forum, nie dostaje listów o nim."""
    from apps.accounts.models import Membership

    forum_on(competition, memberships_enforced=True)
    author, replier = participant_of(competition), participant_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author)
        services.reply(user=replier, competition=competition, thread=thread, body="x")
        Membership.objects.filter(user=author, competition=competition).delete()
        run(django_capture_on_commit_callbacks)

    assert outbox_for(author) == []


def test_the_register_names_the_mail_provider_for_the_forum():
    """List z forum idzie przez dostawcę poczty – rejestr czynności musi go wymieniać (v1.9)."""
    from apps.accounts.processing_register import FORUM_ACTIVITY, MAIL_RECIPIENT

    assert any(item.startswith(MAIL_RECIPIENT) for item in FORUM_ACTIVITY.recipients)
    assert any("powiadomienia e-mail" in item for item in FORUM_ACTIVITY.categories)


# --- język listu ------------------------------------------------------------------------------------


def test_mail_follows_the_competition_language(competition, django_capture_on_commit_callbacks):
    competition.default_language = "en"
    competition.save(update_fields=["default_language"])
    forum_on(competition)
    author, replier = participant_of(competition), participant_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author, title="Room")
        services.reply(user=replier, competition=competition, thread=thread, body="x")
        run(django_capture_on_commit_callbacks)

    [message] = outbox_for(author)
    assert message.subject == "[Olimpiada Kwantowa] Forum: new replies in “Room”"
    assert "This message was sent automatically; please do not reply to it." in message.body


# --- wypis --------------------------------------------------------------------------------------------


def test_the_unsubscribe_token_round_trips_and_rejects_tampering(competition):
    user = participant_of(competition)
    token = notifications.unsubscribe_token(user, notifications.SCOPE_ALL)

    assert notifications.read_unsubscribe_token(token) == (user.pk, notifications.SCOPE_ALL)
    with pytest.raises(signing.BadSignature):
        notifications.read_unsubscribe_token(token[:-2] + "xx")
    forged = signing.dumps({"u": user.pk, "s": "wszystko"}, salt=notifications.UNSUBSCRIBE_SALT)
    with pytest.raises(signing.BadSignature):
        notifications.read_unsubscribe_token(forged)
    other_salt = signing.dumps({"u": user.pk, "s": notifications.SCOPE_ALL}, salt="inna")
    with pytest.raises(signing.BadSignature):
        notifications.read_unsubscribe_token(other_salt)


def test_unsubscribe_scopes(competition):
    forum_on(competition)
    user = participant_of(competition)
    thread = start_thread(competition, user)

    notifications.apply_unsubscribe(user.pk, notifications.thread_scope(thread.pk))
    assert not notifications.is_following(user, thread)

    notifications.apply_unsubscribe(user.pk, notifications.SCOPE_MODERATION)
    assert notifications.preferences_for(user).moderation_digest is False
    assert notifications.preferences_for(user).frequency == NotificationFrequency.IMMEDIATE

    notifications.apply_unsubscribe(user.pk, notifications.SCOPE_ALL)
    assert notifications.preferences_for(user).frequency == NotificationFrequency.NEVER


def test_every_member_mail_has_one_click_unsubscribe_headers(competition, django_capture_on_commit_callbacks):
    forum_on(competition)
    author, replier = participant_of(competition), participant_of(competition)
    with freeze_time(START):
        thread = start_thread(competition, author)
        services.reply(user=replier, competition=competition, thread=thread, body="x")
        run(django_capture_on_commit_callbacks)

    [message] = outbox_for(author)
    header = message.extra_headers["List-Unsubscribe"]
    assert header.startswith("<") and "/forum/unsubscribe/" in header
    assert message.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    token = header.strip("<>").rstrip("/").rsplit("/", 1)[-1]
    assert notifications.read_unsubscribe_token(token) == (author.pk, notifications.SCOPE_ALL)
