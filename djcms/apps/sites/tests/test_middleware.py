"""``CompetitionSiteMiddleware`` (DJ-02 § 5.3): host/prefiks → witryna, pusta 404, prefiks skryptu."""

import urllib.error

import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import get_script_prefix, set_script_prefix

from apps.sites import registry
from apps.sites.middleware import CompetitionSiteMiddleware, PrefixedCurrentPageMiddleware
from apps.sites.models import CompetitionSite

pytestmark = pytest.mark.django_db


@pytest.fixture
def druga(make_competition):
    """Konkurs pod prefiksem ``/druga/`` gospodarza ``kwantowa`` (bramka otwarta)."""
    CompetitionSite.objects.filter(slug="kwantowa").update(hosts_path_prefixes=True)
    return make_competition(
        "druga",
        routing_mode="PATH",
        path_prefix="druga",
        hosts=["druga.olimpiada.example"],
        public_origin="https://testserver",
        public_path_prefix="/druga",
    )


def _run(request, view=None):
    seen = {}

    def get_response(req):
        seen.update(
            site=getattr(req, "site", None),
            competition=getattr(req, "competition_site", None),
            prefix=getattr(req, "competition_path_prefix", None),
            path_info=req.path_info,
            path=req.path,
            script_prefix=get_script_prefix(),
        )
        return view(req) if view else HttpResponse("ok")

    response = CompetitionSiteMiddleware(get_response)(request)
    return response, seen


def test_host_sets_site_and_competition():
    response, seen = _run(RequestFactory().get("/zadania/", HTTP_HOST="testserver"))
    assert response.status_code == 200
    assert seen["competition"].slug == "kwantowa"
    assert seen["site"].pk == 1
    assert (seen["prefix"], seen["path_info"], seen["script_prefix"]) == ("", "/zadania/", "/")


def test_prefix_is_stripped_and_script_prefix_restored(druga):
    response, seen = _run(RequestFactory().get("/druga/zadania/", HTTP_HOST="testserver"))
    assert response.status_code == 200
    assert seen["competition"].slug == "druga"
    assert seen["site"] == druga.site
    assert (seen["prefix"], seen["path_info"], seen["path"]) == ("druga", "/zadania/", "/druga/zadania/")
    assert seen["script_prefix"] == "/druga/"
    assert get_script_prefix() == "/"


def test_script_prefix_restored_after_exception(druga):
    def boom(request):
        raise RuntimeError("widok padł")

    set_script_prefix("/")
    with pytest.raises(RuntimeError):
        _run(RequestFactory().get("/druga/", HTTP_HOST="testserver"), view=boom)
    assert get_script_prefix() == "/"


def test_prefix_without_trailing_slash(druga):
    _, seen = _run(RequestFactory().get("/druga", HTTP_HOST="testserver"))
    assert (seen["competition"].slug, seen["path_info"], seen["script_prefix"]) == ("druga", "/", "/druga/")


def test_path_competition_under_its_own_host_has_no_prefix(druga):
    _, seen = _run(RequestFactory().get("/zadania/", HTTP_HOST="druga.olimpiada.example"))
    assert (seen["competition"].slug, seen["prefix"], seen["script_prefix"]) == ("druga", "", "/")


def test_unknown_host_is_an_empty_404_without_touching_the_export(main_api):
    response, seen = _run(RequestFactory().get("/", HTTP_HOST="nieznany.olimpiada.example"))
    assert response.status_code == 404
    assert response.content == b""
    assert seen == {}
    # Lista konkursów z bufora klienta może zostać przeczytana (D7 p. 2) – paczki nikt nie pobiera,
    # a rejestr się nie zmienia.
    assert main_api.calls("export") == 0
    assert list(CompetitionSite.objects.values_list("slug", flat=True)) == ["kwantowa"]


def test_inactive_competition_host_is_an_empty_404(main_api):
    from conftest import default_competitions_payload

    data = default_competitions_payload()
    data["competitions"][0]["is_active"] = False
    main_api.set("competitions", data, competition=None)
    CompetitionSite.objects.filter(slug="kwantowa").update(is_active=False)
    response, _ = _run(RequestFactory().get("/", HTTP_HOST="testserver"))
    assert (response.status_code, response.content) == (404, b"")


def test_api_reactivates_a_competition_the_registry_had_switched_off(main_api):
    # Lista z API jest autorytatywna: host aktywnego konkursu, wygaszonego w rejestrze, wraca.
    CompetitionSite.objects.filter(slug="kwantowa").update(is_active=False)
    response, seen = _run(RequestFactory().get("/", HTTP_HOST="testserver"))
    assert response.status_code == 200 and seen["competition"].slug == "kwantowa"


