"""Klient błędów (OPS-02 § 2): bez DSN – zero skutków ubocznych; z DSN – nic wrażliwego nie wychodzi.

Oba przypadki w **podprocesie**: inicjalizacja ``sentry_sdk`` łata Django, Celery i Redisa na stałe
(w obrębie procesu), a pytanie „czy moduł w ogóle został zaimportowany” ma sens tylko w świeżym
interpreterze – w procesie pytesta mógł go zaimportować inny test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]


def _run(code: str, **env: str) -> subprocess.CompletedProcess:
    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test", **env}
    return subprocess.run(  # noqa: S603 - stały interpreter i kod z tego pliku
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_without_dsn_the_client_is_never_imported():
    completed = _run(
        "import sys, django; django.setup();"
        "from django.conf import settings;"
        "from apps.monitoring.sentry import init_from_settings;"
        "assert init_from_settings() is False;"
        "assert 'apps.monitoring.middleware.ErrorTrackingTagMiddleware' not in settings.MIDDLEWARE;"
        "print(sorted(m for m in sys.modules if m.startswith('sentry_sdk')))",
        SENTRY_DSN="",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"


def test_base_settings_add_the_tag_middleware_only_with_a_dsn():
    """``base.py`` (bez nakładki testowej): DSN dokłada warstwę tuż za ``CompetitionMiddleware``."""
    code = (
        "import importlib, os;"
        "os.environ.setdefault('DATABASE_URL', 'postgres://u:p@localhost:5432/x');"
        "base = importlib.import_module('config.settings.base');"
        "m = base.MIDDLEWARE;"
        "name = 'apps.monitoring.middleware.ErrorTrackingTagMiddleware';"
        "print(name in m and m.index(name) == m.index('apps.tenancy.middleware.CompetitionMiddleware') + 1)"
    )
    with_dsn = _run(code, SENTRY_DSN="https://k@errors.example.org/1")
    without = _run(code, SENTRY_DSN="")

    assert with_dsn.stdout.strip() == "True", with_dsn.stderr
    assert without.stdout.strip() == "False", without.stderr


SEND_EVENTS = r"""
import json, logging, sys
import django
django.setup()
import sentry_sdk
from sentry_sdk.transport import Transport
from apps.monitoring import sentry as mon

captured = []

class Capture(Transport):
    def capture_envelope(self, envelope):
        for item in envelope.items:
            captured.append(item.payload.get_bytes().decode("utf-8", "replace"))

assert mon.init_sentry(dsn="https://pub@errors.example.org/3", release="v9.9.9", environment="test",
                       transport=Capture)
mon.set_competition_tag("iqo")

def save_person(passport_number, health_notes):
    token = "s3cr3t-T0ken-value"
    raise ValueError(f"Nie zapisano osoby jan.kowalski@example.org, PESEL 08241512345")

try:
    save_person("ZX1234567", "astma")
except ValueError:
    sentry_sdk.capture_exception()

with sentry_sdk.new_scope() as scope:
    scope.set_extra("celery-job", {"task_name": "t", "args": ["jan.kowalski@example.org"], "kwargs": {}})
    scope.set_context("delegation", {"passport": "ZX1234567"})
    logging.getLogger("apps.test").error("Reset hasła: https://x.org/reset/?token=s3cr3t-T0ken-value")

sentry_sdk.get_client().flush(timeout=5)
print(json.dumps(captured))
"""


def test_with_dsn_events_leave_without_personal_data():
    pytest.importorskip("sentry_sdk")
    completed = _run(SEND_EVENTS)

    assert completed.returncode == 0, completed.stderr
    payloads = json.loads(completed.stdout.strip().splitlines()[-1])
    assert len(payloads) == 2
    sent = "\n".join(payloads)
    for secret in ("ZX1234567", "astma", "s3cr3t-T0ken-value", "jan.kowalski@example.org", "08241512345"):
        assert secret not in sent, secret
    first = json.loads(payloads[0])
    assert first["release"] == "v9.9.9"
    assert first["environment"] == "test"
    assert first["tags"]["competition"] == "iqo"
    frames = first["exception"]["values"][0]["stacktrace"]["frames"]
    assert frames[-1]["function"] == "save_person"
    assert all("vars" not in frame for frame in frames)
    assert "user" not in first
    # M1: zamknięta lista integracji – bez samowłączających się (ocena AI zapisywałaby prace
    # uczestników) i bez ``argv``/``modules``.
    integrations = set(first["sdk"]["integrations"])
    assert {"django", "celery", "redis", "logging"} <= integrations
    assert not integrations & {"anthropic", "openai", "google_genai", "argv", "modules", "boto3", "httpx"}
