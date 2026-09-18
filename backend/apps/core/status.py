"""Stan serwisu dla strony ``/status/``: co działa, co nie, i co się teraz dzieje w zawodach.

Po co to jest, skoro jest ``/healthz/``: ``healthz`` odpowiada **orkiestratorowi** i mówi „ok”
albo „degraded” kodem HTTP; nikt go nie czyta, a jego treść nie odpowiada na pytanie uczestnika.
A pytanie uczestnika brzmi: „nie mogę wysłać pracy o 23:40 – to wasz serwis nie działa, czy coś
u mnie?”. Bez strony, na którą da się wtedy wejść, jedyną drogą do odpowiedzi jest telefon do
organizatora w środku nocy.

Stąd zakres: **stan usług** (baza, cache, magazyn plików, kolejka zadań), **czas serwera** (bo
deadline jest w czasie polskim i to on rozstrzyga, a nie zegarek na telefonie) i **stan zawodów**
(czy rejestracja jest otwarta, który etap trwa i do kiedy). Do tego komunikaty organizatora –
te same, które wiszą w banerze.

Czego tu **nie ma** i nie będzie: nazw hostów, wersji bibliotek, numerów wydania poza
``APP_VERSION``, treści błędów i czegokolwiek, co pomaga w rozpoznaniu celu. Strona jest publiczna
i jedyną informacją, jaką oddaje o infrastrukturze, jest binarne „działa / nie działa”.

Każde sprawdzenie jest **tanie i nieblokujące**: ``SELECT 1``, zapis i odczyt z cache'u, jedno
żądanie HEAD do magazynu i odczyt znacznika czasu zostawionego przez workera
(``apps.core.tasks.heartbeat``). Żadne z nich nie pyta kolejki synchronicznie – strona statusu nie
może wisieć dokładnie wtedy, gdy jest potrzebna. Całość jedzie przez ``cache_page`` w widoku,
więc nawet nalot na tę stronę nie zamienia jej w narzędzie do dobijania własnej bazy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.core.cache import cache
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Po ilu minutach bez pulsu uznajemy kolejkę zadań za niedziałającą. Puls jest zapisywany co
#: minutę (``CELERY_BEAT_SCHEDULE``), a jego wpis żyje trzy minuty – ten próg jest tą samą
#: wartością wyrażoną w minutach i istnieje po to, żeby komunikat („brak pulsu od N minut”)
#: liczył się z tego samego progu, z którego liczy się werdykt.
HEARTBEAT_MAX_AGE_MINUTES = 3

#: Klucz kontrolny cache'u. Zapisujemy i odczytujemy go w jednym sprawdzeniu – sam odczyt
#: nieistniejącego klucza zwraca ``None`` także wtedy, gdy Redis odpowiada, więc nie dowodzi niczego.
CACHE_PROBE_KEY = "status:probe"

#: Nazwy kodowe podsystemów. Kod, nie zdanie: tę samą wartość oddaje wariant JSON dla monitoringu
#: zewnętrznego, a zdanie dla człowieka dobiera szablon.
SERVICE_DATABASE = "database"
SERVICE_CACHE = "cache"
SERVICE_STORAGE = "storage"
SERVICE_QUEUE = "queue"


@dataclass(frozen=True)
class ServiceStatus:
    """Stan jednego podsystemu: nazwa kodowa, werdykt i jedno zdanie szczegółu.

    ``detail`` jest **opisem stanu**, nigdy treścią wyjątku: komunikat błędu bazy potrafi nieść
    nazwę hosta, użytkownika i schematu, a ta strona jest publiczna. Wyjątki idą do logu.
    """

    name: str
    ok: bool
    detail: str = ""


def _check_database() -> ServiceStatus:
    """``SELECT 1``. Najtańsze pytanie, które dowodzi i połączenia, i gotowości serwera."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            ok = cursor.fetchone() == (1,)
    except Exception:  # noqa: BLE001 - publiczna strona statusu nie może oddać treści błędu
        logger.warning("Strona statusu: baza danych nie odpowiada.", exc_info=True)
        ok = False
    return ServiceStatus(SERVICE_DATABASE, ok)


