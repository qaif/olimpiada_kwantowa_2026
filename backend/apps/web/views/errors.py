"""Strony błędów z czytelnym wyjaśnieniem – w miejsce surowych komunikatów Django.

Dwa widoki o dwóch różnych ciężarach. ``csrf_failure`` chodzi po pełnym szablonie i wolno mu
wszystko; ``server_error`` renderuje stronę, która stoi **po** awarii, więc nie wolno jej dotknąć
bazy ani sesji – jedyne, co do niej wchodzi, to adres kontaktowy z ustawień (§ 1.1.4, D14).

Najczęstszy z nich to 403 po nieudanej weryfikacji CSRF i prawie nigdy nie jest atakiem: Django
wymienia token CSRF przy każdym logowaniu (``rotate_token``), więc formularz otwarty w drugiej karcie
przed zalogowaniem się na inne konto – typowe przy testach „koordynator, potem członek komitetu”
w jednej przeglądarce – po wysłaniu ma już nieaktualny token. Domyślna strona Django mówi tylko
„Weryfikacja CSRF nie powiodła się” i odsyła do ``DEBUG=True``. Tutaj czytelnik dostaje powód
w jego języku i jedno działanie, które naprawdę pomaga: odświeżyć formularz i wysłać ponownie.

Sam mechanizm zostaje bez zmian – to nadal odmowa 403, a token nie jest „naprawiany” po cichu.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponseServerError
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


def server_error(request, template_name: str = "500.html") -> HttpResponseServerError:
    """Widok z ``handler500``: ta sama strona, co dotąd, plus adres kontaktowy z ustawień.

    Po co własny widok, skoro Django ma ``django.views.defaults.server_error``: bo tamten renderuje
    ``500.html`` **bez kontekstu** i adres organizatora musiałby zostać wpisany w szablon. Adres
    jest konfiguracją instalacji (``ERROR_PAGE_CONTACT_EMAIL``, decyzja organizatora D14), a nie
    treścią – operator, który stawia platformę dla swojego konkursu, ma podać własny adres
    w ``.env``, a nie poprawiać plik w repozytorium.

    Czego ten widok **nie** robi i dlaczego: nie przekazuje ``request=`` do ``render_to_string``.
    Przekazanie uruchomiłoby procesory kontekstu, czyli sesję, konto i ustawienia witryny – a to
    jest strona, którą oglądamy dokładnie wtedy, gdy coś z tych rzeczy właśnie się wywróciło.
    Sens ``500.html`` polega na tym, że renderuje się bez bazy (komentarz w szablonie) i ten widok
    tego nie zmienia: ``settings`` są w pamięci procesu.
    """
    context = {"contact_email": settings.ERROR_PAGE_CONTACT_EMAIL}
    return HttpResponseServerError(render_to_string(template_name, context))
