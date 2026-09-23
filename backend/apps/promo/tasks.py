"""Retencja pseudonimów adresów IP przy pobraniach plakatów.

``PromoDownload.ip_hash`` jest pseudonimem adresu IP, czyli daną osobową (patrz
``apps.promo.tracking``) – i jako taka ma termin usunięcia wpisany do rejestru czynności
przetwarzania (``apps.accounts.processing_register``, czynność ``plakaty``). Termin egzekwuje to
zadanie: raz na dobę zeruje skrót przy zdarzeniach starszych niż ``IP_HASH_RETENTION_MONTHS``
miesięcy. Zdarzenie **zostaje** – data i plakat nie mówią nic o człowieku, a bez nich liczba
pobrań łącznie malałaby z każdym dniem, czyli kłamałaby.

Skutek widoczny na ekranie koordynatora: kolumna „unikalne IP · od początku” liczy wyłącznie
pobrania z ostatnich dwunastu miesięcy. Ekran mówi to wprost pod tabelą.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.accounts.retention import add_months

from .models import IP_HASH_RETENTION_MONTHS, PromoDownload

logger = logging.getLogger(__name__)


def ip_hash_cutoff(now=None):
    """Chwila, przed którą pobrania tracą pseudonim adresu IP."""
    return add_months(now or timezone.now(), -IP_HASH_RETENTION_MONTHS)


def clear_expired_ip_hashes(now=None) -> int:
    """Zeruje ``ip_hash`` pobrań starszych niż okres retencji. Zwraca liczbę wyzerowanych wierszy.

    Jedno ``UPDATE`` na całą tabelę, bez ``for_competition`` i bez ``each_competition``: termin
    retencji jest wspólny dla wszystkich konkursów instalacji (stoi w rejestrze czynności, a nie
    w ustawieniach konkursu), więc przebieg per konkurs byłby tym samym zapytaniem powtórzonym
    N razy.
    """
    cleared = PromoDownload.objects.filter(
        downloaded_at__lt=ip_hash_cutoff(now), ip_hash__isnull=False
    ).update(ip_hash=None)
    if cleared:
        logger.info("Retencja pobrań plakatów: wyzerowano pseudonim IP w %s zdarzeniach.", cleared)
    return cleared


@shared_task(name="apps.promo.tasks.clear_expired_ip_hashes")
def clear_expired_ip_hashes_task() -> int:
    """Wywołanie z ``beat`` (raz na dobę, ``CELERY_BEAT_SCHEDULE``).

    Raz na dobę, bo termin jest liczony w miesiącach – częstszy przebieg przesuwałby moment
    wyzerowania o minuty i nie zmieniał niczego poza obciążeniem bazy (ten sam rachunek, co przy
    ``apps.accounts.retention.anonymise_expired_editions``).
    """
    return clear_expired_ip_hashes()
