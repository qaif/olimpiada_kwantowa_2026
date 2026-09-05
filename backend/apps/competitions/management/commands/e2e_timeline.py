"""``manage.py e2e_timeline --stage <id> --phase <closed|appeals_open|appeals_closed>``.

Przesuwa **wyłącznie oś czasu** etapu, żeby scenariusz end-to-end nie musiał czekać dobę na
otwarcie okna reklamacji. Komenda nie dotyka ani statusów zgłoszeń, ani ocen, ani publikacji –
wszystkie przejścia stanu robi w E2E ta sama ścieżka, co w produkcji (przyciski panelu,
``close_stage_now``, ``decide_appeal``, ``publish_results``).

Bezpiecznik: bez ``E2E_MODE=1`` komenda odmawia. Przesuwanie deadline'ów jest w produkcji
decyzją komitetu podejmowaną w panelu koordynatora albo w adminie, z audytem – a nie
jednolinijkowcem, który każdy z dostępem do powłoki kontenera może wywołać po cichu.

Fazy (wszystkie zachowują porządek wymagany przez ``CheckConstraint`` na ``Stage``):

``closed``
    deadline oddawania rozwiązań i deadline recenzji już minęły, okno reklamacji jeszcze nie.
    To stan, w którym koordynator zamyka etap i przydziela recenzentów.
``appeals_open``
    okno reklamacji jest otwarte (uczestnik może złożyć reklamację, publikacja jest zablokowana
    przez ``APPEAL_WINDOW_OPEN``).
``appeals_closed``
    okno reklamacji jest zamknięte – można publikować wyniki.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.competitions.models import Stage

PHASE_CLOSED = "closed"
PHASE_APPEALS_OPEN = "appeals_open"
PHASE_APPEALS_CLOSED = "appeals_closed"
PHASES = (PHASE_CLOSED, PHASE_APPEALS_OPEN, PHASE_APPEALS_CLOSED)

#: Offsety w **dniach** względem ``now`` dla każdego pola osi czasu etapu.
#: Kolejność wartości musi spełniać: opens < deadline <= review <= appeal_opens < appeal_closes.
#:
#: Skala dobowa, a nie minutowa, jest celowa. Ten sam rozkład wpisuje scenariusz E2E przez panel
#: admina (``e2e/timeline.py``), gdzie pola datetime są renderowane w czasie lokalnym serwera –
#: przy zapasie liczonym w dniach pomyłka o strefę czasową nie może przestawić fazy.
PHASE_OFFSETS: dict[str, dict[str, int]] = {
    PHASE_CLOSED: {
        "opens_at": -30,
        "deadline_at": -2,
        "review_deadline_at": -1,
        "appeal_window_opens_at": 7,
        "appeal_window_closes_at": 14,
    },
    PHASE_APPEALS_OPEN: {
        "opens_at": -30,
        "deadline_at": -4,
        "review_deadline_at": -3,
        "appeal_window_opens_at": -1,
        "appeal_window_closes_at": 7,
    },
    PHASE_APPEALS_CLOSED: {
        "opens_at": -30,
        "deadline_at": -5,
        "review_deadline_at": -4,
        "appeal_window_opens_at": -3,
        "appeal_window_closes_at": -1,
    },
}


def shift_stage_timeline(stage: Stage, phase: str, *, now=None) -> Stage:
    """Ustawia oś czasu etapu na zadaną fazę. Wydzielone z ``handle`` na potrzeby testów."""
    if phase not in PHASE_OFFSETS:
        raise ValueError(f"Nieznana faza: {phase}.")
    now = now or timezone.now()
    for field, offset in PHASE_OFFSETS[phase].items():
        setattr(stage, field, now + timedelta(days=offset))
    # ``grace_seconds`` doliczyłoby się do deadline'u i cofnęło fazę ``closed`` z powrotem do
    # otwartej – oś czasu musi znaczyć dokładnie to, co mówi nazwa fazy.
    stage.grace_seconds = 0
    stage.full_clean()
    stage.save(
        update_fields=[
            "opens_at",
            "deadline_at",
            "grace_seconds",
            "review_deadline_at",
            "appeal_window_opens_at",
            "appeal_window_closes_at",
        ]
    )
    return stage


class Command(BaseCommand):
    help = "Przesuwa oś czasu etapu do zadanej fazy scenariusza E2E. Wymaga E2E_MODE=1."

    def add_arguments(self, parser):
        parser.add_argument("--stage", type=int, required=True, help="Identyfikator etapu (Stage.pk).")
        parser.add_argument(
            "--phase",
            required=True,
            choices=PHASES,
            help="Faza osi czasu: closed | appeals_open | appeals_closed.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not getattr(settings, "E2E_MODE", False):
            raise CommandError(
                "e2e_timeline działa wyłącznie przy E2E_MODE=1. "
                "Terminy etapu zmienia się w panelu koordynatora albo w adminie (z audytem)."
            )
        try:
            stage = Stage.objects.select_for_update().get(pk=options["stage"])
        except Stage.DoesNotExist as exc:
            raise CommandError(f"Nie ma etapu o id={options['stage']}.") from exc

        shift_stage_timeline(stage, options["phase"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Etap {stage.pk} ({stage.kind}) → faza {options['phase']}: "
                f"deadline {stage.deadline_at.isoformat()}, "
                f"okno reklamacji {stage.appeal_window_opens_at.isoformat()} – "
                f"{stage.appeal_window_closes_at.isoformat()}."
            )
        )
