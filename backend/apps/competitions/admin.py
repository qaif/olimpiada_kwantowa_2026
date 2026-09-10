"""Panel koordynatora dla domeny zawodów.

Skala punktacji, próg kwalifikacji i zadania są inline pod etapem – parametryzacja etapu
odbywa się w jednym formularzu (macierz uprawnień 2.3: wyłącznie koordynator).
"""

from django.contrib import admin

from .models import (
    Edition,
    InterviewBooking,
    InterviewSlot,
    Problem,
    QualificationRule,
    ScoringScale,
    Stage,
    StageEntry,
)
from .services import ensure_stage_defaults


class StageInline(admin.TabularInline):
    model = Stage
    extra = 0
    # ``location`` stoi obok terminów, a nie na osobnej karcie: dla etapu stacjonarnego miejsce
    # jest częścią tej samej informacji, co data, i zmienia się razem z nią. ``name`` i ``format``
    # otwierają wiersz, bo odpowiadają na pytanie „co to za etap”, zanim padnie pytanie „kiedy”.
    fields = (
        "kind",
        "name",
        "format",
        "location",
        "opens_at",
        "deadline_at",
        "grace_seconds",
        "results_published_at",
    )
    show_change_link = True


@admin.register(Edition)
class EditionAdmin(admin.ModelAdmin):
    # Okno rejestracji jest widoczne na liście, bo to pierwsza rzecz, o którą pyta się przy
    # edycji („czy przyjmujemy konta?”). Zmienia się je jednak w panelu koordynatora
    # (``/coordinator/registration/``) – tylko tam zapis zostawia wpis audytowy.
    list_display = ("year_label", "is_current", "registration_enabled", "registration_opens_at")
    list_filter = ("is_current", "registration_enabled")
    search_fields = ("year_label",)
    inlines = (StageInline,)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        for stage in form.instance.stages.all():
            ensure_stage_defaults(stage)


class ScoringScaleInline(admin.StackedInline):
    model = ScoringScale
    extra = 0
    can_delete = False


class QualificationRuleInline(admin.StackedInline):
    model = QualificationRule
    extra = 0
    can_delete = False


class ProblemInline(admin.TabularInline):
    model = Problem
    extra = 0
    fields = ("number", "title", "statement_pdf", "allowed_formats", "max_file_mb")
    show_change_link = True


@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = (
        "edition",
        "kind",
        "name",
        "format",
        "location",
        "opens_at",
        "deadline_at",
        "grace_seconds",
        "review_deadline_at",
        "results_published_at",
    )
    list_filter = ("edition", "kind", "format")
    date_hierarchy = "opens_at"
    inlines = (ScoringScaleInline, QualificationRuleInline, ProblemInline)

    def save_related(self, request, form, formsets, change):
        """Etap z admina ma zawsze skalę i próg – tak jak etap z ``create_stage``."""
        super().save_related(request, form, formsets, change)
        ensure_stage_defaults(form.instance)


@admin.register(ScoringScale)
class ScoringScaleAdmin(admin.ModelAdmin):
    list_display = ("stage", "max_value")
    list_filter = ("stage__edition",)


@admin.register(QualificationRule)
class QualificationRuleAdmin(admin.ModelAdmin):
    list_display = ("stage", "mode", "min_points", "top_n")
    list_filter = ("mode", "stage__edition")


@admin.register(Problem)
class ProblemAdmin(admin.ModelAdmin):
    list_display = ("stage", "number", "title", "max_file_mb")
    list_filter = ("stage__edition", "stage__kind")
    search_fields = ("title",)


@admin.register(InterviewSlot)
class InterviewSlotAdmin(admin.ModelAdmin):
    """Terminy rozmów. Wyznacza je panel koordynatora – tu jest podgląd i awaryjna korekta."""

    list_display = ("stage", "starts_at", "ends_at", "capacity", "note")
    list_filter = ("stage__edition", "stage")
    date_hierarchy = "starts_at"
    readonly_fields = ("created_at",)


@admin.register(InterviewBooking)
class InterviewBookingAdmin(admin.ModelAdmin):
    """Zapisy na rozmowy. Zmienia je uczestnik z panelu; tu tylko podgląd."""

    list_display = ("slot", "entry", "created_at")
    list_filter = ("slot__stage__edition", "slot__stage")
    search_fields = ("entry__participant__public_code",)
    readonly_fields = ("created_at",)


@admin.register(StageEntry)
class StageEntryAdmin(admin.ModelAdmin):
    """Wpisy do etapów. Statusy zmienia kwalifikacja (T-07); tu tylko podgląd i korekty ręczne."""

    list_display = ("participant", "stage", "status", "total_points", "created_at")
    list_filter = ("status", "stage__edition", "stage__kind")
    search_fields = ("participant__public_code", "participant__user__email")
    autocomplete_fields = ("participant",)
    readonly_fields = ("created_at",)
