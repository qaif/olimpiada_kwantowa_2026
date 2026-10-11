"""Poprawki po przeglądzie TZ-01 (H1, M1–M5, L1–L6) i luki testowe wskazane w przeglądzie."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.models import Stage, StageFormat
from apps.competitions.tests.factories import CurrentEditionFactory, StageEntryFactory, StageFactory
from apps.core.api import DomainError
from apps.quiz import services as quiz_services
from apps.quiz.models import QuizAttempt, ShowResultsAfter
from apps.quiz.tests.factories import QuizFactory, choice_question
from apps.submissions.services import create_submission
from apps.submissions.tests.factories import pdf_upload
from apps.time_windows import access, services
from apps.time_windows.middleware import ParticipantTimezoneMiddleware, participant_timezone
from apps.time_windows.models import (
    DelegationWindow,
    ParticipantTimezone,
    ParticipantWindow,
    TimeWindow,
    WindowPlan,
)
from apps.time_windows.zones import utc_offset_label

from .conftest import enable_flag, fresh_stage

pytestmark = pytest.mark.django_db


def _view(starts, hour=10):
    plan = WindowPlan(duration_minutes=60, preferred_local_hour=hour)
    windows = [
        TimeWindow(plan=plan, label=label, starts_at=start, pk=index + 1)
        for index, (label, start) in enumerate(sorted(starts, key=lambda item: item[1]))
    ]
    return services.PlanView(plan=plan, stage=None, windows=windows)


def _future_plan(competition):
    enable_flag(competition)
    now = timezone.now()
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(
        edition=edition, opens_at=now + timedelta(hours=1), deadline_at=now + timedelta(days=2)
    )
    plan = services.create_plan(
        stage,
        duration_minutes=300,
        preferred_local_hour=10,
        first_start=stage.opens_at,
        count=3,
        interval_minutes=480,
        now=now,
    )
    return stage, plan, list(plan.windows.order_by("starts_at")), now


# --- H1: uczeń bez wpisu / bez delegacji ------------------------------------------------------------


def test_unlinked_dummy_student_gets_nothing_early(world, client_for):
    """Konto „słup”: profil w konkursie, bez delegacji, bez wyjątku – ostatnie okno i nic przed nim."""
    dummy = ParticipantFactory(competition=world.competition)
    StageEntryFactory(stage=world.stage, participant=dummy)
    stage = fresh_stage(world.stage)

    assert services.resolve(services.load_plan(stage), dummy).window.label == "C"
    assert not access.statements_visible(stage, participant=dummy, now=world.now)
    with pytest.raises(DomainError):
        create_submission(dummy.user, fresh_stage(world.stage), 1, pdf_upload())


def test_statements_need_an_entry_even_inside_an_open_window(world):
    """Wyjątek z oknem A nie wystarcza – treść widzi wyłącznie uczeń zgłoszony do etapu."""
    dummy = ParticipantFactory(competition=world.competition)
    ParticipantWindow.objects.create(
        plan=world.plan, participant=dummy, window=world.windows["A"], reason="pomyłka"
    )
    assert not access.statements_visible(fresh_stage(world.stage), participant=dummy, now=world.now)

    StageEntryFactory(stage=world.stage, participant=dummy)
    assert access.statements_visible(fresh_stage(world.stage), participant=dummy, now=world.now)


def test_statement_pdf_is_404_for_a_participant_without_entry(world, client_for):
    from django.core.files.base import ContentFile

    world.problem.statement_pdf.save("tresc.pdf", ContentFile(b"%PDF-1.7\n%%EOF\n"), save=True)
    dummy = ParticipantFactory(competition=world.competition, delegation=world.delegation_jp)
    client = client_for(world.competition)
    client.force_login(dummy.user)

    assert client.get(reverse("competitions:problem-statement", args=[world.problem.pk])).status_code == 404


# --- M1: karta zadania nie wraca przed oknem ------------------------------------------------------------


def test_upload_view_before_the_window_is_404_without_the_problem_card(world, client_for):
    client = client_for(world.competition)
    client.force_login(world.student_b.user)

    response = client.post(
        reverse("web:problem-upload", args=[world.stage.pk, 1]), {"file": pdf_upload(), "confirm": "on"}
    )

    assert response.status_code == 404
    assert world.problem.title not in response.content.decode()


# --- M2: start podejścia w jednej transakcji ----------------------------------------------------------


def _quiz(world, **fields):
    Stage.objects.filter(pk=world.stage.pk).update(format=StageFormat.QUIZ)
    stage = fresh_stage(world.stage)
    quiz = QuizFactory(stage=stage, **fields)
    choice_question(quiz)
    world.entry_a.stage = stage
    return quiz


def test_start_attempt_runs_in_its_own_atomic_block(world, monkeypatch):
    quiz = _quiz(world)
    outside = len(connection.atomic_blocks)
    seen = {}
    original = QuizAttempt.objects.create

    def spy(**kwargs):
        seen["depth"] = len(connection.atomic_blocks)
        return original(**kwargs)

    monkeypatch.setattr(QuizAttempt.objects, "create", spy)
    quiz_services.start_attempt(quiz=quiz, entry=world.entry_a, now=world.now)

    assert seen["depth"] > outside


# --- M3: dodatkowy czas wydłuża podejście ---------------------------------------------------------------


def test_extra_time_extends_a_short_quiz_attempt(world):
    quiz = _quiz(world, duration_minutes=30)
    ParticipantWindow.objects.create(
        plan=world.plan, participant=world.student_a, extra_minutes=20, reason="dostosowanie"
    )

    attempt = quiz_services.start_attempt(quiz=quiz, entry=world.entry_a, now=world.now)

    assert attempt.deadline_at == world.now + timedelta(minutes=50)


def test_extra_time_is_capped_by_the_students_own_end(world):
    quiz = _quiz(world, duration_minutes=600)
    ParticipantWindow.objects.create(
        plan=world.plan, participant=world.student_a, extra_minutes=20, reason="dostosowanie"
    )

    attempt = quiz_services.start_attempt(quiz=quiz, entry=world.entry_a, now=world.now)

    assert attempt.deadline_at == world.windows["A"].starts_at + timedelta(minutes=320)


# --- M4: strefa ucznia tylko w panelu uczestnika i tylko bez ról personelu -----------------------------


def _request(world, user):
    request = RequestFactory().get("/")
    request.user = user
    request.competition = world.competition
    return request


def test_zone_is_activated_only_for_participant_views(world):
    from apps.time_windows.views import StageWindowsView
    from apps.web.views.participant import MeView

    ParticipantTimezone.objects.create(participant=world.student_a, timezone="Asia/Tokyo")
    middleware = ParticipantTimezoneMiddleware(lambda request: None)
    seen = {}

    def capture(view):
        def inner(request):
            middleware.process_view(request, view, (), {})
            seen[view] = timezone.get_current_timezone_name()

        return inner

    for view in (MeView.as_view(), StageWindowsView.as_view()):
        ParticipantTimezoneMiddleware(capture(view))(_request(world, world.student_a.user))

    names = list(seen.values())
    assert names == ["Asia/Tokyo", "Europe/Warsaw"]
    assert timezone.get_current_timezone_name() == "Europe/Warsaw"  # zdjęta po odpowiedzi


def test_staff_role_keeps_the_service_zone(world):
    from apps.accounts.models import GROUP_COORDINATOR

    ParticipantTimezone.objects.create(participant=world.student_a, timezone="Asia/Tokyo")
    from django.contrib.auth.models import Group

    world.student_a.user.groups.add(Group.objects.get_or_create(name=GROUP_COORDINATOR)[0])

    assert participant_timezone(_request(world, world.student_a.user)) is None


def test_coordinator_screen_is_in_polish_time_even_for_a_participant_coordinator(world, client_for):
    coordinator = CoordinatorFactory()
    ParticipantFactory(competition=world.competition, user=coordinator)
    client = client_for(world.competition)
    client.force_login(coordinator)

    page = client.get(reverse("web:coordinator-stage-windows", args=[world.stage.pk])).content.decode()

    assert "czas polski" in page


# --- M5: podpisy kolumn w strefie ucznia ------------------------------------------------------------


def test_participant_column_labels_name_the_students_zone(world, client_for):
    ParticipantTimezone.objects.create(participant=world.student_a, timezone="Asia/Tokyo")
    # Historia wysyłek (z kolumną „Wysłano”) pojawia się od drugiej wersji.
    create_submission(world.student_a.user, fresh_stage(world.stage), 1, pdf_upload())
    create_submission(world.student_a.user, fresh_stage(world.stage), 1, pdf_upload())
    client = client_for(world.competition)
    client.force_login(world.student_a.user)

    page = client.get(reverse("web:me")).content.decode()
    calendar = client.get(reverse("web:participant-calendar")).content.decode()

    assert "Wysłano (Asia/Tokyo)" in page
    assert "Wysłano (czas polski)" not in page
    assert "Wszystkie godziny: Asia/Tokyo." in calendar


# --- L5: okno ucznia w API wpisów -------------------------------------------------------------------


def test_my_entries_api_carries_the_students_window(world, client_for):
    client_for(world.competition)
    client = APIClient(HTTP_HOST=world.competition.primary_domain)
    client.force_authenticate(world.student_b.user)

    data = client.get(reverse("competitions:my-entries")).json()
    assert data[0]["time_window"]["label"] == "B"
    assert data[0]["time_window"]["opens_at"] == world.windows["B"].starts_at.isoformat()

    enable_flag(world.competition, False)
    assert "time_window" not in client.get(reverse("competitions:my-entries")).json()[0]


# --- L6: ostrzeżenie o wyniku testu „od razu” ---------------------------------------------------------


def test_coordinator_is_warned_about_immediate_quiz_results(world, client_for):
    _quiz(world, show_results_after=ShowResultsAfter.IMMEDIATELY)
    client = client_for(world.competition)
    client.force_login(CoordinatorFactory())

    page = client.get(reverse("web:coordinator-stage-windows", args=[world.stage.pk])).content.decode()

    assert 'data-warning="quiz-results-immediately"' in page


# --- strefy z przesunięciem :30/:45 i czas letni (stałe daty) -----------------------------------


@pytest.mark.parametrize(("zone", "expected"), [("Asia/Kolkata", "A"), ("Asia/Kathmandu", "B")])
def test_default_window_handles_half_and_quarter_hour_offsets(zone, expected):
    # 04:30 UTC = 10:00 w Indiach (UTC+05:30), 04:15 UTC = 10:00 w Nepalu (UTC+05:45).
    view = _view(
        [("A", datetime(2027, 1, 10, 4, 30, tzinfo=UTC)), ("B", datetime(2027, 1, 10, 4, 15, tzinfo=UTC))]
    )
    labels = {item.starts_at: item for item in view.windows}
    chosen = services.default_window(view, zone)

    assert chosen is labels[datetime(2027, 1, 10, 4, 30 if expected == "A" else 15, tzinfo=UTC)]


def test_default_window_follows_daylight_saving_time():
    """Nowy Jork: 14:00 UTC to 10:00 latem (EDT), ale 09:00 zimą (EST)."""
    summer = _view(
        [("A", datetime(2027, 7, 10, 14, 0, tzinfo=UTC)), ("B", datetime(2027, 7, 10, 16, 0, tzinfo=UTC))]
    )
    winter = _view(
        [("A", datetime(2027, 1, 10, 13, 0, tzinfo=UTC)), ("B", datetime(2027, 1, 10, 15, 0, tzinfo=UTC))]
    )

    assert services.default_window(summer, "America/New_York").starts_at.hour == 14
    assert services.default_window(winter, "America/New_York").starts_at.hour == 15


def test_utc_offset_labels_for_odd_offsets():
    assert utc_offset_label(datetime(2027, 1, 10, tzinfo=UTC), "Asia/Kathmandu") == "UTC+05:45"
    assert utc_offset_label(datetime(2027, 1, 10, tzinfo=UTC), "America/St_Johns") == "UTC-03:30"
    assert utc_offset_label(datetime(2027, 7, 10, tzinfo=UTC), "America/St_Johns") == "UTC-02:30"


# --- L3: nachodzenie okien ------------------------------------------------------------------------


def test_added_moved_or_lengthened_windows_cannot_overlap(competition):
    _stage, plan, windows, now = _future_plan(competition)

    for call in (
        lambda: services.add_window(plan, starts_at=windows[0].starts_at + timedelta(hours=1), now=now),
        lambda: services.move_window(
            windows[1], starts_at=windows[0].starts_at + timedelta(hours=2), now=now
        ),
        lambda: services.update_plan(plan, duration_minutes=600, preferred_local_hour=10, now=now),
    ):
        with pytest.raises(DomainError) as error:
            call()
        assert error.value.machine_code == "WINDOWS_OVERLAP"


def test_default_assignment_is_not_frozen_before_the_first_window(competition):
    stage, plan, _windows, now = _future_plan(competition)

    services.load_plan(stage, now)

    assert not DelegationWindow.objects.filter(plan=plan).exists()


# --- L2: rama liczona z własnego końca każdego ucznia ----------------------------------------------


def test_frame_check_uses_each_students_own_end(world):
    """Dodatkowe 2 h w oknie A nie „doklejają się” do okna C: rama tuż za oknem C wystarcza."""
    ParticipantWindow.objects.create(
        plan=world.plan, participant=world.student_a, extra_minutes=120, reason="dostosowanie"
    )
    end_of_c = world.windows["C"].starts_at + timedelta(minutes=300)

    assert services.windows_fit(fresh_stage(world.stage), world.stage.opens_at, end_of_c)
    assert not services.windows_fit(
        fresh_stage(world.stage), world.stage.opens_at, end_of_c - timedelta(minutes=1)
    )


# --- L1: kasowanie ----------------------------------------------------------------------------------


def test_window_with_assignments_cannot_be_deleted_alone(world):
    from django.db.models import RestrictedError

    with pytest.raises(RestrictedError):
        world.windows["A"].delete()


def test_deleting_the_stage_cascades_through_plan_windows_and_assignments(world):
    ParticipantWindow.objects.create(
        plan=world.plan, participant=world.student_b, extra_minutes=5, reason="x"
    )
    plan_pk = world.plan.pk

    Stage.objects.filter(pk=world.stage.pk).delete()

    assert not WindowPlan.objects.filter(pk=plan_pk).exists()
    assert not TimeWindow.objects.filter(plan_id=plan_pk).exists()
    assert not DelegationWindow.objects.filter(plan_id=plan_pk).exists()
    assert not ParticipantWindow.objects.filter(plan_id=plan_pk).exists()


def test_deleting_a_plan_before_the_stage_opens_removes_everything(competition):
    stage, plan, windows, now = _future_plan(competition)
    participant = ParticipantFactory(competition=competition)
    ParticipantWindow.objects.create(plan=plan, participant=participant, window=windows[1], reason="x")

    services.delete_plan(plan, now=now)

    assert not WindowPlan.objects.filter(stage=stage).exists()
    assert not ParticipantWindow.objects.filter(participant=participant).exists()


def test_deleting_a_participant_removes_their_window_rows(world):
    ParticipantTimezone.objects.create(participant=world.student_b, timezone="America/Chicago")
    ParticipantWindow.objects.create(
        plan=world.plan, participant=world.student_b, extra_minutes=5, reason="x"
    )
    pk = world.student_b.pk

    world.entry_b.delete()
    world.student_b.delete()

    assert not ParticipantTimezone.objects.filter(participant_id=pk).exists()
    assert not ParticipantWindow.objects.filter(participant_id=pk).exists()
