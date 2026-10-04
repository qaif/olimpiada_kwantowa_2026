"""``QuantumCircuit`` i jego klocki: rejestry, bity, parametry, instrukcje.

Zakres API odpowiada temu, czego używają materiały dydaktyczne Qiskita 1.x/2.x na poziomie
olimpiady (bramki jedno-, dwu- i trzykubitowe, pomiar, reset, bariera, parametry, ``compose``,
``inverse``, ``to_gate``, ``append``, ``depth``/``size``/``count_ops``, rysunek tekstowy).
Czego **nie** ma i dlaczego – ``docs/tasks/QC-01.md`` § 2.3. Nieobsługiwana metoda kończy się
``AttributeError``/``QclabError`` z czytelnym komunikatem, a nie cichym, innym wynikiem.
"""

from __future__ import annotations

import itertools
import math
import numbers
import re
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any, NamedTuple

import numpy as np

from .exceptions import MAX_OPERATOR_QUBITS, MAX_QUBITS, QclabError
from .gates import GATES, NON_UNITARY, controlled

_circuit_counter = itertools.count()


# --- parametry ------------------------------------------------------------------------------------


class ParameterExpression:
    """Wyrażenie arytmetyczne nad parametrami (``2*theta + pi``) – wartość po podstawieniu."""

    __slots__ = ("_fn", "_params", "_text")

    def __init__(self, params: frozenset, fn, text: str) -> None:
        self._params = params
        self._fn = fn
        self._text = text

    @property
    def parameters(self) -> set:
        return set(self._params)

    def bind(self, values: dict) -> float | ParameterExpression:
        missing = [p for p in self._params if p not in values]
        if missing:
            remaining = {p: values[p] for p in self._params if p in values}
            return ParameterExpression(
                frozenset(missing),
                lambda vals, fn=self._fn, rem=remaining: fn({**rem, **vals}),
                self._text,
            )
        return float(self._fn(values))

    def __float__(self) -> float:
        if self._params:
            raise TypeError(f"Parameter {self} is not bound – use assign_parameters().")
        return float(self._fn({}))

    def _combine(self, other, op, symbol: str, reverse: bool = False) -> ParameterExpression:
        left, right = (other, self) if reverse else (self, other)
        params = set(getattr(self, "_params", ()))
        if isinstance(other, ParameterExpression):
            params |= other._params

        def value(x, vals):
            return x._fn(vals) if isinstance(x, ParameterExpression) else x

        return ParameterExpression(
            frozenset(params),
            lambda vals: op(value(left, vals), value(right, vals)),
            f"({left} {symbol} {right})",
        )

    def __add__(self, other):
        return self._combine(other, lambda a, b: a + b, "+")

    def __radd__(self, other):
        return self._combine(other, lambda a, b: a + b, "+", reverse=True)

    def __sub__(self, other):
        return self._combine(other, lambda a, b: a - b, "-")

    def __rsub__(self, other):
        return self._combine(other, lambda a, b: a - b, "-", reverse=True)

    def __mul__(self, other):
        return self._combine(other, lambda a, b: a * b, "*")

    def __rmul__(self, other):
        return self._combine(other, lambda a, b: a * b, "*", reverse=True)

    def __truediv__(self, other):
        return self._combine(other, lambda a, b: a / b, "/")

    def __rtruediv__(self, other):
        return self._combine(other, lambda a, b: a / b, "/", reverse=True)

    def __neg__(self):
        return self._combine(-1, lambda a, b: a * b, "*")

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return f"ParameterExpression({self._text})"


