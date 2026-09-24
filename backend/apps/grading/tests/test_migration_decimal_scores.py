"""Migracje wydania 0.35.0: kolumny punktów z liczb całkowitych na ``numeric(p, 2)`` bez utraty danych.

Scenariusz odtwarza produkcję: oceny wystawione przed wdrożeniem leżą w kolumnach całkowitych,
a migracja ma je przenieść **co do wartości** (5 → 5.00) i założyć więzy „nie mniej niż zero”
w miejsce tych, które dawał typ ``Positive*``. Dane zakładamy na czole (fabrykami), cofamy trzy
migracje – wtedy kolumny są znów całkowite i trzymają te same liczby – i sprawdzamy przejście w przód
zapytaniami SQL, bez modeli, bo model bieżący zna już tylko typ dziesiętny.

Tak jak w ``competitions/tests/test_pipeline_migration.py``: przewijanie to DDL po DML, więc test jest
transakcyjny, a czoło migracji wraca w ``finally``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from apps.competitions.tests.factories import (
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.core.tests.migration_helpers import migrate_to, migrate_to_head
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

BEFORE = [
    ("grading", "0010_reviewer_roles"),
    ("appeals", "0002_appeal_decision_committee_through"),
    ("competitions", "0031_interview_score"),
]
AFTER = [
    ("grading", "0011_decimal_scores"),
    ("appeals", "0003_decimal_new_score"),
    ("competitions", "0032_free_scores"),
]


def column(table: str, name: str) -> tuple[str, int | None, int | None]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT data_type, numeric_precision, numeric_scale FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            [table, name],
        )
        return cursor.fetchone()


def value(table: str, name: str, pk: int):
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT "{name}" FROM "{table}" WHERE id = %s', [pk])  # noqa: S608 - stałe nazwy
        return cursor.fetchone()[0]


@pytest.mark.django_db(transaction=True)
def test_migracja_przenosi_oceny_calkowite_co_do_wartosci(competition):  # noqa: ARG001 - Konkurs #1
    stage = StageFactory()
    ScoringScaleFactory(stage=stage)
    rule = QualificationRuleFactory(stage=stage, min_points=40)
    problem = ProblemFactory(
        stage=stage, scoring_values=[{"value": 0, "label": "a"}, {"value": 7, "label": "b"}], max_points=7
    )
    entry = StageEntryFactory(stage=stage, total_points=11)
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.GRADED_PROVISIONAL)
    review = ReviewFactory(submission=submission, status=ReviewStatus.SUBMITTED, score=5)
    grade = FinalGradeFactory(submission=submission, score=6)

    try:
        migrate_to(BEFORE)
        assert column("grading_review", "score")[0] == "smallint"
        assert value("grading_review", "score", review.pk) == 5

        migrate_to(AFTER)

        assert column("grading_review", "score") == ("numeric", 7, 2)
        assert column("grading_finalgrade", "score") == ("numeric", 7, 2)
        assert column("competitions_stageentry", "total_points") == ("numeric", 10, 2)
        assert column("competitions_qualificationrule", "min_points") == ("numeric", 10, 2)
        assert column("competitions_problem", "max_points") == ("numeric", 7, 2)
        assert column("appeals_appealdecision", "new_score") == ("numeric", 7, 2)
        assert value("grading_review", "score", review.pk) == Decimal("5.00")
        assert value("grading_finalgrade", "score", grade.pk) == Decimal("6.00")
        assert value("competitions_stageentry", "total_points", entry.pk) == Decimal("11.00")
        assert value("competitions_qualificationrule", "min_points", rule.pk) == Decimal("40.00")
        assert value("competitions_problem", "max_points", problem.pk) == Decimal("7.00")
        assert value("competitions_scoringscale", "free_values", stage.scoring_scale.pk) is False

        # Więz „nie mniej niż zero” stoi po zmianie typu tak, jak stał dzięki ``Positive*``.
        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE grading_review SET score = -1 WHERE id = %s", [review.pk])
    finally:
        migrate_to_head()
