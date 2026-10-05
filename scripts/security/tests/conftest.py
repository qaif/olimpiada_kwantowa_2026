"""Testy skryptów SEC-02 – biegną bez Django i bez sieci (job CI ``supply-chain``)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
