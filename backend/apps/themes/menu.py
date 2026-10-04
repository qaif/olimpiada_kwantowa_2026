"""Nadpisania menu serwisu przez koordynatora (THEME-02 § 1).

Menu buduje ``apps.cms.context_processors.cms_menu`` (drzewo Wagtaila, lista zapasowa, pozycja
„Dla szkół/nauczycieli”) – i tak zostaje. Ten moduł nakłada na gotową listę **warstwę** zapisaną
w ``themes.SiteMenu``: kolejność, ukrycie, etykiety per język interfejsu, własne odnośniki
i jednopoziomowe grupy rozwijane. Wynik ma dokładnie ten kształt, który znają szablony
(``templates/theme/nav.html``, ``header.html`` i sloty motywów), plus pola ``new_tab``/``external``.

**Koszt dla konkursu bez nadpisań: zero.** Numer rewizji menu jest powielony
w ``Competition.theme_options["menu"]`` – kolumnie wiersza, który warstwa konkursu i tak pobiera.
Bez klucza :func:`apply_overrides` oddaje listę bez zmian, bez zapytania i bez kopii (Konkurs #1
co do bajtu i liczby zapytań). Z kluczem – jedno zapytanie na proces na rewizję (pamięć procesu,
jak ``ThemeRuntime``) plus jedno o adresy stron, gdy menu ma własne odnośniki do stron serwisu.

Bezpieczeństwo: etykiety i adresy to **dane** – szablon je escapuje (żadnego ``|safe``), adres
przechodzi :func:`clean_url` przy zapisie (``http``/``https`` albo ścieżka wewnętrzna; nigdy
``javascript:``/``data:``), a odnośnik do strony serwisu jest rozstrzygany przy renderze wyłącznie
w drzewie witryny **tego** konkursu.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
from urllib.parse import urlsplit

from django.utils.translation import get_language
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

#: Klucz rewizji menu w ``Competition.theme_options``.
OPTIONS_KEY = "menu"

TYPE_AUTO = "auto"
TYPE_LINK = "link"
TYPE_GROUP = "group"
TYPES = (TYPE_AUTO, TYPE_LINK, TYPE_GROUP)

MAX_ENTRIES = 40
MAX_LINKS = 15
MAX_GROUPS = 8
MAX_LABEL = 60
MAX_URL = 500

#: Klucze pozycji automatycznych (``cms_menu``) i własnych (``link-…``/``group-…``).
KEY_RE = re.compile(r"^(home|teachers|p\d{1,10}|f:[a-z0-9/_-]{1,80}|link-[0-9a-f]{8}|group-[0-9a-f]{8})$")
LANG_RE = re.compile(r"^[a-z]{2,3}(?:[-_][A-Za-z]{2,4})?$")
CONTROL = re.compile(r"[\x00-\x1f\x7f  ]")

_CACHE: dict[int, tuple[int, list[dict]]] = {}
_LOCK = threading.Lock()


class MenuError(ValueError):
    """Niepoprawne nadpisanie menu – komunikat dla koordynatora."""


# --- walidacja ----------------------------------------------------------------------------------


def new_key(kind: str) -> str:
    return f"{kind}-{secrets.token_hex(4)}"


def clean_label(value) -> str:
    """Etykieta: napis bez znaków sterujących, ≤ ``MAX_LABEL`` znaków (pusta = brak nadpisania)."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MenuError(_("Etykieta musi być napisem."))
    value = " ".join(value.split())
    if CONTROL.search(value):
        raise MenuError(_("Etykieta zawiera niedozwolone znaki."))
    if len(value) > MAX_LABEL:
        raise MenuError(_("Etykieta może mieć najwyżej %(limit)d znaków.") % {"limit": MAX_LABEL})
    return value


