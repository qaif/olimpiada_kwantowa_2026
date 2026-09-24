"""Własne drzewo stron CMS konkursu adresowanego prefiksem ścieżki (uwaga T43).

Przed tą zmianą konkurs w trybie ``PATH`` miał własną witrynę i własne strony (zakładała je
``create_competition``), ale pod ``/<prefiks>/`` serwowało się drzewo **gospodarza**: Wagtail
wybiera witrynę po hoście, a host był hostem platformy. Ten plik sprawdza całą drogę: serwowanie
z drzewa konkursu, adresy stron (menu, ``pageurl``, ``full_url``, podgląd w panelu), izolację
w obie strony, przekierowania, pamięć stron, logowanie i CSRF pod prefiksem – oraz to, że konkurs
z własną domeną niczego z tego nie zauważa.
"""

from __future__ import annotations

import importlib
import re

import pytest
from django.apps import apps as django_apps
from django.test import Client, RequestFactory
from django.urls import set_script_prefix
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Page

from apps.cms.models import ContentPage, HomePage, SiteSettings
from apps.tenancy import page_urls
from apps.tenancy.models import Competition, RoutingMode
from apps.tenancy.provisioning import create_competition_from_template
from apps.tenancy.templates_catalog import TEMPLATE_PUSTY

from .conftest import HOST_A

pytestmark = pytest.mark.django_db

PREFIX = "druga"
SECOND_DOMAIN = "druga.test"


@pytest.fixture
def second(competition):
    """Drugi konkurs założony czynnością ``create_competition`` w trybie prefiksu ścieżki."""
    result = create_competition_from_template(
        slug="druga",
        name="Olimpiada Druga",
        domain=SECOND_DOMAIN,
        template=TEMPLATE_PUSTY,
        path_prefix=PREFIX,
    )
    return result.competition


def home_of(competition) -> HomePage:
    return HomePage.objects.get(pk=competition.site.root_page_id)


def add_page(competition, slug: str, title: str) -> ContentPage:
    """Strona drugiego poziomu w drzewie konkursu – ta sama droga, którą idzie redaktor w ``/cms/``."""
    page = ContentPage(title=title, slug=slug, show_in_menu=True)
    home_of(competition).add_child(instance=page)
    return page


@pytest.fixture
def pages(competition, second):
    """Po jednej stronie, która istnieje wyłącznie w drzewie jednego z konkursów."""
    return {
        "first": add_page(competition, "tylko-kwantowa", "Strona tylko Kwantowej"),
        "second": add_page(second, "tylko-druga", "Strona tylko Drugiej"),
    }


@pytest.fixture
def platform(client_for, competition):
    """Klient pod hostem platformy (Konkursu #1) – tym samym, pod którym stoi konkurs z prefiksem."""
    return client_for(competition)


# --- create_competition ---------------------------------------------------------------------------


def test_create_competition_builds_an_own_tree_and_opens_the_platform(competition, second):
    """Konkurs z ``--path-prefix``: własna witryna, własna strona główna i otwarta bramka gospodarza."""
    competition.refresh_from_db()

    assert second.routing_mode == RoutingMode.PATH
    assert second.site.root_page_id != competition.site.root_page_id
    home = home_of(second)
    assert home.title == "Olimpiada Druga"
    assert set(home.get_children().values_list("slug", flat=True)) >= {"aktualnosci", "zadania", "wyniki"}
    assert competition.has_feature("path_prefix_routing") is True


def test_create_competition_reports_the_opened_gate_once(competition, second):
    """Drugi konkurs pod prefiksem nie „włącza” bramki drugi raz – wynik mówi prawdę o stanie."""
    result = create_competition_from_template(
        slug="trzecia",
        name="Olimpiada Trzecia",
        domain="trzecia.test",
        template=TEMPLATE_PUSTY,
        path_prefix="trzecia",
    )

    assert result.platform == competition
    assert result.opened_platform is False


def test_create_competition_with_a_domain_leaves_the_platform_alone(competition):
    result = create_competition_from_template(
        slug="domenowa", name="Olimpiada Domenowa", domain="domenowa.test", template=TEMPLATE_PUSTY
    )
    competition.refresh_from_db()

    assert result.platform is None
    assert competition.has_feature("path_prefix_routing") is False


def test_dry_run_does_not_open_the_platform(competition):
    create_competition_from_template(
        slug="sucha",
        name="Olimpiada Sucha",
        domain="sucha.test",
        template=TEMPLATE_PUSTY,
        path_prefix="sucha",
        dry_run=True,
    )
    competition.refresh_from_db()

    assert competition.has_feature("path_prefix_routing") is False


# --- serwowanie i izolacja ------------------------------------------------------------------------