def test_unknown_host_listed_by_api_is_synced_and_served(main_api):
    from conftest import default_competitions_payload

    data = default_competitions_payload()
    data["competitions"].append(
        {
            **data["competitions"][0],
            "slug": "fizyka",
            "name": "Fizyka",
            "is_default": False,
            "hosts": ["fizyka.olimpiada.example"],
            "site_hostname": "fizyka.olimpiada.example",
            "fingerprint": "b" * 64,
        }
    )
    main_api.set("competitions", data, competition=None)
    response, seen = _run(RequestFactory().get("/", HTTP_HOST="fizyka.olimpiada.example"))
    assert response.status_code == 200
    assert seen["competition"].slug == "fizyka"


def test_lazy_refresh_runs_once_per_window(main_api, settings):
    settings.DJCMS_SITES_REFRESH_SECONDS = 60
    _run(RequestFactory().get("/", HTTP_HOST="testserver"))
    _run(RequestFactory().get("/", HTTP_HOST="testserver"))
    assert main_api.calls("competitions", competition=None) == 1
    assert cache.get(registry.FRESH_KEY)


def test_lazy_refresh_with_dead_api_still_serves_from_the_registry(main_api, settings):
    settings.DJCMS_SITES_REFRESH_SECONDS = 60
    main_api.fail("competitions", urllib.error.URLError(ConnectionRefusedError()), competition=None)
    response, seen = _run(RequestFactory().get("/", HTTP_HOST="testserver"))
    assert response.status_code == 200
    assert seen["competition"].slug == "kwantowa"


def test_disallowed_host_is_left_to_django():
    response, seen = _run(RequestFactory().get("/", HTTP_HOST="evil.example"))
    assert response.status_code == 404 and seen == {}


# --- healthcheck i statyki: bez 404 i bez API ---------------------------------------------------------


@pytest.mark.parametrize("host", ["testserver", "nieznany.olimpiada.example", "127.0.0.1"])
def test_healthz_answers_for_any_host_without_api(client, main_api, settings, host):
    settings.DJCMS_SITES_REFRESH_SECONDS = 60
    response = client.get("/djcms/healthz/", HTTP_HOST=host)
    assert response.status_code == 200
    assert main_api.requests == []


def test_healthz_answers_with_an_empty_registry(client, main_api):
    CompetitionSite.objects.all().delete()
    assert client.get("/djcms/healthz/", HTTP_HOST="127.0.0.1").status_code == 200


def test_static_files_skip_resolution(django_assert_num_queries):
    with django_assert_num_queries(0):
        _, seen = _run(
            RequestFactory().get("/djcms/static/css/app.css", HTTP_HOST="nieznany.olimpiada.example")
        )
    assert seen["competition"] is None


# --- strona bieżąca pod prefiksem ------------------------------------------------------------------


def test_prefixed_current_page(make_page, druga):
    page = make_page("Zadania drugiej", "zadania", site=druga.site)
    request = RequestFactory().get("/druga/zadania/", HTTP_HOST="testserver")
    request.user = None

    def chain(req):
        return PrefixedCurrentPageMiddleware(lambda r: HttpResponse(str(r.current_page.pk)))(req)

    set_script_prefix("/")
    response = CompetitionSiteMiddleware(chain)(request)
    assert response.content.decode() == str(page.pk)


def test_current_page_middleware_untouched_without_prefix():
    request = RequestFactory().get("/")
    sentinel = object()
    request.current_page = sentinel
    PrefixedCurrentPageMiddleware(lambda r: HttpResponse())(request)
    assert request.current_page is sentinel


@pytest.mark.parametrize("host", ["testserver", "nieznany.olimpiada.example"])
def test_missing_static_file_is_a_404_not_a_500(client, host):
    # WhiteNoise nie zna pliku → urlconf → django CMS; witryna (leniwa) jest, więc 404, a nie 500.
    assert client.get("/djcms/static/nie-ma-takiego.css", HTTP_HOST=host).status_code == 301  # APPEND_SLASH
    assert client.get("/djcms/static/nie-ma-takiego/", HTTP_HOST=host).status_code == 404


def test_soft_site_follows_the_host_and_falls_back_to_default(make_competition):
    from apps.sites.middleware import _soft_site

    fizyka = make_competition("fizyka", hosts=["fizyka.example"])
    assert _soft_site("fizyka.example") == fizyka.site
    assert _soft_site("nieznany.olimpiada.example").pk == 1
    CompetitionSite.objects.all().delete()
    assert _soft_site("nieznany.olimpiada.example").pk == 1  # bez rejestru – pierwsza witryna