def clean_url(value) -> str:
    """Adres własnego odnośnika: ``https://host/…``, ``http://host/…`` albo ``/ścieżka``.

    Zakazane: każdy inny schemat (``javascript:``, ``data:``, ``vbscript:``, ``mailto:``…), adres
    względny bez ``/`` (zależałby od strony, na której stoi menu), ``//host`` (adres z cudzym hostem
    udający ścieżkę), ukośnik wsteczny i znaki sterujące (przeglądarki „naprawiają” ``/\\host``
    i ``java\\tscript:`` na adresy, których nikt tu nie wpisał).
    """
    if not isinstance(value, str):
        raise MenuError(_("Adres musi być napisem."))
    value = value.strip()
    if not value:
        raise MenuError(_("Podaj adres odnośnika albo wybierz stronę serwisu."))
    if len(value) > MAX_URL:
        raise MenuError(_("Adres może mieć najwyżej %(limit)d znaków.") % {"limit": MAX_URL})
    if CONTROL.search(value) or "\\" in value or any(ch.isspace() for ch in value):
        raise MenuError(_("Adres zawiera niedozwolone znaki."))
    if value.startswith("/"):
        if value.startswith("//"):
            raise MenuError(
                _("Adres zaczynający się od „//” jest niedozwolony – podaj pełny adres https://.")
            )
        return value
    parts = urlsplit(value)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or parts.netloc.startswith("@"):
        raise MenuError(
            _("Dozwolone są wyłącznie adresy https:// i http:// albo ścieżki serwisu zaczynające się od „/”.")
        )
    if "@" in parts.netloc:
        raise MenuError(_("Adres z danymi logowania („użytkownik@host”) jest niedozwolony."))
    return value


def is_external(url: str) -> bool:
    return not url.startswith("/")


def _labels(raw, languages: tuple[str, ...]) -> dict[str, str]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise MenuError(_("Etykiety muszą być słownikiem język → napis."))
    out = {}
    for code, label in raw.items():
        if not isinstance(code, str) or not LANG_RE.match(code) or code not in languages:
            raise MenuError(
                _("Język „%(code)s” nie jest językiem interfejsu tego konkursu.") % {"code": code}
            )
        cleaned = clean_label(label)
        if cleaned:
            out[code] = cleaned
    return out


def site_pages(competition):
    """Opublikowane strony drzewa witryny konkursu (queryset) – źródło wyboru strony w odnośniku."""
    from wagtail.models import Page

    site = getattr(competition, "site", None)
    root = getattr(site, "root_page", None) if site is not None else None
    if root is None:
        return Page.objects.none()
    return Page.objects.live().filter(path__startswith=root.path).order_by("path")


def clean_items(competition, raw_items, *, auto_keys: dict[str, dict]) -> list[dict]:
    """Waliduje listę wpisów menu. ``auto_keys`` – pozycje automatyczne: klucz → opis pozycji.

    Wynik ma postać kanoniczną (tylko znane pola), w której zapisujemy go w ``SiteMenu.items``.
    """
    if not isinstance(raw_items, list):
        raise MenuError(_("Menu musi być listą pozycji."))
    if len(raw_items) > MAX_ENTRIES:
        raise MenuError(_("Menu może mieć najwyżej %(limit)d pozycji.") % {"limit": MAX_ENTRIES})
    languages = tuple(getattr(competition, "ui_languages", ()) or ())
    seen: set[str] = set()
    items: list[dict] = []
    page_ids: list[int] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise MenuError(_("Pozycja menu musi być obiektem."))
        kind = raw.get("type", TYPE_AUTO)
        key = raw.get("key")
        if kind not in TYPES or not isinstance(key, str) or not KEY_RE.match(key):
            raise MenuError(_("Nieznana pozycja menu."))
        if key in seen:
            raise MenuError(_("Pozycja menu powtarza się."))
        seen.add(key)
        if kind == TYPE_AUTO and key not in auto_keys:
            # Pozycja, której już nie ma w drzewie (strona wycofana między odczytem a zapisem):
            # pomijamy ją, zamiast odrzucać cały formularz.
            continue
        if (kind == TYPE_LINK) != key.startswith("link-") or (kind == TYPE_GROUP) != key.startswith("group-"):
            raise MenuError(_("Typ pozycji nie zgadza się z jej kluczem."))
        entry = {
            "key": key,
            "type": kind,
            "hidden": bool(raw.get("hidden")),
            "labels": _labels(raw.get("labels"), languages),
        }
        if kind == TYPE_LINK:
            page = raw.get("page")
            if page not in (None, ""):
                if not str(page).isdigit():
                    raise MenuError(_("Nieznana strona serwisu."))
                entry["page"] = int(page)
                entry["url"] = ""
                page_ids.append(int(page))
            else:
                entry["page"] = None
                entry["url"] = clean_url(raw.get("url"))
            entry["new_tab"] = bool(raw.get("new_tab"))
            if not entry["labels"]:
                raise MenuError(_("Własny odnośnik musi mieć etykietę przynajmniej w jednym języku."))
        if kind == TYPE_GROUP and not entry["labels"]:
            raise MenuError(_("Grupa musi mieć etykietę przynajmniej w jednym języku."))
        entry["parent"] = raw.get("parent") or ""
        items.append(entry)
    groups = {item["key"] for item in items if item["type"] == TYPE_GROUP}
    if sum(item["type"] == TYPE_LINK for item in items) > MAX_LINKS:
        raise MenuError(_("Menu może mieć najwyżej %(limit)d własnych odnośników.") % {"limit": MAX_LINKS})
    if len(groups) > MAX_GROUPS:
        raise MenuError(_("Menu może mieć najwyżej %(limit)d grup.") % {"limit": MAX_GROUPS})
    for item in items:
        parent = item["parent"]
        if not parent:
            continue
        if parent not in groups:
            raise MenuError(_("Pozycja wskazuje grupę, której nie ma."))
        if item["type"] == TYPE_GROUP:
            raise MenuError(
                _("Grupa nie może należeć do innej grupy (menu ma jeden poziom list rozwijanych).")
            )
        info = auto_keys.get(item["key"]) or {}
        if info.get("home"):
            raise MenuError(_("Strony głównej nie da się przenieść do grupy."))
        if info.get("has_children"):
            raise MenuError(_("Pozycja z własną listą rozwijaną nie może należeć do grupy."))
    if page_ids:
        allowed = set(site_pages(competition).filter(pk__in=page_ids).values_list("pk", flat=True))
        if set(page_ids) - allowed:
            # Strona spoza drzewa witryny tego konkursu (albo nieopublikowana) – izolacja konkursów.
            raise MenuError(
                _("Wybrana strona nie należy do serwisu tego konkursu albo nie jest opublikowana.")
            )
    return items


