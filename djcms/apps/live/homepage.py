"""Sekcje strony głównej zbierane z **innych** stron djcms: aktualności, partnerzy, dokumenty.

Port trzech kawałków ``HomePage.get_context`` aplikacji głównej (``latest_news``/``news_index``,
``_partners_with_entries``, ``_download_rows``/``documents_index``). To nie są dane zawodów – to
treść redakcyjna ``dj.`` – ale stoją na stronie głównej, której szablon należy do DJ-01f, więc
zapytania mieszkają obok niego.

Jak w Wagtailu żadna sekcja nie zna ani jednego sluga: typ strony rozpoznajemy po szablonie
(``PageContent.template``, tabela 6.1 speca), a dane – po rozszerzeniach i wtyczkach redakcyjnych
z DJ-01e (``NewsMeta``, ``PartnerPlugin``, ``AttachmentPlugin``). Liczą się wyłącznie
**opublikowane** wersje treści (``PageContent.objects`` pod djangocms-versioning oddaje tylko je).

Wszystko jest **per witryna** (DJ-02): sekcje strony głównej konkursu A biorą wyłącznie strony
witryny A – te same szablony (``news.html``, ``partners.html``) stoją w drzewie każdego konkursu.
Bez witryny (``site=None``) sekcje są puste, a nie „ze wszystkich witryn”.

Moduł jest tolerancyjny wobec braków: rozszerzenie, którego strona nie ma, to pusta data/zajawka;
wtyczka, której klasy nie ma w rejestrze, jest pomijana (``downcast_plugins``); logotyp, którego
miniatury nie da się zrobić, to sam podpis. Strona główna ma się wyrenderować zawsze.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from cms.models import CMSPlugin, PageContent
from cms.utils.plugins import downcast_plugins
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist

from .chrome import safe_href

logger = logging.getLogger(__name__)

LANGUAGE = "pl"

#: Szablony typów stron (``CMS_TEMPLATES``, tabela 6.1) – odpowiednik klas stron Wagtaila.
NEWS_TEMPLATE = "dj/pages/news.html"
NEWS_INDEX_TEMPLATE = "dj/pages/news_index.html"
PARTNERS_TEMPLATE = "dj/pages/partners.html"
#: Strona partnerów na żywo z Wagtaila (DJ-02 D9) – dane pasa z ``GET partners``, nie z wtyczek.
LIVE_PARTNERS_TEMPLATE = "dj/live/partners_page.html"
DOCUMENT_INDEX_TEMPLATE = "dj/pages/document_index.html"
#: Strony, przy których wisi lista załączników – ``_download_rows`` bierze ``DocumentPage`` i ``ContentPage``.
ATTACHMENT_TEMPLATES = ("dj/pages/document.html", "dj/pages/content.html")

#: Wtyczki redakcyjne DJ-01e (nazwy klas z tabeli 6.2) i sloty, w których stoją.
PARTNER_PLUGIN = "PartnerPlugin"
PARTNERS_SLOT = "partners"
ATTACHMENT_PLUGIN = "AttachmentPlugin"
ATTACHMENTS_SLOT = "attachments"

#: Relacja odwrotna ``PageContent`` → ``NewsMeta`` (``PageContentExtension``, DJ-01e).
NEWS_META_ACCESSOR = "newsmeta"

LATEST_NEWS_COUNT = 3

#: Miniatura znaku partnera – odpowiednik renditionu ``max-600x240`` z ``home_page.html``.
PARTNER_LOGO_SIZE = (600, 240)


def _published(template: str | tuple[str, ...], site):
    if site is None:
        return PageContent.objects.none()
    templates = (template,) if isinstance(template, str) else template
    return (
        PageContent.objects.filter(language=LANGUAGE, template__in=templates, page__site=site)
        .select_related("page")
        .order_by("page__path")
    )


def _url(content) -> str:
    return content.page.get_absolute_url(LANGUAGE)


def _first(template: str, site) -> dict | None:
    content = _published(template, site).first()
    return {"title": content.title, "url": _url(content)} if content is not None else None


def _plugins(contents, slot: str, plugin_type: str) -> dict[int, list]:
    """Wtyczki ``plugin_type`` najwyższego poziomu w slocie ``slot`` – per ``PageContent.pk``, po pozycji."""
    ids = [content.pk for content in contents]
    if not ids:
        return {}
    queryset = CMSPlugin.objects.filter(
        placeholder__content_type=ContentType.objects.get_for_model(PageContent),
        placeholder__object_id__in=ids,
        placeholder__slot=slot,
        plugin_type=plugin_type,
        language=LANGUAGE,
        parent__isnull=True,
    ).select_related("placeholder")
    found: dict[int, list] = {}
    for plugin in sorted(downcast_plugins(list(queryset)), key=lambda item: item.position):
        found.setdefault(plugin.placeholder.object_id, []).append(plugin)
    return found


# --- aktualności ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    date: date | None
    lead: str
    page_id: int


def _news_meta(content) -> tuple[date | None, str]:
    try:
        meta = getattr(content, NEWS_META_ACCESSOR)
    except AttributeError, ObjectDoesNotExist:
        return None, ""
    value = getattr(meta, "date", None)
    return (value if isinstance(value, date) else None), str(getattr(meta, "lead", "") or "")


def latest_news(site, limit: int = LATEST_NEWS_COUNT) -> list[NewsItem]:
    """Najnowsze aktualności: data malejąco, potem strona malejąco (``order_by("-date", "-pk")``)."""
    items = []
    for content in _published(NEWS_TEMPLATE, site):
        news_date, lead = _news_meta(content)
        items.append(NewsItem(content.title, _url(content), news_date, lead, content.page_id))
    # Aktualność bez daty na końcu – w Wagtailu data jest wymagana, tu rozszerzenia może brakować.
    items.sort(key=lambda item: (item.date is not None, item.date or date.min, item.page_id), reverse=True)
    return items[:limit]


# --- partnerzy ------------------------------------------------------------------------------------


def _logo(logo) -> dict | None:
    """Miniatura znaku (``easy-thumbnails``). Plik, którego nie da się przetworzyć, to brak znaku."""
    if logo is None:
        return None
    try:
        from easy_thumbnails.files import get_thumbnailer

        thumb = get_thumbnailer(logo.file).get_thumbnail({"size": PARTNER_LOGO_SIZE})
    except Exception:  # noqa: BLE001 - zepsuty plik w filerze nie może położyć strony głównej
        logger.warning(
            "Nie udało się przygotować miniatury znaku partnera (plik #%s).", logo.pk, exc_info=True
        )
        return None
    return {"src": thumb.url, "width": thumb.width, "height": thumb.height}


def partners_strip(site, request=None) -> dict | None:
    """Strona partnerów **z co najmniej jednym wpisem** – inaczej ``None`` (sekcja znika).

    Strona partnerów na żywo (``LIVE_PARTNERS_TEMPLATE``, DJ-02 D9) bierze wpisy z ``GET partners``
    – tego samego pobrania, co wtyczka na jej stronie; martwe API = brak sekcji (ozdoba strony
    głównej, a nie dane zawodów).
    """
    page = _published((PARTNERS_TEMPLATE, LIVE_PARTNERS_TEMPLATE), site).first()
    if page is None:
        return None
    if page.template == LIVE_PARTNERS_TEMPLATE:
        from .data import fetch, partners_strip_entries

        live = partners_strip_entries(fetch("partners", request)) if request is not None else []
        return {"title": page.title, "url": _url(page), "entries": live} if live else None
    entries = []
    for plugin in _plugins([page], PARTNERS_SLOT, PARTNER_PLUGIN).get(page.pk, []):
        entries.append(
            {
                "name": getattr(plugin, "name", ""),
                "url": safe_href(getattr(plugin, "url", "")),
                "logo": _logo(getattr(plugin, "logo", None)),
                "is_wide": bool(getattr(plugin, "is_wide", False)),
            }
        )
    if not entries:
        return None
    return {"title": page.title, "url": _url(page), "entries": entries}


# --- dokumenty do pobrania ------------------------------------------------------------------------


def _attachment_href(attachment) -> str:
    """Adres pliku z filera albo adres podany ręcznie – ten drugi tylko ``http(s)``/ścieżka."""
    file = getattr(attachment, "file", None)
    if file is not None:
        return safe_href(file.url)
    return safe_href(getattr(attachment, "url", "") or "")


def _is_pdf(attachment) -> bool:
    return str(getattr(attachment, "extension", "") or "").lower() == "pdf"


def download_rows(site) -> list[dict]:
    """Opublikowane strony z przypiętym PDF-em – pierwszy PDF każdej, w kolejności drzewa."""
    contents = list(_published(ATTACHMENT_TEMPLATES, site))
    attachments = _plugins(contents, ATTACHMENTS_SLOT, ATTACHMENT_PLUGIN)
    rows = []
    for content in contents:
        pdf = next((item for item in attachments.get(content.pk, []) if _is_pdf(item)), None)
        if pdf is None or not _attachment_href(pdf):
            continue
        rows.append(
            {
                "title": content.title,
                "url": _url(content),
                "extension": str(getattr(pdf, "extension", "") or ""),
                "size_bytes": getattr(pdf, "size_bytes", None),
                "href": _attachment_href(pdf),
            }
        )
    return rows


def home_sections(site, request=None) -> dict:
    """Kontekst trzech sekcji strony głównej witryny ``site`` – ``{% dj_home_sections as home %}``."""
    return {
        "latest_news": latest_news(site),
        "news_index": _first(NEWS_INDEX_TEMPLATE, site),
        "partners": partners_strip(site, request),
        "downloads": download_rows(site),
        "documents_index": _first(DOCUMENT_INDEX_TEMPLATE, site),
    }
