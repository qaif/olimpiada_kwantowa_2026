"""Throttling formularzy HTML – ten sam limit, co na odpowiadającym mu endpoincie API.

Powód istnienia modułu (przegląd T-08, ustalenie 1): limity DRF (``ScopedRateThrottle``) chronią
wyłącznie ``/api/…``. Ktoś, kto zgaduje hasła, i tak wyśle POST na ``/login/`` – formularz robi
dokładnie to samo, co ``POST /api/auth/login/``, więc bez tego mixinu limit był ozdobą.

Zasady:

- **jedno źródło stawek.** Wartości czytamy przez ``rest_framework.settings.api_settings``, czyli
  z tego samego obiektu, z którego korzystają throttle'e DRF. Zmiana ``REST_FRAMEWORK``
  (``override_settings``, plik ``settings/test.py``) przestawia więc API i UI jednocześnie –
  rozjazd konfiguracji nie jest możliwy, bo nie ma drugiej konfiguracji,
- ``rate is None`` (tak jest w ``config/settings/test.py``) **wyłącza** limit. Ta sama umowa,
  co w ``SimpleRateThrottle``: testy nie mają walczyć z licznikiem,
- licznik siedzi w cache'u (``django_redis`` produkcyjnie, ``LocMemCache`` w testach). Okno jest
  przesuwne i **dokładne**: kubełek o stawce ``N/okno`` to ``N`` miejsc (osobnych kluczy), a każde
  zajęte miejsce wygasa po pełnym oknie od chwili zajęcia. Dzięki temu ``Retry-After`` jest
  prawdziwy (najstarsze zajęte miejsce + okno), a nie zaokrąglony do końca sztywnego kubełka,
- **zajęcie miejsca jest atomowe** (pakiet 5 po audycie). Do tej zmiany kubełek był listą znaczników
  czasu czytaną ``cache.get`` i zapisywaną ``cache.set`` – a ``check()`` stał osobno przed
  ``consume()``. Równoległe żądania przechodziły więc ``check()`` wszystkie naraz i nadpisywały
  sobie nawzajem historię: seria kilkudziesięciu równoległych POST-ów na ``/login/`` dawała
  kilkakrotnie więcej prób, niż mówiła stawka. Teraz próba **zajmuje** miejsce przez
  ``cache.add`` (w Redisie ``SET NX`` – jedna operacja, której nie da się przepleść); gdy wszystkie
  ``N`` miejsc są zajęte, żądanie dostaje 429. Sprawdzenie i zużycie są jednym krokiem
  (``acquire``), więc nie ma między nimi okna na wyścig,
- **dwa kubełki na żądanie**: sam adres IP oraz para (adres IP + e-mail z POST-a). Przegląd
  prosił o klucz „(scope, ip, e-mail)” – to jest ten węższy kubełek. Sam kubełek IP dochodzi,
  bo bez niego wystarczyłoby zmieniać e-mail w kolejnych żądaniach, żeby limit logowania
  przestał cokolwiek znaczyć (password spraying z jednego adresu). Przekroczenie **któregokolwiek**
  kubełka daje 429,
- **wyjątek: scope'y zalogowanych liczone per konto** (``PER_USER_SCOPES``: czat i forum). Patrz
  komentarz przy stałej – w skrócie: kubełek IP karał całą szkołę za jednym NAT-em, a jedno konto
  przełączające adresy nie miało limitu wcale,
- w cache'u nie ma danych osobowych: adres i e-mail idą do klucza jako skrót SHA-256, a wartością
  miejsca jest wyłącznie znacznik czasu. Podgląd Redisa nie jest więc listą kont, które ktoś
  próbował złamać,
- adres klienta bierzemy z ``apps.core.models.client_ip`` – ta sama reguła zaufania do proxy,
  co w audycie. Nagłówek od nieznanego nadawcy nie wyznacza kubełka,
- **awaria cache'a nie zamyka formularzy, ale nie jest cicha.** ``IGNORE_EXCEPTIONS`` w
  ``CACHES`` (``config/settings/base.py``) każe ``django-redis`` połykać błędy połączenia – bez
  dodatkowego sygnału niedziałający Redis po cichu wyłączał każdy limit w tym module. Zostajemy
  przy przepuszczeniu żądania (zamknięcie logowania na czas awarii Redisa byłoby gorsze: zawody
  mają terminy), ale ``_report_outage`` loguje błąd – raz na proces na minutę, żeby nie zalać logu.

Logowanie liczy **wyłącznie nieudane próby** (``throttle_on_request = False``). Od pakietu 5
miejsce jest **rezerwowane** w ``dispatch`` jeszcze przed sprawdzeniem hasła – inaczej równoległe
próby znowu mijałyby limit – a dopiero wynik je rozstrzyga: ``consume_throttle`` (porażka)
zostawia rezerwację jako zużytą próbę, ``reset_throttle`` (sukces) kasuje kubełki, a rezerwacja
nierozstrzygnięta do końca żądania (np. podgląd w kreatorze konkursu) wraca do puli.

Świadomy kompromis przy zerowaniu: udane logowanie kasuje **oba** kubełki, także ten liczony po
samym adresie IP – tak samo robi ``django-axes`` (``reset_on_success``). Cena jest znana: ktoś,
kto ma jedno prawidłowe konto, może co kilka prób zalogować się poprawnie i wyzerować sobie
licznik adresu, więc rozpylanie haseł po cudzych kontach z jednego IP jest tańsze, niż mówi
sama stawka. Wariant „zeruj tylko kubełek tożsamości” tej dziury nie ma, ale jest w praktyce
pusty: kubełek (IP + e-mail) dostaje dokładnie te same zdarzenia, co kubełek IP, więc nigdy nie
zapełnia się pierwszy i jego wyzerowanie niczego nie zmienia dla użytkownika, który właśnie
dwukrotnie pomylił hasło. Docelowe rozwiązanie to osobny, dłuższy licznik per konto (bez IP)
z limitem na tyle wysokim, żeby nie dało się nim zablokować cudzego konta przed deadline'em –
to jest w backlogu, nie w tym tasku.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time

from django.core.cache import cache
from django.template.response import TemplateResponse
from django.utils.translation import gettext, gettext_noop
from rest_framework.settings import api_settings

from apps.core.models import client_ip

logger = logging.getLogger(__name__)

#: Prefiks kluczy w cache'u. Własny, żeby nie kolidować z kluczami ``SimpleRateThrottle`` DRF.
CACHE_PREFIX = "web-throttle"

#: Mnożniki okresów w zapisie stawek DRF („10/min”, „30/hour”). Liczy się pierwsza litera.
PERIOD_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

#: Pola POST, w których może siedzieć identyfikator konta. ``username`` – bo tak nazywa się pole
#: loginu w ``AuthenticationForm``, mimo że trzyma adres e-mail (apps/web/forms.py).
IDENTITY_FIELDS = ("email", "username")

#: Scope'y liczone **per konto, bez kubełka IP** (pakiet 5 po audycie). Oba obsługują wyłącznie
#: widoki za logowaniem (czat 1:1, forum uczestników), więc tożsamość nadawcy jest pewna, a koszt,
#: który limit ma ograniczać (zasypanie rozmowy, kolejki moderacji), przypada na konto, nie na sieć:
#:
#: - kubełek IP karał **całą szkołę za jednym NAT-em**: trzydzieści osób z pracowni dzieliło jeden
#:   budżet 60 wiadomości na godzinę, więc jedna aktywna rozmowa blokowała pozostałe,
#: - jedno konto przełączające adresy (telefon: Wi-Fi ↔ sieć komórkowa, VPN) dostawało **nowy
#:   budżet z każdym adresem** – limit nie trzymał się tego, kto pisze,
#: - „wiele kont za jednym adresem” nie wraca tylnymi drzwiami: założenie konta przechodzi przez
#:   CAPTCHA, aktywację e-mailem i scope ``register`` (10/h/IP), więc każde dodatkowe konto
#:   kosztuje więcej niż sześćdziesiąt wiadomości, które przynosi.
#:
#: Stawki zostają w ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`` – zmienia się wyłącznie klucz.
#:
#: ``video`` i ``video_rooms`` (v0.39.0) – wejście do pokoju wideo i zakładanie pokoi z długimi
#: linkami. Też wyłącznie za logowaniem, a koszt (wystawione przepustki) też przypada na konto:
#: komisja rozmawiająca z jednej sali za jednym NAT-em nie może dzielić jednego budżetu wejść.
#:
#: ``webinar_join`` (WEB-01) – token wejścia na webinar i czynności prowadzącego, z tego samego powodu.
#: ``webinar_control`` – polecenia prowadzącego (osobny, wyższy kubełek).
#: ``delegation`` (DEL-01) – opiekun drużyny i koordynator są zalogowani, a koszt (listy na wpisane
#: adresy) przypada na konto: dwie delegacje z jednej szkoły za jednym NAT-em nie dzielą budżetu.
#:
#: ``onsite_logistics`` i ``onsite_checkin`` (LOG-01) – opiekun, oficer logistyki i obsługa
#: rejestracji są zalogowani; obsługa przy wejściu skanuje z kilku telefonów za jednym Wi-Fi.
#:
#: ``translation`` (TR-01) – edytor tłumaczeń zadań: autozapis i czynności zalogowanych opiekunów.
PER_USER_SCOPES = frozenset(
    {
        "chat",
        "forum",
        "video",
        "video_rooms",
        "delegation",
        "webinar_join",
        "webinar_control",
        "translation",
        "onsite_logistics",
        "onsite_checkin",
    }
)

#: Jak często (sekundy) wolno zalogować awarię cache'a jednym procesem – patrz ``_report_outage``.
OUTAGE_LOG_INTERVAL = 60

#: Komunikat odmowy. ``gettext_noop``: stała zostaje zwykłym ``str``, tłumaczy ją ``throttled_response``
#: w chwili składania odpowiedzi, czyli w języku żądania.
THROTTLE_MESSAGE = gettext_noop("Zbyt wiele prób z tego adresu. Odczekaj chwilę i spróbuj ponownie.")

#: Dozwolony kształt identyfikatora z nagłówka ``HX-Target`` (nasze szablony generują np.
#: ``problem-12``). Wartość idzie do ``HX-Retarget`` jako selektor CSS, więc nie może nieść
#: nawiasów, spacji ani znaków sterujących nagłówka.
SAFE_TARGET_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")

#: Chwila ostatniego wpisu o awarii cache'a w tym procesie (``time.monotonic``). Stan modułu, a nie
#: cache'a – ten właśnie nie działa.
_last_outage_report: float | None = None


def form_rate(scope: str) -> str | None:
    """Stawka dla scope'u, prosto z konfiguracji DRF (``DEFAULT_THROTTLE_RATES``)."""
    return (api_settings.DEFAULT_THROTTLE_RATES or {}).get(scope)


