"""Adresy logowania przez dostawcę zewnętrznego – wycinek allauth, a nie całe ``allauth.urls``.

Czego tu **nie ma** i dlaczego to jest istotne:

- ``allauth.account.urls`` (m.in. ``/accounts/login/`` i ``/accounts/signup/``) – zamontowanie ich
  dałoby drugą ścieżkę logowania hasłem i drugą ścieżkę zakładania konta: obie poza naszym limitem
  prób (``apps.web.throttle``) i obie bez zgody RODO. Te adresy mają zwracać 404 i jest to
  sprawdzane testem (``apps/web/tests/test_social_login.py``),
- ``allauth.socialaccount.urls`` w całości – bo ten moduł montuje formularz rejestracji
  społecznościowej pod ``/accounts/signup/``, czyli dokładnie pod adresem, który ma być pusty.
  Bierzemy z niego tylko dwa widoki komunikatów, a **nazwę** ``socialaccount_signup`` dostaje nasz
  własny widok. allauth przekierowuje po nazwie (``headed_redirect_response``), nie po ścieżce,
  więc przepływ działa bez zmian, a adres jest nasz i po polsku.

Nazwy ``account_login`` i ``account_inactive`` istnieją, bo allauth używa ich w ``reverse()``
na ścieżkach awaryjnych (przerwany etap logowania, konto nieaktywne). Bez nich zamiast strony
z komunikatem dostalibyśmy ``NoReverseMatch``. ``account_login`` wskazuje na nasz formularz
logowania: wzorzec stoi **za** ``apps.web.urls``, więc ``/login/`` rozwiązuje się do ``web:login``,
a ten wpis służy wyłącznie do odwrotnego rozwiązywania nazwy.
"""

import functools

from allauth.socialaccount import views as allauth_views
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.facebook.provider import FacebookProvider
from allauth.socialaccount.providers.google.provider import GoogleProvider
from django.http import Http404
from django.urls import path
from django.utils.module_loading import import_string

from .views import public, social


def only_if_configured(view):
    """Adresy dostawcy bez kluczy w środowisku mają dawać 404, a nie 500.

    Wzorce są w urlconfie zawsze (dodawanie ich warunkowo znaczyłoby, że ``reverse`` przestaje
    działać w zależności od środowiska), ale allauth bez ``APPS`` w ustawieniach kończy szukanie
    aplikacji wyjątkiem ``SocialApp.DoesNotExist``. To jest publiczny adres: 500 zapełniałby log
    błędów przy pierwszym robocie, który przejdzie po linkach.
    """

    @functools.wraps(view)
    def guarded(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except SocialApp.DoesNotExist as exc:
            raise Http404("Ten dostawca logowania nie jest skonfigurowany.") from exc

    return guarded


def provider_paths(provider_class) -> list:
    """Dwa wzorce dostawcy: rozpoczęcie logowania i adres powrotny.

    Odpowiednik ``allauth…providers.oauth2.urls.default_urlpatterns``, ale bez ``login/token/``
    (logowanie tokenem z SDK w przeglądarce, np. Google One Tap). Tej ścieżki nie używamy, a każdy
    niepotrzebny endpoint przyjmujący poświadczenia jest powierzchnią ataku, której nikt nie testuje.
    Nazwy widoków i slug bierzemy z klasy dostawcy, więc wzorce nie rozjadą się z biblioteką.
    """
    package = provider_class.get_package()
    slug = provider_class.get_slug()
    return [
        path(
            f"accounts/{slug}/login/",
            only_if_configured(import_string(f"{package}.views.oauth2_login")),
            name=f"{provider_class.id}_login",
        ),
        path(
            f"accounts/{slug}/login/callback/",
            only_if_configured(import_string(f"{package}.views.oauth2_callback")),
            name=f"{provider_class.id}_callback",
        ),
    ]


urlpatterns = [
    # Dokończenie rejestracji po OAuth. Adres jest nasz; nazwa musi być ta, której szuka allauth.
    path("rejestracja/dokoncz/", social.SocialSignupView.as_view(), name="socialaccount_signup"),
    path("konto/nieaktywne/", social.AccountInactiveView.as_view(), name="account_inactive"),
    # Komunikaty allauth (anulowanie u dostawcy, błąd uścisku dłoni). Szablony są nadpisane
    # w ``templates/socialaccount/`` i rozszerzają ``base.html``.
    path(
        "accounts/login/cancelled/",
        allauth_views.login_cancelled,
        name="socialaccount_login_cancelled",
    ),
    path("accounts/login/error/", allauth_views.login_error, name="socialaccount_login_error"),
    # Wyłącznie do ``reverse("account_login")`` – patrz docstring modułu.
    path("login/", public.LoginView.as_view(), name="account_login"),
    # Uścisk dłoni: ``/accounts/<dostawca>/login/`` (POST) i ``/accounts/<dostawca>/login/callback/``.
    # Adres powrotny jest częścią konfiguracji aplikacji u dostawcy – patrz README § 4.4.
    *provider_paths(GoogleProvider),
    *provider_paths(FacebookProvider),
]
