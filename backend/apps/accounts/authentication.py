"""Uwierzytelnienie tokenem API, które nie omija drugiego składnika logowania.

``TwoFactorMiddleware`` (``apps.accounts.twofactor``) pilnuje **sesji**: żądanie z nagłówkiem
``Authorization: Token …`` dochodzi do niej jako anonimowe, bo DRF uwierzytelnia dopiero w widoku.
Token wydany po samym haśle otwierałby więc całe ``/api/`` kontu, któremu przeglądarka każe podać
kod z aplikacji – a obietnica drugiego składnika brzmi „wyciekło hasło i nic się nie stało”.

Dwie reguły, obie wyłącznie przy włączonej funkcji (``TWO_FACTOR_ENABLED``):

- konto, od którego organizator **wymaga** drugiego składnika (``TWO_FACTOR_REQUIRED_ROLES``),
  a które go jeszcze nie skonfigurowało, nie uwierzytelnia się tokenem wcale – konfiguracja jest
  w przeglądarce, tak samo jak dla sesji,
- konto z potwierdzonym urządzeniem uwierzytelnia się wyłącznie tokenem wydanym **po** potwierdzeniu
  urządzenia. Taki token powstaje tylko w ``POST /api/auth/login/`` z poprawnym kodem (widok wydaje
  wtedy nowy token), a włączenie drugiego składnika kasuje tokeny wydane wcześniej
  (``twofactor.confirm_setup``) – porównanie dat jest drugą zaporą dla tokenu, który powstałby inną
  drogą (panel ``/admin/``, powłoka).

Przy wyłączonej funkcji klasa nie robi ani jednego dodatkowego zapytania.
"""

from __future__ import annotations

from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

from . import twofactor

TWO_FACTOR_TOKEN_MESSAGE = (
    "Token nie obejmuje drugiego składnika logowania. Zaloguj się ponownie, podając kod z aplikacji."
)


class TwoFactorTokenAuthentication(TokenAuthentication):
    """``TokenAuthentication`` DRF z regułami drugiego składnika – patrz docstring modułu."""

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if not twofactor.is_enabled():
            return user, token
        device = twofactor.confirmed_device(user)
        if device is None:
            if twofactor.is_required_for(user):
                raise AuthenticationFailed(TWO_FACTOR_TOKEN_MESSAGE)
            return user, token
        if token.created < device.confirmed_at:
            raise AuthenticationFailed(TWO_FACTOR_TOKEN_MESSAGE)
        return user, token
