"""Symulator wektora stanu: bramki, pomiar końcowy (próbkowanie) i pomiar w trakcie (trajektorie).

Dwie ścieżki liczenia wyników pomiaru, bo różnią się kosztem o rzędy wielkości:

- **pomiary wyłącznie na końcu** (najczęstszy przypadek: ``measure_all()`` po obwodzie) – jeden
  przebieg wektora stanu i losowanie ``shots`` wyników z rozkładu ``|amplituda|²``,
- **pomiar w trakcie albo reset** – każdy strzał osobno (kolaps stanu po pomiarze). Koszt to
  ``shots × liczba operacji × 2**n``, więc limit strzałów jest tu niższy (``MAX_TRAJECTORY_WORK``).
"""

from __future__ import annotations

import cmath

import numpy as np

from .circuit import Instruction, ParameterExpression, QuantumCircuit, bind_value
from .exceptions import QclabError

#: Górna granica „strzały × operacje × amplitudy” dla trajektorii. 2·10⁸ to kilka sekund NumPy
#: na serwerze i w przeglądarce; większe zadanie kończy się czytelnym błędem, a nie zawieszoną kartą.
MAX_TRAJECTORY_WORK = 200_000_000
MAX_SHOTS = 1_000_000


def apply_matrix(state: np.ndarray, num_qubits: int, matrix: np.ndarray, qubits: list[int]) -> np.ndarray:
    """``matrix`` (konwencja Qiskita: ``qubits[0]`` = najmłodszy bit macierzy) na ``state``."""
    k = len(qubits)
    psi = state.reshape([2] * num_qubits)
    tensor = np.asarray(matrix, dtype=complex).reshape([2] * (2 * k))
    # Oś ``a`` tensora bramki (C-order, oś 0 = najstarszy bit) odpowiada ``qubits[k-1-a]``,
    # a kubit ``q`` wektora stanu leży na osi ``n-1-q``.
    state_axes = [num_qubits - 1 - qubits[k - 1 - a] for a in range(k)]
    result = np.tensordot(tensor, psi, axes=(list(range(k, 2 * k)), state_axes))
    result = np.moveaxis(result, list(range(k)), state_axes)
    return result.reshape(-1)


def _check_qubits(num_qubits: int) -> None:
    from .exceptions import MAX_QUBITS

    if num_qubits > MAX_QUBITS:
        raise QclabError(f"The simulator supports at most {MAX_QUBITS} qubits.")


def _apply_operation(
    state: np.ndarray, num_qubits: int, operation: Instruction, qubits: list[int]
) -> np.ndarray:
    if operation.definition is not None and operation._matrix is None:
        sub = operation.definition
        for sub_op, sub_q, _sub_c in sub.instruction_indices():
            if sub_op.name == "barrier":
                continue
            if not sub_op.is_unitary:
                raise QclabError(f"Composite gate {operation.name} contains operation {sub_op.name}.")
            state = _apply_operation(state, num_qubits, sub_op, [qubits[i] for i in sub_q])
        phase = bind_value(sub.global_phase)
        if isinstance(phase, ParameterExpression):
            raise QclabError("The global phase of a composite gate has an unbound parameter.")
        return state * cmath.exp(1j * phase) if phase else state
    return apply_matrix(state, num_qubits, operation.to_matrix(), qubits)


def _global_phase(circuit: QuantumCircuit) -> complex:
    phase = bind_value(circuit.global_phase)
    if isinstance(phase, ParameterExpression):
        raise QclabError("The global phase of the circuit has an unbound parameter.")
    return cmath.exp(1j * phase)


def evolve_unitary(
    circuit: QuantumCircuit, state: np.ndarray, *, stop_at_save: bool = False, skip_measure: bool = False
) -> np.ndarray:
    """Wektor stanu po części unitarnej. Pomiar/reset → błąd jak w ``Statevector`` Qiskita."""
    _check_qubits(circuit.num_qubits)
    n = circuit.num_qubits
    for operation, qubits, _clbits in circuit.instruction_indices():
        name = operation.name
        if name in ("barrier", "delay"):
            continue
        if name == "save_statevector":
            if stop_at_save:
                break
            continue
        if name == "measure" and skip_measure:
            continue
        if not operation.is_unitary:
            raise QclabError(
                f"Cannot compute a statevector of a circuit containing '{name}'. "
                "Remove measurements (e.g. qc.remove_final_measurements()) or use a sampler/simulator."
            )
        state = _apply_operation(state, n, operation, qubits)
    return state * _global_phase(circuit)


def zero_state(num_qubits: int) -> np.ndarray:
    _check_qubits(num_qubits)
    state = np.zeros(2**num_qubits, dtype=complex)
    state[0] = 1.0
    return state


def _terminal_measurements(circuit: QuantumCircuit) -> list[tuple[int, int]] | None:
    """Pary (kubit, bit) pomiarów końcowych albo ``None``, gdy pomiar/reset jest w trakcie."""
    measured: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for operation, qubits, clbits in circuit.instruction_indices():
        name = operation.name
        if name in ("barrier", "save_statevector", "delay"):
            continue
        if name == "reset":
            return None
        if name == "measure":
            measured.add(qubits[0])
            pairs.append((qubits[0], clbits[0]))
            continue
        if measured.intersection(qubits):
            return None
    return pairs


def _key(circuit: QuantumCircuit, clbit_values: list[int]) -> str:
    """Klucz wyniku jak w ``get_counts()`` Qiskita: rejestry od ostatniego, bit 0 z prawej."""
    if not circuit.cregs:
        return "".join(str(v) for v in reversed(clbit_values))
    parts = []
    for reg in reversed(circuit.cregs):
        indices = [circuit.find_bit(bit).index for bit in reg]
        parts.append("".join(str(clbit_values[i]) for i in reversed(indices)))
    return " ".join(parts)


