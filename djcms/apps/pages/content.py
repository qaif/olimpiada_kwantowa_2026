"""Dane typów stron redakcyjnych – port metod stron Wagtaila (``backend/apps/cms/models.py``).

W Wagtailu spis rozdziałów, zajawka dokumentu, grupy partnerów czy sekcje FAQ to metody modelu
strony liczone z jej pól. Na ``dj.`` strona nie ma pól – ma sloty z wtyczkami – więc te same reguły
liczymy tutaj, z wtyczek w slotach bieżącej treści (``PageContent``), a szablony dostają je przez
znaczniki ``dj_pages``. Każda funkcja wskazuje w docstringu swój oryginał; zmieniając regułę po
stronie Wagtaila, zmień ją też tu (ryzyko 3 speca).

Wersje: renderowana treść to ta, którą wskazał pasek narzędzi – w trybie edycji wersja robocza,
w podglądzie oglądana wersja, publicznie opublikowana (``current_content``). Listy **dzieci**
(aktualności, dokumenty, edycje archiwum) biorą zawsze wersje opublikowane – jak ``live()``
w Wagtailu: wersja robocza aktualności nie może wyciec na listę publiczną.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from html import unescape

from cms.models import CMSPlugin, PageContent
from cms.utils.plugins import downcast_plugins
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.utils.html import strip_tags
from django.utils.text import Truncator

LANGUAGE = "pl"

#: Szablony typów stron (``CMS_TEMPLATES``) – odpowiednik klas stron Wagtaila.
NEWS_TEMPLATE = "dj/pages/news.html"
DOCUMENT_TEMPLATE = "dj/pages/document.html"
ARCHIVE_EDITION_TEMPLATE = "dj/pages/archive_edition.html"

#: ``ContentPage.MIN_CHAPTERS_FOR_TOC`` – przy mniej niż trzech śródtytułach spis byłby dłuższy
#: od tego, co spisuje. Dokument (``DocumentPage.chapters``) pokazuje spis od pierwszego rozdziału.
CONTENT_MIN_CHAPTERS = 3
#: ``DocumentPage.SUMMARY_WORDS`` – długość zajawki na karcie spisu ``/dokumenty/``.
SUMMARY_WORDS = 28


def current_content(context) -> PageContent | None:
    """Renderowana właśnie treść strony: z paska narzędzi, a bez niego – z kontekstu ``render_page``."""
    request = context.get("request")
    toolbar = getattr(request, "toolbar", None)
    obj = toolbar.get_object() if toolbar is not None else None
    if isinstance(obj, PageContent):
        return obj
    content = context.get("current_pagecontent")
    return content if isinstance(content, PageContent) else None


def extension(content, accessor: str):
    """Rozszerzenie treści (``newsmeta``, ``documentmeta``, ``archivemeta``) albo ``None``."""
    if content is None:
        return None
    try:
        return getattr(content, accessor)
    except AttributeError, ObjectDoesNotExist:
        return None


def slot_plugins(contents, slot: str, plugin_types: str | tuple[str, ...]) -> dict[int, list]:
    """Wtyczki najwyższego poziomu danego typu w slocie – per ``PageContent.pk``, w kolejności slotu.

    Jedno zapytanie na typ modelu wtyczki niezależnie od liczby stron (``downcast_plugins``) – spis
    dwudziestu dokumentów kosztuje tyle samo, co jeden, jak ``prefetch_related`` w Wagtailu.
    """
    ids = [content.pk for content in contents if content is not None]
    if not ids:
        return {}
    types = (plugin_types,) if isinstance(plugin_types, str) else tuple(plugin_types)
    queryset = CMSPlugin.objects.filter(
        placeholder__content_type=ContentType.objects.get_for_model(PageContent),
        placeholder__object_id__in=ids,
        placeholder__slot=slot,
        plugin_type__in=types,
        language=LANGUAGE,
        parent__isnull=True,
    ).select_related("placeholder")
    found: dict[int, list] = {}
    for plugin in sorted(downcast_plugins(list(queryset)), key=lambda item: item.position):
        found.setdefault(plugin.placeholder.object_id, []).append(plugin)
    return found


def plugins_of(content, slot: str, plugin_types: str | tuple[str, ...]) -> list:
    if content is None:
        return []
    return slot_plugins([content], slot, plugin_types).get(content.pk, [])


# --- spis treści ----------------------------------------------------------------------------------


def chapters(content, minimum: int = 1) -> list[dict]:
    """``body_chapters``: śródtytuły poziomu 2 ze slotu ``body`` oznaczone „pokaż w spisie”.

    Liczone z wtyczek, a nie z wyrenderowanego HTML-a – jak w oryginale. ``minimum`` to próg,
    od którego spis w ogóle się pokazuje (``CONTENT_MIN_CHAPTERS`` na stronie treści).
    """
    found = [
        {"anchor": plugin.anchor, "text": plugin.text}
        for plugin in plugins_of(content, "body", "HeadingPlugin")
        if plugin.level == "2" and plugin.in_toc
    ]
    return found if len(found) >= minimum else []


# --- załączniki -----------------------------------------------------------------------------------


def attachments(content) -> list:
    """Pliki karty „Do pobrania” z adresem (plik bez adresu nie ma czego pobrać)."""
    return [item for item in plugins_of(content, "attachments", "AttachmentPlugin") if item.href]


def first_pdf(content):
    """``pdf_attachment`` z ``DocumentPage.get_context``: pierwszy PDF – przycisk nad treścią."""
    return next((item for item in attachments(content) if item.is_pdf), None)


# --- zajawka dokumentu ----------------------------------------------------------------------------


def _plain_text(html: str) -> str:
    # Spacja przed każdym znacznikiem – ``</p><p>`` bez niej sklejałoby ostatnie słowo akapitu
    # z pierwszym słowem następnego (uzasadnienie w ``DocumentPage.summary``).
    return " ".join(unescape(strip_tags((html or "").replace("<", " <"))).split())


def summary(content, intro_texts: list | None = None) -> str:
    """``DocumentPage.summary``: pierwsze 28 słów wprowadzenia jako czysty tekst, zapas – opis SEO."""
    texts = intro_texts if intro_texts is not None else plugins_of(content, "intro", "TextPlugin")
    text = " ".join(filter(None, (_plain_text(getattr(item, "body", "")) for item in texts)))
    # djangocms-text dzieli długie słowa miękkimi łącznikami – w zajawce są szumem.
    text = text.replace("­", "")
    if not text:
        return (getattr(content, "meta_description", "") or "").strip()
    return Truncator(text).words(SUMMARY_WORDS, truncate="…")


# --- dzieci stron-spisów --------------------------------------------------------------------------


def published_children(content, template: str) -> list[PageContent]:
    """Opublikowane treści dzieci strony o danym szablonie, w kolejności drzewa.

    ``PageContent.objects`` pod djangocms-versioning oddaje wyłącznie wersje opublikowane – to jest
    odpowiednik ``live()``. Kolejność drzewa = kolejność, którą redaktor ustawia przeciąganiem.
    """
    if content is None:
        return []
    return list(
        PageContent.objects.filter(
            page__in=content.page.get_child_pages(), language=LANGUAGE, template=template
        )
        .select_related("page")
        .order_by("page__path")
    )


def page_url(content) -> str:
    return content.page.get_absolute_url(LANGUAGE)


@dataclass
class NewsItem:
    title: str
    url: str
    date: date | None
    lead: str
    page_id: int


def news_items(content) -> list[NewsItem]:
    """``NewsIndexPage.get_context``: aktualności, data malejąco, potem strona malejąco (``-pk``)."""
    items = []
    for child in published_children(content, NEWS_TEMPLATE):
        meta = extension(child, "newsmeta")
        items.append(
            NewsItem(
                title=child.title,
                url=page_url(child),
                date=getattr(meta, "date", None),
                lead=getattr(meta, "lead", "") or "",
                page_id=child.page_id,
            )
        )
    # Bez metryki (strona dodana z pominięciem paska) – na końcu listy, a nie wywrócona strona.
    items.sort(key=lambda item: (item.date is not None, item.date or date.min, item.page_id), reverse=True)
    return items


@dataclass
class DocumentCard:
    title: str
    url: str
    meta: object | None
    summary: str
    attachments: list = field(default_factory=list)


def document_cards(content) -> list[DocumentCard]:
    """``DocumentIndexPage.get_context`` + karta z ``document_index_page.html``: metryka, zajawka, pliki."""
    children = published_children(content, DOCUMENT_TEMPLATE)
    files = slot_plugins(children, "attachments", "AttachmentPlugin")
    intros = slot_plugins(children, "intro", "TextPlugin")
    return [
        DocumentCard(
            title=child.title,
            url=page_url(child),
            meta=extension(child, "documentmeta"),
            summary=summary(child, intros.get(child.pk, [])),
            attachments=[item for item in files.get(child.pk, []) if item.href],
        )
        for child in children
    ]


@dataclass
class ArchiveCard:
    title: str
    url: str
    edition: dict | None


def archive_cards(content, editions: list | None) -> list[ArchiveCard]:
    """``ArchiveIndexPage.get_context``: edycje archiwum; nazwa edycji z listy API (``GET editions``).

    Kolejność – drzewa (importer odwraca kolejność ``-pk`` Wagtaila przy zakładaniu stron, § 6.1).
    Bez API karta traci tylko nadtytuł z nazwą edycji – tytuł i odnośnik są treścią ``dj.``.
    """
    by_id = {
        item["id"]: item
        for item in editions or []
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }
    cards = []
    for child in published_children(content, ARCHIVE_EDITION_TEMPLATE):
        meta = extension(child, "archivemeta")
        cards.append(ArchiveCard(child.title, page_url(child), by_id.get(getattr(meta, "edition_id", None))))
    return cards


# --- grupy: partnerzy, FAQ ------------------------------------------------------------------------


def partner_groups(content) -> list[dict]:
    """``PartnersPage.groups``: partnerzy w grupach po poziomie, w kolejności ``PARTNER_LEVELS``.

    Poziom spoza listy (wpis sprzed zmiany słownika) trafia do własnej grupy na końcu, zamiast
    zniknąć ze strony. Grupy puste nie wchodzą do wyniku.
    """
    from apps.blocks.models import PARTNER_LEVELS

    labels = dict(PARTNER_LEVELS)
    buckets: dict[str, list] = {key: [] for key, _label in PARTNER_LEVELS}
    for plugin in plugins_of(content, "partners", "PartnerPlugin"):
        buckets.setdefault(plugin.level, []).append(plugin)
    return [
        {"level": key, "label": labels.get(key, key), "partners": entries}
        for key, entries in buckets.items()
        if entries
    ]


def faq_sections(content) -> list[dict]:
    """``FAQPage.sections``: pytania w sekcjach, w kolejności **pierwszego wystąpienia** sekcji."""
    groups: dict[str, dict] = {}
    for plugin in plugins_of(content, "faq", "FAQEntryPlugin"):
        groups.setdefault(plugin.section, {"section": plugin.section, "entries": []})["entries"].append(
            plugin
        )
    return list(groups.values())
