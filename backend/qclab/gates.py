"""Macierze bramek w konwencji Qiskita (little-endian po argumentach bramki).

Macierz bramki k-kubitowej ma indeks, w którym **pierwszy** argument (``qargs[0]``) jest
najmłodszym bitem. Dla ``cx(sterujący, docelowy)`` sterujący to bit 0, więc macierz to
``[[1,0,0,0],[0,0,0,1],[0,0,1,0],[0,1,0,0]]`` – dokładnie tak, jak w dokumentacji Qiskita.
Każda bramka sterowana w tym module trzyma tę samą zasadę: sterujące idą pierwsze (młodsze bity),
docelowe po nich. Zgodność z Qiskitem jest tu jedyną rzeczą, której nie wolno „uprościć” –
zadanie rozwiązane w qclab ma dać ten sam wynik w prawdziwym Qiskicie.
"""

from __future__ import annotations

import cmath
import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

SQRT1_2 = 1 / math.sqrt(2)


def _m(rows) -> np.ndarray:
    return np.array(rows, dtype=complex)


def controlled(base: np.ndarray, num_ctrl: int) -> np.ndarray:
    """Macierz bramki ``base`` sterowanej ``num_ctrl`` kubitami (sterujące = młodsze bity)."""
    target_dim = base.shape[0]
    dim = (2**num_ctrl) * target_dim
    out = np.eye(dim, dtype=complex)
    ones = 2**num_ctrl - 1
    for row in range(target_dim):
        for col in range(target_dim):
            out[ones | (row << num_ctrl), ones | (col << num_ctrl)] = base[row, col]
    return out


def rx(theta: float) -> np.ndarray:
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    return _m([[c, -1j * s], [-1j * s, c]])


def ry(theta: float) -> np.ndarray:
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    return _m([[c, -s], [s, c]])


def rz(phi: float) -> np.ndarray:
    return _m([[cmath.exp(-1j * phi / 2), 0], [0, cmath.exp(1j * phi / 2)]])


def phase(lam: float) -> np.ndarray:
    return _m([[1, 0], [0, cmath.exp(1j * lam)]])


def u(theta: float, phi: float, lam: float) -> np.ndarray:
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    return _m(
        [
            [c, -cmath.exp(1j * lam) * s],
            [cmath.exp(1j * phi) * s, cmath.exp(1j * (phi + lam)) * c],
        ]
    )


def rzz(theta: float) -> np.ndarray:
    a, b = cmath.exp(-1j * theta / 2), cmath.exp(1j * theta / 2)
    return np.diag([a, b, b, a]).astype(complex)


def rxx(theta: float) -> np.ndarray:
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    return _m([[c, 0, 0, -1j * s], [0, c, -1j * s, 0], [0, -1j * s, c, 0], [-1j * s, 0, 0, c]])


def ryy(theta: float) -> np.ndarray:
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    return _m([[c, 0, 0, 1j * s], [0, c, -1j * s, 0], [0, -1j * s, c, 0], [1j * s, 0, 0, c]])


I2 = np.eye(2, dtype=complex)
X = _m([[0, 1], [1, 0]])
Y = _m([[0, -1j], [1j, 0]])
Z = _m([[1, 0], [0, -1]])
H = _m([[SQRT1_2, SQRT1_2], [SQRT1_2, -SQRT1_2]])
S = _m([[1, 0], [0, 1j]])
SDG = _m([[1, 0], [0, -1j]])
T = _m([[1, 0], [0, cmath.exp(1j * math.pi / 4)]])
TDG = _m([[1, 0], [0, cmath.exp(-1j * math.pi / 4)]])
SX = 0.5 * _m([[1 + 1j, 1 - 1j], [1 - 1j, 1 + 1j]])
SXDG = SX.conj().T
SWAP = _m([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]])
ISWAP = _m([[1, 0, 0, 0], [0, 0, 1j, 0], [0, 1j, 0, 0], [0, 0, 0, 1]])


@dataclass(frozen=True)
class GateSpec:
    """Opis bramki z biblioteki: liczba kubitów, parametrów i fabryka macierzy."""

    name: str
    num_qubits: int
    num_params: int
    matrix: Callable[..., np.ndarray]
    #: Nazwa bramki odwrotnej i przekształcenie parametrów (``None`` = ta sama bramka).
    inverse: tuple[str, Callable[[tuple], tuple]] | None = None
    label: str = ""


def _const(matrix: np.ndarray) -> Callable[..., np.ndarray]:
    return lambda: matrix


def _neg(params: tuple) -> tuple:
    return tuple(-p for p in params)


