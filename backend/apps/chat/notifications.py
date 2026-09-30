"""List „masz nową wiadomość” – kiedy wychodzi, do kogo i czego **nigdy** nie niesie.

Wiadomości żyją na platformie; poczta jest wyłącznie sygnałem, że coś czeka. Stąd trzy reguły:

- **bez treści.** List mówi, **od kogo** (podpis „Imię N.” albo „Organizator”) i **gdzie**
  (odnośnik do wątku), a nie **co**. Rozmowa za logowaniem jest głównym zabezpieczeniem
  wypowiedzi osób niepełnoletnich; list przekazany dalej albo czytany na wspólnym komputerze
  byłby drogą obok niego – ta sama decyzja, co w ``apps.forum.notifications``,
- **tylko po doręczeniu.** Wiadomość czekająca na premoderację nie wysyła niczego; list idzie
  w chwili akceptacji. Odbiorca nie może dostać listu o wiadomości, której po kliknięciu nie zobaczy,
- **zbijanie.** O jednej rozmowie do jednego odbiorcy wychodzi najwyżej jeden list na
  ``NOTIFY_INTERVAL_HOURS`` godzin – i następny tylko wtedy, gdy odbiorca **otworzył wątek** po
  poprzednim liście (:func:`is_due`). Kto nie zajrzał po pierwszym liście, wie już, że coś czeka;
  drugi i trzeci list o tej samej, wciąż nieotwartej rozmowie byłyby tym, co odbiorca zgłasza
  potem jako spam. Stan wysyłki to dwa znaczniki obok siebie: ``notified_at`` i ``last_read_at``
  (po stronie organizatora – ``organizer_notified_at`` i ``organizer_last_read_at``).

Adresaci: konto aktywne, z potwierdzonym adresem, niezanonimizowane (``DELIVERABLE``) i bez
wyłączonych listów (:class:`~apps.chat.models.ChatNotificationSettings`; brak wiersza = włączone).
Wiadomość od uczestnika do organizatora idzie do **każdego** koordynatora konkursu wg jego
własnego ustawienia; rozmowa jest wspólna, więc i zbijanie jest wspólne (jeden znacznik na rozmowę).
Nadawca nie dostaje listu o własnej wiadomości.

Podstawa prawna jest ta sama, co przy powiadomieniach forum: kontakt w ramach usługi, z której
odbiorca korzysta (art. 6 ust. 1 lit. f RODO), z wyłączeniem jednym przełącznikiem na ekranie
„Edycja danych” – odnośnik do niego stoi w każdym liście.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .models import (
    NOTIFY_INTERVAL_HOURS,
    ChatNotificationSettings,
    ConversationMember,
    SenderRole,
)

logger = logging.getLogger(__name__)

#: Temat listu. Wzorzec z nazwą konkursu idzie przez ``apps.tenancy.branding.subject`` – przy
#: wyłączonej marce konkursu zostaje literał z nazwą instalacji, jak w pozostałych listach.
SUBJECT = gettext_lazy("Nowa wiadomość – Olimpiada Kwantowa")
SUBJECT_TEMPLATE = gettext_lazy("Nowa wiadomość – %(competition)s")

#: Kotwica sekcji ustawień na ekranie „Edycja danych”.
SETTINGS_ANCHOR = "#wiadomosci"


# --- ustawienia konta ------------------------------------------------------------------------------


def preferences_for(user) -> ChatNotificationSettings:
    """Ustawienie konta albo niezapisany obiekt domyślny (listy włączone)."""
    identified = user is not None and getattr(user, "is_authenticated", False)
    row = ChatNotificationSettings.objects.filter(user=user).first() if identified else None
    return row if row is not None else ChatNotificationSettings(user=user if identified else None)


def save_preferences(user, *, email_on_message: bool) -> ChatNotificationSettings:
    row, _created = ChatNotificationSettings.objects.update_or_create(
        user=user, defaults={"email_on_message": bool(email_on_message)}
    )
    return row


# --- reguła zbijania -------------------------------------------------------------------------------


def is_due(notified_at, last_read_at, now) -> bool:
    """Czy wolno wysłać list o tej rozmowie do tej strony – reguła opisana w docstringu modułu."""
    if notified_at is None:
        return True
    if now - notified_at < timedelta(hours=NOTIFY_INTERVAL_HOURS):
        return False
    return last_read_at is not None and last_read_at >= notified_at


# --- wysyłka ---------------------------------------------------------------------------------------


def _deliverable(user_ids):
    from apps.accounts.messaging import DELIVERABLE
    from apps.accounts.models import User

    return (
        User.objects.filter(DELIVERABLE, pk__in=user_ids)
        .exclude_anonymised()
        .exclude(chat_notification_settings__email_on_message=False)
        .select_related("preference")
        .order_by("pk")
    )


def _sender_label(message) -> str:
    from apps.forum.models import display_author

    if message.sender_role == SenderRole.ORGANIZER:
        return _("Organizator")
    return display_author(message.sender)


def _send(user, competition, message, *, thread_path: str, settings_path: str) -> None:
    from apps.accounts.activation import absolute_url, queue_mail, signature_lines
    from apps.accounts.preferences import language_for
    from apps.tenancy import branding

    with language_for(user, competition):
        lines = [
            _("Masz nową wiadomość od %(sender)s na platformie %(competition)s.")
            % {"sender": _sender_label(message), "competition": branding.competition_name(competition)},
            "",
            _("Przeczytaj: %(link)s") % {"link": absolute_url(thread_path, competition=competition)},
            "",
            _("Treści wiadomości nie przesyłamy pocztą – przeczytasz ją po zalogowaniu."),
            _("Ustawienia powiadomień: %(link)s")
            % {"link": absolute_url(settings_path + SETTINGS_ANCHOR, competition=competition)},
            "",
            *signature_lines(competition),
        ]
        subject = branding.subject(SUBJECT_TEMPLATE, SUBJECT, competition)
        body = "\n".join(lines)
    queue_mail(subject, body, user.email, competition=competition)


def message_delivered(message, now) -> int:
    """Listy po doręczeniu ``message``. Zwraca liczbę zakolejkowanych listów."""
    conversation = message.conversation
    competition = conversation.competition
    if conversation.is_organizer and message.sender_role == SenderRole.PARTICIPANT:
        return _to_organizer(message, conversation, competition, now)
    return _to_participants(message, conversation, competition, now)


def _to_participants(message, conversation, competition, now) -> int:
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    members = ConversationMember.objects.filter(conversation=conversation).select_related("participant__user")
    if message.sender_id is not None:
        members = members.exclude(participant__user_id=message.sender_id)
    sent = 0
    for member in members:
        if not is_due(member.notified_at, member.last_read_at, now):
            continue
        user = _deliverable([member.participant.user_id]).first()
        if user is None or not has_role(user, competition, CompetitionRole.PARTICIPANT):
            continue
        _send(
            user,
            competition,
            message,
            thread_path=reverse("web:chat-thread", args=[conversation.pk]),
            settings_path=reverse("web:profile"),
        )
        ConversationMember.objects.filter(pk=member.pk).update(notified_at=now)
        sent += 1
    return sent


def _to_organizer(message, conversation, competition, now) -> int:
    from apps.accounts.messaging import _role_filter
    from apps.accounts.models import CompetitionRole, User

    from .models import Conversation

    if not is_due(conversation.organizer_notified_at, conversation.organizer_last_read_at, now):
        return 0
    coordinators = User.objects.filter(_role_filter(competition, CompetitionRole.COORDINATOR)).values("pk")
    recipients = _deliverable(coordinators)
    if message.sender_id is not None:
        recipients = recipients.exclude(pk=message.sender_id)
    sent = 0
    for user in recipients.distinct():
        _send(
            user,
            competition,
            message,
            thread_path=reverse("web:coordinator-chat-thread", args=[conversation.pk]),
            settings_path=reverse("web:account-profile"),
        )
        sent += 1
    if sent:
        Conversation.objects.filter(pk=conversation.pk).update(organizer_notified_at=now)
        conversation.organizer_notified_at = now
    return sent
