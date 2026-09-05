"""Panel komisji odwoławczej i złożenie reklamacji z panelu uczestnika (T-08, zakres `/appeals/`)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import GROUP_APPEALS, GROUP_REVIEWER, CommitteeStatus
from apps.accounts.tests.factories import CommitteeMemberFactory, UserFactory
from apps.appeals.models import Appeal, AppealStatus
from apps.appeals.services import appealable_submissions
from apps.grading.models import ROUND_BLIND, GradeMethod
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
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


def test_decide_of_an_appeal_outside_the_members_queue_is_404(
    web_client, participant, graded_submission, appeals_member
):
    """Przegląd T-08, ustalenie 6: obiekt bierzemy z kolejki członka komisji, nie z całej tabeli.

    Członek w konflikcie interesów (recenzował tę pracę) nie widzi reklamacji w kolejce, więc
    i decyzji na niej nie podejmie – a odpowiedź nie potwierdza nawet, że taka reklamacja istnieje.
    """
    appeal = Appeal.objects.create(submission=graded_submission, filed_by=participant, argument=ARGUMENT)
    ReviewFactory(submission=graded_submission, reviewer=appeals_member, round=ROUND_BLIND)
    web_client.force_login(appeals_member.user)

    response = web_client.post(
        f"/appeals/{appeal.pk}/decide/",
        {"status": AppealStatus.REJECTED, "justification": "Bez zmian."},
    )

    assert response.status_code == 404
    appeal.refresh_from_db()
    assert appeal.status == AppealStatus.OPEN


def test_appeals_queue_query_count_does_not_grow_with_rows(
    web_client, participant, entry, problems, appeals_member, django_assert_num_queries
):
    """Kolejka nie może robić zapytania na wiersz – stąd ``select_related`` w ``appeals_queue``."""

    def make_appeal(problem):
        submission = SubmissionFactory(
            entry=entry, problem=problem, status=SubmissionStatus.GRADED_PROVISIONAL
        )
        FinalGradeFactory(submission=submission, score=2, method=GradeMethod.CONSENSUS)
        return Appeal.objects.create(submission=submission, filed_by=participant, argument=ARGUMENT)

    close_submissions(entry.stage)
    web_client.force_login(appeals_member.user)
    make_appeal(problems[0])

    # Rozgrzewka poza pomiarem: Wagtail cache'uje ``Site`` przy pierwszym renderze base.html,
    # więc bez niej pierwsze żądanie ma jedno zapytanie więcej z powodu niezwiązanym z kolejką.
    assert web_client.get("/appeals/").status_code == 200
    with CaptureQueriesContext(connection) as one_row:
        assert web_client.get("/appeals/").status_code == 200

    make_appeal(problems[1])
    with CaptureQueriesContext(connection) as two_rows:
        response = web_client.get("/appeals/")

    assert response.content.decode().count(ARGUMENT) == 2
    assert len(two_rows) == len(one_row), [query["sql"] for query in two_rows]


def test_appealable_submissions_is_the_single_source_of_the_rule(participant, graded_submission):
    """Predykat „co podlega reklamacji” mieszka w serwisie – widok tylko go woła."""
    now = timezone.now()

    assert appealable_submissions(participant.user, now) == [graded_submission]

    Appeal.objects.create(submission=graded_submission, filed_by=participant, argument=ARGUMENT)
    assert appealable_submissions(participant.user, now) == []
