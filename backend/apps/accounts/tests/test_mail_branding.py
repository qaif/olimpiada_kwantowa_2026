"""Marka i koperta listów kont: aktywacja, zmiana adresu, zgoda opiekuna, zaproszenie do komitetu.

Sześć listów z tabeli § 1.1.1 (``docs/UNIWERSALNY-ETAP-2.md``) powstaje w tej aplikacji. Ten plik
sprawdza **obie** odpowiedzi naraz i to jest jego sens:

- konkurs bez flagi ``competition_branding_in_mail`` – czyli Konkurs #1 – dostaje dokładnie ten
  temat, ten podpis i tego nadawcę, co przed etapem 2. Tego samego pilnują testy niezmienności
  (``apps/tenancy/tests/test_invariants.py``); tutaj stoi drugi dowód, czytany z ``mail.outbox``,
- konkurs z włączoną flagą dostaje **własną** nazwę w temacie i w podpisie, w tej samej formie
  gramatycznej, co dotychczasowy literał (zaproszenie odmienia nazwę w dopełniaczu).

Czego tu nie ma: asercji na treść listów poza podpisem. T9 zamienia tematy i podpisy, a nie zdania
w środku – te zostają literałem i mają własnych strażników.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.activation import (
    ACTIVATION_SUBJECT,
    EMAIL_CHANGE_SUBJECT,
    EMAIL_CHANGED_NOTICE_SUBJECT,
    queue_mail,
    send_activation_email,
    send_email_change_confirmation,
    send_email_changed_notice,
)
from apps.accounts.guardian import GUARDIAN_CONFIRMED_SUBJECT, GUARDIAN_SUBJECT
from apps.accounts.services import INVITATION_SUBJECT, send_invitations
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.tenancy.context import competition_context

pytestmark = pytest.mark.django_db

#: Nazwa, odmiana i nadawca drugiego konkursu. Odmiana jest **daną** (``Competition.genitive_name``),
#: a nie regułą fleksyjną w kodzie – patrz ``apps.tenancy.branding.substitutions``.
OTHER_NAME = "Olimpiada Fizyczna"
OTHER_GENITIVE = "Olimpiady Fizycznej"
OTHER_SENDER = "listy@fizyczna.test"

#: Trzy wiersze stopki, tak jak widzi je odbiorca. Powtórzone tutaj zamiast importu
#: z ``test_invariants``: ten test ma pytać, czy podpis brzmi tak, jak brzmiał, a nie czy dwa pliki
#: czytają tę samą stałą.
SIGNATURE_LINES = (
    "--",
    "Olimpiada Kwantowa",
    "Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać.",
)


@pytest.fixture
def branded(other_competition):
    """Konkurs #2 z **włączoną** marką w poczcie i własnym nadawcą.

    Flagę ustawiamy wprost w ``feature_flags``, a nie przez komendę: przedmiotem testu jest to, co
    wychodzi do człowieka po jej włączeniu, a nie droga, którą organizator ją włącza.
    """
    other_competition.name = OTHER_NAME
    other_competition.short_name = OTHER_NAME
    other_competition.genitive_name = OTHER_GENITIVE
    other_competition.locative_name = "Olimpiadzie Fizycznej"
    other_competition.from_email = OTHER_SENDER
    other_competition.feature_flags = {"competition_branding_in_mail": True}
    other_competition.save()
    return other_competition


def _signature_of(message: str) -> tuple[str, ...]:
    return tuple(message.splitlines()[-3:])


# --- koperta ---------------------------------------------------------------------------------------


def test_queue_mail_without_a_competition_sends_from_the_installation(
    settings, django_capture_on_commit_callbacks
):
    """Wołający sprzed etapu 2 (bez ``competition``) zachowuje się dokładnie tak, jak dotąd."""
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        queue_mail("Temat", "Treść", "ktos@example.invalid")

    assert mail.outbox[-1].from_email == settings.DEFAULT_FROM_EMAIL


def test_queue_mail_sends_from_the_competition(branded, django_capture_on_commit_callbacks):
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        queue_mail("Temat", "Treść", "ktos@example.invalid", competition=branded)

    assert mail.outbox[-1].from_email == OTHER_SENDER


def test_queue_mail_reads_the_competition_from_the_context(branded, django_capture_on_commit_callbacks):
    """Konkurs niepodany wprost bierze się z kontekstu – tą samą drogą, co adres w linku.

    Dzięki temu wołający, których T9 nie dotyka (``apps.accounts.bulk_registration``), wysyłają
    listy od nadawcy swojego konkursu bez zmiany ani jednej linijki u siebie.
    """
    mail.outbox.clear()

    with competition_context(branded), django_capture_on_commit_callbacks(execute=True):
        queue_mail("Temat", "Treść", "ktos@example.invalid")

    assert mail.outbox[-1].from_email == OTHER_SENDER


# --- aktywacja i zmiana adresu ---------------------------------------------------------------------


def test_the_activation_letter_of_competition_one_is_unchanged(
    competition, settings, django_capture_on_commit_callbacks
):
    user = UserFactory(email="kandydat@example.invalid")
    mail.outbox.clear()

    with competition_context(competition), django_capture_on_commit_callbacks(execute=True):
        send_activation_email(user)

    letter = mail.outbox[-1]
    assert letter.subject == str(ACTIVATION_SUBJECT) == "Aktywuj konto – Olimpiada Kwantowa"
    assert letter.from_email == settings.DEFAULT_FROM_EMAIL
    assert _signature_of(letter.body) == SIGNATURE_LINES


def test_the_activation_letter_carries_the_brand_of_a_branded_competition(
    branded, django_capture_on_commit_callbacks
):
    user = UserFactory(email="kandydat2@example.invalid")
    mail.outbox.clear()

    with competition_context(branded), django_capture_on_commit_callbacks(execute=True):
        send_activation_email(user)

    letter = mail.outbox[-1]
    assert letter.subject == f"Aktywuj konto – {OTHER_NAME}"
    assert letter.from_email == OTHER_SENDER
    assert _signature_of(letter.body) == ("--", OTHER_NAME, SIGNATURE_LINES[2])


@pytest.mark.parametrize(
    ("send", "fallback", "branded_subject"),
    [
        (
            lambda user: send_email_change_confirmation(user, "nowy@example.invalid"),
            EMAIL_CHANGE_SUBJECT,
            f"Potwierdź nowy adres e-mail – {OTHER_NAME}",
        ),
        (
            lambda user: send_email_changed_notice(user.email, "nowy@example.invalid"),
            EMAIL_CHANGED_NOTICE_SUBJECT,
            f"Adres e-mail konta został zmieniony – {OTHER_NAME}",
        ),
    ],
)
def test_the_letters_about_the_address_follow_the_same_rule(
    competition, branded, send, fallback, branded_subject, django_capture_on_commit_callbacks
):
    """Dwa listy o adresie konta: bez flagi dzisiejszy temat, z flagą nazwa konkursu."""
    user = UserFactory()
    mail.outbox.clear()

    with competition_context(competition), django_capture_on_commit_callbacks(execute=True):
        send(user)
    assert mail.outbox[-1].subject == str(fallback)

    with competition_context(branded), django_capture_on_commit_callbacks(execute=True):
        send(user)
    assert mail.outbox[-1].subject == branded_subject


# --- zgoda opiekuna ---------------------------------------------------------------------------------


def _minor(competition):
    """Uczestnik niepełnoletni – tylko od takiego zbieramy zgodę opiekuna."""
    return ParticipantFactory(competition=competition, birth_year=timezone.now().year - 15)


@pytest.mark.parametrize("branded_run", [False, True])
def test_the_guardian_letters_follow_the_competition_of_the_participant(
    competition, branded, branded_run, settings, django_capture_on_commit_callbacks
):
    """Konkurs listu bierze się z uczestnika, a nie z kontekstu – zgoda dotyczy **jego** startu.

    Kontekst wiążemy celowo z **drugim** konkursem, żeby było widać, że nie on rozstrzyga: opiekun
    ma dostać list o tej olimpiadzie, na którą zapisało się dziecko.
    """
    from apps.accounts.guardian import confirm_consent, request_consent

    subject_competition = branded if branded_run else competition
    context_competition = competition if branded_run else branded
    participant = _minor(subject_competition)
    mail.outbox.clear()

    with competition_context(context_competition), django_capture_on_commit_callbacks(execute=True):
        request_consent(participant, "opiekun@example.invalid")
    request_letter = mail.outbox[-1]

    with competition_context(context_competition), django_capture_on_commit_callbacks(execute=True):
        confirm_consent(participant)
    confirmation = mail.outbox[-1]

    if branded_run:
        assert request_letter.subject == f"Prośba o zgodę opiekuna – {OTHER_NAME}"
        assert confirmation.subject == f"Zgoda opiekuna została potwierdzona – {OTHER_NAME}"
        assert request_letter.from_email == OTHER_SENDER
        assert _signature_of(request_letter.body) == ("--", OTHER_NAME, SIGNATURE_LINES[2])
    else:
        assert request_letter.subject == str(GUARDIAN_SUBJECT)
        assert confirmation.subject == str(GUARDIAN_CONFIRMED_SUBJECT)
        assert request_letter.from_email == settings.DEFAULT_FROM_EMAIL
        assert _signature_of(request_letter.body) == SIGNATURE_LINES


# --- zaproszenie do komitetu -------------------------------------------------------------------------


@pytest.mark.parametrize("branded_run", [False, True])
def test_the_invitation_declines_the_name_of_the_competition(
    competition, branded, branded_run, settings, django_capture_on_commit_callbacks
):
    """Jedyny z szesnastu tematów z odmianą: „Zaproszenie do komitetu **Olimpiady Kwantowej**”.

    Dopełniacz jest w temacie od początku, więc wzorzec sięga po ``genitive_name`` konkursu – gdyby
    sięgnął po mianownik, drugi organizator dostałby w skrzynce zdanie niegramatyczne.
    """
    letter_competition = branded if branded_run else competition
    # Koordynator jest kontem platformy, a nie profilem konkursu – konkurs kodu bierze się
    # z kontekstu (``create_invitation`` → ``default_competition``), i to on ma stać w temacie.
    coordinator = CoordinatorFactory()
    mail.outbox.clear()

    with competition_context(letter_competition), django_capture_on_commit_callbacks(execute=True):
        send_invitations(coordinator, ["recenzent@example.invalid"])

    letter = mail.outbox[-1]
    if branded_run:
        assert letter.subject == f"Zaproszenie do komitetu {OTHER_GENITIVE}"
        assert letter.from_email == OTHER_SENDER
        assert _signature_of(letter.body) == ("--", OTHER_NAME, SIGNATURE_LINES[2])
    else:
        assert letter.subject == INVITATION_SUBJECT == "Zaproszenie do komitetu Olimpiady Kwantowej"
        assert letter.from_email == settings.DEFAULT_FROM_EMAIL
        assert _signature_of(letter.body) == SIGNATURE_LINES
