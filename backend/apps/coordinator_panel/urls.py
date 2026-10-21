"""Adresy panelu koordynatora (UX-PANEL-04) – montowane w ``apps/web/urls.py`` jednym wierszem.

Nazwa z przedrostkiem ``coordinator-``: ten sam przedrostek, co reszta panelu, więc test bramki nadzoru
(``apps/proctoring/tests/test_gate.py``) traktuje adres jako ekran personelu.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("coordinator/pins/", views.PinToggleView.as_view(), name="coordinator-pin-toggle"),
]
