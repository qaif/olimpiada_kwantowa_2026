"""Strony błędów z czytelnym wyjaśnieniem – w miejsce surowych komunikatów Django.

Najczęstszy z nich to 403 po nieudanej weryfikacji CSRF i prawie nigdy nie jest atakiem: Django
wymienia token CSRF przy każdym logowaniu (``rotate_token``), więc formularz otwarty w drugiej karcie
przed zalogowaniem się na inne konto – typowe przy testach „koordynator, potem członek komitetu”
w jednej przeglądarce – po wysłaniu ma już nieaktualny token. Domyślna strona Django mówi tylko
„Weryfikacja CSRF nie powiodła się” i odsyła do ``DEBUG=True``. Tutaj czytelnik dostaje powód
w jego języku i jedno działanie, które naprawdę pomaga: odświeżyć formularz i wysłać ponownie.

Sam mechanizm zostaje bez zmian – to nadal odmowa 403, a token nie jest „naprawiany” po cichu.
"""

from __future__ import annotations

from django.http import HttpResponseForbidden
from django.middleware.csrf import (
    REASON_BAD_ORIGIN,
    REASON_BAD_REFERER,
    REASON_CSRF_TOKEN_MISSING,
    REASON_INCORRECT_LENGTH,
    REASON_INVALID_CHARACTERS,
    REASON_MALFORMED_REFERER,
    REASON_NO_CSRF_COOKIE,
    REASON_NO_REFERER,
)
from django.template.loader import render_to_string

#: Wyjaśnienia dla człowieka, dobrane po powodzie odrzucenia z ``CsrfViewMiddleware``. Powody, których
#: tu nie ma (np. niezgodny token), dostają zdanie domyślne – to właśnie przypadek „zalogowano się
#: w innej karcie”.
HINTS = {
    REASON_NO_CSRF_COOKIE: (
        "Przeglądarka nie przyjęła pliku cookie zabezpieczającego formularze (csrftoken). Sprawdź, "
        "czy nie blokujesz plików cookie dla tej strony, i spróbuj ponownie."
    ),
    REASON_CSRF_TOKEN_MISSING: (
        "Formularz został wysłany bez tokenu zabezpieczającego – odśwież stronę i wyślij go ponownie."
    ),
    REASON_BAD_ORIGIN: (
        "Żądanie przyszło z innego adresu niż ten serwis. Jeśli otworzyłeś formularz z zapisanej "
        "kopii strony albo przez inny adres domeny, wejdź na stronę bezpośrednio."
    ),
    REASON_BAD_REFERER: (
        "Żądanie przyszło z innego adresu niż ten serwis – wejdź na stronę bezpośrednio i spróbuj ponownie."
    ),
    REASON_NO_REFERER: (
        "Przeglądarka ukryła adres strony, z której wysłano formularz (np. rozszerzenie chroniące "
        "prywatność). Zezwól na nagłówek Referer dla tej strony albo spróbuj w innej przeglądarce."
    ),
    REASON_MALFORMED_REFERER: (
        "Przeglądarka wysłała nieprawidłowy adres źródłowy – odśwież stronę i spróbuj ponownie."
    ),
    REASON_INCORRECT_LENGTH: "Token zabezpieczający jest uszkodzony – odśwież stronę i spróbuj ponownie.",
    REASON_INVALID_CHARACTERS: "Token zabezpieczający jest uszkodzony – odśwież stronę i spróbuj ponownie.",
}

DEFAULT_HINT = (
    "Najczęstsza przyczyna: w międzyczasie zalogowano się lub wylogowano w innej karcie tej samej "
    "przeglądarki albo strona z formularzem była otwarta bardzo długo. Zabezpieczenie formularza "
    "jest wtedy nieaktualne."
)


def csrf_failure(request, reason: str = "") -> HttpResponseForbidden:
    """Widok z ``CSRF_FAILURE_VIEW``: 403 z wyjaśnieniem i odnośnikiem do ponownego otwarcia formularza.

    Odnośnik „odśwież” prowadzi pod ten sam adres metodą GET – nie do ``Referer``, którego może
    nie być, i nie do strony głównej, która zmusiłaby do szukania formularza od nowa.
    """
    context = {
        "hint": HINTS.get(reason, DEFAULT_HINT),
        "retry_url": request.get_full_path(),
        "request": request,
    }
    return HttpResponseForbidden(render_to_string("403_csrf.html", context, request=request))
