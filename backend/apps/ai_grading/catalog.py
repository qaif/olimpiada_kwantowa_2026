"""Katalog dostawców: kuratorowane modele i cennik domyślny – stan z 24.09.2026.

Czysty moduł danych (bez SDK i bez bazy), czytany przez modele, dostawców, formularze i migrację.
Identyfikatory i ceny spisane z oficjalnych stron dostawców w dniu wprowadzenia zmiany:

- Anthropic – bez zmian względem v0.34.0 (``claude-opus-5``, ``claude-sonnet-5``; cel przełączenia
  ``fallbacks`` ``claude-opus-4-8`` z tymi samymi stawkami co Opus 5),
- OpenAI – https://developers.openai.com/api/docs/models i
  https://developers.openai.com/api/docs/pricing (rodzina ``gpt-6``: Astra – najdokładniejszy,
  Sol – środek, Luna – najtańszy; odczyt z cache 0,1 stawki wejścia, zapis do cache 1,25),
- Google – https://ai.google.dev/gemini-api/docs/models i
  https://ai.google.dev/gemini-api/docs/pricing (warstwa płatna; ``gemini-3.8-flash`` jest
  stabilnym modelem flagowym, ``gemini-3.1-pro-preview`` – wersją zapoznawczą; odczyt z cache
  0,1 stawki wejścia; stawki 3.8 Flash rosną 1.01.2027 – do poprawienia w tabeli cen),
- Meta – https://dev.meta.ai/docs/models i https://dev.meta.ai/docs/pricing-rate-limits (Meta Model
  API, modele ``muse-spark``; **wyłącznie** warstwa standardowa – warstwa ``-contributor`` pozwala
  Mecie uczyć modele na przesłanych danych, więc do prac uczestników się nie nadaje; odczyt z cache
  0,15 USD przy wejściu 1,25 USD). Dawne Llama API (``api.llama.com``) Meta wyłączyła 6.07.2026 –
  modeli Llama nie ma już u Mety w API, stąd dostawca „Meta” to Meta Model API.

Identyfikatory się zmieniają – dlatego obok listy jest pole „inny identyfikator modelu”, a ceny są
**domyślne**: koordynator nadpisuje je w ustawieniach (``AiGradingSettings.price_overrides``).
Model bez ceny ma koszt „nieznany” (``services.cost_of`` → ``None``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .providers.base import ModelInfo

ANTHROPIC_MODELS = (
    ModelInfo("claude-opus-5", "Claude Opus 5 (domyślny, dokładniejszy)", default=True),
    ModelInfo("claude-sonnet-5", "Claude Sonnet 5 (tańszy)"),
)
OPENAI_MODELS = (
    ModelInfo("gpt-6-astra", "GPT-6 Astra (domyślny, najdokładniejszy)", default=True),
    ModelInfo("gpt-6-sol", "GPT-6 Sol (tańszy)"),
    ModelInfo("gpt-6-luna", "GPT-6 Luna (najtańszy)"),
)
GOOGLE_MODELS = (
    ModelInfo("gemini-3.8-flash", "Gemini 3.8 Flash (domyślny, stabilny)", default=True),
    ModelInfo("gemini-3.1-pro-preview", "Gemini 3.1 Pro (wersja zapoznawcza)"),
    ModelInfo("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite (najtańszy)"),
)
META_MODELS = (
    ModelInfo("muse-spark-1.3", "Muse Spark 1.3 (domyślny)", default=True),
    ModelInfo("muse-spark-1.2", "Muse Spark 1.2 (poprzednia wersja)"),
)


@dataclass(frozen=True)
class Price:
    """Stawki w USD za milion tokenów."""

    input: Decimal
    output: Decimal


def _price(value_in: str, value_out: str) -> Price:
    return Price(Decimal(value_in), Decimal(value_out))


#: Cennik domyślny: (dostawca, model) → stawki. Służy **wyłącznie** szacunkom na ekranie
#: koordynatora – fakturę wystawia dostawca i to ona jest prawdą.
DEFAULT_PRICES: dict[tuple[str, str], Price] = {
    ("anthropic", "claude-opus-5"): _price("5", "25"),
    ("anthropic", "claude-sonnet-5"): _price("2", "10"),
    ("anthropic", "claude-opus-4-8"): _price("5", "25"),
    ("openai", "gpt-6-astra"): _price("10", "50"),
    ("openai", "gpt-6-sol"): _price("2", "10"),
    ("openai", "gpt-6-luna"): _price("0.10", "0.50"),
    ("google", "gemini-3.8-flash"): _price("0.75", "3.75"),
    ("google", "gemini-3.1-pro-preview"): _price("2", "12"),
    ("google", "gemini-3.5-flash-lite"): _price("0.30", "2.50"),
    ("meta", "muse-spark-1.3"): _price("1.25", "4.25"),
    ("meta", "muse-spark-1.2"): _price("1.25", "4.25"),
}
