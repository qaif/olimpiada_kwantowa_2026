"""Kryteria 1–6 i 9 T-06: złożenie reklamacji, okno, unikalność, konflikt interesów, decyzja."""

from datetime import timedelta
from io import BytesIO

import pytest
from freezegun import freeze_time

from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.appeals.models import Appeal, AppealDecision, AppealStatus
from apps.grading.models import ROUND_BLIND, FinalGrade, GradeMethod
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFileFactory

from .conftest import graded_submission, round_one_reviews
from .factories import SHORT_ARGUMENT, VALID_ARGUMENT, AppealsCommitteeMemberFactory

pytestmark = pytest.mark.django_db

APPEALS_URL = "/api/appeals/"
MY_APPEALS_URL = "/api/me/appeals/"

# Dane, które przy rozpatrywaniu reklamacji nie mogą wypłynąć do komisji.
SECRET_FIRST_NAME = "Zenobia"
SECRET_LAST_NAME = "Nieujawniona"
SECRET_SCHOOL = "Tajne Liceum nr 42"


def appeal_url(submission) -> str:
    return f"/api/submissions/{submission.pk}/appeal/"


def decide_url(appeal) -> str:
    return f"/api/appeals/{appeal.pk}/decide/"


def file_appeal_via_api(client, submission, argument: str = VALID_ARGUMENT):
    client.force_authenticate(submission.entry.participant.user)
    return client.post(appeal_url(submission), {"argument": argument}, format="json")


def test_appeal_in_open_window_is_created(client, open_stage):
    """1. Reklamacja w oknie → 201, Submission APPEALED, Appeal OPEN."""
    submission = graded_submission(open_stage)

    response = file_appeal_via_api(client, submission)

    assert response.status_code == 201, response.data
    assert response.data["status"] == AppealStatus.OPEN
    assert response.data["submission_id"] == submission.pk
    appeal = Appeal.objects.get(submission=submission)
    assert appeal.status == AppealStatus.OPEN
    assert appeal.filed_by_id == submission.entry.participant_id
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.APPEALED


def test_appeal_before_window_opens_is_forbidden(client, stage_before_window):
    """2a. Przed ``appeal_window_opens_at`` → 403 APPEAL_WINDOW_CLOSED."""
    submission = graded_submission(stage_before_window)

    response = file_appeal_via_api(client, submission)

    assert response.status_code == 403
    assert response.data["code"] == "APPEAL_WINDOW_CLOSED"
    assert not Appeal.objects.exists()
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_appeal_after_window_closes_is_forbidden(client, open_stage):
    """2b. Po ``appeal_window_closes_at`` → 403 APPEAL_WINDOW_CLOSED (zegar przesunięty)."""
    submission = graded_submission(open_stage)

    with freeze_time(open_stage.appeal_window_closes_at + timedelta(seconds=1)):
        response = file_appeal_via_api(client, submission)

    assert response.status_code == 403
    assert response.data["code"] == "APPEAL_WINDOW_CLOSED"
    assert not Appeal.objects.exists()


def test_second_appeal_on_same_submission_conflicts(client, open_stage):
    """3. Druga reklamacja na to samo rozwiązanie → 409 APPEAL_ALREADY_FILED."""
    submission = graded_submission(open_stage)
    assert file_appeal_via_api(client, submission).status_code == 201

    response = file_appeal_via_api(client, submission)

    assert response.status_code == 409
    assert response.data["code"] == "APPEAL_ALREADY_FILED"
    assert Appeal.objects.filter(submission=submission).count() == 1


def test_appeal_on_foreign_submission_is_not_found(client, open_stage):
    """4. Reklamacja na cudze rozwiązanie → 404 (nie 403 – odpowiedź nie potwierdza istnienia)."""
    submission = graded_submission(open_stage)
    intruder = ParticipantFactory()
    client.force_authenticate(intruder.user)

    response = client.post(appeal_url(submission), {"argument": VALID_ARGUMENT}, format="json")

    assert response.status_code == 404
    assert not Appeal.objects.exists()


