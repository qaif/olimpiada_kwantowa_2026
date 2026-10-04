"""Loader błędów JavaScriptu w Node – ``node --test backend/js_tests/errors.test.js`` (OPS-02 § 5).

Obraz aplikacji Node nie ma, więc w kontenerze test się **pomija**; na maszynie z Node biegnie naprawdę
(ten sam wzorzec co ``apps/chat/tests/test_e2e_js.py``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]
TESTS = BACKEND / "js_tests" / "errors.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="brak Node")
def test_error_loader_passes_in_node():
    result = subprocess.run(  # noqa: S603 - stała ścieżka w repozytorium
        [shutil.which("node"), "--test", str(TESTS)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
