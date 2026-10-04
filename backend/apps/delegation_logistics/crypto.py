"""Szyfrowanie danych wrażliwych logistyki finału w bazie (paszport, zdrowie, kontakt alarmowy).

Ten sam zabieg, co przy sekretach drugiego składnika (``apps.accounts.twofactor``) i kluczach API
(``apps.ai_grading.crypto``): Fernet z kluczem wyprowadzonym z ``SECRET_KEY`` z **własną etykietą**
celu. Etykieta nie jest ozdobą – wspólny klucz dla sekretów TOTP i numerów paszportów znaczyłby, że
pomyłka „odszyfruj nie to pole” w jednym module daje czytelną wartość w drugim.

Różnica wobec tamtych modułów jest jedna i ma powód: tu odczyt próbuje także kluczy z
``SECRET_KEY_FALLBACKS`` (``MultiFernet``). Sekret TOTP i klucz API da się wpisać ponownie, gdy
zmiana ``SECRET_KEY`` je unieważni; numeru paszportu ośmiu członków delegacji tydzień przed finałem
– nie. Rotacja klucza ma więc odbywać się tak, jak przewiduje Django: nowy klucz w ``SECRET_KEY``,
stary w ``SECRET_KEY_FALLBACKS`` do końca retencji (``docs/OPERACJE.md`` § 31). Zapis zawsze idzie
kluczem bieżącym, więc każda poprawka wiersza przepisuje go na nowy klucz.

Co to chroni, a czego nie: wyciek **samej bazy** (kopia zapasowa, zrzut do debugowania, replika)
nie odsłania paszportów ani danych o zdrowiu. Ktoś, kto ma jednocześnie bazę i ``SECRET_KEY``, ma
wszystko – przed nim chroni wyłącznie kontrola dostępu w serwisie.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

#: Etykieta celu w wyprowadzeniu klucza. Zmiana unieważnia wszystkie zapisane wartości – nie zmieniać.
KEY_LABEL = "delegation-logistics"

#: Przedrostek wartości zaszyfrowanej. Pozwala odróżnić szyfrogram od pustego pola bez próby
#: odszyfrowania i daje testom prosty sposób sprawdzenia, że w kolumnie nie leży jawny tekst.
PREFIX = "enc1:"


@lru_cache(maxsize=8)
def _fernet_for(secret_key: str):
    """Klucz Fernet z jednego sekretu – memoizowany po **wartości**, jak w ``twofactor``."""
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(f"{KEY_LABEL}:{secret_key}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _multi():
    """``MultiFernet``: szyfruje kluczem bieżącym, odszyfrowuje bieżącym albo którymś z fallbacków."""
    from cryptography.fernet import MultiFernet

    secrets = [settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", [])]
    return MultiFernet([_fernet_for(secret) for secret in secrets if secret])


def encrypt(plain: str) -> str:
    """Szyfrogram napisu albo pusty napis (puste pole zostaje puste – „brak danych” nie jest tajemnicą)."""
    if not plain:
        return ""
    return PREFIX + _multi().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Odszyfrowana wartość albo pusty napis, gdy się nie da (klucz zmieniony bez fallbacku).

    Pusty napis zamiast wyjątku z tego samego powodu, co w ``twofactor.decrypt_secret``: ekran ma
    pokazać „brak danych – uzupełnij”, a nie błąd 500 w tygodniu finału. Ślad zostaje w logu.
    Wartość bez przedrostka (wiersz zapisany obok serwisu, np. ręcznie w ``/admin/``) jest oddawana
    bez zmian – tak, żeby nie zginęła, a następny zapis przez serwis już ją zaszyfruje.
    """
    if not token:
        return ""
    if not token.startswith(PREFIX):
        return token
    from cryptography.fernet import InvalidToken

    try:
        return _multi().decrypt(token[len(PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken, ValueError, UnicodeDecodeError:
        logger.warning("Logistyka finału: nie udało się odszyfrować pola (zmiana SECRET_KEY bez fallbacku?).")
        return ""


class EncryptedTextField(models.TextField):
    """Pole tekstowe szyfrowane w bazie, przezroczyste dla kodu (``str`` w obie strony).

    Po polu zaszyfrowanym nie da się filtrować ani sortować w SQL-u – i to jest zamierzone: żadne
    zestawienie nie potrzebuje wyszukiwania po numerze paszportu, a zestawienia po diecie czy dacie
    urodzenia liczą się w Pythonie na kilkuset wierszach jednej edycji.
    """

    description = "tekst szyfrowany (Fernet)"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return decrypt(value)

    def to_python(self, value):
        if value is None or not isinstance(value, str):
            return value
        return decrypt(value) if value.startswith(PREFIX) else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None:
            return value
        return encrypt(str(value))
