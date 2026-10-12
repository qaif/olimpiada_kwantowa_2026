"""``config/settings/production.py`` po audycie bezpieczeństwa z 10.10.2026.

1. W6 – compose nie przekazuje już ``MINIO_ROOT_*`` do web/worker/beat: produkcja wstaje na samych
   kontach serwisowych ``S3_PUBLIC_*`` + ``S3_PRIVATE_*``, wstaje też na samym koncie root (instalacja
   sprzed rozdzielenia kont) i odmawia, gdy któregoś bucketu nie ma czym obsłużyć.
2. S19 – ciasteczka sesji i CSRF z prefiksem ``__Host-`` przy ``Secure`` (produkcja), bez prefiksu
   przy ``SESSION_COOKIE_SECURE=0``/``CSRF_COOKIE_SECURE=0`` (dev po http – z prefiksem przeglądarka
   nie przyjęłaby ciasteczka wcale); ciasteczko języka ``Secure`` + ``HttpOnly``.

Podproces z od zera zbudowanym środowiskiem – ten sam powód co w apps/core/tests/test_production_guards.py:
moduł ustawień czyta ``os.environ`` przy imporcie, a zmienne kontenera deweloperskiego podstawiłyby odpowiedź.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

PROBE = """
import importlib, json
from django.conf import global_settings as g
p = importlib.import_module("config.settings.production")
s = lambda name: getattr(p, name, getattr(g, name))  # jak django.conf.settings: moduł, potem domyślne
print("PROBE" + json.dumps({
    "session": s("SESSION_COOKIE_NAME"), "csrf": s("CSRF_COOKIE_NAME"),
    "lang_secure": s("LANGUAGE_COOKIE_SECURE"), "lang_httponly": s("LANGUAGE_COOKIE_HTTPONLY"),
    "session_domain": s("SESSION_COOKIE_DOMAIN"), "csrf_domain": s("CSRF_COOKIE_DOMAIN"),
    "session_path": s("SESSION_COOKIE_PATH"), "csrf_path": s("CSRF_COOKIE_PATH"),
    "public_key": p.STORAGES["default"]["OPTIONS"]["access_key"],
    "private_key": p.STORAGES["private_media"]["OPTIONS"]["access_key"],
}))
"""

#: Środowisko produkcyjne tak, jak je składa docker-compose.yml od 10.10.2026 – BEZ MINIO_ROOT_*.
GOOD = {
    "DJANGO_SECRET_KEY": "k" * 60,
    "DATABASE_URL": "postgres://olimpiada:haslo-bazy-z-generatora@db:5432/olimpiada",
    "S3_PUBLIC_ACCESS_KEY": "wagtail-media",
    "S3_PUBLIC_SECRET_KEY": "sekret-publiczny-z-generatora",
    "S3_PRIVATE_ACCESS_KEY": "app-private",
    "S3_PRIVATE_SECRET_KEY": "sekret-prywatny-z-generatora",
    "DEFAULT_FROM_EMAIL": "olimpiada@example.test",
    "DJANGO_DEBUG": "0",
}


def _load(**changes: str | None) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", ""), "DJANGO_SETTINGS_MODULE": "config.settings.production", **GOOD}
    if "SYSTEMROOT" in os.environ:  # Windows: bez tego podproces Pythona nie wstanie
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
    line = next(line for line in completed.stdout.splitlines() if line.startswith("PROBE"))
    return json.loads(line[len("PROBE") :])


def test_starts_on_service_accounts_without_minio_root():
    probe = _probe(_load())
    assert (probe["public_key"], probe["private_key"]) == ("wagtail-media", "app-private")


def test_starts_on_minio_root_alone_for_installations_before_service_accounts():
    probe = _probe(
        _load(
            MINIO_ROOT_USER="minio-root",
            MINIO_ROOT_PASSWORD="haslo-roota-minio-z-generatora",
            S3_PUBLIC_ACCESS_KEY=None,
            S3_PUBLIC_SECRET_KEY=None,
            S3_PRIVATE_ACCESS_KEY=None,
            S3_PRIVATE_SECRET_KEY=None,
        )
    )
    assert probe["public_key"] == probe["private_key"] == "minio-root"


def test_refuses_when_one_bucket_has_no_credentials():
    completed = _load(S3_PRIVATE_SECRET_KEY=None)
    assert completed.returncode != 0
    assert "ImproperlyConfigured" in completed.stderr
    assert "S3_PRIVATE_ACCESS_KEY/S3_PRIVATE_SECRET_KEY" in completed.stderr
    assert "sekret-publiczny-z-generatora" not in completed.stderr


def test_host_prefixed_cookies_when_secure():
    probe = _probe(_load())
    assert probe["session"] == "__Host-sessionid"
    assert probe["csrf"] == "__Host-csrftoken"
    # Warunki prefiksu __Host-: Path=/ i bez Domain (Secure – bo nazwa zależy od niego).
    assert (probe["session_path"], probe["csrf_path"]) == ("/", "/")
    assert (probe["session_domain"], probe["csrf_domain"]) == (None, None)
    assert probe["lang_secure"] is True and probe["lang_httponly"] is True


def test_plain_cookie_names_over_http_in_dev():
    probe = _probe(_load(SESSION_COOKIE_SECURE="0", CSRF_COOKIE_SECURE="0"))
    assert (probe["session"], probe["csrf"]) == ("sessionid", "csrftoken")
    assert probe["lang_secure"] is False
