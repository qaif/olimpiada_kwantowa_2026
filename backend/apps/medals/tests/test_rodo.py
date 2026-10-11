"""RODO medali (MED-01 § 5): czynność w rejestrze za flagą i sekcja ``medale`` w eksporcie konta."""

from __future__ import annotations

import pytest

from apps.accounts.data_export import export_payload
from apps.accounts.processing_register import activities_for
from apps.medals import services
from apps.medals.models import Award

from .conftest import contestant, final_stage, publish

pytestmark = pytest.mark.django_db


def test_register_has_the_medals_activity_only_with_the_flag(competition):
    assert "medale" not in {activity.key for activity in activities_for(competition)}

    competition.feature_flags = {**(competition.feature_flags or {}), "medals": True}

    assert "medale" in {activity.key for activity in activities_for(competition)}


def test_export_has_an_empty_medals_section_without_medals(competition):
    from apps.accounts.tests.factories import ParticipantFactory

    participant = ParticipantFactory()

    assert export_payload(participant.user)["medale"] == []


def test_export_contains_the_award_and_the_override_justification(iqo, coordinator):
    stage = final_stage(iqo)
    winner = contestant(stage, (6, 6))
    contestant(stage, (1, 1))
    publish(stage, coordinator)
    scheme = services.scheme_for(stage, create=True)
    services.set_override(
        scheme,
        entry_id=winner.pk,
        award=Award.SILVER,
        justification="Decyzja jury z 3.10.",
        actor=coordinator,
    )
    services.freeze(scheme, actor=coordinator)

    section = export_payload(winner.participant.user)["medale"]

    announced = next(row for row in section if "nagroda" in row)
    assert announced["nagroda"] == "srebrny medal"
    assert announced["nagroda_wyliczona"] == "złoty medal"
    override = next(row for row in section if "zmiana_reczna" in row)
    assert override["uzasadnienie"] == "Decyzja jury z 3.10."
    assert "autor" not in override and coordinator.email not in str(section)
