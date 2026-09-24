"""Zadania Celery oceny AI: jedna ocena na zadanie i okresowa siatka asekuracyjna kolejki.

Argumentem zadania jest **wyłącznie identyfikator oceny**. Klucz API nie jedzie przez broker
(Redis), nie ląduje w wyniku zadania ani w logu workera – zadanie czyta go z bazy samo, w chwili
wywołania, i trzyma w pamięci tylko na czas budowy klienta (``apps.ai_grading.client``).

Ponawianie: ``RateLimitError``, błędy 5xx i sieć wracają jako :class:`RunOutcome` ``RETRY``
z wykładniczym opóźnieniem (albo z ``retry-after`` z odpowiedzi 429). Ostatnia próba kończy się
błędem z komunikatem dla koordynatora, a nie kolejnym ponowieniem – limit prób jest tu
bezpiecznikiem kosztu, nie tylko cierpliwości.
"""

from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from .services import mark_failed, pump, recover_stale, run_assessment

logger = logging.getLogger(__name__)

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 900
#: Twardy limit czasu jednej oceny. Musi być krótszy niż ``AI_GRADING_STALE_MINUTES`` (30 min),
#: żeby ocena uznana za zgubioną naprawdę nie mogła już trwać – inaczej ogranicznik
#: współbieżności wypuściłby drugą, gdy pierwsza jeszcze liczy.
SOFT_TIME_LIMIT_SECONDS = 15 * 60
TIME_LIMIT_SECONDS = 16 * 60


@shared_task(
    bind=True,
    max_retries=MAX_RETRIES,
    soft_time_limit=SOFT_TIME_LIMIT_SECONDS,
    time_limit=TIME_LIMIT_SECONDS,
)
def run_ai_assessment(self, assessment_id: int) -> str:
    """Liczy jedną ocenę i woła kolejkę po następną. Zwraca status końcowy (do logu workera)."""
    final_attempt = self.request.retries >= self.max_retries
    try:
        outcome = run_assessment(assessment_id, final_attempt=final_attempt)
    except SoftTimeLimitExceeded:
        outcome = mark_failed(
            assessment_id,
            "timeout",
            "Ocena trwała dłużej niż dopuszczalne 15 minut i została przerwana. Spróbuj ponownie.",
        )
    except Exception:
        # Błąd w naszym kodzie albo w bazie – ocena nie może zostać w ``RUNNING`` na pół godziny,
        # bo blokowałaby kolejkę. Treść wyjątku idzie do logu, a koordynator widzi zdanie ogólne.
        logger.exception("Ocena AI %s: nieoczekiwany błąd.", assessment_id)
        outcome = mark_failed(assessment_id, "internal", "Wewnętrzny błąd serwisu przy liczeniu oceny AI.")
    if outcome.status == "RETRY":
        # Opóźnienie przycięte do kwadransa także wtedy, gdy ``retry-after`` każe czekać dłużej:
        # ocena czekająca na ponowienie liczy się do ogranicznika tylko przez
        # ``AI_GRADING_STALE_MINUTES``, a po tym czasie kolejka wypuściłaby ją drugi raz.
        countdown = min(
            outcome.retry_after or RETRY_BASE_SECONDS * 2**self.request.retries, RETRY_MAX_SECONDS
        )
        raise self.retry(countdown=countdown)
    pump()
    return str(outcome.status)


@shared_task
def pump_ai_assessments() -> dict[str, int]:
    """Beat co kilka minut: domyka oceny osierocone przez restart workera i dopycha kolejkę.

    W normalnym przebiegu nie robi nic – kolejkę napędza koniec każdej oceny. Istnieje dla dwóch
    sytuacji, w których ten napęd staje: worker zginął w trakcie oceny (``RUNNING`` bez końca)
    albo zadanie zniknęło z brokera (``PENDING`` przekazana do kolejki, której już nie ma).
    """
    recovered = recover_stale()
    dispatched = pump()
    if recovered or dispatched:
        logger.info("Ocena AI: %s przerwanych, %s wypuszczonych do kolejki.", recovered, len(dispatched))
    return {"recovered": recovered, "dispatched": len(dispatched)}
