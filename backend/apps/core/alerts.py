"""Watchdog aplikacyjny: co pięć minut sprawdza to, czego nikt nie ogląda, i pisze list.

Po co to jest obok monitoringu zewnętrznego (Uptime Kuma, ``deploy/monitoring/``): monitor
zewnętrzny puka w adres z drugiej strony sieci i wie dokładnie tyle, ile widać z przeglądarki –
„strona odpowiada 200”. Nie wie, że dysk ma dwa procent wolnego miejsca, że w ostatnim kwadransie
sto żądań skończyło się błędem 500 ani że worker przyjmuje zadania i wywraca się na każdym z nich.
To są awarie, które dojrzewają godzinami i wybuchają w noc przed deadline'em. Tę połowę widać
wyłącznie od środka i dlatego stoi tu, a nie tam.

Zakres jest domknięty świadomie – pięć rzeczy, z których każda ma jedną, konkretną reakcję
opisaną w ``docs/OPERACJE.md``:

1. **podsystemy** – dokładnie te same sprawdzenia, co ``/status/`` (``apps.core.status.services``).
   Ta sama funkcja, a nie druga jej kopia: monitoring, który mówi co innego niż strona statusu,
   uczy ludzi ignorować jedno z dwojga,
2. **wolne miejsce na dysku** – bo pełny dysk zatrzymuje naraz bazę, kolejkę i przyjmowanie prac,
   a poprzedza go kilkanaście godzin, w których wszystko jeszcze działa,
3. **zadania Celery kończące się błędem** – licznik z sygnału ``task_failure``. Kolejka, która
   przyjmuje i gubi, wygląda z zewnątrz identycznie jak kolejka zdrowa,
4. **odsetek odpowiedzi 5xx** – licznik z ``apps.core.middleware``. Awaria jednego widoku nie
   ruszy ani ``/healthz/``, ani ``/status/``,
5. **kopie zapasowe** – ``apps.core.backup``: brak kopii i brak testu odtwarzania.

**Wyciszenie (cooldown) jest per klucz alertu i wynosi godzinę.** Bez niego pierwsza awaria
zamieniłaby skrzynkę organizatora w strumień dwustu ośmiu identycznych listów dziennie, a skutek
byłby odwrotny do zamierzonego: reguła w kliencie pocztowym przenosząca „alert” do kosza. Klucz
jest per rodzaj awarii, a nie globalny – trwająca awaria magazynu plików nie może zagłuszyć
informacji o tym, że w międzyczasie skończyło się miejsce na dysku.

Listy idą **synchronicznie** (``django.core.mail.send_mail``), a nie przez ``send_mail_task``.
To jest cały punkt: zadanie alertu już jest poza żądaniem HTTP, więc opóźnienie nikomu nie szkodzi,
a wrzucenie listu na kolejkę ``mail`` znaczyłoby, że informacja o zapchanej kolejce czeka w tej
samej zapchanej kolejce.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Licznik zadań Celery zakończonych błędem. Rośnie w obsłudze sygnału ``task_failure``
#: (rejestrowanej w ``apps.core.apps.CoreConfig.ready``).
FAILED_TASKS_KEY = "alerts:celery-failures"

#: Licznik odpowiedzi 5xx. Rośnie w ``apps.core.middleware.ServerErrorCounterMiddleware``.
SERVER_ERRORS_KEY = "alerts:server-errors"

#: Okno obu liczników. Kwadrans, czyli trzy przebiegi watchdoga: pojedyncza awaria zdąży zostać
#: zauważona co najmniej dwa razy, a wpis i tak znika sam, więc licznik nie pamięta wczorajszego
#: incydentu i nie budzi nikogo z jego powodu.
COUNTER_WINDOW_SECONDS = 900

#: Prefiks kluczy wyciszenia. Osobny od liczników, bo ma inny cykl życia i inny sens.
COOLDOWN_PREFIX = "alerts:cooldown:"
COOLDOWN_SECONDS = 3600

#: Progi. Dobrane tak, żeby pojedyncze zdarzenie nie budziło nikogo, a seria – budziła od razu.
#: Jedno nieudane zadanie w kwadransie to zwykle chwilowo niedostępny MTA (``send_mail_task`` ma
#: własne ponowienia i sam sobie z tym radzi); pięć znaczy, że ponowienia nie pomagają.
FAILED_TASKS_THRESHOLD = 5
#: Dziesięć błędów 500 w kwadransie. Jeden bywa skutkiem żądania od robota z dziwnym nagłówkiem;
#: dziesięć znaczy, że ktoś właśnie nie może oddać pracy.
SERVER_ERRORS_THRESHOLD = 10
#: Poniżej pięciu procent wolnego miejsca Postgres przestaje przyjmować zapisy, zanim ktokolwiek
#: zauważy problem. Dziesięć procent zostawia dobę na reakcję przy typowym tempie przyrostu.
DISK_MIN_FREE_PERCENT = 10

#: Ścieżka do sprawdzenia miejsca. Katalog główny kontenera, bo leży na tym samym urządzeniu, co
#: wolumeny Dockera – kontener ``web`` jest ``read_only`` i innego punktu odniesienia nie ma.
#: Wartość „widziana z kontenera” jest tu prawdą operacyjną: to ona kończy się jako pierwsza.
DISK_PATH = "/"


@dataclass(frozen=True)
class Alert:
    """Jeden powód do napisania listu: klucz wyciszenia, tytuł i jedno zdanie szczegółu."""

    key: str
    title: str
    detail: str = ""


def bump(key: str) -> None:
    """Podbija licznik w oknie ``COUNTER_WINDOW_SECONDS``. Nigdy nie rzuca.

    ``incr`` na nieistniejącym kluczu rzuca ``ValueError`` (tak działa API cache'u Django), więc
    pierwszy wpis w oknie zakłada ``set``. Wyścig dwóch procesów na tej granicy gubi w najgorszym
    razie jedno zliczenie – dla progu liczonego w jednostkach to nie ma znaczenia, a blokada
    rozproszona miałaby tu koszt większy niż cały mierzony problem.

    Wyjątek jest połykany świadomie: ten licznik podbija się w obsłudze **cudzej** awarii (sygnał
    o nieudanym zadaniu, odpowiedź 500). Awaria telemetrii nie ma prawa zamienić błędu 500
    w wyjątek w middleware, bo wtedy alert kosztowałby więcej niż to, o czym miał donieść.
    """
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, COUNTER_WINDOW_SECONDS)
    except Exception:  # noqa: BLE001 - patrz docstring
        logger.warning("Watchdog: nie udało się podbić licznika %s.", key, exc_info=True)


def counter(key: str) -> int:
    try:
        return int(cache.get(key) or 0)
    except Exception:  # noqa: BLE001 - brak cache'u sam w sobie jest już zgłaszany przez /status/
        return 0


def _disk_free_percent() -> float | None:
    """Procent wolnego miejsca. ``None``, gdy systemu plików nie da się odpytać (nie zgadujemy)."""
    try:
        usage = shutil.disk_usage(DISK_PATH)
    except OSError:  # pragma: no cover - zależne od systemu plików kontenera
        logger.warning("Watchdog: nie udało się odczytać zajętości dysku.", exc_info=True)
        return None
    if not usage.total:
        return None
    return usage.free * 100.0 / usage.total


def evaluate() -> list[Alert]:
    """Wszystkie powody do alarmu „na teraz”. Czysta funkcja – niczego nie wysyła i nie zapisuje.

    Rozdzielenie oceny od wysyłki jest tu celowe: dzięki temu test sprawdza regułę („pusty puls
    workera daje alert o kolejce”) bez zaglądania w skrzynkę, a wysyłka ma jeden, wspólny zestaw
    reguł wyciszenia dla wszystkich rodzajów awarii.
    """
    from apps.core.backup import MAX_BACKUP_AGE_HOURS, MAX_VERIFY_AGE_DAYS
    from apps.core.backup import state as backup_state
    from apps.core.status import services

    alerts: list[Alert] = []

    for service in services():
        if not service.ok:
            alerts.append(
                Alert(
                    key=f"service:{service.name}",
                    title=f"podsystem nie odpowiada: {service.name}",
                    detail=service.detail or "sprawdzenie zakończyło się niepowodzeniem",
                )
            )

    free = _disk_free_percent()
    if free is not None and free < DISK_MIN_FREE_PERCENT:
        alerts.append(
            Alert(
                key="disk",
                title="kończy się miejsce na dysku",
                detail=f"wolne: {free:.1f}% (próg: {DISK_MIN_FREE_PERCENT}%)",
            )
        )

    failures = counter(FAILED_TASKS_KEY)
    if failures >= FAILED_TASKS_THRESHOLD:
        alerts.append(
            Alert(
                key="celery-failures",
                title="zadania w tle kończą się błędem",
                detail=f"{failures} nieudanych zadań w ostatnich {COUNTER_WINDOW_SECONDS // 60} min",
            )
        )

    errors = counter(SERVER_ERRORS_KEY)
    if errors >= SERVER_ERRORS_THRESHOLD:
        alerts.append(
            Alert(
                key="server-errors",
                title="serwis oddaje błędy 5xx",
                detail=f"{errors} odpowiedzi 5xx w ostatnich {COUNTER_WINDOW_SECONDS // 60} min",
            )
        )

    backup = backup_state()
    if not backup.backup_fresh:
        when = timezone.localtime(backup.last_ok).strftime("%Y-%m-%d %H:%M") if backup.last_ok else "nigdy"
        detail = f"ostatnia udana kopia: {when} (próg: {MAX_BACKUP_AGE_HOURS} h). {backup.note}"
        alerts.append(Alert(key="backup", title="brak świeżej kopii zapasowej", detail=detail.strip()))
    if not backup.verify_fresh:
        when = (
            timezone.localtime(backup.last_verified).strftime("%Y-%m-%d %H:%M")
            if backup.last_verified
            else "nigdy"
        )
        alerts.append(
            Alert(
                key="backup-verify",
                title="kopia zapasowa nie została sprawdzona odtworzeniem",
                detail=f"ostatni udany test: {when} (próg: {MAX_VERIFY_AGE_DAYS} dni)",
            )
        )

    return alerts


def recipients() -> list[str]:
    """Adresy dyżurnych z ``settings.ALERT_EMAILS``. Pusta lista = watchdog nie wysyła niczego."""
    return [address.strip() for address in (getattr(settings, "ALERT_EMAILS", None) or []) if address.strip()]


def _claim(key: str) -> bool:
    """Czy wolno wysłać alert o tym kluczu. Zajmuje okno wyciszenia w tym samym ruchu.

    ``cache.add`` zamiast ``get`` + ``set``: dwa procesy workerów mogą wykonać ten sam przebieg
    watchdoga równolegle (Celery nie gwarantuje pojedynczego wykonania), a ``add`` jest po stronie
    Redisa operacją atomową – wygrywa dokładnie jeden i tylko on wysyła list.
    """
    try:
        return bool(cache.add(f"{COOLDOWN_PREFIX}{key}", "1", COOLDOWN_SECONDS))
    except Exception:  # noqa: BLE001 - awaria cache'u nie może zablokować alertu o awarii cache'u
        logger.warning("Watchdog: nie udało się sprawdzić wyciszenia dla %s.", key, exc_info=True)
        return True


def _audit(alert: Alert) -> None:
    """Wpis ``alert.sent`` w śladzie audytowym.

    Wiersz zakładamy wprost, a nie helperem :func:`apps.core.models.audit`, bo ten wymaga obiektu
    modelu, na którym coś zrobiono – a alert nie dotyczy żadnego wiersza w bazie. ``target_id``
    niesie klucz alertu, dzięki czemu historia „ile razy w tej edycji kończyło się miejsce na
    dysku” jest jednym zapytaniem po tym samym indeksie, co reszta audytu.
    """
    from apps.core.models import AuditLog

    try:
        AuditLog.objects.create(
            actor=None,
            action="alert.sent",
            target_type="core.alert",
            target_id=alert.key[:64],
            diff={"title": alert.title, "detail": alert.detail},
        )
    except Exception:  # noqa: BLE001 - alert o niedziałającej bazie nie może zależeć od bazy
        logger.warning("Watchdog: nie udało się zapisać audytu alertu %s.", alert.key, exc_info=True)


def send(alerts: list[Alert]) -> int:
    """Wysyła po jednym liście na alert (z pominięciem wyciszonych). Zwraca liczbę wysłanych.

    Jeden list na rodzaj awarii, a nie jeden zbiorczy: wyciszenie jest per klucz, więc list
    zbiorczy musiałby albo czekać na najdłużej wyciszony składnik, albo powtarzać treść, o której
    dyżurny już wie. Osobne listy są też czytelne w kliencie pocztowym jako osobne wątki.
    """
    targets = recipients()
    if not targets:
        if alerts:
            logger.warning("Watchdog: %s alertów bez odbiorcy (ALERT_EMAILS jest puste).", len(alerts))
        return 0

    site = getattr(settings, "WAGTAIL_SITE_NAME", "Olimpiada")
    sent = 0
    for alert in alerts:
        if not _claim(alert.key):
            logger.info("Watchdog: alert %s wyciszony.", alert.key)
            continue
        body = (
            f"{alert.title}\n\n"
            f"{alert.detail}\n\n"
            f"Czas: {timezone.localtime().strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Kolejny list o tej samej awarii najwcześniej za {COOLDOWN_SECONDS // 60} min.\n"
            f"Co z tym zrobić: docs/OPERACJE.md, sekcja „Alarmy”.\n"
        )
        try:
            send_mail(f"[{site}] ALARM: {alert.title}", body, settings.DEFAULT_FROM_EMAIL, targets)
        except Exception:  # noqa: BLE001 - nieudana wysyłka jednego alertu nie może zjeść reszty
            logger.error("Watchdog: nie udało się wysłać alertu %s.", alert.key, exc_info=True)
            continue
        _audit(alert)
        sent += 1
    return sent


def run() -> int:
    """Pełny przebieg watchdoga: oceń i wyślij. Zwraca liczbę wysłanych listów."""
    alerts = evaluate()
    if alerts:
        logger.warning("Watchdog: %s", ", ".join(alert.title for alert in alerts))
    return send(alerts)
