"""Wspólne dane testów części informacyjnej.

Drzewo stron nie jest tu budowane od zera – tworzy je migracja ``apps.cms.0002_initial_tree``,
więc testy pracują dokładnie na tym, co dostanie produkcja. Fixture'y jedynie je odnajdują.
"""

import pytest
from django.test import Client

from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.cms.models import ArchiveIndexPage, HomePage, NewsIndexPage, ProblemsPage, ResultsPage
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageFactory,
)


@pytest.fixture
def web_client() -> Client:
    return Client()


@pytest.fixture
def home_page() -> HomePage:
    return HomePage.objects.get()


@pytest.fixture
def news_index() -> NewsIndexPage:
    return NewsIndexPage.objects.get()


@pytest.fixture
def problems_page() -> ProblemsPage:
    return ProblemsPage.objects.get()


@pytest.fixture
def archive_index() -> ArchiveIndexPage:
    return ArchiveIndexPage.objects.get()


@pytest.fixture
def results_page() -> ResultsPage:
    return ResultsPage.objects.get()


@pytest.fixture
def edition():
    return CurrentEditionFactory()


@pytest.fixture
def open_stage(edition):
    """Etap eliminacyjny otwarty „teraz” (fabryka ustawia ``opens_at`` na dobę wstecz)."""
    stage = StageFactory(edition=edition, kind=StageKind.ELIM)
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=0)
    return stage


@pytest.fixture
def participant():
    return ParticipantFactory(user=UserFactory(email="uczestnik-cms@example.test", groups=["participant"]))


@pytest.fixture
def reviewer():
    return ActiveReviewerFactory()


@pytest.fixture
def coordinator():
    return CoordinatorFactory()
