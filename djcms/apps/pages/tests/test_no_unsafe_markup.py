"""Reguła 4 z § 7 docs/tasks/DJ-01.md: treść redakcyjna tylko przez sanityzator, **zero** ``|safe``.

Szablony djcms nie mogą wyłączać autoescape'u, a kod – oznaczać napisów jako bezpiecznych.
Tekst formatowany przechodzi wyłącznie przez djangocms-text (``TextPlugin``/``HTMLField``
z sanityzacją ``nh3``), a osadzenia budujemy z wzorca. Wyjątków: brak. Test obejmuje także
szablony i moduły, które dopiero powstaną (DJ-01d–g) – patrzy na drzewo katalogów, nie na listę plików.
"""

import re
from pathlib import Path

DJCMS_DIR = Path(__file__).resolve().parents[3]

TEMPLATE_PATTERNS = (
    re.compile(r"\|\s*safe\b"),
    re.compile(r"{%\s*autoescape\s+off"),
    re.compile(r"\bmark_safe\b"),
    re.compile(r"\|\s*safeseq\b"),
)
PYTHON_PATTERNS = (re.compile(r"\bmark_safe\b"), re.compile(r"\bSafeString\b"), re.compile(r"\bSafeData\b"))


def _template_files():
    roots = [DJCMS_DIR / "templates", *(DJCMS_DIR / "apps").glob("*/templates")]
    for root in roots:
        yield from (p for p in root.rglob("*") if p.is_file() and p.suffix in {".html", ".txt", ".xml"})


def _python_files():
    for path in (DJCMS_DIR / "apps").rglob("*.py"):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        yield path
    yield from (DJCMS_DIR / "config").rglob("*.py")


def test_templates_exist_so_the_scan_is_not_vacuous():
    assert any(_template_files())


def test_no_unsafe_markup_in_templates():
    offenders = [
        f"{path.relative_to(DJCMS_DIR)}:{lineno}: {line.strip()}"
        for path in _template_files()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(pattern.search(line) for pattern in TEMPLATE_PATTERNS)
    ]
    assert offenders == []


def test_no_mark_safe_in_python():
    offenders = [
        f"{path.relative_to(DJCMS_DIR)}:{lineno}: {line.strip()}"
        for path in _python_files()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(pattern.search(line) for pattern in PYTHON_PATTERNS)
    ]
    assert offenders == []
