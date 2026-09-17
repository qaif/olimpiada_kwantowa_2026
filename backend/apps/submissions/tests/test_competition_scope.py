"""T3: praca należy do konkursu – i jest jedynym modelem, który wie o tym z własnej kolumny.

``Submission.competition`` to jedyna denormalizacja całego etapu 1 (§ 3.4). Kolumna powtarza to, co
da się odczytać przez ``entry → stage → edition``, więc ma dwa obowiązki naraz: **być szybka**
(jedno złączenie zamiast trzech w zapytaniach, które chodzą kilkanaście razy na żądanie) i **nigdy
nie kłamać**. Drugi z nich jest tu przedmiotem osobnego testu, bo rozjazd między kolumną a drogą
przez rodzica nie objawia się błędem – objawia się cudzą pracą na liście koordynatora.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.competitions.tests.factories import StageFactory
from apps.competitions.tests.scope_helpers import api_client_factory
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import Submission, SubmissionFile, SubmissionSimilarity
from apps.submissions.tasks import close_due_stages
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_for(settings):
    """Klient DRF pod domeną wskazanego konkursu – reguła stoi w ``scope_helpers``."""
    return api_client_factory(settings)


# --- denormalizacja, która nie ma prawa kłamać --------------------------------------------------


def test_submission_competition_matches_entry(competition, other_competition):
    """Kolumna konkursu pracy zgadza się z drogą przez wpis do etapu – dla **każdej** fabryki.

    To jest test, który dokument wymienia z nazwy (§ 3.4): spójności tej kolumny nie da się wyrazić
    ``CheckConstraint``-em, bo warunek sięga innej tabeli, więc pilnuje jej zapis i ten test.
    """
    for owner in (competition, other_competition):
        submission = SubmissionFactory(competition=owner)

        assert submission.competition_id == owner.pk
        assert submission.entry.stage.edition.competition_id == submission.competition_id


def test_submission_saved_without_an_owner_inherits_it_from_the_entry(other_competition):
    """Praca zapisana wprost (import, stary kod, test) dziedziczy konkurs po swoim wpisie.

    Kontekstu żądania tu **nie** czytamy i to jest istotne: konkurs pracy wynika z etapu, w którym
    ją oddano, a nie z domeny, spod której ktoś ją zapisuje. Kontekst w tym teście wskazuje
    Konkurs #1 (autouse ``_bind_competition``), a praca i tak ląduje w konkursie swojego etapu.
    """
    stage_b = StageFactory(competition=other_competition)
    entry_b = SubmissionFactory(competition=other_competition, entry__stage=stage_b).entry

    written = Submission(entry=entry_b, problem=SubmissionFactory(competition=other_competition).problem)
    written.save()

    assert written.competition_id == other_competition.pk


def test_upload_writes_the_competition_of_the_stage(competition):
    """Jedyna droga zapisu w produkcji (``create_submission``) wpisuje właściciela wprost."""
    from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
    from apps.submissions.services import create_submission
    from apps.submissions.tests.factories import pdf_upload

    stage = StageFactory(competition=competition)
    problem = ProblemFactory(competition=competition, stage=stage)
    entry = StageEntryFactory(competition=competition, stage=stage)

    submission = create_submission(
        user=entry.participant.user, stage=stage, problem_number=problem.number, upload=pdf_upload()
    )

    assert submission.competition_id == competition.pk


# --- managery ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "path"),
    [
        (Submission, "competition"),
        (SubmissionFile, "submission__competition"),
        (SubmissionSimilarity, "stage__edition__competition"),
    ],
)
def test_every_model_declares_its_way_to_the_competition(model, path):
    """Ścieżka jest deklaracją modelu (§ 3.5) i ma zgadzać się z tabelą dróg z dokumentu."""
    assert model.objects.all().competition_path == path


def test_files_of_another_competition_are_invisible(competition, other_competition):
    file_b = SubmissionFileFactory(competition=other_competition)

    assert list(SubmissionFile.objects.for_competition(competition)) == []
    assert list(SubmissionFile.objects.for_competition(other_competition)) == [file_b]


# --- widoczność per rola: zakres idzie przed rolą ------------------------------------------------


def test_coordinator_sees_everything_of_his_own_competition_only(competition, other_competition):
    """Najważniejsza linia całej zmiany (§ 3.5).

    Do etapu 1 gałąź koordynatora zwracała ``self``, czyli **wszystko w instalacji**. Odtąd zwraca
    zakres – rola jest rolą w konkursie, a nie w instalacji.
    """
    coordinator = CoordinatorFactory()
    mine = SubmissionFactory(competition=competition)
    theirs = SubmissionFactory(competition=other_competition)

    visible = Submission.objects.for_user(coordinator, competition)

    assert list(visible) == [mine]
    assert theirs not in visible


def test_reviewer_does_not_see_his_assignment_from_another_competition(competition, other_competition):
    """Recenzent w dwóch komitetach: pod domeną A widzi kolejkę A, i tylko ją.

    Dopóki ``CommitteeMember.user`` jest relacją jeden-do-jednego (wydanie B), ta sama osoba nie ma
    dwóch profili, więc bez zakresu obie kolejki zlałyby się w jedną.
    """
    reviewer = ActiveReviewerFactory(competition=competition)
    mine = ReviewFactory(competition=competition, reviewer=reviewer).submission
    theirs = ReviewFactory(competition=other_competition, reviewer=reviewer).submission

    visible = Submission.objects.for_user(reviewer.user, competition)

    assert list(visible) == [mine]
    assert theirs not in visible


def test_participant_of_another_competition_sees_nothing_here(competition, other_competition):
    """Profil uczestnika jest profilem **w jednym** konkursie (§ 3.3) – i tak samo jego prace."""
    participant_b = ParticipantFactory(competition=other_competition)
    SubmissionFactory(competition=other_competition, entry__participant=participant_b)

    assert list(Submission.objects.for_user(participant_b.user, competition)) == []


# --- reguła krzyżowa w API ----------------------------------------------------------------------


def test_locking_a_submission_of_another_competition_is_not_found(api_for, competition, other_competition):
    """Blokada pracy do oceny jest **zapisem** – tym bardziej nie wolno jej wykonać na cudzej."""
    coordinator = CoordinatorFactory()
    submission_b = SubmissionFactory(competition=other_competition)

    response = api_for(competition, coordinator).post(f"/api/submissions/{submission_b.pk}/lock-for-review/")

    assert response.status_code == 404


def test_downloading_a_submission_of_another_competition_is_not_found(
    api_for, competition, other_competition
):
    """404, nie 403: odpowiedź nie może potwierdzać, że dane zgłoszenie w ogóle istnieje."""
    coordinator = CoordinatorFactory()
    file_b = SubmissionFileFactory(competition=other_competition)

    response = api_for(competition, coordinator).get(f"/api/submissions/{file_b.submission_id}/download/")

    assert response.status_code == 404


def test_uploading_to_a_stage_of_another_competition_is_not_found(api_for, competition, other_competition):
    from apps.submissions.tests.factories import pdf_upload

    participant = ParticipantFactory(competition=competition)
    submission_b = SubmissionFactory(competition=other_competition)
    stage_b, problem_b = submission_b.entry.stage, submission_b.problem

    response = api_for(competition, participant.user).post(
        f"/api/stages/{stage_b.pk}/problems/{problem_b.number}/submissions/",
        {"file": pdf_upload()},
        format="multipart",
    )

    assert response.status_code == 404


# --- przebieg wsadowy ---------------------------------------------------------------------------


def test_closing_due_stages_covers_every_competition(competition, other_competition):
    """Zadanie obchodzi oba konkursy: etap po terminie zamyka się niezależnie od tego, czyj jest.

    Zawężenie per konkurs jest tu po to, żeby zdarzenie ``stage.closed`` i listy, które z niego
    wynikają, powstawały w kontekście właściciela – a nie po to, żeby czegokolwiek nie zamknąć.
    """
    past = timezone.now() - timedelta(days=2)
    stages = []
    for owner in (competition, other_competition):
        # Oś czasu etapu przesuwamy w całości: ``opens_at < deadline_at`` jest constraintem w bazie,
        # więc cofnięcie samego deadline'u nie przeszłoby przez zapis.
        stage = StageFactory(competition=owner, opens_at=past - timedelta(days=1), deadline_at=past)
        stages.append(stage)

    closed = close_due_stages()

    assert sorted(closed) == sorted(stage.pk for stage in stages)
