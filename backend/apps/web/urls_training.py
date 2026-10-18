"""Adresy bloku „Zapis do treningu” na ekranie etapu (REG-UX-01 § 3.3) – dołączane w ``apps.web.urls``."""

from django.urls import path

from apps.web.views import coordinator_training

urlpatterns = [
    path(
        "coordinator/stages/<int:stage_id>/training/auto/",
        coordinator_training.StageAutoRegistrationView.as_view(),
        name="coordinator-stage-training-auto",
    ),
    path(
        "coordinator/stages/<int:stage_id>/training/register-all/",
        coordinator_training.StageRegisterAllView.as_view(),
        name="coordinator-stage-training-register-all",
    ),
]
