"""Kreator pierwszego uruchomienia ``/setup/`` — bramka, token, limit i czynności.

Cały sens instalatora jest w tym, żeby pierwsze uruchomienie nie wymagało SSH
(``docs/UNIWERSALNY-ETAP-2.md`` § 1.7.1, decyzja organizatora D20). Cena tej wygody jest jedna
i trzeba ją mieć wypisaną w jednym miejscu: to **publiczny adres, który zakłada superużytkownika**.
Dlatego cała bramka stoi tutaj, a nie w widokach — widok, który zapomni o jednym z warunków,
byłby wtedy widokiem, który zakłada operatora komuś, kto akurat trafił pod adres.

Warunków jest **trzy** i wszystkie są konieczne:

1. w bazie **nie ma ani jednego** ``Competition`` **i nie ma ani jednego** ``User``
   z ``is_superuser=True`` (:func:`setup_available`),
2. żądanie przedstawia **token** (:func:`setup_token`) — z ``.env`` albo wylosowany przy starcie
   kontenera i wypisany raz do logu (:func:`announce_setup_token`),
3. krok zakładający konto przechodzi przez **blokadę doradczą** i sprawdza warunek 1. jeszcze raz,
   już pod blokadą (:func:`setup_lock`), więc dwie przeglądarki otwarte na kroku 1. kończą się
   drugim 404, a nie drugim superużytkownikiem.

Sam brak konkursu nie wystarcza i to nie jest ostrożność na wyrost: instalacja po awaryjnym
przywróceniu kopii ma konta i nie ma konkursu. Odwrotnie też — superużytkownik bez konkursu to
stan przejściowy między ``createsuperuser`` a ``create_competition``.

**Odpowiedź bramki nie jest buforowana.** To dwa zapytania ``EXISTS`` wykonywane wyłącznie na
ścieżce ``/setup/`` (oraz raz na 30 sekund w ``/status.json``, który i tak jedzie przez
``cache_page``), a wynik fałszywie dodatni z pamięci podręcznej znaczyłby otwarty kreator na
produkcji.

Czego ten moduł **nie** robi: nie zakłada konta drugą drogą (woła ``bootstrap_coordinator`` —
dokładnie tę komendę, którą woła ``scripts/deploy.sh``), nie zakłada konkursu drugą drogą (woła
``create_competition``), nie uruchamia seedów treści, nie dotyka ``.env``, nie konfiguruje DNS-u,
poczty ani ``docker compose``.
"""

from __future__ import annotations

import logging
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from io import StringIO
from pathlib import Path

from django.core.cache import cache
from django.core.management import call_command
from django.db import DatabaseError, connection
from rest_framework.settings import api_settings

logger = logging.getLogger(__name__)

# --- bramka -------------------------------------------------------------------------------------

#: Przestrzeń kluczy blokady doradczej Postgresa dla kreatora (``pg_advisory_xact_lock(int4, int4)``).
#: Własna, bo każdy moduł z blokadą doradczą bierze własną — ``apps/grading/services.py`` ma 1005.
ADVISORY_LOCK_NAMESPACE_SETUP = 1007

#: Drugi argument blokady. Kreator jest jeden na instalację, więc klucz jest stały: szeregujemy
#: **czynność**, a nie wiersz — wiersza, o który chodzi, jeszcze nie ma.
ADVISORY_LOCK_KEY_SETUP = 1

#: Nazwa zdarzenia audytu zapisywanego po zakończeniu kreatora (razem z adresem IP).
AUDIT_ACTION_COMPLETED = "setup.completed"


def competition_exists() -> bool:
    """Czy w bazie jest **jakikolwiek** konkurs. Błąd bazy = „tak” (patrz :func:`setup_available`)."""
    from apps.tenancy.models import Competition

    return Competition.objects.exists()


