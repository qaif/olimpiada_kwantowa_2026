"""Paczka treści dla wersji ``dj.`` (``apps.cms.export_bundle``, komenda ``export_cms_bundle``, DJ-01 § 5).

Świat testu to drzewo z migracji + ``seed_cms`` + po jednej stronie każdego typu, z blokami każdego
rodzaju. Pilnujemy trzech rzeczy:

1. **do paczki trafia wyłącznie to, co publiczne** – strony ``live()``, dokumenty z kolekcji bez
   ograniczeń widoczności. Szkic i plik zastrzeżony nie mogą wyciec na drugą wersję serwisu tylną
   furtką importu, także jako odnośnik w tekście,
2. **tekst jest frontowy** – odnośnik do strony staje się ścieżką (z kotwicą), do dokumentu –
   bezwzględnym adresem na domenie głównej, do szkicu – samym tekstem,
3. **obrazy są kompletne i sprawdzalne** – oryginał w ZIP-ie, SHA-1 w manifeście zgodny z bajtami,
   jeden plik na obraz niezależnie od liczby odwołań.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from PIL import Image as PILImage
from wagtail.documents import get_document_model
from wagtail.embeds.blocks import EmbedValue
from wagtail.images import get_image_model
from wagtail.models import Collection, CollectionViewRestriction

from apps.cms.export_bundle import build_bundle, export_richtext
from apps.cms.models import (
    ArchiveDocument,
    ArchiveEditionPage,
    ArchiveIndexPage,
    ContentPage,
    ContentPageAttachment,
    DocumentIndexPage,
    DocumentPage,
    DocumentPageAttachment,
    FAQEntry,
    FAQPage,
    HomePage,
    PartnersPage,
    ProblemsPage,
)
from apps.competitions.tests.factories import EditionFactory

pytestmark = pytest.mark.django_db

MAIN = "https://olimpiada.example.test"

ALL_TYPES = {
    "cms.HomePage",
    "cms.NewsIndexPage",
    "cms.NewsPage",
    "cms.ContentPage",
    "cms.PartnersPage",
    "cms.ProblemsPage",
    "cms.DocumentIndexPage",
    "cms.DocumentPage",
    "cms.ArchiveIndexPage",
    "cms.ArchiveEditionPage",
    "cms.ResultsPage",
    "cms.FAQPage",
}


@pytest.fixture(autouse=True)
def _main_url(settings):
    settings.DJCMS_MAIN_PUBLIC_URL = MAIN


def png(color=(10, 20, 30), size=(12, 4)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def make_image(title: str, data: bytes):
    return get_image_model().objects.create(
        title=title, description=f"opis {title}", file=SimpleUploadedFile(f"{title}.png", data)
    )


def make_document(title: str, collection=None):
    return get_document_model().objects.create(
        title=title,
        collection=collection or Collection.get_first_root_node(),
        file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4 tresc", content_type="application/pdf"),
    )


@pytest.fixture
def world(competition):
    """Drzewo z każdym typem strony i każdym rodzajem bloku – patrz docstring modułu."""
    call_command("seed_cms", verbosity=0)
    home = HomePage.objects.get(pk=competition.site.root_page_id)
    restricted = Collection.get_first_root_node().add_child(name="Zastrzeżone")
    CollectionViewRestriction.objects.create(
        collection=restricted, restriction_type=CollectionViewRestriction.LOGIN
    )
    public_doc = make_document("regulamin")
    secret_doc = make_document("tajny", restricted)
    image = make_image("logo", png())
    problems = ProblemsPage.objects.child_of(home).get()
    draft = home.add_child(instance=ContentPage(title="Szkic", slug="szkic", live=False))

    home.hero_text = (
        f'<p>Zobacz <a linktype="page" id="{problems.pk}" anchor="terminy">zadania</a>, '
        f'<a linktype="document" id="{public_doc.pk}">regulamin</a>, '
        f'<a linktype="page" id="{draft.pk}">szkic</a>, '
        f'<a linktype="document" id="{secret_doc.pk}">tajny</a> i '
        f'<a href="/register/">rejestrację</a> oraz <a href="https://example.test/">zewnętrzny</a>.</p>'
    )
    home.about_body = [
        ("heading", {"text": "O nas", "level": "2", "anchor": "o-nas", "in_toc": True}),
        ("paragraph", f'<p>Akapit z <a linktype="page" id="{problems.pk}">odnośnikiem</a>.</p>'),
        ("image", {"image": image, "caption": "Podpis"}),
        ("document", {"document": public_doc, "label": "Pobierz"}),
        ("document", {"document": secret_doc, "label": "Zastrzeżony"}),
        ("notice", {"tone": "warning", "text": "<p>Uwaga</p>"}),
        (
            "definitions",
            {
                "term_label": "Cel",
                "description_label": "Podstawa",
                "rows": [{"term": "a", "description": "b"}],
            },
        ),
        ("stage_timeline", {"heading": "Terminy"}),
    ]
    home.steps_title = "Jak zacząć"
    home.steps = [("step", {"title": "Załóż konto", "text": "Rejestracja"})]
    home.save()

    content = home.add_child(
        instance=ContentPage(
            title="Warsztaty",
            slug="warsztaty",
            show_in_menu=True,
            intro="<p>Wstęp</p>",
            body=[
                ("embed", EmbedValue("https://www.youtube.com/watch?v=abc123")),
                (
                    "schedule",
                    {
                        "caption": "Plan",
                        "rows": [
                            {"topic": "Kubity", "date": "9 stycznia 2027", "date_value": date(2027, 1, 9)}
                        ],
                    },
                ),
            ],
        )
    )
    ContentPageAttachment.objects.create(page=content, document=public_doc, label="PDF do druku")
    ContentPageAttachment.objects.create(page=content, document=secret_doc, label="Tajny")

    documents = home.add_child(instance=DocumentIndexPage(title="Dokumenty", slug="dokumenty"))
    document_page = documents.add_child(
        instance=DocumentPage(
            title="Regulamin",
            slug="regulamin",
            version_label="1.0",
            document_date=date(2026, 9, 20),
            status_label="Obowiązuje",
        )
    )
    DocumentPageAttachment.objects.create(page=document_page, document=public_doc, label="PDF")
    DocumentPageAttachment.objects.create(page=document_page, document=secret_doc, label="Źródło")

    home.add_child(
        instance=PartnersPage(
            title="Partnerzy",
            slug="partnerzy",
            partners=[
                (
                    "partner",
                    {"name": "Instytut", "level": "partner-naukowy", "logo": image, "url": "https://i.test"},
                ),
                ("partner", {"name": "Bez logo", "level": "sponsor-zloty", "logo": None, "url": ""}),
            ],
            contact_email="partnerzy@example.test",
        )
    )

    faq = home.add_child(instance=FAQPage(title="FAQ", slug="faq"))
    entry = FAQEntry.objects.create(page=faq, section="Konto", question="Jak?", answer="<p>Tak.</p>")

    archive_index = ArchiveIndexPage.objects.child_of(home).get()
    edition = EditionFactory(competition=competition, year_label="Stara edycja")
    archived = archive_index.add_child(
        instance=ArchiveEditionPage(
            title="Stara edycja", slug="stara-edycja", edition=edition, summary="<p>Było</p>"
        )
    )
    ArchiveDocument.objects.create(page=archived, kind="PROBLEMS", title="Zadania", document=public_doc)
    ArchiveDocument.objects.create(page=archived, kind="SOLUTIONS", title="Tajne", document=secret_doc)

    # Strona opublikowana pod szkicem – importer nie miałby jej gdzie powiesić.
    draft.add_child(instance=ContentPage(title="Sierota", slug="sierota"))

    return {
        "home": home,
        "problems": problems,
        "draft": draft,
        "public_doc": public_doc,
        "secret_doc": secret_doc,
        "image": image,
        "faq_entry": entry,
        "edition": edition,
    }


def export(competition) -> tuple[dict, zipfile.ZipFile, object]:
    stream = io.BytesIO()
    report = build_bundle(competition, stream=stream)
    archive = zipfile.ZipFile(io.BytesIO(stream.getvalue()))
    return json.loads(archive.read("manifest.json")), archive, report


def page_by_slug(manifest: dict, slug: str) -> dict:
    return next(page for page in manifest["pages"] if page["slug"] == slug)


# --- manifest i drzewo ----------------------------------------------------------------------------


def test_manifest_header_and_vocabularies(competition, world):  # noqa: ARG001
    manifest, archive, _ = export(competition)

    assert (manifest["format"], manifest["version"]) == ("olimpiada-cms-bundle", 1)
    assert manifest["source"]["competition_slug"] == competition.slug
    assert manifest["source"]["main_public_url"] == MAIN
    assert manifest["source"]["root_page_id"] == competition.site.root_page_id
    assert ["patron-honorowy", "patron honorowy"] in manifest["vocabularies"]["partner_levels"]
    assert "zadania" in manifest["vocabularies"]["menu"]["primary"]
    assert manifest["vocabularies"]["menu"]["promoted_documents"] == ["komitety"]
    assert set(archive.namelist()) == {
        "manifest.json",
        *(item["path"] for item in manifest["images"].values()),
    }


def test_every_page_type_is_exported_in_tree_order(competition, world):  # noqa: ARG001
    manifest, _, _ = export(competition)
    pages = manifest["pages"]

    assert ALL_TYPES <= {page["type"] for page in pages}
    assert [page["tree_path"] for page in pages] == sorted(page["tree_path"] for page in pages)
    assert pages[0]["type"] == "cms.HomePage"
    assert pages[0]["parent_id"] is None
    assert pages[0]["url_path"] == "/"
    ids = {page["id"] for page in pages}
    assert all(page["parent_id"] in ids for page in pages[1:])
    assert page_by_slug(manifest, "regulamin")["url_path"] == "/dokumenty/regulamin/"


def test_only_live_pages_are_exported_and_orphans_are_reported(competition, world):  # noqa: ARG001
    manifest, _, report = export(competition)
    slugs = {page["slug"] for page in manifest["pages"]}

    assert "szkic" not in slugs
    assert "sierota" not in slugs
    assert any("Sierota" in line for line in report.skipped)
    assert report.pages == len(manifest["pages"])


# --- tekst formatowany ----------------------------------------------------------------------------


def test_richtext_links_become_front_end_links(competition, world):
    manifest, _, _ = export(competition)
    hero = manifest["pages"][0]["fields"]["hero_text"]
    doc = world["public_doc"]

    assert '<a href="/zadania/#terminy">zadania</a>' in hero
    assert f'<a href="{MAIN}/documents/{doc.pk}/{doc.filename}">regulamin</a>' in hero
    assert ", szkic, " in hero
    assert "/szkic/" not in hero
    assert ", tajny i " in hero
    assert f'<a href="{MAIN}/register/">rejestrację</a>' in hero
    assert '<a href="https://example.test/">zewnętrzny</a>' in hero
    assert "linktype" not in json.dumps(manifest, ensure_ascii=False)


def test_export_richtext_without_caches_queries_by_itself(competition, world):
    html = (
        f'<p><a linktype="page" id="{world["problems"].pk}">Zadania</a> <a linktype="bogus" id="1">x</a></p>'
    )

    assert export_richtext(html, site=competition.site, main_public_url=MAIN) == (
        '<p><a href="/zadania/">Zadania</a> x</p>'
    )


# --- bloki, dokumenty, obrazy ---------------------------------------------------------------------


def test_stream_blocks_reference_images_and_public_documents_only(competition, world):
    manifest, _, report = export(competition)
    about = manifest["pages"][0]["fields"]["about_body"]
    types = [block["type"] for block in about]

    assert types == ["heading", "paragraph", "image", "document", "notice", "definitions", "stage_timeline"]
    assert about[2]["value"] == {"image": {"image_id": world["image"].pk}, "caption": "Podpis"}
    assert about[3]["value"] == {"document": {"document_id": world["public_doc"].pk}, "label": "Pobierz"}
    assert about[1]["value"] == '<p>Akapit z <a href="/zadania/">odnośnikiem</a>.</p>'
    assert set(manifest["documents"]) == {str(world["public_doc"].pk)}
    assert manifest["documents"][str(world["public_doc"].pk)]["url"].startswith(f"{MAIN}/documents/")
    assert any("tajny" in line for line in report.skipped)
    assert manifest["pages"][0]["fields"]["steps"] == [
        {"type": "step", "value": {"title": "Załóż konto", "text": "Rejestracja"}}
    ]


def test_attachments_and_archive_documents_skip_restricted_files(competition, world):
    manifest, _, _ = export(competition)
    doc_id = world["public_doc"].pk

    assert page_by_slug(manifest, "warsztaty")["attachments"] == [
        {"document_id": doc_id, "label": "PDF do druku"}
    ]
    assert page_by_slug(manifest, "regulamin")["attachments"] == [{"document_id": doc_id, "label": "PDF"}]
    archived = page_by_slug(manifest, "stara-edycja")
    assert archived["archive_documents"] == [{"kind": "PROBLEMS", "title": "Zadania", "document_id": doc_id}]
    assert archived["archive_edition_id"] == world["edition"].pk


def test_type_specific_fields(competition, world):
    manifest, _, _ = export(competition)

    document = page_by_slug(manifest, "regulamin")["fields"]
    assert (document["version_label"], document["document_date"], document["status_label"]) == (
        "1.0",
        "2026-09-20",
        "Obowiązuje",
    )
    workshops = page_by_slug(manifest, "warsztaty")["fields"]
    assert workshops["show_in_menu"] is True
    assert workshops["body"][0] == {"type": "embed", "value": "https://www.youtube.com/watch?v=abc123"}
    assert workshops["body"][1]["value"]["rows"][0]["date_value"] == "2027-01-09"
    partners = page_by_slug(manifest, "partnerzy")["fields"]
    assert partners["partners"][0]["value"]["logo"] == {"image_id": world["image"].pk}
    assert partners["partners"][1]["value"]["logo"] is None
    faq = page_by_slug(manifest, "faq")
    assert faq["faq_entries"] == [
        {
            "id": world["faq_entry"].pk,
            "section": "Konto",
            "question": "Jak?",
            "answer": "<p>Tak.</p>",
            "anchor": f"pytanie-{world['faq_entry'].pk}",
        }
    ]
    news = next(page for page in manifest["pages"] if page["type"] == "cms.NewsPage")
    assert news["fields"]["date"]


def test_images_are_stored_once_with_a_matching_sha1(competition, world):
    manifest, archive, report = export(competition)
    image = world["image"]
    entry = manifest["images"][str(image.pk)]

    assert list(manifest["images"]) == [str(image.pk)]
    assert report.images == 1
    data = archive.read(entry["path"])
    assert entry["path"].startswith(f"images/{image.pk}-")
    assert entry["sha1"] == hashlib.sha1(data, usedforsecurity=False).hexdigest()
    assert (entry["width"], entry["height"]) == (12, 4)
    assert entry["alt"] == "opis logo"


# --- komenda --------------------------------------------------------------------------------------


def test_command_writes_the_bundle_to_stdout_and_the_report_to_stderr(competition, world):  # noqa: ARG001
    out, err = io.BytesIO(), io.StringIO()

    call_command("export_cms_bundle", "--output", "-", stdout=out, stderr=err)

    manifest = json.loads(zipfile.ZipFile(io.BytesIO(out.getvalue())).read("manifest.json"))
    assert manifest["source"]["competition_slug"] == competition.slug
    assert "stron" in err.getvalue()
    assert "pominięto" in err.getvalue()


def test_command_writes_to_a_file_for_the_named_competition(competition, tmp_path):
    target = tmp_path / "paczka.zip"

    call_command(
        "export_cms_bundle", "--competition", competition.slug, "--output", str(target), stderr=io.StringIO()
    )

    assert zipfile.is_zipfile(target)


def test_command_refuses_an_unknown_competition(tmp_path):
    with pytest.raises(CommandError):
        call_command("export_cms_bundle", "--competition", "nie-ma", "--output", str(tmp_path / "x.zip"))
