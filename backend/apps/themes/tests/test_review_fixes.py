"""Poprawki po przeglądzie (4.10.2026): kontekst slotów paczki, zakres stron, lint, pamięć wersji.

Każdy test odpowiada jednemu znalezisku przeglądu (H1, H2, M1, M2, L1, L3) i jest napisany jako
atak, który przed poprawką się udawał.
"""

from __future__ import annotations

import json

import pytest
from django.core.files.storage import default_storage
from wagtail.models import Page

from apps.accounts import super_coordinator
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.themes import runtime, services
from apps.themes.models import ThemeVersion
from apps.themes.package import validate_package
from apps.themes.rendering import forget_engines
from apps.themes.runtime import forget_runtime
from apps.themes.slots import check_rendered
from apps.themes.svg import sanitize_svg

from .helpers import IQO_ZIP, example_files, zip_with

pytestmark = pytest.mark.django_db

WRAPPER = "templates/theme/page_wrapper.html"
FOOTER = "templates/theme/footer.html"


@pytest.fixture(autouse=True)
def _fresh_caches():
    forget_runtime()
    forget_engines()
    yield
    forget_runtime()
    forget_engines()


def _activate(competition, changes: dict) -> ThemeVersion:
    version, result = services.install_package(zip_with(changes))
    assert result.errors == [], result.errors
    services.activate(competition, version)
    competition.refresh_from_db()
    return version


def _bypass_lint(version: ThemeVersion, name: str, source: str) -> None:
    """Szablon zapisany wprost w bazie – ominięcie walidacji wgrania (sprawdza drugą linię)."""
    ThemeVersion.objects.filter(pk=version.pk).update(templates={**version.templates, name: source})
    forget_runtime()
    forget_engines()


def _manifest(**changes) -> str:
    return json.dumps({**json.loads(example_files()["manifest.json"]), **changes})


# --- H1: kontekst z listy dozwolonej ------------------------------------------------------------


def test_package_slot_cannot_call_page_methods(client_for, competition):
    """``{{ page.unpublish }}`` w slocie paczki nie wycofuje strony głównej."""
    _activate(
        competition,
        {
            WRAPPER: (
                '<div data-theme-slot="page_wrapper">[{{ page.unpublish }}][{{ page.title }}]'
                "{{ slot_content }}</div>"
            )
        },
    )
    home = Page.objects.get(pk=competition.site.root_page_id)
    assert home.live
    html = client_for(competition).get("/").content.decode()
    home.refresh_from_db()
    assert home.live
    assert f"[][{home.title}]" in html


def test_package_slot_cannot_call_user_methods_or_reach_models(client_for, competition):
    participant = ParticipantFactory(competition=competition)
    other = ParticipantFactory(competition=competition)
    _activate(
        competition,
        {
            WRAPPER: (
                '<div data-theme-slot="page_wrapper">[{{ user.set_unusable_password }}]'
                "[{% for p in competition.participants.all %}{{ p.user.email }}{% endfor %}]"
                "[{{ request.user.groups.all }}][{{ competition.site.root_page }}]{{ slot_content }}</div>"
            )
        },
    )
    client = client_for(competition)
    client.force_login(participant.user)
    html = client.get("/").content.decode()
    participant.user.refresh_from_db()
    assert participant.user.has_usable_password()
    assert other.user.email not in html
    assert "[][][][]" in html


def test_app_partials_keep_full_context(client_for, competition):
    """Fragment aplikacji dołączony przez slot paczki działa jak w szablonie aplikacji."""
    _activate(competition, {})
    html = client_for(competition).get("/").content.decode()
    # ``web/_support_link.html`` dołączony przez stopkę paczki ``example`` liczy adres sam.
    assert 'data-theme-slot="footer"' in html and "footer__link" in html


# --- H2: sloty paczki tylko na stronach publicznych ---------------------------------------------


@pytest.mark.parametrize("path", ["/login/", "/coordinator/", "/me/"])
def test_panels_and_forms_use_app_slots(client_for, competition, path):
    _activate(competition, {})
    client = client_for(competition)
    if path == "/coordinator/":
        client.force_login(CoordinatorFactory())
    elif path == "/me/":
        client.force_login(ParticipantFactory(competition=competition).user)
    html = client.get(path).content.decode()
    assert "data-theme-slot" not in html
    assert '<footer class="footer">' in html
    assert "tokens.css" in html  # tokeny i arkusz motywu – tak (manifest: supports panels)


def test_supports_public_only_leaves_panels_unthemed(client_for, competition):
    _activate(competition, {"manifest.json": _manifest(supports=["public"])})
    client = client_for(competition)
    assert "tokens.css" in client.get("/").content.decode()
    client.force_login(CoordinatorFactory())
    assert "tokens.css" not in client.get("/coordinator/").content.decode()


def test_theme_screens_never_use_the_theme(client_for, competition):
    _activate(competition, {})
    competition.feature_flags = {**(competition.feature_flags or {}), "themes": True}
    competition.save(update_fields=["feature_flags"])
    client = client_for(competition)
    client.force_login(CoordinatorFactory())
    html = client.get("/coordinator/competition/theme/").content.decode()
    assert "tokens.css" not in html and "data-theme=" not in html


def test_emergency_off_for_super_coordinator_only(client_for, competition):
    _activate(competition, {})
    coordinator = CoordinatorFactory()
    client = client_for(competition)
    client.force_login(coordinator)
    assert 'data-theme="example"' in client.get("/?theme=off").content.decode()
    super_coordinator.grant(coordinator)
    client.force_login(coordinator)
    html = client.get("/?theme=off").content.decode()
    assert "data-theme=" not in html and "tokens.css" not in html


