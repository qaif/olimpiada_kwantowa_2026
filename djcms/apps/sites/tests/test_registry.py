"""Rejestr konkursów: uzgadnianie z ``GET competitions`` (DJ-02 D7, § 5.2) i komenda ``sync_competitions``."""

import io
import urllib.error

import pytest
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.sites import registry
from apps.sites.models import CompetitionHost, CompetitionSite

pytestmark = pytest.mark.django_db


def entry(slug, **overrides) -> dict:
    data = {
        "slug": slug,
        "name": f"Konkurs {slug}",
        "short_name": slug[:3].upper(),
        "is_active": True,
        "is_default": False,
        "routing_mode": "DOMAIN",
        "path_prefix": "",
        "hosts": [f"{slug}.olimpiada.example"],
        "hosts_path_prefixes": False,
        "public_base": {"origin": f"https://{slug}.olimpiada.example", "path_prefix": ""},
        "site_hostname": f"{slug}.olimpiada.example",
        "has_site_aliases": False,
        "linked_paths": ["/dokumenty/regulamin/"],
        "fingerprint": (slug[0] * 64)[:64] if slug[0] in "abcdef" else "1" * 64,
    }
    data.update(overrides)
    return data


def default_entry(**overrides) -> dict:
    data = {
        "is_default": True,
        "hosts": ["testserver", "olimpiada.example"],
        "hosts_path_prefixes": True,
        "public_base": {"origin": "https://olimpiada.example", "path_prefix": ""},
        "site_hostname": "olimpiada.example",
        "fingerprint": "a" * 64,
    }
    data.update(overrides)
    return entry("kwantowa", **data)


def payload(*entries) -> dict:
    return {
        "platform": {"site_domain": "olimpiada.example", "default_slug": "kwantowa"},
        "competitions": list(entries),
    }


def hosts(slug) -> list[str]:
    return sorted(CompetitionHost.objects.filter(competition__slug=slug).values_list("host", flat=True))


# --- zakładanie i aktualizacja ----------------------------------------------------------------------


def test_sync_creates_sites_hosts_and_public_base():
    report = registry.sync_registry(
        payload(
            default_entry(),
            entry("fizyka", hosts=["fizyka.example", "FIZYKA.olimpiada.example."], fingerprint="b" * 64),
            entry(
                "druga",
                routing_mode="PATH",
                path_prefix="druga",
                hosts=["druga.olimpiada.example"],
                public_base={"origin": "https://olimpiada.example", "path_prefix": "/druga"},
                fingerprint="c" * 64,
            ),
        )
    )
    assert report.created == ["druga", "fizyka"]
    assert report.updated == ["kwantowa"]  # konkurs z fixture'a – odcisk inny niż z API
    fizyka = CompetitionSite.objects.get(slug="fizyka")
    assert fizyka.site.domain == "fizyka.olimpiada.example"
    assert fizyka.public_base == "https://fizyka.olimpiada.example"
    assert fizyka.linked_paths == ["/dokumenty/regulamin/"]
    assert hosts("fizyka") == ["fizyka.example", "fizyka.olimpiada.example"]
    druga = CompetitionSite.objects.get(slug="druga")
    assert (druga.routing_mode, druga.path_prefix, druga.public_base) == (
        "PATH",
        "druga",
        "https://olimpiada.example/druga",
    )
    kwantowa = CompetitionSite.objects.get(slug="kwantowa")
    assert kwantowa.site_id == 1 and kwantowa.hosts_path_prefixes
    assert hosts("kwantowa") == ["olimpiada.example", "testserver"]
    assert Site.objects.get(pk=1).domain == "olimpiada.example"


def test_same_fingerprint_means_zero_writes():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    data = payload(default_entry(), entry("fizyka", fingerprint="b" * 64))
    registry.sync_registry(data)
    with CaptureQueriesContext(connection) as queries:
        report = registry.sync_registry(data)
    assert report.unchanged == ["fizyka", "kwantowa"]
    assert not report.changed
    statements = [query["sql"].split()[0].upper() for query in queries.captured_queries]
    # Blokada doradcza i jeden odczyt rejestru (plus punkty zapisu transakcji testu) – żadnego zapisu.
    assert not {"INSERT", "UPDATE", "DELETE"} & set(statements), statements
    assert statements.count("SELECT") == 2


def test_changed_entry_updates_domain_hosts_and_name():
    registry.sync_registry(payload(default_entry(), entry("fizyka", fingerprint="b" * 64)))
    site_pk = CompetitionSite.objects.get(slug="fizyka").site_id
    registry.sync_registry(
        payload(
            default_entry(),
            entry(
                "fizyka",
                name="Olimpiada Fizyczna",
                hosts=["fizyka.example"],
                site_hostname="fizyka.example",
                fingerprint="c" * 64,
            ),
        )
    )
    fizyka = CompetitionSite.objects.get(slug="fizyka")
    assert fizyka.site_id == site_pk  # witryna (drzewo stron) ta sama – zmienia się tylko opis
    assert (fizyka.name, fizyka.site.domain) == ("Olimpiada Fizyczna", "fizyka.example")
    assert hosts("fizyka") == ["fizyka.example"]


