"""Komenda ``import_cms_bundle`` – import paczki treści z Wagtaila do witryny konkursu.

``manage.py import_cms_bundle (PATH | - | --from-api) [--competition SLUG | --all [--skip SLUG …]]
[--replace | --if-empty] [--dry-run] [--user LOGIN]``

Wczytuje paczkę treści z Wagtaila (``olimpiada-cms-bundle`` v1 i v2, § 5.3 docs/tasks/DJ-01.md,
§ 4.4 docs/tasks/DJ-02.md) – z pliku, ze standardowego wejścia (``-``) albo wprost z wewnętrznego
API aplikacji głównej (``--from-api``, ``GET /internal/djcms/v2/c/<slug>/export``). Logika:
``apps.importer.services``.

Witryna docelowa (DJ-02: witryna per konkurs, rejestr ``apps.sites`` – ``manage.py sync_competitions``):

- ``--competition SLUG`` – witryna tego konkursu; paczka innego konkursu jest odrzucana,
- ``--all`` (tylko z ``--from-api``) – każdy **aktywny** konkurs z rejestru po kolei, każdy z własną
  paczką i we własnej transakcji; ``--skip SLUG`` (można powtarzać) pomija konkurs, którego redakcja
  pracuje już w djcms (D8). Błąd jednego konkursu nie zatrzymuje pozostałych – na końcu kod 1
  i lista konkursów z błędem,
- bez obu: plik/stdin – konkurs paczki (``competition.slug`` v2, ``source.competition_slug`` v1),
  a gdy go nie ma w rejestrze – konkurs domyślny; ``--from-api`` – konkurs domyślny.

Tryby (per witryna):

- bez ``--replace``/``--if-empty``: odmowa, gdy w witrynie jest jakakolwiek strona – import nie
  miesza się z treścią zredagowaną już w django CMS,
- ``--if-empty``: gdy strony są – kod 0 i żadnych zmian, **zanim** paczka zostanie pobrana (tak woła
  go ``deploy.sh`` przy każdym wdrożeniu),
- ``--replace``: w jednej transakcji kasuje strony witryny, jej przekierowania z importu i obrazy
  z jej folderu filera „Konkurs: … / Import z Wagtaila”, potem importuje od nowa – ten sam stan
  przy każdym przebiegu; pozostałe witryny zostają nietknięte,
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
    help = "Importuje paczkę treści z Wagtaila (plik, '-' = stdin, albo --from-api) do witryny konkursu."

    def add_arguments(self, parser):
        parser.add_argument("source", nargs="?", help="Ścieżka do paczki ZIP albo '-' (standardowe wejście).")
        parser.add_argument(
            "--from-api", action="store_true", help="Pobierz paczkę z API aplikacji głównej (GET export)."
        )
        target = parser.add_mutually_exclusive_group()
        target.add_argument("--competition", metavar="SLUG", help="Witryna tego konkursu (rejestr witryn).")
        target.add_argument(
            "--all", action="store_true", help="Każdy aktywny konkurs z rejestru (wymaga --from-api)."
        )
        parser.add_argument(
            "--skip",
            action="append",
            default=[],
            metavar="SLUG",
            help="Z --all: pomiń ten konkurs (można powtarzać).",
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--replace",
            action="store_true",
            help="Skasuj strony witryny, jej przekierowania i obrazy importu, zaimportuj od nowa.",
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
        if options["all"] and not from_api:
            raise CommandError("--all importuje każdy konkurs z jego własnej paczki – wymaga --from-api.")
        if options["skip"] and not options["all"]:
            raise CommandError("--skip działa wyłącznie z --all.")
        if options["all"]:
            self._import_all(options)
            return
        competition = services.competition_site(options["competition"]) if options["competition"] else None
        if competition is None and from_api:
            competition = services.competition_of(services.target_site())
        if self._skip_or_refuse(competition, options):
            return
        user = self._user(options.get("user"))
        report = self._import_one(competition, source, user, options)
        if report is not None:
            self._write_report(report, options)

    # --- przebiegi ----------------------------------------------------------------------------------

    def _import_all(self, options) -> None:
        from apps.sites.models import CompetitionSite

        active = list(
            CompetitionSite.objects.filter(is_active=True)
            .select_related("site")
            .order_by("-is_default", "slug")
        )
        if not active:
            raise CommandError("Rejestr witryn jest pusty – najpierw manage.py sync_competitions.")
        skip = set(options["skip"])
        unknown = skip - {competition.slug for competition in active}
        if unknown:
            raise CommandError(
                f"--skip: konkursów {', '.join(sorted(unknown))} nie ma wśród aktywnych w rejestrze."
            )
        user = self._user(options.get("user"))
        failures: list[str] = []
        for competition in active:
            self.stdout.write(f"== Konkurs „{competition.slug}” ({competition.name}) ==")
            if competition.slug in skip:
                self.stdout.write("--skip: pominięty.")
                continue
            try:
                if self._skip_or_refuse(competition, options):
                    continue
                report = self._import_one(competition, None, user, options)
            except CommandError as exc:
                self.stderr.write(f"{competition.slug}: {exc}")
                failures.append(competition.slug)
                continue
            if report is not None:
                self._write_report(report, options)
        if failures:
            raise CommandError(
                f"Import nie powiódł się dla: {', '.join(failures)} (pozostałe zaimportowane)."
            )

    def _skip_or_refuse(self, competition, options) -> bool:
        """``--if-empty`` przy stronach w witrynie → ``True`` (pominąć, **zanim** paczka zostanie
        pobrana); bez trybu przy stronach → odmowa. Witryna nieznana przed paczką (plik) – ``False``."""
        if competition is None:
            return False
        has_pages = services.site_has_pages(competition.site)
        if options["if_empty"] and has_pages:
            self.stdout.write("W witrynie są już strony – --if-empty: import pominięty.")
            return True
        if has_pages and not options["replace"]:
            raise CommandError(
                "W witrynie są już strony. Pełny re-import: --replace (kasuje strony i obrazy importu); "
                "tylko pusta witryna: --if-empty."
            )
        return False

    def _import_one(self, competition, source, user, options) -> services.ImportReport | None:
        """Jedna paczka do jednej witryny; ``None`` – ``--if-empty`` rozstrzygnięte po otwarciu paczki.

        Plik podany ścieżką czytamy wprost – bez kopii i bez sprawdzania miejsca. Paczka musi zostać
        otwarta do końca importu: obrazy idą z niej strumieniem (``services.import_images``).
        """
        spooled = source in (None, "-")
        with self._spool() if spooled else contextlib.nullcontext() as spool:
            if source is None:
                self._download(spool, competition)
            elif source == "-":
                self._copy_stdin(spool)
            if spooled:
                spool.file.seek(0)
            try:
                bundle = services.open_bundle(spool.file if spooled else source)
                if competition is None:
                    competition = self._competition_of_bundle(bundle)
                    if self._skip_or_refuse(competition, options):
                        return None
                return services.run_import(
                    bundle,
                    user=user,
                    replace=options["replace"],
                    dry_run=options["dry_run"],
                    competition=competition,
                )
            except services.BundleError as exc:
                raise CommandError(f"Paczka odrzucona: {exc}") from None
            except services.ImportRefused as exc:
                raise CommandError(str(exc)) from None

    @staticmethod
    def _competition_of_bundle(bundle):
        """Konkurs paczki z pliku/stdin: jej własny, jeśli jest w rejestrze – inaczej domyślny."""
        from apps.sites.models import CompetitionSite

        slug = bundle.competition_slug
        found = CompetitionSite.objects.filter(slug=slug, is_active=True).first() if slug else None
        return found or services.competition_of(services.target_site())

    def _write_report(self, report: services.ImportReport, options) -> None:
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

    def _download(self, spool, competition=None) -> None:
        from apps.live.client import MainApi, MainApiError

        slug = competition.slug if competition is not None else None
        try:
            size = MainApi().download_export(
                spool, limit_bytes=services.MAX_UNCOMPRESSED_BYTES, competition=slug
            )
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
