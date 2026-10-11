"""Formularze okien czasowych: ekran koordynatora (po polsku) i strefa ucznia u opiekuna (gettext).

Formularze sprawdzają wyłącznie **kształt** danych (liczby w zakresie, strefa z listy). Reguły
czasu i ramy etapu stoją w ``apps.time_windows.services`` – formularz nie wie, czy okno już się
zaczęło, i wiedzieć nie powinien: te same reguły obowiązują każdą drogę zapisu.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.web.forms import LocalDateTimeField

from .models import MAX_DURATION_MINUTES, MAX_EXTRA_MINUTES, MAX_WINDOWS, MIN_DURATION_MINUTES
from .zones import timezone_choices


class PlanCreateForm(forms.Form):
    """Włączenie trybu okien: czas pracy, godzina preferowana i generator okien."""

    duration_minutes = forms.IntegerField(
        label="Czas pracy (min)",
        min_value=MIN_DURATION_MINUTES,
        max_value=MAX_DURATION_MINUTES,
        initial=300,
        help_text="Jednakowy dla wszystkich okien, np. 300 = 5 godzin.",
    )
    first_start = LocalDateTimeField(label="Start pierwszego okna (czas polski)")
    count = forms.IntegerField(label="Liczba okien", min_value=1, max_value=MAX_WINDOWS, initial=3)
    interval_minutes = forms.IntegerField(
        label="Odstęp między startami (min)",
        min_value=0,
        max_value=24 * 60,
        initial=480,
        help_text="Np. 480 = trzy okna co 8 godzin w ciągu doby.",
    )
    preferred_local_hour = forms.IntegerField(
        label="Preferowana godzina startu w kraju",
        min_value=0,
        max_value=23,
        initial=10,
        help_text="Kraj trafia domyślnie do okna, którego start w jego strefie jest najbliżej tej godziny.",
    )


class PlanSettingsForm(forms.Form):
    duration_minutes = forms.IntegerField(
        label="Czas pracy (min)", min_value=MIN_DURATION_MINUTES, max_value=MAX_DURATION_MINUTES
    )
    preferred_local_hour = forms.IntegerField(
        label="Preferowana godzina startu w kraju", min_value=0, max_value=23
    )


class WindowStartForm(forms.Form):
    starts_at = LocalDateTimeField(label="Start okna (czas polski)")


def window_choices(windows, *, blank_label: str) -> list[tuple[str, str]]:
    return [("", blank_label), *((str(item.pk), f"Okno {item.label}") for item in windows)]


class DelegationAssignForm(forms.Form):
    window = forms.ChoiceField(label="Okno", required=False)

    def __init__(self, *args, windows=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["window"].choices = window_choices(windows, blank_label="domyślnie (strefa kraju)")


class ParticipantExceptionForm(forms.Form):
    """Wyjątek ucznia – po kodzie publicznym, bo to jedyny identyfikator, który koordynator ma pod ręką."""

    code = forms.CharField(label="Kod uczestnika", max_length=32)
    window = forms.ChoiceField(label="Okno", required=False)
    extra_minutes = forms.IntegerField(
        label="Dodatkowy czas (min)", min_value=0, max_value=MAX_EXTRA_MINUTES, initial=0
    )
    reason = forms.CharField(label="Powód", max_length=300, required=False)

    def __init__(self, *args, windows=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["window"].choices = window_choices(windows, blank_label="jak delegacja")


class CountryTimezoneForm(forms.Form):
    timezone = forms.ChoiceField(label="Strefa czasowa", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = [("", "strefa stolicy (domyślna)"), *timezone_choices()]


class StudentTimezoneForm(forms.Form):
    """Strefa ucznia ustawiana przez opiekuna drużyny – ekran tłumaczony."""

    timezone = forms.ChoiceField(label=_("Strefa czasowa"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = [("", _("jak kraj drużyny")), *timezone_choices()]
