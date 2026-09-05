"""Regresje z przeglądu Critica taska T-05 dotyczące kolejki komisji odwoławczej (finding 2).

``AppealQueueSerializer.file_available`` czyta ``Submission.latest_file``. Dopóki właściwość robiła
własne ``order_by``, omijała prefetch i każda pozycja kolejki dokładała zapytanie – kolejka rosła
liniowo w zapytaniach razem z liczbą reklamacji.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.appeals.models import AppealStatus
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFileFactory

from .conftest import graded_submission
from .factories import VALID_ARGUMENT, AppealFactory, AppealsCommitteeMemberFactory

pytestmark = pytest.mark.django_db

APPEALS_URL = "/api/appeals/"


def pending_appeals(stage, count: int) -> None:
    """``count`` reklamacji czekających na komisję, każda z własnym plikiem rozwiązania."""
    for _ in range(count):
        submission = graded_submission(stage)
        SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN)
        submission.status = SubmissionStatus.APPEALED
        submission.save(update_fields=["status"])
        AppealFactory(
            submission=submission,
            filed_by=submission.entry.participant,
            argument=VALID_ARGUMENT,
            status=AppealStatus.OPEN,
        )


def queries_for_queue(client, member, expected: int) -> int:
    client.force_authenticate(member.user)
    with CaptureQueriesContext(connection) as captured:
        response = client.get(APPEALS_URL)
    assert response.status_code == 200
    assert len(response.data) == expected
    return len(captured)


def test_appeal_queue_query_count_does_not_grow_with_appeals(client, open_stage):
    """Liczba zapytań kolejki nie może zależeć od liczby reklamacji w niej."""
    member = AppealsCommitteeMemberFactory()

    pending_appeals(open_stage, 2)
    baseline = queries_for_queue(client, member, expected=2)

    pending_appeals(open_stage, 8)
    with_ten = queries_for_queue(client, member, expected=10)

    assert with_ten == baseline
    assert with_ten <= 8, f"{with_ten} zapytań na kolejkę reklamacji to już N+1"
