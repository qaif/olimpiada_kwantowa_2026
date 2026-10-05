"""Baner „nie możemy dostarczyć poczty na Twój adres” – ``{% email_undeliverable_banner %}`` w ``base.html``.

Znacznik, a nie procesor kontekstu – ten sam powód, co baner 2FA (``apps.staff_mfa.templatetags``):
liczy się tylko tam, gdzie stoi, i dla anonima nie robi nic. Dla zalogowanego czyta stan adresu
z cache'u (``services.banner_status``), więc zwykła strona nie kosztuje zapytania do bazy. Bez
odbicia oddaje pusty napis – strona jest bajt w bajt taka jak dotąd. Stoi w ``base.html`` poza
slotami motywu, więc motyw (IQO) nie może go zasłonić.
"""

from __future__ import annotations

from django import template
from django.template.loader import render_to_string

register = template.Library()

#: Ekrany, na których baner tylko by przeszkadzał: zmiana adresu (to jest odpowiedź na baner)
#: i jej potwierdzenie.
HIDDEN_ON = frozenset({"email-change", "email-change-confirm"})


@register.simple_tag(takes_context=True)
def email_undeliverable_banner(context) -> str:
    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    if user is None or not user.is_authenticated:
        return ""
    match = getattr(request, "resolver_match", None)
    if match is not None and match.url_name in HIDDEN_ON:
        return ""
    from apps.email_delivery.services import banner_status

    status = banner_status(user.email)
    if not status:
        return ""
    return render_to_string(
        "email_delivery/_banner.html",
        {"email": user.email, "since": status["since"], "next": request.get_full_path()},
        request=request,
    )
