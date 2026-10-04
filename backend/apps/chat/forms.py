"""Formularze Wiadomości. Cienkie, jak na forum: kształt pola tutaj, reguły w ``apps.chat.services``."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy

from .models import (
    DEFAULT_DAILY_NEW_CONVERSATIONS,
    MAX_BODY_LENGTH,
    MAX_CIPHERTEXT_LENGTH,
    MAX_DAILY_NEW_CONVERSATIONS,
    MAX_REASON_LENGTH,
    MIN_DAILY_NEW_CONVERSATIONS,
    AgePolicy,
    PeerMode,
)

#: Opisy zasad wieku na ekranie ustawień – przy ``ANY`` ostrzeżenie wprost (§ 12.3).
AGE_POLICY_HELP = {
    AgePolicy.SAME_GROUP: (
        "Niepełnoletni piszą wyłącznie z niepełnoletnimi, pełnoletni – z pełnoletnimi. Przy samym "
        "roczniku osoba jest traktowana jako niepełnoletnia przez cały rok, w którym kończy 18 lat."
    ),
    AgePolicy.ANY: (
        "Uwaga: dorośli uczestnicy będą mogli rozmawiać 1:1 z niepełnoletnimi. Wybieraj świadomie – "
        "najlepiej razem z moderacją (premoderacja albo postmoderacja)."
    ),
}

REQUIRED_CSS_CLASS = "required"

#: Opisy trybów na ekranie ustawień. Słownik obok pola, bo koordynator wybiera tryb, czytając
#: skutki, a nie nazwy – „postmoderacja” sama nic nie mówi o tym, kto widzi treść.
PEER_MODE_HELP = {
    PeerMode.OFF: "Katalogu nie ma, nowych rozmów nie da się zacząć, istniejące są tylko do odczytu.",
    PeerMode.PRE: (
        "Każda wiadomość czeka na Twoją akceptację; odbiorca widzi ją dopiero po niej. Odrzucenie "
        "wymaga notatki, którą zobaczy nadawca."
    ),
    PeerMode.POST: (
        "Wiadomość dochodzi od razu, a Ty przeglądasz ją po fakcie – możesz ją ukryć albo oznaczyć "
        "jako przejrzaną."
    ),
    PeerMode.NONE: (
        "Wiadomość dochodzi od razu i nikt jej nie przegląda. Widzisz wyłącznie wiadomości zgłoszone "
        "przez odbiorcę – treść pozostałych rozmów jest dla Ciebie niedostępna."
    ),
}


class MessageForm(forms.Form):
    """Jedna wiadomość – jawna (``body``) albo szyfrowana (szyfrogram, IV i odciski kluczy).

    Wszystkie pola są tu **nieobowiązkowe**, a o tym, która postać jest dozwolona w danej rozmowie,
    rozstrzyga serwis (``apps.chat.services.send_participant_message``): jawna treść w rozmowie
    szyfrowanej i szyfrogram w jawnej są odrzucane tam, więc żadna droga zapisu nie ominie reguły.
    W rozmowie szyfrowanej szablon renderuje pole tekstowe **bez atrybutu** ``name`` (§ 11.3) – jeśli
    skrypt nie zadziała, jawna treść nie opuści przeglądarki.
    """

    required_css_class = REQUIRED_CSS_CLASS

    body = forms.CharField(
        label=gettext_lazy("Wiadomość"),
        max_length=MAX_BODY_LENGTH,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": MAX_BODY_LENGTH}),
    )
    ciphertext = forms.CharField(required=False, max_length=MAX_CIPHERTEXT_LENGTH, widget=forms.HiddenInput)
    iv = forms.CharField(required=False, max_length=32, widget=forms.HiddenInput)
    sender_fingerprint = forms.CharField(required=False, max_length=64, widget=forms.HiddenInput)
    recipient_fingerprint = forms.CharField(required=False, max_length=64, widget=forms.HiddenInput)


class ChatKeyForm(forms.Form):
    """Klucz tożsamości do rozmów szyfrowanych – pola wypełnia skrypt, hasła nie ma tu wcale.

    Pola hasła do wiadomości stoją w szablonie **bez** ``name``: hasło nie ma prawa trafić na serwer,
    także przez przypadek (skrypt nie zadziałał, a przeglądarka wysłała formularz sama).
    """

    public_key = forms.CharField(max_length=400, widget=forms.HiddenInput)
    wrapped_private_key = forms.CharField(max_length=1000, widget=forms.HiddenInput)
    kdf_salt = forms.CharField(max_length=64, widget=forms.HiddenInput)
    wrap_iv = forms.CharField(max_length=32, widget=forms.HiddenInput)
    kdf_iterations = forms.IntegerField(widget=forms.HiddenInput)
    replace = forms.BooleanField(required=False, widget=forms.HiddenInput)


class ReportForm(forms.Form):
    required_css_class = REQUIRED_CSS_CLASS

    message = forms.IntegerField(widget=forms.HiddenInput)
    reason = forms.CharField(
        label=gettext_lazy("Dlaczego zgłaszasz tę wiadomość"),
        max_length=MAX_REASON_LENGTH,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=gettext_lazy("Zgłoszenie widzi wyłącznie organizator."),
    )
    #: Kopia jawna wiadomości szyfrowanej – wpisuje ją skrypt po odszyfrowaniu (§ 11.3).
    reported_plaintext = forms.CharField(required=False, max_length=MAX_BODY_LENGTH, widget=forms.HiddenInput)


class ChatSettingsForm(forms.Form):
    """Ekran ustawień koordynatora."""

    enabled = forms.BooleanField(
        label="Wiadomości włączone",
        required=False,
        help_text=(
            "Wyłączenie ukrywa moduł uczestnikom (pozycja w menu znika, adresy odpowiadają „nie "
            "znaleziono”). Rozmowy zostają w bazie i wracają po ponownym włączeniu."
        ),
    )
    peer_mode = forms.ChoiceField(
        label="Rozmowy między uczestnikami",
        choices=PeerMode.choices,
        widget=forms.RadioSelect,
    )
    age_policy = forms.ChoiceField(
        label="Grupa wiekowa w rozmowach uczestników",
        choices=AgePolicy.choices,
        widget=forms.RadioSelect,
        initial=AgePolicy.SAME_GROUP,
        # Nieobowiązkowe w formularzu: brak wartości znaczy „bez zmian” (serwis bierze zapisaną).
        required=False,
    )
    daily_new_conversations = forms.IntegerField(
        label="Nowe rozmowy jednego uczestnika na dobę",
        min_value=MIN_DAILY_NEW_CONVERSATIONS,
        max_value=MAX_DAILY_NEW_CONVERSATIONS,
        initial=DEFAULT_DAILY_NEW_CONVERSATIONS,
        required=False,
        help_text=(
            "Okno kroczące 24 h; nie dotyczy odpowiedzi w istniejących rozmowach ani rozmowy z organizatorem."
        ),
    )
    e2e_enabled = forms.BooleanField(
        label="Szyfrowanie end-to-end nowych rozmów między uczestnikami",
        required=False,
        help_text=(
            "Tylko w trybie „bez moderacji”. Treści rozmów szyfrowanych nie zna serwer ani organizator – "
            "zobaczysz wyłącznie wiadomości zgłoszone, w postaci przekazanej przez zgłaszającego."
        ),
    )


class ReplyTemplateForm(forms.Form):
    """Szablon odpowiedzi koordynatora. ``{imie}`` podmienia się na imię uczestnika przy wstawianiu."""

    required_css_class = REQUIRED_CSS_CLASS

    title = forms.CharField(label="Nazwa", max_length=120)
    body = forms.CharField(
        label="Treść",
        max_length=MAX_BODY_LENGTH,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Znacznik {imie} zostanie zastąpiony imieniem uczestnika rozmowy.",
    )


class ChatPreferencesForm(forms.Form):
    """Sekcja „Wiadomości” na ekranie „Edycja danych”. Etykiety tłumaczone, jak przy forum."""

    discoverable = forms.BooleanField(
        label=gettext_lazy("Inni uczestnicy mogą mnie znaleźć i do mnie napisać"),
        required=False,
        help_text=gettext_lazy(
            "W katalogu widać wyłącznie imię, pierwszą literę nazwiska i województwo – nigdy adres "
            "e-mail, szkołę ani kod uczestnika."
        ),
    )
    email_on_message = forms.BooleanField(
        label=gettext_lazy("Wysyłaj mi e-mail, gdy dostanę nową wiadomość"),
        required=False,
        help_text=gettext_lazy(
            "List nie zawiera treści wiadomości – tylko informację, od kogo jest, i odnośnik."
        ),
    )

    def __init__(self, *args, participant: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        if not participant:
            del self.fields["discoverable"]
