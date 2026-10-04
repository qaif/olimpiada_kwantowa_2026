"""Ponowne potwierdzenie tożsamości aktualnym hasłem przed zmianą poświadczeń (AUTH-01b, przegląd H1/L1/L3).

Zalogowana sesja dowodzi, że ktoś się **kiedyś** zalogował – nie, że przy klawiaturze siedzi
właściciel. Operacje, które przejmują konto na stałe (zmiana hasła, zmiana adresu e-mail – adres
jest loginem i drogą resetu), żądają więc aktualnego hasła. Jedna funkcja dla wszystkich takich
ekranów, żeby reguły nie rozjechały się między nimi:

- **jedno** ``check_password`` na próbę (skrót jest celowo kosztowny),
- **podniesienie skrótu** (``check_password`` przepisuje hash po zmianie ``PASSWORD_HASHERS``
  i zapisuje go od razu) zmienia skrót sesji – bez ``update_session_auth_hash`` bieżąca sesja
  wylogowałaby się przy następnym żądaniu, choć hasło było poprawne, a odmowa dotyczyła np. słabego
  nowego hasła. Przepisujemy go tu, zanim wołający podniesie jakikolwiek inny błąd (przegląd L1),
- **licznik kolejnych pomyłek w sesji** wspólny dla wszystkich ekranów: po
  :data:`MAX_CONSECUTIVE_FAILURES` sesja jest kończona (``logout``), więc dalsze zgadywanie wymaga
  ponownego logowania – a tam stoi limit prób logowania i drugi składnik (przegląd L3). Limit
  POST-ów per konto (``apps.web.throttle.PerAccountThrottleMixin``) liczy osobno, w czasie,
- **audyt** każdej pomyłki pod akcją wołającego, bez wpisanego tekstu.

Konto bez używalnego hasła (logowanie Google/Facebook) nie ma czym się potwierdzić – dostaje odmowę
``PASSWORD_NOT_SET``, a ustawia hasło linkiem na własny adres (``/account/password/``).
"""

from __future__ import annotations

from django.contrib.auth import logout, update_session_auth_hash
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_variables
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit

#: Ile kolejnych pomyłek w jednej sesji kończy tę sesję.
MAX_CONSECUTIVE_FAILURES = 5
#: Klucz licznika w sesji. Sesja, a nie cache: licznik ma zniknąć razem z sesją, którą chroni.
SESSION_FAILURES_KEY = "reauth_failures"

CODE_NO_PASSWORD = "PASSWORD_NOT_SET"
CODE_WRONG = "PASSWORD_INCORRECT"
CODE_LOCKED = "REAUTH_LOCKED"


def _own_session(request, user) -> bool:
    return (
        request is not None
        and hasattr(request, "session")
        and getattr(getattr(request, "user", None), "pk", None) == user.pk
    )


@sensitive_variables("password")
def confirm_current_password(user, password: str, *, failed_action: str, request=None) -> None:
    """Sprawdza aktualne hasło konta. Odmowa = ``DomainError`` z kodem ``CODE_*`` tego modułu.

    ``failed_action`` – nazwa akcji audytu nieudanej próby (``password.change_failed``,
    ``account.email_change_failed``). Przy ``REAUTH_LOCKED`` sesja żądania jest już zakończona –
    wołający odsyła do logowania.
    """
    if not user.has_usable_password():
        raise DomainError(
            _("To konto nie ma jeszcze hasła – ustaw je linkiem wysłanym na adres konta."),
            CODE_NO_PASSWORD,
            status.HTTP_400_BAD_REQUEST,
        )
    hash_before = user.password
    valid = user.check_password(password or "")
    own_session = _own_session(request, user)
    if valid:
        if own_session:
            if user.password != hash_before:
                update_session_auth_hash(request, user)
            request.session.pop(SESSION_FAILURES_KEY, None)
        return

    failures = 1
    if own_session:
        failures = int(request.session.get(SESSION_FAILURES_KEY, 0)) + 1
        request.session[SESSION_FAILURES_KEY] = failures
    locked = own_session and failures >= MAX_CONSECUTIVE_FAILURES
    audit(
        user,
        failed_action,
        user,
        {"reason": "wrong_current", "consecutive": failures, "session_ended": locked},
        request=request,
    )
    if locked:
        logout(request)
        raise DomainError(
            _(
                "Zbyt wiele błędnych haseł z rzędu – ze względów bezpieczeństwa wylogowaliśmy Cię. "
                "Zaloguj się ponownie."
            ),
            CODE_LOCKED,
            status.HTTP_403_FORBIDDEN,
        )
    raise DomainError(_("Aktualne hasło jest nieprawidłowe."), CODE_WRONG, status.HTTP_400_BAD_REQUEST)
