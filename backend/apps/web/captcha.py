"""Ochrona antyspamowa publicznych formularzy rejestracji – w całości u nas, bez usług obcych.

Trzy niezależne warstwy, bo każda łapie inny rodzaj natręta i każda osobno daje się obejść:

1. **CAPTCHA obrazkowa** (``django-simple-captcha``, wyzwanie arytmetyczne) – koszt dla skryptu,
   który wysyła POST-a bez wczytywania strony. Obrazek rysuje Pillow, wyzwanie i odpowiedź leżą
   w naszej bazie, obrazek serwuje nasz adres ``/captcha/image/<klucz>/``. Nie ma tu żadnego
   podmiotu trzeciego ani ciasteczka – patrz sekcja „CAPTCHA” w ``config/settings/base.py``,
2. **pułapka (honeypot)** ``website`` – zwykłe pole tekstowe schowane arkuszem stylów (klasa
   ``hp-field``), a **nie** ``type="hidden"``. To jest sedno: pole ukryte typem jest dla bota
   sygnałem „nie wypełniaj”, a pole ukryte stylem – zwykłym polem formularza, które bot
   wypełniający wszystko wypełni. Człowiek go nie widzi i nie dostanie na nim fokusu
   (``tabindex="-1"``), więc fałszywy alarm wymagałby czytnika ignorującego ``aria-hidden``,
3. **minimalny czas wypełniania** – ukryty, **podpisany** znacznik czasu wystawiony przy
   renderowaniu formularza. Podpis (``django.core.signing``) jest tu konieczny: niepodpisany
   znacznik bot po prostu przepisuje na dowolną wartość z przeszłości.

Czego te warstwy **nie** zastępują: limitu prób (scope ``register``, ``apps/web/throttle.py``) ani
reguł domenowych rejestracji. CAPTCHA podnosi koszt jednego zgłoszenia, a limit ogranicza ich
liczbę z jednego adresu – dopiero razem mają sens.

Komunikat odmowy dla pułapki i dla czasu jest **jeden i ogólny**. Zdanie „wypełniłeś ukryte pole”
byłoby instrukcją obejścia dla autora skryptu, a człowiekowi nic nie mówi, bo tego pola nie widział.
"""

from __future__ import annotations

import time

from captcha.fields import CaptchaField
from django import forms
from django.conf import settings
from django.core import signing

from apps.tenancy.branding import branded_text
from apps.tenancy.context import current_competition

#: Nazwa pułapki. Wygląda jak pole, o które serwis mógłby pytać („strona internetowa”), więc bot
#: dopasowujący pola po nazwie chętnie je wypełni.
HONEYPOT_FIELD_NAME = "website"
TIMESTAMP_FIELD_NAME = "form_ts"
CAPTCHA_FIELD_NAME = "captcha"

#: Pola, których szablon nie renderuje zwykłą pętlą – idą w jednym bloku pod zgodami
#: (``templates/web/_antispam_fields.html``).
ANTISPAM_FIELD_NAMES = (HONEYPOT_FIELD_NAME, TIMESTAMP_FIELD_NAME, CAPTCHA_FIELD_NAME)

#: Sól podpisu znacznika czasu. Własna, żeby podpis z tego formularza nie dał się użyć nigdzie
#: indziej w serwisie (i odwrotnie) – to jedyna rola soli w ``django.core.signing``.
TIMESTAMP_SALT = "apps.web.captcha.form_ts"

#: Domyślny próg, gdy ustawienia go nie podają. Trzy sekundy to czas, w którym człowiek nie zdąży
#: wypełnić nawet jednego pola, a skrypt wysyła cały formularz.
DEFAULT_MIN_FILL_SECONDS = 3

#: Jeden komunikat dla obu pułapek – patrz docstring modułu.
REJECTED_MESSAGE = (
    "Nie udało się potwierdzić, że formularz wypełnił człowiek. Wyślij go jeszcze raz, "
    "a jeśli błąd się powtarza – napisz do contact@qaif.org."
)

CAPTCHA_LABEL = "Zabezpieczenie antyspamowe"

