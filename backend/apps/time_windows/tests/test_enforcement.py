"""Egzekwowanie okien po stronie serwera i ochrona przed przeciekiem (TZ-01 § 2–4).

Świat z ``conftest.py``: okno A trwa (Japonia, ``student_a``), okno B zacznie się za kilka godzin
(USA, ``student_b``). Każda droga do treści zadań i do oddania pracy ma odpowiedzieć uczniowi B
„jeszcze nie”, uczniowi A „tak”, a światu – „po końcu ostatniego okna”.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from freezegun import freeze_time
from rest_framework.test import APIClient

from apps.chat.services import forcing_stage
from apps.cms.live_data import problems_state
from apps.competitions.models import Stage, StageFormat
from apps.core.api import DomainError
from apps.forum.services import stage_forcing_pre_moderation
from apps.quiz import services as quiz_services
from apps.quiz.models import ShowResultsAfter
from apps.quiz.tests.factories import QuizFactory, choice_question
from apps.submissions.models import Submission
from apps.submissions.services import create_submission
from apps.submissions.tests.factories import pdf_upload
from apps.time_windows import access, services
from apps.time_windows.models import ParticipantTimezone, ParticipantWindow
from apps.web.participant_now import STATE_BEFORE, STATE_OPEN

from .conftest import enable_flag, fresh_stage

pytestmark = pytest.mark.django_db


def _statement(world):
    from django.core.files.base import ContentFile

    world.problem.statement_pdf.save("tresc.pdf", ContentFile(b"%PDF-1.7\n%%EOF\n"), save=True)
    return reverse("competitions:problem-statement", args=[world.problem.pk])


# --- upload rozwiązań -----------------------------------------------------------------------------


def test_upload_respects_each_students_own_window(world):
    with pytest.raises(DomainError) as error:
        create_submission(world.student_b.user, fresh_stage(world.stage), 1, pdf_upload())
    assert error.value.machine_code == "STAGE_NOT_OPEN"

    submission = create_submission(world.student_a.user, fresh_stage(world.stage), 1, pdf_upload())
    assert submission.is_late is False


def test_upload_after_the_window_is_refused_unless_extra_time(world):
    after_a = world.windows["A"].starts_at + timedelta(hours=5, minutes=10)
    with pytest.raises(DomainError) as error:
        create_submission(world.student_a.user, fresh_stage(world.stage), 1, pdf_upload(), now=after_a)
    assert error.value.machine_code == "DEADLINE_PASSED"

    ParticipantWindow.objects.create(
        plan=world.plan, participant=world.student_a, extra_minutes=30, reason="dostosowanie"
    )
    submission = create_submission(
        world.student_a.user, fresh_stage(world.stage), 1, pdf_upload(), now=after_a
    )
    assert submission.is_late is False


def test_upload_api_uses_the_same_window(world, client_for):
    client_for(world.competition)  # dopisuje domeny testowe do ALLOWED_HOSTS
    client = APIClient(HTTP_HOST=world.competition.primary_domain)
    url = reverse("submissions:submission-create", kwargs={"stage_id": world.stage.pk, "number": 1})

    client.force_authenticate(world.student_b.user)
    refused = client.post(url, {"file": pdf_upload()}, format="multipart")
    client.force_authenticate(world.student_a.user)
    accepted = client.post(url, {"file": pdf_upload()}, format="multipart")

    assert refused.status_code == 403, refused.content
    assert accepted.status_code == 201, accepted.content
    assert Submission.objects.filter(entry=world.entry_a).count() == 1


# --- treść zadań ----------------------------------------------------------------------------------


def test_statements_visible_per_window_and_publicly_after_release(world):
    stage = fresh_stage(world.stage)
    release = services.load_plan(stage).release_at

    assert not access.statements_visible(stage, now=world.now)
    assert access.statements_visible(stage, participant=world.student_a, now=world.now)
    assert not access.statements_visible(stage, participant=world.student_b, now=world.now)
    assert access.statements_visible(stage, now=release)


def test_statement_pdf_follows_the_window(world, client_for):
    url = _statement(world)

    assert client_for(world.competition).get(url).status_code == 404
    student_b = client_for(world.competition)
    student_b.force_login(world.student_b.user)
    assert student_b.get(url).status_code == 404
    student_a = client_for(world.competition)
    student_a.force_login(world.student_a.user)
    assert student_a.get(url).status_code == 200


def test_public_problem_lists_stay_empty_until_release(world, client_for):
    _statement(world)

    state = problems_state(world.competition, now=world.now)
    assert state.stage == world.stage and not state.stage_has_opened and state.problems == []

    data = client_for(world.competition).get(reverse("competitions:edition-current")).json()
    assert data["problems"] == []

    release = services.load_plan(fresh_stage(world.stage)).release_at
    with freeze_time(release + timedelta(minutes=1)):
        assert problems_state(world.competition).problems == [world.problem]


def test_participant_panel_shows_own_window_and_cards_only_from_its_start(world, client_for):
    student_a = client_for(world.competition)
    student_a.force_login(world.student_a.user)
    page_a = student_a.get(reverse("web:me")).content.decode()

    student_b = client_for(world.competition)
    student_b.force_login(world.student_b.user)
    response_b = student_b.get(reverse("web:me"))
    page_b = response_b.content.decode()

    assert 'data-time-window="A"' in page_a
    assert world.problem.title in page_a
    assert 'data-time-window="B"' in page_b
    assert world.problem.title not in page_b
    # Nagłówek „Co teraz” liczy do startu **okna B**, nie do ramy etapu.
    panel = response_b.context["now_panel"]
    assert panel.state == STATE_BEFORE
    assert panel.deadline.at == world.windows["B"].starts_at
    assert student_a.get(reverse("web:me")).context["now_panel"].state == STATE_OPEN


def test_panel_hours_are_in_the_students_timezone(world, client_for):
    ParticipantTimezone.objects.create(participant=world.student_a, timezone="Asia/Tokyo")
    client = client_for(world.competition)
    client.force_login(world.student_a.user)

    page = client.get(reverse("web:me")).content.decode()

    assert "Asia/Tokyo" in page
    assert "czas polski" not in page


def test_window_card_is_translated_from_the_apps_catalog(world, client_for, english_enabled_site, settings):
    english_enabled_site(world.competition)
    client = client_for(world.competition)
    client.force_login(world.student_a.user)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"

    page = client.get(reverse("web:me")).content.decode()

    assert "Your time window: A" in page


def test_personal_calendar_has_the_students_own_window(world):
    from apps.cms.calendar import participant_calendar

    items = participant_calendar(world.student_b, competition=world.competition)
    own = [item for item in items if item.starts_at == world.windows["B"].starts_at]

    assert len(own) == 1 and "B" in own[0].title


def test_without_the_flag_the_panel_is_unchanged(world, client_for):
    enable_flag(world.competition, False)
    client = client_for(world.competition)
    client.force_login(world.student_b.user)

    page = client.get(reverse("web:me")).content.decode()

    assert "data-time-window" not in page
    assert world.problem.title in page  # rama etapu: otwarty dla wszystkich


# --- test online ----------------------------------------------------------------------------------


def _quiz_world(world, **quiz_fields):
    Stage.objects.filter(pk=world.stage.pk).update(format=StageFormat.QUIZ)
    stage = fresh_stage(world.stage)
    quiz = QuizFactory(stage=stage, duration_minutes=600, **quiz_fields)
    choice_question(quiz)
    world.entry_a.stage = stage
    world.entry_b.stage = stage
    return quiz


def test_quiz_attempt_starts_only_in_the_students_window_and_ends_with_it(world):
    quiz = _quiz_world(world, opens_at=world.now - timedelta(days=1), closes_at=world.now + timedelta(days=1))

    with pytest.raises(DomainError) as error:
        quiz_services.start_attempt(quiz=quiz, entry=world.entry_b, now=world.now)
    assert error.value.machine_code == "QUIZ_CLOSED"

    attempt = quiz_services.start_attempt(quiz=quiz, entry=world.entry_a, now=world.now)
    # Test trwa 10 h, okno 5 h – podejście kończy się z oknem ucznia, a własne terminy testu się nie liczą.
    assert attempt.deadline_at == world.windows["A"].starts_at + timedelta(hours=5)


def test_quiz_result_after_close_waits_for_the_last_window(world):
    quiz = _quiz_world(world, show_results_after=ShowResultsAfter.AFTER_CLOSE)
    attempt = quiz_services.start_attempt(quiz=quiz, entry=world.entry_a, now=world.now)
    attempt.score = 1
    release = services.load_plan(fresh_stage(world.stage)).release_at

    assert not quiz_services.may_show_result(quiz, attempt, world.windows["A"].starts_at + timedelta(hours=6))
    assert quiz_services.may_show_result(quiz, attempt, release)


# --- forum i czat ---------------------------------------------------------------------------------


def test_forum_and_chat_are_premoderated_from_first_window_to_release(world):
    stage = fresh_stage(world.stage)
    release = services.load_plan(stage).release_at

    assert stage_forcing_pre_moderation(world.competition, world.now) == world.stage
    assert forcing_stage(world.competition, world.now) == world.stage
    assert access.windows_running(stage, world.now, world.competition)
    assert not access.windows_running(
        stage, world.windows["A"].starts_at - timedelta(minutes=1), world.competition
    )
    assert not access.windows_running(stage, release, world.competition)
