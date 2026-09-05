"""Panel koordynatora dla rozwiązań. Wszystko tylko do odczytu.

Zgłoszenia i pliki powstają wyłącznie przez API (walidacja treści, sha256, skan), więc panel
nie pozwala ich tworzyć ani edytować – inaczej dałoby się obejść walidację uploadu.
"""

from django.contrib import admin

from .models import Submission, SubmissionFile


class SubmissionFileInline(admin.TabularInline):
    model = SubmissionFile
    extra = 0
    can_delete = False
    fields = ("original_name", "mime", "size_bytes", "sha256", "object_key", "av_status", "scanned_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "entry", "problem", "version", "status", "is_late", "submitted_at")
    list_filter = ("status", "is_late", "problem__stage__edition", "problem__stage__kind")
    search_fields = ("uuid", "entry__participant__public_code", "files__sha256")
    date_hierarchy = "submitted_at"
    inlines = (SubmissionFileInline,)
    readonly_fields = ("uuid", "entry", "problem", "version", "submitted_at", "is_late", "status")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(SubmissionFile)
class SubmissionFileAdmin(admin.ModelAdmin):
    list_display = ("id", "submission", "original_name", "size_bytes", "av_status", "scanned_at")
    list_filter = ("av_status",)
    search_fields = ("sha256", "object_key", "original_name")
    readonly_fields = tuple(field.name for field in SubmissionFile._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
