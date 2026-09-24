"""Google (modele Gemini) – Gemini Developer API przez oficjalne SDK ``google-genai`` (linia 2.x).

Kształt żądania sprawdzony 24.09.2026 w dokumentacji i w źródle SDK 2.25.0
(https://github.com/googleapis/python-genai, ``google/genai/types.py``, ``errors.py``):

- https://ai.google.dev/api/generate-content – ``models.generate_content`` z ``contents``
  (części ``text`` i ``inline_data`` z bajtami PDF-a albo obrazu) i ``config``,
- ``GenerateContentConfig.response_json_schema`` przyjmuje zwykły JSON Schema (z
  ``additionalProperties`` i ``enum``) i wymaga ``response_mime_type="application/json"`` – nasz
  ``prompt.OUTPUT_SCHEMA`` idzie bez tłumaczenia na podzbiór OpenAPI (``response_schema``),
- https://ai.google.dev/gemini-api/docs/gemini-3 – rozumowanie przez ``thinking_level`` (``high``);
  starszy ``thinking_budget`` jest przestarzały, a podanie obu kończy się 400. ``thinking_level``
  wysyłamy wyłącznie modelom ``gemini-3*`` – starsza linia 2.5 zna tylko ``thinking_budget``,
- ``HttpOptions.timeout`` jest w **milisekundach**, a SDK bez ``retry_options`` nie ponawia nic –
  ponowienia w obrębie wywołania włączamy jawnie, tak jak ``max_retries`` u pozostałych,
- https://ai.google.dev/gemini-api/docs/image-understanding – dane wysyłane w żądaniu (inline)
  razem **do 20 MB**; https://ai.google.dev/gemini-api/docs/document-processing – PDF do
  1000 stron,
- odpowiedź: ``prompt_feedback.block_reason`` (prompt zablokowany – brak kandydatów),
  ``candidates[0].finish_reason`` (``STOP``, ``MAX_TOKENS``, ``SAFETY``, ``RECITATION``,
  ``PROHIBITED_CONTENT``, ``BLOCKLIST``, ``SPII``…), ``usage_metadata`` (tokeny „myślenia”
  ``thoughts_token_count`` są rozliczane jak wyjście – https://ai.google.dev/gemini-api/docs/thinking).

Pliku nie wysyłamy przez Files API (przechowywanie u Google przez 48 godzin), tylko w treści
żądania – https://ai.google.dev/gemini-api/docs/zdr zaleca to przy ograniczaniu retencji.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .. import prompt
from ..catalog import GOOGLE_MODELS
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
RETRY_STATUS_CODES = [408, 429, 500, 502, 503, 504]
#: Przyczyny blokady i zakończenia, które znaczą „filtr bezpieczeństwa albo polityka dostawcy”.
BLOCKING_REASONS = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "LANGUAGE",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "OTHER",
    }
)


def _name(value) -> str:
    """Wartość wyliczenia SDK (albo napis) jako napis bez prefiksu klasy."""
    if value is None:
        return ""
    return str(getattr(value, "value", value) or "").rsplit(".", 1)[-1].upper()


@dataclass
class GoogleProvider(Provider):
    name: str = "google"
    label: str = "Google"
    processor: str = "Google (Google Ireland Ltd. / Google LLC, Gemini API) – podmiot przetwarzający"
    sdk_module: str = "google.genai"
    sdk_package: str = "google-genai"
    models: tuple[ModelInfo, ...] = GOOGLE_MODELS
    limits: Limits = field(
        default_factory=lambda: Limits(
            max_request_bytes=20 * MEGABYTE - 512 * 1024,
            max_image_bytes=20 * MEGABYTE - 512 * 1024,
            max_pdf_pages=1000,
        )
    )
    cache_write_multiplier: str = "1"
    cache_read_multiplier: str = "0.1"
    note: str = (
        "Gemini API przyjmuje w jednym żądaniu najwyżej 20 MB danych (po zakodowaniu). Warunki Gemini "
        "API wymagają, żeby korzystające z niego osoby i usługi nie były skierowane do osób poniżej "
        "18 lat – sprawdź to z inspektorem ochrony danych (podręcznik organizatora § 4.12)."
    )

    def is_available(self) -> bool:
        try:
            return super().is_available()
        except ModuleNotFoundError:  # brak samego pakietu ``google`` – find_spec rodzica
            return False

    def build_request(self, model: str, grading: GradingInput) -> ProviderRequest:
        parts = prompt.neutral_parts(grading)
        prompt.check_parts(parts, self.limits, self.label)
        content = []
        for part in parts:
            if part.kind == "text":
                content.append({"text": part.text})
            else:
                content.append({"inline_data": {"mime_type": part.mime, "data": part.data}})
        config = {
            "system_instruction": prompt.system_prompt(grading.competition_name),
            "response_mime_type": "application/json",
            "response_json_schema": prompt.OUTPUT_SCHEMA,
            "max_output_tokens": grading.max_tokens,
        }
        if model.startswith("gemini-3"):
            config["thinking_config"] = {"thinking_level": "high"}
        return ProviderRequest(
            self.name, model=model, contents=[{"role": "user", "parts": content}], config=config
        )

    @sensitive_variables()
    def _client(self, api_key, *, timeout: float, max_retries: int):
        from google import genai
        from google.genai import types

        options = types.HttpOptions(
            timeout=int(timeout * 1000),
            retry_options=types.HttpRetryOptions(
                attempts=max(1, max_retries + 1), http_status_codes=RETRY_STATUS_CODES
            ),
        )
        # Klucz idzie nagłówkiem ``x-goog-api-key`` (``_api_client.py``), a nie w adresie – więc
        # log ``httpx`` z adresem żądania go nie zawiera.
        return genai.Client(api_key=api_key.reveal(), http_options=options)

    def translate_error(self, exc: Exception) -> ApiFailure:
        """Wyjątek SDK ``google-genai`` (albo ``httpx`` pod nim) → :class:`ApiFailure`.

        Zły klucz Gemini API zgłasza bywa jako 400 z powodem ``API_KEY_INVALID`` w szczegółach,
        a nie jako 401 – sprawdzamy oba. 429 ``RESOURCE_EXHAUSTED`` ponawiamy, 402 (wyczerpane środki
        przedpłaty) – nie. Błędów 400 i 500 Google nie rozlicza
        (https://ai.google.dev/gemini-api/docs/billing).
        """
        from google.genai import errors

        if isinstance(exc, errors.APIError):
            status = int(getattr(exc, "code", 0) or 0)
            reason = _name(getattr(exc, "status", ""))
            try:
                details = json.dumps(getattr(exc, "details", None), default=str)
            except (TypeError, ValueError):
                details = ""
            headers = getattr(getattr(exc, "response", None), "headers", None)
            if status in (401, 403) or "API_KEY_INVALID" in details or reason == "UNAUTHENTICATED":
                return ApiFailure(
                    "auth",
                    "Google odrzucił klucz API (nieprawidłowy, unieważniony albo bez dostępu do Gemini "
                    "API w tym projekcie). Wpisz poprawny klucz w ustawieniach oceny AI.",
                    kind=FailureKind.AUTH,
                )
            if status == 404:
                return ApiFailure(
                    "not_found",
                    "Wybrany model nie jest dostępny dla tego klucza API.",
                    kind=FailureKind.PERMANENT,
                )
            if status == 402:
                return ApiFailure(
                    "quota",
                    "Konto Google nie ma środków na Gemini API – trzeba to uregulować w konsoli Google.",
                    kind=FailureKind.PERMANENT,
                )
            if status == 429:
                return ApiFailure(
                    "rate_limit",
                    "Przekroczono limit zapytań do Gemini API – ocena zostanie ponowiona później.",
                    retry_after=retry_after_seconds(headers),
                    kind=FailureKind.RATE_LIMIT,
                )
            if status == 413:
                return ApiFailure(
                    "too_large",
                    "Żądanie przekracza limit Gemini API. Oceń tę pracę innym dostawcą albo bez sugestii AI.",
                    kind=FailureKind.TOO_LARGE,
                )
            if status >= 500:
                return ApiFailure(
                    "server",
                    f"Serwery Google chwilowo nie odpowiadają (HTTP {status}).",
                    retry_after=retry_after_seconds(headers),
                    kind=FailureKind.TRANSIENT,
                )
            return ApiFailure(
                "api",
                f"Gemini API odrzuciło żądanie (HTTP {status}{', ' + reason if reason else ''}).",
                kind=FailureKind.PERMANENT,
            )
        # SDK nie opakowuje błędów sieci – przychodzą wprost z ``httpx`` (albo ``httpx2``).
        if type(exc).__module__.split(".")[0] in ("httpx", "httpx2", "httpcore", "httpcore2") or isinstance(
            exc, (TimeoutError, ConnectionError)
        ):
            return ApiFailure(
                "connection",
                "Brak połączenia z Gemini API albo przekroczony czas odpowiedzi.",
                kind=FailureKind.TRANSIENT,
            )
        return self.unexpected()

    def _failure(self, exc: Exception) -> ApiFailure:
        failure = self.translate_error(exc)
        logger.warning(
            "Ocena AI (%s): wywołanie nieudane (%s, %s).", self.name, failure.code, type(exc).__name__
        )
        return failure

    @sensitive_variables()
    def call(self, api_key, request) -> CallResult:
        client = self._client(
            api_key,
            timeout=float(settings.AI_GRADING_REQUEST_TIMEOUT),
            max_retries=int(settings.AI_GRADING_SDK_MAX_RETRIES),
        )
        try:
            response = client.models.generate_content(**request)
        except Exception as exc:  # noqa: BLE001 - tłumaczone na ApiFailure, bez treści wyjątku
            raise self._failure(exc) from None
        result = parse_response(response, request.get("model", ""))
        logger.info(
            "Ocena AI (%s): odpowiedź %s (stop_reason=%s, response_id=%s).",
            self.name,
            result.model,
            result.stop_reason,
            result.request_id or "-",
        )
        return result

    @sensitive_variables()
    def check_key(self, api_key, model: str) -> tuple[bool, str]:
        """„Sprawdź klucz”: opis modelu (``GET v1beta/models/{model}``) – bez tokenów."""
        client = self._client(api_key, timeout=20.0, max_retries=0)
        try:
            info = client.models.get(model=model)
        except Exception as exc:  # noqa: BLE001 - każdy błąd to „nie działa”, bez treści wyjątku
            return False, self._failure(exc).message
        name = getattr(info, "display_name", "") or model
        return True, f"Klucz działa – model {name} jest dostępny."


def parse_response(response, requested_model: str) -> CallResult:
    """``GenerateContentResponse`` → :class:`CallResult` w słowniku Anthropic.

    Blokada **promptu** (``prompt_feedback.block_reason``) i blokada **odpowiedzi**
    (``finish_reason`` z :data:`BLOCKING_REASONS`) to dla serwisu odmowa – z przyczyną w kategorii.
    ``prompt_token_count`` obejmuje tokeny z cache (``cached_content_token_count``) – odejmujemy je;
    tokeny myślenia doliczamy do wyjścia, bo tak je rozlicza Google.
    """
    feedback = getattr(response, "prompt_feedback", None)
    block = _name(getattr(feedback, "block_reason", None)) if feedback is not None else ""
    candidates = getattr(response, "candidates", None) or []
    candidate = candidates[0] if candidates else None
    finish = _name(getattr(candidate, "finish_reason", None)) if candidate is not None else ""
    category = None
    text = ""
    if block and block != "BLOCKED_REASON_UNSPECIFIED":
        stop_reason, category = "refusal", f"blokada promptu: {block}"
    elif candidate is None:
        stop_reason, category = "refusal", "brak odpowiedzi"
    elif finish == "STOP":
        stop_reason = "end_turn"
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        text = "".join(
            getattr(part, "text", "") or "" for part in parts if not getattr(part, "thought", False)
        )
    elif finish == "MAX_TOKENS":
        stop_reason = "max_tokens"
    elif finish in BLOCKING_REASONS:
        stop_reason, category = "refusal", f"filtr: {finish}"
    else:
        stop_reason = finish.lower() or "unknown"

    usage = getattr(response, "usage_metadata", None)

    def number(name: str) -> int:
        return int(getattr(usage, name, 0) or 0) if usage is not None else 0

    cached = number("cached_content_token_count")
    return CallResult(
        stop_reason=stop_reason,
        text=text,
        model=str(getattr(response, "model_version", "") or requested_model),
        request_id=str(getattr(response, "response_id", "") or ""),
        usage=Usage(
            input_tokens=max(0, number("prompt_token_count") - cached),
            output_tokens=number("candidates_token_count") + number("thoughts_token_count"),
            cache_read_tokens=cached,
        ),
        refusal_category=category,
    )
