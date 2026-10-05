"""Parser DSN, klasyfikacja odbić i skrzynka Maildir (MAIL-02 § 2.1–2.2).

Pliki ``dsn/*.eml`` to **prawdziwe** zawiadomienia z relaya: boky/postfix:v5.1.0-alpine z
``MAIL_BOUNCE_TARGET=capture`` (deploy/mail/docker-init.d/50-bounces.sh), 5.10.2026 – jedno po 550
5.1.1 od Gmaila, drugie po domenie bez MX/A (5.4.4). Adres odbiorcy Gmaila podmieniony.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from apps.email_delivery.bounces import Bounce, classify, clean_reason, parse_dsn, process_maildir

DSN_DIR = Path(__file__).with_name("dsn")


def _dsn(
    *,
    action="failed",
    status="5.1.1",
    diagnostic="smtp; 550 5.1.1 user unknown",
    return_path="<>",
    recipient="uczen@example.org",
    report_type="delivery-status",
) -> bytes:
    return (
        f"Return-Path: {return_path}\n"
        "From: Mail Delivery System <MAILER-DAEMON@mail.platforma.test>\n"
        "To: noreply@platforma.test\n"
        "Subject: Delayed Mail\n"
        "MIME-Version: 1.0\n"
        f'Content-Type: multipart/report; report-type={report_type}; boundary="B"\n'
        "\n"
        "--B\n"
        "Content-Type: text/plain\n"
        "\n"
        "opis\n"
        "--B\n"
        "Content-Type: message/delivery-status\n"
        "\n"
        "Reporting-MTA: dns; mail.platforma.test\n"
        "\n"
        f"Final-Recipient: rfc822; {recipient}\n"
        f"Action: {action}\n"
        f"Status: {status}\n"
        f"Diagnostic-Code: {diagnostic}\n"
        "\n"
        "--B--\n"
    ).encode()


def test_real_gmail_user_unknown_is_a_hard_bounce():
    bounces = parse_dsn((DSN_DIR / "postfix_gmail_5.1.1.eml").read_bytes())

    assert len(bounces) == 1
    bounce = bounces[0]
    assert bounce.email == "nie.istnieje.2026@gmail.com"
    assert bounce.hard is True
    assert bounce.status == "5.1.1"
    assert bounce.reason.startswith("550-5.1.1 The email account that you tried to reach does not exist.")
    assert "\n" not in bounce.reason


def test_real_postfix_domain_not_found_is_a_hard_bounce():
    (bounce,) = parse_dsn((DSN_DIR / "postfix_no_domain_5.4.4.eml").read_bytes())

    assert bounce.email == "jan.kowalski@nonexistent-domain-mail02-probe.com"
    assert bounce.hard is True
    assert bounce.status == "5.4.4"
    assert bounce.reason.startswith("Host or domain name not found.")


def test_delayed_notice_is_soft():
    (bounce,) = parse_dsn(_dsn(action="delayed", status="4.4.1", diagnostic="X-Postfix; connect timed out"))
    assert bounce.hard is False


def test_policy_rejection_is_soft():
    (bounce,) = parse_dsn(_dsn(status="5.7.1", diagnostic="smtp; 550 5.7.1 message rejected as spam"))
    assert bounce.hard is False


def test_mailbox_full_is_soft():
    (bounce,) = parse_dsn(_dsn(status="5.2.2", diagnostic="smtp; 552 5.2.2 mailbox full"))
    assert bounce.hard is False


def test_message_with_a_real_sender_is_not_a_dsn():
    # List aplikacji wysłany na noreply@ (np. ktoś się tym adresem zarejestrował) – nie odbicie.
    assert parse_dsn(_dsn(return_path="<noreply@platforma.test>")) is None


def test_other_reports_and_plain_mail_are_ignored():
    assert parse_dsn(_dsn(report_type="disposition-notification")) is None
    assert parse_dsn(b"Return-Path: <>\nSubject: hej\n\ntekst\n") is None


def test_delivered_action_is_not_a_bounce():
    assert parse_dsn(_dsn(action="delivered", status="2.0.0")) == []


@pytest.mark.parametrize(
    ("status", "diagnostic", "failed", "hard"),
    [
        ("5.1.1", "550 5.1.1 user unknown", True, True),
        ("5.1.2", "550 5.1.2 <x@o2.plo>: Recipient address rejected: Domain not found", True, True),
        ("5.1.10", "550 5.1.10 null MX", True, True),
        ("5.2.1", "550 5.2.1 The email account is disabled", True, True),
        ("5.4.4", "Host or domain name not found", True, True),
        ("5.1.8", "553 5.1.8 sender address rejected", True, False),
        ("5.7.1", "550 5.7.1 user unknown (policy)", True, False),
        ("4.1.2", "450 4.1.2 Domain not found", False, False),
        ("5.5.0", "550 5.5.0 Requested action not taken: mailbox unavailable", True, True),
        ("5.0.0", "550 5.0.0 something odd", True, False),
        ("", "550 No such user here", True, True),
        ("", "450 No such user here", True, False),
        ("", "554 delivery error", True, False),
        ("5.1.1", "", False, False),
    ],
)
def test_classification(status, diagnostic, failed, hard):
    assert classify(status, diagnostic, failed=failed) is hard


def test_clean_reason_drops_the_type_and_folds_lines():
    assert clean_reason("smtp; 550 5.1.1\n    user unknown") == "550 5.1.1 user unknown"
    assert len(clean_reason("x" * 900)) == 500


# --- skrzynka ------------------------------------------------------------------------------------


def _maildir(tmp_path: Path) -> Path:
    for sub in ("new", "cur", "tmp"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_process_maildir_records_and_deletes(tmp_path):
    root = _maildir(tmp_path)
    (root / "new" / "1.gmail").write_bytes((DSN_DIR / "postfix_gmail_5.1.1.eml").read_bytes())
    (root / "new" / "2.domain").write_bytes((DSN_DIR / "postfix_no_domain_5.4.4.eml").read_bytes())
    (root / "new" / "3.plain").write_bytes(b"Return-Path: <noreply@x.test>\nSubject: hej\n\nlist\n")
    (root / "new" / "4.soft").write_bytes(_dsn(action="delayed", status="4.4.1"))
    recorded: list[Bounce] = []

    stats = process_maildir(root, record=recorded.append)

    assert stats == {"files": 4, "hard": 2, "soft": 1, "ignored": 1, "errors": 0}
    assert {bounce.email for bounce in recorded if bounce.hard} == {
        "nie.istnieje.2026@gmail.com",
        "jan.kowalski@nonexistent-domain-mail02-probe.com",
    }
    assert list((root / "new").iterdir()) == []  # zawiadomienie z kopią listu nie zostaje na dysku


def test_failing_file_moves_to_cur_and_expires(tmp_path):
    root = _maildir(tmp_path)
    (root / "new" / "zly").write_bytes(_dsn())

    def boom(_bounce):
        raise RuntimeError("baza leży")

    stats = process_maildir(root, record=boom)

    assert stats["errors"] == 1
    kept = list((root / "cur").iterdir())
    assert [item.name for item in kept] == ["zly:2,"]
    # Drugi przebieg nie bierze go ponownie; po tygodniu znika.
    assert process_maildir(root, record=boom)["files"] == 0
    old = time.time() - 8 * 86400
    os.utime(kept[0], (old, old))
    process_maildir(root, record=boom)
    assert list((root / "cur").iterdir()) == []


def test_missing_maildir_is_not_an_error(tmp_path):
    assert process_maildir(tmp_path / "brak", record=lambda bounce: None)["files"] == 0
