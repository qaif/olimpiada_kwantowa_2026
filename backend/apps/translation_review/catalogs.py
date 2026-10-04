"""Katalogi gettext (``.po``): odczyt z kontekstem, indeks napisów jednego języka i zapis ``msgstr``.

Parser jest własny i mały (bez ``polib``) z tego samego powodu, co w
``apps/core/tests/test_translations.py``: katalogi są w podstawowej postaci, którą wypisuje
``makemessages``, a zależność dla kilkudziesięciu linii byłaby kolejną rzeczą do aktualizacji
w obrazie. W odróżnieniu od tamtego parsera ten zapamiętuje **numery linii** każdego wpisu – zapis
(``rewrite``) podmienia wyłącznie linie ``msgstr`` poprawionych wpisów, a reszta pliku zostaje
bajt w bajt. Dzięki temu PR z poprawkami tłumaczy jest diffem kilku linii, a nie przeformatowanym
plikiem.

Które pliki: katalog projektu (``LOCALE_PATHS``) i katalogi aplikacji z ``INSTALLED_APPS``
(``apps/<app>/locale``) – tam inni dopisują nowe napisy. Kolejność jest kolejnością pierwszeństwa
w Django: ``LOCALE_PATHS`` wygrywa, potem aplikacje w kolejności ``INSTALLED_APPS``.
"""

from __future__ import annotations

import ast
import gettext as gettext_module
import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.utils.translation import to_locale

from .models import string_key

#: Komentarz tłumacza, którym eksport oznacza wpis sprawdzony przez człowieka. Po wdrożeniu
#: i wyczyszczeniu nakładek to on mówi „ten napis już ktoś przejrzał” (filtr ``reviewed``).
#: ``msgmerge`` (``makemessages``) zachowuje komentarze tłumacza, więc znacznik przeżywa
#: odświeżenie katalogu.
REVIEWED_MARKER = "l10n-reviewed"


@dataclass
class PoEntry:
    msgid: str
    msgctxt: str | None = None
    msgid_plural: str | None = None
    msgstr: dict[int, str] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)
    locations: list[str] = field(default_factory=list)
    extracted: list[str] = field(default_factory=list)
    translator_comments: list[str] = field(default_factory=list)
    #: Pierwsza linia wpisu (komentarze włącznie) i zakres linii ``msgstr`` (koniec wyłącznie).
    start: int = 0
    msgstr_start: int | None = None
    msgstr_end: int | None = None

    @property
    def reviewed(self) -> bool:
        return REVIEWED_MARKER in self.translator_comments


def _literal(text: str) -> str:
    """Treść jednego cudzysłowowanego kawałka ``.po``. Ucieczki ``.po`` są ucieczkami Pythona."""
    return ast.literal_eval(text)


def parse(text: str) -> list[PoEntry]:
    """Wpisy katalogu (bez nagłówka i bez wpisów przestarzałych ``#~``)."""
    entries: list[PoEntry] = []
    current: PoEntry | None = None
    target: tuple | None = None  # pole, do którego doklejają się linie kontynuacji

    def finish():
        nonlocal current, target
        if current is not None and current.msgid and current.msgstr_start is not None:
            entries.append(current)
        current, target = None, None

    for index, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line:
            finish()
            continue
        if line.startswith("#~"):
            continue
        starts_entry = line.startswith("#") or line.startswith("msgctxt ") or line.startswith("msgid ")
        if starts_entry and current is not None and current.msgstr_start is not None:
            finish()
        if current is None:
            if not starts_entry:
                continue  # śmieci poza wpisem – nie zgadujemy, do czego należą
            current = PoEntry(msgid="", start=index)
        if line.startswith("#"):
            if line.startswith("#:"):
                current.locations.extend(line[2:].split())
            elif line.startswith("#,"):
                current.flags.update(flag.strip() for flag in line[2:].split(",") if flag.strip())
            elif line.startswith("#."):
                current.extracted.append(line[2:].strip())
            elif line.startswith("#|"):
                pass  # poprzedni msgid (fuzzy) – nie jest kontekstem dla człowieka
            else:
                current.translator_comments.append(line[1:].strip())
            target = None
        elif line.startswith("msgctxt "):
            current.msgctxt = _literal(line[8:])
            target = ("msgctxt",)
        elif line.startswith("msgid_plural "):
            current.msgid_plural = _literal(line[13:])
            target = ("msgid_plural",)
        elif line.startswith("msgid "):
            current.msgid = _literal(line[6:])
            target = ("msgid",)
        elif line.startswith("msgstr"):
            if current.msgstr_start is None:
                current.msgstr_start = index
            if line.startswith("msgstr["):
                form = int(line[7 : line.index("]")])
                current.msgstr[form] = _literal(line[line.index("]") + 1 :].strip())
            else:
                form = 0
                current.msgstr[0] = _literal(line[6:].strip())
            current.msgstr_end = index + 1
            target = ("msgstr", form)
        elif line.startswith('"') and target is not None:
            value = _literal(line)
            if target[0] == "msgstr":
                current.msgstr[target[1]] += value
                current.msgstr_end = index + 1
            else:
                setattr(current, target[0], getattr(current, target[0]) + value)
    finish()
    return entries


