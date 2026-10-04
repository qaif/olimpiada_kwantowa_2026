"""Wgranie paczki (serwis i ``manage.py theme_install``), aktywacja, usunięcie wersji, audyt."""

from __future__ import annotations

import io
import json
import sys

import pytest
from django.core.files.storage import default_storage
from django.core.management import CommandError, call_command
from django.db.models import ProtectedError

from apps.core.models import AuditLog
from apps.themes import services
from apps.themes.models import Theme, ThemeVersion
from apps.themes.runtime import forget_runtime

from .helpers import IQO_ZIP, example_files, zip_with

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_runtime():
    forget_runtime()
    yield
    forget_runtime()


def test_install_publishes_files_under_immutable_prefix():
    version, result = services.install_package(zip_with())
    assert result.errors == []
    assert version.status == ThemeVersion.Status.VALID
    assert version.public_prefix == f"themes/example/1.0.0-{result.sha256[:8]}/"
    for path in (
        "theme.css",
        "tokens.css",
        "assets/fonts/demo.woff2",
        "assets/img/pixel.png",
        "screenshot.png",
    ):
        assert default_storage.exists(version.public_prefix + path), path
    # Szablony i manifest nie trafiają do bucketu publicznego – leżą w bazie.
    assert not default_storage.exists(version.public_prefix + "templates/theme/footer.html")
    assert not default_storage.exists(version.public_prefix + "manifest.json")
    assert "theme/footer.html" in version.templates
    assert version.package.name.endswith(".zip")
    with default_storage.open(version.public_prefix + "tokens.css") as handle:
        assert "--t-primary: #3c4bff;" in handle.read().decode()
    theme = version.theme
    assert (theme.name, theme.author) == ("Przykład", "Testy platformy")


def test_install_audits_upload():
    version, _ = services.install_package(zip_with())
    entry = AuditLog.objects.get(action=services.AUDIT_UPLOADED)
    assert entry.target_id == str(version.pk)
    assert entry.diff["status"] == "valid" and entry.diff["slug"] == "example"


def test_invalid_package_is_saved_with_report_and_publishes_nothing():
    version, result = services.install_package(zip_with({"theme.css": "@import 'x.css';"}))
    assert version.status == ThemeVersion.Status.INVALID
    assert version.public_prefix == ""
    assert any("@import" in e for e in version.report["errors"])
    assert not default_storage.exists(f"themes/example/1.0.0-{result.sha256[:8]}/theme.css")


def test_package_without_manifest_saves_nothing():
    version, result = services.install_package(zip_with({"manifest.json": None}))
    assert version is None and result.errors
    assert not ThemeVersion.objects.exists()


def test_valid_version_is_immutable_and_invalid_is_replaced():
    services.install_package(zip_with({"theme.css": "@import 'x.css';"}))
    version, result = services.install_package(zip_with())
    assert version.is_valid and ThemeVersion.objects.count() == 1
    again, result = services.install_package(zip_with({"theme.css": "a{color:red}"}))
    assert again is None
    assert any("niezmienna" in e for e in result.errors)


def test_classic_slug_cannot_be_uploaded():
    manifest = json.loads(example_files()["manifest.json"])
    manifest["slug"] = "classic"
    version, result = services.install_package(zip_with({"manifest.json": json.dumps(manifest)}))
    assert version is None
    assert Theme.objects.get(slug="classic").versions.count() == 0


def test_av_scan_infected(settings, monkeypatch):
    settings.THEMES_AV_SCAN = True
    monkeypatch.setattr(services, "scan_package", lambda data: ("INFECTED", "Eicar-Test-Signature"))
    version, _ = services.install_package(zip_with())
    assert version.status == ThemeVersion.Status.INVALID
    assert any("Eicar" in e for e in version.report["errors"])


def test_av_scanner_unavailable_is_an_error(settings, monkeypatch):
    from apps.submissions import antivirus

    settings.THEMES_AV_SCAN = True

    def boom(*args, **kwargs):
        raise antivirus.ClamAVUnavailable("brak clamd")

    monkeypatch.setattr(antivirus, "scan_stream", boom)
    version, _ = services.install_package(zip_with())
    assert version.status == ThemeVersion.Status.INVALID
    assert any("skaner niedostępny" in e for e in version.report["errors"])


