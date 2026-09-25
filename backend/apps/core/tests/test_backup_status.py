"""Meldunek o kopii zapasowej: komenda, progi świeżości i to, co z tego widać publicznie.

Sedno tych testów jest jedno: **brak meldunku ma znaczyć „źle”, a nie „pewnie dobrze”**. Kopia
zapasowa jest jedyną rzeczą w tym systemie, o której milczenie jest gorsze od złej wiadomości –
dowiadujemy się o jej braku dokładnie wtedy, gdy jest potrzebna, czyli za późno.

Drugi wątek to zakres tego, co oddaje publiczny ``/status.json``: wartości logiczne, nigdy daty.
Konkretna data ostatniej kopii mówi obcemu, kiedy uderzenie zaboli najbardziej.
"""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.core import backup
from apps.core.tasks import heartbeat

pytestmark = pytest.mark.django_db


# --- progi świeżości -----------------------------------------------------------------------------


def test_without_any_report_the_state_is_not_fresh():
    state = backup.state()

    assert state.last_ok is None
    assert state.backup_fresh is False
    assert state.verify_fresh is False


def test_a_fresh_report_is_fresh():
    backup.record(ok=True, verified=True)

    state = backup.state()

    assert state.backup_fresh is True
    assert state.verify_fresh is True


def test_a_report_older_than_the_threshold_stops_being_fresh():
    backup.record(ok=True, at=timezone.now() - timedelta(hours=backup.MAX_BACKUP_AGE_HOURS + 1))
    backup.record(verified=True, at=timezone.now() - timedelta(days=backup.MAX_VERIFY_AGE_DAYS + 1))

    state = backup.state()

    assert state.backup_fresh is False
    assert state.verify_fresh is False


def test_a_stale_report_is_still_readable_not_gone():
    """Odróżnienie „kopia sprzed pół roku” od „kopii nigdy nie było” jest tu całą wartością wpisu."""
    moment = timezone.now() - timedelta(days=90)
    backup.record(ok=True, at=moment)

    assert backup.state().last_ok is not None


# --- komenda meldunku ----------------------------------------------------------------------------


def test_the_command_refuses_to_do_nothing():
    with pytest.raises(CommandError):
        call_command("record_backup_status")


def test_the_command_records_a_successful_backup():
    call_command("record_backup_status", "--ok", stdout=StringIO())

    assert backup.state().backup_fresh is True
    assert backup.state().verify_fresh is False


def test_the_command_records_a_successful_restore_test():
    call_command("record_backup_status", "--verified", "--note", "db-2026.dump: users=12", stdout=StringIO())

    state = backup.state()
    assert state.verify_fresh is True
    assert "users=12" in state.note


def test_a_failed_run_does_not_move_any_timestamp():
    """Gdyby przesuwało, awaria kopii wyglądałaby w monitoringu dokładnie jak kopia świeża."""
    call_command("record_backup_status", "--failed", "--note", "pg_dump: brak miejsca", stdout=StringIO())

    state = backup.state()
    assert state.last_ok is None
    assert state.backup_fresh is False
    assert "brak miejsca" in state.note


def test_the_command_records_an_offsite_copy_together_with_the_backup():
    """``scripts/backup.sh`` po udanej i zweryfikowanej wysyłce na S3 / Dysk Google."""
    call_command(
        "record_backup_status", "--ok", "--offsite", "--note", "poza serwerem: Dysk", stdout=StringIO()
    )

    state = backup.state()
    assert state.backup_fresh is True
    assert state.offsite_fresh is True
    assert state.offsite_lost is False


def test_a_local_only_backup_is_fresh_but_not_offsite():
    call_command("record_backup_status", "--ok", stdout=StringIO())

    state = backup.state()
    assert state.backup_fresh is True
    assert state.last_offsite is None
    assert state.offsite_fresh is False
    # „Nigdy nie było” to nie „przestało działać” – alarm dostaje wyłącznie to drugie.
    assert state.offsite_lost is False


def test_offsite_without_ok_is_refused():
    """Kopia poza serwerem bez kopii nie istnieje – taki meldunek to błąd skryptu, nie stan."""
    with pytest.raises(CommandError):
        call_command("record_backup_status", "--offsite", stdout=StringIO())
    with pytest.raises(ValueError):
        backup.record(offsite=True)

    assert backup.state().last_offsite is None


def test_a_failed_offsite_upload_keeps_the_last_offsite_timestamp_where_it_was():
    """Nieudana wysyłka melduje ``--failed``: żaden znacznik – ani kopii, ani kopii zdalnej – się
    nie przesuwa, a notatka mówi, gdzie leży kopia lokalna."""
    moment = timezone.now() - timedelta(hours=30)
    backup.record(ok=True, offsite=True, at=moment)

    call_command(
        "record_backup_status",
        "--failed",
        "--note",
        "kopia lokalna 20260925T031500Z jest, poza serwer NIE dotarła: token",
        stdout=StringIO(),
    )

    state = backup.state()
    assert abs((state.last_offsite - moment).total_seconds()) < 1
    assert abs((state.last_ok - moment).total_seconds()) < 1
    assert "NIE dotarła" in state.note


def test_an_offsite_copy_that_stopped_is_lost():
    backup.record(ok=True, offsite=True, at=timezone.now() - timedelta(hours=backup.MAX_BACKUP_AGE_HOURS + 1))
    backup.record(ok=True)

    state = backup.state()
    assert state.backup_fresh is True
    assert state.offsite_lost is True


def test_the_show_mode_prints_dates_for_the_operator():
    backup.record(ok=True, verified=True)
    out = StringIO()

    call_command("record_backup_status", "--show", stdout=out)

    body = out.getvalue()
    assert "ostatnia kopia" in body
    assert timezone.localtime().strftime("%Y-%m-%d") in body


# --- co widać publicznie -------------------------------------------------------------------------


def test_the_public_status_reports_booleans_never_dates(client):
    heartbeat()
    backup.record(ok=True, verified=True)

    payload = json.loads(client.get("/status.json").content)

    # ``is True`` (a nie ``== True``) jest tu treścią testu: wartość ma być logiczna, bo data
    # ostatniej kopii mówiłaby obcemu, kiedy uderzenie zaboli najbardziej.
    assert payload["backup_last_ok"] is True
    assert payload["backup_last_verified"] is True
    # Kopia bez ``offsite`` jest wyłącznie lokalna – i to widać publicznie, też jako wartość logiczna.
    assert payload["backup_offsite"] is False


def test_the_public_status_reports_an_offsite_copy_as_a_boolean(client):
    heartbeat()
    backup.record(ok=True, offsite=True)

    payload = json.loads(client.get("/status.json").content)

    assert payload["backup_offsite"] is True
    assert payload["status"] == "ok"


def test_a_missing_backup_does_not_turn_the_public_page_red(client):
    """Uczestnikowi o 23:40 stan kopii nie zmienia niczego – zapalenie strony nauczyłoby go tylko,
    żeby jej nie czytać."""
    heartbeat()
    cache.delete(backup.LAST_OK_KEY)
    cache.delete(backup.LAST_VERIFIED_KEY)

    payload = json.loads(client.get("/status.json").content)

    assert payload["status"] == "ok"
    assert payload["backup_last_ok"] is False
