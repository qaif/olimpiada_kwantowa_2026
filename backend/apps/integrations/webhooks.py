"""Wychodzące powiadomienia o zdarzeniach edycji: emisja, podpis i stan doręczenia.

Emisja jest celowo **cicha wobec domeny**. ``emit()`` woła się z serwisu domenowego jedną linijką
i nie ma prawa niczego przewrócić: publikacja wyników nie może się nie udać dlatego, że serwer
partnera nie odpowiada. Dlatego kolejność jest zawsze ta sama:

1. serwis domenowy robi swoje w transakcji i woła ``emit()``,
2. ``emit()`` zapisuje wiersze ``WebhookDelivery`` **w tej samej transakcji** – wycofana zmiana
   nie zostawia zapowiedzi zdarzenia, które nie zaszło,
3. dopiero po zatwierdzeniu (``transaction.on_commit``) doręczenia idą na kolejkę. Worker nie
   może zobaczyć identyfikatora, którego jeszcze nie ma w bazie.

Podpis (``X-Olimpiada-Signature: t=<ts>,v1=<hex>``) obejmuje **znacznik czasu i ciało naraz**,
a nie samo ciało. Sam podpis ciała dałoby się nagrać i odtworzyć w dowolnej chwili; ze znacznikiem
odbiorca odrzuca powtórkę starszą niż jego własne okno tolerancji. Wzorzec jest ten sam, co
u dużych dostawców webhooków – i to też jest argument: odbiorca ma już gotowy kod do weryfikacji.

Czego w ładunku **nie ma**: imion, nazwisk, adresów e-mail, treści prac ani punktów konkretnej
osoby. Idą wyłącznie identyfikatory i kody publiczne. Webhook leci na cudzy serwer po zdarzeniu,
którego nikt po naszej stronie w tej chwili nie ogląda – to najgorsze możliwe miejsce na dane
osobowe. Kto ma prawo do szczegółów, dopyta o nie API kluczem z odpowiednim zakresem.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    MAX_CONSECUTIVE_FAILURES,
    WEBHOOK_EVENTS,
    DeliveryStatus,
    WebhookDelivery,
    WebhookEndpoint,
)

logger = logging.getLogger(__name__)

#: Nagłówki doręczenia. Zdarzenie i identyfikator doręczenia są osobno od ciała, żeby odbiorca
#: mógł odrzucić albo skierować dalej wiadomość bez parsowania JSON-a.
SIGNATURE_HEADER = "X-Olimpiada-Signature"
EVENT_HEADER = "X-Olimpiada-Event"
DELIVERY_HEADER = "X-Olimpiada-Delivery"

#: Limit czasu jednego doręczenia (sekundy). Dziesięć sekund to i tak dużo jak na przyjęcie
#: powiadomienia: odbiorca ma je zakolejkować i odpowiedzieć, a nie przetworzyć na miejscu.
TIMEOUT_SECONDS = 10

#: Ile razy w sumie próbujemy doręczyć jedno zdarzenie (pierwsza próba + cztery ponowienia).
MAX_ATTEMPTS = 5

#: Podstawa odstępu między próbami (sekundy) i jego górna granica. Odstęp rośnie wykładniczo:
#: 60 s, 2 min, 4 min, 8 min. Awaria trwająca krócej niż kwadrans zostaje więc nadrobiona sama.
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600

#: Kody odpowiedzi uznawane za przyjęcie. Całe 2xx, bo odbiorcy odpowiadają i 200, i 202,
#: i 204 – a każda z tych odpowiedzi znaczy „mam”.
SUCCESS_RANGE = range(200, 300)


def canonical_body(envelope: dict) -> bytes:
    """Ciało żądania w postaci, którą podpisujemy – i którą odbiorca musi zobaczyć co do bajtu.

    ``sort_keys`` i brak spacji, bo podpis obejmuje **bajty**: gdyby serializacja zależała od
    kolejności kluczy w słowniku, ten sam ładunek podpisany dwa razy dawałby dwa różne podpisy,
    a ponowienie doręczenia przestałoby się weryfikować. ``ensure_ascii=False`` zostawia polskie
    znaki w UTF-8, czyli w postaci, w której są w bazie.
    """
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signature_header(secret: str, body: bytes, timestamp: int) -> str:
    """Wartość nagłówka ``X-Olimpiada-Signature`` dla danego ciała i znacznika czasu."""
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def build_envelope(delivery: WebhookDelivery) -> dict:
    """Koperta zdarzenia: identyfikator doręczenia, nazwa zdarzenia, czas i dane.

    Budowana z wiersza, a nie zapamiętana w nim, ale **wyłącznie z pól niezmiennych** (id, nazwa,
    czas utworzenia, zapisany ładunek). Dzięki temu ponowienie wysyła dokładnie tę samą treść,
    co próba pierwsza – odbiorca rozpoznaje duplikat po ``id`` i może go pominąć.
    """
    return {
        "id": delivery.pk,
        "event": delivery.event,
        "created_at": delivery.created_at.isoformat(),
        "data": delivery.payload or {},
    }


def endpoints_for(event: str, edition_id: int | None):
    """Aktywni odbiorcy zapisani na dane zdarzenie w danej edycji.

    Odbiorca bez edycji dostaje zdarzenia ze **wszystkich** edycji – tak wygląda integracja
    stała (system partnera, który ma po prostu wiedzieć, co się dzieje). Odbiorca z edycją
    dostaje wyłącznie swoją, także wtedy, gdy zdarzenie nie ma edycji w ogóle.
    """
    queryset = WebhookEndpoint.objects.filter(is_active=True, edition__isnull=True)
    if edition_id is not None:
        queryset = WebhookEndpoint.objects.filter(is_active=True).filter(
            Q(edition__isnull=True) | Q(edition_id=edition_id)
        )
    # Filtr po zdarzeniu jest w Pythonie, a nie w zapytaniu: ``events`` jest listą w JSON-ie,
    # a zapytanie „czy lista zawiera wartość” wyglądałoby inaczej w każdej bazie. Odbiorców są
    # jednostki, nie tysiące – to jedno zapytanie i pętla po kilku wierszach.
    return [endpoint for endpoint in queryset.order_by("id") if event in (endpoint.events or [])]


def emit(event: str, payload: dict, edition=None) -> list[int]:
    """Zgłasza zdarzenie domenowe odbiorcom webhooków. Zwraca identyfikatory doręczeń.

    Wołane z serwisów domenowych **wewnątrz** ich transakcji. Nieznane zdarzenie jest błędem
    programisty i wychodzi wyjątkiem od razu – lista zdarzeń jest częścią kontraktu z odbiorcami,
    więc literówka nie ma prawa przejść jako „zdarzenie, na które nikt nie jest zapisany”.

    Brak odbiorców to normalny stan (tak wygląda serwis bez ani jednej integracji), więc nie ma
    tu żadnego ostrzeżenia ani śladu – po prostu nie powstaje nic.
    """
    if event not in WEBHOOK_EVENTS:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Nieznane zdarzenie webhooka: {event}.")
    # ``edition`` bywa obiektem (serwis ma go pod ręką) albo samym identyfikatorem (serwis ma
    # wyłącznie ``stage.edition_id`` i nie ma powodu dobierać wiersza tylko po to, żeby go tu oddać).
    edition_id = edition.pk if hasattr(edition, "pk") else edition
    targets = endpoints_for(event, edition_id)
    if not targets:
        return []
    deliveries = WebhookDelivery.objects.bulk_create(
        [
            WebhookDelivery(endpoint=endpoint, event=event, payload=payload, status=DeliveryStatus.PENDING)
            for endpoint in targets
        ]
    )
    delivery_ids = [delivery.pk for delivery in deliveries]

    def _enqueue(ids: list[int] = delivery_ids) -> None:
        from .tasks import deliver_webhook

        for delivery_id in ids:
            deliver_webhook.delay(delivery_id)

    transaction.on_commit(_enqueue)
    logger.info("Zdarzenie %s: zakolejkowano %s doręczeń.", event, len(delivery_ids))
    return delivery_ids


def post_payload(endpoint: WebhookEndpoint, envelope: dict) -> tuple[bool, str]:
    """Jedno żądanie HTTP. Zwraca parę (udało się, opis błędu).

    Wyjątek biblioteki **nie leci** wyżej: doręczenie jest stanem w bazie, a nie wyjątkiem
    w workerze, i o ponowieniu decyduje zadanie, patrząc na liczbę prób. Treść odpowiedzi jest
    pomijana – webhook to powiadomienie, a nie zapytanie.
    """
    import requests

    body = canonical_body(envelope)
    timestamp = int(timezone.now().timestamp())
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Olimpiada-Kwantowa-Webhook/1",
        EVENT_HEADER: envelope["event"],
        DELIVERY_HEADER: str(envelope["id"]),
        SIGNATURE_HEADER: signature_header(endpoint.secret, body, timestamp),
    }
    try:
        response = requests.post(endpoint.url, data=body, headers=headers, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]
    if response.status_code in SUCCESS_RANGE:
        return True, ""
    return False, f"HTTP {response.status_code}"


def record_success(delivery: WebhookDelivery) -> None:
    """Doręczone: wiersz dostaje datę, a odbiorca zerowany licznik porażek."""
    now = timezone.now()
    delivery.status = DeliveryStatus.DELIVERED
    delivery.delivered_at = now
    delivery.last_error = ""
    delivery.save(update_fields=["status", "delivered_at", "last_error", "attempts"])
    WebhookEndpoint.objects.filter(pk=delivery.endpoint_id).update(failures=0)


def record_failure(delivery: WebhookDelivery, error: str, *, final: bool) -> None:
    """Nieudana próba. Dopiero ostatnia zamyka doręczenie i obciąża licznik odbiorcy.

    Licznik porażek odbiorcy rośnie **o jedno doręczenie**, a nie o każdą próbę: dwadzieścia
    porażek ma znaczyć „dwadzieścia zdarzeń przepadło”, a nie „cztery zdarzenia po pięć prób”.
    """
    delivery.last_error = error[:300]
    delivery.status = DeliveryStatus.FAILED if final else DeliveryStatus.PENDING
    delivery.save(update_fields=["status", "last_error", "attempts"])
    if not final:
        return
    # Blokada wiersza odbiorcy: doręczenia jednego zdarzenia idą równolegle na kilku workerach,
    # a bez niej dwie porażki naraz policzyłyby się jako jedna i próg wygaszenia przesuwałby się
    # w losową stronę.
    with transaction.atomic():
        endpoint = WebhookEndpoint.objects.select_for_update().get(pk=delivery.endpoint_id)
        endpoint.failures += 1
        fields = ["failures"]
        if endpoint.failures >= MAX_CONSECUTIVE_FAILURES and endpoint.is_active:
            # Wygaszenie, a nie kasowanie: koordynator ma zobaczyć, że odbiorca przestał
            # odpowiadać, poprawić adres i włączyć go z powrotem – razem z historią, która
            # to wyjaśnia.
            endpoint.is_active = False
            endpoint.disabled_at = timezone.now()
            fields += ["is_active", "disabled_at"]
            logger.warning(
                "Odbiorca webhooków %s wygaszony po %s nieudanych doręczeniach pod rząd.",
                endpoint.pk,
                endpoint.failures,
            )
        endpoint.save(update_fields=fields)


def attempt_delivery(delivery: WebhookDelivery) -> bool:
    """Jedna próba doręczenia wraz z zapisem skutku. ``True`` = odbiorca potwierdził przyjęcie.

    Zwiększenie licznika prób jest **przed** żądaniem: proces zabity w trakcie połączenia ma
    zostawić ślad próby, a nie wyglądać, jakby nigdy jej nie było.
    """
    delivery.attempts += 1
    delivery.save(update_fields=["attempts"])
    ok, error = post_payload(delivery.endpoint, build_envelope(delivery))
    if ok:
        record_success(delivery)
        return True
    final = delivery.attempts >= MAX_ATTEMPTS
    record_failure(delivery, error, final=final)
    return False


def retry_countdown(attempts: int) -> int:
    """Odstęp przed kolejną próbą: wykładniczy, przycięty do ``RETRY_MAX_SECONDS``."""
    return min(RETRY_BASE_SECONDS * (2 ** max(attempts - 1, 0)), RETRY_MAX_SECONDS)
