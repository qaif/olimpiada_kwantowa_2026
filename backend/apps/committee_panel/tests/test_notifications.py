"""Powiadomienia komisji (UX-PANEL-03 D11–D13): haki przydziałów, rozjemcy, terminu i reklamacji, lista."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.appeals.tests.factories import AppealsCommitteeMemberFactory
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.grading.models import ROUND_BLIND, ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.notifications.kinds import NotificationKind
from apps.notifications.models import Notification
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership
from apps.web.tests.conftest import close_submissions

from .. import notifications as hooks

pytestmark = pytest.mark.django_db


@pytest.fixture
def stage(competition):
    return StageFactory(edition=CurrentEditionFactory(competition=competition), name="Etap okręgowy próbny")


@pytest.fixture
def student(competition):
    profile = ParticipantFactory(competition=competition)
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    return profile


@pytest.fixture
def submission(stage, student):
    entry = StageEntryFactory(participant=student, stage=stage)
    return SubmissionFactory(entry=entry, problem=ProblemFactory(stage=stage), status=SubmissionStatus.LOCKED)


@pytest.fixture
def reviewer(competition):
    member = ActiveReviewerFactory(competition=competition, district=None)
    grant_membership(member.user, competition, CompetitionRole.REVIEWER)
    return member


def rows(user, kind=None):
    queryset = Notification.objects.filter(user=user)
    return list(queryset.filter(kind=kind) if kind else queryset)


def test_manual_assignment_notifies_the_reviewer_without_work_data(reviewer, submission, student):
    from apps.grading.services import assign_reviewer_to_submission

    assign_reviewer_to_submission(submission, reviewer)

    [row] = rows(reviewer.user, NotificationKind.REVIEW_ASSIGNED)
    assert row.params == {"stage": "Etap okręgowy próbny"}
    assert row.target == "web:review-list"
    assert student.public_code not in str(row.params)
    # Uczestnik nie dostaje nic z tego zdarzenia.
    assert rows(student.user) == []


def test_second_assignment_in_the_stage_bumps_instead_of_stacking(reviewer, submission, stage, student):
    from apps.grading.services import assign_reviewer_to_submission

    assign_reviewer_to_submission(submission, reviewer)
    other = SubmissionFactory(
        entry=submission.entry, problem=ProblemFactory(stage=stage, number=9), status=SubmissionStatus.LOCKED
    )
    assign_reviewer_to_submission(other, reviewer)

    assert len(rows(reviewer.user, NotificationKind.REVIEW_ASSIGNED)) == 1


def test_automatic_assignment_notifies_every_chosen_reviewer(competition, stage, submission):
    from apps.grading.services import assign_reviewers

    members = []
    for _ in range(2):
        member = ActiveReviewerFactory(competition=competition, district=None)
        grant_membership(member.user, competition, CompetitionRole.REVIEWER)
        members.append(member)

    assign_reviewers(stage, per_submission=2)

    for member in members:
        assert len(rows(member.user, NotificationKind.REVIEW_ASSIGNED)) == 1


def test_tiebreak_assignment_notifies_the_arbiter(competition, reviewer, submission):
    from apps.grading.services import assign_third_reviewer

    submission.status = SubmissionStatus.MODERATION
    submission.save(update_fields=["status"])

    assign_third_reviewer(submission, reviewer)

    [row] = rows(reviewer.user, NotificationKind.REVIEW_TIEBREAK)
    assert row.target_suffix == "?round=2"


def test_hook_skips_accounts_that_are_not_committee_members(competition, stage, student):
    assert hooks.reviews_assigned(stage, {student.user_id}) == 0
    assert rows(student.user) == []


def test_reminder_task_adds_a_panel_notification(competition, reviewer, submission):
    from apps.grading.tasks import remind_overdue_reviews

    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status"])
    ReviewFactory(
        submission=submission,
        reviewer=reviewer,
        round=ROUND_BLIND,
        status=ReviewStatus.ASSIGNED,
        due_at=timezone.now() - timedelta(days=1),
    )

    remind_overdue_reviews()

    [row] = rows(reviewer.user, NotificationKind.REVIEW_DEADLINE)
    assert row.params == {"count": 1}


def test_filed_appeal_notifies_the_committee_except_the_conflicted(competition, stage, student):
    from apps.appeals.services import file_appeal
    from apps.grading.models import FinalGrade, GradeMethod

    free = AppealsCommitteeMemberFactory(competition=competition)
    conflicted = AppealsCommitteeMemberFactory(competition=competition)
    for member in (free, conflicted):
        grant_membership(member.user, competition, CompetitionRole.APPEALS)
    entry = StageEntryFactory(participant=student, stage=stage)
    work = SubmissionFactory(
        entry=entry, problem=ProblemFactory(stage=stage), status=SubmissionStatus.GRADED_PROVISIONAL
    )
    ReviewFactory(
        submission=work, reviewer=conflicted, round=ROUND_BLIND, status=ReviewStatus.SUBMITTED, score=2
    )
    FinalGrade.objects.create(submission=work, score=2, method=GradeMethod.CONSENSUS)
    close_submissions(stage)

    file_appeal(student.user, work, "Proszę o ponowne sprawdzenie rozwiązania – " * 3)

    [row] = rows(free.user, NotificationKind.APPEAL_PENDING)
    assert row.target == "web:appeals"
    assert rows(conflicted.user, NotificationKind.APPEAL_PENDING) == []


# --- lista w panelu komisji -----------------------------------------------------------------------------


@pytest.fixture
def logged(client_for, competition, reviewer):
    client = client_for(competition)
    client.force_login(reviewer.user)
    return client


def test_list_shows_the_sentence_and_opening_marks_read(logged, competition, reviewer, stage):
    hooks.reviews_assigned(stage, {reviewer.user_id})
    row = Notification.objects.get(user=reviewer.user)

    html = logged.get("/committee/notifications/").content.decode()
    assert "Nowe prace do oceny: Etap okręgowy próbny" in html
    assert 'data-nav="notifications"' in html

    response = logged.get(f"/committee/notifications/{row.pk}/")
    assert response.status_code == 302
    assert response["Location"] == "/review/?tab=todo"
    row.refresh_from_db()
    assert row.read_at is not None


def test_someone_elses_notification_is_404(logged, competition, stage):
    other = ActiveReviewerFactory(competition=competition)
    grant_membership(other.user, competition, CompetitionRole.REVIEWER)
    hooks.reviews_assigned(stage, {other.user_id})
    foreign = Notification.objects.get(user=other.user)

    assert logged.get(f"/committee/notifications/{foreign.pk}/").status_code == 404


def test_read_all_clears_the_bell(logged, reviewer, stage):
    hooks.reviews_assigned(stage, {reviewer.user_id})

    response = logged.post("/committee/notifications/read-all/")

    assert response.status_code == 302
    assert not Notification.objects.filter(user=reviewer.user, read_at__isnull=True).exists()
