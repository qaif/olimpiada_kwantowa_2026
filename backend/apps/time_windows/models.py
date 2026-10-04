"""Okna czasowe etapu zdalnego: plan okien, przydziały i strefy czasowe (docs/tasks/TZ-01.md § 1).

Skąd to się wzięło. Olimpiada międzynarodowa (``iqo``) ma delegacje od obu Ameryk po Azję, a etap
zdalny miał jedno globalne okno ``Stage.opens_at``–``deadline_at``: start o 10:00 w Warszawie to
4:00 w Nowym Jorku i 17:00 w Tokio. Tryb okien rozkłada **ten sam** etap na kilka startów w ciągu
doby, każdy o tym samym czasie pracy, i przydziela do nich kraje.

Granice modeli:

- ``WindowPlan`` – „ten etap pracuje w oknach”: czas pracy i godzina preferowana. Jeden na etap.
  Brak wiersza znaczy „etap jak dotąd” – i to jest stan **każdego** istniejącego etapu,
- ``TimeWindow`` – start jednego okna. Koniec nie jest kolumną, tylko wyliczeniem (start + czas
  pracy planu): czas pracy jest **jeden** dla wszystkich okien, bo inaczej zawody nie byłyby równe,
- ``DelegationWindow`` / ``ParticipantWindow`` – przydziały ręczne. Domyślny przydział kraju
  **nie jest** zapisywany: liczy się ze strefy kraju (``apps.time_windows.services``), a wszystkie
  jego wejścia (okna, godzina preferowana, strefy krajów) są zamrożone od startu pierwszego okna,
  więc wyliczenie nie może się zmienić w trakcie zawodów,
- ``CountryTimezone`` – poprawka mapy ``zones.COUNTRY_TIMEZONES`` dla kraju wielostrefowego,
- ``ParticipantTimezone`` – strefa ucznia **do wyświetlania**.

Nic tu nie dotyka modeli zawodów: etap, delegacja i uczestnik są wskazywane kluczami obcymi,
a migracje tej aplikacji nie zmieniają ani jednej tabeli innej aplikacji.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.timezone import now as tz_now

from apps.competitions.scoping import competition_scoped_manager

#: Przełącznik konkursu (``apps.tenancy.models.FEATURE_DEFAULTS``). Jedyna nazwa w kodzie.
FLAG = "stage_time_windows"

#: Granice czasu pracy okna. Minimum – kwadrans (krócej to nie jest etap, tylko pomyłka w polu),
#: maksimum – doba: okno dłuższe od doby zachodziłoby na samo siebie w kolejnym dniu.
MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 24 * 60

#: Sufit dodatkowego czasu jednego ucznia (dostosowanie). Doba – więcej to inny etap.
MAX_EXTRA_MINUTES = 24 * 60

#: Ile okien może mieć plan. Doba ma 24 godziny; więcej niż 12 startów to już nie okna, tylko
#: harmonogram indywidualny, a ekran przydziału przestaje się mieścić.
MAX_WINDOWS = 12


class WindowPlan(models.Model):
    """„Ten etap pracuje w oknach” – czas pracy i reguła przydziału domyślnego."""

    stage = models.OneToOneField(
        "competitions.Stage", on_delete=models.CASCADE, related_name="window_plan", verbose_name="etap"
    )
    duration_minutes = models.PositiveIntegerField("czas pracy (min)")
    #: Godzina lokalna, do której przydział domyślny dopasowuje okno kraju (0–23).
    preferred_local_hour = models.PositiveSmallIntegerField("preferowana godzina startu", default=10)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField("utworzony", default=tz_now)
    updated_at = models.DateTimeField("zmieniony", default=tz_now)

    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "plan okien etapu"
        verbose_name_plural = "plany okien etapów"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gte=MIN_DURATION_MINUTES)
                & models.Q(duration_minutes__lte=MAX_DURATION_MINUTES),
                name="time_windows_plan_duration_range",
            ),
            models.CheckConstraint(
                condition=models.Q(preferred_local_hour__lte=23),
                name="time_windows_plan_hour_range",
            ),
        ]

    def __str__(self) -> str:
        return f"okna etapu {self.stage_id} ({self.duration_minutes} min)"

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.duration_minutes)


class TimeWindow(models.Model):
    """Jedno okno planu: etykieta i start. Koniec = start + czas pracy planu."""

    plan = models.ForeignKey(WindowPlan, on_delete=models.CASCADE, related_name="windows")
    label = models.CharField("oznaczenie", max_length=16)
    starts_at = models.DateTimeField("start")

    objects = competition_scoped_manager("plan__stage__edition__competition")

    class Meta:
        verbose_name = "okno czasowe"
        verbose_name_plural = "okna czasowe"
        ordering = ("starts_at", "id")
        constraints = [
            models.UniqueConstraint(fields=["plan", "label"], name="time_windows_window_unique_label"),
            models.UniqueConstraint(fields=["plan", "starts_at"], name="time_windows_window_unique_start"),
        ]

    def __str__(self) -> str:
        return f"okno {self.label} ({self.starts_at:%Y-%m-%d %H:%M} UTC)"

    @property
    def ends_at(self):
        return self.starts_at + self.plan.duration


class DelegationWindow(models.Model):
    """Ręczny przydział delegacji (kraju) do okna – nadpisuje przydział domyślny ze strefy."""

    plan = models.ForeignKey(WindowPlan, on_delete=models.CASCADE, related_name="delegation_windows")
    delegation = models.ForeignKey(
        "accounts.Delegation", on_delete=models.CASCADE, related_name="time_windows", verbose_name="delegacja"
    )
    #: ``PROTECT``: okna z przydziałem nie da się skasować po cichu – serwis każe najpierw
    #: przenieść kraj, żeby nikt nie wylądował w „pierwszym oknie” przez przypadek.
    window = models.ForeignKey(TimeWindow, on_delete=models.PROTECT, related_name="delegation_assignments")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    assigned_at = models.DateTimeField("przydzielono", default=tz_now)

    objects = competition_scoped_manager("plan__stage__edition__competition")

    class Meta:
        verbose_name = "przydział delegacji do okna"
        verbose_name_plural = "przydziały delegacji do okien"
        constraints = [
            models.UniqueConstraint(fields=["plan", "delegation"], name="time_windows_delegation_unique"),
        ]

    def __str__(self) -> str:
        return f"delegacja {self.delegation_id} → okno {self.window_id}"


class ParticipantWindow(models.Model):
    """Wyjątek jednego ucznia: inne okno i/lub dodatkowy czas (dostosowanie), zawsze z powodem.

    ``window`` puste znaczy „okno jak delegacja/kraj” – wiersz istnieje wtedy wyłącznie dla
    dodatkowego czasu. Powód jest obowiązkowy, bo wyjątek od równych warunków zawodów jest
    decyzją, którą ktoś kiedyś będzie musiał wytłumaczyć – dokładnie jak kwalifikacja ręczna.
    """

    plan = models.ForeignKey(WindowPlan, on_delete=models.CASCADE, related_name="participant_windows")
    participant = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="time_windows",
        verbose_name="uczestnik",
    )
    window = models.ForeignKey(
        TimeWindow,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="participant_assignments",
    )
    extra_minutes = models.PositiveIntegerField("dodatkowy czas (min)", default=0)
    reason = models.CharField("powód", max_length=300)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    set_at = models.DateTimeField("ustawiono", default=tz_now)

    objects = competition_scoped_manager("plan__stage__edition__competition")

    class Meta:
        verbose_name = "wyjątek ucznia w oknach"
        verbose_name_plural = "wyjątki uczniów w oknach"
        constraints = [
            models.UniqueConstraint(fields=["plan", "participant"], name="time_windows_participant_unique"),
            models.CheckConstraint(
                condition=models.Q(extra_minutes__lte=MAX_EXTRA_MINUTES),
                name="time_windows_participant_extra_range",
            ),
            models.CheckConstraint(condition=~models.Q(reason=""), name="time_windows_participant_reason"),
        ]

    def __str__(self) -> str:
        return f"uczestnik {self.participant_id} (+{self.extra_minutes} min)"

    @property
    def extra(self) -> timedelta:
        return timedelta(minutes=self.extra_minutes)


class CountryTimezone(models.Model):
    """Strefa kraju ustawiona przez koordynatora zamiast strefy stolicy z ``zones.COUNTRY_TIMEZONES``."""

    region = models.OneToOneField(
        "accounts.Region", on_delete=models.CASCADE, related_name="time_zone_setting", verbose_name="kraj"
    )
    timezone = models.CharField("strefa czasowa", max_length=64)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    set_at = models.DateTimeField("ustawiono", default=tz_now)

    objects = competition_scoped_manager("region__competition")

    class Meta:
        verbose_name = "strefa czasowa kraju"
        verbose_name_plural = "strefy czasowe krajów"

    def __str__(self) -> str:
        return f"{self.region_id}: {self.timezone}"

    def clean(self) -> None:
        from .zones import is_valid_timezone

        if not is_valid_timezone(self.timezone):
            raise ValidationError({"timezone": "Nieznana strefa czasowa."})


class ParticipantTimezone(models.Model):
    """Strefa czasowa ucznia – **wyłącznie** do wyświetlania godzin w jego panelu.

    Przydziału do okna strefa ucznia nie zmienia i to jest decyzja bezpieczeństwa, a nie
    uproszczenie: strefę ustawia opiekun drużyny w dowolnej chwili, a gdyby przesuwała okno, uczeń,
    który zobaczył zadania w oknie A, mógłby „przeprowadzić się” do okna C i dostać drugi start.
    """

    participant = models.OneToOneField(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="time_zone_setting",
        verbose_name="uczestnik",
    )
    timezone = models.CharField("strefa czasowa", max_length=64)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    set_at = models.DateTimeField("ustawiono", default=tz_now)

    objects = competition_scoped_manager("participant__competition")

    class Meta:
        verbose_name = "strefa czasowa ucznia"
        verbose_name_plural = "strefy czasowe uczniów"

    def __str__(self) -> str:
        return f"{self.participant_id}: {self.timezone}"

    def clean(self) -> None:
        from .zones import is_valid_timezone

        if not is_valid_timezone(self.timezone):
            raise ValidationError({"timezone": "Nieznana strefa czasowa."})
