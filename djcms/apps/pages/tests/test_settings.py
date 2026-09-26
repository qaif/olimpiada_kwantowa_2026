"""Bezpieczniki ustawień produkcyjnych djcms (§ 8.2 docs/tasks/DJ-01.md).

Compose nie ma ``${DJCMS_SECRET_KEY:?}`` (wywróciłoby ``docker compose config`` każdemu bez tej
zmiennej), więc brak sekretu musi wykryć sam plik ustawień. Każdy przypadek w osobnym procesie:
moduł ustawień czyta środowisko przy imporcie, a proces testów ma już wczytane ustawienia testowe.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

DJCMS_DIR = Path(__file__).resolve().parents[3]
PROBE = (
    "import django; django.setup(); from django.conf import settings as s; "
    "print(s.DEBUG, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.STORAGES['staticfiles']['BACKEND'])"
)


def _run(**env):
    base = {k: v for k, v in os.environ.items() if not k.startswith("DJCMS_")}
    base.update({"DJANGO_SETTINGS_MODULE": "config.settings.production", **env})
    return subprocess.run(
        [sys.executable, "-c", PROBE], cwd=DJCMS_DIR, env=base, capture_output=True, text=True, timeout=60
    )


@pytest.mark.parametrize("secret", ["", "za-krotki"])
def test_production_refuses_missing_or_short_secret(secret):
    result = _run(DJCMS_SECRET_KEY=secret)
    assert result.returncode != 0
    assert "DJCMS_SECRET_KEY" in result.stderr


def test_production_with_secret_is_hardened():
    result = _run(DJCMS_SECRET_KEY="s" * 64)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "False",
        "True",
        "True",
        "whitenoise.storage.CompressedManifestStaticFilesStorage",
    ]


def test_build_flag_allows_loading_without_secret():
    # Tylko ``collectstatic`` w Dockerfile – żaden proces obsługujący żądania nie ma tej flagi.
    result = _run(DJCMS_BUILD="1")
    assert result.returncode == 0, result.stderr
