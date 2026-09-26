"""``manage.py sync_competitions [--dry-run] [--list-hosts] [--import-missing] [--prune SLUG --yes]``
– rejestr konkursów z API (DJ-02 D7).

Pobiera **bieżącą** listę ``GET /internal/djcms/v2/competitions`` (z pominięciem bufora
i bezpiecznika klienta) i uzgadnia z nią rejestr witryn (``apps.sites.registry.sync_registry``):
zakłada witryny nowych konkursów, aktualizuje hosty i adresy publiczne, wygasza konkursy nieaktywne
i zniknięte z listy. Nic nie kasuje. Idempotentne – drugi przebieg bez zmian w aplikacji głównej
nie zapisuje niczego.

- ``--dry-run`` – raport bez zapisu (z ``--import-missing``: lista konkursów, które dostałyby treść),
- ``--list-hosts`` – po uzgodnieniu wypisuje hosty aktywnych konkursów (po jednym w wierszu,
  ``<host> <slug>``) oraz konkursy pod prefiksem (``<host-gospodarza>/<prefiks>/ <slug>``) – dla
  kontroli dymnej skryptów przełączania (DJ-02h),
- ``--import-missing`` – po uzgodnieniu każdy aktywny konkurs, którego witryna nie ma ani jednej
  strony, dostaje treść z eksportu tego konkursu (``apps.importer.starter.import_missing``, bez
  limitu stron – tak woła to ``deploy.sh``). Porażka jednego konkursu = kod 1 na końcu, pozostałe
  importowane,
- ``--prune SLUG --yes`` – **kasuje** witrynę konkursu nieaktywnego w rejestrze (strony, hosty,
  przekierowania; foldery filera zostają). Wyłącznie po kopii bazy (D7) i wyłącznie konkurs, którego
  aplikacja główna już nie wystawia jako aktywnego – aktywnego nie da się skasować tą drogą.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

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
        parser.add_argument(
            "--import-missing",
            action="store_true",
            help="Zaimportuj treść z API do witryn aktywnych konkursów, które nie mają żadnej strony.",
        )
        parser.add_argument("--prune", metavar="SLUG", help="Skasuj witrynę nieaktywnego konkursu (z --yes).")
        parser.add_argument("--yes", action="store_true", help="Potwierdzenie dla --prune.")

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
        if options["prune"]:
            self._prune(options["prune"], confirmed=options["yes"], dry_run=options["dry_run"])
        failures = (
            self._import_missing(report, dry_run=options["dry_run"]) if options["import_missing"] else []
        )
        if options["list_hosts"]:
            for line in host_lines():
                self.stdout.write(line)
        if failures:
            raise CommandError(f"Import treści nie powiódł się dla: {', '.join(failures)}.")
        if not options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("sync_competitions: gotowe."))

    def _import_missing(self, report, *, dry_run: bool) -> list[str]:
        from apps.importer.starter import import_missing

        results = import_missing(dry_run=dry_run)
        if dry_run:
            # Uzgodnienie próbne jest wycofane – nowe konkursy nie mają jeszcze witryny w bazie.
            known = {slug for slug, _ in results}
            fresh = sorted(set(report.created) - set(report.deactivated) - known)
            results += [(slug, "do importu (nowy konkurs)") for slug in fresh]
        if not results:
            self.stdout.write("--import-missing: każda aktywna witryna ma już treść.")
        failures = []
        for slug, outcome in results:
            self.stdout.write(f"--import-missing: {slug} – {outcome}")
            if outcome.startswith("błąd"):
                failures.append(slug)
        return failures

    def _prune(self, slug: str, *, confirmed: bool, dry_run: bool) -> None:
        from cms.models import Page

        competition = CompetitionSite.objects.select_related("site").filter(slug=slug).first()
        if competition is None:
            raise CommandError(f"--prune: konkursu „{slug}” nie ma w rejestrze.")
        if competition.is_active:
            raise CommandError(
                f"--prune: konkurs „{slug}” jest aktywny w aplikacji głównej – kasowanie wyłącznie "
                "konkursu nieaktywnego albo usuniętego z listy."
            )
        pages = Page.objects.filter(site=competition.site).count()
        if dry_run or not confirmed:
            self.stdout.write(
                f"--prune: witryna „{slug}” ma {pages} stron. Skasowanie jest nieodwracalne – najpierw "
                "kopia bazy (scripts/backup.sh), potem to samo polecenie z --yes (bez --dry-run)."
            )
            if not dry_run:
                raise CommandError("--prune wymaga --yes.")
            return
        site = competition.site
        with transaction.atomic():
            for root in Page.get_root_nodes().filter(site=site):
                root.delete()
            competition.delete()  # hosty kaskadą
            site.delete()  # przekierowania i pozostałe wiersze witryny kaskadą
        self.stdout.write(f"--prune: skasowano witrynę „{slug}” ({pages} stron). Foldery filera zostały.")


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
