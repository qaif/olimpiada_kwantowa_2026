"""Formularze kreatora ``/setup/``: konto operatora (krok 1) i pierwszy konkurs (krok 2).

Formularze **nie** zakładają niczego same — sprawdzają dane i oddają je serwisom z
``apps.tenancy.setup``, a te wołają komendy ``bootstrap_coordinator`` i ``create_competition``.
Reguły spójności konkursu (unikalność identyfikatora, domena wolna, prefiks spoza listy
zarezerwowanych) zostają tam, gdzie stały: w ``Competition.clean`` i w komendzie. Powtórzenie ich
tutaj znaczyłoby drugą regułę, która po pierwszej zmianie modelu mówi co innego niż baza.

Krok 1. ma komplet zabezpieczeń publicznego formularza rejestracji (``apps.web.captcha``):
CAPTCHĘ obrazkową z własnego adresu, pułapkę ``website`` ukrytą stylem i minimalny czas
wypełniania. Krok 2. ich nie ma i to jest decyzja, a nie przeoczenie: do kroku 2. dochodzi
wyłącznie zalogowany superużytkownik założony przed chwilą w kroku 1., więc bramką jest tam konto,
a nie wyzwanie obrazkowe.
"""

from __future__ import annotations

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.tenancy.templates_catalog import TEMPLATE_PUSTY, TEMPLATES
from apps.web.captcha import CaptchaFormMixin

#: Szablony startowe do listy wyboru — etykiety prosto z katalogu, żeby kreator i komenda
#: nazywały to samo tak samo.
TEMPLATE_FIELD_CHOICES = tuple((name, spec["label"]) for name, spec in TEMPLATES.items())


class OperatorForm(CaptchaFormMixin):
    """Krok 1: konto operatora instalacji (superużytkownik w grupie ``coordinator``).

    Hasło jest pytane dwa razy i sprawdzane **walidatorami Django** (``AUTH_PASSWORD_VALIDATORS``)
    — tymi samymi, którymi sprawdza je ``bootstrap_coordinator``. Gdyby sprawdzenie zostało
    wyłącznie w komendzie, hasło za krótkie kończyłoby kreator wyjątkiem zamiast czerwonym
    napisem pod polem, a operator nie miałby jak się dowiedzieć, co jest nie tak.
    """

    email = forms.EmailField(
        label="Adres e-mail operatora",
        max_length=254,
        help_text="Tym adresem będziesz się logować. Konto dostaje pełne uprawnienia w instalacji.",
    )
    first_name = forms.CharField(label="Imię", max_length=150, required=False)
    last_name = forms.CharField(label="Nazwisko", max_length=150, required=False)
    password1 = forms.CharField(
        label="Hasło",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        strip=False,
    )
    password2 = forms.CharField(
        label="Powtórz hasło",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        strip=False,
        help_text="Dla pewności, że hasło zostało wpisane tak, jak miało być.",
    )

    def clean_email(self) -> str:
        return (self.cleaned_data["email"] or "").strip().lower()

    def clean(self):
        """Zgodność obu haseł i reguły siły hasła — w tej kolejności.

        Walidatory dostają **niezapisany** obiekt konta, bo część z nich (podobieństwo do danych
        użytkownika) porównuje hasło z adresem e-mail i z imieniem. Bez tego „olimpiada@…” jako
        hasło do konta „olimpiada@…” przeszłoby.
        """
        cleaned = super().clean()
        password1 = cleaned.get("password1") or ""
        password2 = cleaned.get("password2") or ""
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Hasła nie są takie same.")
            return cleaned
        if password1:
            from apps.accounts.models import User

            candidate = User(
                email=cleaned.get("email") or "",
                first_name=cleaned.get("first_name") or "",
                last_name=cleaned.get("last_name") or "",
            )
            try:
                validate_password(password1, candidate)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


class CompetitionForm(forms.Form):
    """Krok 2: pierwszy konkurs instalacji — dane, z których ``create_competition`` składa resztę.

    Pola są dokładnie tymi argumentami komendy, które człowiek musi podać (§ 1.7.1, krok 2):
    nazwa, skrót, identyfikator, domena, organizator, adres kontaktowy i szablon. Reszta —
    witryna, drzewo stron, ustawienia serwisu, edycja, etapy — wynika z szablonu i nie jest
    pytaniem do operatora, który uruchamia serwis pierwszy raz w życiu.
    """

    name = forms.CharField(
        label="Nazwa konkursu",
        max_length=200,
        help_text="Pełna nazwa, np. „Olimpiada Fizyczna”.",
    )
    short_name = forms.CharField(
        label="Nazwa skrócona",
        max_length=100,
        required=False,
        help_text="Do prefiksu tematów listów i do nagłówka. Puste = powtórzy nazwę pełną.",
    )
    slug = forms.SlugField(
        label="Identyfikator",
        max_length=50,
        help_text="Małe litery bez polskich znaków, np. „fizyczna”. Trwały — wchodzi do eksportów.",
    )
    domain = forms.CharField(
        label="Domena",
        max_length=255,
        help_text="Adres, pod którym stoi konkurs, np. „olimpiadafizyczna.pl”. Bez „https://”.",
    )
    organizer = forms.CharField(
        label="Organizator",
        max_length=200,
        required=False,
        help_text="Podmiot prawny w stopce i w dokumentach. Puste = powtórzy nazwę konkursu.",
    )
    contact_email = forms.EmailField(
        label="Adres kontaktowy organizatora",
        max_length=254,
        required=False,
    )
    template = forms.ChoiceField(
        label="Szablon startowy",
        choices=TEMPLATE_FIELD_CHOICES,
        initial=TEMPLATE_PUSTY,
        widget=forms.RadioSelect,
        help_text="Struktura stron, etapów i formatów plików. Wszystko da się później zmienić.",
    )

    def clean_slug(self) -> str:
        return (self.cleaned_data["slug"] or "").strip().lower()

    def clean_domain(self) -> str:
        """Ucina schemat i końcowy ukośnik — resztę rozstrzyga komenda, tak jak dla powłoki."""
        value = (self.cleaned_data["domain"] or "").strip().lower().rstrip("/")
        for scheme in ("https://", "http://"):
            if value.startswith(scheme):
                value = value[len(scheme) :]
        return value
