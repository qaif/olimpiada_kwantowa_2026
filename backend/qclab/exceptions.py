"""Wyjątki qclab. Nazwa ``QiskitError`` jest aliasem, bo tak łapie błędy kod pisany pod Qiskita."""

from __future__ import annotations


class QclabError(Exception):
    """Błąd użycia API (zły kubit, nieznana bramka, obwód poza zakresem symulatora)."""


#: Alias dla ``from qiskit.exceptions import QiskitError``.
QiskitError = QclabError

#: Górna granica kubitów symulatora wektora stanu. 2**20 amplitud complex128 to 16 MB – mieści
#: się w limicie pamięci piaskownicy (``apps.notebooks.runner``) i w karcie przeglądarki, a zadania
#: olimpijskie mają kilka kubitów. Macierz unitarna (``Operator``) ma osobny, niższy limit.
MAX_QUBITS = 20
MAX_OPERATOR_QUBITS = 10
