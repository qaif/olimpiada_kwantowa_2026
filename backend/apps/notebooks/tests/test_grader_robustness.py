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


# --- H1: dopasowanie fazy globalnej na zdegenerowanym wejściu oblewa ------------------------------


def _one(test: dict) -> dict:
    [cleaned] = validate_tests([test])
    return cleaned


@pytest.mark.parametrize("data", [[[0.0, 0.0], [1.0, 0.0]], [[0.0, 0.0], [0.0, 0.0]]])
def test_orthogonal_or_zero_state_fails_even_with_huge_tolerance(data):
    test = _one({"target": "qc", "check": "statevector", "expected": [1, 0], "tolerance": 1})
    assert not evaluate(test, {"type": "statevector", "data": data}).passed


def test_unitary_with_zero_reference_element_fails():
    test = _one({"target": "u", "check": "unitary", "expected": [[1, 0], [0, 1]], "tolerance": 1})
    assert not evaluate(test, {"type": "value", "data": [[0, 1], [1, 0]]}).passed


def test_zero_expected_statevector_is_rejected():
    with pytest.raises(SpecError):
        validate_tests([{"target": "y", "check": "statevector", "expected": [0, 0]}])


# --- H2: koszt oceny w workerze ograniczony budżetem --------------------------------------------


def heavy_artifact(qubits: int = 20, ops: int = 19_000) -> dict:
    qc = QuantumCircuit(qubits, qubits)
    for index in range(ops):
        qc.h(index % qubits)
    qc.measure(range(qubits), range(qubits))
    return json.loads(json.dumps(grader.artifact_of(qc)))


@pytest.mark.parametrize(
    "test",
    [
        {"target": "qc", "check": "probabilities", "expected": {"0" * 20: 1}},
        {"target": "qc", "check": "counts", "expected": {"0" * 20: 1}},
        {"target": "qc", "check": "statevector", "expected": {"0" * 20: 1}},
    ],
)
def test_heavy_20_qubit_circuit_is_refused_quickly(test):
    import time

    artifact = heavy_artifact()
    started = time.monotonic()
    outcome = evaluate(_one(test), artifact)
    assert time.monotonic() - started < 5
    assert not outcome.passed
    assert "too large" in outcome.message


def test_simulation_work_counts_composite_gates():
    qc = QuantumCircuit(3)
    qc.h(0)
    qc.cx(0, 1)
    assert grader.simulation_work(qc) == 2 ** (3 + 1) + 2 ** (3 + 2)


def test_state_is_simulated_once_per_artifact(monkeypatch):
    calls = []
    original = grader.evolve_unitary
    monkeypatch.setattr(grader, "evolve_unitary", lambda *a, **k: calls.append(1) or original(*a, **k))
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    artifact = json.loads(json.dumps(grader.artifact_of(qc)))
    tests = validate_tests(
        [
            {"id": "a", "target": "qc", "check": "statevector", "expected": [0.5**0.5, 0, 0, 0.5**0.5]},
            {"id": "b", "target": "qc", "check": "probabilities", "expected": {"00": 0.5, "11": 0.5}},
            {"id": "c", "target": "qc", "check": "statevector", "expected": [0.5**0.5, 0, 0, 0.5**0.5]},
        ]
    )
    outcomes = grader.evaluate_all(tests, {grader.target_key(tests[0]["target"]): artifact})
    assert [o.passed for o in outcomes] == [True, True, True]
    assert len(calls) == 1


def test_run_budget_is_shared_by_all_artifacts():
    cache = grader.GradingCache(run_budget=100)
    cache.charge(60)
    with pytest.raises(qclab.QclabError):
        cache.charge(60)
    with pytest.raises(qclab.QclabError):
        grader.GradingCache().charge(grader.MAX_GRADING_WORK + 1)


# --- L1: tolerancja zliczeń skalowana szumem próbkowania ------------------------------------------


def test_counts_tolerance_scales_with_shots():
    assert grader.counts_tolerance(0.01, 1024, 2) == pytest.approx((2 / 1024) ** 0.5)
    assert grader.counts_tolerance(0.2, 1_000_000, 2) == 0.2


def test_sampled_counts_of_correct_solution_pass_tight_tolerance():
    test = _one({"target": "c", "check": "counts", "expected": {"00": 1, "11": 1}, "tolerance": 0.01})
    # Typowy wynik 1024 strzałów stanu Bella: odchylenie 0,023 – ponad 0,01 z treści testu.
    assert evaluate(test, {"type": "counts", "data": {"00": 536, "11": 488}}).passed
    assert not evaluate(test, {"type": "counts", "data": {"00": 700, "11": 324}}).passed


def test_too_few_shots_fail_with_explanation():
    test = _one({"target": "c", "check": "counts", "expected": {"0": 1}, "tolerance": 0.5})
    outcome = evaluate(test, {"type": "counts", "data": {"0": 10}}, language="en")
    assert not outcome.passed and "Too few shots" in outcome.message
