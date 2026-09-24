"""Adresy materiałów z warsztatów – widok dla zalogowanych i ekran koordynatora.

Osobny moduł, rozwijany na **końcu** ``urlpatterns`` w ``apps/web/urls.py`` – ten sam wzorzec, co
ekrany wydań E–K: kolejność wzorców jest umową, więc dopisujemy, a nie wstawiamy w środek.
Bramki przełącznika ``workshop_materials`` tu nie ma – rozstrzyga ją widok (404 przy wyłączonym),
żeby ``reverse()`` zawsze dawał adres.

``/warsztaty/materialy/…`` stoi w urlconfie aplikacji, czyli **przed** drzewem stron Wagtaila
(``config/urls.py``) – strona „Warsztaty” zostaje treścią ``/cms/``, a jej podstrona „materialy”
należy do aplikacji. Ścieżki nie ma na allow-liście pamięci stron publicznych
(``apps.web.page_cache.ALLOWED_PATHS`` ma dokładnie ``/warsztaty/``, bez prefiksu).
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_workshop_materials as coordinator
from .views import workshop_materials as viewer

urlpatterns = [
    # --- dla zalogowanych ---------------------------------------------------------------------
    path("warsztaty/materialy/", viewer.WorkshopMaterialsView.as_view(), name="workshop-materials"),
    path(
        "warsztaty/materialy/<int:pk>/", viewer.WorkshopMaterialDetailView.as_view(), name="workshop-material"
    ),
    path(
        "warsztaty/materialy/<int:pk>/pobierz/",
        viewer.WorkshopMaterialOpenView.as_view(),
        name="workshop-material-open",
    ),
    # --- panel koordynatora -------------------------------------------------------------------
    path(
        "coordinator/workshops/materials/",
        coordinator.WorkshopMaterialsView.as_view(),
        name="coordinator-workshop-materials",
    ),
    path(
        "coordinator/workshops/materials/new/",
        coordinator.WorkshopMaterialFormView.as_view(),
        name="coordinator-workshop-material-new",
    ),
    path(
        "coordinator/workshops/materials/<int:pk>/edit/",
        coordinator.WorkshopMaterialFormView.as_view(),
        name="coordinator-workshop-material-edit",
    ),
    path(
        "coordinator/workshops/materials/<int:pk>/preview/",
        coordinator.CoordinatorMaterialPreviewView.as_view(),
        name="coordinator-workshop-material-preview",
    ),
    path(
        "coordinator/workshops/materials/upload/start/",
        coordinator.UploadStartView.as_view(),
        name="coordinator-workshop-material-upload-start",
    ),
    path(
        "coordinator/workshops/materials/<int:pk>/upload/sign/",
        coordinator.UploadSignView.as_view(),
        name="coordinator-workshop-material-upload-sign",
    ),
    path(
        "coordinator/workshops/materials/<int:pk>/upload/complete/",
        coordinator.UploadCompleteView.as_view(),
        name="coordinator-workshop-material-upload-complete",
    ),
    path(
        "coordinator/workshops/materials/<int:pk>/upload/abort/",
        coordinator.UploadAbortView.as_view(),
        name="coordinator-workshop-material-upload-abort",
    ),
]
