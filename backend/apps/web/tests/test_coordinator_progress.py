"""Ekran ``/coordinator/stages/<id>/progress/`` – pulpit postępu oceniania.

Reguły liczenia (segmenty paska, definicja zaległości, treść przypomnienia) mają własne testy
w ``apps/grading/tests/test_reports.py``. Tutaj sprawdzamy sam ekran: że otwiera się wyłącznie
koordynatorowi, że pokazuje komplet recenzentów i że oba przyciski przypomnienia naprawdę
wysyłają listy.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.core.models import AuditLog
from apps.grading.reports import FALLBACK_OVERDUE_DAYS
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db


def _overdue_review(entry, problem, reviewer):
    """Recenzja przydzielona dawno temu i wciąż niewystawiona – zaległa w obu definicjach raportu.

    Oba pola terminu naraz (``assigned_at`` i – gdy model je ma – ``due_at``), bo raport wybiera
    definicję w czasie działania; test ma sprawdzać ekran, a nie to, który wariant obowiązuje.
    """
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.IN_REVIEW)
    review = ReviewFactory(submission=submission, reviewer=reviewer)
    past = timezone.now() - timedelta(days=FALLBACK_OVERDUE_DAYS + 1)
    fields = {"assigned_at": past}
    if any(field.name == "due_at" for field in review._meta.get_fields()):
        fields["due_at"] = past
    type(review).objects.filter(pk=review.pk).update(**fields)
    return review


def test_page_shows_counters_and_every_active_reviewer(web_client, coordinator, elim_stage, entry, problems):
    reviewer = ActiveReviewerFactory()
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.LOCKED)
    web_client.force_login(coordinator)

    response = web_client.get(f"/coordinator/stages/{elim_stage.pk}/progress/")
    content = response.content.decode()

    assert response.status_code == 200
    assert reviewer.user.email in content
    assert response.context["progress"]["total"] == 1


def test_page_explains_which_definition_of_overdue_it_uses(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get(f"/coordinator/stages/{elim_stage.pk}/progress/")
    content = response.content.decode()

    if response.context["has_due_at"]:
        assert "po terminie zapisanym przy recenzji" in content
    else:
        assert f"ponad {FALLBACK_OVERDUE_DAYS} dni temu" in content


def test_page_is_forbidden_for_a_participant(web_client, participant, elim_stage):
    web_client.force_login(participant.user)

    assert web_client.get(f"/coordinator/stages/{elim_stage.pk}/progress/").status_code == 403


def test_reminder_to_everyone_overdue_sends_mail_and_audits_counters(
    web_client, coordinator, elim_stage, entry, problems, django_capture_on_commit_callbacks
):
    reviewer = ActiveReviewerFactory()
    _overdue_review(entry, problems[0], reviewer)
    web_client.force_login(coordinator)
    mail.outbox.clear()

    # List jest kolejkowany po commicie (``transaction.on_commit``), a test biegnie w transakcji,
    # która nigdy się nie zatwierdza – bez tego bloku żadne wywołanie by nie wystartowało.
    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(f"/coordinator/stages/{elim_stage.pk}/progress/remind/")

    assert response.status_code == 302
    assert [message.to for message in mail.outbox] == [[reviewer.user.email]]
    log = AuditLog.objects.get(action="reviewer.reminded")
    assert log.diff["scope"] == "overdue"
    assert reviewer.user.email not in str(log.diff)


def test_reminder_to_a_single_reviewer_skips_the_others(
    web_client, coordinator, elim_stage, entry, problems, django_capture_on_commit_callbacks
):
    target = ActiveReviewerFactory()
    other = ActiveReviewerFactory()
    _overdue_review(entry, problems[0], target)
    _overdue_review(entry, problems[1], other)
    web_client.force_login(coordinator)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        web_client.post(f"/coordinator/stages/{elim_stage.pk}/progress/remind/", {"reviewer_id": target.pk})

    assert [message.to for message in mail.outbox] == [[target.user.email]]


def test_reminder_says_so_when_there_is_nobody_to_remind(web_client, coordinator, elim_stage):
    ActiveReviewerFactory()
    web_client.force_login(coordinator)
    mail.outbox.clear()

    response = web_client.post(f"/coordinator/stages/{elim_stage.pk}/progress/remind/", follow=True)

    assert not mail.outbox
    assert "Nie ma komu przypominać" in response.content.decode()


def test_dashboard_links_to_the_progress_screen(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get("/coordinator/")

    assert f"/coordinator/stages/{elim_stage.pk}/progress/" in response.content.decode()
