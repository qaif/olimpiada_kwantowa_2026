"""Nadzorca piaskownicy: pętla po katalogu ``spool`` (kontener ``notebook-runner``, bez sieci).

Uruchomienie: ``python -m apps.notebooks.runner.daemon`` (zmienne: ``NOTEBOOK_SPOOL_DIR``,
``NOTEBOOK_RUNNER_SLOTS``, ``NOTEBOOK_RUNNER_UID_BASE``, ``NOTEBOOK_RUNNER_MEMORY_MB``…).

Protokół z workerem (``apps.notebooks.spool``):

- worker zapisuje ``jobs/<id>.json`` atomowo (plik tymczasowy + ``rename``),
- nadzorca „zajmuje” zadanie przez ``rename`` do ``claimed/`` – atomowe, więc dwa nadzorcy
  (np. przy rotacji kontenera) nie wykonają go dwa razy,
- wynik ląduje w ``results/<id>.json`` (znów plik tymczasowy + ``rename``); worker go czyta
  i kasuje. Wyniki i zajęte zadania starsze niż godzina sprząta nadzorca.

Sloty: ``NOTEBOOK_RUNNER_SLOTS`` zadań równolegle, każde jako **inny** UID
(``UID_BASE + numer slotu``), więc dwa jednocześnie działające notatniki nie widzą nawzajem
swoich katalogów roboczych (``0700``). Bez uprawnień do zmiany UID (testy, dev) – ten sam UID
i jeden slot.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path

from .sandbox import Limits, execute_job

logger = logging.getLogger("notebook-runner")
JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
MAX_JOB_FILE = 8 * 1024 * 1024
STALE_SECONDS = 3600
POLL_SECONDS = 0.5


def _env_int(name: str, default: int | None) -> int | None:
    value = os.environ.get(name, "")
    return int(value) if value.strip() else default


def ensure_dirs(spool: Path) -> dict[str, Path]:
    """Podkatalogi wymiany. Grupa i tryb jak katalogu ``spool`` (``2770``, grupa workera).

    Worker (UID 1000) i nadzorca (root z grupą 1000 – ``group_add`` w compose, bez
    ``CAP_DAC_OVERRIDE``) piszą do tych samych katalogów wyłącznie przez wspólną grupę; dzieci
    piaskownicy (inne UID, bez grup dodatkowych) nie mają tu wstępu. Zmiana grupy na własną grupę
    dodatkową nie wymaga ``CAP_CHOWN``, a bit setgid przenosi grupę na pliki w środku.
    """
    dirs = {name: spool / name for name in ("jobs", "claimed", "results")}
    try:
        group = spool.stat().st_gid
    except FileNotFoundError:
        group = None
    for path in dirs.values():
        if path.is_dir():
            continue
        path.mkdir(parents=True, exist_ok=True)
        try:
            if group is not None:
                os.chown(path, -1, group)
            # Grupa workera musi pisać (zadania), a reszta świata – nic; patrz docstring.
            os.chmod(path, 0o2770)  # noqa: S103 - celowo grupowy zapis, bez dostępu dla innych
        except OSError:
            pass
    return dirs


def write_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def claim_next(dirs: dict[str, Path]) -> tuple[str, Path] | None:
    """Najstarsze zadanie przeniesione do ``claimed/`` albo ``None``."""
    try:
        entries = sorted(dirs["jobs"].glob("*.json"), key=lambda p: p.stat().st_mtime)
    except FileNotFoundError:
        return None
    for entry in entries:
        job_id = entry.stem
        if not JOB_ID_RE.match(job_id):
            entry.unlink(missing_ok=True)
            continue
        target = dirs["claimed"] / entry.name
        try:
            os.rename(entry, target)
        except FileNotFoundError:
            continue
        return job_id, target
    return None


def process(job_id: str, claimed: Path, dirs: dict[str, Path], limits: Limits, user: int | None) -> dict:
    try:
        if claimed.stat().st_size > MAX_JOB_FILE:
            result = {"status": "invalid_job", "error": "job file too large"}
        else:
            job = json.loads(claimed.read_text(encoding="utf-8"))
            result = execute_job(job, limits=limits, user=user, group=user)
    except (OSError, ValueError) as exc:
        result = {"status": "invalid_job", "error": str(exc)[:300]}
    result["id"] = job_id
    write_atomic(dirs["results"] / f"{job_id}.json", result)
    claimed.unlink(missing_ok=True)
    return result


def housekeeping(dirs: dict[str, Path], now: float | None = None) -> None:
    now = now or time.time()
    for name in ("results", "claimed"):
        for entry in dirs[name].glob("*"):
            try:
                if now - entry.stat().st_mtime > STALE_SECONDS:
                    entry.unlink(missing_ok=True)
            except FileNotFoundError:
                continue


def serve(spool: Path, *, slots: int = 1, uid_base: int | None = None, limits: Limits | None = None) -> None:
    dirs = ensure_dirs(spool)
    limits = limits or Limits()
    can_switch = uid_base is not None and os.geteuid() == 0
    if not can_switch:
        slots = 1
    work: queue.Queue = queue.Queue()
    # Wolne sloty: zadanie zajmujemy (``claim``) dopiero, gdy jest kto je wykonać – zajęte, a czekające
    # w kolejce zadanie byłoby stracone dla drugiego nadzorcy przy rotacji kontenera.
    free = threading.Semaphore(slots)

    def worker(slot: int) -> None:
        user = uid_base + slot if can_switch else None
        while True:
            job_id, claimed = work.get()
            started = time.monotonic()
            result = process(job_id, claimed, dirs, limits, user)
            logger.info(
                "job %s slot %s: %s in %.1fs", job_id, slot, result.get("status"), time.monotonic() - started
            )
            work.task_done()
            free.release()

    for slot in range(slots):
        threading.Thread(target=worker, args=(slot,), daemon=True, name=f"slot-{slot}").start()
    logger.info("notebook runner: spool %s, %s slot(s), uid switching %s", spool, slots, can_switch)
    last_cleanup = 0.0
    while True:
        if time.monotonic() - last_cleanup > 60:
            housekeeping(dirs)
            last_cleanup = time.monotonic()
        if not free.acquire(timeout=POLL_SECONDS):
            continue
        claimed = claim_next(dirs)
        if claimed is None:
            free.release()
            time.sleep(POLL_SECONDS)
            continue
        work.put(claimed)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    spool = Path(os.environ.get("NOTEBOOK_SPOOL_DIR", "/spool"))
    limits = Limits(
        wall=_env_int("NOTEBOOK_RUNNER_WALL_SECONDS", 30) or 30,
        cpu=_env_int("NOTEBOOK_RUNNER_CPU_SECONDS", 20) or 20,
        memory_mb=_env_int("NOTEBOOK_RUNNER_MEMORY_MB", 768) or 768,
        nproc=_env_int("NOTEBOOK_RUNNER_NPROC", 32),
    )
    serve(
        spool,
        slots=_env_int("NOTEBOOK_RUNNER_SLOTS", 1) or 1,
        uid_base=_env_int("NOTEBOOK_RUNNER_UID_BASE", None),
        limits=limits,
    )


if __name__ == "__main__":
    main()
