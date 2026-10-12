"""Wspólne zasady budowy paczek ZIP: osobny katalog roboczy i limit rozmiaru (audyt 10.10.2026, S15).

Paczki (eksport danych konta, paczka prac etapu, paczka recenzenta, dyplomy) powstają w pliku
tymczasowym, który ``FileResponse`` zamyka – i tym samym kasuje – po wysłaniu. Do audytu ten plik
lądował w ``/tmp``: w kontenerze to tmpfs 256 MB **wspólny z wgrywaniem plików** (Django odkłada
tam każdy upload powyżej 2 MB). Jedna duża paczka zapełniała go do końca, a w tym czasie każda
cudza praca > 2 MB kończyła się błędem 500. Uczestnik mógł to wywołać sam: kilkanaście wersji po
20 MB i eksport konta.

Stąd dwie reguły, jedna dla wszystkich paczek:

- **osobny katalog** (:func:`package_tempfile`, ustawienie ``PACKAGE_TMP_DIR``) – produkcyjnie
  wolumen dyskowy, a nie tmpfs uploadów. Pełny katalog paczek nie blokuje już przyjmowania prac,
- **limit sumy rozmiarów przed budową** (:func:`ensure_package_fits`, ``PACKAGE_MAX_BYTES``) –
  liczony z ``size_bytes`` zapisanych przy wgraniu, czyli bez czytania storage. Paczka ponad limit
  jest odrzucana z komunikatem, który mówi, co zrobić (zawęzić zakres), zamiast urwać się
  w połowie na ``ENOSPC``.

:class:`PackageTooLarge` jest ``DomainError``-em: widoki paczek już dziś zamieniają błąd domenowy
na stronę z powodem, a API – na ``{"code", "detail"}``, więc odmowa nie wymaga nowej obsługi.
"""

from __future__ import annotations

import os
import tempfile
from typing import BinaryIO

from django.conf import settings
from rest_framework import status

from apps.core.api import DomainError

MEGABYTE = 1024 * 1024

#: Wartość domyślna, gdy ustawienia nie ma (np. ustawienia sprzed tej zmiany w testach).
DEFAULT_PACKAGE_MAX_BYTES = 200 * MEGABYTE


class PackageTooLarge(DomainError):
    """Paczka przekroczyłaby ``PACKAGE_MAX_BYTES`` – odmowa przed budową."""

    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_code = "PACKAGE_TOO_LARGE"
    default_detail = "Paczka jest za duża."

    def __init__(self, total_bytes: int, limit_bytes: int, hint: str = ""):
        self.total_bytes = total_bytes
        self.limit_bytes = limit_bytes
        detail = (
            f"Paczka miałaby ok. {_megabytes(total_bytes)} MB, a jednorazowo można pobrać "
            f"najwyżej {_megabytes(limit_bytes)} MB."
        )
        if hint:
            detail = f"{detail} {hint}"
        super().__init__(detail)


def _megabytes(value: int) -> int:
    """Zaokrąglenie w górę: „ok. 201 MB” przy limicie 200 MB, a nie „200 MB, limit 200 MB”."""
    return -(-int(value) // MEGABYTE)


def package_max_bytes() -> int:
    return int(getattr(settings, "PACKAGE_MAX_BYTES", DEFAULT_PACKAGE_MAX_BYTES))


def ensure_package_fits(total_bytes: int, *, hint: str = "") -> None:
    """Rzuca :class:`PackageTooLarge`, gdy suma rozmiarów plików przekracza limit paczki."""
    limit = package_max_bytes()
    if int(total_bytes) > limit:
        raise PackageTooLarge(int(total_bytes), limit, hint)


def package_tmp_dir() -> str:
    """Katalog roboczy paczek; zakładany przy pierwszym użyciu (wolumen bywa pusty po starcie)."""
    directory = str(getattr(settings, "PACKAGE_TMP_DIR", "") or tempfile.gettempdir())
    os.makedirs(directory, exist_ok=True)
    return directory


def package_tempfile() -> BinaryIO:
    """Anonimowy plik tymczasowy w katalogu paczek – znika przy zamknięciu, także po zerwaniu połączenia."""
    return tempfile.TemporaryFile(dir=package_tmp_dir())