def parse_rate(rate: str | None) -> tuple[int, int] | None:
    """``"10/min"`` → ``(10, 60)``. ``None`` (limit wyłączony) i śmieci → ``None``."""
    if not rate:
        return None
    count, _, period = str(rate).partition("/")
    try:
        num_requests = int(count)
    except ValueError:
        return None
    seconds = PERIOD_SECONDS.get(period[:1].lower()) if period else None
    if seconds is None or num_requests <= 0:
        return None
    return num_requests, seconds


def _digest(value: str) -> str:
    """Skrót do klucza cache'a – w Redisie nie lądują ani adresy IP, ani adresy e-mail."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def posted_identity(request) -> str:
    """Znormalizowany e-mail z POST-a (``""``, gdy formularz go nie ma)."""
    if request.method != "POST":
        return ""
    for field in IDENTITY_FIELDS:
        value = (request.POST.get(field) or "").strip().lower()
        if value:
            return value[:254]
    return ""


def throttle_keys(scope: str, request, identity: str | None = None) -> list[str]:
    """Kubełki dla żądania: adres IP oraz – gdy znamy e-mail – para IP+e-mail.

    ``identity`` podaje się jawnie tam, gdzie e-maila nie ma w POST-cie, a mimo to wiadomo, o czyje
    konto chodzi: formularz nowego hasła (``/reset/<uid>/<token>/``) niesie wyłącznie hasła, a to
    właśnie tam trzeba wyzerować kubełek logowania właściciela konta.
    """
    address = client_ip(request) or "unknown"
    keys = [f"{CACHE_PREFIX}:{scope}:ip:{_digest(address)}"]
    value = posted_identity(request) if identity is None else (identity or "").strip().lower()[:254]
    if value:
        keys.append(f"{CACHE_PREFIX}:{scope}:id:{_digest(f'{address}|{value}')}")
    return keys


def user_throttle_keys(scope: str, request) -> list[str]:
    """Jedyny kubełek scope'u z ``PER_USER_SCOPES``: konto, bez adresu IP.

    Niezalogowany (do tych widoków nie dochodzi – mixiny ról stoją przed limitem, ale funkcja nie
    zakłada kolejności mixinów) schodzi na zwykłe kubełki adresu, a nie na brak limitu.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return throttle_keys(scope, request)
    return [f"{CACHE_PREFIX}:{scope}:user:{_digest(str(user.pk))}"]


