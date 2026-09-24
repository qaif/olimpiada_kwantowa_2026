"""„Status ucznia” w panelach: uczestnik, koordynator, recenzent – bramki, ekrany, paczki ZIP, RODO.

Prośba organizatora z 24.09.2026. Ten plik pilnuje tego, czego serwis sam nie widzi:

- **flaga**: przy wyłączonej ``student_status_certificate`` żadnego adresu nie ma (404), pulpit
  uczestnika i przyciski paczek wyglądają co do znaku tak, jak przed wydaniem, a parametr
  ``students=verified`` jest odmową, a nie cichą paczką „wszystkich”,
- **role i zakres**: uczestnik widzi wyłącznie swoje, koordynator – wyłącznie swój konkurs,
  recenzent nie ma wstępu do skanów wcale,
- **paczki ZIP**: wybór „wszystkie / tylko potwierdzony status” u koordynatora (etap, zadanie,
  zaznaczone) i u recenzenta (panel i API), z anonimowymi nazwami plików,
- **RODO**: eksport danych niesie zaświadczenia i ich pliki, usunięcie konta zabiera je ze storage.
"""

from __future__ import annotations

import io
import json
import zipfile
from io import BytesIO

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.competitions.tests.factories import CurrentEditionFactory, StageEntryFactory
from apps.core.models import AuditLog
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.student_status.models import CertificateStatus, ScanStatus, StudentStatusCertificate
from apps.student_status.tests.helpers import (
    HTML_BYTES,
    PDF_BYTES,
    PNG_BYTES,
    enable,
    make_certificate,
    stored,
    upload,
)
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.packaging import README_NAME
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db

PAGE = "/me/status-ucznia/"
TEMPLATE_PDF = "/me/status-ucznia/wzor.pdf"
OWN_FILE = "/me/status-ucznia/plik/"
LIST = "/coordinator/student-status/"


def file_url(certificate) -> str:
    return f"{LIST}{certificate.pk}/file/"


def zip_names(response) -> list[str]:
    with zipfile.ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
        return sorted(archive.namelist())


def zip_readme(response) -> str:
    with zipfile.ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
        return archive.read(README_NAME).decode("utf-8")


def stored_submission(entry, problem):
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.LOCKED)
    submission_file = SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN)
    get_submission_storage().put(submission_file.object_key, BytesIO(PDF_BYTES), "application/pdf")
    return submission


@pytest.fixture
def flag_on(competition):
    return enable(competition)


# --- uczestnik: bramki ----------------------------------------------------------------------------


@pytest.mark.parametrize("url", [PAGE, TEMPLATE_PDF, OWN_FILE])
def test_without_the_flag_the_participant_pages_do_not_exist(web_client, participant, edition, url):
    web_client.force_login(participant.user)

    assert web_client.get(url).status_code == 404


def test_without_the_flag_the_dashboard_has_no_reminder(web_client, participant, edition):
    web_client.force_login(participant.user)

    body = web_client.get(reverse("web:me")).content.decode()

    assert "status-ucznia" not in body
    assert "Zaświadczenie o statusie ucznia" not in body


