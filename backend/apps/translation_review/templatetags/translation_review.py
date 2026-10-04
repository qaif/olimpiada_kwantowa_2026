"""Odnośnik „Zgłoś tłumaczenie” w stopce i drobne filtry ekranów przeglądu tłumaczeń."""

from __future__ import annotations

from django import template
from django.conf import settings
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from .. import catalogs, services

register = template.Library()


@register.simple_tag(takes_context=True)
def translation_report_link(context, css_class: str = "") -> str:
    """Odnośnik dla zalogowanego tłumacza, na stronie w języku innym niż źródłowy.

    Trzy tanie warunki przed jedynym kosztownym (``is_translator`` – cache, a na zimno jedno
    zapytanie): konkurs z jednym językiem interfejsu (Konkurs #1) i strona po polsku nie pytają
    o nic, więc stopka tych stron nie płaci za tę funkcję ani jednym zapytaniem.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return ""
    if not services.multilingual(getattr(request, "competition", None)):
        return ""
    if (get_language() or settings.LANGUAGE_CODE) == settings.LANGUAGE_CODE:
        return ""
    if not services.is_translator(user):
        return ""
    url = f"{reverse('web:translation-report')}?{urlencode({'page': request.path})}"
    return format_html('<a class="{}" href="{}">{}</a>', css_class, url, _("Zgłoś tłumaczenie"))


@register.filter
def language_label(code: str) -> str:
    return catalogs.language_label(code)
