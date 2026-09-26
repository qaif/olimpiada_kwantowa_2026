"""``verify_cutover`` (DJ-02 § 10.3) i ``dj_pages.W003`` (S16) – wynik zielony i każdy powód porażki."""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.importer import services
from apps.importer.checks import linked_paths_published
from apps.importer.tests import bundles

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def imported(superuser, main_api, chrome_payload):
    """Konkurs domyślny po imporcie paczki v2; API oddaje tę samą paczkę i dane stron żywych."""
    data = bundles.build_zip(bundles.v2_manifest())
    main_api.set("export", raw=data)
    main_api.set("chrome", chrome_payload())
    main_api.set("workshops", {"page": None, "schedules": [], "materials": {}})
    main_api.set("partners", {"levels": [], "partners": [], "page": None})
    main_api.set("stages", {"rows": [], "edition": None})
    main_api.set("problems", {})
    main_api.set("results", {"tables": [], "archive": []})
    report = services.run_import(services.open_bundle(io.BytesIO(data)), user=superuser)
    return report


def verify(*args) -> str:
    out = io.StringIO()
    call_command("verify_cutover", *args, stdout=out)
    return out.getvalue()


def _set_linked(paths):
    from apps.sites.models import CompetitionSite

    CompetitionSite.objects.filter(slug="kwantowa").update(linked_paths=paths)


def test_green_after_import(imported):
    _set_linked(["/dokumenty/regulamin/", "/warsztaty/"])
    out = verify()
    assert "kwantowa" in out and "17/17" in out and "3/3" in out and "OK" in out
    assert "verify_cutover: 1 witryn gotowych do przełączenia." in out


def test_missing_linked_path_fails_and_warns(imported):
    _set_linked(["/dokumenty/rodo/"])
    with pytest.raises(CommandError, match="nie przeszło kontroli"):
        verify()
    warnings = linked_paths_published(databases=["default"])
    assert [w.id for w in warnings] == ["dj_pages.W003"]
    assert "/dokumenty/rodo/" in warnings[0].msg
    _set_linked(["/dokumenty/regulamin"])  # bez ukośnika końcowego – ta sama strona
    assert linked_paths_published(databases=["default"]) == []
    assert linked_paths_published(databases=None) == []  # zwykły ``check`` bez bazy – nic


def test_page_count_mismatch_and_unpublished_page(imported, superuser):
    from cms.models import PageContent, PageUrl
    from djangocms_versioning.models import Version

    page = PageUrl.objects.get(path="kontakt", page__site_id=1).page
    content = PageContent.objects.get(page=page, language="pl")
    Version.objects.get_for_content(content).unpublish(superuser)
    out = io.StringIO()
    with pytest.raises(CommandError):
        call_command("verify_cutover", stdout=out)
    text = out.getvalue()
    assert "stron opublikowanych 16, w paczce 17" in text
    assert "/kontakt/ → 404" in text


def test_missing_redirect_fails(imported):
    from apps.seo.models import Redirect

    Redirect.objects.filter(old_path="/regulamin").delete()
    out = io.StringIO()
    with pytest.raises(CommandError):
        call_command("verify_cutover", stdout=out)
    assert "przekierowanie /regulamin → /dokumenty/regulamin/ nie jest zapisane" in out.getvalue()


def test_no_home_and_app_path_collision_fail(imported, make_page):
    from cms.models import Page

    Page.objects.filter(site_id=1, is_home=True).update(is_home=False)
    make_page("Zła", "login", site=Page.objects.filter(site_id=1).first().site)
    out = io.StringIO()
    with pytest.raises(CommandError):
        call_command("verify_cutover", stdout=out)
    text = out.getvalue()
    assert "brak strony głównej" in text
    assert "S5 – strona pod adresem aplikacji /login/" in text


def test_export_unavailable_fails(imported, main_api):
    main_api.responses.pop("c/kwantowa/export")
    out = io.StringIO()
    with pytest.raises(CommandError):
        call_command("verify_cutover", stdout=out)
    assert "paczka eksportu niedostępna" in out.getvalue()


def test_path_competition_is_checked_under_the_gateway_host(
    imported, make_competition, superuser, main_api, chrome_payload
):
    from apps.importer.management.commands.verify_cutover import request_target
    from apps.sites.models import CompetitionSite

    CompetitionSite.objects.filter(slug="kwantowa").update(hosts_path_prefixes=True)
    druga = make_competition(
        "druga",
        routing_mode="PATH",
        path_prefix="druga",
        public_origin="https://testserver",
        public_path_prefix="/druga",
    )
    assert request_target(druga) == ("testserver", "/druga")
    data = bundles.build_zip(bundles.v2_manifest("druga", "Druga"))
    main_api.set("export", raw=data, competition="druga")
    main_api.set("chrome", chrome_payload(), competition="druga")
    main_api.set("workshops", {"page": None, "schedules": [], "materials": {}}, competition="druga")
    main_api.set("partners", {"levels": [], "partners": [], "page": None}, competition="druga")
    main_api.set("stages", {"rows": [], "edition": None}, competition="druga")
    main_api.set("problems", {}, competition="druga")
    main_api.set("results", {"tables": [], "archive": []}, competition="druga")
    services.run_import(services.open_bundle(io.BytesIO(data)), user=superuser, competition=druga)
    out = verify("--competition", "druga")
    assert "druga" in out and "17/17" in out


def test_unknown_competition_option(imported):
    with pytest.raises(CommandError, match="nie ma wśród aktywnych"):
        verify("--competition", "nie-ma")
