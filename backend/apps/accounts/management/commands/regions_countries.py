"""``manage.py regions_countries --competition <slug> [--dry-run]`` – kraje zamiast województw.

Przestawia konkurs międzynarodowy (``iqo``) na podział uczestników na **kraje** (docs/tasks/REG-01.md
§ 1.3): włącza ``custom_regions``, zakłada brakujące kraje z ``apps.accounts.countries``,
dezaktywuje szesnaście województw i „poza Polską”. Cała robota mieszka w
``apps.accounts.regions.switch_to_countries`` – tę samą funkcję woła ``create_competition
--regions countries``.

``--dry-run`` wykonuje wszystko i wycofuje transakcję (ten sam wzorzec, co ``create_competition``):
podgląd ma pokazać liczby policzone przez kod, który za chwilę je zapisze. Komenda jest
idempotentna – drugi przebieg wypisze same zera.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.regions import switch_to_countries
from apps.tenancy.context import competition_context
from apps.tenancy.models import Competition


class _DryRun(Exception):
    """Wycofanie transakcji próby na sucho – po policzeniu podsumowania."""


class Command(BaseCommand):
    help = "Przestawia konkurs na podział na kraje (custom_regions + lista ISO 3166-1)."

    def add_arguments(self, parser):
        parser.add_argument("--competition", required=True, help="Identyfikator konkursu, np. iqo.")
        parser.add_argument("--dry-run", action="store_true", help="Policz i pokaż, ale niczego nie zapisuj.")

    def handle(self, *args, **options):
        try:
            competition = Competition.objects.get(slug=options["competition"])
        except Competition.DoesNotExist as exc:
            raise CommandError(f"Nie ma konkursu o identyfikatorze „{options['competition']}”.") from exc

        result = None
        try:
            with transaction.atomic(), competition_context(competition):
                result = switch_to_countries(competition)
                if options["dry_run"]:
                    raise _DryRun
        except _DryRun:
            pass

        prefix = "[próba – nic nie zapisano] " if options["dry_run"] else ""
        self.stdout.write(f"{prefix}Konkurs: {competition.slug} ({competition.name})")
        self.stdout.write(
            f"  flaga custom_regions: {'włączona teraz' if result.flag_enabled else 'już włączona'}"
        )
        self.stdout.write(f"  dodane kraje: {result.added}")
        self.stdout.write(f"  Polska → „Poland”: {'tak' if result.poland_renamed else 'nie'}")
        self.stdout.write(f"  dezaktywowane regiony startowe: {result.deactivated}")
        self.stdout.write(f"  uczestnicy z nieaktywnym regionem: {result.stranded_participants}")
        if result.stranded_participants:
            self.stdout.write(
                self.style.WARNING(
                    "  Ci uczestnicy mają region, którego nie ma już na liście – przypisz im kraj "
                    "w karcie uczestnika (/coordinator/participants/)."
                )
            )
