"""Okno etapu **dla jednego ucznia** – adapter do okien czasowych w strefach (TZ-01).

TZ-01 (``apps.time_windows``) daje uczniowi z Tokio inne okno niż uczniowi z Limy. Nadzór nie liczy
tego sam, tylko pyta – w tej kolejności:

1. funkcję wskazaną w ``PROCTORING_WINDOW_ADAPTER`` (``(stage, participant) -> (opens, closes)``) –
   furtka operatora na inny kalendarz,
2. ``apps.time_windows.access.effective_window(stage, participant)`` – **samo**, gdy aplikacja TZ-01
   jest zainstalowana; jej ``EffectiveWindow`` niesie ``opens_at`` i ``deadline_at`` (z czasem
   dodatkowym ucznia), a tolerancja ``grace_seconds`` zostaje etapowa – jak w ``personal_stage``,
3. okno globalne etapu – to samo, które egzekwuje dziś upload (``opens_at`` … ``submission_deadline``).

Jedno miejsce tej reguły: bramka, token ucznia i token nadzorującego pytają tutaj. Błąd adaptera
(wyjątek, zły kształt odpowiedzi) nie może zamknąć zawodów: logujemy go i wracamy do okna globalnego.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.apps import apps
from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

TIME_WINDOWS_APP = "apps.time_windows"
#: Najdalej rozjechane strefy (UTC−12 … UTC+14): zapas okna „dla kogokolwiek” przy oknach TZ-01.
ZONE_SPREAD = timedelta(hours=14)


def global_window(stage) -> tuple[datetime, datetime]:
    return stage.opens_at, stage.submission_deadline


def _custom_adapter():
    path = (getattr(settings, "PROCTORING_WINDOW_ADAPTER", "") or "").strip()
    return import_string(path) if path else None


def per_student_windows(stage=None) -> bool:
    """Czy okna tego etapu mogą się różnić między uczniami: własny adapter albo **plan okien TZ-01**
    dla etapu (flaga ``stage_time_windows`` konkursu i zapisany plan) – sama instalacja TZ-01 bez
    planu nie poszerza niczego."""
    if (getattr(settings, "PROCTORING_WINDOW_ADAPTER", "") or "").strip():
        return True
    if stage is None or not apps.is_installed(TIME_WINDOWS_APP):
        return False
    from apps.time_windows.access import plan_for

    return plan_for(stage) is not None


def _time_windows(stage, participant):
    from apps.time_windows.access import effective_window as tz_window

    window = tz_window(stage, participant)
    if window is None:
        return None
    grace = timedelta(seconds=stage.grace_seconds or 0)
    return window.opens_at, window.deadline_at + grace


def effective_window(stage, participant=None) -> tuple[datetime, datetime]:
    """``(otwarcie, zamknięcie)`` etapu dla ucznia – z adaptera, z TZ-01 albo globalne."""
    if participant is None:
        return global_window(stage)
    try:
        adapter = _custom_adapter()
        if adapter is not None:
            result = adapter(stage, participant)
        elif apps.is_installed(TIME_WINDOWS_APP):
            result = _time_windows(stage, participant)
        else:
            return global_window(stage)
    except Exception:  # noqa: BLE001 - awaria adaptera nie może zamknąć zawodów
        logger.exception("Adapter okna nadzoru zawiódł dla etapu %s.", stage.pk)
        return global_window(stage)
    if result is None:
        return global_window(stage)
    opens, closes = result
    if not isinstance(opens, datetime) or not isinstance(closes, datetime) or closes <= opens:
        logger.warning("Adapter okna nadzoru oddał niepoprawne okno dla etapu %s.", stage.pk)
        return global_window(stage)
    return opens, closes


def envelope(stage) -> tuple[datetime, datetime]:
    """Okno „dla kogokolwiek”: globalne, przy oknach per uczeń poszerzone o rozjazd stref.

    Tego pytają bramka dla osób spoza listy uczniów (niezalogowany, bez zgłoszenia) i pokój
    nadzorujących – oni nie mają własnego okna, a ktoś w tym czasie może pisać.
    """
    opens, closes = global_window(stage)
    if per_student_windows(stage):
        opens, closes = opens - ZONE_SPREAD, closes + ZONE_SPREAD
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
    """Kiedy działa pokój nadzorujących: okno „dla kogokolwiek” z zapasem ``LEAD``/``GRACE``."""
    opens, closes = envelope(stage)
    return opens - lead(), closes + grace()
