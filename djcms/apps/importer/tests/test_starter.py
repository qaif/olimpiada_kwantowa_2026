"""Drzewo startowe nowego konkursu (DJ-02 D7): leniwie w żądaniu, ``sync_competitions --import-missing``
i ``--prune``."""

from __future__ import annotations

import io
import urllib.error

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.importer import starter
from apps.importer.tests import bundles

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def nowy(make_competition):
    """Konkurs z listy API (``synced_at``), bez stron – tak zostawia go ``sync_registry``."""
    competition = make_competition("nowy", name="Nowy Konkurs", hosts=["nowy.olimpiada.example"])
    competition.synced_at = timezone.now()
    competition.save(update_fields=["synced_at"])
    return competition


def _export(main_api, slug="nowy", **kwargs):
    main_api.set(
        "export",
        raw=bundles.build_zip(bundles.starter_manifest(slug, "Nowy Konkurs", **kwargs)),
        competition=slug,
    )


def _pages(competition) -> int:
    from cms.models import Page

    return Page.objects.filter(site=competition.site).count()


def test_first_request_imports_the_starter_tree(client, main_api, nowy, chrome_payload):
    _export(main_api)
    main_api.set("chrome", chrome_payload(), competition="nowy")
    response = client.get("/zadania/", HTTP_HOST="nowy.olimpiada.example")
    assert response.status_code == 200
    assert _pages(nowy) == 4
    nowy.refresh_from_db()
    assert nowy.content_imported_at is not None
    # Kolejne żądanie – bez pobierania paczki.
    client.get("/", HTTP_HOST="nowy.olimpiada.example")
    assert main_api.calls("export", competition="nowy") == 1
    # Autor wersji: brak superusera → konto techniczne bez hasła, nieaktywne.
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.get(username=starter.IMPORT_USERNAME)
    assert not user.is_active and not user.has_usable_password()


def test_competition_not_from_api_is_never_imported_lazily(client, main_api, make_competition):
    reczny = make_competition("reczny", hosts=["reczny.olimpiada.example"])  # ``synced_at`` puste
    _export(main_api, "reczny")
    assert client.get("/", HTTP_HOST="reczny.olimpiada.example").status_code != 503
    assert main_api.calls("export", competition="reczny") == 0
    assert _pages(reczny) == 0


def test_too_large_tree_is_503_and_left_for_deploy(client, main_api, nowy):
    _export(main_api, extra_pages=starter.STARTER_MAX_PAGES)
    response = client.get("/", HTTP_HOST="nowy.olimpiada.example")
    assert response.status_code == 503 and response["Retry-After"]
    assert "najbliższym wdrożeniu" in response.content.decode()
    assert _pages(nowy) == 0
    # Porażka zamyka próby na chwilę – bez ponownego pobierania paczki w każdym żądaniu.
    assert client.get("/", HTTP_HOST="nowy.olimpiada.example").status_code == 503
    assert main_api.calls("export", competition="nowy") == 1


def test_dead_api_is_503_with_backoff(client, main_api, nowy):
    main_api.fail("export", urllib.error.URLError(ConnectionRefusedError()), competition="nowy")
    assert client.get("/", HTTP_HOST="nowy.olimpiada.example").status_code == 503
    assert client.get("/", HTTP_HOST="nowy.olimpiada.example").status_code == 503
    assert main_api.calls("export", competition="nowy") == 1
    cache.delete(starter.FAILURE_KEY.format(slug="nowy"))
    _export(main_api)
    assert client.get("/", HTTP_HOST="nowy.olimpiada.example").status_code in (200, 404)
    assert _pages(nowy) == 4


def test_ensure_site_does_nothing_when_pages_exist(main_api, nowy, superuser):
    _export(main_api)
    assert starter.ensure_site(nowy) is not None
    assert starter.ensure_site(nowy) is None  # drugi wątek po blokadzie: strony już są
    assert main_api.calls("export", competition="nowy") == 1


def test_ensure_site_refuses_inactive(main_api, nowy):
    nowy.is_active = False
    nowy.save(update_fields=["is_active"])
    with pytest.raises(starter.StarterRefused, match="nieaktywny"):
        starter.ensure_site(nowy)


def test_existing_site_with_pages_is_checked_once_per_process(django_assert_max_num_queries, make_page, nowy):
    make_page("Start", "start", site=nowy.site)
    assert starter.ensure_content_for_request(nowy) is None
    with django_assert_max_num_queries(0):
        assert starter.ensure_content_for_request(nowy) is None


# --- sync_competitions --import-missing / --prune -------------------------------------------------


def _competitions_payload(*extra):
    from conftest import default_competitions_payload

    payload = default_competitions_payload()
    payload["competitions"].extend(extra)
    return payload


def _entry(slug, **overrides):
    from conftest import default_competitions_payload

    entry = {
        **default_competitions_payload()["competitions"][0],
        "slug": slug,
        "name": slug.title(),
        "is_default": False,
        "hosts": [f"{slug}.olimpiada.example"],
        "site_hostname": f"{slug}.olimpiada.example",
        "fingerprint": slug[0] * 64,
    }
    entry.update(overrides)
    return entry


def _sync(*args) -> str:
    out = io.StringIO()
    call_command("sync_competitions", *args, stdout=out)
    return out.getvalue()


def test_import_missing_fills_every_empty_active_site(main_api, superuser, make_page):
    make_page("Start", "start", home=True)  # konkurs domyślny ma treść – bez importu
    main_api.set(
        "competitions",
        _competitions_payload(_entry("nowy"), _entry("stary", is_active=False)),
        competition=None,
    )
    _export(main_api, "nowy", extra_pages=60)  # bez limitu stron przy wdrożeniu
    assert "nowy – do importu" in _sync("--import-missing", "--dry-run")
    out = _sync("--import-missing")
    assert "nowy – zaimportowano stron: 64" in out
    assert main_api.calls("export", competition="kwantowa") == 0
    assert main_api.calls("export", competition="stary") == 0
    assert "każda aktywna witryna ma już treść" in _sync("--import-missing")


def test_import_missing_failure_is_exit_code_1(main_api, superuser, make_page):
    make_page("Start", "start", home=True)
    main_api.set("competitions", _competitions_payload(_entry("nowy")), competition=None)
    with pytest.raises(CommandError, match="Import treści nie powiódł się dla: nowy"):
        _sync("--import-missing")


def test_prune_only_inactive_and_only_with_yes(main_api, superuser, make_page):
    from django.contrib.sites.models import Site

    from apps.sites.models import CompetitionSite

    make_page("Start", "start", home=True)

    main_api.set("competitions", _competitions_payload(_entry("stary")), competition=None)
    _export(main_api, "stary")
    _sync("--import-missing")
    with pytest.raises(CommandError, match="jest aktywny"):
        _sync("--prune", "stary", "--yes")
    main_api.set("competitions", _competitions_payload(), competition=None)  # zniknął z listy
    with pytest.raises(CommandError, match="wymaga --yes"):
        _sync("--prune", "stary")
    assert "ma 4 stron" in _sync("--prune", "stary", "--dry-run")
    site_id = CompetitionSite.objects.get(slug="stary").site_id
    assert "skasowano witrynę „stary” (4 stron)" in _sync("--prune", "stary", "--yes")
    assert not CompetitionSite.objects.filter(slug="stary").exists()
    assert not Site.objects.filter(pk=site_id).exists()
    with pytest.raises(CommandError, match="nie ma w rejestrze"):
        _sync("--prune", "stary", "--yes")
