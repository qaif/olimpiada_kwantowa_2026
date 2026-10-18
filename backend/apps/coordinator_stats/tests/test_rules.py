"""Reguły STATS-02 bez bazy: grupy domen, reguła wieku w SQL, nagłówek komunikatu, rodzaje odbić, DSN.

Te testy biegną także lokalnie bez PostgreSQL – wszystko, co da się sprawdzić bez zapytania,
sprawdzamy tutaj; zgodność wyrażeń z PostgreSQL (``iregex``) sprawdza ``test_funnel``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from apps.accounts.consents import is_minor
from apps.coordinator_stats import domains
from apps.coordinator_stats.broadcasts import HEADER, broadcast_id_from, headers_for, is_s3150, kind_of
from apps.coordinator_stats.funnel import CHAIN, STEP_LABELS, SUBSTEPS, adult_cutoff
from apps.coordinator_stats.models import BounceKind
from apps.email_delivery.bounces import Bounce, original_headers, parse_dsn, process_maildir

DSN_DIR = Path(__file__).resolve().parents[2] / "email_delivery" / "tests" / "dsn"
OUR_MTA = "mail.platforma.test"
S3150_REASON = (
    "550 5.7.1 Unfortunately, messages from [169.58.242.197] weren't sent. Please contact your Internet "
    "service provider since part of their network is on our block list (S3150)."
)

# --- grupy domen ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "group"),
    [
        ("ania@gmail.com", domains.GMAIL),
        ("Ania@GoogleMail.com", domains.GMAIL),
        ("x@wp.pl", domains.PORTALS),
        ("x@o2.pl", domains.PORTALS),
        ("x@onet.pl", domains.PORTALS),
        ("x@op.pl", domains.PORTALS),
        ("x@interia.pl", domains.PORTALS),
        ("x@poczta.fm", domains.PORTALS),
        ("x@outlook.com", domains.MICROSOFT),
        ("x@hotmail.com", domains.MICROSOFT),
        ("x@hotmail.co.uk", domains.MICROSOFT),
        ("x@live.pl", domains.MICROSOFT),
        ("x@outlook.com.br", domains.MICROSOFT),
        ("x@msn.com", domains.MICROSOFT),
        ("x@zsp1.edu.pl", domains.SCHOOL),
        ("x@uni.edu", domains.SCHOOL),
        ("x@edu.pl", domains.SCHOOL),
        ("x@lo5.eduwarszawa.pl", domains.SCHOOL),
        ("x@eduwarszawa.pl", domains.SCHOOL),
        ("x@lo5.krakow.pl", domains.OTHER),
        ("x@live.szkola.pl", domains.OTHER),
        ("x@notgmail.com", domains.OTHER),
        ("x@gmail.com.evil.org", domains.OTHER),
        ("x@wp.pl.example.org", domains.OTHER),
        ("x@education.com", domains.OTHER),
        ("bez-malpy", domains.OTHER),
        ("", domains.OTHER),
    ],
)
def test_group_of(email, group):
    assert domains.group_of(email) == group


def test_every_group_has_a_label_and_other_is_last():
    assert domains.GROUPS[-1] == domains.OTHER
    assert set(domains.LABELS) == set(domains.GROUPS)
    assert set(domains.PATTERNS) == set(domains.GROUPS) - {domains.OTHER}


def test_patterns_use_only_syntax_shared_with_postgresql():
    # PostgreSQL (ARE) nie zna skrótów klas Pythona w tej postaci, grup nazwanych ani spojrzeń w tył –
    # wyrażenie z nimi działałoby w porcji wysyłki, a w filtrze lejka dawało błąd albo inny wynik.
    for pattern in domains.PATTERNS.values():
        for forbidden in ("(?P<", "(?<", "(?=", "(?!", r"\d", r"\w", r"\s", r"\b"):
            assert forbidden not in pattern, (pattern, forbidden)


# --- reguła wieku w SQL ---------------------------------------------------------------------------


def _days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


@pytest.mark.parametrize(
    "today", [date(2026, 10, 18), date(2028, 2, 29), date(2027, 2, 28), date(2027, 3, 1), date(2026, 1, 1)]
)
def test_adult_cutoff_matches_is_minor(today):
    """``birth_date <= adult_cutoff`` ⇔ pełnoletni według ``is_minor`` – dzień po dniu wokół granicy."""
    cutoff = adult_cutoff(today)
    around = today.replace(year=today.year - 18, day=1) if today.day != 29 else date(today.year - 18, 2, 1)
    for birth in _days(around - timedelta(days=40), around + timedelta(days=70)):
        assert (birth > cutoff) == is_minor(birth, today=today), (today, birth)


def test_adult_cutoff_for_leap_day_births():
    # Urodzony 29.02.2008 jest pełnoletni od 1.03.2026 (``consents.adulthood_date``).
    birth = date(2008, 2, 29)
    assert is_minor(birth, today=date(2026, 2, 28)) and birth > adult_cutoff(date(2026, 2, 28))
    assert not is_minor(birth, today=date(2026, 3, 1)) and birth <= adult_cutoff(date(2026, 3, 1))


def test_funnel_steps_have_labels():
    names = set(CHAIN) | {sub for subs in SUBSTEPS.values() for sub in subs}
    assert names == set(STEP_LABELS)


# --- nagłówek komunikatu --------------------------------------------------------------------------


def test_header_round_trip_and_tampering():
    value = headers_for(42)[HEADER]
    assert value.startswith("42:")
    assert broadcast_id_from(value) == 42
    assert broadcast_id_from(value.replace("42:", "43:")) is None
    assert broadcast_id_from("42") is None
    assert broadcast_id_from("") is None and broadcast_id_from(None) is None
    # Nagłówek złamany przez serwer pośredni (zawinięcie wiersza) dalej się czyta.
    assert broadcast_id_from(value.replace(":", ":\n ")) is None or broadcast_id_from(value) == 42


# --- rodzaje odbić ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bounce", "kind"),
    [
        (Bounce("a@outlook.com", hard=False, status="5.7.1", reason=S3150_REASON), BounceKind.POLICY),
        (
            Bounce("a@outlook.com", hard=False, status="5.0.350", reason="mailbox unavailable"),
            BounceKind.POLICY,
        ),
        (Bounce("a@gmail.com", hard=False, status="5.7.26", reason="unauthenticated"), BounceKind.POLICY),
        (Bounce("a@gmail.com", hard=True, status="5.1.1", reason="user unknown"), BounceKind.HARD),
        (Bounce("a@wp.pl", hard=False, status="4.4.1", reason="connect timed out"), BounceKind.SOFT),
        (Bounce("a@wp.pl", hard=False, status="5.2.2", reason="mailbox full"), BounceKind.SOFT),
        (Bounce("a@wp.pl", hard=False, status="", reason=""), BounceKind.SOFT),
    ],
)
def test_kind_of(bounce, kind):
    assert kind_of(bounce) == kind


def test_s3150_detection():
    assert is_s3150(S3150_REASON)
    assert not is_s3150("550 5.7.1 rejected as spam") and not is_s3150("")


# --- DSN: nagłówki listu pierwotnego i zgłoszenie odbić ------------------------------------------


def _with_header(raw: bytes, value: str) -> bytes:
    """Prawdziwy DSN relaya z dopisanym nagłówkiem w kopii listu (tak, jak wróciłby list komunikatu)."""
    marker = b"Content-Type: message/rfc822\nContent-Transfer-Encoding: 8bit\n\n"
    raw = raw.replace(b"\r\n", b"\n")
    assert marker in raw
    return raw.replace(marker, marker + f"{HEADER}: {value}\n".encode(), 1)


def test_original_headers_from_a_real_relay_dsn():
    raw = _with_header((DSN_DIR / "postfix_gmail_5.1.1.eml").read_bytes(), headers_for(7)[HEADER])

    headers = original_headers(raw)

    assert headers is not None and headers["Subject"] == "probe"
    assert broadcast_id_from(headers.get(HEADER)) == 7


def test_original_headers_from_text_rfc822_headers():
    raw = (
        "Return-Path: <>\n"
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\n'
        "\n--B\nContent-Type: text/plain\n\nopis\n"
        "--B\nContent-Type: message/delivery-status\n\n"
        f"Reporting-MTA: dns; {OUR_MTA}\n\n"
        "Final-Recipient: rfc822; a@outlook.com\nAction: failed\nStatus: 5.7.1\n"
        f"Diagnostic-Code: smtp; {S3150_REASON}\n"
        "\n--B\nContent-Type: text/rfc822-headers\n\n"
        f"Subject: Komunikat\n{HEADER}: {headers_for(9)[HEADER]}\n"
        "\n--B--\n"
    ).encode()

    assert broadcast_id_from(original_headers(raw).get(HEADER)) == 9
    (bounce,) = parse_dsn(raw, reporting_mta=OUR_MTA)
    assert bounce.status == "5.7.1" and not bounce.recipient_related and kind_of(bounce) == BounceKind.POLICY


def test_headers_hidden_deeper_than_the_top_level_are_ignored():
    inner = (
        'Content-Type: multipart/mixed; boundary="I"\n\n--I\nContent-Type: message/rfc822\n\n'
        f"{HEADER}: {headers_for(5)[HEADER]}\nSubject: obcy\n\ntresc\n--I--\n"
    )
    raw = (
        "Return-Path: <>\nMIME-Version: 1.0\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\n'
        '\n--B\nContent-Type: text/plain\n\nopis\n--B\nContent-Type: multipart/mixed; boundary="X"\n\n'
        "--X\n" + inner + "--X--\n--B--\n"
    ).encode()

    assert original_headers(raw) is None


def test_maildir_reports_every_bounce_including_policy_ones(tmp_path):
    (tmp_path / "new").mkdir()
    raw = _with_header((DSN_DIR / "postfix_gmail_5.1.1.eml").read_bytes(), headers_for(3)[HEADER])
    (tmp_path / "new" / "1").write_bytes(raw)
    recorded, reported = [], []

    stats = process_maildir(
        tmp_path,
        record=recorded.append,
        reporting_mta=OUR_MTA,
        report=lambda bounces, data: reported.append(
            (bounces, broadcast_id_from(original_headers(data)[HEADER]))
        ),
    )

    assert stats["hard"] == 1 and len(recorded) == 1
    assert [(len(bounces), broadcast) for bounces, broadcast in reported] == [(1, 3)]
    assert not list((tmp_path / "new").iterdir())


def test_maildir_report_error_does_not_stop_the_mailbox(tmp_path):
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "1").write_bytes((DSN_DIR / "postfix_gmail_5.1.1.eml").read_bytes())

    def broken(bounces, data):
        raise RuntimeError("raport padł")

    stats = process_maildir(tmp_path, record=lambda bounce: None, reporting_mta=OUR_MTA, report=broken)

    assert stats["hard"] == 1 and stats["errors"] == 0
    assert not list((tmp_path / "new").iterdir())


def test_retention_task_is_scheduled_daily(settings):
    from apps.coordinator_stats.tasks import purge_broadcast_bounces

    entry = settings.CELERY_BEAT_SCHEDULE["broadcast-bounces-purge"]
    assert entry["task"] == purge_broadcast_bounces.name
    assert purge_broadcast_bounces.name == "apps.coordinator_stats.tasks.purge_broadcast_bounces"
