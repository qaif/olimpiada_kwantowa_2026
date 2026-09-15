"""Arkusz treningowy jako dane: numery, tytuły i plik z treścią zadań.

Piaskownica, w której organizator przechodzi **całą** ścieżkę zawodów na prawdziwych zadaniach:
zgłoszenie → upload → dwie recenzje ślepe → konsensus/moderacja → wyniki. Reguły rodzaju etapu są
w ``models.StageKind``, a kontekst karty na pulpicie liczy sobie ``apps.web.views.participant``.
Tutaj zostaje jedno: **czym jest arkusz treningowy** – żeby komenda siejąca i testy czytały tę samą
listę zamiast dwóch jej kopii.

Treść pochodzi z pliku organizatora „Zadania przykładowe” (I Olimpiada Kwantowa, P1–P4). To jeden
PDF na cztery zadania: rysunek do P2 i treść P3 przechodzą między stronami, więc cięcie na osobne
pliki rozrywałoby zadania. Każde zadanie wskazuje ten sam plik, a numer i tytuł w ``Problem``
mówią uczestnikowi, które z czterech ma rozwiązać.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Katalog z plikami treści – wersjonowany w repozytorium, wgrywany przez ``seed_training_problems``.
TRAINING_DIR = Path(__file__).resolve().parent / "fixtures" / "training"

#: Nazwa etapu treningowego widoczna dla uczestnika. Etap ma własną nazwę zamiast etykiety rodzaju
#: („Trening”), bo w panelu stoi obok etapów zawodów i ma się od nich odróżniać jednym spojrzeniem.
TRAINING_STAGE_NAME = "Zadania treningowe"

#: Plik organizatora z kompletem czterech zadań przykładowych.
ORGANISER_PDF = "zadania-przykladowe.pdf"


@dataclass(frozen=True)
class TrainingProblem:
    """Jedno zadanie arkusza treningowego: numer, tytuł i nazwa pliku z treścią."""

    number: int
    title: str
    file: str = ORGANISER_PDF

    @property
    def statement(self) -> str:
        return self.file

    @property
    def path(self) -> Path:
        return TRAINING_DIR / self.file


#: Arkusz treningowy = cztery zadania organizatora. Tytuły nadane na podstawie treści (plik ich nie
#: ma); koordynator może je zmienić w panelu, a kolejny przebieg komendy je przywróci – dlatego
#: zmianę tytułu na stałe robi się tutaj.
TRAINING_PROBLEMS: tuple[TrainingProblem, ...] = (
    TrainingProblem(1, "P1. Cząstka w nieskończonej studni potencjału"),
    TrainingProblem(2, "P2. Polaryzatory i pojedynczy foton"),
    TrainingProblem(3, "P3. Bramki H, Z, H na kubicie"),
    TrainingProblem(4, "P4. Obwód dwukubitowy z bramką RY(θ) i CNOT"),
)


def statement_file(problem: TrainingProblem) -> bytes:
    """Bajty treści zadania z fixture'ów. Rzuca ``FileNotFoundError``, gdy pliku nie ma."""
    return problem.path.read_bytes()
