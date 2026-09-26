"""Znaczniki szablonów stron djcms (``{% load dj_pages %}``).

Rama (DJ-01d): ``dj_primary_nodes``. Typy stron redakcyjnych (DJ-01e) – dane liczone w
``apps.pages.content`` z wtyczek bieżącej treści:

- ``{% dj_capture as nazwa %}…{% end_dj_capture %}`` – wynik fragmentu szablonu (zwykle
  ``{% placeholder %}``) do zmiennej. Szablony Wagtaila owijają pole w znacznik tylko wtedy, gdy
  pole jest wypełnione (``{% if page.intro %}<div class="lead">…``) – tu warunek sprawdza, czy slot
  coś wyrenderował. W trybie edycji pusty slot też coś renderuje (znaczniki paska narzędzi), więc
  opakowanie stoi – i dobrze: redaktor musi widzieć, gdzie dodać pierwszą wtyczkę,
- ``{% dj_page as content %}`` – renderowana treść strony (wersja z paska narzędzi),
- ``{% dj_chapters minimum as chapters %}``, ``{% dj_first_pdf as pdf %}``,
  ``{% dj_news_items as news %}``, ``{% dj_document_cards as documents %}``,
  ``{% dj_archive_cards as editions %}``, ``{% dj_partner_groups as groups %}``,
  ``{% dj_faq_sections as sections %}``, ``{% dj_extension "documentmeta" as meta %}``.
"""

from __future__ import annotations

import os

from django import template
from django.templatetags.static import static

from apps.live import client

from .. import content as page_content
from .. import seo

register = template.Library()

#: Wersja wydania w stopce trybu ``primary`` – ta sama zmienna co ``APP_VERSION`` aplikacji głównej
#: (``backend/apps/web/context_processors.py``), tu wersja obrazu djcms.
APP_VERSION = os.environ.get("APP_VERSION", "dev")


# --- SEO i tryb (DJ-02 § 8) ------------------------------------------------------------------------
#
# ``{% dj_canonical as url %}`` – adres kanoniczny bieżącej strony (pusty poza stroną CMS albo bez
# adresu publicznego konkursu), ``{% dj_analytics_id as ga_id %}`` – identyfikator GA4 tej odsłony
# (pusty w ``preview``, dla personelu i bez identyfikatora; niepusty przełącza CSP na hosty GA),
# ``{% dj_seo as seo %}`` – ``og_image`` i ``default_description`` z ``chrome.seo`` z wartościami
# zapasowymi, ``{% dj_app_version %}``.


@register.simple_tag(takes_context=True)
def dj_canonical(context) -> str:
    request = context.get("request")
    return seo.canonical_url(request) if request is not None else ""


@register.simple_tag(takes_context=True)
def dj_analytics_id(context) -> str:
    return seo.analytics_id(context.get("request"), context.get("dj_chrome"))


@register.simple_tag(takes_context=True)
def dj_seo(context) -> dict:
    """``chrome.seo`` (API v2) z wartościami zapasowymi na wypadek martwego API.

    ``og_image`` przychodzi bezwzględny i przefiltrowany (``apps.live.chrome.safe_href``); zapasowy
    obraz to własna kopia ze statyków djcms pod originem konkursu (nie z nagłówka ``Host``).
    """
    chrome = context.get("dj_chrome")
    data = (getattr(chrome, "data", None) or {}).get("seo") or {}
    request = context.get("request")
    competition = getattr(request, "competition_site", None)
    origin = (getattr(competition, "public_origin", "") or "").rstrip("/")
    og_image = data.get("og_image") or (f"{origin}{static('img/og-image.png')}" if origin else "")
    description = data.get("default_description")
    return {
        "og_image": og_image,
        "default_description": description
        if isinstance(description, str) and description
        else seo.FALLBACK_DESCRIPTION,
    }


@register.simple_tag
def dj_app_version() -> str:
    return APP_VERSION


@register.filter
def dj_primary_nodes(nodes) -> list:
    """Węzły menu oznaczone „w przyklejonym pasku” (``MenuExtension.primary``, ``cms_menus.py``).

    Osobna lista zamiast warunku w pętli: pasek ``nav--primary`` ma się nie pojawić wcale, gdy
    nie ma w nim ani jednej pozycji – tak jak ``{% if cms_menu_primary %}`` w ``base.html`` backendu.
    """
    return [node for node in nodes or [] if node.attr.get("dj_primary")]


# --- przechwycenie fragmentu ---------------------------------------------------------------------


class CaptureNode(template.Node):
    """Renderuje fragment raz i zapisuje wynik w kontekście.

    Wynik ``NodeList.render`` to już wyrenderowany, autoescapowany HTML (oznaczony przez Django
    jako gotowy), więc ``{{ nazwa }}`` wypisuje go bez ponownego escape'owania – bez żadnego ``|safe``.
    ``child_nodelists`` mówi skanerowi placeholderów django CMS, gdzie szukać ``{% placeholder %}``.
    """

    child_nodelists = ("nodelist",)

    def __init__(self, nodelist, name: str):
        self.nodelist = nodelist
        self.name = name

    def render(self, context):
        context[self.name] = self.nodelist.render(context)
        return ""


@register.tag("dj_capture")
def dj_capture(parser, token):
    bits = token.split_contents()
    if len(bits) != 3 or bits[1] != "as":
        raise template.TemplateSyntaxError("Użycie: {% dj_capture as nazwa %}…{% end_dj_capture %}")
    nodelist = parser.parse(("end_dj_capture",))
    parser.delete_first_token()
    return CaptureNode(nodelist, bits[2])


@register.filter
def dj_filled(value) -> bool:
    """Czy przechwycony fragment ma treść (same białe znaki to pusto)."""
    return bool(str(value or "").strip())


# --- dane typów stron ----------------------------------------------------------------------------


@register.simple_tag(takes_context=True)
def dj_page(context):
    return page_content.current_content(context)


@register.simple_tag(takes_context=True)
def dj_extension(context, accessor: str):
    return page_content.extension(page_content.current_content(context), accessor)


@register.simple_tag(takes_context=True)
def dj_chapters(context, minimum: int = 1) -> list[dict]:
    return page_content.chapters(page_content.current_content(context), minimum=int(minimum))


@register.simple_tag(takes_context=True)
def dj_first_pdf(context):
    return page_content.first_pdf(page_content.current_content(context))


@register.simple_tag(takes_context=True)
def dj_news_items(context) -> list:
    return page_content.news_items(page_content.current_content(context))


@register.simple_tag(takes_context=True)
def dj_document_cards(context) -> list:
    return page_content.document_cards(page_content.current_content(context))


@register.simple_tag(takes_context=True)
def dj_archive_cards(context) -> list:
    """Karty edycji archiwum; nazwy edycji z ``GET editions`` (jedno pobranie na odsłonę)."""
    result = client.get("editions", request=context.get("request"))
    editions = (result.data or {}).get("editions") if result.data else None
    return page_content.archive_cards(page_content.current_content(context), editions)


@register.simple_tag(takes_context=True)
def dj_partner_groups(context) -> list[dict]:
    return page_content.partner_groups(page_content.current_content(context))


@register.simple_tag(takes_context=True)
def dj_faq_sections(context) -> list[dict]:
    return page_content.faq_sections(page_content.current_content(context))
