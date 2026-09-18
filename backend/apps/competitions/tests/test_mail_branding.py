"""Koperta i podpis dwóch listów o rozmowie kwalifikacyjnej.

Te dwa listy są w całym etapie 2 wyjątkiem i dlatego mają własny plik: ich **tematy nie niosą
marki** ani przed zmianą, ani po niej. W temacie stoi nazwa etapu („Termin rozmowy kwalifikacyjnej:
Zawody okręgowe”), czyli dana organizatora, a nie napis w kodzie – dokładanie tam nazwy konkursu
przy włączonej fladze byłoby dopisaniem zdania, którego nikt nie zamówił
(``docs/UNIWERSALNY-ETAP-2.md`` § 0.1).

Marka tych listów idzie więc dwiema innymi drogami i obu pilnuje ten plik: podpisem (tylko
przypomnienie – potwierdzenie terminu podpisu nie ma i nie miało) oraz nadawcą, który od tej zmiany
jest nadawcą konkursu, a nie instalacji.
"""

from __future__ import annotations

import pytest
from django.core import mail

from apps.competitions.video import send_interview_reminders
from apps.tenancy.context import competition_context
from apps.tenancy.tests.golden import book_interview, build_golden

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


def test_the_interview_letters_of_competition_one_are_unchanged(
    competition, settings, django_capture_on_commit_callbacks
):
    golden = build_golden(competition)
    stage_name = golden.district.display_name
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        book_interview(golden)
    confirmation = mail.outbox[-1]

    with django_capture_on_commit_callbacks(execute=True):
        assert send_interview_reminders(competition=competition) == 1
    reminder = mail.outbox[-1]

    assert confirmation.subject == f"Termin rozmowy kwalifikacyjnej: {stage_name}"
    assert reminder.subject == f"Jutro rozmowa kwalifikacyjna: {stage_name}"
    assert confirmation.from_email == reminder.from_email == settings.DEFAULT_FROM_EMAIL
    assert _signature_of(reminder.body) == SIGNATURE_LINES


def test_the_interview_letters_keep_the_stage_in_the_subject_and_take_the_brand_elsewhere(
    branded, django_capture_on_commit_callbacks
):
    """Z włączoną flagą temat zostaje **bez zmian** – marka wchodzi do podpisu i do nadawcy."""
    golden = build_golden(branded)
    stage_name = golden.district.display_name
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        book_interview(golden)
    confirmation = mail.outbox[-1]

    with django_capture_on_commit_callbacks(execute=True):
        assert send_interview_reminders(competition=branded) == 1
    reminder = mail.outbox[-1]

    assert confirmation.subject == f"Termin rozmowy kwalifikacyjnej: {stage_name}"
    assert reminder.subject == f"Jutro rozmowa kwalifikacyjna: {stage_name}"
    assert confirmation.from_email == reminder.from_email == OTHER_SENDER
    assert _signature_of(reminder.body) == ("--", OTHER_NAME, SIGNATURE_LINES[2])


def test_the_reminder_called_without_an_argument_reads_the_competition_from_the_booking(
    branded, django_capture_on_commit_callbacks
):
    """Wołający, który zawęził przebieg **kontekstem**, a nie argumentem, dostaje ten sam list.

    Zbiór zapisów zawęża wtedy ``scope_to_competition`` (czyta kontekst), ale argument funkcji
    zostaje pusty – i to jest jedyna droga, którą marka mogłaby po cichu zniknąć z listu. Konkurs
    bierze się więc z **etapu zapisu**: uczestnik ma dostać list podpisany tą olimpiadą, na której
    rozmowę jest zapisany, a nie tą, którą akurat ktoś związał z wątkiem.
    """
    golden = build_golden(branded)
    with django_capture_on_commit_callbacks(execute=True):
        book_interview(golden)
    mail.outbox.clear()

    with competition_context(branded), django_capture_on_commit_callbacks(execute=True):
        assert send_interview_reminders() == 1

    assert mail.outbox[-1].from_email == OTHER_SENDER
    assert _signature_of(mail.outbox[-1].body) == ("--", OTHER_NAME, SIGNATURE_LINES[2])
