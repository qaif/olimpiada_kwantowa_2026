"""Import paczki treści z Wagtaila (``olimpiada-cms-bundle`` v1) do django CMS – § 5.3 docs/tasks/DJ-01.md.

Paczkę buduje aplikacja główna (``backend/apps/cms/export_bundle.py``); kształt manifestu jest tam,
a nie tylko w specu – przy rozjeździe wygrywa implementacja eksportu. Trzy kroki, każdy osobno
testowalny:

1. ``open_bundle`` – walidacja paczki **zanim** cokolwiek trafi do bazy: format i wersja, limity
   ZIP-a (liczba członków, rozmiar po rozpakowaniu), nazwy członków (bez ``..``, bez ścieżek
   bezwzględnych, wyłącznie ``manifest.json`` i ``images/*``), SHA-1 i czytelność każdego obrazu
   (Pillow), spójność drzewa stron, poziomy partnerów ze słownika paczki, slugi zarezerwowane dla
   adresów aplikacji djcms na poziomie korzenia (reguła 5 z § 7),
2. ``import_images`` – obrazy do filera, deduplikacja po SHA-1 (``filer.models.File.sha1``): obraz,
   który już jest w bibliotece, nie powstaje drugi raz,
3. ``import_pages`` – strony przez ``cms.api.create_page``/``cms.api.add_plugin`` (żadnych zapisów
   wprost do tabel CMS-a), rozszerzenia treści na wersji roboczej, publikacja przez
   djangocms-versioning (``Version.publish``) – jako ostatni krok każdej strony.

``run_import`` spina to w **jednej** transakcji (``--replace`` kasuje stare strony w tej samej),
a ``--dry-run`` ją wycofuje. Pliki obrazów w magazynie transakcją nie są: po wycofaniu importer
kasuje te, które sam zapisał, a pliki starego importu kasuje dopiero po zatwierdzeniu
(``on_commit``) – wycofany ``--replace`` nie zostawia wierszy wskazujących skasowane pliki.

Bezpieczeństwo treści (reguła 4 z § 7): każdy HTML z paczki przechodzi przez sanityzator
djangocms-text (``djangocms_text.html.clean_html`` – ta sama funkcja, którą ``HTMLField`` i model
``Text`` wołają przy zapisie) **przed** ``add_plugin``, a raport wymienia, co sanityzator usunął.
``add_plugin`` nie woła ``clean()`` modeli, więc reguły formularzy sprawdzamy tu sami: adres pliku
wyłącznie ``http(s)``, film wyłącznie z YouTube/Vimeo, kotwice jako slug, e-mail jako e-mail.

Układ drzewa (§ 5.3 p. 3): strona korzenia paczki (``HomePage``) staje się stroną główną
(``set_as_homepage``), jej dzieci – stronami **na poziomie korzenia** (rodzeństwem strony głównej:
ta sama ścieżka ``/zadania/``), głębiej – te same relacje rodzic/dziecko. Kolejność korzenia =
strona główna, potem ``vocabularies.menu.order``, potem reszta w kolejności drzewa Wagtaila; dzieci
spisu archiwum odwrotnie do identyfikatorów Wagtaila (tamten spis sortuje ``-pk``, a djcms rysuje
kolejność drzewa).
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import IO, Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email, validate_slug
from django.db import transaction
from django.utils.text import get_valid_filename, slugify

#: Nazwa i wersja formatu – te same co ``BUNDLE_FORMAT``/``BUNDLE_VERSION`` w eksporcie.
BUNDLE_FORMAT = "olimpiada-cms-bundle"
BUNDLE_VERSION = 1
MANIFEST_NAME = "manifest.json"
IMAGES_PREFIX = "images/"

#: Limity paczki (§ 5.3 p. 1). Także limit pobrania ``--from-api``.
MAX_MEMBERS = 2000
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_MANIFEST_BYTES = 50 * 1024 * 1024

LANGUAGE = "pl"
#: Folder filera na obrazy z importu. ``--replace`` kasuje wyłącznie jego zawartość – pliki, które
#: redaktorzy ``dj.`` wgrali sami do innych folderów, zostają.
IMPORT_FOLDER_NAME = "Import z Wagtaila"

#: Typ strony Wagtaila → szablon django CMS (tabela 6.1).
TEMPLATES = {
    "cms.HomePage": "dj/pages/home.html",
    "cms.NewsIndexPage": "dj/pages/news_index.html",
    "cms.NewsPage": "dj/pages/news.html",
    "cms.ContentPage": "dj/pages/content.html",
    "cms.PartnersPage": "dj/pages/partners.html",
    "cms.ProblemsPage": "dj/pages/problems.html",
    "cms.DocumentIndexPage": "dj/pages/document_index.html",
    "cms.DocumentPage": "dj/pages/document.html",
    "cms.ArchiveIndexPage": "dj/pages/archive_index.html",
    "cms.ArchiveEditionPage": "dj/pages/archive_edition.html",
    "cms.ResultsPage": "dj/pages/results.html",
    "cms.FAQPage": "dj/pages/faq.html",
}
FALLBACK_TEMPLATE = "dj/pages/content.html"

#: Bloki ``ArticleStreamBlock`` (ART) i ``DocumentStreamBlock`` (DOC) – tabela 6.1.
ART_BLOCKS = frozenset({"paragraph", "image", "document", "embed"})
DOC_BLOCKS = ART_BLOCKS | {"heading", "notice", "definitions", "schedule", "stage_timeline"}

#: Bloki, których wartością jest napis (HTML akapitu, adres filmu), a nie słownik pól.
TEXT_VALUE_BLOCKS = frozenset({"paragraph", "embed"})

#: Film bez tytułu w paczce (``EmbedBlock`` Wagtaila nie ma pola tytułu – tytuł ramki dawał oEmbed).
EMBED_DEFAULT_TITLE = "Film"


class BundleError(Exception):
    """Paczka nie przeszła walidacji – nic nie zostało zapisane."""


class ImportRefused(Exception):  # noqa: N818 - nazwa mówi, co się stało
    """W witrynie są strony, a nie podano ``--replace`` (ani ``--if-empty``)."""


# --- raport ---------------------------------------------------------------------------------------


@dataclass
class ImportReport:
    """Co powstało, co wypadło i co zmienił sanityzator – wypisywane przez komendę."""

    pages: Counter = field(default_factory=Counter)
    plugins: Counter = field(default_factory=Counter)
    images_created: int = 0
    images_reused: int = 0
    deleted_pages: int = 0
    deleted_files: int = 0
    skipped: list[str] = field(default_factory=list)
    sanitized: list[str] = field(default_factory=list)
    seconds: float = 0.0
    dry_run: bool = False
    source: dict = field(default_factory=dict)

    def lines(self) -> list[str]:
        source = self.source
        out = [
            f"Paczka {BUNDLE_FORMAT} v{BUNDLE_VERSION}: konkurs „{source.get('competition_slug', '?')}”, "
            f"źródło {source.get('main_public_url', '?')}, eksport {source.get('exported_at', '?')}.",
        ]
        if self.deleted_pages or self.deleted_files:
            out.append(
                f"--replace: skasowano stron {self.deleted_pages}, plików filera z folderu "
                f"„{IMPORT_FOLDER_NAME}” {self.deleted_files}."
            )
        out.append(f"Strony: {sum(self.pages.values())}" + _counter_suffix(self.pages))
        out.append(f"Wtyczki: {sum(self.plugins.values())}" + _counter_suffix(self.plugins))
        out.append(
            f"Obrazy: {self.images_created + self.images_reused} "
            f"(nowe {self.images_created}, z biblioteki po SHA-1 {self.images_reused})."
        )
        out.append(f"Pominięcia: {len(self.skipped)}")
        out.extend(f"  - {item}" for item in self.skipped)
        out.append(f"Sanityzacja HTML (usunięte znaczniki/atrybuty): {len(self.sanitized)}")
        out.extend(f"  - {item}" for item in self.sanitized)
        out.append(f"Czas: {self.seconds:.1f} s.")
        if self.dry_run:
            out.append("Tryb próbny (--dry-run): transakcja wycofana, nic nie zostało zapisane.")
        return out


def _counter_suffix(counter: Counter) -> str:
    if not counter:
        return "."
    return " (" + ", ".join(f"{key} {value}" for key, value in sorted(counter.items())) + ")."


# --- 1. walidacja paczki --------------------------------------------------------------------------


@dataclass
class Bundle:
    manifest: dict
    #: Bajty obrazów po identyfikatorze Wagtaila (klucz manifestu ``images``) – już zweryfikowane.
    images: dict[str, bytes]

    @property
    def pages(self) -> list[dict]:
        return self.manifest["pages"]

    @property
    def documents(self) -> dict[str, dict]:
        return self.manifest["documents"]

    @property
    def menu(self) -> dict:
        return self.manifest["vocabularies"]["menu"]


def _member_name_ok(name: str) -> bool:
    """Wyłącznie ``manifest.json`` i ``images/<plik>`` – bez ``..``, ukośników wstecznych, dysków."""
    if not name or name.startswith("/") or "\\" in name or ":" in name or "\x00" in name:
        return False
    if name == MANIFEST_NAME:
        return True
    if not name.startswith(IMAGES_PREFIX):
        return False
    rest = name[len(IMAGES_PREFIX) :]
    return bool(rest) and "/" not in rest and rest not in {".", ".."}


def open_bundle(src: str | Path | IO[bytes]) -> Bundle:
    """Otwiera i waliduje paczkę. Każda niezgodność → ``BundleError`` z powodem po polsku."""
    try:
        archive = zipfile.ZipFile(src)
    except zipfile.BadZipFile as exc:
        raise BundleError(f"to nie jest poprawny plik ZIP ({exc})") from None
    except OSError as exc:
        raise BundleError(f"nie da się odczytać paczki ({exc.strerror or type(exc).__name__})") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise BundleError(f"paczka ma {len(infos)} członków (limit {MAX_MEMBERS})")
        names: set[str] = set()
        total = 0
        for info in infos:
            if not _member_name_ok(info.filename):
                raise BundleError(f"niedozwolona nazwa w paczce: {info.filename!r}")
            if info.filename in names:
                raise BundleError(f"powtórzona nazwa w paczce: {info.filename!r}")
            if info.flag_bits & 0x1:
                raise BundleError(f"zaszyfrowany członek paczki: {info.filename!r}")
            names.add(info.filename)
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise BundleError(f"paczka po rozpakowaniu przekracza {MAX_UNCOMPRESSED_BYTES} B")
        if MANIFEST_NAME not in names:
            raise BundleError("brak manifest.json")
        manifest = _read_manifest(archive)
        _validate_manifest(manifest)
        images = _read_images(archive, manifest, names)
    _validate_pages(manifest)
    return Bundle(manifest=manifest, images=images)


def _read(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > limit:
        raise BundleError(f"{name}: plik większy niż {limit} B")
    try:
        with archive.open(info) as handle:
            data = handle.read(limit + 1)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        raise BundleError(f"{name}: uszkodzony członek paczki ({exc})") from None
    if len(data) > limit:
        raise BundleError(f"{name}: plik większy niż {limit} B")
    return data


def _read_manifest(archive: zipfile.ZipFile) -> dict:
    raw = _read(archive, MANIFEST_NAME, MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BundleError(f"manifest.json nie jest poprawnym JSON-em ({exc})") from None
    if not isinstance(manifest, dict):
        raise BundleError("manifest.json nie jest obiektem")
    return manifest


def _validate_manifest(manifest: dict) -> None:
    if manifest.get("format") != BUNDLE_FORMAT:
        raise BundleError(f"nieznany format paczki: {manifest.get('format')!r} (oczekiwany {BUNDLE_FORMAT})")
    if manifest.get("version") != BUNDLE_VERSION:
        raise BundleError(f"nieobsługiwana wersja paczki: {manifest.get('version')!r} (obsługiwana 1)")
    for key, kind in (("pages", list), ("images", dict), ("documents", dict), ("vocabularies", dict)):
        if not isinstance(manifest.get(key), kind):
            raise BundleError(f"manifest: pole {key!r} ma zły typ")
    if not isinstance(manifest.get("source", {}), dict):
        raise BundleError("manifest: pole 'source' ma zły typ")
    vocab = manifest["vocabularies"]
    levels = vocab.get("partner_levels")
    menu = vocab.get("menu")
    if not isinstance(levels, list) or not all(
        isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item) for item in levels
    ):
        raise BundleError("manifest: słownik partner_levels ma zły kształt")
    if not isinstance(menu, dict):
        raise BundleError("manifest: słownik menu ma zły kształt")
    for key in ("primary", "order", "hidden", "promoted_documents"):
        if not isinstance(menu.get(key, []), list) or not all(isinstance(x, str) for x in menu.get(key, [])):
            raise BundleError(f"manifest: menu.{key} ma zły kształt")
    if not isinstance(menu.get("titles", {}), dict):
        raise BundleError("manifest: menu.titles ma zły kształt")
    for key, document in manifest["documents"].items():
        if not isinstance(document, dict) or not str(key).isdigit():
            raise BundleError(f"manifest: dokument {key!r} ma zły kształt")


def _read_images(archive: zipfile.ZipFile, manifest: dict, names: set[str]) -> dict[str, bytes]:
    from PIL import Image as PILImage

    images: dict[str, bytes] = {}
    for key, meta in manifest["images"].items():
        if not str(key).isdigit() or not isinstance(meta, dict):
            raise BundleError(f"manifest: obraz {key!r} ma zły kształt")
        path = meta.get("path")
        if not isinstance(path, str) or not path.startswith(IMAGES_PREFIX) or path not in names:
            raise BundleError(f"obraz #{key}: brak pliku {path!r} w paczce")
        data = _read(archive, path, MAX_UNCOMPRESSED_BYTES)
        if hashlib.sha1(data, usedforsecurity=False).hexdigest() != meta.get("sha1"):
            raise BundleError(f"obraz #{key} ({path}): SHA-1 niezgodne z manifestem")
        try:
            with PILImage.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as exc:  # noqa: BLE001 - Pillow zgłasza różne klasy wyjątków dla złego pliku
            raise BundleError(
                f"obraz #{key} ({path}) nie otwiera się w Pillow ({type(exc).__name__})"
            ) from None
        images[str(key)] = data
    return images


def _validate_pages(manifest: dict) -> None:
    """Drzewo spójne: jeden korzeń, rodzic przed dzieckiem, poprawne typy pól, słowniki i slugi."""
    pages = manifest["pages"]
    if not pages:
        raise BundleError("paczka nie ma żadnej strony")
    seen: dict[int, dict] = {}
    roots = 0
    levels = {key for key, _ in manifest["vocabularies"]["partner_levels"]}
    reserved = set(settings.DJ_RESERVED_SLUGS)
    root_id = None
    for dto in pages:
        if not isinstance(dto, dict):
            raise BundleError("manifest: strona ma zły kształt")
        page_id = dto.get("id")
        parent_id = dto.get("parent_id")
        label = f"strona #{page_id} ({dto.get('url_path')!r})"
        if not isinstance(page_id, int) or isinstance(page_id, bool) or page_id in seen:
            raise BundleError(f"{label}: brak albo powtórzony identyfikator")
        for key, kind in (("type", str), ("slug", str), ("title", str), ("fields", dict)):
            if not isinstance(dto.get(key), kind):
                raise BundleError(f"{label}: pole {key!r} ma zły typ")
        if not dto["title"].strip():
            raise BundleError(f"{label}: pusty tytuł")
        for key in ("attachments", "faq_entries", "archive_documents"):
            if key in dto and not isinstance(dto[key], list):
                raise BundleError(f"{label}: pole {key!r} ma zły typ")
        if parent_id is None:
            roots += 1
            root_id = page_id
        elif parent_id not in seen:
            raise BundleError(f"{label}: rodzic #{parent_id} nie poprzedza strony w paczce")
        elif parent_id == root_id and dto["slug"] in reserved:
            raise BundleError(
                f"{label}: slug „{dto['slug']}” jest zarezerwowany dla adresu aplikacji djcms "
                f"({', '.join(sorted(reserved))}) – zmień go w Wagtailu przed importem"
            )
        if not re.fullmatch(r"[\w-]+", dto["slug"]) and parent_id is not None:
            raise BundleError(f"{label}: niepoprawny slug {dto['slug']!r}")
        for item in dto["fields"].get("partners") or []:
            value = item.get("value") if isinstance(item, dict) else None
            level = value.get("level") if isinstance(value, dict) else None
            if level not in levels:
                raise BundleError(f"{label}: poziom partnera {level!r} spoza słownika paczki")
        seen[page_id] = dto
    if roots != 1:
        raise BundleError(f"paczka ma {roots} stron bez rodzica (oczekiwana dokładnie jedna – strona główna)")


# --- sanityzacja ----------------------------------------------------------------------------------


class _MarkupInventory(HTMLParser):
    """Liczy znaczniki i atrybuty (``a``, ``a[href]``) – do raportu tego, co usunął sanityzator."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: Counter = Counter()

    def handle_starttag(self, tag, attrs):
        self.items[tag] += 1
        for name, _value in attrs:
            self.items[f"{tag}[{name}]"] += 1

    handle_startendtag = handle_starttag


