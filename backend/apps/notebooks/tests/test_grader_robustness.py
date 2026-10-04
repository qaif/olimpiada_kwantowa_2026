"""Ocena odporna na wyniki niekończone (NaN, ±inf), dokładny rozkład przy pomiarze w trakcie
i składnia qclab zgodna z Pythonem w Pyodide (uwagi z przeglądu QC-01: H1, H2, L1)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import qclab
from apps.notebooks.labbuild import build
from qclab import QuantumCircuit, grader
from qclab.grader import SpecError, evaluate, validate_tests
from qclab.simulator import exact_distribution

NAN = float("nan")
INF = float("inf")


@pytest.mark.parametrize(
    ("test", "artifact"),
    [
        (
            {"target": "qc", "check": "statevector", "expected": [1, 0]},
            {"type": "statevector", "data": [[NAN, 0.0], [0.0, 0.0]]},
        ),
        (
            {"target": "qc", "check": "statevector", "expected": [1, 0], "tolerance": 1},
            {"type": "statevector", "data": [[INF, 0.0], [0.0, 0.0]]},
        ),
        (
            {"target": "qc", "check": "statevector", "expected": [1, 0], "tolerance": 1},
            {"type": "value", "data": [NAN, 0]},
        ),
        (
            {"target": "qc", "check": "probabilities", "expected": {"0": 1}, "tolerance": 1},
            {"type": "value", "data": {"0": NAN}},
        ),
        (
            {"target": "qc", "check": "probabilities", "expected": {"0": 1}, "tolerance": 1},
            {"type": "value", "data": {"0": INF}},
        ),
        (
            {"target": "qc", "check": "unitary", "expected": [[1, 0], [0, 1]], "tolerance": 1},
            {"type": "operator", "data": [[[NAN, 0], [0, 0]], [[0, 0], [1, 0]]]},
        ),
        (
            {"target": "qc", "check": "unitary", "expected": [[1, 0], [0, 1]], "tolerance": 1},
            {"type": "value", "data": [[NAN, 0], [0, 1]]},
        ),
        (
            {"target": "x", "check": "value", "expected": 1, "tolerance": 1},
            {"type": "value", "data": NAN},
        ),
        (
            {"target": "x", "check": "value", "expected": [1, 2], "tolerance": 1},
            {"type": "value", "data": [INF, 2]},
        ),
        (
            {"target": "qc", "check": "statevector", "expected": [1, 0], "tolerance": 1},
            {"type": "circuit", "num_qubits": 1, "ops": [{"name": "rx", "qubits": [0], "params": [NAN]}]},
        ),
    ],
)
def test_non_finite_outputs_score_zero(test, artifact):
    [cleaned] = validate_tests([test])
    # Wynik z piaskownicy przechodzi przez JSON, a ``json.loads`` przepuszcza NaN/Infinity.
    artifact = json.loads(json.dumps(artifact))
    outcome = evaluate(cleaned, artifact)
    assert not outcome.passed and outcome.points == 0


def test_expected_values_must_be_finite():
    with pytest.raises(SpecError):
        validate_tests([{"target": "y", "check": "statevector", "expected": [NAN, 0]}])
    with pytest.raises(SpecError):
        validate_tests([{"target": "y", "check": "probabilities", "expected": {"0": INF}}])


def test_mid_circuit_measurement_distribution_is_exact():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.measure(0, 0)  # pomiar w trakcie – dalej bramka sterowana tym kubitem i reset
    qc.cx(0, 1)
    qc.reset(0)
    qc.measure(1, 1)
    artifact = json.loads(json.dumps(grader.artifact_of(qc)))
    dist = grader._measured_distribution(grader.rebuild_circuit(artifact))
    assert dist == pytest.approx({"00": 0.5, "11": 0.5}, abs=1e-12)
    [test] = validate_tests(
        [{"target": "qc", "check": "counts", "expected": {"00": 1, "11": 1}, "tolerance": 1e-9}]
    )
    assert evaluate(test, artifact).passed


def test_exact_distribution_falls_back_above_branch_limit():
    def repeated(times: int) -> QuantumCircuit:
        qc = QuantumCircuit(1, 1)
        for _ in range(times):
            qc.h(0)
            qc.measure(0, 0)
        return qc

    assert exact_distribution(repeated(10)) == pytest.approx({"0": 0.5, "1": 0.5})
    assert exact_distribution(repeated(14)) is None  # 2**14 gałęzi > 4096 – wołający losuje
    dist = grader._measured_distribution(repeated(14))
    assert dist["0"] == pytest.approx(0.5, abs=0.02)


def test_qclab_parses_with_the_pyodide_python_version():
    """Kod qclab trafia do przeglądarki – musi się parsować na Pythonie z Pyodide.

    Pyodide numeruje wydania wersją CPythona (``314.x`` = Python 3.14, potwierdzone w
    ``pyodide-lock.json`` tej wersji: ``"python": "3.14.2"``), więc składnia 3.14 (np. ``except A, B:``
    z PEP 758) jest tam dozwolona. Test pilnuje tej zależności przy każdej zmianie wersji Pyodide.
    """
    major = int(str(build.CONFIG["version"]).split(".")[0])
    assert major >= 314, "Pyodide starsze niż 314 = Python < 3.14"
    feature = (3, major - 300)
    root = Path(qclab.__file__).parent
    files = [p for p in root.rglob("*.py") if "_server_stubs" not in p.parts]
    assert files
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=feature)
