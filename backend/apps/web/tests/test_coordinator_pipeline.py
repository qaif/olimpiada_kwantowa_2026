"""Ekran „Przebieg edycji” ``/coordinator/pipeline/`` (etap 2, T27).

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran jest **wyłączony** w konkursie z domyślnymi przełącznikami, a adres daje wtedy 404, a nie
  403: Olimpiada Kwantowa po wdrożeniu ma mieć adresy dokładnie takie, jak przed nim (§ 2.1),
- uczestnik dostaje 403 **niezależnie** od stanu flagi – odpowiedź nie zdradza konfiguracji,
- kolejka po **każdej** zmianie jest ciągiem 1…N: przesunięcie, wpisanie miejsca, wyjęcie kroku
  i przestawienie go poza tor kończą się przenumerowaniem, a nie dziurą albo dwoma krokami na
  jednym miejscu (więz ``competitions_pipelinestep_unique_position``),
- wyjęcie kroku **nie kasuje etapu** – znika wyłącznie miejsce w kolejce i reguły przejścia,
- runda powstaje razem ze skalą, progiem i miejscem na końcu kolejki,
- krok sąsiada nie istnieje nawet po identyfikatorze (404 z zawężonego querysetu, § 3.6),
- każdy zapis zostawia wpis audytowy **bez danych osobowych** – same identyfikatory i liczby.

Adresy są w ``apps/web/urls.py`` – montaż wydania I rozwinął tam ``urls_pipeline.urlpatterns``,
więc testy chodzą po **mapie produkcyjnej**, a nie po jej kopii. Ścieżki się nie zmieniły, bo
kopia była złożona z tych samych wzorców.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.models import PipelineStep, Stage, StageFormat, StageKind
from apps.competitions.tests.factories import EditionFactory, StageFactory
from apps.core.models import AuditLog
from apps.results.services import PROCESS_EDITOR_FLAG
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/pipeline/"
WARSAW_FORMAT = "%Y-%m-%dT%H:%M"


def enable(competition):
    """Włącza ekran tak, jak zrobi to operator platformy – zapisem do ``feature_flags``."""
    competition.feature_flags = {**(competition.feature_flags or {}), PROCESS_EDITOR_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def coordinator_for(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client, user


@pytest.fixture
def coordinator_client(client_for, competition):
    """Zalogowany koordynator tego konkursu, pod jego domeną, z **włączonym** edytorem."""
    enable(competition)
    client, _user = coordinator_for(client_for, competition)
    return client


@pytest.fixture
def stages(edition):
    """Trzy etapy zawodów bieżącej edycji – kształt Olimpiady Kwantowej bez treningu."""
    return [
        StageFactory(edition=edition, kind=StageKind.ELIM),
        StageFactory(edition=edition, kind=StageKind.DISTRICT),
        StageFactory(edition=edition, kind=StageKind.FINAL),
    ]


def step_for(stage, position: int, *, off_pipeline: bool = False) -> PipelineStep:
    return PipelineStep.objects.create(
        edition=stage.edition, stage=stage, position=position, off_pipeline=off_pipeline
    )


def positions(edition) -> list[int]:
    return list(
        PipelineStep.objects.filter(edition=edition, off_pipeline=False)
        .order_by("position")
        .values_list("position", flat=True)
    )


def order_of(edition) -> list[int]:
    return list(
        PipelineStep.objects.filter(edition=edition, off_pipeline=False)
        .order_by("position")
        .values_list("stage_id", flat=True)
    )


# --- przełącznik ekranu ---------------------------------------------------------------------------


def test_screen_is_off_for_a_competition_with_default_flags(client_for, competition, edition, stages):
    """Konkurs #1 po wdrożeniu: adresu nie ma, dopóki nikt świadomie nie włączy edytora."""
    step = step_for(stages[0], 1)
    client, _user = coordinator_for(client_for, competition)

    assert not competition.has_feature(PROCESS_EDITOR_FLAG)
    assert client.get(LIST_URL).status_code == 404
    assert client.get(f"/coordinator/pipeline/{step.pk}/rules/").status_code == 404
    assert client.get(f"/coordinator/stages/{stages[0].pk}/components/").status_code == 404
    assert client.post(f"/coordinator/pipeline/{step.pk}/delete/").status_code == 404


def test_participant_gets_403_regardless_of_the_flag(client_for, competition):
    """Rola sprawdza się przed flagą: odpowiedź nie mówi uczestnikowi, jak skonfigurowano konkurs."""
    user = ParticipantFactory().user
    client = client_for(competition)
    client.force_login(user)

    assert client.get(LIST_URL).status_code == 403

    enable(competition)

    assert client.get(LIST_URL).status_code == 403


def test_anonymous_is_redirected_to_login(client_for, competition):
    enable(competition)
    response = client_for(competition).get(LIST_URL)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


