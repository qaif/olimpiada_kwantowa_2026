"""Kryptografia rozmów szyfrowanych w JavaScripcie – ``node --test backend/js_tests``.

Te same funkcje, które działają w przeglądarce (``static/js/chat-e2e.js``), sprawdzone w Node ≥ 20
(WebCrypto w ``globalThis.crypto``). Obraz aplikacji Node nie ma, więc w kontenerze test się
**pomija**; na maszynie deweloperskiej z Node – i w CI, jeśli go dostanie – biegnie naprawdę.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]
TESTS = BACKEND / "js_tests" / "chat-e2e.test.js"


@pytest.mark.skipif(
    shutil.which("node") is None, reason="brak Node – testy WebCrypto biegną tylko z Node ≥ 20"
)
def test_webcrypto_scheme_passes_in_node():
    result = subprocess.run(  # noqa: S603 - stała ścieżka w repozytorium, bez danych od użytkownika
        [shutil.which("node"), "--test", str(TESTS)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
