"""Marka i koperta dwóch listów o zgłoszeniu (``apps/support/services.py``).

Oba tematy niosą numer sprawy i do etapu 2 były literałem wewnątrz funkcji, którego **nie pilnował
żaden test** (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.1, ryzyko T9). Od tej zmiany są stałą modułu
i parą „stała + wzorzec”, więc test sprawdza jedno i drugie: że numer nadal jest w temacie i że
nazwa konkursu wchodzi na miejsce literału dopiero przy włączonej fladze.

Adresat listu do organizatora bierze się z ``Competition.contact_email`` (tak było już przed
etapem 2), a **nadawca** – z ``Competition.from_email``. To dwa różne pola i nie wolno ich mylić:
pierwsze jest skrzynką, którą organizator czyta, drugie adresem, spod którego piszemy.
"""

from __future__ import annotations

import pytest
from django.core import mail

from apps.tenancy.context import competition_context
from apps.tenancy.tests.golden import answer_support_ticket, build_golden, open_support_ticket

pytestmark = pytest.mark.django_db

OTHER_NAME = "Olimpiada Fizyczna"
OTHER_SENDER = "listy@fizyczna.test"


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


def test_the_ticket_letters_of_competition_one_are_unchanged(
    competition, settings, django_capture_on_commit_callbacks
):
    """Dwa dzisiejsze tematy co do znaku, razem z numerem sprawy i dzisiejszym nadawcą."""
    golden = build_golden(competition)
    mail.outbox.clear()

    with competition_context(competition), django_capture_on_commit_callbacks(execute=True):
        ticket = open_support_ticket(golden)
    opened = mail.outbox[-1]

    with competition_context(competition), django_capture_on_commit_callbacks(execute=True):
        answer_support_ticket(golden, ticket)
    answered = mail.outbox[-1]

    assert opened.subject == f"Nowe zgłoszenie #{ticket.pk} – Olimpiada Kwantowa"
    assert answered.subject == f"Odpowiedź na zgłoszenie #{ticket.pk} – Olimpiada Kwantowa"
    assert opened.from_email == answered.from_email == settings.DEFAULT_FROM_EMAIL


def test_the_ticket_letters_carry_the_brand_of_the_competition_of_the_ticket(
    branded, django_capture_on_commit_callbacks
):
    golden = build_golden(branded)
    mail.outbox.clear()

    with competition_context(branded), django_capture_on_commit_callbacks(execute=True):
        ticket = open_support_ticket(golden)
    opened = mail.outbox[-1]

    with competition_context(branded), django_capture_on_commit_callbacks(execute=True):
        answer_support_ticket(golden, ticket)
    answered = mail.outbox[-1]

    assert opened.subject == f"Nowe zgłoszenie #{ticket.pk} – {OTHER_NAME}"
    assert answered.subject == f"Odpowiedź na zgłoszenie #{ticket.pk} – {OTHER_NAME}"
    assert opened.from_email == answered.from_email == OTHER_SENDER
