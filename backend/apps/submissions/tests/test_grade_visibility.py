"""Oceny w API uczestnika dopiero po ogłoszeniu wyników etapu (v0.38.7).

Decyzja właściciela platformy: „na razie uczeń widzi oceny dopiero po ostatecznym zatwierdzeniu”,
czyli po publikacji wyników etapu (``Stage.results_published_at`` – ten sam sygnał, co zakładka
„Wyniki” panelu). Panel HTML trzymał tę regułę od zawsze; te testy pilnują dwóch dróg API, które
ją omijały:

- ``GET /api/me/submissions/`` – ``final_grade.score``/``method``/``decided_at`` od chwili powstania
  ``FinalGrade`` i ``appeal.new_score`` od chwili decyzji komisji,
- ``GET /api/competitions/me/entries/`` – ``total_points`` zapisywane już przez **podgląd** wyników
  koordynatora.

Każdy przypadek ma parę: odmowę przed publikacją i liczbę po niej – sama odmowa przeszłaby też
wtedy, gdyby API nie oddawało punktów nigdy.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tests.factories import ParticipantFactory
from apps.appeals.models import AppealStatus
from apps.appeals.services import decide_appeal, file_appeal
from apps.appeals.tests.conftest import build_stage, graded_submission
from apps.appeals.tests.factories import VALID_ARGUMENT, AppealsCommitteeMemberFactory
from apps.competitions.models import TRAINING_DEADLINE, Stage, StageEntry, StageKind
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.grading.models import FinalGrade, GradeMethod
from apps.grading.tests.factories import FinalGradeFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.serializers import PARTICIPANT_GRADE_METHOD_REVIEW
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db

MY_SUBMISSIONS_URL = "/api/me/submissions/"
MY_ENTRIES_URL = "/api/competitions/me/entries/"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def open_stage():
    """Etap z otwartym oknem reklamacji – czyli w chwili, w której wyniki **nie mogą** być ogłoszone."""
    return build_stage(opens_in=-timedelta(hours=1), closes_in=timedelta(days=7))


def publish(stage: Stage) -> None:
    """Znacznik ogłoszenia wyników – to, co nakłada ``publish_results`` i co zdejmuje wycofanie."""
    Stage.objects.filter(pk=stage.pk).update(results_published_at=timezone.now())


def latest_of(response) -> dict:
    assert response.status_code == 200, response.data
    return response.data[0]["latest"]


# --- GET /api/me/submissions/ ---------------------------------------------------------------------


@pytest.mark.parametrize("method", [GradeMethod.CONSENSUS, GradeMethod.THIRD_REVIEW, GradeMethod.MODERATION])
def test_ocena_przed_ogloszeniem_nie_ma_punktow_ani_trybu_wewnetrznego(client, open_stage, method):
    """Przed publikacją klucze są, wartości – nie. Tryb jest neutralny także wtedy, gdy liczby nie ma."""
    submission = graded_submission(open_stage, score=5)
    FinalGrade.objects.filter(submission=submission).update(method=method)
    client.force_authenticate(submission.entry.participant.user)

    grade = latest_of(client.get(MY_SUBMISSIONS_URL))["final_grade"]

    assert set(grade) == {"score", "method", "decided_at", "rationale"}
    assert grade == {
        "score": None,
        "method": PARTICIPANT_GRADE_METHOD_REVIEW,
        "decided_at": None,
        "rationale": None,
    }


@pytest.mark.parametrize("method", [GradeMethod.CONSENSUS, GradeMethod.THIRD_REVIEW, GradeMethod.MODERATION])
def test_ocena_po_ogloszeniu_ma_punkty_ale_dalej_nie_zdradza_rozbieznosci(client, open_stage, method):
    submission = graded_submission(open_stage, score=5)
    FinalGrade.objects.filter(submission=submission).update(method=method)
    publish(open_stage)
    client.force_authenticate(submission.entry.participant.user)

    grade = latest_of(client.get(MY_SUBMISSIONS_URL))["final_grade"]

    assert grade["score"] == 5
    assert grade["decided_at"] is not None
    assert grade["method"] == PARTICIPANT_GRADE_METHOD_REVIEW
    assert method not in str(grade)


def test_reklamacja_przed_ogloszeniem_pokazuje_status_i_uzasadnienie_ale_nie_nowe_punkty(client, open_stage):
    """API mówi to samo, co zakładka „Reklamacje” panelu: status i uzasadnienie komisji, bez liczby."""
    submission = graded_submission(open_stage, score=2)
    appeal = file_appeal(submission.entry.participant.user, submission, VALID_ARGUMENT)
    decide_appeal(appeal, AppealsCommitteeMemberFactory(), AppealStatus.ACCEPTED, 6, "Zarzut zasadny.")
    client.force_authenticate(submission.entry.participant.user)

    latest = latest_of(client.get(MY_SUBMISSIONS_URL))

    assert latest["appeal"]["status"] == AppealStatus.ACCEPTED
    assert latest["appeal"]["justification"] == "Zarzut zasadny."
    assert latest["appeal"]["new_score"] is None
    assert latest["final_grade"]["score"] is None
    # ``APPEAL`` zostaje rozpoznawalny: przy nim ``rationale`` to tekst pisany do uczestnika.
    assert latest["final_grade"]["method"] == GradeMethod.APPEAL
    assert latest["final_grade"]["rationale"] == "Zarzut zasadny."


def test_reklamacja_po_ogloszeniu_pokazuje_nowe_punkty(client, open_stage):
    submission = graded_submission(open_stage, score=2)
    appeal = file_appeal(submission.entry.participant.user, submission, VALID_ARGUMENT)
    decide_appeal(appeal, AppealsCommitteeMemberFactory(), AppealStatus.ACCEPTED, 6, "Zarzut zasadny.")
    publish(open_stage)
    client.force_authenticate(submission.entry.participant.user)

    latest = latest_of(client.get(MY_SUBMISSIONS_URL))

    assert latest["appeal"]["new_score"] == 6
    assert (latest["final_grade"]["score"], latest["final_grade"]["method"]) == (6, GradeMethod.APPEAL)


def test_etap_treningowy_nie_ma_wyjatku(client):
    """Panel nie robi wyjątku dla treningu (wyniki też dopiero po publikacji), więc API też nie."""
    stage = StageFactory(
        kind=StageKind.TRAINING,
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE + timedelta(days=14),
        appeal_window_opens_at=TRAINING_DEADLINE + timedelta(days=16),
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=23),
    )
    submission = graded_submission(stage, score=5)
    client.force_authenticate(submission.entry.participant.user)
    assert latest_of(client.get(MY_SUBMISSIONS_URL))["final_grade"]["score"] is None
    publish(stage)
    assert latest_of(client.get(MY_SUBMISSIONS_URL))["final_grade"]["score"] == 5


def test_wycofanie_ogloszenia_znow_chowa_punkty(client, open_stage):
    """Bramką jest znacznik na etapie, a nie sam rekord publikacji – koordynator zdejmuje znacznik."""
    submission = graded_submission(open_stage, score=5)
    publish(open_stage)
    client.force_authenticate(submission.entry.participant.user)
    assert latest_of(client.get(MY_SUBMISSIONS_URL))["final_grade"]["score"] == 5
    Stage.objects.filter(pk=open_stage.pk).update(results_published_at=None)
    assert latest_of(client.get(MY_SUBMISSIONS_URL))["final_grade"]["score"] is None


def test_lista_rozwiazan_nie_robi_zapytania_na_prace(client, open_stage, django_assert_max_num_queries):
    """Bramka czyta etap z ``select_related`` – liczba zapytań nie rośnie z liczbą ocenionych prac."""
    participant = ParticipantFactory()
    entry = StageEntryFactory(stage=open_stage, participant=participant)

    def add_graded() -> None:
        submission = SubmissionFactory(
            entry=entry, problem=ProblemFactory(stage=open_stage), status=SubmissionStatus.GRADED_PROVISIONAL
        )
        FinalGradeFactory(submission=submission, score=2)

    add_graded()
    client.force_authenticate(participant.user)
    with CaptureQueriesContext(connection) as one:
        client.get(MY_SUBMISSIONS_URL)
    for _ in range(3):
        add_graded()
    with django_assert_max_num_queries(len(one.captured_queries)):
        response = client.get(MY_SUBMISSIONS_URL)
    assert len(response.data) == 4


# --- GET /api/competitions/me/entries/ ------------------------------------------------------------


def test_suma_etapu_przed_ogloszeniem_jest_pusta_mimo_podgladu_koordynatora(client):
    """``total_points`` zapisuje już podgląd wyników – API ma go nie oddawać przed publikacją."""
    entry = StageEntryFactory(stage=StageFactory(kind=StageKind.ELIM))
    StageEntry.objects.filter(pk=entry.pk).update(total_points=12)
    client.force_authenticate(entry.participant.user)

    response = client.get(MY_ENTRIES_URL)

    assert response.status_code == 200
    row = response.json()[0]
    assert "total_points" in row
    assert row["total_points"] is None


def test_suma_etapu_po_ogloszeniu_jest_taka_jak_dotad(client):
    entry = StageEntryFactory(stage=StageFactory(kind=StageKind.ELIM))
    StageEntry.objects.filter(pk=entry.pk).update(total_points=12)
    publish(entry.stage)
    client.force_authenticate(entry.participant.user)

    row = client.get(MY_ENTRIES_URL).json()[0]

    assert row["total_points"] is not None
    assert float(row["total_points"]) == 12


def test_wpisy_nie_robia_zapytania_na_etap(client, django_assert_max_num_queries):
    participant = ParticipantFactory()
    StageEntryFactory(stage=StageFactory(kind=StageKind.ELIM), participant=participant)
    client.force_authenticate(participant.user)
    with CaptureQueriesContext(connection) as one:
        client.get(MY_ENTRIES_URL)
    for _ in range(3):
        StageEntryFactory(stage=StageFactory(kind=StageKind.ELIM), participant=participant)
    with django_assert_max_num_queries(len(one.captured_queries)):
        response = client.get(MY_ENTRIES_URL)
    assert len(response.json()) == 4
