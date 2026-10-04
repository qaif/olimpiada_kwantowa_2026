"""Testy zadań notatnikowych: walidacja definicji, ekstrakcja artefaktów i ocena.

Ten sam moduł działa w trzech miejscach i to jest jego jedyna racja bytu jako całości:

1. **przeglądarka** – uczeń uruchamia testy widoczne (``check(...)`` w ostatniej komórce notatnika),
2. **piaskownica serwera** (``apps.notebooks.runner.child``) – po wykonaniu komórek ucznia
   ``extract_artifacts`` zamienia wskazane obiekty (obwód, wektor stanu, liczby) na JSON,
3. **worker** (``apps.notebooks.services``) – ``evaluate`` porównuje artefakt z oczekiwaniem
   testu **ukrytego**. Oczekiwane wartości testów ukrytych nie trafiają ani do przeglądarki, ani
   do procesu, w którym działa kod ucznia – piaskownica dostaje wyłącznie listę celów („co
   wyciągnąć”), a nie „jaki ma być wynik”.

Artefakt przechodzi przez JSON także w przeglądarce (``check`` robi ``json.dumps``/``loads``), więc
test widoczny liczy dokładnie to samo, co liczyłby ten sam test na serwerze.

Format definicji testu – ``docs/tasks/QC-01.md`` § 4.2. Komunikaty ocen są po polsku albo po
angielsku (język treści zadania, ``NotebookTask.language``) – to treść zadania, nie napis
interfejsu, więc nie idzie przez gettext (ten moduł działa też bez Django).
"""

from __future__ import annotations

import ast
import cmath
import json
import math
import numbers
import re
from dataclasses import asdict, dataclass

import numpy as np

from .circuit import Gate, Instruction, ParameterExpression, QuantumCircuit, bind_value, gate_from_name
from .exceptions import MAX_OPERATOR_QUBITS, MAX_QUBITS, QclabError
from .gates import GATES
from .quantum_info import Operator, Statevector
from .simulator import evolve_unitary, zero_state

CHECKS = ("statevector", "probabilities", "counts", "unitary", "value", "circuit")
DEFAULT_TOLERANCE = {
    "statevector": 1e-6,
    "probabilities": 1e-6,
    "counts": 0.05,
    "unitary": 1e-6,
    "value": 1e-6,
}
MAX_TESTS = 50
MAX_POINTS = 1000
MAX_ARGS_JSON = 2000
#: Limity artefaktu z piaskownicy – parent czyta wynik od kodu ucznia, więc wszystko ma granice.
MAX_OPS = 20_000
MAX_NESTING = 5
MAX_MATRIX_QUBITS = 6
MAX_VALUE_ITEMS = 10_000
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$")
LABEL_RE = re.compile(r"^[01]{1,20}$")

MESSAGES = {
    "pl": {
        "ok": "OK",
        "missing": "Nie znaleziono „{target}” – czy komórka, która to tworzy, została uruchomiona?",
        "call_failed": "Wywołanie „{target}” zakończyło się błędem: {error}",
        "unsupported": "„{target}” ma typ, którego ten test nie sprawdza ({kind}).",
        "wrong_qubits": "Obwód ma {got} kubitów, a oczekiwano {expected}.",
        "sv_mismatch": "Wektor stanu różni się od oczekiwanego (największa różnica {diff:.3g}).",
        "prob_mismatch": "Rozkład prawdopodobieństwa różni się od oczekiwanego "
        "(największa różnica {diff:.3g}).",
        "counts_mismatch": "Rozkład wyników różni się od oczekiwanego (odległość {diff:.3g} > {tol:.3g}).",
        "unitary_mismatch": "Macierz obwodu różni się od oczekiwanej (największa różnica {diff:.3g}).",
        "value_mismatch": "Wartość różni się od oczekiwanej.",
        "too_deep": "Obwód ma głębokość {got}, dopuszczalne najwyżej {limit}.",
        "too_big": "Obwód ma {got} operacji, dopuszczalne najwyżej {limit}.",
        "gate_limit": "Bramka {gate} występuje {got} razy, dopuszczalne najwyżej {limit}.",
        "gate_forbidden": "Użyto niedozwolonej bramki: {gate}.",
        "gate_required": "Brakuje wymaganej bramki: {gate}.",
        "has_measure": "Obwód nie może zawierać pomiarów.",
        "no_measure": "Obwód musi mierzyć wynik (brak pomiarów).",
        "eval_error": "Nie udało się sprawdzić wyniku: {error}",
        "summary": "Wynik testów widocznych: {points} / {total} pkt",
        "hidden_note": "To są tylko testy przykładowe – ocenę liczą testy ukryte na serwerze.",
    },
    "en": {
        "ok": "OK",
        "missing": "“{target}” was not found – did you run the cell that defines it?",
        "call_failed": "Calling “{target}” raised an error: {error}",
        "unsupported": "“{target}” has a type this test does not check ({kind}).",
        "wrong_qubits": "The circuit has {got} qubits, expected {expected}.",
        "sv_mismatch": "The statevector differs from the expected one (max difference {diff:.3g}).",
        "prob_mismatch": "The probability distribution differs from the expected one "
        "(max difference {diff:.3g}).",
        "counts_mismatch": "The outcome distribution differs from the expected one "
        "(distance {diff:.3g} > {tol:.3g}).",
        "unitary_mismatch": "The circuit matrix differs from the expected one (max difference {diff:.3g}).",
        "value_mismatch": "The value differs from the expected one.",
        "too_deep": "The circuit depth is {got}, at most {limit} allowed.",
        "too_big": "The circuit has {got} operations, at most {limit} allowed.",
        "gate_limit": "Gate {gate} is used {got} times, at most {limit} allowed.",
        "gate_forbidden": "A forbidden gate was used: {gate}.",
        "gate_required": "A required gate is missing: {gate}.",
        "has_measure": "The circuit must not contain measurements.",
        "no_measure": "The circuit must measure its result (no measurements found).",
        "eval_error": "Could not check the result: {error}",
        "summary": "Visible tests: {points} / {total} points",
        "hidden_note": "These are sample tests only – the score comes from hidden tests on the server.",
    },
}


