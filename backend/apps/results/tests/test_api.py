"""Kryteria 5, 6 i 7 T-07: publiczna tabela ze snapshotu, wyniki uczestnika i uprawnienia."""

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.models import QualificationMode, StageEntryStatus
from apps.grading.models import ROUND_BLIND, FinalGrade, ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.results.models import Anonymization, ResultsPublication
from apps.results.services import publish_results
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import graded_entry, make_stage, stage_problems

pytestmark = pytest.mark.django_db

MY_RESULTS_URL = "/api/me/results/"
INTERNAL_NOTE = "Notatka wewnętrzna: praca podobna do sąsiedniej."
PARTICIPANT_NOTE = "Brakuje uzasadnienia kroku 3."
PUBLIC_ANNOTATION = {"page": 1, "rect": [1.0, 2.0, 3.0, 4.0], "text": "tu jest luka", "public": True}
PRIVATE_ANNOTATION = {"page": 2, "rect": [0.0, 0.0, 1.0, 1.0], "text": "sekret", "public": False}


def public_url(stage) -> str:
    return f"/api/public/results/{stage.pk}/"


def compute_url(stage) -> str:
    return f"/api/stages/{stage.pk}/results/compute/"


def publish_url(stage) -> str:
    return f"/api/stages/{stage.pk}/results/publish/"


# --- kryterium 5: publiczna tabela -------------------------------------------------------------


def test_public_results_require_no_login(client):
    """5. Publiczna tabela jest dostępna bez logowania i pokazuje wyłącznie pseudonimy."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])
    publish_results(stage, None, Anonymization.CODE)

    response = client.get(public_url(stage))

    assert response.status_code == 200, response.data
    assert response.data["anonymization"] == Anonymization.CODE
    assert response.data["rows"][0]["display"] == entry.participant.public_code
    assert response.data["rows"][0]["total"] == 6


def test_public_results_before_publication_are_not_found(client):
    """5. Przed publikacją etap nie istnieje dla publiczności → 404."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])

    response = client.get(public_url(stage))

    assert response.status_code == 404


def test_public_results_come_from_the_snapshot_not_from_live_grades(client):
    """5. Zmiana ``FinalGrade`` po publikacji nie zmienia ogłoszonej tabeli."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])
    publish_results(stage, None, Anonymization.CODE)
    before = client.get(public_url(stage)).data

    grade = FinalGrade.objects.get(submission__entry=entry)
    grade.score = 0
    grade.save(update_fields=["score"])

    after = client.get(public_url(stage)).data
    assert after == before
    assert after["rows"][0]["total"] == 6


# --- kryterium 6: wyniki uczestnika ------------------------------------------------------------


def graded_entry_with_reviews(stage):
    """Wpis uczestnika z oceną i kompletem informacji zwrotnej od recenzenta."""
    entry = graded_entry(stage, [6])
    submission = Submission.objects.get(entry=entry)
    ReviewFactory(
        submission=submission,
        reviewer=ActiveReviewerFactory(),
        round=ROUND_BLIND,
        score=6,
        status=ReviewStatus.SUBMITTED,
        submitted_at=timezone.now(),
        comment_internal=INTERNAL_NOTE,
        comment_for_participant=PARTICIPANT_NOTE,
        annotations=[PUBLIC_ANNOTATION, PRIVATE_ANNOTATION],
    )
    return entry


def test_my_results_hide_unpublished_stages(client):
    """6. Przed publikacją etap nie jest zwracany uczestnikowi."""
    stage = make_stage(problems=1)
    entry = graded_entry_with_reviews(stage)

    client.force_authenticate(entry.participant.user)
    response = client.get(MY_RESULTS_URL)

    assert response.status_code == 200
    assert response.data == []


def test_my_results_after_publication_show_points_and_public_feedback(client):
    """6. Po publikacji: punkty, komentarz dla uczestnika i wyłącznie adnotacje publiczne."""
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=5)
    entry = graded_entry_with_reviews(stage)
    publish_results(stage, None, Anonymization.CODE)

    client.force_authenticate(entry.participant.user)
    response = client.get(MY_RESULTS_URL)

    assert response.status_code == 200
    (result,) = response.data
    assert result["stage_id"] == stage.pk
    assert result["total_points"] == 6
    assert result["status"] == StageEntryStatus.QUALIFIED
    assert result["qualified"] is True
    (problem,) = result["problems"]
    assert problem["score"] == 6
    (feedback,) = problem["feedback"]
    assert feedback["comment_for_participant"] == PARTICIPANT_NOTE
    assert feedback["annotations"] == [PUBLIC_ANNOTATION]
    body = str(response.data)
    assert INTERNAL_NOTE not in body
    assert PRIVATE_ANNOTATION["text"] not in body


def test_my_results_never_show_other_participants(client):
    """6. Uczestnik widzi wyłącznie własny wynik, także gdy etap ma wielu uczestników."""
    stage = make_stage(problems=1)
    mine = graded_entry(stage, [2])
    other = graded_entry(stage, [6])
    publish_results(stage, None, Anonymization.CODE)

    client.force_authenticate(mine.participant.user)
    response = client.get(MY_RESULTS_URL)

    (result,) = response.data
    assert result["total_points"] == 2
    assert other.participant.public_code not in str(response.data)


def test_my_results_require_participant_role(client):
    """6. Konto bez profilu uczestnika nie ma czego tu oglądać → 403."""
    client.force_authenticate(CoordinatorFactory())

    assert client.get(MY_RESULTS_URL).status_code == 403


# --- kryterium 7: uprawnienia koordynatora -----------------------------------------------------


def test_participant_cannot_publish_results(client):
    """7. Uczestnik wywołujący publikację → 403 i żadnej publikacji w bazie."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])

    client.force_authenticate(entry.participant.user)
    response = client.post(publish_url(stage), {"anonymization": Anonymization.CODE}, format="json")

    assert response.status_code == 403
    assert not ResultsPublication.objects.exists()


