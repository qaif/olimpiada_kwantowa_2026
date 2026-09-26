"""Logowanie hasłem do panelu djcms: tylko superużytkownik i z blokadą prób (reguła 13 z § 7 DJ-01).

**Hasłem loguje się wyłącznie superużytkownik** – techniczne konto operatora z
``bootstrap_djcms_admin`` (DJ-02 D6, decyzja użytkownika z 26.09.2026: bez lokalnych kont redaktorów).
Redaktorzy wchodzą z ``/cms/`` aplikacji głównej jednorazowym tokenem (``apps.sites.sso``) i hasła
nie mają (``set_unusable_password``). Konto personelu z hasłem ustawionym ręcznie w panelu i tak się
nim nie zaloguje: ``ThrottledModelBackend`` traktuje to jak nieudaną próbę – inaczej drugie źródło
prawdy o redaktorach wróciłoby tylnymi drzwiami.

``/admin/`` na ``dj.`` jest publiczny i nie ma drugiego składnika, więc jedyną barierą przed
zgadywaniem haseł redaktorów jest limit. Dwa progi:

- **5 nieudanych prób w oknie 15 minut na parę (adres IP, login)** – właściwa blokada. Para, a nie
  sam login – inaczej każdy z internetu mógłby zablokować redaktorowi konto, wpisując pięć razy złe
  hasło; para, a nie sam adres – bo redakcja siedzi czasem za jednym NAT-em szkoły czy uczelni,
- **50 nieudanych prób w oknie godziny na login, z dowolnych adresów** – sufit na zgadywanie
  rozproszone po wielu adresach (botnet, pula adresów IPv6), którego próg na parę nie widzi wcale.
  Próg jest dziesięć razy wyższy, bo jego koszt uboczny jest realny: kto ma tyle adresów, może nim
  zablokować redaktorowi logowanie na godzinę. To świadoma wymiana – godzina przestoju jednego konta
  zamiast nieograniczonej liczby prób odgadnięcia hasła.

Gdzie to działa: w **backendzie uwierzytelnienia** (``ThrottledModelBackend``), a nie w widoku.
Django CMS ma drugą drogę logowania – formularz paska narzędzi (``cms_login`` w ``cms/urls.py``) –
i blokada podpięta pod sam widok admina zostawiłaby ją otwartą. ``django.contrib.auth.authenticate``
woła backend z każdej drogi; udane logowanie (sygnał ``user_logged_in``) zeruje licznik pary.

**Licznik jest w bazie djcms** (``LoginAttempt``), a nie w buforze. Poprzednia wersja trzymała go
w buforze plikowym i miała dwie dziury (przegląd krytyka po DJ-01d):

1. bufor plikowy ma limit wpisów (``MAX_ENTRIES`` = 300) i po jego przekroczeniu wyrzuca losową
   trzecią część – atakujący zasypywał go porażkami na 300 wymyślonych loginów i w ten sposób
   kasował licznik ofiary. Tabela nie ma limitu wpisów i nie wyrzuca niczego przed końcem okna,
2. licznik był czytany i zapisywany osobno („odczyt → dopisz → zapis”), a blokada sprawdzana
   przed hasłem – dziesięć równoległych prób widziało „4 porażki” i wszystkie dochodziły do
   sprawdzenia hasła. Teraz próba **najpierw rezerwuje miejsce** (wiersz w tabeli, zatwierdzony od
   razu), a dopiero potem liczy wiersze w oknie. Z równoległych prób ta, która zapisała się
   ostatnia, widzi wszystkie wcześniejsze – więc do sprawdzenia hasła dochodzi najwyżej tyle prób,
   ile wynosi limit, niezależnie od tego, jak się przeplotą. Wyścig może najwyżej odrzucić próbę,
   która zmieściłaby się w limicie (dwie równoległe widzą nawzajem swoje rezerwacje) – nigdy
   przepuścić o jedną za dużo.

Okno jest przesuwne: wiersz to jedna porażka ze znacznikiem czasu. Próby odrzucone w czasie
blokady **nie** zostają w tabeli (rezerwacja jest cofana) – blokada mija 15 minut po najstarszej
z pięciu porażek, a nie przedłuża się w nieskończoność pod ciągłym pukaniem (to byłaby blokada
konta na życzenie atakującego, tylko wolniejsza). Udane sprawdzenie hasła też cofa rezerwację:
to nie była porażka.

Gwarancja z punktu 2 zakłada, że ``authenticate`` nie biegnie wewnątrz transakcji
(``ATOMIC_REQUESTS`` jest wyłączone, a widoki logowania admina i ``cms_login`` nie są atomowe) –
w transakcji rezerwacja stałaby się widoczna dla innych dopiero przy jej zatwierdzeniu.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from functools import lru_cache

from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.signals import user_logged_in
from django.core.exceptions import PermissionDenied, ValidationError
from django.dispatch import receiver
from django.utils.crypto import salted_hmac

logger = logging.getLogger(__name__)

REAL_IP_HEADER = "HTTP_X_REAL_IP"
#: Sól skrótów w tabeli ``LoginAttempt`` (``salted_hmac`` z ``SECRET_KEY``): zrzut bazy djcms nie
#: zdradza, kto i skąd próbował się logować – skrótu bez sekretu nie da się odwrócić słownikiem.
KEY_SALT = "djcms.login-throttle"

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


def trusted_proxy_networks() -> tuple:
    """``settings.TRUSTED_PROXY_IPS`` jako sieci (``ip_network``); nieprawidłowe wpisy pominięte."""
    return _parse_networks(tuple(getattr(settings, "TRUSTED_PROXY_IPS", ()) or ()))


def is_trusted_proxy(request) -> bool:
    """Czy połączenie przyszło od zaufanego proxy (Caddy) – ``REMOTE_ADDR`` w ``TRUSTED_PROXY_IPS``.

    Jedyna podstawa zaufania do nagłówków, które ustawia Caddy (``X-Real-IP`` tutaj,
    ``X-Djcms-Mode`` w ``apps.pages.mode``). Brak listy albo adres spoza niej = ``False``.
    """
    meta = getattr(request, "META", None) or {}
    remote = _parse_address(meta.get("REMOTE_ADDR"))
    return remote is not None and any(remote in network for network in trusted_proxy_networks())


def client_ip(request) -> str:
    """Adres klienta: ``X-Real-IP`` **wyłącznie** od zaufanego proxy (Caddy), inaczej ``REMOTE_ADDR``.

    Bez tego warunku atakujący wpisywałby sobie w nagłówek nowy adres przy każdej próbie
    i limit na parę (IP, login) nie ograniczałby niczego.
    """
    meta = getattr(request, "META", None) or {}
    remote = _parse_address(meta.get("REMOTE_ADDR"))
    if remote is None:
        return "unknown"
    if not is_trusted_proxy(request):
        return str(remote)
    forwarded = _parse_address(meta.get(REAL_IP_HEADER))
    return str(forwarded) if forwarded is not None else str(remote)


# --- licznik porażek --------------------------------------------------------------------------


def _normalized(username: str | None) -> str:
    return (username or "").strip().lower()


def _digest(raw: str) -> str:
    return salted_hmac(KEY_SALT, raw, algorithm="sha256").hexdigest()


#: Długość prefiksu, do którego zwijamy adres IPv6 w kluczu pary. /64 to najmniejsza sieć, jaką
#: dostawca przydziela jednemu łączu (RFC 6177, RIPE-690), więc dla licznika jest „jednym adresem”.
IPV6_THROTTLE_PREFIX = 64


def throttle_address(value: str) -> str:
    """Adres klienta w kluczu pary: IPv4 bez zmian, IPv6 zwinięty do sieci /64.

    Pojedynczy host IPv6 ma zwykle do dyspozycji całą /64 (2^64 adresów, SLAAC/adresy tymczasowe).
    Klucz po pełnym adresie dawałby mu po 5 prób **na adres** – limit na parę nie ograniczałby
    niczego, a sufit na login (50/h) stałby się narzędziem trwałej blokady konta redaktora z jednego
    łącza. Adres IPv4 zapisany jako IPv6 (``::ffff:a.b.c.d`` – gniazdo dual-stack) to ten sam
    klient co ``a.b.c.d``, więc wraca do postaci IPv4. Napis, który nie jest adresem
    (``"unknown"``), zostaje bez zmian.
    """
    address = _parse_address(value)
    if address is None:
        return value
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/{IPV6_THROTTLE_PREFIX}", strict=False))
    return str(address)


def _key(request, username: str | None) -> str:
    """Skrót pary (IP, login) – IPv6 jako sieć /64 (``throttle_address``). Ani loginu, ani adresu wprost."""
    return _digest(f"pair|{throttle_address(client_ip(request))}|{_normalized(username)}")


def _user_key(username: str | None) -> str:
    return _digest(f"user|{_normalized(username)}")


def _pair_window() -> float:
    return float(settings.DJCMS_LOGIN_WINDOW_SECONDS)


def _user_window() -> float:
    return float(settings.DJCMS_LOGIN_USER_WINDOW_SECONDS)


def _attempts():
    # Import w funkcji: ``AUTHENTICATION_BACKENDS`` bywa importowany, zanim rejestr aplikacji jest
    # gotowy, a import modelu na poziomie modułu wywróciłby wtedy start.
    from .models import LoginAttempt

    return LoginAttempt.objects


def _over_limit(pair_key: str, user_key: str, now: float) -> bool:
    """Czy porażek (razem z ewentualną rezerwacją bieżącej próby) jest więcej, niż wolno."""
    attempts = _attempts()
    pair = attempts.filter(pair_key=pair_key, at__gt=now - _pair_window()).count()
    if pair > settings.DJCMS_LOGIN_MAX_FAILURES:
        return True
    user = attempts.filter(user_key=user_key, at__gt=now - _user_window()).count()
    return user > settings.DJCMS_LOGIN_USER_MAX_FAILURES


def is_locked(request, username: str | None) -> bool:
    """Czy następna próba zostałaby odrzucona (tylko odczyt – dla komunikatu formularza)."""
    if request is None:
        return False
    now = time.time()
    attempts = _attempts()
    pair = attempts.filter(pair_key=_key(request, username), at__gt=now - _pair_window()).count()
    if pair >= settings.DJCMS_LOGIN_MAX_FAILURES:
        return True
    user = attempts.filter(user_key=_user_key(username), at__gt=now - _user_window()).count()
    return user >= settings.DJCMS_LOGIN_USER_MAX_FAILURES


def reserve_attempt(request, username: str | None):
    """Rezerwuje próbę logowania; zwraca wiersz rezerwacji albo ``None``, gdy limit jest wyczerpany.

    Kolejność „zapisz, potem policz” jest istotą poprawki – patrz docstring modułu, punkt 2.
    Zwrócony wiersz jest od razu porażką; ``release_attempt`` cofa go, gdy hasło okazało się dobre.
    """
    now = time.time()
    pair_key, user_key = _key(request, username), _user_key(username)
    attempts = _attempts()
    # Sprzątanie przy okazji: wiersze starsze niż dłuższe z okien niczego już nie liczą.
    attempts.filter(at__lt=now - max(_pair_window(), _user_window())).delete()
    attempt = attempts.create(pair_key=pair_key, user_key=user_key, at=now)
    if _over_limit(pair_key, user_key, now):
        attempt.delete()
        return None
    if attempts.filter(pair_key=pair_key, at__gt=now - _pair_window()).count() >= (
        settings.DJCMS_LOGIN_MAX_FAILURES
    ):
        # Bez loginu i bez adresu w treści – log idzie do ``docker logs``, a to nie jest miejsce
        # na dane osobowe. Końcówka skrótu wystarczy, żeby skorelować zdarzenia.
        logger.warning("Blokada logowania djcms: limit porażek osiągnięty (klucz …%s)", pair_key[-12:])
    return attempt


def release_attempt(attempt) -> None:
    """Cofa rezerwację próby, która nie była porażką (hasło dobre)."""
    if attempt is not None:
        attempt.delete()


def reset_failures(request, username: str | None) -> None:
    """Udane logowanie zeruje licznik **pary**. Sufitu na login nie – patrz niżej."""
    if request is not None:
        # Sufit na login (``user_key``) zostaje: gdyby zerowało go każde udane logowanie
        # właściciela konta, zgadujący z wielu adresów dostawałby po nim świeże 50 prób. Dlatego
        # wiersze nie są kasowane, tylko odpinane od pary – dalej liczą się do sufitu na login.
        _attempts().filter(pair_key=_key(request, username)).update(pair_key="")


@receiver(user_logged_in)
def _on_logged_in(sender, request, user, **kwargs):
    reset_failures(request, user.get_username())


# --- backend i formularz ----------------------------------------------------------------------


class ThrottledModelBackend(ModelBackend):
    """``ModelBackend``, który rezerwuje próbę i odmawia **przed** sprawdzeniem hasła.

    Kolejność ma znaczenie: sprawdzenie hasła w czasie blokady zdradzałoby (czasem odpowiedzi
    i komunikatem), czy hasło było dobre – blokada przestałaby być blokadą, a stałaby się
    wolniejszą wyrocznią. ``PermissionDenied`` przerywa też przeszukiwanie kolejnych backendów.

    Porażka jest zapisywana tutaj (rezerwacja), a nie w sygnale ``user_login_failed``: sygnał
    przychodzi **po** sprawdzeniu hasła, więc licznik oparty na nim zawsze spóźniałby się
    o równoległe próby. Bez ``request`` (komendy, powłoka) blokady nie ma – nie ma czego liczyć.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if request is None:
            return self._superuser_only(
                super().authenticate(request, username=username, password=password, **kwargs)
            )
        attempt = reserve_attempt(request, username)
        if attempt is None:
            raise PermissionDenied(LOCKED_MESSAGE)
        user = self._superuser_only(
            super().authenticate(request, username=username, password=password, **kwargs)
        )
        if user is not None:
            release_attempt(attempt)
        return user

    @staticmethod
    def _superuser_only(user):
        """Hasło otwiera wyłącznie konto superużytkownika (docstring modułu); inne – jak złe hasło."""
        if user is not None and not user.is_superuser:
            logger.warning("Logowanie hasłem djcms odrzucone: konto #%s nie jest superużytkownikiem", user.pk)
            return None
        return user


class ThrottledAdminAuthenticationForm(AdminAuthenticationForm):
    """Formularz logowania panelu: przy blokadzie mówi wprost, że to blokada, a nie złe hasło."""

    error_messages = {
        **AdminAuthenticationForm.error_messages,
        "locked": LOCKED_MESSAGE,
    }

    def clean(self):
        if is_locked(self.request, self.cleaned_data.get("username")):
            raise ValidationError(self.error_messages["locked"], code="locked")
        # Wyścig (limit wyczerpała równoległa próba między sprawdzeniem wyżej a rezerwacją
        # w backendzie) kończy się zwykłym „zły login lub hasło”: ``authenticate`` zamienia
        # ``PermissionDenied`` backendu na ``None``. Następna próba dostanie już komunikat o blokadzie.
        return super().clean()
