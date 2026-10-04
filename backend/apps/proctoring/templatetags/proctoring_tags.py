"""Karta nadzoru na stronach etapu (pulpit ucznia, test) – ``{% proctoring_card stage %}``.

Znacznik szablonu, a nie klucz kontekstu widoku: pulpit ``/me/`` i strony testu należą do innych
aplikacji, a nadzór ma się w nich pojawić **jedną linijką** szablonu, bez zmian w ich widokach.
Konkurs bez flagi ``proctoring`` płaci za to odczyt pola konkursu – zero zapytań (budżet zapytań
``/me/`` Konkursu #1 bez zmian).
"""

from __future__ import annotations

from django import template

register = template.Library()


@register.inclusion_tag("web/proctoring/_stage_card.html", takes_context=True)
def proctoring_card(context, stage, compact=False):
    from apps.proctoring import services

    request = context.get("request")
    competition = getattr(request, "competition", None)
    if stage is None or request is None or not services.enabled(competition):
        return {"show": False}
    config = services.config_for(stage)
    if config is None:
        return {"show": False}
    from apps.accounts.services import participant_for

    participant = participant_for(request.user, competition)
    if participant is None or services.entry_for(participant, stage) is None:
        return {"show": False}
    session = services.session_for(stage, participant, create=False)
    return {
        "show": True,
        "stage": stage,
        "compact": compact,
        "ready": services.is_ready(session, config),
        "request": request,
    }