def test_short_argument_is_rejected(client, open_stage):
    """5. ``argument`` krótszy niż 50 znaków → 400."""
    submission = graded_submission(open_stage)

    response = file_appeal_via_api(client, submission, SHORT_ARGUMENT)

    assert response.status_code == 400
    assert not Appeal.objects.exists()
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_round_one_reviewer_cannot_decide_and_does_not_see_appeal(client, open_stage):
    """6. Autor recenzji rundy 1 → 403 CONFLICT_OF_INTEREST i brak reklamacji w GET appeals/."""
    submission = graded_submission(open_stage)
    conflicted = AppealsCommitteeMemberFactory()
    ReviewFactory(submission=submission, reviewer=conflicted, round=ROUND_BLIND, score=2)
    assert file_appeal_via_api(client, submission).status_code == 201
    appeal = Appeal.objects.get(submission=submission)

    client.force_authenticate(conflicted.user)
    listing = client.get(APPEALS_URL)
    response = client.post(
        decide_url(appeal),
        {"status": AppealStatus.ACCEPTED, "new_score": 6, "justification": "Zarzut zasadny."},
        format="json",
    )

    assert listing.status_code == 200
    assert [item["id"] for item in listing.data] == []
    assert response.status_code == 403
    assert response.data["code"] == "CONFLICT_OF_INTEREST"
    assert not AppealDecision.objects.exists()
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.APPEALED


def test_appeals_queue_shows_public_code_reviews_and_grade_without_personal_data(client, open_stage):
    """Komisja widzi kod publiczny, obie oceny rundy 1 i ocenę uzgodnioną – bez danych osobowych."""
    participant = ParticipantFactory(
        school=SECRET_SCHOOL,
        user__first_name=SECRET_FIRST_NAME,
        user__last_name=SECRET_LAST_NAME,
        user__email="tajny.uczestnik@example.test",
    )
    submission = graded_submission(open_stage, participant=participant)
    round_one_reviews(submission)
    assert file_appeal_via_api(client, submission).status_code == 201
    member = AppealsCommitteeMemberFactory()
    client.force_authenticate(member.user)

    response = client.get(APPEALS_URL)

    assert response.status_code == 200
    assert len(response.data) == 1
    item = response.data[0]
    assert item["participant_public_code"] == participant.public_code
    assert item["argument"] == VALID_ARGUMENT
    assert len(item["reviews"]) == 2
    assert all("Notatka wewnętrzna" in review["comment_internal"] for review in item["reviews"])
    assert item["final_grade"]["score"] == 2
    assert item["download_url"] == f"/api/submissions/{submission.pk}/download/"

    raw = response.content.decode()
    for forbidden in (
        SECRET_FIRST_NAME,
        SECRET_LAST_NAME,
        SECRET_SCHOOL,
        "tajny.uczestnik@example.test",
        "reviewer_id",
        "reviewer_email",
    ):
        assert forbidden not in raw


