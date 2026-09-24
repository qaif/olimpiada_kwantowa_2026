"""List resetu hasła idzie w tle: ``send_mail_task`` na kolejce ``mail``, po commicie.

Uzasadnienie zmiany: ``apps/accounts/password_reset.py``. Pilnujemy tu tego, co ta zmiana mogła
zepsuć, i tego, co miała naprawić:

- **żadnej wysyłki w żądaniu** – odpowiedź nie dotyka MTA, list powstaje dopiero w zadaniu,
- **po commicie** – przed zatwierdzeniem transakcji nic nie jest kolejkowane,
- **właściwe argumenty i właściwa kolejka** – temat, treść, adresat, nadawca, wersja HTML,
- **list bajt w bajt ten sam** – co wysyłał ``PasswordResetForm`` Django w żądaniu,
- **bez enumeracji kont** – adres z kontem i bez daje tę samą odpowiedź, także gdy broker leży,
- **droga koordynatora** – ten sam formularz, ta sama kolejka.

Przepływ od strony użytkownika (link działa, token jednorazowy, limit żądań) sprawdza
``test_password_reset.py`` – z wykonaniem callbacków ``on_commit``, czyli już przez zadanie.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.forms import PasswordResetForm
from django.core import mail
from django.test import RequestFactory

from apps.accounts.password_reset import QueuedPasswordResetForm
from apps.core.tasks import send_mail_task
from apps.web.views.public import PasswordResetView

pytestmark = pytest.mark.django_db

RESET_URL = "/password-reset/"
SENT_URL = "/password-reset/sent/"
RESET_LINK = re.compile(r"https?://[^/\s]+/reset/[^/\s]+/[^/\s]+/")


class Recorder:
    """Podstawka pod ``send_mail_task``: zapisuje wywołania ``delay`` zamiast kolejkować."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[tuple, dict]] = []
        self.error = error

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error


@pytest.fixture
def queued(monkeypatch):
    """Podmienia zadanie w module, z którego formularz je importuje (import leniwy w ``_enqueue``)."""
    recorder = Recorder()
    monkeypatch.setattr("apps.core.tasks.send_mail_task", recorder)
    return recorder


def post_reset(client, email, capture, *, execute=True, **extra):
    with capture(execute=execute) as callbacks:
        response = client.post(RESET_URL, {"email": email}, **extra)
    return response, callbacks


# --- kolejkowanie zamiast wysyłki ----------------------------------------------------------------


def test_the_request_enqueues_the_task_with_the_rendered_message(
    web_client, participant, queued, django_capture_on_commit_callbacks
):
    response, _ = post_reset(web_client, participant.user.email, django_capture_on_commit_callbacks)

    assert response.status_code == 302
    assert response["Location"] == SENT_URL
    # Nic nie wyszło w żądaniu – list powstaje dopiero w zadaniu.
    assert mail.outbox == []
    assert len(queued.calls) == 1
    args, kwargs = queued.calls[0]
    subject, body, recipients, from_email = args
    assert subject == "Reset hasła – Olimpiada Kwantowa"
    assert RESET_LINK.search(body), body
    assert recipients == [participant.user.email]
    # ``None`` = nadawca instalacji (``DEFAULT_FROM_EMAIL``) – dokładnie to, co podawał widok Django.
    assert from_email is None
    html = kwargs["html_message"]
    assert isinstance(html, str) and "<" in html
    # Argumenty jadą przez JSON brokera – wyłącznie zwykłe napisy, bez ``SafeString`` i obiektów.
    assert all(type(value) is str for value in (subject, body, html))


def test_nothing_is_enqueued_before_the_transaction_commits(
    web_client, participant, queued, django_capture_on_commit_callbacks
):
    response, callbacks = post_reset(
        web_client, participant.user.email, django_capture_on_commit_callbacks, execute=False
    )

    assert response.status_code == 302
    assert len(callbacks) == 1
    assert queued.calls == []
    assert mail.outbox == []

    callbacks[0]()

    assert len(queued.calls) == 1


def test_the_task_is_routed_to_the_mail_queue():
    from config.celery import app

    route = app.amqp.router.route({}, send_mail_task.name)

    assert route["queue"].name == "mail"


def test_the_task_attaches_the_html_part_only_when_given():
    send_mail_task.delay("Temat", "Treść", ["a@example.test"], None, html_message="<p>Treść</p>")
    send_mail_task.delay("Temat", "Treść", ["b@example.test"])

    with_html, plain = mail.outbox
    assert with_html.alternatives[0][0] == "<p>Treść</p>"
    assert with_html.alternatives[0][1] == "text/html"
    assert plain.alternatives == []