def test_without_the_flag_the_dashboard_never_touches_the_table(web_client, participant, edition):
    """Zero zapytań do tabeli zaświadczeń przy wyłączonej fladze – budżet ``/me/`` zostaje nietknięty."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    web_client.force_login(participant.user)

    with CaptureQueriesContext(connection) as queries:
        assert web_client.get(reverse("web:me")).status_code == 200

    assert not [query for query in queries if "student_status" in query["sql"]]


def test_an_anonymous_visitor_goes_to_login_and_a_reviewer_gets_403(web_client, flag_on, edition, reviewer):
    assert web_client.get(PAGE).status_code == 302
    web_client.force_login(reviewer.user)
    assert web_client.get(PAGE).status_code == 403


def test_without_a_current_edition_the_page_does_not_exist(web_client, flag_on, participant):
    web_client.force_login(participant.user)

    assert web_client.get(PAGE).status_code == 404


# --- uczestnik: ekran i wgranie ---------------------------------------------------------------------


def test_the_dashboard_reminds_until_the_status_is_accepted(web_client, flag_on, participant, edition):
    web_client.force_login(participant.user)

    body = web_client.get(reverse("web:me")).content.decode()
    assert 'id="status-ucznia"' in body
    assert "Przejdź do zaświadczenia" in body
    assert "nie blokuje oddawania rozwiązań" in body

    make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)
    body = web_client.get(reverse("web:me")).content.decode()
    assert "Status ucznia potwierdzony" in body
    assert "Przejdź do zaświadczenia" not in body


def test_the_page_shows_the_missing_state_and_the_upload_form(web_client, flag_on, participant, edition):
    web_client.force_login(participant.user)

    response = web_client.get(PAGE)

    body = response.content.decode()
    assert response.status_code == 200
    assert "brak" in body
    assert 'enctype="multipart/form-data"' in body
    assert TEMPLATE_PDF in body


def test_the_template_pdf_is_a_personal_attachment_that_is_not_cached(
    web_client, flag_on, participant, edition
):
    web_client.force_login(participant.user)

    response = web_client.get(TEMPLATE_PDF)

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "attachment" in response["Content-Disposition"]
    assert participant.public_code in response["Content-Disposition"]
    assert "no-store" in response["Cache-Control"]
    assert response.content.startswith(b"%PDF-")


def test_upload_creates_a_pending_certificate(web_client, flag_on, participant, edition):
    web_client.force_login(participant.user)

    response = web_client.post(PAGE, {"file": upload("skan.png", PNG_BYTES, "image/png"), "confirmed": "on"})

    assert response.status_code == 302
    certificate = StudentStatusCertificate.objects.get()
    assert certificate.participant == participant
    assert certificate.edition == edition
    assert certificate.mime == "image/png"
    assert certificate.status == CertificateStatus.PENDING
    body = web_client.get(PAGE).content.decode()
    assert "oczekuje na weryfikację" in body


def test_a_spoofed_file_is_refused_with_a_message(web_client, flag_on, participant, edition):
    web_client.force_login(participant.user)

    response = web_client.post(PAGE, {"file": upload("zaswiadczenie.pdf", HTML_BYTES), "confirmed": "on"})

    assert response.status_code == 400
    assert "Rozpoznajemy format po zawartości" in response.content.decode()
    assert not StudentStatusCertificate.objects.exists()


def test_the_confirmation_is_required(web_client, flag_on, participant, edition):
    web_client.force_login(participant.user)

    response = web_client.post(PAGE, {"file": upload("skan.pdf", PDF_BYTES)})

    assert response.status_code == 400
    assert not StudentStatusCertificate.objects.exists()


def test_accepted_page_has_no_upload_form(web_client, flag_on, participant, edition):
    make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)
    web_client.force_login(participant.user)

    body = web_client.get(PAGE).content.decode()

    assert "zaakceptowane" in body
    assert 'enctype="multipart/form-data"' not in body


def test_a_post_after_acceptance_is_refused_with_a_visible_message(web_client, flag_on, participant, edition):
    """POST z karty otwartej przed akceptacją: odmowa widoczna mimo braku formularza na stronie."""
    make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)
    web_client.force_login(participant.user)

    response = web_client.post(PAGE, {"file": upload("skan.pdf", PDF_BYTES), "confirmed": "on"})

    assert response.status_code == 400
    assert "zostało już zaakceptowane" in response.content.decode()
    assert StudentStatusCertificate.objects.count() == 1


def test_rejected_page_shows_the_reason(web_client, flag_on, participant, edition):
    certificate = make_certificate(participant, edition, status=CertificateStatus.REJECTED)
    certificate.rejection_reason = "Brak pieczątki szkoły"
    certificate.save()
    web_client.force_login(participant.user)

    body = web_client.get(PAGE).content.decode()

    assert "Brak pieczątki szkoły" in body
    assert 'enctype="multipart/form-data"' in body


def test_the_participant_downloads_only_their_own_current_file(web_client, flag_on, participant, edition):
    make_certificate(participant, edition, content=PDF_BYTES)
    other = ParticipantFactory()
    make_certificate(other, edition, content=PNG_BYTES, mime="image/png", ext="png")
    web_client.force_login(participant.user)

    response = web_client.get(OWN_FILE)

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF_BYTES


# --- koordynator: bramki i zakres --------------------------------------------------------------------


def test_without_the_flag_the_coordinator_screen_does_not_exist(
    web_client, coordinator, participant, edition
):
    certificate = make_certificate(participant, edition)
    web_client.force_login(coordinator)

    assert web_client.get(LIST).status_code == 404
    assert web_client.get(file_url(certificate)).status_code == 404
    assert web_client.post(f"{LIST}{certificate.pk}/accept/").status_code == 404


@pytest.mark.parametrize("who", ["participant", "reviewer"])
def test_participants_and_reviewers_never_see_the_scans(
    web_client, flag_on, participant, edition, reviewer, who
):
    """Recenzent jest ślepy na tożsamość – skan zaświadczenia to imię, data urodzenia i szkoła."""
    certificate = make_certificate(participant, edition)
    web_client.force_login(participant.user if who == "participant" else reviewer.user)

    assert web_client.get(LIST).status_code == 403
    assert web_client.get(file_url(certificate)).status_code == 403
    assert web_client.post(f"{LIST}{certificate.pk}/accept/").status_code == 403
    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.PENDING


def test_a_certificate_of_another_competition_is_404(web_client, flag_on, coordinator, other_competition):
    enable(other_competition)
    foreign_edition = CurrentEditionFactory(competition=other_competition)
    foreign = make_certificate(ParticipantFactory(competition=other_competition), foreign_edition)
    web_client.force_login(coordinator)

    assert web_client.get(file_url(foreign)).status_code == 404
    assert web_client.post(f"{LIST}{foreign.pk}/accept/").status_code == 404
    assert web_client.post(f"{LIST}{foreign.pk}/reject/", {"reason": "x"}).status_code == 404
    foreign.refresh_from_db()
    assert foreign.status == CertificateStatus.PENDING


def test_an_edition_of_another_competition_is_404(
    web_client, flag_on, coordinator, edition, other_competition
):
    foreign_edition = CurrentEditionFactory(competition=other_competition)
    web_client.force_login(coordinator)

    assert web_client.get(f"{LIST}?edition={foreign_edition.pk}").status_code == 404


def test_the_flag_is_per_competition(client_for, flag_on, other_competition):
    """Konkurs #1 ma flagę, sąsiad nie – u sąsiada ekranu nie ma."""
    from apps.accounts.models import CompetitionRole
    from apps.tenancy.tests.factories import grant_membership

    user = CoordinatorFactory()
    grant_membership(user, other_competition, CompetitionRole.COORDINATOR)
    client = client_for(other_competition)
    client.force_login(user)

    assert client.get(LIST).status_code == 404


