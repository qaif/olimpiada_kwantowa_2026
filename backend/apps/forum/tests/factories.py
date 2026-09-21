"""Fabryki forum. Wyłącznie do testów.

Wszystkie cztery modele niosą własny klucz konkursu (``apps.forum.models`` – docstring modułu
tłumaczy, dlaczego także wpis i zgłoszenie, do których droga wiedzie przez wątek). Dlatego
fabryki dziedziczą po ``CompetitionScopedFactory``, a dzieci biorą konkurs **rodzica**
(``SAME_COMPETITION``), a nie z kontekstu: wpis w wątku konkursu B, podpisany konkursem A,
byłby wierszem, którego nie zobaczy żaden ekran i którego nie złapie żaden test izolacji.
"""

import factory
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.forum.models import (
    ForumCategory,
    ForumPost,
    ForumReport,
    ForumSettings,
    ForumThread,
    ModerationStatus,
)
from apps.tenancy.tests.factories import SAME_COMPETITION, CompetitionScopedFactory


class ForumSettingsFactory(CompetitionScopedFactory):
    """Ustawienia forum konkursu. W praktyce wiersz powstaje dopiero przy pierwszym zapisie."""

    class Meta:
        model = ForumSettings


class ForumCategoryFactory(CompetitionScopedFactory):
    class Meta:
        model = ForumCategory

    name = factory.Sequence(lambda n: f"Dział testowy {n}")
    slug = factory.Sequence(lambda n: f"dzial-{n}")
    description = ""
    is_open = True


class ForumThreadFactory(CompetitionScopedFactory):
    """Wątek **opublikowany**: taki, jaki widzi czytelnik.

    Domyślny stan jest tu inny niż domyślny stan modelu (``PENDING``) i to jest celowe: model
    opisuje wątek świeżo napisany, a test zwykle potrzebuje wątku, który już istnieje na forum.
    Test moderacji podaje stan jawnie i przez to widać w nim, o jaki stan chodzi.
    """

    class Meta:
        model = ForumThread

    category = factory.SubFactory(ForumCategoryFactory, competition=SAME_COMPETITION)
    author = factory.SubFactory(UserFactory)
    title = factory.Sequence(lambda n: f"Wątek testowy {n}")
    status = ModerationStatus.PUBLISHED
    created_at = factory.LazyFunction(timezone.now)
    last_activity_at = factory.LazyFunction(timezone.now)


class ForumPostFactory(CompetitionScopedFactory):
    """Wpis opublikowany – z tego samego powodu, co przy wątku."""

    class Meta:
        model = ForumPost

    thread = factory.SubFactory(ForumThreadFactory, competition=SAME_COMPETITION)
    author = factory.SubFactory(UserFactory)
    body = factory.Sequence(lambda n: f"Treść wpisu testowego {n}")
    status = ModerationStatus.PUBLISHED
    created_at = factory.LazyFunction(timezone.now)


class ForumReportFactory(CompetitionScopedFactory):
    class Meta:
        model = ForumReport

    post = factory.SubFactory(ForumPostFactory, competition=SAME_COMPETITION)
    reporter = factory.SubFactory(UserFactory)
    reason = "Wpis zdradza rozwiązanie zadania."
    created_at = factory.LazyFunction(timezone.now)
