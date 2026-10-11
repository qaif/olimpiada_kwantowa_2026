"""``manage.py export_translations`` – zatwierdzone poprawki tłumaczy do katalogów ``.po``.

Tryby (L10N-01 § 7, opis kroków w ``apps.translation_review.export``):

- bez opcji – nakładki z bazy → ``msgstr`` w katalogach tego checkoutu (``--dry-run`` = tylko raport),
- ``--to-json PLIK`` – zrzut nakładek do pliku (``-`` = standardowe wyjście); na produkcji, gdzie
  kontener nie ma gita, a pliki obrazu i tak zniknęłyby przy kolejnym wdrożeniu,
- ``--from-json PLIK`` – to samo co tryb domyślny, ale z pliku zamiast z bazy (checkout dewelopera),
- ``--prune`` – po wdrożeniu: usuwa z bazy nakładki, których tekst jest już w **skompilowanym**
  katalogu; napisy, których już nie ma, tylko wypisuje (``--prune-stale`` – usuwa i je).

**Zapis do plików wymaga checkoutu dewelopera** (``DEBUG`` i katalog ``.git`` obok ``backend``),
chyba że ``--force``. W kontenerze produkcyjnym zapis trafiłby do warstwy obrazu: zniknąłby przy
następnym wdrożeniu, a do tego czasu ``.po`` różniłby się od ``.mo`` – czyli od tego, co widzi gettext.
"""

from __future__ import annotations

import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.translation_review import catalogs, export


def in_checkout() -> bool:
    """``DEBUG`` i repozytorium git w katalogu ``backend`` albo nad nim (``BASE_DIR/../.git``)."""
    base = Path(settings.BASE_DIR).resolve()
    return bool(settings.DEBUG) and any((folder / ".git").exists() for folder in (base, base.parent))


class Command(BaseCommand):
    help = "Przenosi zatwierdzone poprawki tłumaczy z bazy do plików .po (albo do/z pliku JSON)."

    def add_arguments(self, parser):
        parser.add_argument("--language", action="append", help="Tylko ten język (można powtórzyć).")
        parser.add_argument("--dry-run", action="store_true", help="Pokaż, co by się zmieniło, bez zapisu.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Zapisz do .po także poza checkoutem dewelopera (DEBUG + .git).",
        )
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--to-json", metavar="PLIK", help="Zapisz nakładki do pliku JSON ('-' = stdout).")
        group.add_argument("--from-json", metavar="PLIK", help="Zapisz do .po tłumaczenia z pliku JSON.")
        group.add_argument(
            "--prune", action="store_true", help="Usuń nakładki obecne już w skompilowanych katalogach."
        )
        group.add_argument(
            "--prune-stale",
            action="store_true",
            help="Jak --prune, a do tego usuń nakładki napisów, których nie ma już w katalogach.",
        )

    def handle(self, *args, **options):
        known = catalogs.review_languages()
        languages = options["language"] or known
        unknown = sorted(set(languages) - set(known))
        if unknown:
            raise CommandError(f"Nieznane języki: {', '.join(unknown)}.")

        if options["prune"] or options["prune_stale"]:
            report = export.prune(languages, delete_stale=options["prune_stale"])
            self.stdout.write(f"Usunięte nakładki: {report.removed}, zostają: {report.kept}.")
            for line in report.stale:
                action = "usunięta" if options["prune_stale"] else "zostaje (--prune-stale usuwa)"
                self.stdout.write(f"  napisu nie ma już w katalogach – {action}: {line!r}")
            return

        if options["to_json"]:
            content = export.dump(export.collect(languages))
            if options["to_json"] == "-":
                sys.stdout.write(content)
            else:
                Path(options["to_json"]).write_text(content, encoding="utf-8")
                self.stdout.write(f"Zapisano {options['to_json']}.")
            return

        if not options["dry_run"] and not options["force"] and not in_checkout():
            raise CommandError(
                "Zapis do .po wyłącznie w checkoucie dewelopera (DEBUG i .git obok backend). Na produkcji "
                "użyj --to-json, a plik wczytaj u siebie przez --from-json. Świadomie: --force."
            )

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
            for msgid in report.conflicts:
                self.stdout.write(
                    f"  konflikt (katalog zmienił się od decyzji – przejrzyj w panelu): {msgid!r}"
                )
