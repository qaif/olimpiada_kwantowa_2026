"""``manage.py sync_competitions [--dry-run] [--list-hosts]`` – rejestr konkursów z API (DJ-02 D7).

Pobiera **bieżącą** listę ``GET /internal/djcms/v2/competitions`` (z pominięciem bufora
i bezpiecznika klienta) i uzgadnia z nią rejestr witryn (``apps.sites.registry.sync_registry``):
zakłada witryny nowych konkursów, aktualizuje hosty i adresy publiczne, wygasza konkursy nieaktywne
i zniknięte z listy. Nic nie kasuje. Idempotentne – drugi przebieg bez zmian w aplikacji głównej
nie zapisuje niczego.

- ``--dry-run`` – raport bez zapisu,
- ``--list-hosts`` – po uzgodnieniu wypisuje hosty aktywnych konkursów (po jednym w wierszu,
  ``<host> <slug>``) oraz konkursy pod prefiksem (``<host-gospodarza>/<prefiks>/ <slug>``) – dla
  kontroli dymnej skryptów przełączania (DJ-02h).

Import drzewa startowego nowych konkursów (``--import-missing``) i kasowanie witryny
(``--prune``) – DJ-02e.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.live.client import MainApi, MainApiError
from apps.sites import registry
from apps.sites.models import CompetitionSite, RoutingMode


class Command(BaseCommand):
    help = "Uzgadnia rejestr witryn konkursów z listą konkursów aplikacji głównej."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Raport bez zapisu.")
        parser.add_argument(
            "--list-hosts", action="store_true", help="Wypisz hosty aktywnych konkursów po uzgodnieniu."
        )

    def handle(self, *args, **options):
        try:
            payload = MainApi().fetch_competitions()
        except MainApiError as exc:
            raise CommandError(f"Lista konkursów z API niedostępna: {exc.code}") from None
        try:
            report = registry.sync_registry(payload, dry_run=options["dry_run"])
        except registry.RegistryError as exc:
            raise CommandError(f"Lista konkursów odrzucona: {exc}") from None
        prefix = "[dry-run] " if options["dry_run"] else ""
        for line in report.lines():
            self.stdout.write(f"{prefix}{line}")
        if options["list_hosts"]:
            for line in host_lines():
                self.stdout.write(line)
        if not options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("sync_competitions: gotowe."))


def host_lines() -> list[str]:
    active = list(CompetitionSite.objects.filter(is_active=True).prefetch_related("hosts").order_by("slug"))
    lines = [f"{host.host} {site.slug}" for site in active for host in site.hosts.all()]
    hosts_by_gateway = [site for site in active if site.hosts_path_prefixes]
    for site in active:
        if site.routing_mode != RoutingMode.PATH:
            continue
        for gateway in hosts_by_gateway:
            lines.extend(f"{host.host}/{site.path_prefix}/ {site.slug}" for host in gateway.hosts.all())
    return lines
