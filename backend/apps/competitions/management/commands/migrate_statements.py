"""Przeniesienie ``Problem.statement_pdf`` ze starego, wspólnego storage na ``private_media``.

Do T-09 treści zadań leżały tam, gdzie wszystkie media – w ``MEDIA_ROOT`` (produkcyjnie: w tym
samym miejscu, które po wprowadzeniu Wagtaila stało się **publicznym** bucketem ``public-media``).
Pole ma dziś jawny storage ``private_media``, ale sama zmiana ustawień nie rusza plików już
zapisanych: baza trzyma wyłącznie nazwę obiektu, a nazwa w obu storage'ach wygląda tak samo.
Ta komenda przenosi treść, żeby nazwa faktycznie wskazywała na prywatny storage.

Komenda jest **idempotentna**: obiekt istniejący już w ``private_media`` jest pomijany, więc
ponowne uruchomienie (po nieudanym deployu, na drugim węźle, w cronie) nic nie psuje. Plik ze
źródła nie jest kasowany – usunięcie treści zadania musi być decyzją człowieka, a nie skutkiem
ubocznym migracji; ``--delete-source`` zostawiamy świadomie poza zakresem.
"""

from __future__ import annotations

from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.core.management.base import BaseCommand

from apps.competitions.models import Problem
from apps.competitions.storage import private_media_storage


class Command(BaseCommand):
    help = "Przenosi pliki Problem.statement_pdf ze starego storage mediów do 'private_media'."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Tylko wypisz, co zostałoby przeniesione – bez zapisu.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        # Źródło to zawsze lokalne ``MEDIA_ROOT``: taki był stary storage. Celowo nie sięgamy po
        # alias ``default``, bo ten jest już przestawiony na publiczny bucket Wagtaila i szukanie
        # w nim starych treści zadań udawałoby, że one tam są.
        source = FileSystemStorage()
        target = private_media_storage()

        moved = skipped = missing = 0
        for problem in Problem.objects.exclude(statement_pdf="").order_by("pk").iterator():
            name = problem.statement_pdf.name
            if target.exists(name):
                skipped += 1
                continue
            if not source.exists(name):
                # Plik nie istnieje ani w źródle, ani w celu – rekord wskazuje na pustkę.
                # To nie jest błąd tej komendy, ale musi być widoczne w wyjściu.
                missing += 1
                self.stderr.write(f"BRAK PLIKU: zadanie {problem.pk} → {name}")
                continue
            if dry_run:
                moved += 1
                self.stdout.write(f"[dry-run] {problem.pk}: {name}")
                continue
            with source.open(name) as handle:
                saved = target.save(name, File(handle, name=name))
            if saved != name:
                # Storage z ``file_overwrite=False`` może dopisać sufiks do nazwy. Wtedy baza
                # musi wskazać nową nazwę, inaczej rekord dalej pokazuje na stary storage.
                # ``update`` zamiast ``save()``: żaden sygnał ani ``full_clean`` nie ma tu prawa
                # zadziałać, przenosimy plik, a nie edytujemy zadanie.
                Problem.objects.filter(pk=problem.pk).update(statement_pdf=saved)
            moved += 1
            self.stdout.write(f"{problem.pk}: {name} → {saved}")

        summary = f"przeniesione: {moved}, pominięte (już w private_media): {skipped}, bez pliku: {missing}"
        self.stdout.write(self.style.SUCCESS(f"[dry-run] {summary}" if dry_run else summary))