def test_inactive_and_missing_competitions_are_deactivated_not_deleted(make_page):
    registry.sync_registry(payload(default_entry(), entry("fizyka", fingerprint="b" * 64), entry("chemia")))
    fizyka_site = CompetitionSite.objects.get(slug="fizyka").site
    make_page("Strona fizyki", "strona", site=fizyka_site)
    report = registry.sync_registry(
        payload(default_entry(), entry("fizyka", is_active=False, fingerprint="c" * 64))
    )
    assert report.deactivated == ["chemia", "fizyka"]
    assert set(CompetitionSite.objects.filter(is_active=False).values_list("slug", flat=True)) == {
        "chemia",
        "fizyka",
    }
    # Treść zostaje – kasuje tylko operator (``--prune``, DJ-02e).
    from cms.models import Page

    assert Page.objects.filter(site=fizyka_site).exists()
    # Konkurs wraca na listę – odżywa ta sama witryna.
    registry.sync_registry(payload(default_entry(), entry("chemia", fingerprint="d" * 64)))
    assert CompetitionSite.objects.get(slug="chemia").is_active


def test_host_moves_between_competitions():
    registry.sync_registry(
        payload(
            default_entry(), entry("fizyka", hosts=["wspolna.example"], fingerprint="b" * 64), entry("chemia")
        )
    )
    registry.sync_registry(
        payload(
            default_entry(),
            entry("fizyka", hosts=[], fingerprint="c" * 64),
            entry("chemia", hosts=["wspolna.example"], fingerprint="d" * 64),
        )
    )
    assert hosts("chemia") == ["wspolna.example"]
    assert hosts("fizyka") == []


def test_default_role_moves_without_violating_the_single_default_constraint():
    registry.sync_registry(payload(default_entry(), entry("fizyka", fingerprint="b" * 64)))
    registry.sync_registry(
        payload(
            default_entry(is_default=False, fingerprint="e" * 64),
            entry("fizyka", is_default=True, fingerprint="c" * 64),
        )
    )
    assert list(CompetitionSite.objects.filter(is_default=True).values_list("slug", flat=True)) == ["fizyka"]


def test_default_competition_adopts_the_legacy_site_on_an_empty_registry():
    CompetitionSite.objects.all().delete()
    report = registry.sync_registry(payload(default_entry()))
    assert report.adopted_legacy_site
    assert CompetitionSite.objects.get(slug="kwantowa").site_id == registry.LEGACY_SITE_ID


def test_colliding_site_domain_gets_a_fallback_label():
    Site.objects.create(domain="zajeta.example", name="obca")
    registry.sync_registry(payload(default_entry(), entry("fizyka", site_hostname="zajeta.example")))
    assert CompetitionSite.objects.get(slug="fizyka").site.domain == "fizyka.djcms.invalid"


def test_dry_run_writes_nothing():
    report = registry.sync_registry(payload(default_entry(), entry("fizyka")), dry_run=True)
    assert report.created == ["fizyka"]
    assert not CompetitionSite.objects.filter(slug="fizyka").exists()


@pytest.mark.parametrize(
    "bad",
    [
        {"competitions": "nie lista"},
        payload(entry("A-zly")),
        payload(entry("fizyka", routing_mode="SUBDOMAIN")),
        payload(entry("fizyka", routing_mode="PATH", path_prefix="")),
        payload(entry("fizyka", hosts=["zły host"])),
        payload(entry("fizyka", is_active="tak")),
        payload(entry("fizyka", name="")),
        payload(entry("fizyka"), entry("fizyka")),
        payload(default_entry(), entry("fizyka", is_default=True)),
        payload(entry("fizyka", hosts=["x.example"]), entry("chemia", hosts=["x.example"])),
    ],
)
def test_invalid_list_is_rejected_as_a_whole(bad):
    before = list(CompetitionSite.objects.values_list("slug", "fingerprint"))
    with pytest.raises(registry.RegistryError):
        registry.sync_registry(bad)
    assert list(CompetitionSite.objects.values_list("slug", "fingerprint")) == before


@pytest.mark.parametrize(
    "base",
    [
        {"origin": "javascript:alert(1)", "path_prefix": ""},
        {"origin": "https://olimpiada.example/sciezka", "path_prefix": ""},
        {"origin": "https://user@olimpiada.example", "path_prefix": ""},
        {"origin": "https://olimpiada.example", "path_prefix": "druga"},
        "https://olimpiada.example",
    ],
)
def test_malformed_public_base_is_dropped_not_fatal(base):
    registry.sync_registry(payload(default_entry(), entry("fizyka", public_base=base)))
    assert CompetitionSite.objects.get(slug="fizyka").public_base == ""


