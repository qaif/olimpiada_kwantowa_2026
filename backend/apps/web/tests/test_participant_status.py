"""Ścieżka oceniania pracy w karcie zadania: „oddane → w ocenie → oceniona → wyniki”.

Czego pilnują te testy i co łatwo zepsuć przy pierwszej „drobnej poprawce”:

- **ścieżka nigdy nie niesie punktów.** Krok „wyniki” zapala publikacja etapu, a nie liczba –
  gdyby karta zaczęła pokazywać punkty, ominęłaby bramę, którą trzyma ``apps.results``,
- **reklamacja nie cofa pracy o krok.** ``APPEALED`` jest stanem po wystawieniu oceny, więc krok
  „oceniona” zostaje zapalony; cofnięcie go wyglądałoby jak unieważnienie oceny,
- **wersja odrzucona przez antywirusa nie idzie dalej.** Kończy na pierwszym kroku, także wtedy,
  gdy etap ma już ogłoszone wyniki.
"""

import pytest

from apps.competitions.models import TRAINING_DEADLINE, StageKind
from apps.competitions.tests.factories import StageFactory
from apps.grading.tests.factories import FinalGradeFactory
from apps.results.models import ResultsPublication
from apps.submissions.models import SubmissionStatus
from apps.submissions.status_track import (
    STATE_CURRENT,
    STATE_DONE,
    STATE_FAILED,
    STATE_TODO,
    status_track,
)
from apps.submissions.tests.factories import SubmissionFactory, pdf_upload

pytestmark = pytest.mark.django_db


def states(track) -> list[str]:
    return [step.state for step in track.steps]


def track_for(stage, status=None, publication=None):
    submission = None
    if status is not None:
        submission = SubmissionFactory(status=status)
    return status_track(submission=submission, stage=stage, publication=publication)


# --- mapowanie statusu na krok ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (SubmissionStatus.SUBMITTED, [STATE_DONE, STATE_CURRENT, STATE_TODO, STATE_TODO]),
        (SubmissionStatus.SCANNING, [STATE_DONE, STATE_CURRENT, STATE_TODO, STATE_TODO]),
        (SubmissionStatus.LOCKED, [STATE_DONE, STATE_DONE, STATE_CURRENT, STATE_TODO]),
        (SubmissionStatus.IN_REVIEW, [STATE_DONE, STATE_DONE, STATE_CURRENT, STATE_TODO]),
        (SubmissionStatus.MODERATION, [STATE_DONE, STATE_DONE, STATE_CURRENT, STATE_TODO]),
        (SubmissionStatus.GRADED_PROVISIONAL, [STATE_DONE, STATE_DONE, STATE_DONE, STATE_CURRENT]),
        (SubmissionStatus.FINAL, [STATE_DONE, STATE_DONE, STATE_DONE, STATE_CURRENT]),
    ],
)
def test_status_maps_to_expected_step(elim_stage, status, expected):
    assert states(track_for(elim_stage, status)) == expected


def test_appeal_does_not_move_the_track_backwards(elim_stage):
    """Reklamacja składa się **na ocenę**, więc krok „oceniona” musi zostać zapalony."""
    appealed = states(track_for(elim_stage, SubmissionStatus.APPEALED))

    assert appealed == states(track_for(elim_stage, SubmissionStatus.GRADED_PROVISIONAL))


def test_no_submission_leaves_the_first_step_as_current(elim_stage):
    assert states(track_for(elim_stage)) == [STATE_CURRENT, STATE_TODO, STATE_TODO, STATE_TODO]


def test_infected_version_stops_on_the_first_step(elim_stage):
    track = track_for(elim_stage, SubmissionStatus.REJECTED_INFECTED)

    assert states(track) == [STATE_FAILED, STATE_TODO, STATE_TODO, STATE_TODO]
    assert track.rejected is True


def test_published_results_light_the_last_step(elim_stage):
    publication = ResultsPublication.objects.create(stage=elim_stage)

    track = track_for(elim_stage, SubmissionStatus.FINAL, publication)

    assert states(track) == [STATE_DONE] * 4
    assert track.results_published is True
    assert track.results_at == publication.published_at


def test_publication_does_not_rescue_an_infected_version(elim_stage):
    """Praca odrzucona przez antywirusa nigdy nie weszła do oceniania – „wyniki” nie są jej krokiem."""
    publication = ResultsPublication.objects.create(stage=elim_stage)

    track = track_for(elim_stage, SubmissionStatus.REJECTED_INFECTED, publication)

    assert states(track) == [STATE_FAILED, STATE_TODO, STATE_TODO, STATE_TODO]


# --- termin ogłoszenia wyników ------------------------------------------------------------------


def test_announced_date_before_publication_is_the_appeal_window_opening(elim_stage):
    """Etap nie ma pola „planowane wyniki”; zapowiedzią jest pierwszy moment, w którym znasz ocenę."""
    track = track_for(elim_stage, SubmissionStatus.SUBMITTED)

    assert track.results_at == elim_stage.appeal_window_opens_at
    assert track.results_published is False


def test_training_stage_has_no_announced_date(edition):
    """Trening ma w bazie datę-wartownik z roku 2099 – ogłoszenie jej byłoby kłamstwem."""
    stage = StageFactory(
        edition=edition,
        kind=StageKind.TRAINING,
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE,
        appeal_window_opens_at=TRAINING_DEADLINE,
    )

    assert track_for(stage, SubmissionStatus.SUBMITTED).results_at is None


# --- karta zadania w panelu ---------------------------------------------------------------------


def test_dashboard_renders_the_track_for_every_problem(web_client, participant, entry, problems):
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert content.count('class="status-track"') == len(problems)
    assert "Status oceniania" in content
    assert "w ocenie" in content
    assert "Ogłoszenie wyników:" in content


def test_htmx_upload_response_carries_the_track(web_client, participant, entry, problems):
    """Karta wracająca po wysyłce ma pokazać krok „oddane” od razu, a nie po przeładowaniu strony."""
    web_client.force_login(participant.user)
    url = f"/me/stages/{entry.stage_id}/problems/1/upload/"

    content = web_client.post(
        url, {"file": pdf_upload(), "confirmed": "1"}, HTTP_HX_REQUEST="true"
    ).content.decode()

    assert 'class="status-track"' in content
    assert "status-track__step--done" in content


def test_track_never_shows_points(web_client, participant, entry, problems):
    """Punkty należą do uczestnika dopiero po publikacji i idą osobną drogą (``/feedback/``)."""
    submission = SubmissionFactory(
        entry=entry, problem=problems[0], status=SubmissionStatus.GRADED_PROVISIONAL
    )
    FinalGradeFactory(submission=submission, score=5)
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert "status-track" in content
    assert "5 pkt" not in content