def _self(params: tuple) -> tuple:
    return params


def _u_inverse(params: tuple) -> tuple:
    theta, phi, lam = params
    return (-theta, -lam, -phi)


#: Biblioteka bramek. Klucz to nazwa w ``count_ops()`` i w ``QuantumCircuit.data`` – ta sama, co
#: w Qiskicie (``cx``, a nie ``cnot``; aliasy obsługują metody obwodu).
GATES: dict[str, GateSpec] = {
    spec.name: spec
    for spec in (
        GateSpec("id", 1, 0, _const(I2), ("id", _self), "I"),
        GateSpec("x", 1, 0, _const(X), ("x", _self), "X"),
        GateSpec("y", 1, 0, _const(Y), ("y", _self), "Y"),
        GateSpec("z", 1, 0, _const(Z), ("z", _self), "Z"),
        GateSpec("h", 1, 0, _const(H), ("h", _self), "H"),
        GateSpec("s", 1, 0, _const(S), ("sdg", _self), "S"),
        GateSpec("sdg", 1, 0, _const(SDG), ("s", _self), "Sdg"),
        GateSpec("t", 1, 0, _const(T), ("tdg", _self), "T"),
        GateSpec("tdg", 1, 0, _const(TDG), ("t", _self), "Tdg"),
        GateSpec("sx", 1, 0, _const(SX), ("sxdg", _self), "√X"),
        GateSpec("sxdg", 1, 0, _const(SXDG), ("sx", _self), "√Xdg"),
        GateSpec("rx", 1, 1, rx, ("rx", _neg), "Rx"),
        GateSpec("ry", 1, 1, ry, ("ry", _neg), "Ry"),
        GateSpec("rz", 1, 1, rz, ("rz", _neg), "Rz"),
        GateSpec("p", 1, 1, phase, ("p", _neg), "P"),
        GateSpec("u", 1, 3, u, ("u", _u_inverse), "U"),
        GateSpec("cx", 2, 0, _const(controlled(X, 1)), ("cx", _self), "X"),
        GateSpec("cy", 2, 0, _const(controlled(Y, 1)), ("cy", _self), "Y"),
        GateSpec("cz", 2, 0, _const(controlled(Z, 1)), ("cz", _self), "Z"),
        GateSpec("ch", 2, 0, _const(controlled(H, 1)), ("ch", _self), "H"),
        GateSpec("cs", 2, 0, _const(controlled(S, 1)), ("csdg", _self), "S"),
        GateSpec("csdg", 2, 0, _const(controlled(SDG, 1)), ("cs", _self), "Sdg"),
        GateSpec("csx", 2, 0, _const(controlled(SX, 1)), None, "√X"),
        GateSpec("cp", 2, 1, lambda lam: controlled(phase(lam), 1), ("cp", _neg), "P"),
        GateSpec("crx", 2, 1, lambda t: controlled(rx(t), 1), ("crx", _neg), "Rx"),
        GateSpec("cry", 2, 1, lambda t: controlled(ry(t), 1), ("cry", _neg), "Ry"),
        GateSpec("crz", 2, 1, lambda t: controlled(rz(t), 1), ("crz", _neg), "Rz"),
        GateSpec("swap", 2, 0, _const(SWAP), ("swap", _self), "x"),
        GateSpec("iswap", 2, 0, _const(ISWAP), None, "iSwap"),
        GateSpec("rxx", 2, 1, rxx, ("rxx", _neg), "Rxx"),
        GateSpec("ryy", 2, 1, ryy, ("ryy", _neg), "Ryy"),
        GateSpec("rzz", 2, 1, rzz, ("rzz", _neg), "Rzz"),
        GateSpec("ccx", 3, 0, _const(controlled(X, 2)), ("ccx", _self), "X"),
        GateSpec("ccz", 3, 0, _const(controlled(Z, 2)), ("ccz", _self), "Z"),
        GateSpec("cswap", 3, 0, _const(controlled(SWAP, 1)), ("cswap", _self), "x"),
    )
}

#: Bramki bez odwrotności w tabeli (``csx``, ``iswap``) odwraca ``QuantumCircuit.inverse``
#: sprzężeniem macierzy (bramka ``unitary``) – nazwa w ``count_ops()`` zmienia się wtedy tak samo,
#: jak w Qiskicie zmienia się na ``csx_dg``/``iswap_dg``: z inną nazwą, ale z poprawną macierzą.

#: Dyrektywy i operacje nieunitarne – nie mają macierzy.
NON_UNITARY = frozenset({"measure", "reset", "barrier", "save_statevector", "delay"})
