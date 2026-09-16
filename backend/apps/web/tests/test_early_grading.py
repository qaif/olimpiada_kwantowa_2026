"""Ocenianie przed zamknięciem etapu w interfejsie WWW (prośba organizatora).

Cztery ekrany, po jednym na rolę i sytuację: karta etapu w panelu koordynatora (liczniki i przycisk
blokady), ekran przydziałów (praca oddana wchodzi do oceniania pojedynczo), panel recenzenta
(powód anulowania) i karta zadania uczestnika (ostrzeżenie przed wysłaniem nowej wersji).
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.grading.models import ReviewCancelReason, ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory, pdf_upload

pytestmark = pytest.mark.django_db


def assignments_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/assignments/"


# --- panel koordynatora: karta etapu -------------------------------------------------------


def test_dashboard_shows_counters_and_lock_button(web_client, coordinator, elim_stage, entry, problems):
    SubmissionFactory(entry=entry, problem=problems[0])
    SubmissionFactory(entry=entry, problem=problems[1], status=SubmissionStatus.IN_REVIEW)
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert f"/coordinator/stages/{elim_stage.pk}/lock-for-review/" in content
    assert "Zablokuj oddane prace do oceny" in content
    assert "Oddane (niezablokowane)" in content
    assert "W ocenie" in content


def test_lock_button_locks_works_and_keeps_the_stage_open(
    web_client, coordinator, elim_stage, entry, problems
):
    submission = SubmissionFactory(entry=entry, problem=problems[0])
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/stages/{elim_stage.pk}/lock-for-review/", follow=True)

    submission.refresh_from_db()
    elim_stage.refresh_from_db()
    assert submission.status == SubmissionStatus.LOCKED
    assert elim_stage.closed_at is None
    assert "Zablokowano 1 prac do oceny" in response.content.decode()
    assert "Etap pozostaje otwarty" in response.content.decode()


# --- panel koordynatora: ekran przydziałów -------------------------------------------------


def test_assignments_page_lists_submitted_work_with_a_lock_button(
    web_client, coordinator, elim_stage, entry, problems
):
    """Praca oddana stoi w tabeli, ale zamiast formularza przydziału ma jedno kliknięcie."""
    submission = SubmissionFactory(entry=entry, problem=problems[0])
    web_client.force_login(coordinator)

    content = web_client.get(assignments_url(elim_stage)).content.decode()

    assert submission.entry.participant.public_code in content
    assert f"/coordinator/submissions/{submission.pk}/lock-for-review/" in content
    assert "Zablokuj do oceny" in content
    # Przydział i ocena końcowa czekają na blokadę – formularzy dla tej pracy jeszcze nie ma.
    assert f"/coordinator/submissions/{submission.pk}/assign-reviewer/" not in content
    assert f"/coordinator/submissions/{submission.pk}/final-grade/" not in content


def test_single_lock_returns_to_the_assignments_screen(web_client, coordinator, elim_stage, entry, problems):
    submission = SubmissionFactory(entry=entry, problem=problems[0])
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/submissions/{submission.pk}/lock-for-review/", follow=True)

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.LOCKED
    assert response.redirect_chain[-1][0] == assignments_url(elim_stage)
    assert f"/coordinator/submissions/{submission.pk}/assign-reviewer/" in response.content.decode()


def test_single_lock_refusal_is_a_message_not_a_500(web_client, coordinator, elim_stage, entry, problems):
    older = SubmissionFactory(entry=entry, problem=problems[0], version=1)
    SubmissionFactory(entry=entry, problem=problems[0], version=2)
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/submissions/{older.pk}/lock-for-review/", follow=True)

    older.refresh_from_db()
    assert older.status == SubmissionStatus.SUBMITTED
    assert "nowszą wersję" in response.content.decode()


# --- panel recenzenta ----------------------------------------------------------------------


@pytest.fixture
def cancelled_review(entry, problems):
    """Recenzja unieważniona nową wersją pracy – z zapisanym powodem."""
    submission = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.SUBMITTED)
    return ReviewFactory(
        submission=submission,
        reviewer=ActiveReviewerFactory(),
        status=ReviewStatus.CANCELLED,
        cancel_reason=ReviewCancelReason.SUPERSEDED,
        score=5,
    )


def test_reviewer_detail_explains_the_new_version(web_client, cancelled_review):
    web_client.force_login(cancelled_review.reviewer.user)

    content = web_client.get(f"/review/{cancelled_review.pk}/").content.decode()

    assert "Uczestnik wysłał nową wersję rozwiązania" in content
    assert "Koordynator odebrał Ci tę pracę" not in content


def test_reviewer_list_shows_the_reason_per_row(web_client, cancelled_review):
    web_client.force_login(cancelled_review.reviewer.user)

    content = web_client.get("/review/").content.decode()

    assert "Recenzje anulowane" in content
    assert "Uczestnik wysłał nową wersję rozwiązania" in content


# --- panel uczestnika ----------------------------------------------------------------------


def test_participant_sees_a_warning_when_the_work_is_already_in_review(
    web_client, participant, entry, problems
):
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert "Ta praca jest już w ocenie." in content
    assert "zostanie oceniona od nowa." in content
    # Ostrzeżenie, nie blokada: formularz uploadu stoi dalej.
    assert f"/me/stages/{entry.stage_id}/problems/1/upload/" in content


def test_participant_without_grading_sees_no_warning(web_client, participant, entry, problems):
    SubmissionFactory(entry=entry, problem=problems[0])
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert "Ta praca jest już w ocenie." not in content


def test_warning_appears_in_the_htmx_card_after_upload(web_client, participant, entry, problems):
    """Karta po uploadzie jest tym samym fragmentem, więc nie może mówić czegoś innego."""
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)
    web_client.force_login(participant.user)

    response = web_client.post(
        f"/me/stages/{entry.stage_id}/problems/1/upload/",
        {"file": pdf_upload()},
        HTTP_HX_REQUEST="true",
    )

    # Nowa wersja właśnie unieważniła ocenę, więc karta wraca bez ostrzeżenia – praca czeka na
    # ponowne wciągnięcie do oceniania.
    assert "Ta praca jest już w ocenie." not in response.content.decode()
    assert "wersja 2" in response.content.decode()
