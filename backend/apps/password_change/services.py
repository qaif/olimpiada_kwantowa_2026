"""Zmiana hasła przez zalogowanego właściciela konta (AUTH-01b) – wszystkie reguły w jednym miejscu.

Widok (``apps.password_change.views``) tylko zbiera pola i przypina błędy do formularza; o tym,
czy zmiana jest dozwolona, rozstrzyga ten moduł. Tak samo jak w ``apps.accounts.profile``:
gdyby kiedyś doszło ``POST /api/auth/password/``, endpoint zawoła te same funkcje i dostanie te same
odmowy, a nie kopię reguł z formularza HTML.

Najważniejsze decyzje (uzasadnienie w ``docs/tasks/AUTH-01b.md``):

- **aktualne hasło jest wymagane zawsze.** Sesja dowodzi, że ktoś się kiedyś zalogował, a nie że
  siedzi przy klawiaturze właściciel – porzucona sesja na szkolnym komputerze nie może zamienić
  się w trwałe przejęcie konta jednym POST-em. Hasło sprawdzamy **raz** na próbę (``check_password``
  to celowo kosztowny skrót), więc formularz go nie sprawdza – robi to wyłącznie ten serwis,
- **inne urządzenia są wylogowane zawsze**, nie na życzenie: Django wiąże sesję ze skrótem hasła
  (``get_session_auth_hash``), więc po zmianie każda inna sesja jest czyszczona przy swoim
  następnym żądaniu. Tokeny API skrótu hasła nie mają – kasujemy je jawnie, tak samo jak reset
  hasła (``apps.web.views.public.PasswordResetConfirmView``),
- **konto bez hasła platformy** (logowanie Google/Facebook) nie dostaje „ustaw hasło” w samej
  sesji – pierwsze hasło takiego konta ustawia się drogą potwierdzającą dostęp do skrzynki
  (``apps.accounts.services.register_social_participant``). Ten moduł wysyła mu więc zwykły list
  resetu hasła na **jego własny** adres (:func:`send_set_password_link`).

Audyt nie niesie żadnego sekretu: ani hasła, ani skrótu, ani adresu e-mail – kogo dotyczy wpis,
mówią ``actor`` i ``target_id``.
"""

from __future__ import annotations

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_variables
from rest_framework import status

from apps.accounts import reauth
from apps.accounts.password_reset import QueuedPasswordResetForm
from apps.core.api import DomainError
from apps.core.models import audit
from apps.web.views.public import PasswordResetView, service_name

from .notifications import send_password_changed_notice

#: Scope limitu POST-ów ekranu zmiany hasła. Stawka w ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``.
THROTTLE_SCOPE = "password_change"

#: Akcje audytu. Stałe, bo czytają je testy i (kiedyś) raport bezpieczeństwa koordynatora.
AUDIT_CHANGED = "password.changed"
AUDIT_FAILED = "password.change_failed"
AUDIT_SET_LINK_SENT = "password.set_link_sent"

#: Kody maszynowe odmów – widok przypina po nich błąd do właściwego pola formularza.
CODE_NO_PASSWORD = reauth.CODE_NO_PASSWORD
CODE_WRONG_CURRENT = reauth.CODE_WRONG
CODE_LOCKED = reauth.CODE_LOCKED
CODE_INVALID_NEW = "PASSWORD_INVALID"
CODE_UNCHANGED = "PASSWORD_UNCHANGED"
CODE_HAS_PASSWORD = "PASSWORD_ALREADY_SET"
CODE_INACTIVE = "ACCOUNT_INACTIVE"


def _revoke_api_tokens(user) -> int:
    """Kasuje tokeny API konta. Zwraca ich liczbę (do audytu – sama liczba, bez wartości)."""
    from rest_framework.authtoken.models import Token

    return Token.objects.filter(user=user).delete()[0]


