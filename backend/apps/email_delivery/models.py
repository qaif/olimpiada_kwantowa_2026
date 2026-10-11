"""Stan doręczalności adresu e-mail (MAIL-02 § 2.3).

Klucz to **adres**, a nie konto: odbija także adres rodzica, zaproszenia czy ucznia, którego konto
dopiero powstanie. Z kontem wiersz łączy równość adresu (``accounts.User.email`` jest unikalny bez
względu na wielkość liter i zapisywany małymi literami), więc „``email_undeliverable_at`` konta” to
``DeliveryStatus.undeliverable_at`` jego adresu – bez kolumny w modelu wspólnym ``accounts.User``.

Wiersz nie jest dziennikiem: trzyma pierwsze twarde odbicie, ostatni powód i liczniki. Pełna treść
zawiadomienia (z kopią wysłanego listu – także z linkiem aktywacyjnym) nie jest nigdzie zapisywana.
"""

from __future__ import annotations

from django.db import models


class Source(models.TextChoices):
    #: Relay odmówił adresu w trakcie wysyłki (``SMTPRecipientsRefused`` w backendzie).
    SMTP = "smtp", "odmowa relaya"
    #: Zawiadomienie o niedoręczeniu (DSN) po przyjęciu listu przez relay.
    DSN = "dsn", "zawiadomienie o niedoręczeniu"


class DeliveryStatus(models.Model):
    email = models.CharField("adres e-mail (małe litery)", max_length=254, unique=True)
    undeliverable_at = models.DateTimeField("twarde odbicie od", null=True, blank=True, db_index=True)
    reason = models.CharField("powód (diagnoza serwera)", max_length=500, blank=True)
    status_code = models.CharField("kod stanu (RFC 3463)", max_length=16, blank=True)
    source = models.CharField("źródło", max_length=8, choices=Source.choices, blank=True)
    hard_bounces = models.PositiveIntegerField("twarde odbicia", default=0)
    soft_bounces = models.PositiveIntegerField("miękkie odbicia", default=0)
    last_soft_bounce_at = models.DateTimeField("ostatnie miękkie odbicie", null=True, blank=True)
    last_soft_reason = models.CharField("powód ostatniego miękkiego odbicia", max_length=500, blank=True)
    created_at = models.DateTimeField("utworzono", auto_now_add=True)
    updated_at = models.DateTimeField("ostatnie zdarzenie", auto_now=True, db_index=True)

    class Meta:
        verbose_name = "stan doręczalności adresu"
        verbose_name_plural = "stany doręczalności adresów"
        ordering = ("-undeliverable_at", "email")

    def __str__(self) -> str:
        return self.email

    @property
    def undeliverable(self) -> bool:
        return self.undeliverable_at is not None
