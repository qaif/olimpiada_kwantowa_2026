"""Adresy MAIL-02 – doklejane do ``apps/web/urls.py`` (przestrzeń nazw ``web:``), jak SEC-01."""

from django.urls import path

from . import views

urlpatterns = [
    path(
        "account/email/deliverable/",
        views.ConfirmAddressView.as_view(),
        name="email-undeliverable-confirm",
    ),
    path(
        "coordinator/undeliverable-emails/",
        views.UndeliverableListView.as_view(),
        name="coordinator-undeliverable-emails",
    ),
    path(
        "coordinator/undeliverable-emails/<int:pk>/clear/",
        views.UndeliverableClearView.as_view(),
        name="coordinator-undeliverable-email-clear",
    ),
]
