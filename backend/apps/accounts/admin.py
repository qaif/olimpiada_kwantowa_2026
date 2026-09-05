"""Panel administracyjny kont.

Kod zaproszenia nie jest w bazie ani w adminie – widoczny jest wyłącznie skrót sha256.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import CommitteeMember, InvitationCode, Participant, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("public_code", "user", "school", "district", "birth_year", "publish_full_name")
    list_filter = ("district", "guardian_consent", "publish_full_name")
    search_fields = ("public_code", "user__email", "school")
    readonly_fields = ("public_code", "gdpr_consent_at")
    autocomplete_fields = ("user",)


@admin.register(CommitteeMember)
class CommitteeMemberAdmin(admin.ModelAdmin):
    list_display = ("user", "district", "district_verified", "status", "is_appeals_committee", "approved_at")
    list_filter = ("status", "is_appeals_committee", "district_verified", "district")
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "approved_at", "approved_by")
    autocomplete_fields = ("user",)


@admin.register(InvitationCode)
class InvitationCodeAdmin(admin.ModelAdmin):
    """Kodów nie da się podejrzeć ani wygenerować z admina – od tego jest ``manage.py create_invitation``."""

    list_display = (
        "code_hash_short",
        "created_by",
        "grants_status",
        "is_appeals",
        "district",
        "used_count",
        "max_uses",
        "expires_at",
    )
    list_filter = ("grants_status", "is_appeals", "district")
    search_fields = ("code_hash",)
    readonly_fields = ("code_hash", "created_by", "created_at", "used_count")

    @admin.display(description="sha256 kodu")
    def code_hash_short(self, obj: InvitationCode) -> str:
        return f"{obj.code_hash[:12]}…"

    def has_add_permission(self, request) -> bool:
        return False
