"""``Statevector``, ``Operator``, ``Pauli`` i ``SparsePauliOp`` – podzbiór ``qiskit.quantum_info``."""

from __future__ import annotations

import math
import numbers

import numpy as np

from .circuit import Instruction, QuantumCircuit
from .exceptions import MAX_OPERATOR_QUBITS, MAX_QUBITS, QclabError
from .simulator import apply_matrix, evolve_unitary, sample_counts, zero_state

_PAULI = {
    "I": np.eye(2, dtype=complex),
    "X": np.array([[0, 1], [1, 0]], dtype=complex),
    "Y": np.array([[0, -1j], [1j, 0]], dtype=complex),
    "Z": np.array([[1, 0], [0, -1]], dtype=complex),
}


def _num_qubits_of(dim: int) -> int:
    n = int(round(math.log2(dim))) if dim > 0 else -1
    if n < 0 or 2**n != dim:
        raise QclabError(f"Dimension {dim} is not a power of two – only qubits are supported.")
    return n


def _label(index: int, num_qubits: int) -> str:
    return format(index, f"0{num_qubits}b") if num_qubits else ""


def _round(values: np.ndarray, decimals):
    return np.round(values, decimals) if decimals is not None else values


class Statevector:
    def __init__(self, data, dims=None) -> None:
        if isinstance(data, Statevector):
            vector = data.data.copy()
        elif isinstance(data, QuantumCircuit):
            vector = evolve_unitary(data, zero_state(data.num_qubits))
        elif isinstance(data, Instruction):
            circuit = QuantumCircuit(data.num_qubits)
            circuit.append(data, range(data.num_qubits))
            vector = evolve_unitary(circuit, zero_state(data.num_qubits))
        else:
            vector = np.asarray(data, dtype=complex).reshape(-1)
        self._num_qubits = _num_qubits_of(len(vector))
        if self._num_qubits > MAX_QUBITS:
            raise QclabError(f"The statevector has too many qubits (max {MAX_QUBITS}).")
        self._data = vector

    # --- konstruktory ---

    @classmethod
    def from_label(cls, label: str) -> Statevector:
        single = {
            "0": np.array([1, 0], dtype=complex),
            "1": np.array([0, 1], dtype=complex),
            "+": np.array([1, 1], dtype=complex) / math.sqrt(2),
            "-": np.array([1, -1], dtype=complex) / math.sqrt(2),
            "r": np.array([1, 1j], dtype=complex) / math.sqrt(2),
            "l": np.array([1, -1j], dtype=complex) / math.sqrt(2),
        }
        vector = np.array([1], dtype=complex)
        for char in label:
            if char not in single:
                raise QclabError(f"Unknown state label character: {char!r} (allowed: 0 1 + - r l).")
            # Pierwszy znak to najstarszy kubit – iloczyn tensorowy w kolejności zapisu.
            vector = np.kron(vector, single[char])
        return cls(vector)

    @classmethod
    def from_int(cls, i: int, dims) -> Statevector:
        dim = int(np.prod(dims)) if isinstance(dims, (list, tuple)) else int(dims)
        if not 0 <= int(i) < dim:
            raise QclabError(f"Index {i} out of range for dimension {dim}.")
        vector = np.zeros(dim, dtype=complex)
        vector[int(i)] = 1
        return cls(vector)

    @classmethod
    def from_instruction(cls, instruction) -> Statevector:
        return cls(instruction)

    # --- własności ---

    @property
    def data(self) -> np.ndarray:
        return self._data

    @property
    def num_qubits(self) -> int:
        return self._num_qubits

    @property
    def dim(self) -> int:
        return len(self._data)

    def dims(self, qargs=None) -> tuple:
        return (2,) * (len(qargs) if qargs is not None else self._num_qubits)

    def __array__(self, dtype=None, copy=None):
        return np.asarray(self._data, dtype=dtype)

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._data[int(key, 2)]
        return self._data[key]

    def is_valid(self, atol: float = 1e-8, rtol: float = 1e-5) -> bool:
        return bool(np.isclose(np.linalg.norm(self._data), 1.0, atol=atol, rtol=rtol))

    # --- prawdopodobieństwa ---

    def probabilities(self, qargs=None, decimals=None) -> np.ndarray:
        probs = np.abs(self._data) ** 2
        if qargs is None:
            return _round(probs, decimals)
        qargs = [int(q) for q in qargs]
        n = self._num_qubits
        if any(not 0 <= q < n for q in qargs) or len(set(qargs)) != len(qargs):
            raise QclabError(f"Invalid qubit list: {qargs}")
        tensor = probs.reshape([2] * n)
        keep_axes = [n - 1 - q for q in qargs]
        drop = tuple(a for a in range(n) if a not in keep_axes)
        marginal = tensor.sum(axis=drop) if drop else tensor
        # Pozostałe osie stoją w kolejności rosnącej osi; wynik ma mieć qargs[0] jako najmłodszy bit.
        remaining = sorted(keep_axes)
        order = [remaining.index(n - 1 - q) for q in reversed(qargs)]
        marginal = np.transpose(marginal, order) if len(order) > 1 else marginal
        return _round(np.asarray(marginal).reshape(-1), decimals)

    def probabilities_dict(self, qargs=None, decimals=None) -> dict[str, float]:
        probs = self.probabilities(qargs, decimals)
        width = len(qargs) if qargs is not None else self._num_qubits
        # Próg zamiast ``nonzero()`` Qiskita: szum zmiennoprzecinkowy (1e-33 po H·H) zaśmiecałby
        # słownik pozycjami, których uczeń nie ma jak zinterpretować.
        return {_label(i, width): float(p) for i, p in enumerate(probs) if p > 1e-15}

    def to_dict(self, decimals=None) -> dict[str, complex]:
        values = _round(self._data, decimals)
        return {_label(i, self._num_qubits): complex(v) for i, v in enumerate(values) if abs(v) > 0}

    def sample_counts(self, shots: int, qargs=None, seed: int | None = None) -> dict[str, int]:
        probs = self.probabilities(qargs)
        width = len(qargs) if qargs is not None else self._num_qubits
        rng = np.random.default_rng(seed)
        samples = rng.choice(len(probs), size=int(shots), p=probs / probs.sum())
        values, counts = np.unique(samples, return_counts=True)
        return {_label(v, width): int(c) for v, c in zip(values.tolist(), counts.tolist(), strict=True)}

    def sample_memory(self, shots: int, qargs=None, seed: int | None = None) -> np.ndarray:
        probs = self.probabilities(qargs)
        width = len(qargs) if qargs is not None else self._num_qubits
        rng = np.random.default_rng(seed)
        samples = rng.choice(len(probs), size=int(shots), p=probs / probs.sum())
        return np.array([_label(v, width) for v in samples.tolist()])

    # --- algebra ---

    def evolve(self, other, qargs=None) -> Statevector:
        if isinstance(other, QuantumCircuit):
            if qargs is None:
                if other.num_qubits != self._num_qubits:
                    raise QclabError("evolve(): the circuit and the state must have the same number of qubits.")
                return Statevector(evolve_unitary(other, self._data.copy()))
            other = Operator(other)
        if isinstance(other, Instruction):
            other = Operator(other)
        if not isinstance(other, Operator):
            other = Operator(other)
        qubits = list(range(self._num_qubits)) if qargs is None else [int(q) for q in qargs]
        if other.num_qubits != len(qubits):
            raise QclabError("evolve(): the operator size must match qargs.")
        return Statevector(apply_matrix(self._data.copy(), self._num_qubits, other.data, qubits))

    def inner(self, other) -> complex:
        other = other if isinstance(other, Statevector) else Statevector(other)
        return complex(np.vdot(self._data, other.data))

    def equiv(self, other, rtol: float = 1e-5, atol: float = 1e-8) -> bool:
        """Równość z dokładnością do fazy globalnej (jak ``Statevector.equiv`` w Qiskicie)."""
        other = other if isinstance(other, Statevector) else Statevector(other)
        if other.dim != self.dim:
            return False
        overlap = np.vdot(other.data, self._data)
        if abs(overlap) < atol:
            return bool(np.allclose(self._data, 0, atol=atol) and np.allclose(other.data, 0, atol=atol))
        phase = overlap / abs(overlap)
        return bool(np.allclose(self._data, other.data * phase, rtol=rtol, atol=atol))

    def conjugate(self) -> Statevector:
        return Statevector(self._data.conj())

    def tensor(self, other) -> Statevector:
        other = other if isinstance(other, Statevector) else Statevector(other)
        return Statevector(np.kron(self._data, other.data))

    def expand(self, other) -> Statevector:
        other = other if isinstance(other, Statevector) else Statevector(other)
        return Statevector(np.kron(other.data, self._data))

    def expectation_value(self, oper, qargs=None) -> complex:
        if isinstance(oper, str):
            oper = Pauli(oper)
        if isinstance(oper, (Pauli, SparsePauliOp)):
            matrix = oper.to_matrix()
        elif isinstance(oper, Operator):
            matrix = oper.data
        else:
            matrix = Operator(oper).data
        qubits = list(range(self._num_qubits)) if qargs is None else [int(q) for q in qargs]
        if matrix.shape[0] != 2 ** len(qubits):
            raise QclabError("expectation_value(): the operator size does not match the qubits.")
        evolved = apply_matrix(self._data, self._num_qubits, matrix, qubits)
        return complex(np.vdot(self._data, evolved))

    def __eq__(self, other) -> bool:
        if not isinstance(other, Statevector):
            return NotImplemented
        return self.dim == other.dim and bool(np.allclose(self._data, other.data, rtol=1e-5, atol=1e-8))

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        values = np.array2string(self._data, precision=4, separator=", ", suppress_small=True)
        return f"Statevector({values},\n            dims={self.dims()})"

    def draw(self, output: str | None = None, **kwargs):
        if output in ("text", None, "repr"):
            return repr(self)
        if output == "latex_source" or output == "latex":
            terms = []
            for label, amplitude in self.to_dict(decimals=4).items():
                terms.append(f"({amplitude.real:.4g}{amplitude.imag:+.4g}i)|{label}\\rangle")
            return " + ".join(terms)
        print(f"[qclab] draw(output={output!r}) is not available here – showing text.")
        return repr(self)