def header(text: str) -> str:
    """Nagłówek katalogu (``msgstr`` wpisu z pustym ``msgid``)."""
    lines = text.splitlines()
    for position, raw in enumerate(lines):
        if (
            raw.strip() == 'msgid ""'
            and position + 1 < len(lines)
            and lines[position + 1].startswith("msgstr ")
        ):
            value = _literal(lines[position + 1][7:].strip())
            for follow in lines[position + 2 :]:
                if not follow.startswith('"'):
                    break
                value += _literal(follow)
            return value
    return ""


# --- zapis ---------------------------------------------------------------------------------------

#: Szerokość linii jak w ``msgcat`` – zawijamy tylko po to, żeby diff był czytelny.
WRAP = 76


def _quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\n", "\\n")
    return f'"{escaped}"'


def _chunks(text: str) -> list[str]:
    """Kawałki do zapisu wieloliniowego: po każdym ``\\n`` i po spacjach przy długich liniach."""
    pieces: list[str] = []
    for paragraph in text.splitlines(keepends=True):
        while len(paragraph) > WRAP:
            cut = paragraph.rfind(" ", 0, WRAP)
            if cut <= 0:
                break
            pieces.append(paragraph[: cut + 1])
            paragraph = paragraph[cut + 1 :]
        if paragraph:
            pieces.append(paragraph)
    return pieces


def format_field(keyword: str, text: str) -> list[str]:
    """Linie pola ``msgstr`` w postaci, którą czyta ``msgfmt`` i nasze parsery."""
    single = f"{keyword} {_quote(text)}"
    if "\n" not in text.rstrip("\n") and len(single) <= WRAP + 3:
        return [single]
    return [f'{keyword} ""', *(_quote(piece) for piece in _chunks(text))]


