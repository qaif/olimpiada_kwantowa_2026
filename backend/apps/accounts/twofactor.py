"""Logowanie dwuskładnikowe (TOTP) dla kont, które mają dostęp do cudzych danych.

Po co to jest akurat tutaj, skoro hasła są solidnie hashowane, a logowanie ma limit prób: rachunek
ryzyka jest inny dla uczestnika i inny dla koordynatora. Przejęcie konta uczestnika kosztuje jego
własne prace. Przejęcie konta koordynatora albo członka komitetu kosztuje **wszystkie** prace,
wszystkie dane osobowe małoletnich, protokoły recenzji i możliwość dowolnej zmiany wyników – a te
konta chodzą po tych samych szkolnych komputerach i tych samych skrzynkach pocztowych, co reszta
świata. Drugi składnik jest tu najtańszą rzeczą, która zamienia „wyciekło hasło” w „nic się nie
stało”.

Decyzje, które warto znać przed czytaniem kodu:

**Własna implementacja TOTP zamiast biblioteki.** RFC 6238 to trzydzieści linijek nad ``hmac``
i ``struct`` (funkcja :func:`totp_code` niżej), a każda nowa zależność w obrazie produkcyjnym
kosztuje przy wdrożeniu przez firmowe proxy, przy audycie i przy każdej aktualizacji. Kod QR
składamy z macierzy, którą i tak umie policzyć ``reportlab`` (jest w zależnościach od czasu
dyplomów) – nie dokładamy więc ani ``pyotp``, ani ``qrcode``.

**Sekret jest szyfrowany w bazie** (``cryptography.fernet``, klucz wyprowadzony z ``SECRET_KEY``).
Nie chroni to przed kimś, kto ma jednocześnie zrzut bazy i ``SECRET_KEY`` – chroni przed
najczęstszym w praktyce wyciekiem, czyli samą kopią bazy (dump wysłany pomocy technicznej,
przypadkowo publiczny backup, podejrzenie w panelu). Konsekwencja jest ostra i trzeba ją znać:
**zmiana ``SECRET_KEY`` unieważnia wszystkie drugie składniki**. Sekret nie do odszyfrowania
traktujemy jak urządzenie zepsute – koordynator resetuje je z panelu (docs/OPERACJE.md).

**Kody zapasowe są hashowane** dokładnie z tego samego powodu, co kody zaproszeń
(``accounts.models.hash_invitation_code``): w bazie nigdy nie ma czegoś, czym da się zalogować.
Pokazujemy je raz, przy włączeniu, i nigdy więcej.

**Ochrona przed powtórzeniem kodu.** TOTP jest ważny przez cały krok czasu (30 s), więc kod
podejrzany przez ramię albo wyłowiony z logu proxy dałby się użyć drugi raz. ``last_counter``
zapisuje numer ostatniego przyjętego kroku i kod z tego samego (albo wcześniejszego) kroku jest
odrzucany – po jednym użyciu kod jest spalony.

**Wyłącznik główny.** Cała funkcja wisi na ``settings.TWO_FACTOR_ENABLED`` i **domyślnie jest
wyłączona** – tak zdecydował organizator dla tej instalacji. Wyłączona znaczy: warstwa wymuszająca
przepuszcza wszystko (także konta z potwierdzonym urządzeniem – logują się samym hasłem), ekrany
odpowiadają 404, a w interfejsie nie ma ani jednego odnośnika. Zapisanych urządzeń wyłącznik
**nie kasuje**: wiersze zostają w bazie i ponowne włączenie przywraca stan sprzed wyłączenia.
Pyta o to jedna funkcja, :func:`is_enabled` – patrz jej docstring.

**Wymuszanie** jest w :class:`TwoFactorMiddleware` i domyślnie dotyczy wyłącznie kont, które
**same** sobie drugi składnik włączyły. ``TWO_FACTOR_REQUIRED_ROLES`` (ustawienie, domyślnie
puste) pozwala pójść dalej i zażądać go od wskazanych ról – ale to jest decyzja organizatora
podejmowana wtedy, gdy komitet ma już aplikacje na telefonach, a nie domyślna konfiguracja,
która w dniu wdrożenia zamyka koordynatorowi drogę do własnego panelu.

**SEC-01 (04.10.2026): wymóg dla personelu.** Od kogo drugi składnik jest wymagany, rozstrzyga
``apps.staff_mfa.policy`` – lista ról platformy (``TWO_FACTOR_REQUIRED_ROLES``, teraz domyślnie
``superkoordynator,admin``) i polityka konkursu (domyślnie personel konkursów z danymi
wrażliwymi), z jednorazowym **okresem przejściowym** (baner, potem poczekalnia). Ten moduł dokłada
do protokołu: blokadę konta po serii złych kodów, jednorazowość odporną na równoległe żądania,
ponowne potwierdzenie hasłem i kodem przy wyłączaniu i przy nowym komplecie kodów, „zapamiętaj to
urządzenie” (``apps.staff_mfa.trust``) i listy do właściciela (``apps.staff_mfa.notifications``).
Szczegóły i uzasadnienia: ``docs/tasks/SEC-01.md``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
from functools import lru_cache
from urllib.parse import quote

from django.conf import settings
from django.db import models
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

# --- parametry protokołu ----------------------------------------------------------------------

#: Długość sekretu w bajtach. Dwadzieścia, czyli tyle, ile ma klucz HMAC-SHA1 z RFC 4226 – i tyle,
#: ile zakładają Google Authenticator, Aegis i FreeOTP. Dłuższy sekret nie jest błędem, ale część
#: aplikacji ucina go przy imporcie i uczestnik dostaje kody, które nigdy nie pasują.
SECRET_BYTES = 20

#: Krok czasu w sekundach i liczba cyfr – wartości domyślne RFC 6238. Aplikacje uwierzytelniające
#: zakładają je bez pytania, a zapisanie ich w adresie ``otpauth://`` i tak nie gwarantuje, że
#: którakolwiek je odczyta. Inna wartość = uczestnik widzi kody, które nie działają.
TIME_STEP_SECONDS = 30
CODE_DIGITS = 6

#: Ile kroków czasu wstecz i wprzód akceptujemy. Jeden, czyli ±30 s. Zegar w telefonie potrafi
#: odbiegać o kilkanaście sekund, a przepisanie kodu zajmuje chwilę; szersze okno psułoby jednak
#: ochronę przed powtórzeniem (każdy kod byłby ważny dłużej).
TIME_WINDOW_STEPS = 1

#: Kody zapasowe: dziesięć sztuk po dziesięć znaków. Alfabet bez znaków mylących – kod bywa
#: przepisywany z kartki, którą ktoś wydrukował pół roku wcześniej.
BACKUP_CODE_COUNT = 10
BACKUP_CODE_LENGTH = 10
BACKUP_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

#: Klucz sesji z potwierdzeniem drugiego składnika. Trzyma **identyfikator konta**, a nie ``True``:
#: wartość logiczna przetrwałaby ponowne zalogowanie się w tej samej sesji na inne konto, gdyby
#: Django kiedykolwiek przestało czyścić sesję przy zmianie użytkownika. Porównanie z ``user.pk``
#: kosztuje jedno porównanie liczb i zamyka całą tę klasę błędów.
SESSION_VERIFIED_KEY = "2fa_verified"

#: Scope limitu prób – ten sam mechanizm, co przy logowaniu (``apps.web.throttle``). Stawka stoi
#: w ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``; brak wpisu (tak jest w testach) wyłącza limit.
THROTTLE_SCOPE = "two_factor"

#: Znaczniki sesji warstwy wymuszającej dla kont **bez** urządzenia (SEC-01 § 3). Oba niosą
#: ``[konto, konkurs, wersja polityki]``: odpowiedź „nie musisz” albo „masz czas do…” jest odpowiedzią
#: dla jednego konkursu (konkurs pod prefiksem ścieżki dzieli sesję z gospodarzem) i jednej wersji
#: polityki (``apps.staff_mfa.policy.version``). Przy ``2fa_grace`` czwarty element to termin
#: (znacznik czasu uniksowego) – po nim sesja liczy wymóg od nowa.
SESSION_EXEMPT_KEY = "2fa_exempt"
SESSION_GRACE_KEY = "2fa_grace"

#: Blokada konta po serii złych kodów (SEC-01 § 5). Limit ``two_factor`` liczy próby z jednego
#: adresu; blokada liczy próby na **konto**, niezależnie od adresu – kto zna hasło i rozkłada próby
#: kodu na wiele adresów, po pięciu porażkach i tak czeka kwadrans. Sześć cyfr przy pięciu próbach
#: na kwadrans to statystycznie kilkadziesiąt lat zgadywania.
LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60


# --- szyfrowanie sekretu ----------------------------------------------------------------------


@lru_cache(maxsize=4)
def _fernet_for(secret_key: str):
    """Klucz Fernet wyprowadzony z ``SECRET_KEY``. Memoizowany po **wartości** ustawienia.

    Po wartości, a nie na stałe, z tego samego powodu, co w ``apps.core.models._parse_networks``:
    test podmieniający ``SECRET_KEY`` (``override_settings``) ma dostać inny klucz, a produkcja
    ma go wyprowadzić raz. Wyprowadzenie to pojedyncze SHA-256, a nie KDF z rozciąganiem – nie
    bronimy się tu przed zgadywaniem hasła (``SECRET_KEY`` ma 64 losowe znaki), tylko sprowadzamy
    klucz o dowolnej długości do 32 bajtów, których wymaga Fernet.
    """
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(f"twofactor:{secret_key}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    return _fernet_for(settings.SECRET_KEY).encrypt(plain.encode("ascii")).decode("ascii")


def decrypt_secret(token: str) -> str | None:
    """Odszyfrowany sekret albo ``None``, gdy się nie da (zmieniony ``SECRET_KEY``, uszkodzony wpis).

    ``None`` zamiast wyjątku, bo wołający ma na tę sytuację sensowną odpowiedź: urządzenie jest
    zepsute, żaden kod do niego nie pasuje i trzeba je zresetować. Wyjątek zamieniłby to
    w błąd 500 na ekranie logowania.
    """
    from cryptography.fernet import InvalidToken

    try:
        return _fernet_for(settings.SECRET_KEY).decrypt(token.encode("ascii")).decode("ascii")
    except InvalidToken, ValueError, UnicodeDecodeError:
        logger.warning("2FA: nie udało się odszyfrować sekretu (zmiana SECRET_KEY?).")
        return None


# --- model ------------------------------------------------------------------------------------


class TwoFactorDevice(models.Model):
    """Drugi składnik jednego konta: sekret TOTP, moment potwierdzenia i kody zapasowe.

    Jeden wiersz na konto (``OneToOneField``), bo to jest cała potrzebna tu funkcjonalność:
    „urządzenia” w liczbie mnogiej znaczyłyby wybór na ekranie logowania, a wybór na ekranie
    logowania znaczyłby enumerację (ktoś z samym hasłem widziałby, ile telefonów ma ofiara).

    Wiersz istnieje także **przed** potwierdzeniem: ekran konfiguracji zapisuje sekret od razu,
    żeby kod QR przeżył odświeżenie strony i przełączenie się na telefon. Dopóki ``confirmed_at``
    jest puste, ten wiersz nie znaczy nic – logowanie go nie widzi (patrz :func:`confirmed_device`).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="two_factor",
        verbose_name="konto",
    )
    # Tekst, a nie ``BinaryField``: token Fernet jest ciągiem base64 i w tej postaci przeżywa
    # ``pg_dump``, ``loaddata`` i podgląd w psql bez ani jednej konwersji.
    secret = models.TextField("sekret (zaszyfrowany)")
    confirmed_at = models.DateTimeField("potwierdzone", null=True, blank=True)
    created_at = models.DateTimeField("utworzone", default=timezone.now)
    # Skróty SHA-256 kodów zapasowych. Lista, a nie osobna tabela: kody powstają i giną wyłącznie
    # w komplecie, nie mają własnych atrybutów i nikt ich nie wyszukuje.
    backup_codes = models.JSONField("kody zapasowe (skróty)", default=list, blank=True)
    # Numer ostatniego przyjętego kroku czasu – ochrona przed powtórzeniem kodu. Zero znaczy
    # „jeszcze żadnego”; kroki liczone są od epoki uniksowej, więc mieszczą się w 8 bajtach.
    last_counter = models.BigIntegerField("ostatni użyty krok", default=0)
    last_used_at = models.DateTimeField("ostatnie użycie", null=True, blank=True)

    class Meta:
        verbose_name = "drugi składnik logowania"
        verbose_name_plural = "drugie składniki logowania"

    def __str__(self) -> str:
        return f"2FA: {self.user_id} ({'potwierdzone' if self.confirmed_at else 'w trakcie konfiguracji'})"

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    @property
    def backup_codes_left(self) -> int:
        return len(self.backup_codes or [])

    def plain_secret(self) -> str | None:
        return decrypt_secret(self.secret)


