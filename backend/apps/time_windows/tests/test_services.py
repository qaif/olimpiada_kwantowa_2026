"""Rozstrzyganie okna ucznia i reguły zmian (TZ-01 § 1, § 5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import Stage
from apps.competitions.services import update_stage
from apps.competitions.tests.factories import CurrentEditionFactory, InterviewStageFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.time_windows import access, services, zones
from apps.time_windows.models import (
    DelegationWindow,
    ParticipantTimezone,
    ParticipantWindow,
    TimeWindow,
    WindowPlan,
)
from apps.time_windows.zones import COUNTRY_TIMEZONES, is_valid_timezone

from .conftest import enable_flag, fresh_stage

pytestmark = pytest.mark.django_db


# --- przydział domyślny ze strefy kraju (czysta reguła, stałe daty) ----------------------------


def _view(starts, hour=10):
    plan = WindowPlan(duration_minutes=300, preferred_local_hour=hour)
    windows = [
        TimeWindow(plan=plan, label=label, starts_at=start, pk=index + 1)
        for index, (label, start) in enumerate(starts)
    ]
    return services.PlanView(plan=plan, stage=None, windows=windows)


def test_default_window_is_the_one_starting_closest_to_the_preferred_local_hour():
    # Styczeń (czas zimowy): 00:00 UTC = 09:00 Tokio, 08:00 UTC = 09:00 Warszawa, 16:00 UTC = 11:00 Nowy Jork.
    view = _view(
        [
            ("A", datetime(2027, 1, 10, 0, 0, tzinfo=UTC)),
            ("B", datetime(2027, 1, 10, 8, 0, tzinfo=UTC)),
            ("C", datetime(2027, 1, 10, 16, 0, tzinfo=UTC)),
        ]
    )

    assert services.default_window(view, "Asia/Tokyo").label == "A"
    assert services.default_window(view, "Europe/Warsaw").label == "B"
    assert services.default_window(view, "America/New_York").label == "C"
    # Bez strefy (region spoza mapy) – pierwsze okno, a nie losowe.
    assert services.default_window(view, None).label == "A"


def test_default_window_measures_distance_around_the_clock():
    """23:00 i 01:00 dzielą dwie godziny – nie dwadzieścia dwie."""
    view = _view(
        [("A", datetime(2027, 1, 10, 1, 0, tzinfo=UTC)), ("B", datetime(2027, 1, 10, 13, 0, tzinfo=UTC))],
        hour=23,
    )

    assert services.default_window(view, "UTC").label == "A"


def test_every_country_has_a_valid_capital_timezone():
    from apps.accounts.countries import COUNTRIES

    assert {code for code, _name in COUNTRIES} <= set(COUNTRY_TIMEZONES)
    assert all(is_valid_timezone(name) for name in COUNTRY_TIMEZONES.values())


# --- rozstrzyganie okna ucznia -------------------------------------------------------------------


def test_resolution_order_participant_then_delegation_then_first(world):
    view = services.load_plan(world.stage)

    assert services.resolve(view, world.student_a).window.label == "A"
    assert services.resolve(view, world.student_a).source == services.SOURCE_DELEGATION
    assert services.resolve(view, world.student_b).window.label == "B"

    ParticipantWindow.objects.create(
        plan=world.plan, participant=world.student_b, window=world.windows["C"], reason="test"
    )
    assert services.resolve(view, world.student_b).window.label == "C"
    assert services.resolve(view, world.student_b).source == services.SOURCE_PARTICIPANT

    loner = ParticipantFactory(competition=world.competition)
    # Bez delegacji i bez wyjątku – **ostatnie** okno (H1): konto niepodpięte do drużyny nie
    # dostaje treści wcześniej niż ktokolwiek inny.
    assert services.resolve(view, loner).window.label == "C"
    assert services.resolve(view, loner).source == services.SOURCE_LAST


def test_delegation_without_assignment_gets_the_country_default(world):
    DelegationWindow.objects.filter(delegation=world.delegation_jp).delete()
    view = services._load(world.stage)

    effective = services.resolve(view, world.student_a)
    assert effective.source == services.SOURCE_COUNTRY
    assert effective.window == services.default_window(view, "Asia/Tokyo")


def test_default_assignment_is_frozen_once_the_first_window_starts(world, monkeypatch):
    """L4: po starcie przydział domyślny jest zapisany – zmiana mapy stref (wdrożenie, ``tzdata``)
    w trakcie zawodów nie przenosi kraju do innego okna."""
    DelegationWindow.objects.filter(delegation=world.delegation_jp).delete()
    expected = services.default_window(services._load(world.stage), "Asia/Tokyo")

    services.load_plan(world.stage, world.now)
    frozen = DelegationWindow.objects.get(plan=world.plan, delegation=world.delegation_jp)
    assert frozen.window == expected

    # Inna strefa „stolicy” po wdrożeniu – przydział zostaje.
    monkeypatch.setitem(zones.COUNTRY_TIMEZONES, "jp", "America/Lima")
    assert services.resolve(services.load_plan(world.stage, world.now), world.student_a).window == expected


def test_participant_timezone_does_not_move_the_window(world):
    """Strefa ucznia służy do wyświetlania – zmiana strefy nie może dać drugiego startu."""
    DelegationWindow.objects.filter(delegation=world.delegation_jp).delete()
    view = services.load_plan(world.stage)
    before = services.resolve(view, world.student_a).window

    services.set_participant_timezone(world.student_a, "America/Los_Angeles")

    assert services.resolve(services.load_plan(world.stage), world.student_a).window == before


def test_effective_terms_include_extra_time_and_grace(world):
    world.stage.grace_seconds = 60
    world.stage.save(update_fields=["grace_seconds"])
    ParticipantWindow.objects.create(
        plan=world.plan, participant=world.student_a, extra_minutes=30, reason="dostosowanie"
    )
    view = services.load_plan(fresh_stage(world.stage))

    effective = services.resolve(view, world.student_a)
    window = world.windows["A"]
    assert effective.opens_at == window.starts_at
    assert effective.deadline_at == window.starts_at + timedelta(minutes=330)
    assert effective.submission_deadline == effective.deadline_at + timedelta(seconds=60)
    # Moment ujawnienia: najpóźniejszy **własny** termin ucznia + tolerancja (L2). Dodatkowe 30 min
    # w oknie A nie przesuwa końca okna C.
    assert view.release_at == world.windows["C"].starts_at + timedelta(minutes=300, seconds=60)


# --- kopia etapu ucznia -----------------------------------------------------------------------


def test_personal_stage_carries_the_window_and_refuses_to_be_saved(world):
    stage = fresh_stage(world.stage)
    personal = access.personal_stage(stage, world.student_b)

    assert personal is not stage
    assert personal.opens_at == world.windows["B"].starts_at
    assert not personal.is_open_for_submissions(world.now)
    assert stage.is_open_for_submissions(world.now)  # rama etapu bez zmian
    with pytest.raises(RuntimeError):
        personal.save()


def test_without_the_flag_every_gate_is_the_old_rule_and_asks_nothing(world, django_assert_num_queries):
    enable_flag(world.competition, False)
    stage = fresh_stage(world.stage)

    with django_assert_num_queries(0):
        assert access.personal_stage(stage, world.student_b) is stage
        assert access.statements_visible(stage, competition=world.competition)
        assert access.release_at(stage, world.competition) is None
        assert not access.windows_running(stage, world.now, world.competition)
        access.assert_results_publishable(stage, competition=world.competition)


# --- zakładanie planu i zmiany -------------------------------------------------------------------


def _stage(competition, now, **kwargs):
    edition = CurrentEditionFactory(competition=competition)
    return StageFactory(
        edition=edition, opens_at=now + timedelta(hours=1), deadline_at=now + timedelta(days=2), **kwargs
    )


def _create(stage, now, **overrides):
    values = {
        "duration_minutes": 300,
        "preferred_local_hour": 10,
        "first_start": stage.opens_at,
        "count": 3,
        "interval_minutes": 480,
        "now": now,
    }
    values.update(overrides)
    return services.create_plan(stage, **values)


def test_create_plan_builds_labelled_windows_and_audits(competition):
    now = timezone.now()
    stage = _stage(competition, now)

    plan = _create(stage, now)

    assert [item.label for item in plan.windows.order_by("starts_at")] == ["A", "B", "C"]
    assert AuditLog.objects.filter(action="time_windows.plan_created").exists()


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"interval_minutes": 120}, "WINDOWS_OVERLAP"),
        ({"count": 13}, "WINDOWS_COUNT"),
        ({"duration_minutes": 5}, "WINDOWS_DURATION"),
        ({"interval_minutes": 2000}, "WINDOWS_OUTSIDE_STAGE"),
    ],
)
def test_create_plan_refuses_bad_shapes(competition, overrides, code):
    now = timezone.now()
    stage = _stage(competition, now)

    with pytest.raises(DomainError) as error:
        _create(stage, now, **overrides)
    assert error.value.machine_code == code


def test_create_plan_only_before_the_stage_opens_and_not_for_interviews(competition):
    now = timezone.now()
    stage = _stage(competition, now)
    with pytest.raises(DomainError) as error:
        _create(stage, stage.opens_at + timedelta(minutes=1))
    assert error.value.machine_code == "WINDOWS_STAGE_OPENED"

    interview = InterviewStageFactory(edition=stage.edition, opens_at=now + timedelta(hours=1))
    with pytest.raises(DomainError) as error:
        _create(interview, now)
    assert error.value.machine_code == "WINDOWS_STAGE_FORMAT"


def test_windows_are_frozen_after_the_first_start(world):
    with pytest.raises(DomainError) as error:
        services.move_window(world.windows["C"], starts_at=world.now + timedelta(hours=20), now=world.now)
    assert error.value.machine_code == "WINDOWS_STARTED"
    with pytest.raises(DomainError):
        services.update_plan(world.plan, duration_minutes=200, preferred_local_hour=9, now=world.now)
    with pytest.raises(DomainError):
        services.delete_plan(world.plan, now=world.now)


def test_assignment_changes_only_between_windows_that_have_not_started(world):
    # Ze startującego okna A – odmowa (drugi start po obejrzeniu zadań).
    with pytest.raises(DomainError) as error:
        services.assign_delegation(world.plan, world.delegation_jp, world.windows["C"].pk, now=world.now)
    assert error.value.machine_code == "WINDOWS_ASSIGNMENT_LOCKED"
    # Do okna A, które trwa – odmowa (start w biegu).
    with pytest.raises(DomainError):
        services.assign_delegation(world.plan, world.delegation_us, world.windows["A"].pk, now=world.now)
    # B → C, oba przed startem – zgoda i audyt.
    services.assign_delegation(world.plan, world.delegation_us, world.windows["C"].pk, now=world.now)

    assert DelegationWindow.objects.get(delegation=world.delegation_us).window == world.windows["C"]
    assert AuditLog.objects.filter(action="time_windows.delegation_assigned").exists()


def test_extra_time_during_the_window_is_allowed_but_not_past_the_stage(world):
    services.set_participant_exception(
        world.plan, world.student_a, window_id=None, extra_minutes=45, reason="awaria łącza", now=world.now
    )
    view = services.load_plan(world.stage)
    assert services.resolve(view, world.student_a).extra_minutes == 45

    # Rama etapu tuż za oknem C – doba dodatkowego czasu w oknie A wyszłaby poza nią.
    Stage.objects.filter(pk=world.stage.pk).update(
        deadline_at=world.windows["C"].starts_at + timedelta(hours=6)
    )
    with pytest.raises(DomainError) as error:
        services.set_participant_exception(
            world.plan,
            world.student_a,
            window_id=None,
            extra_minutes=24 * 60,
            reason="za dużo",
            now=world.now,
        )
    assert error.value.machine_code == "WINDOWS_OUTSIDE_STAGE"

    with pytest.raises(DomainError) as error:
        services.set_participant_exception(
            world.plan, world.student_b, window_id=None, extra_minutes=10, reason="", now=world.now
        )
    assert error.value.machine_code == "WINDOWS_REASON_REQUIRED"


def test_extra_time_cannot_change_after_the_students_deadline(world):
    later = world.windows["A"].starts_at + timedelta(hours=6)
    with pytest.raises(DomainError) as error:
        services.set_participant_exception(
            world.plan, world.student_a, window_id=None, extra_minutes=30, reason="po czasie", now=later
        )
    assert error.value.machine_code == "WINDOWS_EXTRA_LOCKED"


def test_country_timezone_change_freezes_started_plans(world):
    DelegationWindow.objects.filter(delegation=world.delegation_jp).delete()
    view = services.load_plan(world.stage)
    before = services.resolve(view, world.student_a).window

    services.set_country_timezone(world.delegation_jp.country, "America/Lima", now=world.now)

    frozen = DelegationWindow.objects.get(delegation=world.delegation_jp)
    assert frozen.window == before
    assert AuditLog.objects.filter(action="time_windows.country_timezone_set").exists()


def test_unknown_timezone_is_refused(world):
    with pytest.raises(DomainError):
        services.set_participant_timezone(world.student_a, "Mars/Olympus")


def test_stage_envelope_cannot_cut_the_windows(world):
    with pytest.raises(DomainError) as error:
        update_stage(world.stage, None, deadline_at=world.now + timedelta(hours=10), now=world.now)
    assert error.value.machine_code == "STAGE_WINDOWS_OUTSIDE"


def test_results_cannot_be_published_before_the_last_window_ends(world):
    stage = fresh_stage(world.stage)
    with pytest.raises(DomainError) as error:
        access.assert_results_publishable(stage, now=world.now)
    assert error.value.machine_code == "WINDOWS_NOT_FINISHED"
    access.assert_results_publishable(stage, now=world.now + timedelta(days=1))


# --- RODO -----------------------------------------------------------------------------------------


def test_export_and_anonymisation_of_time_window_data(world):
    from apps.accounts.processing_register import TIME_WINDOWS_ACTIVITY, activities_for
    from apps.accounts.profile import anonymise_account
    from apps.time_windows.privacy import export_section

    ParticipantTimezone.objects.create(participant=world.student_a, timezone="Asia/Tokyo")
    ParticipantWindow.objects.create(
        plan=world.plan, participant=world.student_a, extra_minutes=15, reason="dostosowanie"
    )

    section = export_section(world.student_a)
    assert section["strefa_czasowa"] == "Asia/Tokyo"
    assert section["wyjatki"][0]["dodatkowy_czas_min"] == 15
    assert TIME_WINDOWS_ACTIVITY in activities_for(world.competition)

    anonymise_account(world.student_a.user)

    assert not ParticipantTimezone.objects.filter(participant=world.student_a).exists()
    row = ParticipantWindow.objects.get(participant=world.student_a)
    assert (row.extra_minutes, row.reason) == (15, "-")
