"""``manage.py add_problem_attachment`` – wgranie załączników zadania z powłoki (PUB-02).

Przykład (produkcja, Etap I, zadanie D2 = numer 2 w etapie o identyfikatorze 7)::

    python manage.py add_problem_attachment --competition kwantowa --stage 7 --number 2 \\
        /tmp/jony2.npy /tmp/jony3.npy /tmp/jony4.npy

``--stage`` przyjmuje identyfikator etapu (dowolnej edycji konkursu) albo jego nazwę („Etap I”)
lub rodzaj (``ELIM``) w bieżącej edycji. ``--description`` dopisuje ten sam opis każdemu plikowi.

**Idempotencja** po (zadanie, nazwa pliku, SHA-256): ten sam plik drugi raz – „bez zmian”. Ta sama
nazwa z inną treścią – błąd, chyba że podano ``--replace`` (podmiana treści, ślad w audycie).
Komenda nie sprawdza roli (operator powłoki), limit rozmiaru i biała lista rozszerzeń obowiązują.
"""

from __future__ import annotations

from pathlib import Path

from django.core.files.base import File
from django.core.management.base import BaseCommand, CommandError

from apps.competitions import attachments as service
from apps.competitions.models import Problem, ProblemAttachment, Stage
from apps.core.api import DomainError


class Command(BaseCommand):
    help = "Dodaje pliki jako załączniki zadania (idempotentnie po nazwie i SHA-256)."

    def add_arguments(self, parser):
        parser.add_argument("--competition", required=True, help="Slug konkursu, np. „kwantowa”.")
        parser.add_argument("--stage", required=True, help="Identyfikator etapu albo nazwa/rodzaj etapu.")
        parser.add_argument("--number", required=True, type=int, help="Numer zadania w etapie.")
        parser.add_argument("--description", default="", help="Opis (opcjonalnie, ten sam dla plików).")
        parser.add_argument(
            "--replace", action="store_true", help="Podmień treść załącznika o tej samej nazwie."
        )
        parser.add_argument("files", nargs="+", help="Ścieżki plików.")

    def _stage(self, competition, value: str) -> Stage:
        value = value.strip()
        stages = Stage.objects.for_competition(competition).select_related("edition")
        if value.isdigit():
            stage = stages.filter(pk=int(value)).first()
            if stage is None:
                raise CommandError(f"Nie ma etapu {value} w konkursie {competition.slug!r}.")
            return stage
        current = [stage for stage in stages.filter(edition__is_current=True)]
        folded = value.casefold()
        matches = [stage for stage in current if stage.display_name.casefold() == folded] or [
            stage for stage in current if stage.kind.casefold() == folded
        ]
        if len(matches) != 1:
            available = ", ".join(f"{stage.pk}: {stage.display_name} ({stage.kind})" for stage in current)
            raise CommandError(
                f"Niejednoznaczne albo nieznane wskazanie etapu {value!r}. Etapy: {available}."
            )
        return matches[0]

    def handle(self, *args, **options):
        from apps.tenancy.context import competition_context
        from apps.tenancy.models import Competition

        competition = Competition.objects.filter(slug=options["competition"]).first()
        if competition is None:
            raise CommandError(f"Nie ma konkursu o identyfikatorze {options['competition']!r}.")
        paths = [Path(item) for item in options["files"]]
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            raise CommandError(f"Nie ma plików: {', '.join(missing)}.")
        with competition_context(competition):
            stage = self._stage(competition, options["stage"])
            problem = Problem.objects.filter(stage=stage, number=options["number"]).first()
            if problem is None:
                raise CommandError(f"Etap {stage.display_name} nie ma zadania nr {options['number']}.")
            for path in paths:
                self._one(problem, path, competition, options)

    def _one(self, problem: Problem, path: Path, competition, options) -> None:
        try:
            name = service.clean_filename(path.name)
            service.validate_extension(name)
            with path.open("rb") as handle:
                uploaded = File(handle, name=name)
                size, sha256 = service.file_digest(uploaded)
                existing = ProblemAttachment.objects.filter(problem=problem, filename=name).first()
                if existing is not None and existing.sha256 == sha256:
                    self.stdout.write(f"bez zmian: {name} ({size} B, sha256 {sha256[:12]}…)")
                    return
                if existing is not None:
                    if not options["replace"]:
                        raise CommandError(
                            f"Zadanie ma już załącznik {name} z inną treścią – użyj --replace, aby podmienić."
                        )
                    service.replace_attachment_file(
                        existing, uploaded, actor=None, competition=competition, system=True
                    )
                    self.stdout.write(
                        self.style.SUCCESS(f"podmieniono: {name} ({size} B, sha256 {sha256[:12]}…)")
                    )
                    return
                attachment = service.add_attachment(
                    problem,
                    uploaded,
                    actor=None,
                    competition=competition,
                    description=options["description"],
                    filename=name,
                    system=True,
                )
        except DomainError as exc:
            raise CommandError(f"{path.name}: {exc.detail}") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"dodano: {attachment.filename} ({attachment.size} B, sha256 {attachment.sha256[:12]}…) "
                f"– zadanie {problem.number}, id załącznika {attachment.pk}"
            )
        )
