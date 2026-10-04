"""Uruchomienie jednego zadania w procesie dziecka – wspólne dla demona piaskownicy i testów.

Rodzic (ten moduł) pilnuje rzeczy, których dziecko samo sobie nie zagwarantuje: czasu ściennego
(``killpg`` całej grupy procesów), objętości wyjścia (czyta najwyżej ``MAX_STDOUT_BYTES``, resztę
wylewa) i tożsamości (``user``/``group`` + pusta lista grup dodatkowych – wymaga uprawnień
SETUID/SETGID, które ma wyłącznie nadzorca w kontenerze ``notebook-runner``). Bez Django.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass

from .child import RESULT_MARKER

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "child.py")
MAX_STDOUT_BYTES = 6 * 1024 * 1024
MAX_STDERR_BYTES = 16 * 1024

#: Twarde sufity – zadanie od workera może prosić o mniej, nigdy o więcej.
CEILING = {"wall": 120, "cpu": 120, "memory_mb": 2048, "file_mb": 64, "output_chars": 200_000}


@dataclass
class Limits:
    wall: int = 30
    cpu: int = 20
    memory_mb: int = 768
    file_mb: int = 8
    nofile: int = 64
    nproc: int | None = None
    output_chars: int = 64_000

    def clamp(self, requested: dict | None) -> Limits:
        values = asdict(self)
        for key, value in (requested or {}).items():
            if key in values and isinstance(value, int) and not isinstance(value, bool) and value > 0:
                values[key] = min(value, CEILING.get(key, value))
        return Limits(**values)


def _drain(stream, cap: int, sink: list[bytes]) -> None:
    """Czyta strumień do końca, zachowując najwyżej ``cap`` bajtów (reszta jest wylewana)."""
    kept = 0
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            if kept < cap:
                piece = chunk[: cap - kept]
                sink.append(piece)
                kept += len(piece)
    except OSError, ValueError:
        pass


def _child_env() -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        # ``/tmp`` dziecka to tmpfs kontenera (noexec); katalog roboczy zakłada dziecko z trybem 0700.
        "HOME": "/tmp",  # noqa: S108
        "TMPDIR": "/tmp",  # noqa: S108
        # Jeden wątek BLAS-a: wątki natywne liczą się do limitu procesów i pamięci, a zadania są małe.
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }


def execute_job(
    job: dict,
    *,
    limits: Limits | None = None,
    user: int | None = None,
    group: int | None = None,
    python: str | None = None,
) -> dict:
    """Wykonuje zadanie i zwraca wynik (słownik JSON). Nigdy nie rzuca z powodu kodu ucznia."""
    limits = (limits or Limits()).clamp(job.get("limits"))
    payload = json.dumps(
        {
            "cells": job.get("cells") or [],
            "targets": job.get("targets") or [],
            "limits": asdict(limits),
        }
    ).encode()
    started = time.monotonic()
    kwargs: dict = {}
    if user is not None:
        kwargs.update(user=user, group=group if group is not None else user, extra_groups=[])
    try:
        process = subprocess.Popen(  # noqa: S603 - stała ścieżka interpretera i skryptu, bez powłoki
            [python or sys.executable, "-I", CHILD],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_child_env(),
            cwd="/" if os.name == "posix" else None,
            close_fds=True,
            start_new_session=True,
            **kwargs,
        )
    except OSError as exc:
        return {"status": "error", "error": f"cannot start sandbox process: {exc}", "duration": 0.0}
    out: list[bytes] = []
    err: list[bytes] = []
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, MAX_STDOUT_BYTES, out), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, MAX_STDERR_BYTES, err), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        process.stdin.write(payload)
        process.stdin.close()
    except BrokenPipeError, OSError:
        pass
    timed_out = False
    try:
        process.wait(timeout=limits.wall)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # Cała grupa procesów – gdyby kod ucznia jednak coś uruchomił, nie przeżyje zadania.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError, PermissionError, OSError:
            pass
        process.wait()
    for reader in readers:
        reader.join(timeout=5)
    duration = round(time.monotonic() - started, 3)
    stdout = b"".join(out).decode("utf-8", errors="replace")
    stderr = b"".join(err).decode("utf-8", errors="replace")[-4000:]
    position = stdout.rfind(RESULT_MARKER)
    if position >= 0 and not timed_out:
        try:
            result = json.loads(stdout[position + len(RESULT_MARKER) :].strip().splitlines()[0])
        except json.JSONDecodeError, IndexError:
            result = None
        if isinstance(result, dict):
            result["status"] = "ok"
            result["duration"] = duration
            return result
    returncode = process.returncode
    if timed_out or returncode in (-signal.SIGXCPU, -signal.SIGKILL):
        status = "timeout"
    elif "MemoryError" in stderr:
        status = "memory"
    else:
        status = "crashed"
    return {"status": status, "returncode": returncode, "stderr": stderr, "duration": duration}
