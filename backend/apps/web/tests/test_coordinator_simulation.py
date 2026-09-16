"""Ekran ``/coordinator/stages/<id>/simulation/`` – symulacja progu kwalifikacji.

Reguły progu mają własne testy w ``apps/results/tests/test_simulation.py``. Tutaj sprawdzamy sam
ekran, a w nim dwie rzeczy: że wejście na stronę (GET) niczego nie zapisuje i że zapis reguły
dzieje się **wyłącznie** po naciśnięciu osobnego przycisku.
"""

import pytest

from apps.competitions.models import QualificationMode, QualificationRule, StageEntryStatus
from apps.core.models import AuditLog
from apps.grading.tests.factories import FinalGradeFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db


def _scored(entry, problem, score: int):
    """Praca z oceną końcową – najkrótsza droga do sumy punktów w tabeli wyników."""
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.FINAL)
    FinalGradeFactory(submission=submission, score=score)
    return submission


def test_page_renders_the_table_with_a_qualification_badge(
    web_client, coordinator, elim_stage, entry, problems
):
    _scored(entry, problems[0], 6)
    web_client.force_login(coordinator)

    response = web_client.get(
        f"/coordinator/stages/{elim_stage.pk}/simulation/",
        {"mode": QualificationMode.MIN_POINTS, "min_points": 5},
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert response.context["result"]["qualified"] == 1
    assert entry.participant.public_code in content
    assert "kwalifikuje się" in content


def test_get_request_changes_nothing(web_client, coordinator, elim_stage, entry, problems):
    _scored(entry, problems[0], 6)
    web_client.force_login(coordinator)

    web_client.get(
        f"/coordinator/stages/{elim_stage.pk}/simulation/",
        {"mode": QualificationMode.TOP_N, "top_n": 1},
    )

    entry.refresh_from_db()
    assert entry.status == StageEntryStatus.REGISTERED
    assert QualificationRule.objects.get(stage=elim_stage).mode == QualificationMode.MIN_POINTS
    assert not AuditLog.objects.exists()


def test_page_without_parameters_shows_the_saved_threshold(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get(f"/coordinator/stages/{elim_stage.pk}/simulation/")

    assert response.status_code == 200
    assert response.context["result"] is None
    assert response.context["form"].initial["mode"] == QualificationMode.MIN_POINTS


def test_incomplete_parameters_are_reported_not_raised(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get(
        f"/coordinator/stages/{elim_stage.pk}/simulation/", {"mode": QualificationMode.TOP_N}
    )

    assert response.status_code == 200
    assert response.context["result"] is None


def test_warning_is_shown_while_grading_is_unfinished(web_client, coordinator, elim_stage, entry, problems):
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)
    web_client.force_login(coordinator)

    response = web_client.get(
        f"/coordinator/stages/{elim_stage.pk}/simulation/",
        {"mode": QualificationMode.MIN_POINTS, "min_points": 0},
    )

    assert "Ocenianie nie jest skończone" in response.content.decode()


def test_apply_button_saves_the_threshold(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/simulation/apply/",
        {"mode": QualificationMode.MIN_POINTS, "min_points": 12, "top_n": ""},
    )

    assert response.status_code == 302
    assert QualificationRule.objects.get(stage=elim_stage).min_points == 12
    assert AuditLog.objects.filter(action="stage.rule_updated").exists()


def test_apply_button_rejects_a_mode_without_its_numbers(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/simulation/apply/",
        {"mode": QualificationMode.TOP_N, "min_points": "", "top_n": ""},
        follow=True,
    )

    # Komunikat pochodzi z walidacji modelu progu – tej samej, którą przechodzi podgląd.
    assert "wymaga podania top_n" in response.content.decode()
    assert QualificationRule.objects.get(stage=elim_stage).mode == QualificationMode.MIN_POINTS


def test_page_is_forbidden_for_a_reviewer(web_client, reviewer, elim_stage):
    web_client.force_login(reviewer.user)

    assert web_client.get(f"/coordinator/stages/{elim_stage.pk}/simulation/").status_code == 403


def test_dashboard_links_to_the_simulation_screen(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get("/coordinator/")

    assert f"/coordinator/stages/{elim_stage.pk}/simulation/" in response.content.decode()
