"""Wydanie D: zgłoszenie idzie do organizatora **tego** konkursu.

Trzy rzeczy są tu ważniejsze od reszty:

- **kolejka koordynatora jest kolejką jego konkursu.** Sprawa napisana pod domeną konkursu B nie
  ma prawa pokazać się ani w liście, ani w liczniku uwagi konkursu A – a licznik i lista mają
  liczyć to samo, więc pytamy o nie jednym serwisem,
- **pusty konkurs znaczy „do operatora platformy”**, a nie „do wszystkich”. To jest jedyny model
  w tym wydaniu, w którym ``NULL`` zostaje na stałe i ma własne znaczenie (§ 3.2), więc trzeba
  pokazać, że takiej sprawy **nie** widzi żaden organizator,
- **adres, na który idzie powiadomienie**, bierze się z konkursu sprawy. Dla Olimpiady Kwantowej
  to dokładnie ten sam adres, co przed zmianą (``tenancy.0002`` przepisała go z ``SiteSettings``),
  i to jest asercja, a nie założenie – § 0.
"""

from __future__ import annotations

import pytest

from apps.support.models import SupportTicket, TicketStatus
from apps.support.services import _organizer_email, open_ticket, open_ticket_count

from .factories import SupportTicketFactory

pytestmark = pytest.mark.django_db


def test_ticket_of_another_competition_is_not_in_our_queue(competition, other_competition):
    ticket_b = SupportTicketFactory(competition=other_competition)

    assert list(SupportTicket.objects.for_competition(competition)) == []
    assert list(SupportTicket.objects.for_competition(other_competition)) == [ticket_b]


def test_ticket_to_the_platform_operator_belongs_to_no_competition(competition, other_competition):
    """Sprawa bez konkursu nie jest sprawą „wszystkich” – nie widzi jej żaden organizator.

    ``for_competition`` jest tu ścisłe z rozmysłem: gdyby dopuszczało ``NULL``, zgłoszenie pisane
    do operatora platformy („nie mogę się nigdzie zalogować”) lądowałoby w kolejce przypadkowego
    organizatora razem z tym, co autor o platformie napisał.
    """
    orphan = SupportTicketFactory(competition=None)

    assert orphan not in list(SupportTicket.objects.for_competition(competition))
    assert orphan not in list(SupportTicket.objects.for_competition(other_competition))


def test_open_ticket_assigns_the_competition_of_the_context(as_competition, other_competition):
    """Konkurs sprawy bierze się z żądania, a poza żądaniem – z kontekstu przebiegu."""
    with as_competition(other_competition):
        ticket = open_ticket(
            user=None,
            category="OTHER",
            subject="Nie działa logowanie",
            body="Opis sprawy.",
            email="ktos@example.invalid",
        )

    assert ticket.competition_id == other_competition.pk


def test_open_ticket_without_a_competition_goes_to_the_platform_operator(unbound_competition, db):  # noqa: ARG001
    """Adres bez konkursu zakłada sprawę bez konkursu – i to jest odpowiedź, nie awaria."""
    ticket = open_ticket(
        user=None,
        category="OTHER",
        subject="Sprawa do operatora",
        body="Opis sprawy.",
        email="ktos@example.invalid",
    )

    assert ticket.competition_id is None


def test_pending_counter_counts_only_our_competition(competition, other_competition, as_competition):
    SupportTicketFactory(competition=competition, status=TicketStatus.OPEN)
    SupportTicketFactory(competition=other_competition, status=TicketStatus.OPEN)
    SupportTicketFactory(competition=other_competition, status=TicketStatus.OPEN)

    assert open_ticket_count(competition) == 1
    assert open_ticket_count(other_competition) == 2
    # Bez argumentu licznik czyta konkurs z kontekstu – tak liczy go pulpit koordynatora.
    with as_competition(other_competition):
        assert open_ticket_count() == 2


def test_notification_address_comes_from_the_competition(competition, other_competition):
    """Powiadomienie o sprawie idzie do organizatora, którego strona ją przyjęła.

    Adres Konkursu #1 zostaje taki, jaki wpisała migracja z ustawień serwisu – to jest warunek
    § 0, a nie szczegół konfiguracji.
    """
    competition.contact_email = "komitet@kwantowa.example.invalid"
    competition.save(update_fields=["contact_email"])
    other_competition.contact_email = "kontakt@fizyczna.example.invalid"
    other_competition.save(update_fields=["contact_email"])

    assert _organizer_email(competition) == "komitet@kwantowa.example.invalid"
    assert _organizer_email(other_competition) == "kontakt@fizyczna.example.invalid"
    # Konkurs bez wpisanego adresu i sprawa do operatora platformy schodzą na ustawienia
    # instalacji – list bez idealnego adresata jest lepszy niż wywrócona wysyłka.
    other_competition.contact_email = ""
    other_competition.save(update_fields=["contact_email"])
    assert "@" in _organizer_email(other_competition)
    assert "@" in _organizer_email(None)