# --- TOTP ---------------------------------------------------------------------------------------


def generate_secret() -> str:
    """Nowy sekret w base32 bez wypełnienia – w takiej postaci wchodzi do adresu ``otpauth://``."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def current_counter(at: float | None = None) -> int:
    """Numer kroku czasu. Liczony z czasu uniksowego, czyli niezależnie od strefy serwera."""
    return int((at if at is not None else time.time()) // TIME_STEP_SECONDS)


def totp_code(secret: str, counter: int) -> str:
    """Kod TOTP dla sekretu i numeru kroku (RFC 6238 + „dynamic truncation” z RFC 4226).

    SHA-1, bo tak robi każda aplikacja uwierzytelniająca i tego oczekuje po adresie ``otpauth://``.
    Nie jest to tu żadną słabością: HMAC-SHA1 nie zależy od odporności SHA-1 na kolizje, a kod
    i tak żyje trzydzieści sekund.
    """
    # Base32 z aplikacji bywa przepisane małymi literami i bez wypełnienia – domykamy oba przypadki,
    # bo sekret wraca do nas także z ręcznego wpisania w telefonie.
    padded = secret.strip().replace(" ", "").upper()
    padded += "=" * (-len(padded) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**CODE_DIGITS)).zfill(CODE_DIGITS)


def provisioning_uri(user, secret: str) -> str:
    """Adres ``otpauth://`` do kodu QR (format „Key Uri” Google Authenticatora).

    Etykieta jest ``<wydawca>:<adres e-mail>``, bo tak aplikacje pokazują wpis na liście – bez
    wydawcy uczestnik z trzema olimpiadami w telefonie widzi trzy identyczne pozycje.
    """
    issuer = getattr(settings, "WAGTAIL_SITE_NAME", "Olimpiada")
    label = quote(f"{issuer}:{user.email}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={CODE_DIGITS}&period={TIME_STEP_SECONDS}"
    )


def qr_svg(data: str, *, quiet_zone: int = 4) -> str:
    """Kod QR jako SVG złożony z macierzy modułów. Pusty ciąg, gdy nie da się go policzyć.

    Macierz liczy ``reportlab`` (zależność, która jest w projekcie od czasu dyplomów), ale jego
    własny renderer SVG tu nie wchodzi: oddaje kilkadziesiąt kilobajtów z deklaracją DOCTYPE
    i tysiącem osobnych prostokątów, czego nie da się wstawić w środek strony HTML. Stąd jedna
    ścieżka ``<path>`` zbudowana ręcznie – kilka kilobajtów i żadnego zewnętrznego zasobu,
    czyli także żadnego wyjątku w polityce CSP.

    ``quiet_zone`` to wymagany normą margines czterech modułów. Bez niego czytniki gubią kod na
    jasnym tle – i jest to najczęstsza przyczyna „aplikacja nie widzi kodu” przy własnych SVG.
    """
    try:
        from reportlab.graphics.barcode import qr as reportlab_qr
    except Exception:  # noqa: BLE001 - brak reportlaba nie może zamknąć drogi do włączenia 2FA
        logger.warning("2FA: nie udało się zbudować kodu QR – zostaje sekret do przepisania.")
        return ""
    widget = reportlab_qr.QrCodeWidget(data)
    widget.qr.make()
    matrix = widget.qr.modules
    size = len(matrix) + 2 * quiet_zone
    parts = []
    for row, cells in enumerate(matrix):
        for column, filled in enumerate(cells):
            if filled:
                parts.append(f"M{column + quiet_zone} {row + quiet_zone}h1v1h-1z")
    body = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'role="img" aria-label="Kod QR do aplikacji uwierzytelniającej" '
        f'shape-rendering="crispEdges" width="240" height="240">'
        f'<rect width="{size}" height="{size}" fill="#ffffff"/>'
        f'<path d="{body}" fill="#000000"/>'
        f"</svg>"
    )


# --- kody zapasowe ------------------------------------------------------------------------------


def hash_backup_code(code: str) -> str:
    """SHA-256 kodu zapasowego. Jedyna postać kodu, jaka trafia do bazy.

    Bez soli i bez rozciągania, tak samo jak przy kodach zaproszeń: kod ma pięćdziesiąt bitów
    entropii z losowego generatora, więc tablica tęczowa nie ma z czego powstać, a ``pbkdf2``
    kosztowałby przy każdej próbie logowania i niczego by nie dołożył.
    """
    return hashlib.sha256(normalize_backup_code(code).encode("ascii")).hexdigest()


def normalize_backup_code(code: str) -> str:
    """Kod sprowadzony do postaci porównywalnej: wielkie litery, bez spacji i myślników.

    Człowiek przepisuje kod z kartki i myślnik wstawia albo nie – różnica w zapisie nie może
    decydować o tym, czy wejdzie na swoje konto.
    """
    return "".join(character for character in (code or "").upper() if character.isalnum())


def generate_backup_codes() -> tuple[list[str], list[str]]:
    """Komplet nowych kodów: postać do pokazania i skróty do zapisania w bazie."""
    plain = [
        "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _pos in range(BACKUP_CODE_LENGTH))
        for _index in range(BACKUP_CODE_COUNT)
    ]
    return [f"{code[:5]}-{code[5:]}" for code in plain], [hash_backup_code(code) for code in plain]


# --- odczyt stanu konta -------------------------------------------------------------------------


def device_for(user) -> TwoFactorDevice | None:
    """Wiersz 2FA konta (także niepotwierdzony) albo ``None``."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return TwoFactorDevice.objects.filter(user=user).first()