def message(language: str, key: str, **params) -> str:
    table = MESSAGES.get(language) or MESSAGES["en"]
    return table[key].format(**params)


class SpecError(ValueError):
    """Błąd w definicji testu – komunikat dla koordynatora (po polsku, z numerem testu)."""


# --- bezpieczne wyrażenia liczbowe ----------------------------------------------------------------

_FUNCS = {
    "sqrt": cmath.sqrt,
    "exp": cmath.exp,
    "cos": cmath.cos,
    "sin": cmath.sin,
    "tan": cmath.tan,
    "log": cmath.log,
    "conj": lambda z: complex(z).conjugate(),
    "abs": abs,
}
_CONSTS = {"pi": math.pi, "e": math.e, "i": 1j, "j": 1j}
MAX_EXPRESSION = 200


def parse_number(value) -> complex:
    """Liczba z JSON-a albo napisu (``"1/sqrt(2)"``, ``"exp(i*pi/4)"``, ``"-0.5j"``).

    Własny ewaluator na drzewie AST, a nie ``eval``: definicje testów wpisuje koordynator, ale
    parsuje je worker – a dowolny kod z formularza nie ma prawa się tam wykonać. Liczby całkowite
    są od razu zamieniane na zmiennoprzecinkowe, więc ``10**10**10`` kończy się błędem przepełnienia,
    a nie minutą liczenia wielkiej liczby całkowitej.
    """
    if isinstance(value, bool):
        raise SpecError("Wartość logiczna nie jest liczbą.")
    if isinstance(value, numbers.Number):
        return complex(value)
    if isinstance(value, dict) and set(value) <= {"re", "im"}:
        return complex(float(value.get("re", 0)), float(value.get("im", 0)))
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, numbers.Real) for v in value):
        return complex(float(value[0]), float(value[1]))
    if not isinstance(value, str):
        raise SpecError(f"Nie rozumiem liczby: {value!r}")
    text = value.strip()
    if not text or len(text) > MAX_EXPRESSION:
        raise SpecError(f"Pusta albo za długa liczba: {value!r}")
    try:
        tree = ast.parse(text.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        raise SpecError(f"Nie rozumiem wyrażenia {value!r}.") from exc

    def walk(node) -> complex:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float, complex))
            and not isinstance(node.value, bool)
        ):
            return complex(node.value)
        if isinstance(node, ast.Name) and node.id in _CONSTS:
            return complex(_CONSTS[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            inner = walk(node.operand)
            return -inner if isinstance(node.op, ast.USub) else inner
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
        ):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if abs(right) > 64:
                raise SpecError("Za duży wykładnik.")
            return left**right
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCS
            and len(node.args) == 1
            and not node.keywords
        ):
            return complex(_FUNCS[node.func.id](walk(node.args[0])))
        raise SpecError(f"Niedozwolony element wyrażenia {value!r}.")

    try:
        result = walk(tree)
    except (ZeroDivisionError, OverflowError, ValueError) as exc:
        raise SpecError(f"Nie da się policzyć {value!r}: {exc}") from exc
    if not (math.isfinite(result.real) and math.isfinite(result.imag)):
        raise SpecError(f"Wartość {value!r} nie jest skończona.")
    return result


# --- walidacja definicji testów -------------------------------------------------------------------


def normalize_target(target) -> dict:
    """``"qc"`` → ``{"name": "qc"}``; ``{"call": "f", "args": [...]}`` – wywołanie funkcji ucznia."""
    if isinstance(target, str):
        target = {"name": target}
    if not isinstance(target, dict):
        raise SpecError('„target” musi być nazwą zmiennej albo obiektem {"call": …, "args": […]}.')
    if "call" in target:
        name = target.get("call")
        args = target.get("args", [])
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise SpecError(f"Nieprawidłowa nazwa funkcji: {name!r}")
        if not isinstance(args, list) or len(json.dumps(args)) > MAX_ARGS_JSON:
            raise SpecError("„args” musi być listą (krótką) wartości JSON.")
        return {"call": name, "args": args}
    name = target.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SpecError(f"Nieprawidłowa nazwa zmiennej: {name!r}")
    return {"name": name}


