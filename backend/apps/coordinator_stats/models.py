"""Liczniki wysyłki komunikatu po grupach domen i odbicia przypisane komunikatowi (STATS-02 § 2).

Dwie tabele, bo mają różny charakter i różny los:

- ``BroadcastDomainTally`` to **liczby** (zakolejkowane, pominięte, odbite) – bez danych osobowych,
  żyją tak długo, jak komunikat. Rejestr komunikatów (``accounts.MessageBroadcast``) celowo nie zna
  adresów odbiorców; liczniki po grupach domen tego nie zmieniają.
- ``BroadcastBounce`` to **adres**, który odbił list komunikatu – lista dla koordynatora w szczegółach
  raportu. Dane osobowe z retencją (``tasks.purge_broadcast_bounces``) i kasowaniem przy zmianie albo
  usunięciu adresu konta (``signals``). Liczniki w ``BroadcastDomainTally`` zostają po ich skasowaniu.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

from . import domains


class BounceKind(models.TextChoices):
    #: Adres albo domena nie istnieje (``email_delivery.bounces.HARD_STATUSES``).
    HARD = "hard", "adres nie istnieje"
    #: Serwer odbiorcy odrzucił list z powodu polityki – reputacja nadawcy, spam, blokada adresu IP
    #: (Microsoft ``S3150``). Mówi o **nas**, a nie o adresie odbiorcy.
    POLICY = "policy", "odrzucone przez politykę serwera odbiorcy"
    #: Wszystko inne: skrzynka pełna, chwilowa niedostępność, opóźnienie, błąd systemu.
    SOFT = "soft", "tymczasowe i inne"


#: Waga rodzaju przy kolejnym odbiciu tego samego adresu z tego samego komunikatu: zawiadomienie
#: o opóźnieniu bywa pierwsze, a ostateczna odmowa przychodzi po dniach – wygrywa poważniejsza.
KIND_WEIGHT = {BounceKind.SOFT: 0, BounceKind.HARD: 1, BounceKind.POLICY: 1}


class BroadcastDomainTally(models.Model):
    broadcast = models.ForeignKey(
        "accounts.MessageBroadcast",
        on_delete=models.CASCADE,
        related_name="domain_tallies",
        verbose_name="komunikat",
    )
    domain_group = models.CharField("grupa domen", max_length=16, choices=domains.CHOICES)
    queued = models.PositiveIntegerField("zakolejkowane", default=0)
    suppressed = models.PositiveIntegerField("pominięte (adres niedoręczalny)", default=0)
    bounced_hard = models.PositiveIntegerField("odbite – adres nie istnieje", default=0)
    bounced_policy = models.PositiveIntegerField("odbite – polityka serwera", default=0)
    bounced_soft = models.PositiveIntegerField("odbite – tymczasowe i inne", default=0)
    #: Podzbiór ``bounced_policy``: odrzucenia Microsoftu z kodem blokady ``S3150``.
    bounced_s3150 = models.PositiveIntegerField("w tym Microsoft S3150", default=0)

    objects = competition_scoped_manager("broadcast__competition")

    class Meta:
        verbose_name = "liczniki komunikatu w grupie domen"
        verbose_name_plural = "liczniki komunikatów w grupach domen"
        constraints = [
            models.UniqueConstraint(
                fields=["broadcast", "domain_group"], name="coordinator_stats_tally_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.broadcast_id} / {self.domain_group}"

    @property
    def bounced(self) -> int:
        return self.bounced_hard + self.bounced_policy + self.bounced_soft


class BroadcastBounce(models.Model):
    broadcast = models.ForeignKey(
        "accounts.MessageBroadcast",
        on_delete=models.CASCADE,
        related_name="reported_bounces",
        verbose_name="komunikat",
    )
    email = models.CharField("adres e-mail (małe litery)", max_length=254)
    domain_group = models.CharField("grupa domen", max_length=16, choices=domains.CHOICES)
    kind = models.CharField("rodzaj", max_length=8, choices=BounceKind.choices)
    status_code = models.CharField("kod stanu (RFC 3463)", max_length=16, blank=True)
    reason = models.CharField("powód (skrócony)", max_length=300, blank=True)
    microsoft_s3150 = models.BooleanField("Microsoft S3150", default=False)
    #: ``email_delivery.models.Source`` – odmowa relaya w trakcie wysyłki albo zawiadomienie (DSN).
    source = models.CharField("źródło", max_length=8, blank=True)
    created_at = models.DateTimeField("pierwsze odbicie", default=timezone.now, db_index=True)
    updated_at = models.DateTimeField("ostatnie odbicie", auto_now=True)

    objects = competition_scoped_manager("broadcast__competition")

    class Meta:
        verbose_name = "odbicie listu komunikatu"
        verbose_name_plural = "odbicia listów komunikatów"
        ordering = ("kind", "email")
        constraints = [
            models.UniqueConstraint(fields=["broadcast", "email"], name="coordinator_stats_bounce_unique"),
        ]
        indexes = [models.Index(fields=["email"], name="coordinator_stats_bounce_email")]

    def __str__(self) -> str:
        return f"{self.broadcast_id} / {self.email}"
