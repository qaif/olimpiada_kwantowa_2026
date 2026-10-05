"""Odbicia po stronie aplikacji (MAIL-02 § 2.3–2.8): zapis, baner, reset, wstrzymanie, panel, RODO."""

from __future__ import annotations

import smtplib
from datetime import timedelta

import pytest
from django.core import mail
from django.core.mail import EmailMessage
from django.utils import timezone

from apps.accounts.models import Membership, MessageBroadcast
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.core.models import AuditLog
from apps.email_delivery import services
from apps.email_delivery.backends import TrackingSMTPBackend
from apps.email_delivery.bounces import Bounce
from apps.email_delivery.models import DeliveryStatus, Source

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/undeliverable-emails/"
CONFIRM_URL = "/account/email/deliverable/"
BANNER = "Nie możemy dostarczyć poczty na adres"


@pytest.fixture(autouse=True)
def tracking(settings):
    settings.EMAIL_BOUNCE_TRACKING = True


def hard(email: str, status: str = "5.1.1", reason: str = "550 5.1.1 user unknown") -> DeliveryStatus:
    return services.record_bounce(Bounce(email=email, hard=True, status=status, reason=reason))


def soft(email: str) -> DeliveryStatus:
    return services.record_bounce(Bounce(email=email, hard=False, status="4.2.2", reason="452 mailbox full"))


# --- zapis --------------------------------------------------------------------------------------


def test_hard_bounce_marks_the_address_once_and_counts():
    first = hard("Jan@Example.org")
    later = timezone.now() + timedelta(days=1)
    second = services.record_bounce(
        Bounce(email="jan@example.org", hard=True, status="5.1.1", reason="znowu"), now=later
    )

    assert first.email == "jan@example.org"
    assert second.undeliverable_at == first.undeliverable_at  # pierwsze odbicie zostaje
    assert second.hard_bounces == 2 and second.reason == "znowu"


def test_soft_bounces_are_counted_without_suppression():
    soft("ola@example.org")
    row = soft("ola@example.org")

    assert row.soft_bounces == 2 and row.undeliverable_at is None
    assert not services.is_suppressed("ola@example.org")


def test_garbage_address_is_ignored():
    assert services.record_bounce(Bounce(email="nie-adres", hard=True, status="", reason="")) is None
    assert not DeliveryStatus.objects.exists()


def test_without_tracking_nothing_is_suppressed_and_no_query_is_made(settings, django_assert_num_queries):
    hard("x@example.org")
    settings.EMAIL_BOUNCE_TRACKING = False
    with django_assert_num_queries(0):
        assert services.is_suppressed("x@example.org") is False
        assert services.suppressed_among(["x@example.org"]) == set()
        assert services.banner_status("x@example.org") is None


# --- odmowa relaya w backendzie -----------------------------------------------------------------


