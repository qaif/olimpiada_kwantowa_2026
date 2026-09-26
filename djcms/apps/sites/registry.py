"""Uzgadnianie rejestru konkursów z listą ``GET competitions`` aplikacji głównej (D7, DJ-02 § 1.2).

``sync_registry(payload)`` jest **idempotentne**: zakłada i aktualizuje ``django.contrib.sites.Site``,
``CompetitionSite`` i ``CompetitionHost``, wygasza konkursy nieaktywne i te, których lista już nie
zawiera. Wpis z tym samym odciskiem (``fingerprint``) co w bazie to zero zapisów – przy stałej
liście uzgadnianie kosztuje jedno zapytanie. Nic nie jest kasowane: skasowanie konkursu (i jego
drzewa stron) to decyzja operatora (``sync_competitions --prune``, DJ-02e), nie skutek uboczny
chwilowo pustej odpowiedzi API.

Wyzwalacze (D7): wdrożenie (``manage.py sync_competitions``), odświeżenie leniwe w warstwie
(``refresh_if_due`` – raz na ``DJCMS_SITES_REFRESH_SECONDS`` na proces, ``cache.add`` jako blokada,
więc odświeża jeden wątek) i nieznany host, który **świeża** lista z API przypisuje aktywnemu
konkursowi (``refresh_for_host``). Zakładanie drzewa startowego nowego konkursu (``ensure_site``)
to DJ-02e.

Witryna konkursu domyślnego przejmuje witrynę sprzed DJ-02 (``LEGACY_SITE_ID`` = dawne
``SITE_ID = 1``), jeśli ta nie należy jeszcze do żadnego konkursu – treść zaimportowana w DJ-01
zostaje więc na miejscu, bez migracji danych i bez znajomości sluga w chwili migracji.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

from .models import CompetitionHost, CompetitionSite, RoutingMode
from .resolution import normalise_host

logger = logging.getLogger(__name__)

#: Witryna z czasów ``SITE_ID = 1`` (DJ-01) – przejmuje ją konkurs domyślny.
LEGACY_SITE_ID = 1

#: Klucz „rejestr świeży” (D7 p. 3) – dopóki istnieje, warstwa nie pyta API o listę konkursów.
FRESH_KEY = "djcms:sites:fresh"

#: Sufiks domeny witryny, gdy ``site_hostname`` z API jest pusty albo zajęty (``Site.domain`` unikalne).
FALLBACK_DOMAIN_SUFFIX = ".djcms.invalid"

SLUG_RE = re.compile(r"^[a-z0-9-]{1,50}$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")

#: Klucz blokady doradczej Postgresa – dwa procesy nie uzgadniają rejestru jednocześnie.
ADVISORY_LOCK_KEY = "djcms-registry"


class RegistryError(Exception):
    """Lista konkursów odrzucona (zły kształt) albo niedostępna."""


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    adopted_legacy_site: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.deactivated)

    def lines(self) -> list[str]:
        out = []
        for label, items in (
            ("nowe", self.created),
            ("zmienione", self.updated),
            ("bez zmian", self.unchanged),
            ("wygaszone", self.deactivated),
        ):
            out.append(f"{label}: {', '.join(items) if items else '–'}")
        if self.adopted_legacy_site:
            out.append(f"konkurs domyślny przejął witrynę #{LEGACY_SITE_ID} (treść sprzed DJ-02)")
        return out


# --- walidacja odpowiedzi -------------------------------------------------------------------------


@dataclass(frozen=True)
class CompetitionEntry:
    slug: str
    name: str
    is_active: bool
    is_default: bool
    routing_mode: str
    path_prefix: str
    hosts: tuple[str, ...]
    hosts_path_prefixes: bool
    public_origin: str
    public_path_prefix: str
    site_hostname: str
    linked_paths: tuple[str, ...]
    fingerprint: str


def _str(value, what: str, *, limit: int, blank: bool = True) -> str:
    if not isinstance(value, str) or len(value) > limit or (not blank and not value.strip()):
        raise RegistryError(f"{what}: zły typ albo długość")
    return value.strip()


def _bool(value, what: str) -> bool:
    if not isinstance(value, bool):
        raise RegistryError(f"{what}: oczekiwano true/false")
    return value


def _public_base(value, what: str) -> tuple[str, str]:
    """``(origin, prefiks)`` adresu publicznego albo ``("", "")``.

    Adres w złym kształcie **nie** odrzuca całej listy (wtedy żaden konkurs nie dostałby witryny) –
    konkurs zostaje bez adresu publicznego (odnośniki do aplikacji wracają do
    ``DJCMS_MAIN_PUBLIC_URL``), a powód trafia do logu. Do ``href`` trafia wyłącznie ``http(s)``.
    """
    if value is None:
        return "", ""
    origin = value.get("origin") if isinstance(value, dict) else None
    prefix = value.get("path_prefix", "") if isinstance(value, dict) else None
    parts = urlsplit(origin) if isinstance(origin, str) and len(origin) <= 255 else None
    valid = (
        parts is not None
        and parts.scheme in ("http", "https")
        and bool(parts.netloc)
        and "@" not in parts.netloc
        and parts.path in ("", "/")
        and not parts.query
        and not parts.fragment
        and isinstance(prefix, str)
        and (prefix == "" or re.fullmatch(r"/[A-Za-z0-9_-]{1,40}", prefix) is not None)
    )
    if not valid or parts is None or not isinstance(prefix, str):
        logger.warning("Lista konkursów: %s – public_base w złym kształcie, pominięty.", what)
        return "", ""
    return f"{parts.scheme}://{parts.netloc.lower()}", prefix


def _entry(raw) -> CompetitionEntry:
    if not isinstance(raw, dict):
        raise RegistryError("competitions: wpis nie jest obiektem")
    slug = raw.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise RegistryError(f"competitions: niepoprawny slug {slug!r}")
    what = f"konkurs {slug}"
    routing_mode = raw.get("routing_mode")
    if routing_mode not in RoutingMode.values:
        raise RegistryError(f"{what}: routing_mode {routing_mode!r} spoza DOMAIN/PATH")
    path_prefix = _str(raw.get("path_prefix", ""), f"{what}.path_prefix", limit=40)
    if routing_mode == RoutingMode.PATH and not PREFIX_RE.match(path_prefix):
        raise RegistryError(f"{what}: konkurs PATH bez poprawnego path_prefix")
    if routing_mode == RoutingMode.DOMAIN:
        path_prefix = ""
    raw_hosts = raw.get("hosts", [])
    if not isinstance(raw_hosts, list):
        raise RegistryError(f"{what}: hosts nie jest listą")
    hosts = []
    for item in raw_hosts:
        host = normalise_host(_str(item, f"{what}.hosts", limit=255))
        if not HOST_RE.match(host):
            raise RegistryError(f"{what}: niepoprawny host {item!r}")
        if host not in hosts:
            hosts.append(host)
    raw_paths = raw.get("linked_paths", [])
    if not isinstance(raw_paths, list):
        raise RegistryError(f"{what}: linked_paths nie jest listą")
    linked_paths = tuple(_str(item, f"{what}.linked_paths", limit=255) for item in raw_paths)
    origin, public_prefix = _public_base(raw.get("public_base"), what)
    fingerprint = raw.get("fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        # Odcisk liczy aplikacja główna; bez niego (albo w innym kształcie) liczymy własny z wpisu –
        # „ten sam wpis = zero zapisów” ma działać niezależnie od tego pola.
        fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    return CompetitionEntry(
        slug=slug,
        name=_str(raw.get("name"), f"{what}.name", limit=200, blank=False),
        is_active=_bool(raw.get("is_active"), f"{what}.is_active"),
        is_default=_bool(raw.get("is_default"), f"{what}.is_default"),
        routing_mode=routing_mode,
        path_prefix=path_prefix,
        hosts=tuple(hosts),
        hosts_path_prefixes=_bool(raw.get("hosts_path_prefixes", False), f"{what}.hosts_path_prefixes"),
        public_origin=origin,
        public_path_prefix=public_prefix,
        site_hostname=normalise_host(
            _str(raw.get("site_hostname") or "", f"{what}.site_hostname", limit=100)
        ),
        linked_paths=linked_paths,
        fingerprint=fingerprint,
    )


def parse_payload(payload) -> list[CompetitionEntry]:
    """Wpisy listy konkursów po walidacji. Jeden zły wpis odrzuca całą listę (bez częściowego stanu)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("competitions"), list):
        raise RegistryError("odpowiedź competitions bez listy konkursów")
    entries = [_entry(raw) for raw in payload["competitions"]]
    slugs = [entry.slug for entry in entries]
    if len(set(slugs)) != len(slugs):
        raise RegistryError("competitions: powtórzony slug")
    if sum(1 for entry in entries if entry.is_default) > 1:
        raise RegistryError("competitions: więcej niż jeden konkurs domyślny")
    owners: dict[str, str] = {}
    for entry in entries:
        if not entry.is_active:
            continue
        for host in entry.hosts:
            if owners.setdefault(host, entry.slug) != entry.slug:
                raise RegistryError(f"competitions: host {host} przypisany dwóm aktywnym konkursom")
    return entries