def _report_outage(operation: str, exc: BaseException | None = None) -> None:
    """Awaria cache'a = limit nie działa. Żądanie przechodzi, ale operator ma to zobaczyć.

    Raz na ``OUTAGE_LOG_INTERVAL`` sekund na proces: przy niedziałającym Redisie każde żądanie
    formularza trafiałoby tutaj, a log zalany tysiącem identycznych wpisów jest tak samo ślepy,
    jak log pusty. W treści nie ma klucza (skrót adresu IP) ani scope'u konkretnego żądania.
    """
    global _last_outage_report
    now = time.monotonic()
    if _last_outage_report is not None and now - _last_outage_report < OUTAGE_LOG_INTERVAL:
        return
    _last_outage_report = now
    logger.error(
        "Limit prób formularzy nie działa: cache nie odpowiada (%s). Żądania są przepuszczane bez limitu.",
        operation,
        exc_info=exc,
    )


def _slot_keys(key: str, num_requests: int) -> list[str]:
    """Klucze ``N`` miejsc kubełka. Miejsce = jedna próba w oknie; wartością jest jej znacznik czasu."""
    return [f"{key}:s{index}" for index in range(num_requests)]


def _occupied(key: str, num_requests: int, now: float, duration: int) -> dict[str, float]:
    """Zajęte miejsca kubełka (klucz → znacznik czasu) z bieżącego okna."""
    stored = cache.get_many(_slot_keys(key, num_requests)) or {}
    return {
        slot: moment
        for slot, moment in stored.items()
        if isinstance(moment, (int, float)) and moment > now - duration
    }


