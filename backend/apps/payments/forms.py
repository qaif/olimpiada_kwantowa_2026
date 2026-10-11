"""Formularze płatności. Kształt danych – tutaj; reguły (kwoty, uprawnienia, stany) – w serwisie.

Formularze płacącego są tłumaczone (gettext): opiekun drużyny IQO i uczestnik czytają je w swoim
języku. Formularze koordynatora są po polsku bez gettext – tak jak cały panel (I18N-01 § 0).

**Żaden formularz nie zbiera kwoty zapłaty.** Kwotę liczy serwer (``apps.payments.pricing``);
kwoty w formularzach koordynatora to ceny cennika, zniżki i zwroty – decyzje organizatora.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.student_status.validators import ACCEPT_ATTRIBUTE

from .models import (
    DELEGATION_KINDS,
    MAX_OBSERVERS,
    AdjustmentKind,
    BuyerType,
    PaymentSettings,
    PriceKind,
    PricePeriod,
)
from .services import price_key


class BillingForm(forms.Form):
    """Dane nabywcy na fakturę – instytucja (np. ministerstwo, szkoła) albo osoba prywatna."""

    buyer_type = forms.ChoiceField(label=_("Nabywca"), choices=BuyerType.choices, widget=forms.RadioSelect)
    buyer_name = forms.CharField(label=_("Nazwa instytucji albo imię i nazwisko"), max_length=200)
    buyer_address = forms.CharField(
        label=_("Adres"), max_length=500, widget=forms.Textarea(attrs={"rows": 3})
    )
    buyer_country = forms.CharField(label=_("Kraj"), max_length=100)
    buyer_vat_id = forms.CharField(
        label=_("NIP / VAT ID"),
        max_length=32,
        required=False,
        help_text=_("Opcjonalnie – jeśli instytucja ma numer podatkowy, który ma znaleźć się na fakturze."),
    )
    buyer_email = forms.EmailField(label=_("E-mail do rozliczeń"))

    def __init__(self, *args, observers: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if observers:
            self.fields["observers"] = forms.IntegerField(
                label=_("Liczba obserwatorów"),
                min_value=0,
                max_value=MAX_OBSERVERS,
                initial=0,
                help_text=_("Osoby towarzyszące drużynie bez roli opiekuna – płatne według cennika."),
            )


class ReasonForm(forms.Form):
    reason = forms.CharField(label=_("Powód"), max_length=300, required=False)


# --- koordynator (po polsku, bez gettext) ------------------------------------------------------------


class SettingsForm(forms.ModelForm):
    class Meta:
        model = PaymentSettings
        fields = [
            "seller_tax_id",
            "bank_account",
            "bank_swift",
            "bank_name",
            "document_prefix",
            "vat_note",
            "invoice_note",
            "proforma_due_days",
            "card_payments",
            "p24_payments",
            "bank_transfer",
        ]
        widgets = {"invoice_note": forms.Textarea(attrs={"rows": 2})}

    def clean_document_prefix(self) -> str:
        value = (self.cleaned_data.get("document_prefix") or "").strip().upper()
        if value and not value.replace("-", "").isalnum():
            raise forms.ValidationError("Prefiks: litery, cyfry i myślnik.")
        return value

    def clean_bank_account(self) -> str:
        return (self.cleaned_data.get("bank_account") or "").replace(" ", "").upper()


class PriceListForm(forms.Form):
    """Cennik delegacji: waluta, terminy i siatka cen (pozycja × okres). Puste pole = brak ceny."""

    currency = forms.CharField(label="Waluta (ISO 4217)", max_length=3, min_length=3, initial="EUR")
    early_until = forms.DateField(
        label="Cena wczesna do (włącznie)", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    late_from = forms.DateField(
        label="Cena późna od", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    is_active = forms.BooleanField(label="Cennik aktywny", required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = dict(PriceKind.choices)
        periods = dict(PricePeriod.choices)
        for kind in DELEGATION_KINDS:
            for period in PricePeriod.values:
                self.fields[price_key(kind, period)] = forms.DecimalField(
                    label=f"{labels[kind]} – {periods[period]}",
                    required=False,
                    min_value=0,
                    max_digits=10,
                    decimal_places=2,
                )

    def clean_currency(self) -> str:
        return (self.cleaned_data.get("currency") or "").strip().upper()

    def clean(self):
        data = super().clean()
        early, late = data.get("early_until"), data.get("late_from")
        if early and late and early >= late:
            raise forms.ValidationError("Cena późna musi zaczynać się po terminie ceny wczesnej.")
        return data

    def grid(self):
        """Wiersze siatki do szablonu: pozycja i trzy pola okresów."""
        labels = dict(PriceKind.choices)
        return [
            {
                "label": labels[kind],
                "fields": [self[price_key(kind, period)] for period in PricePeriod.values],
            }
            for kind in DELEGATION_KINDS
        ]

    def prices(self) -> dict:
        return {
            price_key(kind, period): self.cleaned_data.get(price_key(kind, period))
            for kind in DELEGATION_KINDS
            for period in PricePeriod.values
        }


class AdjustmentForm(forms.Form):
    kind = forms.ChoiceField(label="Rodzaj", choices=[(k, str(v)) for k, v in AdjustmentKind.choices])
    amount = forms.DecimalField(
        label="Kwota zniżki",
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        help_text="Tylko dla zniżki – w walucie cennika. Zwolnienie obejmuje całość.",
    )
    reason = forms.CharField(label="Uzasadnienie", max_length=300)


class CoordinatorReasonForm(forms.Form):
    reason = forms.CharField(label="Powód", max_length=300)


class BankTransferForm(forms.Form):
    received_on = forms.DateField(label="Data wpływu", widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(label="Notatka (np. nr wyciągu)", max_length=300, required=False)
    proof = forms.FileField(
        label="Dowód wpłaty (PDF/JPG/PNG, opcjonalnie)",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ACCEPT_ATTRIBUTE}),
    )


class RefundForm(forms.Form):
    """Powód zwrotu. Pozycje i ilości (``line_<id>``) czyta widok – kwotę liczy serwis z pozycji (M3)."""

    reason = forms.CharField(label="Powód", max_length=300)