def target_label(target: dict) -> str:
    if "call" in target:
        return f"{target['call']}({', '.join(json.dumps(a, ensure_ascii=False) for a in target['args'])})"
    return target["name"]


def _validate_expected(check: str, expected, test: dict) -> None:
    if check == "statevector":
        if isinstance(expected, dict):
            widths = {len(k) for k in expected}
            if not expected or len(widths) != 1 or not all(LABEL_RE.match(k) for k in expected):
                raise SpecError(
                    "Oczekiwany wektor stanu jako słownik: klucze to napisy bitów jednej długości."
                )
            for value in expected.values():
                parse_number(value)
        elif isinstance(expected, list):
            if len(expected) < 2 or len(expected) & (len(expected) - 1) or len(expected) > 2**MAX_QUBITS:
                raise SpecError("Oczekiwany wektor stanu: lista o długości będącej potęgą dwójki.")
            for value in expected:
                parse_number(value)
        else:
            raise SpecError('Oczekiwany wektor stanu: lista amplitud albo słownik {"00": amplituda}.')
    elif check in ("probabilities", "counts"):
        if not isinstance(expected, dict) or not expected:
            raise SpecError('Oczekiwany rozkład: słownik {"00": 0.5, "11": 0.5}.')
        for key, value in expected.items():
            if not isinstance(key, str) or not re.fullmatch(r"[01 ]{1,40}", key):
                raise SpecError(f"Nieprawidłowy klucz rozkładu: {key!r}")
            number = parse_number(value)
            if number.imag or number.real < 0:
                raise SpecError(f"Prawdopodobieństwo {key!r} musi być nieujemną liczbą rzeczywistą.")
    elif check == "unitary":
        if not isinstance(expected, list) or not expected or not all(isinstance(r, list) for r in expected):
            raise SpecError("Oczekiwana macierz: lista wierszy.")
        dim = len(expected)
        if dim & (dim - 1) or dim > 2**MAX_MATRIX_QUBITS or any(len(r) != dim for r in expected):
            raise SpecError("Oczekiwana macierz: kwadratowa, wymiar będący potęgą dwójki (najwyżej 64).")
        for row in expected:
            for value in row:
                parse_number(value)
    elif check == "value":
        if expected is None:
            raise SpecError("Test „value” wymaga pola „expected”.")
        if len(json.dumps(expected)) > 20000:
            raise SpecError("Oczekiwana wartość jest za duża.")
    elif check == "circuit":
        allowed = {"num_qubits", "max_depth", "max_size", "max_gates", "allowed_gates", "required_gates",
                   "measurements"}  # fmt: skip
        if not any(key in test for key in allowed):
            raise SpecError(
                "Test „circuit” wymaga co najmniej jednego warunku (np. max_depth, allowed_gates)."
            )
        for key in ("num_qubits", "max_depth", "max_size"):
            if key in test and (
                not isinstance(test[key], int) or isinstance(test[key], bool) or test[key] < 0
            ):
                raise SpecError(f"„{key}” musi być nieujemną liczbą całkowitą.")
        if "max_gates" in test and (
            not isinstance(test["max_gates"], dict)
            or not all(isinstance(v, int) and v >= 0 for v in test["max_gates"].values())
        ):
            raise SpecError('„max_gates” to słownik {"cx": 2}.')
        for key in ("allowed_gates", "required_gates"):
            if key in test and (
                not isinstance(test[key], list) or not all(isinstance(g, str) for g in test[key])
            ):
                raise SpecError(f"„{key}” to lista nazw bramek.")
        if "measurements" in test and test["measurements"] not in ("required", "forbidden"):
            raise SpecError('„measurements” to "required" albo "forbidden".')


