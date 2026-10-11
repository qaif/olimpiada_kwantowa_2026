"""Moduł zgodności z Qiskitem – wszystko liczy ``qclab`` (docs/tasks/QC-01.md § 2)."""

from qclab import COMPAT_LABEL as __version__
from qclab.backends import generate_preset_pass_manager, transpile
from qclab.circuit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qclab.exceptions import QiskitError

__all__ = [
    "ClassicalRegister",
    "QiskitError",
    "QuantumCircuit",
    "QuantumRegister",
    "__version__",
    "generate_preset_pass_manager",
    "transpile",
]
