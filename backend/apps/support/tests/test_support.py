"""Zgłoszenia i pomoc: zakładanie sprawy, wątek, powiadomienia i granice widoczności.

Cztery rzeczy, na których stoi ten moduł i które te testy sprawdzają:

- **sprawa zawsze ma adres zwrotny.** Zgłoszenie bez konta wymaga e-maila; zgłoszenie z konta
  bierze adres konta. Sprawa, na którą nie da się odpowiedzieć, nie jest sprawą – pilnuje tego
  serwis, a w ostatniej instancji constraint w bazie,
- **kontekst techniczny jest wskazówką, a nie kopią sesji.** Wchodzi do niego adres strony,
  przeglądarka, język, kod uczestnika, etapy i **nazwa** ostatniej czynności z audytu. Nie wchodzą:
  ciasteczka, tokeny, treść wpisu audytowego,
- **stan wraca do kolejki.** Odpowiedź organizatora daje „odpowiedziane”, ale dopisek zgłaszającego
  wraca sprawę do „otwarte”. Bez tej drugiej połowy sprawa, do której ktoś napisał „to nadal nie
  działa”, znikałaby z kolejki na zawsze,
- **cudza sprawa to 404.** Nie 403: numery zgłoszeń są kolejne, więc 403 byłby licznikiem spraw
  w serwisie.
"""

from __future__ import annotations

import time

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.core.models import AuditLog, audit
from apps.support.models import SupportCategory, SupportMessage, SupportTicket, TicketStatus
from apps.support.services import collect_context, open_ticket, open_ticket_count, reply, set_status

pytestmark = pytest.mark.django_db

NEW_URL = "/support/new/"
LIST_URL = "/support/"


@pytest.fixture
def client_() -> Client:
    return Client()


@pytest.fixture
def participant(db):
    return ParticipantFactory(user=UserFactory(email="uczen@example.test", groups=["participant"]))


@pytest.fixture
def coordinator(db):
    return CoordinatorFactory()


def form_data(**overrides) -> dict:
    data = {
        "category": SupportCategory.SUBMISSION,
        "subject": "Nie mogę wysłać pracy",
        "body": "Po kliknięciu „Wyślij” strona pokazuje błąd i plik nie dochodzi.",
        "page_url": "/me/",
    }
    data.update(overrides)
    return data


def captcha_data(**overrides) -> dict:
    """Blok antyspamowy publicznego formularza – ten sam, co w rejestracji."""
    from apps.web.captcha import sign_timestamp

    data = {
        "captcha_0": "klucz-nieistotny-w-trybie-testowym",
        "captcha_1": "PASSED",
        "website": "",
        "form_ts": sign_timestamp(time.time() - 10),
    }
    data.update(overrides)
    return data


# --- zakładanie sprawy ------------------------------------------------------------------------


def test_a_logged_in_participant_opens_a_ticket(client_, participant):
    client_.force_login(participant.user)

    response = client_.post(NEW_URL, form_data())

    ticket = SupportTicket.objects.get()
    assert response.status_code == 302
    assert ticket.user_id == participant.user.pk
    assert ticket.status == TicketStatus.OPEN
    assert ticket.messages.count() == 1


def test_the_role_is_snapshotted_at_the_time_of_the_report(client_, participant):
    """Rola bywa inna pół roku później – sprawę czyta się w kontekście tego, kim nadawca był wtedy."""
    client_.force_login(participant.user)

    client_.post(NEW_URL, form_data())

    assert SupportTicket.objects.get().role_snapshot == "uczestnik"


def test_a_visitor_without_an_account_may_report_a_problem(client_):
    """Kto nie może się zalogować, ma najwięcej powodów, żeby napisać."""
    response = client_.post(
        NEW_URL,
        {**form_data(category=SupportCategory.ACCOUNT), "email": "gosc@example.test", **captcha_data()},
    )

    ticket = SupportTicket.objects.get()
    assert response.status_code == 302
    assert ticket.user_id is None
    assert ticket.email == "gosc@example.test"


def test_an_anonymous_report_without_an_e_mail_is_refused(client_):
    response = client_.post(NEW_URL, {**form_data(), **captcha_data()})

    assert response.status_code == 400
    assert SupportTicket.objects.count() == 0


def test_an_anonymous_report_that_falls_into_the_honeypot_is_refused(client_):
    """Ta sama warstwa, co przy rejestracji: publiczny formularz, który wysyła list."""
    response = client_.post(
        NEW_URL,
        {**form_data(), "email": "bot@example.test", **captcha_data(website="wypelnione-przez-bota")},
    )

    assert response.status_code == 400
    assert SupportTicket.objects.count() == 0


def test_a_logged_in_report_does_not_ask_for_a_captcha(client_, participant):
    """Konto jest już tym kosztem, którego CAPTCHA broni – a nadawca właśnie na coś utknął."""
    client_.force_login(participant.user)

    body = client_.get(NEW_URL).content.decode()

    assert "captcha" not in body


