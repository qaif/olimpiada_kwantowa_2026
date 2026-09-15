"""Arkusz treningowy jako dane: numery, tytuły i pliki z treścią zadań.

Piaskownica, w której organizator przechodzi **całą** ścieżkę zawodów na prawdziwych zadaniach:
zgłoszenie → upload → dwie recenzje ślepe → konsensus/moderacja → wyniki. Reguły rodzaju etapu są
w ``models.StageKind``, skład PDF-ów w ``training_pdf``, a kontekst karty na pulpicie liczy sobie
``apps.web.views.participant``. Tutaj zostaje jedno: **czym jest arkusz treningowy** – żeby komenda
siejąca i testy czytały tę samą listę zamiast dwóch jej kopii.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .training_pdf import ANSWERS_FILENAME, TRAINING_DIR, statement_filename

#: Nazwa etapu treningowego widoczna dla uczestnika. Etap ma własną nazwę zamiast etykiety rodzaju
#: („Trening”), bo w panelu stoi obok etapów zawodów i ma się od nich odróżniać jednym spojrzeniem.
TRAINING_STAGE_NAME = "Zadania treningowe"

#: Plik z odpowiedziami. Nie wchodzi do żadnego ``Problem`` i nie jest nigdzie publikowany –
#: koordynator rozsyła go recenzentom sam. Nazwa jest tu po to, żeby nikt nie wgrał go przez pomyłkę
#: jako treści zadania (test pilnuje, że statement żadnego zadania nie pochodzi z tego pliku).
ANSWERS_PDF = ANSWERS_FILENAME


@dataclass(frozen=True)
class TrainingProblem:
    """Jedno zadanie arkusza treningowego: numer, tytuł i plik z treścią.

    Nazwa pliku nie jest osobnym polem: wiąże ją z numerem ``training_pdf.statement_filename``,
    czyli ta sama funkcja, którą generator nadaje nazwy przy składaniu. Gdyby stała tu kopia,
    zmiana konwencji w generatorze zostawiłaby komendę siejącą ze wskazaniem na nieistniejący plik.
    """

    number: int
    title: str
    #: Nazwa pliku z treścią, gdy nie jest to plik złożony z ``zadania.md`` (zadania organizatora
    #: przychodzą jednym gotowym PDF-em, wspólnym dla całego zestawu).
    file: str | None = None

    @property
    def statement(self) -> str:
        return self.file or statement_filename(self.number)

    @property
    def path(self) -> Path:
        return TRAINING_DIR / self.statement


#: Plik organizatora „Zadania przykładowe” (I Olimpiada Kwantowa, P1–P4) – jeden PDF na cztery
#: zadania, bo rysunek do P2 i treść P3 przechodzą między stronami i cięcie na osobne pliki
#: rozrywałoby zadania. Każde z zadań 1–4 wskazuje więc ten sam plik.
ORGANISER_PDF = "zadania-przykladowe.pdf"

#: Arkusz treningowy: najpierw cztery zadania organizatora (P1–P4 z jego pliku), potem cztery
#: zadania Fabiana złożone z ``zadania.md`` – zostawione na życzenie organizatora i podpisane
#: w tytule, żeby uczestnik widział, które pochodzą z zestawu oficjalnego. Numery 5–8 czytają pliki
#: ``zadanie-1..4.pdf`` (numer w nazwie pliku to numer w zestawie Fabiana, nie w arkuszu).
TRAINING_PROBLEMS: tuple[TrainingProblem, ...] = (
    TrainingProblem(1, "P1. Cząstka w nieskończonej studni potencjału", ORGANISER_PDF),
    TrainingProblem(2, "P2. Polaryzatory i pojedynczy foton", ORGANISER_PDF),
    TrainingProblem(3, "P3. Bramki H, Z, H na kubicie", ORGANISER_PDF),
    TrainingProblem(4, "P4. Obwód dwukubitowy z bramką RY(θ) i CNOT", ORGANISER_PDF),
    TrainingProblem(5, "Zadanie Fabiana 1: Stan kubitu i pomiar (proste)", statement_filename(1)),
    TrainingProblem(6, "Zadanie Fabiana 2: Splątanie z dwóch bramek (średnie)", statement_filename(2)),
    TrainingProblem(7, "Zadanie Fabiana 3: Podsłuch w protokole BB84 (trudne)", statement_filename(3)),
    TrainingProblem(
        8,
        "Zadanie Fabiana 4: Nierówność CHSH i granica Tsirelsona (diabelnie trudne)",
        statement_filename(4),
    ),
)


def statement_file(problem: TrainingProblem) -> bytes:
    """Bajty treści zadania z fixture'ów. Rzuca ``FileNotFoundError``, gdy PDF-a nie wygenerowano."""
    return problem.path.read_bytes()
