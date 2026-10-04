"""Znaczniki okien czasowych: godzina w wybranej strefie i podpis strefy z przesunięciem UTC."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from django import template
from django.utils import timezone
from django.utils.formats import date_format

from apps.time_windows.zones import utc_offset_label

register = template.Library()

#: Format godziny w tabelach okien – data z dniem tygodnia, bo okna sąsiednich krajów wypadają
#: w różne dni kalendarza („pon. 12 lip., 02:00” w Tokio to „ndz. 11 lip., 19:00” w Warszawie).
WINDOW_FORMAT = "D j M Y, H:i"


@register.filter
def in_zone(value, tz_name: str = "") -> str:
    """``{{ moment|in_zone:"Asia/Tokyo" }}`` – godzina w podanej strefie (pusta = aktywna)."""
    if value in (None, ""):
        return ""
    zone = ZoneInfo(tz_name) if tz_name else timezone.get_current_timezone()
    return date_format(value.astimezone(zone), WINDOW_FORMAT)


@register.simple_tag
def zone_label(moment=None, tz_name: str = "") -> str:
    """„Asia/Tokyo (UTC+09:00)” – strefa (domyślnie aktywna) z przesunięciem w chwili ``moment``."""
    name = tz_name or timezone.get_current_timezone_name()
    return f"{name.replace('_', ' ')} ({utc_offset_label(moment or timezone.now(), name)})"


@register.filter
def minutes_as_hours(value) -> str:
    """300 → „5 h”, 330 → „5 h 30 min” – czas pracy okna w tabelach."""
    try:
        total = int(value)
    except TypeError, ValueError:
        return ""
    hours, minutes = divmod(total, 60)
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"
