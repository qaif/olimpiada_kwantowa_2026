"""Ustawienia edytora LaTeX per etap: udostępnianie linków (TEX-01 D5) i asystent AI (D8).

Osobny wiersz w tej aplikacji, a nie kolumna ``competitions.Stage``: etap nie musi wiedzieć
o edytorze, a brak wiersza znaczy dokładnie stan domyślny – praca indywidualna.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class StageLatexSettings(models.Model):
    stage = models.OneToOneField(
        "competitions.Stage",
        on_delete=models.CASCADE,
        related_name="latex_settings",
        verbose_name="etap",
    )
    sharing_enabled = models.BooleanField(
        "udostępnianie linków w edytorze LaTeX",
        default=False,
        help_text="Włączone – uczestnik może zaprosić innych do swojego projektu linkiem. "
        "Wyłączenie natychmiast odcina współpracowników.",
    )
    ai_enabled = models.BooleanField(
        "asystent AI w edytorze LaTeX",
        default=False,
        help_text="Włączone – w edytorze działa asystent AI (czat, poprawianie błędów kompilacji). "
        "Na zawodach to pomoc niedozwolona; włączaj tylko na etapach treningowych.",
    )
    updated_at = models.DateTimeField("zmieniono", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="zmienił(a)",
    )

    class Meta:
        verbose_name = "ustawienia edytora LaTeX etapu"
        verbose_name_plural = "ustawienia edytora LaTeX etapów"

    def __str__(self) -> str:
        return f"Edytor LaTeX: etap {self.stage_id}"
