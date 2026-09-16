"""Formularz rejestracji opiekuna szkolnego.

Osobny moduł od ``apps.web.forms`` z tego samego powodu, co ``coordinator_forms``: ten formularz
obsługuje **jeden** ekran (``/register/supervisor/``), nie ma odpowiednika w API i nie jest
częścią żadnego kontraktu poza własnym widokiem.

Blok antyspamowy jest ten sam, co przy pozostałych rejestracjach (CAPTCHA, pułapka, próg czasu
wypełnienia) – formularz jest publiczny, więc bez niego byłby najtańszą drogą do zakładania kont
w serwisie. Limit żądań dokłada widok (scope ``register``).

Szkoła jest **jednym polem wolnego tekstu** i nie korzysta z wyszukiwarki SIO, w odróżnieniu od
rejestracji uczestnika. Powód jest rzeczowy, a nie oszczędnościowy: nazwa szkoły uczestnika
wchodzi do grupowania w publikowanych wynikach (próg k-anonimowości), więc **musi** być zapisana
tak samo u wszystkich uczniów jednej placówki. Szkoła opiekuna nie wchodzi do żadnego rachunku –
jest podpisem na zaświadczeniu i wskazówką dla organizatora przy weryfikacji. Nauczyciel bywa
zresztą opiekunem uczniów z kilku placówek i wymuszanie na nim jednego wiersza ze słownika
kazałoby mu wybrać nieprawdę.
"""

from __future__ import annotations

from django import forms

from apps.web.captcha import CaptchaFormMixin
from apps.web.forms import (
    PASSWORD_CONFIRM_FIELD,
    REQUIRED_CSS_CLASS,
    clean_password_pair,
    password_field,
    phone_field,
)


class SupervisorRegisterForm(CaptchaFormMixin):
    """Rejestracja opiekuna szkolnego: konto, szkoła i telefon kontaktowy.

    Telefon jest opcjonalny, w odróżnieniu od profilu uczestnika: organizator dzwoni do opiekuna
    wtedy, gdy nie może dodzwonić się do ucznia, a to jest udogodnienie, nie warunek konta.
    """

    required_css_class = REQUIRED_CSS_CLASS
    field_order = [
        "email",
        "password",
        PASSWORD_CONFIRM_FIELD,
        "first_name",
        "last_name",
        "school",
        "phone",
    ]

    email = forms.EmailField(
        label="Adres e-mail",
        max_length=254,
        help_text=(
            "Ten sam adres, który Twoi uczniowie wpisują w swoich profilach – po nim panel "
            "odnajduje ich prace."
        ),
    )
    password = password_field()
    password2 = password_field("Powtórz hasło")
    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)
    school = forms.CharField(label="Szkoła", max_length=255, required=False)
    phone = phone_field(required=False)

    def clean(self):
        return clean_password_pair(self, super().clean())
