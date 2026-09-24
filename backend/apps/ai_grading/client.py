"""Jedyne miejsce, w którym serwis rozmawia z API Anthropic.

Wszystko, co wie o SDK ``anthropic``, stoi tutaj: budowa klienta, strumień odpowiedzi, klasy
wyjątków i to, które z nich warto ponawiać. Reszta aplikacji dostaje :class:`CallResult` albo
:class:`ApiFailure` z **naszym** kodem i **naszym** komunikatem – surowa treść wyjątku SDK nie
trafia ani do bazy, ani na ekran (bywa długa, angielska i niesie szczegóły odpowiedzi HTTP).

Import SDK jest leniwy (wewnątrz funkcji), tak samo jak ``pyhanko`` w podpisie dokumentów:
instalacja z wyłączoną oceną AI nie płaci za załadowanie biblioteki przy starcie, a brak pakietu
w środowisku deweloperskim psuje wyłącznie tę funkcję, a nie cały serwis.

Klucz przychodzi jako :class:`apps.ai_grading.crypto.ApiKey` i jest odsłaniany **w argumencie**
konstruktora klienta – nie ma tu zmiennej z jawnym kluczem, którą traceback mógłby utrwalić.
W logach stoi ``request_id`` (``message._request_id``), bo to jego zna wsparcie Anthropic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings

from .crypto import ApiKey

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class CallResult:
    """Odpowiedź modelu po przeczytaniu strumienia. ``text`` ma sens wyłącznie przy ``end_turn``."""

    stop_reason: str
    text: str
    model: str
    request_id: str
    usage: Usage
    refusal_category: str | None = None


class ApiFailure(Exception):
    """Wywołanie się nie udało. ``retryable`` rozstrzyga, czy zadanie Celery spróbuje jeszcze raz."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after: int | None = None,
        request_id: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after
        self.request_id = request_id or ""


def _client(api_key: ApiKey, *, timeout: float, max_retries: int):
    import anthropic

    return anthropic.Anthropic(api_key=api_key.reveal(), timeout=timeout, max_retries=max_retries)


def _retry_after(exc) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        value = int(float(headers.get("retry-after", "")))
    except (TypeError, ValueError):
        return None
    return max(1, min(value, 3600))


