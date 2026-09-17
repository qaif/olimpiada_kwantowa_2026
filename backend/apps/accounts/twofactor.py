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
    except (InvalidToken, ValueError, UnicodeDecodeError):
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
        "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(BACKUP_CODE_LENGTH))
        for _ in range(BACKUP_CODE_COUNT)
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
    """Role, od których organizator wymaga drugiego składnika (``TWO_FACTOR_REQUIRED_ROLES``)."""
    configured = getattr(settings, "TWO_FACTOR_REQUIRED_ROLES", None) or []
    return {name.strip() for name in configured if name.strip()}


def is_required_for(user) -> bool:
    """Czy to konto **musi** mieć drugi składnik. Domyślnie (puste ustawienie) – nie musi żadne.

    Wyłącznik główny wygrywa z listą ról i sprawdzamy go **pierwszy**: ``TWO_FACTOR_REQUIRED_ROLES``
    zostawione w ``.env`` z poprzedniej konfiguracji nie może po wyłączeniu funkcji odsyłać
    koordynatora na ekran, którego już nie ma (404 w pętli).
    """
    if not is_enabled():
        return False
    roles = required_roles()
    if not roles or not user or not getattr(user, "is_authenticated", False):
        return False
    return user.groups.filter(name__in=roles).exists()


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
        raise DomainError("Najpierw zeskanuj kod QR – konfiguracja nie została rozpoczęta.")
    if device.is_confirmed:
        raise DomainError("Drugi składnik jest już włączony na tym koncie.")
    if not _check_totp(device, code, save=False):
        audit(user, "2fa.failed", user, {"stage": "setup"}, request)
        raise DomainError(
            "Kod z aplikacji nie pasuje. Sprawdź, czy zegar w telefonie jest ustawiony automatycznie."
        )

    plain, hashed = generate_backup_codes()
    device.confirmed_at = timezone.now()
    device.backup_codes = hashed
    device.last_counter = current_counter()
    device.last_used_at = timezone.now()
    device.save(update_fields=["confirmed_at", "backup_codes", "last_counter", "last_used_at"])
    audit(user, "2fa.enabled", user, {"backup_codes": len(hashed)}, request)
    return plain


def disable(user, *, actor=None, request=None) -> bool:
    """Wyłącza drugi składnik na własnym koncie. Zwraca ``False``, gdy nie było czego wyłączać."""
    from apps.core.models import audit

    device = device_for(user)
    if device is None:
        return False
    device.delete()
    audit(actor or user, "2fa.disabled", user, {}, request)
    return True


def reset_by_coordinator(user, *, actor, request=None) -> bool:
    """Kasuje drugi składnik cudzego konta – „zgubiłem telefon” załatwiane przez organizatora.

    Osobna czynność od :func:`disable` wyłącznie dla audytu, i to nie jest kosmetyka: wpis
    ``2fa.reset`` z nazwiskiem koordynatora jest jedyną rzeczą, która po fakcie odróżnia „członek
    komisji zgubił telefon” od „ktoś przejął konto koordynatora i zdejmował zabezpieczenia”.

    Kod zapasowy jest drogą pierwszą, a ta – drugą. Dlatego reset zostawia konto **bez** drugiego
    składnika (a nie z nowym sekretem): człowiek, który stracił telefon, ma po tym wejść hasłem
    i skonfigurować 2FA od nowa na nowym urządzeniu.
    """
    from apps.core.models import audit

    device = device_for(user)
    if device is None:
        return False
    device.delete()
    audit(actor, "2fa.reset", user, {"email": bool(user.email)}, request)
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
                device.last_counter = counter
                device.last_used_at = timezone.now()
                device.save(update_fields=["last_counter", "last_used_at"])
            return True
    return False


def _check_backup_code(device: TwoFactorDevice, code: str) -> bool:
    """Sprawdza kod zapasowy i **zużywa** go. Jednorazowość jest tu całą wartością tych kodów."""
    normalized = normalize_backup_code(code)
    if len(normalized) != BACKUP_CODE_LENGTH:
        return False
    digest = hash_backup_code(normalized)
    remaining = list(device.backup_codes or [])
    for index, stored in enumerate(remaining):
        if hmac.compare_digest(str(stored), digest):
            del remaining[index]
            device.backup_codes = remaining
            device.last_used_at = timezone.now()
            device.save(update_fields=["backup_codes", "last_used_at"])
            return True
    return False


def verify(user, code: str, *, request=None) -> bool:
    """Sprawdza kod przy logowaniu: najpierw TOTP, potem kody zapasowe. Zapisuje audyt.

    Kolejność wynika z częstości, nie z bezpieczeństwa: sześciocyfrowy kod z aplikacji jest tym,
    co ludzie wpisują w 99 przypadkach na 100, a kod zapasowy ma inną długość, więc pomyłka
    „wpisałem nie to pole” nie istnieje.
    """
    from apps.core.models import audit

    device = confirmed_device(user)
    if device is None:
        return False
    if _check_totp(device, code):
        audit(user, "2fa.verified", user, {"method": "totp"}, request)
        return True
    if _check_backup_code(device, code):
        audit(
            user,
            "2fa.verified",
            user,
            {"method": "backup", "codes_left": device.backup_codes_left},
            request,
        )
        return True
    audit(user, "2fa.failed", user, {"stage": "login"}, request)
    return False


# --- sesja i wymuszanie ---------------------------------------------------------------------------


def mark_verified(request) -> None:
    """Zapisuje w sesji, że to konto przeszło drugi składnik."""
    request.session[SESSION_VERIFIED_KEY] = request.user.pk


def session_is_verified(request) -> bool:
    return request.session.get(SESSION_VERIFIED_KEY) == getattr(request.user, "pk", None)


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

        target = self._required_step(user)
        if target is None:
            # Konto bez drugiego składnika i bez obowiązku jego posiadania: bramka jest przejściem
            # otwartym, więc stawiamy znacznik i nie pytamy o to bazy przy każdym kolejnym żądaniu.
            mark_verified(request)
            return self.get_response(request)
        if self._is_allowed(request, target):
            return self.get_response(request)
        return self._stop(request, target)

    def _required_step(self, user) -> str | None:
        """Nazwa adresu, na który trzeba odesłać: weryfikacja, konfiguracja albo ``None``."""
        if confirmed_device(user) is not None:
            return "web:twofactor-verify"
        if is_required_for(user):
            return "web:twofactor-setup"
        return None

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
                {"detail": "Wymagane potwierdzenie drugiego składnika logowania."}, status=403
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
