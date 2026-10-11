"""Nadzorca piaskownicy: pętla po katalogu ``spool`` (kontener ``notebook-runner``, bez sieci).

Uruchomienie: ``python -m apps.notebooks.runner.daemon`` (zmienne: ``NOTEBOOK_SPOOL_DIR``,
``NOTEBOOK_RUNNER_SLOTS``, ``NOTEBOOK_RUNNER_UID_BASE``, ``NOTEBOOK_RUNNER_MEMORY_MB``…).

Protokół z workerem (``apps.notebooks.spool``):

- worker zapisuje ``jobs/<id>.json`` atomowo (plik tymczasowy + ``rename``),
- nadzorca „zajmuje” zadanie przez ``rename`` do ``claimed/`` – atomowe, więc dwa nadzorcy
  (np. przy rotacji kontenera) nie wykonają go dwa razy,
- wynik ląduje w ``results/<id>.json`` (znów plik tymczasowy + ``rename``); worker go czyta
  i kasuje. Wyniki i zajęte zadania starsze niż godzina sprząta nadzorca.

Sloty: ``NOTEBOOK_RUNNER_SLOTS`` zadań równolegle, każde jako **losowy, nieużywany w tej chwili**
UID z puli ``UID_BASE … UID_BASE + UID_SPAN`` (``UidPool``), więc dwa jednocześnie działające
notatniki nie widzą nawzajem swoich katalogów roboczych (``0700``), a resztki zadania nie spotkają
następnego. Po każdym zadaniu nadzorca zabija wszystkie procesy tego UID i kasuje jego pliki
w ``/tmp`` (``cleanup_uid``), a pętla dotyka znacznika ``HEARTBEAT`` dla healthchecka kontenera.
Bez uprawnień do zmiany UID (testy, dev) – ten sam UID, jeden slot i bez sprzątania.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from .sandbox import Limits, execute_job

logger = logging.getLogger("notebook-runner")
JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
MAX_JOB_FILE = 8 * 1024 * 1024
STALE_SECONDS = 3600
POLL_SECONDS = 0.5
#: Pula UID dzieci: każde zadanie dostaje **losowy, nieużywany** UID z ``UID_BASE … UID_BASE+UID_SPAN``.
#: Resztki poprzedniego zadania (proces, który przeżył ``killpg``, plik w ``/tmp``) należą do innego
#: UID niż następne zadanie, więc nie mają z nim nic wspólnego, nawet jeśli sprzątanie zawiedzie.
UID_SPAN = 50_000
HEARTBEAT = Path(os.environ.get("NOTEBOOK_RUNNER_HEARTBEAT", "/tmp/notebook-runner.heartbeat"))  # noqa: S108
#: Sprzątanie po zadaniu, wykonywane **jako UID zadania**: ``kill(-1)`` zabija wszystkie procesy tego
#: UID poza samym sobą (Linux), a potem z ``/tmp`` znika wszystko, co do tego UID należy. Jako ten UID,
#: a nie jako nadzorca, bo nadzorca nie ma ``CAP_DAC_OVERRIDE`` i nie wszedłby do katalogów ``0700``.
CLEANUP_SCRIPT = """
import os, signal
try:
    os.kill(-1, signal.SIGKILL)
except OSError:
    pass
uid = os.getuid()
for root, dirs, files in os.walk("/tmp", topdown=False):
    for name in files + dirs:
        path = os.path.join(root, name)
        try:
            info = os.lstat(path)
            if info.st_uid != uid:
                continue
            if os.path.isdir(path) and not os.path.islink(path):
                os.rmdir(path)
            else:
                os.unlink(path)
        except OSError:
            pass
