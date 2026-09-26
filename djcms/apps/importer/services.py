"""Import paczki treści z Wagtaila (``olimpiada-cms-bundle`` v1 i v2) do django CMS.

Format i mapowania: § 5.3 docs/tasks/DJ-01.md; wiele witryn: § 4.4 i D7–D9 docs/tasks/DJ-02.md.

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

Wiele witryn (DJ-02): każdy import trafia do witryny **jednego** konkursu (``target_site``,
``CompetitionSite`` z rejestru ``apps.sites``); ``--replace`` kasuje strony, przekierowania z importu
i obrazy z folderu importu **tej** witryny (``Konkurs: <nazwa> (<slug>) / Import z Wagtaila``), a
deduplikacja obrazów po SHA-1 nie sięga do folderów importu innych konkursów – ``--replace`` jednej
witryny nie może skasować pliku, na który wskazuje druga. Paczka v2 dokłada (DJ-02 § 4.4):

- ``competition`` – importer odmawia paczki innego konkursu niż witryna docelowa,
- ``data_pages`` – strony-dane (D9) redagowane dalej w Wagtailu: strona „Warsztaty” dostaje
  wtyczki żywe ``WorkshopSchedulePlugin`` (wprowadzenie i tabele harmonogramu z ``GET workshops``)
  zamiast kopii tabel, a strona partnerów – szablon ``dj/live/partners_page.html`` z jedną wtyczką
  ``PartnersLivePlugin`` (``GET partners``) zamiast kart partnerów,
- ``redirects`` – przekierowania witryny (``apps.importer.services.resolve_redirects``: cel-strona
  → ścieżka strony po imporcie), zapisywane w modelu ``dj_seo.Redirect`` (DJ-02f) ze źródłem
  ``import``.

Ścieżki stron sprawdza reguła S5 (``apps.pages.validation.path_collides_with_app``): strona pod
adresem aplikacji głównej (pierwszy segment zarezerwowany, ``warsztaty/materialy``, drugi segment
będący adresem aplikacji) odrzuca paczkę, zanim cokolwiek trafi do bazy.

Układ drzewa (§ 5.3 p. 3): strona korzenia paczki (``HomePage``) staje się stroną główną
(``set_as_homepage``), jej dzieci – stronami **na poziomie korzenia** (rodzeństwem strony głównej:
ta sama ścieżka ``/zadania/``), głębiej – te same relacje rodzic/dziecko. Kolejność korzenia =
strona główna, potem ``vocabularies.menu.order``, potem reszta w kolejności drzewa Wagtaila; dzieci
spisu archiwum odwrotnie do identyfikatorów Wagtaila (tamten spis sortuje ``-pk``, a djcms rysuje
kolejność drzewa).
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tempfile
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
from django.core.management.base import CommandError
from django.core.validators import validate_email, validate_slug
from django.db import transaction
from django.utils.text import get_valid_filename, slugify

#: Nazwa i wersje formatu – te same co ``BUNDLE_FORMAT``/``SUPPORTED_BUNDLE_VERSIONS`` w eksporcie.
BUNDLE_FORMAT = "olimpiada-cms-bundle"
BUNDLE_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)
MANIFEST_NAME = "manifest.json"
IMAGES_PREFIX = "images/"

#: Limity paczki (§ 5.3 p. 1). Także limit pobrania ``--from-api``.
MAX_MEMBERS = 2000
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_MANIFEST_BYTES = 50 * 1024 * 1024
#: Największy obraz (szerokość × wysokość). Zdjęcia z aparatu mają 12–24 Mpx; więcej to albo
#: pomyłka, albo bomba dekompresyjna. Ten sam limit ustawia ``PIL.Image.MAX_IMAGE_PIXELS``
#: (``config/settings/base.py``) – dla miniatur i wgrywania przez redaktorów.
MAX_IMAGE_PIXELS = 40_000_000
#: Porcja strumieniowego czytania członków paczki (SHA-1, kopia obrazu).
READ_CHUNK_BYTES = 1024 * 1024
#: Kopia obrazu z paczki do filera: w pamięci do tej wielkości, powyżej – plik tymczasowy
#: w ``MEDIA_ROOT`` (``/tmp`` kontenera to tmpfs 64 MB, czyli też pamięć).
IMAGE_SPOOL_BYTES = 8 * 1024 * 1024
#: Formaty obrazów (według Pillow, nie według nazwy pliku) → rozszerzenie pliku w filerze.
IMAGE_EXTENSIONS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp", "AVIF": "avif"}

LANGUAGE = "pl"
#: Folder filera na obrazy z importu – podfolder folderu konkursu (``competition_folder``).
#: ``--replace`` kasuje wyłącznie jego zawartość – pliki, które redaktorzy wgrali sami do innych
#: folderów, zostają. Folder o tej nazwie w korzeniu biblioteki to import sprzed DJ-02 (jedna
#: witryna): należy do konkursu domyślnego i jego ``--replace`` sprząta go razem z nowym.
IMPORT_FOLDER_NAME = "Import z Wagtaila"
#: Folder konkursu najwyższego poziomu (DJ-02 D5): ``Konkurs: <nazwa> (<slug>)``. Rozpoznawany po
#: końcówce ``(<slug>)`` – nazwa konkursu może się zmienić, slug nie.
COMPETITION_FOLDER_PREFIX = "Konkurs: "

#: Strona-dana partnerów (DJ-02 D9): szablon z jedną wtyczką żywą zamiast kart partnerów.
LIVE_PARTNERS_TEMPLATE = "dj/live/partners_page.html"
LIVE_PARTNERS_SLOT = "partners"

#: Źródło przekierowania zapisanego przez importer (``dj_seo.Redirect.source``, DJ-02 § 8).
REDIRECT_SOURCE_IMPORT = "import"
#: Limity ścieżek przekierowań – ścieżka dłuższa albo z bajtami sterującymi wypada z raportem.
MAX_REDIRECT_PATH = 255

SLUG_RE = re.compile(r"^[a-z0-9-]{1,50}$")

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


class ImportRefused(CommandError):  # noqa: N818 - nazwa mówi, co się stało
    """W witrynie są strony, a nie podano ``--replace`` (ani ``--if-empty``) – albo witryny nie ma.

    Podklasa ``CommandError``: wołana z komendy (także spoza jej ``try``, np. z ``site_has_pages``
    przy pustym rejestrze konkursów) kończy się czytelnym komunikatem i kodem 1, a nie śladem stosu.
    """


def target_site(site=None):
    """Witryna importu: podana jawnie albo witryna konkursu domyślnego (``apps.sites.registry``).

    Bez ``SITE_ID`` (DJ-02 D4) nie ma „witryny bieżącej” poza żądaniem – importer zawsze działa na
    witrynie wskazanej wprost (``--competition``/``--all`` – ``competition_site``).
    """
    if site is not None:
        return site
    from apps.sites.registry import RegistryError, default_site

    try:
        return default_site()
    except RegistryError as exc:
        raise ImportRefused(f"Brak witryny do importu: {exc}") from None


def competition_of(site):
    """``CompetitionSite`` witryny albo ``None`` (witryna spoza rejestru – tylko w testach i ręcznie)."""
    from apps.sites.models import CompetitionSite

    return CompetitionSite.objects.filter(site=site).first() if site is not None else None


def competition_site(slug: str):
    """Konkurs z rejestru po slugu – **aktywny**; inaczej ``ImportRefused`` z instrukcją."""
    from apps.sites.models import CompetitionSite

    found = CompetitionSite.objects.filter(slug=slug).select_related("site").first()
    if found is None:
        raise ImportRefused(
            f"Konkursu „{slug}” nie ma w rejestrze witryn – uruchom manage.py sync_competitions."
        )
    if not found.is_active:
        raise ImportRefused(f"Konkurs „{slug}” jest nieaktywny – import pominięty.")
    return found


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
    deleted_redirects: int = 0
    redirects: int = 0
    #: Strony-dane zamienione na wtyczki żywe (``warsztaty``/``partnerzy`` → ścieżka strony).
    data_pages: dict[str, str] = field(default_factory=dict)
    #: Ścieżki opublikowanych stron (``/``, ``/zadania/``) – dla ``verify_cutover``.
    paths: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    sanitized: list[str] = field(default_factory=list)
    seconds: float = 0.0
    dry_run: bool = False
    source: dict = field(default_factory=dict)
    version: int = BUNDLE_VERSION
    #: Slug konkursu witryny, do której szedł import (``None`` – witryna spoza rejestru).
    competition: str | None = None

    def lines(self) -> list[str]:
        source = self.source
        target = f" → witryna konkursu „{self.competition}”" if self.competition else ""
        out = [
            f"Paczka {BUNDLE_FORMAT} v{self.version}: konkurs „{source.get('competition_slug', '?')}”"
            f"{target}, źródło {source.get('main_public_url', '?')}, "
            f"eksport {source.get('exported_at', '?')}.",
        ]
        if self.deleted_pages or self.deleted_files or self.deleted_redirects:
            out.append(
                f"--replace: skasowano stron {self.deleted_pages}, plików filera z folderu "
                f"„{IMPORT_FOLDER_NAME}” {self.deleted_files}, "
                f"przekierowań z importu {self.deleted_redirects}."
            )
        out.append(f"Strony: {sum(self.pages.values())}" + _counter_suffix(self.pages))
        out.append(f"Wtyczki: {sum(self.plugins.values())}" + _counter_suffix(self.plugins))
        out.append(
            f"Obrazy: {self.images_created + self.images_reused} "
            f"(nowe {self.images_created}, z biblioteki po SHA-1 {self.images_reused})."
        )
        if self.data_pages:
            pages = ", ".join(f"{key} {path}" for key, path in sorted(self.data_pages.items()))
            out.append(f"Strony-dane na żywo z Wagtaila (D9): {pages}.")
        out.append(f"Przekierowania: {self.redirects}.")
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
    #: Źródło paczki (ścieżka albo plik z ``seek``) – obrazy czytamy z niego **strumieniowo**, po
    #: jednym, dopiero przy imporcie. Paczka potrafi ważyć setki megabajtów, a kontener djcms ma
    #: limit pamięci (``mem_limit``): trzymanie bajtów wszystkich obrazów kończyło się zabiciem
    #: procesu przez OOM. Wołający trzyma plik otwarty do końca ``run_import``.
    source: Any = None
    #: Format Pillow każdego obrazu (klucz manifestu ``images`` → ``"PNG"``, ``"JPEG"``, …) –
    #: ustalony przy walidacji; od niego zależy rozszerzenie pliku w filerze.
    image_formats: dict[str, str] = field(default_factory=dict)

    @property
    def pages(self) -> list[dict]:
        return self.manifest["pages"]

    @property
    def documents(self) -> dict[str, dict]:
        return self.manifest["documents"]

    @property
    def menu(self) -> dict:
        return self.manifest["vocabularies"]["menu"]

    @property
    def version(self) -> int:
        return self.manifest["version"]

    @property
    def competition_slug(self) -> str | None:
        """Konkurs paczki: ``competition.slug`` (v2) albo ``source.competition_slug`` (v1)."""
        competition = self.manifest.get("competition")
        if isinstance(competition, dict):
            return competition["slug"]
        slug = self.manifest.get("source", {}).get("competition_slug")
        return slug if isinstance(slug, str) and slug else None

    @property
    def data_pages(self) -> dict[str, int | None]:
        """Identyfikatory stron-danych (v2); paczka v1 ich nie ma – wszystko jest treścią."""
        return self.manifest.get("data_pages") or {"workshops": None, "partners": None}

    @property
    def redirects(self) -> list:
        return self.manifest.get("redirects") or []


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
        formats = _verify_images(archive, manifest, names)
    _validate_pages(manifest)
    return Bundle(manifest=manifest, source=src, image_formats=formats)


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
    version = manifest.get("version")
    if isinstance(version, bool) or version not in SUPPORTED_VERSIONS:
        raise BundleError(f"nieobsługiwana wersja paczki: {version!r} (obsługiwane 1 i 2)")
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
    if version >= 2:
        _validate_v2(manifest)


def _validate_v2(manifest: dict) -> None:
    """Klucze wersji 2 (DJ-02 § 4.4): kształt; odwołania do stron sprawdza ``_validate_pages``.

    Pojedyncze przekierowanie w złym kształcie **nie** odrzuca paczki (to dane redakcji Wagtaila –
    wypada z raportem przy imporcie, ``resolve_redirects``); zły typ całej listy – tak.
    """
    competition = manifest.get("competition")
    if (
        not isinstance(competition, dict)
        or not isinstance(competition.get("slug"), str)
        or not SLUG_RE.match(competition["slug"])
        or not isinstance(competition.get("name", ""), str)
    ):
        raise BundleError("manifest: pole 'competition' ma zły kształt (v2 wymaga sluga konkursu)")
    data_pages = manifest.get("data_pages")
    if not isinstance(data_pages, dict):
        raise BundleError("manifest: pole 'data_pages' ma zły typ")
    for key in ("workshops", "partners"):
        value = data_pages.get(key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise BundleError(f"manifest: data_pages.{key} nie jest identyfikatorem strony")
    if not isinstance(manifest.get("redirects"), list):
        raise BundleError("manifest: pole 'redirects' ma zły typ")


def _spooled_member(archive: zipfile.ZipFile, path: str, sha1: str, label: str | None = None):
    """Kopia członka paczki w pliku tymczasowym (pamięć do ``IMAGE_SPOOL_BYTES``, dalej ``MEDIA_ROOT``).

    Kopiowanie porcjami ``READ_CHUNK_BYTES`` z SHA-1 liczonym po drodze i porównanym z manifestem –
    przy walidacji i drugi raz przy imporcie (plik paczki na dysku mógł się zmienić między jednym
    a drugim, a do filera ma trafić to, co sprawdzone). Pillow czyta potem **plik**, a nie członka
    ZIP-a: ``ZipExtFile.seek`` do przodu dekompresuje całą odległość naraz (do 16 MB w pamięci).
    """
    Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
    spool = tempfile.SpooledTemporaryFile(max_size=IMAGE_SPOOL_BYTES, dir=settings.MEDIA_ROOT)  # noqa: SIM115
    digest = hashlib.sha1(usedforsecurity=False)
    try:
        with archive.open(path) as handle:
            while chunk := handle.read(READ_CHUNK_BYTES):
                digest.update(chunk)
                spool.write(chunk)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        spool.close()
        raise BundleError(f"{path}: uszkodzony członek paczki ({exc})") from None
    except BaseException:
        spool.close()
        raise
    if digest.hexdigest() != sha1:
        spool.close()
        raise BundleError(
            f"{label}: SHA-1 niezgodne z manifestem"
            if label
            else f"{path}: SHA-1 niezgodne z manifestem (paczka zmieniła się w trakcie importu)"
        )
    spool.seek(0)
    return spool


class _CappedReads:
    """Plik, którego ``read(n)`` czyta najwyżej ``READ_CHUNK_BYTES`` naraz.

    Filer liczy SHA-1 pętlą ``read(104857600)`` (``File.generate_sha1``), a ``read(n)`` na pliku
    z dysku rezerwuje bufor ``n`` bajtów z góry – 100 MB na każdy obraz, także dwukilobajtowy.
    Pętla filera działa tak samo przy krótszych porcjach (czyta do pustego wyniku).
    """

    def __init__(self, file):
        self._file = file

    def read(self, size: int | None = -1) -> bytes:
        if size is not None and size > READ_CHUNK_BYTES:
            size = READ_CHUNK_BYTES
        return self._file.read(size)

    def __getattr__(self, name):
        return getattr(self._file, name)

    def __iter__(self):
        return iter(self._file)


def _image_format(handle, label: str) -> str:
    """Format obrazu według Pillow – po sprawdzeniu wymiarów (bomba dekompresyjna) i ``verify``.

    Wymiary czytamy z nagłówka (``Image.open`` nie dekoduje pikseli) i odrzucamy obraz powyżej
    ``MAX_IMAGE_PIXELS``, zanim cokolwiek go zdekoduje: ``verify`` Pillow tylko **ostrzega** między
    ~89 a ~179 Mpx, a plik 12000×12000 przeszedłby i zabił proces przy pierwszej miniaturze
    (``easy-thumbnails`` na stronie publicznej).
    """
    from PIL import Image as PILImage

    try:
        with PILImage.open(handle) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise BundleError(
                    f"{label}: {width}×{height} px to więcej niż {MAX_IMAGE_PIXELS // 1_000_000} Mpx "
                    "(ochrona przed bombą dekompresyjną)"
                )
            fmt = image.format or ""
            image.verify()
    except BundleError:
        raise
    except PILImage.DecompressionBombError, PILImage.DecompressionBombWarning:
        raise BundleError(
            f"{label}: więcej niż {MAX_IMAGE_PIXELS // 1_000_000} Mpx (ochrona przed bombą dekompresyjną)"
        ) from None
    except Exception as exc:  # noqa: BLE001 - Pillow zgłasza różne klasy wyjątków dla złego pliku
        raise BundleError(f"{label} nie otwiera się w Pillow ({type(exc).__name__})") from None
    if fmt not in IMAGE_EXTENSIONS:
        raise BundleError(
            f"{label}: format {fmt or '?'} spoza dozwolonych ({', '.join(sorted(IMAGE_EXTENSIONS))})"
        )
    return fmt


def _verify_images(archive: zipfile.ZipFile, manifest: dict, names: set[str]) -> dict[str, str]:
    """Każdy obraz: plik w paczce, SHA-1 zgodne z manifestem, Pillow – po jednym, bez bajtów w pamięci."""
    formats: dict[str, str] = {}
    for key, meta in manifest["images"].items():
        if not str(key).isdigit() or not isinstance(meta, dict):
            raise BundleError(f"manifest: obraz {key!r} ma zły kształt")
        path = meta.get("path")
        if not isinstance(path, str) or not path.startswith(IMAGES_PREFIX) or path not in names:
            raise BundleError(f"obraz #{key}: brak pliku {path!r} w paczce")
        label = f"obraz #{key} ({path})"
        with _spooled_member(archive, path, meta.get("sha1"), label) as spool:
            formats[str(key)] = _image_format(spool, label)
    return formats


def _validate_pages(manifest: dict) -> None:
    """Drzewo spójne: jeden korzeń, rodzic przed dzieckiem, poprawne typy pól, słowniki i slugi;
    ścieżki stron poza adresami aplikacji (S5); strony-dane (v2) w paczce i właściwego typu."""
    from apps.pages.validation import path_collides_with_app

    pages = manifest["pages"]
    if not pages:
        raise BundleError("paczka nie ma żadnej strony")
    seen: dict[int, dict] = {}
    #: Ścieżka djcms strony (``PageUrl.path``): dzieci strony głównej Wagtaila stoją w korzeniu.
    paths: dict[int, str] = {}
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
        if parent_id is None:
            paths[page_id] = ""
        else:
            parent_path = paths[parent_id]
            paths[page_id] = f"{parent_path}/{dto['slug']}" if parent_path else dto["slug"]
            # S5: pełna ścieżka, nie tylko pierwszy segment – ``warsztaty/materialy`` i reguła
            # drugiego segmentu (``/o-nas/login/`` pod prefiksem konkursu idzie do aplikacji).
            reason = path_collides_with_app(paths[page_id])
            if reason:
                raise BundleError(f"{label}: {reason} – zmień slug w Wagtailu przed importem")
        seen[page_id] = dto
    if roots != 1:
        raise BundleError(f"paczka ma {roots} stron bez rodzica (oczekiwana dokładnie jedna – strona główna)")
    data_pages = manifest.get("data_pages") if manifest.get("version", 1) >= 2 else None
    for key, page_type in (("workshops", "cms.ContentPage"), ("partners", "cms.PartnersPage")):
        page_id = (data_pages or {}).get(key)
        if page_id is None:
            continue
        if page_id not in seen or seen[page_id]["type"] != page_type or page_id == root_id:
            raise BundleError(f"data_pages.{key}: strona #{page_id} spoza paczki albo nie typu {page_type}")


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


def sanitize(html: str | None, where: str, report: ImportReport, prefix: str = "") -> str:
    """HTML przez sanityzator djangocms-text; to, co usunął, trafia do raportu.

    ``prefix`` – prefiks ścieżki konkursu (``/druga``): odnośniki do stron od korzenia witryny
    dostają go **przed** sanityzacją (``apps.live.data.prefix_links``), tak jak rysuje je Wagtail.
    """
    from djangocms_text.html import clean_html

    from apps.live.data import prefix_links

    source = prefix_links(html or "", prefix)
    cleaned = clean_html(source)
    removed = _inventory(source) - _inventory(cleaned)
    if removed:
        details = ", ".join(f"{key}×{count}" for key, count in sorted(removed.items()))
        report.sanitized.append(f"{where}: {details}")
    return cleaned


# --- 2. obrazy ------------------------------------------------------------------------------------


def competition_folder_name(competition) -> str:
    return f"{COMPETITION_FOLDER_PREFIX}{competition.name} ({competition.slug})"[:255]


def competition_folder(competition, *, create: bool = True):
    """Folder konkursu najwyższego poziomu (DJ-02 D5): ``Konkurs: <nazwa> (<slug>)``.

    Szukany po końcówce ``(<slug>)`` – po zmianie nazwy konkursu folder zostaje ten sam (nazwę
    poprawiamy). ``create=False`` – ``None``, gdy folderu nie ma. Uprawnienia redakcji do folderu
    (``FolderPermission`` dla ``redakcja:<slug>``) nadaje DJ-02g – tą samą funkcją.
    """
    from filer.models import Folder

    folder = (
        Folder.objects.filter(
            parent__isnull=True,
            name__startswith=COMPETITION_FOLDER_PREFIX,
            name__endswith=f"({competition.slug})",
        )
        .order_by("pk")
        .first()
    )
    if folder is None:
        return Folder.objects.create(name=competition_folder_name(competition)) if create else None
    wanted = competition_folder_name(competition)
    if folder.name != wanted:
        folder.name = wanted
        folder.save(update_fields=["name"])
    return folder


def legacy_import_folder():
    """Folder importu sprzed DJ-02 (korzeń biblioteki) – należy do konkursu domyślnego."""
    from filer.models import Folder

    return Folder.objects.filter(name=IMPORT_FOLDER_NAME, parent__isnull=True).order_by("pk").first()


def import_folder(competition=None, *, create: bool = True):
    """Folder obrazów importu witryny: ``Konkurs: … / Import z Wagtaila`` (bez konkursu – w korzeniu)."""
    from filer.models import Folder

    if competition is None:
        folder = legacy_import_folder()
        if folder is None and create:
            folder = Folder.objects.create(name=IMPORT_FOLDER_NAME)
        return folder
    parent = competition_folder(competition, create=create)
    if parent is None:
        return None
    folder = Folder.objects.filter(name=IMPORT_FOLDER_NAME, parent=parent).order_by("pk").first()
    if folder is None and create:
        folder = Folder.objects.create(name=IMPORT_FOLDER_NAME, parent=parent)
    return folder


def _image_filename(meta: dict, key: str, fmt: str) -> str:
    """Nazwa pliku z paczki (bez przedrostka ``<id>-``) z rozszerzeniem **formatu** wykrytego przez
    Pillow – nie tym z nazwy: ``logo.png``, który jest JPEG-iem, trafia do filera jako ``logo.jpg``
    (typ MIME przy podawaniu pliku bierze się z rozszerzenia)."""
    basename = posixpath.basename(meta["path"])
    prefix = f"{key}-"
    original = basename[len(prefix) :] if basename.startswith(prefix) else basename
    stem = original.rsplit(".", 1)[0] if "." in original else original
    return get_valid_filename(f"{stem}.{IMAGE_EXTENSIONS[fmt]}") or f"obraz-{key}.{IMAGE_EXTENSIONS[fmt]}"


def import_images(
    bundle: Bundle, folder, report: ImportReport, created: list | None = None
) -> dict[int, Any]:
    """Obrazy z paczki → ``filer.Image``; istniejący plik o tym samym SHA-1 jest używany ponownie.

    Ponownie – z biblioteki, ale **nie** z folderu importu innej witryny: ``--replace`` tamtej
    skasowałby plik, na który wskazywałyby wtyczki tej (obraz trafia wtedy drugi raz, do własnego
    folderu). Obrazy idą **po jednym** strumieniem z paczki (``_spooled_member``) – w pamięci jest
    najwyżej jeden, i to do ``IMAGE_SPOOL_BYTES``. ``created`` dostaje każdy nowo zapisany obraz –
    wołający skasuje jego plik z magazynu, jeśli transakcja zostanie wycofana (``--dry-run`` albo błąd).
    """
    from django.core.files import File as DjangoFile
    from django.db.models import Q
    from filer.models import Image

    reusable = Q(folder__isnull=True) | ~Q(folder__name=IMPORT_FOLDER_NAME) | Q(folder=folder)
    result: dict[int, Any] = {}
    if not bundle.manifest["images"]:
        return result
    source = bundle.source
    if hasattr(source, "seek"):
        source.seek(0)
    with zipfile.ZipFile(source) as archive:
        for key, meta in bundle.manifest["images"].items():
            existing = Image.objects.filter(reusable, sha1=meta["sha1"]).order_by("pk").first()
            if existing is not None:
                result[int(key)] = existing
                report.images_reused += 1
                continue
            filename = _image_filename(meta, str(key), bundle.image_formats[str(key)])
            with _spooled_member(archive, meta["path"], meta["sha1"]) as spool:
                upload = DjangoFile(_CappedReads(spool), name=filename)
                upload.size = archive.getinfo(meta["path"]).file_size
                image = Image.objects.create(
                    folder=folder,
                    name=_text(meta.get("title"), 255),
                    default_alt_text=_text(meta.get("alt"), 255),
                    original_filename=filename[:255],
                    file=upload,
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
        return sanitize(_text(value), f"{self.label} {where}", self.report, self.importer.prefix)

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
    def __init__(
        self, bundle: Bundle, images: dict[int, Any], user, report: ImportReport, site, prefix: str = ""
    ):
        self.bundle = bundle
        self.site = site
        self.images = images
        self.user = user
        self.report = report
        #: Prefiks ścieżki konkursu (``/druga``) dla odnośników w tekście – ``sanitize``.
        self.prefix = prefix
        data_pages = bundle.data_pages
        self.workshops_id = data_pages.get("workshops")
        self.partners_id = data_pages.get("partners")
        #: Strona djcms po identyfikatorze strony Wagtaila – cele przekierowań.
        self.pages_by_id: dict[int, Any] = {}
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
        live_partners = dto["id"] == self.partners_id
        if live_partners:
            template = LIVE_PARTNERS_TEMPLATE
        in_navigation, menu_title, flags = self.navigation(dto, level)
        page = create_page(
            _text(dto["title"], 255),
            template,
            LANGUAGE,
            slug=dto["slug"],
            created_by=self.user,
            # Witryna zawsze jawnie: bez ``SITE_ID`` ``create_page`` bez ``site`` i bez rodzica
            # rzuciłby ``ImproperlyConfigured`` (bezpiecznik D4).
            site=self.site,
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
        if live_partners:
            # Strona-dana (D9): cała treść na żywo z ``GET partners`` – karty, wprowadzenie
            # i zaproszenie zostają w Wagtailu, tu żadnej kopii.
            builder.add(LIVE_PARTNERS_SLOT, "PartnersLivePlugin")
        elif dto["id"] == self.workshops_id:
            self.fill_workshops(builder)
        elif page_type in TEMPLATES:
            self.fill(builder, content, page_type)
        Version.objects.get_for_content(content).publish(self.user)
        # Ścieżka dopiero po publikacji – versioning ustala ``PageUrl.path`` przy publikowaniu wersji.
        path = _page_path(page)
        if live_partners:
            self.report.data_pages["partners"] = path
        elif dto["id"] == self.workshops_id:
            self.report.data_pages["warsztaty"] = path
        self.report.pages[page_type] += 1
        self.report.paths.append(path)
        self.pages_by_id[dto["id"]] = page
        return page

    def fill_workshops(self, b: _PageBuilder) -> None:
        """Strona „Warsztaty” (D9): wprowadzenie i tabele na żywo, reszta treści jak zwykła strona.

        - ``intro`` → ``WorkshopSchedulePlugin`` (część „wprowadzenie”) – zawsze, także przy pustym
          polu w chwili importu: redakcja dopisuje je w Wagtailu,
        - bloki ``schedule`` → **jedna** wtyczka (część „harmonogram”) w miejscu pierwszego bloku;
          rysuje wszystkie tabele strony z API, więc kolejne bloki wypadają (z wpisem w raporcie).
          Strona bez bloku tabeli dostaje wtyczkę na końcu treści – tabela dopisana w Wagtailu ma
          gdzie się pojawić,
        - śródtytuły, akapity i załączniki – kopia z paczki jak na każdej stronie treści (API ich
          nie oddaje; zmiana w Wagtailu wymaga ponownego importu).
        """
        from apps.live.models import WorkshopSchedule

        f = b.dto["fields"]
        b.add("intro", "WorkshopSchedulePlugin", part=WorkshopSchedule.Part.INTRO)
        b.attachments()
        placed = False
        items = f.get("body") if isinstance(f.get("body"), list) else []
        for index, item in enumerate(items):
            if isinstance(item, dict) and item.get("type") == "schedule":
                if placed:
                    b.skip(f"treść[{index}] schedule: kolejna tabela – rysuje ją wtyczka żywa „Warsztaty”")
                else:
                    b.add("body", "WorkshopSchedulePlugin", part=WorkshopSchedule.Part.SCHEDULE)
                    placed = True
                continue
            b.stream("body", [item], DOC_BLOCKS, f"treść[{index}]")
        if not placed:
            b.add("body", "WorkshopSchedulePlugin", part=WorkshopSchedule.Part.SCHEDULE)

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


def _page_path(page) -> str:
    """Ścieżka strony względem korzenia witryny: ``/``, ``/zadania/``, ``/dokumenty/regulamin/``.

    Z ``PageUrl`` w bazie, a nie z ``Page.get_path``: obiekt strony zaraz po ``create_page`` ma
    w pamięci adresy sprzed ustawienia ścieżki (``get_path`` oddawał ``""`` – stronę główną).
    """
    from cms.models import PageUrl

    path = PageUrl.objects.filter(page=page, language=LANGUAGE).values_list("path", flat=True).first() or ""
    return f"/{path}/" if path else "/"


def import_pages(
    bundle: Bundle, images: dict[int, Any], user, report: ImportReport, site, prefix: str = ""
) -> dict[int, Any]:
    """Tworzy i publikuje strony paczki w witrynie ``site``; zwraca stronę djcms po id strony Wagtaila.

    Wymaga otwartej transakcji (``set_as_homepage``).
    """
    importer = _Importer(bundle, images, user, report, site, prefix)
    importer.run()
    return importer.pages_by_id


# --- przekierowania (paczka v2) ------------------------------------------------------------------


@dataclass(frozen=True)
class RedirectSpec:
    """Przekierowanie gotowe do zapisu: ścieżka względem korzenia witryny → cel."""

    old_path: str
    new_path: str
    is_permanent: bool


def _redirect_path(value: Any) -> str | None:
    """Ścieżka od ``/`` (nie ``//``), bez spacji, znaków sterujących i ``\\``, najwyżej 255 znaków."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return None
    if len(value) > MAX_REDIRECT_PATH or "\\" in value or any(ch.isspace() or ord(ch) < 32 for ch in value):
        return None
    return value


def _normalise_redirect_path(path: str) -> str | None:
    """``old_path`` w kształcie, w którym szuka go warstwa przekierowań (``dj_seo.normalise_path`` –
    port ``Redirect.normalise_path`` Wagtaila). Eksport już normalizuje; tu – drugi raz, na wypadek
    paczki spoza eksportu. Bez aplikacji ``dj_seo`` ścieżka zostaje, jaka jest."""
    try:
        from apps.seo.models import normalise_path
    except ImportError:
        return path
    return _redirect_path(normalise_path(path))


def _redirect_target(
    target: dict, pages_by_id: dict[int, Any], where: str, report: ImportReport
) -> str | None:
    """Cel przekierowania w witrynie djcms albo ``None`` (powód w raporcie)."""
    if "page_id" in target:
        page_id = target.get("page_id")
        page = pages_by_id.get(page_id) if isinstance(page_id, int) else None
        route = target.get("route_path")
        if page is None:
            report.skipped.append(f"{where}: strona docelowa #{page_id} poza importem – pominięte")
            return None
        if isinstance(route, str) and route.strip("/"):
            report.skipped.append(f"{where}: cel z podstroną routowalną {route[:80]!r} – brak odpowiednika")
            return None
        return _page_path(page)
    url = target.get("url")
    if isinstance(url, str):
        url = url.strip()
        new = _redirect_path(url) or (_http_url(url) if len(url) <= 500 else "")
        if new:
            return new
        report.skipped.append(f"{where}: cel {url[:80]!r} nie jest ścieżką ani adresem http(s) – pominięte")
        return None
    report.skipped.append(f"{where}: brak celu – pominięte")
    return None


def resolve_redirects(
    bundle: Bundle, pages_by_id: dict[int, Any], report: ImportReport
) -> list[RedirectSpec]:
    """Przekierowania paczki v2 z celami przełożonymi na witrynę djcms (DJ-02 § 4.4, § 8).

    - ``old_path`` jak w Wagtailu (``Redirect.normalise_path`` eksportu: bez ukośnika końcowego,
      zapytanie posortowane) – warstwa przekierowań ``dj_seo`` normalizuje żądanie tak samo,
    - cel-strona → ścieżka **tej** strony po imporcie (``/dokumenty/regulamin/``), względem
      korzenia witryny – prefiks konkursu dokłada warstwa przekierowań przy odpowiedzi. Strona
      spoza importu albo podstrona routowalna (``route_path``, djcms nie ma odpowiednika) – raport,
    - cel-adres: ścieżka od ``/`` albo ``http(s)://host…``; inne schematy – raport,
    - pętla (cel = ta sama ścieżka) i powtórzona ścieżka – raport.

    Żaden zły wpis nie odrzuca paczki: to dane redakcji Wagtaila, nie kontrakt.
    """
    specs: list[RedirectSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(bundle.redirects):
        where = f"przekierowanie[{index}]"
        if not isinstance(item, dict):
            report.skipped.append(f"{where}: zły kształt – pominięte")
            continue
        old = _redirect_path(item.get("old_path"))
        if old is not None:
            old = _normalise_redirect_path(old)
        if old is None:
            report.skipped.append(
                f"{where}: ścieżka {str(item.get('old_path'))[:80]!r} niepoprawna – pominięte"
            )
            continue
        where = f"przekierowanie {old}"
        if old in seen:
            report.skipped.append(f"{where}: powtórzona ścieżka – pominięte")
            continue
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        new = _redirect_target(target, pages_by_id, where, report)
        if new is None:
            continue
        if new.split("?", 1)[0].split("#", 1)[0].rstrip("/") == old.split("?", 1)[0].rstrip("/"):
            report.skipped.append(f"{where}: cel to ta sama ścieżka (pętla) – pominięte")
            continue
        seen.add(old)
        specs.append(
            RedirectSpec(old_path=old, new_path=new, is_permanent=item.get("is_permanent") is not False)
        )
    return specs


def redirect_model():
    """``dj_seo.Redirect`` (DJ-02f) albo ``None``, gdy aplikacji przekierowań nie ma w projekcie."""
    from django.apps import apps

    try:
        return apps.get_model("dj_seo", "Redirect")
    except LookupError:
        return None


def store_redirects(site, specs: list[RedirectSpec], report: ImportReport) -> None:
    """Zapis przekierowań witryny ze źródłem ``import``; ścieżka zajęta przez wpis redakcji – raport.

    Przekierowania redakcji djcms (``auto``/``manual``) wygrywają z importem: ``--replace`` kasuje
    wyłącznie wpisy ze źródłem ``import`` (``wipe``), a nowy import nie nadpisuje cudzych.
    """
    model = redirect_model()
    if model is None:
        if specs:
            report.skipped.append(
                f"przekierowania ({len(specs)}): brak modelu dj_seo.Redirect (DJ-02f) – nie zapisane"
            )
        return
    taken = set(model.objects.filter(site=site).values_list("old_path", flat=True))
    rows = []
    for spec in specs:
        if spec.old_path in taken:
            report.skipped.append(
                f"przekierowanie {spec.old_path}: ścieżka ma już przekierowanie redakcji – pominięte"
            )
            continue
        rows.append(
            model(
                site=site,
                old_path=spec.old_path,
                new_path=spec.new_path,
                is_permanent=spec.is_permanent,
                source=REDIRECT_SOURCE_IMPORT,
            )
        )
    model.objects.bulk_create(rows)
    report.redirects = len(rows)


# --- przebieg całości -----------------------------------------------------------------------------


def site_has_pages(site=None) -> bool:
    from cms.models import Page

    return Page.objects.filter(site=target_site(site)).exists()


def _import_folders(competition) -> list:
    """Foldery importu witryny: własny, a dla konkursu domyślnego (i witryny spoza rejestru) także
    folder sprzed DJ-02 w korzeniu biblioteki – tamten import był importem witryny domyślnej."""
    folders = [import_folder(competition, create=False)] if competition is not None else []
    if competition is None or competition.is_default:
        folders.append(legacy_import_folder())
    return [folder for folder in folders if folder is not None]


def wipe(site, report: ImportReport, competition=None) -> list:
    """``--replace``: strony witryny, jej przekierowania z importu i pliki z jej folderu importu.

    Strony, przekierowania i pliki **innych** witryn zostają. Zwraca skasowane obiekty plików – ich
    pliki w magazynie kasuje wołający dopiero po zatwierdzeniu transakcji (wycofany import musi
    zostawić je na miejscu, bo wiersze wracają).
    """
    from cms.models import Page
    from filer.models import File

    report.deleted_pages = Page.objects.filter(site=site).count()
    for root in Page.get_root_nodes().filter(site=site):
        root.delete()
    model = redirect_model()
    if model is not None:
        report.deleted_redirects, _ = model.objects.filter(site=site, source=REDIRECT_SOURCE_IMPORT).delete()
    files = list(File.objects.filter(folder__in=_import_folders(competition)))
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


def link_prefix(competition) -> str:
    """Prefiks odnośników w tekście: ``/druga`` dla konkursu pod prefiksem ścieżki, inaczej ``""``."""
    from apps.sites.models import RoutingMode

    if competition is None or competition.routing_mode != RoutingMode.PATH:
        return ""
    return competition.public_path_prefix or f"/{competition.path_prefix}"


def run_import(
    bundle: Bundle,
    *,
    user,
    replace: bool = False,
    dry_run: bool = False,
    site=None,
    competition=None,
) -> ImportReport:
    """Import w jednej transakcji do witryny konkursu ``competition`` (albo witryny ``site``;
    bez obu – konkursu domyślnego).

    Bez ``replace`` i przy istniejących stronach → ``ImportRefused``. Paczka innego konkursu niż
    konkurs witryny → ``ImportRefused`` (zanim cokolwiek trafi do bazy).
    """
    from cms.models import Page
    from django.utils import timezone
    from menus.menu_pool import menu_pool

    report = ImportReport(
        dry_run=dry_run, source={**bundle.manifest.get("source", {})}, version=bundle.version
    )
    report.source["exported_at"] = bundle.manifest.get("exported_at", "?")
    started = time.monotonic()
    if competition is not None:
        site = competition.site
    else:
        site = target_site(site)
        competition = competition_of(site)
    if competition is not None:
        report.competition = competition.slug
        if bundle.competition_slug and bundle.competition_slug != competition.slug:
            raise ImportRefused(
                f"paczka konkursu „{bundle.competition_slug}”, a witryna docelowa należy do "
                f"„{competition.slug}” – import odrzucony"
            )
    created_images: list = []
    committed = False
    try:
        with transaction.atomic():
            if Page.objects.filter(site=site).exists():
                if not replace:
                    raise ImportRefused(
                        "w witrynie są już strony – użyj --replace (pełny re-import) albo --if-empty"
                    )
                removed = wipe(site, report, competition)
                transaction.on_commit(lambda: _delete_stored(removed))
            images = import_images(bundle, import_folder(competition), report, created=created_images)
            pages_by_id = import_pages(bundle, images, user, report, site, link_prefix(competition))
            store_redirects(site, resolve_redirects(bundle, pages_by_id, report), report)
            if competition is not None:
                type(competition).objects.filter(pk=competition.pk).update(content_imported_at=timezone.now())
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
