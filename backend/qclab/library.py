"""Klasy bramek (``HGate()``, ``CXGate()``, ``RYGate(θ)``…) i kilka gotowych obwodów (``QFT``)."""

from __future__ import annotations

import math

import numpy as np

from .circuit import Gate, QuantumCircuit
from .exceptions import MAX_OPERATOR_QUBITS, QclabError
from .gates import GATES, X, controlled


def _gate_class(name: str, class_name: str) -> type:
    spec = GATES[name]

    def __init__(self, *params, label: str | None = None) -> None:
        if len(params) != spec.num_params:
            raise QclabError(f"{class_name} takes {spec.num_params} parameter(s), got {len(params)}.")
        Gate.__init__(self, name, spec.num_qubits, list(params), label=label)

    return type(class_name, (Gate,), {"__init__": __init__, "__module__": __name__})


_CLASSES = {
    "IGate": "id", "XGate": "x", "YGate": "y", "ZGate": "z", "HGate": "h", "SGate": "s",
    "SdgGate": "sdg", "TGate": "t", "TdgGate": "tdg", "SXGate": "sx", "SXdgGate": "sxdg",
    "RXGate": "rx", "RYGate": "ry", "RZGate": "rz", "PhaseGate": "p", "UGate": "u",
    "CXGate": "cx", "CYGate": "cy", "CZGate": "cz", "CHGate": "ch", "CSGate": "cs",
    "CSdgGate": "csdg", "CSXGate": "csx", "CPhaseGate": "cp", "CRXGate": "crx", "CRYGate": "cry",
    "CRZGate": "crz", "SwapGate": "swap", "iSwapGate": "iswap", "RXXGate": "rxx", "RYYGate": "ryy",
    "RZZGate": "rzz", "CCXGate": "ccx", "CCZGate": "ccz", "CSwapGate": "cswap",
}  # fmt: skip

globals().update({cls: _gate_class(gate, cls) for cls, gate in _CLASSES.items()})
CnotGate = globals()["CXGate"]
ToffoliGate = globals()["CCXGate"]
FredkinGate = globals()["CSwapGate"]
U3Gate = globals()["UGate"]
U1Gate = globals()["PhaseGate"]


class MCXGate(Gate):
    def __init__(self, num_ctrl_qubits: int, label: str | None = None, ctrl_state=None) -> None:
        if ctrl_state is not None:
            raise QclabError("MCXGate(ctrl_state=...) is not supported.")
        if num_ctrl_qubits + 1 > MAX_OPERATOR_QUBITS:
            raise QclabError(f"MCXGate: at most {MAX_OPERATOR_QUBITS - 1} control qubits.")
        super().__init__("mcx", num_ctrl_qubits + 1, [], matrix=controlled(X, num_ctrl_qubits), label=label)
        self.num_ctrl_qubits = num_ctrl_qubits


class UnitaryGate(Gate):
    def __init__(self, data, label: str | None = None, check_input: bool = True) -> None:
        matrix = np.asarray(getattr(data, "data", data), dtype=complex)
        dim = matrix.shape[0] if matrix.ndim == 2 else 0
        num_qubits = int(round(math.log2(dim))) if dim else 0
        if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or 2**num_qubits != dim:
            raise QclabError("UnitaryGate: the matrix must be 2^n x 2^n.")
        if check_input and not np.allclose(matrix @ matrix.conj().T, np.eye(dim), atol=1e-8):
            raise QclabError("UnitaryGate: the matrix is not unitary.")
        super().__init__("unitary", num_qubits, [], matrix=matrix, label=label)


def QFT(  # noqa: N802 - nazwa jak w Qiskicie
    num_qubits: int,
    approximation_degree: int = 0,
    do_swaps: bool = True,
    inverse: bool = False,
    insert_barriers: bool = False,
    name: str | None = None,
) -> QuantumCircuit:
    """Kwantowa transformata Fouriera w konwencji Qiskita (kubit 0 = najmłodszy bit)."""
    circuit = QuantumCircuit(num_qubits, name=name or ("IQFT" if inverse else "QFT"))
    for j in reversed(range(num_qubits)):
        circuit.h(j)
        # Liczba bramek CP przy przybliżeniu – ten sam wzór, co w ``QFT._build`` Qiskita.
        entanglements = max(0, j - max(0, approximation_degree - (num_qubits - j - 1)))
        for k in reversed(range(j - entanglements, j)):
            circuit.cp(math.pi / 2 ** (j - k), j, k)
        if insert_barriers:
            circuit.barrier()
    if do_swaps:
        for i in range(num_qubits // 2):
            circuit.swap(i, num_qubits - i - 1)
    return circuit.inverse() if inverse else circuit


def QFTGate(num_qubits: int) -> Gate:  # noqa: N802 - nazwa jak w Qiskicie
    return QFT(num_qubits).to_gate(label="QFT")


__all__ = [*_CLASSES, "CnotGate", "FredkinGate", "MCXGate", "QFT", "QFTGate", "ToffoliGate",
           "U1Gate", "U3Gate", "UnitaryGate"]  # fmt: skip
