"""Nazwy plików do pobrania i budowa paczki ZIP z rozwiązaniami.

Jedno miejsce dla dwóch czytelników: koordynatora (prace całego etapu, zadania albo zaznaczone
wiersze) i recenzenta (własna kolejka). Osobne implementacje rozjechałyby się przy pierwszej
zmianie nazewnictwa, a nazwa pliku jest tu regułą ochrony danych, nie kosmetyką – ``original_name``
pochodzi od uczestnika i regularnie zawiera nazwisko albo szkołę.

Pamięć jest ograniczona z założenia: archiwum powstaje w pliku tymczasowym, a treść każdego
rozwiązania przechodzi ze storage do archiwum kawałkami. Etap finału to tysiące prac – budowanie
paczki w ``BytesIO`` byłoby awarią procesu, a nie wolnym pobieraniem. Kompresja jest wyłączona
(``ZIP_STORED``): PDF i ``.ipynb`` z wynikami są już skompresowane, więc deflate kosztowałby
procesor bez pożytku.
"""

from __future__ import annotations

import tempfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import BinaryIO

from .models import Submission, SubmissionFile
from .storage import get_submission_storage

#: Porcja przepisywana ze storage do archiwum. Kompromis między liczbą żądań a zajętą pamięcią.
COPY_CHUNK = 1024 * 1024

#: Spis treści paczki. Nazwa wielkimi literami, żeby stała na początku listy w menedżerach plików.
README_NAME = "README.txt"


def safe_download_name(name: str) -> str:
    """Nazwa do ``Content-Disposition``: sam plik, bez ścieżek i bez znaków łamiących nagłówek."""
    base = (name or "rozwiazanie").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char for char in base if char.isprintable() and char not in '"\r\n')
    return cleaned.strip() or "rozwiazanie"


def file_extension(submission_file: SubmissionFile) -> str:
    """Rozszerzenie pliku brane z ``object_key`` (klucz jest generowany, więc jest bezpieczny)."""
    tail = submission_file.object_key.rsplit(".", 1)
    candidate = tail[1].lower() if len(tail) == 2 else ""
    if not candidate:
        original = safe_download_name(submission_file.original_name).rsplit(".", 1)
        candidate = original[1].lower() if len(original) == 2 else ""
    return "".join(char for char in candidate if char.isalnum())[:10] or "dat"


def anonymous_download_name(submission: Submission, submission_file: SubmissionFile) -> str:
    """Nazwa pliku dla każdego, kto nie jest autorem rozwiązania.

    Ocenianie jest ślepe, a ``original_name`` pochodzi od uczestnika i regularnie zawiera nazwisko
    albo szkołę („Jan_Kowalski_LO5.pdf”). Recenzent, komisja odwoławcza i koordynator dostają więc
    nazwę zbudowaną wyłącznie z pseudonimu (``public_code``), numeru zadania i wersji.
    """
    return (
        f"{submission.entry.participant.public_code}"
        f"-z{submission.problem.number}-v{submission.version}.{file_extension(submission_file)}"
    )


def zip_entry_name(submission: Submission, submission_file: SubmissionFile) -> str:
    """Nazwa wpisu w paczce: ``<kod>_zad<numer>_v<wersja>.<rozszerzenie>``.

    Podkreślenia zamiast myślników z ``anonymous_download_name``, bo to inny cel: pojedynczy plik
    otwiera się od razu, a wpisy paczki sortują się w menedżerze plików i mają stać kodami obok
    siebie, a w obrębie kodu – po numerze zadania. Dane osobowe są wykluczone tak samo: w nazwie
    nie ma niczego poza pseudonimem, numerem zadania i wersją.
    """
    return (
        f"{submission.entry.participant.public_code}"
        f"_zad{submission.problem.number}_v{submission.version}.{file_extension(submission_file)}"
    )


@dataclass(frozen=True)
class ZipPackage:
    """Gotowe archiwum: otwarty plik tymczasowy ustawiony na początku i liczba prac w środku.

    ``count`` jest jedyną liczbą, która trafia do audytu – nazwy plików nie, bo pseudonim
    uczestnika w zestawieniu z etapem i zadaniem jest już informacją o konkretnej osobie.
    """

    stream: BinaryIO
    count: int


def _copy_file(source: BinaryIO, target: BinaryIO) -> None:
    """Przepisuje strumień kawałkami. Osobna funkcja, żeby pętla nie zamykała zmiennej w lambdzie."""
    while True:
        chunk = source.read(COPY_CHUNK)
        if not chunk:
            return
        target.write(chunk)


def _unique_name(name: str, used: set[str]) -> str:
    """Zabezpieczenie przed kolizją nazw – archiwum z dwoma wpisami o tej samej nazwie jest wadliwe.

    W praktyce trójka (kod, zadanie, wersja) jest unikalna, ale wywołujący dobiera zbiór rozwiązań
    sam i nie ma powodu, żeby ta gwarancja zależała od niego.
    """
    if name not in used:
        used.add(name)
        return name
    stem, _, suffix = name.rpartition(".")
    index = 2
    while f"{stem}({index}).{suffix}" in used:
        index += 1
    unique = f"{stem}({index}).{suffix}"
    used.add(unique)
    return unique


def _readme(entries: list[tuple[Submission, str]], readme_lines, header: str) -> bytes:
    """Spis treści paczki. Bez danych osobowych – tak samo jak nazwy plików."""
    lines = [header] if header else []
    lines.append(f"Prac w paczce: {len(entries)}.")
    lines.append("")
    for submission, name in entries:
        lines.append(readme_lines(submission, name) if readme_lines else name)
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_zip(
    submissions: Iterable[Submission],
    *,
    names: Callable[[Submission, SubmissionFile], str] = zip_entry_name,
    readme_lines: Callable[[Submission, str], str] | None = None,
    header: str = "",
) -> ZipPackage:
    """Buduje archiwum z **czystych** plików podanych rozwiązań i dokłada ``README.txt``.

    Rozwiązanie bez pliku albo z plikiem, który nie przeszedł skanu antywirusowego, jest pomijane
    w ciszy: paczka ma zawierać to, co wolno czytać, a nie tłumaczyć się z każdego braku (stan
    skanu widać w panelu przy pojedynczej pracy). Dzięki temu ta sama reguła obowiązuje obie
    drogi – koordynatora i recenzenta – i nie da się jej obejść doborem zbioru wejściowego.

    ``names`` i ``readme_lines`` są wstrzykiwane, bo spis treści odpowiada na inne pytanie u obu
    czytelników: koordynator dostaje samą listę plików, recenzent – powiązanie „recenzja → plik”,
    po którym odnajduje pracę w swojej kolejce.
    """
    storage = get_submission_storage()
    # ``TemporaryFile`` kasuje się przy zamknięciu, a ``FileResponse`` zamyka strumień po wysłaniu –
    # nie zostaje nic do sprzątania nawet wtedy, gdy klient zerwie połączenie w połowie.
    stream = tempfile.TemporaryFile()
    entries: list[tuple[Submission, str]] = []
    used: set[str] = set()
    try:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            for submission in submissions:
                submission_file = submission.latest_file
                if submission_file is None or not submission_file.is_clean:
                    continue
                name = _unique_name(names(submission, submission_file), used)
                source = storage.open(submission_file.object_key)
                try:
                    with archive.open(name, "w") as target:
                        _copy_file(source, target)
                finally:
                    source.close()
                entries.append((submission, name))
            archive.writestr(README_NAME, _readme(entries, readme_lines, header))
    except BaseException:
        stream.close()
        raise
    stream.seek(0)
    return ZipPackage(stream=stream, count=len(entries))