@sensitive_variables("old_password", "new_password")
def change_password(user, *, old_password: str, new_password: str, request=None) -> None:
    """Zmienia hasło konta po sprawdzeniu aktualnego. Odmowa = ``DomainError`` z kodem maszynowym.

    Kolejność sprawdzeń jest celowa: najpierw to, co nie kosztuje (nowe różni się od starego),
    potem jeden kosztowny ``check_password`` (``apps.accounts.reauth`` – razem z licznikiem pomyłek
    w sesji i przepisaniem skrótu sesji po podniesieniu hasha), a walidatory na końcu – żeby ktoś,
    kto zgaduje aktualne hasło, nie dostawał przy okazji podpowiedzi o regułach nowego.

    ``request`` (gdy jest i należy do tego samego konta) zachowuje bieżącą sesję:
    ``update_session_auth_hash`` przepisuje skrót hasła w sesji i zmienia jej klucz – dane sesji,
    w tym znacznik przejścia drugiego składnika, zostają. Dzieje się to **przed** kolejkowaniem
    listu, więc awaria brokera nie może wylogować człowieka, któremu zmiana się udała (przegląd M1).
    """
    if user.has_usable_password() and old_password and new_password and old_password == new_password:
        # Porównanie napisów przed ``check_password``: odpowiedź nic nie zdradza (dotyczy tylko
        # tego, co człowiek sam wpisał w dwa pola), a oszczędza kosztowny skrót.
        raise DomainError(
            _("Nowe hasło musi się różnić od aktualnego."), CODE_UNCHANGED, status.HTTP_400_BAD_REQUEST
        )
    reauth.confirm_current_password(user, old_password, failed_action=AUDIT_FAILED, request=request)
    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        raise DomainError(" ".join(exc.messages), CODE_INVALID_NEW, status.HTTP_400_BAD_REQUEST) from exc

    changed_at = timezone.now()
    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=["password"])
        revoked = _revoke_api_tokens(user)
        audit(user, AUDIT_CHANGED, user, {"via": "account", "api_tokens_revoked": revoked}, request=request)
        if request is not None and getattr(getattr(request, "user", None), "pk", None) == user.pk:
            update_session_auth_hash(request, user)
        # List po commicie i odporny na awarię brokera (``notifications``): wycofana zmiana nie może
        # ogłosić właścicielowi, że hasło jest nowe, a niedziałający Redis – zamienić udanej zmiany w 500.
        send_password_changed_notice(user, request=request, changed_at=changed_at)


def send_set_password_link(user, *, request) -> None:
    """Wysyła kontu **bez hasła** zwykły list resetu hasła na jego własny adres (AUTH-01b § 5).

    Dlaczego nie publiczny formularz „Nie pamiętasz hasła?”: ``PasswordResetForm.get_users`` Django
    pomija konta bez używalnego hasła, więc tamten formularz takiemu kontu po cichu nic nie wysyła.
    Tutaj konto jest wskazane przez sesję, a adres – przez konto, więc nikt nie wpisuje adresu
    (formularz nie jest wyszukiwarką kont ani wysyłaczem listów na cudze skrzynki).

    List, token i ekran nowego hasła są **dokładnie** tymi z resetu (te same szablony, ten sam
    ``default_token_generator``, ``/reset/<uid>/<token>/`` z walidatorami i audytem
    ``password.reset``) – ten moduł niczego z resetu nie kopiuje, tylko wskazuje adresata.
    """
    if user.has_usable_password():
        raise DomainError(
            _("To konto ma już hasło – zmień je formularzem zmiany hasła."),
            CODE_HAS_PASSWORD,
            status.HTTP_400_BAD_REQUEST,
        )
    if not user.is_active or user.email_verified_at is None:
        # Konto zablokowane albo z niepotwierdzonym adresem nie powinno mieć sesji; gdyby jednak
        # miało (blokada w trakcie sesji), link do hasła nie może być furtką obok blokady.
        raise DomainError(
            _("Na to konto nie można teraz wysłać linku – skontaktuj się z organizatorem."),
            CODE_INACTIVE,
            status.HTTP_400_BAD_REQUEST,
        )

    form = _OwnAccountResetForm(user, data={"email": user.email})
    if form.is_valid():
        form.save(
            use_https=request.is_secure(),
            token_generator=default_token_generator,
            from_email=None,
            email_template_name=PasswordResetView.email_template_name,
            subject_template_name=PasswordResetView.subject_template_name,
            html_email_template_name=PasswordResetView.html_email_template_name,
            request=request,
            extra_email_context={"site_name": service_name(request)},
        )
    audit(user, AUDIT_SET_LINK_SENT, user, {}, request=request)


class _OwnAccountResetForm(QueuedPasswordResetForm):
    """``QueuedPasswordResetForm`` z jednym adresatem: kontem z sesji, i tylko wtedy, gdy nie ma hasła.

    Nadpisane jest wyłącznie ``get_users`` – reszta (token, kontekst, szablony, kolejka ``mail`` po
    commicie, język odbiorcy) to kod resetu bez zmian.
    """

    def __init__(self, account, *args, **kwargs):
        self.account = account
        super().__init__(*args, **kwargs)

    def get_users(self, email):
        account = self.account
        if account.is_active and not account.has_usable_password() and account.email == email.strip().lower():
            yield account
