"""Sprawdzenie systemowe: piaskownica „inline” nie ma prawa działać w produkcji (QC-01 § 5).

Tryb inline wykonuje kod uczniów w podprocesie **workera** – z jego siecią (Redis, baza) i jego
zmiennymi środowiskowymi w zasięgu procesu-rodzica. Hak audytowy i limity zostają, ale granicy
kontenera (``network_mode: none``, brak sekretów) nie ma. Błąd, a nie ostrzeżenie: pomyłka w
``.env`` produkcji ma zatrzymać start, a nie wylądować w logu, którego nikt nie czyta.
"""

from __future__ import annotations

import re

from django.conf import settings
from django.core.checks import Error, Tags, register


@register()
def inline_runner_not_in_production(app_configs, **kwargs):
    if getattr(settings, "NOTEBOOK_RUNNER_INLINE", False) and not settings.DEBUG:
        test_settings = (
            settings.SETTINGS_MODULE.endswith(".test") if hasattr(settings, "SETTINGS_MODULE") else False
        )
        if not test_settings:
            return [
                Error(
                    "NOTEBOOK_RUNNER_INLINE=1 przy DEBUG=0: kod uczniów wykonywałby się w workerze "
                    "bez izolacji.",
                    hint="Usuń NOTEBOOK_RUNNER_INLINE z .env i uruchom kontener notebook-runner "
                    "(profil 'notebooks').",
                    id="notebooks.E001",
                )
            ]
    return []


#: Nazwa hosta z opcjonalnym portem (dev: ``lab.localhost:8000``); bez schematu i ścieżki.
LAB_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:[0-9]{1,5})?$")


@register()
def lab_host_is_separate(app_configs, **kwargs):
    """``NOTEBOOK_LAB_HOST`` (QC-02 § 1): poprawna nazwa i **inna** niż każdy host serwisu.

    Host serwisu podany jako host laboratorium oddałby kod uczniów originowi z sesją – dokładnie to,
    przed czym to ustawienie chroni – a ``NotebookLabHostMiddleware`` zamknąłby na nim całą aplikację.
    """
    host = getattr(settings, "NOTEBOOK_LAB_HOST", "")
    if not host:
        return []
    problem = None
    if not LAB_HOST_RE.match(host):
        problem = "to nie jest nazwa hosta (bez schematu, ścieżki i ukośnika; port tylko w dev)"
    else:
        name = host.split(":")[0]
        extra = getattr(settings, "EXTRA_DOMAINS", [])
        platform = {settings.SITE_DOMAIN.lower(), *(domain.lower() for domain in extra)}
        if name in platform or name.removeprefix("www.") in platform:
            problem = "to jest host serwisu (SITE_DOMAIN albo EXTRA_DOMAINS)"
        elif name in infrastructure_hosts():
            problem = "to jest host usługi platformy (dj., live., meet., monitor., errors., S3, Jitsi)"
    return _e002(host, problem)


def _e002(host: str, problem: str | None) -> list:
    if problem is None:
        return []
    return [
        Error(
            f"NOTEBOOK_LAB_HOST={host!r}: {problem}.",
            hint="Podaj osobny host laboratorium, np. lab.<SITE_DOMAIN> albo osobną domenę "
            "(docs/tasks/QC-02.md § 1), albo usuń zmienną z .env.",
            id="notebooks.E002",
        )
    ]


def infrastructure_hosts() -> set[str]:
    """Hosty usług platformy z własnym blokiem Caddy'ego (przegląd L2): ten sam host jako laboratorium
    to dwa bloki o jednej nazwie albo laboratorium w originie usługi z sesją (djcms, monitoring)."""
    from urllib.parse import urlsplit

    domain = settings.SITE_DOMAIN.lower()
    hosts = {f"{label}.{domain}" for label in ("www", "dj", "live", "meet", "monitor", "errors", "s3")}
    for url in (getattr(settings, "S3_PUBLIC_ENDPOINT_URL", ""), getattr(settings, "S3_ENDPOINT_URL", "")):
        if url:
            hosts.add((urlsplit(url).hostname or "").lower())
    hosts.add(str(getattr(settings, "JITSI_JWT_HOST", "")).lower())
    hosts.discard("")
    return hosts


@register(Tags.database)
def lab_host_is_not_a_competition(app_configs, databases=None, **kwargs):
    """``NOTEBOOK_LAB_HOST`` nie może być hostem istniejącego konkursu (przegląd L2).

    Z bazą, więc pod ``Tags.database``: wykonuje się przy ``migrate`` (entrypoint ``web``) i przy
    ``manage.py check --database default``, a nie przy każdym ``check`` bez bazy. Konkurs pod
    ``lab.<SITE_DOMAIN>`` (subdomena platformy założona przed rezerwacją etykiety ``lab``) albo
    z taką domeną własną zostałby zasłonięty blokiem laboratorium.
    """
    host = getattr(settings, "NOTEBOOK_LAB_HOST", "")
    if not host or not databases:
        return []
    from django.db import DatabaseError

    from apps.tenancy.models import Competition

    name = host.split(":")[0]
    try:
        rows = Competition.objects.select_related("site").only("slug", "primary_domain", "site__hostname")
        taken = {
            host_name.lower()
            for competition in rows
            for host_name in (competition.primary_domain, getattr(competition.site, "hostname", ""))
            if host_name
        }
    except DatabaseError:
        return []
    if name in taken:
        return _e002(host, "pod tym hostem stoi konkurs")
    return []