#: Adres kontaktowy w podpowiedzi, a nie w osobnym akapicie szablonu: ta sama treść ma dojechać do
#: obu formularzy rejestracji, a jeden z nich renderuje się przez ``form.as_p``. Bez odnośnika
#: ``mailto:`` – szablon uczestnika escapuje ``help_text``, więc znacznik zostałby w nim widoczny.
CAPTCHA_HELP_TEXT = (
    "Wpisz wynik działania z obrazka. Nie widzisz obrazka (czytnik ekranu, brak grafiki)? "
    "Napisz do contact@qaif.org – konto założymy ręcznie."
)

#: Te same dwa zdania ze **wzorcem** w miejscu adresu. Zdanie zostaje treścią w kodzie, a adres
#: staje się konfiguracją konkursu (``Competition.contact_email``) – bo czyta te komunikaty
#: człowiek, który właśnie **nie może się zarejestrować**, i adres jest jedyną drogą, jaka mu
#: zostaje. Adres cudzego organizatora byłby tu gorszy niż brak adresu
#: (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.4).
REJECTED_MESSAGE_TEMPLATE = (
    "Nie udało się potwierdzić, że formularz wypełnił człowiek. Wyślij go jeszcze raz, "
    "a jeśli błąd się powtarza – napisz do %(contact)s."
)
CAPTCHA_HELP_TEMPLATE = (
    "Wpisz wynik działania z obrazka. Nie widzisz obrazka (czytnik ekranu, brak grafiki)? "
    "Napisz do %(contact)s – konto założymy ręcznie."
)


def _competition():
    """Konkurs żądania albo ``None`` – **bez** ani jednego zapytania do bazy.

    Czytamy zmienną kontekstową (``CompetitionMiddleware`` ustawia ją na każdym żądaniu),
    a nie ``scoping.resolve_competition()``, i są po temu dwa powody. Po pierwsze koszt:
    formularz rejestracji powstaje w żądaniu objętym budżetem zapytań (§ 5.6), a odwrót
    ``resolve_competition`` dokłada zapytanie zawsze, gdy kontekstu nie ma. Po drugie miejsce
    użycia: ten mixin wchodzi także do kreatora ``/setup/``, czyli do instalacji, w której
    konkursu jeszcze **nie ma** – i tam „nie wiadomo, czyj to formularz” jest odpowiedzią
    poprawną, a nie stanem do dopytania bazy.
    """
    return current_competition()


def _branded_message(template: str, fallback: str, competition=None) -> str:
    """Komunikat z adresem konkursu albo dzisiejsze zdanie, gdy nie ma czego podstawić.

    Dwa odwroty, nie jeden: flagę marki czyta ``branded_text`` (jedno wejście, § 1.0 (c)),
    a pusty ``contact_email`` zatrzymujemy tutaj – komunikat „napisz do ” bez adresu byłby gorszy
    od dzisiejszego zdania, a organizator nie ma obowiązku wypełnić tego pola.
    """
    if competition is None or not competition.contact_email:
        return fallback
    return branded_text(template, fallback, competition, contact=competition.contact_email)


def rejected_message(competition=None) -> str:
    """Komunikat odmowy dla pułapki i dla progu czasu – jeden i ogólny (patrz docstring modułu)."""
    return _branded_message(REJECTED_MESSAGE_TEMPLATE, REJECTED_MESSAGE, competition)


def captcha_help_text(competition=None) -> str:
    """Podpowiedź pod obrazkiem – droga dla kogoś, kto obrazka nie zobaczy."""
    return _branded_message(CAPTCHA_HELP_TEMPLATE, CAPTCHA_HELP_TEXT, competition)


def min_fill_seconds() -> int:
    """Próg z ustawień (``ANTISPAM_MIN_FILL_SECONDS``). Zero wyłącza sprawdzenie – patrz E2E."""
    return int(getattr(settings, "ANTISPAM_MIN_FILL_SECONDS", DEFAULT_MIN_FILL_SECONDS))


def sign_timestamp(moment: float | None = None) -> str:
    """Podpisany znacznik czasu wystawienia formularza (sekundy epoki jako tekst)."""
    seconds = int(moment if moment is not None else time.time())
    return signing.Signer(salt=TIMESTAMP_SALT).sign(str(seconds))


def elapsed_seconds(signed: str) -> int | None:
    """Ile sekund minęło od wystawienia formularza. ``None``, gdy podpisu nie ma albo nie pasuje."""
    try:
        value = signing.Signer(salt=TIMESTAMP_SALT).unsign(signed or "")
    except signing.BadSignature:
        return None
    try:
        return int(time.time()) - int(value)
    except ValueError:  # pragma: no cover - podpis chroni treść, więc to stan nieosiągalny
        return None


