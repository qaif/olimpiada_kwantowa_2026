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

#: Komunikat, gdy dokument zmienił wersję między otwarciem ekranu a wysłaniem formularza.
VERSION_CHANGED = gettext_lazy(
    "Ten dokument zmienił się, kiedy ten ekran był otwarty. Przeczytaj go jeszcze raz i zaznacz pole "
    "ponownie."
)


def version_field_name(consent) -> str:
    return f"{consent.field_name}__version"


class ConsentCompletionForm(forms.Form):
    """Pola brakujących zgód i – przy każdym – ukryta wersja dokumentu, którą uczestnik **widział**.

    Wersja jedzie z formularzem (przegląd L3): bez niej zaznaczenie pola pod wersją X, wysłane po tym,
    jak koordynator przestawił dokument na Y, zapisałoby zgodę na Y – tekst, którego nikt nie czytał.
    """

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
            self.fields[version_field_name(consent)] = forms.CharField(
                required=False, initial=consent.version, widget=forms.HiddenInput
            )
        self.changed = ()

    def clean(self):
        """Wersja z formularza ≠ bieżąca → pole do ponownego zaznaczenia (``changed``), bez zapisu."""
        cleaned = super().clean()
        self.changed = tuple(
            consent
            for consent in self.consents
            if (self.data.get(version_field_name(consent)) or "") != consent.version
        )
        for consent in self.changed:
            # Zamiast „pole wymagane” – powód, dla którego pole jest znowu puste.
            self.errors.pop(consent.field_name, None)
            self.add_error(consent.field_name, VERSION_CHANGED)
        return cleaned

    def versions(self) -> dict[str, str]:
        """Rodzaj → wersja, którą uczestnik widział – serwis sprawdza ją jeszcze raz pod blokadą."""
        return {consent.kind: consent.version for consent in self.consents}

    def given_kinds(self) -> set[str]:
        """Rodzaje zaznaczonych zgód – po ``is_valid()``."""
        return {consent.kind for consent in self.consents if self.cleaned_data.get(consent.field_name)}
