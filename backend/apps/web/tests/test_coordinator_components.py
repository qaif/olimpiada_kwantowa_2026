"""Ekran „Komponenty etapu” ``/coordinator/stages/<id>/components/`` (etap 2, T27).

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran jest za flagą ``process_editor`` i bez niej daje **404**, a nie 403 (§ 2.1),
- etap **bez ani jednego** komponentu liczy się jak przed etapem 2 i ekran mówi o tym wprost –
  pierwszy komponent jest całym przełącznikiem, a usunięcie ostatniego drogą odwrotu,
- waga jest parą liczb i mianownik zerowy odpada **pod polem**, a nie pięćsetką,
- dwa komponenty tego samego rodzaju wolno mieć, ale nie na tym samym miejscu w kolejności,
- komponent cudzego etapu nie istnieje nawet po identyfikatorze.

Adresy pochodzą z ``apps/web/urls.py`` – patrz ``test_coordinator_pipeline.py``.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.models import ComponentKind, StageComponent, StageKind
from apps.competitions.tests.factories import StageFactory
from apps.core.models import AuditLog
from apps.results.services import PROCESS_EDITOR_FLAG
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


def url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/components/"


def payload(**overrides) -> dict:
    data = {
        "kind": ComponentKind.SUBMISSIONS,
        "name": "",
        "position": 1,
        "weight_numerator": 1,
        "weight_denominator": 1,
        "required": "on",
    }
    data.update(overrides)
    return data


def test_screen_is_off_without_the_flag(client_for, competition, elim_stage):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)

    assert client.get(url(elim_stage)).status_code == 404


def test_stage_without_components_says_it_counts_the_old_way(coordinator_client, elim_stage):
    response = coordinator_client.get(url(elim_stage))

    assert response.status_code == 200
    assert response.context["components"] == []
    assert "liczy się z formy etapu" in response.content.decode()


def test_component_is_added_with_an_audit_entry(coordinator_client, elim_stage):
    response = coordinator_client.post(
        f"{url(elim_stage)}new/", payload(name="Część teoretyczna", weight_numerator=2, weight_denominator=3)
    )

    component = StageComponent.objects.get(stage=elim_stage)
    assert response.status_code == 302
    assert component.name == "Część teoretyczna"
    assert (component.weight_numerator, component.weight_denominator) == (2, 3)
    diff = AuditLog.objects.get(action="stage_component.created").diff
    assert diff["weight"] == [2, 3]
    assert diff["stage_id"] == elim_stage.pk


def test_zero_denominator_is_refused_under_the_field(coordinator_client, elim_stage):
    response = coordinator_client.post(f"{url(elim_stage)}new/", payload(weight_denominator=0))

    assert response.status_code == 400
    assert response.context["form"].errors["weight_denominator"]
    assert not StageComponent.objects.filter(stage=elim_stage).exists()


def test_two_components_of_one_kind_need_different_places(coordinator_client, elim_stage):
    coordinator_client.post(f"{url(elim_stage)}new/", payload(name="Test wstępny"))

    clash = coordinator_client.post(f"{url(elim_stage)}new/", payload(name="Test finałowy"))
    apart = coordinator_client.post(f"{url(elim_stage)}new/", payload(name="Test finałowy", position=2))

    assert clash.status_code == 400
    assert apart.status_code == 302
    assert StageComponent.objects.filter(stage=elim_stage).count() == 2


def test_component_can_be_edited(coordinator_client, elim_stage):
    component = StageComponent.objects.create(stage=elim_stage, kind=ComponentKind.QUIZ)

    response = coordinator_client.post(
        f"{url(elim_stage)}{component.pk}/",
        payload(kind=ComponentKind.QUIZ, name="Test online", weight_numerator=1, weight_denominator=2),
    )

    component.refresh_from_db()
    assert response.status_code == 302
    assert component.weight_denominator == 2
    diff = AuditLog.objects.get(action="stage_component.updated").diff
    assert diff["before"]["weight"] == [1, 1]
    assert diff["after"]["weight"] == [1, 2]


def test_deleting_the_last_component_says_the_stage_counts_the_old_way(coordinator_client, elim_stage):
    component = StageComponent.objects.create(stage=elim_stage, kind=ComponentKind.SUBMISSIONS)

    response = coordinator_client.post(f"{url(elim_stage)}{component.pk}/delete/", follow=True)

    assert not StageComponent.objects.filter(stage=elim_stage).exists()
    assert AuditLog.objects.filter(action="stage_component.deleted").exists()
    assert "liczy się teraz z formy etapu" in response.content.decode()


def test_a_component_of_another_stage_is_404(coordinator_client, edition, elim_stage):
    other_stage = StageFactory(edition=edition, kind=StageKind.FINAL)
    foreign = StageComponent.objects.create(stage=other_stage, kind=ComponentKind.SUBMISSIONS)

    assert coordinator_client.get(f"{url(elim_stage)}{foreign.pk}/").status_code == 404
    assert coordinator_client.post(f"{url(elim_stage)}{foreign.pk}/delete/").status_code == 404
    assert StageComponent.objects.filter(pk=foreign.pk).exists()
