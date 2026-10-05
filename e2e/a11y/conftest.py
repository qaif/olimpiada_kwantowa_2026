"""Wspólna infrastruktura suity dostępności: adres, dane z ``state/fixture.json``, sesje ról.

Każda rola ma **jeden** kontekst przeglądarki na całą sesję testów (logowanie raz) – ekranów jest
kilkadziesiąt i logowanie przed każdym byłoby połową czasu przebiegu. Konteksty są osobne, więc
ciasteczka ról się nie mieszają. Logowanie idzie przez prawdziwy formularz (``/login/``), a konto
z 2FA przechodzi drugi krok kodem TOTP liczonym tutaj (RFC 6238, bez zależności spoza biblioteki
standardowej).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from baseline import (  # noqa: E402
    ARTIFACTS,
    append_to_baseline,
    blocking,
    load_baseline,
    update_requested,
    write_report,
)

STATE = Path(__file__).resolve().parent / "state" / "fixture.json"
DESKTOP = {"width": 1280, "height": 900}

#: Wyniki axe zebrane w trakcie sesji – raport (JSON + Markdown) zapisuje ``pytest_sessionfinish``.
RESULTS: dict[str, dict] = {}
NEW_BLOCKING: list[tuple[str, str]] = []


def totp(secret: str, at: float | None = None, step: int = 30) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = int((at if at is not None else time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


@pytest.fixture(scope="session")
def base() -> str:
    return os.environ.get("E2E_BASE_URL", "http://web:8000").rstrip("/")


@pytest.fixture(scope="session")
def data() -> dict:
    if not STATE.exists():
        pytest.exit(f"Brak {STATE} – serwer audytu startuje przez scripts/a11y.sh (e2e/a11y/seed.py).", 2)
    return json.loads(STATE.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def accepted() -> dict[str, str]:
    return load_baseline()


def login(context, base: str, email: str, password: str, *, prefix: str = "", secret: str | None = None):
    page = context.new_page()
    page.goto(f"{base}{prefix}/login/")
    page.fill("#id_username", email)
    page.fill("#id_password", password)
    page.locator("main form button[type=submit]").first.click()
    page.wait_for_load_state()
    if "/login/2fa/" in page.url and secret:
        page.fill("input[name=code]", totp(secret))
        page.locator("main form button[type=submit]").first.click()
        page.wait_for_load_state()
    assert "/login/" not in page.url, f"logowanie {email} nie powiodło się ({page.url})"
    page.close()
    return context


@pytest.fixture(scope="session")
def contexts(browser, base, data):
    """Konteksty ról: ``anon``, ``participant``, ``coordinator``, ``reviewer``, ``leader``, ``student``."""
    made: dict = {}

    def get(role: str):
        if role in made:
            return made[role]
        context = browser.new_context(viewport=DESKTOP, locale="pl-PL")
        password = data["password"]
        iqo = data["iqo_prefix"]
        accounts = {
            "participant": ("uczestnik1@example.com", ""),
            # Osobne konto do włączenia 2FA w teście (ekran kodów zapasowych) – nie psuje logowań reszty.
            "participant2": ("uczestnik2@example.com", ""),
            "coordinator": ("koordynator@example.com", ""),
            "reviewer": ("recenzent1@example.com", ""),
            "leader": (data["iqo"]["leader_email"], iqo),
            "student": (data["iqo"]["student_email"], iqo),
        }
        if role in accounts:
            email, prefix = accounts[role]
            login(context, base, email, password, prefix=prefix)
        made[role] = context
        return context

    yield get
    for context in made.values():
        context.close()


def record(page_id: str, path: str, result: dict, accepted: dict[str, str]) -> list[dict]:
    """Zapisuje wynik ekranu do raportu i zwraca naruszenia, które przewracają przebieg."""
    RESULTS[page_id] = {"path": path, **result}
    failing = blocking(page_id, result["violations"], accepted)
    NEW_BLOCKING.extend((page_id, v["id"]) for v in failing)
    return failing


def pytest_sessionfinish(session, exitstatus):
    if RESULTS:
        write_report(RESULTS)
    if NEW_BLOCKING and update_requested():
        append_to_baseline(NEW_BLOCKING)


def shot(page, name: str) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f"{name}.png"), full_page=False)
