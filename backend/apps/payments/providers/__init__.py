"""Rejestr dostawców płatności online. Przelew tradycyjny nie jest adapterem – zapisuje go człowiek."""

from __future__ import annotations

from .base import PaymentProviderBase, ProviderError, SignatureError
from .przelewy24 import Przelewy24Provider
from .stripe import StripeProvider

_PROVIDERS: dict[str, PaymentProviderBase] = {
    StripeProvider.code: StripeProvider(),
    Przelewy24Provider.code: Przelewy24Provider(),
}


def get_provider(code: str) -> PaymentProviderBase | None:
    return _PROVIDERS.get(code)


__all__ = ["PaymentProviderBase", "ProviderError", "SignatureError", "get_provider"]