def test_prefix_serves_the_home_page_of_its_own_tree(platform, second, competition):
    response = platform.get(f"/{PREFIX}/")

    assert response.status_code == 200
    assert response.context["page"].pk == second.site.root_page_id
    assert response.context["request"].competition == second


def test_platform_root_still_serves_its_own_home_page(platform, second, competition):
    response = platform.get("/")

    assert response.status_code == 200
    assert response.context["page"].pk == competition.site.root_page_id


def test_page_of_the_prefixed_tree_answers_under_the_prefix_only(platform, pages):
    assert platform.get(f"/{PREFIX}/tylko-druga/").status_code == 200
    assert platform.get("/tylko-druga/").status_code == 404


def test_page_of_the_platform_is_404_under_the_prefix(platform, pages):
    """Strona konkursu A **nie** otwiera się pod prefiksem konkursu B – żadnego odwrotu na drzewo hosta."""
    assert platform.get("/tylko-kwantowa/").status_code == 200
    assert platform.get(f"/{PREFIX}/tylko-kwantowa/").status_code == 404


def test_closed_gate_serves_nothing_of_the_prefixed_competition(platform, pages, competition):
    """Bramka zamknięta: ``/druga/…`` jest zwykłym adresem drzewa gospodarza – czyli 404."""
    competition.feature_flags = {}
    competition.save(update_fields=["feature_flags"])

    assert platform.get(f"/{PREFIX}/tylko-druga/").status_code == 404
    assert platform.get(f"/{PREFIX}/").status_code == 404


def test_site_settings_under_the_prefix_are_those_of_the_prefixed_site(platform, second):
    row = SiteSettings.for_site(second.site)
    row.site_name = "Serwis Drugiej"
    row.save()

    body = platform.get(f"/{PREFIX}/").content.decode()

    assert "Serwis Drugiej" in body


# --- adresy stron ---------------------------------------------------------------------------------


def hrefs(body: str) -> set[str]:
    return set(re.findall(r'href="([^"]+)"', body))


def test_menu_and_links_under_the_prefix_carry_the_prefix(platform, pages):
    body = platform.get(f"/{PREFIX}/").content.decode()
    links = hrefs(body)

    assert f"/{PREFIX}/tylko-druga/" in links
    assert f"/{PREFIX}/" in links  # logo i domek w menu
    assert f"/{PREFIX}/dokumenty/rodo/" in links
    assert "/tylko-kwantowa/" not in links
    # Menu gospodarza nie przecieka do konkursu pod prefiksem.
    assert not any(link.startswith("/tylko-") for link in links)


def test_platform_menu_has_no_page_of_the_prefixed_tree(platform, pages):
    links = hrefs(platform.get("/").content.decode())

    assert "/tylko-kwantowa/" in links
    assert not any("tylko-druga" in link for link in links)
    assert not any(link.startswith(f"/{PREFIX}/") for link in links)


def test_page_url_without_a_request_points_under_the_platform_prefix(pages):
    page = pages["second"]

    assert page.full_url == f"http://{HOST_A}/{PREFIX}/tylko-druga/"
    assert page.get_url() == f"http://{HOST_A}/{PREFIX}/tylko-druga/"


def test_platform_page_rendered_inside_a_prefixed_request_drops_the_foreign_prefix(pages):
    set_script_prefix(f"/{PREFIX}/")
    try:
        url = pages["first"].get_url()
    finally:
        set_script_prefix("/")

    assert url == f"http://{HOST_A}/tylko-kwantowa/"


def test_platform_request_links_to_the_prefixed_tree_absolutely(pages, competition):
    request = RequestFactory().get("/", HTTP_HOST=HOST_A)
    request._wagtail_site = competition.site

    assert pages["second"].get_url(request=request) == f"http://{HOST_A}/{PREFIX}/tylko-druga/"
    assert pages["first"].get_url(request=request) == "/tylko-kwantowa/"


def test_own_domain_of_the_prefixed_competition_keeps_plain_addresses(pages, second, settings, client_for):
    """Konkurs, któremu zadziałał DNS, odpowiada pod własną domeną bez prefiksu – i tak linkuje."""
    client = Client(HTTP_HOST=SECOND_DOMAIN, SERVER_NAME=SECOND_DOMAIN)

    response = client.get("/tylko-druga/")

    assert response.status_code == 200
    assert "/tylko-druga/" in hrefs(response.content.decode())
    assert not any(link.startswith(f"/{PREFIX}/") for link in hrefs(response.content.decode()))


def test_single_site_installation_never_reads_the_prefix_map(competition, django_assert_num_queries):
    """Konkurs #1 sam: adres strony bez żądania nie kosztuje zapytania ani odczytu mapy."""
    page = HomePage.objects.get(pk=competition.site.root_page_id)
    page.get_url()  # ścieżki witryn Wagtaila – jego własny koszt, zapamiętany na stronie

    with django_assert_num_queries(0):
        assert page.get_url() == "/"
    assert not hasattr(page, page_urls.MEMO_ATTRIBUTE)