def test_participant_cannot_compute_results(client):
    """7. Podgląd pełnej tabeli (z nazwiskami) jest wyłącznie dla koordynatora."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])

    client.force_authenticate(entry.participant.user)

    assert client.post(compute_url(stage)).status_code == 403


def test_anonymous_cannot_compute_results(client):
    """7. Bez logowania podgląd tabeli daje 401 (nagłówek WWW-Authenticate), nie 200."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])

    assert client.post(compute_url(stage)).status_code == 401


def test_coordinator_compute_returns_full_table_without_publishing(client):
    """Podgląd koordynatora: dane osobowe i suma punktów, ale bez publikacji i bez kwalifikacji."""
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=5)
    participant = ParticipantFactory(
        user=UserFactory(first_name="Anna", last_name="Wiśniewska"), school="II LO Kraków"
    )
    entry = graded_entry(stage, [6], participant=participant)

    client.force_authenticate(CoordinatorFactory())
    response = client.post(compute_url(stage))

    assert response.status_code == 200, response.data
    assert response.data["count"] == 1
    (row,) = response.data["rows"]
    assert row["last_name"] == "Wiśniewska"
    assert row["public_code"] == participant.public_code
    assert row["total"] == 6
    assert row["rank"] == 1
    entry.refresh_from_db()
    assert entry.total_points == 6
    assert entry.status == StageEntryStatus.REGISTERED
    assert not ResultsPublication.objects.exists()
    stage.refresh_from_db()
    assert stage.results_published_at is None


def test_coordinator_publish_returns_201_with_anonymized_rows(client):
    """Publikacja przez koordynatora → 201, snapshot i znacznik etapu."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])
    coordinator = CoordinatorFactory()

    client.force_authenticate(coordinator)
    response = client.post(publish_url(stage), {"anonymization": Anonymization.CODE}, format="json")

    assert response.status_code == 201, response.data
    assert response.data["rows"][0]["display"] == entry.participant.public_code
    publication = ResultsPublication.objects.get(stage=stage)
    assert publication.published_by_id == coordinator.pk


def test_publish_of_unfinished_stage_returns_409(client):
    """Etap z niedokończonym ocenianiem nie da się opublikować – 409 STAGE_NOT_FINALIZED."""
    stage = make_stage(problems=1)
    (problem,) = stage_problems(stage)
    entry = graded_entry(stage, [None])
    SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.IN_REVIEW)

    client.force_authenticate(CoordinatorFactory())
    response = client.post(publish_url(stage), {"anonymization": Anonymization.CODE}, format="json")

    assert response.status_code == 409
    assert response.data["code"] == "STAGE_NOT_FINALIZED"
    assert not ResultsPublication.objects.exists()
