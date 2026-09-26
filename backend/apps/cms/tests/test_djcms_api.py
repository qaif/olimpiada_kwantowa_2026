"""Wewnętrzne API dla wersji ``dj.`` (``/internal/djcms/v1/…``, DJ-01 § 3).

Cztery grupy testów, w kolejności wagi:

1. **bramki** – każda porażka (host publiczny, brak/zły/krótki/pusty token, metoda inna niż GET)
   to pusta 404, nieodróżnialna od nieistniejącego adresu. Host publiczny odpada **także**
   z poprawnym tokenem: token mógł wyciec, a żądanie z internetu nie ma tu czego szukać,
2. **reguły jawności** – zadania dopiero po ``opens_at``, wyniki wyłącznie z białej listy kluczy
   snapshotu, żadnych danych osobowych w żadnym endpoincie, odnośnik komunikatu tylko ``http(s)``,
3. **kształt** – ``api_version``, ``generated_at``, nagłówki, adresy przez ``api_href``,
4. **wybór konkursu** – ``DJCMS_COMPETITION_SLUG`` albo witryna domyślna; brak konkursu = 503.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.cms.checks import check_djcms_token
from apps.cms.djcms_api.serializers import PublicBase, api_href, public_base_context, row_dto
from apps.cms.models import ArchiveEditionPage, ContentPage, ContentPageAttachment
from apps.cms.tests.factories import AnnouncementFactory
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    EditionFactory,
    ProblemFactory,
    StageFactory,
)
from apps.results.models import Anonymization, ResultsPublication

pytestmark = pytest.mark.django_db

TOKEN = "t" * 40
BASE = "/internal/djcms/v1/"
JSON_ENDPOINTS = ("chrome", "stages", "problems", "results", "editions", "workshops")
MAIN = "https://olimpiada.example.test"


@pytest.fixture(autouse=True)
def _api_settings(settings):
    settings.DJCMS_INTERNAL_TOKEN = TOKEN
    settings.DJCMS_COMPETITION_SLUG = ""
    settings.DJCMS_MAIN_PUBLIC_URL = MAIN


def internal(token: str | None = TOKEN, host: str = "web:8000") -> Client:
    headers = {"HTTP_HOST": host, "SERVER_NAME": host.partition(":")[0]}
    if token is not None:
        headers["HTTP_X_DJCMS_TOKEN"] = token
    return Client(**headers)


def get_json(endpoint: str, client: Client | None = None) -> dict:
    response = (client or internal()).get(BASE + endpoint)
    assert response.status_code == 200, (endpoint, response.status_code)
    return response.json()


def publish(stage, snapshot, anonymization=Anonymization.CODE) -> ResultsPublication:
    stage.results_published_at = timezone.now()
    stage.save(update_fields=["results_published_at"])
    return ResultsPublication.objects.create(stage=stage, anonymization=anonymization, snapshot=snapshot)


# --- bramki -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "client_kwargs"),
    [
        pytest.param(TOKEN, {"token": None}, id="brak-naglowka"),
        pytest.param(TOKEN, {"token": "z" * 40}, id="zly-token"),
        pytest.param(TOKEN, {"token": TOKEN[:-1]}, id="token-o-znak-krotszy"),
        pytest.param("k" * 31, {"token": "k" * 31}, id="token-krotszy-niz-32"),
        pytest.param("", {"token": ""}, id="pusty-token"),
        pytest.param(TOKEN, {"host": "testserver"}, id="host-publiczny-z-tokenem"),
        pytest.param(TOKEN, {"host": "kwantowa.invalid"}, id="domena-konkursu-z-tokenem"),
    ],
)
@pytest.mark.parametrize("endpoint", [*JSON_ENDPOINTS, "export", "editions/1/results"])
def test_every_gate_failure_is_an_empty_404(settings, competition, setting, client_kwargs, endpoint):  # noqa: ARG001
    settings.DJCMS_INTERNAL_TOKEN = setting
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "kwantowa.invalid"]

    response = internal(**client_kwargs).get(BASE + endpoint)

    assert response.status_code == 404
    assert response.content == b""


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_other_methods_are_404_even_with_a_valid_token(competition, method):  # noqa: ARG001
    response = getattr(internal(), method)(BASE + "chrome")

    assert response.status_code == 404
    assert response.content == b""


@pytest.mark.parametrize("endpoint", JSON_ENDPOINTS)
def test_a_valid_request_gets_versioned_json_that_is_never_cached(competition, endpoint):  # noqa: ARG001
    response = internal().get(BASE + endpoint)

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json; charset=utf-8"
    assert response["Cache-Control"] == "no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    payload = response.json()
    assert payload["api_version"] == 1
    assert "+" in payload["generated_at"] or payload["generated_at"].endswith("Z")


def test_short_token_raises_the_system_check_warning(settings):
    settings.DJCMS_INTERNAL_TOKEN = "krotki"
    assert [message.id for message in check_djcms_token()] == ["cms.W010"]

    settings.DJCMS_INTERNAL_TOKEN = ""
    assert check_djcms_token() == []

    settings.DJCMS_INTERNAL_TOKEN = TOKEN
    assert check_djcms_token() == []


# --- konkurs API --------------------------------------------------------------------------------


def test_without_a_competition_the_api_answers_503(settings, competition):  # noqa: ARG001
    settings.DJCMS_COMPETITION_SLUG = "nie-ma-takiego"

    response = internal().get(BASE + "stages")

    assert response.status_code == 503
    assert response.json()["error"] == "no-competition"


def test_the_default_site_competition_is_used_without_a_slug(competition):
    CurrentEditionFactory(competition=competition, year_label="I edycja domyślna")

    assert get_json("stages")["edition"]["year_label"] == "I edycja domyślna"
    assert get_json("chrome")["competition"]["slug"] == competition.slug


def test_the_slug_setting_selects_another_competition(settings, competition, other_competition):
    CurrentEditionFactory(competition=competition, year_label="Edycja konkursu pierwszego")
    CurrentEditionFactory(competition=other_competition, year_label="Edycja konkursu drugiego")
    settings.DJCMS_COMPETITION_SLUG = other_competition.slug

    assert get_json("stages")["edition"]["year_label"] == "Edycja konkursu drugiego"
    assert get_json("chrome")["competition"]["slug"] == other_competition.slug


def test_an_inactive_competition_is_not_served(settings, other_competition):
    other_competition.is_active = False
    other_competition.save(update_fields=["is_active"])
    settings.DJCMS_COMPETITION_SLUG = other_competition.slug

    assert internal().get(BASE + "chrome").status_code == 503


# --- stages ---------------------------------------------------------------------------------------


def test_stages_carry_raw_and_formatted_dates_and_results_links(competition):
    edition = CurrentEditionFactory(competition=competition, year_label="XV (2026/2027)")
    stage = StageFactory(edition=edition, location="Warszawa", grace_seconds=600)
    publish(stage, [])

    payload = get_json("stages")

    assert payload["edition"] == {
        "id": edition.pk,
        "year_label": "XV (2026/2027)",
        "title": "edycja XV (2026/2027)",
        "title_cap": "Edycja XV (2026/2027)",
    }
    row = payload["rows"][0]
    dto = row["stage"]
    assert dto["id"] == stage.pk
    assert dto["location"] == "Warszawa"
    assert dto["edition_year_label"] == "XV (2026/2027)"
    assert dto["opens_at"] == timezone.localtime(stage.opens_at).isoformat()
    assert dto["opens_at_local_time"].endswith("(czas polski)")
    assert dto["submission_deadline"] == timezone.localtime(stage.submission_deadline).isoformat()
    assert dto["grace_seconds"] == 600
    assert dto["results_url"] == f"{MAIN}/results/{stage.pk}/"
    assert (row["status"], row["has_results"], row["badge_class"]) == ("published", True, "badge badge--ok")
    assert payload["current_stage"]["id"] == stage.pk


def test_event_range_is_announced_as_one_phrase(competition):
    edition = CurrentEditionFactory(competition=competition)
    StageFactory(edition=edition, event_starts_on=date(2027, 6, 4), event_ends_on=date(2027, 6, 7))

    row = get_json("stages")["rows"][0]

    assert row["stage"]["event_range"] == ["2027-06-04", "2027-06-07"]
    assert row["stage"]["event_dates"] == "4–7 czerwca 2027"
    assert row["is_onsite_event"] is True
    assert row["date_range"] == "4–7 czerwca 2027"
    assert row["stage"]["results_url"] is None


# --- problems: jawne dopiero po opens_at ----------------------------------------------------------


def test_problems_before_opening_carry_no_title_and_no_pdf_link(competition):
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition, opens_at=timezone.now() + timedelta(days=2))
    ProblemFactory(stage=stage, number=1, title="Zadanie pod embargiem", statement_pdf="statements/x.pdf")

    response = internal().get(BASE + "problems")
    payload = response.json()

    assert payload["stage"]["id"] == stage.pk
    assert payload["stage_has_opened"] is False
    assert payload["problems"] == []
    body = response.content.decode()
    assert "Zadanie pod embargiem" not in body
    assert "statement" not in body


def test_problems_after_opening_link_to_the_application_view(competition):
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition, opens_at=timezone.now() - timedelta(hours=1))
    with_pdf = ProblemFactory(stage=stage, number=1, title="Zadanie z PDF", statement_pdf="statements/x.pdf")
    ProblemFactory(stage=stage, number=2, title="Zadanie bez PDF")

    payload = get_json("problems")

    assert payload["stage_has_opened"] is True
    assert [item["title"] for item in payload["problems"]] == ["Zadanie z PDF", "Zadanie bez PDF"]
    assert (
        payload["problems"][0]["statement_url"]
        == f"{MAIN}/api/competitions/problems/{with_pdf.pk}/statement/"
    )
    assert payload["problems"][1]["statement_url"] is None
    assert payload["problems"][0]["allowed_formats"] == list(with_pdf.allowed_formats)


# --- results: biała lista kluczy snapshotu --------------------------------------------------------


def test_results_pass_only_whitelisted_snapshot_keys(competition):
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition)
    publish(
        stage,
        [
            {
                "rank": 1,
                "display": "OLM-7Q2K",
                "district": "mazowieckie",
                "points": {"1": 6, "2": 4.25},
                "total": 10.25,
                "qualified": True,
                "manual": False,
                # Klucze, których snapshot mieć nie powinien – wstrzyknięte ręcznie do JSON-a.
                "email": "wyciek@example.invalid",
                "name": "Jan Wyciekowski",
                "participant_id": 4242,
            }
        ],
    )

    response = internal().get(BASE + "results")
    table = response.json()["tables"][0]

    assert table["rows"] == [
        {
            "rank": 1,
            "display": "OLM-7Q2K",
            "points": {"1": 6, "2": 4.25},
            "points_display": {"1": "6", "2": "4,25"},
            "total": 10.25,
            "total_display": "10,25",
            "qualified": True,
            "manual": False,
            "district": "mazowieckie",
        }
    ]
    assert table["problem_numbers"] == ["1", "2"]
    assert table["publication"]["anonymization"] == "CODE"
    body = response.content.decode()
    for leaked in ("wyciek@example.invalid", "Jan Wyciekowski", "participant_id", "4242"):
        assert leaked not in body


def test_row_dto_keeps_category_only_when_the_snapshot_has_it():
    assert "category" not in row_dto({"rank": 1, "display": "A", "points": {}, "total": 0})
    assert row_dto({"rank": 1, "display": "A", "points": {}, "total": 0, "category": "Juniorzy"})[
        "category"
    ] == ("Juniorzy")
    assert "district" not in row_dto({"rank": 1, "display": "A", "points": {}, "total": 0})


def test_results_of_older_editions_are_archive_links(competition):
    old = StageFactory(edition=EditionFactory(competition=competition, year_label="Stara"))
    CurrentEditionFactory(competition=competition)
    publish(old, [{"rank": 1, "display": "OLM-X", "points": {}, "total": 0}])

    payload = get_json("results")

    assert payload["tables"] == []
    assert payload["archive"][0]["stage"]["results_url"] == f"{MAIN}/results/{old.pk}/"
    assert "rows" not in json.dumps(payload["archive"])


def test_no_endpoint_contains_personal_data(competition):
    """Świat z uczestnikami, pracami, ocenami i opublikowaną tabelą – i żaden ich ślad w API."""
    from apps.tenancy.tests.golden import MAIL_DOMAIN, build_golden, publish_results

    golden = build_golden(competition)
    publish_results(golden)

    bodies = [internal().get(BASE + endpoint).content.decode() for endpoint in JSON_ENDPOINTS]
    bodies.append(internal().get(f"{BASE}editions/{golden.edition.pk}/results").content.decode())

    for body in bodies:
        assert MAIL_DOMAIN not in body
        assert "Testowy" not in body
        assert "Liceum testowe" not in body
    # Pseudonimy z tabeli po kodach są jawne (to one stoją na /wyniki/) – i tylko one.
    assert golden.participants[0].public_code in bodies[JSON_ENDPOINTS.index("results")]


# --- editions -------------------------------------------------------------------------------------


def test_editions_list_only_this_competition(competition, other_competition):
    own = EditionFactory(competition=competition, year_label="Nasza")
    EditionFactory(competition=other_competition, year_label="Cudza")

    labels = [item["year_label"] for item in get_json("editions")["editions"]]

    assert "Nasza" in labels and "Cudza" not in labels
    assert own.pk in [item["id"] for item in get_json("editions")["editions"]]


def test_edition_results_link_published_stages(competition):
    edition = EditionFactory(competition=competition, year_label="Archiwalna")
    stage = StageFactory(edition=edition)
    publish(stage, [])

    payload = get_json(f"editions/{edition.pk}/results")

    assert payload["edition"]["year_label"] == "Archiwalna"
    assert [link["stage"]["id"] for link in payload["links"]] == [stage.pk]
    assert payload["links"][0]["results_url"] == f"{MAIN}/results/{stage.pk}/"


def test_edition_results_of_a_foreign_edition_are_empty(competition, other_competition):  # noqa: ARG001
    foreign = EditionFactory(competition=other_competition)
    publish(StageFactory(competition=other_competition, edition=foreign), [])

    payload = get_json(f"editions/{foreign.pk}/results")

    assert payload["edition"] is None
    assert payload["links"] == []


def test_archive_page_and_api_list_the_same_stages(competition, archive_index):
    edition = EditionFactory(competition=competition)
    stage = StageFactory(edition=edition)
    publish(stage, [])
    page = archive_index.add_child(instance=ArchiveEditionPage(title="Archiwum X", slug="x", edition=edition))

    api = [link["stage"]["id"] for link in get_json(f"editions/{edition.pk}/results")["links"]]

    assert api == [link["stage"].pk for link in page.result_links()]


# --- chrome ---------------------------------------------------------------------------------------


def test_chrome_filters_non_http_announcement_links(competition):
    AnnouncementFactory(
        competition=competition, text="Uwaga, skrypt", link_url="javascript:alert(1)", link_label="Kliknij"
    )
    AnnouncementFactory(
        competition=competition, text="Zwykły", link_url="https://example.test/x", link_label="Więcej"
    )

    items = {item["text"]: item for item in get_json("chrome")["announcements"]}

    assert items["Uwaga, skrypt"]["link_url"] == ""
    assert items["Uwaga, skrypt"]["has_link"] is False
    assert items["Zwykły"]["link_url"] == "https://example.test/x"
    assert items["Zwykły"]["has_link"] is True


def test_chrome_links_point_at_the_main_domain(competition):
    CurrentEditionFactory(
        competition=competition,
        registration_enabled=True,
        registration_opens_at=timezone.now() + timedelta(days=5),
    )

    payload = get_json("chrome")

    assert payload["links"]["login"] == f"{MAIN}/login/"
    assert payload["links"]["register"] == f"{MAIN}/register/"
    assert payload["links"]["support"] == f"{MAIN}/support/new/"
    assert payload["links"]["main_home"] == f"{MAIN}/"
    assert payload["registration"]["reason"] == "not_yet"
    assert payload["registration"]["opens_at_display"]
    assert payload["registration"]["message"].startswith("Rejestracja rusza")
    assert payload["supervisor_registration"] == {"enabled": False, "url": None}
    assert payload["site"]["site_name"]


def test_timeline_strip_urls_go_through_api_href(competition):
    edition = CurrentEditionFactory(competition=competition, year_label="I edycja 2026/2027")
    stage = StageFactory(edition=edition)
    publish(stage, [])

    strip = get_json("chrome")["timeline_strip"]

    assert strip["edition"] == "I edycja 2026/2027"
    urls = [item["url"] for item in strip["items"] if item["url"]]
    assert f"{MAIN}/results/{stage.pk}/" in urls
    assert isinstance(strip["axis_start"], str)


# --- workshops ------------------------------------------------------------------------------------


def test_workshops_come_from_the_wagtail_table_without_keys(competition, home_page):  # noqa: ARG001
    soon = timezone.localdate() + timedelta(days=10)
    page = ContentPage(
        title="Warsztaty",
        slug="warsztaty",
        body=[
            (
                "schedule",
                {
                    "caption": "",
                    "rows": [
                        {
                            "topic": "Kubity",
                            "date": "wkrótce",
                            "date_value": soon,
                            "time": "17:00",
                            "lecturer": "dr X",
                        },
                        {"topic": "Dawno", "date": "dawno", "date_value": date(2020, 1, 1), "time": ""},
                        {"topic": "Bez daty", "date": "do potwierdzenia", "date_value": None, "time": ""},
                    ],
                },
            )
        ],
    )
    home_page.add_child(instance=page)
    page.save_revision().publish()

    response = internal().get(BASE + "workshops")
    payload = response.json()

    assert payload["page_path"] == "/warsztaty/"
    assert [row["topic"] for row in payload["upcoming"]] == ["Kubity"]
    assert [row["topic"] for row in payload["rows"]] == ["Dawno", "Kubity"]
    assert payload["rows"][1] == {
        "topic": "Kubity",
        "date": "wkrótce",
        "date_value": soon.isoformat(),
        "time": "17:00",
        "lecturer": "dr X",
    }
    assert "key" not in response.content.decode()
    assert payload["materials"]["show"] is False
    assert payload["materials"]["login_url"].startswith(f"{MAIN}/login/?next=/")


# --- export ---------------------------------------------------------------------------------------


def test_export_returns_a_zip_bundle(competition):  # noqa: ARG001
    response = internal().get(BASE + "export")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert "attachment" in response["Content-Disposition"]
    assert response["Cache-Control"] == "no-store"
    archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
    manifest = json.loads(archive.read("manifest.json"))
    assert (manifest["format"], manifest["version"]) == ("olimpiada-cms-bundle", 1)
    assert manifest["pages"][0]["type"] == "cms.HomePage"


# --- api_href -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("/warsztaty/", "/warsztaty/"),
        ("/dokumenty/regulamin/#par-16", "/dokumenty/regulamin/#par-16"),
        ("/results/5/", f"{MAIN}/results/5/"),
        ("/register/", f"{MAIN}/register/"),
        ("/documents/5/regulamin.pdf", f"{MAIN}/documents/5/regulamin.pdf"),
        ("/media/images/logo.png", f"{MAIN}/media/images/logo.png"),
        ("https://s3.example.test/public/logo.png", "https://s3.example.test/public/logo.png"),
        ("javascript:alert(1)", ""),
        ("//evil.example/x", ""),
        ("mailto:ktos@example.test", ""),
        ("wzgledny/adres", ""),
        ("", ""),
    ],
)
def test_api_href(url, expected):
    assert api_href(url) == expected


# --- adres konkursu API (DJCMS_COMPETITION_SLUG ≠ konkurs domeny głównej) --------------------------


def test_app_links_of_another_competition_go_to_its_own_domain(settings, competition, other_competition):  # noqa: ARG001
    """``DJCMS_MAIN_PUBLIC_URL`` opisuje domenę główną – linki konkursu #2 idą pod jego domenę."""
    settings.DJCMS_COMPETITION_SLUG = other_competition.slug
    domain = other_competition.primary_domain

    links = get_json("chrome")["links"]

    assert links["login"] == f"https://{domain}/login/"
    assert links["register"] == f"https://{domain}/register/"
    assert links["main_home"] == f"https://{domain}/"
    assert MAIN not in json.dumps(get_json("chrome"))
    assert get_json("workshops")["materials"]["login_url"].startswith(f"https://{domain}/login/?next=/")


