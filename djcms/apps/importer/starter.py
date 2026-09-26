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

Współbieżność: ``ensure_site`` bierze ``pg_advisory_xact_lock(hashtext('djcms-site:' || slug))``
i **po** blokadzie sprawdza jeszcze raz, czy strony już są – drugi wątek (albo proces) czeka na
pierwszy i nie dubluje importu. Porażka (API niedostępne, paczka odrzucona) zamyka próby tego
konkursu na ``FAILURE_SECONDS`` – kolejne odsłony dostają od razu 503, zamiast pobierać paczkę
w każdym żądaniu.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.http import HttpResponse

from . import services

logger = logging.getLogger(__name__)

#: Największe drzewo startowe importowane w żądaniu (D7). Szablon „pusty” ma kilka stron.
STARTER_MAX_PAGES = 50
#: Największa paczka pobierana w żądaniu – drzewo startowe nie ma obrazów w pełnej rozdzielczości.
STARTER_MAX_BYTES = 32 * 1024 * 1024
#: Paczka w pamięci do tej wielkości, powyżej – plik tymczasowy w ``MEDIA_ROOT`` (nie tmpfs ``/tmp``).
STARTER_SPOOL_BYTES = 4 * 1024 * 1024
#: Po porażce kolejna próba dla tego konkursu najwcześniej po tylu sekundach.
FAILURE_SECONDS = 60
#: „Witryna ma treść” zapamiętane w buforze procesu – jedno zapytanie na konkurs na ten czas.
CONTENT_CHECK_SECONDS = 600
#: Konto techniczne – autor wersji importu, gdy nie ma żadnego aktywnego superusera.
IMPORT_USERNAME = "djcms-import"

FAILURE_KEY = "djcms:sites:starter-failed:{slug}"
CONTENT_KEY = "djcms:sites:has-content:{pk}"
LOCK_KEY = "djcms-site:{slug}"


class StarterRefused(Exception):  # noqa: N818 - nazwa mówi, co się stało
    """Drzewa startowego nie da się (albo nie wolno) zaimportować w żądaniu."""


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


def _lock(slug: str) -> None:
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [LOCK_KEY.format(slug=slug)])


def ensure_site(competition, *, max_pages: int | None = STARTER_MAX_PAGES, user=None):
    """Import drzewa konkursu z API do **pustej** witryny, pod blokadą; ``None`` – treść już była.

    ``max_pages`` – limit stron paczki (``None`` = bez limitu, wdrożenie). Błędy: ``StarterRefused``
    (paczka za duża), ``services.BundleError``/``services.ImportRefused``, ``MainApiError``.
    """
    from cms.models import Page

    from apps.live.client import MainApi
    from apps.sites.models import CompetitionSite

    with transaction.atomic():
        _lock(competition.slug)
        current = CompetitionSite.objects.select_related("site").get(pk=competition.pk)
        if not current.is_active:
            raise StarterRefused(f"konkurs „{current.slug}” jest nieaktywny")
        if Page.objects.filter(site_id=current.site_id).exists():
            return None
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
            report = services.run_import(bundle, user=user or import_user(), competition=current)
    logger.info(
        "Drzewo startowe konkursu %s: %s stron, %s wtyczek.",
        current.slug,
        sum(report.pages.values()),
        sum(report.plugins.values()),
    )
    return report


def _unavailable(message: str) -> HttpResponse:
    body = (
        '<!doctype html><html lang="pl"><head><meta charset="utf-8"><title>Serwis w przygotowaniu</title>'
        f"</head><body><h1>Serwis w przygotowaniu</h1><p>{message}</p></body></html>"
    )
    response = HttpResponse(body, status=503, content_type="text/html; charset=utf-8")
    response["Retry-After"] = str(FAILURE_SECONDS)
    response["Cache-Control"] = "no-store"
    return response


def ensure_content_for_request(competition) -> HttpResponse | None:
    """Leniwe drzewo startowe (D7 p. 2) – ``None`` = można obsłużyć żądanie, inaczej odpowiedź 503.

    Nic nie robi dla konkursu spoza listy API (``synced_at`` puste – np. założonego ręcznie) ani
    dla witryny, która ma treść. Komunikaty 503 są stałymi napisami – bez danych z API czy paczki.
    """
    from apps.live.client import MainApiError

    if competition.synced_at is None or has_content(competition):
        return None
    failure_key = FAILURE_KEY.format(slug=competition.slug)
    if cache.get(failure_key):
        return _unavailable("Serwis tego konkursu jest przygotowywany. Spróbuj ponownie za minutę.")
    try:
        ensure_site(competition)
    except StarterRefused as exc:
        logger.warning("Drzewo startowe w żądaniu odrzucone: %s", exc)
        cache.set(failure_key, True, FAILURE_SECONDS)
        return _unavailable(
            "Serwis tego konkursu nie jest jeszcze gotowy – treść zostanie zaimportowana przy "
            "najbliższym wdrożeniu."
        )
    except (MainApiError, services.BundleError, services.ImportRefused) as exc:
        logger.warning("Drzewo startowe konkursu %s: import nie powiódł się (%s).", competition.slug, exc)
        cache.set(failure_key, True, FAILURE_SECONDS)
        return _unavailable("Serwis tego konkursu jest przygotowywany. Spróbuj ponownie za minutę.")
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
            report = ensure_site(competition, max_pages=None, user=user)
        except (StarterRefused, MainApiError, services.BundleError, services.ImportRefused) as exc:
            results.append((competition.slug, f"błąd: {exc}"))
            continue
        pages = sum(report.pages.values()) if report is not None else 0
        results.append((competition.slug, f"zaimportowano stron: {pages}"))
    return results