class FakeSMTP:
    def __init__(self, error=None, refused=None):
        self.error = error
        self.refused = refused or {}
        self.calls = 0

    def sendmail(self, from_addr, to_addrs, msg, *args, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.refused


def _send(connection, to=("kcadera@o2.plo",)) -> int:
    # Tak, jak tworzy go ``mail.mailers`` z ``MAILERS`` (alias + OPTIONS z config/settings/base.py).
    backend = TrackingSMTPBackend(
        alias="default", host="mail", port=587, username="", password="", timeout=10
    )
    backend.connection = connection
    return backend.send_messages([EmailMessage("Temat", "Treść", "noreply@platforma.test", list(to))])


def test_permanent_relay_refusal_is_recorded_and_not_retried():
    # Kształt odpowiedzi relaya sprawdzony 5.10.2026 na boky/postfix (z ``unknown_address_reject_code=550``;
    # w compose kod zostaje domyślny – 450 – i ta droga dotyczy innych twardych odmów 5.1.x).
    refusal = {
        "kcadera@o2.plo": (550, b"5.1.2 <kcadera@o2.plo>: Recipient address rejected: Domain not found")
    }
    sent = _send(FakeSMTP(error=smtplib.SMTPRecipientsRefused(refusal)))

    assert sent == 0  # bez wyjątku – zadanie nie będzie ponawiać
    row = DeliveryStatus.objects.get(email="kcadera@o2.plo")
    assert row.undeliverable_at is not None and row.status_code == "5.1.2" and row.source == Source.SMTP


def test_temporary_relay_refusal_raises_for_retry_and_counts_soft():
    refusal = {
        "kcadera@o2.plo": (450, b"4.1.2 <kcadera@o2.plo>: Recipient address rejected: Domain not found")
    }
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        _send(FakeSMTP(error=smtplib.SMTPRecipientsRefused(refusal)))

    row = DeliveryStatus.objects.get(email="kcadera@o2.plo")
    assert row.undeliverable_at is None and row.soft_bounces == 1


def test_policy_refusal_is_raised_and_not_blamed_on_the_address():
    # Przegląd PR #98, M1: ``554 5.7.1`` (np. pusty nadawca, nadawca spoza listy) to problem relaya
    # albo konfiguracji – wyjątek leci dalej (ponowienia, log, GlitchTip), adres zostaje czysty.
    refusal = {"jan@gmail.com": (554, b"5.7.1 <noreply@obca.test>: Sender address rejected: Access denied")}
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        _send(FakeSMTP(error=smtplib.SMTPRecipientsRefused(refusal)), to=("jan@gmail.com",))

    assert not DeliveryStatus.objects.exists()


def test_mixed_refusal_is_raised_but_the_hard_one_is_recorded():
    refusal = {
        "zly@gmail.com": (550, b"5.1.1 user unknown"),
        "jan@gmail.com": (554, b"5.7.1 Access denied"),
    }
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        _send(FakeSMTP(error=smtplib.SMTPRecipientsRefused(refusal)), to=("zly@gmail.com", "jan@gmail.com"))

    assert list(DeliveryStatus.objects.values_list("email", flat=True)) == ["zly@gmail.com"]


def test_refusal_without_a_status_code_is_raised():
    refusal = {"jan@gmail.com": (550, b"Requested action not taken: mailbox unavailable")}
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        _send(FakeSMTP(error=smtplib.SMTPRecipientsRefused(refusal)), to=("jan@gmail.com",))

    assert not DeliveryStatus.objects.exists()


def test_partial_refusal_is_recorded_and_the_rest_is_sent():
    connection = FakeSMTP(refused={"zly@o2.plo": (550, b"5.1.2 Domain not found")})

    assert _send(connection, to=("dobry@gmail.com", "zly@o2.plo")) == 1
    assert services.is_suppressed("zly@o2.plo")
    assert not DeliveryStatus.objects.filter(email="dobry@gmail.com").exists()


def test_backend_accepts_the_production_mailer_options(settings):
    from django.core.mail import mailers

    settings.MAILERS = {
        "default": {
            "BACKEND": "apps.email_delivery.backends.TrackingSMTPBackend",
            "OPTIONS": {
                "host": "mail",
                "port": 587,
                "username": "",
                "password": "",
                "use_tls": False,
                "use_ssl": False,
                "timeout": 10,
            },
        }
    }

    backend = mailers["default"]

    assert isinstance(backend, TrackingSMTPBackend) and backend.host == "mail"


# --- zadanie skrzynki ---------------------------------------------------------------------------


def test_mailbox_task_records_real_dsns(settings, tmp_path):
    from pathlib import Path

    from apps.email_delivery.tasks import process_bounce_mailbox

    (tmp_path / "new").mkdir()
    source = Path(__file__).with_name("dsn") / "postfix_gmail_5.1.1.eml"
    (tmp_path / "new" / "1").write_bytes(source.read_bytes())
    settings.MAIL_BOUNCE_MAILDIR = str(tmp_path)
    settings.MAIL_BOUNCE_REPORTING_MTA = "mail.platforma.test"

    stats = process_bounce_mailbox()

    assert stats["hard"] == 1
    row = DeliveryStatus.objects.get(email="nie.istnieje.2026@gmail.com")
    assert row.source == Source.DSN and row.status_code == "5.1.1"


def test_mailbox_task_ignores_reports_of_another_mta(settings, tmp_path):
    from pathlib import Path

    from apps.email_delivery.tasks import process_bounce_mailbox

    (tmp_path / "new").mkdir()
    source = Path(__file__).with_name("dsn") / "postfix_gmail_5.1.1.eml"
    (tmp_path / "new" / "1").write_bytes(source.read_bytes())
    settings.MAIL_BOUNCE_MAILDIR = str(tmp_path)
    settings.MAIL_BOUNCE_REPORTING_MTA = ""
    settings.SITE_DOMAIN = "olimpiadakwantowa.pl"  # relay: mail.olimpiadakwantowa.pl ≠ mail.platforma.test

    stats = process_bounce_mailbox()

    assert stats["ignored"] == 1 and not DeliveryStatus.objects.exists()


def test_mailbox_task_is_idle_without_tracking(settings, tmp_path):
    from apps.email_delivery.tasks import process_bounce_mailbox

    settings.EMAIL_BOUNCE_TRACKING = False
    assert process_bounce_mailbox() == {}


# --- wstrzymanie listów nieobowiązkowych --------------------------------------------------------


def test_queue_mail_skips_non_essential_mail_to_a_bounced_address(django_capture_on_commit_callbacks):
    from apps.accounts.activation import queue_mail

    hard("odbija@example.org")
    with django_capture_on_commit_callbacks(execute=True):
        queue_mail("Forum", "Nowa odpowiedź", "odbija@example.org", essential=False)
        queue_mail("Forum", "Nowa odpowiedź", "dziala@example.org", essential=False)
        queue_mail("Aktywacja", "Link", "odbija@example.org")

    assert sorted((message.subject, message.to[0]) for message in mail.outbox) == [
        ("Aktywacja", "odbija@example.org"),
        ("Forum", "dziala@example.org"),
    ]


def test_broadcast_skips_bounced_addresses_but_finishes(competition):
    from apps.accounts.messaging import send_broadcast_chunk

    hard("odbija@example.org")
    broadcast = MessageBroadcast.objects.create(
        competition=competition, group="CUSTOM", subject="Komunikat", body="Treść", recipient_count=2
    )

    queued = send_broadcast_chunk(broadcast.pk, ["odbija@example.org", "dziala@example.org"])

    broadcast.refresh_from_db()
    assert queued == 1
    assert [message.to for message in mail.outbox] == [["dziala@example.org"]]
    assert broadcast.sent_count == 2 and broadcast.status == "SENT"


# --- baner i potwierdzenie ----------------------------------------------------------------------


def test_banner_shows_for_the_owner_and_confirm_clears_it(client, competition):
    user = UserFactory(email="uczen@example.org")
    hard("uczen@example.org")
    client.force_login(user)

    page = client.get("/account/profile/")
    response = client.post(CONFIRM_URL, {"next": "/account/profile/"})
    after = client.get("/account/profile/")

    assert BANNER in page.content.decode()
    assert response.status_code == 302 and response["Location"] == "/account/profile/"
    assert BANNER not in after.content.decode()
    assert not DeliveryStatus.objects.filter(email="uczen@example.org").exists()
    assert AuditLog.objects.filter(action="email.undeliverable_confirmed", actor=user).exists()


def test_banner_is_absent_for_others_and_without_tracking(client, competition, settings):
    hard("ktos-inny@example.org")
    client.force_login(UserFactory(email="ja@example.org"))
    assert BANNER not in client.get("/account/profile/").content.decode()

    client.force_login(UserFactory(email="odbity@example.org"))
    hard("odbity@example.org")
    settings.EMAIL_BOUNCE_TRACKING = False
    assert BANNER not in client.get("/account/profile/").content.decode()


def test_confirm_ignores_foreign_redirects(client, competition):
    client.force_login(UserFactory(email="uczen@example.org"))
    hard("uczen@example.org")

    response = client.post(CONFIRM_URL, {"next": "https://zly.example/"})

    assert response.status_code == 302 and response["Location"] == "/account/email/"


def test_confirm_requires_login(client, competition):
    assert client.post(CONFIRM_URL).status_code == 302
    assert client.get(CONFIRM_URL).status_code in {302, 405}


# --- reset przy zmianie i usunięciu adresu --------------------------------------------------------


def test_changing_the_account_address_clears_the_old_state():
    user = UserFactory(email="stary@example.org")
    hard("stary@example.org")

    user.email = "nowy@example.org"
    user.save()

    assert not DeliveryStatus.objects.filter(email="stary@example.org").exists()


def test_login_style_partial_save_does_not_touch_the_state():
    user = UserFactory(email="stary@example.org")
    hard("stary@example.org")

    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])

    assert DeliveryStatus.objects.filter(email="stary@example.org").exists()