def test_another_competition_keeps_the_scheme_and_port_of_the_main_url(
    settings, competition, other_competition
):  # noqa: ARG001
    """Wszystkie konkursy obsługuje ten sam serwer – w devie ``http`` i port ``runserver``."""
    settings.DJCMS_MAIN_PUBLIC_URL = "http://localhost:8000"
    settings.DJCMS_COMPETITION_SLUG = other_competition.slug

    assert get_json("chrome")["links"]["login"] == f"http://{other_competition.primary_domain}:8000/login/"


def test_the_default_competition_named_by_slug_still_uses_the_main_url(settings, competition):
    settings.DJCMS_COMPETITION_SLUG = competition.slug

    assert get_json("chrome")["links"]["login"] == f"{MAIN}/login/"


def test_a_path_prefix_competition_links_under_the_platform_host_with_its_prefix(
    settings, competition, other_competition
):
    from apps.tenancy.models import RoutingMode

    competition.feature_flags = {**(competition.feature_flags or {}), "path_prefix_routing": True}
    competition.save(update_fields=["feature_flags"])
    other_competition.routing_mode = RoutingMode.PATH
    other_competition.path_prefix = "druga"
    other_competition.save(update_fields=["routing_mode", "path_prefix"])
    settings.DJCMS_COMPETITION_SLUG = other_competition.slug

    links = get_json("chrome")["links"]

    assert links["login"] == f"{MAIN}/druga/login/"
    assert links["main_home"] == f"{MAIN}/druga/"


