"""Zadanie Celery doręczające jeden webhook. Kolejka domyślna, ponowienia z narastającym odstępem.

Dlaczego to w ogóle jest zadanie, a nie żądanie HTTP puszczone wprost z serwisu domenowego:
bo po drugiej stronie stoi cudzy serwer. Dziesięć sekund limitu razy pięciu odbiorców to prawie
minuta doliczona do „Opublikuj wyniki”, a awaria partnera zamieniłaby przycisk koordynatora
w błąd. Na kolejce to samo kosztuje jeden ``INSERT`` i jedno ``delay()``.
"""

from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from .models import DeliveryStatus, WebhookDelivery
from .webhooks import MAX_ATTEMPTS, attempt_delivery, retry_countdown

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=MAX_ATTEMPTS - 1)
def deliver_webhook(self, delivery_id: int) -> str:
    """Doręcza jedno zdarzenie. Porażka wraca na kolejkę, aż do wyczerpania prób.

    Zadanie **nie rzuca** wyjątkiem przy nieudanym doręczeniu: skutek jest zapisany w wierszu
    ``WebhookDelivery``, a niepowodzenie doręczenia webhooka nie jest awarią systemu, tylko jego
    normalnym stanem. Wyjątek zaśmiecałby monitoring błędów kolejki zdarzeniami, na które
    odpowiedzią jest poprawienie adresu przez koordynatora, a nie interwencja w serwisie.

    W trybie ``task_always_eager`` (testy) ponowienia nie ma: nie ma kolejki, więc ``retry``
    wywołałoby to samo zadanie rekurencyjnie wewnątrz żądania, które właśnie publikuje wyniki.
    Przedmiotem testu jest wtedy pierwsza próba i jej zapis – ponowienia sprawdza test zadania.
    """
    delivery = WebhookDelivery.objects.select_related("endpoint").filter(pk=delivery_id).first()
    if delivery is None:
        logger.warning("Doręczenie %s już nie istnieje – pomijam.", delivery_id)
        return "MISSING"
    if delivery.status == DeliveryStatus.DELIVERED:
        return DeliveryStatus.DELIVERED
    if attempt_delivery(delivery):
        return DeliveryStatus.DELIVERED
    if delivery.status == DeliveryStatus.FAILED:
        return DeliveryStatus.FAILED
    if getattr(self.request, "is_eager", False):
        return DeliveryStatus.PENDING
    try:
        raise self.retry(countdown=retry_countdown(delivery.attempts))
    except MaxRetriesExceededError:  # pragma: no cover - próby liczy sam wiersz doręczenia
        logger.warning("Doręczenie %s: wyczerpane ponowienia Celery.", delivery_id)
        return DeliveryStatus.FAILED
