"""Punkty sugestii AI po wydaniu 0.35.0: precyzja kolumn i postać w eksporcie danych uczestnika.

Dwie uwagi integratora 0.35.0: kolumny ``AiAssessment.proposed_points``/``max_points`` były
``numeric(6, 2)`` przy maksimum zadania ``numeric(7, 2)``, a eksport danych (art. 15 i 20 RODO)
pisał punkty tekstem z kolumny („6.00”) – obok ``suma_punktow`` zapisanej już liczbą JSON.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.ai_grading import services
from apps.ai_grading.models import AiAssessment, AiAssessmentStatus, AiStageVisibility
from apps.ai_grading.prompt import ProblemMaterials, scale_text
from apps.results.models import ResultsPublication

from .conftest import enable_ai, make_submission

pytestmark = pytest.mark.django_db


def done(competition, submission, *, proposed, maximum):
    return AiAssessment.objects.create(
        competition=competition,
        submission=submission,
        status=AiAssessmentStatus.DONE,
        proposed_points=proposed,
        max_points=maximum,
        summary="Suma kontrolna",
        sent_at=timezone.now(),
        finished_at=timezone.now(),
    )


def test_kolumny_mieszcza_maksimum_zadania_numeric_7_2(competition, problem):
    submission = make_submission(problem)

    row = done(competition, submission, proposed=Decimal("12345.5"), maximum=Decimal("99999.99"))

    row.refresh_from_db()
    assert row.proposed_points == Decimal("12345.50")
    assert row.max_points == Decimal("99999.99")


@pytest.mark.parametrize(
    ("proposed", "maximum", "expected"),
    [(Decimal("6.00"), Decimal("6.00"), (6, 6)), (Decimal("4.50"), Decimal("12.50"), (4.5, 12.5))],
)
def test_eksport_danych_pisze_punkty_liczba_json(competition, stage, problem, proposed, maximum, expected):
    enable_ai(competition)
    submission = make_submission(problem)
    done(competition, submission, proposed=proposed, maximum=maximum)
    AiStageVisibility.objects.create(stage=stage, show_to_participants=True)
    ResultsPublication.objects.create(stage=stage)

    [item] = services.export_section(submission.entry.participant)

    content = item["tresc"]
    assert (content["proponowane_punkty"], content["maksimum"]) == expected
    # Liczba całkowita jako ``int`` (jak ``suma_punktow`` od 0.35.0), a nie „6.00” ani 6.0.
    assert all(type(value) is type(expected[0]) for value in expected)
    assert '"maksimum": ' + json.dumps(expected[1]) in json.dumps(content)


def test_prompt_podaje_maksimum_kryterium_bez_zbednych_zer():
    materials = ProblemMaterials(
        number=1,
        title="Oscylator",
        statement_pdf=b"",
        model_solution_pdf=b"",
        reviewer_notes="",
        scale_items=[],
        max_points=Decimal("6"),
        rubric=[
            {"title": "Pomysł", "description": "", "max_points": Decimal("2.50")},
            {"title": "Rachunki", "description": "", "max_points": Decimal("4.00")},
        ],
        free_values=True,
    )

    text = scale_text(materials)

    assert "Pomysł (maks. 2.5 pkt)" in text
    assert "Rachunki (maks. 4 pkt)" in text