def validate_tests(raw) -> list[dict]:
    """Lista testów po walidacji (znormalizowany ``target``). ``SpecError`` z numerem testu."""
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SpecError(f"To nie jest poprawny JSON: {exc.msg} (wiersz {exc.lineno}).") from exc
    if not isinstance(raw, list):
        raise SpecError("Testy to lista obiektów JSON: [{…}, {…}].")
    if len(raw) > MAX_TESTS:
        raise SpecError(f"Najwyżej {MAX_TESTS} testów.")
    seen: set[str] = set()
    cleaned = []
    for index, test in enumerate(raw, start=1):
        try:
            if not isinstance(test, dict):
                raise SpecError("test musi być obiektem JSON.")
            test_id = str(test.get("id") or f"t{index}")
            if not ID_RE.match(test_id) or test_id in seen:
                raise SpecError(f"„id” {test_id!r} jest nieprawidłowe albo powtórzone.")
            seen.add(test_id)
            check = test.get("check")
            if check not in CHECKS:
                raise SpecError(f"„check” musi być jednym z: {', '.join(CHECKS)}.")
            points = test.get("points", 1)
            if (
                not isinstance(points, (int, float))
                or isinstance(points, bool)
                or not 0 <= points <= MAX_POINTS
            ):
                raise SpecError(f"„points” musi być liczbą od 0 do {MAX_POINTS}.")
            tolerance = test.get("tolerance", DEFAULT_TOLERANCE.get(check, 0))
            if (
                not isinstance(tolerance, (int, float))
                or isinstance(tolerance, bool)
                or not 0 <= tolerance <= 1
            ):
                raise SpecError("„tolerance” musi być liczbą od 0 do 1.")
            name = str(test.get("name") or test_id)[:120]
            item = {
                **test,
                "id": test_id,
                "name": name,
                "check": check,
                "points": points,
                "tolerance": tolerance,
            }
            item["target"] = normalize_target(test.get("target"))
            if check != "circuit":
                if "expected" not in test:
                    raise SpecError("brak pola „expected”.")
                _validate_expected(check, test["expected"], test)
            else:
                _validate_expected(check, None, test)
            cleaned.append(item)
        except SpecError as exc:
            raise SpecError(f"Test {index}: {exc}") from exc
    return cleaned


def public_view(tests: list[dict]) -> list[dict]:
    """Testy widoczne w postaci dla notatnika (bez pól spoza formatu)."""
    keep = (
        "id", "name", "points", "target", "check", "expected", "tolerance", "global_phase",
        "num_qubits", "max_depth", "max_size", "max_gates", "allowed_gates", "required_gates",
        "measurements",
    )  # fmt: skip
    return [{k: t[k] for k in keep if k in t} for t in tests]


def targets_of(tests: list[dict]) -> list[dict]:
    """Same cele (bez oczekiwań) – to i tylko to dostaje piaskownica. Duplikaty scalone."""
    unique: dict[str, dict] = {}
    for test in tests:
        target = normalize_target(test["target"])
        unique[target_key(target)] = target
    return [{"key": key, **target} for key, target in unique.items()]


def target_key(target: dict) -> str:
    return json.dumps(normalize_target(target), sort_keys=True, ensure_ascii=False)


# --- artefakty (strona ucznia: przeglądarka i piaskownica) ---------------------------------------


def _complex_list(values) -> list[list[float]]:
    array = np.asarray(values, dtype=complex).reshape(-1)
    return [[float(v.real), float(v.imag)] for v in array.tolist()]


def _serialize_ops(circuit: QuantumCircuit, depth: int, budget: list[int]) -> list[dict]:
    if depth > MAX_NESTING:
        raise QclabError("Gates are nested too deeply.")
    ops = []
    for operation, qubits, clbits in circuit.instruction_indices():
        budget[0] += 1
        if budget[0] > MAX_OPS:
            raise QclabError(f"The circuit has more than {MAX_OPS} operations.")
        params = []
        for param in operation.params:
            value = bind_value(param)
            if isinstance(value, ParameterExpression):
                raise QclabError(f"Gate {operation.name} has an unbound parameter – use assign_parameters().")
            params.append(float(value))
        item = {"name": operation.name, "qubits": qubits, "clbits": clbits, "params": params}
        if operation._matrix is not None:
            if operation.num_qubits > MAX_MATRIX_QUBITS:
                raise QclabError(f"Matrix gates are limited to {MAX_MATRIX_QUBITS} qubits.")
            item["matrix"] = _complex_list(operation._matrix)
            item["num_qubits"] = operation.num_qubits
        elif operation.definition is not None:
            sub = operation.definition
            item["definition"] = {
                "num_qubits": sub.num_qubits,
                "num_clbits": sub.num_clbits,
                "global_phase": float(bind_value(sub.global_phase)),
                "ops": _serialize_ops(sub, depth + 1, budget),
            }
        ops.append(item)
    return ops


def serialize_circuit(circuit: QuantumCircuit) -> dict:
    return {
        "type": "circuit",
        "num_qubits": circuit.num_qubits,
        "num_clbits": circuit.num_clbits,
        "cregs": [[reg.name, len(reg)] for reg in circuit.cregs],
        "global_phase": float(bind_value(circuit.global_phase)),
        "ops": _serialize_ops(circuit, 0, [0]),
    }


