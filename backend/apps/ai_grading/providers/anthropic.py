"""Anthropic (Claude) – dostawca, od którego ocena AI się zaczęła (wydanie v0.34.0).

Cienka warstwa nad ``apps.ai_grading.client`` i ``apps.ai_grading.prompt``: żądanie buduje
dokładnie ta sama funkcja co przed dodaniem innych dostawców (``prompt.build_request`` – myślenie
adaptacyjne, ``output_config.format``, beta ``server-side-fallback-2026-07-01`` z ``fallbacks``,
``cache_control`` na ostatnim stałym bloku), a wysyła je ten sam strumień z ``get_final_message``.
Kształt żądania pilnują testy ``test_prompt`` i ``test_client`` – bez zmian w tym wydaniu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.views.decorators.debug import sensitive_variables

from .. import client, crypto, prompt
from ..catalog import ANTHROPIC_MODELS
from .base import ApiFailure, CallResult, GradingInput, Limits, ModelInfo, Provider, ProviderRequest

logger = logging.getLogger(__name__)


@dataclass
class AnthropicProvider(Provider):
    name: str = "anthropic"
    label: str = "Anthropic"
    processor: str = "Anthropic PBC (dostawca modelu Claude) – podmiot przetwarzający"
    sdk_module: str = "anthropic"
    sdk_package: str = "anthropic"
    models: tuple[ModelInfo, ...] = ANTHROPIC_MODELS
    limits: Limits = field(
        default_factory=lambda: Limits(
            max_request_bytes=prompt.MAX_REQUEST_BYTES,
            max_image_bytes=prompt.MAX_IMAGE_BASE64_BYTES,
            max_pdf_pages=prompt.MAX_PDF_PAGES,
        )
    )
    cache_write_multiplier: str = "1.25"
    cache_read_multiplier: str = "0.1"

    def normalise_key(self, raw: str) -> str:
        return crypto.normalise_key(raw)

    def build_request(self, model: str, grading: GradingInput) -> ProviderRequest:
        request = prompt.build_request(
            model=model,
            competition_name=grading.competition_name,
            problem=prompt.problem_blocks(grading.materials),
            submission=prompt.submission_blocks(
                grading.submission, grading.submission_mime, page_count=grading.submission_pages
            ),
            max_tokens=grading.max_tokens,
        )
        return ProviderRequest(self.name, request)

    @sensitive_variables()
    def call(self, api_key, request) -> CallResult:
        """Kopia do zwykłego słownika: SDK dostaje dokładnie te argumenty, co przed zmianą.

        ``client.call_model`` tłumaczy błędy API; wyjątek **spoza** hierarchii SDK (błąd samej
        biblioteki, pamięci, kodowania) zamieniamy tu na ogólny błąd – tak jak u pozostałych
        dostawców – żeby jego treść nie trafiła z tracebackiem do logu workera.
        """
        try:
            return client.call_model(api_key, dict(request))
        except ApiFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - treść wyjątku zostaje poza logiem i bazą
            logger.warning("Ocena AI (anthropic): nieoczekiwany błąd klienta (%s).", type(exc).__name__)
            raise self.unexpected() from None

    def check_key(self, api_key, model: str) -> tuple[bool, str]:
        return client.check_key(api_key, model)

    def translate_error(self, exc: Exception):
        return client.translate_error(exc)
