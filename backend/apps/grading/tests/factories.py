"""Fabryki factory_boy dla oceniania. Używane wyłącznie w testach.

Recenzja ani ocena końcowa własnego klucza do konkursu nie dostają – droga wiedzie przez pracę
(§ 3.4), a ``ReviewQuerySet.competition_path`` to ``submission__competition``. Argument
``competition`` służy tu więc do propagacji na pracę i na profil recenzenta, żeby
``ReviewFactory(competition=inny)`` dawała recenzję w całości należącą do drugiego konkursu.
"""

import factory
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.grading.models import ROUND_BLIND, FinalGrade, GradeMethod, Review, ReviewStatus
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import SAME_COMPETITION, CompetitionScopedFactory


class ReviewFactory(CompetitionScopedFactory):
    class Meta:
        model = Review

    submission = factory.SubFactory(SubmissionFactory, competition=SAME_COMPETITION)
    reviewer = factory.SubFactory(ActiveReviewerFactory, competition=SAME_COMPETITION)
    round = ROUND_BLIND
    status = ReviewStatus.ASSIGNED
    score = None
    assigned_at = factory.LazyFunction(timezone.now)


class FinalGradeFactory(CompetitionScopedFactory):
    class Meta:
        model = FinalGrade

    submission = factory.SubFactory(SubmissionFactory, competition=SAME_COMPETITION)
    score = 6
    method = GradeMethod.CONSENSUS
    decided_by = None
    decided_at = factory.LazyFunction(timezone.now)
    rationale = ""
