"""Adres ekranu reguły zaświadczeń z warsztatów (WS-CERT-02).

Osobny moduł rozwijany na **końcu** ``urlpatterns`` w ``apps/web/urls.py`` – ten sam wzorzec, co
``urls_workshop_materials``: kolejność wzorców jest umową, więc dopisujemy, a nie wstawiamy w środek.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_workshop_certificates as views

urlpatterns = [
    path(
        "coordinator/workshops/auto-certificates/",
        views.WorkshopAutoCertificatesView.as_view(),
        name="coordinator-workshop-auto-certificates",
    ),
]
