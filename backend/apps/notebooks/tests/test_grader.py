"""``qclab.grader``: walidacja definicji testów, ewaluator wyrażeń i ocena artefaktów."""

from __future__ import annotations

import json
import math

import pytest

from qclab import QuantumCircuit, Statevector, grader
from qclab.grader import SpecError, evaluate, parse_number, validate_tests


def bell():
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    return qc


def one(test: dict, value, language: str = "pl"):
    [cleaned] = validate_tests([test])
    artifact = json.loads(json.dumps(grader.artifact_of(value)))
    return evaluate(cleaned, artifact, language)


# --- wyrażenia ---


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1/sqrt(2)", 1 / math.sqrt(2)),
        ("-0.5j", -0.5j),
        ("exp(i*pi/4)", complex(math.cos(math.pi / 4), math.sin(math.pi / 4))),
        ("2^3", 8),
        ({"re": 1, "im": -1}, 1 - 1j),
        ([0.5, 0.5], 0.5 + 0.5j),
        (3, 3),
    ],
)
def test_parse_number(text, value):
    assert parse_number(text) == pytest.approx(value)


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "().__class__",
        "x",
        "10**10**10",
        "1/0",
        "a" * 300,
        True,
    ],
)
def test_parse_number_rejects_code_and_overflow(text):
    with pytest.raises(SpecError):
        parse_number(text)


# --- walidacja ---


def test_validate_normalises_target_and_defaults():
    [test] = validate_tests('[{"target": "qc", "check": "statevector", "expected": [1, 0]}]')
    assert test["id"] == "t1" and test["points"] == 1
    assert test["target"] == {"name": "qc"}
    assert test["tolerance"] == grader.DEFAULT_TOLERANCE["statevector"]


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("not json", "JSON"),
        ('{"a": 1}', "lista"),
        ('[{"target": "qc", "check": "magic", "expected": 1}]', "check"),
        ('[{"target": "qc; import os", "check": "value", "expected": 1}]', "zmiennej"),
        ('[{"target": "qc", "check": "statevector", "expected": [1, 0, 0]}]', "potęgą"),
        ('[{"target": "qc", "check": "circuit"}]', "warunku"),
        (
            '[{"id": "a", "target": "x", "check": "value", "expected": 1}, '
            '{"id": "a", "target": "y", "check": "value", "expected": 1}]',
            "powtórzone",
        ),
        ('[{"target": "qc", "check": "value"}]', "expected"),
        ('[{"target": "qc", "check": "value", "expected": 1, "points": -1}]', "points"),
    ],
)
def test_validate_rejects(raw, fragment):
    with pytest.raises(SpecError) as excinfo:
        validate_tests(raw)
    assert fragment in str(excinfo.value)


def test_targets_carry_no_expectations():
    tests = validate_tests(
        [
            {"id": "a", "target": "qc", "check": "statevector", "expected": {"00": 1}},
            {"id": "b", "target": "qc", "check": "circuit", "max_depth": 3},
            {"id": "c", "target": {"call": "f", "args": [2]}, "check": "value", "expected": 4},
        ]
    )
    targets = grader.targets_of(tests)
    assert len(targets) == 2
    assert "expected" not in json.dumps(targets) and "max_depth" not in json.dumps(targets)


# --- ocena ---


def test_statevector_check_up_to_global_phase():
    test = {"target": "qc", "check": "statevector", "expected": {"00": "1/sqrt(2)", "11": "1/sqrt(2)"}}
    assert one(test, bell()).passed
    phased = bell()
    phased.global_phase = 1.0
    assert one(test, phased).passed
    assert not one({**test, "global_phase": False}, phased).passed
    wrong = QuantumCircuit(2)
    wrong.h(0)
    outcome = one(test, wrong)
    assert not outcome.passed and outcome.points == 0 and "Wektor stanu" in outcome.message


def test_statevector_ignores_final_measurements_and_accepts_statevector_object():
    measured = bell()
    measured.measure_all()
    test = {
        "target": "qc",
        "check": "statevector",
        "expected": [0.7071067811865476, 0, 0, 0.7071067811865476],
    }
    assert one(test, measured).passed
    assert one(test, Statevector(bell())).passed


def test_probabilities_and_counts():
    probs = {"target": "qc", "check": "probabilities", "expected": {"00": 0.5, "11": 0.5}}
    assert one(probs, bell()).passed
    measured = bell()
    measured.measure_all()
    counts = {"target": "qc", "check": "counts", "expected": {"00": 1, "11": 1}}
    assert one(counts, measured).passed  # rozkład dokładny z obwodu
    assert one(counts, {"00": 480, "11": 520}).passed
    assert not one(counts, {"00": 900, "11": 100}).passed
    no_measure = one(counts, bell())
    assert not no_measure.passed and "pomiar" in no_measure.message