# --- zapis ---------------------------------------------------------------------------------------


def _site_domain(entry: CompetitionEntry, site: Site | None) -> str:
    """``Site.domain`` dla konkursu: ``site_hostname`` z API, a przy kolizji – ``<slug>.djcms.invalid``."""
    wanted = entry.site_hostname or f"{entry.slug}{FALLBACK_DOMAIN_SUFFIX}"
    taken = Site.objects.filter(domain__iexact=wanted)
    if site is not None:
        taken = taken.exclude(pk=site.pk)
    return f"{entry.slug}{FALLBACK_DOMAIN_SUFFIX}" if taken.exists() else wanted


def _lock() -> None:
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [ADVISORY_LOCK_KEY])


def _legacy_site() -> Site | None:
    site = Site.objects.filter(pk=LEGACY_SITE_ID).first()
    if site is None or CompetitionSite.objects.filter(site=site).exists():
        return None
    return site


def _apply(entry: CompetitionEntry, current: CompetitionSite | None, now, report: SyncReport) -> None:
    site = current.site if current is not None else None
    if site is None and entry.is_default:
        site = _legacy_site()
        report.adopted_legacy_site = site is not None
    domain = _site_domain(entry, site)
    if site is None:
        site = Site.objects.create(domain=domain, name=entry.name[:50])
    elif site.domain != domain or site.name != entry.name[:50]:
        site.domain, site.name = domain, entry.name[:50]
        site.save(update_fields=["domain", "name"])

    values = {
        "site": site,
        "name": entry.name,
        "is_active": entry.is_active,
        "is_default": entry.is_default,
        "routing_mode": entry.routing_mode,
        "path_prefix": entry.path_prefix,
        "hosts_path_prefixes": entry.hosts_path_prefixes,
        "public_origin": entry.public_origin,
        "public_path_prefix": entry.public_path_prefix,
        "linked_paths": list(entry.linked_paths),
        "fingerprint": entry.fingerprint,
        "synced_at": now,
    }
    if current is None:
        current = CompetitionSite.objects.create(slug=entry.slug, **values)
        report.created.append(entry.slug)
    else:
        for name, value in values.items():
            setattr(current, name, value)
        current.save()
        report.updated.append(entry.slug)

    # Hosty: dokładnie te z API. Host, który przeszedł od innego konkursu, zmienia właściciela
    # (lista jest autorytatywna; ``parse_payload`` odrzuca host dwóch aktywnych konkursów naraz).
    wanted = set(entry.hosts)
    CompetitionHost.objects.filter(competition=current).exclude(host__in=wanted).delete()
    existing = set(CompetitionHost.objects.filter(host__in=wanted).values_list("host", flat=True))
    CompetitionHost.objects.filter(host__in=wanted).exclude(competition=current).update(competition=current)
    CompetitionHost.objects.bulk_create(
        [CompetitionHost(host=host, competition=current) for host in sorted(wanted - existing)]
    )


