"""Wspólna infrastruktura scenariusza E2E: adres bazowy, sesje ról, artefakty diagnostyczne.

Scenariusz chodzi po **prawdziwym** środowisku compose (Postgres, MinIO, Redis, ClamAV, Celery),
więc jedyne, co tu jest podrobione, to nic. Determinizm bierze się z czystego środowiska
(`scripts/e2e.sh`) i z losowego adresu e-mail uczestnika, a nie z mockowania usług.

Artefakty (zrzuty ekranu każdego kroku, log konsoli przeglądarki, zrzut HTML przy błędzie) idą do
``e2e/artifacts/`` – katalog jest w ``.gitignore``.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

LOGGER = logging.getLogger("e2e")

#: Katalog na zrzuty ekranu i logi. Bind-mount ``./e2e:/e2e`` sprawia, że artefakty z kontenera
#: są widoczne na hoście od razu po przebiegu.
ARTIFACTS_DIR = Path(__file__).parent / "artifacts"

#: Hasło kont z ``manage.py seed_demo``.
DEMO_PASSWORD = "Demo12345!"
COORDINATOR_EMAIL = "koordynator@example.com"
REVIEWER_1_EMAIL = "recenzent1@example.com"
REVIEWER_2_EMAIL = "recenzent2@example.com"

#: Domyślny czas oczekiwania na pojedynczą asercję. Sieć compose jest szybka, ale pierwsze
#: żądanie po starcie kontenera potrafi czekać na kompilację szablonów.
EXPECT_TIMEOUT_MS = 15_000


def pytest_configure(config):
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    for stale in ARTIFACTS_DIR.glob("*"):
        if stale.is_file():
            stale.unlink()
    expect.set_options(timeout=EXPECT_TIMEOUT_MS)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Udostępnia wynik fazy testu fixture'om (potrzebne do zrzutów przy błędzie)."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(scope="session")
def base_url() -> str:
    """Adres aplikacji. W sieci compose to ``http://web:8000`` (patrz docker-compose.dev.yml)."""
    return os.environ.get("E2E_BASE_URL", "http://web:8000").rstrip("/")


@pytest.fixture(scope="session")
def s3_host_rewrite() -> tuple[str, str] | None:
    """``localhost:9000=minio:9000`` → para (host publiczny, host w sieci compose).

    Presigned URL jest podpisywany hostem, pod którym MinIO widzi **przeglądarka uczestnika**
    (``S3_PUBLIC_ENDPOINT_URL``). W sieci compose taki host nie istnieje, więc do sprawdzenia
    samego pobrania podmieniamy go – podpis SigV4 obejmuje nagłówek ``Host``, więc żądanie musi
    ten nagłówek zachować (patrz ``e2e/test_full_cycle.py``).
    """
    raw = os.environ.get("E2E_S3_HOST_REWRITE", "").strip()
    if not raw or "=" not in raw:
        return None
    public, _, internal = raw.partition("=")
    return public.strip(), internal.strip()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 1400, "height": 1000},
        "locale": "pl-PL",
        "ignore_https_errors": True,
    }


class RoleSession:
    """Osobny kontekst przeglądarki dla jednej roli – własne ciasteczka, własny log konsoli."""

    def __init__(self, browser: Browser, base_url: str, name: str, context_args: dict):
        self.name = name
        self.base_url = base_url
        self.context: BrowserContext = browser.new_context(**context_args)
        self.page: Page = self.context.new_page()
        self.console: list[str] = []
        self.page.on("console", self._record_console)
        self.page.on("pageerror", lambda error: self.console.append(f"PAGEERROR {error}"))
        # Formularze nieodwracalne (wystawienie oceny, decyzja komisji) pytają przez
        # ``window.confirm``. Playwright domyślnie **odrzuca** dialogi, więc bez tego handlera
        # Alpine anulowałby wysłanie formularza, a test „klikał w próżnię”.
        self.page.on("dialog", lambda dialog: dialog.accept())
        self._step = 0

    def _record_console(self, message) -> None:
        self.console.append(f"{message.type.upper()} {message.text}")

    def goto(self, path: str) -> None:
        self.page.goto(f"{self.base_url}{path}", wait_until="domcontentloaded")

    def step(self, label: str) -> None:
        """Zrzut ekranu z numerem kroku – artefakt do wglądu po nieudanym przebiegu."""
        self._step += 1
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:60]
        LOGGER.info("[%s] %s", self.name, label)
        self.page.screenshot(path=str(ARTIFACTS_DIR / f"{self.name}-{self._step:02d}-{slug}.png"))

    def dump(self, reason: str) -> None:
        """Awaryjny zrzut: pełna strona, HTML i log konsoli."""
        prefix = ARTIFACTS_DIR / f"FAIL-{self.name}-{reason}"
        try:
            self.page.screenshot(path=f"{prefix}.png", full_page=True)
            Path(f"{prefix}.html").write_text(self.page.content(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - diagnostyka nie może przesłonić błędu testu
            LOGGER.warning("Nie udało się zapisać zrzutu dla %s: %s", self.name, exc)
        Path(f"{prefix}.console.log").write_text("\n".join(self.console), encoding="utf-8")

    def close(self) -> None:
        self.context.close()


@pytest.fixture
def sessions(request, browser: Browser, base_url: str, browser_context_args):
    """Fabryka sesji ról. Przy nieudanym teście zrzuca stan każdej otwartej sesji."""
    created: list[RoleSession] = []

    def make(name: str) -> RoleSession:
        session = RoleSession(browser, base_url, name, browser_context_args)
        created.append(session)
        return session

    yield make

    failed = getattr(request.node, "rep_call", None) is not None and request.node.rep_call.failed
    for session in created:
        if failed:
            session.dump("stan-koncowy")
        session.close()


@pytest.fixture(scope="session")
def participant_identity() -> dict:
    """Nowe konto uczestnika na każdy przebieg – scenariusz nie zależy od stanu poprzedniego."""
    token = uuid.uuid4().hex[:10]
    return {
        "email": f"e2e-{token}@example.test",
        "password": "Olimpiada-Testowa-2026",
        "first_name": "Jan",
        "last_name": f"Kowalski{token[:4].upper()}",
        "school": "XIV Liceum Ogólnokształcące",
        "district": "mazowieckie",
        "birth_year": "2008",
    }