# --- koordynator: lista i decyzje ---------------------------------------------------------------------


def test_the_list_filters_and_counts(web_client, flag_on, coordinator, participant, edition, elim_stage):
    make_certificate(participant, edition, status=CertificateStatus.PENDING)
    without = StageEntryFactory(stage=elim_stage).participant
    web_client.force_login(coordinator)

    body = web_client.get(LIST).content.decode()
    assert participant.public_code in body and without.public_code in body
    assert "Akceptuj" in body and "Odrzuć" in body

    pending_only = web_client.get(f"{LIST}?status=oczekujace").content.decode()
    assert participant.public_code in pending_only
    assert without.public_code not in pending_only

    missing_only = web_client.get(f"{LIST}?status=brak").content.decode()
    assert without.public_code in missing_only
    assert participant.public_code not in missing_only


def test_the_preview_is_served_inline_with_security_headers_and_audited(
    web_client, flag_on, coordinator, participant, edition
):
    image = make_certificate(participant, edition, content=PNG_BYTES, mime="image/png", ext="png")
    web_client.force_login(coordinator)

    response = web_client.get(file_url(image))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    assert "attachment" not in response.get("Content-Disposition", "")
    assert b"".join(response.streaming_content) == PNG_BYTES
    assert AuditLog.objects.filter(action="student_status.viewed", actor=coordinator).exists()

    download = web_client.get(f"{file_url(image)}?download=1")
    assert "attachment" in download["Content-Disposition"]
    assert participant.public_code in download["Content-Disposition"]


