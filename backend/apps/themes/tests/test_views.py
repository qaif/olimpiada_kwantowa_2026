"""Panele motywów: uprawnienia, flaga, aktywacja, podgląd, katalog superkoordynatora."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts import super_coordinator
from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import enforce_memberships, grant_membership
from apps.themes import services
from apps.themes.models import ThemeVersion
from apps.themes.rendering import forget_engines
from apps.themes.runtime import PREVIEW_PARAM, forget_runtime

from .helpers import zip_with

pytestmark = pytest.mark.django_db

THEME_URL = "/coordinator/competition/theme/"
PLATFORM_URL = "/coordinator/platform/themes/"


@pytest.fixture(autouse=True)
def _fresh_caches():
    forget_runtime()
    forget_engines()
    yield
    forget_runtime()
    forget_engines()


def _flag(competition, value=True):
    competition.feature_flags = {**(competition.feature_flags or {}), "themes": value}
    competition.save(update_fields=["feature_flags"])
    return competition


def _login(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def example(db):
    version, _ = services.install_package(zip_with())
    return version


def test_urls_are_stable():
    assert reverse("web:coordinator-theme") == THEME_URL
    assert reverse("web:coordinator-platform-themes") == PLATFORM_URL


# --- ekran konkursu ------------------------------------------------------------------------------


def test_theme_page_is_404_without_flag(client_for, competition):
    assert _login(client_for, competition, CoordinatorFactory()).get(THEME_URL).status_code == 404


def test_theme_page_menu_item_only_with_flag(client_for, competition):
    client = _login(client_for, competition, CoordinatorFactory())
    assert THEME_URL not in client.get("/coordinator/").content.decode()
    _flag(competition)
    assert THEME_URL in client.get("/coordinator/").content.decode()


@pytest.mark.parametrize(
    ("make_user", "status"),
    [
        (lambda c: ActiveReviewerFactory().user, 403),
        (lambda c: ParticipantFactory(competition=c).user, 403),
    ],
)
def test_theme_page_roles(client_for, competition, make_user, status):
    _flag(competition)
    assert _login(client_for, competition, make_user(competition)).get(THEME_URL).status_code == status


def test_theme_page_anonymous_redirects_to_login(client_for, competition):
    _flag(competition)
    response = client_for(competition).get(THEME_URL)
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_coordinator_of_other_competition_cannot_open_or_post(
    client_for, competition, other_competition, example
):
    """Koordynator konkursu B pod adresem konkursu A: 403 (rola sprawdzana przed czymkolwiek).

    Adres nie ma identyfikatora konkursu – konkurs wskazuje domena – więc nie istnieje adres,
    pod którym dałoby się zmienić motyw cudzego konkursu; POST pod cudzą domenę kończy się tak
    samo jak GET i niczego nie zapisuje.
    """
    _flag(enforce_memberships(competition))
    enforce_memberships(other_competition)
    stranger = CoordinatorFactory()
    grant_membership(stranger, other_competition, CompetitionRole.COORDINATOR)
    client = _login(client_for, competition, stranger)
    assert client.get(THEME_URL).status_code == 403
    response = client.post(THEME_URL, {"version": example.pk, "action": "activate"})
    assert response.status_code == 403
    competition.refresh_from_db()
    assert competition.theme_version_id is None


def test_gallery_lists_valid_versions_only(client_for, competition, example):
    services.install_package(
        zip_with({"manifest.json": zip_manifest(version="2.0.0"), "theme.css": "@import 'x';"})
    )
    _flag(competition)
    html = _login(client_for, competition, CoordinatorFactory()).get(THEME_URL).content.decode()
    assert "Przykład" in html and "1.0.0" in html and "2.0.0" not in html
    assert "Klasyczny" in html


def zip_manifest(**changes) -> str:
    import json

    from .helpers import example_files

    manifest = json.loads(example_files()["manifest.json"])
    manifest.update(changes)
    return json.dumps(manifest)


def test_activate_via_panel(client_for, competition, example):
    _flag(competition)
    client = _login(client_for, competition, CoordinatorFactory())
    response = client.post(
        THEME_URL,
        {"version": example.pk, "action": "activate", "layout_header": "split", "brand_accent": "on"},
    )
    assert response.status_code == 302
    competition.refresh_from_db()
    assert competition.theme_version_id == example.pk
    assert competition.theme_options["layouts"]["header"] == "split"
    entry = AuditLog.objects.get(action=services.AUDIT_ACTIVATED)
    assert entry.competition_id == competition.pk and entry.diff["theme"] == "example"
    # Powrót do classic.
    client.post(THEME_URL, {"version": "classic", "action": "activate"})
    competition.refresh_from_db()
    assert competition.theme_version_id is None


def test_preview_redirects_with_token_and_saves_nothing(client_for, competition, example):
    _flag(competition)
    client = _login(client_for, competition, CoordinatorFactory())
    response = client.post(THEME_URL, {"version": example.pk, "action": "preview"})
    assert response.status_code == 302
    assert response["Location"].startswith(f"/?{PREVIEW_PARAM}=")
    html = client.get(response["Location"]).content.decode()
    assert 'data-theme="example"' in html
    competition.refresh_from_db()
    assert competition.theme_version_id is None
    assert not AuditLog.objects.filter(action=services.AUDIT_ACTIVATED).exists()


@pytest.mark.parametrize("version", ["999999", "abc", ""])
def test_unknown_version_is_404(client_for, competition, version):
    _flag(competition)
    client = _login(client_for, competition, CoordinatorFactory())
    assert client.post(THEME_URL, {"version": version, "action": "activate"}).status_code == 404


def test_invalid_version_cannot_be_activated_via_panel(client_for, competition):
    invalid, _ = services.install_package(zip_with({"theme.css": "@import 'x';"}))
    assert invalid.status == ThemeVersion.Status.INVALID
    _flag(competition)
    client = _login(client_for, competition, CoordinatorFactory())
    assert client.post(THEME_URL, {"version": invalid.pk, "action": "activate"}).status_code == 404


# --- katalog platformy ---------------------------------------------------------------------------


@pytest.fixture
def operator(db):
    user = CoordinatorFactory()
    super_coordinator.grant(user)
    return user


def test_platform_catalog_requires_super_coordinator(client_for, competition):
    assert _login(client_for, competition, CoordinatorFactory()).get(PLATFORM_URL).status_code == 403
    assert _login(client_for, competition, ActiveReviewerFactory().user).get(PLATFORM_URL).status_code == 403


def test_coordinator_cannot_upload(client_for, competition):
    client = _login(client_for, competition, CoordinatorFactory())
    upload = SimpleUploadedFile("example.zip", zip_with(), content_type="application/zip")
    assert client.post(PLATFORM_URL, {"package": upload}).status_code == 403
    assert not ThemeVersion.objects.exists()


def test_operator_uploads_valid_package(client_for, competition, operator):
    client = _login(client_for, competition, operator)
    assert client.get(PLATFORM_URL).status_code == 200
    upload = SimpleUploadedFile("example.zip", zip_with(), content_type="application/zip")
    response = client.post(PLATFORM_URL, {"package": upload})
    version = ThemeVersion.objects.get()
    assert response.status_code == 302 and response["Location"] == f"{PLATFORM_URL}{version.pk}/"
    assert version.is_valid and version.uploaded_by == operator
    detail = client.get(response["Location"]).content.decode()
    assert "Bez błędów" in detail and "theme/footer.html" in detail
    assert AuditLog.objects.filter(action=services.AUDIT_UPLOADED, actor=operator).exists()


def test_operator_sees_report_of_rejected_package(client_for, competition, operator):
    client = _login(client_for, competition, operator)
    upload = SimpleUploadedFile(
        "evil.zip", zip_with({"theme.css": "@import url(https://evil.example/x.css);"})
    )
    response = client.post(PLATFORM_URL, {"package": upload})
    version = ThemeVersion.objects.get()
    assert version.status == ThemeVersion.Status.INVALID
    assert "@import" in client.get(response["Location"]).content.decode()


def test_upload_without_manifest_shows_errors(client_for, competition, operator):
    client = _login(client_for, competition, operator)
    upload = SimpleUploadedFile("bad.zip", zip_with({"manifest.json": None}))
    response = client.post(PLATFORM_URL, {"package": upload})
    assert response.status_code == 400 and "manifest.json" in response.content.decode()


def test_operator_deletes_unused_version_only(client_for, competition, operator, example):
    client = _login(client_for, competition, operator)
    services.activate(competition, example)
    url = f"{PLATFORM_URL}{example.pk}/"
    client.post(url, {"action": "delete"})
    assert ThemeVersion.objects.filter(pk=example.pk).exists()
    services.activate(competition, None)
    client.post(url, {"action": "delete"})
    assert not ThemeVersion.objects.filter(pk=example.pk).exists()
