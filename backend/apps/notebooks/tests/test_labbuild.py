"""Budowa JupyterLite: przycięcie blokady Pyodide, koło qclab, skrypty inline, zgodność CSP z Caddyfile."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

from apps.notebooks.labbuild import build
from apps.web.middleware import NOTEBOOK_LAB_HEADERS, build_notebook_lab_policy

LOCK = {
    "info": {"python": "3.14.2"},
    "packages": {
        "ipython": {"file_name": "ipython.whl", "depends": ["traitlets", "prompt_toolkit"]},
        "traitlets": {"file_name": "traitlets.whl", "depends": []},
        "prompt-toolkit": {"file_name": "pt.whl", "depends": ["wcwidth"]},
        "wcwidth": {"file_name": "wcwidth.whl", "depends": []},
        "numpy": {"file_name": "numpy.whl", "depends": []},
        "scipy": {"file_name": "scipy.whl", "depends": ["numpy"]},
    },
}


def test_closure_follows_dependencies_with_normalised_names():
    assert build.closure(LOCK["packages"], ["ipython"]) == {
        "ipython",
        "traitlets",
        "prompt-toolkit",
        "wcwidth",
    }
    with pytest.raises(KeyError):
        build.closure(LOCK["packages"], ["qiskit"])


def test_prune_lock_keeps_only_shipped_packages():
    pruned = build.prune_lock(LOCK, {"numpy"})
    assert list(pruned["packages"]) == ["numpy"]
    assert pruned["info"] == LOCK["info"]
    assert "scipy" in LOCK["packages"]  # oryginał nietknięty


def test_pinned_pyodide_config():
    config = build.CONFIG
    assert re.fullmatch(r"[0-9a-f]{64}", config["core_sha256"])
    assert config["version"] in config["core_url"] and config["version"] in config["packages_base_url"]
    assert "numpy" in config["packages"] and "ipython" in config["packages"]
    requirements = (build.HERE / "requirements.txt").read_text(encoding="utf-8")
    pinned = re.findall(r"^([a-z0-9-]+)==", requirements, re.M)
    assert {"jupyterlite-core", "jupyterlite-pyodide-kernel", "jupyterlab-open-url-parameter"} <= set(pinned)
    assert requirements.count("--hash=sha256:") >= len(pinned)


def test_wheel_is_deterministic_and_importable(tmp_path):
    first = build.build_wheel(tmp_path / "a", "0.1.0")
    second = build.build_wheel(tmp_path / "b", "0.1.0")
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert (
            "qclab/__init__.py" in names
            and "qiskit/__init__.py" in names
            and "qiskit_aer/__init__.py" in names
        )
        assert not [n for n in names if n.startswith(("qclab/_compat", "qclab/_server_stubs", "piplite"))]
        record = archive.read("qclab-0.1.0.dist-info/RECORD").decode()
        assert "qclab/grader.py,sha256=" in record
    entry = build.lock_entry(first, "0.1.0")
    assert entry["imports"] == ["qclab", "qiskit", "qiskit_aer"] and entry["depends"] == ["numpy"]
    # Koło działa jak pakiet: ``import qiskit`` daje moduł zgodności qclab.
    sys.path.insert(0, str(first))
    saved = {k: v for k, v in sys.modules.items() if k == "qiskit" or k.startswith("qiskit.")}
    for name in saved:
        del sys.modules[name]
    try:
        import importlib

        module = importlib.import_module("qiskit")
        assert "qclab" in module.__version__
    finally:
        sys.path.remove(str(first))
        for name in [k for k in sys.modules if k == "qiskit" or k.startswith("qiskit.")]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_version_read_without_import():
    from qclab import __version__

    assert build.qclab_version_from_source() == __version__


def test_inline_scripts_are_externalised_but_json_config_stays():
    html = (
        '<script id="jupyter-config-data" type="application/json">{"a": 1}</script>\n'
        "<script>\n  (async function () { await import('../config-utils.js'); })();\n</script>\n"
        '<script src="./x.js"></script>'
    )
    out, scripts = build.externalize_inline_scripts(html)
    assert '{"a": 1}' in out and '<script src="./x.js"></script>' in out
    [(name, body)] = scripts.items()
    assert f'<script src="./{name}"></script>' in out and "config-utils.js" in body
    assert "import('../config-utils.js')" not in out


def test_postprocess_rewrites_every_index(tmp_path):
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "index.html").write_text("<script>var a=1;</script>", encoding="utf-8")
    (tmp_path / "rspack.config.js").write_text("x", encoding="utf-8")
    build.postprocess(tmp_path)
    assert "<script>" not in (tmp_path / "lab" / "index.html").read_text(encoding="utf-8")
    assert len(list((tmp_path / "lab").glob("boot-*.js"))) == 1
    assert not (tmp_path / "rspack.config.js").exists()


def test_caddyfile_sends_the_same_lab_policy():
    """W produkcji pliki laboratorium podaje Caddy – jego nagłówek ma być co do znaku polityką z Django."""
    caddyfile = Path(build.BACKEND).parent / "deploy" / "Caddyfile"
    if not caddyfile.exists():
        caddyfile = Path("/deploy/Caddyfile")
    if not caddyfile.exists():
        pytest.skip("deploy/Caddyfile poza zasięgiem testu (obraz z samym backendem)")
    text = caddyfile.read_text(encoding="utf-8")
    block = re.search(r"^\(notebook_lab\) \{\n(.*?)\n\}", text, re.S | re.M)
    assert block, "brak fragmentu (notebook_lab) w deploy/Caddyfile"
    assert "@notebook_lab path /static/notebook-lab/*" in block.group(1)
    headers = dict(re.findall(r'^\s+([A-Za-z-]+) "([^"]+)"$', block.group(1), re.M))
    # Caddy wstawia origin żądania w miejsce ``{scheme}://{hostport}`` – po podstawieniu ma wyjść
    # dokładnie to, co składa Django dla tego samego originu.
    origin = "https://olimpiada.example"
    caddy_policy = headers.pop("Content-Security-Policy").replace("{scheme}://{hostport}", origin)
    assert caddy_policy == build_notebook_lab_policy(origin)
    assert headers == NOTEBOOK_LAB_HEADERS
    assert text.count("import notebook_lab") >= 1


def test_lite_config_keeps_everything_local():
    config = json.loads((build.HERE / "lite" / "jupyter-lite.json").read_text(encoding="utf-8"))
    kernel = config["jupyter-config-data"]["litePluginSettings"][
        "@jupyterlite/pyodide-kernel-extension:kernel"
    ]
    assert kernel["disablePyPIFallback"] is True
    assert not kernel["pyodideUrl"].startswith("http")
    assert config["jupyter-config-data"]["contentsStorageName"]
