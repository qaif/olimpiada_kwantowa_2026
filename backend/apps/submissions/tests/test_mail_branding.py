"""Marka i koperta czterech powiadomień dla uczestnika (``apps/submissions/notifications.py``).

Cztery listy: przyjęcie pracy, plik odrzucony przez skan, ogłoszenie wyników i decyzja w sprawie
reklamacji. Wszystkie idą tą samą drogą, więc test pyta o jedną rzecz: **czyj** konkurs rozstrzyga
o temacie, podpisie i nadawcy. Odpowiedź ma być jedna – konkurs **pracy**
(``Submission.competition``), a nie konkurs kontekstu żądania.

Różnica jest widoczna dopiero przy dwóch konkursach i dlatego kontekst jest tu wiązany celowo
z konkursem **cudzym**: koordynator, który ogłasza wyniki spod własnej domeny, nie może wysłać
uczestnikowi listu podpisanego marką sąsiada – a dokładnie to zrobiłby odczyt z kontekstu.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core import mail

from apps.submissions.models import Submission
from apps.submissions.notifications import (
    RESULTS_PUBLISHED_SUBJECT,
    SUBMISSION_RECEIVED_SUBJECT,
    notify_results_published,
    notify_submission_received,
)
from apps.tenancy.context import competition_context
from apps.tenancy.tests.golden import build_golden, publish_results

pytestmark = pytest.mark.django_db

OTHER_NAME = "Olimpiada Fizyczna"
OTHER_SENDER = "listy@fizyczna.test"

SIGNATURE_LINES = (
    "--",
    "Olimpiada Kwantowa",
    "Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać.",
)


@pytest.fixture
def branded(other_competition):
    """Konkurs #2 z włączoną marką w poczcie, własnym nadawcą i własnym drzewem stron.

    Strona główna jest tu warunkiem zbudowania złotej fikstury (``build_golden`` dokłada sekcje do
    ``HomePage``), a nie przedmiotem testu: fikstura ``other_competition`` daje witrynie zwykłą
    stronę-korzeń, więc podmieniamy ją tak, jak robi to ``apps/cms/tests/test_competition_scope.py``.
    """
    from wagtail.models import Page

    from apps.cms.models import HomePage

    tree_root = Page.objects.filter(depth=1).order_by("path").first()
    home = tree_root.add_child(instance=HomePage(title=OTHER_NAME, slug="olimpiada-fizyczna"))
    site = other_competition.site
    site.root_page = home
    site.save()
    other_competition.name = OTHER_NAME
    other_competition.short_name = OTHER_NAME
    other_competition.genitive_name = "Olimpiady Fizycznej"
    other_competition.locative_name = "Olimpiadzie Fizycznej"
    other_competition.from_email = OTHER_SENDER
    other_competition.feature_flags = {"competition_branding_in_mail": True}
    other_competition.save()
    return other_competition


def _signature_of(message: str) -> tuple[str, ...]:
    return tuple(message.splitlines()[-3:])


def test_the_receipt_of_competition_one_is_unchanged(
    competition, settings, django_capture_on_commit_callbacks
):
    """Konkurs #1 bez flagi: temat, podpis i nadawca dokładnie takie, jak przed etapem 2."""
    golden = build_golden(competition)
    submission = Submission.objects.filter(entry__participant__in=golden.participants).first()
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        # ``submission_file`` jest tu atrapą, bo list czyta z niego wyłącznie sumę kontrolną –
        # a przedmiotem testu jest koperta i podpis, nie zawartość pliku.
        notify_submission_received(submission, SimpleNamespace(sha256="0" * 64))

    letter = mail.outbox[-1]
    assert letter.subject == str(SUBMISSION_RECEIVED_SUBJECT)
    assert letter.from_email == settings.DEFAULT_FROM_EMAIL
    assert _signature_of(letter.body) == SIGNATURE_LINES


def test_the_receipt_follows_the_competition_of_the_work_not_of_the_context(
    competition, branded, django_capture_on_commit_callbacks
):
    golden = build_golden(branded)
    submission = Submission.objects.filter(entry__participant__in=golden.participants).first()
    mail.outbox.clear()

    # Kontekst celowo wskazuje **cudzy** konkurs – wynik ma nie zależeć od tego ani o znak.
    with competition_context(competition), django_capture_on_commit_callbacks(execute=True):
        notify_submission_received(submission, SimpleNamespace(sha256="0" * 64))

    letter = mail.outbox[-1]
    assert letter.subject == f"Rozwiązanie przyjęte – {OTHER_NAME}"
    assert letter.from_email == OTHER_SENDER
    assert _signature_of(letter.body) == ("--", OTHER_NAME, SIGNATURE_LINES[2])


def test_results_letters_go_to_every_participant_with_one_brand(
    competition, branded, django_capture_on_commit_callbacks
):
    """Ogłoszenie wyników wysyła listy w pętli – marka i nadawca mają być w każdym ten sam.

    Temat składa się **wewnątrz** bloku języka odbiorcy, więc ten test pilnuje przy okazji, że
    wyniesienie go przed pętlę (tańsze o kilka operacji na napisach) nie wróci tylnymi drzwiami.
    """
    golden = build_golden(branded)
    publication = publish_results(golden)
    mail.outbox.clear()

    with competition_context(competition), django_capture_on_commit_callbacks(execute=True):
        sent = notify_results_published(publication)

    assert sent == len(golden.participants)
    # Liczba listów **w skrzynce**, a nie tylko wynik serwisu: zbiory niżej porównują to, co
    # zastały, więc bez tej asercji pusty outbox byłby dla nich stanem nie do odróżnienia od błędu.
    assert len(mail.outbox) == len(golden.participants)
    assert {letter.subject for letter in mail.outbox} == {f"Wyniki etapu ogłoszone – {OTHER_NAME}"}
    assert {letter.from_email for letter in mail.outbox} == {OTHER_SENDER}
    assert {_signature_of(letter.body) for letter in mail.outbox} == {("--", OTHER_NAME, SIGNATURE_LINES[2])}


def test_results_letters_of_competition_one_are_unchanged(
    competition, settings, django_capture_on_commit_callbacks
):
    golden = build_golden(competition)
    publication = publish_results(golden)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        notify_results_published(publication)

    assert len(mail.outbox) == len(golden.participants)
    assert {letter.subject for letter in mail.outbox} == {str(RESULTS_PUBLISHED_SUBJECT)}
    assert {letter.from_email for letter in mail.outbox} == {settings.DEFAULT_FROM_EMAIL}
    assert {_signature_of(letter.body) for letter in mail.outbox} == {SIGNATURE_LINES}