class Parameter(ParameterExpression):
    """Nazwany parametr obwodu (``Parameter("θ")``). Tożsamość po obiekcie, nie po nazwie."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = str(name)
        super().__init__(frozenset([self]), lambda vals, me=self: vals[me], self.name)

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other) -> bool:
        return self is other

    def __repr__(self) -> str:
        return f"Parameter({self.name})"


def _parameter_sort_key(param: Parameter) -> tuple:
    """Kolejność jak w Qiskicie: po nazwie, a elementy wektora (``θ[10]``) po numerze, nie po tekście."""
    match = re.match(r"^(.*)\[(\d+)\]$", param.name)
    if match:
        return (match.group(1), int(match.group(2)))
    return (param.name, -1)


class ParameterVector(list):
    """``ParameterVector("θ", 3)`` – lista parametrów ``θ[0]``, ``θ[1]``, ``θ[2]``."""

    def __init__(self, name: str, length: int = 0) -> None:
        self.name = str(name)
        super().__init__(Parameter(f"{self.name}[{i}]") for i in range(int(length)))

    @property
    def params(self) -> list:
        return list(self)


def bind_value(value, values: dict | None = None):
    """Liczba z parametru bramki (``float``) albo wyrażenie, jeśli parametr nie ma jeszcze wartości."""
    if isinstance(value, ParameterExpression):
        return value.bind(values or {})
    if isinstance(value, numbers.Number):
        if isinstance(value, complex):
            if value.imag:
                raise QclabError("A gate parameter must be a real number.")
            return float(value.real)
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise QclabError(f"Invalid gate parameter: {value!r}") from exc


# --- bity i rejestry ------------------------------------------------------------------------------


class Bit:
    __slots__ = ("_register", "_index")

    def __init__(self, register, index: int) -> None:
        self._register = register
        self._index = index

    @property
    def register(self):
        return self._register

    @property
    def index(self) -> int:
        return self._index

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._register!r}, {self._index})"


class Qubit(Bit):
    __slots__ = ()


class Clbit(Bit):
    __slots__ = ()


class Register:
    bit_type: type = Bit
    prefix = "r"
    _counter = itertools.count()

    def __init__(self, size: int | None = None, name: str | None = None, bits=None) -> None:
        if bits is not None:
            raise QclabError("Registers built from a list of bits are not supported – pass a size.")
        if size is None or int(size) < 0:
            raise QclabError("Register size must be a non-negative integer.")
        self.size = int(size)
        self.name = name if name is not None else f"{self.prefix}{next(self._counter)}"
        self._bits = [self.bit_type(self, i) for i in range(self.size)]

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, key):
        return self._bits[key]

    def __iter__(self):
        return iter(self._bits)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.size}, '{self.name}')"


class QuantumRegister(Register):
    bit_type = Qubit
    prefix = "q"
    _counter = itertools.count()


class ClassicalRegister(Register):
    bit_type = Clbit
    prefix = "c"
    _counter = itertools.count()


class BitLocations(NamedTuple):
    index: int
    registers: list


# --- instrukcje -----------------------------------------------------------------------------------


class Instruction:
    """Operacja w obwodzie: bramka z biblioteki, ``unitary``, bramka złożona albo pomiar/reset."""

    def __init__(
        self,
        name: str,
        num_qubits: int,
        num_clbits: int = 0,
        params: Iterable = (),
        *,
        matrix: np.ndarray | None = None,
        definition: QuantumCircuit | None = None,
        label: str | None = None,
    ) -> None:
        self.name = name
        self.num_qubits = int(num_qubits)
        self.num_clbits = int(num_clbits)
        self.params = list(params)
        self._matrix = matrix
        self.definition = definition
        self.label = label

    @property
    def is_unitary(self) -> bool:
        return self.name not in NON_UNITARY

    def bound(self, values: dict) -> Instruction:
        if not any(isinstance(p, ParameterExpression) for p in self.params):
            return self
        return self._replace(params=[bind_value(p, values) for p in self.params])

    def _replace(self, **changes) -> Instruction:
        clone = object.__new__(type(self))
        clone.__dict__.update(self.__dict__)
        clone.__dict__.update(changes)
        return clone

    def numeric_params(self) -> list[float]:
        values = []
        for param in self.params:
            value = bind_value(param)
            if isinstance(value, ParameterExpression):
                raise QclabError(
                    f"Gate {self.name} has an unbound parameter {value} – use assign_parameters()."
                )
            values.append(value)
        return values

    def to_matrix(self) -> np.ndarray:
        if not self.is_unitary:
            raise QclabError(f"Operation {self.name} is not unitary and has no matrix.")
        if self._matrix is not None:
            return self._matrix
        if self.definition is not None:
            from .quantum_info import Operator

            return Operator(self.definition).data
        spec = GATES.get(self.name)
        if spec is None:
            raise QclabError(f"Unknown gate: {self.name}")
        return spec.matrix(*self.numeric_params())

    def __array__(self, dtype=None, copy=None):
        return np.asarray(self.to_matrix(), dtype=dtype)

    def inverse(self) -> Instruction:
        if not self.is_unitary:
            raise QclabError(f"Operation {self.name} has no inverse.")
        if self.definition is not None:
            return Gate(
                f"{self.name}_dg", self.num_qubits, [], definition=self.definition.inverse(), label=self.label
            )
        spec = GATES.get(self.name)
        if self._matrix is None and spec is not None and spec.inverse is not None:
            name, transform = spec.inverse
            return Gate(name, self.num_qubits, transform(tuple(self.params)))
        return Gate(f"{self.name}_dg", self.num_qubits, [], matrix=self.to_matrix().conj().T)

    def control(self, num_ctrl_qubits: int = 1, label: str | None = None) -> Instruction:
        if num_ctrl_qubits < 1:
            raise QclabError("The number of control qubits must be positive.")
        total = self.num_qubits + num_ctrl_qubits
        if total > MAX_OPERATOR_QUBITS:
            raise QclabError(f"Controlled gate has too many qubits (max {MAX_OPERATOR_QUBITS}).")
        simple = {("x", 1): "cx", ("x", 2): "ccx", ("z", 1): "cz", ("z", 2): "ccz", ("y", 1): "cy",
                  ("h", 1): "ch", ("p", 1): "cp", ("rx", 1): "crx", ("ry", 1): "cry",
                  ("rz", 1): "crz", ("swap", 1): "cswap", ("s", 1): "cs", ("sdg", 1): "csdg",
                  ("sx", 1): "csx"}  # fmt: skip
        name = simple.get((self.name, num_ctrl_qubits))
        if name and self._matrix is None and self.definition is None:
            return Gate(name, total, list(self.params), label=label)
        if self.name == "x" and self._matrix is None:
            return Gate("mcx", total, [], matrix=controlled(self.to_matrix(), num_ctrl_qubits), label=label)
        return Gate(
            f"c{num_ctrl_qubits}_{self.name}" if num_ctrl_qubits > 1 else f"c{self.name}",
            total,
            [],
            matrix=controlled(self.to_matrix(), num_ctrl_qubits),
            label=label,
        )

    def __repr__(self) -> str:
        params = ", ".join(str(p) for p in self.params)
        return f"Instruction(name='{self.name}', num_qubits={self.num_qubits}, params=[{params}])"


class Gate(Instruction):
    def __init__(self, name: str, num_qubits: int, params: Iterable = (), **kwargs) -> None:
        super().__init__(name, num_qubits, 0, params, **kwargs)


class CircuitInstruction:
    """Wpis ``QuantumCircuit.data``. Rozpakowuje się też po staremu: ``for inst, qargs, cargs in``."""

    __slots__ = ("operation", "qubits", "clbits")

    def __init__(self, operation: Instruction, qubits: tuple, clbits: tuple = ()) -> None:
        self.operation = operation
        self.qubits = tuple(qubits)
        self.clbits = tuple(clbits)

    def __iter__(self):
        return iter((self.operation, list(self.qubits), list(self.clbits)))

    def __getitem__(self, key):
        return (self.operation, list(self.qubits), list(self.clbits))[key]

    @property
    def name(self) -> str:
        return self.operation.name

    @property
    def params(self) -> list:
        return self.operation.params

    def __repr__(self) -> str:
        return f"CircuitInstruction(operation={self.operation!r}, qubits={self.qubits}, clbits={self.clbits})"


class InstructionSet(list):
    """Wynik metody bramki. ``c_if`` zniknęło z Qiskita 2.0 i tu też go nie ma."""


# --- obwód ----------------------------------------------------------------------------------------


def _is_int(value) -> bool:
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


class QuantumCircuit:
    def __init__(self, *regs, name: str | None = None, global_phase: float = 0.0, metadata=None) -> None:
        self.name = name or f"circuit-{next(_circuit_counter)}"
        self.global_phase = global_phase
        self.metadata = metadata or {}
        self.qregs: list[QuantumRegister] = []
        self.cregs: list[ClassicalRegister] = []
        self._qubits: list[Qubit] = []
        self._clbits: list[Clbit] = []
        self._qubit_index: dict[int, int] = {}
        self._clbit_index: dict[int, int] = {}
        self._data: list[CircuitInstruction] = []
        ints = [r for r in regs if _is_int(r)]
        if ints and len(ints) != len(regs):
            raise QclabError("Pass either qubit/clbit counts or registers, not both.")
        if ints:
            if len(ints) > 2:
                raise QclabError("QuantumCircuit(num_qubits, num_clbits) takes at most two integers.")
            if ints[0]:
                self.add_register(QuantumRegister(ints[0], "q"))
            if len(ints) == 2 and ints[1]:
                self.add_register(ClassicalRegister(ints[1], "c"))
        else:
            self.add_register(*regs)

    # --- rejestry i bity ---

    def add_register(self, *regs) -> None:
        for reg in regs:
            if isinstance(reg, QuantumRegister):
                if any(r.name == reg.name for r in self.qregs):
                    raise QclabError(f"A register named '{reg.name}' is already in the circuit.")
                self.qregs.append(reg)
                for bit in reg:
                    self._qubit_index[id(bit)] = len(self._qubits)
                    self._qubits.append(bit)
                if len(self._qubits) > MAX_QUBITS:
                    raise QclabError(f"The simulator supports at most {MAX_QUBITS} qubits.")
            elif isinstance(reg, ClassicalRegister):
                if any(r.name == reg.name for r in self.cregs):
                    raise QclabError(f"A register named '{reg.name}' is already in the circuit.")
                self.cregs.append(reg)
                for bit in reg:
                    self._clbit_index[id(bit)] = len(self._clbits)
                    self._clbits.append(bit)
            else:
                raise QclabError(f"Unknown register type: {reg!r}")

    @property
    def qubits(self) -> list[Qubit]:
        return list(self._qubits)

    @property
    def clbits(self) -> list[Clbit]:
        return list(self._clbits)

    @property
    def num_qubits(self) -> int:
        return len(self._qubits)

    @property
    def num_clbits(self) -> int:
        return len(self._clbits)

    @property
    def data(self) -> list[CircuitInstruction]:
        return list(self._data)

    def width(self) -> int:
        return self.num_qubits + self.num_clbits

    def find_bit(self, bit) -> BitLocations:
        if isinstance(bit, Qubit) and id(bit) in self._qubit_index:
            index = self._qubit_index[id(bit)]
        elif isinstance(bit, Clbit) and id(bit) in self._clbit_index:
            index = self._clbit_index[id(bit)]
        else:
            raise QclabError(f"Bit {bit!r} does not belong to this circuit.")
        return BitLocations(index, [(bit.register, bit.index)])

    def _resolve(self, spec, *, quantum: bool) -> list[int]:
        """Indeksy bitów z liczby, bitu, rejestru albo listy – jak w Qiskicie, z ujemnymi indeksami."""
        size = self.num_qubits if quantum else self.num_clbits
        index_map = self._qubit_index if quantum else self._clbit_index
        kind = "qubit" if quantum else "clbit"
        if _is_int(spec):
            value = int(spec)
            if value < 0:
                value += size
            if not 0 <= value < size:
                raise QclabError(f"Index {spec} out of range – the circuit has {size} {kind}(s).")
            return [value]
        if isinstance(spec, Bit):
            if id(spec) not in index_map:
                raise QclabError(f"{kind.capitalize()} {spec!r} does not belong to this circuit.")
            return [index_map[id(spec)]]
        if isinstance(spec, Register):
            return [index for bit in spec for index in self._resolve(bit, quantum=quantum)]
        if isinstance(spec, slice):
            return list(range(size))[spec]
        if isinstance(spec, np.ndarray):
            spec = spec.tolist()
        if isinstance(spec, (list, tuple, range)):
            return [index for item in spec for index in self._resolve(item, quantum=quantum)]
        raise QclabError(f"Cannot interpret {kind} specifier: {spec!r}")

    # --- dopisywanie instrukcji ---

    def _append_indices(self, operation: Instruction, qubits: list[int], clbits: list[int] = ()) -> None:
        if len(set(qubits)) != len(qubits):
            raise QclabError(f"Gate {operation.name} got the same qubit twice: {qubits}.")
        if len(qubits) != operation.num_qubits or len(clbits) != operation.num_clbits:
            raise QclabError(
                f"Operation {operation.name} needs {operation.num_qubits} qubit(s) and "
                f"{operation.num_clbits} clbit(s)."
            )
        self._data.append(
            CircuitInstruction(
                operation,
                tuple(self._qubits[i] for i in qubits),
                tuple(self._clbits[i] for i in clbits),
            )
        )

    def _broadcast(self, operation: Instruction, qargs: list, cargs: list = ()) -> InstructionSet:
        qlists = [self._resolve(q, quantum=True) for q in qargs]
        clists = [self._resolve(c, quantum=False) for c in cargs]
        lengths = {len(item) for item in [*qlists, *clists] if len(item) != 1}
        if len(lengths) > 1:
            raise QclabError(f"Qubit lists for {operation.name} have different lengths.")
        count = lengths.pop() if lengths else 1
        result = InstructionSet()
        for i in range(count):
            qubits = [item[0] if len(item) == 1 else item[i] for item in qlists]
            clbits = [item[0] if len(item) == 1 else item[i] for item in clists]
            self._append_indices(operation, qubits, clbits)
            result.append(self._data[-1])
        return result

    def _gate(self, name: str, params: list, *qargs) -> InstructionSet:
        spec = GATES[name]
        return self._broadcast(Gate(name, spec.num_qubits, params), list(qargs))

    def append(self, instruction, qargs=None, cargs=None, copy: bool = True) -> InstructionSet:
        if isinstance(instruction, CircuitInstruction):
            qargs = qargs if qargs is not None else instruction.qubits
            cargs = cargs if cargs is not None else instruction.clbits
            instruction = instruction.operation
        if isinstance(instruction, QuantumCircuit):
            instruction = instruction.to_instruction()
        if not isinstance(instruction, Instruction):
            raise QclabError(f"append() expects a gate or an instruction, not {instruction!r}.")
        qubits = self._resolve(list(qargs or []), quantum=True)
        clbits = self._resolve(list(cargs or []), quantum=False)
        self._append_indices(instruction, qubits, clbits)
        return InstructionSet([self._data[-1]])

    # bramki jednokubitowe
    def id(self, qubit):
        return self._gate("id", [], qubit)

    i = id

    def x(self, qubit):
        return self._gate("x", [], qubit)

    def y(self, qubit):
        return self._gate("y", [], qubit)

    def z(self, qubit):
        return self._gate("z", [], qubit)

    def h(self, qubit):
        return self._gate("h", [], qubit)

    def s(self, qubit):
        return self._gate("s", [], qubit)

    def sdg(self, qubit):
        return self._gate("sdg", [], qubit)

    def t(self, qubit):
        return self._gate("t", [], qubit)

    def tdg(self, qubit):
        return self._gate("tdg", [], qubit)

    def sx(self, qubit):
        return self._gate("sx", [], qubit)

    def sxdg(self, qubit):
        return self._gate("sxdg", [], qubit)

    def rx(self, theta, qubit):
        return self._gate("rx", [theta], qubit)

    def ry(self, theta, qubit):
        return self._gate("ry", [theta], qubit)

    def rz(self, phi, qubit):
        return self._gate("rz", [phi], qubit)

    def p(self, theta, qubit):
        return self._gate("p", [theta], qubit)

    u1 = p

    def u(self, theta, phi, lam, qubit):
        return self._gate("u", [theta, phi, lam], qubit)

    u3 = u

    def u2(self, phi, lam, qubit):
        return self._gate("u", [math.pi / 2, phi, lam], qubit)

    # bramki dwu- i trzykubitowe
    def cx(self, control_qubit, target_qubit):
        return self._gate("cx", [], control_qubit, target_qubit)

    cnot = cx

    def cy(self, control_qubit, target_qubit):
        return self._gate("cy", [], control_qubit, target_qubit)

    def cz(self, control_qubit, target_qubit):
        return self._gate("cz", [], control_qubit, target_qubit)

    def ch(self, control_qubit, target_qubit):
        return self._gate("ch", [], control_qubit, target_qubit)

    def cs(self, control_qubit, target_qubit):
        return self._gate("cs", [], control_qubit, target_qubit)

    def csdg(self, control_qubit, target_qubit):
        return self._gate("csdg", [], control_qubit, target_qubit)

    def csx(self, control_qubit, target_qubit):
        return self._gate("csx", [], control_qubit, target_qubit)

    def cp(self, theta, control_qubit, target_qubit):
        return self._gate("cp", [theta], control_qubit, target_qubit)

    cu1 = cp

    def crx(self, theta, control_qubit, target_qubit):
        return self._gate("crx", [theta], control_qubit, target_qubit)

    def cry(self, theta, control_qubit, target_qubit):
        return self._gate("cry", [theta], control_qubit, target_qubit)

    def crz(self, theta, control_qubit, target_qubit):
        return self._gate("crz", [theta], control_qubit, target_qubit)

    def swap(self, qubit1, qubit2):
        return self._gate("swap", [], qubit1, qubit2)

    def iswap(self, qubit1, qubit2):
        return self._gate("iswap", [], qubit1, qubit2)

    def rxx(self, theta, qubit1, qubit2):
        return self._gate("rxx", [theta], qubit1, qubit2)

    def ryy(self, theta, qubit1, qubit2):
        return self._gate("ryy", [theta], qubit1, qubit2)

    def rzz(self, theta, qubit1, qubit2):
        return self._gate("rzz", [theta], qubit1, qubit2)

    def ccx(self, control_qubit1, control_qubit2, target_qubit):
        return self._gate("ccx", [], control_qubit1, control_qubit2, target_qubit)

    toffoli = ccx

    def ccz(self, control_qubit1, control_qubit2, target_qubit):
        return self._gate("ccz", [], control_qubit1, control_qubit2, target_qubit)

    def cswap(self, control_qubit, target_qubit1, target_qubit2):
        return self._gate("cswap", [], control_qubit, target_qubit1, target_qubit2)

    fredkin = cswap

    def mcx(self, control_qubits, target_qubit, ancilla_qubits=None, mode=None):
        controls = self._resolve(control_qubits, quantum=True)
        target = self._resolve(target_qubit, quantum=True)
        if len(target) != 1:
            raise QclabError("mcx(): exactly one target qubit.")
        if len(controls) + 1 > MAX_OPERATOR_QUBITS:
            raise QclabError(f"mcx(): at most {MAX_OPERATOR_QUBITS - 1} control qubits.")
        from . import gates

        gate = Gate("mcx", len(controls) + 1, [], matrix=controlled(gates.X, len(controls)))
        self._append_indices(gate, [*controls, target[0]])
        return InstructionSet([self._data[-1]])

    def mcp(self, lam, control_qubits, target_qubit):
        controls = self._resolve(control_qubits, quantum=True)
        target = self._resolve(target_qubit, quantum=True)
        if len(target) != 1:
            raise QclabError("mcp(): exactly one target qubit.")
        if len(controls) + 1 > MAX_OPERATOR_QUBITS:
            raise QclabError(f"mcp(): at most {MAX_OPERATOR_QUBITS - 1} control qubits.")
        value = bind_value(lam)
        if isinstance(value, ParameterExpression):
            raise QclabError("mcp(): the angle must be a number (parameters are not supported here).")
        from . import gates

        gate = Gate(
            "mcphase", len(controls) + 1, [value], matrix=controlled(gates.phase(value), len(controls))
        )
        self._append_indices(gate, [*controls, target[0]])
        return InstructionSet([self._data[-1]])

    def unitary(self, obj, qubits, label: str | None = None):
        matrix = np.asarray(getattr(obj, "data", obj), dtype=complex)
        qubit_list = self._resolve(qubits, quantum=True)
        dim = 2 ** len(qubit_list)
        if matrix.shape != (dim, dim):
            raise QclabError(f"unitary(): the matrix must be {dim}x{dim}.")
        if not np.allclose(matrix @ matrix.conj().T, np.eye(dim), atol=1e-8):
            raise QclabError("unitary(): the matrix is not unitary.")
        self._append_indices(Gate("unitary", len(qubit_list), [], matrix=matrix, label=label), qubit_list)
        return InstructionSet([self._data[-1]])

    # operacje nieunitarne
    def measure(self, qubit, cbit):
        return self._broadcast(Instruction("measure", 1, 1), [qubit], [cbit])

    def measure_all(self, inplace: bool = True, add_bits: bool = True):
        circuit = self if inplace else self.copy()
        if add_bits:
            creg = ClassicalRegister(circuit.num_qubits, "meas")
            circuit.add_register(creg)
            circuit.barrier()
            clbits = [circuit._clbit_index[id(bit)] for bit in creg]
        else:
            if circuit.num_clbits < circuit.num_qubits:
                raise QclabError("measure_all(add_bits=False): not enough clbits.")
            circuit.barrier()
            clbits = list(range(circuit.num_qubits))
        for qubit, clbit in zip(range(circuit.num_qubits), clbits, strict=False):
            circuit._append_indices(Instruction("measure", 1, 1), [qubit], [clbit])
        return None if inplace else circuit

    def reset(self, qubit):
        return self._broadcast(Instruction("reset", 1, 0), [qubit])

    def barrier(self, *qargs, label: str | None = None):
        qubits = self._resolve(list(qargs), quantum=True) if qargs else list(range(self.num_qubits))
        if not qubits:
            return InstructionSet()
        unique = list(dict.fromkeys(qubits))
        self._append_indices(Instruction("barrier", len(unique), 0, label=label), unique)
        return InstructionSet([self._data[-1]])

    def save_statevector(self, label: str = "statevector", pershot: bool = False, conditional: bool = False):
        """Instrukcja Aera: symulator zapisze tu wektor stanu (``result.get_statevector()``)."""
        qubits = list(range(self.num_qubits))
        self._append_indices(Instruction("save_statevector", len(qubits), 0, label=label), qubits)
        return InstructionSet([self._data[-1]])

    def remove_final_measurements(self, inplace: bool = True):
        circuit = self if inplace else self.copy()
        kept: list[CircuitInstruction] = []
        touched: set[int] = set()
        for item in reversed(circuit._data):
            indices = {circuit._qubit_index[id(q)] for q in item.qubits}
            if item.operation.name in ("measure", "barrier") and not (indices & touched):
                continue
            touched |= indices
            kept.append(item)
        circuit._data = list(reversed(kept))
        used = {id(c) for item in circuit._data for c in item.clbits}
        for reg in list(circuit.cregs):
            if not any(id(bit) in used for bit in reg):
                circuit.cregs.remove(reg)
        circuit._clbits = [bit for reg in circuit.cregs for bit in reg]
        circuit._clbit_index = {id(bit): i for i, bit in enumerate(circuit._clbits)}
        return None if inplace else circuit

    # --- przekształcenia ---

    def copy(self, name: str | None = None) -> QuantumCircuit:
        clone = QuantumCircuit(
            *self.qregs, *self.cregs, name=name or self.name, global_phase=self.global_phase
        )
        clone.metadata = dict(self.metadata)
        clone._data = list(self._data)
        return clone

    def copy_empty_like(self, name: str | None = None) -> QuantumCircuit:
        clone = self.copy(name)
        clone._data = []
        return clone

    def _indices_of(self, item: CircuitInstruction) -> tuple[list[int], list[int]]:
        return (
            [self._qubit_index[id(q)] for q in item.qubits],
            [self._clbit_index[id(c)] for c in item.clbits],
        )

    def compose(self, other, qubits=None, clbits=None, front: bool = False, inplace: bool = False):
        if isinstance(other, Instruction):
            circuit = QuantumCircuit(other.num_qubits, other.num_clbits)
            circuit.append(other, range(other.num_qubits), range(other.num_clbits))
            other = circuit
        if not isinstance(other, QuantumCircuit):
            raise QclabError("compose() expects a circuit or a gate.")
        target = self if inplace else self.copy()
        qmap = target._resolve(qubits, quantum=True) if qubits is not None else list(range(other.num_qubits))
        cmap = target._resolve(clbits, quantum=False) if clbits is not None else list(range(other.num_clbits))
        if len(qmap) != other.num_qubits or len(cmap) != other.num_clbits:
            raise QclabError("compose(): qubit/clbit counts do not match the composed circuit.")
        if other.num_qubits > target.num_qubits or other.num_clbits > target.num_clbits:
            raise QclabError("compose(): the composed circuit is wider than the target circuit.")
        added: list[CircuitInstruction] = []
        for item in other._data:
            q_idx, c_idx = other._indices_of(item)
            added.append(
                CircuitInstruction(
                    item.operation,
                    tuple(target._qubits[qmap[i]] for i in q_idx),
                    tuple(target._clbits[cmap[i]] for i in c_idx),
                )
            )
        target._data = added + target._data if front else target._data + added
        target.global_phase = bind_value(target.global_phase) + bind_value(other.global_phase)
        return None if inplace else target

    def tensor(self, other: QuantumCircuit, inplace: bool = False):
        """``self ⊗ other``: kubity ``other`` są młodsze (jak w Qiskicie)."""
        result = QuantumCircuit(other.num_qubits + self.num_qubits, other.num_clbits + self.num_clbits)
        result.compose(other, range(other.num_qubits), range(other.num_clbits), inplace=True)
        result.compose(
            self,
            range(other.num_qubits, result.num_qubits),
            range(other.num_clbits, result.num_clbits),
            inplace=True,
        )
        if inplace:
            raise QclabError("tensor(inplace=True) is not supported – use the returned circuit.")
        return result

    def inverse(self) -> QuantumCircuit:
        clone = self.copy_empty_like(name=f"{self.name}_dg")
        for item in reversed(self._data):
            if item.operation.name == "barrier":
                clone._data.append(item)
                continue
            clone._data.append(CircuitInstruction(item.operation.inverse(), item.qubits, item.clbits))
        clone.global_phase = -bind_value(self.global_phase)
        return clone

    def assign_parameters(self, parameters, inplace: bool = False, strict: bool = True):
        if isinstance(parameters, dict):
            values = dict(parameters)
        else:
            ordered = self.parameters
            values_list = list(parameters)
            if len(values_list) != len(ordered):
                raise QclabError(
                    f"The circuit has {len(ordered)} parameter(s), got {len(values_list)} value(s)."
                )
            values = dict(zip(ordered, values_list, strict=True))
        if strict:
            unknown = [p for p in values if p not in set(self.parameters)]
            if unknown:
                raise QclabError(f"The circuit has no parameter(s): {', '.join(str(p) for p in unknown)}.")
        target = self if inplace else self.copy()
        target._data = [
            CircuitInstruction(item.operation.bound(values), item.qubits, item.clbits)
            for item in target._data
        ]
        if isinstance(target.global_phase, ParameterExpression):
            target.global_phase = target.global_phase.bind(values)
        return None if inplace else target

    @property
    def parameters(self) -> list[Parameter]:
        found: dict[int, Parameter] = {}
        for item in self._data:
            for param in item.operation.params:
                if isinstance(param, ParameterExpression):
                    for p in param.parameters:
                        found[id(p)] = p
        return sorted(found.values(), key=_parameter_sort_key)

    @property
    def num_parameters(self) -> int:
        return len(self.parameters)

    def to_gate(self, label: str | None = None, parameter_map=None) -> Gate:
        if self.num_clbits:
            raise QclabError("to_gate(): a circuit with clbits is not a gate – use to_instruction().")
        for item in self._data:
            if not item.operation.is_unitary and item.operation.name != "barrier":
                raise QclabError(f"to_gate(): operation {item.operation.name} is not unitary.")
        definition = self.copy()
        definition._data = [item for item in definition._data if item.operation.name != "barrier"]
        return Gate(self.name, self.num_qubits, [], definition=definition, label=label)

    def to_instruction(self, label: str | None = None) -> Instruction:
        return Instruction(
            self.name, self.num_qubits, self.num_clbits, [], definition=self.copy(), label=label
        )

    # --- miary obwodu ---

    def size(self) -> int:
        return sum(1 for item in self._data if item.operation.name not in ("barrier", "save_statevector"))

    def __len__(self) -> int:
        return len(self._data)

    def depth(self, filter_function=None) -> int:
        levels: dict[tuple[str, int], int] = {}
        depth = 0
        for item in self._data:
            if item.operation.name in ("barrier", "save_statevector"):
                continue
            if filter_function is not None and not filter_function(item):
                continue
            q_idx, c_idx = self._indices_of(item)
            keys = [("q", i) for i in q_idx] + [("c", i) for i in c_idx]
            level = max((levels.get(k, 0) for k in keys), default=0) + 1
            for key in keys:
                levels[key] = level
            depth = max(depth, level)
        return depth

    def count_ops(self) -> OrderedDict:
        counts: dict[str, int] = {}
        for item in self._data:
            counts[item.operation.name] = counts.get(item.operation.name, 0) + 1
        return OrderedDict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def num_nonlocal_gates(self) -> int:
        return sum(
            1
            for item in self._data
            if item.operation.num_qubits > 1 and item.operation.name not in ("barrier", "save_statevector")
        )

    def instruction_indices(self) -> list[tuple[Instruction, list[int], list[int]]]:
        """Lista ``(operacja, kubity, bity)`` z indeksami – wejście symulatora i serializacji."""
        return [(item.operation, *self._indices_of(item)) for item in self._data]

    # --- prezentacja ---

    def draw(self, output: str | None = None, **kwargs):
        from .drawing import TextDrawing

        if output not in (None, "text"):
            # ``mpl``/``latex`` wymagają matplotlib/LaTeX-a, których w przeglądarce nie ma –
            # rysunek tekstowy jest lepszy niż wyjątek w środku notatnika.
            print(f"[qclab] draw(output={output!r}) is not available here – showing the text drawing.")
        return TextDrawing(self)

    def __str__(self) -> str:
        return str(self.draw())

    def __repr__(self) -> str:
        return (
            f"<QuantumCircuit '{self.name}': {self.num_qubits} qubit(s), {self.num_clbits} clbit(s), "
            f"{self.size()} operation(s)>"
        )

    def _repr_pretty_(self, printer, cycle) -> None:  # pragma: no cover - wyświetlanie w IPythonie
        printer.text(str(self.draw()))

    def __eq__(self, other) -> bool:
        if not isinstance(other, QuantumCircuit):
            return NotImplemented
        if (self.num_qubits, self.num_clbits) != (other.num_qubits, other.num_clbits):
            return False
        mine, theirs = self.instruction_indices(), other.instruction_indices()
        if len(mine) != len(theirs):
            return False
        for (op_a, q_a, c_a), (op_b, q_b, c_b) in zip(mine, theirs, strict=True):
            if (op_a.name, q_a, c_a) != (op_b.name, q_b, c_b):
                return False
            if op_a.is_unitary and not np.allclose(op_a.to_matrix(), op_b.to_matrix()):
                return False
        return True

    __hash__ = None  # type: ignore[assignment]


def gate_from_name(name: str, params: list[Any]) -> Gate:
    """Bramka biblioteczna po nazwie – używane przy odtwarzaniu obwodu z serializacji."""
    spec = GATES.get(name)
    if spec is None:
        raise QclabError(f"Unknown gate: {name}")
    if len(params) != spec.num_params:
        raise QclabError(f"Gate {name} takes {spec.num_params} parameter(s), got {len(params)}.")
    return Gate(name, spec.num_qubits, params)
