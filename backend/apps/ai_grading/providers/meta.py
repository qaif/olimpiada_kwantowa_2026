"""Meta – Meta Model API (modele Muse Spark) przez oficjalne SDK ``openai`` z adresem Mety.

Dlaczego nie „Llama API” i nie SDK ``llama-api-client``: Meta wyłączyła Llama API
(``api.llama.com``) 6.07.2026, a jej adresy dla deweloperów prowadzą dziś do Meta Model API
(https://dev.meta.ai/). SDK ``llama-api-client`` (ostatnie wydanie 0.6.0 z 18.12.2025) rozmawia
z usługą, której już nie ma. Meta Model API jest według dokumentacji zgodne z SDK OpenAI
(https://dev.meta.ai/docs/overview – adres ``https://api.meta.ai/v1``), więc nie potrzebuje
osobnej zależności.

Kształt żądania (sprawdzony 24.09.2026):

- https://dev.meta.ai/docs/protocols/chat-completions – Chat Completions: ``max_completion_tokens``
  (obejmuje rozumowanie), ``reasoning_effort``; parametry ``stop``, ``n``, ``logprobs`` kończą się
  400, więc ich nie wysyłamy. Chat Completions, a nie Responses, bo tylko dla niego dokumentacja
  podaje dokładny kształt schematu odpowiedzi,
- https://dev.meta.ai/docs/structured-output – ``response_format`` typu ``json_schema`` ze
  ``strict: true`` (te same warunki co u OpenAI – nasz schemat je spełnia),
- https://dev.meta.ai/docs/file-handling – PDF jako część ``file`` z ``file_data``
  (``data:application/pdf;base64,…``), do 50 MB; z PDF-a model dostaje tekst pierwszych 100 stron,
  ale **obrazy tylko pierwszych 50** – dłuższy PDF (np. skan pracy ręcznej) byłby oceniany po
  połowie, więc odmawiamy go przed wysyłką (``Limits.max_pages_per_pdf``),
- https://dev.meta.ai/docs/features/image-understanding – obraz jako ``image_url`` z adresem
  ``data:``, do 50 MB,
- https://dev.meta.ai/docs/error-handling – koperta błędów jak u OpenAI, 402
  ``billing_not_configured``, 413 za duże żądanie, 429 z ``Retry-After``.

Wyłącznie modele warstwy **standardowej**: warstwa ``-contributor`` pozwala Mecie używać danych do
ulepszania produktów (https://dev.meta.ai/products/meta-model-api/), a to wyklucza prace uczestników.
Identyfikator z przyrostkiem ``-contributor`` odrzucamy już przy zapisie ustawień.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .. import prompt
from ..catalog import META_MODELS
from .base import CallResult, GradingInput, Limits, ModelInfo, ProviderRequest, Usage
from .openai import SCHEMA_NAME, OpenAICompatibleProvider

logger = logging.getLogger(__name__)

MEGABYTE = 1024 * 1024
META_BASE_URL = "https://api.meta.ai/v1"


@dataclass
class MetaProvider(OpenAICompatibleProvider):
    name: str = "meta"
    label: str = "Meta"
    processor: str = (
        "Meta Platforms Ireland Ltd. (Meta Model API, modele Muse Spark) – podmiot przetwarzający"
    )
    base_url: str | None = META_BASE_URL
    models: tuple[ModelInfo, ...] = META_MODELS
    limits: Limits = field(
        default_factory=lambda: Limits(
            max_request_bytes=50 * MEGABYTE,
            max_image_bytes=50 * MEGABYTE,
            max_file_bytes=50 * MEGABYTE,
            max_pages_per_pdf=50,
        )
    )
    cache_write_multiplier: str = "1"
    cache_read_multiplier: str = "0.12"
    note: str = (
        "Meta Model API jest w wersji zapoznawczej (public preview). Z PDF-a dłuższego niż 50 stron "
        "Meta bierze obrazy tylko pierwszych 50 stron – taki PDF odrzucamy przed wysyłką."
    )

    def build_request(self, model: str, grading: GradingInput) -> ProviderRequest:
        parts = prompt.neutral_parts(grading)
        prompt.check_parts(parts, self.limits, self.label)
        content = []
        for part in parts:
            if part.kind == "text":
                content.append({"type": "text", "text": part.text})
            elif part.kind == "pdf":
                content.append(
                    {"type": "file", "file": {"filename": part.filename, "file_data": prompt.data_url(part)}}
                )
            else:
                content.append({"type": "image_url", "image_url": {"url": prompt.data_url(part)}})
        return ProviderRequest(
            self.name,
            model=model,
            messages=[
                {"role": "system", "content": prompt.system_prompt(grading.competition_name)},
                {"role": "user", "content": content},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": SCHEMA_NAME, "schema": prompt.OUTPUT_SCHEMA, "strict": True},
            },
            max_completion_tokens=grading.max_tokens,
            reasoning_effort="high",
        )

    @sensitive_variables()
    def call(self, api_key, request) -> CallResult:
        client = self._client(
            api_key,
            timeout=float(settings.AI_GRADING_REQUEST_TIMEOUT),
            max_retries=int(settings.AI_GRADING_SDK_MAX_RETRIES),
        )
        try:
            response = client.chat.completions.create(**request)
        except Exception as exc:  # noqa: BLE001 - tłumaczone na ApiFailure, bez treści wyjątku
            raise self._failure(exc) from None
        result = parse_chat_completion(response, request.get("model", ""))
        logger.info(
            "Ocena AI (%s): odpowiedź %s (stop_reason=%s, request_id=%s).",
            self.name,
            result.model,
            result.stop_reason,
            result.request_id or "-",
        )
        return result

    @sensitive_variables()
    def check_key(self, api_key, model: str) -> tuple[bool, str]:
        """„Sprawdź klucz”: lista modeli (``GET /v1/models``) – bez tokenów, więc bez kosztu."""
        client = self._client(api_key, timeout=20.0, max_retries=0)
        try:
            ids = {getattr(item, "id", "") for item in client.models.list()}
        except Exception as exc:  # noqa: BLE001 - każdy błąd to „nie działa”, bez treści wyjątku
            return False, self._failure(exc).message
        if model not in ids:
            return False, f"Klucz działa, ale model {model} nie jest dostępny dla tego konta."
        return True, f"Klucz działa – model {model} jest dostępny."


def parse_chat_completion(response, requested_model: str) -> CallResult:
    """Odpowiedź Chat Completions → :class:`CallResult` w słowniku Anthropic.

    ``finish_reason``: ``stop`` → pełna odpowiedź, ``length`` → ucięta na limicie,
    ``content_filter`` → blokada filtra; ``message.refusal`` → odmowa modelu. ``prompt_tokens``
    obejmuje tokeny z cache – odejmujemy je jak u OpenAI.
    """
    choices = getattr(response, "choices", None) or []
    choice = choices[0] if choices else None
    message = getattr(choice, "message", None)
    finish = str(getattr(choice, "finish_reason", "") or "")
    refusal = getattr(message, "refusal", None)
    category = None
    if refusal:
        stop_reason, category = "refusal", "odmowa modelu"
    elif finish == "stop":
        stop_reason = "end_turn"
    elif finish == "length":
        stop_reason = "max_tokens"
    elif finish == "content_filter":
        stop_reason, category = "refusal", "filtr treści"
    else:
        stop_reason = finish or "unknown"
    usage = getattr(response, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
    return CallResult(
        stop_reason=stop_reason,
        text=str(getattr(message, "content", "") or ""),
        model=str(getattr(response, "model", "") or requested_model),
        request_id=str(getattr(response, "_request_id", "") or getattr(response, "id", "") or ""),
        usage=Usage(
            input_tokens=max(0, prompt_tokens - cached),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0,
            cache_read_tokens=cached,
        ),
        refusal_category=category,
    )