"""


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
    """Jedno zadanie od pliku do pliku wyniku. Nie rzuca – nadzorca ma przeżyć każde zadanie."""
    try:
        if claimed.stat().st_size > MAX_JOB_FILE:
            result = {"status": "invalid_job", "error": "job file too large"}
        else:
            job = json.loads(claimed.read_text(encoding="utf-8"))
            if not isinstance(job, dict):
                raise ValueError("job is not an object")
            result = execute_job(job, limits=limits, user=user, group=user)
    except (OSError, ValueError, RecursionError) as exc:
        result = {"status": "invalid_job", "error": str(exc)[:300]}
    except Exception as exc:  # noqa: BLE001 - błąd nadzorcy zamyka zadanie wynikiem, nie wątkiem slotu
        logger.exception("job %s: unexpected error", job_id)
        result = {"status": "error", "error": type(exc).__name__}
    result["id"] = job_id
    try:
        write_atomic(dirs["results"] / f"{job_id}.json", result)
    except Exception:  # noqa: BLE001
        logger.exception("job %s: cannot write the result", job_id)
    claimed.unlink(missing_ok=True)
    return result


def cleanup_uid(uid: int, python: str | None = None) -> None:
    """Zabija procesy i kasuje pliki ``/tmp`` UID zadania (``CLEANUP_SCRIPT``)."""
    try:
        subprocess.run(  # noqa: S603 - stały skrypt, interpreter nadzorcy, bez powłoki
            [python or sys.executable, "-I", "-c", CLEANUP_SCRIPT],
            user=uid,
            group=uid,
            extra_groups=[],
            env={"PATH": "/usr/bin:/bin"},
            close_fds=True,
            timeout=30,
            check=False,
            capture_output=True,
        )
    except OSError, subprocess.SubprocessError:
        logger.exception("cleanup of uid %s failed", uid)


class UidPool:
    """Losowe UID zadań, bez powtórzeń wśród zadań trwających w tej chwili."""

    def __init__(self, base: int, span: int = UID_SPAN) -> None:
        self.base = base
        self.span = span
        self._active: set[int] = set()
        self._lock = threading.Lock()

    def acquire(self) -> int:
        with self._lock:
            while True:
                uid = self.base + secrets.randbelow(self.span)
                if uid not in self._active:
                    self._active.add(uid)
                    return uid

    def release(self, uid: int) -> None:
        with self._lock:
            self._active.discard(uid)


def beat(path: Path = HEARTBEAT) -> None:
    """Znacznik życia dla healthchecka kontenera (pętla nadzorcy dotyka go co pół sekundy)."""
    try:
        path.write_text(str(time.time()), encoding="ascii")
    except OSError:
        pass


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
    pool = UidPool(uid_base) if can_switch else None

    def worker(slot: int) -> None:
        while True:
            job_id, claimed = work.get()
            user = pool.acquire() if pool is not None else None
            started = time.monotonic()
            status = "error"
            try:
                status = process(job_id, claimed, dirs, limits, user).get("status")
            except Exception:  # noqa: BLE001 - wątek slotu nie może umrzeć (zmalałaby pula slotów)
                logger.exception("job %s: slot failure", job_id)
            finally:
                if user is not None:
                    cleanup_uid(user)
                    pool.release(user)
                work.task_done()
                free.release()
            logger.info(
                "job %s slot %s uid %s: %s in %.1fs", job_id, slot, user, status, time.monotonic() - started
            )

    for slot in range(slots):
        threading.Thread(target=worker, args=(slot,), daemon=True, name=f"slot-{slot}").start()
    logger.info("notebook runner: spool %s, %s slot(s), uid switching %s", spool, slots, can_switch)
    last_cleanup = 0.0
    while True:
        beat()
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
    # Domyślne limity nadzorcy pokrywają największe ustawienie zadania (60 s, 1024 MB) – zadanie
    # prosi o swoje, a ``Limits.clamp`` bierze mniejszą z obu wartości.
    limits = Limits(
        wall=_env_int("NOTEBOOK_RUNNER_WALL_SECONDS", 75) or 75,
        cpu=_env_int("NOTEBOOK_RUNNER_CPU_SECONDS", 60) or 60,
        memory_mb=_env_int("NOTEBOOK_RUNNER_MEMORY_MB", 1024) or 1024,
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