# --- kolejka --------------------------------------------------------------------------------------


def test_list_shows_steps_in_pipeline_order(coordinator_client, edition, stages):
    step_for(stages[2], 1)
    step_for(stages[0], 2)

    response = coordinator_client.get(LIST_URL)

    assert response.status_code == 200
    assert [row["step"].stage_id for row in response.context["rows"]] == [stages[2].pk, stages[0].pk]


def test_step_is_added_at_the_end_and_leaves_an_audit_entry(coordinator_client, edition, stages):
    step_for(stages[0], 1)

    response = coordinator_client.post("/coordinator/pipeline/steps/", {"stage": stages[1].pk})

    assert response.status_code == 302
    assert order_of(edition) == [stages[0].pk, stages[1].pk]
    entry = AuditLog.objects.get(action="pipeline_step.created")
    assert entry.diff["stage_id"] == stages[1].pk
    assert entry.diff["position"] == 2


def test_added_step_can_stand_off_the_pipeline(coordinator_client, edition, stages):
    training = StageFactory(edition=edition, kind=StageKind.TRAINING)
    step_for(stages[0], 1)

    coordinator_client.post("/coordinator/pipeline/steps/", {"stage": training.pk, "off_pipeline": "on"})

    step = PipelineStep.objects.get(stage=training)
    assert step.off_pipeline
    assert step.position == 0
    assert order_of(edition) == [stages[0].pk]


def test_a_stage_with_a_step_is_not_offered_again(coordinator_client, edition, stages):
    step_for(stages[0], 1)

    response = coordinator_client.get(LIST_URL)
    offered = list(response.context["step_form"].fields["stage"].queryset)

    assert stages[0] not in offered
    assert stages[1] in offered


def test_move_down_swaps_two_neighbours(coordinator_client, edition, stages):
    first = step_for(stages[0], 1)
    step_for(stages[1], 2)

    response = coordinator_client.post(f"/coordinator/pipeline/{first.pk}/move/down/")

    assert response.status_code == 302
    assert order_of(edition) == [stages[1].pk, stages[0].pk]
    assert positions(edition) == [1, 2]


def test_move_at_the_edge_changes_nothing(coordinator_client, edition, stages):
    first = step_for(stages[0], 1)
    step_for(stages[1], 2)

    coordinator_client.post(f"/coordinator/pipeline/{first.pk}/move/up/")

    assert order_of(edition) == [stages[0].pk, stages[1].pk]
    assert not AuditLog.objects.filter(action="pipeline_step.updated").exists()


def test_unknown_direction_is_404(coordinator_client, edition, stages):
    step = step_for(stages[0], 1)

    assert coordinator_client.post(f"/coordinator/pipeline/{step.pk}/move/sideways/").status_code == 404


def test_position_form_moves_the_step_and_keeps_numbers_contiguous(coordinator_client, edition, stages):
    step_for(stages[0], 1)
    step_for(stages[1], 2)
    last = step_for(stages[2], 3)

    response = coordinator_client.post(f"/coordinator/pipeline/{last.pk}/", {"position": 1})

    assert response.status_code == 302
    assert order_of(edition) == [stages[2].pk, stages[0].pk, stages[1].pk]
    assert positions(edition) == [1, 2, 3]


def test_position_above_the_queue_length_means_the_end(coordinator_client, edition, stages):
    first = step_for(stages[0], 1)
    step_for(stages[1], 2)

    coordinator_client.post(f"/coordinator/pipeline/{first.pk}/", {"position": 99})

    assert order_of(edition) == [stages[1].pk, stages[0].pk]
    assert positions(edition) == [1, 2]


def test_position_below_one_is_refused_without_touching_the_queue(coordinator_client, edition, stages):
    first = step_for(stages[0], 1)
    step_for(stages[1], 2)

    response = coordinator_client.post(f"/coordinator/pipeline/{first.pk}/", {"position": 0})

    assert response.status_code == 400
    assert order_of(edition) == [stages[0].pk, stages[1].pk]
    assert not AuditLog.objects.filter(action="pipeline_step.updated").exists()


def test_moving_a_step_off_the_pipeline_closes_the_gap(coordinator_client, edition, stages):
    step_for(stages[0], 1)
    middle = step_for(stages[1], 2)
    step_for(stages[2], 3)

    coordinator_client.post(f"/coordinator/pipeline/{middle.pk}/", {"position": 2, "off_pipeline": "on"})

    middle.refresh_from_db()
    assert middle.off_pipeline
    assert middle.position == 0
    assert order_of(edition) == [stages[0].pk, stages[2].pk]
    assert positions(edition) == [1, 2]


