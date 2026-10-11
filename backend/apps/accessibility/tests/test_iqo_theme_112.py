"""Motyw IQO Quantum 1.1.2 (A11Y-01 § 4.2): kontrast pasków ramy aplikacji i deklaracja w stopce.

Paczka jest składana ze źródeł w ``themes/iqo-quantum`` (ta sama lista plików, co ``build_zip.py``),
więc test sprawdza to, co operator wgra na produkcję. W kontenerze, który montuje sam ``backend/``,
katalogu motywów nie ma – wtedy test jest pomijany (CI ma całe repozytorium).
"""

from __future__ import annotations

import importlib.util
import io
import zipfile
from pathlib import Path

import pytest
from django.conf import settings

from apps.themes import services
from apps.themes.package import validate_package

pytestmark = pytest.mark.django_db

THEME_DIR = Path(settings.BASE_DIR).parent / "themes" / "iqo-quantum"


@pytest.fixture(scope="module")
def package() -> bytes:
    if not (THEME_DIR / "build_zip.py").exists():
        pytest.skip("brak themes/iqo-quantum (kontener z samym backendem)")
    spec = importlib.util.spec_from_file_location("iqo_build_zip", THEME_DIR / "build_zip.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in module.collect():
            zf.write(path, path.relative_to(THEME_DIR).as_posix())
    return buffer.getvalue()


def test_package_112_validates_and_requires_the_next_release(package):
    result = validate_package(package, app_version="v0.47.0")
    assert result.errors == []
    assert result.manifest["version"] == "1.1.2" and result.manifest["min_app_version"] == "0.47.0"
    older = validate_package(package, app_version="v0.46.0")
    assert any("0.47.0" in error for error in older.errors)


def test_app_frame_bars_use_navy_primary_not_the_blue_fill(package):
    result = validate_package(package, app_version="v0.47.0")
    css = result.theme_css
    rule = css[css.index(":is(.topbar, .timeline-dock)") :]
    assert "--brand: var(--t-primary" in rule.split("}")[0]
    assert "--brand-ink: var(--t-primary-contrast" in rule.split("}")[0]


def test_footer_links_to_statement_only_when_published(package, competition, client_for, monkeypatch):
    from django.core.cache import cache
    from django.core.management import call_command

    cache.clear()
    monkeypatch.setenv("APP_VERSION", "v0.47.0")
    version, result = services.install_package(package)
    assert result.errors == [], result.errors
    services.activate(competition, version)
    client = client_for(competition)
    link = 'href="/dokumenty/deklaracja-dostepnosci/"'

    html = client.get("/").content.decode()
    assert 'data-theme="iqo-quantum"' in html and "iqo-footer" in html
    assert link not in html  # stopka paczki (safe context): bez opublikowanej strony – bez odnośnika

    call_command("seed_accessibility_statement", competition.slug, publish=True, verbosity=0)
    assert link in client.get("/").content.decode()
    cache.clear()