def is_default(items: list[dict], default_keys: list[str]) -> bool:
    """Czy nadpisania niczego nie zmieniają (kolejność domyślna, bez etykiet, ukryć i własnych)."""
    if any(
        item["type"] != TYPE_AUTO or item["hidden"] or item["labels"] or item.get("parent") for item in items
    ):
        return False
    listed = [item["key"] for item in items]
    return listed == [key for key in default_keys if key in listed] and listed == default_keys[: len(listed)]


# --- render -------------------------------------------------------------------------------------


def _config(competition, revision) -> list[dict]:
    cached = _CACHE.get(competition.pk)
    if cached is not None and cached[0] == revision:
        return cached[1]
    from .models import SiteMenu

    row = SiteMenu.objects.filter(competition_id=competition.pk).values_list("revision", "items").first()
    items = list(row[1] or []) if row is not None else []
    with _LOCK:
        _CACHE[competition.pk] = (row[0] if row is not None else revision, items)
    return items


def forget(competition_id: int | None = None) -> None:
    """Czyści pamięć procesu (testy; zapis menu w tym procesie)."""
    with _LOCK:
        if competition_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(competition_id, None)


def _pick_label(labels: dict[str, str], language: str, fallback_languages: tuple[str, ...]) -> str:
    if not labels:
        return ""
    if labels.get(language):
        return labels[language]
    short = (language or "").split("-")[0]
    if labels.get(short):
        return labels[short]
    for code in fallback_languages:
        if labels.get(code):
            return labels[code]
    return next(iter(labels.values()), "")


def _page_urls(ids: list[int], request, competition) -> dict[int, str]:
    if not ids:
        return {}
    try:
        return {
            page.pk: page.get_url(request=request) or ""
            for page in site_pages(competition).filter(pk__in=ids)
        }
    except Exception:  # noqa: BLE001 - menu nie może położyć strony: odnośnik do strony znika
        logger.warning("Menu: nie udało się wyznaczyć adresów stron własnych odnośników.", exc_info=True)
        return {}


def _internal(url: str) -> str:
    """Ścieżka wewnętrzna względem korzenia serwisu konkursu (prefiks ścieżki w trybie prefiksowym)."""
    from django.urls import get_script_prefix

    prefix = get_script_prefix()
    if prefix != "/" and not url.startswith(prefix):
        return prefix.rstrip("/") + url
    return url


