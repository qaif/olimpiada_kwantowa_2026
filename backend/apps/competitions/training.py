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

    @property
    def statement(self) -> str:
        return statement_filename(self.number)

    @property
    def path(self) -> Path:
        return TRAINING_DIR / self.statement


#: Arkusz treningowy. Tytuł niesie poziom trudności w nawiasie, bo to pierwsza informacja, której
#: szuka uczestnik wybierający zadanie na start – a lista w panelu pokazuje wyłącznie tytuły.
TRAINING_PROBLEMS: tuple[TrainingProblem, ...] = (
    TrainingProblem(1, "Stan kubitu i pomiar (proste)"),
    TrainingProblem(2, "Splątanie z dwóch bramek (średnie)"),
    TrainingProblem(3, "Podsłuch w protokole BB84 (trudne)"),
    TrainingProblem(4, "Nierówność CHSH i granica Tsirelsona (diabelnie trudne)"),
)


def statement_file(problem: TrainingProblem) -> bytes:
    """Bajty treści zadania z fixture'ów. Rzuca ``FileNotFoundError``, gdy PDF-a nie wygenerowano."""
    return problem.path.read_bytes()
