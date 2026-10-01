"""Bezpieczniki ``config/settings/production.py`` dołożone po audycie z 1.10.2026.

1. ``E2E_MODE`` przy ``DJANGO_DEBUG=0`` – odmowa startu (tryb testowy CAPTCHY, zerowy próg
   antyspamowy i przesuwanie terminów na serwerze nie dawałyby żadnego objawu). Środowisko
   deweloperskie (``E2E_MODE=1`` razem z ``DJANGO_DEBUG=1``) startuje dalej.
2. Sekret z .env.example (``change-me…``) w haśle bazy, ``POSTGRES_PASSWORD``, ``MINIO_ROOT_PASSWORD``
   albo sekrecie konta serwisowego S3 – odmowa startu; komunikat wymienia nazwy, nigdy wartości.
3. Oba aliasy S3 (``default``, ``private_media``) chodzą na ``ExtensionContentTypeS3Storage`` – typ
   pliku z rozszerzenia, nie od przeglądarki (apps/core/storage.py).

Podproces, a nie ``override_settings`` ani przeładowanie w tym procesie: moduł ustawień czyta
``os.environ`` w chwili importu, a pytanie brzmi „czy ten moduł w ogóle się wczyta w takim
środowisku”. Środowisko budowane od zera (jak w ``test_mailers_config.py``) – odziedziczone zmienne
kontenera deweloperskiego (``E2E_MODE=1``, ``DJANGO_DEBUG=1``) podstawiłyby odpowiedź. Bez bazy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

#: Katalog z pakietem ``config`` – korzeń, z którego uruchamia się ``manage.py``.
BACKEND_ROOT = Path(__file__).resolve().parents[3]

PROBE = """
import importlib, json
production = importlib.import_module("config.settings.production")
print("PROBE" + json.dumps({
    "debug": production.DEBUG,
    "e2e_mode": production.E2E_MODE,
    "backends": {alias: cfg["BACKEND"] for alias, cfg in production.STORAGES.items()},
}))
"""

#: Komplet poprawnych wartości – każdy test psuje dokładnie jedną.
GOOD = {
    "DJANGO_SECRET_KEY": "k" * 60,
    "DATABASE_URL": "postgres://olimpiada:haslo-bazy-z-generatora@db:5432/olimpiada",
    "POSTGRES_PASSWORD": "haslo-bazy-z-generatora",
    "MINIO_ROOT_USER": "minio-root",
    "MINIO_ROOT_PASSWORD": "haslo-roota-minio-z-generatora",
    "S3_PUBLIC_ACCESS_KEY": "wagtail-media",
    "S3_PUBLIC_SECRET_KEY": "sekret-publiczny-z-generatora",
    "S3_PRIVATE_ACCESS_KEY": "app-private",
    "S3_PRIVATE_SECRET_KEY": "sekret-prywatny-z-generatora",
    "DEFAULT_FROM_EMAIL": "olimpiada@example.test",
    "DJANGO_DEBUG": "0",
}


def _load_production(**changes: str | None) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", ""), "DJANGO_SETTINGS_MODULE": "config.settings.production", **GOOD}
    # Windows: bez SYSTEMROOT Python nie zainicjuje modułu ``random``/gniazd w podprocesie.
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    for name, value in changes.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return subprocess.run(  # noqa: S603 - własny interpreter, własny program, stałe argumenty
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT,
        env=env,
        timeout=120,
        check=False,
    )


def _probe(completed: subprocess.CompletedProcess) -> dict:
    assert completed.returncode == 0, completed.stderr
    marker = [line for line in completed.stdout.splitlines() if line.startswith("PROBE")]
    assert marker, completed.stdout
    return json.loads(marker[-1][len("PROBE") :])


def test_production_with_generated_secrets_starts_and_uses_the_safe_s3_storage():
    result = _probe(_load_production())

    assert result["debug"] is False
    assert result["backends"]["default"] == "apps.core.storage.ExtensionContentTypeS3Storage"
    assert result["backends"]["private_media"] == "apps.core.storage.ExtensionContentTypeS3Storage"


def test_e2e_mode_without_debug_refuses_to_start():
    completed = _load_production(E2E_MODE="1")

    assert completed.returncode != 0
    assert "ImproperlyConfigured" in completed.stderr
    assert "E2E_MODE" in completed.stderr


def test_e2e_mode_with_debug_still_starts_for_the_dev_stack():
    """docker-compose.dev.yml: `web` z ``E2E_MODE=1`` i ``DJANGO_DEBUG=1`` na tym samym module ustawień."""
    result = _probe(_load_production(E2E_MODE="1", DJANGO_DEBUG="1"))

    assert result["e2e_mode"] is True
    assert result["debug"] is True


@pytest.mark.parametrize(
    ("variable", "value", "reported_as"),
    [
        ("POSTGRES_PASSWORD", "change-me", "POSTGRES_PASSWORD"),
        ("DATABASE_URL", "postgres://olimpiada:change-me@db:5432/olimpiada", "DATABASE_URL"),
        ("MINIO_ROOT_PASSWORD", "change-me-min-8-chars", "MINIO_ROOT_PASSWORD"),
        ("S3_PUBLIC_SECRET_KEY", "change-me-min-8-chars", "S3_PUBLIC_SECRET_KEY"),
        ("S3_PRIVATE_SECRET_KEY", "Change-Me-min-8-chars", "S3_PRIVATE_SECRET_KEY"),
    ],
)
def test_env_example_placeholder_secret_refuses_to_start(variable, value, reported_as):
    completed = _load_production(**{variable: value})

    assert completed.returncode != 0
    assert "ImproperlyConfigured" in completed.stderr
    assert reported_as in completed.stderr
    # Komunikat trafia do logu kontenera – nazwa zmiennej tak, wartość sekretu nie.
    assert "min-8-chars" not in completed.stderr.split("ImproperlyConfigured", 1)[-1]


def test_placeholder_check_also_covers_the_root_fallback_of_service_accounts():
    """Bez kont serwisowych storage chodzi na MINIO_ROOT_* – placeholder tam też zatrzymuje start."""
    completed = _load_production(
        S3_PUBLIC_ACCESS_KEY=None,
        S3_PUBLIC_SECRET_KEY=None,
        S3_PRIVATE_ACCESS_KEY=None,
        S3_PRIVATE_SECRET_KEY=None,
        MINIO_ROOT_PASSWORD="change-me-min-8-chars",
    )

    assert completed.returncode != 0
    assert "MINIO_ROOT_PASSWORD" in completed.stderr