def confirmed_device(user) -> TwoFactorDevice | None:
    """Wiersz 2FA **potwierdzony** – jedyny, który cokolwiek znaczy przy logowaniu."""
    device = device_for(user)
    return device if device is not None and device.is_confirmed else None


def is_enabled() -> bool:
    """Wyłącznik główny (``settings.TWO_FACTOR_ENABLED``). **Domyślnie wyłączony.**

    Jedno miejsce, w które pyta wszystko: warstwa wymuszająca, widoki (odpowiadają 404) i szablony
    (nie pokazują ani jednego odnośnika). Rozsypanie tego warunku po piętnastu miejscach znaczyłoby,
    że wyłączenie funkcji jest wyłączeniem piętnastu rzeczy – a piętnasta zawsze zostaje włączona.

    Wyłącznik **nie kasuje** zapisanych urządzeń: wiersze :class:`TwoFactorDevice` zostają w bazie,
    a konto, które ma potwierdzone urządzenie, loguje się samym hasłem. Ponowne włączenie przywraca
    stan sprzed wyłączenia, zamiast kazać całemu komitetowi konfigurować aplikacje od nowa.
    """
    return bool(getattr(settings, "TWO_FACTOR_ENABLED", False))


def required_roles() -> set[str]:
    """Role platformy, od których wymagany jest drugi składnik (``TWO_FACTOR_REQUIRED_ROLES``)."""
    from apps.staff_mfa.policy import platform_roles

    return set(platform_roles())


