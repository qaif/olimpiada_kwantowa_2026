"""Paczka treści redakcyjnej dla wersji porównawczej na django CMS (``olimpiada-cms-bundle`` v1).

Format i mapowania: ``docs/tasks/DJ-01.md`` § 5–6. ZIP z dwoma rodzajami członków:

- ``manifest.json`` – drzewo opublikowanych stron witryny konkursu z polami, blokami StreamField,
  załącznikami, pytaniami FAQ i słownikami (poziomy partnerów, reguły menu),
- ``images/<id>-<nazwa>`` – oryginały obrazów, do których odwołuje się treść (bloki obrazów,
  logotypy partnerów). Każdy z SHA-1 w manifeście – importer deduplikuje po nim w filerze.

Czego paczka **nie** niesie i dlaczego:

- **dokumentów (PDF/DOCX).** To dokumenty urzędowe (regulamin podpisany przez organizatora, RODO,
  standardy ochrony małoletnich). Druga kopia byłaby drugą wersją dokumentu prawnego, która przy
  pierwszej podmianie pliku w Wagtailu stałaby się nieaktualna. Manifest podaje więc bezwzględny
  adres na domenie głównej, gdzie widok ``apps.cms.views.serve`` dalej pilnuje widoczności
  kolekcji. Dokument z kolekcji zastrzeżonej nie trafia nawet do manifestu – blok i załącznik
  z takim plikiem są pomijane, a pominięcie ląduje w raporcie,
- **wersji roboczych.** Wyłącznie ``live()``: szkic nie może wyciec na drugą wersję serwisu tylną
  furtką importu. Z tego samego powodu odnośnik w tekście do strony nieopublikowanej traci ``<a>``,
- **danych aplikacji** – ustawień witryny, komunikatów, obecności na warsztatach. Wersja ``dj.``
  czyta je na żywo z API (``apps.cms.djcms_api``), bo jedno źródło prawdy ma zostać jedno.

Tekst formatowany wychodzi w postaci **frontowej** (``export_richtext``), ale nie jest tu
sanityzowany – robi to importer sanityzatorem ``djangocms-text`` przed zapisem, bo to on wie,
co jego edytor dopuszcza.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from typing import IO, Any

from django.conf import settings
from django.utils import timezone
from django.utils.html import escape
from wagtail import blocks
from wagtail.documents import get_document_model
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.embeds.blocks import EmbedBlock
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Page
from wagtail.rich_text import RichText
from wagtail.rich_text.rewriters import extract_attrs

from .blocks import PARTNER_LEVELS
from .context_processors import (
    HIDDEN_MENU_SLUGS,
    MENU_ORDER,
    MENU_TITLES,
    PRIMARY_MENU_SLUGS,
    PROMOTED_DOCUMENT_SLUGS,
)
from .djcms_api.serializers import _served_by_app
from .views import is_public_document

#: Nazwa i wersja formatu. Importer odrzuca paczkę z inną parą – zmiana kształtu, która nie jest
#: dopisaniem pola, podnosi wersję.
BUNDLE_FORMAT = "olimpiada-cms-bundle"
BUNDLE_VERSION = 1

MANIFEST_NAME = "manifest.json"
IMAGES_DIR = "images"

#: Odnośnik w tekście Wagtaila: ``<a linktype="page" id="8">…</a>``. Leniwe dopasowanie treści,
#: bo HTML nie pozwala zagnieżdżać ``<a>`` – pierwsze ``</a>`` zamyka ten znacznik.
A_TAG_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
HREF_ATTR_RE = re.compile(r'\bhref="([^"]*)"', re.IGNORECASE)


class _Skip:
    """Znacznik „ten blok wypada z paczki” (dokument z kolekcji zastrzeżonej)."""


SKIP = _Skip()


@dataclass
class BundleReport:
    """Co trafiło do paczki i co z niej wypadło – raport komendy i dziennika endpointu."""

    pages: int = 0
    images: int = 0
    documents: int = 0
    skipped: list[str] = field(default_factory=list)


def site_path(page, root) -> str:
    """Ścieżka strony względem korzenia witryny: ``/``, ``/regulamin/``, ``/dokumenty/rodo/``.

    Liczona z ``url_path`` (ścieżka w drzewie od korzenia instalacji), a nie z ``get_url``: to drugie
    zależy od liczby witryn i potrafi oddać adres bezwzględny z hostem, a na ``dj.`` ma stanąć ta
    sama ścieżka bez hosta.
    """
    return page.url_path[len(root.url_path) - 1 :] or "/"


def _front_href(href: str, main_public_url: str) -> str:
    """Zwykły odnośnik z tekstu: adres aplikacji (``/register/``) → bezwzględny na domenie głównej.

    Ścieżki stron Wagtaila, kotwice i adresy bezwzględne zostają bez zmian – strony po imporcie
    stoją na ``dj.`` pod tą samą ścieżką, a reszta i tak prowadzi tam, dokąd prowadziła.
    """
    if not href.startswith("/") or href.startswith("//"):
        return href
    path = href.split("#", 1)[0].split("?", 1)[0] or "/"
    return f"{main_public_url.rstrip('/')}{href}" if _served_by_app(path) else href


def export_richtext(
    html: str,
    *,
    site,
    main_public_url: str,
    page_paths: dict[int, str] | None = None,
    document_urls: dict[int, str | None] | None = None,
) -> str:
    """Tekst z formatu bazy Wagtaila do HTML-a frontowego z adresami działającymi na ``dj.``.

    - ``<a linktype="page" id=N>`` → ``href`` = ścieżka strony względem korzenia witryny (z kotwicą,
      jeśli odnośnik ją niesie); strona nieopublikowana albo spoza witryny → sam tekst bez ``<a>``,
    - ``<a linktype="document" id=N>`` → bezwzględny adres dokumentu na domenie głównej; dokument
      nieistniejący albo z kolekcji zastrzeżonej → sam tekst,
    - ``<a>`` z innym ``linktype`` (nieznany typ) → sam tekst: Wagtail rysuje go jako pusty ``<a>``,
    - zwykły ``<a href>`` do adresu aplikacji → adres bezwzględny (``_front_href``),
    - wszystko inne bez zmian (sanityzuje importer).

    ``page_paths``/``document_urls`` to bufory wołającego (eksport całej witryny liczy je raz);
    bez nich funkcja pyta bazę sama.
    """
    if not html:
        return ""
    if page_paths is None:
        root = site.root_page
        page_paths = {
            page.pk: site_path(page, root) for page in Page.objects.live().descendant_of(root, inclusive=True)
        }
    if document_urls is None:
        document_urls = {}

    def replace(match: re.Match) -> str:
        attrs = extract_attrs(match.group(1))
        inner = match.group(2)
        linktype = attrs.get("linktype")
        if linktype is None:
            href = attrs.get("href")
            if href is None:
                return match.group(0)
            new_href = _front_href(href, main_public_url)
            if new_href == href:
                return match.group(0)
            opening = HREF_ATTR_RE.sub(lambda _m: f'href="{escape(new_href)}"', match.group(1), count=1)
            return f"<a{opening}>{inner}</a>"
        if linktype == "page":
            path = page_paths.get(_int(attrs.get("id")))
            if path is None:
                return inner
            anchor = (attrs.get("anchor") or "").lstrip("#") or (attrs.get("href") or "").partition("#")[2]
            href = f"{path}#{anchor}" if anchor else path
            return f'<a href="{escape(href)}">{inner}</a>'
        if linktype == "document":
            url = _document_url(_int(attrs.get("id")), main_public_url, document_urls)
            return f'<a href="{escape(url)}">{inner}</a>' if url else inner
        return inner

    return A_TAG_RE.sub(replace, html)


def _int(value) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _document_url(document_id: int | None, main_public_url: str, cache: dict[int, str | None]) -> str | None:
    """Bezwzględny adres **publicznego** dokumentu albo ``None``. Wynik zostaje w buforze wołającego."""
    if document_id is None:
        return None
    if document_id not in cache:
        document = get_document_model().objects.filter(pk=document_id).select_related("collection").first()
        cache[document_id] = (
            f"{main_public_url.rstrip('/')}{document.url}"
            if document is not None and is_public_document(document)
            else None
        )
    return cache[document_id]


class _Exporter:
    """Jeden przebieg eksportu: zbiera obrazy i dokumenty w trakcie serializacji stron."""

    def __init__(self, competition, archive: zipfile.ZipFile, main_public_url: str):
        self.competition = competition
        self.site = competition.site
        self.root = self.site.root_page
        self.archive = archive
        self.main_public_url = main_public_url
        self.report = BundleReport()
        self.images: dict[str, dict] = {}
        self._image_refs: dict[int, dict | None] = {}
        self.documents: dict[str, dict] = {}
        self._document_refs: dict[int, dict | _Skip] = {}
        self.document_urls: dict[int, str | None] = {}
        self.page_paths: dict[int, str] = {}
        self.page_label = ""

    # --- obrazy i dokumenty ---------------------------------------------------------------------

    def image_ref(self, image) -> dict | None:
        """``{"image_id": N}`` i oryginał w ZIP-ie. Pliku, którego nie da się odczytać, nie ma w paczce."""
        if image is None:
            return None
        if image.pk not in self._image_refs:
            self._image_refs[image.pk] = self._add_image(image)
        return self._image_refs[image.pk]

    def _add_image(self, image) -> dict | None:
        try:
            with image.file.open("rb") as handle:
                data = handle.read()
        except Exception:  # noqa: BLE001 - brak pliku w magazynie to stan danych, nie błąd eksportu
            self.report.skipped.append(f"obraz #{image.pk} „{image.title}”: nie da się odczytać pliku")
            return None
        name = posixpath.basename(image.file.name.replace("\\", "/")) or f"obraz-{image.pk}"
        path = f"{IMAGES_DIR}/{image.pk}-{name}"
        self.archive.writestr(path, data)
        self.images[str(image.pk)] = {
            "title": image.title,
            "alt": image.default_alt_text,
            "path": path,
            "sha1": hashlib.sha1(data, usedforsecurity=False).hexdigest(),
            "width": image.width,
            "height": image.height,
        }
        self.report.images += 1
        return {"image_id": image.pk}

    def document_ref(self, document) -> dict | _Skip | None:
        """``{"document_id": N}`` dokumentu publicznego; zastrzeżony → ``SKIP`` (i wpis w raporcie)."""
        if document is None:
            return None
        if document.pk not in self._document_refs:
            self._document_refs[document.pk] = self._add_document(document)
        ref = self._document_refs[document.pk]
        if ref is SKIP:
            self.report.skipped.append(
                f"{self.page_label}: dokument #{document.pk} „{document.title}” z kolekcji zastrzeżonej"
            )
        return ref

    def _add_document(self, document) -> dict | _Skip:
        if not is_public_document(document):
            return SKIP
        size = document.file_size
        if size is None:
            # Bez ``get_file_size()``: tamta metoda zapisuje wynik do bazy, a eksport ma niczego
            # nie zmieniać. Brak pliku w magazynie daje ``None`` zamiast przerwanego eksportu.
            try:
                size = document.file.size
            except Exception:  # noqa: BLE001 - patrz wyżej
                size = None
        self.documents[str(document.pk)] = {
            "title": document.title,
            "filename": document.filename,
            "extension": document.file_extension.lower(),
            "size": size,
            "url": f"{self.main_public_url.rstrip('/')}{document.url}",
        }
        self.report.documents += 1
        return {"document_id": document.pk}

    # --- tekst i bloki --------------------------------------------------------------------------

    def richtext(self, value) -> str:
        source = value.source if isinstance(value, RichText) else (value or "")
        return export_richtext(
            source,
            site=self.site,
            main_public_url=self.main_public_url,
            page_paths=self.page_paths,
            document_urls=self.document_urls,
        )

    def block_value(self, block, value) -> Any:
        """Wartość bloku w postaci JSON; obraz/dokument jako odwołanie do manifestu.

        Kolejność sprawdzeń ma znaczenie: bloki wyboru obrazu i dokumentu są podklasami bloku
        wyboru, tekst formatowany – zwykłym ``FieldBlock``.
        """
        if isinstance(block, ImageChooserBlock):
            return self.image_ref(value)
        if isinstance(block, DocumentChooserBlock):
            return self.document_ref(value)
        if isinstance(block, blocks.RichTextBlock):
            return self.richtext(value)
        if isinstance(block, EmbedBlock):
            return getattr(value, "url", "") or ""
        if isinstance(block, blocks.StructBlock):
            result = {}
            for name, child in block.child_blocks.items():
                item = self.block_value(child, value.get(name))
                if item is SKIP:
                    return SKIP
                result[name] = item
            return result
        if isinstance(block, blocks.ListBlock):
            items = [self.block_value(block.child_block, item) for item in value]
            return [item for item in items if item is not SKIP]
        if isinstance(block, blocks.StreamBlock):
            return self.stream(value)
        if isinstance(block, blocks.DateBlock):
            return value.isoformat() if value else None
        return value

    def stream(self, value) -> list[dict]:
        """StreamField jako ``[{"type", "value"}]``. Blok z pominiętym dokumentem wypada w całości."""
        items = []
        for child in value or []:
            item = self.block_value(child.block, child.value)
            if item is SKIP:
                continue
            items.append({"type": child.block_type, "value": item})
        return items

    # --- strony ---------------------------------------------------------------------------------

    def pages(self) -> list[dict]:
        """Opublikowane strony witryny w kolejności drzewa. Strona pod nieopublikowanym rodzicem
        wypada: importer nie miałby jej gdzie powiesić."""
        live = list(Page.objects.live().descendant_of(self.root, inclusive=True).order_by("path").specific())
        ids_by_path = {page.path: page.pk for page in live}
        exported: list = []
        known: set[int] = set()
        for page in live:
            parent_id = None
            if page.pk != self.root.pk:
                # Rodzic bez zapytania: ścieżka treebearda bez ostatniego kroku.
                parent_id = ids_by_path.get(page.path[: -page.steplen])
                if parent_id not in known:
                    self.report.skipped.append(
                        f"strona „{page.title}” ({page.url_path}): rodzic nieopublikowany"
                    )
                    continue
            known.add(page.pk)
            exported.append((page, parent_id))
        self.page_paths.update({page.pk: site_path(page, self.root) for page, _ in exported})
        return [self.page_dto(page, parent_id) for page, parent_id in exported]

    def page_dto(self, page, parent_id) -> dict:
        self.page_label = f"strona „{page.title}” ({site_path(page, self.root)})"
        label = f"{page._meta.app_label}.{type(page).__name__}"
        dto: dict[str, Any] = {
            "id": page.pk,
            "parent_id": parent_id,
            "depth": page.depth,
            "tree_path": page.path,
            "type": label,
            "slug": page.slug,
            "url_path": site_path(page, self.root),
            "title": page.title,
            "seo_title": page.seo_title,
            "search_description": page.search_description,
            "show_in_menus": page.show_in_menus,
            "fields": self.fields(page),
        }
        if hasattr(page, "attachments"):
            dto["attachments"] = self.attachments(page)
        if label == "cms.FAQPage":
            dto["faq_entries"] = [
                {
                    "id": entry.pk,
                    "section": entry.section,
                    "question": entry.question,
                    "answer": self.richtext(entry.answer),
                    "anchor": entry.anchor,
                }
                for entry in page.entries.all()
            ]
        if label == "cms.ArchiveEditionPage":
            dto["archive_documents"] = self.archive_documents(page)
            dto["archive_edition_id"] = page.edition_id
        self.report.pages += 1
        return dto

    def fields(self, page) -> dict:
        """Pola zależne od typu strony (tabela 6.1 speca). Nieznany typ → pusty słownik i ostrzeżenie."""
        name = type(page).__name__
        rt = self.richtext
        if name == "HomePage":
            return {
                "hero_title": page.hero_title,
                "hero_text": rt(page.hero_text),
                "about_title": page.about_title,
                "about_body": self.stream(page.about_body),
                "show_timeline": page.show_timeline,
                "steps_title": page.steps_title,
                "steps": self.stream(page.steps),
            }
        if name in {"NewsIndexPage", "DocumentIndexPage", "ArchiveIndexPage", "ResultsPage", "FAQPage"}:
            return {"intro": rt(page.intro)}
        if name == "NewsPage":
            return {
                "date": page.date.isoformat() if page.date else None,
                "lead": page.lead,
                "body": self.stream(page.body),
            }
        if name == "ContentPage":
            return {
                "show_in_menu": page.show_in_menu,
                "intro": rt(page.intro),
                "body": self.stream(page.body),
            }
        if name == "PartnersPage":
            return {
                "intro": rt(page.intro),
                "partners": self.stream(page.partners),
                "become_partner_title": page.become_partner_title,
                "become_partner_body": rt(page.become_partner_body),
                "contact_email": page.contact_email,
            }
        if name == "ProblemsPage":
            return {
                "intro": rt(page.intro),
                "closed_notice": page.closed_notice,
                "body": self.stream(page.body),
            }
        if name == "DocumentPage":
            return {
                "intro": rt(page.intro),
                "body": self.stream(page.body),
                "version_label": page.version_label,
                "document_date": page.document_date.isoformat() if page.document_date else None,
                "status_label": page.status_label,
            }
        if name == "ArchiveEditionPage":
            return {"summary": rt(page.summary)}
        self.report.skipped.append(
            f"{self.page_label}: typ {name} bez mapowania – wyeksportowane same metadane"
        )
        return {}

    def attachments(self, page) -> list[dict]:
        rows = []
        for item in page.attachments.select_related("document", "document__collection").order_by(
            "sort_order"
        ):
            ref = self.document_ref(item.document)
            if isinstance(ref, dict):
                rows.append({"document_id": ref["document_id"], "label": item.label})
        return rows

    def archive_documents(self, page) -> list[dict]:
        rows = []
        for item in page.documents.select_related("document", "document__collection").order_by("sort_order"):
            ref = self.document_ref(item.document)
            if isinstance(ref, dict):
                rows.append({"kind": item.kind, "title": item.title, "document_id": ref["document_id"]})
        return rows


def vocabularies() -> dict:
    """Słowniki, bez których importer nie odtworzy układu: poziomy partnerów i reguły menu."""
    return {
        "partner_levels": [[key, label] for key, label in PARTNER_LEVELS],
        "menu": {
            "primary": list(PRIMARY_MENU_SLUGS),
            "order": list(MENU_ORDER),
            "titles": dict(MENU_TITLES),
            "hidden": sorted(HIDDEN_MENU_SLUGS),
            "promoted_documents": sorted(PROMOTED_DOCUMENT_SLUGS),
        },
    }


def build_bundle(competition, *, stream: IO[bytes], main_public_url: str | None = None) -> BundleReport:
    """Zapisuje paczkę treści konkursu do ``stream`` i oddaje raport.

    Strumień nie musi umieć ``seek`` (``--output -`` pisze na standardowe wyjście) – ``zipfile``
    dopisuje wtedy deskryptory danych za każdym członkiem. Manifest jest zapisywany **na końcu**:
    dopiero po przejściu stron wiadomo, które obrazy i dokumenty są w użyciu.
    """
    main_public_url = main_public_url or settings.DJCMS_MAIN_PUBLIC_URL
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        exporter = _Exporter(competition, archive, main_public_url)
        pages = exporter.pages()
        manifest = {
            "format": BUNDLE_FORMAT,
            "version": BUNDLE_VERSION,
            "exported_at": timezone.localtime().isoformat(),
            "source": {
                "site_domain": settings.SITE_DOMAIN,
                "main_public_url": main_public_url,
                "competition_slug": competition.slug,
                "root_page_id": exporter.root.pk,
            },
            "vocabularies": vocabularies(),
            "images": exporter.images,
            "documents": exporter.documents,
            "pages": pages,
        }
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=1))
    return exporter.report