def test_the_form_points_at_the_faq_before_reporting(client_, participant):
    client_.force_login(participant.user)

    body = client_.get(NEW_URL).content.decode()

    assert "/faq/" in body


# --- kontekst techniczny ----------------------------------------------------------------------


def test_the_context_carries_the_page_the_browser_and_the_participant_code(rf, participant):
    request = rf.post(NEW_URL, HTTP_USER_AGENT="Firefox/140.0")
    request.user = participant.user

    context = collect_context(request, page_url="/me/stages/1/")

    assert context["adres_strony"] == "/me/stages/1/"
    assert context["przegladarka"] == "Firefox/140.0"
    assert context["kod_uczestnika"] == participant.public_code


def test_the_context_carries_only_the_name_of_the_last_audit_action(rf, participant):
    """Nazwa mówi, co ta osoba robiła; ``diff`` jest już dziennikiem zdarzeń z własnym ekranem."""
    audit(participant.user, "submission.uploaded", participant, {"tajne": "nie-dla-zgloszenia"})
    request = rf.post(NEW_URL)
    request.user = participant.user

    context = collect_context(request)

    assert context["ostatnia_czynnosc"] == "submission.uploaded"
    assert "nie-dla-zgloszenia" not in str(context)


def test_the_context_never_carries_cookies_or_headers_beyond_the_user_agent(rf, participant):
    request = rf.post(NEW_URL, HTTP_AUTHORIZATION="Token tajny-token")
    request.COOKIES["sessionid"] = "tajna-sesja"
    request.user = participant.user

    context = collect_context(request)

    assert "tajny-token" not in str(context)
    assert "tajna-sesja" not in str(context)


def test_the_context_lists_the_stages_the_reporter_is_entered_in(rf, participant):
    from apps.competitions.tests.factories import StageEntryFactory

    entry = StageEntryFactory(participant=participant)
    request = rf.post(NEW_URL)
    request.user = participant.user

    assert collect_context(request)["etapy"] == [entry.stage_id]


# --- powiadomienia ----------------------------------------------------------------------------


def test_opening_a_ticket_notifies_the_organiser_without_the_body(
    participant, django_capture_on_commit_callbacks
):
    mail.outbox.clear()

    # List wychodzi **po commicie** (``queue_mail`` → ``transaction.on_commit``), żeby niedostępny
    # MTA nie zamienił zgłoszonej sprawy w błąd 500 – stąd ``django_capture_on_commit_callbacks``.
    with django_capture_on_commit_callbacks(execute=True):
        open_ticket(
            user=participant.user,
            category=SupportCategory.OTHER,
            subject="Pytanie",
            body="TRESC-ZGLOSZENIA-KTORA-ZOSTAJE-W-SERWISIE",
        )

    assert len(mail.outbox) == 1
    assert "TRESC-ZGLOSZENIA-KTORA-ZOSTAJE-W-SERWISIE" not in mail.outbox[0].body
    assert "/coordinator/support/" in mail.outbox[0].body


def test_the_organiser_notification_goes_to_the_contact_address_from_site_settings(
    participant, django_capture_on_commit_callbacks
):
    """``DEFAULT_FROM_EMAIL`` to adres nadawcy (``noreply@…``) – list o sprawie lądowałby w pustce."""
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        open_ticket(user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść.")

    assert mail.outbox[0].to == ["contact@qaif.org"]


def test_an_answer_notifies_the_reporter_without_the_body(
    participant, coordinator, django_capture_on_commit_callbacks
):
    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść."
    )
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        reply(ticket, "TRESC-ODPOWIEDZI-ORGANIZATORA", author=coordinator, from_coordinator=True)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [participant.user.email]
    assert "TRESC-ODPOWIEDZI-ORGANIZATORA" not in mail.outbox[0].body


# --- wątek i stan -----------------------------------------------------------------------------


def test_an_answer_marks_the_ticket_as_answered(participant, coordinator):
    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść."
    )

    reply(ticket, "Już naprawione.", author=coordinator, from_coordinator=True)

    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.ANSWERED
    assert ticket.answered_at is not None


def test_a_note_from_the_reporter_brings_the_ticket_back_to_the_queue(participant, coordinator):
    """Bez tego sprawa, do której ktoś dopisał „to nadal nie działa”, znikałaby z kolejki na zawsze."""
    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść."
    )
    reply(ticket, "Już naprawione.", author=coordinator, from_coordinator=True)

    reply(ticket, "Nadal nie działa.", author=participant.user, from_coordinator=False)

    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.OPEN


def test_a_closed_ticket_accepts_no_more_messages(participant, coordinator):
    """Zamknięcie jest decyzją organizatora i ma zostać decyzją, a nie stanem unieważnianym dopiskiem."""
    from apps.core.api import DomainError

    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść."
    )
    set_status(ticket, TicketStatus.CLOSED, actor=coordinator)

    with pytest.raises(DomainError):
        reply(ticket, "Jeszcze jedno.", author=participant.user, from_coordinator=False)


