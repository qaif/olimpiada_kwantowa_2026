"""Przypięte pozycje menu koordynatora (UX-PANEL-04 D9).

Jeden wiersz = jedna pozycja menu panelu, którą koordynator przypiął sobie nad sekcjami menu i na Starcie.
Wiersz trzyma **ścieżkę względem prefiksu konkursu** (``coordinator/accounts/?role=participant``), a nie
podpis: podpis bierze się z menu w chwili wyświetlenia (``services.pinned_items``). Dzięki temu w tabeli nie
ma danych osobowych (przypiąć wolno wyłącznie pozycję menu, a nie kartę osoby), podpis jest zawsze w języku
oglądającego, a pozycja za wyłączoną flagą albo po usuniętym etapie po prostu się nie rysuje – zamiast
prowadzić do 404.

Ścieżka bez prefiksu, bo ten sam konkurs bywa pod prefiksem (``/iqo/…``) albo pod własną domeną – adres
składa się w żądaniu oglądającego (``get_script_prefix()`` + ścieżka), tak jak ``reverse``.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

#: Najdłuższa ścieżka pozycji menu z dopiskiem (``coordinator/stages/<id>/assignments/`` ma ~40 znaków).
PATH_MAX_LENGTH = 300


class PinnedItem(models.Model):
    """Pozycja menu przypięta przez jedno konto w jednym konkursie."""

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="konkurs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coordinator_pins",
        verbose_name="konto",
    )
    path = models.CharField("ścieżka (względem prefiksu konkursu)", max_length=PATH_MAX_LENGTH)
    position = models.PositiveSmallIntegerField("kolejność", default=0)
    created_at = models.DateTimeField("przypięto", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "przypięta pozycja menu koordynatora"
        verbose_name_plural = "przypięte pozycje menu koordynatora"
        ordering = ("position", "pk")
        constraints = [
            models.UniqueConstraint(fields=["user", "competition", "path"], name="coordpin_one_per_path"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} → {self.path}"