def _inventory(html: str) -> Counter:
    parser = _MarkupInventory()
    parser.feed(html)
    parser.close()
    return parser.items


def sanitize(html: str | None, where: str, report: ImportReport) -> str:
    """HTML przez sanityzator djangocms-text; to, co usunął, trafia do raportu."""
    from djangocms_text.html import clean_html

    source = html or ""
    cleaned = clean_html(source)
    removed = _inventory(source) - _inventory(cleaned)
    if removed:
        details = ", ".join(f"{key}×{count}" for key, count in sorted(removed.items()))
        report.sanitized.append(f"{where}: {details}")
    return cleaned


# --- 2. obrazy ------------------------------------------------------------------------------------


def import_folder():
    from filer.models import Folder

    folder = Folder.objects.filter(name=IMPORT_FOLDER_NAME, parent__isnull=True).order_by("pk").first()
    return folder or Folder.objects.create(name=IMPORT_FOLDER_NAME)


def import_images(
    bundle: Bundle, folder, report: ImportReport, created: list | None = None
) -> dict[int, Any]:
    """Obrazy z paczki → ``filer.Image``; istniejący plik o tym samym SHA-1 jest używany ponownie.

    ``created`` dostaje każdy nowo zapisany obraz – wołający skasuje jego plik z magazynu, jeśli
    transakcja zostanie wycofana (``--dry-run`` albo błąd).
    """
    from django.core.files.uploadedfile import SimpleUploadedFile
    from filer.models import Image

    result: dict[int, Any] = {}
    for key, meta in bundle.manifest["images"].items():
        data = bundle.images[str(key)]
        existing = Image.objects.filter(sha1=meta["sha1"]).order_by("pk").first()
        if existing is not None:
            result[int(key)] = existing
            report.images_reused += 1
            continue
        basename = posixpath.basename(meta["path"])
        prefix = f"{key}-"
        original = basename[len(prefix) :] if basename.startswith(prefix) else basename
        filename = get_valid_filename(original) or f"obraz-{key}"
        image = Image.objects.create(
            folder=folder,
            name=_text(meta.get("title"), 255),
            default_alt_text=_text(meta.get("alt"), 255),
            original_filename=filename[:255],
            file=SimpleUploadedFile(filename, data),
            is_public=True,
        )
        if created is not None:
            created.append(image)
        result[int(key)] = image
        report.images_created += 1
    return result


