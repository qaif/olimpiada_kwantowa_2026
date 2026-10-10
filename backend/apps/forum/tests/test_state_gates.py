"""Zamknięty wątek, zamknięty dział i powtórne zgłoszenia – audyt bezpieczeństwa 10.10.2026, niskie.

Trzy dziury w regułach, które forum już miało: poprawka wpisu omijała zamknięcie wątku, odpowiedź
omijała zamknięcie działu, a „Zgłoś” klikane w kółko zaśmiecało kolejkę moderatora kopiami.
"""

from __future__ import annotations

import pytest

from apps.core.api import DomainError
from apps.forum import services
from apps.forum.models import ForumReport
from apps.forum.tests.factories import ForumCategoryFactory, ForumPostFactory, ForumThreadFactory

from .test_forum import closed_edition, participant_of

pytestmark = pytest.mark.django_db


def test_a_post_in_a_locked_thread_cannot_be_edited(competition):
    closed_edition(competition)
    profile = participant_of(competition)
    thread = ForumThreadFactory(competition=competition, is_locked=True)
    post = ForumPostFactory(competition=competition, thread=thread, author=profile.user, body="Pierwotna")

    with pytest.raises(DomainError) as error:
        services.edit_post(post=post, user=profile.user, body="Dopisek po zamknięciu")

    assert error.value.machine_code == "FORUM_THREAD_LOCKED"
    post.refresh_from_db()
    assert post.body == "Pierwotna"


def test_a_post_in_a_closed_category_cannot_be_edited(competition):
    closed_edition(competition)
    profile = participant_of(competition)
    category = ForumCategoryFactory(competition=competition, is_open=False)
    thread = ForumThreadFactory(competition=competition, category=category)
    post = ForumPostFactory(competition=competition, thread=thread, author=profile.user)

    with pytest.raises(DomainError) as error:
        services.edit_post(post=post, user=profile.user, body="Poprawka")

    assert error.value.machine_code == "FORUM_CATEGORY_CLOSED"


def test_a_closed_category_takes_no_replies(competition):
    """„Do czytania, nie do pisania” – także w wątkach założonych przed zamknięciem działu."""
    closed_edition(competition)
    profile = participant_of(competition)
    category = ForumCategoryFactory(competition=competition, is_open=False)
    thread = ForumThreadFactory(competition=competition, category=category)

    with pytest.raises(DomainError) as error:
        services.reply(user=profile.user, competition=competition, thread=thread, body="Cześć")

    assert error.value.machine_code == "FORUM_CATEGORY_CLOSED"


def test_a_second_report_of_the_same_post_by_the_same_person_adds_nothing(competition):
    reporter = participant_of(competition)
    post = ForumPostFactory(competition=competition)

    first = services.report_post(post=post, user=reporter.user, reason="Spam.")
    second = services.report_post(post=post, user=reporter.user, reason="Spam, naprawdę.")

    assert second.pk == first.pk
    assert ForumReport.objects.filter(post=post).count() == 1


def test_two_people_may_report_the_same_post(competition):
    """Unikalna jest para wpis–zgłaszający, nie sam wpis: moderator ma zobaczyć oba powody."""
    post = ForumPostFactory(competition=competition)

    services.report_post(post=post, user=participant_of(competition).user, reason="Spam.")
    services.report_post(post=post, user=participant_of(competition).user, reason="Obraźliwe.")

    assert ForumReport.objects.filter(post=post).count() == 2