def test_prefix_is_not_added_to_static_files_and_media():
    """WhiteNoise odpowiada na ``/static/…`` przed warstwą konkursu – z prefiksem byłoby 404."""
    base = PublicBase(MAIN, "/druga")

    with public_base_context(base):
        assert api_href("/results/5/") == f"{MAIN}/druga/results/5/"
        assert api_href("/media/images/logo.png") == f"{MAIN}/media/images/logo.png"
        assert api_href("/static/img/x.svg") == f"{MAIN}/static/img/x.svg"
        assert api_href("/warsztaty/") == "/warsztaty/"
    assert api_href("/results/5/") == f"{MAIN}/results/5/"


@pytest.mark.parametrize("case", ["prefiks-bez-bramki", "bez-domeny"])
def test_a_competition_without_a_public_address_is_refused(settings, competition, other_competition, case):  # noqa: ARG001
    from apps.tenancy.models import Competition, RoutingMode

    if case == "prefiks-bez-bramki":
        # Konkurs platformy bez ``path_prefix_routing``: pod ``/<prefiks>/`` nikt go nie rozstrzyga.
        Competition.objects.filter(pk=other_competition.pk).update(
            routing_mode=RoutingMode.PATH, path_prefix="druga"
        )
    else:
        Competition.objects.filter(pk=other_competition.pk).update(primary_domain="")
    settings.DJCMS_COMPETITION_SLUG = other_competition.slug

    for endpoint in [*JSON_ENDPOINTS, "export"]:
        response = internal().get(BASE + endpoint)
        assert response.status_code == 503, endpoint
        assert response.json() == {"api_version": 1, "error": "no-public-url"}