def _plain_value(value, depth: int = 0):
    if depth > 4:
        raise QclabError("The value is nested too deeply.")
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (numbers.Integral, np.integer)):
        return int(value)
    if isinstance(value, (numbers.Real, np.floating)):
        return float(value)
    if isinstance(value, (numbers.Complex, np.complexfloating)):
        return {"re": float(value.real), "im": float(value.imag)}
    if isinstance(value, str):
        return value[:10000]
    if isinstance(value, np.ndarray):
        if value.size > MAX_VALUE_ITEMS:
            raise QclabError("The array is too large.")
        return [_plain_value(v, depth + 1) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_VALUE_ITEMS:
            raise QclabError("The list is too long.")
        return [_plain_value(v, depth + 1) for v in value]
    if isinstance(value, dict):
        if len(value) > MAX_VALUE_ITEMS:
            raise QclabError("The dictionary is too large.")
        return {str(k)[:200]: _plain_value(v, depth + 1) for k, v in value.items()}
    raise QclabError(f"Unsupported value type: {type(value).__name__}")


def artifact_of(value) -> dict:
    """JSON-owy obraz obiektu ucznia. Wyjątek ``QclabError`` = obiekt nieobsługiwany."""
    if isinstance(value, QuantumCircuit):
        return serialize_circuit(value)
    if isinstance(value, Statevector):
        return {"type": "statevector", "data": _complex_list(value.data)}
    if isinstance(value, Operator):
        if value.num_qubits > MAX_MATRIX_QUBITS:
            raise QclabError(f"Operators are limited to {MAX_MATRIX_QUBITS} qubits.")
        return {"type": "operator", "data": [_complex_list(row) for row in value.data]}
    if isinstance(value, Instruction):
        circuit = QuantumCircuit(value.num_qubits, value.num_clbits)
        circuit.append(value, range(value.num_qubits), range(value.num_clbits))
        return serialize_circuit(circuit)
    if (
        isinstance(value, dict)
        and value
        and all(isinstance(k, str) for k in value)
        and all(
            isinstance(v, (numbers.Integral, np.integer)) and not isinstance(v, bool) for v in value.values()
        )
    ):
        return {"type": "counts", "data": {k[:64]: int(v) for k, v in list(value.items())[:MAX_VALUE_ITEMS]}}
    return {"type": "value", "data": _plain_value(value)}


def extract_artifact(namespace: dict, target: dict) -> dict:
    """Artefakt jednego celu. Błąd (brak zmiennej, wyjątek w funkcji) też jest artefaktem."""
    target = normalize_target(target)
    label = target_label(target)
    try:
        if "call" in target:
            function = namespace.get(target["call"])
            if not callable(function):
                return {"type": "error", "code": "missing", "target": label}
            try:
                value = function(*target["args"])
            except Exception as exc:  # noqa: BLE001 - błąd ucznia jest wynikiem testu, a nie awarią
                return {"type": "error", "code": "call_failed", "target": label,
                        "error": f"{type(exc).__name__}: {exc}"[:500]}  # fmt: skip
        else:
            if target["name"] not in namespace:
                return {"type": "error", "code": "missing", "target": label}
            value = namespace[target["name"]]
        artifact = artifact_of(value)
    except QclabError as exc:
        return {"type": "error", "code": "eval_error", "target": label, "error": str(exc)[:500]}
    except Exception as exc:  # noqa: BLE001 - dowolny obiekt ucznia nie może wywrócić ekstrakcji
        return {"type": "error", "code": "eval_error", "target": label, "error": f"{type(exc).__name__}"}
    encoded = json.dumps(artifact)
    if len(encoded) > MAX_ARTIFACT_BYTES:
        return {"type": "error", "code": "eval_error", "target": label, "error": "result too large"}
    return json.loads(encoded)


def extract_artifacts(namespace: dict, targets: list[dict]) -> dict[str, dict]:
    return {t.get("key") or target_key(t): extract_artifact(namespace, t) for t in targets}


# --- odtwarzanie artefaktów (strona oceniająca) ---------------------------------------------------


def _matrix_from(data, dim: int) -> np.ndarray:
    if not isinstance(data, list) or len(data) != dim * dim:
        raise QclabError("Invalid matrix in the result.")
    values = np.array([complex(float(re_), float(im)) for re_, im in data], dtype=complex)
    return values.reshape(dim, dim)


def _rebuild_ops(circuit: QuantumCircuit, ops, depth: int, budget: list[int]) -> None:
    if depth > MAX_NESTING or not isinstance(ops, list):
        raise QclabError("Invalid circuit structure in the result.")
    for item in ops:
        budget[0] += 1
        if budget[0] > MAX_OPS or not isinstance(item, dict):
            raise QclabError("Invalid circuit structure in the result.")
        name = item.get("name")
        qubits = item.get("qubits") or []
        clbits = item.get("clbits") or []
        params = item.get("params") or []
        if not isinstance(name, str) or len(name) > 64:
            raise QclabError("Invalid gate name in the result.")
        if not all(isinstance(q, int) and not isinstance(q, bool) for q in [*qubits, *clbits]):
            raise QclabError("Invalid qubit index in the result.")
        if not all(isinstance(p, (int, float)) and math.isfinite(p) for p in params):
            raise QclabError("Invalid gate parameter in the result.")
        if name == "measure":
            if len(qubits) != 1 or len(clbits) != 1:
                raise QclabError("Invalid measurement in the result.")
            circuit.measure(qubits[0], clbits[0])
            continue
        if name == "reset":
            if len(qubits) != 1:
                raise QclabError("Invalid reset in the result.")
            circuit.reset(qubits[0])
            continue
        if name == "barrier":
            if qubits:
                circuit.barrier(*qubits)
            continue
        if name in ("save_statevector", "delay"):
            continue
        if "matrix" in item:
            k = len(qubits)
            if k > MAX_MATRIX_QUBITS:
                raise QclabError("Matrix gate too large in the result.")
            matrix = _matrix_from(item["matrix"], 2**k)
            if not np.allclose(matrix @ matrix.conj().T, np.eye(2**k), atol=1e-6):
                raise QclabError(f"Gate {name} in the result is not unitary.")
            gate = Gate(name, k, params, matrix=matrix)
        elif "definition" in item:
            definition = item["definition"]
            if not isinstance(definition, dict):
                raise QclabError("Invalid composite gate in the result.")
            sub = QuantumCircuit(int(definition.get("num_qubits", 0)), name=name)
            phase = definition.get("global_phase", 0.0)
            if not isinstance(phase, (int, float)) or not math.isfinite(phase):
                raise QclabError("Invalid global phase in the result.")
            sub.global_phase = float(phase)
            _rebuild_ops(sub, definition.get("ops"), depth + 1, budget)
            gate = sub.to_gate()
            gate.name = name
        else:
            if name not in GATES:
                raise QclabError(f"Unknown gate {name!r} in the result.")
            gate = gate_from_name(name, params)
        circuit.append(gate, qubits)


def rebuild_circuit(artifact: dict) -> QuantumCircuit:
    num_qubits = artifact.get("num_qubits")
    num_clbits = artifact.get("num_clbits", 0)
    if not isinstance(num_qubits, int) or not 0 <= num_qubits <= MAX_QUBITS:
        raise QclabError("Invalid number of qubits in the result.")
    if not isinstance(num_clbits, int) or not 0 <= num_clbits <= 1024:
        raise QclabError("Invalid number of clbits in the result.")
    from .circuit import ClassicalRegister, QuantumRegister

    regs: list = [QuantumRegister(num_qubits, "q")] if num_qubits else []
    cregs = artifact.get("cregs") or []
    if cregs and sum(int(size) for _name, size in cregs) == num_clbits:
        regs += [ClassicalRegister(int(size), str(name)[:64]) for name, size in cregs]
    elif num_clbits:
        regs.append(ClassicalRegister(num_clbits, "c"))
    circuit = QuantumCircuit(*regs)
    phase = artifact.get("global_phase", 0.0)
    if not isinstance(phase, (int, float)) or not math.isfinite(phase):
        raise QclabError("Invalid global phase in the result.")
    circuit.global_phase = float(phase)
    _rebuild_ops(circuit, artifact.get("ops"), 0, [0])
    return circuit


# --- ocena ----------------------------------------------------------------------------------------


@dataclass
class Outcome:
    id: str
    name: str
    points: float
    max_points: float
    passed: bool
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


def _expected_vector(expected, num_qubits: int | None) -> np.ndarray:
    if isinstance(expected, dict):
        width = len(next(iter(expected)))
        vector = np.zeros(2**width, dtype=complex)
        for label, value in expected.items():
            vector[int(label, 2)] = parse_number(value)
        return vector
    return np.array([parse_number(v) for v in expected], dtype=complex)


def _distribution(expected) -> dict[str, float]:
    values = {key.replace(" ", ""): parse_number(value).real for key, value in expected.items()}
    total = sum(values.values())
    if total <= 0:
        raise SpecError("Rozkład oczekiwany sumuje się do zera.")
    return {k: v / total for k, v in values.items()}


def _state_of(artifact: dict) -> np.ndarray:
    if artifact["type"] == "statevector":
        data = artifact.get("data")
        if not isinstance(data, list) or not data or len(data) > 2**MAX_QUBITS:
            raise QclabError("Invalid statevector in the result.")
        return np.array([complex(float(a), float(b)) for a, b in data], dtype=complex)
    if artifact["type"] == "circuit":
        circuit = rebuild_circuit(artifact).remove_final_measurements(inplace=False)
        return evolve_unitary(circuit, zero_state(circuit.num_qubits))
    if artifact["type"] == "value" and isinstance(artifact.get("data"), list):
        return np.array([parse_number(v) for v in artifact["data"]], dtype=complex)
    raise TypeError(artifact["type"])


def _measured_distribution(circuit: QuantumCircuit) -> dict[str, float]:
    """Dokładny rozkład wyników pomiarów obwodu (bez losowania) – klucze bez spacji."""
    from .simulator import _key, _terminal_measurements

    pairs = _terminal_measurements(circuit)
    if pairs is None:
        from .simulator import sample_counts

        counts = sample_counts(circuit, 20000, seed=12345)
        total = sum(counts.values())
        return {k.replace(" ", ""): v / total for k, v in counts.items()}
    if not pairs:
        raise QclabError("no_measure")
    state = evolve_unitary(circuit, zero_state(circuit.num_qubits), skip_measure=True)
    probs = np.abs(state) ** 2
    result: dict[str, float] = {}
    for index in np.nonzero(probs > 1e-15)[0].tolist():
        clbits = [0] * circuit.num_clbits
        for qubit, clbit in pairs:
            clbits[clbit] = (index >> qubit) & 1
        key = _key(circuit, clbits).replace(" ", "")
        result[key] = result.get(key, 0.0) + float(probs[index])
    return result


def _values_equal(expected, actual, tolerance: float) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, str) and not isinstance(actual, str):
        try:
            expected = parse_number(expected)
        except SpecError:
            return False
    if isinstance(expected, str):
        return expected == actual
    if isinstance(expected, (numbers.Number, dict)) and not (
        isinstance(expected, dict) and set(expected) - {"re", "im"}
    ):
        try:
            a = parse_number(expected)
            b = parse_number(actual)
        except SpecError:
            return False
        return abs(a - b) <= tolerance * max(1.0, abs(a))
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(_values_equal(e, a, tolerance) for e, a in zip(expected, actual, strict=True))
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            return False
        return all(_values_equal(expected[k], actual[k], tolerance) for k in expected)
    return expected == actual


