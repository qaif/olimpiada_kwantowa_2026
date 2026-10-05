"""Skrypt podpowiedzi (MAIL-02 § 1.4): te same listy co ``typos.py`` i te same odpowiedzi (Node).

Listy porównujemy zawsze (parsowanie pliku – bez Node), a zachowanie w ``node --test`` – tylko tam,
gdzie Node jest (obraz aplikacji go nie ma; ten sam wzorzec co ``apps/monitoring/tests/test_errors_js.py``).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from apps.email_delivery.typos import KNOWN_DOMAINS, SHORT_DOMAIN_LENGTH, TLD_TYPOS

BACKEND = Path(__file__).resolve().parents[3]
SCRIPT = BACKEND / "apps" / "email_delivery" / "static" / "email_delivery" / "email-check.js"
TESTS = BACKEND / "js_tests" / "email-check.test.js"


def _js_literal(name: str, opening: str, closing: str) -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    pattern = "var " + name + " = (" + re.escape(opening) + ".*?" + re.escape(closing) + ");"
    match = re.search(pattern, source, flags=re.DOTALL)
    assert match, name
    return match.group(1)


def test_known_domains_are_identical():
    assert json.loads(_js_literal("KNOWN_DOMAINS", "[", "]")) == list(KNOWN_DOMAINS)


def test_tld_typos_are_identical():
    assert json.loads(_js_literal("TLD_TYPOS", "{", "}")) == TLD_TYPOS


def test_short_domain_threshold_is_identical():
    assert f"var SHORT_DOMAIN_LENGTH = {SHORT_DOMAIN_LENGTH};" in SCRIPT.read_text(encoding="utf-8")


def test_script_is_loaded_with_a_nonce_on_every_page():
    base = (BACKEND / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'nonce="{{ request.csp_nonce }}" src="{% static \'email_delivery/email-check.js\' %}"' in base


@pytest.mark.skipif(shutil.which("node") is None, reason="brak Node")
def test_script_passes_in_node():
    result = subprocess.run(  # noqa: S603 - stała ścieżka w repozytorium
        [shutil.which("node"), "--test", str(TESTS)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