def test_deleting_the_account_clears_the_state():
    user = UserFactory(email="usuwany@example.org")
    hard("usuwany@example.org")

    user.delete()

    assert not DeliveryStatus.objects.exists()


# --- panel koordynatora -------------------------------------------------------------------------


def test_coordinator_list_csv_and_clear_are_scoped_to_the_competition(client, competition, other_competition):
    coordinator = CoordinatorFactory()
    mine = ParticipantFactory(user=UserFactory(email="moj@example.org", first_name="Ola"))
    foreign = UserFactory(email="obcy@example.org")
    Membership.objects.create(user=foreign, competition=other_competition, role="participant")
    hard("moj@example.org")
    foreign_row = hard("obcy@example.org")
    hard("bez-konta@example.org")
    client.force_login(coordinator)

    page = client.get(LIST_URL)
    csv = client.get(LIST_URL + "?format=csv")
    denied = client.post(f"/coordinator/undeliverable-emails/{foreign_row.pk}/clear/")
    cleared = client.post(
        f"/coordinator/undeliverable-emails/{DeliveryStatus.objects.get(email='moj@example.org').pk}/clear/"
    )

    body = page.content.decode()
    assert page.status_code == 200
    assert mine.user.email in body and "obcy@example.org" not in body and "bez-konta@example.org" not in body
    assert "Adresy niedoręczalne" in body  # pozycja menu przy włączonym śledzeniu
    content = b"".join(csv.streaming_content).decode("utf-8-sig")
    assert "moj@example.org" in content and "obcy@example.org" not in content and "5.1.1" in content
    assert denied.status_code == 404
    assert cleared.status_code == 302
    assert not DeliveryStatus.objects.filter(email="moj@example.org").exists()
    assert DeliveryStatus.objects.filter(email="obcy@example.org").exists()
    assert {"email.undeliverable_exported", "email.undeliverable_cleared"} <= set(
        AuditLog.objects.values_list("action", flat=True)
    )


