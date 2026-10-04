"""Okno etapu **dla jednego ucznia** – adapter do okien czasowych w strefach (TZ-01).

TZ-01 powstaje równolegle i może dać uczniowi z Tokio inne okno niż uczniowi z Limy. Nadzór nie
liczy tego sam: pyta funkcję wskazaną w ``PROCTORING_WINDOW_ADAPTER`` (``(stage, participant) ->
(opens_at, closes_at)``), a bez niej bierze okno globalne etapu – to samo, które egzekwuje dziś
upload (``Stage.opens_at`` … ``Stage.submission_deadline``). Jedno miejsce tej reguły: bramka,
token ucznia i token nadzorującego pytają tutaj.

Błąd adaptera (wyjątek, zły kształt odpowiedzi) nie może zamknąć zawodów: logujemy go i wracamy do
okna globalnego – tego samego, którego pilnuje serwis uploadu.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


def global_window(stage) -> tuple[datetime, datetime]:
    return stage.opens_at, stage.submission_deadline


def effective_window(stage, participant=None) -> tuple[datetime, datetime]:
    """``(otwarcie, zamknięcie)`` etapu dla ucznia – z adaptera TZ-01 albo globalne."""
    path = (getattr(settings, "PROCTORING_WINDOW_ADAPTER", "") or "").strip()
    if not path or participant is None:
        return global_window(stage)
    try:
        opens, closes = import_string(path)(stage, participant)
    except Exception:  # noqa: BLE001 - awaria adaptera nie może zamknąć zawodów
        logger.exception("Adapter okna %s zawiódł dla etapu %s.", path, stage.pk)
        return global_window(stage)
    if not isinstance(opens, datetime) or not isinstance(closes, datetime) or closes <= opens:
        logger.warning("Adapter okna %s oddał niepoprawne okno dla etapu %s.", path, stage.pk)
        return global_window(stage)
    return opens, closes


def lead() -> timedelta:
    return timedelta(minutes=max(0, int(getattr(settings, "PROCTORING_LEAD_MINUTES", 30))))


def grace() -> timedelta:
    return timedelta(minutes=max(0, int(getattr(settings, "PROCTORING_GRACE_MINUTES", 30))))


def student_window(stage, participant) -> tuple[datetime, datetime]:
    """Kiedy uczeń może włączyć nadzór: ``LEAD`` minut przed swoim otwarciem do swojego zamknięcia."""
    opens, closes = effective_window(stage, participant)
    return opens - lead(), closes


def proctor_window(stage) -> tuple[datetime, datetime]:
    """Kiedy działa pokój nadzorujących: od najwcześniejszego do najpóźniejszego okna z zapasem.

    Bez adaptera to okno globalne ± ``LEAD``/``GRACE``. Z adapterem TZ-01 okna uczniów rozjeżdżają
    się o strefy, więc nadzorujący dostaje zapas ±14 h (skrajne strefy) – to tylko okno **wejścia**
    do pokoju, a widzi w nim wyłącznie uczniów, którzy sami nadają.
    """
    opens, closes = global_window(stage)
    if (getattr(settings, "PROCTORING_WINDOW_ADAPTER", "") or "").strip():
        spread = timedelta(hours=14)
        opens, closes = opens - spread, closes + spread
    return opens - lead(), closes + grace()
