"""Zadanie beat automatycznych przypomnień (MSG-AUTO-01) – wpis ``generate-message-rule-broadcasts``."""

from __future__ import annotations

from celery import shared_task


@shared_task
def generate_rule_broadcasts() -> int:
    """Co 5 minut: reguły → komunikaty planowane (``services.generate_all``). Zwraca liczbę nowych.

    Pięć minut wystarcza, bo komunikat powstaje do tygodnia przed wysyłką (``GENERATION_HORIZON``),
    a wysyła go co minutę osobne zadanie ``dispatch-scheduled-broadcasts`` (MSG-SCHED-01).
    """
    from .services import generate_all

    return generate_all()
