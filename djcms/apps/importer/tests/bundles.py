"""Paczki testowe ``olimpiada-cms-bundle`` v1/v2 – wspólne dla testów importu, drzewa startowego i kontroli.

Paczka v2 to fikstura v1 (``fixtures/bundle_v1``) z kluczami wersji 2 w kształcie z implementacji
eksportu (``backend/apps/cms/export_bundle.py``): ``competition``, ``data_pages`` (strona „Warsztaty”
dołożona tutaj i ``PartnersPage`` fikstury) i ``redirects`` (cel-strona po ``page_id``, cel-adres
po ``url``, w tym wpisy wrogie i niepoprawne).
"""

from __future__ import annotations

import copy
import io
import json
import zipfile
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "bundle_v1"

#: Identyfikatory stron fikstury (Wagtail) użyte w testach v2.
HOME_ID = 3
REGULAMIN_ID = 11
PARTNERS_ID = 22
WORKSHOPS_ID = 40
DRAFT_ID = 999  # strona spoza paczki (szkic) – cel przekierowania do pominięcia

WORKSHOPS_INTRO = '<p>Stare wprowadzenie <a href="/zadania/">zadania</a>.</p>'


def v1_manifest() -> dict:
    return json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))


def fixture_images() -> dict[str, bytes]:
    return {f"images/{path.name}": path.read_bytes() for path in (FIXTURE / "images").iterdir()}


def workshops_page() -> dict:
    """Strona „Warsztaty” (``ContentPage``): śródtytuł, dwie tabele, akapit, załącznik."""
    schedule = {
        "caption": "Warsztaty jesienne",
        "topic_label": "Temat",
        "date_label": "Termin",
        "time_label": "Godziny",
        "lecturer_label": None,
        "rows": [{"topic": "Kopia z importu", "date": "1 października", "date_value": "2026-10-01"}],
    }
    return {
        "id": WORKSHOPS_ID,
        "parent_id": HOME_ID,
        "depth": 3,
        "tree_path": "0001000100099",
        "type": "cms.ContentPage",
        "slug": "warsztaty",
        "url_path": "/warsztaty/",
        "title": "Warsztaty",
        "seo_title": "",
        "search_description": "",
        "show_in_menus": True,
        "fields": {
            "show_in_menu": True,
            "intro": WORKSHOPS_INTRO,
            "body": [
                {"type": "heading", "value": {"text": "Harmonogram", "level": "2", "anchor": "harmonogram"}},
                {"type": "schedule", "value": schedule},
                {"type": "paragraph", "value": "<p>Po warsztatach – materiały.</p>"},
                {"type": "schedule", "value": {**schedule, "caption": "Warsztaty wiosenne"}},
            ],
        },
        "attachments": [],
    }


def v2_redirects() -> list:
    return [
        {"old_path": "/regulamin", "is_permanent": True, "target": {"page_id": REGULAMIN_ID}},
        {"old_path": "/stary-kontakt", "is_permanent": False, "target": {"url": "/kontakt/#adres"}},
        {"old_path": "/zewnetrzny", "is_permanent": True, "target": {"url": "https://example.org/x"}},
        {"old_path": "/zly", "is_permanent": True, "target": {"url": "javascript:alert(1)"}},
        {"old_path": "/szkic", "is_permanent": True, "target": {"page_id": DRAFT_ID}},
        {"old_path": "/routowalna", "is_permanent": True, "target": {"page_id": 7, "route_path": "/2025/"}},
        {"old_path": "/wyniki", "is_permanent": True, "target": {"page_id": 7}},  # pętla po imporcie
        {"old_path": "/regulamin", "is_permanent": True, "target": {"url": "/"}},  # powtórzona ścieżka
        {"old_path": "bez-ukosnika", "is_permanent": True, "target": {"url": "/"}},
        "śmieć",
    ]


def v2_manifest(slug: str = "kwantowa", name: str = "Olimpiada Kwantowa", **overrides) -> dict:
    manifest = v1_manifest()
    manifest["version"] = 2
    manifest["source"]["competition_slug"] = slug
    manifest["competition"] = {"slug": slug, "name": name}
    manifest["pages"].append(workshops_page())
    manifest["data_pages"] = {"workshops": WORKSHOPS_ID, "partners": PARTNERS_ID}
    manifest["redirects"] = v2_redirects()
    manifest.update(overrides)
    return manifest


def starter_manifest(slug: str, name: str, *, extra_pages: int = 0) -> dict:
    """Drzewo startowe nowego konkursu – jak z ``templates_catalog.DEFAULT_PAGES``: bez obrazów."""
    base = v1_manifest()
    pages = [
        {"id": 100, "parent_id": None, "type": "cms.HomePage", "slug": slug, "title": name, "fields": {}},
        {
            "id": 101,
            "parent_id": 100,
            "type": "cms.NewsIndexPage",
            "slug": "aktualnosci",
            "title": "Aktualności",
        },
        {"id": 102, "parent_id": 100, "type": "cms.ProblemsPage", "slug": "zadania", "title": "Zadania"},
        {"id": 103, "parent_id": 100, "type": "cms.ResultsPage", "slug": "wyniki", "title": "Wyniki"},
    ]
    pages += [
        {"id": 200 + n, "parent_id": 101, "type": "cms.NewsPage", "slug": f"news-{n}", "title": f"News {n}"}
        for n in range(extra_pages)
    ]
    for page in pages:
        page.setdefault("fields", {})
        page.setdefault("show_in_menus", True)
        page["url_path"] = "/" if page["parent_id"] is None else f"/{page['slug']}/"
    return {
        "format": "olimpiada-cms-bundle",
        "version": 2,
        "exported_at": "2026-09-26T12:00:00+02:00",
        "source": {
            "site_domain": "olimpiada.example",
            "main_public_url": "https://x",
            "competition_slug": slug,
        },
        "competition": {"slug": slug, "name": name},
        "data_pages": {"workshops": None, "partners": None},
        "redirects": [],
        "vocabularies": copy.deepcopy(base["vocabularies"]),
        "images": {},
        "documents": {},
        "pages": pages,
    }


def build_zip(manifest: dict, *, images: dict[str, bytes] | None = None, extra: dict | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        wanted = {meta["path"] for meta in manifest.get("images", {}).values()}
        for name, content in (fixture_images() if images is None else images).items():
            if images is not None or name in wanted:
                archive.writestr(name, content)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()