def test_export_of_another_competition_points_documents_at_its_domain(
    settings, competition, other_competition
):  # noqa: ARG001
    from django.core.files.uploadedfile import SimpleUploadedFile
    from wagtail.documents import get_document_model
    from wagtail.models import Collection

    document = get_document_model().objects.create(
        title="regulamin",
        collection=Collection.get_first_root_node(),
        file=SimpleUploadedFile("regulamin.pdf", b"%PDF-1.4", content_type="application/pdf"),
    )
    root = other_competition.site.root_page
    info = root.add_child(
        instance=ContentPage(
            title="Info",
            slug="info",
            intro=f'<p><a linktype="document" id="{document.pk}">regulamin</a> '
            f'<a href="/register/">rejestracja</a></p>',
        )
    )
    ContentPageAttachment.objects.create(page=info, document=document, label="PDF")
    settings.DJCMS_COMPETITION_SLUG = other_competition.slug

    response = internal().get(BASE + "export")

    manifest = json.loads(
        zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))).read("manifest.json")
    )
    domain = f"https://{other_competition.primary_domain}"
    assert manifest["source"]["main_public_url"] == domain
    intro = next(page for page in manifest["pages"] if page["slug"] == "info")["fields"]["intro"]
    assert f'href="{domain}/documents/{document.pk}/' in intro
    assert f'href="{domain}/register/"' in intro
    assert manifest["documents"][str(document.pk)]["url"].startswith(f"{domain}/documents/")


