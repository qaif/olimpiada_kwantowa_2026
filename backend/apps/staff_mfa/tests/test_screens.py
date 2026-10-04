"""Ekrany SEC-01: polityka w panelu, menu, ``no-store``, render w motywie IQO i tłumaczenia."""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.staff_mfa.models import PolicyMode, TwoFactorPolicy

from .conftest import enable_fees, enable_for, super_coordinator

pytestmark = pytest.mark.django_db

POLICY_URL = "/coordinator/security/2fa/"
BANNER_NOW = (
    "Twoja rola w tym serwisie wymaga logowania dwuskładnikowego. Włącz je teraz, żeby wrócić do panelu."
)


def _verified(client, user):
    codes = enable_for(user)
    client.force_login(user)
    client.post("/login/2fa/", {"code": codes[0]})


def test_a_coordinator_sees_the_policy_and_the_staff_but_cannot_save(client, competition):
    enable_fees(competition)
    coordinator = CoordinatorFactory(email="koordynator@example.test")
    CoordinatorFactory(email="bez2fa@example.test")
    _verified(client, coordinator)

    page = client.get(POLICY_URL)
    response = client.post(POLICY_URL, {"mode": PolicyMode.CUSTOM, "roles": [], "allow_remember": "on"})

    body = page.content.decode()
    assert page.status_code == 200
    assert "koordynator@example.test" in body and "bez2fa@example.test" in body
    assert "fees" in body
    assert "Zapisz politykę" not in body
    assert page["Cache-Control"].startswith("private")
    assert response.status_code == 403
    assert not TwoFactorPolicy.objects.exists()


def test_the_super_coordinator_saves_the_policy_with_audit(client, competition):
    actor = super_coordinator()
    _verified(client, actor)

    response = client.post(
        POLICY_URL, {"mode": PolicyMode.CUSTOM, "roles": ["reviewer", "appeals"], "grace_days": "7"}
    )

    assert response.status_code == 302
    row = TwoFactorPolicy.objects.get(competition=competition)
    assert row.mode == PolicyMode.CUSTOM and sorted(row.roles) == ["appeals", "reviewer"]
    assert row.grace_days == 7 and row.allow_remember is False
    entry = AuditLog.objects.get(action="2fa.policy_changed")
    assert entry.diff["after"]["roles"] == ["appeals", "reviewer"]


def test_the_policy_screen_rejects_participant_as_a_role(client, competition):
    actor = super_coordinator()
    _verified(client, actor)

    response = client.post(POLICY_URL, {"mode": PolicyMode.CUSTOM, "roles": ["participant"]})

    assert response.status_code == 400
    assert not TwoFactorPolicy.objects.exists()


def test_the_menu_item_exists_only_with_the_feature_on(client, competition, settings):
    coordinator = CoordinatorFactory()
    client.force_login(coordinator)
    assert "Bezpieczeństwo logowania" in client.get("/coordinator/").content.decode()

    settings.TWO_FACTOR_ENABLED = False
    assert "Bezpieczeństwo logowania" not in client.get("/coordinator/").content.decode()
    assert client.get(POLICY_URL).status_code == 404


@pytest.mark.parametrize("url", ["/account/2fa/", "/login/2fa/", "/account/2fa/codes/regenerate/"])
def test_two_factor_pages_are_never_cached(client, competition, url):
    user = ParticipantFactory(competition=competition).user
    _verified(client, user)

    response = client.get(url)

    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]


def test_the_page_cache_never_stores_two_factor_screens(competition):
    """Ekrany 2FA są poza allow-listą pełnostronicowego cache'a (apps/web/page_cache.py)."""
    from apps.web import page_cache

    for path in (
        "/account/2fa/",
        "/login/2fa/",
        "/account/2fa/codes/",
        "/account/2fa/codes/regenerate/",
        "/coordinator/security/2fa/",
    ):
        assert not page_cache.is_cacheable_path(path), path


def test_the_setup_and_verify_pages_render_in_the_iqo_theme(client_for, competition, monkeypatch):
    from apps.themes import services
    from apps.themes.rendering import forget_engines
    from apps.themes.runtime import forget_runtime
    from apps.themes.tests.helpers import IQO_ZIP

    forget_runtime()
    forget_engines()
    monkeypatch.setenv("APP_VERSION", "v0.41.0")
    version, result = services.install_package(IQO_ZIP.read_bytes())
    assert result.errors == []
    competition.interface_languages = ["en"]
    competition.default_language = "en"
    competition.save(update_fields=["interface_languages", "default_language"])
    services.activate(competition, version)
    competition.refresh_from_db()
    enable_fees(competition)
    client = client_for(competition)
    client.cookies["django_language"] = "en"
    coordinator = CoordinatorFactory()
    client.force_login(coordinator)

    panel = client.get("/coordinator/", HTTP_ACCEPT_LANGUAGE="en").content.decode()
    setup = client.get("/account/2fa/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert 'data-theme="iqo-quantum"' in panel and 'data-theme="iqo-quantum"' in setup
    # Baner okresu przejściowego po angielsku, w motywie (poza slotami – motyw go nie zasłania).
    assert "two-factor" in panel.lower() and "/account/2fa/" in panel
    assert "<svg" in setup
    forget_runtime()
    forget_engines()


@pytest.mark.parametrize("language", ["en", "zh-hans", "ar", "ru"])
def test_the_banner_and_mails_are_translated(language):
    from django.utils import translation

    with translation.override(language):
        banner = translation.gettext(BANNER_NOW)
        subject = translation.gettext("Wyłączono logowanie dwuskładnikowe")
    assert "dwuskładnik" not in banner
    assert "dwuskładnik" not in subject