def test_the_counter_holds_only_open_tickets(participant, coordinator):
    open_ticket(user=participant.user, category=SupportCategory.OTHER, subject="Jedno", body="Treść.")
    second = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Drugie", body="Treść."
    )
    set_status(second, TicketStatus.CLOSED, actor=coordinator)

    assert open_ticket_count() == 1


# --- audyt ------------------------------------------------------------------------------------


def test_the_audit_entry_carries_the_category_and_the_length_never_the_body(participant):
    open_ticket(
        user=participant.user,
        category=SupportCategory.RESULTS,
        subject="Pytanie",
        body="TRESC-KTOREJ-NIE-MA-W-AUDYCIE",
    )

    entry = AuditLog.objects.filter(action="ticket.opened").get()
    assert entry.diff["category"] == SupportCategory.RESULTS
    assert entry.diff["anonymous"] is False
    assert "TRESC-KTOREJ-NIE-MA-W-AUDYCIE" not in str(entry.diff)


def test_closing_leaves_an_audit_entry(participant, coordinator):
    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść."
    )

    set_status(ticket, TicketStatus.CLOSED, actor=coordinator)

    assert AuditLog.objects.filter(action="ticket.closed", actor=coordinator).exists()


# --- widoczność -------------------------------------------------------------------------------


def test_the_list_shows_only_own_tickets(client_, participant):
    other = ParticipantFactory(user=UserFactory(email="ktos@example.test", groups=["participant"]))
    open_ticket(user=participant.user, category=SupportCategory.OTHER, subject="MOJE", body="Treść.")
    open_ticket(user=other.user, category=SupportCategory.OTHER, subject="CUDZE", body="Treść.")
    client_.force_login(participant.user)

    body = client_.get(LIST_URL).content.decode()

    assert "MOJE" in body
    assert "CUDZE" not in body


def test_someone_elses_ticket_is_a_404_not_a_403(client_, participant):
    other = ParticipantFactory(user=UserFactory(email="ktos@example.test", groups=["participant"]))
    ticket = open_ticket(user=other.user, category=SupportCategory.OTHER, subject="Cudze", body="Treść.")
    client_.force_login(participant.user)

    assert client_.get(f"/support/{ticket.pk}/").status_code == 404


def test_the_thread_shows_both_sides(client_, participant, coordinator):
    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="MOJE-PYTANIE"
    )
    reply(ticket, "ODPOWIEDZ-ORGANIZATORA", author=coordinator, from_coordinator=True)
    client_.force_login(participant.user)

    body = client_.get(f"/support/{ticket.pk}/").content.decode()

    assert "MOJE-PYTANIE" in body
    assert "ODPOWIEDZ-ORGANIZATORA" in body


def test_the_reporter_can_add_a_note_from_the_thread(client_, participant):
    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść."
    )
    client_.force_login(participant.user)

    client_.post(f"/support/{ticket.pk}/", {"body": "Dopisuję jeszcze jedno zdanie."})

    assert SupportMessage.objects.filter(ticket=ticket, from_coordinator=False).count() == 2


def test_the_account_bar_links_to_support_for_a_logged_in_user(client_, participant):
    """Pasek konta prowadzi teraz do formularza (uwaga organizatora z 21.09.2026), nie do listy
    zgłoszeń – lista własnych zgłoszeń jest jedno kliknięcie dalej, na samym formularzu
    („Moje zgłoszenia”, widoczne tylko zalogowanym – patrz ``templates/web/support/new.html``)."""
    client_.force_login(participant.user)

    body = client_.get(LIST_URL).content.decode()
    account_bar = body[body.index('aria-label="Konto"') : body.index('aria-label="Serwis"')]

    assert f'href="{NEW_URL}"' in account_bar
    assert f'href="{LIST_URL}"' not in account_bar


def test_anonymising_an_account_keeps_its_tickets(participant):
    """Anonimizacja wyciera dane osobowe, ale nie kasuje wiersza konta – sprawa zostaje w aktach."""
    from apps.accounts.profile import anonymise_account

    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść."
    )

    anonymise_account(participant.user)

    ticket.refresh_from_db()
    assert ticket.user_id == participant.user.pk


def test_hard_deleting_an_account_takes_its_tickets_with_it(participant):
    """Konto bez śladu w zawodach znika w całości (art. 17) – jego zgłoszenia są jego danymi."""
    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść."
    )

    participant.user.delete()

    assert not SupportTicket.objects.filter(pk=ticket.pk).exists()


def test_the_creation_timestamp_is_set(participant):
    before = timezone.now()

    ticket = open_ticket(
        user=participant.user, category=SupportCategory.OTHER, subject="Pytanie", body="Treść."
    )

    assert ticket.created_at >= before
