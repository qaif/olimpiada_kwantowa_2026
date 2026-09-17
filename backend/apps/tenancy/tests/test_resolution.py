"""Rozstrzyganie konkursu: host → witryna → konkurs, oraz prefiks ścieżki.

Sprawdzamy regułę, nie implementację: dla instalacji z jednym konkursem każdy host ma oddać ten
konkurs (także host nieznany – przez witrynę domyślną), a przy dwóch konkursach host ma
rozstrzygać jednoznacznie. To jest pierwsza z dwóch bram izolacji; drugą (queryset) opisuje
``apps/tenancy/managers.py``.
"""

import pytest
from django.test import RequestFactory
from wagtail.models import Site

from apps.tenancy.models import Competition, RoutingMode
from apps.tenancy.resolution import resolve_competition, resolve_for_request

from .conftest import HOST_A, HOST_B, make_site


def request_for(host: str, path: str = "/"):
    return RequestFactory().get(path, HTTP_HOST=host)


def with_path_prefix(competition, prefix: str):
    """Przestawia konkurs na tryb prefiksu ścieżki."""
    competition.routing_mode = RoutingMode.PATH
    competition.path_prefix = prefix
    competition.save(update_fields=["routing_mode", "path_prefix"])
    return competition


def test_host_of_the_competition_resolves_to_it(competition):
    assert resolve_competition(request_for(HOST_A)) == competition


def test_each_host_resolves_to_its_own_competition(competition, other_competition):
    assert resolve_competition(request_for(HOST_A)) == competition
    assert resolve_competition(request_for(HOST_B)) == other_competition


def test_unknown_host_falls_back_to_the_default_site(competition):
    """Deweloper bez wpisu w DNS ma dostać Konkurs #1 – dokładnie tak, jak przed tą zmianą."""
    assert resolve_competition(request_for("nieznany.invalid")) == competition


def test_inactive_competition_is_not_resolved(competition):
    """Wyłączony konkurs znaczy „nie ma tu nic”, a nie „pokaż mimo wszystko”."""
    competition.is_active = False
    competition.save(update_fields=["is_active"])
    assert resolve_competition(request_for(HOST_A)) is None


def test_site_without_competition_resolves_to_nothing(competition):
    """Witryna bez konkursu nie może oddać cudzego konkursu.

    To jest ta sama reguła, co „domyślnie zamknięte” w managerach: nieprzypisany host nie widzi
    danych konkursu, który akurat stoi obok.
    """
    make_site("bez-konkursu.invalid")

    assert resolve_competition(request_for("bez-konkursu.invalid")) is None


@pytest.mark.django_db
def test_request_without_any_site_resolves_to_nothing():
    Competition.objects.all().delete()
    Site.objects.all().delete()

    assert resolve_competition(request_for("cokolwiek.invalid")) is None


def test_a_segment_that_is_nobodys_prefix_changes_nothing(competition):
    """Instalacja Olimpiady Kwantowej nie wchodzi w gałąź prefiksu ani razu."""
    resolution = resolve_for_request(request_for(HOST_A, "/kwantowa/me/"))

    assert resolution.competition == competition
    assert resolution.path_prefix == ""


def test_path_prefix_wins_over_the_host(competition, other_competition):
    """Konkurs w trybie prefiksu stoi na domenie platformy – host oddałby konkurs platformy."""
    with_path_prefix(other_competition, "fizyczna")

    resolution = resolve_for_request(request_for(HOST_A, "/fizyczna/me/"))

    assert resolution.competition == other_competition
    assert resolution.path_prefix == "fizyczna"


def test_own_domain_of_a_path_competition_still_resolves_by_host(competition, other_competition):
    with_path_prefix(other_competition, "fizyczna")

    resolution = resolve_for_request(request_for(HOST_B, "/me/"))

    assert resolution.competition == other_competition
    assert resolution.path_prefix == ""


def test_root_path_is_not_treated_as_a_prefix(competition, other_competition):
    """Pusty pierwszy segment nie ma prawa dopasować się do konkursu z pustym prefiksem."""
    other_competition.routing_mode = RoutingMode.PATH
    other_competition.save(update_fields=["routing_mode"])

    resolution = resolve_for_request(request_for(HOST_A, "/"))

    assert resolution.competition == competition
    assert resolution.path_prefix == ""
