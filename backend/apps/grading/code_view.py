"""Podgląd rozwiązania oddanego jako kod (``.py``, ``.ipynb``) i uwagi przypięte do linii.

Po co: warstwa adnotacji z ``static/js/review-annotations.js`` rysuje prostokąty na stronie PDF-u
albo na zdjęciu. Przy rozwiązaniu w Pythonie nie ma czego zaznaczać – pliku nie renderuje pdf.js,
a recenzent i tak mówi o nim inaczej: „linia 42”, a nie „prawy górny róg strony 2”. Ten moduł
pokazuje więc kod **po stronie serwera**, jako ponumerowane wiersze w ``<pre>``, a uwagi przypina
do numeru linii.

Czego tu nie ma i nie będzie: **uruchamiania kodu uczestnika**. Plik jest odczytywany jako bajty,
dekodowany i wypisywany jako tekst – nigdy importowany, nigdy wykonywany, nigdy przekazywany do
podprocesu. Notatnik ``.ipynb`` jest czytany jako JSON, a nie przez ``nbformat``/``nbconvert``:
konwerter uruchamia wtyczki i szablony, a tutaj potrzebna jest wyłącznie zawartość komórek kodu.

Trzy zabezpieczenia odczytu, bo plik pochodzi od uczestnika:

- **limit rozmiaru** (``MAX_SOURCE_BYTES``). Plik większy jest obcinany z widoczną adnotacją, a nie
  wczytywany w całości: podgląd ma się otworzyć, a nie zjeść pamięć procesu,
- **dekodowanie z podmianą** (``errors="replace"``). Rozwiązanie zapisane w cp1250 jest czytelne
  z kilkoma znakami zapytania; wyjątek dekodowania zamieniłby je w pustą stronę,
- **treść trafia do szablonu jako tekst**, autoescapowany przez Django. Nigdzie w tej ścieżce nie
  ma ``mark_safe`` ani budowania HTML-a ze stringów.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

#: Górny limit odczytu jednego pliku. 400 kB to około dziesięciu tysięcy wierszy kodu – granica,
#: powyżej której rozwiązanie i tak nie jest czytane linia po linii, tylko przeglądane w edytorze
#: po pobraniu. Podgląd ma być wygodą, a nie jedyną drogą do pliku.
MAX_SOURCE_BYTES = 400 * 1024

#: Rozszerzenia obsługiwane przez podgląd kodu, w postaci znormalizowanej (bez kropki, małe litery).
SOURCE_EXTENSIONS = ("py", "ipynb")

#: Typy MIME, po których rozpoznajemy plik z kodem. Rozpoznanie po MIME, a nie po nazwie od
#: uczestnika, jest tą samą zasadą, co przy wyborze podglądu PDF/JPEG w panelu recenzenta: nazwę
#: kontroluje przesyłający, a typ wyliczył serwer przy uploadzie (``validate_upload``).
SOURCE_MIMES = {
    "text/x-python": "py",
    "text/x-python-script": "py",
    "application/x-python-code": "py",
    "application/x-ipynb+json": "ipynb",
}

#: Nagłówek wstawiany między komórki notatnika. Po polsku i z numerem, bo to jedyna rzecz w tym
#: widoku, która nie pochodzi z pliku uczestnika, a recenzent musi wiedzieć, że jej tam nie było.
CELL_HEADER = "# --- komórka {number} ---"

#: Adnotacja doklejana na końcu obciętego pliku. Stoi w treści, a nie tylko w metadanych widoku,
#: żeby recenzent czytający sam listing wiedział, że koniec listingu nie jest końcem rozwiązania.
TRUNCATION_NOTICE = "# --- podgląd obcięty: plik jest większy niż limit podglądu ---"


@dataclass(frozen=True)
class CodeLine:
    """Jeden wiersz listingu: numer, treść i to, czy recenzent przypiął do niego uwagę."""

    number: int
    text: str
    noted: bool = False


@dataclass(frozen=True)
class CodeListing:
    """Listing gotowy do wyświetlenia razem z informacją o tym, skąd się wziął."""

    lines: list[CodeLine]
    #: ``py`` albo ``ipynb`` – szablon mówi recenzentowi wprost, co ogląda.
    kind: str
    truncated: bool
    #: Zdanie po polsku, gdy pliku nie dało się pokazać; ``None``, gdy listing jest poprawny.
    error: str | None = None


def source_kind(submission_file) -> str | None:
    """Czy ten plik nadaje się do podglądu kodu: ``py``, ``ipynb`` albo ``None``.

    Najpierw typ MIME wyliczony przez serwer, a dopiero potem rozszerzenie **klucza obiektu** –
    klucz buduje aplikacja (``submissions.storage.build_object_key``), więc jego końcówka jest
    danymi z systemu, a nie napisem od uczestnika. Nazwa oryginalna nie bierze udziału w decyzji.
    """
    if submission_file is None:
        return None
    mime = (getattr(submission_file, "mime", "") or "").lower()
    if mime in SOURCE_MIMES:
        return SOURCE_MIMES[mime]
    key = (getattr(submission_file, "object_key", "") or "").lower()
    _, _, extension = key.rpartition(".")
    return extension if extension in SOURCE_EXTENSIONS else None


def _read_bytes(submission_file) -> tuple[bytes, bool]:
    """Treść pliku przyciętą do limitu plus flaga „obcięto”.

    Czytamy o jeden bajt więcej niż limit: inaczej plik dokładnie równy limitowi byłby oznaczany
    jako obcięty, choć widać go w całości.
    """
    from apps.submissions.storage import get_submission_storage

    stream = get_submission_storage().open(submission_file.object_key)
    try:
        payload = stream.read(MAX_SOURCE_BYTES + 1)
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()
    if len(payload) > MAX_SOURCE_BYTES:
        return payload[:MAX_SOURCE_BYTES], True
    return payload, False


def notebook_source(payload: str) -> str:
    """Komórki kodu notatnika sklejone w jeden listing, z nagłówkiem przed każdą.

    Wyniki wykonania (``outputs``) i komórki tekstowe są pomijane: recenzent ocenia rozwiązanie,
    a nie zrzut z ostatniego uruchomienia u uczestnika, a wklejony obrazek wyniku i tak nie ma
    postaci tekstowej. Notatnik, którego nie da się sparsować jako JSON, trafia tu jako zwykły
    tekst – uczestnik oddał plik i recenzent ma prawo zobaczyć, co w nim jest.

    ``source`` bywa listą wierszy albo jednym napisem (oba kształty są zgodne ze specyfikacją
    nbformat), więc obsługujemy oba.
    """
    try:
        document = json.loads(payload)
    except (ValueError, TypeError):
        return payload
    cells = document.get("cells") if isinstance(document, dict) else None
    if not isinstance(cells, list):
        return payload
    chunks: list[str] = []
    number = 0
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        number += 1
        source = cell.get("source")
        if isinstance(source, list):
            text = "".join(part for part in source if isinstance(part, str))
        elif isinstance(source, str):
            text = source
        else:
            text = ""
        chunks.append(CELL_HEADER.format(number=number) + "\n" + text.rstrip("\n"))
    return "\n\n".join(chunks) if chunks else payload


def code_listing(submission_file, annotations=None) -> CodeListing | None:
    """Listing rozwiązania albo ``None``, gdy ten plik nie jest kodem.

    Uwagi przekazujemy tu w postaci, w jakiej leżą w ``Review.annotations`` – funkcja wyciąga
    z nich same numery linii, żeby podświetlić odpowiednie wiersze. Dzięki temu szablon nie musi
    porównywać niczego w pętli po tysiącu wierszy.

    Błąd odczytu (pliku nie ma na storage, sieć do MinIO nie odpowiada) kończy się listingiem
    z ``error``, a nie wyjątkiem: podgląd jest wygodą, a strona oceny musi stanąć także wtedy, gdy
    storage milczy. Recenzent ma wtedy nadal odnośnik „Pobierz plik” i pełny formularz oceny.
    """
    kind = source_kind(submission_file)
    if kind is None:
        return None
    try:
        payload, truncated = _read_bytes(submission_file)
    except Exception:  # noqa: BLE001 - backendy storage rzucają własnymi typami wyjątków
        return CodeListing(
            lines=[],
            kind=kind,
            truncated=False,
            error="Nie udało się wczytać pliku z rozwiązaniem – pobierz go, aby otworzyć.",
        )
    text = payload.decode("utf-8", errors="replace")
    if kind == "ipynb":
        text = notebook_source(text)
    if truncated:
        text = text + "\n" + TRUNCATION_NOTICE
    noted = line_numbers(annotations)
    lines = [
        CodeLine(number=number, text=raw, noted=number in noted)
        for number, raw in enumerate(text.splitlines(), start=1)
    ]
    return CodeListing(lines=lines, kind=kind, truncated=truncated, error=None)


def line_numbers(annotations) -> set[int]:
    """Numery linii, do których przypięto uwagi – zbiór, bo szablon pyta o przynależność."""
    return {
        item["line"]
        for item in (annotations or [])
        if isinstance(item, dict) and isinstance(item.get("line"), int) and not isinstance(item["line"], bool)
    }


def line_notes(review) -> list[dict]:
    """Uwagi recenzji przypięte do linii, uporządkowane po numerze linii.

    Kolejność po numerze linii, a nie po kolejności dopisywania: lista stoi obok listingu i czyta
    się ją równolegle z kodem. Adnotacje prostokątne (``rect``) zostają w swojej sekcji – oba
    kształty mieszkają w tym samym polu JSON, ale opisują dwie różne rzeczy.
    """
    items = [
        item
        for item in (getattr(review, "annotations", None) or [])
        if isinstance(item, dict) and isinstance(item.get("line"), int) and not isinstance(item["line"], bool)
    ]
    return sorted(items, key=lambda item: (item["line"], item.get("text", "")))


def add_line_note(review, line, text, *, public: bool = False, actor=None, request=None) -> dict:
    """Dopisuje uwagę do linii i zapisuje całą listę adnotacji recenzji. Zwraca dopisany wpis.

    Zapis idzie przez ``services.save_draft``, a nie przez ``review.save()``, i to nie jest
    obejście: tam stoi jedyna bramka mówiąca, czy tę recenzję wolno jeszcze zmieniać
    (``_assert_review_open`` – recenzja wystawiona, anulowana albo praca poza ocenianiem to odmowa)
    oraz walidacja kształtu adnotacji. Osobna droga zapisu znaczyłaby drugą kopię obu reguł.

    Numer linii przychodzi z formularza jako tekst, więc zamiana na liczbę jest tutaj – ``DomainError``
    z gotowym zdaniem po polsku trafia potem tą samą drogą, co odmowy serwisu.

    Import serwisów jest lokalny, żeby moduł czytany przez informację zwrotną uczestnika
    (``apps.results.feedback`` woła stąd ``public_line_notes``) nie zaciągał całego oceniania.
    """
    from rest_framework import status as http

    from apps.core.api import DomainError
    from apps.core.models import audit

    from .services import save_draft

    try:
        number = int(str(line).strip())
    except (TypeError, ValueError) as exc:
        raise DomainError(
            "Numer linii musi być liczbą całkowitą.", "INVALID_ANNOTATIONS", http.HTTP_400_BAD_REQUEST
        ) from exc
    clean = (text or "").strip()
    if not clean:
        raise DomainError(
            "Napisz treść uwagi do linii.", "ANNOTATION_TEXT_REQUIRED", http.HTTP_400_BAD_REQUEST
        )
    note = {"line": number, "text": clean, "public": bool(public)}
    save_draft(review, annotations=list(review.annotations or []) + [note])
    audit(
        actor,
        "review.line_note_added",
        review,
        {"line": number, "public": bool(public), "length": len(clean)},
        request=request,
    )
    return note


def public_line_notes(review) -> list[dict]:
    """Uwagi do linii oznaczone ``public`` – jedyne, które trafiają do uczestnika.

    Wołane z ``apps.results.feedback``: informacja zwrotna po ogłoszeniu wyników pokazuje je jako
    „linia N: treść”, obok komentarza recenzenta. Reguła widoczności jest ta sama, co przy
    adnotacjach prostokątnych (``Review.public_annotations``) – uwaga bez ``public`` jest notatką
    roboczą komitetu i nie opuszcza panelu.
    """
    return [item for item in line_notes(review) if item.get("public")]