# --- M1: obejścia lintu i kontrola wyniku -------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        '<footer>{{ "<script>alert(1)</script>" }}</footer>',
        "<footer>{% firstof \"<iframe src='https://evil.example'>\" %}</footer>",
        "<footer><scr{# #}ipt>alert(1)</script></footer>",
        '<footer o{##}nclick="alert(1)"></footer>',
        '<footer>{{ "<meta http-equiv=refresh content=0;url=https://evil.example>" }}</footer>',
        "<footer>{{ x|dict_get:'password' }}</footer>",
        "<footer><a href='{{ \"javascript:alert(1)\" }}'>x</a></footer>",
    ],
)
def test_lint_bypasses_rejected(source):
    assert validate_package(zip_with({FOOTER: source})).errors


@pytest.mark.parametrize(
    "html",
    [
        "<p>x</p><script>alert(1)</script>",
        '<p onmouseover="x">',
        '<a href="javascript:alert(1)">',
        '<meta http-equiv="refresh" content="0">',
        '<form method="post" action="https://evil.example/">',
        '<iframe src="/x">',
    ],
)
def test_rendered_output_check(html):
    assert check_rendered(html)


def test_rendered_output_check_allows_own_forms():
    assert check_rendered('<form method="post" action="/logout/"><button>x</button></form>') is None


def test_rendered_check_drops_offending_slot(client_for, competition):
    """Slot, którego wynik zawiera atrybut zdarzenia, wraca do stopki aplikacji."""
    version = _activate(competition, {})
    _bypass_lint(
        version, "theme/footer.html", '<footer data-theme-slot="footer"><b onclick="x"></b></footer>'
    )
    html = client_for(competition).get("/").content.decode()
    assert 'onclick="x"' not in html
    assert '<footer class="footer">' in html


# --- M2: pamięć wersji i arkusz akcentu ---------------------------------------------------------


def test_missing_version_is_not_cached():
    assert runtime.runtime_for(987654) is None
    assert 987654 not in runtime._RUNTIMES


def test_overrides_only_for_active_version_for_guests(client_for, competition):
    version = _activate(competition, {})
    competition.accent_colour = "#0055aa"
    competition.save(update_fields=["accent_colour"])
    other, _ = services.install_package(zip_with({"manifest.json": _manifest(version="2.0.0")}))
    client = client_for(competition)
    assert client.get(f"/_theme/overrides.css?v={version.pk}-0055aa").status_code == 200
    assert client.get(f"/_theme/overrides.css?v={other.pk}-0055aa").status_code == 404
    assert client.get("/_theme/overrides.css?v=999999-0055aa").status_code == 404
    assert 999999 not in runtime._RUNTIMES
    client.force_login(CoordinatorFactory())
    assert client.get(f"/_theme/overrides.css?v={other.pk}-0055aa").status_code == 200


# --- L1: SVG – lista dozwolona -----------------------------------------------------------------


def test_svg_style_with_child_element_is_dropped():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>a{}<x/>@import url(https://evil.example/x.css);'
        b'</style><rect width="1" height="1"/></svg>'
    )
    result = sanitize_svg(svg)
    assert result.errors == []
    assert b"evil.example" not in result.data and b"<style" not in result.data


def test_svg_foreign_namespace_elements_removed():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:h="http://www.w3.org/1999/xhtml">'
        b'<h:meta http-equiv="refresh" content="0"/><h:form action="https://evil.example"><h:input/></h:form>'
        b'<circle r="1" data-x="1" onload="alert(1)"/></svg>'
    )
    data = sanitize_svg(svg).data
    for forbidden in (b"meta", b"form", b"input", b"onload", b"data-x"):
        assert forbidden not in data
    assert b"<circle" in data


def test_svg_utf16_rejected():
    svg = '<!DOCTYPE svg [<!ENTITY a "x">]><svg xmlns="http://www.w3.org/2000/svg"/>'.encode("utf-16")
    assert sanitize_svg(svg).errors


# --- L3: storage poza transakcją ----------------------------------------------------------------


def test_failed_save_cleans_published_files(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("baza padła")

    seen: list[str] = []
    original = services._publish

    def record(prefix, path, data):
        seen.append(prefix + path)
        original(prefix, path, data)

    monkeypatch.setattr(services, "_save_version", boom)
    monkeypatch.setattr(services, "_publish", record)
    with pytest.raises(RuntimeError):
        services.install_package(zip_with())
    assert seen and not any(default_storage.exists(name) for name in seen)
    assert not ThemeVersion.objects.exists()


def test_delete_removes_files_after_commit(django_capture_on_commit_callbacks):
    version, _ = services.install_package(zip_with())
    css = version.public_prefix + "theme.css"
    with django_capture_on_commit_callbacks(execute=True):
        services.delete_version(version)
    assert not default_storage.exists(css)


# --- paczka IQO po zmianach --------------------------------------------------------------------


def test_iqo_renders_with_curated_context(client_for, competition, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v0.41.0")
    version, result = services.install_package(IQO_ZIP.read_bytes())
    assert result.errors == []
    assert not [w for w in result.warnings if "usunięto" in w]
    services.activate(competition, version)
    competition.refresh_from_db()
    client = client_for(competition)
    html = client.get("/").content.decode()
    assert 'class="iqo-header"' in html and "iqo-footer" in html and "iqo-hero" in html
    assert "topbar--account" not in html
    # Pasek konta motywu: fragmenty aplikacji z pełnym kontekstem i formularz „Wyloguj” z tokenem.
    user = ParticipantFactory(competition=competition).user
    client.force_login(user)
    html = client.get("/").content.decode()
    assert 'class="iqo-header"' in html
    assert 'name="csrfmiddlewaretoken"' in html and user.email in html
