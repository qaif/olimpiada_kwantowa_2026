"""Import wielowitrynowy (DJ-02e): paczka v2, strony-dane → wtyczki żywe, przekierowania, foldery
per konkurs, ``--competition``/``--all``/``--skip`` i ``--replace`` jednej witryny obok drugiej."""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.importer import services
from apps.importer.tests import bundles

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def free_space(monkeypatch):
    """Dużo wolnego miejsca na dysku mediów (w kontenerze ``tmp_path`` leży na tmpfs 64 MB)."""
    import shutil

    from apps.importer.management.commands import import_cms_bundle as command

    usage = shutil.disk_usage(".")
    monkeypatch.setattr(command.shutil, "disk_usage", lambda _path: usage._replace(free=10 * 2**30))


@pytest.fixture
def fizyka(make_competition):
    return make_competition(
        "fizyka", name="Olimpiada Fizyczna", hosts=["fizyka.example"], public_origin="https://fizyka.example"
    )


def open_v2(manifest=None) -> services.Bundle:
    return services.open_bundle(io.BytesIO(bundles.build_zip(manifest or bundles.v2_manifest())))


def page_at(path: str, site_id: int = 1):
    from cms.models import PageUrl

    return PageUrl.objects.get(path=path.strip("/"), page__site_id=site_id).page


def content_at(path: str, site_id: int = 1):
    from cms.models import PageContent

    return PageContent.admin_manager.get(page=page_at(path, site_id), language="pl")


def slot_plugins(path: str, slot: str, site_id: int = 1) -> list:
    from cms.models import CMSPlugin
    from cms.utils.plugins import downcast_plugins

    content = content_at(path, site_id)
    queryset = CMSPlugin.objects.filter(
        placeholder__object_id=content.pk,
        placeholder__content_type__model="pagecontent",
        placeholder__slot=slot,
    ).order_by("position")
    return list(downcast_plugins(queryset))


def call(*args) -> str:
    out = io.StringIO()
    call_command("import_cms_bundle", *args, stdout=out, stderr=out)
    return out.getvalue()


# --- paczka v2 ------------------------------------------------------------------------------------


def test_v2_bundle_is_accepted_and_v1_still_is():
    assert open_v2().version == 2
    assert services.open_bundle(io.BytesIO(bundles.build_zip(bundles.v1_manifest()))).version == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(competition={"slug": "Zły Slug"}),
        lambda m: m.update(competition="kwantowa"),
        lambda m: m.update(data_pages=[]),
        lambda m: m.update(data_pages={"workshops": "40", "partners": None}),
        lambda m: m.update(redirects={}),
        lambda m: m.update(data_pages={"workshops": 999, "partners": None}),  # strony nie ma w paczce
        lambda m: m.update(data_pages={"workshops": bundles.PARTNERS_ID, "partners": None}),  # zły typ
        lambda m: m.update(data_pages={"workshops": None, "partners": bundles.WORKSHOPS_ID}),
        lambda m: m.update(version=3),
    ],
)
def test_malformed_v2_keys_are_rejected(mutate):
    manifest = bundles.v2_manifest()
    mutate(manifest)
    with pytest.raises(services.BundleError):
        open_v2(manifest)


def test_bundle_of_another_competition_is_refused(superuser, fizyka):
    before = services.site_has_pages(fizyka.site)
    with pytest.raises(services.ImportRefused, match="paczka konkursu „kwantowa”"):
        services.run_import(open_v2(), user=superuser, competition=fizyka)
    assert services.site_has_pages(fizyka.site) is before is False


def test_workshops_data_page_becomes_live_plugins(superuser):
    report = services.run_import(open_v2(), user=superuser)
    content = content_at("/warsztaty/")
    assert content.template == "dj/pages/content.html"
    intro = slot_plugins("/warsztaty/", "intro")
    assert [(p.plugin_type, p.part) for p in intro] == [("WorkshopSchedulePlugin", "intro")]
    body = slot_plugins("/warsztaty/", "body")
    # Tabela z importu nie jest kopiowana: jedna wtyczka żywa w miejscu pierwszej tabeli, druga wypada.
    assert [p.plugin_type for p in body] == ["HeadingPlugin", "WorkshopSchedulePlugin", "TextPlugin"]
    assert body[1].part == "schedule"
    assert not any(p.plugin_type in {"SchedulePlugin", "ScheduleRowPlugin"} for p in body)
    assert report.data_pages == {"warsztaty": "/warsztaty/", "partners": "/partnerzy/"}
    assert any("kolejna tabela" in line for line in report.skipped)
    assert "Strony-dane na żywo z Wagtaila" in "\n".join(report.lines())


