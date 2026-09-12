"""Numer telefonu uczestnika: jeden kształt w bazie, niezależnie od tego, jak został wpisany.

Dlaczego normalizacja w ogóle: ten sam numer ludzie zapisują na kilka sposobów
(``600 000 000``, ``+48 600-000-000``, ``(48) 600 000 000``, ``0048600000000``). Koordynator
dzwoni w sprawach organizacyjnych i szuka numeru po fragmencie – gdyby w bazie leżało pięć
zapisów tego samego numeru, wyszukanie po ``600000000`` nie znalazłoby czterech z nich,
a lista uczestników z tego samego regionu przestałaby dać się posortować.

Dlaczego **nie** pełny E.164 z biblioteką (``phonenumbers``): zakres jest węższy niż globalna
poprawność numeru. Ma wystarczyć, żeby numer dał się wybrać z klawiatury telefonu i żeby dwa
zapisy tego samego numeru dawały jeden wiersz. Rozstrzyganie, czy ``+48 601`` jest istniejącym
prefiksem operatora, byłoby regułą, którą trzeba aktualizować razem z rynkiem – i która odbijałaby
poprawne numery zagraniczne uczestników szkół polskich za granicą.
"""

from __future__ import annotations

import re

from rest_framework import status

from apps.core.api import DomainError

#: Znaki, które wolno wpisać. Poza cyframi wyłącznie separatory, których ludzie faktycznie używają –
#: litery odpadają, bo „606 SZEŚĆ” nie jest numerem, a ``tel:`` z takim numerem nie zadzwoni.
ALLOWED_CHARACTERS = re.compile(r"^[0-9 +()-]+$")

#: Wszystko, co nie jest cyfrą ani wiodącym plusem, jest dla numeru szumem.
SEPARATORS = re.compile(r"[ ()-]")

#: Dolna granica: numery krótsze niż siedem cyfr to numery skrócone (infolinie, wewnętrzne),
#: pod które nie da się zadzwonić z zewnątrz. Górna: E.164 dopuszcza maksymalnie 15 cyfr,
#: dajemy zapas na zapis z zerem wiodącym i numerem wewnętrznym.
MIN_DIGITS = 7
MAX_DIGITS = 20

#: Długość polskiego numeru krajowego bez prefiksu. Dokładnie tyle cyfr znaczy „numer polski
#: wpisany tak, jak się go dyktuje” – i tylko wtedy wolno dołożyć prefiks za uczestnika.
POLISH_NATIONAL_DIGITS = 9
POLISH_PREFIX = "+48"

INVALID_MESSAGE = (
    f"Numer telefonu może zawierać tylko cyfry, spacje, znaki + - ( ) i od {MIN_DIGITS} do {MAX_DIGITS} cyfr."
)


def _invalid() -> DomainError:
    return DomainError(INVALID_MESSAGE, "PHONE_INVALID", status.HTTP_400_BAD_REQUEST)


def normalize_phone(value: str | None, *, required: bool = True) -> str:
    """Sprowadza wpisany numer do jednego kształtu albo podnosi ``DomainError``.

    Reguła jest **w serwisie**, a nie tylko w formularzu, bo wejść jest kilka (formularz WWW,
    ``POST /api/auth/register/participant/``, dokończenie rejestracji społecznościowej, edycja
    profilu) i każda z nich jest równie dobrą drogą do bazy.

    ``required=False`` przepuszcza pustą wartość jako ``""`` – tak wchodzą profile, w których
    numeru po prostu nie ma (konta sprzed wprowadzenia pola; edycja, która numeru nie rusza).
    """
    text = (value or "").strip()
    if not text:
        if required:
            raise DomainError("Numer telefonu jest wymagany.", "PHONE_REQUIRED", status.HTTP_400_BAD_REQUEST)
        return ""
    if not ALLOWED_CHARACTERS.fullmatch(text):
        raise _invalid()
    # Plus ma sens wyłącznie na początku: „600+000” nie jest numerem, tylko literówką.
    if "+" in text[1:]:
        raise _invalid()
    compact = SEPARATORS.sub("", text)
    # „00” to międzynarodowy prefiks wybierania – ten sam numer, inny zapis.
    if compact.startswith("00"):
        compact = f"+{compact[2:]}"
    digits = compact.lstrip("+")
    if not digits.isdigit():
        raise _invalid()
    if not MIN_DIGITS <= len(digits) <= MAX_DIGITS:
        raise _invalid()
    if not compact.startswith("+") and len(digits) == POLISH_NATIONAL_DIGITS:
        # Dziewięć cyfr bez prefiksu = numer krajowy. Prefiks dokładamy tylko tutaj: dla innych
        # długości „domyślenie się” kraju byłoby zgadywaniem, a zły prefiks to numer, pod którym
        # nikt nie odbiera.
        return f"{POLISH_PREFIX}{digits}"
    return compact