def test_prefix_map_is_invalidated_when_a_competition_changes(second):
    assert page_urls.prefix_sites()["prefixes"] == {second.site_id: PREFIX}

    second.routing_mode = RoutingMode.DOMAIN
    second.save()

    assert page_urls.prefix_sites()["prefixes"] == {}


# --- podgląd w panelu -----------------------------------------------------------------------------


def test_admin_preview_of_a_prefixed_page_renders_its_own_competition(pages, second):
    """Podgląd idzie pod ``full_url`` strony, czyli pod prefiks – warstwa konkursu rozstrzyga go tak samo."""
    response = pages["second"].make_preview_request()

    assert response.status_code == 200
    body = response.content.decode()
    assert "Strona tylko Drugiej" in body
    assert f'href="/{PREFIX}/"' in body


# --- przekierowania -------------------------------------------------------------------------------


def test_redirect_of_the_prefixed_site_matches_without_the_prefix_and_stays_inside(platform, pages, second):
    Redirect.objects.create(site=second.site, old_path="/stary", redirect_link="/tylko-druga/")

    response = platform.get(f"/{PREFIX}/stary")

    assert response.status_code == 301
    assert response["Location"] == f"/{PREFIX}/tylko-druga/"


def test_redirect_to_a_page_of_the_prefixed_tree(platform, pages, second):
    Redirect.objects.create(site=second.site, old_path="/do-strony", redirect_page=pages["second"])

    response = platform.get(f"/{PREFIX}/do-strony/")

    assert response.status_code == 301
    assert response["Location"].endswith(f"/{PREFIX}/tylko-druga/")


def test_redirect_of_the_platform_does_not_fire_under_the_prefix(platform, pages, competition):
    Redirect.objects.create(site=competition.site, old_path="/stary-k", redirect_link="/tylko-kwantowa/")

    assert platform.get("/stary-k")["Location"] == "/tylko-kwantowa/"
    # Pod prefiksem przekierowanie gospodarza nie istnieje: adres bez ukośnika dostaje zwykłe
    # dopisanie ukośnika (``APPEND_SLASH``, wciąż pod prefiksem), a z ukośnikiem – 404.
    assert platform.get(f"/{PREFIX}/stary-k")["Location"] == f"/{PREFIX}/stary-k/"
    assert platform.get(f"/{PREFIX}/stary-k/").status_code == 404


def test_external_redirect_link_is_left_alone(platform, second):
    Redirect.objects.create(site=second.site, old_path="/zewn", redirect_link="https://example.org/x")

    assert platform.get(f"/{PREFIX}/zewn")["Location"] == "https://example.org/x"


# --- logowanie, CSRF, ciasteczka ------------------------------------------------------------------


def test_login_redirect_stays_under_the_prefix(platform, second):
    response = platform.get(f"/{PREFIX}/me/")

    assert response.status_code == 302
    assert response["Location"] == f"/{PREFIX}/login/?next=/{PREFIX}/me/"


def test_login_redirect_of_the_platform_is_unchanged(platform, second):
    assert platform.get("/me/")["Location"] == "/login/?next=/me/"


def test_csrf_protected_post_works_under_the_prefix_and_cookies_stay_host_wide(competition, second, settings):
    """Formularz pod prefiksem wysyła token, a ciasteczka stoją na ścieżce ``/`` – jak cały host (§ 2.3)."""
    client = Client(HTTP_HOST=HOST_A, SERVER_NAME=HOST_A, enforce_csrf_checks=True)
    page = client.get(f"/{PREFIX}/login/")
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)

    # Formularz logowania nie ma ``action`` – wysyła się pod adres, z którego przyszedł, czyli pod prefiks.
    assert '<form method="post" class="form">' in page.content.decode()
    assert client.cookies[settings.CSRF_COOKIE_NAME]["path"] == "/"

    refused = Client(HTTP_HOST=HOST_A, enforce_csrf_checks=True).post(
        f"/{PREFIX}/login/", {"username": "x@example.test", "password": "zle"}
    )
    accepted = client.post(
        f"/{PREFIX}/login/",
        {"username": "x@example.test", "password": "zle", "csrfmiddlewaretoken": token},
    )

    assert refused.status_code == 403
    assert accepted.status_code == 200  # formularz z błędem logowania, a nie odmowa CSRF


# --- pamięć stron i pasek harmonogramu ------------------------------------------------------------


