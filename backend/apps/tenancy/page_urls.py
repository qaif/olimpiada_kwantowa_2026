"""Adresy stron Wagtaila dla konkursu adresowanego prefiksem ścieżki (§ 2.3, uwaga T43).

Wagtail liczy adres strony z dwóch rzeczy: ``root_url`` witryny, w której drzewie strona stoi,
i ``reverse("wagtail_serve", …)``, czyli ścieżki poprzedzonej **bieżącym** prefiksem skryptu
(``get_script_prefix``). Dla konkursu z własną domeną to jest dokładnie to, czego chcemy. Dla
konkursu pod prefiksem ścieżki obie połówki kłamią:

- ``root_url`` jego witryny to domena, **na którą konkurs dopiero czeka** (``e2e-druga.test``),
  a odpowiada on pod hostem platformy – ``full_url`` (podgląd w panelu, adres kanoniczny, link
  w liście) prowadziłby pod adres, którego nie ma;
- prefiks skryptu jest prefiksem **żądania**, a nie strony: w żądaniu ``/druga/…`` link do strony
  Olimpiady Kwantowej dostałby ``/druga/`` z przodu, a w żądaniu bez prefiksu link do strony
  konkursu drugiego – nie dostałby go wcale.

Poprawiamy więc wynik ``Page.get_url_parts`` – tej jednej metody, z której Wagtail składa
``url``, ``full_url``, ``relative_url``, ``get_site`` i ``{% pageurl %}``. **Dlaczego podmiana
metody klasy bazowej, a nie nadpisanie w ``CMSPage``:** menu, przekierowania, linki w treści
i panel redakcyjny czytają strony jako bazowe ``wagtailcore.Page`` (bez ``.specific``), więc
nadpisanie w naszej podklasie ominęłoby właśnie te miejsca, a Wagtail nie ma haka na adres
strony. Podmianę instaluje ``TenancyConfig.ready``, jest idempotentna i opakowuje oryginał –
nie kopiuje go.

**Koszt dla Konkursu #1: zero.** Strona witryny, z której przyszło żądanie, w żądaniu bez
prefiksu wraca **tym samym** obiektem, który policzył Wagtail (pierwsza gałąź
:func:`rebase_url_parts`), a instalacja z jedną witryną nie wchodzi dalej także bez żądania.
Mapa „witryna → prefiks” (:func:`prefix_sites`) jest potrzebna wyłącznie dla linków między
witrynami i dla adresów liczonych bez żądania – i wtedy kosztuje jeden odczyt pamięci podręcznej
(na zimno jedno zapytanie), zapamiętany na żądaniu albo na stronie tak, jak Wagtail zapamiętuje
ścieżki witryn.
"""

from __future__ import annotations

import functools
import logging

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Q
from django.http import HttpRequest
from django.urls import get_script_prefix

logger = logging.getLogger(__name__)

#: Klucz mapy w pamięci podręcznej. Unieważniany sygnałami (``apps/tenancy/signals.py``) przy
#: każdym zapisie i skasowaniu konkursu albo witryny – czas życia jest tylko siatką asekuracyjną.
CACHE_KEY = "tenancy:path_prefix_sites"
CACHE_SECONDS = 60 * 60

#: Atrybut, pod którym mapę zapamiętujemy na żądaniu albo na stronie (jak
#: ``_wagtail_cached_site_root_paths`` Wagtaila) – drugi link na tej samej stronie nie pyta już
#: nawet pamięci podręcznej.
MEMO_ATTRIBUTE = "_tenancy_path_prefix_sites"


def invalidate() -> None:
    """Zdejmuje mapę z pamięci podręcznej – woła to sygnał zapisu konkursu i witryny."""
    cache.delete(CACHE_KEY)


def _load() -> dict:
    """``{"platform": <root_url witryny domyślnej>, "prefixes": {site_id: prefiks}}`` z bazy.

    Jedno zapytanie: witryna domyślna (adres platformy) i witryny aktywnych konkursów w trybie
    ``PATH`` to wiersze tej samej tabeli, więc alternatywa w jednym ``WHERE`` wystarcza.
    """
    from wagtail.models import Site

    from apps.tenancy.models import RoutingMode

    rows = Site.objects.filter(
        Q(is_default_site=True) | Q(competition__routing_mode=RoutingMode.PATH, competition__is_active=True)
    ).values_list(
        "pk",
        "hostname",
        "port",
        "is_default_site",
        "competition__routing_mode",
        "competition__is_active",
        "competition__path_prefix",
    )
    platform = ""
    prefixes: dict[int, str] = {}
    for pk, hostname, port, is_default, mode, active, prefix in rows:
        if is_default:
            platform = Site(hostname=hostname, port=port).root_url
        if mode == RoutingMode.PATH and active and prefix:
            prefixes[pk] = prefix
    return {"platform": platform, "prefixes": prefixes}