def _competition_or_context(competition):
    if competition is not None:
        return competition
    from apps.tenancy.context import current_competition

    return current_competition()


def is_required_for(user, competition=None) -> bool:
    """Czy to konto **musi** mieć drugi składnik w tym konkursie (bez względu na okres przejściowy).

    Wyłącznik główny wygrywa z każdą polityką i sprawdzamy go **pierwszy**: ``TWO_FACTOR_REQUIRED_ROLES``
    zostawione w ``.env`` z poprzedniej konfiguracji nie może po wyłączeniu funkcji odsyłać
    koordynatora na ekran, którego już nie ma (404 w pętli). Konkurs niepodany – z kontekstu żądania.
    """
    if not is_enabled() or not user or not getattr(user, "is_authenticated", False):
        return False
    from apps.staff_mfa.policy import matching_roles

    return bool(matching_roles(user, _competition_or_context(competition)))


def requirement(user, competition=None, *, request=None, start_grace: bool = True):
    """Wymóg z terminem (``apps.staff_mfa.policy.Requirement``) albo ``None``."""
    if not is_enabled() or not user or not getattr(user, "is_authenticated", False):
        return None
    from apps.staff_mfa.policy import requirement_for

    return requirement_for(
        user, _competition_or_context(competition), request=request, start_grace=start_grace
    )


def setup_overdue(user, competition=None, *, request=None) -> bool:
    """Czy konto bez urządzenia jest już **po terminie** okresu przejściowego (API, token)."""
    if confirmed_device(user) is not None:
        return False
    found = requirement(user, competition, request=request)
    return found is not None and found.overdue()


# --- blokada po serii złych kodów ---------------------------------------------------------------


def _fail_key(user) -> str:
    return f"2fa:fails:{user.pk}"


def _lock_key(user) -> str:
    return f"2fa:lock:{user.pk}"


def locked_until(user) -> float | None:
    """Znacznik czasu końca blokady albo ``None``. Awaria cache'a = brak blokady (jak limity)."""
    from django.core.cache import cache

    value = cache.get(_lock_key(user))
    return float(value) if value and float(value) > time.time() else None


