"""Nazwane role recenzenckie etapu (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.7).

Cztery rzeczy, których pilnuje ten plik, w kolejności wagi:

- **Etap bez ról zachowuje się dokładnie jak przed etapem 2.** Konkurs #1 nie włącza flagi
  ``reviewer_roles``, żadna migracja nie wpisuje mu ról, więc ``assign_reviewers`` czyta liczbę
  z argumentu, recenzje powstają z ``role = NULL``, a zgodność ocen liczy się ze wszystkich
  nieanulowanych recenzji rundy 1.
- **Rola jest etykietą nad rundą, a nie zamiast niej.** ``Review.round`` zostaje autorytatywne:
  praca z rolami idzie tą samą maszyną stanów, co praca bez nich – zgodne oceny dają
  ``FinalGrade(CONSENSUS)``, różne dają moderację.
- **Etap z rolami sam mówi, ilu ma recenzentów.** Suma ``ReviewerRole.count`` wygrywa
  z ``per_submission``, bo liczba wpisana w formularzu przydziału byłaby drugą, sprzeczną
  deklaracją tej samej rzeczy.
- **Rola spoza zgodności nie unieważnia zgodności.** Opinia przewodniczącego komisji obok dwóch
  zgodnych recenzji nie robi z pracy rozjazdu – ani wtedy, gdy się różni, ani wtedy, gdy jej nie ma.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.grading.models import ROUND_BLIND, FinalGrade, GradeMethod, Review, ReviewerRole, ReviewStatus
from apps.grading.services import assign_reviewers, stage_reviewer_roles, submit_review
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import locked_submission

pytestmark = pytest.mark.django_db

FEATURE = "reviewer_roles"


def enable_roles(competition):
    """Włącza flagę obszaru tą samą drogą, którą zrobi to wdrożenie: wartością w bazie."""
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def role(stage, code: str, *, name="", count=1, consensus=True, position=0) -> ReviewerRole:
    return ReviewerRole.objects.create(
        stage=stage,
        code=code,
        name=name or code,
        count=count,
        counts_towards_consensus=consensus,
        position=position,
    )


def reviewers(count: int) -> list:
    return [ActiveReviewerFactory(district="") for _ in range(count)]


# --- etap bez ról ------------------------------------------------------------------------------


def test_etap_bez_rol_nie_zna_zadnej(stage, competition):
    enable_roles(competition)

    assert stage_reviewer_roles(stage) == []


def test_bez_flagi_role_sa_niewidoczne(stage):
    """Wiersze mogą istnieć (np. po wyłączeniu flagi), a i tak nie rządzą przydziałem."""
    role(stage, "first")

    assert stage_reviewer_roles(stage) == []


def test_bez_rol_przydzial_czyta_argument_i_nie_wpisuje_roli(stage):
    reviewers(3)
    submission = locked_submission(stage)

    assign_reviewers(stage, per_submission=2)

    assigned = list(Review.objects.filter(submission=submission))
    assert len(assigned) == 2
    assert {review.role_id for review in assigned} == {None}


# --- etap z rolami -----------------------------------------------------------------------------


def test_liczba_recenzentow_bierze_sie_z_rol(stage, competition):
    """Trzy miejsca z ról wygrywają z dwoma z argumentu – etap wie o sobie więcej niż formularz."""
    enable_roles(competition)
    role(stage, "first", count=2, position=1)
    role(stage, "arbiter", count=1, position=2)
    reviewers(4)
    submission = locked_submission(stage)

    assign_reviewers(stage, per_submission=2)

    assert Review.objects.filter(submission=submission).count() == 3


def test_role_obsadzaja_sie_w_kolejnosci(stage, competition):
    enable_roles(competition)
    first = role(stage, "first", count=1, position=1)
    second = role(stage, "second", count=1, position=2)
    reviewers(2)
    submission = locked_submission(stage)

    assign_reviewers(stage)

    codes = [
        review.role.code
        for review in Review.objects.filter(submission=submission).select_related("role").order_by("id")
    ]
    assert codes == [first.code, second.code]


def test_drugi_przebieg_dopelnia_wolne_miejsce(stage, competition):
    """Przydział jest idempotentny, a rola nowej recenzji bierze się z pierwszego wolnego miejsca."""
    enable_roles(competition)
    role(stage, "first", count=1, position=1)
    second = role(stage, "second", count=1, position=2)
    pool = reviewers(2)
    submission = locked_submission(stage)
    Review.objects.create(
        submission=submission,
        reviewer=pool[0],
        round=ROUND_BLIND,
        status=ReviewStatus.ASSIGNED,
        role=ReviewerRole.objects.get(code="first"),
    )

    assign_reviewers(stage)

    added = Review.objects.filter(submission=submission).exclude(reviewer=pool[0]).get()
    assert added.role_id == second.pk


def test_runda_zostaje_autorytatywna(stage, competition):
    """Rola nie zmienia rundy: dwie zgodne oceny rundy 1 nadal dają ocenę uzgodnioną."""
    enable_roles(competition)
    role(stage, "first", count=2)
    reviewers(2)
    submission = locked_submission(stage)
    assign_reviewers(stage)

    for review in Review.objects.filter(submission=submission):
        assert review.round == ROUND_BLIND
        submit_review(review, 5)

    grade = FinalGrade.objects.get(submission=submission)
    assert grade.method == GradeMethod.CONSENSUS
    assert grade.score == 5


# --- zgodność ocen -----------------------------------------------------------------------------


def graded_submission(stage):
    return SubmissionFactory(
        entry=StageEntryFactory(stage=stage),
        problem=ProblemFactory(stage=stage),
        status=SubmissionStatus.IN_REVIEW,
    )


def test_rola_spoza_zgodnosci_nie_psuje_zgodnosci(stage, competition):
    """Przewodniczący komisji z inną oceną: dwie zgodne recenzje nadal dają konsensus."""
    enable_roles(competition)
    counting = role(stage, "first", count=2, position=1)
    chair = role(stage, "chair", count=1, consensus=False, position=2)
    submission = graded_submission(stage)
    people = reviewers(3)
    first = Review.objects.create(submission=submission, reviewer=people[0], role=counting)
    second = Review.objects.create(submission=submission, reviewer=people[1], role=counting)
    opinion = Review.objects.create(submission=submission, reviewer=people[2], role=chair)

    submit_review(opinion, 0)
    submit_review(first, 5)
    submit_review(second, 5)

    grade = FinalGrade.objects.get(submission=submission)
    assert grade.score == 5
    assert grade.method == GradeMethod.CONSENSUS


def test_nieoddana_opinia_spoza_zgodnosci_nie_wstrzymuje_oceny(stage, competition):
    enable_roles(competition)
    counting = role(stage, "first", count=2, position=1)
    chair = role(stage, "chair", count=1, consensus=False, position=2)
    submission = graded_submission(stage)
    people = reviewers(3)
    first = Review.objects.create(submission=submission, reviewer=people[0], role=counting)
    second = Review.objects.create(submission=submission, reviewer=people[1], role=counting)
    Review.objects.create(submission=submission, reviewer=people[2], role=chair)

    submit_review(first, 2)
    submit_review(second, 2)

    assert FinalGrade.objects.filter(submission=submission).exists()


def test_rozjazd_ocen_liczacych_sie_nadal_daje_moderacje(stage, competition):
    enable_roles(competition)
    counting = role(stage, "first", count=2)
    submission = graded_submission(stage)
    people = reviewers(2)
    first = Review.objects.create(submission=submission, reviewer=people[0], role=counting)
    second = Review.objects.create(submission=submission, reviewer=people[1], role=counting)

    submit_review(first, 2)
    submit_review(second, 5)

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.MODERATION
    assert not FinalGrade.objects.filter(submission=submission).exists()


# --- więzy -------------------------------------------------------------------------------------


def test_kod_roli_jest_unikalny_w_etapie(stage):
    role(stage, "first")

    with pytest.raises(IntegrityError), transaction.atomic():
        ReviewerRole.objects.create(stage=stage, code="first", name="Pierwszy raz jeszcze")


def test_rola_bez_ani_jednego_recenzenta_odpada(stage):
    with pytest.raises(IntegrityError), transaction.atomic():
        ReviewerRole.objects.create(stage=stage, code="ghost", name="Widmo", count=0)