def sync_registry(payload, *, dry_run: bool = False) -> SyncReport:
    """Uzgadnia rejestr z listą konkursów. ``dry_run`` – raport bez zapisu (transakcja wycofana)."""
    entries = parse_payload(payload)
    report = SyncReport()
    now = timezone.now()
    with transaction.atomic():
        _lock()
        current = {site.slug: site for site in CompetitionSite.objects.select_related("site")}

        def stale(entry: CompetitionEntry) -> bool:
            row = current.get(entry.slug)
            # Odcisk z API obejmuje cały wpis; stan lokalny porównujemy osobno, bo mógł go zmienić
            # ktoś poza uzgadnianiem (wygaszenie konkursu, który zniknął z listy; ręczna poprawka).
            return row is None or (row.fingerprint, row.is_active, row.is_default) != (
                entry.fingerprint,
                entry.is_active,
                entry.is_default,
            )

        changed = [entry for entry in entries if stale(entry)]
        report.unchanged = sorted(entry.slug for entry in entries if not stale(entry))

        # Rolę konkursu domyślnego zdejmujemy najpierw wszystkim, którzy ją tracą – ograniczenie
        # „najwyżej jeden domyślny” obowiązuje po każdym pojedynczym zapisie.
        new_default = next((entry.slug for entry in entries if entry.is_default), None)
        if any(row.is_default and slug != new_default for slug, row in current.items()):
            CompetitionSite.objects.filter(is_default=True).exclude(slug=new_default).update(is_default=False)

        # Konkurs, którego lista już nie zawiera: wygaszony (hosty → 404), treść zostaje.
        listed = {entry.slug for entry in entries}
        for slug, gone in current.items():
            if slug in listed or not gone.is_active:
                continue
            gone.is_active, gone.is_default, gone.fingerprint, gone.synced_at = False, False, "", now
            gone.save(update_fields=["is_active", "is_default", "fingerprint", "synced_at"])
            report.deactivated.append(slug)

        for entry in sorted(changed, key=lambda item: (not item.is_default, item.slug)):
            row = current.get(entry.slug)
            was_active = row is not None and row.is_active
            _apply(entry, row, now, report)
            if not entry.is_active and (row is None or was_active):
                report.deactivated.append(entry.slug)

        report.created.sort()
        report.updated.sort()
        report.deactivated.sort()
        if dry_run:
            transaction.set_rollback(True)
    if report.changed and not dry_run:
        # ``SITE_CACHE`` Django trzyma witryny po id i po domenie – po zmianie domeny byłby nieaktualny.
        Site.objects.clear_cache()
    return report


