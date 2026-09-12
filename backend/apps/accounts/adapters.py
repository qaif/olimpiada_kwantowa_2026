"""Adaptery django-allauth: co wolno zrobić loginowi z Google/Facebooka, a czego nie.

Podział ról jest tu ostry i celowy:

- **allauth robi wyłącznie uścisk dłoni OAuth2** (przekierowanie do dostawcy, ``state``, PKCE,
  wymiana kodu na token, odczyt profilu) oraz trzyma powiązanie ``SocialAccount``,
- **konto zakłada nasz kod** – ``apps.web.views.social.SocialSignupView`` woła
  ``apps.accounts.services.register_social_participant``. Dzięki temu żaden ``User`` nie powstaje
  przed zebraniem zgody RODO: allauth ma ``SOCIALACCOUNT_AUTO_SIGNUP = False``, więc po udanym
  logowaniu bez dopasowanego konta odsyła na nasz formularz, a nie tworzy wiersza w bazie.

Trzy decyzje bezpieczeństwa, które mieszkają w ``SocialAccountAdapter.pre_social_login``:

1. **konto nieaktywne nie loguje się przez OAuth.** Odmowa zapada, zanim allauth cokolwiek zapisze:
   jego własna kontrola ``is_active`` jest dopiero w ``pre_login``, czyli **po** ewentualnym
   powiązaniu konta i po wyczyszczeniu hasła (patrz punkt 2). Blokada musi być wcześniej,
2. **automatyczne łączenie po adresie e-mail wyłącznie z dostawcą, któremu ufamy** (Google,
   i tylko dla adresu z ``email_verified``). Facebook ma ``EMAIL_AUTHENTICATION: False``
   i ``VERIFIED_EMAIL: False``, więc jego adres nigdy nie przejmie istniejącego konta. Kiedy
   dostawca podaje adres zajęty przez konto, którego nie wolno połączyć, kończymy jawną stroną
   („konto istnieje – zaloguj się hasłem albo zresetuj hasło”), a nie formularzem rejestracji,
   który i tak odbiłby się o ``EMAIL_TAKEN``,
3. **brak adresu e-mail od dostawcy = brak logowania.** E-mail jest u nas loginem i jedyną drogą
   odzyskania konta; bez niego nie ma czego założyć ani z czym połączyć.

Uwaga o czyszczeniu hasła (zachowanie allauth, świadomie zostawione): przy łączeniu po adresie
e-mail allauth wywołuje ``wipe_password`` dla konta, którego adres nie jest u nas potwierdzony.
To jest właściwa reakcja przy naszym modelu rejestracji (nie weryfikujemy adresu przy zakładaniu
konta hasłem, więc ktoś mógł założyć konto na cudzy adres i czekać na właściciela). Skutek dla
właściciela konta: po pierwszym logowaniu Google'em hasło przestaje działać i ustawia je na nowo
przez „Nie pamiętasz hasła?”. Fakt trafia do audytu (``login.social_connect``).
"""

from __future__ import annotations

from http import HTTPStatus

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import render

from apps.core.models import audit

from .models import User

#: Szablon strony odmowy. Jeden dla wszystkich powodów – treść wybiera ``reason``, a szczegóły
#: techniczne (nazwa dostawcy w błędzie, kod błędu OAuth) nie trafiają do przeglądarki.
REFUSAL_TEMPLATE = "socialaccount/refused.html"

#: Powody odmowy. Wartości są nazwami wariantów w szablonie, nie komunikatami.
REASON_INACTIVE = "inactive"
REASON_EMAIL_TAKEN = "email_taken"
REASON_NO_EMAIL = "no_email"


def provider_label(sociallogin) -> str:
    """Czytelna nazwa dostawcy („Google”, „Facebook”) do treści strony."""
    provider = getattr(sociallogin, "provider", None)
    return getattr(provider, "name", "") or sociallogin.account.provider


def provider_id(sociallogin) -> str:
    """Identyfikator dostawcy do audytu (``google``/``facebook``). Nie jest daną osobową."""
    return sociallogin.account.provider


def sociallogin_email(sociallogin) -> str:
    """Adres e-mail z loginu społecznościowego, znormalizowany jak w ``accounts.User.save``.

    Kolejność jest ta, którą ustawia allauth (``cleanup_email_addresses``): najpierw adres
    główny/potwierdzony, potem to, co dostawca wpisał do profilu.
    """
    for address in sociallogin.email_addresses:
        if address.email:
            return address.email.strip().lower()
    user = getattr(sociallogin, "user", None)
    return (getattr(user, "email", "") or "").strip().lower()


def sociallogin_email_verified(sociallogin) -> bool:
    """Czy **dostawca** potwierdził adres, którym zakładamy konto.

    Rozstrzyga to o jednej rzeczy: czy konto powstaje aktywne, czy przechodzi przez nasz link
    aktywacyjny (``apps.accounts.activation``). Google podaje ``email_verified`` i wtedy drugie
    potwierdzenie tego samego adresu naszym listem byłoby pytaniem o coś, co już wiemy. Facebook
    nie potwierdza adresu wcale (``VERIFIED_EMAIL: False`` w ``config/settings/base.py``), więc jego
    konto przechodzi aktywację – inaczej wystarczyłoby wpisać cudzy adres w profilu Facebooka, żeby
    dostać w naszym serwisie konto podpisane tym adresem.

    Odpowiedź czytamy z ``sociallogin.email_addresses``, czyli z tego, co allauth wyliczył
    z odpowiedzi dostawcy – a nie z konfiguracji naszego serwisu. Porównanie po adresie, bo
    dostawca może podać kilka adresów i potwierdzony bywa inny niż ten, którym zakładamy konto.
    """
    chosen = sociallogin_email(sociallogin)
    return any(
        bool(getattr(address, "verified", False))
        for address in sociallogin.email_addresses
        if (address.email or "").strip().lower() == chosen
    )


