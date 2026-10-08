"""Wewnętrzne API v2 dla serwisu na django CMS (``/internal/djcms/v2/…``, DJ-02 § 4).

Grupy testów, w kolejności wagi:

1. **bramki (S2)** – te same co w DJ-01: host publiczny (także z dobrym tokenem), brak/zły/krótki token,
   metoda inna niż ``GET``, zły kształt sluga, nieznany adres → ta sama pusta 404. Dopiero **po**
   bramkach nieistniejący/nieaktywny konkurs → JSON 404 ``no-competition``,
2. **brak przecieku między konkursami (S1)** – dwa konkursy z unikalnymi napisami: żadna odpowiedź
   ``c/A/*`` (także paczka) nie zawiera napisu konkursu B i odwrotnie,
3. **lista ``competitions``** – hosty, tryby, adresy publiczne, konkursy nieaktywne, ``linked_paths``,
   ``fingerprint``, brak danych osobowych,
4. **nowe dane** – ``partners``, pola parytetu w ``chrome`` v2, pełna tabela warsztatów.

Reguły jawności z DJ-01 (zadania po ``opens_at``, wyniki z białej listy snapshotu) i kształt
poszczególnych endpointów sprawdza ``test_djcms_api.py``; tutaj – to, co dotyczy wielu konkursów.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import resolve
from django.utils import timezone
from PIL import Image as PILImage
from wagtail.images import get_image_model
from wagtail.models import Page, PageViewRestriction

from apps.cms.djcms_api.competitions import APP_LITERAL_PAGE_PATHS, fingerprint
from apps.cms.djcms_api.views import DEFAULT_DESCRIPTION
from apps.cms.models import ContentPage, DocumentPage, HomePage, PartnersPage, SiteSettings
from apps.cms.tests.factories import AnnouncementFactory
from apps.competitions.tests.factories import CurrentEditionFactory, ProblemFactory, StageFactory
from apps.results.models import Anonymization, ResultsPublication
from apps.tenancy.models import Competition, RoutingMode
from conftest import make_competition

pytestmark = pytest.mark.django_db

TOKEN = "t" * 40
BASE = "/internal/djcms/v2/"
MAIN = "https://olimpiada.example.test"
JSON_ENDPOINTS = ("chrome", "stages", "problems", "results", "editions", "workshops", "partners")


@pytest.fixture(autouse=True)
def _api_settings(settings):
    settings.DJCMS_INTERNAL_TOKEN = TOKEN
    settings.DJCMS_MAIN_PUBLIC_URL = MAIN


def internal(token: str | None = TOKEN, host: str = "web:8000") -> Client:
    headers = {"HTTP_HOST": host, "SERVER_NAME": host.partition(":")[0]}
    if token is not None:
        headers["HTTP_X_DJCMS_TOKEN"] = token
    return Client(**headers)


def get_json(path: str) -> dict:
    response = internal().get(BASE + path)
    assert response.status_code == 200, (path, response.status_code, response.content[:200])
    return response.json()


def png(size=(40, 10)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def make_image(title: str, size=(40, 10)):
    return get_image_model().objects.create(title=title, file=SimpleUploadedFile(f"{title}.png", png(size)))


def publish(stage, snapshot) -> ResultsPublication:
    stage.results_published_at = timezone.now()
    stage.save(update_fields=["results_published_at"])
    return ResultsPublication.objects.create(stage=stage, anonymization=Anonymization.CODE, snapshot=snapshot)


def competition_entry(payload: dict, slug: str) -> dict:
    return next(entry for entry in payload["competitions"] if entry["slug"] == slug)


# --- bramki (S2) ----------------------------------------------------------------------------------


GATE_FAILURES = [
    pytest.param(TOKEN, {"token": None}, id="brak-naglowka"),
    pytest.param(TOKEN, {"token": "z" * 40}, id="zly-token"),
    pytest.param("k" * 31, {"token": "k" * 31}, id="token-krotszy-niz-32"),
    pytest.param("", {"token": ""}, id="pusty-token"),
    pytest.param(TOKEN, {"host": "testserver"}, id="host-publiczny-z-tokenem"),
]


@pytest.mark.parametrize(("setting", "client_kwargs"), GATE_FAILURES)
@pytest.mark.parametrize(
    "path",
    [
        "competitions",
        *(f"c/kwantowa/{endpoint}" for endpoint in (*JSON_ENDPOINTS, "export", "editions/1/results")),
        "c/nie-ma-takiego/chrome",
    ],
)
def test_every_gate_failure_is_an_empty_404(settings, competition, setting, client_kwargs, path):  # noqa: ARG001
    settings.DJCMS_INTERNAL_TOKEN = setting

    response = internal(**client_kwargs).get(BASE + path)

    assert response.status_code == 404
    assert response.content == b""


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
@pytest.mark.parametrize("path", ["competitions", "c/kwantowa/chrome"])
def test_other_methods_are_404_even_with_a_valid_token(competition, method, path):  # noqa: ARG001
    response = getattr(internal(), method)(BASE + path)

    assert response.status_code == 404
    assert response.content == b""


@pytest.mark.parametrize(
    "path",
    [
        "c/Kwantowa/chrome",
        "c/kwan_towa/chrome",
        "c/kwan.towa/chrome",
        f"c/{'a' * 51}/chrome",
        "c//chrome",
        "c/kwantowa/nie-ma",
        "c/kwantowa/chrome/",
        "c/kwantowa",
        "c/kwantowa/",
        "c/kwantowa/editions/abc/results",
        "competitions/",
        "chrome",
        "nie-ma",
        "",
    ],
)
def test_bad_slug_shapes_and_unknown_paths_are_the_same_empty_404(competition, path):  # noqa: ARG001
    for method in ("get", "post", "head"):
        response = getattr(internal(), method)(BASE + path)
        assert response.status_code == 404, (method, path)
        assert response.content == b"", (method, path)
        assert "Location" not in response


def test_the_v2_branch_without_a_slash_is_an_empty_404(competition):  # noqa: ARG001
    response = internal().get("/internal/djcms/v2")

    assert response.status_code == 404
    assert response.content == b""


@pytest.mark.parametrize("state", ["nie-istnieje", "nieaktywny"])
def test_unknown_or_inactive_competition_after_the_gates_is_a_json_404(competition, other_competition, state):  # noqa: ARG001
    slug = "nie-ma-takiego"
    if state == "nieaktywny":
        Competition.objects.filter(pk=other_competition.pk).update(is_active=False)
        slug = other_competition.slug

    for endpoint in (*JSON_ENDPOINTS, "export"):
        response = internal().get(f"{BASE}c/{slug}/{endpoint}")
        assert response.status_code == 404, endpoint
        assert response.json() == {"api_version": 2, "error": "no-competition"}
        assert response["Cache-Control"] == "no-store"


def test_competitions_is_versioned_json(competition):  # noqa: ARG001
    response = internal().get(BASE + "competitions")

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.json()["api_version"] == 2


@pytest.mark.parametrize("endpoint", ["chrome", "stages", "export", "partners", "nie-ma"])
def test_the_removed_v1_is_an_empty_404(competition, endpoint):  # noqa: ARG001
    """API v1 usunięte w DJ-02k: poprawny token i host wewnętrzny, a odpowiedź jak zamknięta bramka."""
    response = internal().get(f"/internal/djcms/v1/{endpoint}")

    assert response.status_code == 404
    assert response.content == b""
    assert "Location" not in response


# --- brak przecieku między konkursami (S1) ------------------------------------------------------


def _world_of(competition, tag: str, home) -> dict:
    """Konkurs z napisami, które występują **wyłącznie** w nim (``tag``)."""
    edition = CurrentEditionFactory(competition=competition, year_label=f"Edycja-{tag}")
    stage = StageFactory(
        edition=edition,
        name=f"Etap-{tag}",
        location=f"Miasto-{tag}",
        opens_at=timezone.now() - timedelta(hours=1),
    )
    ProblemFactory(stage=stage, number=1, title=f"Zadanie-{tag}")
    publish(stage, [{"rank": 1, "display": f"KOD-{tag}", "points": {"1": 3}, "total": 3}])
    AnnouncementFactory(competition=competition, text=f"Komunikat-{tag}")
    settings_row = SiteSettings.for_site(competition.site)
    settings_row.site_name = f"Serwis-{tag}"
    settings_row.organizer_name = f"Organizator-{tag}"
    settings_row.save()
    workshops = home.add_child(
        instance=ContentPage(
            title=f"Warsztaty-{tag}",
            slug="warsztaty",
            intro=f"<p>Wstęp-{tag}</p>",
            body=[
                (
                    "schedule",
                    {
                        "caption": f"Plan-{tag}",
                        "rows": [
                            {
                                "topic": f"Temat-{tag}",
                                "date": "wkrótce",
                                "date_value": timezone.localdate() + timedelta(days=5),
                                "lecturer": f"Prowadzący-{tag}",
                            }
                        ],
                    },
                )
            ],
        )
    )
    home.add_child(
        instance=PartnersPage(
            title=f"Partnerzy-{tag}",
            slug="partnerzy",
            partners=[("partner", {"name": f"Partner-{tag}", "level": "partner-naukowy", "url": ""})],
        )
    )
    home.add_child(instance=ContentPage(title=f"Strona-{tag}", slug=f"strona-{tag.lower()}"))
    return {"edition": edition, "workshops": workshops}


def _bodies(slug: str, edition_id: int) -> dict[str, str]:
    bodies = {
        endpoint: internal().get(f"{BASE}c/{slug}/{endpoint}").content.decode() for endpoint in JSON_ENDPOINTS
    }
    bodies["edition_results"] = (
        internal().get(f"{BASE}c/{slug}/editions/{edition_id}/results").content.decode()
    )
    response = internal().get(f"{BASE}c/{slug}/export")
    archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
    bodies["export"] = archive.read("manifest.json").decode()
    return bodies


def test_no_endpoint_of_one_competition_contains_anything_of_the_other(competition, other_competition):
    home_a = HomePage.objects.get(pk=competition.site.root_page_id)
    world_a = _world_of(competition, "ALFA", home_a)
    world_b = _world_of(other_competition, "BETA", other_competition.site.root_page)

    bodies_a = _bodies(competition.slug, world_a["edition"].pk)
    bodies_b = _bodies(other_competition.slug, world_b["edition"].pk)

    # Każdy konkurs widzi swoje napisy – inaczej test przechodziłby na pustych odpowiedziach.
    assert "Etap-ALFA" in bodies_a["stages"] and "Etap-BETA" in bodies_b["stages"]
    assert "Zadanie-ALFA" in bodies_a["problems"] and "KOD-BETA" in bodies_b["results"]
    assert "Partner-ALFA" in bodies_a["partners"] and "Temat-BETA" in bodies_b["workshops"]
    assert "Strona-BETA" in bodies_b["export"]
    for name, body in bodies_a.items():
        assert "BETA" not in body, name
    for name, body in bodies_b.items():
        assert "ALFA" not in body, name


def test_an_edition_of_another_competition_is_empty_in_v2(competition, other_competition):
    edition = CurrentEditionFactory(competition=other_competition)
    publish(StageFactory(edition=edition), [])

    payload = get_json(f"c/{competition.slug}/editions/{edition.pk}/results")

    assert payload == {**payload, "edition": None, "links": []}


def test_problems_stay_hidden_before_opening_in_v2(competition):
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition, opens_at=timezone.now() + timedelta(days=1))
    ProblemFactory(stage=stage, title="Zadanie pod embargiem", statement_pdf="statements/x.pdf")

    response = internal().get(f"{BASE}c/{competition.slug}/problems")

    assert response.json()["problems"] == []
    assert "Zadanie pod embargiem" not in response.content.decode()


# --- competitions -------------------------------------------------------------------------------


def test_competitions_describe_domain_subdomain_path_and_inactive(settings, competition, other_competition):
    settings.SITE_DOMAIN = "olimpiada.example.test"
    settings.PLATFORM_SUBDOMAINS = True
    competition.feature_flags = {**(competition.feature_flags or {}), "path_prefix_routing": True}
    competition.save(update_fields=["feature_flags"])

    path_competition = make_competition(
        "druga.invalid", "druga", routing_mode=RoutingMode.PATH, path_prefix="druga"
    )
    sleeping = make_competition("uspiona.example.test", "uspiona", is_active=False)

    payload = get_json("competitions")

    assert payload["platform"] == {
        "site_domain": "olimpiada.example.test",
        "platform_subdomains": True,
        "default_slug": competition.slug,
    }
    default = competition_entry(payload, competition.slug)
    assert default["is_default"] is True
    assert default["hosts"] == sorted({competition.site.hostname, "olimpiada.example.test"})
    assert default["hosts_path_prefixes"] is True
    assert default["public_base"] == {"origin": MAIN, "path_prefix": ""}
    assert default["routing_mode"] == "DOMAIN" and default["path_prefix"] == ""

    other = competition_entry(payload, other_competition.slug)
    assert other["hosts"] == [other_competition.primary_domain]
    assert other["public_base"] == {
        "origin": f"https://{other_competition.primary_domain}",
        "path_prefix": "",
    }
    assert other["is_default"] is False and other["hosts_path_prefixes"] is False

    prefixed = competition_entry(payload, path_competition.slug)
    assert prefixed["routing_mode"] == "PATH"
    assert prefixed["path_prefix"] == "druga"
    # Witryna konkursu pod prefiksem ma własny host – resolve_for_request rozstrzyga go jak każdy.
    assert prefixed["hosts"] == ["druga.invalid"]
    assert prefixed["public_base"] == {"origin": MAIN, "path_prefix": "/druga"}

    inactive = competition_entry(payload, sleeping.slug)
    assert inactive["is_active"] is False
    assert inactive["hosts"] == ["uspiona.example.test"]


def test_path_competition_without_an_open_gate_has_no_public_base(competition):  # noqa: ARG001

    make_competition("druga.invalid", "druga", routing_mode=RoutingMode.PATH, path_prefix="druga")

    assert competition_entry(get_json("competitions"), "druga")["public_base"] is None


def test_hosts_are_disjoint_and_the_site_hostname_wins(competition, other_competition):
    # Rozjazd danych: konkurs B ma w ``primary_domain`` host witryny A – rozstrzyga witryna.
    Competition.objects.filter(pk=other_competition.pk).update(primary_domain=competition.site.hostname)

    payload = get_json("competitions")

    all_hosts = [host for entry in payload["competitions"] for host in entry["hosts"]]
    assert len(all_hosts) == len(set(all_hosts))
    assert competition.site.hostname in competition_entry(payload, competition.slug)["hosts"]
    assert competition_entry(payload, other_competition.slug)["hosts"] == [other_competition.site.hostname]


def test_an_extra_host_claimed_twice_goes_to_one_competition_only(competition, other_competition):  # noqa: ARG001
    Competition.objects.filter(pk=other_competition.pk).update(primary_domain="wspolna.example.test")
    make_competition("trzecia.example.test", "trzecia", primary_domain="wspolna.example.test")

    payload = get_json("competitions")

    all_hosts = [host for entry in payload["competitions"] for host in entry["hosts"]]
    assert all_hosts.count("wspolna.example.test") == 1
    assert len(all_hosts) == len(set(all_hosts))


def test_hosts_are_normalised(competition, other_competition):  # noqa: ARG001
    Competition.objects.filter(pk=other_competition.pk).update(primary_domain="Druga.Example.TEST.:8000")

    hosts = competition_entry(get_json("competitions"), other_competition.slug)["hosts"]

    assert "druga.example.test" in hosts
    assert all(host == host.lower() and ":" not in host and not host.endswith(".") for host in hosts)


def test_fingerprint_is_stable_and_follows_the_data(competition):
    first = competition_entry(get_json("competitions"), competition.slug)
    second = competition_entry(get_json("competitions"), competition.slug)
    assert first["fingerprint"] == second["fingerprint"] == fingerprint(first)
    assert re.fullmatch(r"[0-9a-f]{64}", first["fingerprint"])

    Competition.objects.filter(pk=competition.pk).update(name="Nowa nazwa")

    assert (
        competition_entry(get_json("competitions"), competition.slug)["fingerprint"] != first["fingerprint"]
    )


def test_fingerprint_ignores_key_order():
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1, "fingerprint": "x"})


def test_competitions_carry_no_personal_data_and_no_organizer_emails(competition, other_competition):  # noqa: ARG001
    Competition.objects.filter(pk=competition.pk).update(
        contact_email="kontakt@organizator.invalid", dpo_email="iod@organizator.invalid"
    )
    from apps.tenancy.tests.golden import MAIL_DOMAIN, build_golden

    build_golden(competition)

    body = internal().get(BASE + "competitions").content.decode()

    assert "@" not in body
    assert MAIL_DOMAIN not in body
    expected_keys = {
        "slug",
        "name",
        "short_name",
        "is_active",
        "is_default",
        "routing_mode",
        "path_prefix",
        "hosts",
        "hosts_path_prefixes",
        "public_base",
        "site_hostname",
        "has_site_aliases",
        "linked_paths",
        "fingerprint",
    }
    for entry in json.loads(body)["competitions"]:
        assert set(entry) == expected_keys


def test_linked_paths_follow_consent_documents_workshops_and_template_literals(competition):
    home = HomePage.objects.get(pk=competition.site.root_page_id)
    documents = home.add_child(instance=ContentPage(title="Pisma", slug="pisma"))
    documents.add_child(instance=DocumentPage(title="Regulamin", slug="regulamin"))
    home.add_child(instance=ContentPage(title="Warsztaty", slug="warsztaty"))

    paths = competition_entry(get_json("competitions"), competition.slug)["linked_paths"]

    # Dokument zgody stoi w drzewie gdzie indziej – liczy się jego prawdziwa ścieżka.
    assert "/pisma/regulamin/" in paths
    assert "/warsztaty/" in paths
    assert paths == sorted(set(paths))
    # Dokumentów bez strony (RODO, zgoda opiekuna) nie ma – odpowiadają 404 po obu stronach
    # przełączenia, więc nie ma czego porównywać (także literał ``/dokumenty/rodo/`` z szablonów).
    assert not [path for path in paths if path.startswith("/dokumenty/")]


def test_template_literals_are_listed_only_with_a_published_public_page(competition):
    """Konkurs z szablonu nie musi mieć ``/faq/`` ani ``/harmonogram/`` – bez strony nie blokują."""
    home = HomePage.objects.get(pk=competition.site.root_page_id)
    Page.objects.child_of(home).filter(slug__in=["faq", "harmonogram", "warsztaty"]).delete()

    paths = competition_entry(get_json("competitions"), competition.slug)["linked_paths"]
    assert not set(APP_LITERAL_PAGE_PATHS) & set(paths)

    home.add_child(instance=ContentPage(title="FAQ", slug="faq"))
    schedule = home.add_child(instance=ContentPage(title="Harmonogram", slug="harmonogram"))
    PageViewRestriction.objects.create(page=schedule, restriction_type=PageViewRestriction.LOGIN)
    paths = competition_entry(get_json("competitions"), competition.slug)["linked_paths"]
    assert "/faq/" in paths
    assert "/harmonogram/" not in paths  # z ograniczonym dostępem – eksport jej nie przenosi
    assert "/warsztaty/" not in paths


def test_consent_documents_are_listed_only_with_a_published_public_page(competition):
    home = HomePage.objects.get(pk=competition.site.root_page_id)
    documents = home.add_child(instance=ContentPage(title="Dokumenty", slug="dokumenty"))
    documents.add_child(instance=DocumentPage(title="Regulamin", slug="regulamin"))
    rodo = documents.add_child(instance=DocumentPage(title="RODO", slug="rodo"))
    guardian = documents.add_child(instance=DocumentPage(title="Zgoda", slug="zgoda-opiekuna"))
    guardian.unpublish()
    PageViewRestriction.objects.create(page=rodo, restriction_type=PageViewRestriction.LOGIN)

    paths = competition_entry(get_json("competitions"), competition.slug)["linked_paths"]

    assert "/dokumenty/regulamin/" in paths
    # Nieopublikowana i z ograniczonym dostępem – eksport ich nie przenosi, Wagtail anonimowi ich
    # nie pokazuje, więc parytet nie ma tu czego sprawdzać.
    assert "/dokumenty/zgoda-opiekuna/" not in paths
    assert "/dokumenty/rodo/" not in paths

    PageViewRestriction.objects.filter(page=rodo).delete()
    paths = competition_entry(get_json("competitions"), competition.slug)["linked_paths"]
    assert "/dokumenty/rodo/" in paths


def test_a_path_prefix_competition_does_not_inherit_template_literals(competition, other_competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "path_prefix_routing": True}
    competition.save(update_fields=["feature_flags"])
    Competition.objects.filter(pk=other_competition.pk).update(
        routing_mode=RoutingMode.PATH, path_prefix="druga"
    )

    paths = competition_entry(get_json("competitions"), other_competition.slug)["linked_paths"]

    assert "/faq/" not in paths and "/harmonogram/" not in paths
    # Bez strony dokumentu – bez wpisu (patrz test wyżej); ze stroną – jej ścieżka w drzewie konkursu.
    assert "/dokumenty/regulamin/" not in paths
    root = Page.objects.get(pk=other_competition.site.root_page_id)
    root.add_child(instance=ContentPage(title="Dokumenty", slug="dokumenty")).add_child(
        instance=DocumentPage(title="Regulamin", slug="regulamin")
    )
    paths = competition_entry(get_json("competitions"), other_competition.slug)["linked_paths"]
    assert "/dokumenty/regulamin/" in paths


def test_every_literal_page_link_in_app_templates_is_listed():
    """Nowy ``href="/…/"`` do strony redakcyjnej w szablonie aplikacji musi trafić do listy.

    Strona redakcyjna = ścieżka, którą urlconf oddaje catch-allowi Wagtaila. Szablony stron
    (``templates/cms/``) linkują przez drzewo, więc ich nie liczymy.
    """
    roots = [
        Path(django_settings.BASE_DIR) / "templates",
        *Path(django_settings.BASE_DIR).glob("apps/*/templates"),
    ]
    found = set()
    for root in roots:
        for template in root.rglob("*.html"):
            relative = template.relative_to(root).as_posix()
            if relative.startswith("cms/") or relative.startswith("wagtail"):
                continue
            for path in re.findall(r'href="(/[a-z0-9/_-]*)"', template.read_text(encoding="utf-8")):
                if resolve(path).url_name == "wagtail_serve" and path != "/":
                    found.add(path)
    assert found <= set(APP_LITERAL_PAGE_PATHS), sorted(found - set(APP_LITERAL_PAGE_PATHS))


def test_site_aliases_are_flagged(competition):
    from wagtail.models import Locale, Page, Site

    from apps.tenancy.aliases import CompetitionSiteAlias

    locale = Locale.objects.get_or_create(language_code="en")[0]
    root = (
        Page.objects.filter(depth=1)
        .first()
        .add_child(instance=Page(title="en", slug="en-root", locale=locale))
    )
    alias_site = Site.objects.create(hostname="en.example.test", port=80, root_page=root)
    CompetitionSiteAlias.objects.create(competition=competition, site=alias_site, locale=locale)

    assert competition_entry(get_json("competitions"), competition.slug)["has_site_aliases"] is True


# --- chrome v2 ------------------------------------------------------------------------------------


def test_chrome_v2_carries_brand_ga_and_seo(competition):
    Competition.objects.filter(pk=competition.pk).update(
        accent_colour="#1f6feb",
        short_name="OK",
        logo=make_image("logo", (600, 200)),
        favicon=make_image("ikona", (64, 64)),
    )
    settings_row = SiteSettings.for_site(competition.site)
    settings_row.ga_measurement_id = "G-TEST123"
    settings_row.save()

    payload = get_json(f"c/{competition.slug}/chrome")

    brand = payload["competition"]
    assert (brand["slug"], brand["short_name"], brand["accent_colour"]) == (competition.slug, "OK", "#1f6feb")
    assert brand["logo"]["width"] == 600 and brand["logo"]["src"]
    assert set(brand["favicon"]) == {"src"} and brand["favicon"]["src"]
    assert payload["site"]["ga_measurement_id"] == "G-TEST123"
    assert payload["seo"]["og_image"].startswith(f"{MAIN}/static/")
    assert "og-image" in payload["seo"]["og_image"]
    assert payload["seo"]["default_description"] == DEFAULT_DESCRIPTION


def test_chrome_v2_without_brand_images_says_null(competition):
    payload = get_json(f"c/{competition.slug}/chrome")

    assert payload["competition"]["logo"] is None
    assert payload["competition"]["favicon"] is None
    assert payload["site"]["ga_measurement_id"] == ""


def test_default_description_is_still_the_one_in_the_wagtail_base_template():
    base = (Path(django_settings.BASE_DIR) / "templates" / "base.html").read_text(encoding="utf-8")

    assert DEFAULT_DESCRIPTION in base
    assert "img/og-image.png" in base


def test_chrome_v2_of_another_competition_links_to_its_domain(competition, other_competition):  # noqa: ARG001
    payload = get_json(f"c/{other_competition.slug}/chrome")

    domain = f"https://{other_competition.primary_domain}"
    assert payload["links"]["login"] == f"{domain}/login/"
    assert payload["seo"]["og_image"].startswith(f"{domain}/static/")
    assert MAIN not in json.dumps(payload)


def test_chrome_v2_of_a_path_prefix_competition_keeps_static_files_without_the_prefix(
    competition, other_competition
):
    competition.feature_flags = {**(competition.feature_flags or {}), "path_prefix_routing": True}
    competition.save(update_fields=["feature_flags"])
    Competition.objects.filter(pk=other_competition.pk).update(
        routing_mode=RoutingMode.PATH, path_prefix="druga"
    )

    payload = get_json(f"c/{other_competition.slug}/chrome")

    assert payload["links"]["login"] == f"{MAIN}/druga/login/"
    assert payload["seo"]["og_image"].startswith(f"{MAIN}/static/")


# --- partners -------------------------------------------------------------------------------------


@pytest.fixture
def partners_page(competition):
    home = HomePage.objects.get(pk=competition.site.root_page_id)
    PartnersPage.objects.all().delete()
    page = home.add_child(
        instance=PartnersPage(
            title="Partnerzy",
            slug="partnerzy",
            intro='<p>Wstęp <a href="/register/">rejestracja</a></p>',
            become_partner_title="Zostań partnerem",
            become_partner_body="<p>Napisz</p>",
            contact_email="partnerzy@example.test",
            partners=[
                (
                    "partner",
                    {"name": "Sponsor Złoty", "level": "sponsor-zloty", "url": "javascript:alert(1)"},
                ),
                (
                    "partner",
                    {
                        "name": "Instytut Fizyki im. Adama",
                        "level": "partner-naukowy",
                        "logo": make_image("pas", (700, 100)),
                        "url": "https://instytut.example.test/",
                        "description": "Jedno zdanie.",
                    },
                ),
                (
                    "partner",
                    {
                        "name": "Uniwersytet",
                        "level": "patron-honorowy",
                        "logo": make_image("godlo", (100, 100)),
                    },
                ),
            ],
        )
    )
    return page


def test_partners_keep_the_page_order_and_the_card_fields(competition, partners_page):  # noqa: ARG001
    payload = get_json(f"c/{competition.slug}/partners")

    assert payload["page_path"] == "/partnerzy/"
    assert payload["levels"][0] == ["patron-honorowy", "patron honorowy"]
    names = [partner["name"] for partner in payload["partners"]]
    assert names == ["Sponsor Złoty", "Instytut Fizyki im. Adama", "Uniwersytet"]
    first, wide, square = payload["partners"]
    assert first["url"] == ""
    assert first["logo"] is None
    assert first["initials"] == "SZ"
    assert wide == {
        "name": "Instytut Fizyki im. Adama",
        "level": "partner-naukowy",
        "logo": wide["logo"],
        "url": "https://instytut.example.test/",
        "description": "Jedno zdanie.",
        "initials": "IF",
        "is_wide": True,
    }
    assert set(wide["logo"]) == {"src", "width", "height"}
    assert square["is_wide"] is False
    page = payload["page"]
    assert page["title"] == "Partnerzy"
    assert f'href="{MAIN}/register/"' in page["intro"]
    assert page["become_partner_title"] == "Zostań partnerem"
    assert page["contact_email"] == "partnerzy@example.test"


def test_partners_of_a_restricted_or_missing_page_are_empty(competition, partners_page):
    PageViewRestriction.objects.create(page=partners_page, restriction_type=PageViewRestriction.LOGIN)

    payload = get_json(f"c/{competition.slug}/partners")

    assert payload == {**payload, "page_path": None, "partners": [], "page": None}
    assert len(payload["levels"]) > 0


def test_partners_of_another_competition_come_from_its_own_tree(
    competition, other_competition, partners_page
):  # noqa: ARG001
    assert get_json(f"c/{other_competition.slug}/partners")["partners"] == []


# --- workshops v2 ---------------------------------------------------------------------------------


def test_workshops_v2_carry_the_whole_schedule_table(competition):
    home = HomePage.objects.get(pk=competition.site.root_page_id)
    soon = timezone.localdate() + timedelta(days=3)
    home.add_child(
        instance=ContentPage(
            title="Warsztaty",
            slug="warsztaty",
            intro="<p>Zapraszamy</p>",
            body=[
                (
                    "schedule",
                    {
                        "caption": "Semestr zimowy",
                        "topic_label": "Zajęcia",
                        "rows": [
                            {"topic": "Później", "date": "wkrótce", "date_value": soon, "time": "17:00"},
                            {"topic": "Dawno", "date": "dawno", "date_value": date(2020, 1, 1), "time": " "},
                            {"topic": "Bez daty", "date": "do potwierdzenia", "date_value": None},
                        ],
                    },
                ),
                (
                    "schedule",
                    {"caption": "Lato", "rows": [{"topic": "Obóz", "date": "lipiec", "lecturer": "dr Y"}]},
                ),
            ],
        )
    )

    payload = get_json(f"c/{competition.slug}/workshops")

    assert payload["page"] == {"title": "Warsztaty", "intro": "<p>Zapraszamy</p>"}
    winter, summer = payload["schedules"]
    assert winter["caption"] == "Semestr zimowy"
    assert winter["topic_label"] == "Zajęcia" and winter["date_label"] == ""
    assert [row["topic"] for row in winter["rows"]] == ["Później", "Dawno", "Bez daty"]
    assert winter["rows"][2]["date_value"] is None
    assert winter["rows"][0]["date_value"] == soon.isoformat()
    assert (winter["has_time"], winter["has_lecturer"]) == (True, False)
    assert (summer["has_time"], summer["has_lecturer"]) == (False, True)
    # Pola v1 bez zmian: wiersze z datą, posortowane.
    assert [row["topic"] for row in payload["rows"]] == ["Dawno", "Później"]


def test_workshops_v2_of_a_restricted_page_carry_no_table(competition):
    home = HomePage.objects.get(pk=competition.site.root_page_id)
    page = home.add_child(
        instance=ContentPage(
            title="Warsztaty",
            slug="warsztaty",
            body=[("schedule", {"rows": [{"topic": "Tajne", "date": "kiedyś", "date_value": None}]})],
        )
    )
    PageViewRestriction.objects.create(page=page, restriction_type=PageViewRestriction.PASSWORD, password="x")

    response = internal().get(f"{BASE}c/{competition.slug}/workshops")

    assert response.json()["page"] is None
    assert response.json()["schedules"] == []
    assert "Tajne" not in response.content.decode()


# --- export v2 ------------------------------------------------------------------------------------


def test_export_v2_returns_a_v2_bundle_of_the_named_competition(competition, other_competition):
    other_competition.site.root_page.add_child(instance=ContentPage(title="Tylko B", slug="tylko-b"))

    response = internal().get(f"{BASE}c/{other_competition.slug}/export")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    manifest = json.loads(
        zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))).read("manifest.json")
    )
    assert manifest["version"] == 2
    assert manifest["competition"]["slug"] == other_competition.slug
    assert manifest["source"]["main_public_url"] == f"https://{other_competition.primary_domain}"
    assert {"data_pages", "redirects"} <= set(manifest)
    assert [page["slug"] for page in manifest["pages"]][1:] == ["tylko-b"]
    assert competition.slug not in json.dumps(manifest["competition"])
