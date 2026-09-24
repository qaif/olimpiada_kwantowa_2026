"""Rejestr dostawców modelu i dyspozytor wywołań.

Pakiet celowo **nie** importuje modułów dostawców przy imporcie samego siebie: ``client`` (moduł
Anthropic) sięga do ``providers.base`` po wspólne typy, a ``providers.anthropic`` – do ``client``.
Rejestr składa się przy pierwszym :func:`get`, gdy wszystkie moduły są już załadowane.
"""

from __future__ import annotations

from functools import cache

#: Kolejność na ekranach: najpierw dostawca, od którego funkcja się zaczęła.
PROVIDER_NAMES = ("anthropic", "openai", "google", "meta")


@cache
def registry() -> dict:
    from .anthropic import AnthropicProvider
    from .google import GoogleProvider
    from .meta import MetaProvider
    from .openai import OpenAIProvider

    items = (AnthropicProvider(), OpenAIProvider(), GoogleProvider(), MetaProvider())
    return {item.name: item for item in items}


def get(name: str):
    """Dostawca po nazwie. Nieznana nazwa to ``KeyError`` – wołający sprawdza ją wcześniej."""
    return registry()[name]


def all_providers() -> list:
    return [registry()[name] for name in PROVIDER_NAMES]


def call_model(api_key, request):
    """Jedno wywołanie modelu u dostawcy, dla którego żądanie zbudowano (``ProviderRequest``).

    Zwykły słownik bez atrybutu dostawcy to żądanie Anthropic – tak wyglądały wszystkie żądania
    przed dodaniem innych dostawców.
    """
    return get(getattr(request, "provider", "anthropic")).call(api_key, request)


def check_key(api_key, model: str, provider: str = "anthropic") -> tuple[bool, str]:
    return get(provider).check_key(api_key, model)
