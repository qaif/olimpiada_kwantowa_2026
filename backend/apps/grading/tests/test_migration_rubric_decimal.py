"""Migracje „ułamki w rubrykach” bez utraty danych: maksimum kryterium i punkty sugestii AI.

``grading.0012`` przenosi ``RubricCriterion.max_points`` z ``smallint`` na ``numeric(7, 2)``,
a ``ai_grading.0003`` poszerza ``AiAssessment.proposed_points``/``max_points`` z ``numeric(6, 2)``
do ``numeric(7, 2)`` – tej samej precyzji, co maksimum zadania (uwaga integratora 0.35.0).
Scenariusz jak w ``test_migration_decimal_scores``: dane zakładamy na czole, cofamy obie migracje
(kolumny wracają do starego typu z tymi samymi liczbami), przewijamy w przód i czytamy SQL-em.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.ai_grading.models import AiAssessment, AiAssessmentStatus
from apps.competitions.tests.factories import ProblemFactory, ScoringScaleFactory, StageFactory
from apps.core.tests.migration_helpers import migrate_to, migrate_to_head
from apps.grading.models import RubricCriterion
from apps.submissions.tests.factories import SubmissionFactory

from .test_migration_decimal_scores import column, value

BEFORE = [
    ("grading", "0011_decimal_scores"),
    ("ai_grading", "0002_providers"),
]
AFTER = [
    ("grading", "0012_rubric_decimal_points"),
    ("ai_grading", "0003_points_precision"),
]


@pytest.mark.django_db(transaction=True)
def test_migracja_przenosi_maksima_kryteriow_i_punkty_ai_co_do_wartosci(competition):
    stage = StageFactory()
    ScoringScaleFactory(stage=stage)
    problem = ProblemFactory(stage=stage)
    criterion = RubricCriterion.objects.create(problem=problem, order=1, title="Pomysł", max_points=4)
    submission = SubmissionFactory(problem=problem, entry__stage=stage)
    assessment = AiAssessment.objects.create(
        competition=competition,
        submission=submission,
        status=AiAssessmentStatus.DONE,
        proposed_points=Decimal("4.5"),
        max_points=Decimal("6"),
        finished_at=timezone.now(),
    )

    try:
        migrate_to(BEFORE)
        assert column("grading_rubriccriterion", "max_points")[0] == "smallint"
        assert value("grading_rubriccriterion", "max_points", criterion.pk) == 4
        assert column("ai_grading_aiassessment", "proposed_points") == ("numeric", 6, 2)

        migrate_to(AFTER)

        assert column("grading_rubriccriterion", "max_points") == ("numeric", 7, 2)
        assert value("grading_rubriccriterion", "max_points", criterion.pk) == Decimal("4.00")
        assert column("ai_grading_aiassessment", "proposed_points") == ("numeric", 7, 2)
        assert column("ai_grading_aiassessment", "max_points") == ("numeric", 7, 2)
        assert value("ai_grading_aiassessment", "proposed_points", assessment.pk) == Decimal("4.50")
        assert value("ai_grading_aiassessment", "max_points", assessment.pk) == Decimal("6.00")

        # Więz „maksimum dodatnie” stoi po zmianie typu – już jako ``> 0``: 0,5 przechodzi, 0 nie.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE grading_rubriccriterion SET max_points = 0.5 WHERE id = %s", [criterion.pk]
            )
        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE grading_rubriccriterion SET max_points = 0 WHERE id = %s", [criterion.pk])
        # Suma cyfr (7, 2): 12 345,5 – maksimum zadania, które ``numeric(6, 2)`` by odrzuciło.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE ai_grading_aiassessment SET max_points = 12345.5 WHERE id = %s", [assessment.pk]
            )
    finally:
        migrate_to_head()
