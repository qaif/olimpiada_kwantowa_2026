"""Panel komisji odwoławczej i złożenie reklamacji z panelu uczestnika (T-08, zakres `/appeals/`)."""

import pytest

from apps.accounts.models import GROUP_APPEALS, GROUP_REVIEWER, CommitteeStatus
from apps.accounts.tests.factories import CommitteeMemberFactory, UserFactory
from apps.appeals.models import Appeal, AppealStatus
from apps.grading.models import GradeMethod
from apps.grading.tests.factories import FinalGradeFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import PARTICIPANT_LAST_NAME, close_submissions

pytestmark = pytest.mark.django_db

ARGUMENT = "Rozwiązanie zawiera pełny dowód nierówności, a ocena nie uwzględnia lematu ze strony drugiej."


@pytest.fixture
def graded_submission(entry, problems):
    close_submissions(entry.stage)
    submission = SubmissionFactory(
        entry=entry, problem=problems[0], status=SubmissionStatus.GRADED_PROVISIONAL
    )
    FinalGradeFactory(submission=submission, score=2, method=GradeMethod.CONSENSUS)
    return submission


@pytest.fixture
def appeals_member():
    return CommitteeMemberFactory(
        user=UserFactory(groups=[GROUP_REVIEWER, GROUP_APPEALS]),
        status=CommitteeStatus.ACTIVE,
        is_appeals_committee=True,
    )


def test_participant_files_appeal_from_dashboard(web_client, participant, graded_submission):
    web_client.force_login(participant.user)
    content = web_client.get("/me/").content.decode()
    assert f"/me/submissions/{graded_submission.pk}/appeal/" in content

    response = web_client.post(f"/me/submissions/{graded_submission.pk}/appeal/", {"argument": ARGUMENT})

    assert response.status_code == 302
    appeal = Appeal.objects.get(submission=graded_submission)
    assert appeal.status == AppealStatus.OPEN
    graded_submission.refresh_from_db()
    assert graded_submission.status == SubmissionStatus.APPEALED


def test_committee_sees_queue_without_personal_data(
    web_client, participant, graded_submission, appeals_member
):
    Appeal.objects.create(submission=graded_submission, filed_by=participant, argument=ARGUMENT)
    web_client.force_login(appeals_member.user)

    content = web_client.get("/appeals/").content.decode()

    assert ARGUMENT in content
    assert participant.public_code in content
    assert PARTICIPANT_LAST_NAME not in content


def test_committee_decision_changes_final_grade(web_client, participant, graded_submission, appeals_member):
    appeal = Appeal.objects.create(submission=graded_submission, filed_by=participant, argument=ARGUMENT)
    web_client.force_login(appeals_member.user)

    response = web_client.post(
        f"/appeals/{appeal.pk}/decide/",
        {"status": AppealStatus.ACCEPTED, "new_score": 6, "justification": "Lemat jest poprawny."},
    )

    appeal.refresh_from_db()
    graded_submission.refresh_from_db()
    assert response.status_code == 302
    assert appeal.status == AppealStatus.ACCEPTED
    assert graded_submission.final_grade.score == 6
    assert graded_submission.final_grade.method == GradeMethod.APPEAL
    assert graded_submission.status == SubmissionStatus.FINAL