def is_locked(user) -> bool:
    return locked_until(user) is not None


def lock_minutes_left(user) -> int:
    until = locked_until(user)
    return max(1, int((until - time.time() + 59) // 60)) if until else 0


def _register_failure(user, request=None) -> None:
    """Liczy złą próbę kodu; piąta w oknie zakłada blokadę, audyt i list do właściciela."""
    from django.core.cache import cache

    from apps.core.models import audit

    key = _fail_key(user)
    cache.add(key, 0, LOCKOUT_WINDOW_SECONDS)
    try:
        count = cache.incr(key)
    except ValueError:  # klucz wygasł między ``add`` a ``incr``
        cache.set(key, 1, LOCKOUT_WINDOW_SECONDS)
        count = 1
    if count < LOCKOUT_THRESHOLD:
        return
    cache.set(_lock_key(user), time.time() + LOCKOUT_SECONDS, LOCKOUT_SECONDS)
    cache.delete(key)
    audit(user, "2fa.locked", user, {"seconds": LOCKOUT_SECONDS}, request)
    _notify(user, "locked", request=request, minutes=LOCKOUT_SECONDS // 60)


def _clear_failures(user) -> None:
    from django.core.cache import cache

    cache.delete(_fail_key(user))


def _notify(user, event: str, *, request=None, **params) -> None:
    """List do właściciela (``apps.staff_mfa.notifications``). Awaria składania listu nie cofa zdarzenia."""
    try:
        from apps.staff_mfa.notifications import notify

        notify(user, event, request=request, **params)
    except Exception:  # noqa: BLE001 - list jest skutkiem ubocznym, nie warunkiem czynności
        logger.exception("2FA: nie udało się zakolejkować listu %s.", event)


# --- czynności ----------------------------------------------------------------------------------


def begin_setup(user) -> TwoFactorDevice:
    """Zakłada (albo nadpisuje) niepotwierdzony wiersz z nowym sekretem i zwraca go.

    Nadpisanie dotyczy **wyłącznie** wiersza niepotwierdzonego. Konto z działającym drugim
    składnikiem, które wejdzie na ekran konfiguracji, nie może stracić sekretu przez samo
    otwarcie strony – inaczej przypadkowe kliknięcie w menu odcinałoby człowieka od konta.
    """
    device = device_for(user)
    if device is not None and device.is_confirmed:
        return device
    if device is None:
        device = TwoFactorDevice(user=user)
    device.secret = encrypt_secret(generate_secret())
    device.backup_codes = []
    device.last_counter = 0
    device.created_at = timezone.now()
    device.save()
    return device


def confirm_setup(user, code: str, *, request=None) -> list[str]:
    """Potwierdza konfigurację kodem z aplikacji. Zwraca kody zapasowe (jedyny raz, kiedy je widać).

    Potwierdzenie kodem, a nie samym kliknięciem „gotowe”: bez niego człowiek, który źle
    zeskanował QR, dowiedziałby się o tym dopiero przy następnym logowaniu – czyli wtedy, gdy nie
    ma już jak tego naprawić samodzielnie.

    ``DomainError`` (a nie ``ValueError``), bo tę odmowę pokazuje użytkownikowi warstwa WWW razem
    z resztą odmów domenowych.
    """
    from apps.core.api import DomainError
    from apps.core.models import audit

    device = device_for(user)
    if device is None:
        raise DomainError(_("Najpierw zeskanuj kod QR – konfiguracja nie została rozpoczęta."))
    if device.is_confirmed:
        raise DomainError(_("Drugi składnik jest już włączony na tym koncie."))
    if not _check_totp(device, code, save=False):
        audit(user, "2fa.failed", user, {"stage": "setup"}, request)
        raise DomainError(
            _("Kod z aplikacji nie pasuje. Sprawdź, czy zegar w telefonie jest ustawiony automatycznie.")
        )

    plain, hashed = generate_backup_codes()
    device.confirmed_at = timezone.now()
    device.backup_codes = hashed
    device.last_counter = current_counter()
    device.last_used_at = timezone.now()
    device.save(update_fields=["confirmed_at", "backup_codes", "last_counter", "last_used_at"])
    # Token API wydany po samym haśle traci ważność razem z włączeniem drugiego składnika:
    # następny powstaje dopiero w logowaniu z kodem (``apps.accounts.api.LoginView``).
    from rest_framework.authtoken.models import Token

    Token.objects.filter(user=user).delete()
    audit(user, "2fa.enabled", user, {"backup_codes": len(hashed)}, request)
    _notify(user, "enabled", request=request)
    return plain


def disable(user, *, actor=None, request=None) -> bool:
    """Wyłącza drugi składnik na własnym koncie. Zwraca ``False``, gdy nie było czego wyłączać.

    Bez sprawdzania poświadczeń – to robi :func:`check_credentials` w ekranie właściciela.
    """
    from apps.core.models import audit

    device = device_for(user)
    if device is None:
        return False
    was_confirmed = device.is_confirmed
    device.delete()
    audit(actor or user, "2fa.disabled", user, {}, request)
    if was_confirmed:
        from apps.staff_mfa.policy import close_grace

        close_grace(user, _competition_or_context(getattr(request, "competition", None)))
        _notify(user, "disabled", request=request)
    return True


def check_credentials(user, password: str, code: str, *, request=None) -> None:
    """Hasło **i** bieżący kod (z aplikacji albo zapasowy) – przed wyłączeniem i nowym kompletem kodów.

    Dlaczego oba, skoro sesja przeszła już drugi składnik: sesja bywa porzucona (komputer w pokoju
    komisji, „zapamiętane urządzenie”), a te dwie czynności są dokładnie tym, czego potrzebuje ktoś,
    kto chce zabezpieczenie zdjąć albo przejąć na stałe (nowe kody = dostęp bez telefonu). Hasło
    sprawdza ``check_password`` (bez ``authenticate``: konto jest już zalogowane, a backendy
    zewnętrzne nie mają tu nic do powiedzenia). Zły kod liczy się do blokady konta tak samo, jak
    przy logowaniu – inaczej ten formularz byłby wyrocznią do zgadywania kodów.
    """
    from apps.core.api import DomainError

    if not password or not user.check_password(password):
        raise DomainError(_("Hasło się nie zgadza."), "TWO_FACTOR_BAD_PASSWORD", 400)
    if is_locked(user):
        raise DomainError(
            _("Zbyt wiele błędnych kodów. Spróbuj ponownie za %(minutes)s min.")
            % {"minutes": lock_minutes_left(user)},
            "TWO_FACTOR_LOCKED",
            429,
        )
    if not verify(user, code, request=request):
        raise DomainError(_("Kod nie pasuje."), "TWO_FACTOR_INVALID", 400)


def regenerate_backup_codes(user, *, request=None) -> list[str]:
    """Nowy komplet kodów zapasowych; poprzednie przestają działać. Poświadczenia sprawdza wołający."""
    from apps.core.api import DomainError
    from apps.core.models import audit

    device = confirmed_device(user)
    if device is None:
        raise DomainError(_("Na tym koncie nie ma włączonego drugiego składnika."))
    plain, hashed = generate_backup_codes()
    device.backup_codes = hashed
    device.save(update_fields=["backup_codes"])
    audit(user, "2fa.codes_regenerated", user, {"backup_codes": len(hashed)}, request)
    _notify(user, "codes_regenerated", request=request)
    return plain


def reset_by_coordinator(user, *, actor, request=None) -> bool:
    """Kasuje drugi składnik cudzego konta – „zgubiłem telefon” załatwiane przez organizatora.

    Osobna czynność od :func:`disable` wyłącznie dla audytu, i to nie jest kosmetyka: wpis
    ``2fa.reset`` z nazwiskiem koordynatora jest jedyną rzeczą, która po fakcie odróżnia „członek
    komisji zgubił telefon” od „ktoś przejął konto koordynatora i zdejmował zabezpieczenia”.

    Kod zapasowy jest drogą pierwszą, a ta – drugą. Dlatego reset zostawia konto **bez** drugiego
    składnika (a nie z nowym sekretem): człowiek, który stracił telefon, ma po tym wejść hasłem
    i skonfigurować 2FA od nowa na nowym urządzeniu.

    SEC-01: konto **personelu** resetuje wyłącznie superkoordynator (``staff_mfa.policy.may_reset``)
    – sprawdzane tutaj, w serwisie, a nie tylko w widoku. Właściciel dostaje list.
    """
    from django.core.exceptions import PermissionDenied

    from apps.core.models import audit
    from apps.staff_mfa.policy import may_reset

    competition = _competition_or_context(
        getattr(request, "competition", None) if request is not None else None
    )
    if not may_reset(actor, user, competition):
        raise PermissionDenied(
            "Drugi składnik konta personelu zdejmuje wyłącznie superkoordynator (docs/OPERACJE.md § 41)."
        )
    device = device_for(user)
    if device is None:
        return False
    device.delete()
    from apps.staff_mfa.policy import close_grace

    close_grace(user, competition)
    audit(actor, "2fa.reset", user, {"email": bool(user.email)}, request)
    _notify(user, "reset", request=request)
    return True


def _check_totp(device: TwoFactorDevice, code: str, *, save: bool = True) -> bool:
    """Sprawdza kod TOTP w oknie ±``TIME_WINDOW_STEPS`` i pilnuje jednorazowości.

    Porównanie przez ``hmac.compare_digest``: różnica czasu wykonania przy porównaniu napisów
    zdradza, ile pierwszych cyfr się zgadza, a przy sześciu cyfrach to jest różnica między
    milionem prób a sześćdziesięcioma.
    """
    secret = device.plain_secret()
    if not secret:
        return False
    digits = "".join(character for character in (code or "") if character.isdigit())
    if len(digits) != CODE_DIGITS:
        return False
    now = current_counter()
    for step in range(-TIME_WINDOW_STEPS, TIME_WINDOW_STEPS + 1):
        counter = now + step
        # Kod z kroku już użytego (albo starszego) jest spalony – patrz docstring modułu.
        if counter <= device.last_counter:
            continue
        if hmac.compare_digest(totp_code(secret, counter), digits):
            if save:
                # Warunkowy ``UPDATE`` zamiast ``save()`` (SEC-01 § 5): dwa równoległe żądania
                # z tym samym kodem czytają ten sam ``last_counter`` i oba przechodziły sprawdzenie
                # wyżej. Baza przyjmuje krok **raz** – drugie żądanie dostaje zero wierszy.
                now_dt = timezone.now()
                accepted = TwoFactorDevice.objects.filter(pk=device.pk, last_counter__lt=counter).update(
                    last_counter=counter, last_used_at=now_dt
                )
                if accepted != 1:
                    return False
                device.last_counter = counter
                device.last_used_at = now_dt
            return True
    return False


def _check_backup_code(device: TwoFactorDevice, code: str) -> bool:
    """Sprawdza kod zapasowy i **zużywa** go. Jednorazowość jest tu całą wartością tych kodów.

    Pod ``select_for_update``: lista kodów jest jednym polem JSON, więc dwa równoległe żądania
    z tym samym kodem bez blokady wiersza oba znajdowały go na liście i oba wpuszczały.
    """
    from django.db import transaction

    normalized = normalize_backup_code(code)
    if len(normalized) != BACKUP_CODE_LENGTH:
        return False
    digest = hash_backup_code(normalized)
    with transaction.atomic():
        locked = TwoFactorDevice.objects.select_for_update().filter(pk=device.pk).first()
        if locked is None:
            return False
        remaining = list(locked.backup_codes or [])
        for index, stored in enumerate(remaining):
            if hmac.compare_digest(str(stored), digest):
                del remaining[index]
                locked.backup_codes = remaining
                locked.last_used_at = timezone.now()
                locked.save(update_fields=["backup_codes", "last_used_at"])
                device.backup_codes = remaining
                device.last_used_at = locked.last_used_at
                return True
    return False


def verify(user, code: str, *, request=None) -> bool:
    """Sprawdza kod przy logowaniu: najpierw TOTP, potem kody zapasowe. Zapisuje audyt.

    Kolejność wynika z częstości, nie z bezpieczeństwa: sześciocyfrowy kod z aplikacji jest tym,
    co ludzie wpisują w 99 przypadkach na 100, a kod zapasowy ma inną długość, więc pomyłka
    „wpisałem nie to pole” nie istnieje.

    Konto w blokadzie (:func:`is_locked`) dostaje ``False`` **bez sprawdzania kodu** – inaczej
    blokada byłaby wyłącznie spowolnieniem, a odpowiedź „dobry kod” nadal wyciekałaby w trakcie.
    """
    from apps.core.models import audit

    device = confirmed_device(user)
    if device is None:
        return False
    if is_locked(user):
        audit(user, "2fa.failed", user, {"stage": "locked"}, request)
        return False
    if _check_totp(device, code):
        _clear_failures(user)
        audit(user, "2fa.verified", user, {"method": "totp"}, request)
        return True
    if _check_backup_code(device, code):
        _clear_failures(user)
        audit(
            user,
            "2fa.verified",
            user,
            {"method": "backup", "codes_left": device.backup_codes_left},
            request,
        )
        _notify(user, "backup_used", request=request, codes_left=device.backup_codes_left)
        return True
    audit(user, "2fa.failed", user, {"stage": "login"}, request)
    _register_failure(user, request)
    return False


# --- sesja i wymuszanie ---------------------------------------------------------------------------


def mark_verified(request) -> None:
    """Zapisuje w sesji, że to konto przeszło drugi składnik."""
    request.session[SESSION_VERIFIED_KEY] = request.user.pk


def session_is_verified(request) -> bool:
    return request.session.get(SESSION_VERIFIED_KEY) == getattr(request.user, "pk", None)


def clear_session_markers(request) -> None:
    """Kasuje wszystkie znaczniki tej warstwy z sesji – następne żądanie liczy bramkę od nowa."""
    for key in (SESSION_VERIFIED_KEY, SESSION_EXEMPT_KEY, SESSION_GRACE_KEY):
        request.session.pop(key, None)


def _from_timestamp(value: float):
    from datetime import UTC, datetime

    return datetime.fromtimestamp(float(value), tz=UTC)


class TwoFactorMiddleware:
    """Zamyka zalogowaną sesję w poczekalni, dopóki nie przejdzie drugiego składnika.

    Dlaczego warstwa pośrednicząca, a nie doklejenie kroku do widoku logowania: dróg do
    zalogowanej sesji jest kilka (formularz ``/login/``, logowanie przez Google, panel
    ``/admin/``), a każda z nich, o której byśmy zapomnieli, byłaby obejściem całego mechanizmu.
    Tutaj reguła jest jedna i obowiązuje **każde** żądanie zalogowanego konta.

    Sesja po samym haśle jest technicznie zalogowana (``request.user`` jest ustawiony) i to jest
    świadomy kompromis: alternatywą jest trzymanie „połowicznego” logowania w sesji i ręczne
    wołanie ``auth.login`` po kodzie, co przy logowaniu przez dostawcę zewnętrznego wymaga
    przepisania przepływu allauth. Cena kompromisu jest tutaj ograniczona do zera **pod warunkiem**,
    że przepuszczamy wyłącznie adresy z listy niżej – i dlatego ta lista jest tak krótka.

    Koszt: jedno zapytanie do bazy na sesję. Po przejściu bramki (albo po stwierdzeniu, że konto
    drugiego składnika nie ma) sesja niesie znacznik i kolejne żądania nie pytają już o nic.
    Postawienie znacznika kontu bez 2FA nie jest luką: samo włączenie 2FA później w tej samej
    sesji jest dowodem posiadania telefonu, a wylogowanie czyści sesję w całości.

    SEC-01: konto bez urządzenia dostaje znacznik **konkursu** (``2fa_exempt``) albo znacznik okresu
    przejściowego z terminem (``2fa_grace``; ``request.two_factor_grace_until`` czyta baner). Po
    terminie – ta sama poczekalnia „skonfiguruj”, co dotąd, dla **całej** sesji, a nie tylko paneli:
    sesja po samym haśle na koncie personelu nie może też zmienić adresu e-mail ani zabrać eksportu
    danych z ``/account/``. Konto z urządzeniem i ważnym ciasteczkiem „zapamiętaj to urządzenie”
    (``apps.staff_mfa.trust``) przechodzi bramkę bez kodu.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Wyłącznik główny na samym początku i **przed** dotknięciem sesji: przy wyłączonej funkcji
        # ta warstwa ma nie kosztować ani jednego zapytania, ani jednego zapisu do sesji. Dotyczy
        # to również kont z potwierdzonym urządzeniem – one też logują się wtedy samym hasłem.
        if not is_enabled():
            return self.get_response(request)
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or session_is_verified(request):
            return self.get_response(request)

        # Odpowiedzi zapamiętane w sesji dla konta **bez** urządzenia (SEC-01 § 3): „nie musisz”
        # i „masz czas do …” – obie dla jednego konkursu. Dzięki nim polityka nie kosztuje
        # zapytania na każdym żądaniu, a termin okresu przejściowego i tak działa w trakcie sesji.
        from apps.staff_mfa.policy import version

        # Znacznik = [konto, konkurs, wersja polityki]: zmiana polityki albo konkurs pod prefiksem
        # ścieżki (wspólna sesja) liczy bramkę od nowa.
        marker = [user.pk, getattr(getattr(request, "competition", None), "pk", None), version()]
        if request.session.get(SESSION_EXEMPT_KEY) == marker:
            return self.get_response(request)
        grace = request.session.get(SESSION_GRACE_KEY)
        if isinstance(grace, list) and grace[:3] == marker and time.time() < grace[3]:
            request.two_factor_grace_until = _from_timestamp(grace[3])
            return self.get_response(request)

        target = self._required_step(request, user, marker)
        if target is None:
            return self.get_response(request)
        if self._is_allowed(request, target):
            return self.get_response(request)
        return self._stop(request, target)

    def _required_step(self, request, user, marker) -> str | None:
        """Nazwa adresu, na który trzeba odesłać: weryfikacja, konfiguracja albo ``None``.

        ``None`` zostawia w sesji znacznik (zwolnienie, okres przejściowy albo potwierdzenie przez
        zapamiętane urządzenie), więc kolejne żądania nie pytają już bazy.
        """
        from apps.core.models import audit
        from apps.staff_mfa import trust

        device = confirmed_device(user)
        if device is not None:
            if trust.is_trusted(request, user, device):
                mark_verified(request)
                audit(user, "2fa.remembered", user, {}, request)
                return None
            return "web:twofactor-verify"
        found = requirement(user, getattr(request, "competition", None), request=request)
        if found is None:
            # Konto bez drugiego składnika i bez obowiązku jego posiadania: bramka jest przejściem
            # otwartym, więc stawiamy znacznik i nie pytamy o to bazy przy każdym kolejnym żądaniu.
            request.session[SESSION_EXEMPT_KEY] = marker
            return None
        if not found.overdue():
            request.session[SESSION_GRACE_KEY] = [*marker, found.deadline.timestamp()]
            request.two_factor_grace_until = found.deadline
            return None
        return "web:twofactor-setup"

    def _is_allowed(self, request, target: str) -> bool:
        """Czy ten adres wolno obsłużyć bez drugiego składnika.

        Lista zależy od **kroku**, na który czeka to konto, i to jest tu rzecz najważniejsza.
        Wspólna lista dla obu kroków przepuszczałaby sesję po samym haśle na ``/account/2fa/…``,
        czyli także na ``/account/2fa/disable/`` – i cały mechanizm dałby się zdjąć jednym POST-em
        przez kogoś, kto zna wyłącznie hasło. Konto czekające na kod widzi więc tylko ekran kodu.
        """
        path = request.path
        return any(path.startswith(prefix) for prefix in _allowed_prefixes(target))

    def _stop(self, request, target: str):
        """Przekierowanie dla przeglądarki, 403 dla API – każdy dostaje odpowiedź, którą rozumie."""
        from django.http import JsonResponse

        if request.path.startswith("/api/"):
            return JsonResponse(
                {"detail": _("Wymagane potwierdzenie drugiego składnika logowania.")}, status=403
            )
        return redirect(target)


#: Adresy publiczne i bezstanowe, dostępne niezależnie od kroku: strona statusu (czytana dokładnie
#: wtedy, gdy coś nie działa), sonda orkiestratora i pliki statyczne. Żaden z nich nie robi niczego
#: w imieniu konta, więc przepuszczenie ich nie jest ustępstwem.
_PUBLIC_PREFIXES = ("/status/", "/status.json", "/healthz/", "/static/")


@lru_cache(maxsize=4)
def _allowed_prefixes(target: str) -> tuple[str, ...]:
    """Adresy dostępne dla sesji czekającej na krok ``target``. Liczone raz, z urlconfa.

    Z ``reverse``, a nie z literałów: adres ekranu weryfikacji jest zapisany w jednym miejscu
    (``apps/web/urls.py``) i literał w tym module rozjechałby się z nim po cichu – a skutkiem
    byłoby przekierowanie w pętli na ekranie logowania.

    Przepuszczamy **wyłącznie** adres tego jednego kroku (plus wylogowanie i ustawienia
    prezentacji, które nie dotykają uprawnień). Dołożenie tu drugiego kroku „na wszelki wypadek”
    otworzyłoby sesji czekającej na kod drogę do ``/account/2fa/disable/``.
    """
    from django.urls import NoReverseMatch, reverse

    prefixes = []
    for name in (target, "web:logout", "web:account-preferences"):
        try:
            prefixes.append(reverse(name))
        except NoReverseMatch:  # pragma: no cover - wyłapuje literówkę w nazwie adresu
            logger.error("2FA: nie znam adresu %s – warstwa wymuszająca go nie przepuści.", name)
    return tuple(prefixes) + _PUBLIC_PREFIXES
