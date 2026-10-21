"""Adresy ramy panelu uczestnika – rozwijane na końcu ``apps/web/urls.py`` (przestrzeń ``web``).

Bramki flag tu nie ma – rozstrzyga widok (404), żeby ``reverse()`` działał zawsze tak samo (jak
``apps/notebooks/urls.py``). ``me/notebooks/`` nie koliduje z ``me/notebooks/<int:pk>/`` notatników.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("me/notebooks/", views.NotebooksHubView.as_view(), name="participant-notebooks"),
    path("me/latex/", views.LatexHubView.as_view(), name="participant-latex"),
]
