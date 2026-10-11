"""qclab – mały symulator obwodów kwantowych z API podzbioru Qiskita (zadanie QC-01).

Po co własny symulator, a nie Qiskit: Qiskit 2.x ma rdzeń w Ruście (``qiskit._accelerate``)
i nie wydaje kół dla Pyodide/Emscriptena, więc w JupyterLite (przeglądarka, bez instalacji) się
nie uruchomi – uzasadnienie i alternatywy w ``docs/tasks/QC-01.md`` § 1. Ten pakiet:

- działa **tak samo** w przeglądarce (Pyodide 314, Python 3.14) i na serwerze (ten sam Python,
  ten sam NumPy 2.4) – wynik testu widocznego w przeglądarce i testu ukrytego na serwerze liczy
  ten sam kod,
- zależy wyłącznie od NumPy (jest w dystrybucji Pyodide),
- trzyma się konwencji Qiskita co do bitów: kubit 0 to **najmłodszy** bit indeksu wektora stanu,
  a w napisach wyników (``"01"``) stoi **najbardziej z prawej**.

Moduły zgodności ``qiskit``, ``qiskit_aer`` (katalog ``_compat``) pozwalają pisać
``from qiskit import QuantumCircuit`` bez zmian w kodzie ucznia; ich zakres i granice opisuje
``docs/tasks/QC-01.md`` § 2.
"""

from __future__ import annotations

from .circuit import (
    ClassicalRegister,
    Gate,
    Instruction,
    Parameter,
    QuantumCircuit,
    QuantumRegister,
)
from .exceptions import QclabError
from .quantum_info import Operator, SparsePauliOp, Statevector
from .simulator import sample_counts

__version__ = "0.1.0"

#: Opis zgodności widoczny w ``qiskit.__version__`` w notatniku – uczeń, który wypisze wersję,
#: ma od razu wiedzieć, że to nie jest pełny Qiskit.
COMPAT_LABEL = f"qclab-{__version__} (Qiskit-compatible subset)"

__all__ = [
    "COMPAT_LABEL",
    "ClassicalRegister",
    "Gate",
    "Instruction",
    "Operator",
    "Parameter",
    "QclabError",
    "QuantumCircuit",
    "QuantumRegister",
    "SparsePauliOp",
    "Statevector",
    "sample_counts",
]