# --- odświeżanie z API ---------------------------------------------------------------------------


def refresh_seconds() -> int:
    return int(getattr(settings, "DJCMS_SITES_REFRESH_SECONDS", 60))


def refresh_from_api(*, request=None) -> SyncReport | None:
    """Rejestr z listy konkursów przez klienta (bufor, kopia, bezpiecznik). ``None`` = brak listy."""
    from apps.live.client import MainApi

    result = MainApi().competitions(request=request)
    if result.data is None:
        return None
    try:
        return sync_registry(result.data)
    except RegistryError as exc:
        logger.warning("Lista konkursów z API odrzucona: %s", exc)
        return None


def refresh_if_due(*, request=None) -> SyncReport | None:
    """Odświeżenie leniwe (D7 p. 3): raz na ``DJCMS_SITES_REFRESH_SECONDS`` na proces.

    ``cache.add`` jest blokadą: klucz zakłada dokładnie jeden wątek i tylko on odświeża; pozostałe
    idą dalej z rejestrem, który jest w bazie. Porażka API nie jest tu błędem żądania – rejestr
    zostaje, jaki był, a klucz świeżości chroni API przed pytaniem przy każdej odsłonie.
    """
    seconds = refresh_seconds()
    if seconds <= 0 or not cache.add(FRESH_KEY, True, seconds):
        return None
    try:
        return refresh_from_api(request=request)
    except Exception:  # noqa: BLE001 - odświeżenie rejestru nie może wywrócić odsłony
        logger.warning("Odświeżenie rejestru konkursów nie powiodło się.", exc_info=True)
        return None


def listed_host(host: str, *, request=None) -> bool:
    """Czy lista konkursów z API (bufor klienta) przypisuje ten host **aktywnemu** konkursowi."""
    from apps.live.client import MainApi

    result = MainApi().competitions(request=request)
    competitions = (result.data or {}).get("competitions") if isinstance(result.data, dict) else None
    for raw in competitions if isinstance(competitions, list) else []:
        if not isinstance(raw, dict) or raw.get("is_active") is not True:
            continue
        hosts = raw.get("hosts")
        if isinstance(hosts, list) and any(isinstance(h, str) and normalise_host(h) == host for h in hosts):
            return True
    return False


def refresh_for_host(host: str, *, request=None) -> bool:
    """Nieznany host (D7 p. 2): uzgodnij rejestr, **o ile** lista z API zna ten host jako aktywny.

    Host spoza listy nie zmienia niczego w bazie (S15) – losowe nagłówki ``Host`` pod wildcardem
    platformy kosztują odczyt bufora klienta, nie zapis i nie pobranie paczki. Zwraca ``True``, gdy
    rejestr został uzgodniony (warto rozstrzygnąć żądanie jeszcze raz).
    """
    host = normalise_host(host)
    if not host or not listed_host(host, request=request):
        return False
    return refresh_from_api(request=request) is not None


def default_site() -> Site:
    """Witryna konkursu domyślnego – dla kodu poza żądaniem (importer sprzed DJ-02e).

    Brak w rejestrze = jedno uzgodnienie z **bieżącą** listą z API (komenda ma działać na świeżej
    bazie tuż po wdrożeniu). Dalej brak = ``RegistryError`` z instrukcją dla operatora.
    """
    found = CompetitionSite.objects.filter(is_default=True, is_active=True).select_related("site").first()
    if found is None:
        from apps.live.client import MainApi, MainApiError

        try:
            sync_registry(MainApi().fetch_competitions())
        except MainApiError as exc:
            raise RegistryError(f"lista konkursów z API niedostępna ({exc.code})") from None
        found = CompetitionSite.objects.filter(is_default=True, is_active=True).select_related("site").first()
    if found is None:
        raise RegistryError(
            "brak aktywnego konkursu domyślnego w rejestrze – uruchom manage.py sync_competitions"
        )
    return found.site
