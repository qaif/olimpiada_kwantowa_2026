"""Informacja zwrotna uczestnika: ``GET /me/stages/<id>/feedback/``.

Trzy rzeczy, których pilnują te testy i których nie wolno „ułatwić”:

- **przed publikacją nie ma strony.** 404, a nie 403 i nie pusta tabela: odpowiedź nie może
  potwierdzać, że wynik jest już policzony i czeka na ogłoszenie,
- **recenzent zostaje anonimowy.** Do uczestnika idzie ``comment_for_participant`` i adnotacje
  z ``public=True``; ani ``comment_internal``, ani nazwisko recenzenta, ani adnotacja prywatna,
- **miejsce i próg pochodzą z ogłoszonej tabeli**, a nie z przeliczenia na żywo – inaczej
  uczestnik widziałby inne miejsce niż publiczność.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.competitions.models import QualificationMode, StageEntryStatus
from apps.competitions.tests.factories import StageEntryFactory
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.results.feedback import participant_feedback
from apps.results.services import publish_results
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import close_stage_timeline

pytestmark = pytest.mark.django_db

INTERNAL_NOTE = "Recenzent uważa, że praca jest przepisana z podręcznika."
PUBLIC_COMMENT = "Dowód poprawny, brakuje uzasadnienia kroku z nierównością."
PRIVATE_ANNOTATION = "tu ewidentnie ściąga"
PUBLIC_ANNOTATION = "ten krok wymaga uzasadnienia"


def url_for(stage) -> str:
    return f"/me/stages/{stage.pk}/feedback/"


def graded(entry, problem, score: int, **review_kwargs):
    """Praca w stanie FINAL z oceną uzgodnioną i (opcjonalnie) jedną oddaną recenzją."""
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.FINAL)
    FinalGradeFactory(submission=submission, score=score)
    if review_kwargs:
        ReviewFactory(submission=submission, status=ReviewStatus.SUBMITTED, score=score, **review_kwargs)
    return submission


def publish(stage, coordinator):
    """Domyka oś czasu etapu i ogłasza wyniki – tak jak zrobiłby to koordynator w panelu."""
    close_stage_timeline(stage)
    stage.refresh_from_db()
    return publish_results(stage, coordinator, "CODE")


# --- brama czasowa ------------------------------------------------------------------------------


def test_feedback_is_404_before_publication(web_client, participant, entry, problems):
    web_client.force_login(participant.user)

    assert web_client.get(url_for(entry.stage)).status_code == 404


def test_feedback_is_404_for_a_stage_the_participant_did_not_enter(
    web_client, participant, elim_stage, problems, coordinator
):
    """Ta sama odpowiedź, co „wyniki nieogłoszone” – rozróżnienie zdradzałoby cudze wpisy."""
    other = StageEntryFactory(stage=elim_stage, participant=ParticipantFactory())
    graded(other, problems[0], 6)
    publish(elim_stage, coordinator)
    web_client.force_login(participant.user)

    assert web_client.get(url_for(elim_stage)).status_code == 404


def test_reviewer_cannot_read_the_participant_page(web_client, reviewer, elim_stage):
    """Ekran jest wyłącznie dla uczestników – obca rola dostaje 403 z ``ParticipantRequiredMixin``."""
    web_client.force_login(reviewer.user)

    assert web_client.get(url_for(elim_stage)).status_code == 403


# --- treść po publikacji -------------------------------------------------------------------------


def test_feedback_shows_score_rank_and_public_comment(web_client, participant, entry, problems, coordinator):
    reviewer = ActiveReviewerFactory()
    graded(
        entry,
        problems[0],
        5,
        reviewer=reviewer,
        comment_for_participant=PUBLIC_COMMENT,
        comment_internal=INTERNAL_NOTE,
        annotations=[
            {"page": 2, "rect": [1.0, 2.0, 3.0, 4.0], "text": PUBLIC_ANNOTATION, "public": True},
            {"page": 3, "rect": [1.0, 2.0, 3.0, 4.0], "text": PRIVATE_ANNOTATION, "public": False},
        ],
    )
    graded(entry, problems[1], 2)
    publish(entry.stage, coordinator)
    web_client.force_login(participant.user)

    content = web_client.get(url_for(entry.stage)).content.decode()

    assert "7 pkt" in content
    assert "Recenzent A" in content
    assert PUBLIC_COMMENT in content
    assert PUBLIC_ANNOTATION in content
    # Anonimowość i tajemnica komitetu – jedno i drugie musi zostać po tamtej stronie.
    assert INTERNAL_NOTE not in content
    assert PRIVATE_ANNOTATION not in content
    assert reviewer.user.last_name not in content


def test_rank_and_threshold_come_from_the_published_table(
    web_client, participant, entry, problems, coordinator
):
    # Próg etapu tworzy fixture ``elim_stage`` (min. 0 pkt) – tu podnosimy go do ośmiu punktów,
    # żeby opis progu na stronie miał co pokazać.
    entry.stage.qualification_rule.mode = QualificationMode.MIN_POINTS
    entry.stage.qualification_rule.min_points = 8
    entry.stage.qualification_rule.save(update_fields=["mode", "min_points"])
    graded(entry, problems[0], 2)
    best = StageEntryFactory(stage=entry.stage, participant=ParticipantFactory())
    graded(best, problems[0], 6)
    graded(best, problems[1], 6)
    publish(entry.stage, coordinator)
    web_client.force_login(participant.user)

    content = web_client.get(url_for(entry.stage)).content.decode()

    # Dwa wpisy: lepszy ma 12 pkt i miejsce 1, uczestnik 2 pkt i miejsce 2.
    assert "2 z 2" in content
    assert "co najmniej 8 pkt" in content


def test_feedback_reports_a_late_committee_decision(participant, entry, problems, coordinator):
    """Tabela jest zamrożona, punkty liczymy na żywo – rozjazd musi być widoczny, a nie zamiatany."""
    submission = graded(entry, problems[0], 2)
    publish(entry.stage, coordinator)
    grade = submission.final_grade
    grade.score = 6
    grade.save(update_fields=["score"])

    feedback = participant_feedback(participant, entry.stage)

    assert feedback.total == 6
    assert feedback.published_total == 2
    assert feedback.differs_from_published is True


def test_reviewers_are_labelled_by_position_not_by_round(participant, entry, problems, coordinator):
    """Podpis „Recenzent A/B” idzie po liście wypisanych recenzji – nie zdradza numeru rundy."""
    submission = graded(entry, problems[0], 5)
    ReviewFactory(
        submission=submission,
        round=2,
        status=ReviewStatus.SUBMITTED,
        score=5,
        comment_for_participant="druga recenzja",
    )
    ReviewFactory(
        submission=submission,
        round=3,
        status=ReviewStatus.SUBMITTED,
        score=5,
        comment_for_participant="trzecia recenzja",
    )
    publish(entry.stage, coordinator)

    feedback = participant_feedback(participant, entry.stage)
    labels = [review.label for review in feedback.problems[0].reviews]

    assert labels == ["Recenzent A", "Recenzent B"]


def test_unsubmitted_review_is_not_shown(participant, entry, problems, coordinator):
    submission = graded(entry, problems[0], 5)
    ReviewFactory(
        submission=submission,
        status=ReviewStatus.ASSIGNED,
        comment_for_participant="szkic, jeszcze nieoddany",
    )
    publish(entry.stage, coordinator)

    feedback = participant_feedback(participant, entry.stage)

    assert feedback.problems[0].reviews == []


def test_qualification_decision_is_carried(participant, entry, problems, coordinator):
    graded(entry, problems[0], 6)
    graded(entry, problems[1], 6)
    publish(entry.stage, coordinator)

    feedback = participant_feedback(participant, entry.stage)

    assert feedback.qualified is True
    assert feedback.entry.status == StageEntryStatus.QUALIFIED
    assert feedback.cutoff_total == 12


# --- odnośniki -----------------------------------------------------------------------------------


def test_public_results_page_links_a_logged_in_participant_to_the_page(
    web_client, participant, entry, problems, coordinator
):
    graded(entry, problems[0], 6)
    publish(entry.stage, coordinator)
    web_client.force_login(participant.user)

    content = web_client.get(f"/results/{entry.stage_id}/").content.decode()

    assert url_for(entry.stage) in content


def test_public_results_page_has_no_such_link_for_anonymous(
    web_client, participant, entry, problems, coordinator
):
    graded(entry, problems[0], 6)
    publish(entry.stage, coordinator)

    content = web_client.get(f"/results/{entry.stage_id}/").content.decode()

    assert url_for(entry.stage) not in content