class Operator:
    def __init__(self, data, input_dims=None, output_dims=None) -> None:
        if isinstance(data, Operator):
            matrix = data.data.copy()
        elif isinstance(data, QuantumCircuit):
            if data.num_qubits > MAX_OPERATOR_QUBITS:
                raise QclabError(f"Operator(): at most {MAX_OPERATOR_QUBITS} qubits.")
            n = data.num_qubits
            dim = 2**n
            # Kolumna j macierzy to obraz stanu bazowego |j>.
            matrix = np.zeros((dim, dim), dtype=complex)
            for col in range(dim):
                basis = np.zeros(dim, dtype=complex)
                basis[col] = 1
                matrix[:, col] = evolve_unitary(data, basis)
        elif isinstance(data, Instruction):
            matrix = data.to_matrix()
        elif isinstance(data, (Pauli, SparsePauliOp)):
            matrix = data.to_matrix()
        else:
            matrix = np.asarray(data, dtype=complex)
        if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
            raise QclabError("An Operator must be a square matrix.")
        self._num_qubits = _num_qubits_of(matrix.shape[0])
        self._data = matrix

    @classmethod
    def from_label(cls, label: str) -> Operator:
        return cls(Pauli(label).to_matrix())

    @property
    def data(self) -> np.ndarray:
        return self._data

    @property
    def num_qubits(self) -> int:
        return self._num_qubits

    @property
    def dim(self) -> tuple[int, int]:
        return self._data.shape

    def to_matrix(self) -> np.ndarray:
        return self._data

    def __array__(self, dtype=None, copy=None):
        return np.asarray(self._data, dtype=dtype)

    def is_unitary(self, atol: float = 1e-8, rtol: float = 1e-5) -> bool:
        identity = np.eye(self._data.shape[0])
        return bool(np.allclose(self._data @ self._data.conj().T, identity, atol=atol, rtol=rtol))

    def adjoint(self) -> Operator:
        return Operator(self._data.conj().T)

    def conjugate(self) -> Operator:
        return Operator(self._data.conj())

    def transpose(self) -> Operator:
        return Operator(self._data.T)

    def compose(self, other, qargs=None, front: bool = False) -> Operator:
        """``A.compose(B)`` = najpierw A, potem B (macierz ``B·A``), jak w Qiskicie."""
        other = other if isinstance(other, Operator) else Operator(other)
        if qargs is not None:
            raise QclabError("Operator.compose(qargs=...) is not supported.")
        return Operator(self._data @ other.data if front else other.data @ self._data)

    def dot(self, other, qargs=None) -> Operator:
        return self.compose(other, qargs, front=True)

    def tensor(self, other) -> Operator:
        other = other if isinstance(other, Operator) else Operator(other)
        return Operator(np.kron(self._data, other.data))

    def expand(self, other) -> Operator:
        other = other if isinstance(other, Operator) else Operator(other)
        return Operator(np.kron(other.data, self._data))

    def power(self, n: int) -> Operator:
        return Operator(np.linalg.matrix_power(self._data, int(n)))

    def equiv(self, other, rtol: float = 1e-5, atol: float = 1e-8) -> bool:
        other = other if isinstance(other, Operator) else Operator(other)
        if other.data.shape != self._data.shape:
            return False
        flat_a, flat_b = self._data.reshape(-1), other.data.reshape(-1)
        index = int(np.argmax(np.abs(flat_b)))
        if abs(flat_b[index]) < atol:
            return bool(np.allclose(flat_a, 0, atol=atol))
        phase = flat_a[index] / flat_b[index]
        if not np.isclose(abs(phase), 1, rtol=rtol, atol=atol):
            return False
        return bool(np.allclose(flat_a, flat_b * phase, rtol=rtol, atol=atol))

    def __matmul__(self, other) -> Operator:
        other = other if isinstance(other, Operator) else Operator(other)
        return Operator(self._data @ other.data)

    def __mul__(self, other) -> Operator:
        if isinstance(other, numbers.Number):
            return Operator(self._data * other)
        return NotImplemented

    __rmul__ = __mul__

    def __add__(self, other) -> Operator:
        other = other if isinstance(other, Operator) else Operator(other)
        return Operator(self._data + other.data)

    def __sub__(self, other) -> Operator:
        other = other if isinstance(other, Operator) else Operator(other)
        return Operator(self._data - other.data)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Operator):
            return NotImplemented
        return self._data.shape == other.data.shape and bool(np.allclose(self._data, other.data))

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        values = np.array2string(self._data, precision=4, separator=", ", suppress_small=True)
        return f"Operator({values},\n         input_dims={(2,) * self._num_qubits}, output_dims={(2,) * self._num_qubits})"


