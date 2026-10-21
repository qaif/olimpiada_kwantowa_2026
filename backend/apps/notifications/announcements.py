"""Rozesłanie ogłoszenia organizatora (CZ-ANN-01) jako powiadomienia (UX-PANEL-01 § 2 D7).

Dwie drogi do tej samej funkcji :func:`deliver`:

- **od razu** po publikacji – hak ``hooks.announcement_published`` kolejkuje zadanie po commicie,
- **przebiegiem beat** (``deliver_due`` co 10 minut) – ogłoszenie zaplanowane („widoczne od”) wchodzi
  w swoje okno bez niczyjego kliknięcia, a zadanie z ``eta`` na kilka dni naprzód gubiłoby się przy
  restarcie brokera. Ten sam przebieg łata też publikację, której zadanie przepadło (broker wyłączony).

Jedna publikacja = jedno rozesłanie: znacznik :class:`AnnouncementDelivery` po (ogłoszenie,
``published_at``) z więzem unikalności. Ponowne „Opublikuj” przesuwa ``published_at`` i jest dla
uczestnika nową informacją – wtedy wychodzi drugi raz (nieprzeczytane powiadomienie zostaje podbite).

Odbiorcy: uczestnicy konkursu ogłoszenia **w chwili rozesłania**. Kto zarejestruje się później,
widzi ogłoszenie w bloku na pulpicie (reguła CZ-ANN-01), bez powiadomienia o nim.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import services
from .kinds import NotificationKind
from .models import AnnouncementDelivery

#: Kotwica bloku „Ogłoszenia organizatora” na pulpicie (``web/chat/_announcements.html``).
ANCHOR = "#ogloszenia-organizatora"


def deliver(announcement, now=None) -> int:
    """Rozsyła ogłoszenie, jeśli jest teraz widoczne i ta publikacja jeszcze nie wyszła. Zwraca odbiorców."""
    from apps.chat.announcements import visible_q

    now = now or timezone.now()
    if announcement.published_at is None:
        return 0
    visible = type(announcement).objects.filter(pk=announcement.pk).filter(visible_q(now)).exists()
    if not visible:
        return 0
    try:
        with transaction.atomic():
            marker = AnnouncementDelivery.objects.create(
                announcement=announcement, published_at=announcement.published_at, delivered_at=now
            )
    except IntegrityError:
        return 0
    recipients = services.notify_many(
        services.participant_user_ids(announcement.competition_id),
        announcement.competition_id,
        NotificationKind.ANNOUNCEMENT,
        "web:me",
        suffix=ANCHOR,
        params={"title": announcement.title},
        key=f"announcement:{announcement.pk}",
        now=now,
    )
    AnnouncementDelivery.objects.filter(pk=marker.pk).update(recipients=recipients)
    return recipients


def deliver_due(now=None) -> int:
    """Wszystkie ogłoszenia widoczne teraz, których bieżąca publikacja nie została rozesłana."""
    from apps.chat.announcements import visible_q
    from apps.chat.models import OrganizerAnnouncement

    now = now or timezone.now()
    total = 0
    for announcement in OrganizerAnnouncement.objects.filter(visible_q(now)).exclude(
        published_at__isnull=True
    ):
        if AnnouncementDelivery.objects.filter(
            announcement=announcement, published_at=announcement.published_at
        ).exists():
            continue
        total += deliver(announcement, now)
    return total
