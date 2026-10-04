"""Adresy SEC-01 – doklejane do ``apps/web/urls.py`` (przestrzeń nazw ``web:``), jak logistyka finału."""

from django.urls import path

from . import views

urlpatterns = [
    path(
        "account/2fa/codes/regenerate/",
        views.RegenerateCodesView.as_view(),
        name="twofactor-regenerate",
    ),
    path(
        "account/2fa/forget-devices/",
        views.ForgetDevicesView.as_view(),
        name="twofactor-forget-devices",
    ),
    path("coordinator/security/2fa/", views.PolicyView.as_view(), name="coordinator-two-factor"),
]