def test_workshops_page_without_schedule_block_still_gets_the_live_table(superuser):
    manifest = bundles.v2_manifest()
    page = next(dto for dto in manifest["pages"] if dto["id"] == bundles.WORKSHOPS_ID)
    page["fields"]["body"] = [{"type": "paragraph", "value": "<p>Wkrótce.</p>"}]
    services.run_import(open_v2(manifest), user=superuser)
    assert [p.plugin_type for p in slot_plugins("/warsztaty/", "body")] == [
        "TextPlugin",
        "WorkshopSchedulePlugin",
    ]


def test_partners_data_page_becomes_the_live_template(superuser):
    services.run_import(open_v2(), user=superuser)
    assert content_at("/partnerzy/").template == "dj/live/partners_page.html"
    assert [p.plugin_type for p in slot_plugins("/partnerzy/", "partners")] == ["PartnersLivePlugin"]
    # Żadnej kopii kart partnerów ani zaproszenia – dane zostają w Wagtailu.
    from cms.models import CMSPlugin

    assert not CMSPlugin.objects.filter(plugin_type__in=["PartnerPlugin", "BecomePartnerPlugin"]).exists()


def test_v1_bundle_keeps_partner_cards(superuser):
    services.run_import(
        services.open_bundle(io.BytesIO(bundles.build_zip(bundles.v1_manifest()))), user=superuser
    )
    assert content_at("/partnerzy/").template == "dj/pages/partners.html"
    assert len(slot_plugins("/partnerzy/", "partners")) == 2


def test_imported_pages_render_with_live_data_pages(superuser, client, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload())
    main_api.set(
        "workshops",
        {
            "page": {"title": "Warsztaty", "intro": "<p>Nowe wprowadzenie</p>"},
            "schedules": [],
            "materials": {},
        },
    )
    main_api.set("partners", {"levels": [], "partners": [], "page": None})
    services.run_import(open_v2(), user=superuser)
    html = client.get("/warsztaty/").content.decode()
    assert "Nowe wprowadzenie" in html and "Stare wprowadzenie" not in html
    assert "Kopia z importu" not in html
    assert client.get("/partnerzy/").status_code == 200


# --- przekierowania -------------------------------------------------------------------------------


def test_resolve_redirects_maps_targets_and_reports_rejects(superuser):
    report = services.run_import(open_v2(), user=superuser)
    specs = services.resolve_redirects(
        open_v2(), {bundles.REGULAMIN_ID: page_at("/dokumenty/regulamin/"), 7: page_at("/wyniki/")}, report
    )
    assert [(s.old_path, s.new_path, s.is_permanent) for s in specs] == [
        ("/regulamin", "/dokumenty/regulamin/", True),
        ("/stary-kontakt", "/kontakt/#adres", False),
        ("/zewnetrzny", "https://example.org/x", True),
    ]
    skipped = "\n".join(report.skipped)
    for needle in (
        "javascript:alert(1)",
        "#999 poza importem",
        "podstroną routowalną",
        "pętla",
        "powtórzona ścieżka",
        "'bez-ukosnika' niepoprawna",
        "zły kształt",
    ):
        assert needle in skipped, needle


def test_redirects_are_stored_per_site_and_replaced_with_the_site(superuser, fizyka):
    from apps.seo.models import Redirect

    Redirect.objects.create(site=fizyka.site, old_path="/regulamin", new_path="/x/", source="manual")
    Redirect.objects.create(site_id=1, old_path="/stary-kontakt", new_path="/moj/", source="manual")
    report = services.run_import(open_v2(), user=superuser)
    rows = {(r.old_path, r.new_path, r.source) for r in Redirect.objects.filter(site_id=1)}
    assert rows == {
        ("/regulamin", "/dokumenty/regulamin/", "import"),
        ("/zewnetrzny", "https://example.org/x", "import"),
        ("/stary-kontakt", "/moj/", "manual"),  # wpis redakcji wygrywa z importem
    }
    assert report.redirects == 2 and any("przekierowanie redakcji" in line for line in report.skipped)
    again = services.run_import(open_v2(), user=superuser, replace=True)
    assert again.deleted_redirects == 2 and again.redirects == 2
    # Przekierowanie drugiej witryny nietknięte.
    assert Redirect.objects.filter(site=fizyka.site).count() == 1


