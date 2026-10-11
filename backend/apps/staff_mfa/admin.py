"""``/admin/``: polityka 2FA konkursu (operator) i podgląd okresów przejściowych.

Zapis polityki z ``/admin/`` podbija wersję (``policy.bump_version``) i zostawia wpis audytu –
tak samo jak ekran superkoordynatora, żeby żadna droga zmiany nie omijała śladu.
"""

from __future__ import annotations

from django.contrib import admin

from apps.core.models import audit

from . import policy
from .models import TwoFactorGrace, TwoFactorPolicy


@admin.register(TwoFactorPolicy)
class TwoFactorPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "competition",
        "mode",
        "roles",
        "grace_days",
        "allow_remember",
        "updated_at",
        "updated_by",
    )
    readonly_fields = ("updated_at", "updated_by")

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        policy.bump_version()
        audit(
            request.user,
            "2fa.policy_changed",
            obj,
            {"after": {"mode": obj.mode, "roles": sorted(obj.roles or []), "grace_days": obj.grace_days}},
            request,
        )


@admin.register(TwoFactorGrace)
class TwoFactorGraceAdmin(admin.ModelAdmin):
    """Do odczytu: okres przejściowy jest jednorazowy, a jego przesunięcie z panelu byłoby obejściem."""

    list_display = ("user", "required_since")
    search_fields = ("user__email",)
    readonly_fields = ("user", "required_since")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Skasowanie wiersza dałoby kontu świeży okres przejściowy (przegląd, L3) – czyli obejście
        # wymogu jednym kliknięciem w /admin/. Wiersz znika wyłącznie z kontem albo przy anonimizacji.
        return False
