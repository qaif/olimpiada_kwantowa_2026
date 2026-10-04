"""Ekrany THEME-02: menu serwisu i dostosowanie motywu – uprawnienia, flaga, audyt, limit, cache."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import enforce_memberships, grant_membership
from apps.themes import services
from apps.themes.models import SiteMenu, ThemeCustomization
from apps.themes.runtime import PREVIEW_PARAM

from .helpers import zip_with

pytestmark = pytest.mark.django_db

MENU_URL = "/coordinator/competition/theme/menu/"
CUSTOM_URL = "/coordinator/competition/theme/customize/"


def _flag(competition, value=True):
    competition.feature_flags = {**(competition.feature_flags or {}), "themes": value}
    competition.save(update_fields=["feature_flags"])
    return competition


def _login(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def coordinator_client(client_for, competition):
    _flag(competition)
    return _login(client_for, competition, CoordinatorFactory())


@pytest.fixture
def example(competition):
    version, result = services.install_package(zip_with())
    assert result.errors == []
    return version


def test_urls_are_stable():
    assert reverse("web:coordinator-theme-menu") == MENU_URL
    assert reverse("web:coordinator-theme-customize") == CUSTOM_URL
    assert reverse("web:theme-custom") == "/_theme/custom.css"


@pytest.mark.parametrize("url", [MENU_URL, CUSTOM_URL])
def test_404_without_flag(client_for, competition, url):
    assert _login(client_for, competition, CoordinatorFactory()).get(url).status_code == 404


@pytest.mark.parametrize("url", [MENU_URL, CUSTOM_URL])
@pytest.mark.parametrize(
    "make_user", [lambda c: ActiveReviewerFactory().user, lambda c: ParticipantFactory(competition=c).user]
)
def test_other_roles_are_forbidden(client_for, competition, url, make_user):
    _flag(competition)
    client = _login(client_for, competition, make_user(competition))
    assert client.get(url).status_code == 403
    assert client.post(url, {"action": "reset"}).status_code == 403


@pytest.mark.parametrize("url", [MENU_URL, CUSTOM_URL])
def test_anonymous_redirects_to_login(client_for, competition, url):
    _flag(competition)
    response = client_for(competition).get(url)
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_coordinator_of_other_competition_cannot_change_menu(client_for, competition, other_competition):
    _flag(enforce_memberships(competition))
    enforce_memberships(other_competition)
    stranger = CoordinatorFactory()
    grant_membership(stranger, other_competition, CompetitionRole.COORDINATOR)
    client = _login(client_for, competition, stranger)
    response = client.post(
        MENU_URL, {"action": "add_link", "new_url": "https://evil.example/", "new_label_pl": "Zły"}
    )
    assert response.status_code == 403
    assert not SiteMenu.objects.exists()


def test_screens_render_without_theme(coordinator_client, competition, example):
    services.activate(competition, example)
    for url in (MENU_URL, CUSTOM_URL):
        response = coordinator_client.get(url)
        assert response.status_code == 200
        html = response.content.decode()
        assert "data-theme=" not in html and "tokens.css" not in html


# --- menu -----------------------------------------------------------------------------------------


def test_menu_editor_lists_default_items(coordinator_client):
    html = coordinator_client.get(MENU_URL).content.decode()
    assert 'name="order_home"' in html and "Zadania" in html and "Wyniki" in html


def test_add_link_hide_and_reorder_via_form(coordinator_client, client_for, competition):
    response = coordinator_client.post(
        MENU_URL,
        {
            "action": "add_link",
            "new_url": "https://iqo-official.org/",
            "new_label_pl": "Strona IQO",
            "new_tab": "on",
        },
    )
    assert response.status_code == 302
    row = SiteMenu.objects.get(competition=competition)
    link = next(item for item in row.items if item["type"] == "link")
    assert link["new_tab"] is True and link["url"] == "https://iqo-official.org/"
    assert AuditLog.objects.filter(action=services.AUDIT_MENU_SAVED, competition=competition).exists()

    # Formularz tabeli: link na samą górę, „home” ukryty.
    editor = coordinator_client.get(MENU_URL)
    rows = editor.context["rows"]
    data = {"action": "save", "row_keys": [entry["key"] for entry in rows]}
    for position, entry in enumerate(rows, start=1):
        data[f"order_{entry['key']}"] = position + 1
        if entry["key"] != "home":
            data[f"visible_{entry['key']}"] = "on"
    data[f"order_{link['key']}"] = 1
    data[f"url_{link['key']}"] = "https://iqo-official.org/"
    data[f"label_{link['key']}_pl"] = "Strona IQO"
    assert coordinator_client.post(MENU_URL, data).status_code == 302
    competition.refresh_from_db()
    menu = client_for(competition).get("/").context["cms_menu"]
    assert menu[0]["key"] == link["key"] and "home" not in [item["key"] for item in menu]
    assert menu[0]["new_tab"] is False  # pole niezaznaczone przy zapisie tabeli


def test_move_buttons(coordinator_client, client_for, competition):
    rows = coordinator_client.get(MENU_URL).context["rows"]
    data = {"action": "save", "move": f"{rows[1]['key']}:up", "row_keys": [entry["key"] for entry in rows]}
    for position, entry in enumerate(rows, start=1):
        data[f"order_{entry['key']}"] = position
        data[f"visible_{entry['key']}"] = "on"
    coordinator_client.post(MENU_URL, data)
    competition.refresh_from_db()
    menu = client_for(competition).get("/").context["cms_menu"]
    assert [item["key"] for item in menu][:2] == [rows[1]["key"], rows[0]["key"]]


def test_invalid_link_is_rejected_with_400(coordinator_client, competition):
    response = coordinator_client.post(
        MENU_URL, {"action": "add_link", "new_url": "javascript:alert(1)", "new_label_pl": "x"}
    )
    assert response.status_code == 400
    assert not SiteMenu.objects.exists()


def test_reset_menu_via_form(coordinator_client, competition):
    coordinator_client.post(MENU_URL, {"action": "add_group", "group_label_pl": "Więcej"})
    assert SiteMenu.objects.exists()
    coordinator_client.post(MENU_URL, {"action": "reset"})
    assert not SiteMenu.objects.exists()
    competition.refresh_from_db()
    assert "menu" not in (competition.theme_options or {})


def test_menu_save_invalidates_page_cache(coordinator_client, competition, monkeypatch):
    calls = []
    monkeypatch.setattr("apps.web.page_cache.invalidate_competition", lambda pk: calls.append(pk))
    coordinator_client.post(MENU_URL, {"action": "add_group", "group_label_pl": "Więcej"})
    assert calls == [competition.pk]


def test_posts_are_throttled(coordinator_client, settings):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "theme_settings": "2/hour",
        },
    }
    statuses = [coordinator_client.post(MENU_URL, {"action": "nonsense"}).status_code for _ in range(3)]
    assert statuses[-1] == 429


# --- dostosowanie ---------------------------------------------------------------------------------


def test_customize_without_package_theme_explains(coordinator_client):
    html = coordinator_client.get(CUSTOM_URL).content.decode()
    assert "Klasyczny" in html and 'type="color"' not in html


def test_customize_lists_palettes_of_scheme(coordinator_client, competition, example):
    services.activate(competition, example)
    html = coordinator_client.get(CUSTOM_URL).content.decode()
    assert 'name="color_light_accent"' in html and 'name="color_dark_accent"' in html
    assert 'name="scheme"' in html


def test_customize_save_and_reset(coordinator_client, competition, example):
    services.activate(competition, example)
    response = coordinator_client.post(
        CUSTOM_URL, {"version": example.pk, "action": "save", "color_light_accent": "#7a0d3c"}
    )
    assert response.status_code == 302
    competition.refresh_from_db()
    assert competition.theme_options["colors"] == {"light": {"accent": "#7a0d3c"}}
    coordinator_client.post(CUSTOM_URL, {"version": example.pk, "action": "reset"})
    competition.refresh_from_db()
    assert "colors" not in competition.theme_options


def test_customize_bad_contrast_is_400_and_saves_nothing(coordinator_client, competition, example):
    services.activate(competition, example)
    response = coordinator_client.post(
        CUSTOM_URL, {"version": example.pk, "action": "save", "color_light_text": "#ffffff"}
    )
    assert response.status_code == 400
    assert "WCAG" in response.content.decode()
    assert not ThemeCustomization.objects.exists()


def test_customize_preview_shows_colours_only_to_coordinator(
    coordinator_client, client_for, competition, example
):
    services.activate(competition, example)
    response = coordinator_client.post(
        CUSTOM_URL, {"version": example.pk, "action": "preview", "color_light_accent": "#7a0d3c"}
    )
    assert response.status_code == 302 and response["Location"].startswith(f"/?{PREVIEW_PARAM}=")
    assert "/_theme/custom.css?s=" in coordinator_client.get(response["Location"]).content.decode()
    assert "/_theme/custom.css" not in client_for(competition).get(response["Location"]).content.decode()
    assert not ThemeCustomization.objects.exists()


def test_customize_inactive_version_is_stored_for_activation(coordinator_client, competition, example):
    response = coordinator_client.post(
        CUSTOM_URL, {"version": example.pk, "action": "save", "color_light_accent": "#7a0d3c"}
    )
    assert response.status_code == 302
    competition.refresh_from_db()
    assert competition.theme_version_id is None and competition.theme_options in ({}, None)
    assert ThemeCustomization.objects.get().options["colors"] == {"light": {"accent": "#7a0d3c"}}


@pytest.mark.parametrize("version", ["999999", "abc"])
def test_customize_unknown_version_404(coordinator_client, version):
    assert coordinator_client.get(f"{CUSTOM_URL}?version={version}").status_code == 404
