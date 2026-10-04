"""Adresy płatności (PAY-01) – rozwijane na końcu ``apps/web/urls.py`` (przestrzeń nazw ``web``).

Bramki flagi ``fees`` tu nie ma – rozstrzyga ją widok (404), żeby ``reverse()`` dawał ten sam adres
w każdym konkursie. Stałe ścieżki stoją przed wzorcami z parametrem.

Webhooki mają własny prefiks ``payments/webhooks/`` – **nie** ``/api/v1/payments/<slug>/``, bo tamten
adres należy do neutralnego stuba z wydania K (sekret per konkurs w bazie), a te do dostawców,
których sekrety są w środowisku instalacji.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator, payer, webhooks

urlpatterns = [
    # --- webhooki dostawców ---------------------------------------------------------------------
    path("payments/webhooks/stripe/", webhooks.StripeWebhookView.as_view(), name="payments-webhook-stripe"),
    path(
        "payments/webhooks/przelewy24/", webhooks.Przelewy24WebhookView.as_view(), name="payments-webhook-p24"
    ),
    path(
        "payments/webhooks/przelewy24/refund/",
        webhooks.Przelewy24RefundWebhookView.as_view(),
        name="payments-webhook-p24-refund",
    ),
    # --- płacący --------------------------------------------------------------------------------
    path("payments/orders/<int:pk>/", payer.OrderView.as_view(), name="payment-order"),
    path(
        "payments/orders/<int:pk>/pay/<slug:provider>/",
        payer.OrderPayView.as_view(),
        name="payment-order-pay",
    ),
    path("payments/orders/<int:pk>/cancel/", payer.OrderCancelView.as_view(), name="payment-order-cancel"),
    path("payments/documents/<int:pk>/", payer.DocumentView.as_view(), name="payment-document"),
    path("delegation/payments/", payer.DelegationPaymentsView.as_view(), name="delegation-payments"),
    path(
        "delegation/payments/billing/",
        payer.DelegationBillingView.as_view(),
        name="delegation-payments-billing",
    ),
    path(
        "delegation/payments/prepare/",
        payer.DelegationPrepareView.as_view(),
        name="delegation-payments-prepare",
    ),
    path("me/fees/pay/", payer.ParticipantPayView.as_view(), name="participant-fee-pay"),
    # --- koordynator ----------------------------------------------------------------------------
    path("coordinator/payments/", coordinator.DashboardView.as_view(), name="coordinator-payments"),
    path(
        "coordinator/payments/prices/", coordinator.PricesView.as_view(), name="coordinator-payments-prices"
    ),
    path(
        "coordinator/payments/settings/",
        coordinator.SettingsView.as_view(),
        name="coordinator-payments-settings",
    ),
    path(
        "coordinator/payments/export.csv",
        coordinator.ExportView.as_view(),
        name="coordinator-payments-export",
    ),
    path(
        "coordinator/payments/delegations/<int:pk>/",
        coordinator.DelegationView.as_view(),
        name="coordinator-payments-delegation",
    ),
    path(
        "coordinator/payments/delegations/<int:pk>/adjustments/",
        coordinator.AdjustmentAddView.as_view(),
        name="coordinator-payments-adjustment-add",
    ),
    path(
        "coordinator/payments/adjustments/<int:pk>/revoke/",
        coordinator.AdjustmentRevokeView.as_view(),
        name="coordinator-payments-adjustment-revoke",
    ),
    path(
        "coordinator/payments/orders/<int:pk>/",
        coordinator.OrderAdminView.as_view(),
        name="coordinator-payments-order",
    ),
    path(
        "coordinator/payments/orders/<int:pk>/mark-paid/",
        coordinator.OrderMarkPaidView.as_view(),
        name="coordinator-payments-order-paid",
    ),
    path(
        "coordinator/payments/orders/<int:pk>/cancel/",
        coordinator.OrderCancelAdminView.as_view(),
        name="coordinator-payments-order-cancel",
    ),
    path(
        "coordinator/payments/payments/<int:pk>/refund/",
        coordinator.RefundView.as_view(),
        name="coordinator-payments-refund",
    ),
    path(
        "coordinator/payments/payments/<int:pk>/proof/",
        coordinator.ProofView.as_view(),
        name="coordinator-payments-proof",
    ),
]
