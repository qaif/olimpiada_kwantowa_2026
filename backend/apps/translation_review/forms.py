"""Formularze przeglądu tłumaczeń. Reguły treści są w ``validation``/``services`` – tu tylko kształt pól."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from . import catalogs
from .models import GrantLevel
from .services import COMMENT_MAX, PHRASE_MAX


def language_choices(codes) -> list[tuple[str, str]]:
    return [(code, catalogs.language_label(code)) for code in codes]


class FilterForm(forms.Form):
    status = forms.ChoiceField(
        label=_("Pokaż"),
        required=False,
        choices=[
            ("", _("wszystkie napisy")),
            ("untranslated", _("bez tłumaczenia")),
            ("machine", _("tłumaczenie maszynowe (nieprzejrzane)")),
            ("reviewed", _("przejrzane")),
            ("pending", _("z oczekującą propozycją")),
        ],
    )
    q = forms.CharField(label=_("Szukaj"), required=False, max_length=200)


class SuggestionForm(forms.Form):
    text = forms.CharField(
        label=_("Twoja propozycja"),
        widget=forms.Textarea(attrs={"rows": 3, "dir": "auto"}),
        max_length=4000,
    )
    approve = forms.BooleanField(label=_("Zatwierdź od razu (recenzent)"), required=False)


class ReportForm(forms.Form):
    language = forms.ChoiceField(label=_("Język"))
    page = forms.CharField(required=False, widget=forms.HiddenInput, max_length=2000)
    phrase = forms.CharField(
        label=_("Napis na stronie"),
        required=False,
        max_length=PHRASE_MAX,
        help_text=_("Skopiuj fragment źle przetłumaczonego tekstu – po nim znajdziemy napis."),
    )
    comment = forms.CharField(
        label=_("Co jest nie tak / jak powinno brzmieć"),
        widget=forms.Textarea(attrs={"rows": 4, "dir": "auto"}),
        max_length=COMMENT_MAX,
    )

    def __init__(self, *args, languages=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["language"].choices = language_choices(languages)


class GrantForm(forms.Form):
    email = forms.EmailField(label=_("Adres e-mail konta"))
    language = forms.ChoiceField(label=_("Język"))
    level = forms.ChoiceField(label=_("Poziom"))

    def __init__(self, *args, reviewer_allowed=False, languages=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["language"].choices = language_choices(languages)
        levels = [(GrantLevel.TRANSLATOR, _("tłumacz – proponuje i głosuje"))]
        if reviewer_allowed:
            levels.append((GrantLevel.REVIEWER, _("recenzent – także zatwierdza poprawki")))
        self.fields["level"].choices = levels