def refuse(request, reason: str, sociallogin) -> ImmediateHttpResponse:
    """Buduje wyjątek przerywający logowanie stroną w naszym stylu (HTTP 401)."""
    response = render(
        request,
        REFUSAL_TEMPLATE,
        {"reason": reason, "provider_name": provider_label(sociallogin)},
        status=HTTPStatus.UNAUTHORIZED,
    )
    return ImmediateHttpResponse(response)


class AccountAdapter(DefaultAccountAdapter):
    """Adapter „lokalnej” części allauth. Jego zadaniem jest **nie dopuścić** do drugiej ścieżki kont.

    Rejestracja hasłem i logowanie hasłem mają u nas jedną drogę – ``apps.web.views.public`` – bo
    tylko tam działa limit prób (``apps.web.throttle``) i tylko tam zbieramy zgodę RODO. Widoki
    allauth nie są zamontowane w urlconfie, a ``is_open_for_signup`` domyka to od strony logiki:
    gdyby ktoś kiedyś dopisał ``allauth.account.urls``, rejestracja i tak zostanie zamknięta.
    """

    def is_open_for_signup(self, request) -> bool:
        return False

    def get_login_redirect_url(self, request) -> str:
        from apps.web.views.public import default_panel_url

        return default_panel_url(request)

    def get_signup_redirect_url(self, request) -> str:
        from apps.web.views.public import default_panel_url

        return default_panel_url(request)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Adapter logowania przez dostawcę. Cała polityka wpuszczania jest w ``pre_social_login``."""

    def is_open_for_signup(self, request, sociallogin) -> bool:
        """Rejestracja **społecznościowa** jest otwarta, mimo zamkniętej rejestracji lokalnej.

        Bez tego nadpisania allauth pytałby ``AccountAdapter.is_open_for_signup`` (czyli ``False``)
        i każdy nowy użytkownik Google'a dostawałby „rejestracja zamknięta”.
        """
        return True

    def save_user(self, request, sociallogin, form=None):
        """Nieużywane – konto zakłada ``apps.accounts.services.register_social_participant``.

        Metoda zostaje jako bezpiecznik: gdyby ktoś włączył ``SOCIALACCOUNT_AUTO_SIGNUP``, allauth
        utworzyłby konto tą drogą, czyli **bez zgody RODO i bez profilu uczestnika**. Wyjątek jest
        głośniejszy niż cicho powstałe konto bez zgody.
        """
        raise ImproperlyConfigured(
            "Konto społecznościowe zakłada apps.accounts.services.register_social_participant "
            "(formularz z wymaganą zgodą RODO). Automatyczna rejestracja allauth jest wyłączona."
        )

    def pre_social_login(self, request, sociallogin) -> None:
        email = sociallogin_email(sociallogin)
        if sociallogin.is_existing:
            self._check_existing(request, sociallogin, email)
            return
        if not email:
            raise refuse(request, REASON_NO_EMAIL, sociallogin)
        if User.objects.filter(email=email).exists():
            # Dostawca podał adres zajęty przez konto, z którym nie wolno się połączyć automatycznie
            # (Facebook zawsze; Google, gdy adres nie jest u niego potwierdzony). Gdyby przepuścić
            # to dalej, użytkownik wypełniłby cały formularz rejestracji i dopiero na końcu dostał
            # „konto z tym adresem już istnieje”.
            raise refuse(request, REASON_EMAIL_TAKEN, sociallogin)

    def _check_existing(self, request, sociallogin, email: str) -> None:
        """Konto już jest: albo powiązane z dostawcą, albo dopasowane po zweryfikowanym adresie."""
        user = sociallogin.user
        if not user.is_active:
            raise refuse(request, REASON_INACTIVE, sociallogin)
        matched_by_email = getattr(sociallogin, "_did_authenticate_by_email", None)
        if not matched_by_email:
            return
        # Zaraz po powrocie z tego haka allauth zapisze powiązanie ``SocialAccount`` (auto-connect)
        # i – dla konta z niepotwierdzonym u nas adresem – wyczyści hasło. Jedno i drugie jest
        # zmianą sposobu logowania do konta, więc musi zostawić ślad. W ``diff`` nie ma adresu
        # e-mail ani nazwiska: kogo dotyczy wpis, mówi ``actor``/``target_id``.
        password_wiped = user.has_usable_password() and not (
            EmailAddress.objects.filter(user=user, email__iexact=matched_by_email, verified=True).exists()
        )
        audit(
            user,
            "login.social_connect",
            user,
            {"provider": provider_id(sociallogin), "password_wiped": password_wiped},
            request=request,
        )
