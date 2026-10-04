"""Proces dziecka piaskownicy: wykonuje komórki notatnika ucznia i oddaje artefakty celów.

Uruchamiany przez ``sandbox.execute_job`` jako ``python -I child.py``; zadanie (JSON) czyta ze
standardowego wejścia, wynik pisze na standardowe wyjście jako **ostatni** wiersz po znaczniku
``RESULT_MARKER`` i natychmiast kończy się ``os._exit`` (bez ``atexit`` ucznia).

Kolejność kroków jest częścią zabezpieczenia:

1. limity ``setrlimit`` (twarde = miękkie, więc kod ucznia ich nie podniesie),
2. prywatny katalog roboczy ``0700`` w ``/tmp`` (posprzątanie resztek po poprzednim zadaniu tego
   UID – w trybie bez zmiany UID; w kontenerze UID jest za każdym razem inny),
3. import NumPy i qclab **przed** hakiem audytowym (biblioteki czytają swoje pliki),
4. hak audytowy (``_install_guard``) – od tej chwili tylko odczyt bibliotek i zapis w katalogu
   roboczym, bez sieci, podprocesów, wątków i ``ctypes``,
5. komórki ucznia, każda osobno (błąd w jednej nie przerywa kolejnych – patrz QC-01 § 6.3),
6. ekstrakcja artefaktów celów (``qclab.grader.extract_artifacts``).

Hak audytowy **nie jest** granicą bezpieczeństwa (Python sam to zastrzega) – utrudnia nadużycia
i daje czytelne błędy („network access is not allowed”), a granicą jest kontener.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import io
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

RESULT_MARKER = "\n@@QCLAB-RESULT@@"
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
QCLAB_DIR = os.path.join(APP_ROOT, "qclab")
COMPAT_DIRS = (os.path.join(QCLAB_DIR, "_compat"), os.path.join(QCLAB_DIR, "_server_stubs"))
WORK_PREFIX = "qcwork-"
#: Wiek resztek po poprzednich zadaniach, które dziecko sprząta (sufit czasu zadania to 120 s).
STALE_WORKDIR_SECONDS = 600
MAX_JOB_BYTES = 8 * 1024 * 1024

#: Moduły, których import jest zablokowany (sieć, podprocesy, kod natywny, wątki).
BLOCKED_MODULES = frozenset(
    {
        "socket", "_socket", "ssl", "_ssl", "select", "selectors", "asyncio", "_asyncio",
        "subprocess", "_posixsubprocess", "multiprocessing", "_multiprocessing", "concurrent",
        "ctypes", "_ctypes", "cffi", "mmap", "fcntl", "pty", "termios",
        "urllib.request", "http.client", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib",
        "webbrowser",
    }
)  # fmt: skip
#: Zdarzenia audytowe odrzucane w całości (prefiksy).
BLOCKED_EVENT_PREFIXES = (
    "socket.", "ctypes.", "subprocess.", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
    "os.forkpty", "os.kill", "os.killpg", "os.system", "os.chdir", "os.chroot", "os.symlink",
    "os.link", "os.setuid", "os.setgid", "os.setgroups", "os.chown", "os.chflags", "os.lchown",
    "pty.", "urllib.", "http.", "ftplib.", "smtplib.", "poplib.", "imaplib.", "nntplib.",
    "telnetlib.", "webbrowser.", "mmap.", "fcntl.", "signal.pthread_kill", "_thread.start",
    "gc.get_objects", "gc.get_referrers", "gc.get_referents", "sys._current_frames",
    "sys.remote_exec", "resource.setrlimit", "resource.prlimit", "os.setpgid", "os.setsid",
)  # fmt: skip
#: Zdarzenia na ścieżkach: zapis dozwolony wyłącznie w katalogu roboczym.
WRITE_EVENTS = frozenset(
    {"os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.truncate", "os.utime", "os.chmod",
     "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.move",
     "shutil.rmtree", "shutil.make_archive", "shutil.unpack_archive"}
)  # fmt: skip
READ_EVENTS = frozenset({"os.listdir", "os.scandir", "glob.glob", "glob.glob/2", "os.walk"})


class GuardError(PermissionError):
    pass


def _set_limits(limits: dict) -> None:
    import resource

    def both(kind, value) -> None:
        try:
            resource.setrlimit(kind, (value, value))
        except ValueError, OSError:
            # Limit wyższy niż obecny twardy (np. kontener ustawił niższy) – zostaje niższy.
            soft, hard = resource.getrlimit(kind)
            if hard == resource.RLIM_INFINITY or value < hard:
                resource.setrlimit(kind, (min(value, hard), min(value, hard)))

    both(resource.RLIMIT_CPU, int(limits.get("cpu", 20)))
    both(resource.RLIMIT_AS, int(limits.get("memory_mb", 512)) * 1024 * 1024)
    both(resource.RLIMIT_FSIZE, int(limits.get("file_mb", 8)) * 1024 * 1024)
    both(resource.RLIMIT_NOFILE, int(limits.get("nofile", 64)))
    both(resource.RLIMIT_CORE, 0)
    if limits.get("nproc"):
        both(resource.RLIMIT_NPROC, int(limits["nproc"]))


def _prepare_workdir() -> str:
    """Prywatny katalog ``0700``; najpierw sprząta katalogi poprzednich zadań tego samego UID."""
    tmp = tempfile.gettempdir()
    uid = os.getuid()
    now = time.time()
    for entry in os.listdir(tmp):
        path = os.path.join(tmp, entry)
        try:
            info = os.stat(path)
        except OSError:
            continue
        # Tylko katalogi starsze niż każdy możliwy przebieg: przy tym samym UID (dev, testy, tryb
        # inline) równolegle działające dziecko nie może stracić katalogu w trakcie pracy.
        if (
            entry.startswith(WORK_PREFIX)
            and os.path.isdir(path)
            and info.st_uid == uid
            and now - info.st_mtime > STALE_WORKDIR_SECONDS
        ):
            shutil.rmtree(path, ignore_errors=True)
    workdir = tempfile.mkdtemp(prefix=WORK_PREFIX, dir=tmp)
    os.chmod(workdir, 0o700)
    return os.path.realpath(workdir)


def _under(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root + os.sep) for root in roots)


def _install_guard(workdir: str, read_roots: tuple[str, ...]) -> None:
    write_roots = (workdir,)
    read_roots = tuple(sorted({*read_roots, workdir}))
    trusted_code = tuple(r for r in read_roots if r != workdir)
    devices = frozenset({"/dev/null", "/dev/urandom", "/dev/random"})
    blocked_prefixes = BLOCKED_EVENT_PREFIXES
    blocked_modules = BLOCKED_MODULES
    write_events = WRITE_EVENTS
    read_events = READ_EVENTS
    realpath = os.path.realpath
    fsdecode = os.fsdecode
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def resolve(raw) -> str | None:
        try:
            if isinstance(raw, int):
                return None
            return realpath(fsdecode(raw))
        except Exception:  # noqa: BLE001 - nieczytelna ścieżka = odmowa
            raise GuardError("invalid path") from None

    def hook(event: str, args: tuple) -> None:
        if event.startswith(blocked_prefixes):
            if event.startswith("socket.") or event.startswith(("urllib.", "http.")):
                raise GuardError("network access is not allowed in the grading sandbox")
            raise GuardError(f"operation not allowed in the grading sandbox: {event}")
        if event == "import":
            name = args[0] or ""
            if name in blocked_modules or name.split(".")[0] in blocked_modules:
                raise GuardError(f"module not allowed in the grading sandbox: {name}")
            filename = args[1]
            if filename:
                path = resolve(filename)
                if path is not None and not path.endswith((".py", ".pyc")) and not _under(path, trusted_code):
                    raise GuardError("loading native code from this location is not allowed")
            return
        if event == "open":
            path = resolve(args[0])
            if path is None:
                return
            mode = args[1] or "r"
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writing = any(ch in str(mode) for ch in "wax+") or bool(flags & write_flags)
            if writing:
                if not _under(path, write_roots):
                    raise GuardError(f"writing outside the working directory is not allowed: {path}")
            elif not (_under(path, read_roots) or path in devices):
                raise GuardError(f"reading this file is not allowed: {path}")
            return
        if event in write_events:
            for raw in args:
                if isinstance(raw, (str, bytes, os.PathLike)):
                    path = resolve(raw)
                    if path is not None and not _under(path, write_roots):
                        raise GuardError("changing files outside the working directory is not allowed")
            return
        if event in read_events:
            raw = args[0] if args else "."
            if isinstance(raw, (str, bytes, os.PathLike)):
                path = resolve(raw)
                if path is not None and not _under(path, read_roots):
                    raise GuardError(f"listing this directory is not allowed: {path}")
            return
        if (
            event == "object.__setattr__"
            and len(args) >= 2
            and args[1]
            in (
                "__code__",
                "__defaults__",
                "__kwdefaults__",
                "__globals__",
            )
        ):
            raise GuardError("modifying function internals is not allowed in the grading sandbox")

    sys.addaudithook(hook)


class _CappedWriter(io.TextIOBase):
    """``sys.stdout`` ucznia: zapamiętuje najwyżej ``cap`` znaków, resztę po cichu odrzuca."""

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    def write(self, text) -> int:
        text = str(text)
        room = self._cap - self._size
        if room > 0:
            piece = text[:room]
            self._parts.append(piece)
            self._size += len(piece)
        if len(text) > max(room, 0):
            self.truncated = True
        return len(text)

    def writable(self) -> bool:
        return True

    def getvalue(self) -> str:
        return "".join(self._parts)


def strip_magics(source: str) -> str:
    """Bez magii IPythona (``%pip``, ``!ls``, ``%%time``) – na serwerze jest czysty Python."""
    lines = source.splitlines()
    if lines and lines[0].lstrip().startswith("%%"):
        return ""
    kept = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("%", "!")):
            kept.append(line[: len(line) - len(stripped)] + "pass")
        else:
            kept.append(line)
    return "\n".join(kept)


def _run_cell(source: str, index: int, namespace: dict) -> None:
    code = compile(strip_magics(source), f"<cell {index}>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    if code.co_flags & inspect.CO_COROUTINE:
        # ``await`` na najwyższym poziomie (np. ``await piplite.install(...)``): bez pętli asyncio
        # (wymaga gniazd). Atrapy z ``_server_stubs`` kończą się od razu; prawdziwe czekanie – błąd.
        coroutine = eval(code, namespace)  # noqa: S307 - kod ucznia w piaskownicy, cel tego modułu
        try:
            coroutine.send(None)
        except StopIteration:
            return
        coroutine.close()
        raise RuntimeError("asynchronous waiting is not supported in the grading sandbox")
    exec(code, namespace)  # noqa: S102 - kod ucznia w piaskownicy, cel tego modułu


def _format_error(exc: BaseException) -> str:
    lines = traceback.format_exception_only(type(exc), exc)
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename.startswith("<cell")]
    where = f" ({frames[-1].filename[1:-1]}, line {frames[-1].lineno})" if frames else ""
    return ("".join(lines).strip() + where)[:1000]


def main() -> None:
    started = time.monotonic()
    raw = sys.stdin.buffer.read(MAX_JOB_BYTES + 1)
    job = json.loads(raw[:MAX_JOB_BYTES])
    _set_limits(job.get("limits") or {})
    sys.dont_write_bytecode = True
    workdir = _prepare_workdir()
    os.chdir(workdir)
    sys.path[:0] = [APP_ROOT, *COMPAT_DIRS]
    import numpy  # noqa: F401 - import przed hakiem: biblioteka czyta swoje pliki
    import numpy.linalg  # noqa: F401

    import qclab  # noqa: F401
    from qclab import backends, grader, library, visualization  # noqa: F401

    for name in ("math", "cmath", "random", "itertools", "functools", "collections", "fractions",
                 "statistics", "json", "re", "copy", "decimal", "string", "operator", "typing"):  # fmt: skip
        __import__(name)
    import qiskit  # noqa: F401 - moduł zgodności z ``qclab/_compat``

    stdlib = os.path.dirname(os.__file__)
    site_dirs = [p for p in sys.path if p.endswith("site-packages")]
    read_roots = tuple(
        os.path.realpath(p)
        for p in [stdlib, *site_dirs, QCLAB_DIR, sys.prefix, sys.base_prefix, "/usr/share/zoneinfo"]
        if p and os.path.exists(p)
    )
    output_cap = int((job.get("limits") or {}).get("output_chars", 64_000))
    captured = _CappedWriter(output_cap)
    real_stdout = sys.stdout
    # ``sys.__stdout__`` też podmieniony – kod ucznia nie dopisze nic za wynikiem przez obiekt Pythona.
    sys.stdout = sys.stderr = captured
    sys.__stdout__ = sys.__stderr__ = captured  # type: ignore[misc]
    real_fd = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    _install_guard(workdir, read_roots)

    namespace: dict = {"__name__": "__main__", "__builtins__": builtins}
    cell_errors = []
    for index, source in enumerate(job.get("cells") or [], start=1):
        try:
            _run_cell(str(source), index, namespace)
        except BaseException as exc:  # noqa: BLE001 - każdy błąd ucznia (także SystemExit) to wynik
            cell_errors.append({"cell": index, "error": _format_error(exc)})
    try:
        artifacts = grader.extract_artifacts(namespace, job.get("targets") or [])
    except BaseException as exc:  # noqa: BLE001
        artifacts = {}
        cell_errors.append({"cell": 0, "error": _format_error(exc)})
    # Pliki ucznia znikają razem z zadaniem – tmpfs piaskownicy jest wspólny dla kolejnych zadań.
    shutil.rmtree(workdir, ignore_errors=True)
    result = {
        "status": "ok",
        "cell_errors": cell_errors[:50],
        "artifacts": artifacts,
        "output": captured.getvalue(),
        "output_truncated": captured.truncated,
        "duration": round(time.monotonic() - started, 3),
    }
    payload = (RESULT_MARKER + json.dumps(result) + "\n").encode()
    del real_stdout
    view = memoryview(payload)
    while view:
        written = os.write(real_fd, view)
        view = view[written:]
    os._exit(0)


if __name__ == "__main__":
    main()
