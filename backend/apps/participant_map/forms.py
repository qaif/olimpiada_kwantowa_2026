"""Formularz ustawień mapy uczestników w panelu koordynatora (MAP-01)."""

from __future__ import annotations

from django import forms

from .models import MAX_TOWN_COUNT, MIN_TOWN_COUNT


class MapSettingsForm(forms.Form):
    enabled = forms.BooleanField(
        label="Pokazuj mapę uczestników w sekcji „Dokumenty”",
        required=False,
        help_text=(
            "Strona /dokumenty/mapa-uczestnikow/ i jej pozycja w menu „Dokumenty” oraz w spisie dokumentów."
        ),
    )
    min_town_count = forms.IntegerField(
        label="Próg ochrony – najmniej uczestników w miejscowości pokazanej z nazwą",
        min_value=MIN_TOWN_COUNT,
        max_value=MAX_TOWN_COUNT,
        help_text=(
            f"Miejscowość z mniejszą liczbą uczestników trafia do pozycji „pozostałe miejscowości” swojego "
            f"województwa – bez nazwy i bez kółka na mapie. Najmniej {MIN_TOWN_COUNT}: próg można "
            "podnieść, nie można go obniżyć."
        ),
    )
