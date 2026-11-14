"""Załączniki zadań – pliki danych do pobrania przy treści zadania (PUB-02, ``docs/tasks/PUB-02.md``).

Przykład, dla którego to powstało: zadanie D2 Etapu I każe pobrać „z załączników do tego zadania”
pliki ``jony2.npy``, ``jony3.npy`` i ``jony4.npy``. Załącznik jest **częścią treści zadania**:

- leży w tym samym prywatnym storage, co PDF treści (``private_media``),
- jest jawny dokładnie wtedy, kiedy treść (``competitions.api.problem_content_visible``) – przed
  publikacją 404 dla wszystkich poza koordynatorem; cudzy konkurs – 404,
- wychodzi zawsze jako ``application/octet-stream`` z ``Content-Disposition: attachment`` – nigdy
  do wyświetlenia w przeglądarce; stąd też biała lista rozszerzeń bez HTML, SVG i plików
  wykonywalnych (druga warstwa, gdyby nagłówek kiedyś zginął).

Zarządza nimi koordynator (panel, ``web:coordinator-problem-attachment-*``) albo operator z powłoki
(``manage.py add_problem_attachment``). Rola jest sprawdzana **tutaj**, a nie tylko w widoku.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath

from django.core.files.base import File
from django.db import IntegrityError, transaction
from rest_framework import status

from apps.core.api import DomainError

from .models import Problem, ProblemAttachment

#: Rozszerzenia dopuszczone w załącznikach – dane, notatniki, kod źródłowy, archiwa, obrazy, PDF.
#: Bez HTML, SVG, JS i plików wykonywalnych: plik idzie i tak jako ``octet-stream`` do pobrania,
#: ale lista jest drugą warstwą na dzień, w którym ktoś zmieni nagłówek.
ALLOWED_EXTENSIONS = frozenset(
    {
        ".npy",
        ".npz",
        ".csv",
        ".txt",
        ".json",
        ".dat",
        ".h5",
        ".hdf5",
        ".ipynb",
        ".py",
        ".zip",
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
    }
)

#: Limit rozmiaru jednego załącznika. Uwaga: wgranie z panelu przechodzi przez Caddy, który tnie
#: żądania powyżej ``MAX_UPLOAD_MB`` (domyślnie 25 MB) – większy plik wgrywa się komendą.
MAX_ATTACHMENT_MB = 50
MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024

#: Długość nazwy pliku po oczyszczeniu (pole modelu ma 150 znaków).
MAX_FILENAME_LENGTH = 120

_UNSAFE = re.compile(r"[^\w.\-]+", re.UNICODE)


def _error(message: str, code: str, http_status: int = status.HTTP_400_BAD_REQUEST) -> DomainError:
    return DomainError(message, code, http_status)


def clean_filename(name: str) -> str:
    """Nazwa do pobrania: sam ostatni człon ścieżki, bez znaków sterujących i separatorów.

    Litery (także polskie), cyfry, ``.``, ``-`` i ``_`` zostają; reszta (spacje, cudzysłowy,
    ukośniki) zamienia się na ``_``. Kropki na początku znikają (bez plików ukrytych), rozszerzenie
    jest małymi literami. Pusta nazwa po oczyszczeniu to błąd domenowy, a nie ``"plik"`` –
    koordynator ma wiedzieć, że nazwa była zła.
    """
    base = PureWindowsPath(PurePosixPath(str(name or "")).name).name
    base = unicodedata.normalize("NFC", base)
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C")
    base = _UNSAFE.sub("_", base).strip("._ ")
    base = re.sub(r"_+", "_", base)
    path = PurePosixPath(base)
    suffix = path.suffix.lower()
    stem = path.stem if suffix else base
    if not stem or not suffix:
        raise _error(
            "Nazwa pliku musi mieć część główną i rozszerzenie (np. dane.npy).", "ATTACHMENT_NAME_INVALID"
        )
    stem = stem[: MAX_FILENAME_LENGTH - len(suffix)]
    return f"{stem}{suffix}"


def validate_extension(filename: str) -> None:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise _error(
            f"Niedozwolony typ pliku „{suffix or filename}”. Dozwolone: {allowed}.",
            "ATTACHMENT_TYPE_NOT_ALLOWED",
        )


def file_digest(uploaded) -> tuple[int, str]:
    """Rozmiar i SHA-256 pliku – liczone po kawałkach, bez wczytywania całości do pamięci."""
    digest = hashlib.sha256()
    size = 0
    uploaded.seek(0)
    chunks = uploaded.chunks() if hasattr(uploaded, "chunks") else iter(lambda: uploaded.read(65536), b"")
    for chunk in chunks:
        size += len(chunk)
        if size > MAX_ATTACHMENT_BYTES:
            raise _error(
                f"Plik jest za duży – limit załącznika to {MAX_ATTACHMENT_MB} MB.", "ATTACHMENT_TOO_LARGE"
            )
        digest.update(chunk)
    uploaded.seek(0)
    if size == 0:
        raise _error("Plik jest pusty.", "ATTACHMENT_EMPTY")
    return size, digest.hexdigest()


def require_coordinator(actor, competition) -> None:
    """Koordynator **tego** konkursu – reguła serwisu, nie tylko widoku (bez eskalacji superusera)."""
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    if actor is None or not has_role(actor, competition, CompetitionRole.COORDINATOR):
        raise _error(
            "Załącznikami zadań zarządza wyłącznie koordynator konkursu.",
            "FORBIDDEN",
            status.HTTP_403_FORBIDDEN,
        )


def attachments_for(problem: Problem):
    """Załączniki zadania w kolejności wyświetlania."""
    return ProblemAttachment.objects.filter(problem=problem).order_by("position", "id")


def _next_position(problem: Problem) -> int:
    last = attachments_for(problem).order_by("-position").values_list("position", flat=True).first()
    return 0 if last is None else last + 1


def _audit(actor, action: str, attachment: ProblemAttachment, diff: dict, request=None) -> None:
    from apps.core.models import audit

    audit(actor, action, attachment, {"problem": attachment.problem_id, **diff}, request=request)


@transaction.atomic
def add_attachment(
    problem: Problem,
    uploaded,
    *,
    actor,
    competition,
    description: str = "",
    filename: str | None = None,
    request=None,
    system: bool = False,
) -> ProblemAttachment:
    """Nowy załącznik zadania. ``system=True`` – operator z powłoki (komenda), bez sprawdzania roli.

    Odmowy: niedozwolone rozszerzenie, plik pusty albo ponad limit, nazwa zajęta w tym zadaniu.
    """
    if not system:
        require_coordinator(actor, competition)
    name = clean_filename(filename or getattr(uploaded, "name", ""))
    validate_extension(name)
    size, sha256 = file_digest(uploaded)
    attachment = ProblemAttachment(
        problem=problem,
        filename=name,
        description=(description or "").strip()[:300],
        position=_next_position(problem),
        size=size,
        sha256=sha256,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    attachment.file.save(name, File(uploaded, name=name), save=False)
    try:
        with transaction.atomic():
            attachment.save()
    except IntegrityError as exc:
        attachment.file.delete(save=False)
        raise _error(
            f"Zadanie ma już załącznik o nazwie {name}. Usuń go najpierw albo zmień nazwę pliku.",
            "ATTACHMENT_NAME_TAKEN",
            status.HTTP_409_CONFLICT,
        ) from exc
    _audit(
        actor,
        "problem.attachment_added",
        attachment,
        {"filename": name, "size": size, "sha256": sha256, "source": "command" if system else "panel"},
        request=request,
    )
    return attachment


@transaction.atomic
def replace_attachment_file(
    attachment: ProblemAttachment, uploaded, *, actor, competition, request=None, system: bool = False
) -> ProblemAttachment:
    """Nowa treść istniejącego załącznika (ta sama nazwa). Stary obiekt w storage jest kasowany."""
    if not system:
        require_coordinator(actor, competition)
    size, sha256 = file_digest(uploaded)
    old_name, old_sha = attachment.file.name, attachment.sha256
    attachment.file.save(attachment.filename, File(uploaded, name=attachment.filename), save=False)
    attachment.size, attachment.sha256 = size, sha256
    attachment.save(update_fields=["file", "size", "sha256"])
    if old_name:
        storage = attachment.file.storage
        transaction.on_commit(lambda: storage.delete(old_name))
    _audit(
        actor,
        "problem.attachment_replaced",
        attachment,
        {"filename": attachment.filename, "sha256": {"from": old_sha, "to": sha256}, "size": size},
        request=request,
    )
    return attachment


@transaction.atomic
def update_attachment(
    attachment: ProblemAttachment,
    *,
    actor,
    competition,
    description: str | None = None,
    position: int | None = None,
    request=None,
) -> ProblemAttachment:
    """Zmiana opisu albo kolejności. Bez zmian – bez wpisu w audycie."""
    require_coordinator(actor, competition)
    diff: dict = {}
    if description is not None:
        description = description.strip()[:300]
        if description != attachment.description:
            diff["description"] = {"from": attachment.description, "to": description}
            attachment.description = description
    if position is not None and position != attachment.position:
        if position < 0 or position > 32767:
            raise _error("Kolejność musi być liczbą od 0 do 32767.", "ATTACHMENT_POSITION_INVALID")
        diff["position"] = {"from": attachment.position, "to": position}
        attachment.position = position
    if not diff:
        return attachment
    attachment.save(update_fields=["description", "position"])
    _audit(actor, "problem.attachment_updated", attachment, diff, request=request)
    return attachment


@transaction.atomic
def delete_attachment(attachment: ProblemAttachment, *, actor, competition, request=None) -> None:
    """Usunięcie załącznika – wiersz i obiekt w storage. Ślad (nazwa, SHA-256) zostaje w audycie."""
    require_coordinator(actor, competition)
    _audit(
        actor,
        "problem.attachment_deleted",
        attachment,
        {"filename": attachment.filename, "sha256": attachment.sha256, "size": attachment.size},
        request=request,
    )
    name = attachment.file.name
    storage = attachment.file.storage
    attachment.delete()
    if name:
        # Po zatwierdzeniu transakcji: wycofany ``DELETE`` nie może zostawić wiersza bez pliku.
        transaction.on_commit(lambda: storage.delete(name))