def test_workshops_page_path_skips_a_restricted_page(competition, home_page):  # noqa: ARG001
    from wagtail.models import PageViewRestriction

    page = home_page.add_child(instance=ContentPage(title="Warsztaty", slug="warsztaty"))
    assert get_json("workshops")["page_path"] == "/warsztaty/"

    PageViewRestriction.objects.create(page=page, restriction_type=PageViewRestriction.LOGIN)

    assert get_json("workshops")["page_path"] is None


# --- adresy nieistniejące -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        BASE + "nie-ma",
        BASE + "nie-ma/",
        BASE + "chrome/",
        BASE + "editions/abc/results",
        BASE + "editions/1/results/",
        "/internal/djcms/v1",
        "/internal/djcms/",
        "/internal/djcms",
        "/internal/djcms/v2/chrome",
    ],
)
@pytest.mark.parametrize(
    "client_kwargs", [{}, {"token": None}, {"host": "testserver"}], ids=["ok", "bez-tokenu", "publiczny"]
)
def test_unknown_paths_are_the_same_empty_404_as_a_closed_gate(competition, path, client_kwargs):  # noqa: ARG001
    for method in ("get", "post", "head"):
        response = getattr(internal(**client_kwargs), method)(path)
        assert response.status_code == 404, (method, path)
        assert response.content == b"", (method, path)
        assert "Location" not in response
