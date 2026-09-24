"""Ekrany powiadomień z forum: przycisk „Obserwuj wątek”, blok ustawień na profilu i strona wypisu.

Reguły wysyłki mają własną suitę (``apps/forum/tests/test_notifications.py``). Tutaj stoi to,
czego serwis nie widzi: kod odpowiedzi, to, co jest w HTML-u, i to, że wypis działa **bez
logowania i bez tokenu CSRF** (klient poczty z nagłówka ``List-Unsubscribe-Post`` nie ma skąd go
wziąć), a zwykłe ``GET`` – skaner odnośników w skrzynce – niczego nie zmienia.
"""

from __future__ import annotations

import pytest
from django.test import Client

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.forum import notifications
from apps.forum.models import ForumSubscription, ModerationStatus, NotificationFrequency
from apps.forum.tests.factories import ForumThreadFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

SECTION_ID = 'id="powiadomienia-forum"'


def with_forum(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "participant_forum": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def participant_of(competition):
    profile = ParticipantFactory(competition=competition)
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    return profile.user


def coordinator_of(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


# --- „Obserwuj wątek” ------------------------------------------------------------------------------


def test_the_thread_page_offers_follow_and_the_button_toggles(web_client, competition):
    with_forum(competition)
    user = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    web_client.force_login(user)
    url = f"/forum/t/{thread.pk}/"

    assert "Obserwuj wątek" in web_client.get(url).content.decode()

    response = web_client.post(f"{url}follow/", {"follow": "1"})
    assert response.status_code == 302
    assert notifications.is_following(user, thread)
    assert "Przestań obserwować" in web_client.get(url).content.decode()

    web_client.post(f"{url}follow/", {"follow": "0"})
    assert not notifications.is_following(user, thread)


def test_a_pending_or_foreign_thread_cannot_be_followed(web_client, competition, other_competition):
    with_forum(competition)
    user = participant_of(competition)
    web_client.force_login(user)
    pending = ForumThreadFactory(competition=competition, author=user, status=ModerationStatus.PENDING)
    foreign = ForumThreadFactory(competition=other_competition)

    assert web_client.post(f"/forum/t/{pending.pk}/follow/", {"follow": "1"}).status_code == 404
    assert web_client.post(f"/forum/t/{foreign.pk}/follow/", {"follow": "1"}).status_code == 404
    assert not ForumSubscription.objects.exists()


# --- ustawienia na profilu -------------------------------------------------------------------------


def test_the_profile_shows_the_block_only_where_the_forum_exists(web_client, competition):
    user = participant_of(competition)
    web_client.force_login(user)

    assert SECTION_ID not in web_client.get("/me/profile/").content.decode()

    with_forum(competition)
    page = web_client.get("/me/profile/").content.decode()
    assert SECTION_ID in page
    # Uczestnik nie moderuje, więc pola listów o kolejce nie widzi.
    assert 'name="moderation_digest"' not in page


def test_a_coordinator_sees_the_moderation_digest_switch(web_client, competition):
    with_forum(competition)
    web_client.force_login(coordinator_of(competition))

    page = web_client.get("/account/profile/").content.decode()

    assert SECTION_ID in page
    assert 'name="moderation_digest"' in page


def test_saving_the_settings(web_client, competition):
    with_forum(competition)
    user = participant_of(competition)
    web_client.force_login(user)

    response = web_client.post("/account/forum-notifications/", {"frequency": NotificationFrequency.DAILY})

    assert response.status_code == 302
    assert response["Location"].endswith("#powiadomienia-forum")
    current = notifications.preferences_for(user)
    assert current.frequency == NotificationFrequency.DAILY
    # Pola nie było w formularzu uczestnika – wartość zostaje nietknięta, a nie gaśnie.
    assert current.moderation_digest is True


def test_an_unknown_frequency_is_rejected(web_client, competition):
    with_forum(competition)
    user = participant_of(competition)
    web_client.force_login(user)

    web_client.post("/account/forum-notifications/", {"frequency": "CO-MINUTE"})

    assert notifications.preferences_for(user).pk is None


# --- wypis bez logowania ----------------------------------------------------------------------------


def test_get_does_not_unsubscribe_but_post_does_without_login_or_csrf(competition):
    user = participant_of(competition)
    url = f"/forum/unsubscribe/{notifications.unsubscribe_token(user, notifications.SCOPE_ALL)}/"
    client = Client(enforce_csrf_checks=True)

    page = client.get(url)
    assert page.status_code == 200
    assert "Tak, wypisz mnie" in page.content.decode()
    assert notifications.preferences_for(user).frequency == NotificationFrequency.IMMEDIATE

    # Tak wysyła ``POST`` klient poczty z nagłówka ``List-Unsubscribe-Post`` (RFC 8058).
    done = client.post(url, {"List-Unsubscribe": "One-Click"})
    assert done.status_code == 200
    assert notifications.preferences_for(user).frequency == NotificationFrequency.NEVER
    # Strona nie mówi, czyje to konto – link bywa przekazany dalej razem z listem.
    assert user.email not in done.content.decode()


def test_a_forged_token_is_a_404(competition):
    user = participant_of(competition)
    token = notifications.unsubscribe_token(user, notifications.SCOPE_ALL)

    assert Client().get(f"/forum/unsubscribe/{token[:-3]}abc/").status_code == 404
    assert Client().post(f"/forum/unsubscribe/{token[:-3]}abc/").status_code == 404
    assert notifications.preferences_for(user).pk is None


def test_the_link_of_a_deleted_account_still_answers(competition):
    user = participant_of(competition)
    token = notifications.unsubscribe_token(user, notifications.SCOPE_ALL)
    user.delete()

    assert Client().post(f"/forum/unsubscribe/{token}/").status_code == 200


def test_the_queue_screen_states_the_mail_rhythm(web_client, competition, settings):
    settings.FORUM_MODERATION_DIGEST_DELAY_MINUTES = 10
    settings.FORUM_MODERATION_DIGEST_INTERVAL_HOURS = 3
    with_forum(competition)
    web_client.force_login(coordinator_of(competition))

    page = web_client.get("/coordinator/forum/").content.decode()

    assert "po 10 min" in page
    assert "co 3 godz." in page
