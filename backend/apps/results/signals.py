"""Odbiorniki sygnałów aplikacji wyników – dziś jeden: pamięć podręczna statystyk.

Dlaczego sygnał, a nie wywołanie w serwisie: **wycofanie** ogłoszenia nie ma własnego serwisu.
Koordynator robi je, czyszcząc ``Stage.results_published_at`` (panel administracyjny), a rekord
``ResultsPublication`` zostaje jako ślad. Jedynym miejscem, które widzi każdą taką zmianę – z panelu,
z ``/admin/``, z komendy – jest zapis wiersza etapu (audyt 10.10.2026, S2).
"""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.competitions.models import Stage

from . import statistics
from .models import ResultsPublication


def _invalidate_after_commit() -> None:
    """Zrzut wpisu **po** commicie: wcześniejszy pozwoliłby równoległemu wejściu odbudować wpis
    ze stanu sprzed zmiany i trzymać go przez kolejne dziesięć minut."""
    transaction.on_commit(statistics.invalidate)


@receiver(post_save, sender=Stage, dispatch_uid="results.statistics.invalidate_on_stage_save")
def _on_stage_saved(sender, instance, update_fields=None, **kwargs) -> None:
    # Tylko zapis, który może dotyczyć znacznika ogłoszenia. Zapis pełny (``update_fields=None``,
    # formularz admina) też się liczy – nie wiadomo, które pola zmienił.
    if update_fields is not None and "results_published_at" not in update_fields:
        return
    _invalidate_after_commit()


@receiver(post_save, sender=ResultsPublication, dispatch_uid="results.statistics.invalidate_on_publication")
def _on_publication_saved(sender, instance, **kwargs) -> None:
    # Ponowna publikacja nadpisuje snapshot – statystyki mają się zgadzać z nową tabelą od razu.
    _invalidate_after_commit()