def _wait(occupied: dict[str, float], now: float, duration: int) -> float:
    """Ile sekund do zwolnienia najstarszego miejsca pełnego kubełka."""
    if not occupied:
        return 1.0
    return max(0.0, min(occupied.values()) + duration - now)


def _take_slot(key: str, num_requests: int, now: float, duration: int) -> tuple[str | None, float | None]:
    """Zajmuje jedno wolne miejsce kubełka. ``(klucz miejsca, None)`` albo ``(None, ile czekać)``.

    ``cache.add`` jest atomowe (Redis: ``SET NX``; ``LocMemCache`` pod własną blokadą) – dwa
    równoległe żądania nie zajmą tego samego miejsca, a przegrany próbuje kolejnego wolnego.
    Miejsca widziane jako wolne, ale zajęte w międzyczasie przez innych, liczymy jako pełny
    kubełek: ostrożniej o jedno żądanie, nigdy hojniej niż stawka.

    ``(None, None)`` = cache nie działa (``django-redis`` z ``IGNORE_EXCEPTIONS`` zwraca wtedy
    ``None`` zamiast ``True``/``False``) – wołający przepuszcza żądanie.
    """
    occupied = _occupied(key, num_requests, now, duration)
    for slot in _slot_keys(key, num_requests):
        if slot in occupied:
            continue
        added = cache.add(slot, now, duration)
        if added is None:
            _report_outage("add")
            return None, None
        if added:
            return slot, None
    return None, _wait(occupied, now, duration)


