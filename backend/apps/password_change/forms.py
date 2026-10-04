"""Formularz zmiany hasła (AUTH-01b).

Własny formularz zamiast ``django.contrib.auth.forms.PasswordChangeForm`` z jednego powodu:
tamten sprawdza aktualne hasło w ``clean_old_password``, a u nas robi to serwis
(``apps.password_change.services.change_password``) – drugi ``check_password`` w formularzu
podwajałby koszt każdej próby (skrót hasła jest celowo wolny) i rozdzielał regułę na dwa miejsca.

Formularz pilnuje wyłącznie tego, co jest **jego** sprawą: obecności pól i zgodności powtórzenia.
Walidatory nowego hasła woła serwis (z kontem jako ``user``), a widok przypina ich komunikaty do
pola nowego hasła – tak, jak przy rejestracji.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy

from apps.web.forms import PASSWORD_MISMATCH_MESSAGE


class PasswordChangeForm(forms.Form):
    old_password = forms.CharField(
        label=gettext_lazy("Aktualne hasło"),
        strip=False,
        max_length=200,
        # ``current-password``: menedżer haseł podstawia zapisane hasło do **tego** konta,
        # a nie proponuje wygenerowania nowego w polu, które ma przyjąć stare.
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "autofocus": True}),
    )
    new_password1 = forms.CharField(
        label=gettext_lazy("Nowe hasło"),
        strip=False,
        max_length=200,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=gettext_lazy(
            "Co najmniej 10 znaków; nie może być popularnym hasłem, ciągiem samych cyfr "
            "ani przypominać Twojego imienia, nazwiska lub adresu e-mail."
        ),
    )
    new_password2 = forms.CharField(
        label=gettext_lazy("Powtórz nowe hasło"),
        strip=False,
        max_length=200,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned = super().clean()
        first, second = cleaned.get("new_password1"), cleaned.get("new_password2")
        if first and second and first != second:
            self.add_error("new_password2", PASSWORD_MISMATCH_MESSAGE)
        return cleaned
