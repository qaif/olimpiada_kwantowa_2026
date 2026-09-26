"""``manage.py export_cms_bundle [--competition SLUG] --output PATH|-`` – paczka CMS (wersja 2).

Ta sama paczka, którą oddaje ``GET /internal/djcms/v2/c/<slug>/export``, tylko do pliku albo na
standardowe wyjście – dla importu ręcznego i dla przeniesienia treści między instalacjami bez sieci
compose'a. Raport idzie na **stderr**, bo stdout bywa samą paczką (``--output -``): tekst wymieszany
z bajtami ZIP-a dałby plik, którego nie otworzy żaden importer.

Bez ``--competition`` – aktywny konkurs witryny domyślnej (ten spod ``SITE_DOMAIN``). Adresy
aplikacji w paczce idą pod adres **tego** konkursu (``competition_public_base``); konkurs bez
adresu kończy się błędem, zanim powstanie plik. Paczki wersji 1 (DJ-01) ta komenda już nie buduje
– usunięta w DJ-02k razem z API v1.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.cms.djcms_api.serializers import competition_public_base, platform_competition
from apps.cms.export_bundle import build_bundle
from apps.tenancy.context import competition_context


class Command(BaseCommand):
    help = "Buduje paczkę treści redakcyjnej (ZIP) dla wersji porównawczej na django CMS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--competition",
            metavar="SLUG",
            default="",
            help="Identyfikator konkursu. Domyślnie aktywny konkurs witryny domyślnej.",
        )
        parser.add_argument(
            "--output",
            required=True,
            metavar="PATH",
            help="Plik docelowy albo „-” (standardowe wyjście).",
        )

    def handle(self, *args, **options):
        competition = self._competition(options["competition"])
        output = options["output"]
        # Przed otwarciem pliku: konkurs bez adresu nie może zostawić po sobie pustego ZIP-a.
        base = competition_public_base(competition)
        if base is None:
            raise CommandError(
                f"Konkurs „{competition.slug}” nie ma adresu w aplikacji głównej (domena albo prefiks "
                "ścieżki z otwartą bramką path_prefix_routing konkursu platformy)."
            )
        with competition_context(competition):
            if output == "-":
                # ``OutputWrapper`` Django pisze tekst; paczka jest binarna, więc piszemy do
                # strumienia pod spodem (``sys.stdout.buffer`` albo strumień bajtów podany w teście).
                target = self.stdout._out
                report = build_bundle(
                    competition,
                    stream=getattr(target, "buffer", target),
                    main_public_url=base,
                )
            else:
                path = Path(output)
                with path.open("wb") as handle:
                    report = build_bundle(competition, stream=handle, main_public_url=base)
        self.stderr.write(
            f"Paczka konkursu „{competition.slug}”: {report.pages} stron, {report.images} obrazów, "
            f"{report.documents} dokumentów."
        )
        for line in report.skipped:
            self.stderr.write(f"  pominięto – {line}")

    def _competition(self, slug: str):
        from apps.tenancy.models import Competition

        if slug:
            competition = Competition.objects.filter(slug=slug).select_related("site").first()
            if competition is None:
                raise CommandError(f"Nie ma konkursu o identyfikatorze „{slug}”.")
            return competition
        competition = platform_competition()
        if competition is None:
            raise CommandError("Witryna domyślna nie ma aktywnego konkursu – podaj --competition.")
        return competition
