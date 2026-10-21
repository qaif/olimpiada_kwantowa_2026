"""Haki źródeł powiadomień – wołane jedną linijką z miejsc, które już wysyłają list albo zapisują zdarzenie.

Każdy hak:

- **nigdy nie przerywa źródła.** Wiadomość ma dojść, decyzja koordynatora ma się zapisać, nawet gdy
  powiadomienie się nie uda. Stąd :func:`_safely`: punkt zapisu (``transaction.atomic``) wokół haka
  i wpis w logu zamiast wyjątku – błąd w środku transakcji wołającego cofa wtedy wyłącznie punkt
  zapisu, a nie wiadomość,
- **biegnie w transakcji źródła**, więc wycofana decyzja nie zostawia powiadomienia o niej (ta sama
  zasada, co ``queue_mail`` po commicie),
- zapisuje wyłącznie dla uczestników tego konkursu (D9) i nie kopiuje treści (``kinds``).

Rozsyłanie ogłoszenia jest wyjątkiem od „w transakcji źródła”: odbiorców bywają tysiące, więc idzie
zadaniem po commicie (``tasks.deliver_announcement``), a ogłoszenie zaplanowane – przebiegiem beat.
"""

from __future__ import annotations

import functools
import logging

from django.db import transaction
from django.utils import timezone

from . import services
from .kinds import NotificationKind

logger = logging.getLogger(__name__)


def _safely(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            with transaction.atomic():
                return func(*args, **kwargs)
        except Exception:  # noqa: BLE001 - powiadomienie nie może wywrócić źródła
            logger.exception("Powiadomienie w panelu nie powstało (%s).", func.__name__)
            return None

    return wrapper


def chat_key(conversation_id: int) -> str:
    return f"chat:{conversation_id}"


def forum_thread_key(thread_id: int) -> str:
    return f"forum-thread:{thread_id}"


# --- Wiadomości (apps.chat.services) -------------------------------------------------------------------


@_safely
def chat_message_delivered(message) -> int:
    """Doręczona wiadomość: odbiorcy-uczestnicy dostają (albo podbijają) powiadomienie o rozmowie.

    Wiadomość uczestnika do organizatora nie daje powiadomienia nikomu – koordynatorzy mają odznakę
    w menu swojego panelu. Nadawca nie dostaje powiadomienia o własnej wiadomości.
    """
    from apps.chat.models import ConversationMember, SenderRole

    conversation = message.conversation
    if conversation.is_organizer and message.sender_role == SenderRole.PARTICIPANT:
        return 0
    members = ConversationMember.objects.filter(conversation_id=conversation.pk)
    if message.sender_id is not None:
        members = members.exclude(participant__user_id=message.sender_id)
    kind = NotificationKind.CHAT_ORGANIZER if conversation.is_organizer else NotificationKind.CHAT_PEER
    return services.notify_many(
        members.values_list("participant__user_id", flat=True),
        conversation.competition_id,
        kind,
        "web:chat-thread",
        args=[conversation.pk],
        key=chat_key(conversation.pk),
    )


@_safely
def chat_thread_read(member) -> int:
    """Otwarty wątek gasi powiadomienie o tej rozmowie – licznik dzwonka nie kłamie po przeczytaniu."""
    from apps.accounts.models import Participant
    from apps.notifications.models import Notification

    user_id = Participant.objects.filter(pk=member.participant_id).values("user_id")[:1]
    return Notification.objects.filter(
        user_id__in=user_id, dedupe_key=chat_key(member.conversation_id), read_at__isnull=True
    ).update(read_at=timezone.now())


# --- forum (apps.forum.notifications) -----------------------------------------------------------------


@_safely
def forum_post_published(post) -> int:
    """Opublikowana odpowiedź: obserwujący wątek uczestnicy (poza autorem) – jedno powiadomienie na wątek."""
    from apps.forum.models import ForumSubscription

    thread = post.thread
    subscribers = ForumSubscription.objects.filter(thread_id=thread.pk, is_active=True)
    if post.author_id is not None:
        subscribers = subscribers.exclude(user_id=post.author_id)
    user_ids = services.participant_user_ids(
        thread.competition_id, subscribers.values_list("user_id", flat=True)
    )
    return services.notify_many(
        user_ids,
        thread.competition_id,
        NotificationKind.FORUM_REPLY,
        "web:forum-thread",
        args=[thread.pk],
        key=forum_thread_key(thread.pk),
    )


@_safely
def forum_decision(*, author_id: int, thread) -> int:
    """Decyzja moderatora o wpisie albo wątku autora. Odnośnik prowadzi na forum (wątek bywa odrzucony)."""
    user_ids = services.participant_user_ids(thread.competition_id, [author_id])
    return services.notify_many(user_ids, thread.competition_id, NotificationKind.FORUM_DECISION, "web:forum")


# --- zaświadczenie o statusie ucznia (apps.student_status.notifications) -------------------------------


@_safely
def student_status_decided(certificate, *, accepted: bool) -> int:
    participant = certificate.participant
    kind = NotificationKind.STUDENT_STATUS_ACCEPTED if accepted else NotificationKind.STUDENT_STATUS_REJECTED
    return services.notify_many(
        [participant.user_id],
        participant.competition_id,
        kind,
        "web:student-status",
        key=f"student-status:{certificate.edition_id}",
    )


# --- wyniki i reklamacje (apps.submissions.notifications) -----------------------------------------------


@_safely
def results_published(publication) -> int:
    """Ogłoszone wyniki etapu: każdy uczestnik etapu – stała liczba zapytań (``bulk_create``)."""
    from apps.competitions.models import StageEntry

    stage = publication.stage
    competition_id = stage.edition.competition_id
    user_ids = services.participant_user_ids(
        competition_id,
        StageEntry.objects.filter(stage_id=stage.pk).values_list("participant__user_id", flat=True),
    )
    return services.notify_many(
        user_ids,
        competition_id,
        NotificationKind.RESULTS_PUBLISHED,
        "web:me",
        suffix="?tab=wyniki#sekcja",
        params={"stage": stage.display_name},
        key=f"results:{stage.pk}",
    )


@_safely
def appeal_decided(appeal) -> int:
    participant = appeal.filed_by
    return services.notify_many(
        [participant.user_id],
        participant.competition_id,
        NotificationKind.APPEAL_DECIDED,
        "web:me",
        suffix="?tab=reklamacje#sekcja",
        key=f"appeal:{appeal.pk}",
    )


# --- ogłoszenia organizatora (apps.chat.announcements) -------------------------------------------------


def announcement_published(announcement) -> None:
    """Po commicie publikacji: rozesłanie zadaniem (odbiorców są tysiące). Zaplanowane – przebieg beat."""
    from .tasks import deliver_announcement

    announcement_id = announcement.pk

    def _enqueue() -> None:
        try:
            deliver_announcement.delay(announcement_id)
        except Exception:  # noqa: BLE001 - brak brokera nie może wywrócić publikacji
            logger.exception("Nie udało się zakolejkować rozesłania ogłoszenia %s.", announcement_id)

    transaction.on_commit(_enqueue)
