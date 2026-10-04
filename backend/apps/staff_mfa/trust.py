"""„Zapamiętaj to urządzenie” – podpisane, krótkotrwałe ciasteczko zamiast kodu (SEC-01 § 4).

Ciasteczko nie jest sekretem drugiego składnika, tylko **zaświadczeniem**, że w tej przeglądarce
ktoś niedawno podał poprawny kod. Dlatego trzyma wyłącznie:

- identyfikator konta (zaświadczenie nie przechodzi na inne konto w tej samej przeglądarce),
- chwilę potwierdzenia urządzenia (wyłączenie, reset i ponowne włączenie 2FA zmieniają ją,
  więc zaświadczenie umiera razem z urządzeniem, dla którego powstało),
- fragment ``get_session_auth_hash()`` – zmiana hasła unieważnia je tak samo, jak sesje.

Podpis ``django.core.signing`` z własną solą; termin ważności sprawdza ``max_age`` przy odczycie
(nie ufamy ``Max-Age`` przeglądarki). ``HttpOnly`` i ``SameSite=Lax`` zawsze, ``Secure`` – jak
ciasteczko sesji. Hasło nadal jest wymagane: ciasteczko zastępuje wyłącznie **drugi** krok.
"""

from __future__ import annotations

import hmac

from django.conf import settings
from django.core import signing

from . import policy

COOKIE_NAME = "2fa_trust"
SALT = "apps.staff_mfa.trust"
#: Długość fragmentu skrótu hasła. Cały skrót nie jest potrzebny – wystarczy, że zmiana hasła
#: daje inną wartość; krótszy fragment nie zdradza niczego o haśle nawet po odczytaniu ciasteczka.
HASH_PREFIX = 20


def _payload(user, device) -> dict:
    return {
        "u": user.pk,
        "d": int(device.confirmed_at.timestamp()),
        "h": user.get_session_auth_hash()[:HASH_PREFIX],
    }


def remember(response, request, user, device) -> bool:
    """Ustawia ciasteczko, jeśli polityka konkursu na to pozwala. Zwraca, czy ustawiono."""
    days = policy.remember_days(getattr(request, "competition", None))
    if days <= 0 or device is None or device.confirmed_at is None:
        return False
    value = signing.dumps(_payload(user, device), salt=SALT, compress=True)
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=days * 86400,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )
    return True


def is_trusted(request, user, device) -> bool:
    """Czy ta przeglądarka ma ważne zaświadczenie dla tego konta i tego urządzenia."""
    raw = request.COOKIES.get(COOKIE_NAME)
    if not raw or device is None or device.confirmed_at is None:
        return False
    days = policy.remember_days(getattr(request, "competition", None))
    if days <= 0:
        return False
    try:
        data = signing.loads(raw, salt=SALT, max_age=days * 86400)
    except signing.BadSignature:  # obejmuje SignatureExpired
        return False
    expected = _payload(user, device)
    if not isinstance(data, dict) or data.get("u") != expected["u"] or data.get("d") != expected["d"]:
        return False
    return hmac.compare_digest(str(data.get("h", "")), expected["h"])


def forget(response) -> None:
    response.delete_cookie(COOKIE_NAME, samesite="Lax")
