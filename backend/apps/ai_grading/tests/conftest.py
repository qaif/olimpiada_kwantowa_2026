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
#: Fałszywe klucze pozostałych dostawców – każdy z inną końcówką, żeby test wycieku wiedział,
#: którego szuka.
PROVIDER_KEYS = {
    "anthropic": FAKE_KEY,
    "openai": "sk-proj-TESTOPENAITESTOPENAITESTOPENAI-OPN1",
    "google": "AIzaTESTGOOGLETESTGOOGLETESTGOOGLE-GGL2",
    "meta": "LLM|TESTMETATESTMETATESTMETATESTMETA|MTA3",
}

_emails = itertools.count(1)

#: Znacznik treści sugestii – po nim testy szukają wycieku na ekranach uczestnika.
SENTINEL_SUMMARY = "SENTINEL-AI-7f3c: brak uzasadnienia kroku indukcyjnego"


@pytest.fixture(autouse=True)
def clamd(monkeypatch):
    """Skan antywirusowy podmieniony zawsze (w CI nie ma clamd): ``clamd.verdict`` steruje werdyktem.

    Podmiana w module ``antivirus``, bo skan pracy testowej importuje ``scan_stream`` leniwie,
    a w ``submissions.tasks`` – bo tamtędy idzie skan prac uczestników.
    """
    from types import SimpleNamespace

    from apps.submissions.antivirus import VERDICT_CLEAN

    state = SimpleNamespace(verdict=(VERDICT_CLEAN, ""), scanned=[])

    def _scan_stream(fileobj, **kwargs):
        state.scanned.append(fileobj.read())
        return state.verdict

    monkeypatch.setattr("apps.submissions.antivirus.scan_stream", _scan_stream)
    monkeypatch.setattr("apps.submissions.tasks.scan_stream", _scan_stream)
    return state


#: Moduły SDK dostawców (``Provider.sdk_module`` i rodzic ``google``) – patrz ``_sdk_installed``.
SDK_MODULES = frozenset({"anthropic", "openai", "google", "google.genai"})


@pytest.fixture(autouse=True)
def _sdk_installed(monkeypatch):
    """Dostawcy widzą swoje SDK jako zainstalowane – także na maszynie, na której go nie ma.

    Dostępność dostawcy to ``importlib.util.find_spec(sdk_module)`` (``providers.base``), a prawie
    każdy test tego pakietu zakłada dostawcę dostępnego: żaden nie woła prawdziwego API
    (``services.call_model`` i fabryka klienta są podmieniane). Bez tej fikstury wynik ~40 testów
    zależał od tego, czy w środowisku stoją pakiety ``anthropic``/``openai``/``google-genai`` –
    obraz deweloperski zbudowany przed ich dodaniem (24.09.2026) dawał czerwone testy usług, które
    z SDK nie mają nic wspólnego. W CI pakiety są zawsze (``pyproject.toml``).

    Testy, które naprawdę składają obiekty SDK, same się pomijają bez pakietu
    (``pytest.importorskip``); test braku SDK (``test_providers.py``) podmienia ``find_spec``
    jeszcze raz, na tej podmianie.
    """
    import importlib.machinery
    import importlib.util

    real = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        try:
            spec = real(name, *args, **kwargs)
        except ModuleNotFoundError:
            spec = None
        if spec is None and name in SDK_MODULES:
            return importlib.machinery.ModuleSpec(name, loader=None)
        return spec

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)


def enable_ai(competition):
    """Konkurs z włączoną oceną AI – zapisany, bo flagę czyta middleware z bazy."""
    competition.feature_flags = {**(competition.feature_flags or {}), "ai_grading": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def with_key(
    competition, actor=None, *, provider: str = "anthropic", dpa: bool = True, key: str | None = None
):
    """Klucz dostawcy i – domyślnie – potwierdzona umowa powierzenia (bramka prac uczestników).

    Do v0.34.0 klucz był jedynym warunkiem zlecenia; od dodania dostawców drugim jest umowa
    powierzenia. Testy sprzed tej zmiany dostają oba warunki naraz, a testy bramki DPA wołają
    ``with_key(..., dpa=False)``.
    """
    account = services.set_api_key(
        competition, key or PROVIDER_KEYS.get(provider, FAKE_KEY), actor=actor, provider=provider
    )
    if dpa:
        account, _ = services.set_dpa_confirmation(competition, provider, True, actor=actor)
    return account


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