def check(scope: str, keys: list[str]) -> float | None:
    """Ile sekund zostało do zwolnienia limitu. ``None`` = można przepuścić żądanie.

    Wyłącznie **odczyt** – nic nie rezerwuje. Do decyzji „przepuścić i policzyć” służy
    ``acquire``: osobne ``check`` + ``consume`` to dokładnie ten wyścig, który usunął pakiet 5.
    """
    rate = parse_rate(form_rate(scope))
    if rate is None:
        return None
    num_requests, duration = rate
    now = time.time()
    wait: float | None = None
    try:
        for key in keys:
            occupied = _occupied(key, num_requests, now, duration)
            if len(occupied) >= num_requests:
                remaining = _wait(occupied, now, duration)
                wait = remaining if wait is None else max(wait, remaining)
    except Exception as exc:  # noqa: BLE001 - awaria cache'a nie zamyka formularza (docstring modułu)
        _report_outage("get", exc)
        return None
    return wait


def acquire(scope: str, keys: list[str]) -> tuple[float | None, list[str]]:
    """Atomowo zajmuje po jednym miejscu w **każdym** kubełku. ``(None, miejsca)`` albo ``(czekaj, [])``.

    Pełny którykolwiek kubełek = odmowa, a miejsca zajęte już w pozostałych wracają do puli –
    odbite żądanie niczego nie zużywa (tak samo jak przed zmianą: odmowa nie wydłuża blokady).
    Zwrócone miejsca wołający może oddać ``release`` (rezerwacja przy logowaniu).
    """
    rate = parse_rate(form_rate(scope))
    if rate is None:
        return None, []
    num_requests, duration = rate
    now = time.time()
    taken: list[str] = []
    wait: float | None = None
    try:
        for key in keys:
            slot, key_wait = _take_slot(key, num_requests, now, duration)
            if slot is not None:
                taken.append(slot)
            elif key_wait is not None:
                wait = key_wait if wait is None else max(wait, key_wait)
            # ``(None, None)``: cache nie działa – ``_take_slot`` już to zgłosił, kubełek pomijamy.
    except Exception as exc:  # noqa: BLE001 - awaria cache'a nie zamyka formularza (docstring modułu)
        _report_outage("add", exc)
        return None, taken
    if wait is not None:
        release(taken)
        return wait, []
    return None, taken


def release(slots: list[str]) -> None:
    """Oddaje zarezerwowane miejsca – próba, która ostatecznie się nie liczy."""
    if not slots:
        return
    try:
        cache.delete_many(slots)
    except Exception as exc:  # noqa: BLE001
        _report_outage("delete", exc)


def consume(scope: str, keys: list[str]) -> None:
    """Dopisuje próbę do każdego kubełka (zajmuje miejsce, o ile jest wolne)."""
    acquire(scope, keys)


def reset(scope: str, keys: list[str]) -> None:
    """Kasuje kubełki – po udanym logowaniu nieudane próby przestają się liczyć."""
    if not keys:
        return
    rate = parse_rate(form_rate(scope))
    if rate is None:
        return
    num_requests, _ = rate
    slots = [slot for key in keys for slot in _slot_keys(key, num_requests)]
    try:
        cache.delete_many(slots)
    except Exception as exc:  # noqa: BLE001
        _report_outage("delete", exc)


def reset_for_identity(scope: str, request, identity: str) -> None:
    """Zeruje kubełki scope'u dla wskazanego e-maila i adresu żądania.

    Po ustawieniu nowego hasła stare, nieudane próby logowania nie mogą blokować wejścia: człowiek,
    który zapomniał hasła, zwykle najpierw wyczerpuje limit zgadywaniem, a dopiero potem prosi
    o reset. Bez tego link z e-maila działałby, a logowanie zaraz po nim – nie.
    """
    reset(scope, throttle_keys(scope, request, identity=identity))


