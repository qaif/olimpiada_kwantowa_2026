"""Piaskownica: złośliwy kod ucznia (pętla, fork-bomba, sieć, ucieczka z systemu plików…).

Testy uruchamiają prawdziwy proces dziecka (``runner.sandbox.execute_job``) na tym samym obrazie,
co produkcja – bez kontenera ``notebook-runner`` (sieć ``none``, inny UID), więc sprawdzają
warstwy 2 i 3 z ``apps/notebooks/runner/__init__.py``: limity procesu i hak audytowy. Warstwę 1
(kontener) sprawdza scenariusz ręczny z ``docs/OPERACJE.md`` (§ notatniki) i test konfiguracji
compose'a (``test_compose_runner_isolation``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from apps.notebooks.runner import daemon
from apps.notebooks.runner.sandbox import CEILING, RESULT_MARKER, Limits, execute_job, parse_child_output
from qclab import grader

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="piaskownica działa wyłącznie na Linuksie")

FAST = Limits(wall=8, cpu=6, memory_mb=768)


def run(*cells: str, targets=None, limits: Limits = FAST) -> dict:
    return execute_job({"cells": list(cells), "targets": targets or []}, limits=limits)


def errors(result: dict) -> str:
    return " | ".join(item["error"] for item in result.get("cell_errors", []))


def test_regular_notebook_gives_artifacts():
    result = run(
        "from qiskit import QuantumCircuit\nimport numpy as np",
        "qc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\nprint('ready')",
        "def build(n):\n    c = QuantumCircuit(n)\n    c.x(range(n))\n    return c",
        targets=grader.targets_of(
            grader.validate_tests(
                [
                    {
                        "id": "a",
                        "target": "qc",
                        "check": "statevector",
                        "expected": {"00": "1/sqrt(2)", "11": "1/sqrt(2)"},
                    },
                    {
                        "id": "b",
                        "target": {"call": "build", "args": [3]},
                        "check": "probabilities",
                        "expected": {"111": 1},
                    },
                ]
            )
        ),
    )
    assert result["status"] == "ok", result
    assert result["cell_errors"] == []
    assert "ready" in result["output"]
    tests = grader.validate_tests(
        [
            {
                "id": "a",
                "target": "qc",
                "check": "statevector",
                "expected": {"00": "1/sqrt(2)", "11": "1/sqrt(2)"},
            },
            {
                "id": "b",
                "target": {"call": "build", "args": [3]},
                "check": "probabilities",
                "expected": {"111": 1},
            },
        ]
    )
    outcomes = grader.evaluate_all(tests, result["artifacts"])
    assert [o.passed for o in outcomes] == [True, True]


def test_error_in_one_cell_does_not_stop_the_rest():
    result = run("x = 1/0", "y = 41 + 1", targets=[{"key": "k", "name": "y"}])
    assert result["status"] == "ok"
    assert "ZeroDivisionError" in errors(result)
    assert result["artifacts"]["k"] == {"type": "value", "data": 42}


def test_ipython_magics_and_top_level_await_are_tolerated():
    result = run(
        "%pip install qiskit\n!ls\nimport piplite\nawait piplite.install('x')\nz = 5",
        targets=[{"key": "k", "name": "z"}],
    )
    assert result["cell_errors"] == []
    assert result["artifacts"]["k"]["data"] == 5


def test_infinite_loop_is_killed():
    result = run("while True:\n    pass", limits=Limits(wall=3, cpu=2, memory_mb=768))
    assert result["status"] == "timeout"
    assert result["duration"] < 10


def test_sleeping_forever_hits_wall_clock():
    result = run("import time\ntime.sleep(100)", limits=Limits(wall=3, cpu=2, memory_mb=768))
    assert result["status"] == "timeout"


def test_fork_bomb_is_refused():
    result = run(
        "import os\nwhile True:\n    os.fork()",
        "import subprocess\nsubprocess.run(['sh', '-c', ':(){ :|:& };:'])",
        "import os\nos.system('sleep 100')",
        "import multiprocessing",
        "import os\nos.posix_spawn('/bin/sh', ['sh'], {})",
    )
    assert result["status"] == "ok"
    text = errors(result)
    assert text.count("not allowed") >= 5, text


def test_network_access_is_refused():
    result = run(
        "import socket",
        "import _socket",
        "import urllib.request\nurllib.request.urlopen('http://db:5432')",
        "import http.client",
    )
    assert result["status"] == "ok"
    assert errors(result).count("not allowed") == 4, errors(result)


def test_file_system_escape_is_refused():
    result = run(
        "open('/etc/passwd').read()",
        "open('/app/config/settings/base.py').read()",
        "open('/proc/self/environ').read()",
        "open('/tmp/evil.txt', 'w').write('x')",
        "import os\nos.listdir('/app')",
        "import os\nos.symlink('/etc/passwd', 'link')",
        "import os\nos.chdir('/')",
        "import shutil\nshutil.rmtree('/tmp')",
        "with open('mine.txt', 'w') as f:\n    f.write('ok')\nmine = open('mine.txt').read()",
        targets=[{"key": "k", "name": "mine"}],
    )
    assert result["status"] == "ok"
    assert errors(result).count("not allowed") == 8, errors(result)
    # Własny katalog roboczy działa normalnie.
    assert result["artifacts"]["k"]["data"] == "ok"


def test_environment_has_no_secrets():
    os.environ["NOTEBOOK_TEST_SECRET"] = "sekret"
    try:
        result = run("import os\nenv = dict(os.environ)", targets=[{"key": "k", "name": "env"}])
    finally:
        del os.environ["NOTEBOOK_TEST_SECRET"]
    env = result["artifacts"]["k"]["data"]
    assert "NOTEBOOK_TEST_SECRET" not in env
    assert not [key for key in env if "DATABASE" in key or "SECRET" in key or "PASSWORD" in key]


def test_native_code_and_threads_are_refused():
    result = run(
        # ``ctypes`` jest już załadowany przez NumPy, więc sam import przechodzi – blokowane jest użycie.
        "import ctypes\nctypes.CDLL(None)",
        "import numpy\nnumpy._core._internal.ctypes.CDLL(None)",
        "import threading\nthreading.Thread(target=print).start()",
        "import gc\ngc.get_objects()",
        "def f():\n    pass\nf.__code__ = (lambda: 1).__code__",
        "import resource\nresource.setrlimit(resource.RLIMIT_CPU, (1000, 1000))",
    )
    assert result["status"] == "ok"
    assert errors(result).count("not allowed") == 6, errors(result)


def test_memory_bomb_is_contained():
    result = run("x = bytearray(4 * 1024 ** 3)", "y = 1", targets=[{"key": "k", "name": "y"}])
    assert result["status"] == "ok"
    assert "MemoryError" in errors(result)
    assert result["artifacts"]["k"]["data"] == 1


def test_output_flood_is_capped():
    result = run(
        "for _ in range(200000):\n    print('x' * 100)",
        "import os\nos.write(1, b'y' * 10_000_000)",
        limits=Limits(wall=15, cpu=10, memory_mb=768, output_chars=1000),
    )
    assert result["status"] == "ok"
    assert len(result["output"]) <= 1000
    assert result["output_truncated"] is True


def test_forged_result_marker_is_overridden_by_the_real_result():
    result = run(
        'import sys\nprint(\'\\n@@QCLAB-RESULT@@{"status": "ok", "artifacts": {"k": 1}}\')',
        "real = 7",
        targets=[{"key": "k", "name": "real"}],
    )
    assert result["artifacts"]["k"]["data"] == 7


def test_crash_is_reported():
    result = run("import os\nos._exit(3)")
    assert result["status"] == "crashed"


def test_daemon_spool_roundtrip(tmp_path: Path):
    dirs = daemon.ensure_dirs(tmp_path)
    job_id = "a" * 32
    daemon.write_atomic(
        dirs["jobs"] / f"{job_id}.json", {"cells": ["v = 3"], "targets": [{"key": "k", "name": "v"}]}
    )
    (dirs["jobs"] / "not-an-id.json").write_text("{}")
    claimed = daemon.claim_next(dirs)
    assert claimed is not None and claimed[0] == job_id
    result = daemon.process(*claimed, dirs, FAST, None)
    assert result["status"] == "ok"
    saved = (dirs["results"] / f"{job_id}.json").read_text()
    assert '"data": 3' in saved
    assert daemon.claim_next(dirs) is None
    assert not list(dirs["jobs"].glob("*.json"))  # nieprawidłowa nazwa – usunięta, nie wykonana
    assert not list(dirs["claimed"].glob("*"))


def test_native_extension_from_workdir_is_refused():
    """Haczyk audytu dostaje nazwę pliku wyłącznie przy ładowaniu rozszerzenia (``_imp.create_dynamic``)
    – i właśnie wtedy odmawia, gdy plik nie leży w zaufanych bibliotekach. Kopia ``.so`` NumPy
    w katalogu roboczym ucznia nie zostaje załadowana (w kontenerze dodatkowo ``/tmp`` ma ``noexec``)."""
    result = run(
        "import importlib.machinery, importlib.util, os, numpy\n"
        "lib = os.path.join(os.path.dirname(numpy.__file__), '_core')\n"
        "sos = [os.path.join(lib, f) for f in os.listdir(lib) if f.endswith('.so')]\n"
        "src = min(sos, key=os.path.getsize)  # najmniejsze rozszerzenie – limit rozmiaru pliku\n"
        "dst = os.path.join(os.getcwd(), os.path.basename(src))\n"
        "data = open(src, 'rb').read()\n"
        "open(dst, 'wb').write(data)\n"
        "name = os.path.basename(dst).split('.')[0]\n"
        "loader = importlib.machinery.ExtensionFileLoader(name, dst)\n"
        "importlib.util.module_from_spec(importlib.util.spec_from_file_location(name, dst, loader=loader))"
    )
    assert result["status"] == "ok"
    assert "loading native code from this location is not allowed" in errors(result), errors(result)


# --- nadzorca: limity, parser wyniku, odporność pętli (przegląd QC-01: M1, M2, M3) --------------------


def test_clamp_takes_the_smallest_of_daemon_request_and_ceiling():
    daemon_limits = Limits(wall=40, cpu=30, memory_mb=512)
    assert daemon_limits.clamp({"wall": 100, "cpu": 10}).wall == 40  # prośba nie podnosi limitu nadzorcy
    assert daemon_limits.clamp({"wall": 100, "cpu": 10}).cpu == 10  # ale może go obniżyć
    assert Limits(wall=10_000).clamp({"wall": 5_000}).wall == CEILING["wall"]
    clamped = daemon_limits.clamp({"memory_mb": True, "nproc": 1, "nofile": 1, "file_mb": -5, "x": 1})
    assert clamped == daemon_limits  # bool, ujemne, nieznane i nieżądalne klucze – pominięte


@pytest.mark.parametrize(
    "stdout",
    [
        "no marker at all",
        RESULT_MARKER,
        RESULT_MARKER + "[1, 2]",
        RESULT_MARKER + "{not json",
        RESULT_MARKER + "[" * 200_000 + "]" * 200_000,
        RESULT_MARKER + '{"a": ' * 100_000 + "1" + "}" * 100_000,
        RESULT_MARKER + "1" * 10_000,
    ],
)
def test_child_output_parser_never_raises(stdout):
    assert parse_child_output(stdout) is None


def test_child_output_parser_takes_last_marker():
    assert parse_child_output(f'{RESULT_MARKER}{{"a": 1}}\n{RESULT_MARKER}{{"a": 2}}\n') == {"a": 2}


@pytest.mark.parametrize(
    "payload",
    ["[1, 2]", "not json", "[" * 200_000 + "]" * 200_000, '"text"', b"\xff\xfe"],
)
def test_daemon_survives_garbage_job(tmp_path: Path, payload):
    dirs = daemon.ensure_dirs(tmp_path)
    job_id = "b" * 32
    claimed = dirs["claimed"] / f"{job_id}.json"
    if isinstance(payload, bytes):
        claimed.write_bytes(payload)
    else:
        claimed.write_text(payload, encoding="utf-8")
    result = daemon.process(job_id, claimed, dirs, FAST, None)
    assert result["status"] == "invalid_job"
    assert json.loads((dirs["results"] / f"{job_id}.json").read_text())["status"] == "invalid_job"
    assert not claimed.exists()


def test_daemon_survives_unexpected_error(tmp_path: Path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(daemon, "execute_job", broken)
    dirs = daemon.ensure_dirs(tmp_path)
    job_id = "c" * 32
    claimed = dirs["claimed"] / f"{job_id}.json"
    claimed.write_text('{"cells": []}', encoding="utf-8")
    result = daemon.process(job_id, claimed, dirs, FAST, None)
    assert result["status"] == "error"
    assert (dirs["results"] / f"{job_id}.json").exists()


def test_uid_pool_gives_distinct_random_uids():
    pool = daemon.UidPool(200_000, span=3_000)
    taken = [pool.acquire() for _ in range(2_000)]
    assert len(set(taken)) == len(taken)
    assert all(200_000 <= uid < 203_000 for uid in taken)
    pool.release(taken[0])
    assert taken[0] not in pool._active


def test_cleanup_script_compiles_and_heartbeat_is_written(tmp_path: Path):
    # Skryptu nie uruchamiamy: ``kill(-1)`` jako UID testu zabiłby procesy testu.
    compile(daemon.CLEANUP_SCRIPT, "cleanup", "exec")
    assert "os.kill(-1, signal.SIGKILL)" in daemon.CLEANUP_SCRIPT
    daemon.beat(tmp_path / "hb")
    assert float((tmp_path / "hb").read_text()) > 0


def _compose() -> dict:
    compose_path = Path(__file__).resolve().parents[4] / "docker-compose.yml"
    if not compose_path.exists():
        compose_path = Path("/docker-compose.yml")
    if not compose_path.exists():
        if os.environ.get("CI"):
            pytest.fail("docker-compose.yml nie znaleziony w CI – test zgodności nie może być pominięty")
        pytest.skip("docker-compose.yml poza obrazem testowym")
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))


def test_compose_runner_isolation():
    """Warstwa 1: konfiguracja kontenera piaskownicy w ``docker-compose.yml`` (bez sieci, bez sekretów)."""
    compose = _compose()
    runner = compose["services"]["notebook-runner"]
    assert runner["network_mode"] == "none"
    assert runner["read_only"] is True
    assert runner["cap_drop"] == ["ALL"]
    assert set(runner["cap_add"]) == {"SETUID", "SETGID", "KILL"}
    assert "no-new-privileges:true" in runner["security_opt"]
    assert "env_file" not in runner
    assert not [
        k for k in runner.get("environment", {}) if "SECRET" in k or "PASSWORD" in k or "DATABASE" in k
    ]
    assert any("noexec" in item for item in runner["tmpfs"])
    assert runner.get("pids_limit")
    assert runner.get("mem_limit")
    assert "heartbeat" in " ".join(runner["healthcheck"]["test"])


def test_compose_grading_runs_on_dedicated_worker():
    """Ocena notatników: osobny worker kolejki ``notebooks`` (H2); główny worker nie widzi spoolu."""
    from django.conf import settings

    services = _compose()["services"]
    notebook_worker = services["notebook-worker"]
    assert notebook_worker["profiles"] == ["notebooks"]
    assert "-Q notebooks" in notebook_worker["command"]
    assert "notebook_spool:/spool" in notebook_worker["volumes"]
    assert "notebook_spool:/spool" not in (services["worker"].get("volumes") or [])
    routes = settings.CELERY_TASK_ROUTES
    for task in ("apps.notebooks.tasks.run_notebook", "apps.notebooks.tasks.collect_notebook_run"):
        assert routes[task]["queue"] == "notebooks"