class Pauli:
    """Operator Pauliego z etykiety (``"XZ"`` – ostatni znak działa na kubit 0), z fazą ``-``/``i``."""

    def __init__(self, label: str) -> None:
        text = str(label).strip()
        phase = 1 + 0j
        for prefix, value in (("-i", -1j), ("+i", 1j), ("i", 1j), ("-", -1), ("+", 1)):
            if text.startswith(prefix) and len(text) > len(prefix) and text[len(prefix)] in _PAULI:
                phase = value
                text = text[len(prefix) :]
                break
        if not text or any(ch not in _PAULI for ch in text):
            raise QclabError(f"Invalid Pauli label: {label!r}")
        if len(text) > MAX_QUBITS:
            raise QclabError("Pauli label too long.")
        self.label = text
        self.phase = phase

    @property
    def num_qubits(self) -> int:
        return len(self.label)

    def to_matrix(self) -> np.ndarray:
        if len(self.label) > MAX_OPERATOR_QUBITS:
            raise QclabError(f"Pauli matrix: at most {MAX_OPERATOR_QUBITS} qubits.")
        matrix = np.array([[1]], dtype=complex)
        for char in self.label:
            matrix = np.kron(matrix, _PAULI[char])
        return self.phase * matrix

    def __repr__(self) -> str:
        return f"Pauli('{self.label}')"


