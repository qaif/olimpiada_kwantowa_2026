"""Formularz ustawień notatnika zadania (panel koordynatora, QC-01 § 7)."""

from __future__ import annotations

import json

from django import forms
from django.utils.translation import gettext_lazy as _

from . import notebook_io
from .models import ContentLanguage, NotebookMode, ResultsVisibility

TESTS_EXAMPLE = """[
  {"id": "bell", "name": "Stan Bella", "points": 2, "target": "qc",
   "check": "statevector", "expected": {"00": "1/sqrt(2)", "11": "1/sqrt(2)"}},
  {"id": "depth", "name": "Głębokość", "points": 1, "target": "qc",
   "check": "circuit", "max_depth": 2, "allowed_gates": ["h", "cx"]}
]"""


class NotebookTaskForm(forms.Form):
    mode = forms.ChoiceField(label=_("Tryb"), choices=NotebookMode.choices)
    language = forms.ChoiceField(
        label=_("Język treści notatnika"),
        choices=ContentLanguage.choices,
        help_text=_("Język notatnika startowego i komunikatów testów. Interfejs JupyterLab jest angielski."),
    )
    time_limit_seconds = forms.IntegerField(
        label=_("Limit czasu wykonania (s)"), min_value=5, max_value=60, initial=20
    )
    memory_limit_mb = forms.IntegerField(
        label=_("Limit pamięci (MB)"), min_value=256, max_value=1024, initial=512
    )
    results_visibility = forms.ChoiceField(
        label=_("Kto widzi wynik testów ukrytych"),
        choices=ResultsVisibility.choices,
        help_text=_(
            "„Od razu” daje uczestnikowi wyrocznię: może wysyłać kolejne wersje i patrzeć, które testy "
            "ukryte przechodzą. Wybieraj to wyłącznie na etapach treningowych."
        ),
    )
    starter_file = forms.FileField(
        label=_("Notatnik startowy (.ipynb)"),
        required=False,
        help_text=_(
            "Puste = zostaje obecny (albo generowany szablon). Wyjścia komórek zostaną wyczyszczone."
        ),
    )
    clear_starter = forms.BooleanField(
        label=_("Usuń wgrany notatnik startowy (użyj szablonu)"), required=False
    )
    reference_file = forms.FileField(
        label=_("Notatnik wzorcowy (.ipynb)"),
        required=False,
        help_text=_("Twoje rozwiązanie – służy wyłącznie do sprawdzenia testów. Uczestnicy go nie widzą."),
    )
    visible_tests = forms.CharField(
        label=_("Testy widoczne (JSON)"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 10, "spellcheck": "false", "class": "code-input"}),
        help_text=_("Trafiają do notatnika uczestnika – uczestnik widzi je i uruchamia w przeglądarce."),
    )
    hidden_tests = forms.CharField(
        label=_("Testy ukryte (JSON)"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 14, "spellcheck": "false", "class": "code-input"}),
        help_text=_("Liczone wyłącznie na serwerze. Nigdy nie trafiają do przeglądarki uczestnika."),
    )

    def _notebook(self, name: str):
        upload = self.cleaned_data.get(name)
        if not upload:
            return None
        if not str(upload.name).lower().endswith(".ipynb"):
            raise forms.ValidationError(_("To musi być plik .ipynb."))
        data = upload.read(notebook_io.MAX_NOTEBOOK_BYTES + 1)
        try:
            return notebook_io.validate_uploaded(data)
        except notebook_io.NotebookError as exc:
            if str(exc) == "too_large":
                raise forms.ValidationError(_("Notatnik jest za duży (najwyżej 1 MB).")) from exc
            raise forms.ValidationError(_("To nie jest poprawny notatnik Jupytera (nbformat 4).")) from exc

    def clean_starter_file(self):
        return self._notebook("starter_file")

    def clean_reference_file(self):
        return self._notebook("reference_file")


def tests_as_text(tests) -> str:
    return json.dumps(tests or [], ensure_ascii=False, indent=2) if tests else ""
