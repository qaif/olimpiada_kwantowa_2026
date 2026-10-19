"""Przełącznik udostępniania per etap: ekran koordynatora, dziennik zdarzeń, zadanie Celery do webTEX."""

from __future__ import annotations

import json
from unittest import mock

import pytest
import requests
from django.test import Client

from apps.core.models import AuditLog
from apps.webtex import services, tasks
from apps.webtex.models import StageLatexSettings

from .conftest import INTERNAL_URL, enable, json_response

pytestmark = pytest.mark.django_db


def settings_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/latex/"


@pytest.fixture
def web():
    return Client()


def test_screen_is_404_without_flag(web, coordinator, stage):
    web.force_login(coordinator)
    assert web.get(settings_url(stage)).status_code == 404


def test_participant_cannot_open_the_screen(web, competition, participant, stage):
    enable(competition)
    web.force_login(participant.user)
    assert web.get(settings_url(stage)).status_code == 403


def test_stage_form_links_the_screen_only_with_flag(web, competition, coordinator, stage):
    web.force_login(coordinator)
    edit = f"/coordinator/stages/{stage.pk}/edit/"
    assert settings_url(stage) not in web.get(edit).content.decode()
    enable(competition)
    html = web.get(edit).content.decode()
    assert settings_url(stage) in html
    assert "wyłączone (praca indywidualna)" in html


def test_toggle_is_off_by_default_and_saves_audit_and_enqueues_push(
    web, competition, coordinator, stage, django_capture_on_commit_callbacks
):
    enable(competition)
    web.force_login(coordinator)
    assert 'name="sharing_enabled"' in web.get(settings_url(stage)).content.decode()
    assert services.sharing_enabled(stage) is False

    with (
        mock.patch.object(tasks.push_stage_sharing, "delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = web.post(settings_url(stage), {"sharing_enabled": "on"})

    assert response.status_code == 302
    assert StageLatexSettings.objects.get(stage=stage).sharing_enabled is True
    assert StageLatexSettings.objects.get(stage=stage).updated_by == coordinator
    delay.assert_called_once_with(stage.pk)
    entry = AuditLog.objects.get(action="webtex.sharing_changed")
    assert entry.actor == coordinator
    assert entry.competition == competition
    assert entry.diff == {"stage": stage.pk, "sharing_enabled": [False, True]}

    # Ten sam stan – bez wpisu i bez zadania.
    with (
        mock.patch.object(tasks.push_stage_sharing, "delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        web.post(settings_url(stage), {"sharing_enabled": "on"})
    delay.assert_not_called()
    assert AuditLog.objects.filter(action="webtex.sharing_changed").count() == 1

    # Wyłączenie – checkbox nieobecny w POST.
    with (
        mock.patch.object(tasks.push_stage_sharing, "delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        web.post(settings_url(stage), {})
    delay.assert_called_once_with(stage.pk)
    assert services.sharing_enabled(stage) is False


def test_task_posts_current_state_to_webtex(competition, coordinator, stage, webtex):
    services.set_sharing(stage, True, actor=coordinator)
    webtex.responses.append(json_response(200, {"projects": 4}))

    assert tasks.push_stage_sharing(stage.pk) == "OK"

    call = webtex.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{INTERNAL_URL}/api/integration/sharing"
    assert json.loads(call["data"]) == {"scope": f"{competition.slug}/s{stage.pk}", "enabled": True}
    assert call["headers"]["X-Webtex-Signature"].startswith("t=")
    assert call["timeout"][1] == 10


@pytest.mark.parametrize("failure", [requests.ConnectionError("down"), json_response(503, {})])
def test_task_retries_with_backoff_when_webtex_fails(competition, stage, webtex, failure):
    webtex.responses.append(failure)

    class Retry(Exception):
        pass

    with mock.patch.object(tasks.push_stage_sharing, "retry", side_effect=Retry) as retry:
        with pytest.raises(Retry):
            tasks.push_stage_sharing(stage.pk)

    assert retry.call_args.kwargs["countdown"] == tasks.RETRY_BASE_SECONDS


def test_task_skips_without_configuration(stage, settings, webtex):
    settings.WEBTEX_SHARED_KEY = ""
    assert tasks.push_stage_sharing(stage.pk) == "NOT_CONFIGURED"
    assert webtex.calls == []


# --- asystent AI (Uzupełnienie A, D8/D10) -----------------------------------------------------------


def test_ai_toggle_is_off_by_default_and_independent_of_sharing(
    web, competition, coordinator, stage, django_capture_on_commit_callbacks
):
    enable(competition)
    web.force_login(coordinator)
    html = web.get(settings_url(stage)).content.decode()
    assert 'name="ai_enabled"' in html
    assert "Asystent AI w edytorze LaTeX" in html
    assert services.ai_enabled(stage) is False

    with (
        mock.patch.object(tasks.push_stage_ai, "delay") as ai_delay,
        mock.patch.object(tasks.push_stage_sharing, "delay") as sharing_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        web.post(settings_url(stage), {"ai_enabled": "on"})

    assert services.stage_options(stage) == {"sharing_enabled": False, "ai_enabled": True}
    ai_delay.assert_called_once_with(stage.pk)
    sharing_delay.assert_not_called()
    entry = AuditLog.objects.get(action="webtex.ai_changed")
    assert entry.actor == coordinator
    assert entry.diff == {"stage": stage.pk, "ai_enabled": [False, True]}
    assert not AuditLog.objects.filter(action="webtex.sharing_changed").exists()
    assert "asystent AI: włączony" in web.get(f"/coordinator/stages/{stage.pk}/edit/").content.decode()

    # Oba naraz: udostępnianie włączone, asystent wyłączony – dwa wpisy, dwa zadania.
    with (
        mock.patch.object(tasks.push_stage_ai, "delay") as ai_delay,
        mock.patch.object(tasks.push_stage_sharing, "delay") as sharing_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        web.post(settings_url(stage), {"sharing_enabled": "on"})
    ai_delay.assert_called_once_with(stage.pk)
    sharing_delay.assert_called_once_with(stage.pk)
    assert services.stage_options(stage) == {"sharing_enabled": True, "ai_enabled": False}


def test_ai_task_posts_current_state_to_webtex(competition, coordinator, stage, webtex):
    services.set_ai(stage, True, actor=coordinator)
    webtex.responses.append(json_response(200, {"projects": 0}))

    assert tasks.push_stage_ai(stage.pk) == "OK"

    call = webtex.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{INTERNAL_URL}/api/integration/ai"
    assert json.loads(call["data"]) == {"scope": f"{competition.slug}/s{stage.pk}", "enabled": True}
    assert call["headers"]["X-Webtex-Signature"].startswith("t=")


@pytest.mark.parametrize(
    "failure", [requests.ConnectionError("down"), json_response(401, {"error": "bad_signature"})]
)
def test_ai_task_retries_with_backoff_when_webtex_fails(competition, stage, webtex, failure):
    webtex.responses.append(failure)

    class Retry(Exception):
        pass

    with mock.patch.object(tasks.push_stage_ai, "retry", side_effect=Retry) as retry:
        with pytest.raises(Retry):
            tasks.push_stage_ai(stage.pk)

    assert retry.call_args.kwargs["countdown"] == tasks.RETRY_BASE_SECONDS