# --- 3. strony ------------------------------------------------------------------------------------


def _text(value: Any, limit: int | None = None) -> str:
    text = "" if value is None else str(value)
    return text[:limit] if limit is not None else text


def _dicts(items: Any) -> list[dict]:
    """Elementy listy z paczki, które są słownikami – reszta (zły kształt) jest pomijana."""
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _http_url(value: Any) -> str:
    from apps.blocks.models import HTTP_URL_VALIDATOR

    url = _text(value).strip()
    if not url:
        return ""
    try:
        HTTP_URL_VALIDATOR(url)
    except ValidationError:
        return ""
    return url


class _PageBuilder:
    """Jedna strona: placeholdery wersji roboczej, dodawanie wtyczek z liczeniem do raportu."""

    def __init__(self, importer: _Importer, dto: dict, placeholders: dict):
        self.importer = importer
        self.dto = dto
        self.placeholders = placeholders
        self.label = f"{dto.get('url_path') or dto['slug']}"

    @property
    def report(self) -> ImportReport:
        return self.importer.report

    def skip(self, reason: str) -> None:
        self.report.skipped.append(f"{self.label}: {reason}")

    def cut(self, value: Any, limit: int, what: str) -> str:
        text = _text(value)
        if len(text) > limit:
            self.skip(f"{what} skrócone do {limit} znaków")
            return text[:limit]
        return text

    def add(self, slot: str, plugin_type: str, target=None, **data):
        from cms.api import add_plugin

        plugin = add_plugin(self.placeholders[slot], plugin_type, LANGUAGE, target=target, **data)
        self.report.plugins[plugin_type] += 1
        return plugin

    def image(self, ref: Any):
        """``filer.Image`` dla odwołania ``{"image_id": N}`` z paczki albo ``None``."""
        image_id = ref.get("image_id") if isinstance(ref, dict) else None
        return self.importer.images.get(image_id) if isinstance(image_id, int) else None

    def html(self, value: Any, where: str) -> str:
        return sanitize(_text(value), f"{self.label} {where}", self.report)

    def text(self, slot: str, value: Any, where: str, target=None) -> None:
        """Pole RichText → ``TextPlugin``. Puste pole = brak wtyczki (jak ``{% if page.intro %}``)."""
        if not _text(value).strip():
            return
        self.add(slot, "TextPlugin", target=target, body=self.html(value, where))

    # --- bloki StreamField ------------------------------------------------------------------------

    def stream(self, slot: str, items: Any, allowed: frozenset[str], where: str, target=None) -> None:
        for index, item in enumerate(items or []):
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                self.skip(f"{where}[{index}]: blok bez typu")
                continue
            kind, value = item["type"], item.get("value")
            if kind not in allowed:
                self.skip(f"{where}[{index}]: blok „{kind}” niedozwolony w tym slocie")
                continue
            if kind not in TEXT_VALUE_BLOCKS and not isinstance(value, dict):
                self.skip(f"{where}[{index}]: blok „{kind}” bez wartości – pominięty")
                continue
            handler = getattr(self, f"block_{kind}")
            handler(slot, value, f"{where}[{index}] {kind}", target)

    def block_paragraph(self, slot, value, where, target):
        self.text(slot, value, where, target=target)

    def block_image(self, slot, value, where, target):
        ref = value.get("image") or {}
        image = self.image(ref)
        if image is None:
            self.skip(f"{where}: obrazu nie ma w paczce – blok pominięty")
            return
        self.add(
            slot,
            "ImageWithCaptionPlugin",
            target=target,
            image=image,
            caption=self.cut(value.get("caption"), 250, f"{where} podpis"),
        )

    def _document(self, ref: Any, where: str) -> dict | None:
        document_id = ref.get("document_id") if isinstance(ref, dict) else None
        document = self.importer.bundle.documents.get(str(document_id)) if document_id is not None else None
        if document is None:
            self.skip(f"{where}: dokumentu nie ma w paczce (niepubliczny albo usunięty) – pominięty")
            return None
        url = _http_url(document.get("url"))
        if not url or len(url) > 500:
            self.skip(f"{where}: adres dokumentu nie jest adresem http(s) – pominięty")
            return None
        size = document.get("size")
        return {
            "title": _text(document.get("title"), 250),
            "url": url,
            "extension": _text(document.get("extension"), 10).lower(),
            "size_bytes": size if isinstance(size, int) and size >= 0 else None,
        }

    def block_document(self, slot, value, where, target):
        document = self._document(value.get("document"), where)
        if document is not None:
            self.add(
                slot,
                "DocumentLinkPlugin",
                target=target,
                label=self.cut(value.get("label"), 250, f"{where} etykieta"),
                **document,
            )

    def block_embed(self, slot, value, where, target):
        from apps.blocks.embeds import embed_src

        url = _http_url(value if isinstance(value, str) else "")
        if not url or len(url) > 500 or embed_src(url) is None:
            self.skip(f"{where}: film spoza YouTube/Vimeo ({_text(value)[:80]!r}) – pominięty")
            return
        self.add(slot, "EmbedPlugin", target=target, url=url, title=EMBED_DEFAULT_TITLE)

    def block_heading(self, slot, value, where, target):
        anchor = _text(value.get("anchor")).strip()
        try:
            validate_slug(anchor)
        except ValidationError:
            fixed = slugify(anchor)[:100] or f"sekcja-{self.report.plugins['HeadingPlugin'] + 1}"
            self.skip(f"{where}: kotwica {anchor!r} nie jest slugiem – zapisana jako {fixed!r}")
            anchor = fixed
        level = _text(value.get("level"))
        self.add(
            slot,
            "HeadingPlugin",
            target=target,
            text=self.cut(value.get("text"), 250, f"{where} tekst"),
            level=level if level in {"2", "3"} else "2",
            anchor=anchor[:100],
            in_toc=bool(value.get("in_toc", True)),
        )

    def block_notice(self, slot, value, where, target):
        tone = _text(value.get("tone"))
        self.add(
            slot,
            "NoticePlugin",
            target=target,
            tone=tone if tone in {"info", "warning"} else "info",
            text=self.html(value.get("text"), where),
        )

    def block_definitions(self, slot, value, where, target):
        parent = self.add(
            slot,
            "DefinitionListPlugin",
            target=target,
            term_label=self.cut(value.get("term_label"), 100, f"{where} nagłówek etykiet"),
            description_label=self.cut(value.get("description_label"), 100, f"{where} nagłówek wartości"),
        )
        for row in _dicts(value.get("rows")):
            self.add(
                slot,
                "DefinitionItemPlugin",
                target=parent,
                term=self.cut(row.get("term"), 500, f"{where} etykieta"),
                description=self.cut(row.get("description"), 1000, f"{where} wartość"),
            )

    def block_schedule(self, slot, value, where, target):
        parent = self.add(
            slot,
            "SchedulePlugin",
            target=target,
            caption=self.cut(value.get("caption"), 250, f"{where} podpis"),
            topic_label=self.cut(value.get("topic_label"), 100, f"{where} nagłówek"),
            date_label=self.cut(value.get("date_label"), 100, f"{where} nagłówek"),
            time_label=self.cut(value.get("time_label"), 100, f"{where} nagłówek"),
            lecturer_label=self.cut(value.get("lecturer_label"), 100, f"{where} nagłówek"),
        )
        for row in _dicts(value.get("rows")):
            self.add(
                slot,
                "ScheduleRowPlugin",
                target=parent,
                topic=self.cut(row.get("topic"), 250, f"{where} temat"),
                date=self.cut(row.get("date"), 100, f"{where} termin"),
                date_value=_date(row.get("date_value")),
                time=self.cut(row.get("time"), 100, f"{where} godziny"),
                lecturer=self.cut(row.get("lecturer"), 200, f"{where} prowadzący"),
            )

    def block_stage_timeline(self, slot, value, where, target):
        self.add(
            slot,
            "StageTimelinePlugin",
            target=target,
            variant="block",
            heading=self.cut(value.get("heading"), 200, f"{where} nagłówek"),
        )

    # --- załączniki, materiały archiwum, partnerzy, FAQ -------------------------------------------

    def attachments(self) -> None:
        for index, row in enumerate(_dicts(self.dto.get("attachments"))):
            document = self._document(row, f"załącznik[{index}]")
            if document is not None:
                self.add(
                    "attachments",
                    "AttachmentPlugin",
                    label=self.cut(row.get("label"), 100, f"załącznik[{index}] etykieta"),
                    **document,
                )

    def archive_documents(self) -> None:
        from apps.blocks.models import ArchiveDocument

        kinds = set(ArchiveDocument.Kind.values)
        for index, row in enumerate(_dicts(self.dto.get("archive_documents"))):
            document = self._document(row, f"materiał[{index}]")
            if document is None:
                continue
            document.pop("title")
            kind = _text(row.get("kind"))
            self.add(
                "documents",
                "ArchiveDocumentPlugin",
                kind=kind if kind in kinds else ArchiveDocument.Kind.OTHER,
                title=self.cut(row.get("title"), 200, f"materiał[{index}] etykieta"),
                **document,
            )

    def partners(self, items: Any) -> None:
        from apps.blocks.models import PARTNER_LEVELS

        known = {key for key, _ in PARTNER_LEVELS}
        for index, item in enumerate(items or []):
            value = item.get("value") or {}
            where = f"partner[{index}] „{_text(value.get('name'))[:60]}”"
            level = _text(value.get("level"))
            if level not in known:
                self.skip(f"{where}: poziom {level!r} nieznany w djcms – partner pominięty")
                continue
            logo = None
            ref = value.get("logo")
            if isinstance(ref, dict):
                logo = self.image(ref)
                if logo is None:
                    self.skip(f"{where}: logotypu nie ma w paczce – karta z inicjałami")
            url = _http_url(value.get("url"))
            if _text(value.get("url")).strip() and not url:
                self.skip(f"{where}: adres {_text(value.get('url'))[:80]!r} nie jest http(s) – pominięty")
            self.add(
                "partners",
                "PartnerPlugin",
                name=self.cut(value.get("name"), 200, f"{where} nazwa"),
                level=level,
                logo=logo,
                url=url[:300],
                description=self.cut(value.get("description"), 300, f"{where} opis"),
            )

    def become_partner(self, fields: dict) -> None:
        title, body = _text(fields.get("become_partner_title")), _text(fields.get("become_partner_body"))
        if not (title.strip() and body.strip()):
            return  # ``{% if page.become_partner_title and page.become_partner_body %}``
        email = _text(fields.get("contact_email")).strip()
        try:
            if email:
                validate_email(email)
        except ValidationError:
            self.skip(f"„Zostań partnerem”: adres {email!r} nie jest e-mailem – bez przycisku")
            email = ""
        self.add(
            "become_partner",
            "BecomePartnerPlugin",
            title=self.cut(title, 200, "„Zostań partnerem” nagłówek"),
            body=self.html(body, "„Zostań partnerem”"),
            contact_email=email[:254],
        )

    def faq(self) -> None:
        for index, entry in enumerate(_dicts(self.dto.get("faq_entries"))):
            anchor = _text(entry.get("anchor")).strip()
            try:
                validate_slug(anchor)
            except ValidationError:
                anchor = f"pytanie-{entry.get('id')}" if isinstance(entry.get("id"), int) else ""
            self.add(
                "faq",
                "FAQEntryPlugin",
                section=self.cut(entry.get("section"), 100, f"pytanie[{index}] sekcja"),
                question=self.cut(entry.get("question"), 250, f"pytanie[{index}]"),
                answer=self.html(entry.get("answer"), f"pytanie[{index}] odpowiedź"),
                anchor=anchor[:100],
            )


