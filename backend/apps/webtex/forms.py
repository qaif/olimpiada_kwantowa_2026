from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy


class LatexSubmitForm(forms.Form):
    """Złożenie PDF-u z edytora – samo potwierdzenie, jak ``SubmissionUploadForm.confirmed``.

    Wymagane po stronie serwera, a nie tylko atrybutem ``required`` w HTML: formularz da się wysłać
    także bez przeglądarki, a „złożyłem nie to zadanie” jest najczęstszą reklamacją.
    """

    confirmed = forms.BooleanField(
        label=gettext_lazy("Potwierdzam, że to rozwiązanie właściwego zadania"),
        error_messages={
            "required": gettext_lazy("Zaznacz potwierdzenie, że składasz rozwiązanie właściwego zadania.")
        },
    )


class StageLatexSettingsForm(forms.Form):
    """Oba przełączniki etapu w jednym formularzu – zapis zmienia tylko te, które się zmieniły."""

    sharing_enabled = forms.BooleanField(
        label="Udostępnianie linków w edytorze LaTeX",
        required=False,
        help_text="Włączone – uczestnik etapu może zaprosić innych do swojego projektu linkiem. "
        "Domyślnie wyłączone (praca indywidualna). Wyłączenie natychmiast odcina współpracowników "
        "i blokuje dołączanie.",
    )
    ai_enabled = forms.BooleanField(
        label="Asystent AI w edytorze LaTeX",
        required=False,
        help_text="Włączone – w edytorze działa asystent AI (czat, poprawianie błędów kompilacji, "
        "przeredagowanie zaznaczenia). Na zawodach to pomoc niedozwolona: włączaj tylko na etapach "
        "treningowych. Domyślnie wyłączony; wyłączenie działa natychmiast także w otwartych oknach.",
    )
