"""Mapa uczestników z wiersza poleceń (MAP-01): stan, włączenie, wyłączenie, przeliczenie.

Dla operatora – ta sama robota, co ekran koordynatora ``/coordinator/participant-map/``, bez logowania
do panelu (np. włączenie mapy Olimpiadzie Kwantowej zaraz po wdrożeniu). Zmiana flagi idzie przez
``Competition.save`` (unieważnia zapisane strony gościa), przeliczenie – przez tę samą funkcję
agregatu, co strona; wpis w dzienniku zdarzeń bez wykonawcy (operator, nie konto).

    manage.py participant_map kwantowa             # stan i liczby
    manage.py participant_map kwantowa --enable    # włącza stronę i przelicza agregat
    manage.py participant_map kwantowa --disable
    manage.py participant_map kwantowa --refresh   # przelicza teraz
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.participant_map import services
from apps.tenancy.models import Competition


class Command(BaseCommand):
    help = "Mapa uczestników konkursu: stan, --enable, --disable, --refresh."

    def add_arguments(self, parser):
        parser.add_argument("competition", help="identyfikator (slug) konkursu, np. kwantowa")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--enable", action="store_true", help="włącza stronę (flaga participant_map)")
        group.add_argument("--disable", action="store_true", help="wyłącza stronę")
        parser.add_argument("--refresh", action="store_true", help="przelicza agregat od razu")

    def handle(self, *args, **options):
        from apps.core.models import audit
        from apps.tenancy.context import competition_context

        try:
            competition = Competition.objects.get(slug=options["competition"])
        except Competition.DoesNotExist as exc:
            raise CommandError(f"Nie ma konkursu „{options['competition']}”.") from exc
        with competition_context(competition):
            if options["enable"] or options["disable"]:
                wanted = bool(options["enable"])
                if services.enabled(competition) != wanted:
                    competition.feature_flags = {
                        **(competition.feature_flags or {}),
                        services.MAP_FLAG: wanted,
                    }
                    competition.save(update_fields=["feature_flags"])
                    audit(
                        None,
                        "participant_map.settings_changed",
                        competition,
                        {"enabled": [not wanted, wanted]},
                    )
                    self.stdout.write(f"Mapa uczestników: {'włączona' if wanted else 'wyłączona'}.")
                else:
                    self.stdout.write("Mapa uczestników: bez zmian.")
            if options["refresh"] or options["enable"]:
                result = services.recompute(competition)
                audit(
                    None,
                    "participant_map.refreshed",
                    competition,
                    {"total": result["total"], "towns": len(result["towns"])},
                )
            else:
                result = services.aggregate(competition)
        state = "włączona" if services.enabled(competition) else "wyłączona"
        self.stdout.write(
            f"{competition.slug}: strona {state}; uczestnicy {result['total']}, miejscowości na liście "
            f"{len(result['towns'])}, pod progiem {result['hidden_towns']} (próg {result['min_count']}), "
            f"bez punktu {result['unmapped']}, szkoła spoza wykazu {result['outside_directory']}."
        )
