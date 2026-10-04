"""Rejestr czynności (OPS-02 § 6): wiersz monitorowania błędów wyłącznie przy włączonym klientcie."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from apps.accounts.processing_register import HOSTING_RECIPIENT, activities_for
from apps.monitoring.register import RETENTION_DAYS, activity

pytestmark = pytest.mark.django_db

REPO = Path(__file__).resolve().parents[4]


def _keys(competition=None):
    return [a.key for a in activities_for(competition)]


def test_no_row_without_dsn(settings, competition):
    settings.SENTRY_DSN = ""

    assert activity() is None
    assert "monitoring-bledow" not in _keys(competition)


def test_row_with_dsn_names_an_internal_processor_without_third_country(settings, competition):
    settings.SENTRY_DSN = "https://k@errors.example.org/1"
    settings.SENTRY_BROWSER = False

    row = next(a for a in activities_for(competition) if a.key == "monitoring-bledow")

    assert HOSTING_RECIPIENT in row.recipients
    assert any("bez przekazania do państwa trzeciego" in r for r in row.recipients)
    assert str(RETENTION_DAYS) in row.retention
    assert not any("JavaScript" in c for c in row.categories)
    assert all([row.name, row.purpose, row.legal_basis, row.subjects, row.categories, row.measures])


def test_browser_errors_add_their_own_category(settings):
    settings.SENTRY_DSN = "https://k@errors.example.org/1"
    settings.SENTRY_BROWSER = True

    assert any("JavaScript" in c for c in activity().categories)


def test_browser_only_errors_still_get_the_row(settings):
    """M5: pusty DSN serwera, ale przeglądarki wysyłają (``SENTRY_BROWSER_DSN``) – wiersz jest."""
    settings.SENTRY_DSN = ""
    settings.SENTRY_BROWSER = True
    settings.SENTRY_BROWSER_DSN = "https://k@errors.example.org/2"

    assert activity() is not None
    settings.SENTRY_BROWSER = False
    assert activity() is None


def test_retention_matches_the_glitchtip_setting_in_compose():
    compose = REPO / "docker-compose.yml"
    if not compose.exists():
        pytest.skip("docker-compose.yml poza obrazem (kontener montuje tylko backend/)")
    match = re.search(r'GLITCHTIP_RETENTION_DAYS: "(\d+)"', compose.read_text(encoding="utf-8"))

    assert match and int(match.group(1)) == RETENTION_DAYS


def test_errors_label_is_reserved_for_platform_subdomains():
    """Konkurs ze slugiem ``errors`` zająłby ``errors.<domena>`` – adres GlitchTipa (ERRORS_PROXY=1)."""
    from apps.web.competition_create_forms import reserved_labels

    assert "errors" in reserved_labels()