def test_an_unscanned_file_has_no_preview_and_cannot_be_accepted(
    web_client, flag_on, coordinator, participant, edition
):
    certificate = make_certificate(participant, edition, scan=ScanStatus.PENDING)
    web_client.force_login(coordinator)

    assert web_client.get(file_url(certificate)).status_code == 404
    response = web_client.post(f"{LIST}{certificate.pk}/accept/", follow=True)
    assert "skanu antywirusowego" in response.content.decode()
    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.PENDING


def test_accept_from_the_list_informs_the_participant(
    web_client, flag_on, coordinator, participant, edition, django_capture_on_commit_callbacks
):
    certificate = make_certificate(participant, edition)
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(
            f"{LIST}{certificate.pk}/accept/", {"return_query": "status=oczekujace&next=https://evil.test/"}
        )

    assert response.status_code == 302
    # Powrót na listę z tym samym filtrem – i z niczym więcej z formularza.
    assert response["Location"].startswith(LIST)
    assert "status=oczekujace" in response["Location"]
    assert "evil" not in response["Location"]
    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.ACCEPTED
    assert mail.outbox[-1].to == [participant.user.email]


def test_reject_needs_a_reason(
    web_client, flag_on, coordinator, participant, edition, django_capture_on_commit_callbacks
):
    certificate = make_certificate(participant, edition)
    web_client.force_login(coordinator)

    web_client.post(f"{LIST}{certificate.pk}/reject/", {"reason": ""})
    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.PENDING

    with django_capture_on_commit_callbacks(execute=True):
        web_client.post(f"{LIST}{certificate.pk}/reject/", {"reason": "Nieczytelna pieczątka"})
    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.REJECTED
    assert certificate.rejection_reason == "Nieczytelna pieczątka"
    assert "Nieczytelna pieczątka" in mail.outbox[-1].body
    assert AuditLog.objects.filter(action="student_status.rejected").exists()


def test_the_participant_card_shows_the_status_only_with_the_flag(
    web_client, coordinator, participant, edition
):
    make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)
    web_client.force_login(coordinator)
    card = reverse("web:coordinator-participant", args=[participant.pk])

    assert 'id="status-ucznia"' not in web_client.get(card).content.decode()
    enable(participant.competition)
    body = web_client.get(card).content.decode()
    assert 'id="status-ucznia"' in body
    assert "zaakceptowane" in body


def test_the_menu_item_appears_with_the_flag(web_client, coordinator, competition):
    web_client.force_login(coordinator)

    assert LIST not in web_client.get("/coordinator/").content.decode()
    enable(competition)
    assert LIST in web_client.get("/coordinator/").content.decode()


# --- paczki ZIP koordynatora ------------------------------------------------------------------------


@pytest.fixture
def two_works(elim_stage, problems, edition):
    """Dwie prace etapu: autor pierwszej ma zaakceptowane zaświadczenie, drugiej – oczekujące."""
    verified = stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    pending = stored_submission(StageEntryFactory(stage=elim_stage), problems[1])
    make_certificate(verified.entry.participant, elim_stage.edition, status=CertificateStatus.ACCEPTED)
    make_certificate(pending.entry.participant, elim_stage.edition, status=CertificateStatus.PENDING)
    return verified, pending


def test_the_stage_zip_by_default_holds_every_work(web_client, flag_on, coordinator, elim_stage, two_works):
    verified, pending = two_works
    web_client.force_login(coordinator)

    response = web_client.get(reverse("web:coordinator-stage-download", args=[elim_stage.pk]))

    assert response.status_code == 200
    assert zip_names(response) == sorted(
        [
            README_NAME,
            f"{verified.entry.participant.public_code}_zad1_v1.pdf",
            f"{pending.entry.participant.public_code}_zad2_v1.pdf",
        ]
    )