def test_redirect_to_imported_page_is_served(superuser, client, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload())
    services.run_import(open_v2(), user=superuser)
    response = client.get("/regulamin")
    assert response.status_code == 301 and response["Location"].endswith("/dokumenty/regulamin/")


def test_missing_redirect_model_is_reported(superuser, monkeypatch):
    monkeypatch.setattr(services, "redirect_model", lambda: None)
    report = services.run_import(open_v2(), user=superuser)
    assert any("brak modelu dj_seo.Redirect" in line for line in report.skipped)


# --- dwie witryny w jednej bazie ------------------------------------------------------------------


def test_two_sites_same_paths_separate_trees_and_folders(superuser, fizyka):
    from cms.models import Page
    from filer.models import Image

    services.run_import(open_v2(), user=superuser)
    services.run_import(
        open_v2(bundles.v2_manifest("fizyka", "Olimpiada Fizyczna")), user=superuser, competition=fizyka
    )
    assert Page.objects.filter(site_id=1).count() == Page.objects.filter(site=fizyka.site).count() == 17
    assert page_at("/zadania/", 1).pk != page_at("/zadania/", fizyka.site_id).pk
    folders = sorted({(image.folder.parent.name, image.folder.name) for image in Image.objects.all()})
    assert folders == [
        ("Konkurs: Olimpiada Fizyczna (fizyka)", services.IMPORT_FOLDER_NAME),
        ("Konkurs: Olimpiada Kwantowa (kwantowa)", services.IMPORT_FOLDER_NAME),
    ]
    # Ten sam obraz (SHA-1) w obu witrynach – osobne pliki: ``--replace`` jednej nie może
    # skasować pliku drugiej.
    assert Image.objects.count() == 4


def test_replace_of_one_site_leaves_the_other_untouched(
    superuser, fizyka, django_capture_on_commit_callbacks
):
    from cms.models import Page
    from filer.models import Image

    services.run_import(open_v2(), user=superuser)
    services.run_import(open_v2(bundles.v2_manifest("fizyka", "F")), user=superuser, competition=fizyka)
    fizyka_pages = set(Page.objects.filter(site=fizyka.site).values_list("pk", flat=True))
    fizyka_files = [
        (image.pk, image.file.name)
        for image in Image.objects.filter(folder__parent__name__endswith="(fizyka)")
    ]
    with django_capture_on_commit_callbacks(execute=True):
        report = services.run_import(open_v2(), user=superuser, replace=True)
    assert report.deleted_pages == 17 and report.deleted_files == 2
    assert set(Page.objects.filter(site=fizyka.site).values_list("pk", flat=True)) == fizyka_pages
    for pk, name in fizyka_files:
        image = Image.objects.get(pk=pk)
        assert image.file.storage.exists(name)


def test_legacy_root_import_folder_belongs_to_the_default_site(superuser, fizyka):
    """Import sprzed DJ-02 leży w folderze „Import z Wagtaila” w korzeniu – ``--replace`` konkursu
    domyślnego go sprząta, a import innego konkursu nie używa jego obrazów ponownie."""
    from filer.models import Folder, Image

    legacy = Folder.objects.create(name=services.IMPORT_FOLDER_NAME)
    from django.core.files.uploadedfile import SimpleUploadedFile

    data = (bundles.FIXTURE / "images" / "17-logo-pasek.png").read_bytes()
    Image.objects.create(folder=legacy, original_filename="a.png", file=SimpleUploadedFile("a.png", data))
    report = services.run_import(
        open_v2(bundles.v2_manifest("fizyka", "F")), user=superuser, competition=fizyka
    )
    assert report.images_reused == 0 and report.images_created == 2
    services.run_import(open_v2(), user=superuser)
    replaced = services.run_import(open_v2(), user=superuser, replace=True)
    assert replaced.deleted_files == 3  # dwa własne + jeden sprzed DJ-02
    assert not Image.objects.filter(folder=legacy).exists()


def test_competition_folder_follows_a_renamed_competition(superuser):
    from apps.sites.models import CompetitionSite

    competition = CompetitionSite.objects.get(slug="kwantowa")
    folder = services.competition_folder(competition)
    competition.name = "Nowa nazwa"
    assert services.competition_folder(competition).pk == folder.pk
    folder.refresh_from_db()
    assert folder.name == "Konkurs: Nowa nazwa (kwantowa)"


