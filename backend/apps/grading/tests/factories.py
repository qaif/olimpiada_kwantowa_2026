"""Fabryki factory_boy dla oceniania. Używane wyłącznie w testach."""

import factory
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.grading.models import ROUND_BLIND, FinalGrade, GradeMethod, Review, ReviewStatus
from apps.submissions.tests.factories import SubmissionFactory


class ReviewFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Review

    submission = factory.SubFactory(SubmissionFactory)
    reviewer = factory.SubFactory(ActiveReviewerFactory)
    round = ROUND_BLIND
    status = ReviewStatus.ASSIGNED
    score = None
    assigned_at = factory.LazyFunction(timezone.now)


class FinalGradeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FinalGrade

    submission = factory.SubFactory(SubmissionFactory)
    score = 6
    method = GradeMethod.CONSENSUS
    decided_by = None
    decided_at = factory.LazyFunction(timezone.now)
    rationale = ""
