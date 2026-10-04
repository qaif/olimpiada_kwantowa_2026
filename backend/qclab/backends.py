"""Symulatory w stylu ``BasicSimulator``/``AerSimulator`` i prymitywy V2 (``StatevectorSampler``).

Wszystko liczy ten sam ``qclab.simulator`` – nazwy klas są tylko po to, żeby kod z materiałów
Qiskita (``AerSimulator().run(qc, shots=1000).result().get_counts()``) działał bez zmian.
"""

from __future__ import annotations

import numpy as np

from .circuit import ParameterExpression, QuantumCircuit
from .exceptions import QclabError
from .quantum_info import Pauli, SparsePauliOp, Statevector
from .simulator import evolve_unitary, sample_counts, zero_state

DEFAULT_SHOTS = 1024


class Result:
    def __init__(self, circuits: list[QuantumCircuit], counts: list, memories: list, statevectors: list) -> None:
        self._circuits = circuits
        self._counts = counts
        self._memories = memories
        self._statevectors = statevectors
        self.success = True

    def _index(self, experiment) -> int:
        if experiment is None:
            return 0
        if isinstance(experiment, int):
            return experiment
        if isinstance(experiment, QuantumCircuit):
            for i, circuit in enumerate(self._circuits):
                if circuit is experiment:
                    return i
        if isinstance(experiment, str):
            for i, circuit in enumerate(self._circuits):
                if circuit.name == experiment:
                    return i
        raise QclabError(f"No result for experiment {experiment!r}.")

    def get_counts(self, experiment=None):
        if experiment is None and len(self._counts) > 1:
            return [dict(c) for c in self._counts]
        counts = self._counts[self._index(experiment)]
        if counts is None:
            raise QclabError("This experiment has no counts (no measurements in the circuit).")
        return dict(counts)

    def get_memory(self, experiment=None) -> list[str]:
        memory = self._memories[self._index(experiment)]
        if memory is None:
            raise QclabError("Memory was not requested – run(..., memory=True).")
        return list(memory)

    def get_statevector(self, experiment=None, decimals=None):
        state = self._statevectors[self._index(experiment)]
        if state is None:
            raise QclabError("No statevector saved – add qc.save_statevector() before running.")
        return np.round(state, decimals) if decimals is not None else state


class Job:
    def __init__(self, result) -> None:
        self._result = result

    def result(self):
        return self._result

    def status(self) -> str:
        return "DONE"

    def job_id(self) -> str:
        return "qclab-local"


def _as_list(circuits) -> list[QuantumCircuit]:
    items = circuits if isinstance(circuits, (list, tuple)) else [circuits]
    for item in items:
        if not isinstance(item, QuantumCircuit):
            raise QclabError(f"Expected a QuantumCircuit, got {item!r}.")
    return list(items)


def _has_measurements(circuit: QuantumCircuit) -> bool:
    return any(op.name == "measure" for op, _q, _c in circuit.instruction_indices())


class BasicSimulator:
    """Odpowiednik ``qiskit.providers.basic_provider.BasicSimulator`` i ``AerSimulator``."""

    def __init__(self, method: str = "automatic", **options) -> None:
        self.name = "basic_simulator"
        self.method = method
        self.options = options
        self.num_qubits = 20

    def run(self, circuits, shots: int | None = None, seed_simulator: int | None = None, memory: bool = False, **kw):
        items = _as_list(circuits)
        shots = int(shots or self.options.get("shots") or DEFAULT_SHOTS)
        counts, memories, states = [], [], []
        for circuit in items:
            if any(isinstance(p, ParameterExpression) for op, _q, _c in circuit.instruction_indices() for p in op.params):
                raise QclabError("The circuit has unbound parameters – use assign_parameters().")
            saves = any(op.name == "save_statevector" for op, _q, _c in circuit.instruction_indices())
            wants_state = saves or (self.method == "statevector_only" and not _has_measurements(circuit))
            states.append(
                Statevector(evolve_unitary(circuit, zero_state(circuit.num_qubits), stop_at_save=True))
                if wants_state
                else None
            )
            if _has_measurements(circuit):
                result = sample_counts(circuit, shots, seed_simulator, memory=memory)
                if memory:
                    counts.append(result[0])
                    memories.append(result[1])
                else:
                    counts.append(result)
                    memories.append(None)
            else:
                counts.append(None)
                memories.append(None)
        return Job(Result(items, counts, memories, states))

    def __repr__(self) -> str:
        return f"{type(self).__name__}('{self.name}')"


class AerSimulator(BasicSimulator):
    def __init__(self, method: str = "automatic", **options) -> None:
        super().__init__(method, **options)
        self.name = "aer_simulator"


class StatevectorBackend(BasicSimulator):
    """``Aer.get_backend("statevector_simulator")`` – wynik zawsze z wektorem stanu."""

    def __init__(self) -> None:
        super().__init__("statevector_only")
        self.name = "statevector_simulator"


class _Aer:
    def get_backend(self, name: str = "aer_simulator"):
        if name in ("statevector_simulator", "aer_simulator_statevector"):
            return StatevectorBackend()
        if name in ("qasm_simulator", "aer_simulator"):
            return AerSimulator()
        raise QclabError(f"Unknown backend: {name} (available: aer_simulator, statevector_simulator).")

    def backends(self):
        return [AerSimulator(), StatevectorBackend()]


