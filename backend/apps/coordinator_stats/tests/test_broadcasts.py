"""Raport wysyłki komunikatu (STATS-02 § 2): liczniki porcji, przypisanie odbić, raport, historia, RODO."""

from __future__ import annotations

import smtplib
from datetime import timedelta
from pathlib import Path

import pytest
from django.core import mail
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CompetitionRole, MessageBroadcast
from apps.accounts.tests.factories import CoordinatorFactory, UserFactory
from apps.coordinator_stats import broadcasts, domains
from apps.coordinator_stats.broadcasts import HEADER, headers_for
from apps.coordinator_stats.models import BounceKind, BroadcastBounce, BroadcastDomainTally
from apps.email_delivery import services
from apps.email_delivery.backends import TrackingSMTPBackend
from apps.email_delivery.bounces import Bounce
from apps.email_delivery.models import DeliveryStatus, Source
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

DSN_DIR = Path(__file__).resolve().parents[2] / "email_delivery" / "tests" / "dsn"
OUR_MTA = "mail.platforma.test"
S3150 = (
    "550 5.7.1 Unfortunately, messages from [169.58.242.197] weren't sent. Please contact your Internet "
    "service provider since part of their network is on our block list (S3150)."
)


@pytest.fixture(autouse=True)
def tracking(settings, tmp_path):
    settings.EMAIL_BOUNCE_TRACKING = True
    settings.MAIL_BOUNCE_MAILDIR = str(tmp_path)
    settings.MAIL_BOUNCE_REPORTING_MTA = OUR_MTA
    (tmp_path / "new").mkdir()


@pytest.fixture
def broadcast(competition):
    return MessageBroadcast.objects.create(
        competition=competition, group="CUSTOM", subject="Termin etapu I", body="Treść", recipient_count=4
    )


@pytest.fixture
def coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


def tally(broadcast) -> dict:
    return {row.domain_group: row for row in BroadcastDomainTally.objects.filter(broadcast=broadcast)}


def dsn_with_header(broadcast_id: int, *, header: str | None = None) -> bytes:
    raw = (DSN_DIR / "postfix_gmail_5.1.1.eml").read_bytes().replace(b"\r\n", b"\n")
    marker = b"Content-Type: message/rfc822\nContent-Transfer-Encoding: 8bit\n\n"
    value = header if header is not None else headers_for(broadcast_id)[HEADER]
    return raw.replace(marker, marker + f"{HEADER}: {value}\n".encode(), 1)


def policy_dsn(broadcast_id: int, recipient: str = "uczen@outlook.com") -> bytes:
    return (
        "Return-Path: <>\n"
        f"From: Mail Delivery System <MAILER-DAEMON@{OUR_MTA}>\n"
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\n'
        "\n--B\nContent-Type: text/plain\n\nopis\n"
        "--B\nContent-Type: message/delivery-status\n\n"
        f"Reporting-MTA: dns; {OUR_MTA}\n\n"
        f"Final-Recipient: rfc822; {recipient}\nAction: failed\nStatus: 5.7.1\n"
        f"Diagnostic-Code: smtp; {S3150}\n"
        "\n--B\nContent-Type: message/rfc822\n\n"
        f"Subject: Termin etapu I\nTo: {recipient}\n{HEADER}: {headers_for(broadcast_id)[HEADER]}\n\ntresc\n"
        "\n--B--\n"
    ).encode()


def run_mailbox(tmp_path, *files: bytes) -> dict:
    from apps.email_delivery.tasks import process_bounce_mailbox

    for index, raw in enumerate(files):
        (tmp_path / "new" / f"{index}").write_bytes(raw)
    return process_bounce_mailbox()


# --- liczniki porcji wysyłki ----------------------------------------------------------------------


