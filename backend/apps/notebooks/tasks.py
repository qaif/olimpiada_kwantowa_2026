"""Zadania Celery notatników: wysyłka do piaskownicy, odbiór wyniku i beat napędzający kolejkę.

Odbiór (``collect_notebook_run``) **nie czeka w pętli**: sprawdza katalog wyników i, jeśli wyniku
jeszcze nie ma, ponawia się za ``COLLECT_INTERVAL`` sekund. Proces workera jest wolny między
sprawdzeniami – sto prac oddanych w ostatniej minucie nie zatrzyma skanu antywirusowego ani poczty
na czas ich wykonania w piaskownicy (worker ma dwa procesy na wszystkie kolejki).
"""

from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from . import services

logger = logging.getLogger(__name__)

COLLECT_INTERVAL = 2
#: Bezpiecznik ponowień: (limit czasu zadania ≤ 60 s + zapas 60 s) / 2 s z dużym marginesem.
#: Właściwy koniec oczekiwania wyznacza ``services.collect_run`` (``RUNNER_GRACE_SECONDS``).
MAX_COLLECT_RETRIES = 200


@shared_task
def pump_notebook_runs() -> dict:
    result = services.pump()
    if result["created"] or result["recovered"]:
        logger.info("Notatniki: %s nowych przebiegów, %s zgubionych.", result["created"], result["recovered"])
    return result


#: Krótkie limity zadań kolejki ``notebooks`` (``CELERY_TASK_ROUTES``, osobny worker
#: ``notebook-worker``): wysyłka czyta jeden plik, odbiór ocenia testy w budżecie
#: ``qclab.grader.MAX_RUN_WORK`` (kilkanaście sekund NumPy). Przekroczenie = błąd przebiegu,
#: a nie zajęty proces workera.
RUN_SOFT_LIMIT, RUN_HARD_LIMIT = 60, 75
COLLECT_SOFT_LIMIT, COLLECT_HARD_LIMIT = 90, 120


@shared_task(soft_time_limit=RUN_SOFT_LIMIT, time_limit=RUN_HARD_LIMIT)
def run_notebook(run_id: int) -> str:
    try:
        outcome = services.start_run(run_id)
    except SoftTimeLimitExceeded:
        services.mark_error(run_id, "error")
        return "timeout"
    if outcome == "sent":
        collect_notebook_run.apply_async((run_id,), countdown=COLLECT_INTERVAL)
    return outcome


@shared_task(
    bind=True,
    max_retries=MAX_COLLECT_RETRIES,
    soft_time_limit=COLLECT_SOFT_LIMIT,
    time_limit=COLLECT_HARD_LIMIT,
)
def collect_notebook_run(self, run_id: int) -> str:
    try:
        state = services.collect_run(run_id)
    except SoftTimeLimitExceeded:
        logger.warning("Notatniki: ocena przebiegu %s przekroczyła limit czasu.", run_id)
        services.mark_error(run_id, "error")
        return "timeout"
    if state == "wait":
        if self.request.is_eager:
            # Testy (``CELERY_TASK_ALWAYS_EAGER``): ponowienie wykonałoby się od razu, w pętli.
            return state
        if self.request.retries >= self.max_retries:
            services.mark_error(run_id, "runner_timeout")
            return "timeout"
        raise self.retry(countdown=COLLECT_INTERVAL)
    return state
