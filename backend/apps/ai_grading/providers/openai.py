"""OpenAI (modele GPT) – Responses API przez oficjalne SDK ``openai`` (linia 3.x).

Kształt żądania sprawdzony 24.09.2026 w dokumentacji i w źródle SDK 3.19.2:

- https://developers.openai.com/api/reference/resources/responses/methods/create –
  ``instructions`` (prompt systemowy), ``input`` z częściami ``input_text`` / ``input_file`` /
  ``input_image``, ``max_output_tokens`` (limit obejmuje tokeny rozumowania), ``reasoning.effort``,
  ``store`` (domyślnie ``true`` – odpowiedź leżałaby u OpenAI co najmniej 30 dni, więc ``False``),
- https://developers.openai.com/api/docs/guides/structured-outputs – ``text.format`` typu
  ``json_schema`` ze ``strict: true`` (wymaga ``additionalProperties: false`` i kompletu ``required``
  – nasz ``prompt.OUTPUT_SCHEMA`` spełnia oba warunki bez zmian),
- https://developers.openai.com/api/docs/guides/file-inputs – PDF jako ``file_data`` z adresem
  ``data:application/pdf;base64,…``; wszystkie pliki jednego żądania razem **do 50 MB**,
- https://developers.openai.com/api/docs/guides/images-vision – obraz jako ``image_url`` z adresem
  ``data:``,
- https://developers.openai.com/api/docs/guides/error-codes – 429 bywa **trwały**
  (``insufficient_quota`` i pokrewne: wyczerpane środki albo limit wydatków projektu) – takiego
  nie ponawiamy.

Wywołanie jest strumieniowe (``responses.stream`` + ``get_final_response``) z tego samego powodu,
co u Anthropic: rozumowanie na ``effort: high`` trwa minuty, a zwykłe żądanie bez ruchu na łączu
groziłoby przekroczeniem czasu po drodze.

Tu stoi też to, co dzielą dostawcy mówiący protokołem OpenAI (Meta Model API – ``providers.meta``):
budowa klienta i tłumaczenie wyjątków SDK.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .. import prompt
from ..catalog import OPENAI_MODELS
from .base import (
    ApiFailure,
    CallResult,
    FailureKind,
    GradingInput,
    Limits,
    ModelInfo,
    Provider,
    ProviderRequest,
    Usage,
    retry_after_seconds,
)

logger = logging.getLogger(__name__)

MEGABYTE = 1024 * 1024

#: Kody 429/402, przy których ponawianie nic nie da: konto nie ma środków albo przekroczyło
#: limit wydatków ustawiony u dostawcy (error-codes OpenAI; ``billing_not_configured`` – Meta).
QUOTA_CODES = frozenset(
    {
        "insufficient_quota",
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "billing_not_configured",
    }
)
#: Kody błędu 400 znaczące „dostawca odrzucił treść” (filtr bezpieczeństwa), a nie zły kształt.
REFUSAL_CODES = frozenset({"content_policy_violation", "invalid_prompt", "content_filter"})
TOO_LARGE_CODES = frozenset({"context_length_exceeded", "string_above_max_length", "payload_too_large"})

#: Nazwa schematu w ``text.format`` – widać ją w panelu dostawcy, więc jest opisowa.
SCHEMA_NAME = "ocena_pracy"


def _error_code(exc) -> str:
    code = getattr(exc, "code", None)
    if not code:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error", body)
            code = error.get("code") or error.get("type") if isinstance(error, dict) else None
    return str(code or "")


def translate_openai_error(exc: Exception, label: str) -> ApiFailure:
    """Wyjątek SDK ``openai`` → :class:`ApiFailure`. Łańcuch od najwęższej klasy.

    Komunikat jest **nasz** – treść wyjątku (a w niej fragment odpowiedzi HTTP) nie wychodzi stąd
    nigdzie; zostaje identyfikator żądania, po którym wsparcie dostawcy znajdzie szczegóły.
    """
    import openai

    request_id = str(getattr(exc, "request_id", "") or "")
    code = _error_code(exc)
    if isinstance(exc, openai.AuthenticationError):
        return ApiFailure(
            "auth",
            f"{label} odrzucił klucz API (nieprawidłowy albo unieważniony). Wpisz poprawny klucz "
            "w ustawieniach oceny AI.",
            request_id=request_id,
            kind=FailureKind.AUTH,
        )
    if isinstance(exc, openai.PermissionDeniedError):
        return ApiFailure(
            "permission",
            f"Klucz API nie ma uprawnień do tego modelu albo {label} nie obsługuje tego kraju lub regionu.",
            request_id=request_id,
            kind=FailureKind.AUTH,
        )
    if isinstance(exc, openai.NotFoundError):
        return ApiFailure(
            "not_found",
            "Wybrany model nie jest dostępny dla tego klucza API.",
            request_id=request_id,
            kind=FailureKind.PERMANENT,
        )
    if code in QUOTA_CODES or int(getattr(exc, "status_code", 0) or 0) == 402:
        return ApiFailure(
            "quota",
            f"Konto {label} nie ma środków albo przekroczyło limit wydatków ustawiony u dostawcy – "
            "ponawianie nic nie da, trzeba to uregulować w konsoli dostawcy.",
            request_id=request_id,
            kind=FailureKind.PERMANENT,
        )
    if isinstance(exc, openai.RateLimitError):
        return ApiFailure(
            "rate_limit",
            f"Przekroczono limit zapytań do API {label} – ocena zostanie ponowiona później.",
            retry_after=retry_after_seconds(getattr(getattr(exc, "response", None), "headers", None)),
            request_id=request_id,
            kind=FailureKind.RATE_LIMIT,
        )
    if isinstance(exc, openai.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0)
        if status == 413 or code in TOO_LARGE_CODES:
            return ApiFailure(
                "too_large",
                f"Żądanie przekracza limit {label} (rozmiar albo długość kontekstu). Oceń tę pracę "
                "innym dostawcą albo bez sugestii AI.",
                request_id=request_id,
                kind=FailureKind.TOO_LARGE,
            )
        if code in REFUSAL_CODES:
            return ApiFailure(
                "refusal",
                f"{label} odrzucił treść żądania (filtr bezpieczeństwa). Oceń tę pracę bez sugestii AI "
                "albo innym dostawcą.",
                request_id=request_id,
                kind=FailureKind.REFUSAL,
            )
        if status >= 500:
            return ApiFailure(
                "server",
                f"Serwery {label} chwilowo nie odpowiadają (HTTP {status}).",
                retry_after=retry_after_seconds(getattr(getattr(exc, "response", None), "headers", None)),
                request_id=request_id,
                kind=FailureKind.TRANSIENT,
            )
        return ApiFailure(
            "api",
            f"API {label} odrzuciło żądanie (HTTP {status}). Szczegóły są u dostawcy pod "
            "identyfikatorem żądania.",
            request_id=request_id,
            kind=FailureKind.PERMANENT,
        )
    if isinstance(exc, openai.APIConnectionError):  # obejmuje APITimeoutError
        return ApiFailure(
            "connection",
            f"Brak połączenia z API {label} albo przekroczony czas odpowiedzi.",
            kind=FailureKind.TRANSIENT,
        )
    return ApiFailure("api", f"Nieoczekiwany błąd klienta API {label}.", kind=FailureKind.PERMANENT)


def cache_key(parts: list[prompt.Part]) -> str:
    """``prompt_cache_key`` – skrót stałego prefiksu (materiały zadania), bez danych o pracy.

    OpenAI buforuje prefiks automatycznie, a klucz kieruje żądania z tym samym prefiksem na te
    same serwery – seria prac jednego zadania częściej trafia w cache (0,1 stawki wejścia).
    """
    digest = hashlib.sha256()
    for part in parts:
        if part.stable:
            digest.update(part.kind.encode())
            digest.update(part.text.encode("utf-8"))
            digest.update(part.data)
    return f"ocena-ai-{digest.hexdigest()[:40]}"


@dataclass
class OpenAICompatibleProvider(Provider):
    """Wspólny rdzeń dostawców mówiących protokołem OpenAI: klient SDK i tłumaczenie błędów."""

    sdk_module: str = "openai"
    sdk_package: str = "openai"
    base_url: str | None = None

    @sensitive_variables()
    def _client(self, api_key, *, timeout: float, max_retries: int):
        import openai

        kwargs = {"timeout": timeout, "max_retries": max_retries}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return openai.OpenAI(api_key=api_key.reveal(), **kwargs)

    def translate_error(self, exc: Exception) -> ApiFailure:
        return translate_openai_error(exc, self.label)

    def _failure(self, exc: Exception) -> ApiFailure:
        """Każdy wyjątek z wywołania → :class:`ApiFailure`; w logu kod i identyfikator, nic więcej."""
        import openai

        failure = self.translate_error(exc) if isinstance(exc, openai.APIError) else self.unexpected()
        logger.warning(
            "Ocena AI (%s): wywołanie nieudane (%s, %s, request_id=%s).",
            self.name,
            failure.code,
            type(exc).__name__,
            failure.request_id or "-",
        )
        return failure

    @sensitive_variables()
    def check_key(self, api_key, model: str) -> tuple[bool, str]:
        """„Sprawdź klucz”: opis modelu (``GET /models/{model}``) – bez tokenów, więc bez kosztu."""
        client = self._client(api_key, timeout=20.0, max_retries=0)
        try:
            info = client.models.retrieve(model)
        except Exception as exc:  # noqa: BLE001 - każdy błąd to „nie działa”, bez treści wyjątku
            failure = self._failure(exc)
            return False, failure.message
        return True, f"Klucz działa – model {getattr(info, 'id', '') or model} jest dostępny."


@dataclass
class OpenAIProvider(OpenAICompatibleProvider):
    name: str = "openai"
    label: str = "OpenAI"
    processor: str = (
        "OpenAI (OpenAI Ireland Ltd. / OpenAI, L.L.C., dostawca modeli GPT) – podmiot przetwarzający"
    )
    models: tuple[ModelInfo, ...] = OPENAI_MODELS
    limits: Limits = field(
        default_factory=lambda: Limits(
            # 50 MB to limit **plików** jednego żądania; liczymy zachowawczo rozmiar po base64.
            max_request_bytes=50 * MEGABYTE,
            max_image_bytes=50 * MEGABYTE,
            max_file_bytes=50 * MEGABYTE,
        )
    )
    cache_write_multiplier: str = "1.25"
    cache_read_multiplier: str = "0.1"

    def build_request(self, model: str, grading: GradingInput) -> ProviderRequest:
        parts = prompt.neutral_parts(grading)
        prompt.check_parts(parts, self.limits, self.label)
        content = []
        for part in parts:
            if part.kind == "text":
                content.append({"type": "input_text", "text": part.text})
            elif part.kind == "pdf":
                content.append(
                    {"type": "input_file", "filename": part.filename, "file_data": prompt.data_url(part)}
                )
            else:
                content.append({"type": "input_image", "image_url": prompt.data_url(part), "detail": "high"})
        return ProviderRequest(
            self.name,
            model=model,
            instructions=prompt.system_prompt(grading.competition_name),
            input=[{"role": "user", "content": content}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "schema": prompt.OUTPUT_SCHEMA,
                    "strict": True,
                }
            },
            reasoning={"effort": "high"},
            max_output_tokens=grading.max_tokens,
            store=False,
            prompt_cache_key=cache_key(parts),
        )

    @sensitive_variables()
    def call(self, api_key, request) -> CallResult:
        client = self._client(
            api_key,
            timeout=float(settings.AI_GRADING_REQUEST_TIMEOUT),
            max_retries=int(settings.AI_GRADING_SDK_MAX_RETRIES),
        )
        try:
            with client.responses.stream(**request) as stream:
                response = stream.get_final_response()
        except Exception as exc:  # noqa: BLE001 - tłumaczone na ApiFailure, bez treści wyjątku
            raise self._failure(exc) from None
        result = parse_response(response, request.get("model", ""))
        logger.info(
            "Ocena AI (%s): odpowiedź %s (stop_reason=%s, request_id=%s).",
            self.name,
            result.model,
            result.stop_reason,
            result.request_id or "-",
        )
        return result


def parse_response(response, requested_model: str) -> CallResult:
    """Obiekt ``Response`` → :class:`CallResult` w słowniku Anthropic.

    - odmowa modelu to część ``refusal`` w treści wiadomości – przy ``status == "completed"``,
    - ``incomplete`` z powodem ``max_output_tokens`` to ucięcie na limicie, ``content_filter`` –
      blokada filtra (traktowana jak odmowa),
    - ``input_tokens`` OpenAI obejmuje tokeny z cache i zapisane do cache – odejmujemy je, żeby
      ``Usage.input_tokens`` znaczyło to samo co u Anthropic.
    """
    text_parts: list[str] = []
    refusal = ""
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "message":
            continue
        for content in getattr(item, "content", None) or []:
            kind = getattr(content, "type", "")
            if kind == "output_text":
                text_parts.append(getattr(content, "text", "") or "")
            elif kind == "refusal":
                refusal = getattr(content, "refusal", "") or "refusal"
    status = str(getattr(response, "status", "") or "")
    category = None
    if refusal:
        stop_reason, category = "refusal", "odmowa modelu"
    elif status == "completed":
        stop_reason = "end_turn"
    elif status == "incomplete":
        reason = str(getattr(getattr(response, "incomplete_details", None), "reason", "") or "")
        if reason == "max_output_tokens":
            stop_reason = "max_tokens"
        elif reason == "content_filter":
            stop_reason, category = "refusal", "filtr treści"
        else:
            stop_reason = f"incomplete:{reason or '?'}"
    else:
        stop_reason = status or "unknown"

    usage = getattr(response, "usage", None)
    details = getattr(usage, "input_tokens_details", None)

    def number(source, name: str) -> int:
        return int(getattr(source, name, 0) or 0) if source is not None else 0

    cached = number(details, "cached_tokens")
    written = number(details, "cache_write_tokens")
    total_in = number(usage, "input_tokens")
    request_id = str(getattr(response, "_request_id", "") or getattr(response, "id", "") or "")
    return CallResult(
        stop_reason=stop_reason,
        text="".join(text_parts),
        model=str(getattr(response, "model", "") or requested_model),
        request_id=request_id,
        usage=Usage(
            input_tokens=max(0, total_in - cached - written),
            output_tokens=number(usage, "output_tokens"),
            cache_write_tokens=written,
            cache_read_tokens=cached,
        ),
        refusal_category=category,
    )
