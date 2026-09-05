"""Panel administracyjny rdzenia: audyt wyłącznie do odczytu.

Wpisu audytowego nie da się z panelu dodać, zmienić ani usunąć – ślad, który da się edytować,
nie jest śladem audytowym.
"""

from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("at", "actor", "action", "target_type", "target_id", "ip")
    list_filter = ("action", "target_type")
    search_fields = ("action", "target_type", "target_id", "actor__email")
    date_hierarchy = "at"
    readonly_fields = tuple(field.name for field in AuditLog._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