def _check_cache() -> ServiceStatus:
    """Zapis i natychmiastowy odczyt. Sam odczyt nie dowodzi niczego – ``None`` zwraca też Redis."""
    try:
        cache.set(CACHE_PROBE_KEY, "1", 10)
        ok = cache.get(CACHE_PROBE_KEY) == "1"
    except Exception:  # noqa: BLE001 - jak wyżej
        logger.warning("Strona statusu: cache nie odpowiada.", exc_info=True)
        ok = False
    return ServiceStatus(SERVICE_CACHE, ok)


def _check_storage() -> ServiceStatus:
    """Jedno żądanie do magazynu rozwiązań (``HeadBucket`` na S3/MinIO, istnienie katalogu lokalnie).

    Pytanie jest o **magazyn**, a nie o konkretny plik: pusty bucket nie jest awarią, a brak
    dostępu do niego – jest, bo oddana praca nie miałaby gdzie wylądować.
    """
    try:
        from apps.submissions.storage import get_submission_storage

        ok = bool(get_submission_storage().healthy())
    except Exception:  # noqa: BLE001 - jak wyżej
        logger.warning("Strona statusu: magazyn rozwiązań nie odpowiada.", exc_info=True)
        ok = False
    return ServiceStatus(SERVICE_STORAGE, ok)


