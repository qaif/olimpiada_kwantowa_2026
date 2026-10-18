"""Odbiorniki: odbicie listu → komunikat (STATS-02 § 2.3) i reset adresu konta (STATS-02 § 4).

Odbicie przychodzi sygnałem ``apps.email_delivery.signals.bounces_reported`` – ``email_delivery``
nie wie o komunikatach i nie musi: wysyła nagłówki listu pierwotnego, a tutaj czytamy z nich numer.

Zmiana adresu konta i usunięcie konta kasują odbicia starego adresu, z tego samego powodu, co reset
``DeliveryStatus`` (``apps.email_delivery.signals``): lista „odbite adresy” przy komunikacie nie może
pokazywać adresu, którego konto już nie ma. Poprzedni adres zapamiętuje ``email_delivery`` w
``post_init`` (bez zapytania); tu go tylko czytamy. Bez ``EMAIL_BOUNCE_TRACKING`` odbić nie ma skąd
wziąć (DSN i odmowy relaya czyta wyłącznie śledzenie MAIL-02), więc oba odbiorniki kończą się bez
zapytania – zapis konta kosztuje tyle samo, co przed STATS-02.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, pre_save

logger = logging.getLogger(__name__)


def _bounces_reported(sender, bounces=(), headers=None, source="", **kwargs) -> None:
    from .broadcasts import record_bounces

    try:
        record_bounces(bounces, headers, source=source)
    except Exception:  # noqa: BLE001 - raport komunikatu jest dodatkiem do odbicia, nie jego warunkiem
        logger.exception("Nie udało się przypisać odbicia do komunikatu.")


def _email_changed(sender, instance, raw=False, update_fields=None, **kwargs) -> None:
    if raw or instance.pk is None:
        return
    if update_fields is not None and "email" not in update_fields:
        return
    from apps.email_delivery.services import tracking_enabled
    from apps.email_delivery.signals import ORIGINAL

    if not tracking_enabled():
        return
    previous = instance.__dict__.get(ORIGINAL)
    if previous and previous.strip().lower() != (instance.email or "").strip().lower():
        from .broadcasts import forget_address

        forget_address(previous)


def _user_deleted(sender, instance, **kwargs) -> None:
    from apps.email_delivery.services import tracking_enabled

    if tracking_enabled() and "email" in instance.__dict__:
        from .broadcasts import forget_address

        forget_address(instance.email)


def connect() -> None:
    from apps.email_delivery.signals import bounces_reported

    bounces_reported.connect(_bounces_reported, dispatch_uid="coordinator_stats_bounces")
    user_model = get_user_model()
    pre_save.connect(_email_changed, sender=user_model, dispatch_uid="coordinator_stats_email_changed")
    post_delete.connect(_user_deleted, sender=user_model, dispatch_uid="coordinator_stats_user_deleted")
