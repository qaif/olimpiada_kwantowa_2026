"""``wagtailcore_tags`` dla silnika motywu: ``{% pageurl %}`` przyjmuje też :class:`PageProxy`.

Szablon paczki nie dostaje obiektów stron (``apps.themes.safe_context``), a ``{% pageurl item %}``
w Wagtailu wymaga ``Page``. Ta biblioteka zastępuje ``wagtailcore_tags`` **wyłącznie** w silniku
motywu: dla stron prawdziwych (fragmenty aplikacji dołączane przez slot) woła oryginał, dla
pośrednika oddaje policzony wcześniej adres. Pozostałe znaczniki i filtry są te same.
"""

from __future__ import annotations

from django import template
from wagtail.templatetags import wagtailcore_tags as original

from .safe_context import PageProxy

register = template.Library()
register.tags.update(original.register.tags)
register.filters.update(original.register.filters)


@register.simple_tag(takes_context=True)
def pageurl(context, page, fallback=None):
    if isinstance(page, PageProxy):
        return page.url
    if page is not None and not hasattr(page, "get_url"):
        return ""
    return original.pageurl(context, page, fallback)


@register.simple_tag(takes_context=True)
def fullpageurl(context, page, fallback=None):
    if isinstance(page, PageProxy):
        return page.url
    if page is not None and not hasattr(page, "get_url"):
        return ""
    return original.fullpageurl(context, page, fallback)


@register.simple_tag(takes_context=True)
def slugurl(context, slug):
    # Szablon paczki ma w ``request`` słownik, a nie obiekt żądania – adres liczymy od żądania
    # prawdziwego, schowanego poza zasięgiem zmiennych szablonu.
    full = context.get("_full_context")
    real = full if full is not None else context
    return original.slugurl(template.Context({"request": real.get("request")}), slug)
