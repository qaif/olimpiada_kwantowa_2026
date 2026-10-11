"""Wspólne narzędzia testów medali (MED-01).

Konkurs międzynarodowy to Konkurs #1 przestawiony na kraje i tryb delegacji (tak, jak operator
przestawi ``iqo``) **z włączoną flagą ``medals``**. Olimpiada Kwantowa to ten sam konkurs bez
przestawienia – tam żadnego z tych ekranów i żadnej zmiany w dyplomach nie ma.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import Region
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.accounts.tests.test_delegations import make_delegations_competition
from apps.competitions.models import StageKind
from apps.competitions.services import current_edition
from apps.results.models import Anonymization
from apps.results.services import publish_results
from apps.results.tests.conftest import graded_entry, make_stage


def enable_medals(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "medals": True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def iqo(competition):
    """Konkurs w trybie delegacji, z krajami i z medalami – bieżąca edycja założona."""
    return enable_medals(make_delegations_competition(competition))


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


def country(competition, code: str) -> Region:
    return Region.objects.get(competition=competition, code=code)


def contestant(stage, scores, *, code: str = "de", status=None, **participant_kwargs):
    """Wpis z ocenami i uczestnikiem z kraju ``code`` (region profilu, jak po zgłoszeniu delegacji)."""
    competition = stage.edition.competition
    region = country(competition, code)
    participant = ParticipantFactory(region=region, district=region.code, **participant_kwargs)
    kwargs = {"participant": participant}
    if status is not None:
        kwargs["status"] = status
    return graded_entry(stage, scores, **kwargs)


def final_stage(competition, *, problems: int = 2):
    """Finał bieżącej edycji z zamkniętym oknem reklamacji (``make_stage``) – ranking ostateczny."""
    return make_stage(kind=StageKind.FINAL, edition=current_edition(competition), problems=problems)


def publish(stage, actor, anonymization=Anonymization.CODE, **kwargs):
    return publish_results(stage, actor, anonymization, **kwargs)
