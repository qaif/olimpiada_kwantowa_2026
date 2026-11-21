"""Mapa uczestników (MAP-01): agregat, próg ochrony, izolacja, flaga, menu, pamięć, strona bez JS, panel.

Pilnujemy przede wszystkim tego, co na stronie publicznej z danymi małoletnich psuje się najciszej:

- **próg ochrony** – miejscowość z mniej niż 3 uczestnikami nie wychodzi z nazwą ani z kółkiem,
  jej uczniowie lądują w „pozostałych miejscowościach” województwa, a próg nie da się obniżyć,
- **izolacja** – uczestnicy innego konkursu i konta zanonimizowane nie są liczeni,
- **flaga** – bez niej adresu nie ma (404), a menu i spis dokumentów są takie jak dziś,
- **koszt** – stała liczba zapytań na zimno, zero na ciepło; przycisk koordynatora przelicza
  i gasi zapisane strony gościa.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Page

from apps.accounts.anonymised import ANONYMISED_EMAIL_MARKER
from apps.accounts.models import CompetitionRole, Voivodeship
from apps.accounts.processing_register import activities_for
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.cms.models import DocumentIndexPage, DocumentPage, HomePage
from apps.competitions.tests.factories import CurrentEditionFactory
from apps.core.models import AuditLog
from apps.participant_map import geometry, services
from apps.participant_map.models import MapSettings
from apps.schools.models import TownLocation
from apps.schools.tests.factories import SchoolFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

URL = "/dokumenty/mapa-uczestnikow/"
PANEL = "/coordinator/participant-map/"
REFRESH = "/coordinator/participant-map/refresh/"


# --- świat testu ---------------------------------------------------------------------------------


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(competition=competition, created_at=timezone.now() - timedelta(days=30))


def school(city: str, voivodeship: str, postal_code: str = "30-001", **kwargs):
    return SchoolFactory(city=city, voivodeship=voivodeship, postal_code=postal_code, **kwargs)


def pupils(competition, school_row, count: int, **kwargs) -> list:
    return [
        ParticipantFactory(competition=competition, school_ref=school_row, **kwargs) for _ in range(count)
    ]


@pytest.fixture
def points():
    """Punkty dla miejscowości testu (w prawdziwych województwach – patrz test geometrii)."""
    rows = [
        ("Kraków", Voivodeship.MALOPOLSKIE, 50.0619, 19.9369),
        ("Gdynia", Voivodeship.POMORSKIE, 54.5165, 18.5403),
        ("Pcim", Voivodeship.MALOPOLSKIE, 49.7525, 19.9714),
    ]
    TownLocation.objects.bulk_create(
        [TownLocation(city_parent=c, voivodeship=v, latitude=lat, longitude=lon) for c, v, lat, lon in rows]
    )


@pytest.fixture
def world(competition, edition, points):
    """Kraków 4 (dwie dzielnice), Gdynia 3, Pcim 2 (pod progiem), Gorlice 3 bez punktu, 1 spoza wykazu."""
    krakow_a = school("Kraków-Podgórze", Voivodeship.MALOPOLSKIE, name="V LICEUM W KRAKOWIE")
    krakow_b = school("Kraków-Krowodrza", Voivodeship.MALOPOLSKIE, name="II LICEUM W KRAKOWIE")
    pupils(competition, krakow_a, 3)
    pupils(competition, krakow_b, 1)
    pupils(competition, school("Gdynia", Voivodeship.POMORSKIE, "81-001", name="III LO GDYNIA"), 3)
    pupils(competition, school("Pcim", Voivodeship.MALOPOLSKIE, "32-432", name="ZESPÓŁ SZKÓŁ W PCIMIU"), 2)
    pupils(competition, school("Gorlice", Voivodeship.MALOPOLSKIE, "38-300", name="LO GORLICE"), 3)
    ParticipantFactory(competition=competition, school_ref=None, school="Szkoła za granicą")
    return competition


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), services.MAP_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def coordinator(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def panel(client_for, competition, coordinator):
    client = client_for(competition)
    client.force_login(coordinator)
    return client


# --- agregat i próg ochrony ----------------------------------------------------------------------


def test_aggregate_groups_by_town_and_hides_towns_under_the_threshold(world):
    data = services.compute(world)

    assert data["total"] == 13
    assert data["outside_directory"] == 1
    assert data["schools"] == 5
    assert [(t["name"], t["count"], t["rank"]) for t in data["towns"]] == [
        ("Kraków", 4, 1),
        ("Gdynia", 3, 2),
        ("Gorlice", 3, 2),
    ]
    # Pcim (2 < 3) – bez nazwy, w „pozostałych miejscowościach” małopolskiego.
    assert data["others"] == [
        {
            "voivodeship": "malopolskie",
            "voivodeship_label": "małopolskie",
            "count": 2,
            "towns": 1,
            "search": "malopolskie",
        }
    ]
    assert data["hidden_towns"] == 1 and data["hidden_people"] == 2
    assert data["towns_total"] == 4
    assert data["unmapped"] == 1  # Gorlice – na liście, bez kółka
    assert [t["name"] for t in data["dots"]] == ["Kraków", "Gdynia"]
    assert data["top_share"] == round(100 * 10 / 13)


def test_threshold_from_settings_and_never_below_three(world):
    MapSettings.objects.create(competition=world, min_town_count=4)
    assert [t["name"] for t in services.compute(world)["towns"]] == ["Kraków"]

    MapSettings.objects.filter(competition=world).update(min_town_count=1)  # z pominięciem walidacji
    assert services.min_town_count(world) == 3
    assert "Pcim" not in {t["name"] for t in services.compute(world)["towns"]}


def test_anonymised_accounts_and_other_competitions_are_not_counted(world, other_competition):
    gdynia = TownLocation.objects.get(city_parent="Gdynia")
    assert gdynia
    other_edition = CurrentEditionFactory(
        competition=other_competition, created_at=timezone.now() - timedelta(days=3)
    )
    assert other_edition
    pupils(other_competition, school("Kraków-Nowa Huta", Voivodeship.MALOPOLSKIE), 5)
    ghost = UserFactory(email=f"x{ANONYMISED_EMAIL_MARKER}example.invalid")
    ParticipantFactory(
        competition=world, user=ghost, school_ref=school("Gdynia", Voivodeship.POMORSKIE, "81-002")
    )

    data = services.compute(world)

    assert data["total"] == 13
    assert dict((t["name"], t["count"]) for t in data["towns"])["Kraków"] == 4
    assert services.compute(other_competition)["total"] == 5


def test_participants_of_earlier_editions_are_not_counted(competition, edition, points):
    old = ParticipantFactory(competition=competition, school_ref=school("Kraków", Voivodeship.MALOPOLSKIE))
    old.user.date_joined = edition.created_at - timedelta(days=200)
    old.user.save(update_fields=["date_joined"])

    assert services.compute(competition)["total"] == 0


def test_no_current_edition_gives_an_empty_map(competition):
    data = services.compute(competition)
    assert data["total"] == 0 and data["towns"] == []


# --- flaga, strona publiczna, menu ---------------------------------------------------------------


def test_without_the_flag_the_page_does_not_exist(client_for, world):
    assert client_for(world).get(URL).status_code == 404


def test_page_renders_list_and_map_without_javascript(client_for, world):
    enable(world)

    response = client_for(world).get(URL)
    html = response.content.decode()

    assert response.status_code == 200
    # Lista i mapa są w HTML-u z serwera – bez skryptu widać obie.
    assert re.findall(r'class="pm-row__link">([^<]+)<', html) == ["Kraków", "Gdynia"]
    assert "Gorlice" in html and html.count('class="pm-dot"') == 2
    assert html.count('class="pm-voivodeship"') == 16
    # Pod progiem: ani nazwy, ani kółka; zbiorczo w województwie.
    assert "Pcim" not in html and "pozostałe miejscowości" in html
    # Żadnych nazw szkół ani danych osób.
    for name in ("LICEUM", "GDYNIA", "ZESPÓŁ", "Szkoła za granicą", "Kowalski"):
        assert name not in html
    # Skrypt wyłącznie z pliku, z nonce'em; wyszukiwarka startuje ukryta.
    assert re.search(r'<script defer nonce="[^"]+" src="[^"]*participant-map\.js"', html)
    assert re.search(r"data-pm-filter hidden", html)
    # Współrzędne SVG z kropką, nie z przecinkiem polskiej lokalizacji.
    assert not re.search(r'c[xy]="\d+,\d', html)
    assert "OpenStreetMap" in html and "Państwowy Rejestr Granic" in html
    assert "stan na" in html


def test_page_of_another_competition_does_not_leak(client_for, world, other_competition):
    enable(world)
    assert client_for(other_competition).get(URL).status_code == 404


def _documents_section(competition) -> DocumentIndexPage:
    home = HomePage.objects.get(pk=competition.site.root_page_id)
    index = DocumentIndexPage.objects.child_of(home).first() or home.add_child(
        instance=DocumentIndexPage(title="Dokumenty", slug="dokumenty", show_in_menus=True)
    )
    if not Page.objects.child_of(index).filter(slug="regulamin").exists():
        index.add_child(instance=DocumentPage(title="Regulamin", slug="regulamin"))
    return index


def _documents_children(response) -> list[tuple[str, str, bool]]:
    item = next(item for item in response.context["cms_menu"] if item["slug"] == "dokumenty")
    return [(child["title"], child["url"], child["active"]) for child in item["children"]], item["active"]


def test_menu_item_and_documents_card_follow_the_flag(client_for, world):
    _documents_section(world)
    client = client_for(world)

    children, _active = _documents_children(client.get("/"))
    assert ("Mapa uczestników", URL, False) not in children
    assert URL not in client.get("/dokumenty/").content.decode()

    enable(world)
    children, _active = _documents_children(client.get("/"))
    assert children[-1] == ("Mapa uczestników", URL, False)
    assert f'href="{URL}"' in client.get("/dokumenty/").content.decode()

    children, active = _documents_children(client.get(URL))
    assert children[-1] == ("Mapa uczestników", URL, True)
    assert active is True


# --- pamięć podręczna i koszt --------------------------------------------------------------------


def test_aggregate_is_cached_and_costs_nothing_when_warm(world):
    services.aggregate(world)
    with CaptureQueriesContext(connection) as warm:
        services.aggregate(world)
    assert len(warm.captured_queries) == 0


def test_cold_cost_does_not_grow_with_participants_or_towns(world):
    with CaptureQueriesContext(connection) as small:
        services.compute(world)
    pupils(world, school("Gdańsk", Voivodeship.POMORSKIE, "80-001"), 4)
    pupils(world, school("Tarnów", Voivodeship.MALOPOLSKIE, "33-100"), 5)
    with CaptureQueriesContext(connection) as large:
        services.compute(world)
    assert len(large.captured_queries) == len(small.captured_queries) <= 6


def test_page_query_budget(client_for, world, django_assert_max_num_queries):
    from apps.core.tests.query_budgets import budget

    enable(world)
    client = client_for(world)
    with django_assert_max_num_queries(budget("/dokumenty/mapa-uczestnikow/")):
        assert client.get(URL).status_code == 200


def test_new_town_points_invalidate_every_cached_map(world):
    first = services.aggregate(world)
    assert first["unmapped"] == 1
    TownLocation.objects.create(
        city_parent="Gorlice", voivodeship=Voivodeship.MALOPOLSKIE, latitude=49.655, longitude=21.159
    )
    assert services.aggregate(world)["unmapped"] == 1  # nadal z pamięci
    services.invalidate_all()
    assert services.aggregate(world)["unmapped"] == 0


# --- panel koordynatora --------------------------------------------------------------------------


def test_refresh_recomputes_clears_page_cache_and_writes_audit(panel, world, settings):
    from apps.web import page_cache

    stale = services.aggregate(world)
    pupils(world, school("Gdańsk", Voivodeship.POMORSKIE, "80-001"), 3)
    assert services.aggregate(world)["total"] == stale["total"]
    before = page_cache._versions(world.pk)

    response = panel.post(REFRESH, follow=True)

    assert response.status_code == 200
    assert services.aggregate(world)["total"] == stale["total"] + 3
    assert page_cache._versions(world.pk) != before
    assert "Mapa odświeżona – stan na" in response.content.decode()
    entry = AuditLog.objects.get(action="participant_map.refreshed")
    assert entry.competition_id == world.pk and entry.diff["total"] == 16


def test_panel_shows_the_last_computation_and_unmapped_towns(panel, world):
    html = panel.get(PANEL).content.decode()

    assert "Ostatnie przeliczenie" in html and "Gorlice" in html
    assert "Odśwież mapę uczestników" in html
    assert "Pcim" not in html


def test_settings_enable_the_page_and_raise_the_threshold(panel, client_for, world):
    response = panel.post(PANEL, {"enabled": "on", "min_town_count": "4"})

    assert response.status_code == 302
    world.refresh_from_db()
    assert world.has_feature(services.MAP_FLAG)
    assert MapSettings.objects.get(competition=world).min_town_count == 4
    html = client_for(world).get(URL).content.decode()
    assert "Gdynia" not in html and "Kraków" in html
    entry = AuditLog.objects.get(action="participant_map.settings_changed")
    assert entry.diff == {"enabled": [False, True], "min_count": [3, 4]}


def test_threshold_below_three_is_refused(panel, world):
    response = panel.post(PANEL, {"enabled": "on", "min_town_count": "2"})

    assert response.status_code == 400
    world.refresh_from_db()
    assert not world.has_feature(services.MAP_FLAG)
    assert not MapSettings.objects.exists()


@pytest.mark.parametrize("url", [PANEL, REFRESH])
def test_panel_is_for_the_coordinator_only(client_for, world, url):
    client = client_for(world)
    assert client.post(url).status_code == 302  # niezalogowany → logowanie
    participant = ParticipantFactory(competition=world)
    client.force_login(participant.user)
    assert client.post(url).status_code == 403
    assert not AuditLog.objects.filter(action__startswith="participant_map.").exists()


def test_service_checks_the_role_itself(world):
    from django.core.exceptions import PermissionDenied

    stranger = ParticipantFactory(competition=world).user
    with pytest.raises(PermissionDenied):
        services.refresh(world, stranger)
    with pytest.raises(PermissionDenied):
        services.save_settings(world, None, is_enabled=True, threshold=3)


def test_refresh_is_throttled(panel, world, settings):
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], "participant_map": "1/hour"}
    with override_settings(REST_FRAMEWORK=config):
        assert panel.post(REFRESH).status_code == 302
        assert panel.post(REFRESH).status_code == 429
    assert AuditLog.objects.filter(action="participant_map.refreshed").count() == 1


def test_panel_menu_has_the_screen(panel, world):
    html = panel.get("/coordinator/").content.decode()
    assert f'href="{reverse("web:coordinator-participant-map")}"' in html


# --- rejestr czynności, punkty miejscowości, geometria ------------------------------------------------


def test_processing_register_row_follows_the_flag(world):
    assert "mapa-uczestnikow" not in {activity.key for activity in activities_for(world)}
    enable(world)
    assert "mapa-uczestnikow" in {activity.key for activity in activities_for(world)}


def test_seed_town_locations_mirrors_the_fixture(tmp_path, points):
    fixture = tmp_path / "punkty.json"
    fixture.write_text(
        json.dumps(
            {"towns": [["Kraków", "malopolskie", 50.06, 19.94], ["Gorlice", "malopolskie", 49.655, 21.159]]}
        ),
        encoding="utf-8",
    )

    call_command("seed_town_locations", fixture=fixture)

    assert sorted(TownLocation.objects.values_list("city_parent", "latitude")) == [
        ("Gorlice", 49.655),
        ("Kraków", 50.06),
    ]


@pytest.mark.parametrize(
    "row",
    [
        ["Kraków", "krakowskie", 50.06, 19.94],
        ["Kraków", "malopolskie", 19.94, 50.06],
        ["", "malopolskie", 50.0, 20.0],
    ],
)
def test_seed_town_locations_refuses_a_bad_file(tmp_path, row):
    fixture = tmp_path / "punkty.json"
    fixture.write_text(json.dumps({"towns": [row]}), encoding="utf-8")

    with pytest.raises(CommandError):
        call_command("seed_town_locations", fixture=fixture)
    assert not TownLocation.objects.exists()


def test_bundled_fixture_points_lie_in_their_voivodeships():
    from apps.schools.management.commands.seed_town_locations import DEFAULT_FIXTURE, load_fixture

    rows = load_fixture(DEFAULT_FIXTURE)
    assert len(rows) > 1000
    outside = [(c, v) for c, v, lat, lon in rows if not geometry.contains(v, lon, lat)]
    assert outside == []


def test_geometry_knows_where_krakow_is():
    assert geometry.contains("malopolskie", 19.9369, 50.0619)
    assert not geometry.contains("pomorskie", 19.9369, 50.0619)
    assert len(geometry.voivodeship_paths()) == 16


# --- tłumaczenia ----------------------------------------------------------------------------------


def _template_msgids(path) -> set[str]:
    """Napisy do tłumaczenia z szablonu – ta sama ekstrakcja, której używa ``makemessages``."""
    import ast

    from django.utils.translation.template import templatize

    code = templatize(path.read_text(encoding="utf-8"), origin=str(path))
    # ``templatize`` zamienia resztę szablonu na „XXXX”, a napisy – na wywołania ``gettext(u'…')``.
    return {ast.literal_eval(match) for match in re.findall(r"gettext\(u?('(?:[^'\\]|\\.)*')\)", code)}


@pytest.mark.parametrize("language", ["en", "zh-hans", "hi", "es", "ar", "fr", "bn", "pt", "ru", "id"])
def test_every_string_of_the_public_page_is_translated(language):
    from pathlib import Path

    from django.utils import translation

    root = Path(__file__).resolve().parents[1]
    templates = [
        root / "templates/web/participant_map/page.html",
        root.parents[1] / "templates/cms/document_index_page.html",
    ]
    msgids = set().union(*(_template_msgids(path) for path in templates)) | {"Mapa uczestników"}
    msgids -= {"Dokumenty", "Szukaj", "Czytaj"}  # z katalogu głównego
    # Wpis w katalogu, a nie „napis różny od źródła”: „Mapa” po hiszpańsku to też „Mapa”.
    catalog = translation.trans_real.translation(translation.to_locale(language))._catalog
    untranslated = sorted(m for m in msgids if not catalog.get(m))
    assert untranslated == []