class CaptchaFormMixin(forms.Form):
    """Dokłada formularzowi CAPTCHĘ, pułapkę i próg czasu. Pierwszy na liście baz.

    Pola powstają **w** ``__init__``, a nie jako atrybuty klasy, i to jest celowe: mają stanąć na
    samym końcu formularza, także w formularzach z własnym ``field_order`` i z polami dokładanymi
    dynamicznie (zgody – ``ConsentFieldsMixin``). ``Form.order_fields`` przesuwa na koniec to,
    czego nie ma w ``field_order``, ale kolejność wewnątrz tej reszty zostaje taka, w jakiej pola
    powstały – więc pole zadeklarowane w klasie bazowej wylądowałoby **przed** zgodami.

    Dlaczego mixin jako pierwsza baza: jego ``clean()`` musi zdjąć z ``cleaned_data`` klucze
    pomocnicze (widoki wołają serwis przez ``**form.cleaned_data``) **po** tym, jak reszta
    łańcucha ``clean()`` zrobi swoje.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields[HONEYPOT_FIELD_NAME] = forms.CharField(
            # Bez etykiety: ``as_p`` i nasz szablon pomijają wtedy ``<label>`` w całości, więc
            # w formularzu nie zostaje po pułapce żaden widoczny ślad (poza samym, ukrytym polem).
            label="",
            required=False,
            max_length=200,
            widget=forms.TextInput(
                attrs={
                    "class": "hp-field",
                    "tabindex": "-1",
                    "autocomplete": "off",
                    "aria-hidden": "true",
                }
            ),
        )
        self.fields[TIMESTAMP_FIELD_NAME] = forms.CharField(
            label="",
            required=False,
            # ``initial`` jako funkcja, nie wartość: wartość policzona przy imporcie modułu
            # zamroziłaby moment wystawienia formularza na start procesu i po kilku sekundach
            # życia aplikacji przepuszczałaby każdy POST.
            initial=sign_timestamp,
            widget=forms.HiddenInput,
        )
        # Konkurs czytamy **raz** na formularz i trzymamy pod własną nazwą: ``clean()`` składa
        # z niego ten sam komunikat odmowy, a nazwa z podkreśleniem nie wchodzi w drogę polom,
        # które formularze rejestracji trzymają pod ``self.competition``.
        self._captcha_competition = _competition()
        self.fields[CAPTCHA_FIELD_NAME] = CaptchaField(
            label=CAPTCHA_LABEL,
            help_text=captcha_help_text(self._captcha_competition),
            error_messages={"invalid": "Wynik działania jest niepoprawny. Spróbuj z nowym obrazkiem."},
        )

    @property
    def antispam_field_names(self) -> tuple[str, ...]:
        """Nazwy pól bloku antyspamowego – szablon renderuje je razem, poza zwykłą pętlą."""
        return ANTISPAM_FIELD_NAMES

    def clean(self):
        """Sprawdza pułapkę i czas, a potem zdejmuje klucze pomocnicze z ``cleaned_data``.

        Sprawdzenia idą **po** ``super().clean()``: jeśli formularz ma już błędy merytoryczne,
        człowiek ma je zobaczyć wszystkie naraz, a nie po jednym na wysyłkę.
        """
        cleaned = super().clean()
        # Do serwisu jadą wyłącznie dane rejestracji – każdy nadmiarowy klucz to TypeError
        # w ``register_participant``/``register_committee``.
        honeypot = cleaned.pop(HONEYPOT_FIELD_NAME, "") or ""
        signed = cleaned.pop(TIMESTAMP_FIELD_NAME, "") or ""
        cleaned.pop(CAPTCHA_FIELD_NAME, None)
        message = rejected_message(self._captcha_competition)
        if honeypot.strip():
            self.add_error(None, message)
            # Bez drugiego komunikatu: bot, który wpadł w pułapkę, i tak nie czyta odpowiedzi.
            return cleaned
        threshold = min_fill_seconds()
        if threshold:
            elapsed = elapsed_seconds(signed)
            if elapsed is None or elapsed < threshold:
                self.add_error(None, message)
        return cleaned
