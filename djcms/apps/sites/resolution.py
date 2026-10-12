"""Rozstrzyganie konkursu żądania w djcms: host + ścieżka → ``CompetitionSite`` (DJ-02 § 2.1).

Lustro ``apps.tenancy.resolution.resolve_for_request`` aplikacji głównej – ta sama reguła
pierwszeństwa, sprawdzana wspólnymi wektorami testowymi (``backend/djcms_contract/
resolution_cases.json``, ``apps/sites/tests/test_resolution.py``). Mapowanie host → konkurs jest
autorytatywne w aplikacji głównej; tu działa na kopii z rejestru (``apps.sites.registry``).

1. ``CompetitionHost.host == host`` → konkurs hosta (tylko aktywny),
2. host lokalny (``localhost``, ``127.0.0.1``, ``djcms``) bez trafienia → konkurs domyślny
   (dev, healthcheck, wywołania z sieci compose'a),
3. konkurs hosta ma ``hosts_path_prefixes`` i pierwszy segment ścieżki to ``path_prefix`` innego
   aktywnego konkursu ``PATH`` → ten konkurs + prefiks do zdjęcia,
4. inaczej konkurs hosta albo ``None`` (warstwa odpowiada pustą 404, zanim cokolwiek się wyrenderuje).

Różnica wobec Wagtaila, świadoma: ``Site.find_for_request`` przy nieznanym hoście oddaje witrynę
domyślną; tu nieznany host (poza lokalnymi) to brak konkursu. Do djcms trafiają wyłącznie hosty,
które Caddy zna, więc nieznany host oznacza błąd konfiguracji albo konkurs, którego rejestr jeszcze
nie zna – a wtedy właściwą odpowiedzią jest odświeżenie rejestru, nie treść cudzego konkursu.

**Koszt: jedno zapytanie** (konkurs hosta, domyślny i prefiksu – alternatywą w jednym zapytaniu).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.db.models import Exists, OuterRef, Q

from .models import CompetitionHost, CompetitionSite, RoutingMode

#: Hosty, pod którymi bez własnego wpisu odpowiada konkurs domyślny (reguła 2).
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "djcms"})


def is_platform_host(host: str) -> bool:
    """Czy host należy do platformy – cel przekierowania, który wolno ustawić redaktorowi konkursu.

    Platforma to ``ALLOWED_HOSTS`` djcms (bez ``*``; z tych samych zmiennych co aplikacja główna:
    ``SITE_DOMAIN``, ``EXTRA_DOMAINS``, subdomeny platformy) oraz hosty i adresy publiczne
    **aktywnych** konkursów z rejestru. Konkurs wygaszony się nie liczy: jego domena mogła wygasnąć
    i trafić do kogoś innego.
    """
    from django.conf import settings
    from django.http.request import validate_host

    host = normalise_host(host)
    if not host:
        return False
    if validate_host(host, [pattern for pattern in settings.ALLOWED_HOSTS if pattern != "*"]):
        return True
    if CompetitionHost.objects.filter(host=host, competition__is_active=True).exists():
        return True
    origins = CompetitionSite.objects.filter(is_active=True).exclude(public_origin="")
    return any(
        normalise_host(urlsplit(origin).netloc) == host
        for origin in origins.values_list("public_origin", flat=True)
    )


@dataclass(frozen=True)
class Resolution:
    site: CompetitionSite | None
    #: Niepusty wyłącznie przy konkursie rozstrzygniętym po prefiksie ścieżki (reguła 3).
    path_prefix: str = ""


def normalise_host(value: str) -> str:
    """Host do porównań: bez portu, małymi literami, bez kropki końcowej i spacji.

    Ten sam kształt co ``_normalise_host`` aplikacji głównej plus zdjęcie portu (``request_host``);
    adres IPv6 w nawiasach (``[::1]:8000``) zostaje z nawiasami, bez portu.
    """
    host = (value or "").strip().lower()
    if host.startswith("["):
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host.rstrip(".")


def first_path_segment(path_info: str) -> str:
    """``"/druga/zadania/"`` → ``"druga"``; ``"/"`` → ``""`` (jak w aplikacji głównej)."""
    return (path_info or "").lstrip("/").split("/", 1)[0]


def resolve(host: str, path_info: str) -> Resolution:
    host = normalise_host(host)
    segment = first_path_segment(path_info)
    local = host in LOCAL_HOSTS

    by_host = Exists(CompetitionHost.objects.filter(competition=OuterRef("pk"), host=host))
    match = Q(by_host=True)
    if local:
        match |= Q(is_default=True)
    if segment:
        match |= Q(routing_mode=RoutingMode.PATH, path_prefix=segment)
    found = list(
        CompetitionSite.objects.filter(is_active=True)
        .annotate(by_host=by_host)
        .filter(match)
        .select_related("site")
        .order_by("pk")[:3]
    )

    host_site = next((c for c in found if c.by_host), None)
    if host_site is None and local:
        host_site = next((c for c in found if c.is_default), None)
    if host_site is not None and host_site.hosts_path_prefixes and segment:
        for candidate in found:
            if (
                candidate.pk != host_site.pk
                and candidate.routing_mode == RoutingMode.PATH
                and candidate.path_prefix == segment
            ):
                return Resolution(candidate, segment)
    return Resolution(host_site)