def test_chunk_counts_queued_and_suppressed_per_domain_and_signs_every_letter(broadcast):
    from apps.accounts.messaging import send_broadcast_chunk

    services.record_bounce(Bounce(email="odbija@wp.pl", hard=True, status="5.1.1", reason="user unknown"))
    recipients = ["a@gmail.com", "b@gmail.com", "c@outlook.com", "odbija@wp.pl"]

    assert send_broadcast_chunk(broadcast.pk, recipients) == 3

    rows = tally(broadcast)
    assert rows[domains.GMAIL].queued == 2 and rows[domains.MICROSOFT].queued == 1
    assert rows[domains.PORTALS].queued == 0 and rows[domains.PORTALS].suppressed == 1
    assert sorted(message.to[0] for message in mail.outbox) == ["a@gmail.com", "b@gmail.com", "c@outlook.com"]
    assert {broadcasts.broadcast_id_from(message.extra_headers[HEADER]) for message in mail.outbox} == {
        broadcast.pk
    }
    broadcast.refresh_from_db()
    assert broadcast.sent_count == 4 and broadcast.status == "SENT"


def test_tally_failure_does_not_fail_the_chunk(broadcast, monkeypatch):
    from apps.accounts.messaging import send_broadcast_chunk

    def broken(*args, **kwargs):
        raise RuntimeError("baza liczników leży")

    monkeypatch.setattr(broadcasts, "record_chunk", broken)

    assert send_broadcast_chunk(broadcast.pk, ["a@gmail.com"]) == 1
    assert len(mail.outbox) == 1


# --- przypisanie odbić ----------------------------------------------------------------------------


def test_relay_dsn_is_assigned_to_the_broadcast(broadcast, tmp_path):
    stats = run_mailbox(tmp_path, dsn_with_header(broadcast.pk))

    assert stats["hard"] == 1
    bounce = BroadcastBounce.objects.get(broadcast=broadcast)
    assert bounce.email == "nie.istnieje.2026@gmail.com" and bounce.kind == BounceKind.HARD
    assert bounce.source == Source.DSN and bounce.status_code == "5.1.1"
    assert tally(broadcast)[domains.GMAIL].bounced_hard == 1
    # MAIL-02 bez zmian: twarde odbicie zapisane także w stanie adresu.
    assert DeliveryStatus.objects.filter(email="nie.istnieje.2026@gmail.com").exists()


def test_microsoft_s3150_rejection_is_counted_as_policy_without_blaming_the_address(broadcast, tmp_path):
    run_mailbox(tmp_path, policy_dsn(broadcast.pk))

    bounce = BroadcastBounce.objects.get(broadcast=broadcast)
    assert (
        bounce.kind == BounceKind.POLICY
        and bounce.microsoft_s3150
        and bounce.domain_group == domains.MICROSOFT
    )
    row = tally(broadcast)[domains.MICROSOFT]
    assert row.bounced_policy == 1 and row.bounced_s3150 == 1 and row.bounced_hard == 0
    assert not DeliveryStatus.objects.filter(email="uczen@outlook.com").exists()


def test_forged_or_missing_header_assigns_nothing(broadcast, tmp_path):
    forged = f"{broadcast.pk}:podrobiony-podpis"
    raw = (DSN_DIR / "postfix_gmail_5.1.1.eml").read_bytes()

    run_mailbox(tmp_path, dsn_with_header(broadcast.pk, header=forged), raw)

    assert not BroadcastBounce.objects.exists() and not BroadcastDomainTally.objects.exists()


def test_header_of_a_deleted_broadcast_is_ignored(competition, tmp_path):
    gone = MessageBroadcast.objects.create(competition=competition, group="CUSTOM", subject="x", body="y")
    raw = dsn_with_header(gone.pk)
    gone.delete()

    run_mailbox(tmp_path, raw)

    assert not BroadcastBounce.objects.exists()


