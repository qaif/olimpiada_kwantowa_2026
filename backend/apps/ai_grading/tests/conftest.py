"""Wspólne narzędzia testów oceny AI.

Żaden test nie woła prawdziwego API Anthropic – nie ma tu klucza i nie ma sieci. SDK jest
podmieniane na dwóch poziomach: testy przebiegu oceny podmieniają ``services.call_model``
(gotowy :class:`CallResult` albo :class:`ApiFailure`), a testy klienta – samą fabrykę klienta
SDK (``client._client``), żeby sprawdzić dokładny kształt wywołania ``beta.messages.stream``.
"""

from __future__ import annotations

import io
import itertools
import json
from decimal import Decimal

import pytest
from django.core.files.base import ContentFile

from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.ai_grading import services
from apps.ai_grading.client import CallResult, Usage
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFactory, SubmissionFileFactory

#: Wygląda jak klucz Anthropic, ale nim nie jest. Końcówka ``WXYZ`` jest tym, co wolno pokazać.
FAKE_KEY = "sk-ant-api03-TESTTESTTESTTESTTESTTESTTESTTESTTEST-WXYZ"

_emails = itertools.count(1)

#: Znacznik treści sugestii – po nim testy szukają wycieku na ekranach uczestnika.
SENTINEL_SUMMARY = "SENTINEL-AI-7f3c: brak uzasadnienia kroku indukcyjnego"


def enable_ai(competition):
    """Konkurs z włączoną oceną AI – zapisany, bo flagę czyta middleware z bazy."""
    competition.feature_flags = {**(competition.feature_flags or {}), "ai_grading": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def with_key(competition, actor=None):
    return services.set_api_key(competition, FAKE_KEY, actor=actor)


@pytest.fixture
def stage(competition):
    edition = CurrentEditionFactory(competition=competition)
    created = StageFactory(competition=competition, edition=edition)
    ScoringScaleFactory(competition=competition, stage=created)
    QualificationRuleFactory(competition=competition, stage=created, min_points=0)
    return created


@pytest.fixture
def problem(stage):
    item = ProblemFactory(stage=stage, number=3, title="Oscylator kwantowy")
    item.model_solution_pdf.save("wzorcowka.pdf", ContentFile(PDF_BYTES), save=True)
    item.statement_pdf.save("tresc.pdf", ContentFile(PDF_BYTES), save=True)
    return item


def make_submission(
    problem,
    *,
    participant=None,
    content: bytes = PDF_BYTES,
    mime: str = "application/pdf",
    status=SubmissionStatus.IN_REVIEW,
    clean: bool = True,
    version: int = 1,
    entry=None,
):
    """Praca z plikiem zapisanym w storage'u testowym (``MEDIA_ROOT`` z ``tmp_path``)."""
    if entry is None:
        participant = participant or ParticipantFactory(
            user=UserFactory(
                first_name="Zenobia", last_name="Kwiatkowska", email=f"zenobia.k{next(_emails)}@example.test"
            ),
            school="Liceum Ogólnokształcące nr 7",
        )
        entry = StageEntryFactory(participant=participant, stage=problem.stage)
    submission = SubmissionFactory(entry=entry, problem=problem, status=status, version=version)
    extension = {"application/pdf": "pdf", "image/jpeg": "jpg", "text/x-python": "py"}.get(mime, "ipynb")
    submission_file = SubmissionFileFactory(
        submission=submission,
        mime=mime,
        size_bytes=len(content),
        original_name=f"Kwiatkowska_Zenobia.{extension}",
        av_status=AvStatus.CLEAN if clean else AvStatus.PENDING,
        object_key=f"test/{submission.pk}/{'b' * 64}.{extension}",
    )
    get_submission_storage().put(submission_file.object_key, io.BytesIO(content), mime)
    return submission


def answer(**overrides) -> str:
    data = {
        "proposed_points": 5,
        "max_points": 6,
        "criteria": [
            {"name": "Ułożenie równania", "points": 2, "max": 2, "comment": "Poprawnie."},
            {"name": "Rozwiązanie", "points": 3, "max": 4, "comment": "Brak normalizacji."},
        ],
        "summary": SENTINEL_SUMMARY,
        "errors": ["Nie znormalizowano funkcji falowej."],
        "confidence": "średnia",
        "injection_suspected": False,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def result(text: str | None = None, *, stop_reason: str = "end_turn", **kwargs) -> CallResult:
    return CallResult(
        stop_reason=stop_reason,
        text=answer() if text is None else text,
        model=kwargs.pop("model", "claude-opus-5"),
        request_id=kwargs.pop("request_id", "req_test_123"),
        usage=kwargs.pop(
            "usage",
            Usage(input_tokens=1000, output_tokens=2000, cache_write_tokens=4000, cache_read_tokens=0),
        ),
        refusal_category=kwargs.pop("refusal_category", None),
    )


#: Koszt ``result()`` przy stawkach Opus 5: 1000·5 + 4000·5·1,25 + 2000·25 = 80 000 / 10⁶ USD.
RESULT_COST = Decimal("0.080000")
