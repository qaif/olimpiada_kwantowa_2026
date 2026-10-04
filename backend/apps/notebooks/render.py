"""Podgląd notatnika ``.ipynb`` tylko do odczytu – dla personelu (QC-01 § 3.5, decyzja M4).

Personel (koordynator, recenzent, komisja, opiekun…) **nie** otwiera notatników w laboratorium:
tam kod się wykonuje, w domenie serwisu, z sesją oglądającego. Do sprawdzania pracy służy ten
podgląd – nic się nie wykonuje, a wszystko, co notatnik niesie, trafia na stronę jako tekst:

- źródła komórek (kod, markdown, raw) – zwykły tekst, escapowany przez szablon (markdown nie jest
  renderowany: renderer HTML z dowolnym wejściem to kolejna powierzchnia XSS, a do oceny wystarczy
  źródło),
- wyjścia ``text/plain`` i strumienie – tekst bez sekwencji ANSI,
- obrazy ``image/png``/``image/jpeg`` – jako ``data:`` w ``<img>`` (przeglądarka dekoduje bitmapę,
  nic nie wykonuje; polityka CSP serwisu ma ``img-src data:``), po sprawdzeniu, że to czysty base64
  i nagłówek pliku zgadza się z typem,
- wszystko inne (``text/html``, ``application/javascript``, ``image/svg+xml``, widżety, LaTeX) –
  **pomijane** z adnotacją „pominięto wyjście typu …”. SVG też: bywa nośnikiem skryptów, gdy ktoś
  otworzy go poza ``<img>``.
"""

from __future__ import annotations

import base64
import binascii
import re

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
MAX_TEXT = 20_000
MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_SIGNATURES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
TEXT_MIME = "text/plain"


def _text(value) -> str:
    if isinstance(value, list):
        value = "".join(str(part) for part in value)
    text = ANSI.sub("", str(value or ""))
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n…"


def _image(mime: str, value) -> str | None:
    """``data:``-URL obrazu albo ``None`` (zły base64, inny nagłówek, za duży)."""
    raw = "".join(value) if isinstance(value, list) else str(value or "")
    raw = "".join(raw.split())
    if len(raw) > MAX_IMAGE_BYTES * 4 // 3 + 4:
        return None
    try:
        data = base64.b64decode(raw, validate=True)
    except binascii.Error, ValueError:
        return None
    if not data.startswith(IMAGE_SIGNATURES[mime]):
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _output(output: dict) -> list[dict]:
    if not isinstance(output, dict):
        return []
    kind = output.get("output_type")
    if kind == "stream":
        return [{"kind": "text", "stream": output.get("name") == "stderr", "text": _text(output.get("text"))}]
    if kind == "error":
        trace = output.get("traceback") or []
        text = _text("\n".join(str(line) for line in trace)) if trace else ""
        header = f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"
        return [{"kind": "error", "text": text or _text(header)}]
    if kind in ("execute_result", "display_data"):
        data = output.get("data") or {}
        if not isinstance(data, dict):
            return []
        for mime in IMAGE_SIGNATURES:
            if mime in data:
                url = _image(mime, data[mime])
                if url:
                    return [{"kind": "image", "src": url}]
        if TEXT_MIME in data:
            return [{"kind": "text", "stream": False, "text": _text(data[TEXT_MIME])}]
        return [{"kind": "omitted", "mime": ", ".join(sorted(str(m) for m in data)[:5])}]
    return []


def render_cells(notebook: dict) -> list[dict]:
    """Komórki do szablonu: ``{type, source, outputs, count}`` – wyłącznie dane, żadnego HTML."""
    cells = []
    for cell in notebook.get("cells", [])[:500]:
        if not isinstance(cell, dict):
            continue
        cell_type = cell.get("cell_type")
        if cell_type not in ("code", "markdown", "raw"):
            continue
        outputs = []
        if cell_type == "code":
            for output in (cell.get("outputs") or [])[:100]:
                outputs.extend(_output(output))
        count = cell.get("execution_count")
        cells.append(
            {
                "type": cell_type,
                "source": _text(cell.get("source")),
                "outputs": outputs,
                "count": count if isinstance(count, int) else None,
            }
        )
    return cells
