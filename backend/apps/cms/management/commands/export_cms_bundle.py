"""``manage.py export_cms_bundle [--competition SLUG] --output PATH|-`` – paczka treści dla ``dj.``.

Ta sama paczka, którą oddaje ``GET /internal/djcms/v1/export`` (``apps.cms.export_bundle``), tylko
do pliku albo na standardowe wyjście – dla importu ręcznego i dla przeniesienia treści między
instalacjami bez sieci compose'a. Raport idzie na **stderr**, bo stdout bywa samą paczką
(``--output -``): tekst wymieszany z bajtami ZIP-a dałby plik, którego nie otworzy żaden importer.

Konkurs domyślny jest ten sam, co w API (``djcms_competition``): ``DJCMS_COMPETITION_SLUG`` albo
konkurs witryny domyślnej.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.cms.djcms_api.auth import djcms_competition
from apps.cms.export_bundle import build_bundle
from apps.tenancy.context import competition_context


class Command(BaseCommand):
    help = "Buduje paczkę treści redakcyjnej (ZIP) dla wersji porównawczej na django CMS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--competition",
            metavar="SLUG",
            default="",
            help="Identyfikator konkursu. Domyślnie DJCMS_COMPETITION_SLUG albo konkurs witryny domyślnej.",
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
        with competition_context(competition):
            if output == "-":
                # ``OutputWrapper`` Django pisze tekst; paczka jest binarna, więc piszemy do
                # strumienia pod spodem (``sys.stdout.buffer`` albo strumień bajtów podany w teście).
                target = self.stdout._out
                report = build_bundle(competition, stream=getattr(target, "buffer", target))
            else:
                path = Path(output)
                with path.open("wb") as handle:
                    report = build_bundle(competition, stream=handle)
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
        competition = djcms_competition()
        if competition is None:
            raise CommandError(
                "Nie udało się ustalić konkursu: ustaw DJCMS_COMPETITION_SLUG albo podaj --competition."
            )
        return competition
