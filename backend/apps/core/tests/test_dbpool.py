"""Pula połączeń z Postgresem: kto ją dostaje, jaki ma rozmiar i jak współgra z ``CONN_MAX_AGE``.

Suita sama chodzi **bez** puli (``config/settings/test.py``), więc to, że pula działa naprawdę,
sprawdza się na stosie compose'a (docs/OPERACJE.md § 11.2). Tutaj pilnujemy reguł, które decydują
o tym, co ląduje w ``DATABASES`` – bo pomyłka w nich wychodzi dopiero na produkcji:

- ``CONN_MAX_AGE`` różne od zera przy włączonej puli to ``ImproperlyConfigured`` przy pierwszym
  zapytaniu, czyli każda strona 500,
- pula w procesie Celery to otwieranie i zamykanie puli przy każdym zadaniu,
- literówka w ``.env`` (``DB_POOL_MIN_SIZE`` > ``DB_POOL_MAX_SIZE``) nie może zatrzymać startu.
"""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from config import dbpool
from config.settings import base as settings_module

# --- kto dostaje pulę ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["/opt/venv/bin/celery", "-A", "config", "worker", "-Q", "default,scan,mail"], True),
        (["/opt/venv/bin/celery", "-A", "config", "beat"], True),
        (["/opt/venv/lib/python3.14/site-packages/celery/__main__.py", "-A", "config", "worker"], True),
        (["/opt/venv/bin/gunicorn", "config.wsgi:application"], False),
        (["manage.py", "migrate"], False),
        (["/opt/venv/bin/pytest", "-q"], False),
        ([], False),
    ],
)
def test_only_celery_processes_are_recognised_as_celery(argv, expected):
    assert dbpool.running_under_celery(argv) is expected
    assert dbpool.pool_enabled_by_default(argv) is not expected


def test_without_the_package_the_pool_is_off_by_default(monkeypatch):
    """Nieprzebudowany obraz: ``web`` ma chodzić jak przed pulą, a nie dawać 500 na każdej stronie."""
    monkeypatch.setattr(dbpool, "pool_available", lambda: False)

    assert dbpool.pool_enabled_by_default(["/opt/venv/bin/gunicorn", "config.wsgi:application"]) is False


# --- rozmiar -------------------------------------------------------------------------------------


def test_pool_options_pass_sane_values_through():
    assert dbpool.pool_options(min_size=1, max_size=4, timeout=10) == {
        "min_size": 1,
        "max_size": 4,
        "timeout": 10.0,
    }


def test_min_size_above_max_size_is_clamped_not_rejected():
    """Górna granica wygrywa – to ona chroni ``max_connections``."""
    assert dbpool.pool_options(min_size=8, max_size=4, timeout=10)["min_size"] == 4


@pytest.mark.parametrize(("max_size", "expected"), [(0, 1), (-3, 1)])
def test_max_size_is_at_least_one(max_size, expected):
    options = dbpool.pool_options(min_size=0, max_size=max_size, timeout=10)
    assert options["max_size"] == expected
    assert options["min_size"] <= options["max_size"]


@pytest.mark.parametrize("timeout", [0, -1])
def test_non_positive_timeout_falls_back_to_the_default(timeout):
    assert (
        dbpool.pool_options(min_size=1, max_size=4, timeout=timeout)["timeout"]
        == dbpool.DEFAULT_TIMEOUT_SECONDS
    )


# --- ustawienia ----------------------------------------------------------------------------------


def _base_settings(monkeypatch, **environ) -> dict:
    """Wykonuje ``config/settings/base.py`` od nowa z podanymi zmiennymi środowiskowymi.

    ``run_path`` buduje świeży słownik modułu i nie dotyka ``sys.modules`` – działające
    ustawienia testów zostają nietknięte.
    """
    for name in ("DB_POOL", "DB_POOL_MIN_SIZE", "DB_POOL_MAX_SIZE", "DB_POOL_TIMEOUT", "DB_CONN_MAX_AGE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    return runpy.run_path(str(Path(settings_module.__file__)), run_name="settings_probe")


def test_pool_turns_persistent_connections_off(monkeypatch):
    """Django przy puli wymaga ``CONN_MAX_AGE=0`` – ``DB_CONN_MAX_AGE`` jest wtedy ignorowane."""
    probe = _base_settings(monkeypatch, DB_POOL="1", DB_CONN_MAX_AGE="60", WEB_THREADS="4")
    database = probe["DATABASES"]["default"]

    assert database["OPTIONS"]["pool"] == {"min_size": 1, "max_size": 4, "timeout": 10.0}
    assert database["CONN_MAX_AGE"] == 0
    assert database["CONN_HEALTH_CHECKS"] is True


def test_pool_size_follows_web_threads_unless_set_explicitly(monkeypatch):
    assert (
        _base_settings(monkeypatch, DB_POOL="1", WEB_THREADS="8")["DATABASES"]["default"]["OPTIONS"]["pool"][
            "max_size"
        ]
        == 8
    )
    explicit = _base_settings(
        monkeypatch,
        DB_POOL="1",
        WEB_THREADS="8",
        DB_POOL_MAX_SIZE="6",
        DB_POOL_MIN_SIZE="2",
        DB_POOL_TIMEOUT="5",
    )
    assert explicit["DATABASES"]["default"]["OPTIONS"]["pool"] == {
        "min_size": 2,
        "max_size": 6,
        "timeout": 5.0,
    }


def test_without_the_pool_conn_max_age_comes_from_the_environment(monkeypatch):
    """``worker`` i ``beat`` (``DB_POOL=0`` w compose) zachowują się dokładnie tak, jak przed pulą."""
    probe = _base_settings(monkeypatch, DB_POOL="0", DB_CONN_MAX_AGE="45")
    database = probe["DATABASES"]["default"]

    assert "pool" not in database["OPTIONS"]
    assert database["CONN_MAX_AGE"] == 45


def test_every_connection_is_labelled_with_the_service_name(monkeypatch):
    probe = _base_settings(monkeypatch, DB_POOL="0", DB_APPLICATION_NAME="olimpiada-worker")
    assert probe["DATABASES"]["default"]["OPTIONS"]["application_name"] == "olimpiada-worker"


def test_the_test_suite_itself_runs_without_the_pool(settings):
    assert "pool" not in settings.DATABASES["default"].get("OPTIONS", {})


def test_the_pool_package_is_installed():
    """``psycopg[pool]`` w zależnościach – bez niego włączona pula to 500 przy pierwszym zapytaniu."""
    import psycopg_pool

    assert psycopg_pool.ConnectionPool
