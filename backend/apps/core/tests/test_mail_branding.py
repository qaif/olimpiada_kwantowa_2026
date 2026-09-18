"""Koperta listu: nadawca z konkursu zamiast nadawcy instalacji wpisanego na sztywno.

Do etapu 2 ``send_mail_task`` wpisywał ``settings.DEFAULT_FROM_EMAIL`` wprost, więc
``Competition.from_email`` – pole wypełniane migracją ``tenancy.0002`` i edytowalne w panelu –
nie miało ani jednego czytelnika (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.1). Ten plik pilnuje dwóch
rzeczy naraz: że pole jest już czytane i że brak wartości nadal znaczy „nadawca instalacji”.

Nadawca **nie** stoi za flagą ``competition_branding_in_mail`` i to jest świadome: marka jest
napisem dla człowieka, a koperta jest konfiguracją instalacji, którą organizator wypełnił dawno
temu. Gdyby szła za flagą, wyłączenie marki zabierałoby też adres nadawcy – czyli jedno ustawienie
sterowałoby dwiema niezależnymi rzeczami.
"""

from __future__ import annotations

import pytest
from django.core import mail

from apps.core.tasks import mail_from, send_mail_task

pytestmark = pytest.mark.django_db


def test_the_task_falls_back_to_the_installation_sender(settings):
    """Wywołanie sprzed etapu 2 (trzy argumenty) wychodzi od nadawcy instalacji – jak dotąd."""
    mail.outbox.clear()

    send_mail_task("Temat", "Treść", ["ktos@example.invalid"])

    assert mail.outbox[-1].from_email == settings.DEFAULT_FROM_EMAIL


def test_the_task_uses_the_sender_it_was_given():
    """Czwarty argument jest jedyną drogą, którą nadawca konkursu dociera do workera.

    Zadanie dostaje **napis**, a nie konkurs: worker nie czyta bazy i nie zależy od tego, czy
    wiersz konkursu istnieje jeszcze w chwili wysyłki (docstring ``send_mail_task``).
    """
    mail.outbox.clear()

    send_mail_task("Temat", "Treść", ["ktos@example.invalid"], "listy@fizyczna.test")

    assert mail.outbox[-1].from_email == "listy@fizyczna.test"


def test_competition_one_keeps_the_installation_sender(competition, settings):
    """Konkurs #1 ma w kolumnie dokładnie ``DEFAULT_FROM_EMAIL``, więc nic się dla niego nie zmienia.

    To jest ten sam fakt, co ``test_competition_one_keeps_the_installation_mail_settings``
    w ``apps/tenancy/tests/test_invariants.py``, tylko widziany od strony wysyłki: nawet gdyby
    ktoś wpisał tam coś innego, wynik ``mail_from`` ma się zgadzać z nagłówkiem ``From:``, który
    uczestnicy dostawali dotąd – zmiana nadawcy bywa czytana przez filtry jak nowy korespondent.
    """
    assert mail_from(competition) == settings.DEFAULT_FROM_EMAIL


def test_a_competition_without_its_own_sender_means_the_installation(other_competition):
    """Puste pole znaczy „weź ustawienie instalacji”, a nie „wyślij bez nadawcy”."""
    assert other_competition.from_email == ""
    assert mail_from(other_competition) is None
    assert mail_from(None) is None


def test_the_sender_of_the_competition_wins(other_competition):
    other_competition.from_email = " listy@fizyczna.test "
    other_competition.save(update_fields=["from_email"])

    assert mail_from(other_competition) == "listy@fizyczna.test"