def sample_counts(
    circuit: QuantumCircuit, shots: int = 1024, seed: int | None = None, memory: bool = False
) -> dict[str, int] | tuple[dict[str, int], list[str]]:
    """Wyniki pomiarów obwodu (``{"00": 512, "11": 512}``)."""
    shots = int(shots)
    if not 1 <= shots <= MAX_SHOTS:
        raise QclabError(f"shots must be between 1 and {MAX_SHOTS}.")
    rng = np.random.default_rng(seed)
    n = circuit.num_qubits
    pairs = _terminal_measurements(circuit)
    shots_list: list[str] = []
    if pairs is not None:
        # Pomiary są tu wyłącznie końcowe (sprawdzone wyżej), więc stan przed nimi = stan końcowy.
        state = evolve_unitary(circuit, zero_state(n), skip_measure=True)
        probs = np.abs(state) ** 2
        probs = probs / probs.sum()
        samples = rng.choice(len(probs), size=shots, p=probs)
        values, counts = np.unique(samples, return_counts=True)
        result: dict[str, int] = {}
        keys_for: dict[int, str] = {}
        for value, count in zip(values.tolist(), counts.tolist(), strict=True):
            clbits = [0] * circuit.num_clbits
            for qubit, clbit in pairs:
                clbits[clbit] = (value >> qubit) & 1
            key = _key(circuit, clbits)
            keys_for[value] = key
            result[key] = result.get(key, 0) + count
        if memory:
            shots_list = [keys_for[v] for v in samples.tolist()]
            return result, shots_list
        return result
    work = shots * max(1, len(circuit.data)) * (2**n)
    if work > MAX_TRAJECTORY_WORK:
        raise QclabError(
            "A circuit with mid-circuit measurement or reset is too large to simulate this many shots – "
            "reduce shots or qubits."
        )
    result = {}
    ops = circuit.instruction_indices()
    for _ in range(shots):
        state = zero_state(n)
        clbits = [0] * circuit.num_clbits
        for operation, qubits, cbits in ops:
            name = operation.name
            if name in ("barrier", "save_statevector", "delay"):
                continue
            if name in ("measure", "reset"):
                outcome, state = _collapse(state, n, qubits[0], rng)
                if name == "measure":
                    clbits[cbits[0]] = outcome
                elif outcome:
                    state = apply_matrix(state, n, np.array([[0, 1], [1, 0]], dtype=complex), qubits)
                continue
            state = _apply_operation(state, n, operation, qubits)
        key = _key(circuit, clbits)
        result[key] = result.get(key, 0) + 1
        if memory:
            shots_list.append(key)
    return (result, shots_list) if memory else result


#: Najwięcej gałęzi dokładnego rozkładu (każdy pomiar/reset w trakcie podwaja ich liczbę).
MAX_EXACT_BRANCHES = 4096


def exact_distribution(
    circuit: QuantumCircuit, max_branches: int = MAX_EXACT_BRANCHES
) -> dict[str, float] | None:
    """Dokładny rozkład wyników pomiarów także przy pomiarze w trakcie i resecie – albo ``None``.

    Każdy pomiar rozgałęzia stan na wynik 0 i 1 z ich prawdopodobieństwami (gałęzie o zerowym
    prawdopodobieństwie odpadają). ``None``, gdy gałęzi byłoby więcej niż ``max_branches`` – wtedy
    wołający losuje (``sample_counts``).
    """
    _check_qubits(circuit.num_qubits)
    n = circuit.num_qubits
    x_gate = np.array([[0, 1], [1, 0]], dtype=complex)
    branches: list[tuple[float, np.ndarray, tuple]] = [(1.0, zero_state(n), (0,) * circuit.num_clbits)]
    for operation, qubits, cbits in circuit.instruction_indices():
        name = operation.name
        if name in ("barrier", "save_statevector", "delay"):
            continue
        if name in ("measure", "reset"):
            qubit = qubits[0]
            mask = ((np.arange(2**n) >> qubit) & 1).astype(bool)
            split = []
            for prob, state, clbits in branches:
                p1 = float(np.sum(np.abs(state[mask]) ** 2))
                for outcome, p_out in ((0, 1.0 - p1), (1, p1)):
                    if p_out <= 1e-15:
                        continue
                    keep = mask if outcome else ~mask
                    collapsed = np.where(keep, state, 0) / np.sqrt(p_out)
                    new_bits = clbits
                    if name == "measure":
                        new_bits = clbits[: cbits[0]] + (outcome,) + clbits[cbits[0] + 1 :]
                    elif outcome:
                        collapsed = apply_matrix(collapsed, n, x_gate, qubits)
                    split.append((prob * p_out, collapsed, new_bits))
            if len(split) > max_branches:
                return None
            branches = split
            continue
        branches = [
            (prob, _apply_operation(state, n, operation, qubits), clbits) for prob, state, clbits in branches
        ]
    result: dict[str, float] = {}
    for prob, _state, clbits in branches:
        key = _key(circuit, list(clbits))
        result[key] = result.get(key, 0.0) + prob
    return result


def _collapse(state: np.ndarray, n: int, qubit: int, rng) -> tuple[int, np.ndarray]:
    indices = np.arange(len(state))
    mask = ((indices >> qubit) & 1).astype(bool)
    p1 = float(np.sum(np.abs(state[mask]) ** 2))
    outcome = 1 if rng.random() < p1 else 0
    keep = mask if outcome else ~mask
    collapsed = np.where(keep, state, 0)
    norm = np.linalg.norm(collapsed)
    return outcome, collapsed / norm if norm else collapsed