def translate_error(exc: Exception) -> ApiFailure:
    """Wyjątek SDK → :class:`ApiFailure`. Łańcuch od najwęższego, jak w dokumentacji SDK.

    - 401 (zły albo unieważniony klucz) – bez ponowień, komunikat kieruje koordynatora do ustawień,
    - 403/404 – klucz bez dostępu do modelu albo model niedostępny: bez ponowień,
    - 429 – limit zapytań: ponawiamy z opóźnieniem z ``retry-after``,
    - ≥ 500 (także 529 „przeciążenie”) – ponawiamy,
    - pozostałe 4xx – błąd żądania: ponawianie dałoby ten sam wynik za tę samą cenę,
    - błąd połączenia i przekroczenie czasu – ponawiamy.
    """
    import anthropic

    request_id = getattr(exc, "request_id", "") or ""
    if isinstance(exc, anthropic.AuthenticationError):
        return ApiFailure(
            "auth",
            "Anthropic odrzucił klucz API (nieprawidłowy albo unieważniony). Wpisz poprawny klucz "
            "w ustawieniach oceny AI.",
            request_id=request_id,
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ApiFailure(
            "permission",
            "Klucz API nie ma uprawnień do tego modelu albo organizacja Anthropic go zablokowała.",
            request_id=request_id,
        )
    if isinstance(exc, anthropic.NotFoundError):
        return ApiFailure(
            "not_found", "Wybrany model nie jest dostępny dla tego klucza API.", request_id=request_id
        )
    if isinstance(exc, anthropic.RateLimitError):
        return ApiFailure(
            "rate_limit",
            "Przekroczono limit zapytań do API Anthropic – ocena zostanie ponowiona później.",
            retryable=True,
            retry_after=_retry_after(exc),
            request_id=request_id,
        )
    if isinstance(exc, anthropic.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0)
        if status >= 500:
            return ApiFailure(
                "server",
                f"Serwery Anthropic chwilowo nie odpowiadają (HTTP {status}).",
                retryable=True,
                request_id=request_id,
            )
        return ApiFailure(
            "api",
            f"API Anthropic odrzuciło żądanie (HTTP {status}). Szczegóły są w logu serwera "
            "pod identyfikatorem żądania.",
            request_id=request_id,
        )
    if isinstance(exc, anthropic.APIConnectionError):
        return ApiFailure(
            "connection",
            "Brak połączenia z API Anthropic albo przekroczony czas odpowiedzi.",
            retryable=True,
        )
    return ApiFailure("api", "Nieoczekiwany błąd klienta API Anthropic.")


def _usage(message) -> Usage:
    usage = getattr(message, "usage", None)

    def read(name: str) -> int:
        value = getattr(usage, name, 0) if usage is not None else 0
        return int(value or 0)

    return Usage(
        input_tokens=read("input_tokens"),
        output_tokens=read("output_tokens"),
        cache_write_tokens=read("cache_creation_input_tokens"),
        cache_read_tokens=read("cache_read_input_tokens"),
    )


def call_model(api_key: ApiKey, request: dict) -> CallResult:
    """Jedno wywołanie strumieniowe. Strumień, bo wejście bywa długie (PDF-y), a odpowiedź z
    myśleniem adaptacyjnym – też; zwykłe żądanie o takim ``max_tokens`` groziłoby przekroczeniem
    czasu HTTP. ``get_final_message()`` składa całość, więc zdarzeń nie obsługujemy ręcznie.

    ``max_retries`` SDK (domyślnie 2) przykrywa krótkie czkawki sieci w obrębie jednego
    wywołania; dłuższe przerwy obsługuje ponowienie zadania Celery z opóźnieniem.
    """
    import anthropic

    client = _client(
        api_key,
        timeout=float(settings.AI_GRADING_REQUEST_TIMEOUT),
        max_retries=int(settings.AI_GRADING_SDK_MAX_RETRIES),
    )
    try:
        with client.beta.messages.stream(**request) as stream:
            message = stream.get_final_message()
    except anthropic.APIError as exc:
        failure = translate_error(exc)
        logger.warning(
            "Ocena AI: wywołanie nieudane (%s, request_id=%s).", failure.code, failure.request_id or "-"
        )
        raise failure from None

    request_id = str(getattr(message, "_request_id", "") or "")
    stop_reason = str(getattr(message, "stop_reason", "") or "")
    details = getattr(message, "stop_details", None)
    category = getattr(details, "category", None) if details is not None else None
    text = "".join(
        getattr(block, "text", "") for block in getattr(message, "content", []) or [] if block.type == "text"
    )
    logger.info(
        "Ocena AI: odpowiedź %s (stop_reason=%s, request_id=%s).",
        getattr(message, "model", ""),
        stop_reason,
        request_id or "-",
    )
    return CallResult(
        stop_reason=stop_reason,
        text=text,
        model=str(getattr(message, "model", "") or request.get("model", "")),
        request_id=request_id,
        usage=_usage(message),
        refusal_category=str(category) if category else None,
    )


def check_key(api_key: ApiKey, model: str) -> tuple[bool, str]:
    """„Sprawdź klucz”: najtańsze pytanie, które wymaga ważnego klucza – opis modelu.

    ``models.retrieve`` nie zużywa tokenów, więc sprawdzenie nic nie kosztuje, a przy okazji
    potwierdza, że klucz widzi wybrany model. Krótki czas i bez ponowień: koordynator czeka na
    odpowiedź przed ekranem.
    """
    import anthropic

    client = _client(api_key, timeout=20.0, max_retries=0)
    try:
        info = client.models.retrieve(model)
    except anthropic.APIError as exc:
        failure = translate_error(exc)
        logger.info("Ocena AI: sprawdzenie klucza nieudane (%s).", failure.code)
        return False, failure.message
    name = getattr(info, "display_name", "") or model
    return True, f"Klucz działa – model {name} jest dostępny."