def test_unitary_and_value_checks():
    qc = QuantumCircuit(1)
    qc.h(0)
    unitary = {
        "target": "qc",
        "check": "unitary",
        "expected": [["1/sqrt(2)", "1/sqrt(2)"], ["1/sqrt(2)", "-1/sqrt(2)"]],
    }
    assert one(unitary, qc).passed
    assert one({"target": "x", "check": "value", "expected": "pi"}, math.pi).passed
    assert not one({"target": "x", "check": "value", "expected": 3}, 3.5).passed
    assert one({"target": "x", "check": "value", "expected": [1, 2]}, [1.0, 2.0]).passed
    assert one({"target": "x", "check": "value", "expected": True}, True).passed
    assert one({"target": "x", "check": "value", "expected": "ab"}, "ab").passed


def test_circuit_constraints():
    qc = bell()
    base = {"target": "qc", "check": "circuit"}
    assert one({**base, "num_qubits": 2, "max_depth": 2, "allowed_gates": ["h", "cx"]}, qc).passed
    assert "głębokość" in one({**base, "max_depth": 1}, qc).message
    assert "niedozwolonej" in one({**base, "allowed_gates": ["h"]}, qc).message
    assert "cx" in one({**base, "max_gates": {"cx": 0}}, qc).message
    assert one({**base, "required_gates": ["h"]}, qc).passed
    assert not one({**base, "measurements": "required"}, qc).passed


def test_missing_target_and_failed_call_are_failures_with_messages():
    [test] = validate_tests([{"target": {"call": "build", "args": [2]}, "check": "value", "expected": 1}])
    artifact = grader.extract_artifact({}, test["target"])
    outcome = evaluate(test, artifact, "en")
    assert not outcome.passed and "was not found" in outcome.message

    def build(n):
        raise ValueError("boom")

    artifact = grader.extract_artifact({"build": build}, test["target"])
    assert "boom" in evaluate(test, artifact, "pl").message


def test_language_of_messages():
    test = {"target": "qc", "check": "circuit", "max_depth": 0}
    assert one(test, bell(), "en").message.startswith("The circuit depth")
    assert one(test, bell(), "xx").message.startswith("The circuit depth")  # nieznany język → angielski


@pytest.mark.parametrize(
    "artifact",
    [
        {"type": "circuit", "num_qubits": 99, "ops": []},
        {"type": "circuit", "num_qubits": 2, "ops": [{"name": "rm -rf", "qubits": [0]}]},
        {"type": "circuit", "num_qubits": 2, "ops": [{"name": "h", "qubits": [5]}]},
        {"type": "circuit", "num_qubits": 2, "ops": [{"name": "cx", "qubits": [0, 0]}]},
        {"type": "circuit", "num_qubits": 1, "ops": [{"name": "rx", "qubits": [0], "params": ["nan"]}]},
        {
            "type": "circuit",
            "num_qubits": 1,
            "ops": [{"name": "u", "qubits": [0], "matrix": [[2, 0], [0, 0], [0, 0], [2, 0]]}],
        },
        {"type": "circuit", "num_qubits": 1, "ops": [{"name": "h", "qubits": [0]}] * (grader.MAX_OPS + 1)},
        {"type": "statevector", "data": "nope"},
    ],
)
def test_malicious_artifacts_fail_cleanly(artifact):
    [test] = validate_tests([{"target": "qc", "check": "statevector", "expected": [1, 0]}])
    outcome = evaluate(test, artifact)
    assert not outcome.passed and outcome.points == 0


def test_composite_gate_roundtrip():
    inner = bell()
    inner.name = "bell"
    outer = QuantumCircuit(3)
    outer.append(inner.to_gate(), [1, 2])
    artifact = json.loads(json.dumps(grader.artifact_of(outer)))
    rebuilt = grader.rebuild_circuit(artifact)
    assert rebuilt.count_ops() == {"bell": 1}
    assert Statevector(rebuilt).equiv(Statevector(outer))


def test_visible_check_prints_table(capsys):
    summary = grader.check(
        [
            {
                "id": "a",
                "name": "Bell",
                "points": 2,
                "target": "qc",
                "check": "probabilities",
                "expected": {"00": 0.5, "11": 0.5},
            }
        ],
        {"qc": bell()},
        language="pl",
    )
    assert summary["points"] == 2
    printed = capsys.readouterr().out
    assert "✔ Bell" in printed and "testy ukryte" in printed
