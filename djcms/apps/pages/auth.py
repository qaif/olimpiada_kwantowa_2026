"""Blokada prób logowania do panelu djcms (reguła 13 z § 7 docs/tasks/DJ-01.md).

``/admin/`` na ``dj.`` jest publiczny i nie ma drugiego składnika, więc jedyną barierą przed
zgadywaniem haseł redaktorów jest limit: **5 nieudanych prób w oknie 15 minut na parę
(adres IP, login)**. Para, a nie sam login – inaczej każdy z internetu mógłby zablokować
redaktorowi konto, wpisując pięć razy złe hasło; para, a nie sam adres – bo redakcja siedzi
czasem za jednym NAT-em szkoły czy uczelni.

Gdzie to działa: w **backendzie uwierzytelnienia** (``ThrottledModelBackend``), a nie w widoku.
Django CMS ma drugą drogę logowania – formularz paska narzędzi (``cms_login`` w ``cms/urls.py``) –
i blokada podpięta pod sam widok admina zostawiłaby ją otwartą. ``django.contrib.auth.authenticate``
woła backend z każdej drogi. Liczenie idzie przez sygnał ``user_login_failed`` (wysyła go
``authenticate`` po każdej porażce, niezależnie od drogi), a udane logowanie zeruje licznik.

Okno jest przesuwne: trzymamy znaczniki czasu porażek, a nie licznik z czasem życia klucza.
Próby wykonane w czasie blokady **nie** są dopisywane – blokada mija 15 minut po najstarszej
z pięciu porażek, a nie przedłuża się w nieskończoność pod ciągłym pukaniem (to byłaby blokada
konta na życzenie atakującego, tylko wolniejsza).

Bufor ``throttle`` (``config/settings/base.py``) jest plikowy w ``/tmp`` kontenera – wspólny dla
procesów gunicorna, więc limit jest naprawdę 5, a nie 5 na proces.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
from functools import lru_cache

from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.cache import caches
from django.core.exceptions import PermissionDenied, ValidationError
from django.dispatch import receiver

logger = logging.getLogger(__name__)

REAL_IP_HEADER = "HTTP_X_REAL_IP"
CACHE_ALIAS = "throttle"
KEY_PREFIX = "djcms:login-fail:"

LOCKED_MESSAGE = (
    "Zbyt wiele nieudanych prób logowania. Spróbuj ponownie za kilkanaście minut "
    "albo poproś administratora o pomoc."
)


# --- adres klienta (port ``apps.core.models.client_ip`` z backendu) --------------------------


@lru_cache(maxsize=8)
def _parse_networks(entries: tuple[str, ...]) -> tuple:
    """Lista adresów/CIDR z ustawień → ``ip_network``; memoizowana po wartości ustawienia."""
    networks = []
    for entry in entries:
        text = (entry or "").strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            logger.warning("TRUSTED_PROXY_IPS: pomijam nieprawidłowy wpis %r", text)
    return tuple(networks)


def _parse_address(value: str | None):
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def client_ip(request) -> str:
    """Adres klienta: ``X-Real-IP`` **wyłącznie** od zaufanego proxy (Caddy), inaczej ``REMOTE_ADDR``.

    Bez tego warunku atakujący wpisywałby sobie w nagłówek nowy adres przy każdej próbie
    i limit na parę (IP, login) nie ograniczałby niczego.
    """
    meta = getattr(request, "META", None) or {}
    remote = _parse_address(meta.get("REMOTE_ADDR"))
    if remote is None:
        return "unknown"
    trusted = _parse_networks(tuple(getattr(settings, "TRUSTED_PROXY_IPS", ()) or ()))
    if not any(remote in network for network in trusted):
        return str(remote)
    forwarded = _parse_address(meta.get(REAL_IP_HEADER))
    return str(forwarded) if forwarded is not None else str(remote)


# --- licznik porażek --------------------------------------------------------------------------


def _cache():
    return caches[CACHE_ALIAS]


def _key(request, username: str | None) -> str:
    # Skrót, a nie login wprost: klucz trafia do nazwy pliku bufora w ``/tmp`` i nie może
    # nieść ani znaków spoza ASCII, ani (w logach/zrzutach) samego loginu z adresem.
    raw = f"{client_ip(request)}|{(username or '').strip().lower()}"
    return KEY_PREFIX + hashlib.sha256(raw.encode()).hexdigest()


def _window() -> int:
    return int(settings.DJCMS_LOGIN_WINDOW_SECONDS)


def _recent_failures(key: str, now: float) -> list[float]:
    stamps = _cache().get(key) or []
    return [stamp for stamp in stamps if now - stamp < _window()]


def is_locked(request, username: str | None) -> bool:
    """Czy para (IP, login) wyczerpała limit porażek w bieżącym oknie."""
    if request is None:
        return False
    return len(_recent_failures(_key(request, username), time.time())) >= settings.DJCMS_LOGIN_MAX_FAILURES


def record_failure(request, username: str | None) -> None:
    """Dopisuje porażkę – chyba że para jest już zablokowana (patrz docstring modułu)."""
    if request is None:
        return
    key = _key(request, username)
    now = time.time()
    stamps = _recent_failures(key, now)
    if len(stamps) >= settings.DJCMS_LOGIN_MAX_FAILURES:
        return
    stamps.append(now)
    _cache().set(key, stamps, timeout=_window())
    if len(stamps) >= settings.DJCMS_LOGIN_MAX_FAILURES:
        # Bez loginu i bez adresu w treści – log idzie do ``docker logs``, a to nie jest miejsce
        # na dane osobowe. Skrót klucza wystarczy, żeby skorelować zdarzenia.
        logger.warning("Blokada logowania djcms: limit porażek osiągnięty (klucz %s…)", key[-12:])


def reset_failures(request, username: str | None) -> None:
    if request is not None:
        _cache().delete(_key(request, username))


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    record_failure(request, (credentials or {}).get(get_user_model().USERNAME_FIELD))


@receiver(user_logged_in)
def _on_logged_in(sender, request, user, **kwargs):
    reset_failures(request, user.get_username())


# --- backend i formularz ----------------------------------------------------------------------


class ThrottledModelBackend(ModelBackend):
    """``ModelBackend``, który odmawia **przed** sprawdzeniem hasła, gdy para jest zablokowana.

    Kolejność ma znaczenie: sprawdzenie hasła w czasie blokady zdradzałoby (czasem odpowiedzi
    i komunikatem), czy hasło było dobre – blokada przestałaby być blokadą, a stałaby się
    wolniejszą wyrocznią. ``PermissionDenied`` przerywa też przeszukiwanie kolejnych backendów.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if is_locked(request, username):
            raise PermissionDenied(LOCKED_MESSAGE)
        return super().authenticate(request, username=username, password=password, **kwargs)


class ThrottledAdminAuthenticationForm(AdminAuthenticationForm):
    """Formularz logowania panelu: przy blokadzie mówi wprost, że to blokada, a nie złe hasło."""

    error_messages = {
        **AdminAuthenticationForm.error_messages,
        "locked": LOCKED_MESSAGE,
    }

    def clean(self):
        if is_locked(self.request, self.cleaned_data.get("username")):
            raise ValidationError(self.error_messages["locked"], code="locked")
        return super().clean()
