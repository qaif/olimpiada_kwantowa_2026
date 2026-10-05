"""Formularz ekranu „Uzupełnij zgody” – wyłącznie pola brakujących zgód.

Etykieta jest **ta sama**, co w rejestracji (``consents.label``: treść, odnośnik do PDF-a albo strony
dokumentu, nazwa organizatora) – to ta sama czynność prawna i musi mieć to samo brzmienie. Pole jest
wymagane, bo na tym ekranie stoją wyłącznie zgody wymagane; komunikat o braku to
``Consent.missing_message`` (ten sam, który zna rejestracja i API).
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy

from apps.accounts.consents import label as consent_label
from apps.web.forms import REQUIRED_CSS_CLASS


class ConsentCompletionForm(forms.Form):
    required_css_class = REQUIRED_CSS_CLASS

    def __init__(self, *args, consents=(), organizer: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.consents = tuple(consents)
        for consent in self.consents:
            self.fields[consent.field_name] = forms.BooleanField(
                label=consent_label(consent, organizer=organizer),
                required=True,
                help_text=consent.help_text,
                label_suffix="",
                error_messages={
                    "required": consent.missing_message or gettext_lazy("Ta zgoda jest wymagana."),
                },
            )

    def given_kinds(self) -> set[str]:
        """Rodzaje zaznaczonych zgód – po ``is_valid()``."""
        return {consent.kind for consent in self.consents if self.cleaned_data.get(consent.field_name)}
