"""Zmiana wersji oficjalnej zadania zapisana poza ekranami tłumaczeń (TR-01 § 2).

Koordynator podmienia PDF albo tytuł zadania na dotychczasowym ekranie „Zadania”
(``apps.competitions.services``), a ten o tłumaczeniach nie wie i nie musi. Sygnał ``post_save``
porównuje odcisk wersji (``models.source_fingerprint``) z zapisanym; różnica podnosi numer wersji
i oznacza tłumaczenia jako nieaktualne. Zadanie bez wiersza źródła kończy się jednym zapytaniem.
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.competitions.models import Problem


@receiver(post_save, sender=Problem, dispatch_uid="problem_translations_sync_source")
def problem_saved(sender, instance: Problem, created: bool, raw: bool = False, **kwargs) -> None:
    if created or raw:
        return
    from .services import sync_source

    sync_source(instance)
