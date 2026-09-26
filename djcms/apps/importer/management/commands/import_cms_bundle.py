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

Paczka z ``--from-api`` albo ze stdin trafia najpierw do pliku tymczasowego (ZIP wymaga pliku, po
którym da się skakać). Ten plik leży w ``MEDIA_ROOT`` (wolumen ``djcms_media``), a nie w ``/tmp``:
kontener produkcyjny ma system plików tylko do odczytu i ``/tmp`` jako tmpfs 64 MB
(docker-compose.yml), a limit paczki to ``services.MAX_UNCOMPRESSED_BYTES`` (500 MB). Paczka
większa niż 64 MB kończyła się w ``/tmp`` błędem „No space left on device” w połowie pobierania –
a tmpfs to RAM, więc nawet mniejsza zajmowała pamięć kontenera (tę samą, z której żyje gunicorn
serwisu dj.). Obniżenie limitu do 64 MB
odrzucone: to limit kontraktu paczki (DJ-01 § 5.3), wspólny dla pliku, stdin i API, a treść
z obrazami w pełnej rozdzielczości potrafi go przekroczyć. Szczegóły: ``Command._spool``
i ``_GuardedSpool``.
"""

from __future__ import annotations

import contextlib
import shutil
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.importer import services

#: Ile miejsca na dysku mediów musi zostać wolne, gdy zapisujemy paczkę. Wolumen ``djcms_media``
#: leży na tym samym dysku co baza (``pg_data``): pobranie, które zapełni dysk do zera, zatrzymałoby
#: zapis WAL-u Postgresa, czyli **cały** serwis, a nie tylko import. 256 MB to kilkanaście segmentów
#: WAL (16 MB) – margines na czas importu, nie miejsce na dane.
SPOOL_FREE_RESERVE_BYTES = 256 * 1024 * 1024
#: Co ile zapisanych bajtów sprawdzać wolne miejsce (``statvfs`` jest tani, ale nie przy każdym
#: kawałku 64 KB). Między sprawdzeniami paczka może zjeść najwyżej tyle – mniej niż zapas.
SPOOL_CHECK_EVERY_BYTES = 8 * 1024 * 1024


class _GuardedSpool:
    """Zapis do pliku tymczasowego, który przerywa import, zanim dysk mediów zejdzie poniżej zapasu.

    Sprawdzenie w trakcie zapisu, a nie „czy zmieści się najgorszy przypadek” przed pobraniem:
    wielkość paczki z API nie jest znana z góry, a wymaganie 500 MB (limit) na paczkę, która ma
    zwykle kilkadziesiąt, blokowałoby import na dysku, na którym zmieściłby się bez trudu.

    Błąd to ``CommandError`` z czytelnym powodem. Gdyby zamiast tego dysk się zapełnił, ``write``
    rzuciłby ``OSError`` (ENOSPC), a ``MainApi.download_export`` zamienia każdy ``OSError`` na
    „connection” – operator szukałby wtedy awarii sieci. ``CommandError`` przez tamten ``except``
    przechodzi bez zmian (nie jest ``OSError``).
    """

    def __init__(self, file, directory: Path):
        self.file = file
        self.directory = directory
        self._unchecked = SPOOL_CHECK_EVERY_BYTES  # pierwszy zapis sprawdza od razu

    def check(self, pending: int = 0) -> None:
        free = shutil.disk_usage(self.directory).free
        if free - pending < SPOOL_FREE_RESERVE_BYTES:
            raise CommandError(
                f"Za mało miejsca na paczkę w {self.directory}: wolne {free // 2**20} MB, a musi zostać "
                f"{SPOOL_FREE_RESERVE_BYTES // 2**20} MB zapasu (ten sam dysk co baza). Zwolnij miejsce "
                "albo podaj paczkę jako plik (PATH) z innego dysku."
            )

    def write(self, data: bytes) -> int:
        self._unchecked += len(data)
        if self._unchecked >= SPOOL_CHECK_EVERY_BYTES:
            self.check(pending=len(data))
            self._unchecked = 0
        return self.file.write(data)


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

        # Plik podany ścieżką czytamy wprost – bez kopii i bez sprawdzania miejsca.
        spooled = source in (None, "-")
        with self._spool() if spooled else contextlib.nullcontext() as spool:
            if from_api:
                self._download(spool)
            elif source == "-":
                self._copy_stdin(spool)
            if spooled:
                spool.file.seek(0)
            try:
                bundle = services.open_bundle(spool.file if spooled else source)
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

    @contextlib.contextmanager
    def _spool(self):
        """Anonimowy plik tymczasowy na wolumenie ``MEDIA_ROOT`` (patrz docstring modułu).

        ``tempfile.TemporaryFile(dir=…)`` na Linuksie otwiera plik z ``O_TMPFILE`` (albo zakłada
        i od razu kasuje nazwę), więc plik **nie ma nazwy** w katalogu mediów: Caddy, który podaje
        ten wolumen pod ``/media/*``, nie ma czego podać, a miejsce wraca do systemu przy zamknięciu
        – także po błędzie importu i po zabiciu procesu (tego ``finally`` by nie załatwił).

        Wolne miejsce: raz przed pobraniem (pełny dysk = odmowa bez łączenia się z API) i w trakcie
        zapisu (``_GuardedSpool``).
        """
        directory = Path(settings.MEDIA_ROOT)
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as file:
            spool = _GuardedSpool(file, directory)
            spool.check()
            yield spool

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