Aer = _Aer()


# --- prymitywy V2 ---------------------------------------------------------------------------------


class BitArray:
    def __init__(self, bitstrings: list[str], num_bits: int) -> None:
        self._bitstrings = bitstrings
        self.num_bits = num_bits

    @property
    def num_shots(self) -> int:
        return len(self._bitstrings)

    def get_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self._bitstrings:
            counts[item] = counts.get(item, 0) + 1
        return counts

    def get_int_counts(self) -> dict[int, int]:
        return {int(k, 2): v for k, v in self.get_counts().items()}

    def get_bitstrings(self) -> list[str]:
        return list(self._bitstrings)

    def __repr__(self) -> str:
        return f"BitArray(<shape=(), num_shots={self.num_shots}, num_bits={self.num_bits}>)"


class DataBin:
    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)
        self._fields = list(fields)

    def __getitem__(self, key):
        return getattr(self, key)

    def keys(self):
        return list(self._fields)

    def __repr__(self) -> str:
        return f"DataBin({', '.join(self._fields)})"


class PubResult:
    def __init__(self, data: DataBin, metadata: dict | None = None) -> None:
        self.data = data
        self.metadata = metadata or {}

    def join_data(self) -> BitArray:
        fields = self.data.keys()
        if len(fields) != 1:
            raise QclabError("join_data() works for a single classical register only.")
        return self.data[fields[0]]


class PrimitiveResult(list):
    pass


def _bind_pub(circuit: QuantumCircuit, values) -> QuantumCircuit:
    if values is None:
        if circuit.num_parameters:
            raise QclabError("The circuit has parameters – pass values: run([(qc, values)]).")
        return circuit
    array = values if isinstance(values, dict) else np.asarray(values, dtype=float).reshape(-1).tolist()
    return circuit.assign_parameters(array)


def _pubs(pubs) -> list:
    """Lista „pubów”: pojedynczy obwód albo krotka też są przyjmowane, jak w Qiskicie."""
    if isinstance(pubs, (QuantumCircuit, tuple)):
        return [pubs]
    return list(pubs)


class StatevectorSampler:
    def __init__(self, *, default_shots: int = DEFAULT_SHOTS, seed=None) -> None:
        self.default_shots = default_shots
        self.seed = seed

    def run(self, pubs, *, shots: int | None = None):
        results = PrimitiveResult()
        for pub in _pubs(pubs):
            circuit, values, pub_shots = (pub, None, None) if isinstance(pub, QuantumCircuit) else (
                (list(pub) + [None, None])[:3]
            )
            bound = _bind_pub(circuit, values)
            count = int(pub_shots or shots or self.default_shots)
            if not bound.cregs:
                raise QclabError("The circuit has no classical registers – add measurements (measure_all()).")
            _counts, memory = sample_counts(bound, count, self.seed, memory=True)
            fields = {}
            # Klucz ``memory`` ma rejestry od ostatniego, rozdzielone spacją – rozcinamy na pola.
            for position, reg in enumerate(reversed(bound.cregs)):
                fields[reg.name] = BitArray([item.split(" ")[position] for item in memory], len(reg))
            ordered = {reg.name: fields[reg.name] for reg in bound.cregs}
            results.append(PubResult(DataBin(**ordered), {"shots": count}))
        return Job(results)


class StatevectorEstimator:
    def __init__(self, *, default_precision: float = 0.0, seed=None) -> None:
        self.default_precision = default_precision

    def run(self, pubs, *, precision: float | None = None):
        results = PrimitiveResult()
        for pub in _pubs(pubs):
            if isinstance(pub, QuantumCircuit):
                raise QclabError("Estimator pubs are tuples: run([(circuit, observable)]).")
            items = list(pub) + [None]
            circuit, observables, values = items[0], items[1], items[2]
            bound = _bind_pub(circuit, values)
            if any(op.name == "measure" for op, _q, _c in bound.instruction_indices()):
                bound = bound.remove_final_measurements(inplace=False)
            state = Statevector(bound)
            single = not isinstance(observables, (list, tuple))
            obs_list = [observables] if single else list(observables)
            evs = []
            for obs in obs_list:
                if isinstance(obs, str):
                    obs = Pauli(obs)
                if isinstance(obs, dict):
                    obs = SparsePauliOp.from_list(list(obs.items()))
                evs.append(float(np.real(state.expectation_value(obs))))
            evs_value = np.float64(evs[0]) if single else np.array(evs)
            stds = np.float64(0.0) if single else np.zeros(len(evs))
            results.append(PubResult(DataBin(evs=evs_value, stds=stds), {"precision": precision or 0.0}))
        return Job(results)


def transpile(circuits, backend=None, **kwargs):
    """Bez kompilacji: symulator przyjmuje każdą bramkę biblioteki, więc obwód wraca bez zmian."""
    if isinstance(circuits, (list, tuple)):
        return [c.copy() for c in circuits]
    return circuits.copy()


class _PassManager:
    def run(self, circuits):
        return transpile(circuits)


def generate_preset_pass_manager(optimization_level=None, backend=None, **kwargs) -> _PassManager:
    return _PassManager()
