"""Powiadomienia w panelu uczestnika (UX-PANEL-01 § 2 D4–D10).

Jeden wiersz = jedna rzecz, która czeka na tę osobę w tym konkursie: wiadomość, ogłoszenie,
decyzja. Wiersz nie jest kopią treści – mówi, **co się stało** (rodzaj + kilka parametrów) i **gdzie
to obejrzeć** (nazwa adresu z argumentami). Adres jest nazwą, a nie gotową ścieżką: składa go
``reverse`` w żądaniu oglądającego, więc odnośnik działa także pod prefiksem konkursu (``/iqo/…``)
i po zmianie domeny – ścieżka zapisana w chwili zdarzenia (np. w zadaniu Celery, bez żądania)
prowadziłaby w konkursie z prefiksem pod zły adres.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

from .kinds import NotificationKind

#: Najdłuższy parametr tekstowy (tytuł ogłoszenia ma 200 znaków – ``ANNOUNCEMENT_TITLE_LENGTH``).
PARAM_MAX_LENGTH = 200


class Notification(models.Model):
    """Powiadomienie jednej osoby w jednym konkursie."""

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="konkurs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="panel_notifications",
        verbose_name="konto",
    )
    kind = models.CharField("rodzaj", max_length=32, choices=NotificationKind.choices)
    #: Parametry zdania (``kinds.KINDS``) – wyłącznie napisy jawne dla odbiorcy (tytuł ogłoszenia,
    #: nazwa etapu). Nigdy treść wiadomości, wpisu ani powód odrzucenia.
    params = models.JSONField("parametry", default=dict, blank=True)
    #: Cel odnośnika: nazwa adresu (``web:chat-thread``), argumenty i dopisek (``?tab=wyniki#sekcja``).
    target = models.CharField("adres (nazwa)", max_length=80)
    target_args = models.JSONField("argumenty adresu", default=list, blank=True)
    target_suffix = models.CharField("dopisek adresu", max_length=80, blank=True)
    #: Klucz scalania (``chat:12``, ``forum-thread:5``): kolejne zdarzenie z tym samym kluczem podbija
    #: **nieprzeczytane** powiadomienie zamiast dokładać następne (D6). Puste = bez scalania.
    dedupe_key = models.CharField("klucz scalania", max_length=64, blank=True)
    created_at = models.DateTimeField("utworzono", default=timezone.now)
    read_at = models.DateTimeField("przeczytano", null=True, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "powiadomienie"
        verbose_name_plural = "powiadomienia"
        ordering = ("-created_at", "-pk")
        indexes = [
            # Licznik w menu i lista: zawsze konto + konkurs, nieprzeczytane / od najnowszego.
            models.Index(fields=["user", "competition", "read_at"], name="notif_user_unread"),
            models.Index(fields=["user", "competition", "-created_at"], name="notif_user_recent"),
            # Retencja (zadanie beat) idzie po dacie.
            models.Index(fields=["created_at"], name="notif_created"),
        ]
        constraints = [
            # Jedno **nieprzeczytane** powiadomienie na klucz – podstawa scalania (D6).
            models.UniqueConstraint(
                fields=["user", "competition", "dedupe_key"],
                condition=Q(read_at__isnull=True) & ~Q(dedupe_key=""),
                name="notif_one_unread_per_key",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} → {self.user_id}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class AnnouncementDelivery(models.Model):
    """Znacznik „to opublikowanie ogłoszenia zostało już rozesłane” (D7).

    Para (ogłoszenie, ``published_at``), bo ponowna publikacja przesuwa ``published_at``
    i jest nowym zdarzeniem dla uczestnika, a powtórzony przebieg zadania – nie jest.
    """

    announcement = models.ForeignKey(
        "chat.OrganizerAnnouncement",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="ogłoszenie",
    )
    published_at = models.DateTimeField("opublikowano")
    delivered_at = models.DateTimeField("rozesłano", default=timezone.now)
    recipients = models.PositiveIntegerField("odbiorców", default=0)

    class Meta:
        verbose_name = "rozesłanie ogłoszenia"
        verbose_name_plural = "rozesłania ogłoszeń"
        constraints = [
            models.UniqueConstraint(fields=["announcement", "published_at"], name="notif_announcement_once"),
        ]

    def __str__(self) -> str:
        return f"{self.announcement_id} @ {self.published_at:%Y-%m-%d %H:%M}"