def _check_circuit(test: dict, circuit: QuantumCircuit, language: str) -> str | None:
    if "num_qubits" in test and circuit.num_qubits != test["num_qubits"]:
        return message(language, "wrong_qubits", got=circuit.num_qubits, expected=test["num_qubits"])
    if "max_depth" in test and circuit.depth() > test["max_depth"]:
        return message(language, "too_deep", got=circuit.depth(), limit=test["max_depth"])
    if "max_size" in test and circuit.size() > test["max_size"]:
        return message(language, "too_big", got=circuit.size(), limit=test["max_size"])
    ops = circuit.count_ops()
    for gate, limit in (test.get("max_gates") or {}).items():
        if ops.get(gate, 0) > limit:
            return message(language, "gate_limit", gate=gate, got=ops.get(gate, 0), limit=limit)
    if "allowed_gates" in test:
        allowed = set(test["allowed_gates"]) | {"barrier", "measure"}
        for gate in ops:
            if gate not in allowed:
                return message(language, "gate_forbidden", gate=gate)
    for gate in test.get("required_gates") or []:
        if gate not in ops:
            return message(language, "gate_required", gate=gate)
    if test.get("measurements") == "forbidden" and "measure" in ops:
        return message(language, "has_measure")
    if test.get("measurements") == "required" and "measure" not in ops:
        return message(language, "no_measure")
    return None


