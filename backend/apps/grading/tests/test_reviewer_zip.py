"""Paczka ZIP recenzenta: zakres (własne, nieanulowane), anonimowe nazwy, audyt i pusta kolejka.

Zakres jest tu jedyną rzeczą, która naprawdę chroni dane: pobranie idzie przez serwis, a nie przez
``Submission.objects.for_user``, więc błąd w filtrze oznaczałby wydanie cudzych prac jednym plikiem.
"""

import zipfile
from io import BytesIO

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ReviewCancelReason, ReviewStatus
from apps.grading.services import build_reviewer_zip
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.packaging import README_NAME
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db


def assigned_submission(stage, reviewer, *, status=ReviewStatus.ASSIGNED, cancel_reason=""):
    """Praca z plikiem po skanie i recenzją tego recenzenta."""
    entry = StageEntryFactory(stage=stage)
    submission = SubmissionFactory(
        entry=entry, problem=ProblemFactory(stage=stage), status=SubmissionStatus.IN_REVIEW
    )
    submission_file = SubmissionFileFactory(
        submission=submission, av_status=AvStatus.CLEAN, original_name="Jan_Kowalski.pdf"
    )
    get_submission_storage().put(submission_file.object_key, BytesIO(PDF_BYTES), "application/pdf")
    review = ReviewFactory(
        submission=submission, reviewer=reviewer, status=status, cancel_reason=cancel_reason
    )
    return submission, review


def test_paczka_zawiera_wylacznie_wlasne_nieanulowane_przydzialy(stage):
    reviewer = ActiveReviewerFactory()
    mine, review = assigned_submission(stage, reviewer)
    cancelled, _ = assigned_submission(
        stage, reviewer, status=ReviewStatus.CANCELLED, cancel_reason=ReviewCancelReason.COORDINATOR
    )
    stranger, _ = assigned_submission(stage, ActiveReviewerFactory())

    package = build_reviewer_zip(reviewer)

    with zipfile.ZipFile(package.stream) as archive:
        names = sorted(archive.namelist())
        readme = archive.read(README_NAME).decode("utf-8")
    assert package.count == 1
    assert names == sorted(
        [README_NAME, f"{mine.entry.participant.public_code}_zad{mine.problem.number}_v1.pdf"]
    )
    # README wiąże plik z numerem recenzji – bez tego recenzent nie odnajdzie pracy w panelu.
    assert f"recenzja {review.pk} →" in readme
    assert cancelled.entry.participant.public_code not in readme
    assert stranger.entry.participant.public_code not in readme


def test_pusta_kolejka_to_404_z_powodem(stage):
    reviewer = ActiveReviewerFactory()

    with pytest.raises(DomainError) as excinfo:
        build_reviewer_zip(reviewer)

    assert excinfo.value.status_code == 404
    assert str(excinfo.value.detail) == "Brak przydzielonych prac do pobrania."


def test_audyt_notuje_sama_liczbe(stage):
    reviewer = ActiveReviewerFactory()
    assigned_submission(stage, reviewer)

    build_reviewer_zip(reviewer, actor=reviewer.user)

    log = AuditLog.objects.get(action="review.downloaded_zip")
    assert log.target_id == str(reviewer.pk)
    assert log.diff == {"count": 1}


def test_api_recenzenta_oddaje_archiwum(client, stage):
    reviewer = ActiveReviewerFactory()
    assigned_submission(stage, reviewer)
    client.force_authenticate(reviewer.user)

    response = client.get(reverse("grading:review-download"))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
        assert README_NAME in archive.namelist()


def test_api_pustej_kolejki_odpowiada_404(client, stage):
    client.force_authenticate(ActiveReviewerFactory().user)

    response = client.get(reverse("grading:review-download"))

    assert response.status_code == 404
    assert response.data["code"] == "NO_ASSIGNED_SUBMISSIONS"


def test_api_odmawia_komus_kto_nie_jest_recenzentem(client, stage):
    from apps.accounts.tests.factories import ParticipantFactory

    client.force_authenticate(ParticipantFactory().user)

    assert client.get(reverse("grading:review-download")).status_code == 403