def prefix_sites(cache_object=None) -> dict:
    """Mapa witryn konkursów pod prefiksem i adres platformy. Patrz :func:`_load`.

    Błąd bazy daje mapę pustą, czyli adresy dokładnie takie, jakie policzył Wagtail: link bez
    prefiksu jest lepszy niż wywrócona strona, a ta funkcja chodzi przy składaniu każdego menu.
    """
    if cache_object is not None:
        memo = getattr(cache_object, MEMO_ATTRIBUTE, None)
        if memo is not None:
            return memo
    value = cache.get(CACHE_KEY)
    if value is None:
        try:
            value = _load()
        except DatabaseError:
            logger.warning("Nie udało się odczytać konkursów pod prefiksem ścieżki.", exc_info=True)
            return {"platform": "", "prefixes": {}}
        cache.set(CACHE_KEY, value, CACHE_SECONDS)
    if cache_object is not None:
        try:
            setattr(cache_object, MEMO_ATTRIBUTE, value)
        except AttributeError:  # pragma: no cover - obiekt bez ``__dict__``
            pass
    return value


def _current_site(request):
    """Witryna żądania **bez** zapytania: ta, którą zapamiętał Wagtail albo warstwa konkursu.

    Czytamy zapamiętaną wartość zamiast wołać ``Site.find_for_request``: w żądaniu, które przeszło
    przez ``CompetitionMiddleware``, jest ona zawsze (warstwa rozstrzyga witrynę na wejściu), a
    żądanie złożone ręcznie (``RequestFactory``) nie ma płacić tu zapytaniem, którego Wagtail sam
    by nie zadał.
    """
    if not isinstance(request, HttpRequest):
        return None
    return getattr(request, "_wagtail_site", None)


def rebase_url_parts(page, parts, request=None):
    """``(site_id, root_url, page_path)`` z prefiksem konkursu strony zamiast prefiksu żądania."""
    if parts is None or parts[2] is None:
        return parts
    site_id, root_url, page_path = parts

    current = _current_site(request)
    if current is not None and current.pk == site_id:
        # Strona witryny, z której przyszło żądanie: prefiks żądania **jest** prefiksem strony.
        request_prefix = getattr(request, "competition_script_prefix", "")
        if not request_prefix:
            return parts
        host_site = getattr(request, "competition_host_site", None)
        platform = host_site.root_url if host_site is not None else prefix_sites(request)["platform"]
        return (site_id, platform or root_url, page_path)

    script_prefix = get_script_prefix()
    cache_object = request if isinstance(request, HttpRequest) else page
    if script_prefix == "/" and len({path[0] for path in page._get_site_root_paths(cache_object)}) == 1:
        # Jedna witryna i żądanie bez prefiksu: nie ma czego przeliczać (Konkurs #1 bez żądania).
        return parts

    mapping = prefix_sites(cache_object)
    target = mapping["prefixes"].get(site_id)
    if target is None and script_prefix == "/":
        return parts
    relative = (
        page_path[len(script_prefix) :] if page_path.startswith(script_prefix) else page_path.lstrip("/")
    )
    if target is None:
        # Strona konkursu z własną domeną, liczona w żądaniu pod cudzym prefiksem: prefiks żądania
        # nie należy do jej adresu.
        return (site_id, root_url, f"/{relative}")
    return (site_id, mapping["platform"] or root_url, f"/{target}/{relative}")


def install(page_class) -> None:
    """Opakowuje ``page_class.get_url_parts`` – raz, niezależnie od liczby wywołań ``ready``."""
    original = page_class.get_url_parts
    if getattr(original, "_tenancy_prefix_aware", False):
        return

    @functools.wraps(original)
    def get_url_parts(self, request=None):
        return rebase_url_parts(self, original(self, request=request), request)

    get_url_parts._tenancy_prefix_aware = True
    page_class.get_url_parts = get_url_parts
