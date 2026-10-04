"""Wspólne fikstury testów notatników: konkurs z flagą, zadanie, uczestnik, praca ``.ipynb``.

Piaskownica w testach działa w trybie **inline** (podproces workera, ``spool.inline``) z katalogiem
spool w ``tmp_path`` – ta sama ścieżka odbioru wyniku co z kontenera, bez kontenera.
"""

from __future__ import annotations

import io
import itertools
import json

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory
from apps.tenancy.tests.factories import grant_membership

_seq = itertools.count(1)

HIDDEN_TESTS = [
    {"id": "bell", "name": "Stan Bella", "points": 3, "target": "qc", "check": "statevector",
     "expected": {"00": "1/sqrt(2)", "11": "1/sqrt(2)"}},
    {"id": "small", "name": "Głębokość", "points": 1, "target": "qc", "check": "circuit", "max_depth": 2},
]  # fmt: skip
VISIBLE_TESTS = [
    {"id": "probs", "name": "Rozkład", "points": 1, "target": "qc", "check": "probabilities",
     "expected": {"00": 0.5, "11": 0.5}},
]  # fmt: skip
#: Wartość oczekiwana tylko w teście ukrytym – po niej testy szukają wycieku do przeglądarki.
HIDDEN_SENTINEL = "SENTINEL-HIDDEN-0.123456789"


def notebook(*sources: str) -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            {"cell_type": "code", "metadata": {}, "source": source, "outputs": [], "execution_count": None}
            for source in sources
        ],
    }


GOOD = notebook("from qiskit import QuantumCircuit", "qc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)")
WRONG = notebook("from qiskit import QuantumCircuit", "qc = QuantumCircuit(2)\nqc.h(0)\nqc.h(1)\nqc.x(0)")


@pytest.fixture(autouse=True)
def inline_runner(settings, tmp_path):
    settings.NOTEBOOK_RUNNER_INLINE = True
    settings.NOTEBOOK_SPOOL_DIR = str(tmp_path / "spool")
    settings.NOTEBOOK_LAB_DIR = str(tmp_path / "lab")
    return tmp_path


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "quantum_notebooks": True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def stage(competition):
    edition = CurrentEditionFactory(competition=competition)
    return StageFactory(competition=competition, edition=edition)


@pytest.fixture
def problem(stage):
    return ProblemFactory(stage=stage, number=2, title="Stan Bella", allowed_formats=["pdf", "ipynb"])


def coordinator_of(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def coordinator(competition):
    return coordinator_of(competition)


@pytest.fixture
def participant(competition, stage):
    item = ParticipantFactory(
        user=UserFactory(email=f"uczen{next(_seq)}@example.test", groups=["participant"])
    )
    grant_membership(item.user, competition, CompetitionRole.PARTICIPANT)
    StageEntryFactory(participant=item, stage=stage)
    return item


def submit(problem, participant, nb: dict, *, version: int = 1, clean: bool = True):
    from apps.competitions.models import StageEntry

    entry = StageEntry.objects.get(participant=participant, stage=problem.stage)
    submission = SubmissionFactory(
        entry=entry, problem=problem, version=version, status=SubmissionStatus.SUBMITTED
    )
    content = json.dumps(nb).encode()
    item = SubmissionFileFactory(
        submission=submission,
        mime="application/x-ipynb+json",
        size_bytes=len(content),
        original_name="praca.ipynb",
        av_status=AvStatus.CLEAN if clean else AvStatus.PENDING,
        object_key=f"test/{submission.pk}/{next(_seq):064d}.ipynb",
    )
    get_submission_storage().put(item.object_key, io.BytesIO(content), "application/x-ipynb+json")
    return submission
