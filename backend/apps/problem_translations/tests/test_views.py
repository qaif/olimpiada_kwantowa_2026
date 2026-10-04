"""Ekrany tłumaczeń (TR-01): bramki poufności po HTTP, edytor, przegląd, eksport, uczeń."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.tests.factories import CurrentEditionFactory, ProblemFactory, StageFactory
from apps.core.models import AuditLog
from apps.problem_translations import services
from apps.problem_translations.models import Translation, TranslationStatus

from .conftest import activated_student, approve_text, open_stage

pytestmark = pytest.mark.django_db


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


def editor_url(problem, language="de"):
    return reverse("web:delegation-translation", args=[problem.pk, language])


# --- Olimpiada Kwantowa: nic się nie zmienia ---------------------------------------------------------


def test_open_competition_has_no_translation_screens(client_for, competition):
    CurrentEditionFactory(competition=competition)
    coordinator = logged_in(client_for, competition, CoordinatorFactory())

    assert coordinator.get(reverse("web:coordinator-translations")).status_code == 404
    assert "Tłumaczenia zadań" not in coordinator.get("/coordinator/").content.decode()


def test_problem_card_tag_is_silent_outside_delegations(competition):
    stage = StageFactory(edition=CurrentEditionFactory(competition=competition))
    problem = ProblemFactory(stage=stage)
    request = RequestFactory().get("/")
    request.competition = competition
    request.user = CoordinatorFactory()

    rendered = Template("{% load problem_translations %}[{% student_translation_link problem %}]").render(
        Context({"request": request, "problem": problem})
    )
    assert rendered == "[]"


# --- poufność ----------------------------------------------------------------------------------------


def test_editor_inside_window_is_private_and_audited(client_for, iqo, window, problem, leader_de):
    client = logged_in(client_for, iqo, leader_de.user)
    response = client.get(editor_url(problem))

    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert "Quantum harmonic oscillator" in response.content.decode()
    assert AuditLog.objects.filter(action="translation.source_viewed", actor=leader_de.user).exists()


def test_dashboard_lists_problems_only_in_open_window(client_for, iqo, window, problem, leader_de):
    client = logged_in(client_for, iqo, leader_de.user)
    assert problem.title in client.get(reverse("web:delegation-translations")).content.decode()

    window.opens_at = timezone.now() + timedelta(hours=1)
    window.closes_at = timezone.now() + timedelta(hours=2)
    window.save()
    content = client.get(reverse("web:delegation-translations")).content.decode()
    assert problem.title not in content
    assert client.get(editor_url(problem)).status_code == 404
    assert client.get(reverse("web:delegation-translation-source-pdf", args=[problem.pk])).status_code == 404


def test_other_language_and_other_roles_are_refused(client_for, iqo, window, problem, leader_fr, leader_de):
    fr = logged_in(client_for, iqo, leader_fr.user)
    assert fr.get(editor_url(problem, "de")).status_code == 404

    student = activated_student(leader_de)
    pupil = logged_in(client_for, iqo, student.user)
    assert pupil.get(editor_url(problem)).status_code == 403
    assert pupil.get(reverse("web:delegation-translation-source-pdf", args=[problem.pk])).status_code == 403
    assert pupil.get(reverse("web:coordinator-translations")).status_code == 403
    # Przed otwarciem etapu uczeń nie dowiaduje się nawet, czy tłumaczenie istnieje.
    assert pupil.get(reverse("web:student-translation", args=[problem.pk])).status_code == 404

    anonymous = client_for(iqo)
    response = anonymous.get(editor_url(problem))
    assert response.status_code == 302 and "/login" in response["Location"]


def test_source_pdf_is_watermarked_for_the_leader(client_for, iqo, window, problem, leader_de):
    client = logged_in(client_for, iqo, leader_de.user)
    response = client.get(reverse("web:delegation-translation-source-pdf", args=[problem.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "no-store" in response["Cache-Control"]
    assert AuditLog.objects.filter(action="translation.source_downloaded").exists()


# --- edytor ------------------------------------------------------------------------------------------


def test_autosave_saves_draft_and_returns_preview(client_for, iqo, window, problem, leader_de):
    client = logged_in(client_for, iqo, leader_de.user)
    response = client.post(
        reverse("web:delegation-translation-autosave", args=[problem.pk, "de"]),
        {"title": "Oszillator", "body_md": "**Aufgabe** $x^2$"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "<strong>Aufgabe</strong>" in content and 'class="tr-math"' in content
    translation = Translation.objects.get(problem=problem, language="de")
    assert translation.body_md == "**Aufgabe** $x^2$" and translation.status == TranslationStatus.DRAFT


def test_submit_from_editor_sends_what_is_in_the_field(client_for, iqo, window, problem, leader_de):
    client = logged_in(client_for, iqo, leader_de.user)
    response = client.post(
        reverse("web:delegation-translation-submit", args=[problem.pk, "de"]),
        {"title": "T", "body_md": "Endfassung"},
    )

    assert response.status_code == 302
    translation = Translation.objects.get(problem=problem, language="de")
    assert translation.status == TranslationStatus.SUBMITTED
    assert translation.revisions.get().body_md == "Endfassung"


def test_languages_and_student_language_forms(client_for, iqo, window, leader_de):
    client = logged_in(client_for, iqo, leader_de.user)
    client.post(reverse("web:delegation-translations-languages"), {"primary": "de", "secondary": "fr"})
    assert services.languages_of(leader_de.delegation) == ["de", "fr"]

    student = activated_student(leader_de)
    client.post(
        reverse("web:delegation-translations-student-language", args=[student.pk]), {"language": "fr"}
    )
    assert services.student_language(student) == "fr"


# --- komisja ------------------------------------------------------------------------------------------


def test_coordinator_review_approve_and_return(client_for, iqo, window, problem, leader_de, coordinator):
    services.save_draft(leader_de, problem, "de", title="", body_md="Text", actor=leader_de.user)
    services.submit(leader_de, problem, "de", actor=leader_de.user)
    translation = Translation.objects.get()
    client = logged_in(client_for, iqo, coordinator)

    page = client.get(reverse("web:coordinator-translation", args=[translation.pk]))
    assert page.status_code == 200 and "Zatwierdź wersję 1" in page.content.decode()
    assert AuditLog.objects.filter(action="translation.reviewed").exists()

    refused = client.post(
        reverse("web:coordinator-translation-return", args=[translation.pk]), {"comment": ""}
    )
    assert refused.status_code == 400

    client.post(reverse("web:coordinator-translation-approve", args=[translation.pk]))
    translation.refresh_from_db()
    assert translation.status == TranslationStatus.APPROVED

    for name in ("web:coordinator-translations", "web:coordinator-translations-stage"):
        args = [problem.stage.pk] if name.endswith("stage") else []
        assert client.get(reverse(name, args=args)).status_code == 200


def test_coordinator_window_form_validates_closing_before_stage(client_for, iqo, stage, coordinator):
    client = logged_in(client_for, iqo, coordinator)
    late = timezone.localtime(stage.opens_at + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    response = client.post(
        reverse("web:coordinator-translations-stage", args=[stage.pk]),
        {"opens_at": "2020-01-01T10:00", "closes_at": late, "sharing_mode": "SEPARATE"},
    )
    assert response.status_code == 400


def test_export_print_and_pdf(client_for, iqo, window, stage, problem, leader_de, coordinator):
    approve_text(leader_de, problem, coordinator)
    client = logged_in(client_for, iqo, coordinator)
    query = f"?delegation={leader_de.delegation.pk}"

    page = client.get(reverse("web:coordinator-translations-print", args=[stage.pk, "de"]) + query)
    assert page.status_code == 200 and "Aufgabe" in page.content.decode()
    pdf = client.get(reverse("web:coordinator-translations-pdf", args=[stage.pk, "de"]) + query)
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert (
        client.get(
            reverse("web:coordinator-translations-pdf", args=[stage.pk, "de"]) + "?delegation=999999"
        ).status_code
        == 404
    )


# --- uczeń -------------------------------------------------------------------------------------------


def test_student_reads_approved_translation_after_opening(
    client_for, iqo, window, stage, problem, leader_de, coordinator
):
    student = activated_student(leader_de)
    approve_text(leader_de, problem, coordinator)
    open_stage(stage)
    client = logged_in(client_for, iqo, student.user)

    response = client.get(reverse("web:student-translation", args=[problem.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Oszillator" in content and 'data-display="0">x^2' in content
    assert reverse("competitions:problem-statement", args=[problem.pk]) in content
    assert AuditLog.objects.filter(action="translation.student_viewed", actor=student.user).exists()

    request = RequestFactory().get("/")
    request.competition = iqo
    request.user = student.user
    link = Template("{% load problem_translations %}{% student_translation_link problem %}").render(
        Context({"request": request, "problem": problem})
    )
    assert reverse("web:student-translation", args=[problem.pk]) in link and "Deutsch" in link


def test_student_of_other_delegation_gets_nothing(
    client_for, iqo, window, stage, problem, leader_de, leader_fr, coordinator
):
    approve_text(leader_de, problem, coordinator)
    open_stage(stage)
    foreign = activated_student(leader_fr, email="fr-kid@example.test")
    client = logged_in(client_for, iqo, foreign.user)

    assert client.get(reverse("web:student-translation", args=[problem.pk])).status_code == 404
