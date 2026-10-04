"""Rysunek tekstowy obwodu (``qc.draw()``) – jedna kolumna na operację, bez upychania.

Rysunek matplotlib (``draw("mpl")``) wymagałby ~10 MB pakietów w przeglądarce, a zadania
olimpijskie mają kilka kubitów – tekst w zupełności wystarcza i działa także w piaskownicy serwera.
"""

from __future__ import annotations

from .circuit import ParameterExpression, QuantumCircuit


def _fmt_param(value) -> str:
    if isinstance(value, ParameterExpression):
        return str(value)
    try:
        number = float(value)
    except TypeError, ValueError:
        return str(value)
    for den in (1, 2, 3, 4, 6, 8):
        for sign in (1, -1):
            ratio = sign * number * den / 3.141592653589793
            if abs(ratio - round(ratio)) < 1e-9 and round(ratio) != 0:
                num = int(round(ratio))
                head = "π" if abs(num) == 1 else f"{abs(num)}π"
                head = ("-" if num * sign < 0 else "") + head
                return head if den == 1 else f"{head}/{den}"
    return f"{number:.4g}"


class TextDrawing:
    def __init__(self, circuit: QuantumCircuit) -> None:
        self.circuit = circuit

    def _cells(self, operation, qubits, clbits, n):
        """Napisy w kolumnie: słownik wiersz → etykieta i zakres pionowej kreski."""
        from .gates import GATES

        name = operation.name
        cells: dict[int, str] = {}
        if name == "barrier":
            for q in qubits:
                cells[q] = "░"
            return cells, None
        if name == "measure":
            cells[qubits[0]] = f"M→{clbits[0]}"
            return cells, None
        if name == "reset":
            cells[qubits[0]] = "|0>"
            return cells, None
        if name == "save_statevector":
            for q in qubits:
                cells[q] = "S"
            return cells, None
        spec = GATES.get(name)
        label = operation.label or (spec.label if spec else name)
        params = [_fmt_param(p) for p in operation.params]
        text = f"{label}({','.join(params)})" if params else label
        if name in ("cx", "cy", "cz", "ch", "cs", "csdg", "csx", "cp", "crx", "cry", "crz"):
            cells[qubits[0]] = "■"
            cells[qubits[1]] = "⊕" if name == "cx" else (f"[{text}]" if name != "cz" else "■")
        elif name in ("ccx", "ccz"):
            cells[qubits[0]] = cells[qubits[1]] = "■"
            cells[qubits[2]] = "⊕" if name == "ccx" else "■"
        elif name == "mcx":
            for q in qubits[:-1]:
                cells[q] = "■"
            cells[qubits[-1]] = "⊕"
        elif name == "swap":
            cells[qubits[0]] = cells[qubits[1]] = "x"
        elif name == "cswap":
            cells[qubits[0]] = "■"
            cells[qubits[1]] = cells[qubits[2]] = "x"
        elif len(qubits) == 1:
            cells[qubits[0]] = f"[{text}]"
        else:
            for i, q in enumerate(qubits):
                cells[q] = f"[{text}:{i}]"
        span = (min(qubits), max(qubits)) if len(qubits) > 1 else None
        return cells, span

    def lines(self) -> list[str]:
        circuit = self.circuit
        n = circuit.num_qubits
        names = []
        for reg in circuit.qregs:
            for i in range(len(reg)):
                names.append(f"{reg.name}_{i}: " if len(circuit.qregs) > 1 or reg.name != "q" else f"q_{i}: ")
        width = max((len(x) for x in names), default=0)
        rows = [name.rjust(width) for name in names]
        for operation, qubits, clbits in circuit.instruction_indices():
            cells, span = self._cells(operation, qubits, clbits, n)
            col = max(len(text) for text in cells.values()) + 2
            for q in range(n):
                if q in cells:
                    rows[q] += cells[q].center(col, "─")
                elif span and span[0] < q < span[1]:
                    rows[q] += "┼".center(col, "─")
                else:
                    rows[q] += "─" * col
        if circuit.num_clbits:
            rows.append(" " * width + f"c: {circuit.num_clbits} classical bit(s)")
        return rows

    def __str__(self) -> str:
        return "\n".join(self.lines())

    def __repr__(self) -> str:
        return str(self)

    def _repr_pretty_(self, printer, cycle) -> None:  # pragma: no cover - wyświetlanie w IPythonie
        printer.text(str(self))