def test_accepted_appeal_updates_grade_and_is_visible_to_participant(client, open_stage):
    """7. ACCEPTED z ``new_score`` → FinalGrade APPEAL, FINAL; uczestnik widzi decyzję."""
    submission = graded_submission(open_stage, score=2)
    assert file_appeal_via_api(client, submission).status_code == 201
    appeal = Appeal.objects.get(submission=submission)
    member = AppealsCommitteeMemberFactory()
    client.force_authenticate(member.user)

    response = client.post(
        decide_url(appeal),
        {"status": AppealStatus.ACCEPTED, "new_score": 6, "justification": "Dowód jest pełny."},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["status"] == AppealStatus.ACCEPTED
    grade = FinalGrade.objects.get(submission=submission)
    assert (grade.score, grade.method) == (6, GradeMethod.APPEAL)
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.FINAL

    client.force_authenticate(submission.entry.participant.user)
    mine = client.get(MY_APPEALS_URL)
    assert mine.status_code == 200
    assert mine.data[0]["decision"] == {
        "status": AppealStatus.ACCEPTED,
        "new_score": 6,
        "justification": "Dowód jest pełny.",
        "decided_at": response.data["decision"]["decided_at"],
    }

    submissions = client.get("/api/me/submissions/")
    assert submissions.status_code == 200
    latest = submissions.data[0]["latest"]
    assert latest["final_grade"]["score"] == 6
    assert latest["final_grade"]["method"] == GradeMethod.APPEAL
    assert latest["appeal"]["status"] == AppealStatus.ACCEPTED
    assert latest["appeal"]["new_score"] == 6
    assert "comment_internal" not in submissions.content.decode()


def test_score_outside_scale_is_rejected(client, open_stage):
    """9. ``new_score`` spoza skali → 400 SCORE_NOT_IN_SCALE."""
    submission = graded_submission(open_stage, score=2)
    assert file_appeal_via_api(client, submission).status_code == 201
    appeal = Appeal.objects.get(submission=submission)
    client.force_authenticate(AppealsCommitteeMemberFactory().user)

    response = client.post(
        decide_url(appeal),
        {"status": AppealStatus.ACCEPTED, "new_score": 4, "justification": "Poza skalą."},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "SCORE_NOT_IN_SCALE"
    assert FinalGrade.objects.get(submission=submission).score == 2
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.APPEALED


def test_reviewer_without_appeals_role_cannot_use_committee_endpoints(client, open_stage):
    """Sama rola recenzenta nie otwiera kolejki komisji odwoławczej (jawne permission_classes)."""
    submission = graded_submission(open_stage)
    assert file_appeal_via_api(client, submission).status_code == 201
    client.force_authenticate(ActiveReviewerFactory().user)

    assert client.get(APPEALS_URL).status_code == 403


@pytest.fixture
def stored_appeal(client, open_stage):
    """Reklamacja na rozwiązanie z realnym, czystym plikiem w storage testowym."""
    submission = graded_submission(open_stage)
    submission_file = SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN)
    get_submission_storage().put(submission_file.object_key, BytesIO(PDF_BYTES), "application/pdf")
    assert file_appeal_via_api(client, submission).status_code == 201
    return Appeal.objects.get(submission=submission)


def test_committee_downloads_appealed_submission(client, stored_appeal):
    """Komisja pobiera pracę, którą rozpatruje – ``download_url`` z kolejki musi działać."""
    client.force_authenticate(AppealsCommitteeMemberFactory().user)

    response = client.get(f"/api/submissions/{stored_appeal.submission_id}/download/")

    assert response.status_code in (200, 302)
    if response.status_code == 200:
        assert b"".join(response.streaming_content) == PDF_BYTES


def test_conflicted_member_keeps_reviewer_access_but_not_the_appeal(client, stored_appeal):
    """Konflikt interesów odcina reklamację, a nie własny przydział recenzencki.

    Autor recenzji widział tę pracę już w T-05 i nadal ma do niej prawo jako recenzent – ukrywanie
    pliku niczego by nie chroniło. Reguła konfliktu dotyczy reklamacji: nie ma jej w kolejce komisji
    i nie da się jej rozstrzygnąć (kryterium 6).
    """
    conflicted = AppealsCommitteeMemberFactory()
    ReviewFactory(submission=stored_appeal.submission, reviewer=conflicted, round=ROUND_BLIND, score=2)
    client.force_authenticate(conflicted.user)

    assert client.get(f"/api/submissions/{stored_appeal.submission_id}/download/").status_code in (200, 302)
    assert client.get(APPEALS_URL).data == []


def test_committee_cannot_download_submission_without_appeal(client, open_stage):
    """Bez reklamacji rozwiązanie jest dla komisji odwoławczej niewidoczne (404)."""
    submission = graded_submission(open_stage)
    client.force_authenticate(AppealsCommitteeMemberFactory().user)

    assert client.get(f"/api/submissions/{submission.pk}/download/").status_code == 404