class _Importer:
    def __init__(self, bundle: Bundle, images: dict[int, Any], user, report: ImportReport):
        self.bundle = bundle
        self.images = images
        self.user = user
        self.report = report
        menu = bundle.menu
        self.primary = set(menu.get("primary", []))
        self.order = {slug: rank for rank, slug in enumerate(menu.get("order", []))}
        self.titles = {str(k): str(v) for k, v in (menu.get("titles") or {}).items()}
        self.hidden = set(menu.get("hidden", []))
        self.promoted = set(menu.get("promoted_documents", []))
        self.children: dict[int | None, list[dict]] = {}
        for dto in bundle.pages:
            self.children.setdefault(dto.get("parent_id"), []).append(dto)
        self.by_id = {dto["id"]: dto for dto in bundle.pages}

    def run(self) -> list:
        root = self.children[None][0]
        created = [self.create(root, parent=None, level="home")]
        created[0].set_as_homepage(self.user)
        top = sorted(
            self.children.get(root["id"], []),
            key=lambda dto: (self.order.get(dto["slug"], len(self.order)), dto.get("tree_path") or ""),
        )
        for dto in top:
            created.extend(self.subtree(dto, parent=None, level="top"))
        return created

    def subtree(self, dto: dict, parent, level: str) -> list:
        page = self.create(dto, parent=parent, level=level)
        result = [page]
        kids = self.children.get(dto["id"], [])
        if dto["type"] == "cms.ArchiveIndexPage":
            kids = sorted(kids, key=lambda child: -child["id"])  # spis archiwum w Wagtailu: ``-pk``
        else:
            kids = sorted(kids, key=lambda child: child.get("tree_path") or "")
        child_level = "expand-child" if dto["type"] == "cms.DocumentIndexPage" else "nested"
        for child in kids:
            result.extend(self.subtree(child, parent=page, level=child_level))
        return result

    def navigation(self, dto: dict, level: str) -> tuple[bool, str | None, dict[str, bool]]:
        """``in_navigation``, tytuł w menu i flagi ``MenuExtension`` (§ 5.3 p. 4, § 6.3).

        - strona główna – zawsze w nawigacji (domek, jak pierwsza pozycja menu Wagtaila),
        - poziom korzenia – ``show_in_menus`` i slug spoza ``hidden``; ``primary`` ze słownika,
          ``expand`` dla spisu dokumentów (w Wagtailu lista rozwijana to ten typ strony),
        - dzieci spisu dokumentów – **zawsze** w nawigacji: lista rozwijana Wagtaila bierze wszystkie
          opublikowane dzieci niezależnie od „pokaż w menu” (``_expandable_children``), a django CMS
          rysuje wyłącznie węzły „w nawigacji”; ``promote`` dla slugów ``promoted_documents``,
        - głębiej – ``show_in_menus`` (menu ich nie rysuje; flaga przenosi stan Wagtaila).
        """
        slug = dto["slug"]
        flags = {"primary": False, "expand": False, "promote": False}
        if level == "home":
            return True, None, flags
        if level == "top":
            flags["primary"] = slug in self.primary
            flags["expand"] = dto["type"] == "cms.DocumentIndexPage"
            visible = bool(dto.get("show_in_menus")) and slug not in self.hidden
            return visible, self.titles.get(slug), flags
        if level == "expand-child":
            flags["promote"] = slug in self.promoted
            return True, self.titles.get(slug) if flags["promote"] else None, flags
        return bool(dto.get("show_in_menus")), None, flags

    def create(self, dto: dict, parent, level: str):
        from cms.api import create_page
        from cms.models import PageContent
        from djangocms_versioning.models import Version

        from apps.pages.models import MenuExtension

        page_type = dto["type"]
        template = TEMPLATES.get(page_type)
        label = dto.get("url_path") or dto["slug"]
        if template is None:
            template = FALLBACK_TEMPLATE
            self.report.skipped.append(f"{label}: typ {page_type} bez mapowania – strona treści bez wtyczek")
        in_navigation, menu_title, flags = self.navigation(dto, level)
        page = create_page(
            _text(dto["title"], 255),
            template,
            LANGUAGE,
            slug=dto["slug"],
            created_by=self.user,
            parent=parent,
            in_navigation=in_navigation,
            menu_title=menu_title,
            page_title=_text(dto.get("seo_title"), 255) or None,
            meta_description=_text(dto.get("search_description")) or None,
            position="last-child",
        )
        if any(flags.values()):
            MenuExtension.objects.create(extended_object=page, **flags)
        content = PageContent.admin_manager.get(page=page, language=LANGUAGE)
        builder = _PageBuilder(self, dto, content.rescan_placeholders())
        if page_type in TEMPLATES:
            self.fill(builder, content, page_type)
        Version.objects.get_for_content(content).publish(self.user)
        self.report.pages[page_type] += 1
        return page

    def fill(self, b: _PageBuilder, content, page_type: str) -> None:
        """Pola strony → sloty i rozszerzenia (tabela 6.1). Rozszerzenia na wersji roboczej."""
        from apps.pages.models import ArchiveMeta, DocumentMeta, NewsMeta

        f = b.dto["fields"]
        if page_type == "cms.HomePage":
            if _text(f.get("hero_title")).strip() or _text(f.get("hero_text")).strip():
                b.add(
                    "hero",
                    "HeroPlugin",
                    title=b.cut(f.get("hero_title"), 200, "hasło"),
                    text=b.html(f.get("hero_text"), "hasło (tekst)"),
                )
            if f.get("show_timeline"):
                b.add("timeline", "StageTimelinePlugin", variant="home", heading="")
            steps = [item for item in _dicts(f.get("steps")) if item.get("type") == "step"]
            if _text(f.get("steps_title")).strip() and steps:  # ``{% if page.steps_title and page.steps %}``
                section = b.add(
                    "steps", "StepsSectionPlugin", title=b.cut(f.get("steps_title"), 200, "kroki")
                )
                for index, item in enumerate(steps):
                    value = item["value"] if isinstance(item.get("value"), dict) else {}
                    b.add(
                        "steps",
                        "StepPlugin",
                        target=section,
                        title=b.cut(value.get("title"), 120, f"krok[{index}] tytuł"),
                        text=b.cut(value.get("text"), 300, f"krok[{index}] opis"),
                    )
            if _text(f.get("about_title")).strip() and f.get("about_body"):
                about = b.add("about", "AboutSectionPlugin", title=b.cut(f.get("about_title"), 200, "o nas"))
                b.stream("about", f.get("about_body"), DOC_BLOCKS, "o-olimpiadzie", target=about)
        elif page_type in {"cms.NewsIndexPage", "cms.DocumentIndexPage", "cms.ArchiveIndexPage"}:
            b.text("intro", f.get("intro"), "wprowadzenie")
        elif page_type == "cms.NewsPage":
            news_date = _date(f.get("date"))
            meta: dict[str, Any] = {"lead": b.cut(f.get("lead"), 500, "lead")}
            if news_date is not None:
                meta["date"] = news_date
            else:
                b.skip("brak daty aktualności – dzisiejsza")
            NewsMeta.objects.create(extended_object=content, **meta)
            b.stream("body", f.get("body"), ART_BLOCKS, "treść")
        elif page_type in {"cms.ContentPage", "cms.DocumentPage"}:
            if page_type == "cms.DocumentPage":
                DocumentMeta.objects.create(
                    extended_object=content,
                    version_label=b.cut(f.get("version_label"), 50, "wersja"),
                    document_date=_date(f.get("document_date")),
                    status_label=b.cut(f.get("status_label"), 200, "status"),
                )
            b.text("intro", f.get("intro"), "wprowadzenie")
            b.attachments()
            b.stream("body", f.get("body"), DOC_BLOCKS, "treść")
        elif page_type == "cms.PartnersPage":
            b.text("intro", f.get("intro"), "wprowadzenie")
            b.partners(f.get("partners"))
            b.become_partner(f)
        elif page_type == "cms.ProblemsPage":
            b.text("intro", f.get("intro"), "wprowadzenie")
            b.add("problems", "ProblemsPlugin", closed_notice=b.cut(f.get("closed_notice"), 500, "komunikat"))
            b.stream("body", f.get("body"), ART_BLOCKS, "treść")
        elif page_type == "cms.ResultsPage":
            b.text("intro", f.get("intro"), "wprowadzenie")
            b.add("results", "ResultsPlugin")
        elif page_type == "cms.ArchiveEditionPage":
            edition_id = b.dto.get("archive_edition_id")
            valid = isinstance(edition_id, int) and not isinstance(edition_id, bool) and edition_id > 0
            ArchiveMeta.objects.create(extended_object=content, edition_id=edition_id if valid else None)
            b.text("summary", f.get("summary"), "podsumowanie")
            b.archive_documents()
            b.add("results", "ArchiveResultsPlugin")
        elif page_type == "cms.FAQPage":
            b.text("intro", f.get("intro"), "wprowadzenie")
            b.faq()


