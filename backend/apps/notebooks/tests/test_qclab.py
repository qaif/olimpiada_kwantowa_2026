"""Symulator ``qclab``: konwencje Qiskita (kolejność bitów!), bramki, pomiar, API zgodności.

Część „parzystości” (``test_parity_*``) porównuje wyniki z prawdziwym Qiskitem, jeśli jest
zainstalowany (obraz testowy CI go nie ma – wtedy testy się pomijają; lokalnie
``pip install qiskit`` i uruchomić). Reszta stoi na wartościach policzonych ręcznie.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

import qclab
from qclab import Operator, Parameter, QuantumCircuit, Statevector
from qclab.backends import AerSimulator, StatevectorEstimator, StatevectorSampler
from qclab.exceptions import QclabError
from qclab.library import QFT, CXGate, HGate, RYGate, UnitaryGate
from qclab.quantum_info import SparsePauliOp, state_fidelity

S2 = 1 / math.sqrt(2)


def test_bell_state_and_bit_order():
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    assert np.allclose(Statevector(qc).data, [S2, 0, 0, S2])
    # Konwencja Qiskita: X na kubicie 0 daje |01> (indeks 1), a nie |10>.
    one = QuantumCircuit(2)
    one.x(0)
    assert np.allclose(Statevector(one).data, [0, 1, 0, 0])
    assert Statevector(one).probabilities_dict() == {"01": 1.0}


def test_cx_matrix_matches_qiskit_documentation():
    expected = np.array([[1, 0, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0], [0, 1, 0, 0]])
    assert np.allclose(CXGate().to_matrix(), expected)
    qc = QuantumCircuit(2)
    qc.cx(0, 1)
    assert np.allclose(Operator(qc).data, expected)


def test_rotations_and_u_gate():
    theta = 0.7
    qc = QuantumCircuit(1)
    qc.ry(theta, 0)
    assert np.allclose(Statevector(qc).data, [math.cos(theta / 2), math.sin(theta / 2)])
    assert np.allclose(RYGate(theta).to_matrix(), [[math.cos(0.35), -math.sin(0.35)], [math.sin(0.35), math.cos(0.35)]])
    u = QuantumCircuit(1)
    u.u(math.pi, 0, math.pi, 0)  # U(π, 0, π) = X
    assert Operator(u).equiv(Operator(np.array([[0, 1], [1, 0]])))


def test_three_qubit_gate_on_non_adjacent_qubits():
    qc = QuantumCircuit(3)
    qc.x(0)
    qc.x(2)
    qc.ccx(0, 2, 1)
    assert Statevector(qc).probabilities_dict() == {"111": 1.0}


def test_qft_equals_dft_matrix():
    n = 3
    dim = 2**n
    omega = np.exp(2j * math.pi / dim)
    dft = np.array([[omega ** (j * k) for j in range(dim)] for k in range(dim)]) / math.sqrt(dim)
    assert Operator(QFT(n)).equiv(Operator(dft))
    assert Operator(QFT(n, inverse=True)).equiv(Operator(dft.conj().T))


def test_terminal_measurement_counts_and_register_keys():
    qc = QuantumCircuit(2, 2)
    qc.x(1)
    qc.measure([0, 1], [0, 1])
    counts = AerSimulator().run(qc, shots=100, seed_simulator=1).result().get_counts()
    assert counts == {"10": 100}
    ghz = QuantumCircuit(3)
    ghz.h(0)
    ghz.cx(0, 1)
    ghz.cx(1, 2)
    ghz.measure_all()
    counts = AerSimulator().run(ghz, shots=2000, seed_simulator=7).result().get_counts()
    assert set(counts) == {"000", "111"}
    assert 800 < counts["000"] < 1200


def test_two_registers_are_space_separated_last_register_first():
    qc = QuantumCircuit(2, 1)
    qc.x(0)
    qc.measure(0, 0)
    qc.measure_all()
    counts = AerSimulator().run(qc, shots=10).result().get_counts()
    assert counts == {"01 1": 10}


def test_mid_circuit_measurement_and_reset():
    qc = QuantumCircuit(1, 2)
    qc.x(0)
    qc.measure(0, 0)
    qc.reset(0)
    qc.measure(0, 1)
    counts = AerSimulator().run(qc, shots=50, seed_simulator=3).result().get_counts()
    assert counts == {"01": 50}


def test_statevector_with_measurement_raises_like_qiskit():
    qc = QuantumCircuit(1)
    qc.h(0)
    qc.measure_all()
    with pytest.raises(QclabError):
        Statevector(qc)
    assert np.allclose(Statevector(qc.remove_final_measurements(inplace=False)).data, [S2, S2])


def test_parameters_and_assign():
    theta = Parameter("θ")
    qc = QuantumCircuit(1)
    qc.rx(2 * theta, 0)
    assert qc.parameters == [theta]
    bound = qc.assign_parameters({theta: math.pi / 2})
    assert np.allclose(Statevector(bound).probabilities(), [0, 1])
    with pytest.raises(QclabError):
        Statevector(qc)


def test_compose_inverse_to_gate_and_append():
    bell = QuantumCircuit(2, name="bell")
    bell.h(0)
    bell.cx(0, 1)
    big = QuantumCircuit(3)
    big.append(bell.to_gate(), [1, 2])
    assert Statevector(big).probabilities_dict() == pytest.approx({"000": 0.5, "110": 0.5})
    identity = bell.compose(bell.inverse())
    assert Operator(identity).equiv(Operator(np.eye(4)))
    big.append(HGate(), [0])
    assert big.count_ops() == {"bell": 1, "h": 1}


def test_depth_size_count_ops():
    qc = QuantumCircuit(3)
    qc.h(0)
    qc.h(1)
    qc.cx(0, 2)
    qc.barrier()
    qc.x(1)
    assert qc.depth() == 2
    assert qc.size() == 4
    assert qc.count_ops()["h"] == 2
    assert qc.num_nonlocal_gates() == 1


def test_unitary_gate_and_controlled():
    qc = QuantumCircuit(2)
    qc.append(UnitaryGate(np.array([[0, 1], [1, 0]])).control(1), [0, 1])
    qc2 = QuantumCircuit(2)
    qc2.cx(0, 1)
    assert Operator(qc).equiv(Operator(qc2))


def test_primitives_v2():
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    estimator = StatevectorEstimator()
    result = estimator.run([(qc, SparsePauliOp(["ZZ", "XX"]))]).result()
    assert float(result[0].data.evs) == pytest.approx(2.0)
    qc.measure_all()
    sampled = StatevectorSampler(seed=1).run([qc], shots=100).result()[0]
    counts = sampled.data.meas.get_counts()
    assert set(counts) <= {"00", "11"} and sum(counts.values()) == 100


def test_fidelity_and_from_label():
    plus = Statevector.from_label("+")
    qc = QuantumCircuit(1)
    qc.h(0)
    assert state_fidelity(plus, Statevector(qc)) == pytest.approx(1.0)
    assert Statevector.from_label("01").probabilities_dict() == {"01": 1.0}


def test_qubit_limit():
    with pytest.raises(QclabError):
        QuantumCircuit(qclab.exceptions.MAX_QUBITS + 1)


def test_compat_import_paths():
    compat = str(Path(qclab.__file__).parent / "_compat")
    sys.path.insert(0, compat)
    try:
        for name in [m for m in list(sys.modules) if m == "qiskit" or m.startswith("qiskit.")]:
            sys.modules.pop(name)
        import importlib

        qiskit = importlib.import_module("qiskit")
        if "qclab" not in qiskit.__version__:
            pytest.skip("Zainstalowany prawdziwy Qiskit zasłania moduł zgodności.")
        from qiskit import QuantumCircuit as QC  # noqa: N814
        from qiskit.circuit.library import QFT as LibQFT  # noqa: F401
        from qiskit.primitives import StatevectorSampler as Sampler  # noqa: F401
        from qiskit.providers.basic_provider import BasicSimulator
        from qiskit.quantum_info import Statevector as SV
        from qiskit.visualization import plot_histogram

        qc = QC(1, 1)
        qc.x(0)
        qc.measure(0, 0)
        counts = BasicSimulator().run(qc, shots=5).result().get_counts()
        assert counts == {"1": 5}
        assert "1 |" in repr(plot_histogram(counts))
        assert SV.from_label("1").probabilities_dict() == {"1": 1.0}
    finally:
        sys.path.remove(compat)
        for name in [m for m in list(sys.modules) if m == "qiskit" or m.startswith("qiskit.")]:
            sys.modules.pop(name)


def test_text_drawing_mentions_gates():
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.rz(math.pi / 2, 1)
    drawing = str(qc.draw())
    assert "[H]" in drawing and "⊕" in drawing and "Rz(π/2)" in drawing


# --- parzystość z prawdziwym Qiskitem ---------------------------------------------------------------


def _random_circuit_spec(seed: int, n: int, depth: int):
    rng = np.random.default_rng(seed)
    one = ["h", "x", "y", "z", "s", "sdg", "t", "tdg", "sx", "rx", "ry", "rz", "p", "u"]
    two = ["cx", "cy", "cz", "ch", "cp", "crx", "cry", "crz", "swap", "rzz", "rxx", "ryy", "iswap"]
    three = ["ccx", "cswap", "ccz"]
    spec = []
    for _ in range(depth):
        roll = rng.random()
        pool, width = (one, 1) if roll < 0.5 or n < 2 else ((two, 2) if roll < 0.9 or n < 3 else (three, 3))
        name = pool[int(rng.integers(len(pool)))]
        qubits = rng.choice(n, size=width, replace=False).tolist()
        nparams = {"rx": 1, "ry": 1, "rz": 1, "p": 1, "u": 3, "cp": 1, "crx": 1, "cry": 1, "crz": 1,
                   "rzz": 1, "rxx": 1, "ryy": 1}.get(name, 0)  # fmt: skip
        spec.append((name, [float(x) for x in rng.uniform(-math.pi, math.pi, nparams)], qubits))
    return spec


def _build(module_circuit, spec, n):
    qc = module_circuit(n)
    for name, params, qubits in spec:
        getattr(qc, name)(*params, *qubits)
    return qc


@pytest.mark.parametrize("seed", range(12))
def test_parity_statevector_with_real_qiskit(seed):
    qiskit = pytest.importorskip("qiskit")
    from qiskit.quantum_info import Statevector as QStatevector

    n = 2 + seed % 3
    spec = _random_circuit_spec(seed, n, 25)
    ours = Statevector(_build(QuantumCircuit, spec, n)).data
    theirs = QStatevector(_build(qiskit.QuantumCircuit, spec, n)).data
    assert np.allclose(ours, theirs, atol=1e-9)


def test_parity_counts_keys_with_real_qiskit():
    qiskit = pytest.importorskip("qiskit")
    from qiskit.providers.basic_provider import BasicSimulator as QBasic

    def build(cls, creg_cls, qreg_cls):
        qr = qreg_cls(3, "q")
        a = creg_cls(1, "a")
        b = creg_cls(2, "b")
        qc = cls(qr, a, b)
        qc.x(0)
        qc.x(2)
        qc.measure(0, b[1])
        qc.measure(2, a[0])
        return qc

    ours = AerSimulator().run(build(QuantumCircuit, qclab.ClassicalRegister, qclab.QuantumRegister), shots=8)
    theirs = QBasic().run(build(qiskit.QuantumCircuit, qiskit.ClassicalRegister, qiskit.QuantumRegister), shots=8)
    assert ours.result().get_counts() == theirs.result().get_counts()


def test_parity_qft_and_depth_with_real_qiskit():
    pytest.importorskip("qiskit")
    from qiskit.circuit.library import QFT as QQFT
    from qiskit.quantum_info import Operator as QOperator

    for n in (2, 3, 4):
        assert np.allclose(Operator(QFT(n)).data, QOperator(QQFT(n)).data, atol=1e-9)
        assert np.allclose(Operator(QFT(n, approximation_degree=1)).data, QOperator(QQFT(n, approximation_degree=1)).data)
