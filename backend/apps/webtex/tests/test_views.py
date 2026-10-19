"""Edytor LaTeX na platformie: bramki otwarcia, złożenie PDF-u przez ``create_submission``, karta zadania."""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlsplit

import pytest
import requests
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.utils import timezone, translation

from apps.accounts.models import CompetitionRole
from apps.competitions.models import StageEntry, StageEntryStatus
from apps.submissions.models import Submission
from apps.tenancy.tests.factories import grant_membership
from apps.webtex import services

from .conftest import INTERNAL_URL, enable, json_response, make_participant, pdf_response
from .test_keys import decode_token, independent_key

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


def open_url(problem) -> str:
    return f"/me/stages/{problem.stage_id}/problems/{problem.number}/latex/"


def submit_url(problem) -> str:
    return f"/me/stages/{problem.stage_id}/problems/{problem.number}/latex/submit/"


@pytest.fixture
def web():
    return Client()


def token_from(response) -> tuple[dict, dict]:
    location = response["Location"]
    parts = urlsplit(location)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://tex.example.test/sso"
    assert parts.query == ""
    assert parts.fragment.startswith("token=")
    return decode_token(parts.fragment.removeprefix("token="))


# --- otwarcie ---------------------------------------------------------------------------------------


def test_open_redirects_to_webtex_with_one_time_token(web, competition, problem, participant):
    enable(competition)
    web.force_login(participant.user)

    response = web.get(open_url(problem))

    assert response.status_code == 302
    assert response["Cache-Control"] == "no-store"
    _header, claims = token_from(response)
    stage = problem.stage
    assert claims["sub"] == str(participant.user.pk)
    assert claims["project"] == f"{competition.slug}/s{stage.pk}/p3"
    assert claims["scope"] == f"{competition.slug}/s{stage.pk}"
    assert claims["share"] is False
    assert claims["ai"] is False
    assert claims["code"] == participant.public_code
    assert claims["name"] == "Ada Lovelace"
    assert claims["lang"] == "pl"
    assert claims["title"] == "Zadanie 3. Splątanie"
    assert claims["return_url"].startswith("http://testserver/me/?tab=zadania")
    assert claims["return_url"].endswith(f"#problem-{problem.pk}")
    # Każde kliknięcie – nowy token.
    _h, again = token_from(web.get(open_url(problem)))
    assert again["jti"] != claims["jti"]


def test_open_carries_sharing_of_the_stage(web, competition, coordinator, problem, participant):
    enable(competition)
    services.set_sharing(problem.stage, True, actor=coordinator)
    web.force_login(participant.user)

    _header, claims = token_from(web.get(open_url(problem)))

    assert claims["share"] is True
    assert claims["ai"] is False


def test_open_carries_ai_switch_of_the_stage(web, competition, coordinator, problem, participant):
    enable(competition)
    services.set_ai(problem.stage, True, actor=coordinator)
    web.force_login(participant.user)

    _header, claims = token_from(web.get(open_url(problem)))

    assert claims["ai"] is True
    assert claims["share"] is False


@pytest.mark.parametrize(("active", "lang"), [("pl", "pl"), ("en", "en"), ("ar", "en"), ("zh-hans", "en")])
def test_token_language_is_pl_or_en(active, lang):
    """Kontrakt zna dwa języki szablonu: polski i angielski (każdy inny interfejs dostaje angielski)."""
    with translation.override(active):
        assert services._lang() == lang


def test_open_is_404_without_flag_or_key(web, competition, problem, participant, settings):
    web.force_login(participant.user)
    assert web.get(open_url(problem)).status_code == 404
    enable(competition)
    settings.WEBTEX_SHARED_KEY = ""
    assert web.get(open_url(problem)).status_code == 404


def test_open_gates(web, competition, problem, participant):
    enable(competition)
    # Uczestnik bez wpisu na etap – 404.
    stranger = make_participant(competition)
    web.force_login(stranger.user)
    assert web.get(open_url(problem)).status_code == 404
    # Zadanie spoza etapu – 404.
    web.force_login(participant.user)
    assert web.get(f"/me/stages/{problem.stage_id}/problems/99/latex/").status_code == 404
    # Etap jeszcze nieotwarty – 404 (treść zadania nie jest jawna).
    stage = problem.stage
    stage.opens_at = timezone.now() + timezone.timedelta(days=1)
    stage.deadline_at = stage.opens_at + timezone.timedelta(days=1)
    stage.save()
    assert web.get(open_url(problem)).status_code == 404


