"""Drzewo startowe nowego konkursu (DJ-02 D7): witryna z rejestru bez stron → import z eksportu.

Nowy konkurs zakłada aplikacja główna (``create_competition``, ekran „Nowy konkurs”) razem z drzewem
Wagtaila z szablonu (``templates_catalog`` – kilka pustych stron), a rejestr witryn djcms dowiaduje
się o nim z listy ``GET competitions`` (``apps.sites.registry``). Witryna bez ani jednej strony
odpowiadałaby 404 na wszystko, więc treść startowa przychodzi z eksportu tego konkursu – tą samą
drogą co każdy import (``services.run_import``: walidacja, sanityzacja, publikacja).

Dwa wyzwalacze:

- **leniwie, w żądaniu** (``ensure_content_for_request``, woła go ``CompetitionSiteMiddleware``):
  pierwsze wejście na host konkursu, którego witryna nie ma stron. Wyłącznie konkurs, który
  przyszedł z listy API (``synced_at`` ustawione – S15; host spoza listy nie dociera tu wcale,
  bo warstwa odpowiada mu pustą 404). Paczka większa niż ``STARTER_MAX_PAGES`` stron → 503
  z komunikatem: duże drzewo (konkurs sprzed włączenia djcms) importuje wdrożenie albo operator,
  a nie odsłona z limitem czasu gunicorna,
- **przy wdrożeniu** – ``manage.py sync_competitions --import-missing`` (``import_missing``): każdy
  aktywny konkurs bez stron, bez limitu stron.

Współbieżność (audyt 2026-10-10, S22). Import w żądaniu wyzwala **anonim**, więc nic tu nie może
czekać w kolejce: dwanaście równoległych odsłon pustej witryny, które stoją jedna za drugą na blokadzie
i każda po kolei pobiera i waliduje paczkę, zajmuje wszystkie wątki djcms – i strony **wszystkich**
konkursów przestają odpowiadać. Dlatego:

- ``pg_try_advisory_xact_lock(hashtext('djcms-site:' || slug))`` – kto nie dostał blokady, dostaje
  od razu 503 z ``Retry-After`` (import trwa w innym wątku albo procesie). Czeka na blokadę wyłącznie
  wdrożenie (``deploy=True``) – i to najwyżej ``DEPLOY_LOCK_TIMEOUT``,
- ``SET LOCAL lock_timeout`` w transakcji importu: blokady wierszy i drzewa stron, na które import
  natrafi (redaktor zapisuje akurat inną stronę), też nie wstrzymają wątku na dłużej,
- znacznik porażki **w bazie** (``CompetitionSite.starter_failed_at``) – wspólny dla procesów
  i trwały po restarcie (bufor LocMem był per proces: każdy z workerów gunicorna pobierał paczkę
  od nowa). Sprawdzany przed blokadą (tani, z obiektu żądania) i **po** niej, na świeżo odczytanym
  wierszu – drugi wątek, który dostał blokadę tuż po porażce pierwszego, nie powtarza pobierania.
  Porażkę zapisujemy w tej samej transakcji co blokadę (import w punkcie zapisu), więc blokada
  zwalnia się dopiero razem ze znacznikiem,
- semafor procesu (``STARTER_CONCURRENCY``): najwyżej tyle importów naraz w jednym procesie, także
  dla różnych konkursów – reszta wątków obsługuje zwykłe strony.

Liczba stron paczki: lista ``competitions`` kontraktu v2 jej dziś nie podaje
(``backend/apps/cms/djcms_api/competitions.py::competition_dto``), więc limit ``STARTER_MAX_PAGES``
sprawdzamy po pobraniu paczki (najwyżej ``STARTER_MAX_BYTES``), a porażka zamyka próby na
``retry_seconds()``. Docelowo import tylko przy wdrożeniu.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, connection, transaction
from django.http import HttpResponse
from django.utils import timezone

from . import services

logger = logging.getLogger(__name__)

#: Największe drzewo startowe importowane w żądaniu (D7). Szablon „pusty” ma kilka stron.
STARTER_MAX_PAGES = 50
#: Największa paczka pobierana w żądaniu – drzewo startowe nie ma obrazów w pełnej rozdzielczości.
STARTER_MAX_BYTES = 32 * 1024 * 1024
#: Paczka w pamięci do tej wielkości, powyżej – plik tymczasowy w ``MEDIA_ROOT`` (nie tmpfs ``/tmp``).
STARTER_SPOOL_BYTES = 4 * 1024 * 1024
#: Najwięcej importów w żądaniu naraz w jednym procesie (wszystkie konkursy razem).
STARTER_CONCURRENCY = 1
#: ``Retry-After`` odmowy „import trwa w innym wątku” – pobranie i import paczki startowej to sekundy.
BUSY_RETRY_SECONDS = 30
#: ``lock_timeout`` transakcji importu w żądaniu i przy wdrożeniu (składnia Postgresa).
REQUEST_LOCK_TIMEOUT = "5s"
DEPLOY_LOCK_TIMEOUT = "5min"
#: „Witryna ma treść” zapamiętane w buforze procesu – jedno zapytanie na konkurs na ten czas.
CONTENT_CHECK_SECONDS = 600
#: Konto techniczne – autor wersji importu, gdy nie ma żadnego aktywnego superusera.
IMPORT_USERNAME = "djcms-import"
#: SQLSTATE ``lock_not_available`` – przekroczony ``lock_timeout``.
LOCK_NOT_AVAILABLE = "55P03"

CONTENT_KEY = "djcms:sites:has-content:{pk}"
LOCK_KEY = "djcms-site:{slug}"

MESSAGE_PREPARING = "Serwis tego konkursu jest przygotowywany. Spróbuj ponownie za kilka minut."
MESSAGE_BUSY = "Serwis tego konkursu jest właśnie przygotowywany. Spróbuj ponownie za chwilę."
MESSAGE_DEPLOY = (
    "Serwis tego konkursu nie jest jeszcze gotowy – treść zostanie zaimportowana przy najbliższym wdrożeniu."
)

#: Semafor procesu (patrz docstring modułu) – ``acquire(blocking=False)``, nigdy nie czekamy.
_import_slots = threading.BoundedSemaphore(STARTER_CONCURRENCY)


class StarterRefused(Exception):  # noqa: N818 - nazwa mówi, co się stało
    """Drzewa startowego nie da się (albo nie wolno) zaimportować w żądaniu."""


class StarterBusy(Exception):  # noqa: N818
    """Import tej witryny trwa w innym wątku albo procesie (blokada zajęta, ``lock_timeout``)."""


class StarterBackoff(Exception):  # noqa: N818
    """Ostatnia próba importu nie powiodła się niedawno – kolejna dopiero po ``retry_seconds()``."""


def retry_seconds() -> int:
    """Po porażce kolejna próba w żądaniu najwcześniej po tylu sekundach (``DJCMS_STARTER_RETRY_SECONDS``)."""
    return int(getattr(settings, "DJCMS_STARTER_RETRY_SECONDS", 10 * 60))


def retry_after(competition) -> int:
    """Sekundy do końca okna po porażce (0 – można próbować). Czyta pole obiektu, bez zapytania."""
    failed_at = competition.starter_failed_at
    if failed_at is None:
        return 0
    left = failed_at + timedelta(seconds=retry_seconds()) - timezone.now()
    return int(left.total_seconds()) + 1 if left > timedelta(0) else 0


def import_user():
    """Autor wersji: pierwszy aktywny superuser, a bez niego – nieaktywne konto techniczne bez hasła."""
    from django.contrib.auth import get_user_model

    model = get_user_model()
    user = model.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
    if user is not None:
        return user
    user, created = model.objects.get_or_create(username=IMPORT_USERNAME, defaults={"is_active": False})
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def has_content(competition) -> bool:
    """Czy witryna konkursu ma choć jedną stronę (albo była już importowana)."""
    from cms.models import Page

    if competition.content_imported_at is not None:
        return True
    key = CONTENT_KEY.format(pk=competition.pk)
    if cache.get(key):
        return True
    found = Page.objects.filter(site_id=competition.site_id).exists()
    if found:
        cache.set(key, True, CONTENT_CHECK_SECONDS)
    return found


def _lock(slug: str, *, wait: bool) -> bool:
    """Blokada transakcji na import witryny; ``wait=False`` – bez czekania (``False`` = zajęta)."""
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cursor:
        key = LOCK_KEY.format(slug=slug)
        if wait:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [key])
            return True
        cursor.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", [key])
        return bool(cursor.fetchone()[0])


def _set_lock_timeout(value: str) -> None:
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            # ``SET LOCAL`` nie przyjmuje parametrów zapytania – wartość jest stałą modułu.
            cursor.execute(f"SET LOCAL lock_timeout = '{value}'")


def _lock_timed_out(exc: OperationalError) -> bool:
    return getattr(exc.__cause__, "sqlstate", None) == LOCK_NOT_AVAILABLE


def _import(current, *, max_pages: int | None, user):
    """Pobranie, walidacja i import paczki – w punkcie zapisu transakcji ``ensure_site``."""
    from apps.live.client import MainApi

    Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
    limit = STARTER_MAX_BYTES if max_pages is not None else services.MAX_UNCOMPRESSED_BYTES
    with tempfile.SpooledTemporaryFile(max_size=STARTER_SPOOL_BYTES, dir=settings.MEDIA_ROOT) as spool:
        MainApi().download_export(spool, limit_bytes=limit, competition=current.slug)
        spool.seek(0)
        bundle = services.open_bundle(spool)
        if max_pages is not None and len(bundle.pages) > max_pages:
            raise StarterRefused(
                f"paczka konkursu „{current.slug}” ma {len(bundle.pages)} stron (w żądaniu najwyżej "
                f"{max_pages}) – zaimportuje ją wdrożenie albo operator: "
                f"manage.py import_cms_bundle --from-api --competition {current.slug} --if-empty"
            )
        return services.run_import(bundle, user=user or import_user(), competition=current)


def ensure_site(competition, *, max_pages: int | None = STARTER_MAX_PAGES, user=None, deploy: bool = False):
    """Import drzewa konkursu z API do **pustej** witryny, pod blokadą; ``None`` – treść już była.

    ``max_pages`` – limit stron paczki (``None`` = bez limitu, wdrożenie). ``deploy`` – czekać na
    blokadę (najwyżej ``DEPLOY_LOCK_TIMEOUT``) i nie patrzeć na znacznik porażki: operator uruchamia
    import świadomie. Błędy: ``StarterBusy`` (blokada zajęta), ``StarterBackoff`` (niedawna porażka),
    ``StarterRefused`` (paczka za duża, konkurs nieaktywny), ``services.BundleError``/
    ``services.ImportRefused``, ``MainApiError``. Każda porażka samego importu ustawia
    ``starter_failed_at``, udany import go czyści.
    """
    from cms.models import Page

    from apps.live.client import MainApiError
    from apps.sites.models import CompetitionSite

    failure: Exception | None = None
    report = None
    with transaction.atomic():
        _set_lock_timeout(DEPLOY_LOCK_TIMEOUT if deploy else REQUEST_LOCK_TIMEOUT)
        try:
            locked = _lock(competition.slug, wait=deploy)
        except OperationalError as exc:
            if not _lock_timed_out(exc):
                raise
            locked = False
        if not locked:
            raise StarterBusy(f"import witryny „{competition.slug}” trwa w innym wątku albo procesie")
        current = CompetitionSite.objects.select_related("site").get(pk=competition.pk)
        if not current.is_active:
            raise StarterRefused(f"konkurs „{current.slug}” jest nieaktywny")
        if Page.objects.filter(site_id=current.site_id).exists():
            return None
        if not deploy and retry_after(current):
            # Porażka zapisana przez inny proces, kiedy ten czekał przed blokadą – bez drugiego pobrania.
            raise StarterBackoff(f"ostatni import witryny „{current.slug}” nie powiódł się niedawno")
        try:
            with transaction.atomic():
                report = _import(current, max_pages=max_pages, user=user)
        except (StarterRefused, MainApiError, services.BundleError, services.ImportRefused) as exc:
            # Punkt zapisu wycofany, transakcja z blokadą żyje dalej – znacznik zatwierdzi się razem
            # z nią, a blokada zwolni dopiero po nim.
            failure = exc
            CompetitionSite.objects.filter(pk=current.pk).update(starter_failed_at=timezone.now())
        except OperationalError as exc:
            if not _lock_timed_out(exc):
                raise
            # ``lock_timeout`` w środku importu – konflikt z redakcją, a nie zła paczka: bez znacznika.
            failure = StarterBusy(f"import witryny „{current.slug}” czekał na blokadę dłużej niż limit")
        else:
            if current.starter_failed_at is not None:
                CompetitionSite.objects.filter(pk=current.pk).update(starter_failed_at=None)
    if failure is not None:
        raise failure
    logger.info(
        "Drzewo startowe konkursu %s: %s stron, %s wtyczek.",
        current.slug,
        sum(report.pages.values()),
        sum(report.plugins.values()),
    )
    return report


def _unavailable(message: str, *, retry: int) -> HttpResponse:
    body = (
        '<!doctype html><html lang="pl"><head><meta charset="utf-8"><title>Serwis w przygotowaniu</title>'
        f"</head><body><h1>Serwis w przygotowaniu</h1><p>{message}</p></body></html>"
    )
    response = HttpResponse(body, status=503, content_type="text/html; charset=utf-8")
    response["Retry-After"] = str(max(1, retry))
    response["Cache-Control"] = "no-store"
    return response


def ensure_content_for_request(competition) -> HttpResponse | None:
    """Leniwe drzewo startowe (D7 p. 2) – ``None`` = można obsłużyć żądanie, inaczej odpowiedź 503.

    Nic nie robi dla konkursu spoza listy API (``synced_at`` puste – np. założonego ręcznie) ani
    dla witryny, która ma treść. Nigdy nie czeka: blokada zajęta, semafor procesu pełny albo
    niedawna porażka → od razu 503 z ``Retry-After``. Komunikaty 503 są stałymi napisami – bez
    danych z API czy paczki.
    """
    from apps.live.client import MainApiError

    if competition.synced_at is None or has_content(competition):
        return None
    if wait := retry_after(competition):
        return _unavailable(MESSAGE_PREPARING, retry=wait)
    if not _import_slots.acquire(blocking=False):
        return _unavailable(MESSAGE_BUSY, retry=BUSY_RETRY_SECONDS)
    try:
        ensure_site(competition)
    except StarterBusy:
        return _unavailable(MESSAGE_BUSY, retry=BUSY_RETRY_SECONDS)
    except StarterBackoff:
        return _unavailable(MESSAGE_PREPARING, retry=retry_seconds())
    except StarterRefused as exc:
        logger.warning("Drzewo startowe w żądaniu odrzucone: %s", exc)
        return _unavailable(MESSAGE_DEPLOY, retry=retry_seconds())
    except (MainApiError, services.BundleError, services.ImportRefused) as exc:
        logger.warning("Drzewo startowe konkursu %s: import nie powiódł się (%s).", competition.slug, exc)
        return _unavailable(MESSAGE_PREPARING, retry=retry_seconds())
    finally:
        _import_slots.release()
    cache.set(CONTENT_KEY.format(pk=competition.pk), True, CONTENT_CHECK_SECONDS)
    return None


def import_missing(*, user=None, dry_run: bool = False) -> list[tuple[str, str]]:
    """``sync_competitions --import-missing``: każdy aktywny konkurs bez stron – pełny import z API.

    Zwraca ``[(slug, wynik)]`` – wynik to liczba stron albo powód porażki; porażka jednego konkursu
    nie zatrzymuje pozostałych. ``dry_run`` – tylko lista konkursów do importu.
    """
    from cms.models import Page

    from apps.live.client import MainApiError
    from apps.sites.models import CompetitionSite

    results: list[tuple[str, str]] = []
    for competition in CompetitionSite.objects.filter(is_active=True).select_related("site").order_by("slug"):
        if Page.objects.filter(site_id=competition.site_id).exists():
            continue
        if dry_run:
            results.append((competition.slug, "do importu"))
            continue
        try:
            report = ensure_site(competition, max_pages=None, user=user, deploy=True)
        except (
            StarterRefused,
            StarterBusy,
            MainApiError,
            services.BundleError,
            services.ImportRefused,
        ) as exc:
            results.append((competition.slug, f"błąd: {exc}"))
            continue
        pages = sum(report.pages.values()) if report is not None else 0
        results.append((competition.slug, f"zaimportowano stron: {pages}"))
    return results
