"""Strefa czasowa ucznia na czas żądania (docs/tasks/TZ-01.md § 4).

Uczeń z Tokio ma zobaczyć start swojego okna jako „10:00”, a nie „03:00 (czas polski)”. Django
renderuje każdą datę w **aktywnej** strefie (``USE_TZ=True``), więc wystarczy ją aktywować na czas
żądania – nagłówek „Co teraz”, karty zadań, test i kalendarz mówią wtedy jednym czasem.

Granice (poprawki po przeglądzie): strefa ucznia jest aktywowana **wyłącznie**

- w konkursie z flagą ``stage_time_windows``,
- w widokach panelu uczestnika – klasach dziedziczących po ``ParticipantRequiredMixin`` (pulpit,
  wysyłka, test online, kalendarz, informacja zwrotna). Panel koordynatora, recenzenta, komisji,
  ``/admin/``, ``/cms/`` i strony publiczne zostają w strefie serwisu: tam godziny wpisuje się
  formularzami w czasie polskim (``LocalDateTimeField``), a strefa ucznia przesunęłaby wpisaną
  godzinę o kilka godzin bez ostrzeżenia,
- dla konta bez żadnej roli personelu (koordynator, recenzent, komisja, opiekun szkolny, opiekun
  drużyny, ``is_staff``) – osoba z dwiema rolami pracuje w panelu w czasie organizatora.

Strefa jest **zawsze** zdejmowana po odpowiedzi (``finally``): wątek gunicorna wraca do puli,
a następne żądanie nie może dostać cudzej strefy.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from django.utils import timezone

from .access import enabled


def _is_participant_view(view_func) -> bool:
    from apps.web.mixins import ParticipantRequiredMixin

    view_class = getattr(view_func, "view_class", None)
    return isinstance(view_class, type) and issubclass(view_class, ParticipantRequiredMixin)


def holds_staff_role(user, competition) -> bool:
    """Czy konto ma w tym konkursie jakąkolwiek rolę inną niż uczestnik (albo jest personelem Django)."""
    from apps.accounts.models import CompetitionRole, Membership
    from apps.accounts.services import has_role, memberships_enforced

    if user.is_staff or user.is_superuser:
        return True
    if has_role(user, competition, CompetitionRole.COORDINATOR):
        return True
    if memberships_enforced(competition):
        return (
            Membership.objects.filter(user=user, competition=competition)
            .exclude(role=CompetitionRole.PARTICIPANT)
            .exists()
        )
    return user.groups.exclude(name=CompetitionRole.PARTICIPANT).exists()


def participant_timezone(request) -> str | None:
    """Strefa uczestnika żądania albo ``None`` (brak flagi, anonim, personel, strefa serwisu)."""
    competition = getattr(request, "competition", None)
    if not enabled(competition):
        return None
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    if holds_staff_role(user, competition):
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
    """Aktywuje strefę ucznia w widokach panelu uczestnika i zawsze ją zdejmuje."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request._time_windows_zone_active = False
        try:
            return self.get_response(request)
        finally:
            if request._time_windows_zone_active:
                timezone.deactivate()

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not enabled(getattr(request, "competition", None)) or not _is_participant_view(view_func):
            return None
        name = participant_timezone(request)
        if not name:
            return None
        try:
            zone = ZoneInfo(name)
        except ValueError, KeyError:  # pragma: no cover - strefa sprawdzana przy zapisie
            return None
        timezone.activate(zone)
        request._time_windows_zone_active = True
        return None