def rewrite(
    text: str, updates: dict[tuple[str | None, str], dict[int, str | None]], *, mark_reviewed: bool = True
) -> tuple[str, int]:
    """Nowa treść katalogu z podmienionymi ``msgstr`` i znacznikiem przeglądu.

    ``updates`` to ``{(msgctxt, msgid): {numer formy: tekst}}``; tekst ``None`` znaczy „nie zmieniaj
    ``msgstr``, dopisz sam znacznik” (potwierdzenie obecnego tłumaczenia). Zmieniają się wyłącznie linie
    ``msgstr`` wskazanych wpisów i jedna linia komentarza ``# l10n-reviewed`` (gdy jej brakowało);
    reszta pliku – łącznie z końcami linii – zostaje bez zmian. Zwraca też liczbę zmienionych wpisów.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(("\n", "\r\n"))
    lines = text.splitlines()
    changed = 0
    # Od końca pliku, żeby wstawione linie nie przesuwały numerów wpisów jeszcze nieprzetworzonych.
    for entry in reversed(parse(text)):
        forms = updates.get((entry.msgctxt or None, entry.msgid))
        if not forms:
            continue
        texts = {form: value for form, value in forms.items() if value is not None}
        if texts:
            merged = dict(entry.msgstr)
            merged.update(texts)
            if entry.msgid_plural is None:
                new_lines = format_field("msgstr", merged.get(0, ""))
            else:
                new_lines = []
                for form in sorted(merged):
                    new_lines.extend(format_field(f"msgstr[{form}]", merged[form]))
            lines[entry.msgstr_start : entry.msgstr_end] = new_lines
        elif entry.reviewed or not mark_reviewed:
            continue  # sam znacznik, a ten już jest – wpis bez zmian
        if mark_reviewed and not entry.reviewed:
            lines.insert(entry.start, f"# {REVIEWED_MARKER}")
        changed += 1
    result = newline.join(lines)
    return (result + newline if trailing else result), changed


# --- pliki i indeks ------------------------------------------------------------------------------


_compiled: dict[str, tuple[int, dict]] = {}


def compiled_text(language: str, msgctxt: str | None, msgid: str, plural_index: int | None) -> str | None:
    """Tekst, który dziś oddaje **skompilowany** katalog (``.mo`` obok ``.po``), albo ``None``.

    ``--prune`` pyta właśnie o to, a nie o ``.po``: plik źródłowy bywa już poprawiony w checkoucie,
    zanim obraz z nowym ``.mo`` stanie na serwerze – a dopiero ``.mo`` widzi gettext.
    """
    base = f"{msgctxt}\x04{msgid}" if msgctxt else msgid
    key = base if plural_index is None else (base, plural_index)
    for path in catalog_paths(language):
        mo = path.with_suffix(".mo")
        if not mo.is_file():
            continue
        stamp = mo.stat().st_mtime_ns
        cached = _compiled.get(str(mo))
        if cached is None or cached[0] != stamp:
            with mo.open("rb") as handle:
                catalog = gettext_module.GNUTranslations(handle)._catalog
            cached = (stamp, catalog)
            _compiled[str(mo)] = cached
        value = cached[1].get(key)
        if value:
            return value
    return None


def review_languages() -> list[str]:
    """Języki do przeglądu: wszystkie z ``LANGUAGES`` poza źródłowym (polskie ``msgid``)."""
    return [code for code, _label in settings.LANGUAGES if code != settings.LANGUAGE_CODE]


def language_label(code: str) -> str:
    return dict(settings.LANGUAGES).get(code, code)


def catalog_paths(language: str) -> list[Path]:
    """Istniejące pliki ``django.po`` języka, w kolejności pierwszeństwa Django."""
    folder = to_locale(language)
    roots = [Path(path) for path in settings.LOCALE_PATHS]
    base = Path(settings.BASE_DIR).resolve()
    for config in django_apps.get_app_configs():
        app_path = Path(config.path).resolve()
        # Wyłącznie nasze aplikacje: katalogi Django, Wagtaila i bibliotek nie są do poprawiania
        # przez wolontariuszy (i nie trafiają do naszego repozytorium).
        if base in app_path.parents:
            roots.append(app_path / "locale")
    paths = []
    for root in roots:
        candidate = root / folder / "LC_MESSAGES" / "django.po"
        if candidate.is_file() and candidate not in paths:
            paths.append(candidate)
    return paths


_plurals: dict[str, object] = {}


def plural_function(language: str):
    """Reguła form mnogich **naszych** katalogów języka (``Plural-Forms`` katalogu projektu).

    Nie ta z obiektu ``DjangoTranslation``: ten bierze regułę z katalogu Django, a ta bywa inna
    (hiszpański Django ma trzy formy, nasz katalog – dwie). Numer formy w nakładce pochodzi z naszego
    ``.po``, więc i reguła wyboru formy musi pochodzić stamtąd. ``None`` = brak katalogu albo reguły.
    """
    if language in _plurals:
        return _plurals[language]
    function = None
    for path in catalog_paths(language):
        match = re.search(r"plural=([^;]+);?", header(path.read_text(encoding="utf-8")))
        if match:
            try:
                function = gettext_module.c2py(match.group(1).strip())
            except ValueError:
                function = None
            break
    _plurals[language] = function
    return function


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(settings.BASE_DIR).resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class Row:
    """Jeden napis do przejrzenia (forma mnoga = osobny wiersz)."""

    key: str
    msgctxt: str | None
    msgid: str
    msgid_plural: str | None
    plural_index: int | None
    #: Tekst źródłowy tej formy: ``msgid``, a dla form mnogich od drugiej – ``msgid_plural``.
    source: str
    #: Tłumaczenie z katalogu (pierwsze niepuste wg pierwszeństwa), bez nakładki z bazy.
    translation: str
    reviewed: bool
    locations: tuple[str, ...]
    comments: tuple[str, ...]
    flags: frozenset[str]
    catalogs: tuple[str, ...]


@dataclass
class Index:
    rows: list[Row]
    by_key: dict[str, Row]


_cache: dict[str, tuple[tuple, Index]] = {}
_lock = threading.Lock()


def _signature(paths: list[Path]) -> tuple:
    signature = []
    for path in paths:
        stat = path.stat()
        signature.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def _build(paths: list[Path]) -> Index:
    rows: dict[str, Row] = {}
    for path in paths:
        name = relative(path)
        for entry in parse(path.read_text(encoding="utf-8")):
            indexes = sorted(entry.msgstr) if entry.msgid_plural is not None else [None]
            for form in indexes:
                key = string_key(entry.msgctxt, entry.msgid, form)
                text = entry.msgstr.get(form or 0, "")
                existing = rows.get(key)
                if existing is not None:
                    # Ten sam napis w kolejnym katalogu: liczy się pierwsze **niepuste** tłumaczenie
                    # (tak wybiera Django), a lista plików rośnie – eksport pisze do każdego z nich.
                    rows[key] = replace(
                        existing,
                        translation=existing.translation or text,
                        reviewed=existing.reviewed or entry.reviewed,
                        catalogs=(*existing.catalogs, name),
                    )
                    continue
                rows[key] = Row(
                    key=key,
                    msgctxt=entry.msgctxt,
                    msgid=entry.msgid,
                    msgid_plural=entry.msgid_plural,
                    plural_index=form,
                    source=entry.msgid_plural if form else entry.msgid,
                    translation=text,
                    reviewed=entry.reviewed,
                    locations=tuple(entry.locations),
                    comments=tuple(entry.extracted),
                    flags=frozenset(entry.flags),
                    catalogs=(name,),
                )
    ordered = list(rows.values())
    return Index(rows=ordered, by_key=rows)


def index(language: str) -> Index:
    """Napisy języka. Buforowane w procesie do zmiany któregoś z plików (czas modyfikacji)."""
    paths = catalog_paths(language)
    signature = _signature(paths)
    with _lock:
        cached = _cache.get(language)
        if cached is not None and cached[0] == signature:
            return cached[1]
    built = _build(paths)
    with _lock:
        _cache[language] = (signature, built)
    return built


def forget() -> None:
    """Czyści bufor indeksów (testy, eksport do plików w tym samym procesie)."""
    with _lock:
        _cache.clear()
        _plurals.clear()
