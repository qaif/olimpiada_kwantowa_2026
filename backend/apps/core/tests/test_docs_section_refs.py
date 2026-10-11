"""Odwołania „§ N” w dokumentacji i w kodzie wskazują sekcje, które istnieją.

Kod i dokumenty odsyłają do siebie numerami sekcji („``docs/OPERACJE.md`` § 43.5”, „podręcznik
organizatora § 10m”, „THEME-02 § 2.3”). Numer jest tani w zapisie i drogi w utrzymaniu: kilka gałęzi
dopisuje sekcje równolegle, więc przy scaleniu łatwo o dwa „§ 29” albo o odwołanie do sekcji, która
w międzyczasie dostała inny numer. Ten test czyta wszystkie odwołania i sprawdza, że dokument
docelowy ma sekcję o tym numerze: nagłówek („## 30.4.”) albo – w specyfikacjach zadań, gdzie
„§ 1.2” to drugi punkt listy pod „## 1.” – numerowany punkt najwyższego poziomu.

Do którego dokumentu należy odwołanie, rozstrzyga kolejno:

1. nazwa dokumentu **tuż przed** nim (``OPERACJE``, ``PODRECZNIK-ORGANIZATORA.md``, „podręcznik
   organizatora”, ``DEL-01``, „etapu 1”) – także przez łańcuch („OPERACJE § 43 (oraz § 1.4, § 3.2)”),
2. nazwa **tuż po** nim („§ 43.5 OPERACJE”, „§ 1.5.1 etapu 2”),
3. łańcuch z poprzedniego wiersza, gdy tamten kończy się nazwą albo odwołaniem,
4. w pliku z ``docs/`` – ten sam plik; w kodzie odwołań bez nazwy nie sprawdzamy (nie wiadomo, o
   który plan chodzi).

Nazwa spoza ``docs/`` („regulamin”, „ustawa”, ``README.md``, sam „podręcznik”) też jest nazwą – tyle
że odwołań do niej nie sprawdzamy. Parser jest heurystyką, a nie gramatyką: prawdziwy wyjątek
dopisuje się do ``KNOWN_EXCEPTIONS`` z powodem, zamiast rozluźniać reguły dla wszystkich.

Test potrzebuje całego repozytorium (``docs/`` leży obok ``backend/``) – w kontenerze, który montuje
tylko ``backend/``, jest pomijany; CI uruchamia go z pełnego checkoutu. Bez Django:
``python apps/core/tests/test_docs_section_refs.py`` wypisuje zepsute odwołania.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
DOCS = REPO / "docs"

#: Katalogi z kodem i konfiguracją, w których szukamy odwołań do dokumentów (obok ``docs/``).
CODE_ROOTS = ("backend", "scripts", "deploy", "djcms", "themes", ".github", "caddy")
CODE_SUFFIXES = {".py", ".html", ".txt", ".sh", ".yml", ".yaml", ".js", ".css", ".md", ".example"}
SKIP_DIRS = {".venv", "node_modules", "__pycache__", "staticfiles", "media", "dist", "vendor", "locale"}

#: Nagłówek sekcji: „## 30.4. Menu …”, „### 2.1 Okno …”, „## 10b. Delegacje …”, „#### 1.1.1. …”.
HEADING = re.compile(r"^#{1,6}\s+§?\s*(\d+[a-z]?(?:\.\d+[a-z]?)*)\.?(?:\s|$)")
#: Punkt listy najwyższego poziomu („1. **Formularze:** …”) – w specyfikacjach to „§ <sekcja>.<punkt>”.
LIST_ITEM = re.compile(r"^(\d+)\.\s")
#: Wiersz tabeli z numerem w pierwszej kolumnie („| 3.2.8 | …” w ``SECURITY_CHECKLIST.md``).
TABLE_ROW = re.compile(r"^\|\s*(\d+(?:\.\d+)+[a-z]?)\s*\|")
NUM = r"\d+[a-z]?(?:\.\d+[a-z]?)*"
#: „§ 30.4”, „§ 30.4–30.7” (zakres bez spacji), „§ 4.1, 7.2a, 9.1” (kolejne numery należą do tego §).
REF = re.compile(
    rf"§§?\s*(?P<first>{NUM})(?:[–-](?P<last>{NUM}))?"
    # Kolejne numery tylko z kropką albo literą („7.2a”, „10a”) – „§ 13, 120 s” to nie dwa odwołania.
    rf"(?P<more>(?:,\s*\d+(?:[a-z]|(?:\.\d+[a-z]?)+)(?=[,;)\s]|$)(?!\s*(?:ust|pkt|r\.|%)))*)"
)

#: Odwołanie należy do nazwy, jeśli dzieli je od niej (albo od poprzedniego odwołania z łańcucha)
#: najwyżej tyle znaków przed / po.
CHAIN_GAP = 30
POSTFIX_GAP = 12

#: Dokumenty z ``docs/``, których nazwa jest zwykłym słowem („API”, „TESTY”, „PROJEKT”) – liczą
#: się wyłącznie zapisane jako plik (``API.md``). Bez rozszerzenia wolno pisać: OPERACJE, podręczniki,
#: plany etapów i kody zadań (``DEL-01``, ``THEME-02``).
TASK_CODE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*-\d+[A-Z]?$")
BARE_OK = re.compile(rf"^(?:OPERACJE|PODRECZNIK-.+|SECURITY_CHECKLIST)$|{TASK_CODE.pattern}")

#: Polskie nazwy dokumentów używane w tekście zamiast nazwy pliku.
ALIASES = (
    (r"podręcznik\w*\s+organizatora", "PODRECZNIK-ORGANIZATORA"),
    (r"podręcznik\w*\s+uczestnika", "PODRECZNIK-UCZESTNIKA"),
    (r"podręcznik\w*\s+recenzenta", "PODRECZNIK-RECENZENTA"),
    (r"podręcznik\w*\s+administratora", "PODRECZNIK-ADMINISTRATORA"),
    (r"przewodnik\w*\s+opiekuna(?:\s+drużyny)?", "PODRECZNIK-OPIEKUNA-DRUZYNY"),
    # „etap 1 § 3.7” albo „§ 3.8 etapu 1” – samo „etapu 2 (§ 8)” to zwykłe zdanie, nie nazwa planu.
    (r"\betap\w*\s+1(?=\s*§)|(?<=\d\s)etap\w*\s+1\b", "UNIWERSALNY-ETAP-1"),
    (r"\betap\w*\s+2(?=\s*§)|(?<=\d\s)etap\w*\s+2\b", "UNIWERSALNY-ETAP-2"),
)
#: Nazwy spoza ``docs/`` (albo niejednoznaczne) – odwołań do nich nie sprawdzamy.
FOREIGN = (
    r"\b(?:regulamin\w*|statut\w*|ustaw[aiyeę]|rozporządzeni\w*|RODO|art\.|podręcznik\w*|"
    r"tamt\w+\s+dokument\w*|v\d)(?!\w)|[\w./-]+\.(?:md|sh|py)\b"
)

#: Odwołania, których heurystyka nie rozstrzyga: (plik, wiersz z odwołaniem zawiera, numer) → powód.
KNOWN_EXCEPTIONS: dict[tuple[str, str, str], str] = {
    (
        "docs/OPERACJE.md",
        "§ 0.5",
        "0.5",
    ): "§ 8 opisuje listę kontrolną § 0.5 planu etapu 2 (nazwa we wstępie § 8)",
    ("docs/OPERACJE.md", "listy § 0.5", "0.5"): "jw.",
    (
        "backend/apps/results/services.py",
        "§ 1.0 (a) etapu 2",
        "1.0",
    ): "„§ 1.0 (a) etapu 2” – nazwa po nawiasie",
    ("docs/tasks/DJ-02.md", "| § ", "*"): "tabela porównania z DJ-01 – lewa kolumna to numery DJ-01",
    (
        "docs/tasks/LOG-01.md",
        "§ 1.5.2 –",
        "1.5.2",
    ): "flaga z katalogu etapu 2 (``UNIWERSALNY-ETAP-2.md`` § 1.5.2)",
}


@dataclass(frozen=True)
class Ref:
    path: str
    line: int
    doc: str
    number: str
    text: str


_ASCII = str.maketrans({"Ę": "E", "Ż": "Z"})


def _stem(name: str) -> str:
    """``PODRĘCZNIK-UCZESTNIKA`` i ``PODRECZNIK-UCZESTNIKA`` to ten sam dokument."""
    return name.upper().translate(_ASCII)


def doc_sections(text: str) -> set[str]:
    """Numery sekcji dokumentu: nagłówki i punkty list najwyższego poziomu pod nagłówkiem."""
    numbers: set[str] = set()
    current: str | None = None
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if m := HEADING.match(line):
            current = m.group(1)
            numbers.add(current)
        elif current and (m := LIST_ITEM.match(line)):
            numbers.add(f"{current}.{m.group(1)}")
        elif m := TABLE_ROW.match(line):
            numbers.add(m.group(1))
    return numbers


def numbered_docs() -> dict[str, set[str]]:
    """Dokument (nazwa pliku bez ``.md``, wielkimi literami) → numery jego sekcji."""
    return {
        _stem(p.stem): doc_sections(p.read_text(encoding="utf-8"))
        for p in sorted(DOCS.glob("*.md")) + sorted((DOCS / "tasks").glob("*.md"))
    }


def section_exists(numbers: set[str], number: str) -> bool:
    """Numer jest sekcją albo prefiksem sekcji („§ 0” istnieje, gdy są „0.1”, „0.2”)."""
    return number in numbers or any(n.startswith(number + ".") for n in numbers)


@functools.cache
def _doc_name_pattern(names: frozenset[str]) -> re.Pattern[str]:
    """Wzorzec nazwy dokumentu – składany raz na zbiór nazw, a nie raz na wiersz (ok. 200 nazw)."""
    stems = "|".join(map(re.escape, sorted(names, key=len, reverse=True)))
    return re.compile(rf"(?<![\w-])(?:docs/)?(?:tasks/)?({stems})(\.md)?(?![\w-])")


def _doc_mentions(line: str, docs: dict[str, set[str]]) -> list[tuple[int, int, str | None]]:
    """Nazwy dokumentów w wierszu: (początek, koniec, dokument albo ``None`` = spoza ``docs/``)."""
    found: list[tuple[int, int, str | None]] = []
    for m in _doc_name_pattern(frozenset(docs)).finditer(line.translate(_ASCII)):
        bare_code = not m.group(2) and TASK_CODE.match(m.group(1))
        # Kod zadania bez „.md” jest nazwą dokumentu tylko tuż przed „§” („THEME-02 § 2.3”); w „(QC-02,
        # § 40.7)” albo „od OPS-04 – § 48” to etykieta zmiany, a § należy do dokumentu wokół.
        if bare_code and not line[m.end() : m.end() + 4].lstrip(" `*").startswith("§"):
            continue
        if m.group(2) or BARE_OK.match(m.group(1)):
            found.append((m.start(), m.end(), m.group(1)))
    for pattern, doc in ALIASES:
        found += [(m.start(), m.end(), doc) for m in re.finditer(pattern, line, re.IGNORECASE)]
    for m in re.finditer(FOREIGN, line, re.IGNORECASE):
        if not any(s < m.end() and m.start() < e for s, e, _ in found):
            found.append((m.start(), m.end(), None))
    return sorted(found)


def extract_refs(path: Path, docs: dict[str, set[str]], text: str | None = None) -> list[Ref]:
    """Wszystkie odwołania „§ N” w pliku, każde z dokumentem, do którego należy."""
    rel = path.relative_to(REPO).as_posix()
    own = _stem(path.stem) if rel.startswith("docs/") else None
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    # Bez „§” nie ma odwołań – tak wygląda zdecydowana większość plików kodu, a szukanie nazw
    # dokumentów w każdym ich wierszu kosztowało w CI 30–40 s na przebieg.
    if "§" not in text:
        return []
    refs: list[Ref] = []
    carry: tuple[str | None] | None = None  # łańcuch, który doszedł do końca poprzedniego wiersza
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        # Nazwy dokumentów w wierszu liczą się tylko dla odwołań tego wiersza i dla łańcucha
        # przenoszonego do **następnego** – a ten działa wyłącznie wtedy, gdy następny ma „§”.
        # Wiersz bez „§” przed wierszem bez „§” nie zmienia więc wyniku; pomijamy go w całości.
        if "§" not in line and (lineno == len(lines) or "§" not in lines[lineno]):
            carry = None
            continue
        mentions = _doc_mentions(line, docs)
        # (dokument, koniec, ustępuje) ostatniego ogniwa łańcucha. Łańcuch z poprzedniego wiersza
        # i nazwa, która była dopiskiem poprzedniego odwołania („§ 5.3 DJ-01.md; … § 4.4 … DJ-02.md”),
        # ustępują nazwie stojącej tuż po odwołaniu.
        chain: tuple[str | None, int, bool] | None = (carry[0], 0, True) if carry else None
        postfixes: set[int] = set()
        refs_here = list(REF.finditer(line))
        # Nazwa, za którą od razu stoi własne „§” („podręcznik uczestnika § 7”), nie jest dopiskiem.
        leading = {s for s, e, _ in mentions if any(0 <= r.start() - e <= 3 for r in refs_here)}
        for m in refs_here:
            before = [(doc, e, s in postfixes) for s, e, doc in mentions if e <= m.start()]
            anchor = chain
            if before and (anchor is None or before[-1][1] >= anchor[1]):
                anchor = before[-1]
            after = [
                (s, doc)
                for s, _, doc in mentions
                if s >= m.end() and s - m.end() <= POSTFIX_GAP and s not in leading
            ]
            if after and (anchor is None or anchor[2] or m.start() - anchor[1] > CHAIN_GAP):
                postfixes.add(after[0][0])
                doc = after[0][1]
            elif anchor is not None and m.start() - anchor[1] <= CHAIN_GAP:
                doc = anchor[0]
            else:
                doc = own
            chain = (doc, m.end(), False)
            if doc is None or not docs.get(doc):
                continue
            numbers = [m.group("first")]
            if m.group("last"):
                numbers.append(m.group("last"))
            numbers += re.findall(NUM, m.group("more") or "")
            refs += [Ref(rel, lineno, doc, n, line.strip()) for n in numbers]
        # Łańcuch przechodzi do następnego wiersza tylko wtedy, gdy ten wiersz kończy się nazwą
        # dokumentu albo odwołaniem („… przewodnik opiekuna⏎drużyny § 5a”, „(§ 10e, problem⏎…”).
        tails = [(e, doc) for _, e, doc in mentions] + ([(chain[1], chain[0])] if chain and chain[1] else [])
        last = max(tails, key=lambda t: t[0], default=None)
        ends_there = last is not None and not line[last[0] :].strip(" `*,;:–-")
        open_paren = last is not None and line.count("(") > line.count(")")
        carry = (last[1],) if ends_there or open_paren else None
    return refs


def _is_known_exception(ref: Ref) -> bool:
    return any(
        ref.path == path and fragment in ref.text and number in (ref.number, "*")
        for path, fragment, number in KNOWN_EXCEPTIONS
    )


def files_to_scan() -> list[Path]:
    """Dokumentacja i kod – bez tego pliku, którego przykłady celowo nie wskazują prawdziwych sekcji."""
    this = Path(__file__).resolve()
    paths = sorted(DOCS.rglob("*.md"))
    for root in CODE_ROOTS:
        base = REPO / root
        if base.is_dir():
            paths += sorted(
                p
                for p in base.rglob("*")
                if p.is_file() and p.suffix in CODE_SUFFIXES and not SKIP_DIRS.intersection(p.parts)
            )
    return [p for p in paths if p.resolve() != this]


def broken_refs() -> list[Ref]:
    docs = numbered_docs()
    return [
        ref
        for path in files_to_scan()
        for ref in extract_refs(path, docs)
        if not section_exists(docs[ref.doc], ref.number) and not _is_known_exception(ref)
    ]


needs_repo = pytest.mark.skipif(not DOCS.is_dir(), reason="brak docs/ – kontener montuje tylko backend/")


@needs_repo
def test_every_section_reference_points_to_existing_section():
    broken = broken_refs()
    assert not broken, "Odwołania do nieistniejących sekcji:\n" + "\n".join(
        f"{r.path}:{r.line}: {r.doc} § {r.number}  ←  {r.text[:120]}" for r in broken
    )


@needs_repo
@pytest.mark.parametrize(
    "line, expected",
    [
        (
            "opis: `docs/OPERACJE.md` § 43 (oraz § 1.4, § 3.2).",
            [("OPERACJE", "43"), ("OPERACJE", "1.4"), ("OPERACJE", "3.2")],
        ),
        (
            "PODRĘCZNIK-ORGANIZATORA § 4.1, 7.2a, 9.1",
            [("PODRECZNIK-ORGANIZATORA", n) for n in ("4.1", "7.2a", "9.1")],
        ),
        (
            "`OPERACJE.md` § 31, podręcznik organizatora § 10d, przewodnik opiekuna § 7a.",
            [("OPERACJE", "31"), ("PODRECZNIK-ORGANIZATORA", "10d"), ("PODRECZNIK-OPIEKUNA-DRUZYNY", "7a")],
        ),
        ("OPERACJE § 30.4–30.7", [("OPERACJE", "30.4"), ("OPERACJE", "30.7")]),
        ("Widełki są zmiennymi środowiskowymi (§ 43.5 OPERACJE).", [("OPERACJE", "43.5")]),
        ("regulamin § 1 ust. 4, ustawa § 2, klucz API § 17.6", []),
    ],
)
def test_parser_attributes_references_to_documents(line, expected):
    """Heurystyka przypisania – na przykładach z prawdziwych dokumentów (w pliku spoza ``docs/``)."""
    got = [(r.doc, r.number) for r in extract_refs(REPO / "backend" / "probe.py", numbered_docs(), text=line)]
    assert got == expected


def test_list_items_under_heading_are_sections():
    text = "## 1. Zakres\n\n1. **Formularze:** …\n   2. wcięte – nie\n2. **Wyświetlanie:** …\n## 2. Testy\n"
    assert doc_sections(text) == {"1", "1.1", "1.2", "2"}


@needs_repo
def test_operacje_top_level_sections_are_in_ascending_order():
    """Sekcje OPERACJE stoją fizycznie po kolei i bez duplikatów.

    Numerów się nie przenumerowuje (kod odsyła do „OPERACJE § N”), więc nowa sekcja dostaje
    następny wolny numer i staje na swoim miejscu. Luki są dozwolone – to numery zarezerwowane
    przez gałęzie w toku.
    """
    numbers = []
    fence = False
    for line in (DOCS / "OPERACJE.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fence = not fence
        elif not fence and (m := re.match(r"^## (\d+)\. ", line)):
            numbers.append(int(m.group(1)))
    assert len(numbers) == len(set(numbers)), "zdublowany numer sekcji w OPERACJE"
    assert numbers == sorted(numbers), numbers


if __name__ == "__main__":  # pragma: no cover – szybkie sprawdzenie bez Django
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    for r in broken_refs():
        print(f"{r.path}:{r.line}: {r.doc} § {r.number}  <-  {r.text[:140]}")
