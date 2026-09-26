"""Komenda ``import_cms_bundle`` – import paczki treści z Wagtaila.

``manage.py import_cms_bundle (PATH | - | --from-api) [--replace | --if-empty] [--dry-run] [--user LOGIN]``

Wczytuje paczkę treści z Wagtaila (``olimpiada-cms-bundle`` v1, § 5.3 docs/tasks/DJ-01.md) – z pliku,
ze standardowego wejścia (``-``) albo wprost z wewnętrznego API aplikacji głównej (``--from-api``,
``GET /internal/djcms/v1/export``). Logika: ``apps.importer.services``.

- bez ``--replace``/``--if-empty``: odmowa, gdy w witrynie jest jakakolwiek strona – import nie
  miesza się z treścią zredagowaną już w django CMS,
- ``--if-empty``: gdy strony są – kod 0 i żadnych zmian, **zanim** paczka zostanie pobrana (tak woła
  go ``deploy.sh`` przy każdym wdrożeniu),
- ``--replace``: w jednej transakcji kasuje wszystkie strony witryny i obrazy z folderu filera
  „Import z Wagtaila”, potem importuje od nowa – ten sam stan przy każdym przebiegu,
- ``--dry-run``: pełna walidacja i przebieg importu z raportem, transakcja wycofana,
- ``--user``: autor wersji (``Version.created_by``); domyślnie pierwszy aktywny superuser.

Raport idzie na standardowe wyjście (liczby stron i wtyczek per typ, obrazy nowe/ponownie użyte,
pominięcia, zmiany sanityzatora, czas).
"""

from __future__ import annotations

import sys
import tempfile

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.importer import services


class Command(BaseCommand):
    help = "Importuje paczkę treści z Wagtaila (plik, '-' = stdin, albo --from-api) do django CMS."

    def add_arguments(self, parser):
        parser.add_argument("source", nargs="?", help="Ścieżka do paczki ZIP albo '-' (standardowe wejście).")
        parser.add_argument(
            "--from-api", action="store_true", help="Pobierz paczkę z API aplikacji głównej (GET export)."
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--replace",
            action="store_true",
            help="Skasuj strony witryny i obrazy importu, zaimportuj od nowa.",
        )
        mode.add_argument(
            "--if-empty", action="store_true", help="Nic nie rób (kod 0), gdy w witrynie są już strony."
        )
        parser.add_argument("--dry-run", action="store_true", help="Waliduj i raportuj, bez zapisu.")
        parser.add_argument("--user", help="Login autora wersji (domyślnie pierwszy aktywny superuser).")

    def handle(self, *args, **options):
        source, from_api = options.get("source"), options["from_api"]
        if bool(source) == bool(from_api):
            raise CommandError("Podaj dokładnie jedno źródło paczki: PATH, '-' albo --from-api.")
        if options["if_empty"] and services.site_has_pages():
            self.stdout.write("W witrynie są już strony – --if-empty: import pominięty.")
            return
        if not options["replace"] and not options["if_empty"] and services.site_has_pages():
            raise CommandError(
                "W witrynie są już strony. Pełny re-import: --replace (kasuje strony i obrazy importu); "
                "tylko pusta witryna: --if-empty."
            )
        user = self._user(options.get("user"))

        with tempfile.TemporaryFile() as spool:
            if from_api:
                self._download(spool)
            elif source == "-":
                self._copy_stdin(spool)
            spool.seek(0)
            try:
                bundle = services.open_bundle(spool if source in (None, "-") else source)
                report = services.run_import(
                    bundle, user=user, replace=options["replace"], dry_run=options["dry_run"]
                )
            except services.BundleError as exc:
                raise CommandError(f"Paczka odrzucona: {exc}") from None
            except services.ImportRefused as exc:
                raise CommandError(str(exc)) from None
        for line in report.lines():
            self.stdout.write(line)
        if not options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("import_cms_bundle: gotowe."))

    def _user(self, username: str | None):
        users = get_user_model().objects.filter(is_active=True)
        if username:
            user = users.filter(username=username).first()
            if user is None:
                raise CommandError(f"Brak aktywnego użytkownika {username!r}.")
            return user
        user = users.filter(is_superuser=True).order_by("pk").first()
        if user is None:
            raise CommandError(
                "Brak aktywnego superusera – autora wersji. "
                "Utwórz konto (bootstrap_djcms_admin) albo podaj --user."
            )
        return user

    def _download(self, spool) -> None:
        from apps.live.client import MainApi, MainApiError

        try:
            size = MainApi().download_export(spool, limit_bytes=services.MAX_UNCOMPRESSED_BYTES)
        except MainApiError as exc:
            raise CommandError(f"Nie udało się pobrać paczki z API aplikacji głównej: {exc}") from None
        self.stdout.write(f"Pobrano paczkę z API: {size} B.")

    def _copy_stdin(self, spool) -> None:
        stream = sys.stdin.buffer
        copied = 0
        while chunk := stream.read(64 * 1024):
            copied += len(chunk)
            if copied > services.MAX_UNCOMPRESSED_BYTES:
                raise CommandError(f"Paczka na wejściu większa niż {services.MAX_UNCOMPRESSED_BYTES} B.")
            spool.write(chunk)
