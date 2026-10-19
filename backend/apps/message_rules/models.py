"""Reguły automatycznych przypomnień i ślad tego, co z nich już wygenerowano (MSG-AUTO-01).

Reguła **nie wysyła** niczego sama. Jest przepisem „z każdego warsztatu (z każdego dnia przed
terminem etapu) zrób komunikat planowany”, a komunikat to zwykły wiersz
``accounts.MessageBroadcast`` w stanie „zaplanowana” – ten sam, który koordynator widzi i anuluje
na ekranie komunikatów. Dzięki temu wysyłka, liczenie odbiorców w chwili wysyłki, przeterminowanie
i audyt mają jedną implementację (``apps.accounts.messaging``), a ta aplikacja tylko planuje.

Dlaczego osobna aplikacja, a nie model w ``accounts``: reguła jest klientem wysyłki, nie jej częścią
(zależność w jedną stronę), a ``accounts/models.py`` jest wspólnym plikiem wielu równoległych prac.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import BroadcastGroup
from apps.tenancy.managers import CompetitionScopedManager


class RuleKind(models.TextChoices):
    """Skąd reguła bierze wydarzenia: z harmonogramu warsztatów albo z terminu etapu."""

    WORKSHOP = "WORKSHOP", "przypomnienie o warsztacie"
    STAGE_DEADLINE = "STAGE_DEADLINE", "przypomnienie przed terminem etapu"


class RuleTiming(models.TextChoices):
    """Kiedy wysłać względem wydarzenia.

    ``DAY_AT`` liczy **dni kalendarzowe w czasie polskim** i godzinę ścienną („w dniu warsztatu
    o 06:00”, „7 dni przed terminem o 09:00”) – tak myśli organizator i tak ma działać także w dniu
    zmiany czasu. ``HOURS_BEFORE`` liczy **rzeczywiste godziny** przed początkiem warsztatu
    – „na 2 godziny przed” ma znaczyć dwie godziny, nie „tę samą godzinę na zegarze minus dwa”.
    """

    DAY_AT = "DAY_AT", "N dni przed, o wybranej godzinie"
    HOURS_BEFORE = "HOURS_BEFORE", "N godzin przed początkiem"


class MessageRule(models.Model):
    """Reguła przypomnienia jednego konkursu.

    Grupa odbiorców jest zapisana w **tej samej postaci**, co w zaplanowanym komunikacie
    (``group``, ``parameters`` – argumenty ``resolve_recipients``, ``target`` – opis do historii),
    bo trafia do ``schedule_broadcast`` bez przekształceń. Odbiorców reguła nie liczy nigdy: robi to
    beat wysyłki w chwili wysyłki każdego wygenerowanego komunikatu.

    ``created_by`` jest **aktorem** wygenerowanych komunikatów (``broadcast.scheduled``): to autor
    reguły zdecydował o listach, beat tylko ich dopilnował. Konto bez roli koordynatora nie
    wygeneruje niczego – ``schedule_broadcast`` sprawdza rolę aktora, a błąd ląduje w ``last_error``.
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        verbose_name="konkurs",
        on_delete=models.PROTECT,
        related_name="message_rules",
    )
    name = models.CharField("nazwa", max_length=120)
    kind = models.CharField("rodzaj", max_length=16, choices=RuleKind.choices)
    enabled = models.BooleanField("włączona", default=True)
    #: Kod grupy z ``BroadcastGroup`` – **bez** ``choices`` w polu celowo: lista grup należy do
    #: ``accounts`` i rośnie z kolejnymi prośbami organizatora, a ``choices`` tutaj oznaczałoby
    #: migrację tej aplikacji przy każdej nowej grupie. Kod sprawdza formularz (lista z ``accounts``).
    group = models.CharField("grupa odbiorców", max_length=32)
    #: Argumenty ``resolve_recipients`` w postaci JSON – jak ``MessageBroadcast.parameters``.
    parameters = models.JSONField("parametry grupy", default=dict, blank=True)
    #: Opis grupy do historii – jak ``MessageBroadcast.target`` (etykieta z chwili zapisu reguły).
    target = models.JSONField("opis grupy", default=dict, blank=True)
    #: Etap, którego termin (``deadline_at``) wyznacza wysyłki reguły „termin etapu”. ``SET_NULL``:
    #: skasowanie etapu nie może skasować reguły po cichu – reguła zostaje i nic nie generuje.
    stage = models.ForeignKey(
        "competitions.Stage",
        verbose_name="etap (termin)",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_rules",
    )
    subject_template = models.CharField("szablon tematu", max_length=200)
    body_template = models.TextField("szablon treści")
    link = models.URLField("link", max_length=500, blank=True)
    timing = models.CharField(
        "moment wysyłki", max_length=16, choices=RuleTiming.choices, default=RuleTiming.DAY_AT
    )
    #: Dni przed wydarzeniem (``DAY_AT``) – lista liczb, np. ``[30, 7, 1]``; ``[0]`` = w dniu wydarzenia.
    days_before = models.JSONField("dni przed", default=list, blank=True)
    send_time = models.TimeField("godzina wysyłki", default=dt.time(6, 0))
    hours_before = models.PositiveSmallIntegerField("godzin przed", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="autor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField("utworzona", default=timezone.now)
    updated_at = models.DateTimeField("zmieniona", auto_now=True)
    last_run_at = models.DateTimeField("ostatni przebieg", null=True, blank=True)
    #: Ostatni błąd generowania (np. autor bez roli) – pusty, gdy ostatni przebieg się udał.
    last_error = models.CharField("ostatni błąd", max_length=500, blank=True)

    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "reguła przypomnienia"
        verbose_name_plural = "reguły przypomnień"
        ordering = ("name", "id")

    def __str__(self) -> str:
        return self.name

    @property
    def target_label(self) -> str:
        return str((self.target or {}).get("label") or "")

    @property
    def group_label(self) -> str:
        """Etykieta grupy jak na ekranie komunikatów; nieznany kod – sam kod (nie wyjątek)."""
        try:
            return str(BroadcastGroup(self.group).label)
        except ValueError:
            return self.group


class MessageRuleOccurrence(models.Model):
    """„Ta reguła obsłużyła już to wystąpienie” – jedyny mechanizm idempotencji generowania.

    Wiersz powstaje razem z komunikatem planowanym i **zostaje** po jego anulowaniu: ręczne
    „Anuluj” przy wygenerowanym przypomnieniu ma znaczyć „nie wysyłaj tego jednego”, a nie
    „wygeneruj od nowa za pięć minut”. Usuwa go wyłącznie samo generowanie, gdy wycofuje komunikat
    nieaktualny (zmiana reguły albo harmonogramu – ``apps.message_rules.services``), żeby nowa
    wersja mogła powstać.

    ``fingerprint`` to skrót terminu, tematu, treści i grupy – porównanie z nim mówi, czy czekający
    komunikat jest jeszcze tym, który reguła wygenerowałaby dziś.
    """

    rule = models.ForeignKey(MessageRule, on_delete=models.CASCADE, related_name="occurrences")
    key = models.CharField("klucz wystąpienia", max_length=200)
    broadcast = models.ForeignKey(
        "accounts.MessageBroadcast",
        verbose_name="komunikat",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    send_at = models.DateTimeField("termin wysyłki")
    fingerprint = models.CharField("odcisk", max_length=64)
    created_at = models.DateTimeField("wygenerowano", default=timezone.now)

    class Meta:
        verbose_name = "wystąpienie reguły"
        verbose_name_plural = "wystąpienia reguł"
        constraints = [
            models.UniqueConstraint(fields=("rule", "key"), name="message_rule_occurrence_unique_key"),
        ]

    def __str__(self) -> str:
        return f"{self.rule_id}:{self.key}"
