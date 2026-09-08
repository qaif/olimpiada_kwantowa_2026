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
  przesuwne – trzymamy znaczniki czasu prób z ostatniego okna, jak robi to DRF. Dzięki temu
  ``Retry-After`` jest prawdziwy, a nie zaokrąglony do końca sztywnego kubełka,
- **dwa kubełki na żądanie**: sam adres IP oraz para (adres IP + e-mail z POST-a). Przegląd
  prosił o klucz „(scope, ip, e-mail)” – to jest ten węższy kubełek. Sam kubełek IP dochodzi,
  bo bez niego wystarczyłoby zmieniać e-mail w kolejnych żądaniach, żeby limit logowania
  przestał cokolwiek znaczyć (password spraying z jednego adresu). Przekroczenie **któregokolwiek**
  kubełka daje 429,
- w cache'u nie ma danych osobowych: adres i e-mail idą do klucza jako skrót SHA-256. Podgląd
  Redisa nie jest więc listą kont, które ktoś próbował złamać,
- adres klienta bierzemy z ``apps.core.models.client_ip`` – ta sama reguła zaufania do proxy,
  co w audycie. Nagłówek od nieznanego nadawcy nie wyznacza kubełka.

Logowanie liczy **wyłącznie nieudane próby** (``throttle_on_request = False`` + ``consume`` w
``form_invalid`` i ``reset`` w ``form_valid``). Inaczej człowiek, który po prostu często się
loguje, blokowałby sam siebie, a limit ma trafiać w zgadywanie haseł.

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
import re
import time

from django.core.cache import cache
from django.template.response import TemplateResponse
from rest_framework.settings import api_settings

from apps.core.models import client_ip

#: Prefiks kluczy w cache'u. Własny, żeby nie kolidować z kluczami ``SimpleRateThrottle`` DRF.
CACHE_PREFIX = "web-throttle"

#: Mnożniki okresów w zapisie stawek DRF („10/min”, „30/hour”). Liczy się pierwsza litera.
PERIOD_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

#: Pola POST, w których może siedzieć identyfikator konta. ``username`` – bo tak nazywa się pole
#: loginu w ``AuthenticationForm``, mimo że trzyma adres e-mail (apps/web/forms.py).
IDENTITY_FIELDS = ("email", "username")

THROTTLE_MESSAGE = "Zbyt wiele prób z tego adresu. Odczekaj chwilę i spróbuj ponownie."

#: Dozwolony kształt identyfikatora z nagłówka ``HX-Target`` (nasze szablony generują np.
#: ``problem-12``). Wartość idzie do ``HX-Retarget`` jako selektor CSS, więc nie może nieść
#: nawiasów, spacji ani znaków sterujących nagłówka.
SAFE_TARGET_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")


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


def _history(key: str, now: float, duration: int) -> list[float]:
    """Znaczniki prób z bieżącego okna, od najnowszej. Stare wpisy wypadają przy odczycie."""
    stored = cache.get(key) or []
    return [moment for moment in stored if moment > now - duration]


def check(scope: str, keys: list[str]) -> float | None:
    """Ile sekund zostało do zwolnienia limitu. ``None`` = można przepuścić żądanie."""
    rate = parse_rate(form_rate(scope))
    if rate is None:
        return None
    num_requests, duration = rate
    now = time.time()
    wait: float | None = None
    for key in keys:
        history = _history(key, now, duration)
        if len(history) >= num_requests:
            # ``num_requests``-ta od końca próba zwalnia miejsce dopiero po pełnym oknie.
            remaining = history[num_requests - 1] + duration - now
            wait = remaining if wait is None else max(wait, remaining)
    return wait


def consume(scope: str, keys: list[str]) -> None:
    """Dopisuje próbę do każdego kubełka."""
    rate = parse_rate(form_rate(scope))
    if rate is None:
        return
    _, duration = rate
    now = time.time()
    for key in keys:
        history = _history(key, now, duration)
        history.insert(0, now)
        cache.set(key, history, duration)


def reset(scope: str, keys: list[str]) -> None:
    """Kasuje kubełki – po udanym logowaniu nieudane próby przestają się liczyć."""
    if not keys:
        return
    cache.delete_many(keys)


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
    ``throttle_on_request = False`` i sam woła ``consume_throttle`` / ``reset_throttle``.
    """

    #: Nazwa scope'u z ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``. Pusty = mixin nic nie robi.
    throttle_scope: str = ""
    #: Czy sam POST konsumuje limit (rejestracja, upload), czy dopiero nieudana próba (logowanie).
    throttle_on_request: bool = True
    throttle_template_name = "web/throttled.html"
    #: Odpowiedź na żądanie HTMX to fragment – pełna strona wylądowałaby w środku karty zadania.
    throttle_template_name_partial = "web/_throttled.html"

    def dispatch(self, request, *args, **kwargs):
        self.throttle_bucket_keys: list[str] = []
        if request.method == "POST" and self.throttle_scope:
            self.throttle_bucket_keys = throttle_keys(self.throttle_scope, request)
            wait = check(self.throttle_scope, self.throttle_bucket_keys)
            if wait is not None:
                return self.throttled_response(request, wait)
            if self.throttle_on_request:
                consume(self.throttle_scope, self.throttle_bucket_keys)
        return super().dispatch(request, *args, **kwargs)

    def consume_throttle(self) -> None:
        consume(self.throttle_scope, getattr(self, "throttle_bucket_keys", []))

    def reset_throttle(self) -> None:
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
            {"message": THROTTLE_MESSAGE, "retry_after": retry_after},
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
