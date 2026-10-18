"""Adres publicznego kalendarza (CAL-01) – rozwijany w ``apps/web/urls.py`` (przestrzeń ``web:``).

``/kalendarz.ics``, a nie ``?format=ics`` przy ``/warsztaty/``: kalendarze subskrybują adres, a część
z nich rozpoznaje plik po rozszerzeniu (ten sam powód, co ``me/calendar.ics``). Wzorzec musi stać przed
catch-allem Wagtaila – ``apps.web.urls`` jest montowane wcześniej w ``config/urls.py``.
"""

from __future__ import annotations

from django.urls import path

from .views import PublicCalendarFeedView

urlpatterns = [
    path("kalendarz.ics", PublicCalendarFeedView.as_view(), name="calendar-feed"),
]
