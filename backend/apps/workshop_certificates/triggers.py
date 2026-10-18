"""Zapis obecności → **jedno** zadanie Celery na transakcję (WS-CERT-02 § 2).

Odbiornik sygnału nie pyta bazy: zbiera identyfikatory uczestników w pamięci połączenia i przy
pierwszym zapisie w transakcji rejestruje jeden ``on_commit``. Po commicie pada jedno zapytanie
(„czy jakakolwiek reguła jest włączona” – instalacja bez reguł nie ma dostawać zadań przy każdym
„Zapisz” w tabeli obecności) i jedno ``delay``. Import trzystu osób to więc jedno zadanie, a nie
trzysta, i zero dodatkowych zapytań w żądaniu poza tym jednym po commicie.

Zbiór oczekujących wisi na obiekcie połączenia (``connection.__dict__``), bo to połączenie wyznacza
transakcję – zmienna globalna mieszałaby wątki, a zmienna kontekstowa nie wie, kiedy transakcja się
kończy. Wycofanie transakcji (albo punktu zapisu, w którym zarejestrowano ``on_commit``) usuwa
callback; następny zapis widzi to po liście ``run_on_commit`` i zakłada nowy zbiór, więc wycofana
obecność nie zleca niczego, a kolejna nie ginie.
"""

from __future__ import annotations

import logging

from django.db import DEFAULT_DB_ALIAS, transaction
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)

_PENDING_ATTR = "_workshop_certificates_pending"


class _Pending:
    """Identyfikatory uczestników zebrane w jednej transakcji i jej ``flush`` po commicie."""

    def __init__(self, using: str):
        self.using = using
        self.ids: set[int] = set()

    def flush(self) -> None:
        connection = transaction.get_connection(self.using)
        if connection.__dict__.get(_PENDING_ATTR) is self:
            del connection.__dict__[_PENDING_ATTR]
        enqueue(sorted(self.ids))


def _registered(connection, pending: _Pending) -> bool:
    """Czy ``flush`` tego zbioru nadal czeka na commit (nie zniknął z wycofanym punktem zapisu)."""
    return any(entry[1] == pending.flush for entry in getattr(connection, "run_on_commit", ()))


def schedule(participant_ids, using: str | None = None) -> None:
    """Dopisuje uczestników do zadania, które wyjdzie po commicie bieżącej transakcji."""
    ids = {int(pk) for pk in participant_ids if pk}
    if not ids:
        return
    using = using or DEFAULT_DB_ALIAS
    connection = transaction.get_connection(using)
    if not connection.in_atomic_block:
        # Autocommit: zapis już jest w bazie, ``on_commit`` wykonałby się od razu – bez pośrednika.
        enqueue(sorted(ids))
        return
    pending = connection.__dict__.get(_PENDING_ATTR)
    if pending is None or not _registered(connection, pending):
        pending = _Pending(using)
        connection.__dict__[_PENDING_ATTR] = pending
        # ``robust``: błąd brokera po commicie nie może zamienić zapisanej obecności w 500 –
        # „Przelicz teraz” nadrobi, a błąd zostaje w logu.
        transaction.on_commit(pending.flush, using=using, robust=True)
    pending.ids |= ids


def enqueue(participant_ids: list[int]) -> None:
    """Zleca zadanie – o ile jakakolwiek reguła w instalacji jest włączona."""
    if not participant_ids:
        return
    from .models import AutoIssuePolicy
    from .tasks import issue_for_participants

    if not AutoIssuePolicy.objects.filter(enabled=True).exists():
        return
    try:
        issue_for_participants.delay(participant_ids)
    except Exception:  # noqa: BLE001 - broker niedostępny: obecność zapisana, zadanie do nadrobienia
        logger.exception("Nie udało się zlecić zaświadczeń z warsztatów dla %s osób.", len(participant_ids))


def on_attendance_recorded(sender, participant_ids=(), using=None, **kwargs) -> None:
    """Odbiornik ``apps.cms.workshops.attendance_recorded`` (hurtowy zapis obecności)."""
    schedule(participant_ids, using)


def on_attendance_saved(sender, instance, created, raw=False, using=None, **kwargs) -> None:
    """Pojedynczy ``create()`` / ``save()`` obecności – ta sama droga, co zapis hurtowy."""
    if raw or not created:
        return
    schedule([instance.participant_id], using)


def connect() -> None:
    """Podłącza odbiorniki. Wołane z ``ready()``; ``dispatch_uid`` czyni to idempotentnym."""
    from apps.cms.models import WorkshopAttendance
    from apps.cms.workshops import attendance_recorded

    attendance_recorded.connect(
        on_attendance_recorded, sender=WorkshopAttendance, dispatch_uid="workshop_certificates.bulk"
    )
    post_save.connect(
        on_attendance_saved, sender=WorkshopAttendance, dispatch_uid="workshop_certificates.save"
    )
