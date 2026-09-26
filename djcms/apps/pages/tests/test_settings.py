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


HOSTS_PROBE = (
    "import django; django.setup(); from django.conf import settings as s; import json; "
    "print(json.dumps([s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS, hasattr(s, 'SITE_ID')]))"
)


def _run_probe(probe, **env):
    base = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DJCMS_", "SITE_DOMAIN", "EXTRA_", "PLATFORM_"))
    }
    base.update({"DJANGO_SETTINGS_MODULE": "config.settings.production", "DJCMS_SECRET_KEY": "s" * 64, **env})
    return subprocess.run(
        [sys.executable, "-c", probe], cwd=DJCMS_DIR, env=base, capture_output=True, text=True, timeout=60
    )


def test_hosts_follow_the_main_app_variables():
    """DJ-02 § 5.4: te same zmienne co backend + ``dj.``, ``djcms``; ``DJCMS_ALLOWED_HOSTS`` tylko dokłada."""
    import json

    result = _run_probe(
        HOSTS_PROBE,
        SITE_DOMAIN="Olimpiada.test",
        EXTRA_DOMAINS="fizyka.example  chemia.example",
        PLATFORM_SUBDOMAINS="1",
        DJCMS_ALLOWED_HOSTS="dodatkowy.example",
    )
    assert result.returncode == 0, result.stderr
    hosts, origins, has_site_id = json.loads(result.stdout)
    assert hosts == [
        "dodatkowy.example",
        "djcms",
        "olimpiada.test",
        "dj.olimpiada.test",
        "fizyka.example",
        "chemia.example",
        ".olimpiada.test",
    ]
    assert origins == [
        "https://olimpiada.test",
        "https://dj.olimpiada.test",
        "https://fizyka.example",
        "https://chemia.example",
        "https://*.olimpiada.test",
    ]
    assert has_site_id is False  # DJ-02 D4: witrynę wyznacza warstwa, nie ustawienie


def test_hosts_without_platform_subdomains_have_no_wildcard():
    import json

    result = _run_probe(HOSTS_PROBE, SITE_DOMAIN="olimpiada.test")
    assert result.returncode == 0, result.stderr
    hosts, origins, _ = json.loads(result.stdout)
    assert not any(host.startswith(".") for host in hosts)
    assert not any("*" in origin for origin in origins)


def test_missing_route_contract_stops_the_start(tmp_path):
    result = _run_probe(HOSTS_PROBE, DJCMS_CONTRACT_DIR=str(tmp_path))
    assert result.returncode != 0
    assert "Kontrakt tras" in result.stderr and "app_routes.json" in result.stderr


def test_missing_route_contract_is_tolerated_only_while_building(tmp_path):
    result = _run_probe(HOSTS_PROBE, DJCMS_CONTRACT_DIR=str(tmp_path), DJCMS_BUILD="1")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "content",
    ["{", '{"version": 2}', '{"version": 1, "first_segments": []}', None],
)
def test_contract_loader_rejects_bad_files(tmp_path, content):
    from apps.pages.contract import ContractError, load_app_routes

    if content is not None:
        (tmp_path / "app_routes.json").write_text(content, encoding="utf-8")
    with pytest.raises(ContractError):
        load_app_routes(tmp_path)


def test_contract_loader_rejects_bad_regex(tmp_path):
    import json

    from apps.pages.contract import ContractError, load_app_routes

    data = {key: [] for key in ("first_segments", "nested_paths", "root_regexes", "private_prefixes")}
    data.update({"version": 1, "app_re": "(", "app_re_prefixed": "^/x$"})
    (tmp_path / "app_routes.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ContractError, match="app_re"):
        load_app_routes(tmp_path)