def test_open_refuses_disqualified_entry(web, competition, problem, participant):
    enable(competition)
    StageEntry.objects.filter(participant=participant).update(status=StageEntryStatus.DISQUALIFIED)
    web.force_login(participant.user)

    response = web.get(open_url(problem))

    assert response.status_code == 403
    assert "Location" not in response


def test_open_refuses_non_participant_roles(web, competition, coordinator, problem):
    enable(competition)
    web.force_login(coordinator)
    assert web.get(open_url(problem)).status_code == 403


def test_staff_account_with_participant_profile_never_gets_a_token(web, competition, problem, participant):
    enable(competition)
    grant_membership(participant.user, competition, CompetitionRole.REVIEWER)
    web.force_login(participant.user)

    response = web.get(open_url(problem))

    assert response.status_code == 403
    assert "Location" not in response
    assert "rolę personelu" in response.content.decode()


def test_open_url_is_behind_the_proctoring_gate():
    from apps.proctoring.middleware import GATED_VIEWS

    assert GATED_VIEWS["web:problem-latex"] == "stage"
    assert GATED_VIEWS["web:problem-latex-submit"] == "stage"


# --- złożenie ---------------------------------------------------------------------------------------


def test_submit_builds_pdf_and_creates_new_versions(web, competition, problem, participant, webtex):
    enable(competition)
    web.force_login(participant.user)

    response = web.post(submit_url(problem), {"confirmed": "1"}, **HX)

    assert response.status_code == 200
    content = response.content.decode()
    assert f'id="problem-{problem.pk}"' in content
    assert "wersja 1" in content
    submission = Submission.objects.get(entry__participant=participant, problem=problem)
    assert submission.version == 1
    assert submission.latest_file.original_name == "rozwiazanie.pdf"
    assert submission.latest_file.mime == "application/pdf"

    # Żądanie do webTEX: adres wewnętrzny, ciało z kontraktu i podpis policzony niezależnie.
    call = webtex.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{INTERNAL_URL}/api/integration/build"
    assert json.loads(call["data"]) == {
        "sub": str(participant.user.pk),
        "project": f"{competition.slug}/s{problem.stage_id}/p3",
    }
    t, v1 = (part.split("=", 1)[1] for part in call["headers"]["X-Webtex-Signature"].split(","))
    expected = hmac.new(
        independent_key("webtex-api-v1"),
        f"{t}.POST./api/integration/build.".encode() + call["data"],
        hashlib.sha256,
    ).hexdigest()
    assert v1 == expected
    assert abs(int(t) - int(timezone.now().timestamp())) < 10
    assert call["timeout"][1] == 120

    second = web.post(submit_url(problem), {"confirmed": "1"}, **HX)
    assert "wersja 2" in second.content.decode()
    assert Submission.objects.filter(entry__participant=participant, problem=problem).count() == 2


def test_submit_requires_confirmation(web, competition, problem, participant, webtex):
    enable(competition)
    web.force_login(participant.user)

    response = web.post(submit_url(problem), {}, **HX)

    assert response.status_code == 200
    assert "Zaznacz potwierdzenie" in response.content.decode()
    assert webtex.calls == []
    assert not Submission.objects.exists()


def test_compile_failure_lists_latex_errors(web, competition, problem, participant, webtex):
    enable(competition)
    webtex.responses.append(
        json_response(
            422,
            {
                "error": "compile_failed",
                "issues": [
                    {"file": "main.tex", "line": 12, "message": "Undefined control sequence \\ket"},
                    {"file": None, "line": None, "message": "Emergency stop."},
                ],
            },
        )
    )
    web.force_login(participant.user)

    content = web.post(submit_url(problem), {"confirmed": "1"}, **HX).content.decode()

    assert "Kompilacja w edytorze nie dała pliku PDF" in content
    assert "main.tex:12" in content
    assert "Undefined control sequence \\ket" in content
    assert "Emergency stop." in content
    assert not Submission.objects.exists()


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (json_response(404, {"error": "no_project"}), "nie ma jeszcze Twojego projektu"),
        (json_response(503, {"error": "compiler_unavailable"}), "Kompilator LaTeX jest chwilowo niedostępny"),
        (requests.ConnectionError("down"), "Edytor LaTeX nie odpowiada"),
        (json_response(500, {}), "Edytor LaTeX nie odpowiada"),
    ],
)
def test_webtex_failures_become_messages_on_the_card(
    web, competition, problem, participant, webtex, answer, message
):
    enable(competition)
    webtex.responses.append(answer)
    web.force_login(participant.user)

    response = web.post(submit_url(problem), {"confirmed": "1"}, **HX)

    assert response.status_code == 200
    assert message in response.content.decode()
    assert not Submission.objects.exists()


