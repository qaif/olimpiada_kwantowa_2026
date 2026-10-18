"""Znaczniki kalendarza publicznego (CAL-01): karta „Dodaj do kalendarza” i link jednego warsztatu.

Oba wyniki trafiają do HTML-a, który leży w pamięci stron publicznych (``/warsztaty/``), więc nie ma
w nich niczego zależnego od osoby – wyłącznie adresy zbudowane z hosta żądania i z harmonogramu.
"""

from __future__ import annotations

from django import template
from django.urls import reverse

from apps.calendar_feed.feed import workshop_event, workshops_url
from apps.calendar_feed.ics import google_event_url, google_subscribe_url, webcal_url, zone_for
from apps.calendar_feed.views import base_url
from apps.cms.workshops import workshop_key

register = template.Library()


@register.inclusion_tag("calendar_feed/_subscribe.html", takes_context=True)
def calendar_subscribe(context):
    """Karta z trzema przyciskami: Google (subskrypcja), Outlook/Apple (``webcal://``) i plik.

    Bez konkursu w żądaniu karta znika – kanał odpowiada wtedy 404 i przyciski nie miałyby dokąd
    prowadzić (podgląd strony w panelu redakcyjnym, renderowanie poza żądaniem).
    """
    request = context.get("request")
    if request is None or getattr(request, "competition", None) is None:
        return {"show": False}
    feed_url = request.build_absolute_uri(reverse("web:calendar-feed"))
    return {
        "show": True,
        "feed_url": feed_url,
        "webcal_url": webcal_url(feed_url),
        "google_url": google_subscribe_url(feed_url),
    }


@register.simple_tag(takes_context=True)
def workshop_google_url(context, row) -> str:
    """Szablon jednego warsztatu w Google Calendar – ten sam opis i godziny, co w kanale ``.ics``.

    Pusty napis dla wiersza bez daty maszynowej (``date_value``): takiego warsztatu nie ma też
    w kanale, bo nie da się go postawić w kalendarzu.
    """
    request = context.get("request")
    competition = getattr(request, "competition", None)
    if competition is None or not row.get("date_value"):
        return ""
    topic = row.get("topic", "")
    entry = {
        "topic": topic,
        "date_value": row["date_value"],
        "time": row.get("time", "") or "",
        "lecturer": row.get("lecturer", "") or "",
        "key": workshop_key(topic, row["date_value"]),
    }
    event = workshop_event(entry, competition=competition, page_url=workshops_url(base_url(request)))
    return google_event_url(event, zone_for(competition.time_zone))
