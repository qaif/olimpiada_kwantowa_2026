"""Zadania Celery oceniania: przypomnienia o terminach recenzji.

``remind_overdue_reviews`` uruchamia ``beat`` raz na dobę (``CELERY_BEAT_SCHEDULE``). Reguła
terminu i doboru recenzji stoi w ``apps.grading.deadlines`` – tutaj jest wyłącznie przebieg:
pogrupuj po recenzencie, złóż jeden list, oznacz recenzje jako przypomniane.

Dlaczego znacznik (``Review.reminded_at``) zapisujemy **po** zakolejkowaniu listu, a nie przed:
gdyby zapis padł, recenzent dostanie jutro drugie przypomnienie – to koszt znikomy. Odwrotna
kolejność groziłaby czymś gorszym: recenzja oznaczona jako przypomniana, o której nikt nigdy nie
przypomniał.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.core.models import audit

from .deadlines import group_by_reviewer, reminder_message, reviews_needing_reminder
from .models import Review

logger = logging.getLogger(__name__)


@shared_task
def remind_overdue_reviews() -> dict[str, int]:
    """Beat raz na dobę: jeden list do recenzenta z recenzjami po terminie i tuż przed nim.

    Zwraca ``{"reviewers": …, "reviews": …}`` – liczby, a nie adresy: wynik zadania ląduje
    w backendzie Celery i w logach, a tam nie ma po co trzymać, kto się spóźnia.

    W audycie zostaje sam licznik (``review.reminder_sent``) z tego samego powodu: fakt wysyłki
    jest częścią historii procesu, treść listu i lista prac – nie.
    """
    from apps.core.tasks import send_mail_task

    now = timezone.now()
    grouped = group_by_reviewer(reviews_needing_reminder(now))
    reviewers = 0
    reminded = 0
    for reviews in grouped.values():
        recipient = reviews[0].reviewer.user.email
        if not recipient:
            # Konto bez adresu e-mail (import, konto techniczne): nie ma dokąd wysłać, a znacznik
            # zostawiamy pusty – gdy adres się pojawi, przypomnienie pójdzie przy kolejnym przebiegu.
            continue
        subject, message = reminder_message(reviews, now)
        send_mail_task.delay(subject, message, [recipient])
        Review.objects.filter(pk__in=[review.pk for review in reviews]).update(reminded_at=now)
        # Wpis audytowy na członka komitetu, a nie na każdą recenzję z osobna: zdarzeniem jest
        # „poszedł list do tej osoby”, a nie „przypomniano o tej pracy”. W ``diff`` stoją same
        # liczby – treść listu i lista prac nie są faktem, który akta mają przechowywać.
        audit(None, "review.reminder_sent", reviews[0].reviewer, {"reviews": len(reviews)})
        reviewers += 1
        reminded += len(reviews)
    if reminded:
        logger.info("Przypomnienia o terminach recenzji: %s listów, %s recenzji", reviewers, reminded)
    return {"reviewers": reviewers, "reviews": reminded}
