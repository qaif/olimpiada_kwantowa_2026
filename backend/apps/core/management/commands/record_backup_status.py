"""``manage.py record_backup_status`` – meldunek skryptu kopii zapasowych do aplikacji.

Woła ją ``scripts/backup.sh`` (``--ok``) i ``scripts/backup_verify.sh`` (``--verified``
albo ``--failed``). Jest to jedyna droga, którą wynik pracy crona hosta trafia do
``/status.json`` i do watchdoga alertów – uzasadnienie tego podziału stoi w ``apps.core.backup``.

Dlaczego komenda zarządzająca, a nie zapis wprost do Redisa z poziomu skryptu: adres Redisa,
numer bazy i format znacznika są szczegółem aplikacji, a nie skryptu powłoki. Skrypt, który pisze
do cache'u wprost, przestaje działać po cichu przy pierwszej zmianie ``REDIS_URL`` – i nikt tego
nie zauważa, bo brak meldunku wygląda dokładnie tak samo, jak brak kopii.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.backup import MAX_BACKUP_AGE_HOURS, MAX_VERIFY_AGE_DAYS, record, state


class Command(BaseCommand):
    help = "Zapisuje wynik kopii zapasowej (--ok / --verified / --failed) albo pokazuje stan (--show)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--ok", action="store_true", help="kopia zapasowa powstała i została wysłana")
        parser.add_argument(
            "--verified",
            action="store_true",
            help="kopię odtworzono do tymczasowej bazy i policzono w niej wiersze",
        )
        parser.add_argument(
            "--failed",
            action="store_true",
            help="przebieg się nie powiódł – zapisuje wyłącznie notatkę, żadnego znacznika czasu",
        )
        parser.add_argument("--note", default="", help="jednozdaniowy opis przebiegu (trafia do alertu)")
        parser.add_argument("--show", action="store_true", help="wypisuje stan i nic nie zapisuje")

    def handle(self, *args, **options) -> None:
        wanted = [name for name in ("ok", "verified", "failed", "show") if options.get(name)]
        if not wanted:
            raise CommandError("podaj --ok, --verified, --failed albo --show")
        if options["show"]:
            self._show()
            return

        note = options["note"].strip()
        if options["failed"]:
            # Nieudany przebieg **nie** przesuwa żadnego znacznika czasu i to jest cały sens tej
            # gałęzi: gdyby przesuwał, awaria kopii wyglądałaby w /status.json jak kopia świeża.
            # Zostaje po nim wyłącznie notatka, którą watchdog dołącza do treści alertu.
            record(note=note or "przebieg zakończony błędem")
            self.stdout.write(self.style.ERROR("Zapisano notatkę o nieudanym przebiegu."))
            return

        record(ok=options["ok"], verified=options["verified"], note=note)
        labels = (("kopia", options["ok"]), ("test odtwarzania", options["verified"]))
        what = " i ".join(label for label, flag in labels if flag)
        when = timezone.localtime().strftime("%Y-%m-%d %H:%M")
        self.stdout.write(self.style.SUCCESS(f"Zapisano meldunek: {what} ({when})."))

    def _show(self) -> None:
        """Pełne znaczniki czasu dla dyżurnego. ``/status.json`` oddaje wyłącznie dwie wartości
        logiczne (jest publiczny), więc to jest jedyne miejsce, w którym widać konkretne daty."""
        current = state()
        rows = (
            ("ostatnia kopia", current.last_ok, current.backup_fresh, f"{MAX_BACKUP_AGE_HOURS} h"),
            (
                "ostatni test odtwarzania",
                current.last_verified,
                current.verify_fresh,
                f"{MAX_VERIFY_AGE_DAYS} dni",
            ),
        )
        for label, moment, fresh, window in rows:
            when = timezone.localtime(moment).strftime("%Y-%m-%d %H:%M") if moment else "brak meldunku"
            verdict = "ok" if fresh else f"POZA PROGIEM ({window})"
            style = self.style.SUCCESS if fresh else self.style.ERROR
            self.stdout.write(style(f"{label:26} {when:20} {verdict}"))
        if current.note:
            self.stdout.write(f"{'notatka':26} {current.note}")
