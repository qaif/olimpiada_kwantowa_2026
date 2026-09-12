"""``manage.py seed_training_problems`` – etap treningowy bieżącej edycji i cztery zadania do niego.

Po co: uczestnik (i sam organizator) musi mieć gdzie przejść **całą** ścieżkę portalu – zgłoszenie
do etapu, pobranie treści, upload rozwiązania, dwie ślepe recenzje, konsensus, wyniki – zanim
zacznie się liczyć punkty. Dopóki jedynym otwartym etapem były zawody, taki przebieg albo nie był
możliwy, albo kończył się śmieciową pracą w etapie, którego wyniki się ogłasza. Etap treningowy
jest piaskownicą: bez terminu, poza kwalifikacją i poza publiczną osią czasu (patrz
``StageKind.TRAINING``).

Skąd biorą się pliki: PDF-y leżą w ``apps/competitions/fixtures/training/`` i są **wersjonowane
razem ze źródłem** (``zadania.md``); składa je ``scripts/build_training_problem_pdfs.py``. Ta
komenda niczego nie generuje – wgrywa gotowe pliki, dzięki czemu ``reportlab`` jest zależnością
dev, a serwer nie musi umieć składać dokumentów. ``odpowiedzi.pdf`` zostaje **poza** bazą: to
materiał dla komisji, a zadanie z wgranym kluczem odpowiedzi przestaje być zadaniem.

Idempotencja jest tu warunkiem użyteczności, nie ozdobą: organizator uruchamia komendę ręcznie,
a potem bywa, że drugi raz – po poprawce w treści. Dlatego etap rozpoznajemy po parze (edycja,
rodzaj), zadania po numerze, a **plik podmieniamy tylko wtedy, gdy bajty się różnią**. Bez tego
porównania każdy przebieg zapisywałby nową kopię PDF-a pod nową nazwą w prywatnym buckecie
i unieważniał odnośnik, który uczestnik ma otwarty.

Komenda świadomie **nie jest** w ``scripts/deploy.sh``: trening to decyzja organizatora, a nie
element każdego wdrożenia. Ukrycie treningu = skasowanie zadań w panelu albo etapu w ``/admin/``
(README § 6.3).
"""

from __future__ import annotations

from datetime import timedelta

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.competitions.models import TRAINING_DEADLINE, StageFormat, StageKind
from apps.competitions.services import (
    create_problem,
    create_stage,
    current_edition,
    training_stage,
    update_problem,
)
from apps.competitions.training import TRAINING_PROBLEMS, TRAINING_STAGE_NAME, statement_file
from apps.competitions.training_pdf import ANSWERS_FILENAME, TRAINING_DIR

#: Okno reklamacji etapu bez terminu. Musi być niepuste (``appeal_window_opens_at <
#: appeal_window_closes_at`` jest constraintem w bazie), a jego długość niczego tu nie znaczy –
#: do roku 2099 nikt tego okna nie otworzy.
APPEAL_WINDOW = timedelta(days=1)


def _stored_bytes(problem) -> bytes | None:
    """Zawartość wgranego PDF-a albo ``None``, gdy zadanie nie ma pliku (lub plik zniknął).

    Czytamy **bajty**, a nie datę czy rozmiar: to jedyne porównanie, które odpowiada na właściwe
    pytanie („czy uczestnik zobaczy inną treść”). Brak pliku w storage traktujemy jak brak treści,
    a nie jak błąd – komenda ma wtedy po prostu wgrać plik jeszcze raz.
    """
    if not problem.statement_pdf:
        return None
    try:
        with problem.statement_pdf.open("rb") as stored:
            return stored.read()
    except (FileNotFoundError, OSError):
        return None


class Command(BaseCommand):
    help = (
        "Tworzy etap treningowy bieżącej edycji (bez terminu, poza kwalifikacją) i wgrywa do niego "
        "cztery zadania z apps/competitions/fixtures/training/."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        edition = current_edition()
        if edition is None:
            raise CommandError(
                "Brak bieżącej edycji. Utwórz ją (np. seed_edition_kwantowa --make-current) "
                "i uruchom komendę ponownie."
            )

        stage = training_stage(edition)
        if stage is None:
            now = timezone.now()
            stage = create_stage(
                edition=edition,
                kind=StageKind.TRAINING,
                name=TRAINING_STAGE_NAME,
                format=StageFormat.SUBMISSIONS,
                opens_at=now,
                # Data-wartownik zamiast terminu – interfejs jej nie pokazuje, bo pyta
                # o ``Stage.has_deadline``. Patrz komentarz przy ``TRAINING_DEADLINE``.
                deadline_at=TRAINING_DEADLINE,
                review_deadline_at=TRAINING_DEADLINE,
                appeal_window_opens_at=TRAINING_DEADLINE,
                appeal_window_closes_at=TRAINING_DEADLINE + APPEAL_WINDOW,
                # Trening nikogo nie kwalifikuje (``apps.results.services`` i tak go pomija), więc
                # próg jest tu wyłącznie wartością wymaganą przez model.
                min_points=0,
            )
            self.stdout.write(self.style.SUCCESS(f"Etap treningowy utworzony: {stage}."))
        else:
            self.stdout.write(f"Etap treningowy już jest: {stage}.")

        created = updated = unchanged = 0
        for spec in TRAINING_PROBLEMS:
            try:
                wanted = statement_file(spec)
            except FileNotFoundError as exc:
                raise CommandError(
                    f"Brak pliku treści {spec.path}. Złóż PDF-y: "
                    "python scripts/build_training_problem_pdfs.py"
                ) from exc
            problem = stage.problems.filter(number=spec.number).first()

            if problem is None:
                with spec.path.open("rb") as handle:
                    create_problem(
                        stage=stage,
                        actor=None,
                        statement=File(handle, name=spec.statement),
                        number=spec.number,
                        title=spec.title,
                    )
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  zadanie {spec.number}: utworzone ({spec.title})."))
                continue

            fields = {"title": spec.title} if problem.title != spec.title else {}
            statement_changed = _stored_bytes(problem) != wanted
            if not fields and not statement_changed:
                unchanged += 1
                self.stdout.write(f"  zadanie {spec.number}: bez zmian.")
                continue

            if statement_changed:
                with spec.path.open("rb") as handle:
                    update_problem(
                        problem,
                        None,
                        statement=File(handle, name=spec.statement),
                        # Etap treningowy jest otwarty od chwili utworzenia, więc bez tej flagi
                        # serwis odmówiłby podmiany treści. Ostrzeżenie („uczestnicy widzą tę
                        # wersję”) jest tu bez znaczenia: trening nie jest oceniany i nie ma
                        # zawodników, których podmiana dzieliłaby na dwie grupy.
                        confirm_open_stage=True,
                        **fields,
                    )
            else:
                update_problem(problem, None, **fields)
            updated += 1
            self.stdout.write(self.style.WARNING(f"  zadanie {spec.number}: zaktualizowane ({spec.title})."))

        self.stdout.write(
            self.style.SUCCESS(
                f"Gotowe: {created} utworzonych, {updated} zaktualizowanych, {unchanged} bez zmian. "
                f"Szkice rozwiązań (poza bazą, dla komisji): "
                f"{TRAINING_DIR / ANSWERS_FILENAME}"
            )
        )