def _check_queue(now=None) -> ServiceStatus:
    """Wiek pulsu zostawionego przez workera (``apps.core.tasks.heartbeat``).

    Nie pytamy kolejki synchronicznie (``inspect ping``) i to jest sedno: takie pytanie czeka na
    odpowiedź, więc strona statusu wisiałaby dokładnie wtedy, gdy worker nie żyje – czyli
    w jedynym przypadku, dla którego istnieje.

    Brak wpisu znaczy jedno z dwóch: kolejka nie działa albo serwis wystartował przed chwilą
    i pierwszy przebieg jeszcze nie przeszedł. Rozróżniać tych przypadków nie próbujemy – oba
    znaczą dla uczestnika to samo (list nie wyjdzie, skan się nie wykona), a zgadywanie „chyba
    dopiero wystartowaliśmy” zamieniłoby awarię w komunikat uspokajający.
    """
    now = now or timezone.now()
    try:
        from apps.core.tasks import HEARTBEAT_CACHE_KEY

        raw = cache.get(HEARTBEAT_CACHE_KEY)
    except Exception:  # noqa: BLE001 - jak wyżej
        logger.warning("Strona statusu: nie udało się odczytać pulsu workera.", exc_info=True)
        raw = None
    if not raw:
        return ServiceStatus(SERVICE_QUEUE, False, "brak sygnału od workera")
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:  # pragma: no cover - wpis zapisuje wyłącznie nasze zadanie
        return ServiceStatus(SERVICE_QUEUE, False, "brak sygnału od workera")
    minutes = int((now - stamp).total_seconds() // 60)
    if minutes > HEARTBEAT_MAX_AGE_MINUTES:
        return ServiceStatus(SERVICE_QUEUE, False, f"brak sygnału od {minutes} min")
    return ServiceStatus(
        SERVICE_QUEUE,
        True,
        "sygnał sprzed mniej niż minuty" if minutes < 1 else f"sygnał sprzed {minutes} min",
    )


def services(now=None) -> list[ServiceStatus]:
    """Stan wszystkich podsystemów, w kolejności od najbliższego uczestnikowi.

    Kolejność jest treścią: baza i magazyn plików rozstrzygają o tym, czy da się oddać pracę,
    a kolejka – czy przyjdzie list. Cache jest między nimi, bo jego awaria wyłącza sesje i limity,
    ale nie gubi ani jednej pracy.
    """
    return [_check_database(), _check_cache(), _check_storage(), _check_queue(now)]


def competition_state(now=None, competition=None) -> dict:
    """Stan zawodów „na teraz”: rejestracja, etap bieżący i jego najbliższy termin.

    Czytamy to z tych samych funkcji, co reszta serwisu (``apps.competitions.registration``
    i ``apps.competitions.services``), bo strona statusu nie może pokazać innego stanu niż ten,
    który egzekwuje serwer – byłaby wtedy gorsza niż jej brak.

    **Strona statusu jest per host.** Pod każdą domeną stoi jeden konkurs, a „trwa etap
    eliminacyjny” bez powiedzenia, czyj, byłoby na platformie wielokonkursowej odpowiedzią
    przypadkową. Konkurs bierzemy z argumentu albo z kontekstu żądania (ustawia go
    ``apps.tenancy.middleware.CompetitionMiddleware``), bo widok statusu woła ``snapshot()``
    bez argumentów – i ma tak zostać, skoro źródłem prawdy jest ta jedna funkcja.

    Rozstrzygnięty konkurs jedzie dalej **argumentem**, a nie kontekstem: ``current_edition``
    i ``current_registration_status`` umieją go wprawdzie wziąć z kontekstu, ale tę funkcję woła
    też kod, który konkurs zna i podaje go wprost (komenda, test, przebieg wsadowy) – a wtedy
    kontekst bywa pusty albo należy do sąsiada.

    Błąd bazy nie wywraca strony: pola zostają puste, a wiersz „baza danych” w tabeli usług i tak
    już powiedział, co się dzieje.
    """
    from apps.tenancy.context import current_competition

    now = now or timezone.now()
    competition = competition or current_competition()
    state = {
        # Nazwa konkursu, a nie jego identyfikator: strona jest dla człowieka, a identyfikator
        # niczego mu nie mówi. Pusty napis (host bez konkursu) szablon pomija tak samo, jak pomija
        # pustą etykietę edycji.
        "name": str(competition) if competition is not None else "",
        "registration_open": None,
        "registration_message": "",
        "edition": "",
        "stage": "",
        "stage_deadline": None,
    }
    try:
        from apps.competitions.registration import current_registration_status, registration_message
        from apps.competitions.services import current_edition, current_stage

        # Konkurs podajemy **wprost**, choć obie funkcje umieją go wziąć z kontekstu: ta funkcja
        # bywa wołana z argumentem przez kod, który konkurs zna (komenda, test), a wtedy kontekst
        # jest pusty albo cudzy – i strona statusu konkursu A pokazałaby harmonogram konkursu B.
        status = current_registration_status(now, competition)
        state["registration_open"] = status.is_open
        state["registration_message"] = registration_message(status)
        edition = current_edition(competition)
        if edition is not None:
            state["edition"] = edition.year_label
            stage = current_stage(edition, now)
            if stage is not None:
                state["stage"] = stage.display_name
                # ``submission_deadline`` zamiast ``deadline_at``: uczestnika obowiązuje termin
                # razem z tolerancją, bo to on rozstrzyga o przyjęciu pliku.
                state["stage_deadline"] = stage.submission_deadline if stage.has_deadline else None
    except Exception:  # noqa: BLE001 - patrz docstring
        logger.warning("Strona statusu: nie udało się odczytać stanu zawodów.", exc_info=True)
    return state


def snapshot(now=None, competition=None) -> dict:
    """Komplet danych strony statusu – jedno źródło dla wariantu HTML i dla JSON-a.

    Dwa warianty tej samej strony **muszą** pokazywać to samo: monitoring zewnętrzny odpytuje
    ``/status.json``, a człowiek patrzy na ``/status/``, i rozjazd między nimi znaczyłby, że jedno
    z tych dwóch kłamie. Stąd jedna funkcja i dwa renderery nad nią.

    ``competition`` podaje wołający, który konkurs zna (komenda, test); w żądaniu wystarcza
    kontekst warstwy ``CompetitionMiddleware`` – patrz ``competition_state``.
    """
    from apps.cms.announcements import cached_announcements
    from apps.web.context_processors import APP_VERSION

    now = now or timezone.now()
    checks = services(now)
    return {
        "now": now,
        "version": APP_VERSION,
        "services": checks,
        # „Wszystko działa” znaczy: **każdy** podsystem odpowiada. Wariant „większość działa”
        # nie istnieje, bo uczestnikowi z niedziałającym magazynem plików nie pomaga to, że baza
        # ma się dobrze.
        "all_ok": all(item.ok for item in checks),
        "competition": competition_state(now, competition),
        # Komunikaty **tego** konkursu: od zadania T4 baner jest zakresowany, a jego odczyt bierze
        # konkurs pierwszym argumentem. ``None`` znaczy tu „weź z kontekstu żądania” – dokładnie
        # to samo, co wyżej w ``competition_state``.
        "announcements": cached_announcements(competition, now),
    }


def as_json(data: dict) -> dict:
    """Snapshot sprowadzony do typów prostych – ciało odpowiedzi ``/status.json``.

    Kształt jest **kontraktem** dla zewnętrznego monitoringu, więc pola są jawnie wypisane,
    a nie zrzucane z obiektu: dołożenie kiedyś pola do ``snapshot`` nie może po cichu wypchnąć
    na zewnątrz czegoś, czego nie chcieliśmy pokazać.

    Znaczniki czasu idą w ISO 8601 w czasie **lokalnym serwisu** (Europe/Warsaw) – ten sam, w
    którym podane są deadline'y. Strefa jest w zapisie jawna, więc maszyna nic nie traci.

    Stan kopii zapasowych (``backup_last_ok``, ``backup_last_verified``) idzie tu jako **wartość
    logiczna**, a nie data, i to jest świadome odstępstwo od reszty pól: ta odpowiedź jest
    publiczna, a konkretna data ostatniej kopii mówi obcemu, kiedy uderzenie zaboli najbardziej.
    „Tak/nie” wystarcza monitorowi zewnętrznemu, żeby zapalić lampkę; daty ogląda dyżurny przez
    ``manage.py record_backup_status --show`` i dostaje je w treści alertu.
    """
    from apps.core.backup import state as backup_state
    from apps.tenancy.setup import setup_available

    competition = data["competition"]
    deadline = competition.get("stage_deadline")
    backup = backup_state()
    return {
        "status": "ok" if data["all_ok"] else "degraded",
        "time": timezone.localtime(data["now"]).isoformat(),
        "version": data["version"],
        "services": {item.name: item.ok for item in data["services"]},
        # Kopie zapasowe **nie** wchodzą do ``status`` ani do ``all_ok`` wyżej: dla uczestnika,
        # który o 23:40 pyta, czy da się oddać pracę, stan kopii nie zmienia niczego. Zapalenie
        # przez nie całej strony na „degraded” nauczyłoby tylko jednego – żeby tej strony nie
        # czytać. Monitoring operacyjny pyta o te dwa pola osobnym monitorem.
        "backup_last_ok": backup.backup_fresh,
        "backup_last_verified": backup.verify_fresh,
        "registration_open": competition.get("registration_open"),
        "edition": competition.get("edition") or None,
        "stage": competition.get("stage") or None,
        "stage_deadline": timezone.localtime(deadline).isoformat() if deadline else None,
        "announcements": [
            {"level": item.level, "text": item.text} for item in data.get("announcements") or []
        ],
        # Czy instalacja czeka jeszcze na kreator pierwszego uruchomienia (``/setup/``, § 1.7.3).
        # Klucz jest **dołożony na końcu**: jedenaście pól wyżej zostaje bez zmian i w tej samej
        # kolejności, bo kształt tej odpowiedzi jest kontraktem monitoringu zewnętrznego.
        # Na produkcji jest to zawsze ``false`` (jest konkurs i jest superużytkownik), a monitor
        # dostaje dzięki temu jednoznaczną odpowiedź na pytanie „czy ten adres to świeża, niczyja
        # instalacja” – bez pukania do samego ``/setup/``, które i tak odpowiada 404.
        "setup_pending": setup_available(),
    }
