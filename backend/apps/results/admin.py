"""Panel koordynatora dla publikacji wyników.

Publikacja powstaje wyłącznie przez ``services.publish_results`` (przeliczenie, kwalifikacja,
anonimizacja, audyt), więc panel jej nie tworzy ani nie edytuje: ręcznie wpisany snapshot
ominąłby anonimizację i mógłby ogłosić dane osobowe bez zgody. Zostaje podgląd i usunięcie
(wycofanie ogłoszenia).
"""

from django.contrib import admin

from .models import ResultsPublication


@admin.register(ResultsPublication)
class ResultsPublicationAdmin(admin.ModelAdmin):
    list_display = ("id", "stage", "anonymization", "published_at", "published_by")
    list_filter = ("anonymization", "stage__edition", "stage__kind")
    search_fields = ("stage__edition__year_label",)
    readonly_fields = (
        "stage",
        "published_at",
        "published_by",
        "anonymization",
        "snapshot",
        "entry_totals",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
