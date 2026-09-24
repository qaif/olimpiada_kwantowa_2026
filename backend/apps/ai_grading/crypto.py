"""Klucze API dostawców modelu: szyfrowanie w bazie, maskowanie na ekranie i opakowanie w pamięci.

Szyfrowanie jest tym samym zabiegiem, co przy sekretach drugiego składnika
(``apps.accounts.twofactor._fernet_for``): Fernet z kluczem wyprowadzonym z ``SECRET_KEY``,
z **własną etykietą** celu. Etykieta nie jest ozdobą – ten sam klucz Fernet dla sekretów TOTP
i dla klucza API znaczyłby, że błąd w jednym miejscu (np. odszyfrowanie „nie tego pola”) daje
czytelną wartość w drugim. Konsekwencje są też te same i trzeba je znać: szyfrowanie chroni przed
wyciekiem samej bazy (kopia zapasowa, zrzut do debugowania), a nie przed kimś, kto ma jednocześnie
bazę i ``SECRET_KEY``; **zmiana ``SECRET_KEY`` unieważnia zapisany klucz** – koordynator widzi
wtedy komunikat „wpisz klucz ponownie”, a nie błąd 500.

:class:`ApiKey` istnieje po to, żeby odszyfrowana wartość nie leżała w zwykłej zmiennej typu
``str``. Zwykły napis ląduje w ``repr`` ramki stosu – w logu z ``logger.exception``, w raporcie
błędu Django przy ``DEBUG`` i w każdym narzędziu, które zbiera zmienne lokalne z tracebacku.
Opakowanie przedstawia się jako ``ApiKey(…abcd)``, a wartość oddaje wyłącznie jawnym
``reveal()`` wołanym w argumencie konstruktora klienta – nigdzie jej nie przypisujemy.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

#: Klucze API Anthropic zaczynają się od tego przedrostka. Sprawdzenie jest **uprzejmością**:
#: odróżnia wklejony klucz od wklejonego przez pomyłkę hasła albo adresu, a o ważności klucza
#: rozstrzyga dopiero „Sprawdź klucz”.
KEY_PREFIX = "sk-ant-"
#: Klucz administracyjny organizacji nie woła Messages API – przyjęcie go kończyłoby się błędem
#: autoryzacji przy pierwszej pracy, a przy okazji zostawiało w bazie klucz o szerszych prawach
#: niż potrzebne.
ADMIN_KEY_PREFIX = "sk-ant-admin"
MIN_KEY_LENGTH = 20
MAX_KEY_LENGTH = 400


class InvalidApiKey(ValueError):
    """Wklejona wartość na pewno nie jest kluczem API do Messages API. Komunikat jest dla człowieka."""


@lru_cache(maxsize=4)
def _fernet_for(secret_key: str):
    """Klucz Fernet z ``SECRET_KEY`` – memoizowany po **wartości**, jak w ``twofactor``."""
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(f"ai-grading-api-key:{secret_key}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def normalise_key(raw: str) -> str:
    """Klucz po zdjęciu białych znaków albo :class:`InvalidApiKey`.

    Białe znaki zdejmujemy, bo klucz wkleja się z konsoli Anthropic i z maila – spacja albo
    znak nowej linii na końcu to najczęstszy powód „klucz nie działa”, a w samym kluczu białych
    znaków nie ma. Komunikaty błędów **nie cytują** wklejonej wartości: to mogło być cudze hasło.
    """
    value = "".join((raw or "").split())
    if not value:
        raise InvalidApiKey("Wklej klucz API.")
    if value.startswith(ADMIN_KEY_PREFIX):
        raise InvalidApiKey(
            "To jest klucz administracyjny organizacji – potrzebny jest zwykły klucz API "
            "(Console Anthropic → API Keys)."
        )
    if not value.startswith(KEY_PREFIX):
        raise InvalidApiKey(f"Klucz API Anthropic zaczyna się od „{KEY_PREFIX}”.")
    if not (MIN_KEY_LENGTH <= len(value) <= MAX_KEY_LENGTH) or not value.isascii():
        raise InvalidApiKey("To nie wygląda na klucz API Anthropic – sprawdź, czy wkleiłeś go w całości.")
    return value


def generic_key(raw: str, label: str) -> str:
    """Klucz dostawcy bez własnego przedrostka – sprawdzenie „czy to w ogóle wygląda na klucz”.

    Te same zasady, co w :func:`normalise_key`: białe znaki zdejmujemy (klucz wkleja się z konsoli
    i z maila), wartości **nie cytujemy** w komunikacie, a o ważności rozstrzyga „Sprawdź klucz”.
    Przedrostków OpenAI, Google i Meta nie sprawdzamy ściśle: zmieniały się już kilka razy
    (``sk-``, ``sk-proj-``, ``AIza…``), a odrzucenie działającego klucza z powodu nowego formatu
    byłoby gorsze niż przyjęcie śmieci, które „Sprawdź klucz” i tak od razu wskaże.
    """
    value = "".join((raw or "").split())
    if not value:
        raise InvalidApiKey("Wklej klucz API.")
    if not (MIN_KEY_LENGTH <= len(value) <= MAX_KEY_LENGTH) or not value.isascii():
        raise InvalidApiKey(f"To nie wygląda na klucz API {label} – sprawdź, czy wkleiłeś go w całości.")
    return value


def encrypt_key(plain: str) -> str:
    return _fernet_for(settings.SECRET_KEY).encrypt(plain.encode("ascii")).decode("ascii")


def last4(plain: str) -> str:
    return plain[-4:]


class ApiKey:
    """Odszyfrowany klucz w pamięci. ``repr``/``str`` nigdy nie oddają wartości."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"ApiKey(…{self._value[-4:]})"

    __str__ = __repr__

    def __reduce__(self):  # pragma: no cover - obrona przed przypadkowym pickle (Celery, cache)
        raise TypeError("Klucza API nie serializujemy – zadanie czyta go z bazy samo.")


def decrypt_key(token: str) -> ApiKey | None:
    """Klucz z bazy albo ``None``, gdy się nie da (zmieniony ``SECRET_KEY``, uszkodzony wpis).

    ``None`` zamiast wyjątku z tego samego powodu, co w ``twofactor.decrypt_secret``: wołający
    ma na to sensowną odpowiedź („wpisz klucz ponownie”), a wyjątek byłby błędem 500.
    """
    if not token:
        return None
    from cryptography.fernet import InvalidToken

    try:
        return ApiKey(_fernet_for(settings.SECRET_KEY).decrypt(token.encode("ascii")).decode("ascii"))
    except (InvalidToken, ValueError, UnicodeDecodeError):
        logger.warning("Ocena AI: nie udało się odszyfrować klucza API (zmiana SECRET_KEY?).")
        return None
