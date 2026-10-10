"""Reklamacja a korekta koordynatora – bramki stanu (audyt bezpieczeństwa 10.10.2026, niskie).

Dwa rozjazdy, które te testy zamykają: korekta oceny końcowej przestawiała pracę w reklamacji na
``GRADED_PROVISIONAL`` (sprawa znikała z kolejki komisji bez decyzji), a ``decide_appeal`` nie
sprawdzał, czy praca w ogóle jest w reklamacji.
"""

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.appeals.models import AppealDecision, AppealStatus
from apps.appeals.services import decide_appeal, file_appeal
from apps.core.api import DomainError
from apps.grading.services import override_final_grade
from apps.submissions.models import Submission, SubmissionStatus

from .conftest import graded_submission
from .factories import VALID_ARGUMENT, AppealsCommitteeMemberFactory

pytestmark = pytest.mark.django_db


def _appealed(stage):
    submission = graded_submission(stage, score=2)
    appeal = file_appeal(submission.entry.participant.user, submission, VALID_ARGUMENT)
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.APPEALED
    return submission, appeal


def test_override_keeps_an_appealed_work_in_appeal(open_stage):
    submission, appeal = _appealed(open_stage)

    override_final_grade(
        submission, 5, rationale="Korekta z posiedzenia komisji.", actor=CoordinatorFactory()
    )

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.APPEALED
    # Komisja nadal może rozstrzygnąć – sprawa nie zniknęła z jej kolejki.
    decide_appeal(appeal, AppealsCommitteeMemberFactory(), AppealStatus.REJECTED, None, "Zarzut chybiony.")
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.FINAL


def test_decision_requires_the_work_to_be_in_appeal(open_stage):
    submission, appeal = _appealed(open_stage)
    # Praca wyprowadzona z reklamacji inną drogą (np. dane poprawione ręcznie).
    Submission.objects.filter(pk=submission.pk).update(status=SubmissionStatus.GRADED_PROVISIONAL)

    with pytest.raises(DomainError) as exc:
        decide_appeal(appeal, AppealsCommitteeMemberFactory(), AppealStatus.ACCEPTED, 6, "Pełne rozwiązanie.")

    assert exc.value.machine_code == "SUBMISSION_NOT_APPEALED"
    assert not AppealDecision.objects.filter(appeal=appeal).exists()
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL
