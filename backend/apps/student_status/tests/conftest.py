"""Świat testów serwisu: edycja bieżąca Konkursu #1 i uczestnik tego konkursu."""

import pytest

from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.tests.factories import CurrentEditionFactory


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(competition=competition, year_label="I edycja 2026/2027")


@pytest.fixture
def participant(competition):
    return ParticipantFactory(
        competition=competition,
        user=UserFactory(
            email="uczen@example.test", first_name="Łucja", last_name="Śniadecka", groups=["participant"]
        ),
        school="I LO im. M. Kopernika w Krakowie",
    )
