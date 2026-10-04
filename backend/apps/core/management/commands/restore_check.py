"""``manage.py restore_check`` – conocny test odtwarzania kopii (OPS-01), strona aplikacji.

Woła ją wyłącznie ``scripts/backup_verify.sh`` (poza ``show``, które jest dla dyżurnego):

- ``live-counts`` – w żywym ``web``: liczności tabel kluczowych jako JSON na stdout,
- ``verify`` – w jednorazowym kontenerze podłączonym **tylko** do bazy tymczasowej: na stdin
  pierwsza linia to nagłówek JSON od skryptu, kolejne – lista obiektów z paczki plików
  (``tar -tf``); na stdout jeden dokument JSON wyniku, na stderr podsumowanie dla logu crona,
- ``record`` – w żywym ``web``: wynik ze stdin (albo ``--failure KROK --detail …`` dla przebiegu
  przerwanego przed sprawdzeniami) do cache'u, audytu i – przy porażce – do listu alarmowego,
- ``show`` – ostatni wynik ze szczegółami.

Kod wyjścia ``verify``: 0 przy wyniku ``ok``, 1 przy ``failed`` (i przy odmowie bramki – wtedy
stdout jest pusty, a skrypt melduje przerwanie). ``record`` kończy się zerem, gdy **zapisał**
wynik – także nieudany: o kodzie wyjścia skryptu decyduje wynik, a nie to, czy meldunek doszedł.

``requires_system_checks = []``: kontener ``verify`` nie ma sieci poza bazą tymczasową, a część
sprawdzeń systemowych dotyka ustawień infrastruktury – test kopii nie może się wywrócić na
ostrzeżeniu, które dotyczy żywej instalacji, a nie kopii.
"""

from __future__ import annotations

import json
import sys

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core import restore_check


class Command(BaseCommand):
    help = "Test odtwarzania kopii: live-counts | verify | record | show (docs/OPERACJE.md § 43)."
    requires_system_checks: list = []

    def add_arguments(self, parser) -> None:
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("live-counts", help="liczności tabel kluczowych bazy żywej (JSON)")
        sub.add_parser("verify", help="sprawdzenia odtworzonej bazy; nagłówek i lista plików na stdin")
        rec = sub.add_parser("record", help="zapis wyniku (JSON na stdin albo --failure)")
        rec.add_argument("--failure", default="", help="nazwa kroku, który przerwał przebieg")
        rec.add_argument("--detail", default="", help="jednozdaniowy opis przerwania")
        rec.add_argument("--backup", default="", help="nazwa sprawdzanej paczki")
        rec.add_argument("--no-alert", action="store_true", help="bez natychmiastowego listu (test lokalny)")
        sub.add_parser("show", help="ostatni wynik")

    def handle(self, *args, **options) -> None:
        action = options["action"]
        if action == "live-counts":
            self.stdout.write(json.dumps(restore_check.live_counts()))
        elif action == "verify":
            self._verify()
        elif action == "record":
            self._record(options)
        else:
            self._show()

    def _verify(self) -> None:
        try:
            restore_check.guard_isolated()
        except restore_check.NotIsolated as exc:
            raise CommandError(f"odmowa: baza docelowa może być bazą żywą ({exc})") from exc
        restore_check._enter_read_only()
        stream = sys.stdin
        first = stream.readline()
        try:
            header = json.loads(first) if first.strip() else {}
        except ValueError as exc:
            raise CommandError("pierwsza linia stdin nie jest nagłówkiem JSON") from exc
        listing = restore_check.normalize_listing(stream) if header.get("files_status") == "ok" else None
        result = restore_check.verify(header, listing)
        self.stdout.write(json.dumps(result, ensure_ascii=False))
        for item in result["checks"]:
            sys.stderr.write(f"    {item['status']:5} {item['name']:16} {item.get('detail', '')}\n")
        if result["status"] != restore_check.STATUS_OK:
            sys.exit(1)

    def _record(self, options) -> None:
        if options["failure"]:
            result = restore_check.failure_result(
                options["failure"],
                options["detail"] or "przebieg przerwany",
                backup={"name": options["backup"]} if options["backup"] else {},
            )
        else:
            raw = sys.stdin.read()
            try:
                result = json.loads(raw)
            except ValueError as exc:
                raise CommandError("stdin nie jest dokumentem JSON wyniku") from exc
            if not isinstance(result, dict) or "status" not in result:
                raise CommandError("dokument wyniku bez pola status")
        restore_check.record(result, send_alert=not options["no_alert"])
        text = restore_check.summary(result)
        if result.get("status") == restore_check.STATUS_OK:
            self.stdout.write(self.style.SUCCESS(f"Zapisano: {text}"))
            return
        self.stdout.write(self.style.ERROR(f"Zapisano: {text}"))

    def _show(self) -> None:
        result = restore_check.last_result()
        current = restore_check.level(result)
        if not result:
            self.stdout.write(self.style.ERROR("brak wyniku testu odtwarzania (poziom: unknown)"))
            return
        style = self.style.SUCCESS if current == restore_check.LEVEL_OK else self.style.ERROR
        finished = restore_check.parse_docker_time(result.get("finished_at"))
        when = timezone.localtime(finished).strftime("%Y-%m-%d %H:%M") if finished else "?"
        backup = result.get("backup") or {}
        self.stdout.write(style(f"poziom: {current}   wynik: {result.get('status')}   koniec: {when}"))
        self.stdout.write(f"kopia: {backup.get('name', '?')}   wiek: {backup.get('age_hours', '?')} h")
        timings = result.get("timings") or {}
        if timings:
            self.stdout.write("czasy: " + ", ".join(f"{key}={value}" for key, value in timings.items()))
        for item in result.get("checks") or []:
            self.stdout.write(
                f"  {item.get('status', '?'):5} {item.get('name', '?'):16} {item.get('detail', '')}"
            )