def test_page_cache_key_carries_the_prefix_only_under_a_prefix(competition, second):
    from apps.web.page_cache import build_key

    plain = RequestFactory().get("/")
    plain.competition = second
    prefixed = RequestFactory().get(f"/{PREFIX}/")
    prefixed.path_info = "/"
    prefixed.competition = second
    prefixed.competition_script_prefix = f"/{PREFIX}"

    assert build_key(plain) != build_key(prefixed)
    assert build_key(prefixed).endswith(f":prefix=/{PREFIX}")
    assert str(second.pk) in build_key(prefixed).split(":")


def test_timeline_cache_key_is_unchanged_without_a_prefix():
    from apps.cms.timeline import _cache_key

    assert _cache_key(7, 3) == "cms:timeline-strip:3:7"
    assert _cache_key(7, 3, f"/{PREFIX}/") == f"cms:timeline-strip:3:7:/{PREFIX}/"


# --- poczta ---------------------------------------------------------------------------------------


def test_mail_link_of_a_prefixed_competition_points_under_the_platform(second, competition):
    from apps.accounts.activation import absolute_url

    assert absolute_url("/me/", competition=second) == f"https://{HOST_A}/{PREFIX}/me/"
    assert absolute_url("/me/", competition=competition) == f"https://{HOST_A}/me/"


# --- migracja danych ------------------------------------------------------------------------------


def test_data_migration_opens_the_platform_only_when_a_prefixed_competition_exists(
    competition, other_competition
):
    migration = importlib.import_module("apps.tenancy.migrations.0010_path_prefix_routing_on_platform")

    migration.open_platform(django_apps, None)
    competition.refresh_from_db()
    assert competition.feature_flags.get("path_prefix_routing") is None

    Competition.objects.filter(pk=other_competition.pk).update(
        routing_mode=RoutingMode.PATH, path_prefix="inny"
    )
    migration.open_platform(django_apps, None)
    migration.open_platform(django_apps, None)  # idempotentnie
    competition.refresh_from_db()
    assert competition.feature_flags == {"path_prefix_routing": True}


def test_prefixed_tree_is_a_separate_subtree(second, competition):
    """Drzewa nie zachodzą na siebie – inaczej izolacja serwowania byłaby przypadkiem kolejności."""
    first_root = Page.objects.get(pk=competition.site.root_page_id)
    second_root = Page.objects.get(pk=second.site.root_page_id)

    assert not second_root.is_descendant_of(first_root)
    assert not first_root.is_descendant_of(second_root)


# --- superkoordynator i /cms/ per konkurs (wydanie 0.36.0: prefiks ścieżki + zawężenie /cms/) ----


@pytest.fixture
def super_user():
    from apps.accounts import super_coordinator
    from apps.accounts.tests.factories import UserFactory

    user = UserFactory(email="super-prefiks@example.test")
    super_coordinator.grant(user)
    return user


def test_switcher_links_the_prefixed_competition_under_the_platform_host(
    competition, other_competition, second, client_for, super_user
):
    """Konkurs pod prefiksem odpowiada wyłącznie pod hostem platformy – także z domeny innego konkursu.

    Względne ``/druga/coordinator/`` pod domeną konkursu B trafiłoby w host, który prefiksu nie
    rozstrzyga; ``primary_domain`` konkursu pod prefiksem to domena, na którą dopiero czeka.
    """
    expected = f'href="http://{HOST_A}/{PREFIX}/coordinator/"'
    for client, path in (
        (client_for(other_competition), "/coordinator/"),
        (client_for(competition), f"/{PREFIX}/coordinator/"),
        (client_for(competition), "/coordinator/"),
    ):
        client.force_login(super_user)
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.content.decode()
        assert expected in body, path
        assert f'href="/{PREFIX}/coordinator/"' not in body
        assert f"{SECOND_DOMAIN}/coordinator/" not in body


def test_switcher_skips_the_prefixed_competition_behind_a_closed_gate(competition, second, client_for, super_user):
    competition.feature_flags = {**competition.feature_flags, "path_prefix_routing": False}
    competition.save(update_fields=["feature_flags"])
    client = client_for(competition)
    client.force_login(super_user)

    body = client.get("/coordinator/").content.decode()

    assert "Konkursy platformy" in body
    assert f"/{PREFIX}/coordinator/" not in body
    assert f"{SECOND_DOMAIN}/coordinator/" not in body


def test_coordinator_of_the_prefixed_competition_is_scoped_to_its_own_site_root(competition, second):
    """Grupa ``cms:<slug>`` konkursu pod prefiksem ma prawa na korzeniu **jego** witryny, nie platformy."""
    from wagtail.models import GroupPagePermission

    from apps.cms.permissions import ensure_cms_group

    group = ensure_cms_group(second)
    page_ids = set(GroupPagePermission.objects.filter(group=group).values_list("page_id", flat=True))

    assert page_ids == {second.site.root_page_id}
    assert competition.site.root_page_id not in page_ids