def _link_url(entry: dict, page_urls: dict[int, str]) -> str:
    if entry.get("page"):
        return page_urls.get(entry["page"], "")
    url = entry.get("url") or ""
    return _internal(url) if url.startswith("/") else url


def build_menu(menu: list[dict], items: list[dict], request, competition, *, language: str) -> list[dict]:
    """Menu domyślne ``menu`` po nałożeniu wpisów ``items`` (czysta funkcja – testowalna bez bazy)."""
    path = getattr(request, "path", "")
    fallback = (getattr(competition, "default_language", "") or "", "en", "pl")
    defaults = {item.get("key") or item.get("slug"): item for item in menu}
    page_ids = [item["page"] for item in items if item.get("type") == TYPE_LINK and item.get("page")]
    urls = _page_urls(page_ids, request, competition)
    used: set[str] = set()
    ordered: list[tuple[dict, str]] = []
    groups: dict[str, dict] = {}
    for entry in items:
        kind = entry.get("type", TYPE_AUTO)
        key = entry.get("key", "")
        labels = entry.get("labels") or {}
        # Pozycja automatyczna bez etykiety w tym języku zostaje przy tytule domyślnym (przetłumaczonym
        # przez ramę serwisu); własna pozycja nie ma tytułu domyślnego, więc schodzi na inne języki.
        if kind == TYPE_AUTO:
            label = labels.get(language) or labels.get(language.split("-")[0], "")
        else:
            label = _pick_label(labels, language, fallback)
        if kind == TYPE_AUTO:
            base = defaults.get(key)
            if base is None:
                continue
            used.add(key)
            if entry.get("hidden"):
                continue
            item = dict(base)
            if label:
                item["title"] = label
        elif kind == TYPE_LINK:
            if entry.get("hidden") or not label:
                continue
            url = _link_url(entry, urls)
            if not url:
                continue
            item = {
                "key": key,
                "slug": "",
                "title": label,
                "url": url,
                "children": [],
                "active": path == url,
                "primary": False,
                "home": False,
                "new_tab": bool(entry.get("new_tab")),
                "external": is_external(url),
            }
        elif kind == TYPE_GROUP:
            if entry.get("hidden") or not label:
                continue
            item = {
                "key": key,
                "slug": "",
                "title": label,
                "url": "",
                "children": [],
                "active": False,
                "primary": False,
                "home": False,
                "group": True,
                "new_tab": False,
                "external": False,
            }
            groups[key] = item
        else:
            continue
        ordered.append((item, entry.get("parent") or ""))
    # Pozycje domyślne, których koordynator jeszcze nie widział (nowa strona w drzewie), stają na
    # końcu w kolejności domyślnej – nadpisanie nie może zamknąć menu przed redakcją.
    ordered += [(item, "") for item in menu if (item.get("key") or item.get("slug")) not in used]
    result: list[dict] = []
    for item, parent in ordered:
        group = groups.get(parent)
        if group is not None and not item.get("children") and not item.get("group") and not item.get("home"):
            group["children"].append(
                {
                    "title": item["title"],
                    "url": item["url"],
                    "active": item.get("active", False),
                    "new_tab": item.get("new_tab", False),
                    "external": item.get("external", False),
                }
            )
            continue
        result.append(item)
    final = []
    for item in result:
        if item.get("group"):
            if not item["children"]:
                continue
            item["url"] = item["children"][0]["url"]
            item["active"] = any(child["active"] for child in item["children"])
        final.append(item)
    return final


def apply_overrides(menu: list[dict], request) -> list[dict]:
    """Menu z nadpisaniami konkursu żądania – albo ``menu`` bez zmian (bez zapytania)."""
    competition = getattr(request, "competition", None)
    if competition is None or getattr(request, "_skip_menu_overrides", False):
        return menu
    revision = (getattr(competition, "theme_options", None) or {}).get(OPTIONS_KEY)
    if not revision:
        return menu
    try:
        items = _config(competition, revision)
        if not items:
            return menu
        return build_menu(menu, items, request, competition, language=get_language() or "")
    except Exception:  # noqa: BLE001 - zepsute nadpisanie nie może położyć nagłówka każdej strony
        logger.exception("Menu: nadpisania konkursu %s pominięte (błąd).", competition.pk)
        return menu
