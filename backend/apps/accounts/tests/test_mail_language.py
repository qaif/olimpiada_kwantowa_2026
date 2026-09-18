"""Język listu: konto odbiorcy, a gdy nic nie zapisał – konkurs, a nie serwer.

``language_for`` (``apps.accounts.preferences``) obsługuje listy składane **poza** żądaniem
adresata: ogłoszenie wyników (składa je koordynator dla tysiąca osób naraz) i nocne przypomnienie
o rozmowie (składa je zadanie w tle). Do etapu 2 znało jedno źródło – zapis na koncie; konto bez
zapisu dostawało język, który akurat obowiązywał składającemu. Dla konkursu prowadzonego po
angielsku znaczyłoby to list po polsku z serwisu, który uczestnik widział wyłącznie po angielsku
(``docs/UNIWERSALNY-ETAP-2.md`` § 1.6.3).

Ten plik pyta o obie odpowiedzi naraz i to jest jego sens:

- **Konkurs #1** (``default_language = "pl"``, konta bez zapisanego języka) dostaje listy polskie
  co do bajtu – także wtedy, gdy koordynator ogłaszający wyniki ma własny panel po angielsku.
  Tego samego pilnują testy niezmienności (``apps/tenancy/tests/test_invariants.py``,
  ``test_branding.py``); tutaj stoi drugi dowód, czytany ze skrzynki,
- konkurs z ``default_language = "en"`` dostaje listy angielskie, a konto z własnym wyborem
  wygrywa z konkursem – bo to jedyna z tych odpowiedzi, której ktoś udzielił świadomie.

Tematy stoją tu **literałami**, a nie importem leniwych napisów z modułów powiadomień: przedmiotem
testu jest zdanie, które wpadło do skrzynki, a ``str(gettext_lazy(...))`` odpowiedziałby w języku
akurat aktywnym – czyli powtórzyłby błąd zamiast go pokazać.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core import mail
from django.utils import translation

from apps.accounts.models import UserPreference
from apps.accounts.preferences import competition_language, language_for
from apps.accounts.tests.factories import UserFactory
from apps.competitions.video import send_interview_reminders
from apps.submissions.notifications import notify_results_published
from apps.tenancy.context import competition_context
from apps.tenancy.tests.golden import book_interview, build_golden, publish_results

pytestmark = pytest.mark.django_db

#: Temat listu o ogłoszeniu wyników w obu językach instalacji. Wersja angielska jest tą, którą ma
#: skompilowany katalog (``locale/en/LC_MESSAGES``) – test sprawdza więc drogę od konkursu do
#: przekładu, a nie samo ustawienie zmiennej.
RESULTS_SUBJECT_PL = "Wyniki etapu ogłoszone – Olimpiada Kwantowa"
RESULTS_SUBJECT_EN = "Stage results announced – Quantum Olympiad"

#: Ostatni wiersz stopki listu. Jest w każdym liście i w obu językach, więc nadaje się na próbkę
#: języka treści tam, gdzie temat języka nie niesie (przypomnienie o rozmowie ma w temacie nazwę
#: etapu, czyli daną organizatora).
AUTOMATIC_NOTE_PL = "Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać."
AUTOMATIC_NOTE_EN = "This message was sent automatically; please do not reply to it."


def _in_english(competition):
    """Ten sam konkurs, tyle że prowadzony po angielsku. Zapis, bo listy czytają go z bazy."""
    competition.default_language = "en"
    competition.save(update_fields=["default_language"])
    return competition


# --- rozstrzyganie języka --------------------------------------------------------------------------


def test_the_account_language_wins_over_the_competition(competition):
    """Wybór człowieka bije ustawienie organizatora – i to jest cała hierarchia tej funkcji."""
    user = UserFactory()
    UserPreference.objects.create(user=user, language="en")

    with language_for(user, competition):
        assert translation.get_language() == "en"


def test_the_competition_language_applies_to_an_account_without_a_preference(other_competition):
    user = UserFactory()

    with language_for(user, _in_english(other_competition)):
        assert translation.get_language() == "en"


def test_competition_one_keeps_polish_for_an_account_without_a_preference(competition):
    """Konkurs #1 po polsku, choć składający ma panel po angielsku – to jest warunek ciągłości.

    Przed etapem 2 wygrywał tu język składającego, bo blok bez zapisanego wyboru nie robił nic.
    Zmiana idzie więc w stronę **większej** polskości listów Olimpiady Kwantowej, a nie mniejszej.
    """
    user = UserFactory()

    with translation.override("en"), language_for(user, competition):
        assert translation.get_language() == "pl"


def test_the_competition_of_the_context_applies_when_the_caller_passes_none(other_competition):
    """Kolejność źródeł jest ta sama, co w ``mail_competition``: wprost, a potem kontekst.

    Dzięki temu marka w temacie, nadawca w kopercie i język treści jednego listu pochodzą z jednego
    konkursu – nawet u wołającego, który konkursu nie podaje, bo związał kontekst.
    """
    user = UserFactory()

    with competition_context(_in_english(other_competition)), language_for(user):
        assert translation.get_language() == "en"


def test_an_explicit_competition_wins_over_the_context(competition, other_competition):
    """Kontekst wskazuje konkurs **cudzy** – list ma iść w języku tego, o którym jest."""
    user = UserFactory()

    with competition_context(competition), language_for(user, _in_english(other_competition)):
        assert translation.get_language() == "en"


def test_the_old_signature_still_works(competition):  # noqa: ARG001 - konkurs wiąże autouse kontekstu
    """Wołający sprzed etapu 2 podaje sam odbiorcę i nadal dostaje sensowną odpowiedź."""
    with language_for(UserFactory()):
        assert translation.get_language() == "pl"


def test_without_any_competition_the_installation_language_applies(unbound_competition):  # noqa: ARG001
    """Ostatni odwrót to ``settings.LANGUAGE_CODE``, czyli dokładnie to, co obowiązywało tu wcześniej."""
    user = UserFactory()

    with translation.override("en"), language_for(user):
        assert translation.get_language() == settings.LANGUAGE_CODE


def test_a_language_the_installation_does_not_have_is_ignored(other_competition):
    """Kod spoza ``settings.LANGUAGES`` jest brakiem odpowiedzi, a nie odpowiedzią.

    Katalogu tłumaczeń dla niego nie ma, więc aktywowanie go dałoby napisy źródłowe podane jako
    przekład – czyli polszczyznę udającą niemiecki.
    """
    other_competition.default_language = "de"
    other_competition.save(update_fields=["default_language"])
    user = UserFactory()

    assert competition_language(other_competition) == ""
    with language_for(user, other_competition):
        assert translation.get_language() == settings.LANGUAGE_CODE


def test_the_previous_language_comes_back_after_the_block(competition):
    user = UserFactory()

    with translation.override("en"):
        with language_for(user, competition):
            assert translation.get_language() == "pl"
        assert translation.get_language() == "en"


# --- listy ------------------------------------------------------------------------------------------


def test_the_results_letters_of_competition_one_stay_polish(competition, django_capture_on_commit_callbacks):
    """Ogłoszenie wyników z panelu koordynatora przestawionego na angielski – listy po polsku."""
    golden = build_golden(competition)
    publication = publish_results(golden)
    mail.outbox.clear()

    with translation.override("en"), django_capture_on_commit_callbacks(execute=True):
        sent = notify_results_published(publication)

    assert sent == len(golden.participants)
    assert {letter.subject for letter in mail.outbox} == {RESULTS_SUBJECT_PL}
    assert {letter.body.splitlines()[-1] for letter in mail.outbox} == {AUTOMATIC_NOTE_PL}


def test_the_results_letters_follow_the_language_of_the_competition(
    competition, django_capture_on_commit_callbacks
):
    """Ten sam przebieg w konkursie prowadzonym po angielsku – temat i stopka po angielsku."""
    golden = build_golden(_in_english(competition))
    publication = publish_results(golden)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        notify_results_published(publication)

    assert {letter.subject for letter in mail.outbox} == {RESULTS_SUBJECT_EN}
    assert {letter.body.splitlines()[-1] for letter in mail.outbox} == {AUTOMATIC_NOTE_EN}


def test_the_account_language_still_decides_inside_the_loop(competition, django_capture_on_commit_callbacks):
    """Jedno ogłoszenie, dwa języki: konkurs po angielsku, jeden uczestnik z polskim na koncie."""
    golden = build_golden(_in_english(competition))
    publication = publish_results(golden)
    chosen = golden.participants[0].user
    UserPreference.objects.create(user=chosen, language="pl")
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        notify_results_published(publication)

    subjects = {letter.to[0]: letter.subject for letter in mail.outbox}
    assert subjects[chosen.email] == RESULTS_SUBJECT_PL
    assert set(subjects.values()) == {RESULTS_SUBJECT_PL, RESULTS_SUBJECT_EN}


def test_the_interview_reminder_of_competition_one_stays_polish(
    competition, django_capture_on_commit_callbacks
):
    """Przypomnienie składa zadanie w tle, więc żaden język nie jest tam „naturalnie” aktywny."""
    golden = build_golden(competition)
    with django_capture_on_commit_callbacks(execute=True):
        book_interview(golden)
    mail.outbox.clear()

    with translation.override("en"), django_capture_on_commit_callbacks(execute=True):
        assert send_interview_reminders(competition=competition) == 1

    assert mail.outbox[-1].body.splitlines()[-1] == AUTOMATIC_NOTE_PL


def test_the_interview_reminder_follows_the_language_of_the_competition(
    competition, django_capture_on_commit_callbacks
):
    golden = build_golden(_in_english(competition))
    with django_capture_on_commit_callbacks(execute=True):
        book_interview(golden)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        assert send_interview_reminders(competition=competition) == 1

    assert mail.outbox[-1].body.splitlines()[-1] == AUTOMATIC_NOTE_EN
