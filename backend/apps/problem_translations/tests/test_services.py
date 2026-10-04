"""Serwis tłumaczeń zadań (TR-01): okno, przepływ, tryby, wersja oficjalna, język ucznia, eksport."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.utils import timezone

from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.problem_translations import services
from apps.problem_translations.markup import plain_blocks, render
from apps.problem_translations.models import (
    RevisionDecision,
    SharingMode,
    StudentLanguage,
    Translation,
    TranslationKind,
    TranslationStatus,
)

from .conftest import activated_student, approve_text, open_stage, pdf_bytes, pdf_upload

pytestmark = pytest.mark.django_db


# --- okno tłumaczeń ---------------------------------------------------------------------------------


def test_window_must_close_before_the_stage_opens(stage, coordinator):
    now = timezone.now()
    with pytest.raises(DomainError) as error:
        services.set_window(
            stage,
            opens_at=now,
            closes_at=stage.opens_at + timedelta(minutes=1),
            sharing_mode=SharingMode.SEPARATE,
            actor=coordinator,
        )
    assert error.value.machine_code == "TRANSLATION_WINDOW_INVALID"


def test_window_closes_when_the_stage_is_moved_earlier(window, stage):
    assert window.is_open()
    stage.opens_at = timezone.now() - timedelta(seconds=1)
    stage.save(update_fields=["opens_at"])
    window.refresh_from_db()

    assert not window.is_open()
    assert window.effective_closes_at == stage.opens_at


def test_sharing_mode_is_locked_once_translations_exist(window, stage, problem, leader_de, coordinator):
    services.save_draft(leader_de, problem, "de", title="", body_md="Text", actor=leader_de.user)
    with pytest.raises(DomainError) as error:
        services.set_window(
            stage,
            opens_at=window.opens_at,
            closes_at=window.closes_at,
            sharing_mode=SharingMode.SHARED,
            actor=coordinator,
        )
    assert error.value.machine_code == "TRANSLATION_MODE_LOCKED"


# --- poufność: kto i kiedy widzi źródło ---------------------------------------------------------------


def test_leader_sees_problem_only_inside_the_window(window, problem, leader_de):
    assert services.problem_for_leader(leader_de, problem.pk) == problem

    window.opens_at = timezone.now() + timedelta(hours=1)
    window.closes_at = timezone.now() + timedelta(hours=2)
    window.save()
    with pytest.raises(Http404):
        services.problem_for_leader(leader_de, problem.pk)


def test_no_window_means_no_access(problem, leader_de):
    with pytest.raises(Http404):
        services.problem_for_leader(leader_de, problem.pk)


def test_leader_without_declared_language_sees_nothing(window, problem, iqo, coordinator):
    from apps.accounts.tests.test_delegations import leader_for_country

    leader = leader_for_country(iqo, coordinator, "pl-lead@example.test", "pl")
    with pytest.raises(Http404):
        services.problem_for_leader(leader, problem.pk)


def test_language_outside_the_delegation_is_404(window, problem, leader_de):
    with pytest.raises(Http404):
        services.save_draft(leader_de, problem, "fr", title="", body_md="x", actor=leader_de.user)


def test_removed_leader_loses_access(window, problem, leader_de, coordinator, iqo):
    from apps.accounts import delegation_services

    delegation_services.remove_leader(leader_de, actor=coordinator)

    assert services.leader_access(leader_de.user, iqo) is None


# --- przepływ: szkic → wysłanie → zwrot → poprawka → zatwierdzenie → blokada ---------------------------


def test_full_review_flow(
    window, problem, leader_de, coordinator, django_capture_on_commit_callbacks, mailoutbox
):
    draft = services.save_draft(
        leader_de, problem, "de", title="Oszillator", body_md="Erste Fassung", actor=leader_de.user
    )
    assert draft.status == TranslationStatus.DRAFT
    assert draft.delegation == leader_de.delegation

    first = services.submit(leader_de, problem, "de", actor=leader_de.user)
    assert first.number == 1
    with pytest.raises(DomainError):  # wysłane jest zablokowane do decyzji
        services.save_draft(leader_de, problem, "de", title="", body_md="zmiana", actor=leader_de.user)

    with django_capture_on_commit_callbacks(execute=True):
        services.return_translation(draft, "Bitte Formel prüfen", actor=coordinator)
    draft.refresh_from_db()
    first.refresh_from_db()
    assert draft.status == TranslationStatus.RETURNED
    assert first.decision == RevisionDecision.RETURNED
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["de-lead@example.test"]
    assert "Bitte Formel" not in mailoutbox[0].body  # komentarz zostaje w panelu

    services.save_draft(
        leader_de, problem, "de", title="Oszillator", body_md="Zweite Fassung", actor=leader_de.user
    )
    second = services.submit(leader_de, problem, "de", actor=leader_de.user)
    previous, diff = services.revision_diff(second)
    assert previous.number == 1
    assert ("delete", "Erste Fassung") in [(line.tag, line.text) for line in diff]
    assert ("insert", "Zweite Fassung") in [(line.tag, line.text) for line in diff]

    services.approve(draft, actor=coordinator)
    draft.refresh_from_db()
    assert draft.status == TranslationStatus.APPROVED
    assert draft.approved_revision == second
    with pytest.raises(DomainError) as error:
        services.save_draft(leader_de, problem, "de", title="", body_md="nach Freigabe", actor=leader_de.user)
    assert error.value.machine_code == "TRANSLATION_LOCKED"
    actions = set(AuditLog.objects.filter(action__startswith="translation.").values_list("action", flat=True))
    assert {"translation.submitted", "translation.returned", "translation.approved"} <= actions


def test_empty_translation_cannot_be_submitted(window, problem, leader_de):
    services.save_draft(leader_de, problem, "de", title="", body_md="   ", actor=leader_de.user)
    with pytest.raises(DomainError) as error:
        services.submit(leader_de, problem, "de", actor=leader_de.user)
    assert error.value.machine_code == "TRANSLATION_EMPTY"


def test_withdraw_returns_to_draft_and_keeps_history(window, problem, leader_de):
    services.save_draft(leader_de, problem, "de", title="", body_md="Text", actor=leader_de.user)
    revision = services.submit(leader_de, problem, "de", actor=leader_de.user)
    translation = services.withdraw(leader_de, problem, "de", actor=leader_de.user)
    revision.refresh_from_db()

    assert translation.status == TranslationStatus.DRAFT
    assert revision.decision == RevisionDecision.WITHDRAWN


def test_return_requires_a_comment(window, problem, leader_de, coordinator):
    services.save_draft(leader_de, problem, "de", title="", body_md="Text", actor=leader_de.user)
    services.submit(leader_de, problem, "de", actor=leader_de.user)
    with pytest.raises(DomainError):
        services.return_translation(
            services.find_translation(leader_de, problem, "de"), "  ", actor=coordinator
        )


# --- tryb osobny i wspólny --------------------------------------------------------------------------


def test_separate_mode_gives_each_delegation_its_own_translation(window, problem, leader_de, leader_at):
    services.save_draft(leader_de, problem, "de", title="", body_md="DE", actor=leader_de.user)
    services.save_draft(leader_at, problem, "de", title="", body_md="AT", actor=leader_at.user)

    assert Translation.objects.filter(problem=problem, language="de").count() == 2
    assert services.find_translation(leader_de, problem, "de").body_md == "DE"
    assert services.find_translation(leader_at, problem, "de").body_md == "AT"


def test_shared_mode_gives_one_translation_per_language(stage, problem, leader_de, leader_at, coordinator):
    now = timezone.now()
    services.set_window(
        stage,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(days=1),
        sharing_mode=SharingMode.SHARED,
        actor=coordinator,
    )
    services.save_draft(leader_de, problem, "de", title="", body_md="gemeinsam", actor=leader_de.user)
    shared = services.find_translation(leader_at, problem, "de")

    assert shared is not None and shared.delegation is None and shared.body_md == "gemeinsam"
    assert Translation.objects.filter(problem=problem).count() == 1


# --- wersja oficjalna -----------------------------------------------------------------------------


def test_new_official_pdf_marks_translations_outdated_and_notifies(
    window, problem, leader_de, coordinator, django_capture_on_commit_callbacks, mailoutbox
):
    translation = approve_text(leader_de, problem, coordinator)
    assert not translation.is_outdated

    with django_capture_on_commit_callbacks(execute=True):
        problem.statement_pdf = SimpleUploadedFile(
            "v2.pdf", pdf_bytes("Official v2"), content_type="application/pdf"
        )
        problem.save()
    translation.refresh_from_db()

    assert translation.is_outdated
    assert services.ensure_source(problem).version == 2
    assert any("2" in mail.body and mail.to == ["de-lead@example.test"] for mail in mailoutbox)
    # Zatwierdzone, ale nieaktualne: opiekun otwiera je ponownie, uczniowie dalej mają zatwierdzone.
    reopened = services.reopen(leader_de, problem, "de", actor=leader_de.user)
    assert reopened.status == TranslationStatus.DRAFT
    assert reopened.approved_revision is not None


def test_outdated_submission_cannot_be_approved(window, problem, leader_de, coordinator):
    services.save_draft(leader_de, problem, "de", title="", body_md="Text", actor=leader_de.user)
    services.submit(leader_de, problem, "de", actor=leader_de.user)
    services.update_source_text(problem, "New official text $E$", actor=coordinator)

    with pytest.raises(DomainError) as error:
        services.approve(services.find_translation(leader_de, problem, "de"), actor=coordinator)
    assert error.value.machine_code == "TRANSLATION_OUTDATED"


def test_source_diff_shows_what_changed(window, problem, leader_de, coordinator):
    services.update_source_text(problem, "Line one", actor=coordinator)
    services.save_draft(leader_de, problem, "de", title="", body_md="Zeile", actor=leader_de.user)
    translation = services.find_translation(leader_de, problem, "de")
    services.update_source_text(problem, "Line one\nLine two", actor=coordinator)
    translation.refresh_from_db()

    diff = services.source_diff(problem, translation.source_version)
    assert ("insert", "Line two") in [(line.tag, line.text) for line in diff]


def test_unchanged_problem_save_does_not_bump_version(window, problem, leader_de):
    services.save_draft(leader_de, problem, "de", title="", body_md="x", actor=leader_de.user)
    problem.max_file_mb = 10
    problem.save()

    assert services.ensure_source(problem).version == 1


# --- PDF -------------------------------------------------------------------------------------------


def test_pdf_upload_is_validated_scanned_and_watermarked(window, problem, leader_de, monkeypatch):
    from pypdf import PdfReader

    translation = services.upload_pdf(leader_de, problem, "de", pdf_upload(), actor=leader_de.user)
    assert translation.kind == TranslationKind.PDF and len(translation.pdf_sha256) == 64

    data = services.translation_pdf_for_leader(leader_de, translation)
    text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
    assert "DE · CONFIDENTIAL" in text

    official = services.source_pdf_for_leader(leader_de, problem)
    assert "CONFIDENTIAL" in PdfReader(io.BytesIO(official)).pages[0].extract_text()
    assert AuditLog.objects.filter(action="translation.source_downloaded").exists()

    with pytest.raises(DomainError):
        services.upload_pdf(
            leader_de,
            problem,
            "de",
            SimpleUploadedFile("x.pdf", b"not a pdf", content_type="application/pdf"),
            actor=leader_de.user,
        )
    monkeypatch.setattr(
        "apps.submissions.antivirus.scan_stream", lambda fileobj, **kwargs: ("INFECTED", "Eicar")
    )
    with pytest.raises(DomainError) as error:
        services.upload_pdf(leader_de, problem, "de", pdf_upload(), actor=leader_de.user)
    assert error.value.machine_code == "TRANSLATION_FILE_INFECTED"


def test_pdf_upload_fails_closed_without_antivirus(window, problem, leader_de, monkeypatch):
    from apps.submissions.antivirus import ClamAVUnavailable

    def down(fileobj, **kwargs):
        raise ClamAVUnavailable("down")

    monkeypatch.setattr("apps.submissions.antivirus.scan_stream", down)
    with pytest.raises(DomainError) as error:
        services.upload_pdf(leader_de, problem, "de", pdf_upload(), actor=leader_de.user)
    assert error.value.machine_code == "TRANSLATION_SCAN_UNAVAILABLE"
    assert not Translation.objects.exists()


# --- uczeń ------------------------------------------------------------------------------------------


def test_student_gets_approved_translation_only_after_the_stage_opens(
    window, stage, problem, leader_de, coordinator
):
    student = activated_student(leader_de)
    approve_text(leader_de, problem, coordinator)

    assert services.approved_for_student(student, problem) is None
    open_stage(stage)
    problem.refresh_from_db()
    revision = services.approved_for_student(student, problem)
    assert revision is not None and revision.body_md == "Aufgabe: $x^2$"


def test_student_language_default_and_override(window, stage, problem, leader_de, coordinator):
    services.declare_languages(leader_de, ["de", "fr"], actor=leader_de.user)
    student = activated_student(leader_de)
    assert services.student_language(student) == "de"

    services.set_student_language(leader_de, student.pk, "fr", actor=leader_de.user)
    assert services.student_language(student) == "fr"

    approve_text(leader_de, problem, coordinator, language="fr", body="Énoncé")
    open_stage(stage)
    problem.refresh_from_db()
    assert services.approved_for_student(student, problem).body_md == "Énoncé"

    services.set_student_language(leader_de, student.pk, "", actor=leader_de.user)
    assert not StudentLanguage.objects.exists()


def test_leader_cannot_set_language_of_another_countrys_student(window, leader_de, leader_fr):
    foreign = activated_student(leader_fr, email="fr-kid@example.test")
    with pytest.raises(Http404):
        services.set_student_language(leader_de, foreign.pk, "de", actor=leader_de.user)


def test_language_with_submitted_translation_cannot_be_removed(window, problem, leader_de):
    services.save_draft(leader_de, problem, "de", title="", body_md="x", actor=leader_de.user)
    services.submit(leader_de, problem, "de", actor=leader_de.user)
    with pytest.raises(DomainError) as error:
        services.declare_languages(leader_de, ["fr"], actor=leader_de.user)
    assert error.value.machine_code == "TRANSLATION_LANGUAGE_IN_USE"


def test_at_most_two_languages(window, leader_de):
    with pytest.raises(DomainError):
        services.declare_languages(leader_de, ["de", "fr", "it"], actor=leader_de.user)


# --- eksport ---------------------------------------------------------------------------------------


def test_export_pdf_bundles_text_and_pdf_translations(window, stage, problem, leader_de, coordinator):
    from pypdf import PdfReader

    from apps.competitions.tests.factories import ProblemFactory

    second = ProblemFactory(stage=stage, number=2, title="Second")
    approve_text(leader_de, problem, coordinator, body="# Teil A\nText $x$\n\n$$E=mc^2$$")
    services.upload_pdf(leader_de, second, "de", pdf_upload(text="Aufgabe 2 PDF"), actor=leader_de.user)
    services.submit(leader_de, second, "de", actor=leader_de.user)
    services.approve(services.find_translation(leader_de, second, "de"), actor=coordinator)

    variants = services.export_variants(stage)
    assert [(v.language, v.delegation, v.approved) for v in variants] == [("de", leader_de.delegation, 2)]
    data = services.export_pdf(stage, "de", leader_de.delegation, actor=coordinator)
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
    assert "Oszillator" in text and "E=mc^2" in text and "Aufgabe 2 PDF" in text
    assert AuditLog.objects.filter(action="translation.exported").exists()


def test_export_without_approved_translations_is_refused(window, stage, problem, coordinator):
    with pytest.raises(DomainError):
        services.export_pdf(stage, "de", None, actor=coordinator)


# --- Markdown ----------------------------------------------------------------------------------------


def test_markdown_escapes_html_and_keeps_math_as_text():
    html = str(
        render("<script>alert(1)</script> **b** $a<b$\n\n$$\\frac{1}{2}$$\n\n[x](javascript:alert(1))")
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>b</strong>" in html
    assert '<span class="tr-math" data-display="0">a&lt;b</span>' in html
    assert 'data-display="1">\\frac{1}{2}</span>' in html
    assert "href" not in html


def test_markdown_lists_tables_and_headings():
    html = str(render("# Part A\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |"))

    assert "<h2>Part A</h2>" in html
    assert "<ul><li>one</li><li>two</li></ul>" in html
    assert "<td>1</td><td>2</td>" in html


def test_plain_blocks_for_pdf():
    blocks = plain_blocks("# H\ntext $x$\n\n$$y$$")
    assert blocks == [("heading", "H"), ("paragraph", "text $x$"), ("math", "y")]
