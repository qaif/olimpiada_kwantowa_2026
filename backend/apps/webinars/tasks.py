"""Zadania Celery webinarów: zaproszenie (na żądanie koordynatora) i przypomnienia (beat).

Listy do całej grupy odbiorców składa worker, a nie żądanie koordynatora: „wszyscy uczestnicy
konkursu” to bywa kilka tysięcy listów, a każdy w języku swojego odbiorcy.

Oba zadania pracują w kontekście **konkursu webinaru** (``competition_context``): odnośnik do
panelu w liście powstaje przez ``absolute_url``, które czyta konkurs z kontekstu – bez tego
uczestnik konkursu B dostałby link pod domenę konkursu A.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def send_webinar_invitation(webinar_id: int) -> int:
    """Zaproszenie – kolejkowane przez ``services.announce`` po commicie. Webinar odwołany: nic."""
    from apps.tenancy.context import competition_context

    from .models import Webinar
    from .notifications import INVITE, send
    from .services import available

    webinar = Webinar._base_manager.select_related("competition").filter(pk=webinar_id).first()
    if webinar is None or webinar.cancelled_at is not None or not available(webinar.competition):
        return 0
    with competition_context(webinar.competition):
        return send(webinar, INVITE)


@shared_task
def remind_webinars() -> int:
    """Przypomnienia ``WEBINAR_REMINDER_MINUTES`` przed startem – raz na webinar (``reminder_sent_at``).

    Zajęcie webinaru jest warunkowym ``UPDATE`` (``reminder_sent_at IS NULL``): beat po restarcie
    potrafi puścić zadanie dwa razy pod rząd, a drugi przebieg nie może wysłać drugiego kompletu.
    """
    from apps.competitions.scoping import each_competition

    from .models import Webinar
    from .notifications import REMINDER, send
    from .services import available, purge_webhook_events

    now = timezone.now()
    purge_webhook_events(now)
    horizon = now + timedelta(minutes=max(1, int(settings.WEBINAR_REMINDER_MINUTES)))
    total = 0
    for competition in each_competition():
        if not available(competition):
            continue
        due = Webinar.objects.for_competition(competition).filter(
            email_reminder=True,
            reminder_sent_at__isnull=True,
            cancelled_at__isnull=True,
            ended_at__isnull=True,
            starts_at__gt=now,
            starts_at__lte=horizon,
        )
        for webinar in due.select_related("competition"):
            claimed = Webinar.objects.filter(pk=webinar.pk, reminder_sent_at__isnull=True).update(
                reminder_sent_at=now
            )
            if claimed:
                total += send(webinar, REMINDER)
    return total
