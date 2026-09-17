"""Watchdog aplikacyjny: co uznaje za awarię, komu o tym pisze i jak często.

Trzy rzeczy są tu przedmiotem testu, bo trzy mogą zawieść osobno:

1. **ocena** – czy watchdog widzi to, co ma widzieć (martwa kolejka, pełny dysk, seria błędów 500,
   brak kopii zapasowej), i czy **nie** alarmuje o zdrowym systemie. Fałszywy alarm kosztuje tu
   tyle samo, co przegapiona awaria: dyżurny, którego budzi się bez powodu, po tygodniu przestaje
   czytać te listy,
2. **wyciszenie** – czy trwająca awaria daje jeden list na godzinę, a nie dwieście osiem dziennie,
3. **ślad** – czy w audycie zostaje, że alarm poszedł. Bez tego nie da się po fakcie ustalić, kiedy
   organizator dowiedział się o awarii.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.core.cache import cache
from django.utils import timezone

from apps.core import alerts, backup
from apps.core.middleware import ServerErrorCounterMiddleware
from apps.core.models import AuditLog
from apps.core.tasks import heartbeat

pytestmark = pytest.mark.django_db

ALERT_ADDRESS = "dyzurny@example.test"


@pytest.fixture(autouse=True)
def _healthy_baseline(settings):
    """Instalacja „zdrowa”: puls workera świeży, kopia zapasowa zgłoszona przed chwilą.

    Bez tego **każdy** test w tym pliku startowałby z trzema alertami w tle (martwa kolejka, brak
    kopii, brak testu odtwarzania) i asercje mówiłyby o czymś innym, niż im się wydaje.
    """
    settings.ALERT_EMAILS = [ALERT_ADDRESS]
    heartbeat()
    backup.record(ok=True)
    backup.record(verified=True)
    yield


def keys_of(found) -> set[str]:
    return {alert.key for alert in found}


def _usage(*, total: int, free: int):
    """Podmiana ``shutil.disk_usage``. Krotka nazwana, bo watchdog czyta pola po nazwie.

    Prawdziwej zajętości dysku w teście udawać się nie da, a jest to jedyne sprawdzenie
    watchdoga, którego nie sposób wywołać zdarzeniem w systemie.
    """
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    return lambda _path: usage(total, total - free, free)


# --- ocena ---------------------------------------------------------------------------------------


def test_a_healthy_installation_produces_no_alerts():
    assert alerts.evaluate() == []


def test_a_dead_queue_is_an_alert():
    """Brak pulsu workera = oddane prace nie zostaną zeskanowane, a listy nie wyjdą."""
    from apps.core.tasks import HEARTBEAT_CACHE_KEY

    cache.delete(HEARTBEAT_CACHE_KEY)

    assert "service:queue" in keys_of(alerts.evaluate())


def test_a_full_disk_is_an_alert(monkeypatch):
    """Pełny dysk zatrzymuje naraz bazę, kolejkę i przyjmowanie prac – i poprzedza go cisza."""
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", _usage(total=100, free=2))

    found = [alert for alert in alerts.evaluate() if alert.key == "disk"]

    assert len(found) == 1
    assert "2.0%" in found[0].detail


def test_free_disk_above_the_threshold_is_not_an_alert(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", _usage(total=100, free=50))

    assert "disk" not in keys_of(alerts.evaluate())


def test_a_single_failed_task_is_not_an_alert_but_a_series_is():
    """Jedno nieudane zadanie to zwykle chwilowo niedostępny MTA – z tym radzą sobie ponowienia."""
    alerts.bump(alerts.FAILED_TASKS_KEY)

    assert "celery-failures" not in keys_of(alerts.evaluate())

    for _ in range(alerts.FAILED_TASKS_THRESHOLD):
        alerts.bump(alerts.FAILED_TASKS_KEY)

    assert "celery-failures" in keys_of(alerts.evaluate())


def test_a_series_of_5xx_responses_is_an_alert():
    for _ in range(alerts.SERVER_ERRORS_THRESHOLD):
        alerts.bump(alerts.SERVER_ERRORS_KEY)

    assert "server-errors" in keys_of(alerts.evaluate())


def test_a_missing_backup_is_an_alert():
    cache.delete(backup.LAST_OK_KEY)

    assert "backup" in keys_of(alerts.evaluate())


def test_a_stale_backup_is_an_alert():
    """Kopia sprzed trzech dób jest tak samo warta zgłoszenia, jak kopia, której nie ma."""
    backup.record(ok=True, at=timezone.now() - timedelta(days=3))

    assert "backup" in keys_of(alerts.evaluate())


def test_a_backup_that_was_never_restored_is_a_separate_alert():
    """Kopia, której nikt nigdy nie odtworzył, jest hipotezą – i to jest inny fakt niż jej brak."""
    cache.delete(backup.LAST_VERIFIED_KEY)

    found = keys_of(alerts.evaluate())

    assert "backup-verify" in found
    assert "backup" not in found


# --- licznik odpowiedzi 5xx ----------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


@pytest.mark.parametrize("status,expected", [(200, 0), (404, 0), (403, 0), (500, 1), (503, 1)])
def test_only_server_errors_are_counted(status, expected):
    """404 od robota i 403 od kogoś, kto wszedł nie tam, są normalnym ruchem, a nie awarią."""
    middleware = ServerErrorCounterMiddleware(lambda _request: _Response(status))

    middleware(object())

    assert alerts.counter(alerts.SERVER_ERRORS_KEY) == expected


# --- wysyłka i wyciszenie ------------------------------------------------------------------------


def test_an_alert_is_sent_to_every_address_from_the_setting():
    cache.delete(backup.LAST_OK_KEY)

    assert alerts.run() == 1
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [ALERT_ADDRESS]
    assert "kopii zapasowej" in mail.outbox[0].subject


def test_the_same_alert_is_not_repeated_within_the_cooldown():
    cache.delete(backup.LAST_OK_KEY)

    alerts.run()
    alerts.run()
    alerts.run()

    assert len(mail.outbox) == 1


def test_a_different_alert_is_not_silenced_by_an_ongoing_one():
    """Trwająca awaria magazynu nie może zagłuszyć informacji o tym, że skończyło się miejsce."""
    cache.delete(backup.LAST_OK_KEY)
    alerts.run()
    cache.delete(backup.LAST_VERIFIED_KEY)

    alerts.run()

    assert len(mail.outbox) == 2


def test_nothing_is_sent_when_nobody_is_on_duty(settings):
    """Instalacja deweloperska nikogo nie budzi – i nie wywraca się na braku odbiorcy."""
    settings.ALERT_EMAILS = []
    cache.delete(backup.LAST_OK_KEY)

    assert alerts.run() == 0
    assert mail.outbox == []


def test_a_sent_alert_leaves_an_audit_entry():
    cache.delete(backup.LAST_OK_KEY)

    alerts.run()

    entry = AuditLog.objects.get(action="alert.sent")
    assert entry.target_type == "core.alert"
    assert entry.target_id == "backup"
    assert entry.actor_id is None


def test_the_celery_task_runs_the_same_check():
    from apps.core.tasks import alerts_check

    cache.delete(backup.LAST_OK_KEY)

    assert alerts_check() == 1
    assert len(mail.outbox) == 1
