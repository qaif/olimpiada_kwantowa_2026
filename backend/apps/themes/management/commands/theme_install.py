"""``manage.py theme_install <paczka.zip | -> [--activate <slug_konkursu>]`` – motyw przy wdrożeniu.

Ta sama ścieżka co panel superkoordynatora (``apps.themes.services.install_package``): walidacja,
skan ClamAV, publikacja plików, wpis audytu. ``-`` czyta paczkę ze standardowego wejścia, żeby
dało się ją podać do kontenera bez kopiowania pliku::

    docker compose exec -T web python manage.py theme_install - --activate iqo < iqo-quantum-1.0.0.zip

``--activate`` ustawia wersję w konkursie od razu (z domyślnymi wariantami układów). Bez niego
motyw czeka w katalogu na wybór koordynatora w panelu. Kod wyjścia ≠ 0 przy paczce odrzuconej –
skrypt wdrożenia ma się na niej zatrzymać.
"""

from __future__ import annotations

import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.context import competition_context
from apps.themes import services
from apps.themes.package import MAX_PACKAGE_BYTES


class Command(BaseCommand):
    help = (
        "Wgrywa paczkę motywu (ZIP; '-' = stdin) i opcjonalnie aktywuje ją w konkursie (--activate <slug>)."
    )

    def add_arguments(self, parser):
        parser.add_argument("package", help="ścieżka do paczki .zip albo '-' (standardowe wejście)")
        parser.add_argument("--activate", metavar="SLUG", help="slug konkursu, w którym aktywować motyw")

    def handle(self, *args, **options):
        from apps.tenancy.models import Competition

        competition = None
        if options["activate"]:
            competition = Competition.objects.filter(slug=options["activate"]).first()
            if competition is None:
                raise CommandError(f"Nie ma konkursu o slugu {options['activate']!r}.")
        source = options["package"]
        if source == "-":
            data = sys.stdin.buffer.read(MAX_PACKAGE_BYTES + 1)
        else:
            path = Path(source)
            if not path.is_file():
                raise CommandError(f"Nie ma pliku {source!r}.")
            data = path.read_bytes()
        version, result = services.install_package(data)
        for warning in result.warnings:
            self.stdout.write(f"  ostrzeżenie: {warning}")
        for error in result.errors:
            self.stderr.write(f"  błąd: {error}")
        if version is None or not version.is_valid:
            raise CommandError("Paczka odrzucona – szczegóły wyżej.")
        self.stdout.write(
            self.style.SUCCESS(
                f"Wgrano motyw {version.theme.slug} {version.version} (id {version.pk}, "
                f"pliki pod {version.public_prefix})."
            )
        )
        if competition is not None:
            with competition_context(competition):
                services.activate(competition, version, competition.theme_options)
            self.stdout.write(self.style.SUCCESS(f"Aktywowano w konkursie {competition.slug}."))
