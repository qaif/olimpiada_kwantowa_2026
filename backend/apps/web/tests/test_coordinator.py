"""Kryterium 7 z T-08: przydział recenzentów i publikacja wyników z panelu koordynatora."""

import pytest

from apps.accounts.models import CommitteeStatus
from apps.accounts.tests.factories import ActiveReviewerFactory, CommitteeMemberFactory
from apps.grading.models import Review
from apps.results.models import ResultsPublication
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import close_stage_timeline

pytestmark = pytest.mark.django_db


def test_assign_creates_reviews(web_client, coordinator, elim_stage, entry, problems):
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.LOCKED)
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/stages/{elim_stage.pk}/assign/", {"per_submission": 2})

    assert response.status_code == 302
    assert Review.objects.filter(submission__entry__stage=elim_stage).count() == 2
    assert Submission.objects.get(entry=entry).status == SubmissionStatus.IN_REVIEW


def test_publish_creates_results_publication(web_client, coordinator, elim_stage, entry):
    # Publikacja wymaga zamkniętego okna reklamacji (apps.results.services.apply_qualification).
    close_stage_timeline(elim_stage)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/results/publish/", {"anonymization": "CODE"}
    )

    assert response.status_code == 302
    publication = ResultsPublication.objects.get(stage=elim_stage)
    assert publication.anonymization == "CODE"
    assert publication.rows[0]["display"] == entry.participant.public_code


def test_close_stage_locks_submissions(web_client, coordinator, elim_stage, entry, problems):
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.SUBMITTED)
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/stages/{elim_stage.pk}/close/")

    elim_stage.refresh_from_db()
    assert response.status_code == 302
    assert elim_stage.closed_at is not None
    assert Submission.objects.get(entry=entry).status == SubmissionStatus.LOCKED


def test_approve_committee_member_from_panel(web_client, coordinator):
    member = CommitteeMemberFactory(status=CommitteeStatus.PENDING)
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/committee/{member.pk}/approve/")

    member.refresh_from_db()
    assert response.status_code == 302
    assert member.status == CommitteeStatus.ACTIVE
    assert member.user.groups.filter(name="reviewer").exists()


def test_invitation_code_is_shown_once_in_messages(web_client, coordinator):
    web_client.force_login(coordinator)

    response = web_client.post(
        "/coordinator/invitations/",
        {"district": "mazowieckie", "valid_days": 14, "max_uses": 1},
        follow=True,
    )
    content = response.content.decode()
    assert "Kod zaproszenia" in content

    # Komunikat sesyjny znika po odczytaniu – kod nie da się odtworzyć z bazy (jest tam sha256).
    assert "Kod zaproszenia (widoczny tylko teraz" not in web_client.get("/coordinator/").content.decode()


def test_compute_results_preview_is_rendered(web_client, coordinator, elim_stage, entry):
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/stages/{elim_stage.pk}/results/compute/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Podgląd wyników etapu" in content
    assert entry.participant.public_code in content
