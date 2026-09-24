"""Formularze funkcji „status ucznia”: wgranie skanu przez uczestnika i odrzucenie przez koordynatora.

Formularze trzymają wyłącznie pola i komunikaty o **polach**. Reguły o pliku (format po treści,
rozmiar) i o stanie sprawy (czy wolno wgrać ponownie, czy wolno odrzucić) są w ``services`` –
formularz panelu jest jednym z wołających, a nie miejscem, w którym te reguły mieszkają.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy

from .services import MAX_REASON_LENGTH
from .validators import ACCEPT_ATTRIBUTE


class ScanUploadForm(forms.Form):
    """Skan albo zdjęcie podstemplowanego zaświadczenia.

    Potwierdzenie jest wymagane **po stronie serwera**, z tego samego powodu, co przy rozwiązaniach:
    najczęstszy błąd to plik nie ten (niepodpisany wzór prosto z drukarki), a jedyną tanią chwilą,
    żeby go zatrzymać, jest kliknięcie „Wyślij”.
    """

    file = forms.FileField(
        label=gettext_lazy("Skan albo zdjęcie zaświadczenia (PDF, JPG lub PNG, do 10 MB)"),
        widget=forms.ClearableFileInput(attrs={"accept": ACCEPT_ATTRIBUTE}),
    )
    confirmed = forms.BooleanField(
        label=gettext_lazy(
            "Potwierdzam, że zaświadczenie ma pieczątkę szkoły i podpis, a plik jest czytelny"
        ),
        error_messages={
            "required": gettext_lazy(
                "Zaznacz potwierdzenie, że zaświadczenie jest podstemplowane i podpisane, a plik czytelny."
            )
        },
    )


class RejectForm(forms.Form):
    """Powód odrzucenia – napisany do uczestnika, bo to on go przeczyta."""

    reason = forms.CharField(
        label="Powód odrzucenia (zobaczy go uczestnik)",
        max_length=MAX_REASON_LENGTH,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
