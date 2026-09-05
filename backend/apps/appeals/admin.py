"""Panel koordynatora dla reklamacji.

Reklamacje i decyzje powstają wyłącznie przez serwisy (okno, konflikt interesów, blokada wiersza,
aktualizacja ``FinalGrade`` i audyt), więc panel nie pozwala ich dodawać ręcznie – ręczny wpis
ominąłby całą procedurę i zostawiłby ocenę bez śladu w audycie.
"""

from django.contrib import admin

from .models import Appeal, AppealDecision


@admin.register(Appeal)
class AppealAdmin(admin.ModelAdmin):
    list_display = ("id", "submission", "filed_by", "status", "filed_at")
    list_filter = ("status", "submission__entry__stage__edition", "submission__entry__stage__kind")
    search_fields = ("submission__uuid", "filed_by__public_code")
    readonly_fields = ("submission", "filed_by", "filed_at", "argument")

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(AppealDecision)
class AppealDecisionAdmin(admin.ModelAdmin):
    list_display = ("id", "appeal", "new_score", "decided_by", "decided_at")
    list_filter = ("decided_at",)
    search_fields = ("appeal__submission__uuid", "appeal__filed_by__public_code")
    readonly_fields = ("appeal", "committee", "decided_by", "new_score", "justification", "decided_at")

    def has_add_permission(self, request) -> bool:
        return False