def test_path_competition_gets_prefixed_links(superuser, make_competition):
    from apps.sites.models import CompetitionSite

    CompetitionSite.objects.filter(slug="kwantowa").update(hosts_path_prefixes=True)
    druga = make_competition(
        "druga",
        routing_mode="PATH",
        path_prefix="druga",
        public_origin="https://x",
        public_path_prefix="/druga",
    )
    services.run_import(open_v2(bundles.v2_manifest("druga", "Druga")), user=superuser, competition=druga)
    text = next(p for p in slot_plugins("/aktualnosci/ruszyla-rejestracja/", "body", druga.site_id))
    assert 'href="/druga/zadania/"' in text.body


def test_content_imported_at_is_set_but_not_on_dry_run(superuser):
    from apps.sites.models import CompetitionSite

    services.run_import(open_v2(), user=superuser, dry_run=True)
    assert CompetitionSite.objects.get(slug="kwantowa").content_imported_at is None
    services.run_import(open_v2(), user=superuser)
    assert CompetitionSite.objects.get(slug="kwantowa").content_imported_at is not None


# --- komenda --------------------------------------------------------------------------------------


def _exports(main_api, *slugs):
    for slug in slugs:
        main_api.set(
            "export", raw=bundles.build_zip(bundles.v2_manifest(slug, slug.title())), competition=slug
        )


def test_command_competition_option(superuser, fizyka, main_api):
    _exports(main_api, "fizyka")
    out = call("--from-api", "--competition", "fizyka")
    assert "witryna konkursu „fizyka”" in out and "Strony: 17" in out
    assert main_api.calls("export", competition="fizyka") == 1
    assert not services.site_has_pages(fizyka.site.__class__.objects.get(pk=1))


def test_command_unknown_or_inactive_competition(superuser, make_competition):
    with pytest.raises(CommandError, match="nie ma w rejestrze"):
        call("--from-api", "--competition", "nie-ma")
    make_competition("stary", is_active=False)
    with pytest.raises(CommandError, match="nieaktywny"):
        call("--from-api", "--competition", "stary")


def test_command_all_skip_and_failures(superuser, fizyka, make_competition, main_api):
    make_competition("chemia", hosts=["chemia.example"])
    make_competition("wygaszony", is_active=False)
    _exports(main_api, "kwantowa", "fizyka")  # „chemia” bez eksportu → błąd tylko tego konkursu
    with pytest.raises(CommandError, match="Import nie powiódł się dla: chemia"):
        call("--from-api", "--all")
    assert services.site_has_pages(fizyka.site)
    assert main_api.calls("export", competition="wygaszony") == 0
    # Drugi przebieg: --skip + --replace; kwantowa pominięta, fizyka od nowa.
    _exports(main_api, "chemia")
    out = call("--from-api", "--all", "--replace", "--skip", "kwantowa")
    assert "--skip: pominięty." in out and out.count("import_cms_bundle: gotowe.") == 2
    assert main_api.calls("export", competition="kwantowa") == 1


def test_command_all_if_empty_downloads_nothing_for_filled_sites(superuser, fizyka, main_api):
    _exports(main_api, "kwantowa", "fizyka")
    call("--from-api", "--all")
    out = call("--from-api", "--all", "--if-empty")
    assert out.count("--if-empty: import pominięty") == 2
    assert main_api.calls("export", competition="kwantowa") == 1


def test_command_option_errors(superuser, tmp_path):
    with pytest.raises(CommandError, match="wymaga --from-api"):
        call(str(tmp_path / "x.zip"), "--all")
    with pytest.raises(CommandError, match="--skip działa wyłącznie z --all"):
        call("--from-api", "--skip", "kwantowa")
    with pytest.raises(CommandError, match="nie ma wśród aktywnych"):
        call("--from-api", "--all", "--skip", "nie-ma")


def test_command_file_goes_to_the_bundle_competition(superuser, fizyka, tmp_path):
    path = tmp_path / "fizyka.zip"
    path.write_bytes(bundles.build_zip(bundles.v2_manifest("fizyka", "F")))
    assert "witryna konkursu „fizyka”" in call(str(path))
    with pytest.raises(CommandError, match="--replace"):
        call(str(path))
    with pytest.raises(CommandError, match="paczka konkursu „fizyka”"):
        call(str(path), "--competition", "kwantowa")