def _fail(test: dict, text: str) -> Outcome:
    return Outcome(test["id"], test["name"], 0.0, float(test["points"]), False, text)


def evaluate(test: dict, artifact: dict | None, language: str = "pl") -> Outcome:
    """Wynik jednego testu na artefakcie. Nigdy nie rzuca – błąd to test niezaliczony z opisem."""
    label = target_label(normalize_target(test["target"]))
    if not artifact or artifact.get("type") == "error":
        code = (artifact or {}).get("code", "missing")
        if code not in ("missing", "call_failed", "eval_error"):
            code = "eval_error"
        return _fail(test, message(language, code, target=label, error=(artifact or {}).get("error", "")))
    check = test["check"]
    tolerance = float(test.get("tolerance", DEFAULT_TOLERANCE.get(check, 0)))
    kind = artifact.get("type")
    try:
        if check == "circuit":
            if kind != "circuit":
                return _fail(test, message(language, "unsupported", target=label, kind=kind))
            problem = _check_circuit(test, rebuild_circuit(artifact), language)
            if problem:
                return _fail(test, problem)
        elif check == "statevector":
            if kind not in ("circuit", "statevector", "value"):
                return _fail(test, message(language, "unsupported", target=label, kind=kind))
            actual = _state_of(artifact)
            expected = _expected_vector(test["expected"], None)
            if actual.shape != expected.shape:
                width = int(round(math.log2(len(expected))))
                return _fail(test, message(language, "wrong_qubits", got=int(round(math.log2(len(actual)))),
                                           expected=width))  # fmt: skip
            if test.get("global_phase", True):
                overlap = np.vdot(actual, expected)
                if abs(overlap) > 1e-12:
                    actual = actual * (overlap / abs(overlap))
            diff = float(np.max(np.abs(actual - expected)))
            if diff > tolerance:
                return _fail(test, message(language, "sv_mismatch", diff=diff))
        elif check == "probabilities":
            if kind not in ("circuit", "statevector", "value"):
                return _fail(test, message(language, "unsupported", target=label, kind=kind))
            if kind == "value" and isinstance(artifact.get("data"), dict):
                actual_dist = {k.replace(" ", ""): parse_number(v).real for k, v in artifact["data"].items()}
            else:
                state = _state_of(artifact)
                width = int(round(math.log2(len(state))))
                actual_dist = {format(i, f"0{width}b"): float(abs(a) ** 2) for i, a in enumerate(state)}
            expected_dist = {k.replace(" ", ""): parse_number(v).real for k, v in test["expected"].items()}
            keys = set(actual_dist) | set(expected_dist)
            diff = max((abs(actual_dist.get(k, 0.0) - expected_dist.get(k, 0.0)) for k in keys), default=0.0)
            if diff > tolerance:
                return _fail(test, message(language, "prob_mismatch", diff=diff))
        elif check == "counts":
            if kind == "counts":
                data = artifact.get("data") or {}
                total = sum(v for v in data.values() if isinstance(v, int) and v > 0)
                if total <= 0:
                    return _fail(test, message(language, "counts_mismatch", diff=1.0, tol=tolerance))
                actual_dist = {k.replace(" ", ""): v / total for k, v in data.items() if isinstance(v, int)}
            elif kind == "circuit":
                try:
                    actual_dist = _measured_distribution(rebuild_circuit(artifact))
                except QclabError as exc:
                    if str(exc) == "no_measure":
                        return _fail(test, message(language, "no_measure"))
                    raise
            else:
                return _fail(test, message(language, "unsupported", target=label, kind=kind))
            expected_dist = _distribution(test["expected"])
            keys = set(actual_dist) | set(expected_dist)
            distance = 0.5 * sum(abs(actual_dist.get(k, 0.0) - expected_dist.get(k, 0.0)) for k in keys)
            if distance > tolerance:
                return _fail(test, message(language, "counts_mismatch", diff=distance, tol=tolerance))
        elif check == "unitary":
            if kind == "circuit":
                circuit = rebuild_circuit(artifact)
                if circuit.num_qubits > MAX_OPERATOR_QUBITS:
                    return _fail(test, message(language, "unsupported", target=label, kind="circuit"))
                actual = Operator(circuit.remove_final_measurements(inplace=False)).data
            elif kind == "operator" or (kind == "value" and isinstance(artifact.get("data"), list)):
                rows = artifact["data"]
                actual = np.array([[parse_number(v) for v in row] for row in rows], dtype=complex)
            else:
                return _fail(test, message(language, "unsupported", target=label, kind=kind))
            expected = np.array([[parse_number(v) for v in row] for row in test["expected"]], dtype=complex)
            if actual.shape != expected.shape:
                return _fail(test, message(language, "unitary_mismatch", diff=float("inf")))
            if test.get("global_phase", True):
                index = int(np.argmax(np.abs(expected)))
                ref = expected.reshape(-1)[index]
                got = actual.reshape(-1)[index]
                if abs(got) > 1e-12:
                    actual = actual * (ref / got) / abs(ref / got)
            diff = float(np.max(np.abs(actual - expected)))
            if diff > tolerance:
                return _fail(test, message(language, "unitary_mismatch", diff=diff))
        elif check == "value":
            actual = artifact.get("data") if kind in ("value", "counts") else None
            if kind not in ("value", "counts") or not _values_equal(test["expected"], actual, tolerance):
                return _fail(test, message(language, "value_mismatch"))
    except (QclabError, SpecError, TypeError, ValueError, KeyError, IndexError, OverflowError) as exc:
        return _fail(test, message(language, "eval_error", target=label, error=str(exc)[:300]))
    return Outcome(test["id"], test["name"], float(test["points"]), float(test["points"]), True,
                   message(language, "ok"))  # fmt: skip


