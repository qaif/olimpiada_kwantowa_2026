"""``manage.py import_stage_rules`` – wersja „Zasad Organizacji Zawodów” etapu z pliku (ZOZ-01).

Treść ZOZ jest dokumentem organizatora, a nie częścią kodu: nie leży w repozytorium, tylko trafia
do bazy tą komendą (albo z panelu koordynatora – ten sam serwis ``apps.competitions.stage_rules``,
to samo oczyszczanie HTML-a, ten sam audyt).

Przykład (produkcja, Etap I Olimpiady Kwantowej)::

    python manage.py import_stage_rules --competition kwantowa --stage "Etap I" \\
        --version 1.0 --date 2026-11-12 --html /tmp/zoz-etap-1-v1.0.html \\
        --pdf /tmp/ZOZ_Etap_I_v1.0.pdf --publish

``--stage`` przyjmuje identyfikator etapu, jego nazwę („Etap I”) albo rodzaj (``ELIM``) – zawsze
w obrębie bieżącej edycji wskazanego konkursu (albo ``--edition``). Niejednoznaczne wskazanie
kończy się błędem, a nie zgadywaniem.

**Idempotencja.** Wersja jest rozpoznawana po parze (etap, numer wersji). Ponowne uruchomienie
z tymi samymi plikami nie zmienia niczego (ani wiersza, ani audytu). Inna treść dla **projektu**
– zastępuje go. Inna treść dla wersji **opublikowanej** – błąd, chyba że operator poda jawnie
``--replace-published`` (poprawka transkrypcji bez zmiany numeru wersji; ślad w audycie).
Plik PDF jest porównywany po skrócie SHA-256 – ten sam plik nie jest wgrywany drugi raz.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.competitions.models import Edition, Stage
from apps.competitions.stage_rules import (
    DEFAULT_TITLE,
    StageRules,
    StageRulesStatus,
    create_rules,
    publish_rules,
    sanitize_html,
    update_rules,
)
from apps.core.api import DomainError


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Command(BaseCommand):
    help = "Zakłada albo aktualizuje wersję Zasad Organizacji Zawodów etapu z pliku HTML (i PDF)."

    def create_parser(self, prog_name, subcommand, **kwargs):
        """``--version`` jest tu numerem wersji **dokumentu**, a nie wersją Django.

        ``BaseCommand`` rezerwuje ``--version`` na wypisanie wersji frameworka; ``resolve`` pozwala
        tej komendzie przejąć nazwę – tak brzmi polecenie w instrukcji wdrożenia (OPERACJE).
        """
        kwargs.setdefault("conflict_handler", "resolve")
        return super().create_parser(prog_name, subcommand, **kwargs)

    def add_arguments(self, parser):
        parser.add_argument("--competition", required=True, help="Slug konkursu, np. „kwantowa”.")
        parser.add_argument(
            "--edition", default=None, help="Oznaczenie edycji (year_label); domyślnie edycja bieżąca."
        )
        parser.add_argument(
            "--stage", required=True, help="Identyfikator, nazwa („Etap I”) albo rodzaj (ELIM) etapu."
        )
        parser.add_argument("--version", dest="rules_version", required=True, help="Numer wersji, np. 1.0.")
        parser.add_argument("--date", required=True, help="Data dokumentu, RRRR-MM-DD.")
        parser.add_argument("--html", required=True, help="Plik HTML z treścią dokumentu (UTF-8).")
        parser.add_argument("--pdf", default=None, help="Plik PDF do pobrania (opcjonalnie).")
        parser.add_argument("--title", default=DEFAULT_TITLE, help=f"Tytuł (domyślnie „{DEFAULT_TITLE}”).")
        parser.add_argument("--publish", action="store_true", help="Opublikuj wersję (jeśli nie jest).")
        parser.add_argument(
            "--replace-published",
            action="store_true",
            help="Pozwól zmienić treść/PDF wersji już opublikowanej (poprawka bez nowego numeru).",
        )

    # --- rozstrzyganie argumentów ----------------------------------------------------------------

    def _competition(self, slug: str):
        from apps.tenancy.models import Competition

        competition = Competition.objects.filter(slug=slug).first()
        if competition is None:
            raise CommandError(f"Nie ma konkursu o identyfikatorze {slug!r}.")
        return competition

    def _edition(self, competition, label: str | None) -> Edition:
        editions = Edition.objects.filter(competition=competition)
        edition = (
            editions.filter(year_label=label).first() if label else editions.filter(is_current=True).first()
        )
        if edition is None:
            raise CommandError(
                f"Nie ma edycji {label!r} w tym konkursie." if label else "Konkurs nie ma bieżącej edycji."
            )
        return edition

    def _stage(self, edition: Edition, value: str) -> Stage:
        stages = list(edition.stages.all())
        value = value.strip()
        if value.isdigit():
            matches = [stage for stage in stages if stage.pk == int(value)]
        else:
            folded = value.casefold()
            matches = [stage for stage in stages if stage.display_name.casefold() == folded]
            if not matches:
                matches = [stage for stage in stages if stage.kind.casefold() == folded]
        if len(matches) != 1:
            available = ", ".join(f"{stage.pk}: {stage.display_name} ({stage.kind})" for stage in stages)
            what = "Niejednoznaczne" if matches else "Nieznane"
            raise CommandError(f"{what} wskazanie etapu {value!r}. Etapy edycji: {available or 'brak'}.")
        return matches[0]

    @staticmethod
    def _read(path: str | None, *, binary: bool = False):
        if path is None:
            return None
        file = Path(path)
        if not file.is_file():
            raise CommandError(f"Nie ma pliku {path}.")
        data = file.read_bytes()
        if binary:
            return data
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CommandError(f"Plik {path} nie jest zapisany w UTF-8.") from exc

    # --- główna część ----------------------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        competition = self._competition(options["competition"])
        edition = self._edition(competition, options.get("edition"))
        stage = self._stage(edition, options["stage"])
        version = options["rules_version"].strip()
        try:
            document_date = date.fromisoformat(options["date"])
        except ValueError as exc:
            raise CommandError("Data dokumentu w formacie RRRR-MM-DD.") from exc
        html = self._read(options["html"])
        pdf_bytes = self._read(options.get("pdf"), binary=True)
        pdf_name = Path(options["pdf"]).name if options.get("pdf") else None
        title = (options.get("title") or DEFAULT_TITLE).strip()

        from apps.tenancy.context import competition_context

        with competition_context(competition):
            rules = StageRules.objects.filter(stage=stage, version=version).first()
            try:
                if rules is None:
                    rules = create_rules(
                        stage,
                        None,
                        version=version,
                        document_date=document_date,
                        body_html=html,
                        title=title,
                        pdf=ContentFile(pdf_bytes, name=pdf_name) if pdf_bytes is not None else None,
                    )
                    self.stdout.write(f"Utworzono wersję {version} ({stage.display_name}).")
                else:
                    rules = self._update(rules, options, document_date, html, title, pdf_bytes, pdf_name)
                if options["publish"] and rules.status != StageRulesStatus.PUBLISHED:
                    rules = publish_rules(rules, None)
                    self.stdout.write(f"Opublikowano wersję {version}.")
            except DomainError as exc:
                raise CommandError(str(exc.detail)) from exc

        from django.urls import reverse

        self.stdout.write(
            self.style.SUCCESS(
                f"{rules.title} – {stage.display_name}, wersja {rules.version}: "
                f"{rules.get_status_display()}. "
                f"Adres: {reverse('web:stage-rules', args=[stage.pk])}"
            )
        )

    def _update(self, rules, options, document_date, html, title, pdf_bytes, pdf_name):
        same_body = sanitize_html(html) == rules.body_html
        same_pdf = pdf_bytes is None or (bool(rules.pdf) and self._stored_digest(rules) == _sha256(pdf_bytes))
        same_meta = rules.document_date == document_date and rules.title == title
        if same_body and same_pdf and same_meta:
            self.stdout.write(f"Wersja {rules.version} jest aktualna – bez zmian.")
            return rules
        if rules.status == StageRulesStatus.PUBLISHED and not options["replace_published"]:
            raise CommandError(
                f"Wersja {rules.version} jest opublikowana, a pliki różnią się od zapisanej treści. "
                "Nadaj nową wersję albo podaj --replace-published (poprawka bez zmiany numeru)."
            )
        rules, changed = update_rules(
            rules,
            None,
            document_date=document_date,
            body_html=html,
            title=title,
            pdf=None if same_pdf else ContentFile(pdf_bytes, name=pdf_name),
            allow_published=True,
        )
        if changed:
            self.stdout.write(f"Zaktualizowano wersję {rules.version}.")
        return rules

    @staticmethod
    def _stored_digest(rules) -> str | None:
        try:
            with rules.pdf.open("rb") as stored:
                return _sha256(stored.read())
        except FileNotFoundError, OSError:
            return None
