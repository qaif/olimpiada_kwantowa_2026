"""Panel koordynatora dla oceniania.

Recenzje i oceny uzgodnione powstają wyłącznie przez serwisy (walidacja skali, konsensus,
blokada wiersza), więc panel nie pozwala ich dodawać. Korekta oceny uzgodnionej jest możliwa
tylko na poziomie uzasadnienia i punktów – każda taka zmiana należy do procedury odwoławczej.
"""

from django.contrib import admin

from .models import FinalGrade, Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("id", "submission", "reviewer", "round", "status", "score", "submitted_at")
    list_filter = ("status", "round", "submission__entry__stage__edition", "submission__entry__stage__kind")
    search_fields = ("submission__uuid", "reviewer__user__email")
    readonly_fields = ("submission", "reviewer", "round", "assigned_at", "submitted_at")

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(FinalGrade)
class FinalGradeAdmin(admin.ModelAdmin):
    list_display = ("id", "submission", "score", "method", "decided_by", "decided_at")
    list_filter = ("method", "submission__entry__stage__edition", "submission__entry__stage__kind")
    search_fields = ("submission__uuid", "submission__entry__participant__public_code")
    readonly_fields = ("submission", "method", "decided_by", "decided_at")

    def has_add_permission(self, request) -> bool:
        return False
