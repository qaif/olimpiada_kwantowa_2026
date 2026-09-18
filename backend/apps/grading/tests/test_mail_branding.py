"""Marka i koperta dwóch listów oceniania: przypomnienia koordynatora i przypomnienia beatu.

Dwa różne listy o tej samej sprawie i dlatego oba są tutaj:

- ``apps.grading.reports.remind_reviewers`` – wysyła koordynator jednym kliknięciem, temat jest
  stałą modułu (``REMINDER_SUBJECT``),
- ``apps.grading.tasks.remind_overdue_reviews`` – wysyła beat raz na dobę, a temat składa się
  w serwisie i ma **dwa** brzmienia zależnie od tego, czy któraś recenzja jest po terminie
  (``apps.grading.deadlines``). Do etapu 2 nie pilnował go żaden test (§ 1.1.1).

Przebieg beatu obchodzi wszystkie konkursy naraz, więc jest zarazem jedynym miejscem, w którym da
się sprawdzić, że dwa konkursy **nie** mieszają się w jednej kopercie.
"""

from __future__ import annotations

import pytest
from django.core import mail

from apps.grading.reports import REMINDER_SUBJECT, remind_reviewers
from apps.grading.tasks import remind_overdue_reviews
from apps.tenancy.tests.golden import assign_pending_review, build_golden

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


def test_the_coordinator_reminder_of_competition_one_is_unchanged(
    competition, settings, django_capture_on_commit_callbacks
):
    golden = build_golden(competition)
    assign_pending_review(golden)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        remind_reviewers(golden.elim)

    assert mail.outbox, "Recenzent z przydzieloną pracą ma dostać przypomnienie."
    assert {letter.subject for letter in mail.outbox} == {REMINDER_SUBJECT}
    assert {letter.from_email for letter in mail.outbox} == {settings.DEFAULT_FROM_EMAIL}
    assert {_signature_of(letter.body) for letter in mail.outbox} == {SIGNATURE_LINES}


def test_the_coordinator_reminder_carries_the_brand_of_the_competition_of_the_stage(
    branded, django_capture_on_commit_callbacks
):
    golden = build_golden(branded)
    assign_pending_review(golden)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        remind_reviewers(golden.elim)

    assert {letter.subject for letter in mail.outbox} == {
        f"Przypomnienie o zaległych recenzjach – {OTHER_NAME}"
    }
    assert {letter.from_email for letter in mail.outbox} == {OTHER_SENDER}
    assert {_signature_of(letter.body) for letter in mail.outbox} == {("--", OTHER_NAME, SIGNATURE_LINES[2])}


@pytest.mark.parametrize(
    ("overdue", "expected"),
    [(True, f"{OTHER_NAME}: 1 recenzji po terminie"), (False, f"{OTHER_NAME}: zbliża się termin recenzji")],
)
def test_both_wordings_of_the_beat_reminder_carry_the_brand(branded, overdue, expected):
    """Dwa brzmienia tematu, jedna reguła: nazwa konkursu wchodzi na miejsce dzisiejszego literału.

    Przebieg idzie przez zadanie, a nie przez ``reminder_message``: o tym, które z dwóch zdań
    wyjdzie, rozstrzyga **dobór** recenzji, a nie wołający.
    """
    golden = build_golden(branded)
    assign_pending_review(golden, overdue=overdue)
    mail.outbox.clear()

    remind_overdue_reviews()

    assert len(mail.outbox) == 1
    assert mail.outbox[-1].subject == expected
    assert mail.outbox[-1].from_email == OTHER_SENDER


def test_the_beat_keeps_competition_one_unchanged(competition, settings):
    golden = build_golden(competition)
    assign_pending_review(golden)
    mail.outbox.clear()

    remind_overdue_reviews()

    assert len(mail.outbox) == 1
    assert mail.outbox[-1].subject == "Olimpiada Kwantowa: 1 recenzji po terminie"
    assert mail.outbox[-1].from_email == settings.DEFAULT_FROM_EMAIL