def test_the_stage_zip_can_hold_only_verified_students(
    web_client, flag_on, coordinator, elim_stage, two_works
):
    verified, _pending = two_works
    web_client.force_login(coordinator)

    response = web_client.get(
        reverse("web:coordinator-stage-download", args=[elim_stage.pk]) + "?students=verified"
    )

    assert response.status_code == 200
    assert "status-potwierdzony" in response["Content-Disposition"]
    names = zip_names(response)
    assert names == sorted([README_NAME, f"{verified.entry.participant.public_code}_zad1_v1.pdf"])
    entry = AuditLog.objects.filter(action="stage.downloaded_zip").latest("at")
    assert entry.diff["verified_only"] is True and entry.diff["count"] == 1


def test_the_problem_and_selected_zips_honour_the_choice(
    web_client, flag_on, coordinator, elim_stage, problems, two_works
):
    verified, pending = two_works
    web_client.force_login(coordinator)
    url = reverse("web:coordinator-stage-download", args=[elim_stage.pk])

    only_problem_two = web_client.get(f"{url}?problem={problems[1].pk}&students=verified")
    assert only_problem_two.status_code == 404
    assert "potwierdzonego statusu" in only_problem_two.content.decode()

    selected = web_client.post(url, {"submission_ids": [verified.pk, pending.pk], "students": "verified"})
    assert zip_names(selected) == sorted(
        [README_NAME, f"{verified.entry.participant.public_code}_zad1_v1.pdf"]
    )


def test_without_the_flag_the_verified_scope_is_refused(web_client, coordinator, elim_stage, two_works):
    web_client.force_login(coordinator)

    response = web_client.get(
        reverse("web:coordinator-stage-download", args=[elim_stage.pk]) + "?students=verified"
    )

    assert response.status_code == 404
    assert "nie jest dostępny" in response.content.decode()


def test_the_download_buttons_offer_the_choice_only_with_the_flag(
    web_client, coordinator, elim_stage, problems, competition
):
    stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    web_client.force_login(coordinator)
    assignments = reverse("web:coordinator-stage-assignments", args=[elim_stage.pk])
    problem_card = reverse("web:coordinator-problem", args=[problems[0].pk])

    for url in (assignments, problem_card, "/coordinator/"):
        assert 'name="students"' not in web_client.get(url).content.decode()
    enable(competition)
    for url in (assignments, problem_card, "/coordinator/"):
        body = web_client.get(url).content.decode()
        assert 'name="students"' in body, url
        assert 'value="verified"' in body, url


# --- paczka recenzenta ------------------------------------------------------------------------------


