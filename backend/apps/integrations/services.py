"""Reguły domenowe warstwy integracyjnej: wystawianie kluczy i zarządzanie odbiorcami webhooków.

Podział jak w całym projekcie: reguła mieszka tutaj, widok (panel koordynatora) wyłącznie
orkiestruje, a odmowa wychodzi ``DomainError`` z maszynowym kodem – tym samym dla ekranu HTML
i dla API. Każda zmiana zostawia wpis audytowy: klucz API otwiera drogę do danych uczestników,
więc „kto go wystawił i komu” musi dać się odczytać rok później.

Jedna rzecz jest tu nietypowa i celowa: ``create_api_key`` zwraca **parę** (wiersz, klucz jawny).
Klucza nie da się odzyskać później, bo w bazie leży wyłącznie jego skrót – to jedyny moment
w życiu klucza, w którym istnieje jego postać jawna, i dlatego serwis oddaje ją wołającemu
zamiast zapisywać gdziekolwiek.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit

from .models import (
    DEFAULT_RATE_LIMIT,
    MAX_RATE_LIMIT,
    TOKEN_PREFIX,
    ApiKey,
    DeliveryStatus,
    WebhookDelivery,
    WebhookEndpoint,
    generate_prefix,
    generate_secret,
    hash_secret,
    validate_events,
    validate_scopes,
)

logger = logging.getLogger(__name__)

#: Pola odbiorcy, które wolno zmienić z panelu. Zamknięta lista, bo widok podaje słownik
#: z formularza – bez niej literówka w nazwie pola cicho zapisywałaby atrybut obok modelu.
ENDPOINT_EDITABLE_FIELDS = ("url", "events", "is_active")

#: Nazwa zdarzenia doręczenia próbnego. Świadomie **spoza** listy zdarzeń, na które da się
#: zapisać: odbiorca ma rozpoznać test po nazwie i nie pomylić go z prawdziwym ogłoszeniem
#: wyników. Sprawdzanie podpisu i trasa sieciowa są dokładnie te same, co przy zdarzeniu realnym.
TEST_EVENT = "ping"


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _as_domain_error(exc: ValidationError, code: str) -> DomainError:
    """Zamienia ``ValidationError`` modelu na błąd domenowy ze zdaniem dla koordynatora."""
    messages = []
    for value in getattr(exc, "message_dict", {}).values():
        messages.extend(value)
    return _bad_request(" ".join(messages) or "Nieprawidłowe dane.", code)


# --- klucze API ---------------------------------------------------------------------------------


def build_token(prefix: str, secret: str) -> str:
    """Klucz w postaci jawnej: ``ok_<prefix>_<sekret>``."""
    return f"{TOKEN_PREFIX}_{prefix}_{secret}"


@transaction.atomic
def create_api_key(
    *,
    name: str,
    scopes: list[str],
    edition=None,
    pii_allowed: bool = False,
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT,
    actor=None,
    request=None,
) -> tuple[ApiKey, str]:
    """Wystawia klucz. Zwraca ``(wiersz, klucz jawny)`` – klucz jawny istnieje tylko tutaj.

    Klucz bez ani jednego zakresu jest odrzucany: uwierzytelniłby się, a potem odbijał każde
    żądanie z 403, czyli wyglądałby na awarię systemu, a nie na pomyłkę przy wystawianiu.
    """
    name = (name or "").strip()
    if not name:
        raise _bad_request("Klucz musi mieć nazwę – inaczej nie wiadomo, komu go odebrać.", "NAME_REQUIRED")
    try:
        cleaned_scopes = validate_scopes(list(scopes or []))
    except ValidationError as exc:
        raise _as_domain_error(exc, "INVALID_SCOPES") from exc
    if not cleaned_scopes:
        raise _bad_request("Klucz bez ani jednego zakresu niczego nie otworzy.", "SCOPES_REQUIRED")
    try:
        limit = int(rate_limit_per_minute)
    except (TypeError, ValueError) as exc:
        raise _bad_request("Limit żądań musi być liczbą.", "INVALID_RATE_LIMIT") from exc
    if not (1 <= limit <= MAX_RATE_LIMIT):
        raise _bad_request(
            f"Limit żądań musi mieścić się w zakresie 1–{MAX_RATE_LIMIT}.", "INVALID_RATE_LIMIT"
        )

    secret = generate_secret()
    key = ApiKey(
        edition=edition,
        name=name,
        prefix=generate_prefix(),
        key_hash=hash_secret(secret),
        scopes=cleaned_scopes,
        pii_allowed=bool(pii_allowed),
        rate_limit_per_minute=limit,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    try:
        key.full_clean(exclude=["created_by", "key_hash"])
    except ValidationError as exc:
        raise _as_domain_error(exc, "INVALID_API_KEY") from exc
    key.save()

    audit(
        actor,
        "apikey.created",
        key,
        {
            "name": key.name,
            "prefix": key.prefix,
            "edition": key.edition_id,
            "scopes": key.scopes,
            "pii_allowed": key.pii_allowed,
            "rate_limit_per_minute": key.rate_limit_per_minute,
        },
        request=request,
    )
    logger.info("Wystawiono klucz API %s (%s), zakresy: %s", key.pk, key.prefix, ",".join(key.scopes))
    return key, build_token(key.prefix, secret)


@transaction.atomic
def revoke_api_key(key: ApiKey, *, actor=None, request=None) -> ApiKey:
    """Unieważnia klucz. Wiersz **zostaje** – razem z historią tego, co nim otwierano.

    Skasowanie wiersza zabrałoby audytowi obiekt, do którego odwołują się wpisy ``apikey.created``,
    a przy okazji zwolniłoby przedrostek do ponownego losowania.
    """
    locked = ApiKey.objects.select_for_update().get(pk=key.pk)
    if locked.revoked_at is not None:
        raise _conflict("Ten klucz jest już unieważniony.", "API_KEY_ALREADY_REVOKED")
    locked.revoked_at = timezone.now()
    locked.save(update_fields=["revoked_at"])
    audit(actor, "apikey.revoked", locked, {"name": locked.name, "prefix": locked.prefix}, request=request)
    logger.info("Unieważniono klucz API %s (%s).", locked.pk, locked.prefix)
    key.revoked_at = locked.revoked_at
    return locked


def api_keys_for_panel(edition=None) -> list[ApiKey]:
    """Klucze do listy w panelu: bieżące i unieważnione, najnowsze na górze.

    Unieważnione zostają na liście celowo – to jedyne miejsce, w którym widać, że partner
    **miał** dostęp i kiedy go stracił.
    """
    queryset = ApiKey.objects.select_related("edition", "created_by")
    if edition is not None:
        queryset = queryset.filter(edition__in=[edition, None])
    return list(queryset)


# --- odbiorcy webhooków -------------------------------------------------------------------------


@transaction.atomic
def create_endpoint(
    *, url: str, events: list[str], edition=None, actor=None, request=None
) -> WebhookEndpoint:
    """Dodaje odbiorcę webhooków wraz z wylosowanym sekretem podpisu."""
    cleaned = _clean_endpoint_fields({"url": url, "events": list(events or [])})
    endpoint = WebhookEndpoint(
        edition=edition,
        url=cleaned["url"],
        events=cleaned["events"],
        secret=generate_secret(),
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    try:
        endpoint.full_clean(exclude=["created_by"])
    except ValidationError as exc:
        raise _as_domain_error(exc, "INVALID_WEBHOOK") from exc
    endpoint.save()
    audit(
        actor,
        "webhook.created",
        endpoint,
        {"url": endpoint.url, "events": endpoint.events, "edition": endpoint.edition_id},
        request=request,
    )
    logger.info("Dodano odbiorcę webhooków %s (%s).", endpoint.pk, endpoint.url)
    return endpoint


def _clean_endpoint_fields(fields: dict) -> dict:
    """Wspólna walidacja dodania i zmiany – jedna reguła, dwa wejścia."""
    cleaned = dict(fields)
    if "url" in cleaned:
        cleaned["url"] = (cleaned["url"] or "").strip()
        if not cleaned["url"]:
            raise _bad_request("Odbiorca musi mieć adres.", "URL_REQUIRED")
    if "events" in cleaned:
        try:
            cleaned["events"] = validate_events(list(cleaned["events"] or []))
        except ValidationError as exc:
            raise _as_domain_error(exc, "INVALID_EVENTS") from exc
        if not cleaned["events"]:
            raise _bad_request(
                "Odbiorca bez ani jednego zdarzenia nigdy nic nie dostanie.", "EVENTS_REQUIRED"
            )
    return cleaned


@transaction.atomic
def update_endpoint(endpoint: WebhookEndpoint, *, actor=None, request=None, **fields) -> WebhookEndpoint:
    """Zmiana odbiorcy. Zapisuje wyłącznie pola, które faktycznie się zmieniły.

    Ponowne włączenie wygaszonego odbiorcy **zeruje licznik porażek** i datę wygaszenia: bez tego
    pierwsza kolejna porażka natychmiast wygasiłaby go z powrotem, bo licznik stałby już na
    dwudziestu, a koordynator włącza odbiorcę dokładnie po to, że poprawił adres.
    """
    unknown = sorted(set(fields) - set(ENDPOINT_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu odbiorcy: {', '.join(unknown)}.")
    cleaned = _clean_endpoint_fields(fields)
    locked = WebhookEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    changed = {name: value for name, value in cleaned.items() if getattr(locked, name) != value}
    if not changed:
        return locked
    diff = {name: {"from": getattr(locked, name), "to": value} for name, value in changed.items()}
    for name, value in changed.items():
        setattr(locked, name, value)
    if changed.get("is_active") is True:
        locked.failures = 0
        locked.disabled_at = None
        changed["failures"] = 0
        changed["disabled_at"] = None
    try:
        locked.full_clean(exclude=["created_by"])
    except ValidationError as exc:
        raise _as_domain_error(exc, "INVALID_WEBHOOK") from exc
    locked.save(update_fields=list(changed))
    audit(actor, "webhook.updated", locked, diff, request=request)
    for name in changed:
        setattr(endpoint, name, getattr(locked, name))
    return locked


@transaction.atomic
def delete_endpoint(endpoint: WebhookEndpoint, *, actor=None, request=None) -> None:
    """Usuwa odbiorcę razem z jego dziennikiem doręczeń.

    Wpis audytowy powstaje **przed** skasowaniem: po ``delete()`` obiekt nie ma ``pk``, więc
    śladu nie dałoby się już z niczym połączyć. Dziennik znika razem z odbiorcą (``CASCADE``) –
    to korespondencja z konkretnym adresem, a nie dokumentacja zawodów.
    """
    audit(
        actor,
        "webhook.deleted",
        endpoint,
        {"url": endpoint.url, "events": endpoint.events, "edition": endpoint.edition_id},
        request=request,
    )
    endpoint.delete()


@transaction.atomic
def send_test_delivery(endpoint: WebhookEndpoint, *, actor=None, request=None) -> WebhookDelivery:
    """Doręczenie próbne (zdarzenie ``ping``) – ta sama droga, co zdarzenia prawdziwego.

    Po to, żeby koordynator mógł sprawdzić adres i podpis **zanim** wydarzy się coś naprawdę.
    Wysyłka idzie po commicie, tak samo jak przy emisji: inaczej worker sięgnąłby po wiersz,
    którego jeszcze nie ma w bazie.
    """
    delivery = WebhookDelivery.objects.create(
        endpoint=endpoint,
        event=TEST_EVENT,
        payload={"message": "Doręczenie próbne z panelu koordynatora.", "endpoint_id": endpoint.pk},
        status=DeliveryStatus.PENDING,
    )
    _enqueue(delivery.pk)
    audit(actor, "webhook.test_sent", endpoint, {"delivery": delivery.pk}, request=request)
    return delivery


@transaction.atomic
def resend_delivery(delivery: WebhookDelivery, *, actor=None, request=None) -> WebhookDelivery:
    """Ponawia doręczenie z dziennika – tym samym wierszem, a nie kopią.

    Ten sam wiersz, bo koperta niesie jego identyfikator: odbiorca, który przyjął zdarzenie,
    a potem stracił odpowiedź, rozpozna po nim duplikat i nie policzy zdarzenia dwa razy.
    Licznik prób startuje od zera – to jest nowe podejście do tej samej przesyłki.
    """
    locked = WebhookDelivery.objects.select_for_update().select_related("endpoint").get(pk=delivery.pk)
    if not locked.endpoint.is_active:
        raise _conflict("Odbiorca jest wygaszony – włącz go, zanim ponowisz doręczenie.", "ENDPOINT_DISABLED")
    locked.status = DeliveryStatus.PENDING
    locked.attempts = 0
    locked.last_error = ""
    locked.delivered_at = None
    locked.save(update_fields=["status", "attempts", "last_error", "delivered_at"])
    _enqueue(locked.pk)
    diff = {"delivery": locked.pk, "event": locked.event}
    audit(actor, "webhook.resent", locked.endpoint, diff, request=request)
    return locked


def _enqueue(delivery_id: int) -> None:
    """Kolejkuje doręczenie po zatwierdzeniu transakcji."""

    def _send(pk: int = delivery_id) -> None:
        from .tasks import deliver_webhook

        deliver_webhook.delay(pk)

    transaction.on_commit(_send)


def deliveries_for_panel(*, endpoint=None, limit: int = 50) -> list[WebhookDelivery]:
    """Ostatnie doręczenia do dziennika w panelu – domyślnie pięćdziesiąt najnowszych.

    Limit jest twardy, bo dziennik rośnie z każdym zdarzeniem razy liczba odbiorców: ekran ma
    odpowiadać na pytanie „czy ostatnie rzeczy doszły”, a nie serwować całą historię.
    """
    queryset = WebhookDelivery.objects.select_related("endpoint")
    if endpoint is not None:
        queryset = queryset.filter(endpoint=endpoint)
    return list(queryset[:limit])
