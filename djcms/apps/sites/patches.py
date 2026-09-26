"""Łatka ``SiteManager.get_current``: witryna z ``request.site``, jeśli warstwa ją ustawiła (D4).

Po co (sprawdzone w źródłach django CMS 5.1.3 / Django 6.1.1, docs/tasks/DJ-02.md § 5.1):
``cms.utils.get_current_site(request)`` czyta najpierw ``request.site``, ale część kodu woła
**bezpośrednio** ``Site.objects.get_current(request)`` i ``request.site`` pomija:

- ``cms.plugin_rendering.BaseRenderer.current_site`` – witryna w kluczu bufora placeholderów
  (``cms.cache.placeholder``: ``…|site:<id>|…``); unieważnienie bufora po edycji liczy się od
  ``page.site_id`` (``Placeholder.clear_cache``), więc render musi użyć tej samej witryny,
- ``django.contrib.sites.shortcuts.get_current_site`` (sitemapy Django, „zobacz na stronie”
  w adminie), ``cms.api`` (``get_page_draft``/``_verify_plugin_type``), admin uprawnień i użytkowników
  django CMS (``cms/admin/permissionadmin.py``, ``useradmin.py``).

Bez ``SITE_ID`` oryginał dopasowuje wyłącznie po **hoście** (``Site.domain``), czyli dla
konkursu pod prefiksem ścieżki oddałby witrynę gospodarza, a dla hosta, który nie jest
``Site.domain`` (``localhost:8100``, druga domena konkursu), rzuciłby ``Site.DoesNotExist`` – 500.

Łatka jest wąska: żądanie z ``request.site`` (ustawionym przez ``CompetitionSiteMiddleware``)
dostaje tę witrynę, **każde inne wywołanie** – oryginał. W szczególności wywołanie **bez żądania**
dalej kończy się ``ImproperlyConfigured`` (brak ``SITE_ID``) – to jest bezpiecznik: kod poza
żądaniem (komenda, importer) musi podać witrynę jawnie.

Łatka sprawdza sygnaturę oryginału: zmiana API Django (``get_current(self, request=None)``) ma
wywrócić start aplikacji i test, a nie cicho zmienić zachowanie.
"""

from __future__ import annotations

import inspect

from django.contrib.sites.models import Site, SiteManager
from django.core.exceptions import ImproperlyConfigured

PATCH_MARKER = "_dj_sites_patched"

#: Oryginał sprzed łatki – dla testów i dla gałęzi „bez ``request.site``”.
original_get_current = SiteManager.get_current

EXPECTED_PARAMETERS = ("self", "request")


def _check_signature() -> None:
    params = tuple(inspect.signature(original_get_current).parameters)
    if params != EXPECTED_PARAMETERS:
        raise ImproperlyConfigured(
            "django.contrib.sites.models.SiteManager.get_current ma inną sygnaturę niż "
            f"{EXPECTED_PARAMETERS} (jest {params}) – łatka apps.sites.patches wymaga przeglądu."
        )


def get_current(self, request=None):
    site = getattr(request, "site", None) if request is not None else None
    if isinstance(site, Site):
        return site
    return original_get_current(self, request)


setattr(get_current, PATCH_MARKER, True)


def is_installed() -> bool:
    return getattr(SiteManager.get_current, PATCH_MARKER, False)


def install() -> None:
    """Idempotentnie podmienia ``SiteManager.get_current`` (``AppConfig.ready``)."""
    if is_installed():
        return
    _check_signature()
    SiteManager.get_current = get_current  # type: ignore[method-assign]