def evaluate_all(tests: list[dict], artifacts: dict[str, dict], language: str = "pl") -> list[Outcome]:
    return [evaluate(test, artifacts.get(target_key(test["target"])), language) for test in tests]


# --- testy widoczne w notatniku -------------------------------------------------------------------


def check(tests, namespace: dict | None = None, language: str = "pl") -> dict:
    """Uruchamia testy widoczne na zmiennych notatnika i drukuje tabelkę wyników.

    Ostatnia komórka notatnika startowego woła ``check(TESTS)`` – ``namespace`` to wtedy globalne
    zmienne notatnika (ramka wywołującego).
    """
    if namespace is None:
        import sys

        namespace = sys._getframe(1).f_globals
    cleaned = validate_tests(tests)
    artifacts = {}
    for target in targets_of(cleaned):
        artifact = extract_artifact(namespace, target)
        artifacts[target["key"]] = json.loads(json.dumps(artifact))
    outcomes = evaluate_all(cleaned, artifacts, language)
    total = sum(o.max_points for o in outcomes)
    points = sum(o.points for o in outcomes)
    for outcome in outcomes:
        mark = "✔" if outcome.passed else "✘"
        print(f"{mark} {outcome.name}  [{outcome.points:g}/{outcome.max_points:g}]  {outcome.message}")
    print(message(language, "summary", points=f"{points:g}", total=f"{total:g}"))
    print(message(language, "hidden_note"))
    return {"points": points, "total": total, "outcomes": [o.as_dict() for o in outcomes]}
