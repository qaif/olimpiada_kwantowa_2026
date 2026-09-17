"""Zadania Celery reklamacji: finalizacja rozwiązań po zamknięciu okna reklamacji.

``finalize_closed_appeal_windows`` uruchamia ``beat`` co 5 minut (``CELERY_BEAT_SCHEDULE``).
Zadanie jest idempotentne: etap bez rozwiązań w GRADED_PROVISIONAL nie trafia ani do wyniku, ani –
odkąd ``stages_with_closed_appeal_window`` zawęża listę – do samej pętli. Bez tego co pięć minut
przeglądane byłyby wszystkie zamknięte etapy w historii olimpiady.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .services import finalize_unappealed, stages_with_closed_appeal_window

logger = logging.getLogger(__name__)


@shared_task
def finalize_closed_appeal_windows() -> dict[str, int]:
    """Beat co 5 min: po ``appeal_window_closes_at`` GRADED_PROVISIONAL → FINAL.

    Rozwiązania w stanie APPEALED zostają nietknięte – czekają na decyzję komisji.
    Zwraca mapę ``{id etapu: liczba sfinalizowanych rozwiązań}`` wyłącznie dla etapów, w których
    coś się zmieniło (klucze jako tekst – wynik zadania jest serializowany do JSON).

    Przebieg obchodzi **wszystkie** konkursy, każdy w jego kontekście. Identyfikatory etapów są
    unikalne w całej instalacji, więc mapa wyniku zostaje płaska – zagnieżdżenie jej po konkursie
    zmieniłoby kształt odpowiedzi zadania, a to jest kontrakt, który czytają logi i testy.
    """
    from apps.competitions.scoping import each_competition

    now = timezone.now()
    finalized: dict[str, int] = {}
    for competition in each_competition():
        for stage in stages_with_closed_appeal_window(now, competition):
            count = finalize_unappealed(stage, now=now)
            if count:
                finalized[str(stage.pk)] = count
    if finalized:
        logger.info("Finalizacja po oknie reklamacji: %s", finalized)
    return finalized
