"""Wspólne narzędzia testów reklamacji: etap z otwartym oknem i ocenione wstępnie rozwiązanie."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    ProblemFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.grading.models import ROUND_BLIND, ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def build_stage(*, opens_in: timedelta, closes_in: timedelta):
    """Etap ze skalą 0/2/5/6 i oknem reklamacji przesuniętym względem „teraz”.

    Cała oś czasu etapu musi być spójna (constraints w bazie: otwarcie < deadline <= deadline
    recenzji <= otwarcie okna < zamknięcie okna), więc pozostałe znaczniki liczymy wstecz od okna.
    """
    now = timezone.now()
    appeal_opens = now + opens_in
    stage = StageFactory(
        kind=StageKind.ELIM,
        opens_at=appeal_opens - timedelta(days=40),
        deadline_at=appeal_opens - timedelta(days=30),
        review_deadline_at=appeal_opens - timedelta(days=1),
        appeal_window_opens_at=appeal_opens,
        appeal_window_closes_at=now + closes_in,
    )
    ScoringScaleFactory(stage=stage)
    return stage


@pytest.fixture
def open_stage():
    """Etap z oknem reklamacji otwartym w tej chwili (otwarte godzinę temu, zamknięcie za tydzień)."""
    return build_stage(opens_in=-timedelta(hours=1), closes_in=timedelta(days=7))


@pytest.fixture
def stage_before_window():
    """Etap, którego okno reklamacji jeszcze się nie otworzyło."""
    return build_stage(opens_in=timedelta(days=1), closes_in=timedelta(days=8))


@pytest.fixture
def stage_after_window():
    """Etap, którego okno reklamacji już się zamknęło."""
    return build_stage(opens_in=-timedelta(days=8), closes_in=-timedelta(days=1))


def graded_submission(stage, *, score: int = 2, problem=None, participant=None):
    """Rozwiązanie z oceną wstępną: dokładnie taki stan zostawia zamknięcie rundy 1."""
    participant = participant or ParticipantFactory()
    entry = StageEntryFactory(stage=stage, participant=participant)
    submission = SubmissionFactory(
        entry=entry,
        problem=problem or ProblemFactory(stage=stage),
        status=SubmissionStatus.GRADED_PROVISIONAL,
    )
    FinalGradeFactory(submission=submission, score=score)
    return submission


def round_one_reviews(submission, *, score: int = 2, count: int = 2):
    """Komplet ocen rundy 1 – ich autorzy są w konflikcie interesów przy reklamacji."""
    return [
        ReviewFactory(
            submission=submission,
            reviewer=ActiveReviewerFactory(),
            round=ROUND_BLIND,
            score=score,
            status=ReviewStatus.SUBMITTED,
            comment_internal=f"Notatka wewnętrzna recenzenta {index}.",
            submitted_at=timezone.now(),
        )
        for index in range(count)
    ]
