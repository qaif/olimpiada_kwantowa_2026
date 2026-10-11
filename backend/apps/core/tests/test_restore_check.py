"""Conocny test odtwarzania kopii (OPS-01): sprawdzenia, ich werdykty i to, co z wyniku widać.

Sprawdzenia biegną tu na **testowej** bazie – zmigrowanej tą samą wersją kodu, więc dla nich jest
to „kopia idealna”. Na niej sprawdzamy, że żadne sprawdzenie nie zgłasza fałszywego alarmu
(dyżurny budzony bez powodu przestaje czytać listy), a przypadki złe – na czystych funkcjach
werdyktu, którym podajemy dane złej kopii wprost. Pełny cykl na prawdziwym Postgresie z prawdziwą
paczką robi ``scripts/tests/restore_check_e2e.sh``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from apps.core import alerts, backup, restore_check
from apps.core.models import AuditLog
from apps.core.tasks import heartbeat
from apps.delegation_logistics import crypto
from apps.delegation_logistics.tests.conftest import coordinator, iqo, leader  # noqa: F401 - fikstury

pytestmark = pytest.mark.django_db

ALERT_ADDRESS = "dyzurny@example.test"
T = restore_check.Thresholds()


@pytest.fixture(autouse=True)
def _alert_address(settings):
    settings.ALERT_EMAILS = [ALERT_ADDRESS]


def stamp(moment: datetime) -> str:
    return f"db-{moment.astimezone(UTC):%Y%m%dT%H%M%SZ}.dump.gpg"


def ok_result(**overrides) -> dict:
    return restore_check.build_result(
        checks=[{"name": "row_counts", "status": "ok", "detail": ""}],
        backup={"name": stamp(timezone.now())},
        timings={"total_s": 12.5},
        **overrides,
    )


# --- bramka izolacji -----------------------------------------------------------------------------

GOOD_DB = {"NAME": "restorecheck_main", "HOST": "olimpiada-restore-check-123"}
GOOD_ENV = {"RESTORE_CHECK_ISOLATED": "1", "POSTGRES_DB": "olimpiada"}


def test_the_guard_accepts_the_throwaway_database():
    restore_check.guard_isolated(GOOD_DB, GOOD_ENV)


@pytest.mark.parametrize(
    ("db", "env", "reason"),
    [
        (GOOD_DB, {"POSTGRES_DB": "olimpiada"}, "RESTORE_CHECK_ISOLATED"),
        ({**GOOD_DB, "NAME": "olimpiada"}, GOOD_ENV, "przedrostka"),
        ({**GOOD_DB, "HOST": "db"}, GOOD_ENV, "baza żywa"),
        ({**GOOD_DB, "HOST": ""}, GOOD_ENV, "baza żywa"),
        (GOOD_DB, {**GOOD_ENV, "POSTGRES_DB": "restorecheck_main"}, "POSTGRES_DB"),
    ],
)
def test_the_guard_refuses_anything_that_could_be_the_live_database(db, env, reason):
    with pytest.raises(restore_check.NotIsolated, match=reason):
        restore_check.guard_isolated(db, env)


def test_verify_refuses_to_run_against_the_application_database():
    """Ręczne ``restore_check verify`` w kontenerze ``web`` nie może policzyć żywej bazy jako kopii."""
    with pytest.raises(CommandError, match="odmowa"):
        call_command("restore_check", "verify", stdout=StringIO())


# --- liczności -----------------------------------------------------------------------------------


def test_counts_within_the_window_pass():
    status, problems, _ = restore_check.compare_counts(
        {"accounts.User": 980, "core.AuditLog": 50_000}, {"accounts.User": 1000, "core.AuditLog": 50_100}, T
    )
    assert (status, problems) == ("ok", [])


def test_a_dump_cut_in_half_fails():
    status, problems, _ = restore_check.compare_counts(
        {"core.AuditLog": 25_000}, {"core.AuditLog": 50_000}, T
    )
    assert status == "fail"
    assert "core.AuditLog" in problems[0]


def test_a_backup_much_bigger_than_the_live_database_fails():
    """Od nocy coś masowo zniknęło z bazy żywej – dyżurny ma się o tym dowiedzieć rano."""
    status, _, _ = restore_check.compare_counts(
        {"submissions.Submission": 900}, {"submissions.Submission": 500}, T
    )
    assert status == "fail"


def test_small_tables_get_absolute_slack():
    status, _, _ = restore_check.compare_counts({"competitions.Stage": 4}, {"competitions.Stage": 6}, T)
    assert status == "ok"


def test_a_missing_table_and_an_empty_user_table_fail():
    status, problems, _ = restore_check.compare_counts({"accounts.User": 0, "core.AuditLog": None}, {}, T)
    assert status == "fail"
    assert any("brak tabeli" in item for item in problems)
    assert any("accounts.User: 0" in item for item in problems)


def test_live_counts_cover_the_key_tables():
    out = StringIO()
    call_command("restore_check", "live-counts", stdout=out)
    counts = json.loads(out.getvalue())
    assert {"accounts.User", "submissions.SubmissionFile", "core.AuditLog"} <= set(counts)
    assert all(isinstance(value, int) for value in counts.values())


# --- migracje ------------------------------------------------------------------------------------


def test_missing_migrations_the_running_version_already_had_fail():
    status, detail = restore_check.migration_verdict(
        pending=["submissions.0042_x"], unknown=[], deployed_after_backup=False
    )
    assert status == "fail"
    assert "submissions.0042_x" in detail


def test_migrations_deployed_after_the_dump_only_warn():
    status, _ = restore_check.migration_verdict(pending=["a.0002"], unknown=[], deployed_after_backup=True)
    assert status == "warn"


def test_migrations_unknown_to_the_code_only_warn():
    status, _ = restore_check.migration_verdict(pending=[], unknown=["a.0099"], deployed_after_backup=False)
    assert status == "warn"


def test_the_migrated_test_database_is_at_head():
    check = restore_check.check_migrations(deployed_after_backup=False)
    assert check["status"] == "ok", check
    assert check["applied"] > 0


# --- integralność --------------------------------------------------------------------------------


def test_every_model_is_readable_on_a_database_migrated_by_this_code():
    check = restore_check.check_models_readable()
    assert check["status"] == "ok", check


def test_a_primary_key_sequence_behind_max_id_fails():
    from apps.accounts.tests.factories import UserFactory

    UserFactory()
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_serial_sequence('accounts_user', 'id')")
        sequence = cursor.fetchone()[0]
        cursor.execute(f"SELECT last_value, is_called FROM {sequence}")  # noqa: S608
        last_value, is_called = cursor.fetchone()
        assert restore_check.check_sequences()["status"] == "ok"
        # Sekwencje są poza transakcją testu – stan przywracamy ręcznie, inaczej następne testy
        # tego procesu dostałyby kolizje kluczy.
        cursor.execute("SELECT setval(%s, 1, false)", [sequence])
        try:
            check = restore_check.check_sequences()
        finally:
            cursor.execute("SELECT setval(%s, %s, %s)", [sequence, last_value, is_called])
    assert check["status"] == "fail"
    assert "accounts_user" in check["detail"]


# --- Fernet --------------------------------------------------------------------------------------


def test_fernet_tokens_from_this_installation_decrypt_with_the_current_key():
    status, _ = restore_check.fernet_verdict([crypto.encrypt("AB123456")])
    assert status == "ok"


def test_fernet_tokens_readable_only_with_a_fallback_key_warn(settings):
    token = crypto.encrypt("AB123456")
    settings.SECRET_KEY_FALLBACKS = [settings.SECRET_KEY]
    settings.SECRET_KEY = "nowy-klucz-" + "x" * 50
    status, detail = restore_check.fernet_verdict([token])
    assert status == "warn"
    assert "FALLBACKS" in detail


def test_fernet_tokens_from_a_different_key_fail(settings):
    token = crypto.encrypt("AB123456")
    settings.SECRET_KEY = "zupelnie-inny-klucz-" + "x" * 50
    status, detail = restore_check.fernet_verdict([token])
    assert status == "fail"
    assert "AB123456" not in detail


def test_no_encrypted_rows_means_the_check_is_skipped():
    assert restore_check.fernet_verdict([])[0] == "skip"


def test_the_sampler_finds_an_encrypted_logistics_field(leader):  # noqa: F811 - fikstura
    from apps.delegation_logistics.models import DelegationMember, MemberKind

    DelegationMember.objects.create(
        delegation=leader.delegation, kind=MemberKind.LEADER, user=leader.user, passport_number="AB123456"
    )
    tokens = restore_check._sample_tokens()
    assert tokens and all(token.startswith(crypto.PREFIX) for token in tokens)
    check = restore_check.check_fernet()
    assert check["status"] == "ok"
    assert "AB123456" not in json.dumps(check)


# --- pliki ---------------------------------------------------------------------------------------


def test_the_listing_is_normalized():
    listing = restore_check.normalize_listing(
        ["./", "./submissions/", "./submissions/1/2/a.pdf\n", "public-media/x.png"]
    )
    assert listing == {"submissions/1/2/a.pdf", "public-media/x.png"}


@pytest.mark.parametrize(
    ("missing", "expected"),
    [(0, "ok"), (2, "warn"), (3, "fail")],
)
def test_media_sample_tolerates_a_few_files_deleted_between_dump_and_mirror(missing, expected):
    sample = [f"submissions/k{i}.pdf" for i in range(20)]
    listing = set(sample[missing:])
    assert restore_check.media_verdict(sample, listing, T)[0] == expected


def test_media_without_a_file_archive_fails_and_without_files_is_skipped():
    assert restore_check.media_verdict(["submissions/a.pdf"], None, T)[0] == "fail"
    assert restore_check.media_verdict([], set(), T)[0] == "skip"


# --- wiek kopii ----------------------------------------------------------------------------------


def test_the_backup_age_comes_from_the_stamp_in_the_name():
    now = timezone.now()
    check, age = restore_check.check_backup_age({"name": stamp(now - timedelta(hours=2))}, T, now)
    assert check["status"] == "ok"
    assert 1.9 < age < 2.1


def test_a_backup_older_than_the_threshold_fails():
    now = timezone.now()
    check, _ = restore_check.check_backup_age({"name": stamp(now - timedelta(hours=30))}, T, now)
    assert check["status"] == "fail"


def test_docker_timestamps_with_nanoseconds_are_parsed():
    parsed = restore_check.parse_docker_time("2026-10-04T03:20:11.123456789Z")
    assert parsed == datetime(2026, 10, 4, 3, 20, 11, 123456, tzinfo=UTC)


# --- całość --------------------------------------------------------------------------------------


def test_verify_on_a_database_migrated_by_this_code_passes():
    from apps.accounts.tests.factories import UserFactory

    UserFactory()
    now = timezone.now()
    header = {
        "backup": {"name": stamp(now - timedelta(hours=1)), "size_bytes": 1234},
        "timings": {"db_restore_s": 3.2},
        "live_counts": restore_check.live_counts(),
        "files_status": "ok",
        "files_entries": 0,
        "web_created_at": (now - timedelta(days=3)).isoformat(),
        "extra_checks": [{"name": "djcms_db", "status": "ok", "detail": "cms_page=3"}],
    }
    result = restore_check.verify(header, set(), now=now, thresholds=T)

    assert result["status"] == "ok", [item for item in result["checks"] if item["status"] == "fail"]
    names = [item["name"] for item in result["checks"]]
    assert names[:3] == ["backup_age", "migrations", "row_counts"]
    assert {"models_readable", "sequences", "fernet", "files_archive", "media_sample", "djcms_db"} <= set(
        names
    )
    assert result["timings"]["db_restore_s"] == 3.2 and "checks_s" in result["timings"]
    assert result["backup"]["age_hours"] == pytest.approx(1, abs=0.1)


def test_verify_without_a_file_archive_fails():
    now = timezone.now()
    header = {
        "backup": {"name": stamp(now)},
        "live_counts": restore_check.live_counts(),
        "files_status": "missing",
    }
    result = restore_check.verify(header, None, now=now, thresholds=T)
    assert result["status"] == "failed"
    assert "files_archive" in result["failed"]


# --- wynik w aplikacji ---------------------------------------------------------------------------


def test_recording_a_passed_check_moves_the_verified_marker_and_writes_audit():
    restore_check.record(ok_result())

    assert backup.state().verify_fresh is True
    assert restore_check.level() == restore_check.LEVEL_OK
    entry = AuditLog.objects.get(action=restore_check.AUDIT_ACTION)
    assert entry.diff["status"] == "ok"
    assert mail.outbox == []


def test_recording_a_failed_check_mails_the_operators_at_once_and_keeps_alarming():
    result = restore_check.failure_result(
        "pg_restore", "pg_restore zakończył się kodem 1", backup={"name": "db-x.dump.gpg"}
    )

    restore_check.record(result)

    assert backup.state().verify_fresh is False
    assert restore_check.level() == restore_check.LEVEL_FAILED
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [ALERT_ADDRESS]
    assert "pg_restore" in mail.outbox[0].body
    # Watchdog widzi tę samą porażkę tym samym kluczem – i milczy w oknie wyciszenia.
    heartbeat()
    found = alerts.evaluate()
    assert restore_check.ALERT_KEY in {alert.key for alert in found}
    alerts.send([alert for alert in found if alert.key == restore_check.ALERT_KEY])
    assert len(mail.outbox) == 1


def test_a_passed_check_after_a_failure_clears_the_alarm():
    restore_check.record(restore_check.failure_result("gpg", "złe hasło"), send_alert=False)
    restore_check.record(ok_result())
    assert restore_check.ALERT_KEY not in {alert.key for alert in alerts.evaluate()}


def test_an_old_passed_result_is_stale():
    result = ok_result(now=timezone.now() - timedelta(hours=backup.MAX_VERIFY_AGE_HOURS + 2))
    assert restore_check.level(result) == restore_check.LEVEL_STALE
    assert restore_check.level({}) == restore_check.LEVEL_UNKNOWN


def test_the_record_command_reads_the_result_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(ok_result())))
    out = StringIO()
    call_command("restore_check", "record", stdout=out)
    assert "OK" in out.getvalue()
    assert restore_check.level() == restore_check.LEVEL_OK


def test_the_record_command_with_failure_records_a_failed_result():
    out = StringIO()
    call_command("restore_check", "record", "--failure", "no-backup", "--detail", "brak kopii", stdout=out)
    assert "NIEUDANY" in out.getvalue()
    assert restore_check.last_result()["failed"] == ["no-backup"]
    assert len(mail.outbox) == 1


def test_show_prints_the_last_result():
    restore_check.record(ok_result())
    out = StringIO()
    call_command("restore_check", "show", stdout=out)
    assert "poziom: ok" in out.getvalue()
    assert "row_counts" in out.getvalue()


def test_the_level_is_public_but_nothing_else(client):
    restore_check.record(restore_check.failure_result("pg_restore", "szczegół"), send_alert=False)
    heartbeat()

    status = json.loads(client.get("/status.json").content)
    health = client.get("/healthz/").json()

    assert status["backup_restore_check"] == "failed"
    assert health["backup_restore_check"] == "failed"
    # Porażka kopii nie gasi strony: uczestnik o 23:40 pyta o oddanie pracy, nie o kopie.
    assert status["status"] == "ok"
    assert "szczegół" not in json.dumps(status) + json.dumps(health)
