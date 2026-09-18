"""Adres organizatora w komunikatach CAPTCHY – z konkursu, z odwrotem na dzisiejsze zdanie.

Te dwa komunikaty czyta człowiek, który **nie może się zarejestrować**: albo nie widzi obrazka,
albo wpadł w pułapkę antyspamową. Adres w nich jest jedyną drogą, jaka mu zostaje – i dlatego ma
być adresem organizatora **tego** konkursu, a nie cudzego (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.4,
zadanie T14). Zdanie wokół adresu zostaje treścią w kodzie; konfiguracją jest sam adres.

Konkurs bierze się tu ze zmiennej kontekstowej, a nie z argumentu formularza, bo formularze
rejestracji dostają ten mixin przez dziedziczenie i nie przekazują mu niczego – ustawia ją
``CompetitionMiddleware`` na każdym żądaniu (a w testach z bazą fikstura ``_bind_competition``).
Niezmienność komunikatu dla Konkursu #1 zamraża osobno ``apps/tenancy/tests/test_branding.py``.
"""

import pytest

from apps.tenancy.branding import BRANDING_FLAG
from apps.tenancy.models import Competition
from apps.web.captcha import (
    CAPTCHA_HELP_TEXT,
    REJECTED_MESSAGE,
    captcha_help_text,
    rejected_message,
)
from apps.web.forms import ParticipantRegisterForm

from .conftest import captcha_fields

# Baza dla **całego** pliku, choć część testów jej nie potrzebuje: autouse'owa fikstura tego
# katalogu (``_reset_panel_counters``) sięga do bazy sama, więc test bez ``django_db`` wywraca się
# w przygotowaniu, zanim dojdzie do czegokolwiek własnego.
pytestmark = pytest.mark.django_db

CONTACT = "kontakt@olimpiadajuniorow.pl"


def competition_with(*, branded: bool, contact_email: str = CONTACT) -> Competition:
    """Konkurs w pamięci – moduł marki nie zadaje zapytań, więc baza nie jest tu do niczego."""
    return Competition(
        name="Olimpiada Matematyczna Juniorów",
        short_name="Olimpiada Juniorów",
        contact_email=contact_email,
        feature_flags={BRANDING_FLAG: True} if branded else {},
    )


# --- odwroty ---------------------------------------------------------------------------------------


def test_without_a_competition_both_messages_are_todays():
    """Kreator ``/setup/`` używa tego samego mixinu na instalacji, w której konkursu jeszcze nie ma."""
    assert rejected_message() == REJECTED_MESSAGE
    assert captcha_help_text() == CAPTCHA_HELP_TEXT


def test_without_the_flag_the_address_stays_todays():
    """Konkurs #1 ma wypełniony ``contact_email``, ale flagi marki nie ma – i to rozstrzyga."""
    competition = competition_with(branded=False)

    assert rejected_message(competition) == REJECTED_MESSAGE
    assert captcha_help_text(competition) == CAPTCHA_HELP_TEXT


def test_empty_contact_email_keeps_todays_message():
    """„Napisz do ” bez adresu byłoby gorsze niż dzisiejsze zdanie – pole jest nieobowiązkowe."""
    competition = competition_with(branded=True, contact_email="")

    assert rejected_message(competition) == REJECTED_MESSAGE
    assert captcha_help_text(competition) == CAPTCHA_HELP_TEXT


# --- flaga włączona ----------------------------------------------------------------------------------


def test_flag_puts_the_competition_address_in_both_messages():
    competition = competition_with(branded=True)

    assert rejected_message(competition) == (
        "Nie udało się potwierdzić, że formularz wypełnił człowiek. Wyślij go jeszcze raz, "
        f"a jeśli błąd się powtarza – napisz do {CONTACT}."
    )
    assert captcha_help_text(competition) == (
        "Wpisz wynik działania z obrazka. Nie widzisz obrazka (czytnik ekranu, brak grafiki)? "
        f"Napisz do {CONTACT} – konto założymy ręcznie."
    )


# --- przez formularz ------------------------------------------------------------------------------------


def test_registration_form_takes_the_address_from_the_competition(competition):
    """Podpowiedź pola i komunikat odmowy składa formularz – ten sam, który widzi uczestnik.

    Przedmiotem jest droga „kontekst żądania → pole formularza”: sama funkcja jest sprawdzona
    wyżej, a to, czego nikt inny nie sprawdzi, to czy konkurs w ogóle do niej dojechał.
    """
    competition.feature_flags = {**(competition.feature_flags or {}), BRANDING_FLAG: True}
    competition.contact_email = CONTACT
    competition.save(update_fields=["feature_flags", "contact_email"])

    form = ParticipantRegisterForm()

    assert CONTACT in str(form.fields["captcha"].help_text)


def test_rejected_submission_answers_with_the_competition_address(competition):
    """Pułapka odrzuca **jednym** komunikatem – i to w nim stoi adres, pod który można napisać."""
    competition.feature_flags = {**(competition.feature_flags or {}), BRANDING_FLAG: True}
    competition.contact_email = CONTACT
    competition.save(update_fields=["feature_flags", "contact_email"])

    form = ParticipantRegisterForm({**captcha_fields(honeypot="https://spam.example")})

    assert form.is_valid() is False
    assert any(CONTACT in error for error in form.errors["__all__"])
