"""Formularze tłumaczeń zadań. Reguły (okno, języki, blokady) rozstrzyga ``services`` – tu tylko kształt.

Formularze opiekuna mają etykiety przez gettext (ekran anglojęzyczny), formularze koordynatora –
po polsku bez gettext, jak reszta panelu (I18N-01 § 0, DEL-01 § 6 p. 9).
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from . import languages
from .models import BODY_MAX_LENGTH, SharingMode

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


class LanguagesForm(forms.Form):
    primary = forms.ChoiceField(label=_("Język 1 (domyślny dla uczniów)"))
    secondary = forms.ChoiceField(label=_("Język 2 (opcjonalnie)"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        options = languages.choices()
        self.fields["primary"].choices = [("", "—"), *options]
        self.fields["secondary"].choices = [("", "—"), *options]

    def codes(self) -> list[str]:
        return [self.cleaned_data["primary"], self.cleaned_data.get("secondary") or ""]


class DraftForm(forms.Form):
    title = forms.CharField(label=_("Tytuł zadania"), max_length=300, required=False)
    body_md = forms.CharField(
        label=_("Treść (Markdown, wzory w $…$)"),
        required=False,
        max_length=BODY_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 28, "spellcheck": "true"}),
    )


class PdfForm(forms.Form):
    file = forms.FileField(label=_("Przetłumaczony PDF"))
    title = forms.CharField(label=_("Tytuł zadania"), max_length=300, required=False)


class WindowForm(forms.Form):
    opens_at = forms.DateTimeField(
        label="Otwarcie okna",
        input_formats=[DATETIME_FORMAT],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_FORMAT),
    )
    closes_at = forms.DateTimeField(
        label="Zamknięcie okna",
        input_formats=[DATETIME_FORMAT],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_FORMAT),
        help_text="Najpóźniej w chwili otwarcia etapu.",
    )
    sharing_mode = forms.ChoiceField(
        label="Tłumaczenia delegacji jednego języka", choices=SharingMode.choices
    )


class SourceForm(forms.Form):
    body_md = forms.CharField(
        label="Treść oficjalna (Markdown, wzory w $…$)",
        required=False,
        max_length=BODY_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 24}),
    )


class ReturnForm(forms.Form):
    comment = forms.CharField(
        label="Komentarz dla tłumaczy", max_length=4000, widget=forms.Textarea(attrs={"rows": 4})
    )