class ThrottledFormMixin:
    """Limit żądań POST dla widoku formularza HTML. ``scope`` jest ten sam, co w API.

    Widok, który ma liczyć dopiero nieudane próby (logowanie), ustawia
    ``throttle_on_request = False`` i sam rozstrzyga rezerwację: ``consume_throttle`` (próba się
    liczy) albo ``reset_throttle`` (sukces, kubełki od zera). Rezerwacja, której widok nie
    rozstrzygnął, wraca do puli po zakończeniu żądania.
    """

    #: Nazwa scope'u z ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``. Pusty = mixin nic nie robi.
    throttle_scope: str = ""
    #: Czy sam POST konsumuje limit (rejestracja, upload), czy dopiero nieudana próba (logowanie).
    throttle_on_request: bool = True
    #: Metody, które liczy limit. Domyślnie wyłącznie POST – formularz HTML. Widok, który wystawia
    #: coś cennego w odpowiedzi na GET (wejście do pokoju wideo: przepustka w przekierowaniu),
    #: dopisuje tu ``"GET"``; GET pozostałych widoków nie zmienia się ani o bajt.
    throttle_methods: tuple[str, ...] = ("POST",)
    throttle_template_name = "web/throttled.html"
    #: Odpowiedź na żądanie HTMX to fragment – pełna strona wylądowałaby w środku karty zadania.
    throttle_template_name_partial = "web/_throttled.html"

    def get_throttle_keys(self, request) -> list[str]:
        if self.throttle_scope in PER_USER_SCOPES:
            return user_throttle_keys(self.throttle_scope, request)
        return throttle_keys(self.throttle_scope, request)

    def dispatch(self, request, *args, **kwargs):
        self.throttle_bucket_keys: list[str] = []
        self._throttle_reserved: list[str] = []
        if request.method in self.throttle_methods and self.throttle_scope:
            self.throttle_bucket_keys = self.get_throttle_keys(request)
            # Sprawdzenie i zużycie w jednym, atomowym kroku – także dla logowania, gdzie miejsce
            # jest na razie tylko rezerwacją (docstring modułu).
            wait, slots = acquire(self.throttle_scope, self.throttle_bucket_keys)
            if wait is not None:
                return self.throttled_response(request, wait)
            if not self.throttle_on_request:
                self._throttle_reserved = slots
                try:
                    return super().dispatch(request, *args, **kwargs)
                finally:
                    release(self._throttle_reserved)
                    self._throttle_reserved = []
        return super().dispatch(request, *args, **kwargs)

    def consume_throttle(self) -> None:
        """Nieudana próba: rezerwacja z ``dispatch`` zostaje w kubełku jako zużyte miejsce."""
        if getattr(self, "_throttle_reserved", None):
            self._throttle_reserved = []
            return
        consume(self.throttle_scope, getattr(self, "throttle_bucket_keys", []))

    def reset_throttle(self) -> None:
        self._throttle_reserved = []
        reset(self.throttle_scope, getattr(self, "throttle_bucket_keys", []))

    def throttled_response(self, request, wait: float):
        """HTTP 429 z ``Retry-After``. Treść jest szablonem, nie gołym tekstem.

        Dla żądań HTMX odpowiedź musi jeszcze **trafić do DOM** (dług T-08). htmx domyślnie nie
        podmienia treści dla kodów 4xx – bez tego uczestnik po przekroczeniu limitu uploadu widział
        stronę bez żadnej zmiany, jakby przycisk był zepsuty. Naprawa ma dwie połowy:

        - klient: ``static/js/app.js`` włącza podmianę dla statusu 429 w ``htmx:beforeSwap``
          (zdarzenie, nie ``hx-on`` – atrybutowy handler wymagałby ``unsafe-eval`` w CSP),
        - serwer: te dwa nagłówki. ``HX-Reswap: beforeend`` dokleja komunikat **wewnątrz** celu,
          zamiast zastąpić nim całą kartę zadania razem z formularzem uploadu, a ``HX-Retarget``
          przypina go do elementu wskazanego przez samo żądanie (nagłówek ``HX-Target``), więc
          reguła nie zna żadnego identyfikatora z szablonu.
        """
        retry_after = max(1, int(wait) + 1)
        is_htmx = bool(request.headers.get("HX-Request"))
        template = self.throttle_template_name_partial if is_htmx else self.throttle_template_name
        response = TemplateResponse(
            request,
            template,
            {"message": gettext(THROTTLE_MESSAGE), "retry_after": retry_after},
            status=429,
        )
        response["Retry-After"] = str(retry_after)
        if is_htmx:
            # Identyfikator przychodzi od klienta, a ląduje w nagłówku i w selektorze CSS – przez
            # sito przechodzą wyłącznie identyfikatory w kształcie, jaki generują nasze szablony.
            target = (request.headers.get("HX-Target") or "").strip()
            if SAFE_TARGET_ID.fullmatch(target):
                response["HX-Retarget"] = f"#{target}"
            response["HX-Reswap"] = "beforeend"
        return response
