"""Zadania okresowe forum: przebiegi wysyłki powiadomień e-mail.

Dwa zadania, bo są dwie pory: **częsta** (co dwie minuty – list o kolejce moderacji i listy „na
bieżąco”) i **dzienna** (podsumowanie dla kont „raz dziennie”). Reguły – kto, kiedy, co wolno
napisać – stoją w ``apps.forum.notifications``; tutaj jest wyłącznie obejście konkursów, każdy
w jego własnym kontekście (``each_competition``), żeby odnośnik w liście prowadził pod domenę
tego forum, o którym jest list.

Dlaczego przebieg okresowy, a nie list wysłany z żądania, które opublikowało wpis: limit „jeden
list o wątku na N godzin” i opóźnienie listu do koordynatora **są** czasem. List wysłany od razu
musiałby i tak zostawić ślad „resztę wyślij później” – a wtedy przebieg okresowy jest potrzebny
tak czy inaczej, a list z żądania byłby drugą drogą do tego samego.

Zadania nie mają ponowień: następny przebieg przyjdzie za dwie minuty (albo jutro) i zastanie
ten sam stan. Pojedynczy list ponawia ``send_mail_task`` na kolejce ``mail``.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="apps.forum.tasks.send_forum_notifications")
def send_forum_notifications() -> dict[str, int]:
    """Przebieg częsty: listy o kolejce moderacji i listy „na bieżąco”. Zwraca liczby listów."""
    from apps.competitions.scoping import each_competition

    from .notifications import send_member_notifications, send_moderation_digest

    now = timezone.now()
    moderation = members = 0
    for competition in each_competition():
        if competition is None:
            continue
        moderation += send_moderation_digest(competition, now)
        members += send_member_notifications(competition, now)
    if moderation or members:
        logger.info("Forum: listy o kolejce: %s, listy do uczestników: %s.", moderation, members)
    return {"moderation": moderation, "members": members}


@shared_task(name="apps.forum.tasks.send_daily_forum_digest")
def send_daily_forum_digest() -> dict[str, int]:
    """Przebieg dzienny: jedno podsumowanie dla każdego konta „raz dziennie”, które ma co czytać."""
    from apps.competitions.scoping import each_competition

    from .notifications import send_member_notifications

    now = timezone.now()
    members = 0
    for competition in each_competition():
        if competition is None:
            continue
        members += send_member_notifications(competition, now, daily=True)
    if members:
        logger.info("Forum: podsumowania dzienne: %s.", members)
    return {"members": members}
