"""Ekrany „Okna czasowe” koordynatora i opiekuna drużyny: role, izolacja, czynności, audyt."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.delegations import DelegationLeader
from apps.accounts.models import GROUP_TEAM_LEADER
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.core.models import AuditLog
from apps.time_windows.models import DelegationWindow, ParticipantTimezone, ParticipantWindow, WindowPlan

from .conftest import enable_flag

pytestmark = pytest.mark.django_db


def windows_url(stage, name="coordinator-stage-windows", *args):
    return reverse(f"web:{name}", args=[stage.pk, *args])


@pytest.fixture
def coordinator_client(world, client_for):
    client = client_for(world.competition)
    client.force_login(CoordinatorFactory())
    return client


def test_coordinator_sees_timeline_counts_and_students(world, coordinator_client):
    response = coordinator_client.get(windows_url(world.stage))

    assert response.status_code == 200
    rows = {row.window.label: row for row in response.context["overview"].windows}
    assert (rows["A"].students, rows["A"].in_progress, rows["A"].state) == (1, 1, "open")
    assert (rows["B"].students, rows["B"].in_progress, rows["B"].state) == (1, 0, "before")
    page = response.content.decode()
    assert world.student_a.public_code in page and world.student_b.public_code in page


def test_screen_is_gated_by_role_flag_and_competition(world, client_for, other_competition):
    participant = client_for(world.competition)
    participant.force_login(world.student_a.user)
    assert participant.get(windows_url(world.stage)).status_code == 403

    coordinator = client_for(world.competition)
    coordinator.force_login(CoordinatorFactory())
    foreign = StageFactory(edition=CurrentEditionFactory(competition=other_competition))
    assert coordinator.get(windows_url(foreign)).status_code == 404

    enable_flag(world.competition, False)
    assert coordinator.get(windows_url(world.stage)).status_code == 404


def test_coordinator_creates_plan_from_the_screen(competition, client_for):
    enable_flag(competition)
    now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
    stage = StageFactory(
        edition=CurrentEditionFactory(competition=competition),
        opens_at=now + timedelta(hours=1),
        deadline_at=now + timedelta(days=2),
    )
    client = client_for(competition)
    client.force_login(CoordinatorFactory())

    response = client.post(
        windows_url(stage, "coordinator-stage-windows-create"),
        {
            "duration_minutes": 300,
            "first_start": (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "count": 3,
            "interval_minutes": 480,
            "preferred_local_hour": 10,
        },
    )

    assert response.status_code == 302
    plan = WindowPlan.objects.get(stage=stage)
    assert plan.windows.count() == 3
    assert AuditLog.objects.filter(action="time_windows.plan_created").exists()


def test_coordinator_moves_a_country_and_sets_an_exception(world, coordinator_client):
    coordinator_client.post(
        windows_url(world.stage, "coordinator-stage-windows-delegation", world.delegation_us.pk),
        {"window": world.windows["C"].pk},
    )
    coordinator_client.post(
        windows_url(world.stage, "coordinator-stage-windows-exception"),
        {"code": world.student_a.public_code, "window": "", "extra_minutes": 20, "reason": "dostosowanie"},
    )

    assert DelegationWindow.objects.get(delegation=world.delegation_us).window == world.windows["C"]
    assert ParticipantWindow.objects.get(participant=world.student_a).extra_minutes == 20


def test_locked_assignment_is_reported_not_applied(world, coordinator_client):
    response = coordinator_client.post(
        windows_url(world.stage, "coordinator-stage-windows-delegation", world.delegation_jp.pk),
        {"window": world.windows["C"].pk},
        follow=True,
    )

    assert "już się zaczęło" in response.content.decode()
    assert DelegationWindow.objects.get(delegation=world.delegation_jp).window == world.windows["A"]


# --- opiekun drużyny ------------------------------------------------------------------------------


def _leader(world, delegation):
    user = UserFactory(groups=[GROUP_TEAM_LEADER])
    DelegationLeader.objects.create(delegation=delegation, user=user)
    return user


def test_leader_sees_own_students_windows_and_sets_a_timezone(world, client_for):
    world.student_b.user.first_name, world.student_b.user.last_name = "Ann", "Other"
    world.student_b.user.save(update_fields=["first_name", "last_name"])
    client = client_for(world.competition)
    client.force_login(_leader(world, world.delegation_jp))

    page = client.get(reverse("web:delegation-time-windows"))
    assert page.status_code == 200
    content = page.content.decode()
    assert world.student_a.user.get_full_name() in content
    assert world.student_b.user.get_full_name() not in content

    client.post(
        reverse("web:delegation-time-windows-timezone", args=[world.student_a.pk]), {"timezone": "Asia/Seoul"}
    )
    assert ParticipantTimezone.objects.get(participant=world.student_a).timezone == "Asia/Seoul"
    assert AuditLog.objects.filter(action="time_windows.participant_timezone_set").exists()


def test_leader_cannot_touch_another_countrys_student(world, client_for):
    client = client_for(world.competition)
    client.force_login(_leader(world, world.delegation_jp))

    response = client.post(
        reverse("web:delegation-time-windows-timezone", args=[world.student_b.pk]), {"timezone": "Asia/Tokyo"}
    )

    assert response.status_code == 404
    assert not ParticipantTimezone.objects.filter(participant=world.student_b).exists()


def test_participant_is_not_a_leader(world, client_for):
    client = client_for(world.competition)
    client.force_login(ParticipantFactory(competition=world.competition).user)

    assert client.get(reverse("web:delegation-time-windows")).status_code == 403
