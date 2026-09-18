"""Ekrany punktacji etapu: wagi, remisy, punkty z rozmowy i role recenzenckie (wydanie J, T34).

Przedmiotem jest to, czego nie widać po kodzie widoków:

- **każdy z tych ekranów jest za inną flagą**, a bez niej adres daje 404, nie 403 (§ 2.1);
  Olimpiada Kwantowa ma po wdrożeniu widzieć dokładnie to, co przed nim,
- **istniejący ekran skali nie zyskuje ani jednego pola** bez flagi ``weighted_scoring`` – to jest
  ten jeden ekran, który § 2.2 pozwala rozszerzyć, i wolno mu się zmienić **tylko** za flagą,
- reguły są w serwisach: skalę oceny rozmowy sprawdza ``record_interview_score``, zgodność
  kryterium remisu z etapem – ``TieBreak.clean()``, a ochronę roli z wystawionymi recenzjami –
  więz ``PROTECT``. Widok tłumaczy odmowę na **kod**, a nie na 302 z komunikatem,
- zmiana zostawia ślad w audycie i nie zostawia go, gdy nic się nie zmieniło.

Adresy są w ``apps/web/urls.py``: montaż wydania J rozwinął tam ``urls_scoring.urlpatterns``,
więc testy chodzą po mapie produkcyjnej. Ścieżki się nie zmieniły.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.models import ComponentKind, InterviewScore, StageComponent, TieBreak, TieBreakKey
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.core.models import AuditLog
from apps.grading.models import Review, ReviewerRole, ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

WEIGHTED = "weighted_scoring"
PROCESS_EDITOR = "process_editor"
REVIEWER_ROLES = "reviewer_roles"


def enable(competition, *names):
    competition.feature_flags = {**(competition.feature_flags or {}), **{name: True for name in names}}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(competition=competition)


@pytest.fixture
def stage(competition, edition):
    """Etap ze skalą 0/2/5/6 i dwoma zadaniami – punktem wyjścia każdego z tych ekranów."""
    stage = StageFactory(competition=competition, edition=edition)
    ScoringScaleFactory(stage=stage)
    ProblemFactory(competition=competition, stage=stage, number=1)
    ProblemFactory(competition=competition, stage=stage, number=2)
    return stage


@pytest.fixture
def coordinator(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def panel(client_for, competition, coordinator):
    client = client_for(competition)
    client.force_login(coordinator)
    return client


def scale_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/scale/"


def weights_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/weights/"


def tie_breaks_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/tie-breaks/"


def interview_scores_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/interview-scores/"


def reviewer_roles_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/reviewer-roles/"


def weight_payload(stage, **overrides) -> dict:
    """Komplet pól formularza wag w stanie „bez zmian”, plus to, co test podmienia."""
    payload = {}
    for problem in stage.problems.order_by("number"):
        prefix = f"p{problem.pk}"
        payload[f"{prefix}-weight_numerator"] = overrides.get(f"{problem.number}n", 1)
        payload[f"{prefix}-weight_denominator"] = overrides.get(f"{problem.number}d", 1)
    return payload


# --- ekran skali: sekcja wag istnieje wyłącznie za flagą -----------------------------------------


def test_the_scale_screen_gains_nothing_without_the_flag(panel, stage):
    """Ten jeden istniejący ekran wolno rozszerzyć – ale tylko za flagą (§ 2.2)."""
    body = panel.get(scale_url(stage)).content.decode()

    assert "Wagi zadań" not in body
    assert "Przesunięcie skali" not in body


def test_the_scale_screen_shows_weights_and_the_offset_with_the_flag(panel, competition, stage):
    enable(competition, WEIGHTED)

    body = panel.get(scale_url(stage)).content.decode()

    assert "Wagi zadań" in body
    assert "Przesunięcie skali" in body
    # Podgląd postaci przechowywanej: przy przesunięciu zero to ta sama skala, co widzi recenzent.
    assert "Wartości zapisane w bazie" in body


# --- wagi zadań ----------------------------------------------------------------------------------


def test_weights_are_a_404_without_the_flag(panel, stage):
    assert panel.post(weights_url(stage), weight_payload(stage)).status_code == 404


def test_weights_are_saved_for_every_problem_at_once(panel, competition, stage):
    enable(competition, WEIGHTED)
    first, second = stage.problems.order_by("number")

    response = panel.post(weights_url(stage), weight_payload(stage, **{"1n": 1, "1d": 3}))

    first.refresh_from_db()
    second.refresh_from_db()
    assert response.status_code == 302
    assert (first.weight_numerator, first.weight_denominator) == (1, 3)
    assert (second.weight_numerator, second.weight_denominator) == (1, 1)
    assert AuditLog.objects.filter(action="problem.weight_updated").count() == 1


def test_saving_unchanged_weights_leaves_no_trace(panel, competition, stage):
    enable(competition, WEIGHTED)

    response = panel.post(weights_url(stage), weight_payload(stage))

    assert response.status_code == 302
    assert AuditLog.objects.filter(action="problem.weight_updated").count() == 0


def test_a_zero_denominator_comes_back_to_the_scale_screen(panel, competition, stage):
    enable(competition, WEIGHTED)
    first = stage.problems.order_by("number").first()

    response = panel.post(weights_url(stage), weight_payload(stage, **{"1d": 0}))

    first.refresh_from_db()
    assert response.status_code == 400
    assert first.weight_denominator == 1
    assert "Wagi zadań" in response.content.decode()


# --- rozstrzyganie remisów -----------------------------------------------------------------------


def test_tie_breaks_are_a_404_without_the_flag(panel, stage):
    assert panel.get(tie_breaks_url(stage)).status_code == 404


def test_a_participant_gets_403_whatever_the_flag(client_for, competition, stage):
    participant = ParticipantFactory(competition=competition)
    client = client_for(competition)
    client.force_login(participant.user)

    assert client.get(tie_breaks_url(stage)).status_code == 403
    enable(competition, WEIGHTED)
    assert client.get(tie_breaks_url(stage)).status_code == 403


def test_a_tie_break_is_added_in_its_place(panel, competition, stage):
    enable(competition, WEIGHTED)

    response = panel.post(
        tie_breaks_url(stage),
        {"key": TieBreakKey.HIGHEST_SINGLE, "descending": "on", "position": 1},
    )

    rule = TieBreak.objects.get(stage=stage)
    assert response.status_code == 302
    assert (rule.key, rule.position, rule.descending) == (TieBreakKey.HIGHEST_SINGLE, 1, True)
    assert AuditLog.objects.filter(action="stage.tie_break_added").count() == 1


def test_a_criterion_without_its_problem_is_refused(panel, competition, stage):
    """„Wynik we wskazanym zadaniu” bez zadania po cichu nie rozstrzygałby niczego."""
    enable(competition, WEIGHTED)

    response = panel.post(tie_breaks_url(stage), {"key": TieBreakKey.PROBLEM_SCORE, "position": 1})

    assert response.status_code == 400
    assert TieBreak.objects.count() == 0


def test_a_problem_from_another_stage_is_refused(panel, competition, edition, stage):
    enable(competition, WEIGHTED)
    other_stage = StageFactory(competition=competition, edition=edition, kind="FINAL")
    foreign = ProblemFactory(competition=competition, stage=other_stage, number=1)

    response = panel.post(
        tie_breaks_url(stage),
        {"key": TieBreakKey.PROBLEM_SCORE, "problem": foreign.pk, "position": 1},
    )

    assert response.status_code == 400
    assert TieBreak.objects.count() == 0


def test_two_criteria_on_the_same_place_are_a_conflict(panel, competition, stage):
    enable(competition, WEIGHTED)
    TieBreak.objects.create(stage=stage, key=TieBreakKey.SOLVED_COUNT, position=1)

    response = panel.post(tie_breaks_url(stage), {"key": TieBreakKey.HIGHEST_SINGLE, "position": 1})

    assert response.status_code == 409
    assert TieBreak.objects.count() == 1


def test_a_tie_break_is_taken_off_the_list(panel, competition, stage):
    enable(competition, WEIGHTED)
    rule = TieBreak.objects.create(stage=stage, key=TieBreakKey.SOLVED_COUNT, position=1)

    response = panel.post(f"/coordinator/tie-breaks/{rule.pk}/delete/")

    assert response.status_code == 302
    assert TieBreak.objects.count() == 0
    assert AuditLog.objects.filter(action="stage.tie_break_removed").count() == 1


# --- punkty z rozmowy ------------------------------------------------------------------------------


@pytest.fixture
def interview(competition, stage):
    """Komponent rozmowy i jeden wpis do etapu – tyle, ile potrzeba, żeby wpisać punkty."""
    component = StageComponent.objects.create(stage=stage, kind=ComponentKind.INTERVIEW, position=1)
    entry = StageEntryFactory(competition=competition, stage=stage)
    return component, entry


def test_interview_scores_are_a_404_without_the_flag(panel, stage, interview):
    assert panel.get(interview_scores_url(stage)).status_code == 404


def test_the_committee_records_a_score(panel, competition, stage, interview):
    enable(competition, PROCESS_EDITOR)
    component, entry = interview

    response = panel.post(
        interview_scores_url(stage),
        {"component": component.pk, "entry": entry.pk, "points": 5, "note": "spokojna obrona"},
    )

    score = InterviewScore.objects.get(entry=entry, component=component)
    assert response.status_code == 302
    assert (score.points, score.max_points) == (5, 6)
    assert AuditLog.objects.filter(action="interview.scored").count() == 1


def test_a_second_record_is_a_correction_not_a_second_result(panel, competition, stage, interview):
    enable(competition, PROCESS_EDITOR)
    component, entry = interview
    payload = {"component": component.pk, "entry": entry.pk, "points": 5, "note": ""}
    panel.post(interview_scores_url(stage), payload)

    panel.post(interview_scores_url(stage), {**payload, "points": 2})

    assert InterviewScore.objects.filter(entry=entry).count() == 1
    assert InterviewScore.objects.get(entry=entry).points == 2


def test_a_score_outside_the_scale_is_refused(panel, competition, stage, interview):
    enable(competition, PROCESS_EDITOR)
    component, entry = interview

    response = panel.post(
        interview_scores_url(stage),
        {"component": component.pk, "entry": entry.pk, "points": 4, "note": ""},
    )

    assert response.status_code == 400
    assert InterviewScore.objects.count() == 0


def test_a_stage_without_an_interview_component_says_so(panel, competition, stage):
    enable(competition, PROCESS_EDITOR)

    response = panel.get(interview_scores_url(stage))

    assert response.status_code == 200
    assert "ani jednego komponentu rozmowy" in response.content.decode()


# --- role recenzenckie -----------------------------------------------------------------------------


def role_payload(**overrides) -> dict:
    payload = {"code": "first", "name": "pierwszy recenzent", "round": 1, "count": 2, "position": 0}
    payload["counts_towards_consensus"] = "on"
    return {**payload, **overrides}


def test_reviewer_roles_are_a_404_without_the_flag(panel, stage):
    assert panel.get(reviewer_roles_url(stage)).status_code == 404


def test_a_role_is_added_to_the_stage(panel, competition, stage):
    enable(competition, REVIEWER_ROLES)

    response = panel.post(reviewer_roles_url(stage), role_payload())

    role = ReviewerRole.objects.get(stage=stage)
    assert response.status_code == 302
    assert (role.code, role.count, role.counts_towards_consensus) == ("first", 2, True)
    assert AuditLog.objects.filter(action="stage.reviewer_role_added").count() == 1


def test_a_repeated_code_is_a_conflict(panel, competition, stage):
    enable(competition, REVIEWER_ROLES)
    ReviewerRole.objects.create(stage=stage, code="first", name="pierwszy", count=1)

    response = panel.post(reviewer_roles_url(stage), role_payload())

    assert response.status_code == 409
    assert ReviewerRole.objects.count() == 1


def test_a_role_is_changed_from_its_row(panel, competition, stage):
    enable(competition, REVIEWER_ROLES)
    role = ReviewerRole.objects.create(stage=stage, code="first", name="pierwszy", count=1)

    response = panel.post(
        f"/coordinator/reviewer-roles/{role.pk}/update/",
        {f"r{role.pk}-{key}": value for key, value in role_payload(count=3).items()},
    )

    role.refresh_from_db()
    assert response.status_code == 302
    assert role.count == 3
    assert AuditLog.objects.filter(action="stage.reviewer_role_updated").count() == 1


def test_a_role_is_withdrawn(panel, competition, stage):
    enable(competition, REVIEWER_ROLES)
    role = ReviewerRole.objects.create(stage=stage, code="first", name="pierwszy", count=1)

    response = panel.post(f"/coordinator/reviewer-roles/{role.pk}/delete/")

    assert response.status_code == 302
    assert ReviewerRole.objects.count() == 0


def test_a_role_with_reviews_cannot_be_withdrawn(panel, competition, stage):
    """Skasowanie roli, na którą ktoś już recenzował, zabrałoby znaczenie wystawionej ocenie."""
    enable(competition, REVIEWER_ROLES)
    role = ReviewerRole.objects.create(stage=stage, code="first", name="pierwszy", count=1)
    ReviewFactory(competition=competition, role=role)

    response = panel.post(f"/coordinator/reviewer-roles/{role.pk}/delete/")

    assert response.status_code == 409
    assert ReviewerRole.objects.count() == 1


def test_a_role_of_another_competition_is_a_404(panel, competition, other_competition):
    enable(competition, REVIEWER_ROLES)
    enable(other_competition, REVIEWER_ROLES)
    foreign_stage = StageFactory(
        competition=other_competition, edition=CurrentEditionFactory(competition=other_competition)
    )
    foreign = ReviewerRole.objects.create(stage=foreign_stage, code="first", name="obca", count=1)

    assert panel.post(f"/coordinator/reviewer-roles/{foreign.pk}/delete/").status_code == 404


# --- etykieta roli w kolejce recenzenta (§ 2.4) ----------------------------------------------------


@pytest.fixture
def assigned_review(entry, problems, reviewer):
    """Jeden przydział w kolejce recenzenta – tyle, ile potrzeba, żeby zobaczyć wiersz."""
    submission = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)
    return ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.ASSIGNED)


def named_role(stage, review):
    """Rola przypięta do istniejącego przydziału – tak, jak zrobiłby to przydział przy włączonej fladze."""
    role = ReviewerRole.objects.create(stage=stage, code="first", name="pierwszy recenzent", count=1)
    Review.objects.filter(pk=review.pk).update(role=role)
    return role


def test_the_queue_shows_the_round_and_no_role_without_the_flag(
    client_for, competition, elim_stage, assigned_review
):
    """Kolejka Olimpiady Kwantowej wygląda dokładnie tak, jak przed wydaniem J."""
    named_role(elim_stage, assigned_review)
    client = client_for(competition)
    client.force_login(assigned_review.reviewer.user)

    assert "pierwszy recenzent" not in client.get("/review/").content.decode()


def test_the_queue_shows_the_role_label_with_the_flag(client_for, competition, elim_stage, assigned_review):
    """Rola jest **etykietą nad rundą**, a nie zamiast niej – numer rundy zostaje na swoim miejscu."""
    enable(competition, REVIEWER_ROLES)
    named_role(elim_stage, assigned_review)
    client = client_for(competition)
    client.force_login(assigned_review.reviewer.user)

    body = client.get("/review/").content.decode()

    assert "pierwszy recenzent" in body
    assert str(assigned_review.round) in body