def test_repeated_bounce_of_one_address_is_one_row_and_the_worse_kind_wins(broadcast):
    headers = headers_for(broadcast.pk)
    delayed = Bounce("x@wp.pl", hard=False, status="4.4.1", reason="connect timed out")
    final = Bounce("x@wp.pl", hard=True, status="5.1.1", reason="user unknown")

    assert broadcasts.record_bounces([delayed], headers, source=Source.DSN) == 1
    assert broadcasts.record_bounces([final], headers, source=Source.DSN) == 0
    assert broadcasts.record_bounces([delayed], headers, source=Source.DSN) == 0

    (row,) = BroadcastBounce.objects.filter(broadcast=broadcast)
    assert row.kind == BounceKind.HARD and row.status_code == "5.1.1"
    counters = tally(broadcast)[domains.PORTALS]
    assert counters.bounced_soft == 0 and counters.bounced_hard == 1


class FakeSMTP:
    def __init__(self, error=None, refused=None):
        self.error = error
        self.refused = refused or {}

    def sendmail(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return self.refused


def test_relay_refusal_during_sending_is_assigned_from_the_letter_headers(broadcast):
    refusal = {"ktos@hotmail.com": (550, S3150.encode())}
    backend = TrackingSMTPBackend(
        alias="default", host="mail", port=587, username="", password="", timeout=10
    )
    backend.connection = FakeSMTP(refused=refusal)
    message = EmailMessage(
        "Termin",
        "Treść",
        "noreply@platforma.test",
        ["ktos@hotmail.com", "a@gmail.com"],
        headers=headers_for(broadcast.pk),
    )

    backend.send_messages([message])

    bounce = BroadcastBounce.objects.get(broadcast=broadcast)
    assert bounce.source == Source.SMTP and bounce.kind == BounceKind.POLICY and bounce.microsoft_s3150


def test_permanent_refusal_of_everyone_is_still_assigned(broadcast):
    refusal = {
        "kcadera@o2.plo": (550, b"5.1.2 <kcadera@o2.plo>: Recipient address rejected: Domain not found")
    }
    backend = TrackingSMTPBackend(
        alias="default", host="mail", port=587, username="", password="", timeout=10
    )
    backend.connection = FakeSMTP(error=smtplib.SMTPRecipientsRefused(refusal))
    message = EmailMessage(
        "T", "B", "noreply@platforma.test", ["kcadera@o2.plo"], headers=headers_for(broadcast.pk)
    )

    assert backend.send_messages([message]) == 0
    assert BroadcastBounce.objects.get(broadcast=broadcast).kind == BounceKind.HARD


# --- raport i historia ----------------------------------------------------------------------------


def test_report_shows_counts_domains_and_the_s3150_warning(coordinator_client, broadcast, tmp_path):
    from apps.accounts.messaging import send_broadcast_chunk

    send_broadcast_chunk(broadcast.pk, ["a@gmail.com", "uczen@outlook.com", "c@zsp1.edu.pl", "d@firma.pl"])
    run_mailbox(tmp_path, policy_dsn(broadcast.pk))

    response = coordinator_client.get(reverse("web:coordinator-messages-report", args=[broadcast.pk]))
    html = response.content.decode()

    assert response.status_code == 200
    report = response.context["report"]
    assert (
        report["totals"]["queued"] == 4
        and report["totals"]["bounced"] == 1
        and report["totals"]["s3150"] == 1
    )
    assert [row["group"] for row in report["rows"]] == [
        domains.GMAIL,
        domains.MICROSOFT,
        domains.SCHOOL,
        domains.OTHER,
    ]
    assert "S3150" in html and "uczen@outlook.com" in html
    assert "cs-bounced" in html  # słupek odbitych


def test_report_without_data_says_so(coordinator_client, broadcast):
    response = coordinator_client.get(reverse("web:coordinator-messages-report", args=[broadcast.pk]))

    assert response.status_code == 200 and response.context["report"]["has_data"] is False


def test_report_of_another_competition_is_404(coordinator_client, other_competition):
    foreign = MessageBroadcast.objects.create(
        competition=other_competition, group="CUSTOM", subject="x", body="y"
    )

    assert (
        coordinator_client.get(reverse("web:coordinator-messages-report", args=[foreign.pk])).status_code
        == 404
    )


def test_report_needs_the_coordinator_role(client_for, competition, broadcast):
    client = client_for(competition)
    client.force_login(UserFactory())

    assert client.get(reverse("web:coordinator-messages-report", args=[broadcast.pk])).status_code == 403


def test_history_shows_skipped_and_bounced_with_one_query(
    coordinator_client, competition, broadcast, tmp_path
):
    from apps.accounts.messaging import recent_broadcasts, send_broadcast_chunk

    services.record_bounce(Bounce(email="odbija@wp.pl", hard=True, status="5.1.1", reason="user unknown"))
    send_broadcast_chunk(broadcast.pk, ["a@gmail.com", "uczen@outlook.com", "odbija@wp.pl"])
    run_mailbox(tmp_path, policy_dsn(broadcast.pk))
    legacy = MessageBroadcast.objects.create(
        competition=competition, group="CUSTOM", subject="stary", body="y", sent_count=3
    )

    rows = broadcasts.attach_summaries(recent_broadcasts(competition))
    by_pk = {row.pk: row for row in rows}
    assert by_pk[broadcast.pk].delivery == {"queued": 2, "suppressed": 1, "bounced": 1, "s3150": 1}
    assert by_pk[legacy.pk].delivery is None

    html = coordinator_client.get(reverse("web:coordinator-messages")).content.decode()
    assert reverse("web:coordinator-messages-report", args=[broadcast.pk]) in html and "S3150: 1" in html


def test_history_summaries_cost_one_query(competition, django_assert_num_queries):
    items = [
        MessageBroadcast.objects.create(competition=competition, group="CUSTOM", subject=str(i), body="y")
        for i in range(5)
    ]
    for item in items:
        broadcasts.record_chunk(item.pk, queued=["a@gmail.com"], suppressed=[])

    with django_assert_num_queries(1):
        broadcasts.attach_summaries(items)


# --- RODO ----------------------------------------------------------------------------------------


def test_retention_deletes_addresses_but_keeps_counters(broadcast):
    broadcasts.record_bounces(
        [Bounce("x@wp.pl", hard=True, status="5.1.1", reason="")], headers_for(broadcast.pk)
    )
    BroadcastBounce.objects.update(updated_at=timezone.now() - timedelta(days=400))

    from apps.coordinator_stats.tasks import purge_broadcast_bounces

    assert purge_broadcast_bounces() == 1
    assert not BroadcastBounce.objects.exists()
    assert tally(broadcast)[domains.PORTALS].bounced_hard == 1


def test_changing_or_deleting_the_account_address_forgets_its_bounces(broadcast):
    user = UserFactory(email="stary@wp.pl")
    other = UserFactory(email="drugi@wp.pl")
    headers = headers_for(broadcast.pk)
    broadcasts.record_bounces(
        [
            Bounce("stary@wp.pl", hard=True, status="5.1.1", reason=""),
            Bounce("drugi@wp.pl", hard=True, status="5.1.1", reason=""),
        ],
        headers,
    )

    user.email = "nowy@wp.pl"
    user.save()
    other.delete()

    assert not BroadcastBounce.objects.exists()


def test_account_export_lists_broadcast_bounces(broadcast):
    from apps.accounts.data_export import export_payload

    user = UserFactory(email="eksport@wp.pl")
    broadcasts.record_bounces(
        [Bounce("eksport@wp.pl", hard=False, status="5.7.1", reason=S3150)], headers_for(broadcast.pk)
    )

    section = export_payload(user)["doreczalnosc_poczty"]

    assert section["odbicia_komunikatow"][0]["komunikat"] == "Termin etapu I"
    assert section["adres_niedoreczalny"] is False


def test_register_row_mentions_broadcast_bounces(competition):
    from apps.accounts.processing_register import activities_for

    (row,) = [activity for activity in activities_for(competition) if activity.key == "doreczalnosc-poczty"]
    assert any("komunikatów grupowych" in category for category in row.categories)
