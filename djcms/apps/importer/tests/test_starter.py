"""Drzewo startowe nowego konkursu (DJ-02 D7): leniwie w żądaniu, ``sync_competitions --import-missing``
i ``--prune``."""

from __future__ import annotations

import io
import urllib.error
from datetime import timedelta

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
    nowy.refresh_from_db()
    assert nowy.starter_failed_at is not None
    # Porażka zamyka próby na chwilę – bez ponownego pobierania paczki w każdym żądaniu.
    assert client.get("/", HTTP_HOST="nowy.olimpiada.example").status_code == 503
    assert main_api.calls("export", competition="nowy") == 1


def test_dead_api_is_503_with_backoff(client, main_api, nowy):
    main_api.fail("export", urllib.error.URLError(ConnectionRefusedError()), competition="nowy")
    assert client.get("/", HTTP_HOST="nowy.olimpiada.example").status_code == 503
    second = client.get("/", HTTP_HOST="nowy.olimpiada.example")
    assert second.status_code == 503
    assert 0 < int(second["Retry-After"]) <= starter.retry_seconds() + 1
    assert main_api.calls("export", competition="nowy") == 1
    # Znacznik porażki jest w bazie, nie w buforze procesu: inny worker (pusty bufor) też go widzi.
    cache.clear()
    assert client.get("/", HTTP_HOST="nowy.olimpiada.example").status_code == 503
    assert main_api.calls("export", competition="nowy") == 1
    _fail_long_ago(nowy)
    _export(main_api)
    assert client.get("/", HTTP_HOST="nowy.olimpiada.example").status_code in (200, 404)
    assert _pages(nowy) == 4
    nowy.refresh_from_db()
    assert nowy.starter_failed_at is None  # udany import czyści znacznik


def _fail_long_ago(competition):
    from apps.sites.models import CompetitionSite

    past = timezone.now() - timedelta(seconds=starter.retry_seconds() + 1)
    CompetitionSite.objects.filter(pk=competition.pk).update(starter_failed_at=past)


def test_failure_marker_is_checked_again_after_the_lock(main_api, nowy):
    """Wątek z nieaktualnym obiektem konkursu (porażkę zapisał inny proces) nie pobiera paczki."""
    from apps.sites.models import CompetitionSite

    _export(main_api)
    CompetitionSite.objects.filter(pk=nowy.pk).update(starter_failed_at=timezone.now())
    assert nowy.starter_failed_at is None  # obiekt z rozstrzygnięcia żądania – sprzed porażki
    response = starter.ensure_content_for_request(nowy)
    assert response is not None and response.status_code == 503
    assert main_api.calls("export", competition="nowy") == 0
    assert _pages(nowy) == 0


def test_lock_held_elsewhere_is_503_at_once(main_api, nowy):
    """Blokada w innym połączeniu (inny proces importuje) – 503 bez czekania i bez pobierania paczki."""
    from django.db import connection, connections

    if connection.vendor != "postgresql":
        pytest.skip("blokady doradcze tylko w Postgresie")
    _export(main_api)
    other = connections.create_connection("default")
    try:
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", [starter.LOCK_KEY.format(slug="nowy")])
        response = starter.ensure_content_for_request(nowy)
        assert response.status_code == 503
        assert response["Retry-After"] == str(starter.BUSY_RETRY_SECONDS)
        assert main_api.calls("export", competition="nowy") == 0
        nowy.refresh_from_db()
        assert nowy.starter_failed_at is None  # zajęta blokada to nie porażka importu
        with pytest.raises(starter.StarterBusy):
            starter.ensure_site(nowy)
    finally:
        other.close()
    assert starter.ensure_content_for_request(nowy) is None
    assert _pages(nowy) == 4


def test_full_process_semaphore_is_503_without_download(main_api, nowy, monkeypatch):
    import threading

    slots = threading.BoundedSemaphore(1)
    slots.acquire()  # import innego konkursu w tym procesie
    monkeypatch.setattr(starter, "_import_slots", slots)
    _export(main_api)
    response = starter.ensure_content_for_request(nowy)
    assert response.status_code == 503
    assert main_api.calls("export", competition="nowy") == 0


def test_import_transaction_has_a_lock_timeout(main_api, nowy, superuser, monkeypatch):
    from django.db import connection

    if connection.vendor != "postgresql":
        pytest.skip("lock_timeout tylko w Postgresie")
    seen = []
    original = starter.services.run_import

    def spy(*args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            seen.append(cursor.fetchone()[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(starter.services, "run_import", spy)
    _export(main_api)
    assert starter.ensure_site(nowy) is not None
    # ``SET LOCAL`` żyje do końca transakcji – w teście to transakcja testu, w żądaniu ``ensure_site``.
    assert seen == [starter.REQUEST_LOCK_TIMEOUT]


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
