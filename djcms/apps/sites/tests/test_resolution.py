"""Rozstrzyganie konkursu w djcms (DJ-02 § 2.1) – wspólne wektory z aplikacją główną.

``backend/djcms_contract/resolution_cases.json`` jest autorytatywny: aplikacja główna sprawdza te same
przypadki przez ``resolve_for_request`` (``apps/tenancy/tests/test_resolution_contract.py``), a tu
każdy świat trafia do rejestru **przez** ``sync_registry`` (ta sama droga co lista z API), po czym
każdy przypadek idzie przez ``apps.sites.resolution.resolve``.
"""

import json
from pathlib import Path

import pytest
from django.conf import settings

from apps.sites import registry
from apps.sites.models import CompetitionSite
from apps.sites.resolution import first_path_segment, normalise_host, resolve

CASES_FILE = "resolution_cases.json"


def _cases() -> dict:
    path = Path(settings.DJCMS_CONTRACT_DIR) / CASES_FILE
    return json.loads(path.read_text(encoding="utf-8"))


def _worlds():
    data = _cases()
    assert data["version"] == 1
    for world in data["worlds"]:
        for case in world["cases"]:
            yield pytest.param(world, case, id=f"{world['id']}:{case['id']}")


def _payload(world) -> dict:
    """Świat z pliku jako odpowiedź ``competitions`` – pola spoza rozstrzygania uzupełnione."""
    competitions = []
    for entry in world["competitions"]:
        competitions.append({"name": entry["slug"].title(), "public_base": None, "linked_paths": [], **entry})
    return {"platform": world["platform"], "competitions": competitions}


def test_contract_file_is_present_and_versioned():
    data = _cases()
    assert data["version"] == 1
    assert data["worlds"], "resolution_cases.json bez światów"


@pytest.mark.django_db
@pytest.mark.parametrize(("world", "case"), list(_worlds()))
def test_shared_resolution_vectors(world, case):
    # Świat testów zaczyna od pustego rejestru – konkurs z fixture'a ``competition_site`` odchodzi.
    CompetitionSite.objects.all().delete()
    registry.sync_registry(_payload(world))
    result = resolve(case["host"], case["path"])
    got = {"slug": result.site.slug if result.site else None, "path_prefix": result.path_prefix}
    assert got == case["expect"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Olimpiada.TEST", "olimpiada.test"),
        ("olimpiada.test.", "olimpiada.test"),
        ("olimpiada.test:8443", "olimpiada.test"),
        (" localhost:8100 ", "localhost"),
        ("[::1]:8000", "[::1]"),
        ("", ""),
    ],
)
def test_normalise_host(raw, expected):
    assert normalise_host(raw) == expected


def test_first_path_segment():
    assert first_path_segment("/druga/zadania/") == "druga"
    assert first_path_segment("//druga/") == "druga"
    assert first_path_segment("/") == ""


@pytest.mark.django_db
def test_resolution_costs_one_query(make_competition, django_assert_num_queries):
    CompetitionSite.objects.filter(slug="kwantowa").update(hosts_path_prefixes=True)
    make_competition("druga", routing_mode="PATH", path_prefix="druga")
    with django_assert_num_queries(1):
        result = resolve("testserver", "/druga/zadania/")
    assert (result.site.slug, result.path_prefix) == ("druga", "druga")
    # Witryna django CMS przychodzi tym samym zapytaniem (``select_related``).
    with django_assert_num_queries(0):
        assert result.site.site.pk


@pytest.mark.django_db
def test_local_host_prefers_its_own_entry_over_default(make_competition):
    make_competition("lokalny", hosts=["localhost"])
    assert resolve("localhost", "/").site.slug == "lokalny"
    assert resolve("127.0.0.1", "/").site.slug == "kwantowa"