def test_missing_fingerprint_is_computed_locally(django_assert_max_num_queries):
    data = payload(default_entry(), entry("fizyka", fingerprint=None))
    registry.sync_registry(data)
    stored = CompetitionSite.objects.get(slug="fizyka").fingerprint
    assert len(stored) == 64
    assert not registry.sync_registry(data).changed


# --- odświeżanie z API -------------------------------------------------------------------------------


def test_refresh_if_due_runs_once_per_window(main_api, settings):
    settings.DJCMS_SITES_REFRESH_SECONDS = 60
    main_api.set("competitions", payload(default_entry(), entry("fizyka")), competition=None)
    assert registry.refresh_if_due() is not None
    assert CompetitionSite.objects.filter(slug="fizyka").exists()
    assert registry.refresh_if_due() is None  # klucz świeżości żyje – drugi wątek nie odświeża
    cache.delete(registry.FRESH_KEY)
    cache.delete("djcms:api:v2:_:competitions")
    assert registry.refresh_if_due() is not None
    assert main_api.calls("competitions", competition=None) == 2


def test_refresh_disabled_with_zero(main_api, settings):
    settings.DJCMS_SITES_REFRESH_SECONDS = 0
    assert registry.refresh_if_due() is None
    assert main_api.requests == []


def test_refresh_survives_dead_api_and_bad_list(main_api, settings):
    settings.DJCMS_SITES_REFRESH_SECONDS = 60
    main_api.fail("competitions", urllib.error.URLError(ConnectionRefusedError()), competition=None)
    assert registry.refresh_if_due() is None
    cache.clear()
    main_api.set("competitions", {"competitions": "zepsute"}, competition=None)
    assert registry.refresh_if_due() is None
    assert list(CompetitionSite.objects.values_list("slug", flat=True)) == ["kwantowa"]


def test_refresh_for_host_syncs_only_for_a_listed_active_host(main_api):
    main_api.set(
        "competitions",
        payload(default_entry(), entry("fizyka"), entry("uspiona", is_active=False)),
        competition=None,
    )
    assert registry.refresh_for_host("nieznany.olimpiada.example") is False
    assert registry.refresh_for_host("uspiona.olimpiada.example") is False
    assert not CompetitionSite.objects.filter(slug="fizyka").exists()
    assert registry.refresh_for_host("FIZYKA.olimpiada.example") is True
    assert CompetitionSite.objects.get(slug="fizyka").is_active


def test_default_site_from_registry_or_api(main_api):
    assert registry.default_site().pk == 1
    CompetitionSite.objects.all().delete()
    assert registry.default_site().pk == 1  # uzgodnienie z bieżącą listą (konkurs domyślny przejmuje #1)
    assert main_api.calls("competitions", competition=None) == 1


def test_default_site_without_default_competition_is_an_error(main_api):
    CompetitionSite.objects.all().delete()
    main_api.set("competitions", payload(entry("fizyka")), competition=None)
    with pytest.raises(registry.RegistryError, match="sync_competitions"):
        registry.default_site()
    main_api.fail("competitions", TimeoutError(), competition=None)
    with pytest.raises(registry.RegistryError, match="niedostępna"):
        registry.default_site()


# --- komenda ----------------------------------------------------------------------------------------


def run(*args) -> str:
    out = io.StringIO()
    call_command("sync_competitions", *args, stdout=out)
    return out.getvalue()


def test_command_syncs_and_lists_hosts(main_api):
    main_api.set(
        "competitions",
        payload(
            default_entry(),
            entry("fizyka", hosts=["fizyka.example"], fingerprint="b" * 64),
            entry("druga", routing_mode="PATH", path_prefix="druga", hosts=[], fingerprint="c" * 64),
        ),
        competition=None,
    )
    out = run("--list-hosts")
    assert "nowe: druga, fizyka" in out
    assert "fizyka.example fizyka" in out
    assert "testserver kwantowa" in out
    assert "olimpiada.example/druga/ druga" in out
    assert "sync_competitions: gotowe." in out
    assert "bez zmian: druga, fizyka, kwantowa" in run()


def test_command_dry_run(main_api):
    main_api.set("competitions", payload(default_entry(), entry("fizyka")), competition=None)
    out = run("--dry-run")
    assert "[dry-run] nowe: fizyka" in out
    assert not CompetitionSite.objects.filter(slug="fizyka").exists()


def test_command_ignores_the_client_breaker(main_api):
    cache.set("djcms:api:breaker", True, 15)
    main_api.set("competitions", payload(default_entry()), competition=None)
    assert "sync_competitions: gotowe." in run()


def test_command_errors(main_api):
    main_api.fail("competitions", TimeoutError(), competition=None)
    with pytest.raises(CommandError, match="niedostępna: timeout"):
        run()
    main_api.set("competitions", {"competitions": [{"slug": "ZLE"}]}, competition=None)
    with pytest.raises(CommandError, match="odrzucona"):
        run()