def import_pages(bundle: Bundle, images: dict[int, Any], user, report: ImportReport) -> list:
    """Tworzy i publikuje strony paczki. Wymaga otwartej transakcji (``set_as_homepage``)."""
    return _Importer(bundle, images, user, report).run()


# --- przebieg całości -----------------------------------------------------------------------------


def site_has_pages(site=None) -> bool:
    from cms.models import Page
    from django.contrib.sites.models import Site

    return Page.objects.filter(site=site or Site.objects.get_current()).exists()


def wipe(site, report: ImportReport) -> list:
    """``--replace``: wszystkie strony witryny i pliki filera z folderu importu.

    Zwraca skasowane obiekty plików – ich pliki w magazynie kasuje wołający dopiero po zatwierdzeniu
    transakcji (wycofany import musi zostawić je na miejscu, bo wiersze wracają).
    """
    from cms.models import Page
    from filer.models import File

    report.deleted_pages = Page.objects.filter(site=site).count()
    for root in Page.get_root_nodes().filter(site=site):
        root.delete()
    files = list(File.objects.filter(folder__name=IMPORT_FOLDER_NAME, folder__parent__isnull=True))
    File.objects.filter(pk__in=[item.pk for item in files]).delete()
    report.deleted_files = len(files)
    return files


def _delete_stored(files: list) -> None:
    """Pliki w magazynie po skasowanych wierszach filera – o ile żaden inny wiersz ich nie wskazuje."""
    from filer.models import File

    for item in files:
        name = getattr(item.file, "name", "")
        if not name or File.objects.filter(file=name).exists():
            continue
        try:
            if hasattr(item.file, "delete_thumbnails"):
                item.file.delete_thumbnails()
            item.file.storage.delete(name)
        except Exception:  # noqa: BLE001, S112 - sprzątanie magazynu nie może wywrócić importu
            continue


