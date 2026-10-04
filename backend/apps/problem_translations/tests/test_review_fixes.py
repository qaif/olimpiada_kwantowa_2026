"""Poprawki po przeglądzie TR-01: wyścigi decyzji i zapisu, wersja oficjalna EN, autozapis, banery,
odwrót języka, odwołani opiekunowie, izolacja konkursów, limit pobrań."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts import delegation_services
from apps.accounts.tests.test_delegations import make_delegations_competition
from apps.competitions.services import current_edition
from apps.competitions.tests.factories import ProblemFactory, StageFactory
from apps.core.api import DomainError
from apps.problem_translations import services
from apps.problem_translations.models import SharingMode, Translation, TranslationKind, TranslationStatus

from .conftest import activated_student, approve_text, open_stage, pdf_bytes, pdf_upload

pytestmark = pytest.mark.django_db


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


def submitted(leader, problem, body="Text", language="de"):
    services.save_draft(leader, problem, language, title="T", body_md=body, actor=leader.user)
    return services.submit(leader, problem, language, actor=leader.user)


def shared_window(stage, coordinator):
    now = timezone.now()
    return services.set_window(
        stage,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(days=1),
        sharing_mode=SharingMode.SHARED,
        actor=coordinator,
    )


# --- H1: decyzja komisji dotyczy wersji, którą komisja widziała ---------------------------------------


def test_approve_of_stale_revision_after_resubmit_is_refused(window, problem, leader_de, coordinator):
    submitted(leader_de, problem, "Erste")
    translation = services.find_translation(leader_de, problem, "de")
    # Komisja ma na ekranie wersję 1, opiekun w tym czasie cofa i wysyła wersję 2.
    services.withdraw(leader_de, problem, "de", actor=leader_de.user)
    submitted(leader_de, problem, "Zweite")

    with pytest.raises(DomainError) as error:
        services.approve(translation, actor=coordinator, expected_revision=1)
    assert error.value.machine_code == "TRANSLATION_REVISION_CHANGED"
    assert error.value.status_code == 409
    translation.refresh_from_db()
    assert translation.status == TranslationStatus.SUBMITTED and translation.approved_revision is None

    with pytest.raises(DomainError):
        services.return_translation(translation, "x", actor=coordinator, expected_revision=1)
    assert services.approve(translation, actor=coordinator, expected_revision=2).number == 2


def test_approve_view_answers_409_with_message(client_for, iqo, window, problem, leader_de, coordinator):
    submitted(leader_de, problem, "Erste")
    translation = Translation.objects.get()
    services.withdraw(leader_de, problem, "de", actor=leader_de.user)
    submitted(leader_de, problem, "Zweite")
    client = logged_in(client_for, iqo, coordinator)

    response = client.post(
        reverse("web:coordinator-translation-approve", args=[translation.pk]), {"revision": "1"}
    )
    assert response.status_code == 409
    assert "nową wersję" in response.content.decode()
    assert "Zweite" in response.content.decode()  # ekran pokazuje już bieżącą wersję
    missing = client.post(
        reverse("web:coordinator-translation-return", args=[translation.pk]), {"comment": "x"}
    )
    assert missing.status_code == 409


# --- M1: wysłanie z edytora wyrenderowanego przy starej wersji oficjalnej ------------------------------


def test_submit_with_old_source_version_is_refused_with_diff(
    client_for, iqo, window, problem, leader_de, coordinator
):
    services.update_source_text(problem, "Line one", actor=coordinator)  # v2
    client = logged_in(client_for, iqo, leader_de.user)
    services.update_source_text(problem, "Line one\nLine two", actor=coordinator)  # v3 w trakcie pisania

    response = client.post(
        reverse("web:delegation-translation-submit", args=[problem.pk, "de"]),
        {"title": "T", "body_md": "Zeile eins", "edit_version": "0", "source_version": "2"},
    )
    assert response.status_code == 409
    content = response.content.decode()
    assert "Line two" in content and "Zeile eins" in content  # różnice źródła i tekst opiekuna
    translation = Translation.objects.get()
    assert translation.status == TranslationStatus.DRAFT and not translation.revisions.exists()


# --- M2: wersja oficjalna angielska ------------------------------------------------------------------


def english_official(iqo):
    iqo.default_language = "en"
    iqo.save(update_fields=["default_language"])


def test_leader_gets_the_english_official_pdf(client_for, iqo, window, problem, leader_de):
    from pypdf import PdfReader

    english_official(iqo)
    problem.title_en = "Oscillator (official EN)"
    problem.statement_pdf_en = SimpleUploadedFile(
        "en.pdf", pdf_bytes("English official"), content_type="application/pdf"
    )
    problem.save()
    client = logged_in(client_for, iqo, leader_de.user)

    response = client.get(reverse("web:delegation-translation-source-pdf", args=[problem.pk]))
    assert response.status_code == 200
    assert "official-en" in response["Content-Disposition"]
    text = PdfReader(io.BytesIO(response.content)).pages[0].extract_text()
    assert "English official" in text
    assert (
        "Oscillator (official EN)"
        in client.get(reverse("web:delegation-translation", args=[problem.pk, "de"])).content.decode()
    )


def test_errata_on_english_pdf_marks_outdated_and_emails(
    iqo, window, problem, leader_de, coordinator, django_capture_on_commit_callbacks, mailoutbox
):
    english_official(iqo)
    translation = approve_text(leader_de, problem, coordinator)
    with django_capture_on_commit_callbacks(execute=True):
        problem.statement_pdf_en = SimpleUploadedFile(
            "errata.pdf", pdf_bytes("Errata"), content_type="application/pdf"
        )
        problem.save()
    translation.refresh_from_db()

    assert translation.is_outdated
    source = services.ensure_source(problem)
    assert source.version == 2 and source.revisions.get(version=2).pdf_en_name.endswith(".pdf")
    assert [mail.to for mail in mailoutbox] == [["de-lead@example.test"]]


# --- M3: autozapis nie zawodzi po cichu ---------------------------------------------------------------


def test_autosave_after_window_closed_shows_error(client_for, iqo, window, problem, leader_de):
    services.save_draft(leader_de, problem, "de", title="", body_md="vorher", actor=leader_de.user)
    client = logged_in(client_for, iqo, leader_de.user)
    window.closes_at = timezone.now() - timedelta(seconds=1)
    window.opens_at = window.closes_at - timedelta(hours=1)
    window.save()

    response = client.post(
        reverse("web:delegation-translation-autosave", args=[problem.pk, "de"]),
        {"title": "", "body_md": "nachher", "edit_version": "1", "source_version": "1"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()
    assert Translation.objects.get().body_md == "vorher"


def test_autosave_conflict_is_visible_and_success_updates_token(client_for, iqo, window, problem, leader_de):
    client = logged_in(client_for, iqo, leader_de.user)
    url = reverse("web:delegation-translation-autosave", args=[problem.pk, "de"])
    ok = client.post(url, {"title": "", "body_md": "eins", "edit_version": "0", "source_version": "1"})
    assert 'id="tr-edit-version" value="1" hx-swap-oob="true"' in ok.content.decode()

    stale = client.post(url, {"title": "", "body_md": "zwei", "edit_version": "0", "source_version": "1"})
    assert stale.status_code == 200
    assert 'data-error-code="TRANSLATION_CONFLICT"' in stale.content.decode()
    assert Translation.objects.get().body_md == "eins"


# --- M4: optymistyczna współbieżność w obu trybach ------------------------------------------------------


@pytest.mark.parametrize("mode", [SharingMode.SEPARATE, SharingMode.SHARED])
def test_stale_token_cannot_overwrite(mode, stage, problem, leader_de, leader_at, coordinator):
    now = timezone.now()
    services.set_window(
        stage,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(days=1),
        sharing_mode=mode,
        actor=coordinator,
    )
    first = services.save_draft(
        leader_de, problem, "de", title="", body_md="A", actor=leader_de.user, expected_version=0
    )
    token = first.edit_version
    # Druga karta tego samego opiekuna (tryb osobny) albo drugi kraj (tryb wspólny) zapisuje pierwszy.
    other = leader_at if mode == SharingMode.SHARED else leader_de
    services.save_draft(other, problem, "de", title="", body_md="B", actor=other.user, expected_version=token)

    with pytest.raises(DomainError) as error:
        services.save_draft(
            leader_de, problem, "de", title="", body_md="C", actor=leader_de.user, expected_version=token
        )
    assert error.value.machine_code == "TRANSLATION_CONFLICT" and error.value.status_code == 409
    with pytest.raises(DomainError):
        services.submit(leader_de, problem, "de", actor=leader_de.user, expected_version=token)
    assert services.find_translation(leader_de, problem, "de").body_md == "B"


def test_editor_save_conflict_keeps_the_text(client_for, iqo, window, problem, leader_de):
    services.save_draft(leader_de, problem, "de", title="", body_md="fremd", actor=leader_de.user)
    client = logged_in(client_for, iqo, leader_de.user)

    response = client.post(
        reverse("web:delegation-translation", args=[problem.pk, "de"]),
        {"title": "", "body_md": "mein Text", "edit_version": "0", "source_version": "1"},
    )
    assert response.status_code == 409
    assert "mein Text" in response.content.decode()


# --- M5: baner „wersja oficjalna zmieniła się po zatwierdzeniu” -----------------------------------------


def test_stale_banner_for_student_print_and_export(
    client_for, iqo, window, stage, problem, leader_de, coordinator
):
    from pypdf import PdfReader

    student = activated_student(leader_de)
    approve_text(leader_de, problem, coordinator)
    services.update_source_text(problem, "Erratum", actor=coordinator)
    open_stage(stage)

    page = logged_in(client_for, iqo, student.user).get(reverse("web:student-translation", args=[problem.pk]))
    assert page.status_code == 200 and 'role="note"' in page.content.decode()
    staff = logged_in(client_for, iqo, coordinator)
    query = f"?delegation={leader_de.delegation.pk}"
    printed = staff.get(reverse("web:coordinator-translations-print", args=[stage.pk, "de"]) + query)
    assert services.STALE_NOTE in printed.content.decode()
    data = services.export_pdf(stage, "de", leader_de.delegation, actor=coordinator)
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
    assert "official version was updated" in text


# --- L1: odwołany opiekun nie dostaje listów --------------------------------------------------------------


def test_removed_leaders_get_no_mail(
    stage, problem, leader_de, leader_at, coordinator, django_capture_on_commit_callbacks, mailoutbox
):
    shared_window(stage, coordinator)
    delegation_services.remove_leader(leader_at, actor=coordinator)
    submitted(leader_de, problem)
    translation = services.find_translation(leader_de, problem, "de")
    assert translation.delegation is None

    with django_capture_on_commit_callbacks(execute=True):
        services.return_translation(translation, "Bitte prüfen", actor=coordinator)
    assert [mail.to for mail in mailoutbox] == [["de-lead@example.test"]]


def test_removed_leader_of_separate_delegation_gets_no_mail(
    window, problem, leader_de, coordinator, iqo, django_capture_on_commit_callbacks, mailoutbox
):
    from apps.accounts.tests.test_delegations import leader_for_country

    deputy = leader_for_country(iqo, coordinator, "de-deputy@example.test", "de")
    delegation_services.remove_leader(deputy, actor=coordinator)
    submitted(leader_de, problem)
    with django_capture_on_commit_callbacks(execute=True):
        services.return_translation(
            services.find_translation(leader_de, problem, "de"), "x", actor=coordinator
        )
    assert [mail.to for mail in mailoutbox] == [["de-lead@example.test"]]


# --- L2: autozapis nie zmienia PDF-u w tekst --------------------------------------------------------------


def test_autosave_does_not_flip_pdf_to_text(window, problem, leader_de):
    services.upload_pdf(leader_de, problem, "de", pdf_upload(), actor=leader_de.user)
    with pytest.raises(DomainError) as error:
        services.save_draft(leader_de, problem, "de", title="", body_md="x", actor=leader_de.user)
    assert error.value.machine_code == "TRANSLATION_IS_PDF"
    assert services.find_translation(leader_de, problem, "de").kind == TranslationKind.PDF

    services.use_text(leader_de, problem, "de", actor=leader_de.user)
    assert (
        services.save_draft(leader_de, problem, "de", title="", body_md="x", actor=leader_de.user).kind
        == "TEXT"
    )


# --- L5: odwrót na drugi język delegacji --------------------------------------------------------------------


def test_student_falls_back_to_second_language(
    client_for, iqo, window, stage, problem, leader_de, coordinator
):
    services.declare_languages(leader_de, ["de", "fr"], actor=leader_de.user)
    student = activated_student(leader_de)
    approve_text(leader_de, problem, coordinator, language="fr", body="Énoncé")
    open_stage(stage)
    problem.refresh_from_db()

    revision = services.approved_for_student(student, problem)
    assert revision.translation.language == "fr" and services.is_fallback(student, revision)
    page = logged_in(client_for, iqo, student.user).get(reverse("web:student-translation", args=[problem.pk]))
    assert "Deutsch" in page.content.decode() and "Énoncé" in page.content.decode()


# --- L4: izolacja konkursów --------------------------------------------------------------------


@pytest.fixture
def foreign(other_competition):
    make_delegations_competition(other_competition)
    stage = StageFactory(
        edition=current_edition(other_competition), opens_at=timezone.now() - timedelta(days=1)
    )
    problem = ProblemFactory(stage=stage, number=1, title="Foreign secret")
    translation = Translation.objects.create(problem=problem, language="de", body_md="fremd")
    return stage, problem, translation


def test_cross_competition_objects_are_404(client_for, iqo, window, problem, leader_de, coordinator, foreign):
    foreign_stage, foreign_problem, foreign_translation = foreign
    leader = logged_in(client_for, iqo, leader_de.user)
    assert (
        leader.get(reverse("web:delegation-translation", args=[foreign_problem.pk, "de"])).status_code == 404
    )
    assert (
        leader.get(reverse("web:delegation-translation-source-pdf", args=[foreign_problem.pk])).status_code
        == 404
    )

    staff = logged_in(client_for, iqo, coordinator)
    assert staff.get(reverse("web:coordinator-translation", args=[foreign_translation.pk])).status_code == 404
    assert (
        staff.get(reverse("web:coordinator-translations-stage", args=[foreign_stage.pk])).status_code == 404
    )
    assert (
        staff.get(reverse("web:coordinator-translations-source", args=[foreign_problem.pk])).status_code
        == 404
    )
    approve = staff.post(
        reverse("web:coordinator-translation-approve", args=[foreign_translation.pk]), {"revision": "1"}
    )
    assert approve.status_code == 404

    student = activated_student(leader_de)
    pupil = logged_in(client_for, iqo, student.user)
    assert pupil.get(reverse("web:student-translation", args=[foreign_problem.pk])).status_code == 404


# --- L7: limit pobrań PDF-ów -------------------------------------------------------------------


def test_source_pdf_download_is_throttled(client_for, iqo, window, problem, leader_de, settings):
    rates = {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "translation": "2/hour"}
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": rates}
    client = logged_in(client_for, iqo, leader_de.user)
    url = reverse("web:delegation-translation-source-pdf", args=[problem.pk])

    assert [client.get(url).status_code for _ in range(3)] == [200, 200, 429]
