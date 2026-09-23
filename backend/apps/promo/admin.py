"""Plakaty w panelu operatora platformy (``/admin/``) – do diagnozy, nie do redakcji.

Redakcja plakatów mieszka w panelu koordynatora (``/coordinator/posters/``): tam jest walidacja pliku
po treści, miniatura, unieważnianie pamięci stopki i wpis audytowy przy każdej zmianie. Formularz
administracyjny obchodziłby wszystkie cztery, więc plik i stan publikacji są tu tylko do odczytu,
tak samo jak treść wpisów forum w ``apps.forum.admin``.
"""

from django.contrib import admin

from .models import PromoDownload, PromoMaterial


@admin.register(PromoMaterial)
class PromoMaterialAdmin(admin.ModelAdmin):
    list_display = ("title", "competition", "file_format", "is_published", "archived_at", "position")
    list_filter = ("competition", "is_published", "file_format")
    search_fields = ("title", "description")
    raw_id_fields = ("created_by",)
    readonly_fields = (
        "file",
        "file_format",
        "file_size",
        "preview",
        "preview_is_generated",
        "is_published",
        "archived_at",
        "created_at",
        "updated_at",
    )


@admin.register(PromoDownload)
class PromoDownloadAdmin(admin.ModelAdmin):
    """Zdarzenia pobrań – wyłącznie do odczytu. Statystyka nie jest czymś, co się poprawia ręcznie."""

    list_display = ("material", "competition", "downloaded_at")
    list_filter = ("competition",)
    date_hierarchy = "downloaded_at"
    raw_id_fields = ("material",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