def test_the_reviewer_zip_can_hold_only_verified_students_and_stays_anonymous(
    web_client, flag_on, elim_stage, two_works
):
    verified, pending = two_works
    reviewer = ActiveReviewerFactory()
    ReviewFactory(submission=verified, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    ReviewFactory(submission=pending, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    web_client.force_login(reviewer.user)

    everything = web_client.get(reverse("web:review-download"))
    assert len(zip_names(everything)) == 3

    response = web_client.get(reverse("web:review-download") + "?students=verified")

    assert response.status_code == 200
    assert "status-potwierdzony" in response["Content-Disposition"]
    names = zip_names(response)
    assert names == sorted([README_NAME, f"{verified.entry.participant.public_code}_zad1_v1.pdf"])
    readme = zip_readme(web_client.get(reverse("web:review-download") + "?students=verified"))
    # Paczka recenzenta nie niesie niczego poza pseudonimami: ani skanu, ani nazwiska, ani szkoły.
    participant = verified.entry.participant
    for secret in (participant.user.last_name, participant.user.email, participant.school):
        assert secret not in readme
        assert all(secret not in name for name in names)
    assert not any(name.startswith("student-status") or "zaswiadczenie" in name for name in names)


def test_the_reviewer_list_offers_the_choice_only_with_the_flag(
    web_client, elim_stage, problems, competition
):
    reviewer = ActiveReviewerFactory()
    submission = stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    web_client.force_login(reviewer.user)

    body = web_client.get(reverse("web:review-list")).content.decode()
    assert f'href="{reverse("web:review-download")}"' in body
    assert 'name="students"' not in body

    enable(competition)
    body = web_client.get(reverse("web:review-list")).content.decode()
    assert 'name="students"' in body


def test_the_reviewer_api_honours_the_choice(web_client, flag_on, elim_stage, two_works):
    verified, pending = two_works
    reviewer = ActiveReviewerFactory()
    ReviewFactory(submission=verified, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    ReviewFactory(submission=pending, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    web_client.force_login(reviewer.user)

    response = web_client.get("/api/grading/reviews/download/?students=verified")

    assert response.status_code == 200
    assert zip_names(response) == sorted(
        [README_NAME, f"{verified.entry.participant.public_code}_zad1_v1.pdf"]
    )


def test_the_reviewer_api_refuses_the_choice_without_the_flag(web_client, elim_stage, two_works):
    verified, _pending = two_works
    reviewer = ActiveReviewerFactory()
    ReviewFactory(submission=verified, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    web_client.force_login(reviewer.user)

    response = web_client.get("/api/grading/reviews/download/?students=verified")

    assert response.status_code == 404
    assert response.json()["code"] == "STUDENT_STATUS_SCOPE_UNAVAILABLE"


# --- RODO: eksport i usunięcie ------------------------------------------------------------------------


def test_the_data_export_carries_the_certificates_and_the_clean_file(
    web_client, flag_on, participant, edition
):
    certificate = make_certificate(participant, edition, status=CertificateStatus.REJECTED)
    certificate.rejection_reason = "Brak podpisu"
    certificate.save()
    web_client.force_login(participant.user)

    response = web_client.get("/account/export/")

    with zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))) as package:
        data = json.loads(package.read("dane.json").decode("utf-8"))
        names = package.namelist()
        section = data["zaswiadczenia_statusu_ucznia"]
        assert section[0]["stan"] == "odrzucone"
        assert section[0]["powod_odrzucenia"] == "Brak podpisu"
        name = f"pliki/zaswiadczenie-status-ucznia-e{edition.pk}-v1.pdf"
        assert name in names
        assert package.read(name) == PDF_BYTES


def test_the_export_has_the_section_even_without_the_feature(web_client, participant):
    web_client.force_login(participant.user)

    response = web_client.get("/account/export/")

    with zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))) as package:
        assert json.loads(package.read("dane.json"))["zaswiadczenia_statusu_ucznia"] == []


def test_deleting_the_account_removes_the_scans(
    web_client, flag_on, participant, edition, django_capture_on_commit_callbacks
):
    from apps.accounts.tests.factories import DEFAULT_PASSWORD

    certificate = make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)
    key = certificate.object_key
    web_client.force_login(participant.user)

    with django_capture_on_commit_callbacks(execute=True):
        web_client.post("/account/delete/", {"password": DEFAULT_PASSWORD, "confirm": "on"})

    assert not StudentStatusCertificate.objects.exists()
    assert not stored(key)


def test_anonymising_a_participant_with_works_removes_the_scans(
    web_client, flag_on, participant, entry, problems, edition, django_capture_on_commit_callbacks
):
    """Konto ze śladem w zawodach przechodzi anonimizację – skan i tak znika, praca zostaje."""
    from apps.accounts.tests.factories import DEFAULT_PASSWORD

    submission = stored_submission(entry, problems[0])
    certificate = make_certificate(participant, edition, status=CertificateStatus.ACCEPTED)
    key = certificate.object_key
    web_client.force_login(participant.user)

    with django_capture_on_commit_callbacks(execute=True):
        web_client.post("/account/delete/", {"password": DEFAULT_PASSWORD, "confirm": "on"})

    assert not StudentStatusCertificate.objects.exists()
    assert not stored(key)
    assert stored(submission.latest_file.object_key)


def test_the_processing_register_lists_the_activity_only_with_the_flag(competition):
    from apps.accounts.processing_register import STUDENT_STATUS_ACTIVITY, activities_for

    assert STUDENT_STATUS_ACTIVITY not in activities_for(competition)
    enable(competition)
    assert STUDENT_STATUS_ACTIVITY in activities_for(competition)
