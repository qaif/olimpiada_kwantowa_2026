"""Strona workera w protokole z piaskownicą: zapis zadania i odbiór wyniku (``runner/daemon.py``).

Tryb ``NOTEBOOK_RUNNER_INLINE`` (dev, testy): zadanie wykonuje się od razu w podprocesie workera
(``runner.sandbox.execute_job``) z tymi samymi limitami i hakiem audytowym, ale **bez** warstwy
kontenera – stąd zakaz tego trybu w produkcji (``apps.notebooks.checks``). Wynik i tak ląduje
w ``results/`` katalogu spool – odbiera go ta sama ścieżka, co wynik z kontenera, także wtedy, gdy
zadanie odbioru trafi do innego procesu workera niż zadanie wysyłki.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from django.conf import settings

from .runner.daemon import JOB_ID_RE, ensure_dirs, write_atomic
from .runner.sandbox import Limits, execute_job

MAX_RESULT_BYTES = 12 * 1024 * 1024


class SpoolUnavailable(RuntimeError):
    pass


def spool_dir() -> Path:
    return Path(settings.NOTEBOOK_SPOOL_DIR)


def inline() -> bool:
    return bool(getattr(settings, "NOTEBOOK_RUNNER_INLINE", False))


def submit(job: dict) -> str:
    """Zapisuje zadanie i zwraca jego identyfikator. W trybie inline wynik powstaje od razu."""
    job_id = uuid.uuid4().hex
    if inline():
        dirs = ensure_dirs(spool_dir())
        result = execute_job(job, limits=Limits())
        result["id"] = job_id
        write_atomic(dirs["results"] / f"{job_id}.json", result)
        return job_id
    root = spool_dir()
    if not root.is_dir():
        # Brak wolumenu ``notebook_spool`` w workerze – instalacja bez piaskownicy.
        raise SpoolUnavailable(f"spool directory {root} does not exist")
    dirs = ensure_dirs(root)
    write_atomic(dirs["jobs"] / f"{job_id}.json", job)
    return job_id


def fetch(job_id: str) -> dict | None:
    """Wynik zadania (plik jest od razu usuwany) albo ``None``, gdy jeszcze go nie ma."""
    if not JOB_ID_RE.match(job_id or ""):
        return None
    path = spool_dir() / "results" / f"{job_id}.json"
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    try:
        if size > MAX_RESULT_BYTES:
            return {"status": "error", "error": "result too large"}
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {"status": "error", "error": "unreadable result"}
    finally:
        path.unlink(missing_ok=True)
    return data if isinstance(data, dict) else {"status": "error", "error": "invalid result"}


def cancel(job_id: str) -> None:
    """Usuwa niepodjęte zadanie i ewentualny spóźniony wynik (po przekroczeniu czasu oczekiwania)."""
    if not JOB_ID_RE.match(job_id or ""):
        return
    for name in ("jobs", "results"):
        try:
            os.unlink(spool_dir() / name / f"{job_id}.json")
        except FileNotFoundError:
            continue
