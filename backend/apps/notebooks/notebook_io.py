"""Notatniki ``.ipynb``: odczyt komórek kodu, notatnik startowy i komórka testów widocznych.

Notatnik czytamy jako JSON, a nie przez ``nbformat.read`` z walidacją schematu: plik ucznia
przeszedł już walidację wysyłki (``apps.submissions.validators``), a do oceny potrzebne są
wyłącznie źródła komórek kodu. Ten sam powód co w ``apps.grading.code_view``.
"""

from __future__ import annotations

import json
import pprint

from qclab import grader

#: Znacznik komórki testów widocznych (``metadata.tags``). Serwer ją dokleja do notatnika startowego
#: i ją pomija przy ocenie – testy widoczne liczą się w przeglądarce, a na serwerze liczy się
#: komplet testów osobno.
VISIBLE_TESTS_TAG = "qc-visible-tests"
MAX_NOTEBOOK_BYTES = 1024 * 1024
MAX_CELLS = 500
MAX_CELL_CHARS = 200_000


class NotebookError(ValueError):
    pass


def _source(cell: dict) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        source = "".join(str(part) for part in source)
    return str(source)


def load(data: bytes | str | dict) -> dict:
    """Słownik notatnika z bajtów/tekstu/słownika; ``NotebookError`` przy nie-notatniku."""
    if isinstance(data, dict):
        notebook = data
    else:
        if isinstance(data, bytes):
            if len(data) > MAX_NOTEBOOK_BYTES * 20:
                raise NotebookError("notebook too large")
            data = data.decode("utf-8", errors="replace")
        try:
            notebook = json.loads(data)
        except json.JSONDecodeError as exc:
            raise NotebookError("not a JSON notebook") from exc
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        raise NotebookError("not a Jupyter notebook (no cell list)")
    return notebook


def code_cells(notebook: dict) -> list[str]:
    """Źródła komórek kodu w kolejności – bez komórki testów widocznych."""
    cells = []
    for cell in notebook.get("cells", [])[:MAX_CELLS]:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        tags = (cell.get("metadata") or {}).get("tags") or []
        if isinstance(tags, list) and VISIBLE_TESTS_TAG in tags:
            continue
        cells.append(_source(cell)[:MAX_CELL_CHARS])
    return cells


def _cell(cell_type: str, source: str, tags: list[str] | None = None) -> dict:
    cell: dict = {"cell_type": cell_type, "metadata": {"tags": tags} if tags else {}, "source": source}
    if cell_type == "code":
        cell.update({"execution_count": None, "outputs": []})
    return cell


TEMPLATE_TEXT = {
    "pl": {
        "intro": "# {title}\n\nNotatnik działa w przeglądarce (JupyterLite, Python w WebAssembly). "
        "Moduł `qiskit` to zgodny podzbiór Qiskita (symulator `qclab`) – pełna lista w podręczniku "
        "uczestnika.\n\n**Oddanie pracy:** *File → Download*, a potem wyślij plik `.ipynb` "
        "w karcie zadania na platformie.",
        "code": "from qiskit import QuantumCircuit\nfrom qiskit.quantum_info import Statevector\n"
        "import numpy as np\n\n# Twoje rozwiązanie:\n",
        "tests": "## Testy przykładowe\n\nUruchom komórkę poniżej, żeby sprawdzić rozwiązanie na "
        "testach przykładowych. Ocenę liczą testy ukryte na serwerze.",
    },
    "en": {
        "intro": "# {title}\n\nThis notebook runs in your browser (JupyterLite, Python in WebAssembly). "
        "The `qiskit` module is a compatible subset of Qiskit (the `qclab` simulator) – see the "
        "participant guide for the full list.\n\n**Submitting:** *File → Download*, then upload "
        "the `.ipynb` file in the task card on the platform.",
        "code": "from qiskit import QuantumCircuit\nfrom qiskit.quantum_info import Statevector\n"
        "import numpy as np\n\n# Your solution:\n",
        "tests": "## Sample tests\n\nRun the cell below to check your solution against the sample "
        "tests. The score is computed by hidden tests on the server.",
    },
}


def default_starter(title: str, language: str) -> dict:
    text = TEMPLATE_TEXT.get(language, TEMPLATE_TEXT["en"])
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"name": "python", "display_name": "Python (Pyodide)", "language": "python"},
            "language_info": {"name": "python"},
        },
        "cells": [
            _cell("markdown", text["intro"].format(title=title)),
            _cell("code", text["code"]),
        ],
    }


def visible_tests_cell(tests: list[dict], language: str) -> dict:
    """Komórka z testami widocznymi – literał Pythona, uruchamiana przez ``qclab.grader.check``.

    ``pformat``, a nie ``json.dumps``: JSON-owe ``true``/``null`` nie są Pythonem, a ``repr`` struktury
    z samych napisów, liczb, list i słowników jest poprawnym literałem i czyta się go jak kod.
    """
    payload = pprint.pformat(grader.public_view(tests), width=100, sort_dicts=False)
    source = (
        "from qclab.grader import check\n\n"
        f"TESTS = {payload}\n\n"
        # Średnik: IPython nie wypisze słownika wyniku pod tabelką (tabelkę drukuje ``check``).
        f"check(TESTS, globals(), language={language!r});\n"
    )
    return _cell("code", source, tags=[VISIBLE_TESTS_TAG])


def starter_for_participant(
    starter: dict | None, title: str, language: str, visible_tests: list[dict]
) -> dict:
    """Notatnik startowy, który dostaje uczeń: wzór koordynatora (albo generowany) + testy widoczne.

    Komórki z tagiem testów widocznych ze wzoru są usuwane i zastępowane świeżymi – zmiana testów
    w panelu działa od razu, bez ponownego wgrywania notatnika. Wyjścia komórek wzoru są czyszczone
    (koordynator mógł zostawić w nich wyniki rozwiązania).
    """
    notebook = json.loads(json.dumps(starter)) if starter else default_starter(title, language)
    cells = []
    for cell in notebook.get("cells", []):
        tags = (cell.get("metadata") or {}).get("tags") or []
        if VISIBLE_TESTS_TAG in tags:
            continue
        if cell.get("cell_type") == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
        cells.append(cell)
    if visible_tests:
        text = TEMPLATE_TEXT.get(language, TEMPLATE_TEXT["en"])
        cells.append(_cell("markdown", text["tests"]))
        cells.append(visible_tests_cell(visible_tests, language))
    notebook["cells"] = cells
    notebook.setdefault("nbformat", 4)
    notebook.setdefault("nbformat_minor", 5)
    notebook.setdefault("metadata", {})
    return notebook


def validate_uploaded(data: bytes) -> dict:
    """Notatnik wgrany przez koordynatora (startowy albo wzorcowy): rozmiar i struktura."""
    if len(data) > MAX_NOTEBOOK_BYTES:
        raise NotebookError("too_large")
    notebook = load(data)
    if notebook.get("nbformat") != 4:
        raise NotebookError("nbformat")
    return notebook
