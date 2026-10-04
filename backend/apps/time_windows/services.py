"""Okna czasowe: rozstrzyganie okna ucznia i czynności koordynatora (docs/tasks/TZ-01.md).

Dwie części, celowo w jednym module, bo obie stoją na tej samej regule rozstrzygania:

1. **Odczyt** – ``load_plan`` (migawka planu jednego etapu), ``resolve`` (okno jednego ucznia),
   ``resolve_many`` (okna całej listy uczniów bez zapytania na wiersz), ``default_window`` (okno
   kraju z jego strefy). Odczyt jest **czysty** względem bazy poza samym wczytaniem migawki,
   więc ekran koordynatora i bramka uploadu liczą okno tą samą funkcją.
2. **Zapis** – plan, okna, przydziały, wyjątki, strefy. Każda czynność: rola sprawdzona przez
   wołającego (widok koordynatora / opiekuna), blokada wiersza planu, reguła czasu, audyt.

Reguły czasu (dlaczego takie):

- **okna, czas pracy, godzina preferowana** – tylko przed startem pierwszego okna. Po nim ktoś
  już widział zadania, a każda z tych wartości przesuwa komuś termin albo okno,
- **przydział delegacji / ucznia** – tylko gdy **ani stare, ani nowe** okno jeszcze się nie
  zaczęło. Przeniesienie z rozpoczętego okna znaczyłoby drugi start po obejrzeniu zadań,
  a przeniesienie do rozpoczętego – start „w biegu” z krótszym czasem,
- **dodatkowy czas** – do upływu obecnego terminu ucznia (awaria w trakcie pracy jest typowym
  powodem), ale nowy termin nie może wyjść poza ramę etapu ani wypaść w przeszłości,
- **strefa kraju** – w każdej chwili, ale przydział domyślny kraju w etapach, które już się
  zaczęły, jest przed zmianą **zamrażany** wierszem ``DelegationWindow``: historia i trwające
  zawody nie mogą się przesunąć od poprawki strefy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit

from .models import (
    MAX_DURATION_MINUTES,
    MAX_EXTRA_MINUTES,
    MAX_WINDOWS,
    MIN_DURATION_MINUTES,
    CountryTimezone,
    DelegationWindow,
    ParticipantTimezone,
    ParticipantWindow,
    TimeWindow,
    WindowPlan,
)
from .zones import country_default_timezone, is_valid_timezone

#: Skąd uczeń ma swoje okno – do ekranu koordynatora i opiekuna („ręcznie”, „z kraju” …).
SOURCE_PARTICIPANT = "participant"
SOURCE_DELEGATION = "delegation"
SOURCE_COUNTRY = "country"
SOURCE_FIRST = "first"

SOURCE_LABELS = {
    SOURCE_PARTICIPANT: "wyjątek ucznia",
    SOURCE_DELEGATION: "przydział delegacji",
    SOURCE_COUNTRY: "domyślnie ze strefy kraju",
    SOURCE_FIRST: "pierwsze okno (brak kraju)",
}

#: Etykiety kolejnych okien: A, B, C … – krótkie, bo stoją w kolumnach tabel i w panelu ucznia.
LABELS = "ABCDEFGHIJKL"


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, status.HTTP_409_CONFLICT)


def _bad(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, status.HTTP_400_BAD_REQUEST)


# --- odczyt ---------------------------------------------------------------------------------------


@dataclass
class PlanView:
    """Migawka planu jednego etapu: okna w kolejności startu i liczby potrzebne każdej bramce."""

    plan: WindowPlan
    stage: object
    windows: list[TimeWindow]
    delegation_windows: dict[int, int] = field(default_factory=dict)
    country_timezones: dict[int, str] = field(default_factory=dict)
    _max_extra: int | None = None

    @property
    def duration(self) -> timedelta:
        return self.plan.duration

    def window(self, pk: int | None) -> TimeWindow | None:
        return next((item for item in self.windows if item.pk == pk), None)

    def ends_at(self, window: TimeWindow) -> datetime:
        return window.starts_at + self.duration

    @property
    def first_start(self) -> datetime | None:
        return self.windows[0].starts_at if self.windows else None

    @property
    def last_end(self) -> datetime | None:
        return max((self.ends_at(item) for item in self.windows), default=None)

    @property
    def max_extra_minutes(self) -> int:
        if self._max_extra is None:
            top = ParticipantWindow.objects.filter(plan=self.plan).aggregate(top=Max("extra_minutes"))["top"]
            self._max_extra = int(top or 0)
        return self._max_extra

    @property
    def release_at(self) -> datetime | None:
        """Moment ujawnienia: koniec najpóźniejszego okna + największy dodatkowy czas + grace.

        Przybliżenie **z góry** (największy dodatkowy czas doliczony do ostatniego okna, nawet
        jeśli ma go uczeń z okna A): ujawnienie trochę za późno nie szkodzi nikomu, a za wcześnie
        – temu, kto jeszcze pisze.
        """
        last = self.last_end
        if last is None:
            return None
        grace = timedelta(seconds=self.stage.grace_seconds or 0)
        return last + timedelta(minutes=self.max_extra_minutes) + grace

    def started(self, now: datetime) -> bool:
        first = self.first_start
        return first is not None and first <= now

    def running(self, now: datetime) -> bool:
        """Od startu pierwszego okna do momentu ujawnienia – czas wymuszonej premoderacji."""
        first, release = self.first_start, self.release_at
        return first is not None and release is not None and first <= now < release


@dataclass(frozen=True)
class EffectiveWindow:
    """Okno jednego ucznia: które okno, skąd przydział i jego własne terminy."""

    window: TimeWindow
    source: str
    opens_at: datetime
    deadline_at: datetime
    submission_deadline: datetime
    extra_minutes: int
    reason: str = ""

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    def is_open(self, now: datetime) -> bool:
        return self.opens_at <= now < self.submission_deadline

    def state(self, now: datetime) -> str:
        if now < self.opens_at:
            return "before"
        if now < self.submission_deadline:
            return "open"
        return "after"


def load_plan(stage) -> PlanView | None:
    """Migawka planu etapu albo ``None`` (etap bez okien). Trzy zapytania, bez względu na skalę.

    Przydziały delegacji i strefy krajów są wczytywane od razu, bo pyta o nie każde rozstrzygnięcie,
    a ich liczba jest liczbą **krajów** (dziesiątki), nie uczniów.
    """
    plan = WindowPlan.objects.filter(stage_id=stage.pk).first()
    if plan is None:
        return None
    windows = list(plan.windows.order_by("starts_at", "id"))
    for item in windows:
        item.plan = plan  # ``ends_at`` czyta czas pracy z planu – bez zapytania na okno
    delegation_windows = dict(
        DelegationWindow.objects.filter(plan=plan).values_list("delegation_id", "window_id")
    )
    # Strefy krajów **konkursu etapu** – przez edycję, jednym zapytaniem, bez wczytywania edycji.
    zones = CountryTimezone.objects.filter(region__competition__editions__id=stage.edition_id)
    return PlanView(
        plan=plan,
        stage=stage,
        windows=windows,
        delegation_windows=delegation_windows,
        country_timezones=dict(zones.values_list("region_id", "timezone")),
    )


def country_timezone(view: PlanView, region) -> str | None:
    """Strefa kraju: poprawka koordynatora albo strefa stolicy z mapy."""
    if region is None:
        return None
    return view.country_timezones.get(region.pk) or country_default_timezone(region.code)


def default_window(view: PlanView, tz_name: str | None) -> TimeWindow | None:
    """Okno kraju ze strefą ``tz_name``: start lokalny najbliższy godzinie preferowanej.

    Odległość liczona **po okręgu doby** (23:00 i 1:00 dzielą dwie godziny, nie dwadzieścia dwie).
    Remis – wcześniejsze okno, żeby wynik nie zależał od kolejności wierszy w bazie.
    """
    if not view.windows:
        return None
    if not tz_name:
        return view.windows[0]
    zone = ZoneInfo(tz_name)
    target = view.plan.preferred_local_hour * 60

    def distance(window: TimeWindow) -> tuple[int, datetime]:
        local = window.starts_at.astimezone(zone)
        minutes = local.hour * 60 + local.minute
        gap = abs(minutes - target)
        return (min(gap, 24 * 60 - gap), window.starts_at)

    return min(view.windows, key=distance)


def delegation_default(view: PlanView, delegation) -> TimeWindow | None:
    return default_window(view, country_timezone(view, delegation.country))


def delegation_window(view: PlanView, delegation) -> tuple[TimeWindow | None, str]:
    """Okno delegacji i jego źródło: przydział ręczny albo domyślny ze strefy kraju."""
    assigned = view.window(view.delegation_windows.get(delegation.pk))
    if assigned is not None:
        return assigned, SOURCE_DELEGATION
    return delegation_default(view, delegation), SOURCE_COUNTRY


def _effective(view: PlanView, window: TimeWindow, source: str, exception: ParticipantWindow | None):
    extra = exception.extra_minutes if exception is not None else 0
    deadline = view.ends_at(window) + timedelta(minutes=extra)
    return EffectiveWindow(
        window=window,
        source=source,
        opens_at=window.starts_at,
        deadline_at=deadline,
        submission_deadline=deadline + timedelta(seconds=view.stage.grace_seconds or 0),
        extra_minutes=extra,
        reason=exception.reason if exception is not None else "",
    )


def _resolve_with(view: PlanView, participant, exception: ParticipantWindow | None) -> EffectiveWindow | None:
    """Kolejność: wyjątek ucznia → delegacja (ręcznie albo ze strefy kraju) → pierwsze okno.

    Uczeń **bez delegacji** dostaje pierwsze okno, a nie okno ze swojego regionu: region ucznia
    w trybie otwartym zmienia on sam w profilu, a przydział, który da się przestawić samemu,
    otwierałby drogę do drugiego startu. Takiemu uczniowi okno ustawia koordynator wyjątkiem.
    """
    if not view.windows:
        return None
    if exception is not None and exception.window_id:
        window = view.window(exception.window_id)
        if window is not None:
            return _effective(view, window, SOURCE_PARTICIPANT, exception)
    delegation = getattr(participant, "delegation", None) if participant.delegation_id else None
    if delegation is not None:
        window, source = delegation_window(view, delegation)
        if window is not None:
            return _effective(view, window, source, exception)
    return _effective(view, view.windows[0], SOURCE_FIRST, exception)


def resolve(view: PlanView, participant) -> EffectiveWindow | None:
    """Okno jednego ucznia (jedno zapytanie o wyjątek; delegacja i kraj z ``select_related`` albo leniwie)."""
    exception = ParticipantWindow.objects.filter(plan=view.plan, participant=participant).first()
    return _resolve_with(view, participant, exception)


def resolve_many(view: PlanView, participants) -> dict[int, EffectiveWindow]:
    """Okna listy uczniów: jedno zapytanie o wyjątki dla całej listy (ekran koordynatora)."""
    participants = list(participants)
    exceptions = {
        row.participant_id: row
        for row in ParticipantWindow.objects.filter(
            plan=view.plan, participant_id__in=[item.pk for item in participants]
        )
    }
    resolved = {}
    for participant in participants:
        effective = _resolve_with(view, participant, exceptions.get(participant.pk))
        if effective is not None:
            resolved[participant.pk] = effective
    return resolved


# --- strefa wyświetlania ucznia -------------------------------------------------------------------


def display_timezone(participant) -> str | None:
    """Strefa, w której uczeń widzi godziny: własna (opiekun/koordynator) → kraju → ``None``.

    ``None`` znaczy „strefa serwisu” – wołający nie aktywuje wtedy niczego.
    """
    own = (
        ParticipantTimezone.objects.filter(participant=participant).values_list("timezone", flat=True).first()
    )
    if own:
        return own
    region = None
    if participant.delegation_id:
        region = participant.delegation.country
    elif participant.region_id:
        region = participant.region
    if region is None:
        return None
    override = CountryTimezone.objects.filter(region=region).values_list("timezone", flat=True).first()
    return override or country_default_timezone(region.code)


# --- zapis: plan i okna ---------------------------------------------------------------------------


def _assert_stage_supports(stage) -> None:
    if stage.is_interview or stage.is_training:
        raise _bad(
            "Okna czasowe są dostępne wyłącznie dla etapu z pracami do oddania albo testem online.",
            "WINDOWS_STAGE_FORMAT",
        )
    if stage.closed_at is not None:
        raise _conflict("Etap jest zamknięty – okien nie można już ustawić.", "WINDOWS_STAGE_CLOSED")


def _assert_not_started(view: PlanView, now: datetime) -> None:
    if view.started(now):
        raise _conflict(
            "Pierwsze okno już się zaczęło – okien, czasu pracy ani godziny preferowanej nie można zmienić.",
            "WINDOWS_STARTED",
        )


def _check_envelope(stage, starts: list[datetime], duration_minutes: int, max_extra: int) -> None:
    """Okna (z największym dodatkowym czasem) mieszczą się w ramie etapu ``opens_at``–``deadline_at``.

    Rama zostaje źródłem prawdy dla wszystkiego, co liczy się po etapie (zamknięcie przez beat,
    recenzje, reklamacje, publikacja) – dlatego okna muszą się w niej mieścić, a nie ją przesuwać.
    """
    if not starts:
        raise _bad("Plan musi mieć co najmniej jedno okno.", "WINDOWS_EMPTY")
    first = min(starts)
    last_end = max(starts) + timedelta(minutes=duration_minutes + max_extra)
    if first < stage.opens_at:
        raise _bad(
            "Pierwsze okno zaczyna się przed otwarciem etapu – przesuń okno albo otwarcie etapu.",
            "WINDOWS_OUTSIDE_STAGE",
        )
    if last_end > stage.deadline_at:
        raise _bad(
            "Ostatnie okno (z dodatkowym czasem uczniów) kończy się po terminie oddania etapu – "
            "przesuń okno albo termin etapu.",
            "WINDOWS_OUTSIDE_STAGE",
        )


def windows_fit(stage, opens_at: datetime, deadline_at: datetime) -> bool:
    """Czy okna etapu zmieszczą się w nowej ramie – pytanie ``competitions.services.update_stage``.

    Bez flagi konkursu: rama jest regułą bazy danych tego etapu, a nie funkcją ekranu.
    """
    view = load_plan(stage)
    if view is None or not view.windows:
        return True
    last_end = view.last_end + timedelta(minutes=view.max_extra_minutes)
    return view.first_start >= opens_at and last_end <= deadline_at


def _locked_plan(plan: WindowPlan) -> WindowPlan:
    return WindowPlan.objects.select_for_update().select_related("stage", "stage__edition").get(pk=plan.pk)


def _validate_duration(duration_minutes: int, preferred_local_hour: int) -> None:
    if not MIN_DURATION_MINUTES <= duration_minutes <= MAX_DURATION_MINUTES:
        raise _bad(
            f"Czas pracy musi mieścić się w {MIN_DURATION_MINUTES}–{MAX_DURATION_MINUTES} min.",
            "WINDOWS_DURATION",
        )
    if not 0 <= preferred_local_hour <= 23:
        raise _bad("Godzina preferowana to liczba 0–23.", "WINDOWS_HOUR")


@transaction.atomic
def create_plan(
    stage,
    *,
    duration_minutes: int,
    preferred_local_hour: int,
    first_start: datetime,
    count: int,
    interval_minutes: int,
    actor=None,
    request=None,
    now=None,
) -> WindowPlan:
    """Włącza tryb okien: plan i ``count`` okien co ``interval_minutes`` od ``first_start``.

    Tylko **przed otwarciem etapu**: po ``opens_at`` treść zadań była już jawna dla wszystkich,
    więc rozłożenie etapu na okna niczego by nie chroniło, a tylko udawało ochronę.
    """
    from apps.competitions.models import Stage

    now = now or timezone.now()
    stage = Stage.objects.select_for_update().select_related("edition").get(pk=stage.pk)
    _assert_stage_supports(stage)
    if now >= stage.opens_at:
        raise _conflict(
            "Etap już się otworzył – tryb okien włącza się przed otwarciem etapu.", "WINDOWS_STAGE_OPENED"
        )
    if WindowPlan.objects.filter(stage=stage).exists():
        raise _conflict("Ten etap ma już okna czasowe.", "WINDOWS_EXISTS")
    _validate_duration(duration_minutes, preferred_local_hour)
    if not 1 <= count <= MAX_WINDOWS:
        raise _bad(f"Liczba okien: 1–{MAX_WINDOWS}.", "WINDOWS_COUNT")
    if count > 1 and interval_minutes < duration_minutes:
        # Nachodzące okna nie są błędem samym w sobie, ale przy jednym zestawie zadań uczeń
        # okna A pisałby równolegle z uczniem okna B – a wtedy po co okna. Świadomy wybór
        # (np. dwa okna z przesunięciem 2 h) wymaga odstępu ≥ czasu pracy.
        raise _bad("Odstęp między oknami nie może być krótszy niż czas pracy.", "WINDOWS_OVERLAP")
    starts = [first_start + timedelta(minutes=interval_minutes * index) for index in range(count)]
    _check_envelope(stage, starts, duration_minutes, 0)
    plan = WindowPlan.objects.create(
        stage=stage,
        duration_minutes=duration_minutes,
        preferred_local_hour=preferred_local_hour,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        created_at=now,
        updated_at=now,
    )
    TimeWindow.objects.bulk_create(
        [TimeWindow(plan=plan, label=LABELS[index], starts_at=start) for index, start in enumerate(starts)]
    )
    audit(
        actor,
        "time_windows.plan_created",
        plan,
        {
            "stage": stage.pk,
            "duration_minutes": duration_minutes,
            "preferred_local_hour": preferred_local_hour,
            "windows": [start.isoformat() for start in starts],
        },
        request=request,
    )
    return plan


@transaction.atomic
def update_plan(
    plan: WindowPlan, *, duration_minutes: int, preferred_local_hour: int, actor=None, request=None, now=None
) -> WindowPlan:
    now = now or timezone.now()
    plan = _locked_plan(plan)
    view = load_plan(plan.stage)
    _assert_not_started(view, now)
    _validate_duration(duration_minutes, preferred_local_hour)
    _check_envelope(
        plan.stage, [item.starts_at for item in view.windows], duration_minutes, view.max_extra_minutes
    )
    diff = {
        "duration_minutes": {"from": plan.duration_minutes, "to": duration_minutes},
        "preferred_local_hour": {"from": plan.preferred_local_hour, "to": preferred_local_hour},
    }
    plan.duration_minutes = duration_minutes
    plan.preferred_local_hour = preferred_local_hour
    plan.updated_at = now
    plan.save(update_fields=["duration_minutes", "preferred_local_hour", "updated_at"])
    audit(actor, "time_windows.plan_updated", plan, diff, request=request)
    return plan


@transaction.atomic
def move_window(window: TimeWindow, *, starts_at: datetime, actor=None, request=None, now=None) -> TimeWindow:
    """Nowy start okna – przed startem pierwszego okna i nie w przeszłości."""
    now = now or timezone.now()
    plan = _locked_plan(window.plan)
    view = load_plan(plan.stage)
    _assert_not_started(view, now)
    if starts_at <= now:
        raise _bad("Nowy start okna musi być w przyszłości.", "WINDOWS_START_PAST")
    if TimeWindow.objects.filter(plan=plan, starts_at=starts_at).exclude(pk=window.pk).exists():
        raise _bad("Inne okno zaczyna się o tej samej godzinie.", "WINDOWS_DUPLICATE_START")
    starts = [starts_at if item.pk == window.pk else item.starts_at for item in view.windows]
    _check_envelope(plan.stage, starts, plan.duration_minutes, view.max_extra_minutes)
    before = window.starts_at
    window.starts_at = starts_at
    window.save(update_fields=["starts_at"])
    audit(
        actor,
        "time_windows.window_moved",
        window,
        {"label": window.label, "from": before.isoformat(), "to": starts_at.isoformat()},
        request=request,
    )
    return window


@transaction.atomic
def add_window(plan: WindowPlan, *, starts_at: datetime, actor=None, request=None, now=None) -> TimeWindow:
    now = now or timezone.now()
    plan = _locked_plan(plan)
    view = load_plan(plan.stage)
    _assert_not_started(view, now)
    if len(view.windows) >= MAX_WINDOWS:
        raise _bad(f"Plan ma już {MAX_WINDOWS} okien.", "WINDOWS_COUNT")
    if starts_at <= now:
        raise _bad("Start okna musi być w przyszłości.", "WINDOWS_START_PAST")
    if any(item.starts_at == starts_at for item in view.windows):
        raise _bad("Inne okno zaczyna się o tej samej godzinie.", "WINDOWS_DUPLICATE_START")
    _check_envelope(
        plan.stage,
        [*(item.starts_at for item in view.windows), starts_at],
        plan.duration_minutes,
        view.max_extra_minutes,
    )
    used = {item.label for item in view.windows}
    label = next(letter for letter in LABELS if letter not in used)
    window = TimeWindow.objects.create(plan=plan, label=label, starts_at=starts_at)
    audit(
        actor,
        "time_windows.window_added",
        window,
        {"label": label, "starts_at": starts_at.isoformat()},
        request=request,
    )
    return window


@transaction.atomic
def delete_window(window: TimeWindow, *, actor=None, request=None, now=None) -> None:
    now = now or timezone.now()
    plan = _locked_plan(window.plan)
    view = load_plan(plan.stage)
    _assert_not_started(view, now)
    if len(view.windows) <= 1:
        raise _conflict("To jedyne okno planu – usuń cały tryb okien zamiast okna.", "WINDOWS_LAST")
    if window.delegation_assignments.exists() or window.participant_assignments.exists():
        raise _conflict(
            "Do okna są przydzielone delegacje albo uczniowie – najpierw przenieś ich do innego okna.",
            "WINDOWS_IN_USE",
        )
    audit(
        actor,
        "time_windows.window_deleted",
        window,
        {"label": window.label, "starts_at": window.starts_at.isoformat()},
        request=request,
    )
    window.delete()


@transaction.atomic
def delete_plan(plan: WindowPlan, *, actor=None, request=None, now=None) -> None:
    """Wyłącza tryb okien – tylko przed otwarciem etapu (ramy), bo potem treść stałaby się jawna od razu."""
    now = now or timezone.now()
    plan = _locked_plan(plan)
    if now >= plan.stage.opens_at:
        raise _conflict(
            "Etap już się otworzył – wyłączenie okien ujawniłoby zadania wszystkim naraz.",
            "WINDOWS_STAGE_OPENED",
        )
    audit(actor, "time_windows.plan_deleted", plan, {"stage": plan.stage_id}, request=request)
    DelegationWindow.objects.filter(plan=plan).delete()
    ParticipantWindow.objects.filter(plan=plan).delete()
    TimeWindow.objects.filter(plan=plan).delete()
    plan.delete()


# --- zapis: przydziały ----------------------------------------------------------------------------


def _assert_movable(old: TimeWindow | None, new: TimeWindow | None, now: datetime) -> None:
    for window in (old, new):
        if window is not None and window.starts_at <= now:
            raise _conflict(
                f"Okno {window.label} już się zaczęło – przydziału nie można zmienić.",
                "WINDOWS_ASSIGNMENT_LOCKED",
            )


def _window_of_plan(view: PlanView, window_id) -> TimeWindow | None:
    if window_id in (None, ""):
        return None
    window = view.window(int(window_id))
    if window is None:
        raise _bad("Nie ma takiego okna w tym etapie.", "WINDOWS_UNKNOWN_WINDOW")
    return window


@transaction.atomic
def assign_delegation(plan: WindowPlan, delegation, window_id, *, actor=None, request=None, now=None) -> None:
    """Przydział kraju do okna (``window_id`` puste = powrót do przydziału domyślnego)."""
    now = now or timezone.now()
    plan = _locked_plan(plan)
    if delegation.edition_id != plan.stage.edition_id:
        raise _bad("Delegacja należy do innej edycji niż etap.", "WINDOWS_FOREIGN_DELEGATION")
    view = load_plan(plan.stage)
    old, _source = delegation_window(view, delegation)
    target = _window_of_plan(view, window_id)
    new = target if target is not None else delegation_default(view, delegation)
    if (old.pk if old else None) == (new.pk if new else None) and (target is None) == (
        delegation.pk not in view.delegation_windows
    ):
        return
    _assert_movable(old, new, now)
    if target is None:
        DelegationWindow.objects.filter(plan=plan, delegation=delegation).delete()
    else:
        DelegationWindow.objects.update_or_create(
            plan=plan,
            delegation=delegation,
            defaults={
                "window": target,
                "assigned_by": actor if getattr(actor, "is_authenticated", False) else None,
                "assigned_at": now,
            },
        )
    audit(
        actor,
        "time_windows.delegation_assigned",
        delegation,
        {
            "stage": plan.stage_id,
            "from": old.label if old else None,
            "to": new.label if new else None,
            "manual": target is not None,
        },
        request=request,
    )


@transaction.atomic
def set_participant_exception(
    plan: WindowPlan,
    participant,
    *,
    window_id,
    extra_minutes: int,
    reason: str,
    actor=None,
    request=None,
    now=None,
) -> ParticipantWindow | None:
    """Wyjątek ucznia: okno (puste = jak delegacja) i dodatkowy czas. Pusty wyjątek usuwa wiersz."""
    now = now or timezone.now()
    plan = _locked_plan(plan)
    if participant.competition_id != plan.stage.edition.competition_id:
        raise _bad("Uczestnik należy do innego konkursu.", "WINDOWS_FOREIGN_PARTICIPANT")
    reason = (reason or "").strip()[:300]
    if not 0 <= extra_minutes <= MAX_EXTRA_MINUTES:
        raise _bad(f"Dodatkowy czas: 0–{MAX_EXTRA_MINUTES} min.", "WINDOWS_EXTRA_RANGE")
    view = load_plan(plan.stage)
    current_row = ParticipantWindow.objects.filter(plan=plan, participant=participant).first()
    before = _resolve_with(view, participant, current_row)
    target = _window_of_plan(view, window_id)
    removing = target is None and extra_minutes == 0
    if removing and current_row is None:
        return None
    if not removing and not reason:
        raise _bad("Podaj powód wyjątku – zostaje w audycie i na ekranie okien.", "WINDOWS_REASON_REQUIRED")
    draft = ParticipantWindow(
        plan=plan,
        participant=participant,
        window=target,
        extra_minutes=extra_minutes,
        reason=reason or "-",
    )
    after = _resolve_with(view, participant, None if removing else draft)
    if before.window.pk != after.window.pk:
        _assert_movable(before.window, after.window, now)
    if before.deadline_at != after.deadline_at:
        if now >= before.submission_deadline:
            raise _conflict(
                "Termin ucznia już minął – dodatkowego czasu nie można już zmienić.", "WINDOWS_EXTRA_LOCKED"
            )
        if after.submission_deadline <= now:
            raise _bad("Nowy termin ucznia wypadłby w przeszłości.", "WINDOWS_EXTRA_PAST")
    if after.deadline_at > plan.stage.deadline_at:
        raise _bad(
            "Dodatkowy czas wychodzi poza termin oddania etapu – najpierw przesuń termin etapu.",
            "WINDOWS_OUTSIDE_STAGE",
        )
    diff = {
        "stage": plan.stage_id,
        "window": {"from": before.window.label, "to": after.window.label},
        "extra_minutes": {"from": before.extra_minutes, "to": after.extra_minutes},
        "reason": reason,
    }
    if removing:
        ParticipantWindow.objects.filter(plan=plan, participant=participant).delete()
        audit(actor, "time_windows.participant_exception_removed", participant, diff, request=request)
        return None
    row, _created = ParticipantWindow.objects.update_or_create(
        plan=plan,
        participant=participant,
        defaults={
            "window": target,
            "extra_minutes": extra_minutes,
            "reason": reason,
            "set_by": actor if getattr(actor, "is_authenticated", False) else None,
            "set_at": now,
        },
    )
    audit(actor, "time_windows.participant_exception_set", participant, diff, request=request)
    return row


# --- zapis: strefy --------------------------------------------------------------------------------


@transaction.atomic
def set_country_timezone(region, tz_name: str, *, actor=None, request=None, now=None) -> None:
    """Strefa kraju (pusta = strefa stolicy z mapy). Rozpoczęte etapy dostają przydział zamrożony.

    Zamrożenie jest **przed** zmianą: dla każdego planu tego konkursu, którego pierwsze okno już
    się zaczęło, delegacja tego kraju bez przydziału ręcznego dostaje wiersz z oknem, które ma
    dziś. Dzięki temu poprawka strefy działa wyłącznie na etapy przyszłe.
    """
    from apps.accounts.delegations import Delegation

    now = now or timezone.now()
    tz_name = (tz_name or "").strip()
    if tz_name and not is_valid_timezone(tz_name):
        raise _bad("Nieznana strefa czasowa.", "WINDOWS_UNKNOWN_TIMEZONE")
    previous = CountryTimezone.objects.filter(region=region).values_list("timezone", flat=True).first() or ""
    if previous == tz_name:
        return
    plans = WindowPlan.objects.filter(stage__edition__competition_id=region.competition_id).select_related(
        "stage", "stage__edition"
    )
    frozen = 0
    for plan in plans.select_for_update(of=("self",)):
        view = load_plan(plan.stage)
        if view is None or not view.started(now):
            continue
        for delegation in Delegation.objects.filter(
            edition_id=plan.stage.edition_id, country=region
        ).select_related("country"):
            if delegation.pk in view.delegation_windows:
                continue
            window = delegation_default(view, delegation)
            if window is not None:
                DelegationWindow.objects.create(
                    plan=plan, delegation=delegation, window=window, assigned_at=now
                )
                frozen += 1
    if tz_name:
        CountryTimezone.objects.update_or_create(
            region=region,
            defaults={
                "timezone": tz_name,
                "set_by": actor if getattr(actor, "is_authenticated", False) else None,
                "set_at": now,
            },
        )
    else:
        CountryTimezone.objects.filter(region=region).delete()
    audit(
        actor,
        "time_windows.country_timezone_set",
        region,
        {"from": previous, "to": tz_name, "frozen_assignments": frozen},
        request=request,
    )


@transaction.atomic
def set_participant_timezone(participant, tz_name: str, *, actor=None, request=None, now=None) -> None:
    """Strefa ucznia do wyświetlania (pusta = strefa kraju). Nie zmienia okna – patrz model."""
    now = now or timezone.now()
    tz_name = (tz_name or "").strip()
    if tz_name and not is_valid_timezone(tz_name):
        raise _bad("Nieznana strefa czasowa.", "WINDOWS_UNKNOWN_TIMEZONE")
    previous = (
        ParticipantTimezone.objects.filter(participant=participant).values_list("timezone", flat=True).first()
        or ""
    )
    if previous == tz_name:
        return
    if tz_name:
        ParticipantTimezone.objects.update_or_create(
            participant=participant,
            defaults={
                "timezone": tz_name,
                "set_by": actor if getattr(actor, "is_authenticated", False) else None,
                "set_at": now,
            },
        )
    else:
        ParticipantTimezone.objects.filter(participant=participant).delete()
    audit(
        actor,
        "time_windows.participant_timezone_set",
        participant,
        {"from": previous, "to": tz_name},
        request=request,
    )