def test_a_step_returns_to_the_pipeline_at_the_requested_place(coordinator_client, edition, stages):
    off = step_for(stages[2], 0, off_pipeline=True)
    step_for(stages[0], 1)
    step_for(stages[1], 2)

    coordinator_client.post(f"/coordinator/pipeline/{off.pk}/", {"position": 2})

    assert order_of(edition) == [stages[0].pk, stages[2].pk, stages[1].pk]
    assert positions(edition) == [1, 2, 3]


def test_deleting_a_step_keeps_the_stage_and_renumbers(coordinator_client, edition, stages):
    step_for(stages[0], 1)
    middle = step_for(stages[1], 2)
    step_for(stages[2], 3)

    response = coordinator_client.post(f"/coordinator/pipeline/{middle.pk}/delete/")

    assert response.status_code == 302
    assert Stage.objects.filter(pk=stages[1].pk).exists()
    assert order_of(edition) == [stages[0].pk, stages[2].pk]
    assert positions(edition) == [1, 2]
    assert AuditLog.objects.get(action="pipeline_step.deleted").diff["stage_id"] == stages[1].pk


# --- nowa runda -----------------------------------------------------------------------------------


def round_payload(**overrides) -> dict:
    """Komplet pól ``StageCreateForm`` w formacie ``<input type="datetime-local">`` (czas polski)."""
    opens = timezone.localtime(timezone.now()) + timedelta(days=40)
    payload = {
        "kind": StageKind.ROUND,
        "name": "Runda 1",
        "format": StageFormat.SUBMISSIONS,
        "location": "",
        "grace_seconds": 0,
        "opens_at": opens.strftime(WARSAW_FORMAT),
        "deadline_at": (opens + timedelta(days=10)).strftime(WARSAW_FORMAT),
        "review_deadline_at": (opens + timedelta(days=24)).strftime(WARSAW_FORMAT),
        "review_deadline_days": 14,
        "appeal_window_opens_at": (opens + timedelta(days=26)).strftime(WARSAW_FORMAT),
        "appeal_window_closes_at": (opens + timedelta(days=33)).strftime(WARSAW_FORMAT),
    }
    payload.update(overrides)
    return payload


def test_round_is_created_with_scale_rule_and_a_place_in_the_queue(coordinator_client, edition, stages):
    step_for(stages[0], 1)

    response = coordinator_client.post("/coordinator/pipeline/rounds/", round_payload())

    stage = Stage.objects.get(edition=edition, kind=StageKind.ROUND)
    assert response.status_code == 302
    assert stage.scoring_scale.max_value == 6
    assert stage.qualification_rule is not None
    assert stage.pipeline_step.position == 2
    assert AuditLog.objects.get(action="stage.created").diff["kind"] == StageKind.ROUND


def test_two_rounds_live_side_by_side(coordinator_client, edition):
    """Więz ``competitions_stage_unique_kind`` jest dla rundy zawieszony – rozróżnia je nazwa."""
    coordinator_client.post("/coordinator/pipeline/rounds/", round_payload(name="Runda 1"))
    coordinator_client.post("/coordinator/pipeline/rounds/", round_payload(name="Runda 2"))

    assert Stage.objects.filter(edition=edition, kind=StageKind.ROUND).count() == 2
    assert positions(edition) == [1, 2]


def test_broken_round_dates_are_refused_without_creating_anything(coordinator_client, edition):
    opens = timezone.localtime(timezone.now()) + timedelta(days=40)

    response = coordinator_client.post(
        "/coordinator/pipeline/rounds/",
        round_payload(deadline_at=(opens - timedelta(days=1)).strftime(WARSAW_FORMAT)),
    )

    assert response.status_code == 400
    assert not Stage.objects.filter(edition=edition, kind=StageKind.ROUND).exists()
    assert not PipelineStep.objects.filter(edition=edition).exists()


# --- izolacja -------------------------------------------------------------------------------------


def test_a_step_of_another_competition_is_404(coordinator_client, other_competition):
    enable(other_competition)
    foreign_edition = EditionFactory(competition=other_competition)
    foreign_stage = StageFactory(edition=foreign_edition, competition=other_competition)
    foreign_step = PipelineStep.objects.create(edition=foreign_edition, stage=foreign_stage, position=1)

    assert coordinator_client.get(f"/coordinator/pipeline/{foreign_step.pk}/rules/").status_code == 404
    assert coordinator_client.post(f"/coordinator/pipeline/{foreign_step.pk}/delete/").status_code == 404
    assert PipelineStep.objects.filter(pk=foreign_step.pk).exists()


def test_audit_entries_carry_no_personal_data(coordinator_client, edition, stages):
    """Wpis o kroku ma nieść identyfikatory i liczby – nigdy nazwiska ani pseudonimu."""
    coordinator_client.post("/coordinator/pipeline/steps/", {"stage": stages[0].pk})

    diff = AuditLog.objects.get(action="pipeline_step.created").diff

    assert set(diff) == {"stage_id", "kind", "position", "off"}
    assert all(isinstance(value, int | bool | str) for value in diff.values())
