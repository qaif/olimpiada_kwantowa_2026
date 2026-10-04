"""Strefa czasowa ucznia na czas żądania (docs/tasks/TZ-01.md § 4).

Uczeń z Tokio ma zobaczyć start swojego okna jako „10:00”, a nie „03:00 (czas polski)”. Django
renderuje każdą datę w **aktywnej** strefie (``USE_TZ=True``), więc wystarczy ją aktywować na czas
żądania – nagłówek „Co teraz”, karty zadań, forum i kalendarz mówią wtedy jednym czasem.

Warstwa działa **wyłącznie** w konkursie z flagą ``stage_time_windows`` i wyłącznie dla
zalogowanego uczestnika tego konkursu. Każde inne żądanie przechodzi bez zapytania i bez zmiany
strefy – Olimpiada Kwantowa nie płaci za nią nic. Strefa jest **zawsze** zdejmowana po odpowiedzi
(``finally``): wątek gunicorna wraca do puli, a następne żądanie nie może dostać cudzej strefy.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from django.utils import timezone

from .access import enabled


def participant_timezone(request) -> str | None:
    """Strefa uczestnika żądania albo ``None`` (brak flagi, anonim, nie-uczestnik, strefa serwisu)."""
    competition = getattr(request, "competition", None)
    if not enabled(competition):
        return None
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    from apps.accounts.models import Participant

    participant = (
        Participant.objects.filter(user=user, competition=competition)
        .select_related("delegation__country", "region")
        .first()
    )
    if participant is None:
        return None
    from .services import display_timezone

    return display_timezone(participant)


class ParticipantTimezoneMiddleware:
    """Aktywuje strefę ucznia na czas żądania i zawsze ją zdejmuje."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        name = participant_timezone(request)
        if not name:
            return self.get_response(request)
        try:
            zone = ZoneInfo(name)
        except ValueError, KeyError:  # pragma: no cover - strefa sprawdzana przy zapisie
            return self.get_response(request)
        timezone.activate(zone)
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
