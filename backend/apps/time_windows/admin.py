"""Okna czasowe w ``/admin/`` – do wglądu operatora.

Zmiany idą wyłącznie przez ekran koordynatora: tam są reguły czasu i audyt.
"""

from django.contrib import admin

from .models import (
    CountryTimezone,
    DelegationWindow,
    ParticipantTimezone,
    ParticipantWindow,
    TimeWindow,
    WindowPlan,
)


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Bez zapisu: zmiana okna z pominięciem serwisu ominęłaby blokadę „po starcie” i audyt."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TimeWindowInline(admin.TabularInline):
    model = TimeWindow
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(WindowPlan)
class WindowPlanAdmin(_ReadOnlyAdmin):
    list_display = ("stage", "duration_minutes", "preferred_local_hour", "created_at")
    inlines = [TimeWindowInline]


@admin.register(DelegationWindow)
class DelegationWindowAdmin(_ReadOnlyAdmin):
    list_display = ("plan", "delegation", "window", "assigned_at")


@admin.register(ParticipantWindow)
class ParticipantWindowAdmin(_ReadOnlyAdmin):
    list_display = ("plan", "participant", "window", "extra_minutes", "set_at")


@admin.register(CountryTimezone)
class CountryTimezoneAdmin(_ReadOnlyAdmin):
    list_display = ("region", "timezone", "set_at")


@admin.register(ParticipantTimezone)
class ParticipantTimezoneAdmin(_ReadOnlyAdmin):
    list_display = ("participant", "timezone", "set_at")
