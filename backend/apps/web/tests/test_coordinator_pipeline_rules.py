"""Ekran „Reguły przejścia kroku” ``/coordinator/pipeline/<krok>/rules/`` (etap 2, T27).

Reguły progu mają własne testy w ``apps/results/tests/``; tutaj sprawdzamy sam ekran, a w nim
cztery rzeczy, których po kodzie widoku nie widać:

- samo wejście **nic nie liczy** – podgląd jest pełnym przeliczeniem etapu i trzeba o niego
  poprosić; żaden podgląd niczego nie zapisuje,
- podgląd reguły dopisywanej bierze reguły **zapisane plus** tę z formularza, bo kilka reguł na
  kroku się sumuje; przy zmianie reguły już zapisanej jej wersja z bazy z zestawu wypada,
- komplet parametrów trybu sprawdza model, a komunikat staje **pod polem**, nie na stronie błędu,
- skasowanie ostatniej reguły nie znaczy „nikt nie przechodzi”, tylko „nie skonfigurowano” –
  próg wraca wtedy do ``QualificationRule`` i ekran mówi o tym wprost.

Adresy pochodzą z ``apps/web/urls.py`` – patrz ``test_coordinator_pipeline.py``.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.models import (
    Category,
    PipelineStep,
    TransitionGroupBy,
    TransitionMode,
    TransitionRule,
)
from apps.core.models import AuditLog
from apps.grading.tests.factories import FinalGradeFactory
from apps.results.services import PROCESS_EDITOR_FLAG
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator_client(client_for, competition):
    competition.feature_flags = {**(competition.feature_flags or {}), PROCESS_EDITOR_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def step(elim_stage):
    return PipelineStep.objects.create(edition=elim_stage.edition, stage=elim_stage, position=1)


@pytest.fixture
def scored(entry, problems):
    """Jedna praca z oceną końcową – najkrótsza droga do niezerowej sumy w tabeli wyników."""
    submission = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.FINAL)
    FinalGradeFactory(submission=submission, score=6)
    return entry


def rules_url(step) -> str:
    return f"/coordinator/pipeline/{step.pk}/rules/"


def payload(**overrides) -> dict:
    data = {
        "mode": TransitionMode.MIN_POINTS,
        "group_by": "",
        "category": "",
        "min_points": 5,
        "top_n": "",
        "percentile": "",
        "position": 0,
    }
    data.update(overrides)
    return data


# --- wejście i podgląd ----------------------------------------------------------------------------


def test_entering_the_screen_computes_nothing(coordinator_client, step, scored):
    response = coordinator_client.get(rules_url(step))

    assert response.status_code == 200
    assert response.context["preview"] is None


def test_preview_of_the_saved_rules_is_computed_on_demand(coordinator_client, step, scored):
    TransitionRule.objects.create(step=step, mode=TransitionMode.MIN_POINTS, min_points=5)

    response = coordinator_client.get(rules_url(step), {"preview": "current"})

    assert response.status_code == 200
    assert response.context["preview"]["qualified"] == 1


def test_preview_adds_the_candidate_to_the_saved_rules(coordinator_client, step, scored):
    """Kilka reguł się sumuje: reguła nie do przejścia plus nowa dają skład **nowej**."""
    TransitionRule.objects.create(step=step, mode=TransitionMode.MIN_POINTS, min_points=1000)

    response = coordinator_client.post(
        f"{rules_url(step)}preview/", payload(mode=TransitionMode.TOP_N, min_points="", top_n=1)
    )

    assert response.status_code == 200
    assert response.context["preview"]["qualified"] == 1
    assert TransitionRule.objects.filter(step=step).count() == 1


def test_preview_saves_nothing(coordinator_client, step, scored, entry):
    coordinator_client.post(f"{rules_url(step)}preview/", payload())

    entry.refresh_from_db()
    assert not TransitionRule.objects.filter(step=step).exists()
    assert not AuditLog.objects.filter(action__startswith="transition_rule").exists()


def test_incomplete_mode_is_reported_under_the_field(coordinator_client, step):
    response = coordinator_client.post(
        f"{rules_url(step)}preview/", payload(mode=TransitionMode.TOP_N, min_points="", top_n="")
    )

    assert response.status_code == 400
    assert response.context["form"].errors["top_n"]
    assert response.context["preview"] is None


def test_group_mode_without_a_split_is_refused(coordinator_client, step):
    response = coordinator_client.post(
        f"{rules_url(step)}preview/",
        payload(mode=TransitionMode.TOP_N_PER_GROUP, min_points="", top_n=3),
    )

    assert response.status_code == 400
    assert response.context["form"].errors["group_by"]


# --- zapis ----------------------------------------------------------------------------------------


def test_rule_is_saved_with_an_audit_entry(coordinator_client, step):
    response = coordinator_client.post(f"{rules_url(step)}new/", payload(min_points=42))

    rule = TransitionRule.objects.get(step=step)
    assert response.status_code == 302
    assert rule.mode == TransitionMode.MIN_POINTS
    assert rule.min_points == 42
    diff = AuditLog.objects.get(action="transition_rule.created").diff
    assert diff["min_points"] == 42
    assert diff["category"] is None


def test_saving_an_incomplete_rule_changes_nothing(coordinator_client, step):
    response = coordinator_client.post(
        f"{rules_url(step)}new/", payload(mode=TransitionMode.TOP_N, min_points="", top_n="")
    )

    assert response.status_code == 400
    assert not TransitionRule.objects.filter(step=step).exists()


def test_rule_can_be_edited(coordinator_client, step):
    rule = TransitionRule.objects.create(step=step, mode=TransitionMode.MIN_POINTS, min_points=5)

    response = coordinator_client.post(f"{rules_url(step)}{rule.pk}/", payload(min_points=80))

    rule.refresh_from_db()
    assert response.status_code == 302
    assert rule.min_points == 80
    diff = AuditLog.objects.get(action="transition_rule.updated").diff
    assert diff["before"]["min_points"] == 5
    assert diff["after"]["min_points"] == 80


def test_editing_screen_offers_the_saved_values(coordinator_client, step):
    rule = TransitionRule.objects.create(step=step, mode=TransitionMode.TOP_N, top_n=30)

    response = coordinator_client.get(f"{rules_url(step)}{rule.pk}/")

    assert response.status_code == 200
    assert response.context["editing"] == rule
    assert response.context["form"].instance == rule


def test_preview_of_an_edited_rule_replaces_its_saved_version(coordinator_client, step, scored):
    """Podgląd zmiany nie liczy sumy starej i nowej wartości tego samego wiersza."""
    rule = TransitionRule.objects.create(step=step, mode=TransitionMode.MIN_POINTS, min_points=1)

    response = coordinator_client.post(f"{rules_url(step)}preview/", payload(rule=rule.pk, min_points=1000))

    assert response.status_code == 200
    assert response.context["preview"]["qualified"] == 0
    assert response.context["editing"] == rule


def test_deleting_the_last_rule_says_the_threshold_falls_back(coordinator_client, step):
    rule = TransitionRule.objects.create(step=step, mode=TransitionMode.MIN_POINTS, min_points=5)

    response = coordinator_client.post(f"{rules_url(step)}{rule.pk}/delete/", follow=True)

    assert not TransitionRule.objects.filter(step=step).exists()
    assert AuditLog.objects.filter(action="transition_rule.deleted").exists()
    assert "próg wraca" in response.content.decode()


# --- zakres ---------------------------------------------------------------------------------------


def test_category_choices_stop_at_the_competition_border(
    coordinator_client, step, competition, other_competition
):
    mine = Category.objects.create(competition=competition, code="mine", name="Moja")
    theirs = Category.objects.create(competition=other_competition, code="theirs", name="Cudza")

    response = coordinator_client.get(rules_url(step))
    offered = list(response.context["form"].fields["category"].queryset)

    assert mine in offered
    assert theirs not in offered


def test_a_rule_of_another_step_is_404(coordinator_client, step, edition, elim_stage):
    from apps.competitions.tests.factories import StageFactory

    other_stage = StageFactory(edition=edition, kind="FINAL")
    other_step = PipelineStep.objects.create(edition=edition, stage=other_stage, position=2)
    foreign = TransitionRule.objects.create(step=other_step, mode=TransitionMode.MIN_POINTS, min_points=5)

    assert coordinator_client.get(f"{rules_url(step)}{foreign.pk}/").status_code == 404
    assert coordinator_client.post(f"{rules_url(step)}{foreign.pk}/delete/").status_code == 404
    assert TransitionRule.objects.filter(pk=foreign.pk).exists()


def test_group_by_region_is_accepted(coordinator_client, step):
    """Dzisiejsze „N na województwo” to ten sam tryb z podziałem po regionie – musi dać się zapisać."""
    response = coordinator_client.post(
        f"{rules_url(step)}new/",
        payload(
            mode=TransitionMode.TOP_N_PER_GROUP,
            min_points="",
            top_n=3,
            group_by=TransitionGroupBy.REGION,
        ),
    )

    assert response.status_code == 302
    assert TransitionRule.objects.get(step=step).group_by == TransitionGroupBy.REGION
