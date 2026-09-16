"""Kolejka zgłoszeń w panelu koordynatora: kolejność, filtry, odpowiedź i zamknięcie.

Kolejność kolejki jest regułą, nie ustawieniem sortowania, i to ona jest tu głównym przedmiotem:
otwarte na górze, a w obrębie stanu od najstarszego. Najdłużej czekająca sprawa ma stać pierwsza
także (a właściwie zwłaszcza) wtedy, gdy ruch rośnie – sortowanie „od najnowszej” spychałoby ją
na dół dokładnie w tym momencie.

Poza tym: kontekst techniczny widoczny **wyłącznie** dla organizatora, odpowiedź razem
z zamknięciem jednym zapisem formularza i licznik na pulpicie.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import AuditLog
from apps.support.models import SupportCategory, SupportMessage, SupportTicket, TicketStatus
from apps.support.services import open_ticket, reply, set_status

pytestmark = pytest.mark.django_db

QUEUE_URL = "/coordinator/support/"


def ticket(user=None, *, subject="Sprawa", category=SupportCategory.OTHER, age_days: int = 0):
    """Zgłoszenie o zadanym wieku. Wiek ustawiamy po zapisie – ``created_at`` ma domyślne „teraz”."""
    item = open_ticket(user=user, category=category, subject=subject, body="Treść zgłoszenia.")
    if age_days:
        SupportTicket.objects.filter(pk=item.pk).update(created_at=timezone.now() - timedelta(days=age_days))
        item.refresh_from_db()
    return item


# --- dostęp ---------------------------------------------------------------------------------


def test_the_queue_is_for_the_coordinator_only(web_client, participant):
    web_client.force_login(participant.user)

    assert web_client.get(QUEUE_URL).status_code == 403


def test_an_anonymous_visitor_is_sent_to_the_login_page(web_client):
    response = web_client.get(QUEUE_URL)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


# --- kolejność i filtry -----------------------------------------------------------------------


def test_open_tickets_come_first_and_the_oldest_of_them_is_on_top(web_client, coordinator, participant):
    answered = ticket(participant.user, subject="ODPOWIEDZIANE", age_days=10)
    reply(answered, "Już wyjaśnione.", author=coordinator, from_coordinator=True)
    ticket(participant.user, subject="NOWE-OTWARTE", age_days=1)
    ticket(participant.user, subject="STARE-OTWARTE", age_days=5)
    web_client.force_login(coordinator)

    body = web_client.get(QUEUE_URL).content.decode()

    assert body.index("STARE-OTWARTE") < body.index("NOWE-OTWARTE") < body.index("ODPOWIEDZIANE")


def test_the_status_filter_narrows_the_queue(web_client, coordinator, participant):
    open_one = ticket(participant.user, subject="OTWARTE")
    closed = ticket(participant.user, subject="ZAMKNIETE")
    set_status(closed, TicketStatus.CLOSED, actor=coordinator)
    web_client.force_login(coordinator)

    body = web_client.get(f"{QUEUE_URL}?status={TicketStatus.CLOSED}").content.decode()

    assert "ZAMKNIETE" in body
    assert open_one.subject not in body


def test_the_category_filter_narrows_the_queue(web_client, coordinator, participant):
    ticket(participant.user, subject="O-WYSYLCE", category=SupportCategory.SUBMISSION)
    ticket(participant.user, subject="O-WYNIKACH", category=SupportCategory.RESULTS)
    web_client.force_login(coordinator)

    body = web_client.get(f"{QUEUE_URL}?category={SupportCategory.SUBMISSION}").content.decode()

    assert "O-WYSYLCE" in body
    assert "O-WYNIKACH" not in body


def test_the_queue_shows_reports_without_an_account(web_client, coordinator):
    open_ticket(
        user=None,
        category=SupportCategory.ACCOUNT,
        subject="Bez konta",
        body="Treść.",
        email="gosc@example.test",
    )
    web_client.force_login(coordinator)

    body = web_client.get(QUEUE_URL).content.decode()

    assert "gosc@example.test" in body
    assert "bez konta" in body


# --- wątek ------------------------------------------------------------------------------------


def test_the_coordinator_sees_the_technical_context(web_client, coordinator, participant):
    item = ticket(participant.user)
    SupportTicket.objects.filter(pk=item.pk).update(
        context={"adres_strony": "/me/", "przegladarka": "Firefox/140.0"}
    )
    web_client.force_login(coordinator)

    body = web_client.get(f"{QUEUE_URL}{item.pk}/").content.decode()

    assert "Firefox/140.0" in body
    assert "/me/" in body


def test_the_reporter_does_not_see_the_technical_context(web_client, participant):
    """Sprawa ma się czytać jak rozmowa; pełny kontekst uczestnik i tak ma w eksporcie danych."""
    item = ticket(participant.user)
    SupportTicket.objects.filter(pk=item.pk).update(context={"przegladarka": "Firefox/140.0"})
    web_client.force_login(participant.user)

    body = web_client.get(f"/support/{item.pk}/").content.decode()

    assert "Firefox/140.0" not in body


def test_an_answer_is_added_to_the_thread_and_changes_the_status(web_client, coordinator, participant):
    item = ticket(participant.user)
    web_client.force_login(coordinator)

    web_client.post(f"{QUEUE_URL}{item.pk}/", {"body": "Sprawdziliśmy – już działa."})

    item.refresh_from_db()
    assert item.status == TicketStatus.ANSWERED
    assert SupportMessage.objects.filter(ticket=item, from_coordinator=True).count() == 1


def test_an_answer_with_the_checkbox_closes_the_ticket_in_one_save(web_client, coordinator, participant):
    """Jedna decyzja („odpisuję i uważam sprawę za załatwioną”) ma być jednym zapisem formularza."""
    item = ticket(participant.user)
    web_client.force_login(coordinator)

    web_client.post(f"{QUEUE_URL}{item.pk}/", {"body": "Sprawdziliśmy – już działa.", "close": "on"})

    item.refresh_from_db()
    assert item.status == TicketStatus.CLOSED
    assert item.closed_at is not None


def test_closing_without_an_answer_is_possible(web_client, coordinator, participant):
    """Zgłoszenie bywa duplikatem – wymuszanie na to odpowiedzi byłoby wymuszaniem pustego zdania."""
    item = ticket(participant.user)
    web_client.force_login(coordinator)

    web_client.post(f"{QUEUE_URL}{item.pk}/", {"action": "close"})

    item.refresh_from_db()
    assert item.status == TicketStatus.CLOSED
    assert SupportMessage.objects.filter(ticket=item, from_coordinator=True).count() == 0


def test_an_empty_answer_is_refused(web_client, coordinator, participant):
    item = ticket(participant.user)
    web_client.force_login(coordinator)

    response = web_client.post(f"{QUEUE_URL}{item.pk}/", {"body": "   "})

    item.refresh_from_db()
    assert response.status_code == 400
    assert item.status == TicketStatus.OPEN


def test_answering_leaves_an_audit_entry_with_the_length_not_the_body(web_client, coordinator, participant):
    item = ticket(participant.user)
    web_client.force_login(coordinator)

    web_client.post(f"{QUEUE_URL}{item.pk}/", {"body": "TRESC-ODPOWIEDZI"})

    entry = AuditLog.objects.filter(action="ticket.answered").get()
    assert entry.actor_id == coordinator.pk
    assert "TRESC-ODPOWIEDZI" not in str(entry.diff)
    assert entry.diff["length"] == len("TRESC-ODPOWIEDZI")


def test_a_missing_ticket_is_a_404(web_client, coordinator):
    web_client.force_login(coordinator)

    assert web_client.get(f"{QUEUE_URL}9999/").status_code == 404


# --- licznik na pulpicie ----------------------------------------------------------------------


def test_the_dashboard_counts_open_tickets(web_client, coordinator, participant):
    ticket(participant.user, subject="Pierwsze")
    closed = ticket(participant.user, subject="Drugie")
    set_status(closed, TicketStatus.CLOSED, actor=coordinator)
    web_client.force_login(coordinator)

    from apps.support.services import open_ticket_count

    response = web_client.get("/coordinator/")

    assert response.status_code == 200
    assert open_ticket_count() == 1
    # Kolejka jest osiągalna z pulpitu – licznik bez drogi do listy nie jest do niczego potrzebny.
    assert QUEUE_URL in response.content.decode()
