"""Odczyt dla ekranu koordynatora i panelu opiekuna: oś czasu okien, kraje, uczniowie, liczby na żywo.

Wszystko liczone z **jednej** migawki planu (``services.load_plan``) i jednej reguły rozstrzygania
(``services.resolve_many``) – ta sama funkcja wpuszcza ucznia do uploadu, więc ekran nie może
pokazać innego okna niż to, które obowiązuje. Liczba zapytań nie zależy od liczby uczniów:
wpisy etapu, wyjątki, prace i podejścia do testu idą po jednym zapytaniu na rodzaj.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from apps.accounts.delegations import Delegation
from apps.competitions.models import StageEntry

from .models import ParticipantWindow
from .services import (
    PlanView,
    country_timezone,
    delegation_default,
    delegation_window,
    resolve_many,
)
from .zones import country_default_timezone

STATE_LABELS = {"before": "przed startem", "open": "trwa", "after": "zakończone"}


@dataclass
class WindowRow:
    window: object
    ends_at: datetime
    state: str
    delegations: int = 0
    students: int = 0
    in_progress: int = 0
    submitted: int = 0

    @property
    def state_label(self) -> str:
        return STATE_LABELS[self.state]


@dataclass
class Overview:
    view: PlanView
    windows: list[WindowRow]
    delegations: list[dict] = field(default_factory=list)
    students: list[dict] = field(default_factory=list)
    exceptions: list[ParticipantWindow] = field(default_factory=list)


def _window_state(view: PlanView, window, now: datetime) -> str:
    if now < window.starts_at:
        return "before"
    if now < view.ends_at(window):
        return "open"
    return "after"


def _active_participants(stage) -> set[int]:
    """Wpisy z czymkolwiek oddanym: praca (dowolna wersja) albo podejście do testu."""
    from apps.quiz.models import QuizAttempt
    from apps.submissions.models import Submission

    worked = set(
        Submission.objects.filter(entry__stage=stage).values_list("entry__participant_id", flat=True)
    )
    worked |= set(
        QuizAttempt.objects.filter(entry__stage=stage).values_list("entry__participant_id", flat=True)
    )
    return worked


def overview(stage, view: PlanView, competition, now: datetime) -> Overview:
    rows = {
        item.pk: WindowRow(window=item, ends_at=view.ends_at(item), state=_window_state(view, item, now))
        for item in view.windows
    }

    delegations = list(
        Delegation.objects.for_competition(competition)
        .filter(edition_id=stage.edition_id)
        .select_related("country")
    )
    delegation_rows = []
    for delegation in delegations:
        window, source = delegation_window(view, delegation)
        default = delegation_default(view, delegation)
        tz_name = country_timezone(view, delegation.country)
        delegation_rows.append(
            {
                "delegation": delegation,
                "window": window,
                "default": default,
                "manual": source != "country",
                "timezone": tz_name or "",
                "timezone_overridden": delegation.country_id in view.country_timezones,
                "default_timezone": country_default_timezone(delegation.country.code) or "",
                "locked": window is not None and window.starts_at <= now,
            }
        )
        if window is not None:
            rows[window.pk].delegations += 1

    entries = list(
        StageEntry.objects.filter(stage=stage, participant__isnull=False).select_related(
            "participant__user", "participant__delegation__country"
        )
    )
    participants = [entry.participant for entry in entries]
    resolved = resolve_many(view, participants)
    worked = _active_participants(stage)
    student_rows = []
    for participant in participants:
        effective = resolved.get(participant.pk)
        if effective is None:
            continue
        row = rows[effective.window.pk]
        row.students += 1
        if effective.is_open(now):
            row.in_progress += 1
        if participant.pk in worked:
            row.submitted += 1
        student_rows.append(
            {"participant": participant, "effective": effective, "state": STATE_LABELS[effective.state(now)]}
        )
    student_rows.sort(
        key=lambda item: (
            item["effective"].opens_at,
            item["participant"].user.last_name,
            item["participant"].pk,
        )
    )

    exceptions = list(
        ParticipantWindow.objects.filter(plan=view.plan)
        .select_related("participant__user", "window", "set_by")
        .order_by("participant__user__last_name", "id")
    )
    return Overview(
        view=view,
        windows=list(rows.values()),
        delegations=delegation_rows,
        students=student_rows,
        exceptions=exceptions,
    )


def leader_rows(delegation, students, now: datetime) -> list[dict]:
    """Okna uczniów delegacji w każdym etapie bieżącej edycji, który ma okna – panel opiekuna."""
    from apps.competitions.models import Stage

    from .access import plan_for

    stages = list(
        Stage.objects.filter(edition_id=delegation.edition_id)
        .select_related("edition", "edition__competition")
        .order_by("opens_at", "id")
    )
    result = []
    for stage in stages:
        view = plan_for(stage, delegation.competition)
        if view is None:
            continue
        resolved = resolve_many(view, students)
        result.append(
            {
                "stage": stage,
                "view": view,
                "rows": [{"participant": item, "effective": resolved.get(item.pk)} for item in students],
            }
        )
    return result
