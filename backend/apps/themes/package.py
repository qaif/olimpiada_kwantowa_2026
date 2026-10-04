"""Odczyt i walidacja paczki motywu (ZIP) – czysta funkcja, bez zapisu czegokolwiek.

Wynikiem jest :class:`PackageResult`: manifest, tokeny, szablony, pliki do publikacji (już
oczyszczone) i raport (błędy + ostrzeżenia). Zapis do bazy i storage robi ``apps.themes.services``
dopiero wtedy, gdy raport nie ma błędów – odrzucona paczka nie publikuje ani jednego pliku.

Kolejność kontroli jest celowa: najpierw rzeczy, które chronią **serwer** (rozmiar, liczba plików,
ścieżki, bomba ZIP – liczone na bajtach faktycznie rozpakowanych, nie na nagłówkach archiwum), potem
treść (manifest, CSS, SVG, szablony), na końcu skan ClamAV całej paczki.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass, field

from . import css as css_mod
from . import slots
from .svg import sanitize_svg
from .tokens import TokenSet, parse_tokens

MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 60 * 1024 * 1024
MAX_FILES = 500
MAX_FILE_BYTES = 10 * 1024 * 1024

#: Pliki publikowane (pod ``assets/``) – rozszerzenie → typ MIME, który ustawi storage.
ASSET_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}
#: Pliki tekstowe dopuszczone w paczce (licencje krojów OFL, README) – **nie** są publikowane.
TEXT_EXTENSIONS = (".txt", ".md")
ROOT_FILES = {"manifest.json", "theme.css", "tokens.json", "screenshot.png"}
REQUIRED = ("manifest.json", "theme.css", "tokens.json")

#: Znaki dozwolone w ścieżce pliku paczki. Wąsko: adres pliku trafia do ``url("…")`` w arkuszu
#: i do klucza obiektu w storage – cudzysłów, spacja czy ``%`` nie mają tam czego szukać.
PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".woff2": (b"wOF2",),
}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}(?:[-+][0-9A-Za-z.-]{1,20})?$")
LAYOUT_VOCABULARY = {
    "header": ("centered", "split", "minimal"),
    "home_hero": ("full-bleed", "split", "compact"),
    "footer": ("columns", "compact"),
    "cards": ("flat", "elevated", "outline"),
}
COLOR_SCHEMES = ("light", "dark", "auto")
SUPPORTS = ("public", "panels")
MANIFEST_KEYS = {
    "schema",
    "slug",
    "name",
    "version",
    "author",
    "description",
    "supports",
    "layouts",
    "color_scheme",
    "min_app_version",
    # THEME-02 § 2.1 – opcjonalne listy wariantów do wyboru przez koordynatora.
    "logos",
    "fonts",
}

#: Identyfikator wariantu logo/krojów w manifeście (trafia do ``theme_options`` i do adresu arkusza).
OPTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MAX_OPTIONS = 8
LOGO_TYPES = (".svg", ".png", ".webp", ".jpg", ".jpeg")
FONT_ROLES = ("body", "display", "mono")


@dataclass
class PublicFile:
    path: str
    data: bytes
    content_type: str


@dataclass
class PackageResult:
    sha256: str = ""
    size: int = 0
    manifest: dict = field(default_factory=dict)
    tokens: TokenSet = field(default_factory=TokenSet)
    theme_css: str = ""
    templates: dict[str, str] = field(default_factory=dict)
    public_files: list[PublicFile] = field(default_factory=list)
    has_screenshot: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def slug(self) -> str | None:
        slug = self.manifest.get("slug")
        return slug if isinstance(slug, str) and SLUG_RE.match(slug) else None

    @property
    def version(self) -> str | None:
        version = self.manifest.get("version")
        return version if isinstance(version, str) and VERSION_RE.match(version) else None

    def report(self) -> dict:
        return {"errors": list(self.errors), "warnings": list(self.warnings)}


# --- ZIP -------------------------------------------------------------------------------------


def _safe_member_name(name: str) -> str | None:
    """Nazwa po normalizacji albo ``None`` dla ścieżki niebezpiecznej (ZIP-slip, bezwzględnej…)."""
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = name.split("/")
    if any(part in ("..", ".") for part in parts) or "" in parts[:-1]:
        return None
    return name


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def read_zip(data: bytes, result: PackageResult) -> dict[str, bytes]:
    """Rozpakowuje paczkę do pamięci z kontrolą ścieżek i limitów. Błędy → ``result.errors``."""
    files: dict[str, bytes] = {}
    if len(data) > MAX_PACKAGE_BYTES:
        result.errors.append(f"Paczka ma {len(data)} B – limit to {MAX_PACKAGE_BYTES // 1024 // 1024} MB.")
        return files
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        result.errors.append("Plik nie jest poprawnym archiwum ZIP.")
        return files
    infos = archive.infolist()
    if len(infos) > MAX_FILES:
        result.errors.append(f"Paczka ma {len(infos)} pozycji – limit to {MAX_FILES}.")
        return files
    names = [info.filename for info in infos if not info.is_dir()]
    # Archiwum spakowane razem z katalogiem (``iqo-quantum/manifest.json``) – zdejmujemy jeden
    # wspólny katalog nadrzędny, żeby autor nie musiał pamiętać, „z którego miejsca” pakować.
    strip = ""
    tops = {name.split("/", 1)[0] for name in names}
    if "manifest.json" not in names and len(tops) == 1:
        top = next(iter(tops))
        if f"{top}/manifest.json" in names:
            strip = top + "/"
    budget = MAX_UNPACKED_BYTES
    for info in infos:
        if info.is_dir():
            continue
        name = _safe_member_name(info.filename)
        if name is None:
            result.errors.append(f"Niebezpieczna ścieżka w archiwum: {info.filename!r}.")
            continue
        if _is_symlink(info):
            result.errors.append(f"Dowiązanie symboliczne w archiwum: {info.filename!r}.")
            continue
        if info.flag_bits & 0x1:
            result.errors.append(f"Plik zaszyfrowany w archiwum: {info.filename!r}.")
            continue
        if strip:
            name = name[len(strip) :]
        if name in files:
            result.errors.append(f"Powtórzona ścieżka w archiwum: {name!r}.")
            continue
        # Bomba ZIP: liczymy bajty faktycznie rozpakowane (nagłówek ``file_size`` może kłamać)
        # i przerywamy w chwili przekroczenia limitu, zanim cały plik trafi do pamięci.
        limit = min(MAX_FILE_BYTES, budget)
        chunks: list[bytes] = []
        read = 0
        try:
            with archive.open(info) as handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    read += len(chunk)
                    if read > limit:
                        break
                    chunks.append(chunk)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError) as exc:
            result.errors.append(f"Nie da się rozpakować {name!r}: {exc}.")
            continue
        if read > limit:
            result.errors.append(
                f"Rozpakowana treść przekracza limit ({name!r}; plik ≤ {MAX_FILE_BYTES // 1024 // 1024} MB, "
                f"całość ≤ {MAX_UNPACKED_BYTES // 1024 // 1024} MB) – możliwa bomba ZIP."
            )
            return {}
        budget -= read
        files[name] = b"".join(chunks)
    return files


# --- manifest --------------------------------------------------------------------------------


def _layouts(raw, result: PackageResult) -> dict[str, list[str]]:
    """``{"header": ["minimal", …]}`` – pierwsza wartość jest domyślna dla konkursu."""
    layouts: dict[str, list[str]] = {}
    if raw in (None, {}):
        return layouts
    if not isinstance(raw, dict):
        result.errors.append("manifest.json: „layouts” musi być obiektem.")
        return layouts
    for key, value in raw.items():
        if key not in LAYOUT_VOCABULARY:
            result.warnings.append(f"manifest.json: nieznany układ „{key}” – pominięty.")
            continue
        options = value.split("|") if isinstance(value, str) else value
        if not isinstance(options, list) or not options or not all(isinstance(o, str) for o in options):
            result.errors.append(
                f"manifest.json: wartość układu „{key}” musi być napisem albo listą napisów."
            )
            continue
        options = [o.strip() for o in options]
        unknown = [o for o in options if o not in LAYOUT_VOCABULARY[key]]
        if unknown:
            result.errors.append(
                f"manifest.json: układ „{key}” nie zna wariantów {unknown} "
                f"(dozwolone: {list(LAYOUT_VOCABULARY[key])})."
            )
            continue
        layouts[key] = list(dict.fromkeys(options))
    return layouts


def _options_list(raw, key: str, result: PackageResult) -> list[dict] | None:
    """Wspólna część ``logos``/``fonts``: lista obiektów z unikalnym ``id`` i ``label`` (≤ 60)."""
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw or len(raw) > MAX_OPTIONS:
        result.errors.append(f"manifest.json: „{key}” – niepusta lista do {MAX_OPTIONS} obiektów.")
        return None
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            result.errors.append(f"manifest.json: każdy wpis „{key}” musi być obiektem.")
            return None
        ident, label = entry.get("id"), entry.get("label")
        if not isinstance(ident, str) or not OPTION_ID_RE.match(ident) or ident in seen:
            result.errors.append(
                f"manifest.json: „{key}” – „id” unikalne, małe litery, cyfry i myślniki (do 32 znaków)."
            )
            return None
        if not isinstance(label, str) or not label.strip() or len(label) > 60:
            result.errors.append(f"manifest.json: „{key}.{ident}” – „label” to niepusty napis do 60 znaków.")
            return None
        seen.add(ident)
    return raw


def _logos(raw, result: PackageResult) -> list[dict]:
    """``[{"id", "label", "light", "dark"}]`` – pliki sprawdzane później (:func:`_check_logo_files`)."""
    entries = _options_list(raw, "logos", result)
    out = []
    for entry in entries or []:
        files = {
            variant: entry.get(variant) for variant in ("light", "dark") if entry.get(variant) is not None
        }
        if not files or not all(isinstance(path, str) for path in files.values()):
            result.errors.append(
                f"manifest.json: logo „{entry['id']}” musi wskazywać plik „light” i/lub „dark” "
                "(ścieżki w assets/)."
            )
            continue
        out.append({"id": entry["id"], "label": entry["label"].strip(), **files})
    return out


def _fonts(raw, result: PackageResult) -> list[dict]:
    """``[{"id", "label", "body", "display", "mono"}]`` – stosy krojów jak wartości tokenów."""
    from .tokens import _check_value

    entries = _options_list(raw, "fonts", result)
    out = []
    for entry in entries or []:
        cleaned = {"id": entry["id"], "label": entry["label"].strip()}
        for role in FONT_ROLES:
            if role not in entry:
                continue
            value = _check_value(f"fonts.{entry['id']}.{role}", entry[role], result.errors)
            if value is not None:
                cleaned[role] = value
        if not any(role in cleaned for role in FONT_ROLES):
            result.errors.append(f"manifest.json: para krojów „{entry['id']}” bez „body”/„display”/„mono”.")
            continue
        out.append(cleaned)
    return out


def _check_logo_files(manifest: dict, assets: set[str], result: PackageResult) -> None:
    for entry in manifest.get("logos") or []:
        for variant in ("light", "dark"):
            path = entry.get(variant)
            if path is None:
                continue
            if (
                not path.startswith("assets/")
                or posixpath.splitext(path)[1].lower() not in LOGO_TYPES
                or path not in assets
            ):
                result.errors.append(
                    f"manifest.json: logo „{entry['id']}” ({variant}) wskazuje {path!r} – "
                    "potrzebny obraz z katalogu assets/ tej paczki."
                )


def _version_tuple(value: str) -> tuple[int, ...] | None:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", value or "")
    return tuple(int(x) for x in match.groups()) if match else None


def parse_manifest(raw: bytes, result: PackageResult, *, app_version: str = "dev") -> dict:
    from .models import CLASSIC_SLUG

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result.errors.append(f"manifest.json: niepoprawny JSON ({exc}).")
        return {}
    if not isinstance(data, dict):
        result.errors.append("manifest.json: oczekiwany obiekt JSON.")
        return {}
    if data.get("schema") != 1:
        result.errors.append("manifest.json: „schema” musi mieć wartość 1.")
    slug = data.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        result.errors.append("manifest.json: „slug” – małe litery, cyfry i myślniki, 2–49 znaków.")
    elif slug == CLASSIC_SLUG:
        result.errors.append("manifest.json: slug „classic” jest zarezerwowany dla motywu wbudowanego.")
    if not isinstance(data.get("version"), str) or not VERSION_RE.match(data["version"]):
        result.errors.append("manifest.json: „version” w postaci X.Y.Z (np. 1.0.0).")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        result.errors.append("manifest.json: „name” – niepusty napis do 80 znaków.")
    for key, limit in (("author", 120), ("description", 2000), ("min_app_version", 32)):
        if key in data and (not isinstance(data[key], str) or len(data[key]) > limit):
            result.errors.append(f"manifest.json: „{key}” – napis do {limit} znaków.")
    supports = data.get("supports", list(SUPPORTS))
    if not isinstance(supports, list) or not set(supports) <= set(SUPPORTS) or not supports:
        result.errors.append(f"manifest.json: „supports” – niepusta lista z {list(SUPPORTS)}.")
        supports = list(SUPPORTS)
    scheme = data.get("color_scheme", "light")
    if scheme not in COLOR_SCHEMES:
        result.errors.append(f"manifest.json: „color_scheme” – jedno z {list(COLOR_SCHEMES)}.")
        scheme = "light"
    for key in sorted(set(data) - MANIFEST_KEYS):
        result.warnings.append(f"manifest.json: nieznane pole „{key}” – pominięte.")
    minimum = data.get("min_app_version")
    if isinstance(minimum, str):
        required, current = _version_tuple(minimum), _version_tuple(app_version)
        if required is None:
            result.errors.append("manifest.json: „min_app_version” w postaci X.Y.Z.")
        elif current is not None and current < required:
            result.errors.append(
                f"Motyw wymaga wersji aplikacji {minimum}, a ta instalacja ma {app_version}."
            )
        elif current is None:
            result.warnings.append(
                f"Nie da się porównać „min_app_version” {minimum} z wersją „{app_version}”."
            )
    return {
        "schema": 1,
        "slug": slug if isinstance(slug, str) else "",
        "name": (name or "").strip() if isinstance(name, str) else "",
        "version": data.get("version") if isinstance(data.get("version"), str) else "",
        "author": data.get("author", "") if isinstance(data.get("author"), str) else "",
        "description": data.get("description", "") if isinstance(data.get("description"), str) else "",
        "supports": supports,
        "layouts": _layouts(data.get("layouts"), result),
        "color_scheme": scheme,
        "min_app_version": minimum if isinstance(minimum, str) else "",
        "logos": _logos(data.get("logos"), result),
        "fonts": _fonts(data.get("fonts"), result),
    }


# --- pliki -----------------------------------------------------------------------------------


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None


def _check_magic(path: str, data: bytes) -> bool:
    ext = posixpath.splitext(path)[1].lower()
    if ext == ".webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    signatures = MAGIC.get(ext)
    return True if signatures is None else data.startswith(signatures)


def validate_package(data: bytes, *, app_version: str = "dev", scan=None) -> PackageResult:
    """Pełna walidacja paczki. ``scan(bytes) -> (werdykt, sygnatura)`` – ClamAV (``None`` = pomiń)."""
    result = PackageResult(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    files = read_zip(data, result)
    if result.errors:
        return result
    for required in REQUIRED:
        if required not in files:
            result.errors.append(f"Brak wymaganego pliku {required}.")
    if result.errors:
        return result

    result.manifest = parse_manifest(files["manifest.json"], result, app_version=app_version)
    tokens = parse_tokens(files["tokens.json"])
    result.tokens = tokens
    result.errors += tokens.errors
    result.warnings += tokens.warnings

    assets: dict[str, bytes] = {}
    templates: dict[str, str] = {}
    for path, content in sorted(files.items()):
        if not PATH_RE.match(path):
            result.errors.append(f"Niedozwolone znaki w nazwie pliku: {path!r}.")
            continue
        ext = posixpath.splitext(path)[1].lower()
        if path in ROOT_FILES:
            continue
        if ext in TEXT_EXTENSIONS:
            continue  # licencje i opisy – zostają w paczce prywatnej, nie są publikowane
        if path.startswith("assets/"):
            if ext not in ASSET_TYPES:
                result.errors.append(
                    f"Niedozwolony typ pliku w assets/: {path!r} (dozwolone: {sorted(ASSET_TYPES)})."
                )
                continue
            if not _check_magic(path, content):
                result.errors.append(f"Treść pliku {path!r} nie odpowiada rozszerzeniu.")
                continue
            assets[path] = content
            continue
        if path.startswith("templates/"):
            name = path[len("templates/") :]
            if ext != ".html" or not slots.is_package_template(name):
                result.errors.append(
                    f"Szablon spoza listy dozwolonej: {path!r} (sloty: {sorted(slots.SLOT_TEMPLATES)}, "
                    "pomocnicze: templates/theme/partials/*.html)."
                )
                continue
            try:
                templates[name] = content.decode("utf-8")
            except UnicodeDecodeError:
                result.errors.append(f"Szablon {path!r} nie jest w UTF-8.")
            continue
        result.errors.append(f"Plik spoza struktury paczki: {path!r}.")

    # Szablony: lint + kompilacja ograniczonym silnikiem + cykle include.
    for name, source in sorted(templates.items()):
        problems = slots.lint_template(name, source, package_templates=set(templates))
        result.errors += problems
        if not problems:
            result.errors += slots.compile_check(name, source)
    result.errors += slots.include_cycles(templates)
    result.templates = templates

    # Obrazy SVG – oczyszczane (ostrzeżenie z listą usuniętych elementów), niepoprawne – błąd.
    published: list[PublicFile] = []
    for path, content in sorted(assets.items()):
        ext = posixpath.splitext(path)[1].lower()
        if ext == ".svg":
            cleaned = sanitize_svg(content)
            if cleaned.errors:
                result.errors += [f"{path}: {e}." for e in cleaned.errors]
                continue
            if cleaned.removed:
                result.warnings.append(f"{path}: usunięto {', '.join(sorted(set(cleaned.removed)))}.")
            content = cleaned.data
        published.append(PublicFile(path=path, data=content, content_type=ASSET_TYPES[ext]))

    _check_logo_files(result.manifest, set(assets), result)

    # Arkusz motywu.
    try:
        css_text = files["theme.css"].decode("utf-8")
    except UnicodeDecodeError:
        result.errors.append("theme.css nie jest w UTF-8.")
        css_text = ""
    checked = css_mod.sanitize_css(css_text, assets=set(assets))
    result.errors += [f"theme.css: {e}" for e in checked.errors]
    result.theme_css = checked.css
    own_tokens = sorted(set(re.findall(r"(--t-[a-z0-9-]+)\s*:", checked.css)))
    if own_tokens:
        # Ostrzeżenie, nie błąd (THEME-02, przegląd L5): tokeny ustala ``tokens.json``, a arkusz motywu
        # ma je wyłącznie czytać – inaczej nadpisuje wartości z ``tokens.css`` i omija generator
        # pochodnych oraz kontrolę kontrastu. Dostosowanie koordynatora i tak wygrywa kolejnością.
        result.warnings.append(
            "theme.css ustawia tokeny platformy ("
            + ", ".join(own_tokens[:8])
            + (" …" if len(own_tokens) > 8 else "")
            + ") – tokeny należą do tokens.json; arkusz motywu powinien je tylko czytać (var(--t-…))."
        )

    screenshot = files.get("screenshot.png")
    if screenshot is not None:
        size = _png_size(screenshot)
        if size is None:
            result.errors.append("screenshot.png nie jest plikiem PNG.")
        else:
            result.has_screenshot = True
            if size != (1200, 900):
                result.warnings.append(f"screenshot.png ma {size[0]}×{size[1]} px – zalecane 1200×900.")
            published.append(PublicFile(path="screenshot.png", data=screenshot, content_type="image/png"))
    else:
        result.warnings.append("Brak screenshot.png – galeria pokaże motyw bez podglądu.")
    result.public_files = published

    if scan is not None and not result.errors:
        verdict, signature = scan(data)
        if verdict != "CLEAN":
            result.errors.append(f"Skaner antywirusowy odrzucił paczkę ({signature or verdict}).")
    return result