def test_coordinator_screen_needs_the_role_and_the_switch(client, competition, settings):
    client.force_login(UserFactory())
    assert client.get(LIST_URL).status_code == 403

    client.force_login(CoordinatorFactory())
    settings.EMAIL_BOUNCE_TRACKING = False
    assert client.get(LIST_URL).status_code == 404


def test_menu_has_no_new_item_without_tracking(client, competition, settings):
    settings.EMAIL_BOUNCE_TRACKING = False
    client.force_login(CoordinatorFactory())
    assert "Adresy niedoręczalne" not in client.get("/coordinator/").content.decode()


# --- RODO ---------------------------------------------------------------------------------------


def test_export_section_describes_the_account_address():
    user = UserFactory(email="eksport@example.org")
    assert services.export_section(user) == {"adres_niedoreczalny": False}

    hard("eksport@example.org")
    section = services.export_section(user)

    assert section["adres_niedoreczalny"] is True and section["kod_stanu"] == "5.1.1"


def test_account_export_contains_the_section():
    from apps.accounts.data_export import export_payload

    user = UserFactory(email="eksport@example.org")
    hard("eksport@example.org")

    assert export_payload(user)["doreczalnosc_poczty"]["adres_niedoreczalny"] is True


def test_register_row_is_conditional(settings, competition):
    from apps.accounts.processing_register import REGISTER_VERSION, activities_for

    assert REGISTER_VERSION == "1.22"
    assert "doreczalnosc-poczty" in {activity.key for activity in activities_for(competition)}
    settings.EMAIL_BOUNCE_TRACKING = False
    assert "doreczalnosc-poczty" not in {activity.key for activity in activities_for(competition)}


def test_retention_purges_old_rows():
    hard("stare@example.org")
    hard("nowe@example.org")
    DeliveryStatus.objects.filter(email="stare@example.org").update(
        updated_at=timezone.now() - timedelta(days=services.RETENTION_DAYS + 1)
    )

    assert services.purge_expired() == 1
    assert list(DeliveryStatus.objects.values_list("email", flat=True)) == ["nowe@example.org"]
