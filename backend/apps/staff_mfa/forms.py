"""Formularz polityki 2FA konkursu (ekran koordynatora – po polsku, I18N-01 § 0)."""

from __future__ import annotations

from django import forms

from . import policy
from .models import PolicyMode, TwoFactorPolicy


class PolicyForm(forms.ModelForm):
    """Tryb, role **konkursu** (role platformy ustawia operator w ``.env``), okres, ciasteczko."""

    roles = forms.MultipleChoiceField(
        label="Wymagane role (tryb „wybrane role”)",
        choices=[(key, policy.ROLE_KEYS[key]) for key in policy.COMPETITION_ROLE_KEYS],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model = TwoFactorPolicy
        fields = ("mode", "roles", "grace_days", "allow_remember")
        widgets = {"mode": forms.RadioSelect}

    def clean_grace_days(self):
        value = self.cleaned_data.get("grace_days")
        # Sito na literówki („140” zamiast „14”): okres dłuższy niż kwartał nie jest okresem
        # przejściowym, tylko wyłączeniem wymogu na cały sezon.
        if value is not None and value > 90:
            raise forms.ValidationError("Okres przejściowy może trwać najwyżej 90 dni.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") != PolicyMode.CUSTOM:
            # W trybie automatycznym lista ról nie znaczy nic – nie zapisujemy jej, żeby po
            # przełączeniu na „wybrane role” nie wróciło nieoczekiwanie stare zaznaczenie.
            cleaned["roles"] = []
        return cleaned
