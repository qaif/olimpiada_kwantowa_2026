"""Wykresy w wersji tekstowej. matplotlib nie jest częścią środowiska (rozmiar pobrania – QC-01 § 5)."""

from __future__ import annotations

from .quantum_info import Statevector


class TextFigure:
    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return self.text

    def _repr_pretty_(self, printer, cycle) -> None:  # pragma: no cover - wyświetlanie w IPythonie
        printer.text(self.text)

    def savefig(self, *args, **kwargs) -> None:
        print("[qclab] savefig() is not available – figures are text-only here.")


def plot_histogram(data, figsize=None, color=None, number_to_keep=None, sort="asc", title=None, **kwargs):
    series = data if isinstance(data, list) else [data]
    lines = [title] if title else []
    for index, counts in enumerate(series):
        if not counts:
            continue
        total = sum(counts.values())
        items = sorted(counts.items(), reverse=sort == "desc")
        if number_to_keep:
            items = sorted(items, key=lambda kv: -kv[1])[:number_to_keep]
        width = max(len(str(k)) for k, _ in items)
        if len(series) > 1:
            lines.append(f"# {index}")
        for key, value in items:
            share = value / total if total else 0
            lines.append(f"{str(key).rjust(width)} | {'#' * round(share * 40):<40} {share:.3f}")
    return TextFigure("\n".join(lines))


plot_distribution = plot_histogram


def plot_bloch_multivector(state, title: str = "", **kwargs):
    state = state if isinstance(state, Statevector) else Statevector(state)
    import numpy as np

    from .quantum_info import Pauli

    lines = [title] if title else []
    n = state.num_qubits
    for qubit in range(n):
        vector = []
        for label in ("X", "Y", "Z"):
            full = "".join(label if q == qubit else "I" for q in reversed(range(n)))
            vector.append(float(np.real(state.expectation_value(Pauli(full)))))
        lines.append(f"qubit {qubit}: Bloch (x, y, z) = ({vector[0]:+.3f}, {vector[1]:+.3f}, {vector[2]:+.3f})")
    return TextFigure("\n".join(lines))


def circuit_drawer(circuit, output=None, **kwargs):
    return circuit.draw(output)


def array_to_latex(array, prefix: str = "", **kwargs) -> TextFigure:
    import numpy as np

    return TextFigure(prefix + np.array2string(np.asarray(array), precision=4, suppress_small=True))
