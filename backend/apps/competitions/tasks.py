"""Zadania okresowe kalendarza zawodów.

Na razie jedno: przypomnienie o jutrzejszej rozmowie kwalifikacyjnej. Moduł istnieje, bo Celery
znajduje zadania wyłącznie w ``tasks.py`` aplikacji (``app.autodiscover_tasks()`` w
``config/celery.py``) – cała logika siedzi w ``apps.competitions.video`` i tam należy jej szukać.

Przebieg jest **dzienny**, i to jest zarazem reguła wysyłki: uczestnik ma dostać jedno
przypomnienie, a nie dwadzieścia cztery. Ta sama decyzja, co przy ``remind_overdue_reviews``
w ``apps.grading.tasks``.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def remind_interviews() -> int:
    """Wysyła przypomnienia o rozmowach zaczynających się w ciągu najbliższej doby.

    Zwraca liczbę listów przekazanych do kolejki – wartość jest dla logu i testów, nikt jej nie
    czyta z bazy. Idempotencję zapewnia ``InterviewBooking.reminder_sent_at``, a nie częstotliwość
    przebiegu: beat po restarcie potrafi puścić zadanie od razu po poprzednim.
    """
    from .video import send_interview_reminders

    return send_interview_reminders()