def test_activate_and_rollback(competition):
    v1, _ = services.install_package(zip_with())
    manifest = json.loads(example_files()["manifest.json"])
    manifest["version"] = "1.1.0"
    v2, _ = services.install_package(zip_with({"manifest.json": json.dumps(manifest)}))
    services.activate(
        competition, v2, {"layouts": {"header": "split", "cards": "nonsense"}, "brand_accent": 1}
    )
    competition.refresh_from_db()
    assert competition.theme_version_id == v2.pk
    assert competition.theme_options == {
        "layouts": {"header": "split", "cards": "outline"},
        "brand_accent": True,
    }
    services.activate(competition, v1)
    competition.refresh_from_db()
    assert competition.theme_version_id == v1.pk
    services.activate(competition, None)
    competition.refresh_from_db()
    assert competition.theme_version_id is None and competition.theme_options == {}
    actions = list(
        AuditLog.objects.filter(action=services.AUDIT_ACTIVATED).order_by("pk").values_list("diff", flat=True)
    )
    assert [a["theme"] for a in actions] == ["example", "example", "classic"]


def test_cannot_activate_invalid_version(competition):
    version, _ = services.install_package(zip_with({"theme.css": "@import 'x';"}))
    with pytest.raises(services.ThemeError):
        services.activate(competition, version)


def test_used_version_cannot_be_deleted(competition):
    version, _ = services.install_package(zip_with())
    services.activate(competition, version)
    with pytest.raises(services.ThemeError):
        services.delete_version(version)
    with pytest.raises(ProtectedError):
        version.delete()


def test_delete_unused_version_removes_files():
    version, _ = services.install_package(zip_with())
    prefix = version.public_prefix
    services.delete_version(version)
    assert not ThemeVersion.objects.exists()
    assert not Theme.objects.filter(slug="example").exists()
    assert not default_storage.exists(prefix + "theme.css")
    assert AuditLog.objects.filter(action=services.AUDIT_DELETED).exists()


# --- komenda -------------------------------------------------------------------------------------


def test_theme_install_command_from_file(tmp_path):
    path = tmp_path / "example.zip"
    path.write_bytes(zip_with())
    out = io.StringIO()
    call_command("theme_install", str(path), stdout=out)
    assert "Wgrano motyw example 1.0.0" in out.getvalue()


def test_theme_install_command_from_stdin_and_activate(competition, monkeypatch):
    stdin = io.TextIOWrapper(io.BytesIO(zip_with()))
    monkeypatch.setattr(sys, "stdin", stdin)
    out = io.StringIO()
    call_command("theme_install", "-", "--activate", competition.slug, stdout=out)
    competition.refresh_from_db()
    assert competition.theme_version.theme.slug == "example"
    assert "Aktywowano" in out.getvalue()


def test_theme_install_command_fails_on_invalid(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(zip_with({"theme.css": "@import 'x';"}))
    with pytest.raises(CommandError):
        call_command("theme_install", str(path), stdout=io.StringIO(), stderr=io.StringIO())


def test_theme_install_command_unknown_competition(tmp_path):
    path = tmp_path / "example.zip"
    path.write_bytes(zip_with())
    with pytest.raises(CommandError):
        call_command("theme_install", str(path), "--activate", "nie-ma-takiego")


def test_iqo_package_installs_and_activates(competition, monkeypatch):
    """Prawdziwa paczka IQO: ``theme_install --activate`` kończy się sukcesem."""
    monkeypatch.setenv("APP_VERSION", "v0.41.0")
    out = io.StringIO()
    call_command("theme_install", str(IQO_ZIP), "--activate", competition.slug, stdout=out)
    competition.refresh_from_db()
    version = competition.theme_version
    assert version.theme.slug == "iqo-quantum" and version.color_scheme == "dark"
    assert set(version.templates) == {"theme/header.html", "theme/footer.html", "theme/home_hero.html"}
    with default_storage.open(version.public_prefix + "theme.css") as handle:
        assert 'url("assets/fonts/space-grotesk-var.woff2")' in handle.read().decode()