def run_import(bundle: Bundle, *, user, replace: bool = False, dry_run: bool = False) -> ImportReport:
    """Import w jednej transakcji. Bez ``replace`` i przy istniejących stronach → ``ImportRefused``."""
    from cms.models import Page
    from django.contrib.sites.models import Site
    from menus.menu_pool import menu_pool

    report = ImportReport(dry_run=dry_run, source={**bundle.manifest.get("source", {})})
    report.source["exported_at"] = bundle.manifest.get("exported_at", "?")
    started = time.monotonic()
    site = Site.objects.get_current()
    created_images: list = []
    committed = False
    try:
        with transaction.atomic():
            if Page.objects.filter(site=site).exists():
                if not replace:
                    raise ImportRefused(
                        "w witrynie są już strony – użyj --replace (pełny re-import) albo --if-empty"
                    )
                removed = wipe(site, report)
                transaction.on_commit(lambda: _delete_stored(removed))
            images = import_images(bundle, import_folder(), report, created=created_images)
            import_pages(bundle, images, user, report)
            if dry_run:
                transaction.set_rollback(True)
        committed = not dry_run
    finally:
        if not committed:
            _delete_stored(created_images)
    if committed:
        menu_pool.clear(site_id=site.pk, all=True)
    report.seconds = time.monotonic() - started
    return report
