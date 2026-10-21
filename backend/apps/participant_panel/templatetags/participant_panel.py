"""Znaczniki ramy panelu uczestnika: menu (``participant_nav``) i kafle pulpitu (``participant_start_tiles``).

Znaczniki, a nie dopisywanie kontekstu w każdym widoku ``apps.web``: ramę dostaje kilkanaście ekranów
(pulpit, kalendarz, archiwum, Wiadomości, profil…), a każdy z nich ma już swój widok i swoje testy.
Szablon dziedziczy ``participant_panel/base.html`` i ma menu – bez zmiany ani jednego widoku.
"""

from __future__ import annotations

from django import template

from ..nav import latex_available, navigation, notebooks_available

register = template.Library()


@register.inclusion_tag("participant_panel/_nav.html", takes_context=True)
def participant_nav(context):
    data = navigation(context)
    data["request"] = context.get("request")
    return data


def _next_workshop(competition):
    """Najbliższy warsztat z harmonogramu strony „Warsztaty” – temat, data, godzina; bez linku do spotkania.

    Link do spotkania (Meet) świadomie nie wychodzi poza ogłoszenia organizatora: adres, pod którym
    wchodzi się bez logowania, jest de facto przepustką (ta sama zasada, co kalendarz uczestnika).
    """
    from apps.cms.workshops import upcoming_workshops, workshops_page

    rows = upcoming_workshops(workshops_page(competition), limit=1)
    return rows[0] if rows else None


@register.inclusion_tag("participant_panel/_tiles.html", takes_context=True)
def participant_start_tiles(context):
    """Kafle „Start” na pulpicie (D12). Zapytania: strona „Warsztaty” (jedno); licznik powiadomień
    jest już policzony przez menu tej samej strony (pamięć na żądaniu)."""
    from apps.notifications.services import unread_count

    request = context.get("request")
    competition = getattr(request, "competition", None)
    now_panel = context.get("now_panel")
    chat_visible = bool(context.get("chat_visible"))
    materials = bool(competition is not None and competition.has_feature("workshop_materials")) and bool(
        context.get("workshop_materials_available")
    )
    return {
        "request": request,
        "deadline": getattr(now_panel, "deadline", None),
        "stage_name": getattr(now_panel, "stage_name", ""),
        "state_label": getattr(now_panel, "state_label", ""),
        "state_tone": getattr(now_panel, "state_tone", ""),
        "student_status": context.get("student_status"),
        "workshop": _next_workshop(competition) if competition is not None else None,
        "workshop_materials": materials,
        "chat_visible": chat_visible,
        "chat_unread": int(context.get("chat_unread") or 0) if chat_visible else 0,
        "notifications_unread": unread_count(request) if request is not None else 0,
        "notebooks": notebooks_available(competition),
        "latex": latex_available(competition),
    }