def superuser_exists() -> bool:
    """Czy w bazie jest **jakiekolwiek** konto z ``is_superuser=True``."""
    from apps.accounts.models import User

    return User.objects.filter(is_superuser=True).exists()


def setup_available() -> bool:
    """Dwa warunki naraz i oba są konieczne: brak konkursu **i** brak superużytkownika.

    Baza, której nie da się przeczytać (brak migracji, zerwane połączenie), daje ``False``:
    „nie wiem” musi tu znaczyć „zamknięte”. Kreator otwarty z powodu awarii bazy byłby publicznym
    adresem zakładającym superużytkownika dokładnie w tej chwili, w której nikt nie patrzy na logi.
    """
    try:
        return not competition_exists() and not superuser_exists()
    except DatabaseError:  # pragma: no cover - baza bez migracji albo bez połączenia
        logger.warning("Nie udało się sprawdzić bramki kreatora /setup/.", exc_info=True)
        return False


@contextmanager
def setup_lock():
    """Blokada doradcza na czas zakładania operatora i konkursu.

    Szeregujemy **operację**, a nie wiersz: wiersza, o który chodzi, jeszcze nie ma, więc nie ma
    czego zablokować ``select_for_update``. Blokadę zwalnia koniec transakcji — także przy wyjątku.
    Wołający jest odpowiedzialny za ``transaction.atomic`` i za **powtórne** sprawdzenie bramki
    już pod blokadą; bez tego wyścig dwóch przeglądarek dawałby dwa konta operatora.

    Poza Postgresem (dziś: nigdzie) blokada jest pusta — to jedyne miejsce w module z surowym SQL-em
    i jedyne możliwe, bo ta funkcja nie ma odpowiednika w ORM. Parametry idą przez placeholdery.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                [ADVISORY_LOCK_NAMESPACE_SETUP, ADVISORY_LOCK_KEY_SETUP],
            )
    yield


# --- token --------------------------------------------------------------------------------------

#: Zmienna środowiskowa z tokenem kreatora. Ustawiona w ``.env`` = token znany administratorowi
#: **przed** pierwszym uruchomieniem i niezmienny między restartami.
SETUP_TOKEN_ENV = "SETUP_TOKEN"

#: Plik, w którym trzymany jest token wylosowany przy starcie kontenera. Wspólny dla wszystkich
#: procesów roboczych gunicorna (te powstają przez ``fork`` **po** starcie, a każdy importuje
#: aplikację osobno — token trzymany w pamięci procesu byłby inny w każdym z nich, czyli
#: działałby dla co trzeciego żądania).
SETUP_TOKEN_FILE_ENV = "SETUP_TOKEN_FILE"
DEFAULT_SETUP_TOKEN_FILE = Path(tempfile.gettempdir()) / "olimpiada-setup-token"

#: Nazwa parametru adresu, którym token wchodzi do sesji: ``/setup/?token=…``.
TOKEN_QUERY_PARAM = "token"

#: Klucz w sesji: „ta przeglądarka pokazała prawidłowy token”. Token znika z adresu zaraz po
#: pierwszym żądaniu (przekierowanie na ``/setup/``), więc nie zostaje ani w nagłówku ``Referer``,
#: ani w historii przeglądarki, ani w logu proxy dla kolejnych kroków.
SESSION_TOKEN_KEY = "setup_token_ok"

#: Liczba bajtów losowości tokenu. 32 bajty to 43 znaki w ``token_urlsafe`` — do przepisania z logu
#: kopiuj-wklej, nie do przepisania ręcznie, i o rząd wielkości za dużo na zgadywanie.
TOKEN_BYTES = 32

#: **Dokładna** linia w logu kontenera ``web``, z której administrator bierze adres kreatora.
#: Kształt jest kontraktem: opisuje go ``docs/UNIWERSALNY-ETAP-2.md`` § 1.7.1 i README wdrożenia,
#: a dyżurny szuka jej przez ``docker compose logs web | grep 'setup/?token='``.
TOKEN_LOG_TEMPLATE = (
    "KREATOR /setup/: otworzy go wyłącznie adres /setup/?token=%s "
    "(token na czas tego uruchomienia kontenera; wpisz SETUP_TOKEN do .env, żeby go nie losować)"
)


def token_file() -> Path:
    """Ścieżka pliku z wylosowanym tokenem (``SETUP_TOKEN_FILE`` albo katalog tymczasowy)."""
    return Path(os.environ.get(SETUP_TOKEN_FILE_ENV) or DEFAULT_SETUP_TOKEN_FILE)


def _read_or_create_token(*, announce: bool) -> str:
    """Token z pliku; zakłada go, gdy pliku nie ma. ``announce`` steruje wyłącznie wpisem do logu.

    Zakładanie idzie przez ``O_CREAT | O_EXCL`` z prawami ``0600``: to jest ten sam wyścig, co przy
    starcie trzech procesów roboczych naraz, i rozstrzyga go jądro, a nie kolejność w kodzie.
    Przegrany czyta to, co wygrany zapisał, więc token jest jeden na kontener.
    """
    path = token_file()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    except OSError:  # pragma: no cover - katalog tylko do odczytu
        # Token trzymany w pamięci procesu byłby inny w każdym procesie roboczym, więc zamiast
        # udawać, że kreator działa, mówimy wprost: tu trzeba wpisać SETUP_TOKEN do .env.
        logger.warning("Nie udało się zapisać tokenu kreatora w %s — ustaw %s w .env.", path, SETUP_TOKEN_ENV)
        return ""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
    if announce:
        logger.warning(TOKEN_LOG_TEMPLATE, token)
    return token


def setup_token() -> str:
    """Token wymagany przez kreator: z ``SETUP_TOKEN`` albo ten wylosowany przy starcie kontenera.

    Pusty napis znaczy „tokenu nie ma i nie da się go wystawić” — wtedy kreator nie otworzy się
    nigdy, bo :func:`token_matches` odmawia każdemu żądaniu. To jest właściwa odmowa: adres
    zakładający superużytkownika bez żadnego sekretu byłby otwarty dla każdego, kto zna ścieżkę.
    """
    configured = (os.environ.get(SETUP_TOKEN_ENV) or "").strip()
    if configured:
        return configured
    return _read_or_create_token(announce=False)


def announce_setup_token() -> None:
    """Losuje token przy starcie kontenera i wypisuje go **raz** do logu (``TenancyConfig.ready``).

    Wypisuje wyłącznie proces, który plik tokenu **założył** — pozostałe (kolejne procesy robocze,
    ``collectstatic``, ``manage.py …``) czytają go po cichu. Gdy token jest w ``.env``, w logu nie
    ma nic: sekret z konfiguracji nie ma powodu wędrować do dziennika kontenera.
    """
    if (os.environ.get(SETUP_TOKEN_ENV) or "").strip():
        return
    _read_or_create_token(announce=True)


def token_matches(value: str) -> bool:
    """Porównanie tokenu odporne na pomiar czasu. Brak tokenu po którejkolwiek stronie = odmowa."""
    expected = setup_token()
    if not expected or not value:
        return False
    return secrets.compare_digest(str(value), expected)


def token_accepted(request) -> bool:
    """Czy **ta** przeglądarka pokazała już prawidłowy token (klucz w sesji)."""
    return bool(request.session.get(SESSION_TOKEN_KEY))


def accept_token(request) -> None:
    """Zapamiętuje w sesji, że token był prawidłowy — kolejne kroki nie noszą go już w adresie."""
    request.session[SESSION_TOKEN_KEY] = True


# --- limit prób ---------------------------------------------------------------------------------

#: Scope limitu — ten sam napis, który wejdzie do ``DEFAULT_THROTTLE_RATES``
#: (``config/settings/base.py``, plik zadania T45).
THROTTLE_SCOPE = "setup"

#: Zapasowe źródło stawki, dopóki scope'u nie ma w ustawieniach DRF. Kolejność jest jawna:
#: ustawienia (gdy **znają** scope, także z wartością ``None``, czyli „limit wyłączony”), potem
#: zmienna środowiskowa, na końcu wartość z § 1.7.1. Dzięki temu dopisanie scope'u do ustawień
#: przez zadanie T45 nie zmienia zachowania, a testy wyłączają limit tak samo, jak dla ``support``.
SETUP_THROTTLE_RATE_ENV = "SETUP_THROTTLE_RATE"
DEFAULT_THROTTLE_RATE = "10/hour"


def throttle_rate() -> str | None:
    """Stawka limitu dla kreatora („10/hour”) albo ``None``, gdy limit jest wyłączony."""
    rates = api_settings.DEFAULT_THROTTLE_RATES or {}
    if THROTTLE_SCOPE in rates:
        return rates[THROTTLE_SCOPE]
    return os.environ.get(SETUP_THROTTLE_RATE_ENV, DEFAULT_THROTTLE_RATE)


def _throttle_window(request) -> tuple[list[str], int, int] | None:
    """``(kubełki, ile prób, długość okna)`` albo ``None``, gdy limitu nie ma."""
    from apps.web.throttle import parse_rate, throttle_keys

    rate = parse_rate(throttle_rate())
    if rate is None:
        return None
    num_requests, duration = rate
    return throttle_keys(THROTTLE_SCOPE, request), num_requests, duration


def throttle_wait(request) -> float | None:
    """Ile sekund zostało do zwolnienia limitu. ``None`` = można przepuścić żądanie.

    Okno jest przesuwne i liczone tak samo, jak w ``apps.web.throttle`` — tam jednak stawka
    przychodzi wyłącznie z ustawień DRF, a tych kreator nie ma prawa dopisać (plik należy do
    innego zadania). Te kilkanaście linii znika w chwili, w której scope ``setup`` wejdzie do
    ``DEFAULT_THROTTLE_RATES``, i widok wróci do ``ThrottledFormMixin`` bez żadnej innej zmiany.
    """
    window = _throttle_window(request)
    if window is None:
        return None
    keys, num_requests, duration = window
    now = time.time()
    wait: float | None = None
    for key in keys:
        history = [moment for moment in (cache.get(key) or []) if moment > now - duration]
        if len(history) >= num_requests:
            remaining = history[num_requests - 1] + duration - now
            wait = remaining if wait is None else max(wait, remaining)
    return wait


def throttle_consume(request) -> None:
    """Dopisuje próbę do każdego kubełka limitu."""
    window = _throttle_window(request)
    if window is None:
        return
    keys, _, duration = window
    now = time.time()
    for key in keys:
        history = [moment for moment in (cache.get(key) or []) if moment > now - duration]
        history.insert(0, now)
        cache.set(key, history, duration)


# --- czynności kreatora ---------------------------------------------------------------------------

#: Klucze sesji niosące stan kreatora między krokami. Identyfikatory, a nie obiekty: sesja jedzie
#: przez serializator JSON-a, a stan kroku 2. ma przeżyć odświeżenie strony.
SESSION_OPERATOR_KEY = "setup_operator_id"
SESSION_COMPETITION_KEY = "setup_competition_id"
#: Szablon wybrany w kroku 2. — ekran podsumowania bierze z niego listę dokumentów do wpisania.
#: Konkurs tej wartości nie pamięta (i nie ma powodu pamiętać: szablon jest **startem**, a nie
#: właściwością konkursu), więc niesie ją sesja kreatora.
SESSION_TEMPLATE_KEY = "setup_template"
#: Raport komendy ``create_competition`` — te same linijki do ``.env`` i ta sama lista następnych
#: kroków, które dostaje administrator uruchamiający ją z powłoki.
SESSION_REPORT_KEY = "setup_report"

#: Zmienne, z których ``bootstrap_coordinator`` czyta dane konta (komenda nie przyjmuje argumentów
#: **celowo**: argumenty trafiają do historii powłoki i do listy procesów).
COORDINATOR_EMAIL_ENV = "COORDINATOR_EMAIL"
COORDINATOR_PASSWORD_ENV = "COORDINATOR_PASSWORD"


@contextmanager
def _environment(**values: str):
    """Zmienne środowiskowe na czas jednego wywołania komendy; przywraca stan zastany."""
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def create_operator(*, email: str, password: str, first_name: str = "", last_name: str = ""):
    """Pierwsze konto operatora — przez ``manage.py bootstrap_coordinator``, bez drugiej drogi.

    Komenda jest ta sama, którą woła ``scripts/deploy.sh`` (krok 6/8): superużytkownik w grupie
    ``coordinator``, z adresem uznanym za potwierdzony, bo na świeżej instalacji nie ma jeszcze
    ani poczty, ani nikogo, kto odebrałby list aktywacyjny. Drugie miejsce zakładające takie konto
    znaczyłoby dwie reguły „co to jest operator” i pewność, że rozjadą się przy pierwszej zmianie.

    Dane idą **zmiennymi środowiskowymi procesu**, bo tylko takie wejście komenda ma. Ustawienie
    jest na czas jednego wywołania i zdejmowane w ``finally``; wołający trzyma je pod blokadą
    doradczą kreatora, więc dwa wywołania naraz nie są możliwe.

    Imię i nazwisko dopisujemy **po** komendzie: podpis listu i nagłówek panelu biorą je z konta,
    a komenda ich nie zna. To jest uzupełnienie wiersza, a nie druga droga jego zakładania.
    """
    from apps.accounts.models import User

    with _environment(**{COORDINATOR_EMAIL_ENV: email.strip().lower(), COORDINATOR_PASSWORD_ENV: password}):
        call_command("bootstrap_coordinator", stdout=StringIO(), stderr=StringIO())

    operator = User.objects.get(email=email.strip().lower())
    if first_name or last_name:
        operator.first_name = first_name
        operator.last_name = last_name
        operator.save(update_fields=["first_name", "last_name"])
    return operator


def create_first_competition(
    *,
    slug: str,
    name: str,
    domain: str,
    template: str,
    short_name: str = "",
    organizer: str = "",
    contact_email: str = "",
    coordinator_email: str = "",
) -> tuple[object, str]:
    """Pierwszy konkurs — przez ``manage.py create_competition``. Zwraca ``(konkurs, raport)``.

    Komenda jest **jedynym** wejściem do zakładania konkursu (jej docstring mówi, dlaczego), więc
    kreator jej nie omija ani nie powtarza: witryna, drzewo stron, ustawienia serwisu, konkurs,
    pierwsza edycja i etapy z szablonu powstają dokładnie tak samo, jak przy zakładaniu konkursu
    z powłoki. Raport komendy (te same linijki do ``.env``, ta sama lista dokumentów) idzie na
    ekran podsumowania — kreator niczego z niego nie wylicza po swojemu.

    ``coordinator_email`` nadaje rolę koordynatora **istniejącemu** kontu operatora z kroku 1.
    Bez tego operator byłby superużytkownikiem bez roli w jedynym konkursie instalacji, czyli
    osobą, której panel koordynatora nie wpuszcza do własnego konkursu.
    """
    from apps.tenancy.models import Competition

    output = StringIO()
    call_command(
        "create_competition",
        slug=slug,
        name=name,
        domain=domain,
        from_template=template,
        short_name=short_name,
        organizer=organizer,
        contact_email=contact_email,
        coordinator_email=coordinator_email,
        stdout=output,
        stderr=output,
    )
    competition = Competition.objects.select_related("site").get(slug=slug)
    return competition, output.getvalue()
