"""Znaczniki szablonów dla danych na żywo (``{% load dj_live %}``).

- ``{% dj_unavailable %}`` – komunikat „dane chwilowo niedostępne” z odnośnikiem do tej samej
  ścieżki na domenie głównej (§ 8.3 „Degradacja”). Używają go sekcje żywe (DJ-01f), gdy API nie
  oddało danych ani z bufora, ani z kopii,
- ``result|dj_stale_label`` – dopisek „stan na HH:MM” przy danych z kopii zapasowej,
- ``"/sciezka/"|dj_main_url`` – adres w aplikacji głównej (``DJCMS_MAIN_PUBLIC_URL``); w szablonach
  stron ``{% dj_main_href "/sciezka/" %}`` – to samo pod adresem publicznym konkursu żądania,
- ``{% dj_live_data "stages" as stages %}`` – odpowiedź endpointu dla szablonu strony
  (``apps.live.data.LiveData``: ``available``, ``data``, ``stale_label``); to samo pobranie, co
  wtyczek tej odsłony (pamięć żądania),
- ``{% dj_archive_results as archive %}`` – edycja i odnośniki do wyników strony archiwum
  (``ArchiveMeta`` renderowanej treści),
- ``{% dj_home_sections as home %}`` – aktualności, pas partnerów i dokumenty do pobrania
  na stronę główną (``apps.live.homepage``),
- ``{% dj_slot_filled "intro" as has_intro %}`` – czy slot renderowanej treści ma wtyczki
  (odpowiednik ``{% if page.intro %}`` wokół opakowania ``<div class="lead">``),
- ``{% dj_workshop_materials_teaser %}`` – zapowiedź materiałów z warsztatów dla gościa (port
  ``{% workshop_materials_teaser %}``; stoi na stronie „Warsztaty”).
"""

from __future__ import annotations

from django import template

from .. import data, homepage
from ..chrome import main_url, request_page_path, stale_label

register = template.Library()


@register.inclusion_tag("dj/partials/_unavailable.html", takes_context=True)
def dj_unavailable(context, path: str | None = None) -> dict:
    request = context.get("request")
    if path is None:
        path = request_page_path(request)
    return {"main_url": main_url(path, request)}


@register.filter
def dj_stale_label(result) -> str:
    return stale_label(result)


@register.filter
def dj_main_url(path: str) -> str:
    return main_url(str(path or "/"))


@register.simple_tag(takes_context=True)
def dj_main_href(context, path: str = "/") -> str:
    return main_url(str(path or "/"), context.get("request"))


@register.simple_tag(takes_context=True)
def dj_live_data(context, endpoint: str) -> data.LiveData:
    return data.fetch(endpoint, context.get("request"))


@register.simple_tag(takes_context=True)
def dj_archive_results(context) -> data.ArchiveResults:
    return data.archive_results(data.current_content(context), context.get("request"))


@register.inclusion_tag("dj/live/_workshop_materials_teaser.html", takes_context=True)
def dj_workshop_materials_teaser(context) -> dict:
    """Zapowiedź materiałów – liczba i odnośnik do logowania na domenie głównej, nic więcej.

    Na ``dj.`` nikt nie jest zalogowany do aplikacji głównej, więc zawsze wariant dla gościa.
    Martwe API = brak zapowiedzi (to dodatek do strony, a nie dane zawodów – bez komunikatu).
    """
    live = data.fetch("workshops", context.get("request"))
    materials = live.data.get("materials")
    materials = materials if isinstance(materials, dict) else {}
    count = materials.get("count")
    return {
        "show": bool(materials.get("show")) and isinstance(count, int) and bool(materials.get("login_url")),
        "count": count,
        "login_url": materials.get("login_url", ""),
    }


@register.simple_tag(takes_context=True)
def dj_home_sections(context) -> dict:
    """Sekcje strony głównej **tej** witryny – aktualności, partnerzy i dokumenty jednego konkursu."""
    request = context.get("request")
    return homepage.home_sections(getattr(request, "site", None))


@register.simple_tag(takes_context=True)
def dj_slot_filled(context, slot: str) -> bool:
    """Czy slot ma treść – żeby opakowanie (``<div class="lead">``) nie stało puste.

    Szablony Wagtaila owijają pole w znacznik tylko wtedy, gdy pole jest wypełnione, a pusty
    ``.lead`` ma w arkuszu marginesy. W trybie edycji i struktury odpowiedź brzmi zawsze „tak”:
    redaktor musi widzieć pusty slot, żeby mieć gdzie dodać wtyczkę.
    """
    request = context.get("request")
    toolbar = getattr(request, "toolbar", None)
    if toolbar is not None and (toolbar.edit_mode_active or toolbar.structure_mode_active):
        return True
    content = data.current_content(context)
    if content is None or not hasattr(content, "get_placeholders"):
        return False
    return any(
        placeholder.slot == slot and placeholder.has_plugins(content.language)
        for placeholder in content.get_placeholders()
    )