def test_pdf_from_editor_goes_through_upload_validation(web, competition, problem, participant, webtex):
    """„PDF” z edytora przechodzi tę samą walidację treści, co upload – zły plik odrzuca serwis."""
    enable(competition)
    webtex.responses.append(pdf_response(b"<html>nie PDF</html>"))
    web.force_login(participant.user)

    content = web.post(submit_url(problem), {"confirmed": "1"}, **HX).content.decode()

    assert "msg-error" in content
    assert not Submission.objects.exists()


def test_non_pdf_content_type_is_refused_without_reading(web, competition, problem, participant, webtex):
    enable(competition)
    webtex.responses.append(json_response(200, {"ok": True}))
    web.force_login(participant.user)

    content = web.post(submit_url(problem), {"confirmed": "1"}, **HX).content.decode()

    assert "Edytor LaTeX nie odpowiada" in content
    assert not Submission.objects.exists()


def test_submit_after_deadline_does_not_build(web, competition, problem, participant, webtex):
    enable(competition)
    stage = problem.stage
    stage.opens_at = timezone.now() - timezone.timedelta(days=3)
    stage.deadline_at = timezone.now() - timezone.timedelta(days=1)
    stage.grace_seconds = 0
    stage.save()
    web.force_login(participant.user)

    content = web.post(submit_url(problem), {"confirmed": "1"}, **HX).content.decode()

    assert "Termin oddania rozwiązań minął" in content
    assert webtex.calls == []
    assert not Submission.objects.exists()


def test_problem_without_pdf_is_not_submitted(web, competition, problem, participant, webtex):
    enable(competition)
    problem.allowed_formats = ["ipynb"]
    problem.save()
    web.force_login(participant.user)

    content = web.post(submit_url(problem), {"confirmed": "1"}, **HX).content.decode()

    assert "nie przyjmuje rozwiązań w PDF" in content
    assert webtex.calls == []
    assert not Submission.objects.exists()


def test_submit_is_404_without_flag(web, competition, problem, participant, webtex):
    web.force_login(participant.user)
    assert web.post(submit_url(problem), {"confirmed": "1"}, **HX).status_code == 404
    assert webtex.calls == []


def test_staff_account_cannot_submit_from_editor(web, competition, problem, participant, webtex):
    enable(competition)
    grant_membership(participant.user, competition, CompetitionRole.REVIEWER)
    web.force_login(participant.user)

    content = web.post(submit_url(problem), {"confirmed": "1"}, **HX).content.decode()

    assert "rolę personelu" in content
    assert webtex.calls == []
    assert not Submission.objects.exists()


# --- karta zadania -------------------------------------------------------------------------------


def test_card_shows_editor_link_in_new_tab_and_submit_form(web, competition, problem, participant):
    web.force_login(participant.user)
    assert open_url(problem) not in web.get("/me/").content.decode()

    enable(competition)
    html = web.get("/me/").content.decode()

    assert f'href="{open_url(problem)}" target="_blank" rel="noopener noreferrer"' in html
    assert f'hx-post="{submit_url(problem)}"' in html
    assert "Złóż PDF z edytora" in html


def test_card_has_no_submit_form_after_deadline_or_without_pdf(web, competition, problem, participant):
    enable(competition)
    problem.allowed_formats = ["ipynb"]
    problem.save()
    web.force_login(participant.user)

    html = web.get("/me/").content.decode()

    assert open_url(problem) in html
    assert submit_url(problem) not in html


def test_card_for_staff_account_explains_instead_of_linking(web, competition, problem, participant):
    enable(competition)
    grant_membership(participant.user, competition, CompetitionRole.REVIEWER)
    web.force_login(participant.user)

    html = web.get("/me/").content.decode()

    assert open_url(problem) not in html
    assert "edytor LaTeX jako uczestnik jest dla niego wyłączony" in html


@pytest.mark.parametrize("flag_on", [False, True])
def test_card_panel_costs_no_queries_when_off(
    competition, problem, participant, settings, django_assert_num_queries, flag_on
):
    """Bez flagi (albo z flagą, ale bez klucza integracji) tag nie pyta bazy i nic nie rysuje."""
    if flag_on:
        enable(competition)
        settings.WEBTEX_SHARED_KEY = ""
    request = RequestFactory().get("/me/")
    request.competition = competition
    request.user = participant.user
    template = Template("{% load webtex_tags %}{% latex_card_panel row stage %}")
    context = Context(
        {"request": request, "row": {"problem": problem}, "stage": problem.stage, "upload_open": True}
    )

    with django_assert_num_queries(0):
        html = template.render(context)

    assert html == ""
