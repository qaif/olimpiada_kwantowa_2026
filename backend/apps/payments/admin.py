"""Podgląd płatności w ``/admin/`` dla operatora – **tylko do odczytu**.

Każda zmiana stanu pieniędzy idzie przez serwis (audyt, numeracja, listy); edycja wiersza w panelu
administracyjnym ominęłaby wszystkie trzy, więc panel pokazuje, ale nie zapisuje.
"""

from __future__ import annotations

from django.contrib import admin

from .models import BillingDocument, Order, Payment, ProviderEvent, Refund


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(ReadOnlyAdmin):
    list_display = ("reference", "competition", "total", "currency", "status", "created_at", "paid_at")
    list_filter = ("status", "currency", "competition")
    search_fields = ("reference",)


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("uuid", "order", "provider", "status", "amount", "currency", "created_at", "succeeded_at")
    list_filter = ("provider", "status")
    search_fields = ("provider_ref", "provider_payment_id", "order__reference")


@admin.register(Refund)
class RefundAdmin(ReadOnlyAdmin):
    list_display = ("uuid", "payment", "amount", "status", "created_at", "completed_at")
    list_filter = ("status",)


@admin.register(ProviderEvent)
class ProviderEventAdmin(ReadOnlyAdmin):
    list_display = ("provider", "event_type", "outcome", "received_at", "payment")
    list_filter = ("provider", "outcome")
    search_fields = ("event_id",)


@admin.register(BillingDocument)
class BillingDocumentAdmin(ReadOnlyAdmin):
    list_display = ("number", "kind", "order", "issued_at", "language")
    list_filter = ("kind", "competition")
    search_fields = ("number",)