class SparsePauliOp:
    """Suma ważonych Paulich: ``SparsePauliOp(["ZZ", "XI"], coeffs=[1, 0.5])``."""

    def __init__(self, data, coeffs=None) -> None:
        if isinstance(data, SparsePauliOp):
            self.paulis = list(data.paulis)
            self.coeffs = np.array(data.coeffs, dtype=complex)
            return
        labels = [data] if isinstance(data, (str, Pauli)) else list(data)
        self.paulis = [p if isinstance(p, Pauli) else Pauli(p) for p in labels]
        if coeffs is None:
            coeffs = [1.0] * len(self.paulis)
        self.coeffs = np.array(coeffs, dtype=complex).reshape(-1)
        if len(self.coeffs) != len(self.paulis):
            raise QclabError("SparsePauliOp: the number of coefficients must match the number of Paulis.")
        widths = {p.num_qubits for p in self.paulis}
        if len(widths) > 1:
            raise QclabError("SparsePauliOp: all labels must have the same length.")

    @classmethod
    def from_list(cls, obj, num_qubits=None) -> SparsePauliOp:
        items = list(obj)
        return cls([label for label, _ in items], [coeff for _, coeff in items])

    @property
    def num_qubits(self) -> int:
        return self.paulis[0].num_qubits if self.paulis else 0

    def to_matrix(self, sparse: bool = False) -> np.ndarray:
        dim = 2**self.num_qubits
        total = np.zeros((dim, dim), dtype=complex)
        for pauli, coeff in zip(self.paulis, self.coeffs, strict=True):
            total += coeff * pauli.to_matrix()
        return total

    def to_list(self) -> list[tuple[str, complex]]:
        return [(p.label, complex(c * p.phase)) for p, c in zip(self.paulis, self.coeffs, strict=True)]

    def __repr__(self) -> str:
        return f"SparsePauliOp({[p.label for p in self.paulis]},\n              coeffs={self.coeffs})"


def state_fidelity(state1, state2, validate: bool = True) -> float:
    """Wierność dwóch stanów czystych ``|<a|b>|²``."""
    a = state1 if isinstance(state1, Statevector) else Statevector(state1)
    b = state2 if isinstance(state2, Statevector) else Statevector(state2)
    if validate and not (a.is_valid() and b.is_valid()):
        raise QclabError("state_fidelity(): input states must be normalised.")
    return float(abs(np.vdot(a.data, b.data)) ** 2)


def random_statevector(dims, seed=None) -> Statevector:
    dim = int(np.prod(dims)) if isinstance(dims, (list, tuple)) else int(dims)
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=dim) + 1j * rng.normal(size=dim)
    return Statevector(vector / np.linalg.norm(vector))


__all__ = ["Operator", "Pauli", "SparsePauliOp", "Statevector", "random_statevector", "sample_counts", "state_fidelity"]
