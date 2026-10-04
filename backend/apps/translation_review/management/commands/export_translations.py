"""``manage.py export_translations`` – zatwierdzone poprawki tłumaczy do katalogów ``.po``.

Tryby (L10N-01 § 7, opis kroków w ``apps.translation_review.export``):

- bez opcji – nakładki z bazy → ``msgstr`` w katalogach tego checkoutu (``--dry-run`` = tylko raport),
- ``--to-json PLIK`` – zrzut nakładek do pliku (``-`` = standardowe wyjście); na produkcji, gdzie
  kontener nie ma gita, a pliki obrazu i tak zniknęłyby przy kolejnym wdrożeniu,
- ``--from-json PLIK`` – to samo co tryb domyślny, ale z pliku zamiast z bazy (checkout dewelopera),
- ``--prune`` – po wdrożeniu: usuwa z bazy nakładki, których tekst jest już w katalogu.
"""

from __future__ import annotations

import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.translation_review import catalogs, export


class Command(BaseCommand):
    help = "Przenosi zatwierdzone poprawki tłumaczy z bazy do plików .po (albo do/z pliku JSON)."

    def add_arguments(self, parser):
        parser.add_argument("--language", action="append", help="Tylko ten język (można powtórzyć).")
        parser.add_argument("--dry-run", action="store_true", help="Pokaż, co by się zmieniło, bez zapisu.")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--to-json", metavar="PLIK", help="Zapisz nakładki do pliku JSON ('-' = stdout).")
        group.add_argument("--from-json", metavar="PLIK", help="Zapisz do .po tłumaczenia z pliku JSON.")
        group.add_argument("--prune", action="store_true", help="Usuń nakładki obecne już w katalogach.")

    def handle(self, *args, **options):
        known = catalogs.review_languages()
        languages = options["language"] or known
        unknown = sorted(set(languages) - set(known))
        if unknown:
            raise CommandError(f"Nieznane języki: {', '.join(unknown)}.")

        if options["prune"]:
            removed = export.prune(languages)
            self.stdout.write(f"Usunięte nakładki (już w katalogach): {removed}.")
            return

        if options["to_json"]:
            content = export.dump(export.collect(languages))
            if options["to_json"] == "-":
                sys.stdout.write(content)
            else:
                Path(options["to_json"]).write_text(content, encoding="utf-8")
                self.stdout.write(f"Zapisano {options['to_json']}.")
            return

        if options["from_json"]:
            try:
                items = export.load(Path(options["from_json"]).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CommandError(str(exc)) from exc
            items = [item for item in items if item["language"] in languages]
        else:
            items = export.collect(languages)

        reports = export.write(items, dry_run=options["dry_run"])
        prefix = "[próba] " if options["dry_run"] else ""
        for language, report in sorted(reports.items()):
            self.stdout.write(
                f"{prefix}{language}: wpisy {report.entries}, pliki: {', '.join(report.files) or '—'}"
            )
            for msgid in report.stale:
                self.stdout.write(f"  nieaktualny (brak w katalogach): {msgid!r}")
            for msgid in report.invalid:
                self.stdout.write(f"  odrzucony (nie przechodzi walidacji): {msgid!r}")