# --- ten sam list, co przed zmianą ---------------------------------------------------------------


class FixedTokens:
    """Token zależy od chwili wygenerowania – do porównania dwóch dróg musi być stały."""

    def make_token(self, user):
        return "cf3k2p-0123456789abcdef0123456789abcdef"


def _save(form_class, email: str, request) -> None:
    form = form_class(data={"email": email})
    assert form.is_valid()
    form.save(
        use_https=False,
        token_generator=FixedTokens(),
        from_email=None,
        email_template_name=PasswordResetView.email_template_name,
        subject_template_name=PasswordResetView.subject_template_name,
        html_email_template_name=PasswordResetView.html_email_template_name,
        request=request,
        extra_email_context={"site_name": "Olimpiada Kwantowa"},
    )


def test_the_queued_message_is_identical_to_the_one_django_sent_in_the_request(
    participant, django_capture_on_commit_callbacks
):
    request = RequestFactory().post(RESET_URL)
    email = participant.user.email

    _save(PasswordResetForm, email, request)  # dawna droga: wysyłka w żądaniu
    with django_capture_on_commit_callbacks(execute=True):
        _save(QueuedPasswordResetForm, email, request)  # nowa: zadanie (eager w testach)

    before, after = mail.outbox
    for attribute in (
        "subject",
        "body",
        "from_email",
        "to",
        "cc",
        "bcc",
        "reply_to",
        "alternatives",
        "content_subtype",
        "extra_headers",
        "attachments",
    ):
        assert getattr(after, attribute) == getattr(before, attribute), attribute


# --- bez enumeracji kont -------------------------------------------------------------------------


def test_known_and_unknown_addresses_get_the_same_response(
    web_client, participant, queued, django_capture_on_commit_callbacks
):
    known, _ = post_reset(web_client, participant.user.email, django_capture_on_commit_callbacks)
    unknown, _ = post_reset(web_client, "nie-ma-takiego@example.test", django_capture_on_commit_callbacks)

    assert unknown.status_code == known.status_code == 302
    assert unknown["Location"] == known["Location"] == SENT_URL
    assert unknown.content == known.content
    # Jedyna różnica jest poza odpowiedzią: list zakolejkowano tylko dla adresu z kontem.
    assert len(queued.calls) == 1
    assert queued.calls[0][0][2] == [participant.user.email]


def test_a_broker_failure_does_not_turn_a_known_address_into_an_error(
    web_client, participant, monkeypatch, django_capture_on_commit_callbacks, caplog
):
    """Niedostępny Redis: 302 jak dla adresu bez konta, a nie 500 – inaczej błąd byłby wyrocznią."""
    monkeypatch.setattr("apps.core.tasks.send_mail_task", Recorder(error=ConnectionError("redis down")))

    response, _ = post_reset(web_client, participant.user.email, django_capture_on_commit_callbacks)

    assert response.status_code == 302
    assert response["Location"] == SENT_URL
    assert mail.outbox == []
    # Log po kluczu konta, nie po adresie – ten sam wybór, co w ``PasswordResetForm`` Django.
    assert f"konta {participant.user.pk}" in caplog.text
    assert participant.user.email not in caplog.text


def test_an_inactive_account_enqueues_nothing(
    web_client, participant, queued, django_capture_on_commit_callbacks
):
    participant.user.is_active = False
    participant.user.save(update_fields=["is_active"])

    response, callbacks = post_reset(web_client, participant.user.email, django_capture_on_commit_callbacks)

    assert response["Location"] == SENT_URL
    assert callbacks == []
    assert queued.calls == []


# --- droga koordynatora --------------------------------------------------------------------------


def test_the_coordinator_reset_goes_through_the_same_queue(
    web_client, coordinator, participant, queued, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = web_client.post(f"/coordinator/accounts/{participant.user.pk}/password-reset/")

    assert response.status_code == 302
    assert mail.outbox == []
    assert queued.calls == []
    assert len(callbacks) == 1

    callbacks[0]()

    assert len(queued.calls) == 1
    args, kwargs = queued.calls[0]
    assert args[0] == "Reset hasła – Olimpiada Kwantowa"
    assert args[2] == [participant.user.email]
    assert RESET_LINK.search(args[1])
    assert kwargs["html_message"]
