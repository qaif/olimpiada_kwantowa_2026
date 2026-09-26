"""Import paczki treści z Wagtaila (§ 5.3 docs/tasks/DJ-01.md, podkrok DJ-01g).

Paczka testowa leży w ``fixtures/bundle_v1/`` w układzie ZIP-a (``manifest.json`` + ``images/``)
i jest pakowana w teście – każdy przypadek negatywny psuje kopię manifestu albo dokłada członka.
Manifest ma kształt z **implementacji** eksportu (``backend/apps/cms/export_bundle.py``): bloki
jako ``{"type", "value"}``, obrazy/dokumenty jako ``{"image_id"}``/``{"document_id"}``, ``None``
w pustych polach opcjonalnych. Są w nim wszystkie typy stron i bloków z tabel 6.1/6.2 oraz
wartości wrogie (``<script>``, ``onerror``, ``javascript:``, film spoza YouTube/Vimeo, kotwica
niebędąca slugiem, dokument spoza paczki).
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.importer import services

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).parent / "fixtures" / "bundle_v1"
MAIN = "https://olimpiada.example"


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def manifest() -> dict:
    return json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))


def fixture_images() -> dict[str, bytes]:
    return {f"images/{path.name}": path.read_bytes() for path in (FIXTURE / "images").iterdir()}


def build_zip(data: dict | None = None, *, extra: dict[str, bytes] | None = None, images=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(data if data is not None else manifest()))
        for name, content in (fixture_images() if images is None else images).items():
            archive.writestr(name, content)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def bundle(data: dict | None = None, **kwargs) -> services.Bundle:
    return services.open_bundle(io.BytesIO(build_zip(data, **kwargs)))


def run(user, data=None, **kwargs) -> services.ImportReport:
    return services.run_import(bundle(data), user=user, **kwargs)


def state() -> dict:
    """Liczby obiektów – porównanie „ten sam stan” po ``--replace``."""
    from cms.models import CMSPlugin, Page, PageContent, PageUrl, Placeholder
    from djangocms_versioning.models import Version
    from filer.models import File

    from apps.pages.models import ArchiveMeta, DocumentMeta, MenuExtension, NewsMeta

    return {
        "pages": Page.objects.count(),
        "published": PageContent.objects.count(),
        "contents": PageContent.admin_manager.count(),
        "versions": Version.objects.count(),
        "placeholders": Placeholder.objects.count(),
        "plugins": CMSPlugin.objects.count(),
        "files": File.objects.count(),
        "menu": MenuExtension.objects.count(),
        "meta": NewsMeta.objects.count() + DocumentMeta.objects.count() + ArchiveMeta.objects.count(),
        "urls": sorted(PageUrl.objects.values_list("path", flat=True)),
    }


def content_of(path: str):
    from cms.models import PageContent, PageUrl

    page = PageUrl.objects.get(path=path.strip("/")).page
    return PageContent.admin_manager.get(page=page, language="pl")


def plugins(path: str, slot: str | None = None) -> list:
    from cms.models import CMSPlugin
    from cms.utils.plugins import downcast_plugins

    content = content_of(path)
    queryset = CMSPlugin.objects.filter(
        placeholder__object_id=content.pk, placeholder__content_type__model="pagecontent"
    )
    if slot is not None:
        queryset = queryset.filter(placeholder__slot=slot)
    return list(downcast_plugins(queryset.order_by("position")))


def types(path: str, slot: str) -> list[str]:
    return [plugin.plugin_type for plugin in plugins(path, slot)]


# --- import: drzewo, adresy, menu, wtyczki, publikacja --------------------------------------------


def test_import_builds_tree_with_same_paths_and_publishes_every_page(superuser):
    from cms.models import Page, PageContent
    from djangocms_versioning.constants import PUBLISHED
    from djangocms_versioning.models import Version

    report = run(superuser)

    assert state()["urls"] == sorted(
        [
            "", "aktualnosci", "aktualnosci/ruszyla-rejestracja", "zadania", "archiwum", "archiwum/edycja-0",
            "archiwum/edycja-1", "wyniki", "harmonogram", "dokumenty", "dokumenty/regulamin",
            "dokumenty/komitety", "partnerzy", "kontakt", "faq", "dla-nauczycieli",
        ]
    )  # fmt: skip
    home = Page.objects.get(is_home=True)
    assert content_of("/").template == "dj/pages/home.html"
    # Dzieci strony głównej Wagtaila są na poziomie korzenia (rodzeństwo strony głównej).
    roots = [content_of_page(page).slug for page in Page.get_root_nodes()]
    assert roots == [
        "home", "partnerzy", "harmonogram", "zadania", "wyniki", "dokumenty", "kontakt", "faq",
        "aktualnosci", "archiwum", "dla-nauczycieli",
    ]  # fmt: skip
    assert home.get_children().count() == 0
    # Każda strona opublikowana, autor wersji = użytkownik importu.
    assert PageContent.objects.count() == Page.objects.count() == 16
    assert set(Version.objects.values_list("state", flat=True)) == {PUBLISHED}
    assert set(Version.objects.values_list("created_by_id", flat=True)) == {superuser.pk}
    assert sum(report.pages.values()) == 16
    assert report.pages["cms.DocumentPage"] == 2
    # Szablon = typ strony Wagtaila.
    assert content_of("/dokumenty/regulamin/").template == "dj/pages/document.html"
    assert content_of("/archiwum/edycja-0/").template == "dj/pages/archive_edition.html"
    assert content_of("/faq/").template == "dj/pages/faq.html"


def content_of_page(page):
    from cms.models import PageContent

    return PageContent.admin_manager.get(page=page, language="pl")


def test_menu_flags_titles_and_navigation(superuser):
    from apps.pages.models import MenuExtension

    run(superuser)
    nav = {path: content_of(path).in_navigation for path in ["/", "/zadania/", "/aktualnosci/", "/archiwum/"]}
    # Strona główna zawsze (domek), ukryte slugi poza menu mimo „pokaż w menu” w Wagtailu.
    assert nav == {"/": True, "/zadania/": True, "/aktualnosci/": False, "/archiwum/": False}
    assert content_of("/dla-nauczycieli/").in_navigation is False  # show_in_menus = False
    # Dzieci spisu dokumentów – w nawigacji mimo show_in_menus = False (lista rozwijana Wagtaila).
    assert content_of("/dokumenty/regulamin/").in_navigation is True
    assert content_of("/dokumenty/komitety/").menu_title == "Komitety"
    assert content_of("/faq/").menu_title == "FAQ"
    flags = {
        content_of_page(ext.extended_object).slug: (ext.primary, ext.expand, ext.promote)
        for ext in MenuExtension.objects.select_related("extended_object")
    }
    assert flags == {
        "zadania": (True, False, False),
        "harmonogram": (True, False, False),
        "kontakt": (True, False, False),
        "dokumenty": (False, True, False),
        "komitety": (False, False, True),
    }


def test_plugins_per_slot_match_wagtail_fields(superuser):
    run(superuser)
    assert types("/", "hero") == ["HeroPlugin"]
    assert types("/", "timeline") == ["StageTimelinePlugin"]
    assert types("/", "steps") == ["StepsSectionPlugin", "StepPlugin", "StepPlugin"]
    assert types("/", "about") == ["AboutSectionPlugin", "HeadingPlugin", "TextPlugin", "NoticePlugin"]
    timeline = plugins("/", "timeline")[0]
    assert (timeline.variant, timeline.heading) == ("home", "")
    hero = plugins("/", "hero")[0]
    assert hero.title == "Przyszłość ma naturę kwantową."
    assert "<strong>szkół ponadpodstawowych</strong>" in hero.text

    assert types("/zadania/", "intro") == ["TextPlugin"]
    assert types("/zadania/", "problems") == ["ProblemsPlugin"]
    assert plugins("/zadania/", "problems")[0].closed_notice == "Zadania pojawią się 1 października."
    assert types("/zadania/", "body") == ["TextPlugin"]
    assert types("/wyniki/", "results") == ["ResultsPlugin"]

    body = plugins("/harmonogram/", "body")
    assert [p.plugin_type for p in body] == [
        "HeadingPlugin", "StageTimelinePlugin", "HeadingPlugin", "SchedulePlugin", "ScheduleRowPlugin",
        "ScheduleRowPlugin", "DefinitionListPlugin", "DefinitionItemPlugin", "DefinitionItemPlugin",
    ]  # fmt: skip
    stage = body[1]
    assert (stage.variant, stage.heading) == ("block", "Etapy bieżącej edycji")
    rows = [p for p in body if p.plugin_type == "ScheduleRowPlugin"]
    assert rows[0].date_value == date(2026, 10, 10)
    assert rows[1].date_value is None and rows[1].time == "" and rows[1].lecturer == "dr X"
    schedule = next(p for p in body if p.plugin_type == "SchedulePlugin")
    assert schedule.lecturer_label == "" and schedule.has_lecturer is True
    attachment = plugins("/harmonogram/", "attachments")[0]
    assert (attachment.label, attachment.url, attachment.extension, attachment.size_bytes) == (
        "PDF do druku",
        f"{MAIN}/documents/5/regulamin.pdf",
        "pdf",
        254113,
    )
    assert attachment.file_id is None


def test_news_documents_archive_faq_and_partners(superuser):
    from apps.pages.models import ArchiveMeta, DocumentMeta, NewsMeta

    run(superuser)
    news = NewsMeta.objects.get(extended_object=content_of("/aktualnosci/ruszyla-rejestracja/"))
    assert (news.date, news.lead) == (date(2026, 9, 1), "Konto zakłada się samodzielnie.")
    body = plugins("/aktualnosci/ruszyla-rejestracja/", "body")
    assert [p.plugin_type for p in body] == [
        "TextPlugin",
        "ImageWithCaptionPlugin",
        "DocumentLinkPlugin",
        "EmbedPlugin",
    ]
    image = body[1]
    assert image.caption == "Finał 2026"
    assert image.image.default_alt_text == "Uczestnicy finału przy tablicy"
    assert image.image.folder.name == services.IMPORT_FOLDER_NAME
    link = body[2]
    assert (link.label, link.title, link.url) == (
        "Pobierz regulamin",
        "Regulamin (PDF)",
        f"{MAIN}/documents/5/regulamin.pdf",
    )
    embed = body[3]
    assert embed.src == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ" and embed.title

    meta = DocumentMeta.objects.get(extended_object=content_of("/dokumenty/regulamin/"))
    assert (meta.version_label, meta.document_date, meta.status_label) == (
        "1.0",
        date(2026, 9, 20),
        "Projekt do zatwierdzenia",
    )
    # Załącznik z adresem ``javascript:`` wypada; blok z dokumentem spoza paczki też.
    assert types("/dokumenty/regulamin/", "attachments") == ["AttachmentPlugin"]
    assert types("/dokumenty/regulamin/", "body") == ["NoticePlugin", "HeadingPlugin", "TextPlugin"]

    # Spis archiwum: kolejność ``-pk`` Wagtaila = kolejność drzewa djcms.
    from cms.models import PageUrl

    archive = PageUrl.objects.get(path="archiwum").page
    assert [content_of_page(child).slug for child in archive.get_children()] == ["edycja-1", "edycja-0"]
    assert ArchiveMeta.objects.get(extended_object=content_of("/archiwum/edycja-0/")).edition_id == 2
    assert ArchiveMeta.objects.get(extended_object=content_of("/archiwum/edycja-1/")).edition_id is None
    materials = plugins("/archiwum/edycja-0/", "documents")
    assert [(m.kind, m.title) for m in materials] == [("PROBLEMS", "Zadania etapu I"), ("OTHER", "Inne")]
    assert types("/archiwum/edycja-0/", "results") == ["ArchiveResultsPlugin"]
    assert types("/archiwum/edycja-1/", "summary") == []

    faq = plugins("/faq/", "faq")
    assert [(e.section, e.anchor) for e in faq] == [
        ("Konto", "pytanie-41"),
        ("Konto", "pytanie-42"),
        ("Rozwiązania", "pytanie-43"),
    ]
    assert "<strong>spam</strong>" in faq[0].answer

    partners = plugins("/partnerzy/", "partners")
    assert [(p.name, p.level) for p in partners] == [
        ("Instytut Testowy", "partner-naukowy"),
        ("Fundacja Bez Logo", "sponsor-zloty"),
    ]
    assert partners[0].logo is not None and partners[0].is_wide is True
    assert partners[1].logo is None and partners[1].url == ""
    become = plugins("/partnerzy/", "become_partner")[0]
    assert (become.title, become.contact_email) == ("Zostań partnerem", "partnerzy@olimpiada.example")


def test_hostile_markup_is_sanitized_and_reported(superuser):
    report = run(superuser)
    text = plugins("/aktualnosci/ruszyla-rejestracja/", "body")[0].body
    assert "<script" not in text and "alert(1)" not in text
    assert "onerror" not in text
    assert "javascript:" not in text
    assert 'href="/zadania/"' in text
    assert any("ruszyla-rejestracja" in line and "script" in line for line in report.sanitized)
    assert any("img[onerror]" in line for line in report.sanitized)
    assert any("a[href]" in line for line in report.sanitized)


def test_rejected_values_are_listed_in_report(superuser):
    report = run(superuser)
    skipped = "\n".join(report.skipped)
    assert "obrazu nie ma w paczce" in skipped
    assert "film spoza YouTube/Vimeo" in skipped
    assert "dokumentu nie ma w paczce" in skipped  # dokument #6 (zastrzeżony) i blok w regulaminie
    assert "nie jest adresem http(s)" in skipped  # dokument #7 z ``javascript:``
    assert "nie jest http(s)" in skipped  # adres partnera
    assert "'zła kotwica'" in skipped
    heading = [p for p in plugins("/harmonogram/", "body") if p.plugin_type == "HeadingPlugin"][1]
    assert heading.anchor == "za-kotwica"
    lines = "\n".join(report.lines())
    assert "Strony: 16" in lines and "Obrazy: 2 (nowe 2" in lines


def test_imported_pages_render(superuser, client):
    run(superuser)
    for path, needle in [
        ("/", "Przyszłość ma naturę kwantową."),
        ("/faq/", 'id="pytanie-41"'),
        ("/dokumenty/regulamin/", 'id="rozdzial-1"'),
        ("/partnerzy/", "Instytut Testowy"),
        ("/harmonogram/", "Liczby zespolone"),
        ("/aktualnosci/ruszyla-rejestracja/", "youtube-nocookie.com/embed/dQw4w9WgXcQ"),
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert needle in response.content.decode(), path


# --- tryby: odmowa, --if-empty, --replace, --dry-run ----------------------------------------------


@pytest.fixture
def bundle_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("paczka") / "paczka.zip"
    path.write_bytes(build_zip())
    return path


def call(*args):
    out = io.StringIO()
    call_command("import_cms_bundle", *args, stdout=out)
    return out.getvalue()


def test_second_import_without_flag_is_refused(superuser, bundle_file):
    call(str(bundle_file))
    before = state()
    with pytest.raises(CommandError, match="--replace"):
        call(str(bundle_file))
    with pytest.raises(services.ImportRefused):
        run(superuser)
    assert state() == before


def test_if_empty_imports_into_empty_site_and_is_a_noop_afterwards(superuser, bundle_file, main_api):
    assert "Strony: 16" in call(str(bundle_file), "--if-empty")
    before = state()
    out = call("--from-api", "--if-empty")
    assert "import pominięty" in out
    assert state() == before
    assert main_api.calls("export") == 0  # bez pobierania paczki


def test_replace_gives_the_same_state_without_duplicate_images(superuser, bundle_file, media_root):
    from filer.models import Image

    call(str(bundle_file))
    first = state()
    old_files = sorted(image.file.name for image in Image.objects.all())
    out = call(str(bundle_file), "--replace")
    assert "skasowano stron 16, plików filera" in out
    assert state() == first
    assert Image.objects.count() == 2
    assert len(set(Image.objects.values_list("sha1", flat=True))) == 2
    assert sorted(image.file.name for image in Image.objects.all()) != old_files


def test_replace_removes_old_image_files_only_after_commit(superuser, django_capture_on_commit_callbacks):
    from filer.models import Image

    run(superuser)
    old = [image.file.name for image in Image.objects.all()]
    storage = Image.objects.first().file.storage
    with django_capture_on_commit_callbacks(execute=True):
        run(superuser, replace=True)
        # Przed zatwierdzeniem stare pliki są na miejscu – wycofany import ich nie traci.
        assert all(storage.exists(name) for name in old)
    assert not any(storage.exists(name) for name in old)
    assert all(storage.exists(image.file.name) for image in Image.objects.all())


def test_replace_keeps_files_outside_the_import_folder(superuser):
    from apps.blocks.tests.factories import filer_image

    own = filer_image("wlasny.png", size=(10, 10))
    run(superuser)
    run(superuser, replace=True)
    own.refresh_from_db()
    assert own.file.storage.exists(own.file.name)


def test_dry_run_changes_nothing_and_leaves_no_files(superuser, bundle_file, media_root):
    empty = state()
    out = call(str(bundle_file), "--dry-run")
    assert "Tryb próbny" in out and "Strony: 16" in out
    assert state() == empty
    assert not [path for path in media_root.rglob("*") if path.is_file()]


def test_dry_run_replace_keeps_existing_pages(superuser, bundle_file):
    call(str(bundle_file))
    before = state()
    call(str(bundle_file), "--replace", "--dry-run")
    assert state() == before


def test_images_are_deduplicated_by_sha1(superuser):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from filer.models import Image

    data = (FIXTURE / "images" / "17-logo-pasek.png").read_bytes()
    existing = Image.objects.create(
        original_filename="juz.png", file=SimpleUploadedFile("juz.png", data), name="Już jest"
    )
    assert existing.sha1 == hashlib.sha1(data).hexdigest()
    report = run(superuser)
    assert (report.images_created, report.images_reused) == (1, 1)
    assert plugins("/partnerzy/", "partners")[0].logo_id == existing.pk
    assert Image.objects.count() == 2


# --- źródła: stdin, --from-api, --user ------------------------------------------------------------


def test_stdin_source(superuser, monkeypatch):
    import sys

    class Stdin:
        buffer = io.BytesIO(build_zip())

    monkeypatch.setattr(sys, "stdin", Stdin)
    assert "Strony: 16" in call("-")


def test_from_api_source(superuser, main_api):
    main_api.set("export", raw=build_zip())
    out = call("--from-api")
    assert "Pobrano paczkę z API" in out and "Strony: 16" in out
    assert main_api.requests[0].get_header("Accept") == "application/zip"


def test_from_api_failure_is_a_command_error(superuser, main_api):
    with pytest.raises(CommandError, match="Nie udało się pobrać paczki"):
        call("--from-api")


def test_exactly_one_source_is_required(superuser, bundle_file):
    with pytest.raises(CommandError, match="dokładnie jedno źródło"):
        call()
    with pytest.raises(CommandError, match="dokładnie jedno źródło"):
        call(str(bundle_file), "--from-api")


def test_user_option(superuser, editor, bundle_file):
    from djangocms_versioning.models import Version

    with pytest.raises(CommandError, match="Brak aktywnego użytkownika"):
        call(str(bundle_file), "--user", "nikt")
    call(str(bundle_file), "--user", editor.username)
    assert set(Version.objects.values_list("created_by_id", flat=True)) == {editor.pk}


def test_without_superuser_import_needs_user(db, bundle_file):
    with pytest.raises(CommandError, match="Brak aktywnego superusera"):
        call(str(bundle_file))


# --- walidacja paczki -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../evil.png",
        "/etc/passwd",
        "images/../../x.png",
        "images/sub/x.png",
        "notatka.txt",
        "images\\x.png",
        "C:x",
    ],
)
def test_zip_slip_and_foreign_members_are_rejected(name):
    with pytest.raises(services.BundleError, match="niedozwolona nazwa"):
        bundle(extra={name: b"x"})


def test_not_a_zip_is_rejected():
    with pytest.raises(services.BundleError, match="ZIP"):
        services.open_bundle(io.BytesIO(b"to nie jest zip"))


def test_missing_manifest_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("images/1-a.png", b"x")
    with pytest.raises(services.BundleError, match="brak manifest.json"):
        services.open_bundle(io.BytesIO(buffer.getvalue()))


@pytest.mark.parametrize(("key", "value"), [("format", "inny-format"), ("version", 2), ("pages", {})])
def test_wrong_format_or_version_is_rejected(key, value):
    data = manifest()
    data[key] = value
    with pytest.raises(services.BundleError):
        bundle(data)


def test_too_many_members_is_rejected(monkeypatch):
    monkeypatch.setattr(services, "MAX_MEMBERS", 2)
    with pytest.raises(services.BundleError, match="członków"):
        bundle()


def test_too_large_bundle_is_rejected(monkeypatch):
    monkeypatch.setattr(services, "MAX_UNCOMPRESSED_BYTES", 1000)
    with pytest.raises(services.BundleError, match="przekracza"):
        bundle(extra={"images/99-duzy.png": b"\0" * 2000})


def test_invalid_image_is_rejected():
    data = manifest()
    junk = b"<svg onload=alert(1)></svg>"
    data["images"]["18"]["sha1"] = hashlib.sha1(junk).hexdigest()
    images = fixture_images()
    images["images/18-foto.png"] = junk
    with pytest.raises(services.BundleError, match="Pillow"):
        bundle(data, images=images)


def test_sha1_mismatch_is_rejected():
    data = manifest()
    data["images"]["17"]["sha1"] = "0" * 40
    with pytest.raises(services.BundleError, match="SHA-1"):
        bundle(data)


def test_image_missing_from_zip_is_rejected():
    images = fixture_images()
    del images["images/17-logo-pasek.png"]
    with pytest.raises(services.BundleError, match="brak pliku"):
        bundle(images=images)


@pytest.mark.parametrize("slug", ["admin", "static", "media", "healthz", "internal", "filer"])
def test_reserved_root_slug_is_rejected(slug):
    data = manifest()
    next(dto for dto in data["pages"] if dto["slug"] == "kontakt")["slug"] = slug
    with pytest.raises(services.BundleError, match="zarezerwowany"):
        bundle(data)


def test_reserved_slug_deeper_in_the_tree_is_allowed():
    data = manifest()
    next(dto for dto in data["pages"] if dto["slug"] == "komitety")["slug"] = "admin"
    assert bundle(data).pages


def test_partner_level_outside_bundle_vocabulary_is_rejected():
    data = manifest()
    partners = next(dto for dto in data["pages"] if dto["slug"] == "partnerzy")["fields"]["partners"]
    partners[0]["value"]["level"] = "sponsor-kosmiczny"
    with pytest.raises(services.BundleError, match="poziom partnera"):
        bundle(data)


def test_partner_level_unknown_to_djcms_is_skipped(superuser):
    data = manifest()
    data["vocabularies"]["partner_levels"].append(["mecenas", "mecenas"])
    partners = next(dto for dto in data["pages"] if dto["slug"] == "partnerzy")["fields"]["partners"]
    partners[1]["value"]["level"] = "mecenas"
    report = run(superuser, data)
    assert [p.name for p in plugins("/partnerzy/", "partners")] == ["Instytut Testowy"]
    assert any("nieznany w djcms" in line for line in report.skipped)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda pages: pages.append({**copy.deepcopy(pages[1]), "parent_id": 999}),  # rodzic nieznany
        lambda pages: pages.append({**copy.deepcopy(pages[1])}),  # powtórzony identyfikator
        lambda pages: pages.append({**copy.deepcopy(pages[1]), "id": 500, "parent_id": None}),  # dwa korzenie
        lambda pages: pages[1].update(fields=[]),  # zły typ pól
        lambda pages: pages[1].update(slug="zły/slug"),  # slug z ukośnikiem
    ],
)
def test_inconsistent_tree_is_rejected(mutate):
    data = manifest()
    mutate(data["pages"])
    with pytest.raises(services.BundleError):
        bundle(data)


def test_rejected_bundle_changes_nothing(superuser, bundle_file):
    data = manifest()
    data["version"] = 99
    path = bundle_file.parent / "zla.zip"
    path.write_bytes(build_zip(data))
    with pytest.raises(CommandError, match="Paczka odrzucona"):
        call(str(path))
    assert state()["pages"] == 0


def test_malformed_block_values_are_skipped_not_fatal(superuser):
    data = manifest()
    news = next(dto for dto in data["pages"] if dto["slug"] == "ruszyla-rejestracja")
    news["fields"]["body"] += [{"type": "image", "value": "obraz.png"}, {"value": "bez typu"}, "śmieć"]
    harmonogram = next(dto for dto in data["pages"] if dto["slug"] == "harmonogram")
    harmonogram["fields"]["body"].append(
        {"type": "definitions", "value": {"rows": ["zły wiersz", {"term": "A"}]}}
    )
    harmonogram["attachments"].append("zły załącznik")
    report = run(superuser, data)
    assert any("bez wartości" in line for line in report.skipped)
    assert any("blok bez typu" in line for line in report.skipped)
    items = [p for p in plugins("/harmonogram/", "body") if p.plugin_type == "DefinitionItemPlugin"]
    assert [(i.term, i.description) for i in items][-1] == ("A", "")
