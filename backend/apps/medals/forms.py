"""Formularze ekranu medali koordynatora (po polsku, bez gettext – I18N-01 § 0)."""

from __future__ import annotations

from decimal import Decimal

from django import forms

from .models import Award, MedalScheme

PERCENT_HELP = "Odsetek uczestników etapu (bez zdyskwalifikowanych); pula zaokrąglana w górę."


class SchemeForm(forms.ModelForm):
    """Progi i kryteria. Suma pul sprawdzana tu (komunikat przy polu) i jeszcze raz w serwisie."""

    class Meta:
        model = MedalScheme
        fields = (
            "gold_percent",
            "silver_percent",
            "bronze_percent",
            "tie_policy",
            "hm_percent_of_best",
            "hm_full_solution",
        )
        help_texts = {
            "gold_percent": PERCENT_HELP,
            "silver_percent": "Kolejny odsetek po złocie (domyślnie 17 %, łącznie 25 %).",
            "bronze_percent": "Kolejny odsetek po srebrze (domyślnie 25 %, łącznie 50 %).",
            "tie_policy": "Remis nigdy nie jest dzielony – ten sam wynik zawsze daje tę samą nagrodę.",
            "hm_percent_of_best": "Puste = bez kryterium procentowego (zostaje pełne rozwiązanie zadania).",
            "hm_full_solution": "Jak na IMO: maksimum punktów za choć jedno zadanie daje wyróżnienie.",
        }

    def clean(self):
        cleaned = super().clean()
        total = sum(
            (
                cleaned.get(name) or Decimal(0)
                for name in ("gold_percent", "silver_percent", "bronze_percent")
            ),
            Decimal(0),
        )
        if total > 100:
            raise forms.ValidationError("Złoto, srebro i brąz razem nie mogą przekroczyć 100 % uczestników.")
        return cleaned


class OverrideForm(forms.Form):
    """Ręczna nagroda jednego wpisu. Uzasadnienie obowiązkowe."""

    entry_id = forms.IntegerField(widget=forms.HiddenInput)
    award = forms.ChoiceField(label="Nagroda", choices=Award.choices)
    justification = forms.CharField(
        label="Uzasadnienie",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Powód decyzji komitetu. Nie wpisuj danych osobowych – wpis widzą wszyscy koordynatorzy.",
    )


class UnfreezeForm(forms.Form):
    justification = forms.CharField(
        label="Powód odmrożenia",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Trafia do dziennika zdarzeń. Bez danych osobowych.",
    )


class IssueForm(forms.Form):
    participation = forms.BooleanField(
        label="Także zaświadczenia o udziale dla wszystkich uczestników etapu",
        required=False,
        initial=True,
    )
