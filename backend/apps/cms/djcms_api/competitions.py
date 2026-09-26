"""Lista konkursów dla serwisu na django CMS (``GET /internal/djcms/v2/competitions``, DJ-02 § 4.2).

djcms obsługuje **każdy** konkurs platformy i sam rozstrzyga, który konkurs stoi pod hostem
żądania (``djcms/apps/sites/resolution.py``). Mapowanie host → konkurs jest jednak autorytatywne
tutaj, w aplikacji głównej (``apps.tenancy.resolution.resolve_for_request``), więc ta lista niesie
dokładnie te dane, z których reguła aplikacji korzysta:

- ``hosts`` – nazwy, pod którymi Wagtail dopasowuje witrynę konkursu (``Site.hostname``) plus
  ``primary_domain`` (adres z linków w listach i z pytania o certyfikat); konkurs witryny domyślnej
  dodatkowo ``SITE_DOMAIN``. **Także w trybie ``PATH``**: witryna konkursu pod prefiksem ma własny
  host (domenę, na którą czeka) i ``resolve_for_request`` rozstrzyga go po nim tak samo, jak każdy
  inny – subdomena platformy takiego konkursu działa w ``web`` od chwili założenia,
- ``hosts_path_prefixes`` – bramka ``path_prefix_routing`` konkursu-gospodarza,
- ``routing_mode``/``path_prefix`` – konkurs pod prefiksem ścieżki,
- ``is_default`` – witryna domyślna (adresy ``localhost``/``127.0.0.1`` w devie).

Zgodność obu algorytmów pilnują wspólne wektory ``backend/djcms_contract/resolution_cases.json``
(test ``apps/tenancy/tests/test_resolution_contract.py`` i ich lustro w djcms).

Hosty są **rozłączne między konkursami** (djcms trzyma je z ``unique``): host będący witryną
jednego konkursu nie trafia do drugiego tylko dlatego, że ten ma go (po rozjeździe danych) w
``primary_domain`` – rozstrzyga witryna, bo to po niej dopasowuje Wagtail.

Lista obejmuje także konkursy **nieaktywne** (``is_active: false``): djcms musi wiedzieć, że ma
wygasić ich hosty, a nie tylko „przestać o nich słyszeć”. Bez danych osobowych i bez adresów
e-mail organizatora – djcms nie ma tu czego z nich budować.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.conf import settings
from django.db.models import Exists, OuterRef

from . import serializers as s

#: Ścieżki stron, do których aplikacja linkuje **literałem** w szablonach spoza ``templates/cms/``
#: (``href="/faq/"`` w zgłoszeniach, ``/harmonogram/`` przy rejestracji, ``/dokumenty/rodo/`` po
#: usunięciu konta, ``/warsztaty/`` przy materiałach). Test (``test_djcms_api_v2.py``) przeszukuje
#: szablony i pada, gdy pojawi się literał spoza tej listy – nowy odnośnik aplikacji do strony
#: redakcyjnej musi trafić tutaj, inaczej djcms nie ostrzeże o stronie, pod którą nic nie stoi.
APP_LITERAL_PAGE_PATHS = ("/dokumenty/rodo/", "/faq/", "/harmonogram/", "/warsztaty/")


def normalise_host(value: str) -> str:
    """Host do porównań: bez portu, bez kropki końcowej, małymi literami (jak ``resolution``)."""
    return (value or "").strip().lower().partition(":")[0].rstrip(".")


def fingerprint(dto: dict) -> str:
    """SHA-256 kanonicznego JSON-u pól konkursu (bez samego ``fingerprint``).

    djcms porównuje go z zapisanym i przy zgodności nie zapisuje niczego – uzgadnianie rejestru co
    minutę kosztuje wtedy jedno żądanie, a nie N zapisów. Klucze posortowane, bez odstępów: ta sama
    treść daje ten sam skrót niezależnie od kolejności budowania słownika.
    """
    payload = {key: value for key, value in dto.items() if key != "fingerprint"}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def linked_paths(competition) -> list[str]:
    """Ścieżki stron (względem korzenia witryny konkursu), do których linkuje **aplikacja**.

    Trzy źródła:

    - dokumenty zgód konkursu (``apps.accounts.consents.document_url`` dla slugów z
      ``consent_set``) – strona dokumentu w drzewie konkursu, a bez niej adres kanoniczny
      ``/dokumenty/<slug>/``, bo dokładnie tam prowadzi wtedy link przy zgodzie,
    - strona warsztatów (``workshops_page``) i adres ``/warsztaty/``, który buduje pasek osi czasu
      (``apps.cms.timeline``),
    - literały z szablonów aplikacji (``APP_LITERAL_PAGE_PATHS``) – **poza** konkursem pod
      prefiksem: literał ``/faq/`` pod ``/<prefiks>/…`` prowadzi do konkursu-gospodarza, więc to
      jego lista go wymienia.

    Strona z ograniczonym dostępem liczy się jak każda: aplikacja do niej linkuje, a djcms jej nie
    dostanie (eksport pomija takie strony) – i właśnie o tym ma ostrzec ``dj_pages.W003``.
    """
    from apps.accounts.consents import DOCUMENTS_PATH, consent_set
    from apps.cms.export_bundle import site_path
    from apps.cms.models import DocumentPage
    from apps.cms.workshops import WORKSHOPS_SLUG, workshops_page
    from apps.tenancy.models import RoutingMode

    root = competition.site.root_page
    paths: list[str] = []
    slugs = [consent.document_slug for consent in consent_set(competition) if consent.document_slug]
    if slugs:
        found = {
            page.slug: site_path(page, root)
            for page in DocumentPage.objects.live()
            .descendant_of(root, inclusive=True)
            .filter(slug__in=slugs)
            .order_by("path")
        }
        paths.extend(found.get(slug, f"{DOCUMENTS_PATH}{slug}/") for slug in slugs)
    workshops = workshops_page(competition)
    if workshops is not None:
        paths.extend([site_path(workshops, root), f"/{WORKSHOPS_SLUG}/"])
    if competition.routing_mode != RoutingMode.PATH:
        paths.extend(APP_LITERAL_PAGE_PATHS)
    return sorted(set(paths))


def _hosts(competition, *, claimed: dict[str, int]) -> list[str]:
    """Hosty konkursu – rozłączne z hostami innych konkursów (patrz docstring modułu).

    ``claimed`` (host → konkurs) startuje z hostami **witryn** – te są rozstrzygnięte, bo po nich
    dopasowuje Wagtail – i rośnie o hosty dodatkowe (``primary_domain``, ``SITE_DOMAIN``) w kolejności
    listy. Host zajęty przez inny konkurs nie trafia do drugiego: djcms trzyma hosty z ``unique``,
    a dwa konkursy pod jednym hostem to i tak rozjazd danych, który zgłasza ``check_domains``.
    """
    own = normalise_host(competition.site.hostname)
    hosts = {own} if own else set()
    candidates = [competition.primary_domain]
    if competition.site.is_default_site:
        candidates.append(getattr(settings, "SITE_DOMAIN", "") or "")
    for candidate in candidates:
        host = normalise_host(candidate)
        if host and claimed.setdefault(host, competition.pk) == competition.pk:
            hosts.add(host)
    return sorted(hosts)


def competition_dto(competition, *, platform: Any, claimed: dict[str, int]) -> dict:
    """Jeden konkurs listy ``competitions`` – kształt z DJ-02 § 4.2 i ``fingerprint`` na końcu."""
    from apps.tenancy.models import RoutingMode
    from apps.tenancy.resolution import hosts_path_prefixes

    is_path = competition.routing_mode == RoutingMode.PATH
    base = s.competition_public_base(competition, platform=platform)
    dto: dict[str, Any] = {
        "slug": competition.slug,
        "name": competition.name,
        "short_name": competition.short_name,
        "is_active": competition.is_active,
        "is_default": bool(competition.site.is_default_site),
        "routing_mode": competition.routing_mode,
        "path_prefix": competition.path_prefix if is_path else "",
        "hosts": _hosts(competition, claimed=claimed),
        "hosts_path_prefixes": hosts_path_prefixes(competition),
        "public_base": {"origin": base.origin, "path_prefix": base.path_prefix} if base is not None else None,
        "site_hostname": normalise_host(competition.site.hostname),
        "has_site_aliases": bool(getattr(competition, "has_site_aliases", False)),
        "linked_paths": linked_paths(competition),
    }
    dto["fingerprint"] = fingerprint(dto)
    return dto


def competitions_payload() -> dict:
    """Treść odpowiedzi ``competitions`` (bez koperty ``api_version``/``generated_at``)."""
    from apps.tenancy.aliases import CompetitionSiteAlias
    from apps.tenancy.models import Competition

    rows = list(
        Competition.objects.select_related("site", "site__root_page")
        .annotate(has_site_aliases=Exists(CompetitionSiteAlias.objects.filter(competition=OuterRef("pk"))))
        .order_by("slug")
    )
    claimed = {normalise_host(row.site.hostname): row.pk for row in rows if row.site.hostname}
    platform = next((row for row in rows if row.site.is_default_site and row.is_active), None)
    default = next((row for row in rows if row.site.is_default_site), None)
    return {
        "platform": {
            "site_domain": normalise_host(getattr(settings, "SITE_DOMAIN", "") or ""),
            "platform_subdomains": bool(getattr(settings, "PLATFORM_SUBDOMAINS", False)),
            "default_slug": default.slug if default is not None else None,
        },
        "competitions": [competition_dto(row, platform=platform, claimed=claimed) for row in rows],
    }
