"""Jedno wejście do zestawu zgód – test z § 5.3 (etap 2, T11).

Zestaw zgód ma **jedno** źródło: ``apps.accounts.consents.consent_set``. Dwa źródła znaczyłyby, że
formularz rejestracji, ``GET /api/auth/consents/``, panel uczestnika i eksport mogą pokazać cztery
różne zestawy – a przy zgodzie różnica między tym, o co poproszono, a tym, co zapisano, jest
różnicą między dowodem a jego brakiem.

Przedmiotem jest nazwa ``CONSENTS`` – alias zostawiony na sezon przy zmianie nazwy stałej na
``DEFAULT_CONSENTS`` (§ 1.1.2). Alias żyje po to, żeby testy niezmienności mogły zaimportować
jedno i drugie i porównać; kod produkcyjny ma go **nie** czytać, bo czytanie stałej jest odczytem
„zestawu domyślnego”, a nie „zestawu tego konkursu”.

Szukamy drzewem składni, a nie greppem po napisach, i to jest różnica praktyczna: nazwa stałej
stoi w kilkunastu komentarzach i docstringach (``apps/tenancy/models.py``, ``templates_catalog.py``,
opis złotego testu), a zdanie „kod tego nie czyta” nie jest zdaniem o komentarzach. Grep kazałby
albo wyliczyć te pliki jako wyjątki – czyli wpisać na listę dozwolonych moduły, które niczego nie
czytają – albo przestać o nich pisać.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
APPS_ROOT = BACKEND_ROOT / "apps"

#: Nazwa aliasu. Stała, a nie literał w trzech miejscach testu.
NAME = "CONSENTS"

#: Moduły, którym wolno czytać stałą, i powód każdego z nich.
ALLOWED = {
    # Dom stałej: tu powstaje alias i tu stoi jedyne wejście, które go zwraca.
    "apps/accounts/consents.py",
    # Testy niezmienności **porównują** stałą z bazą – bez importu obu nie miałyby czego porównać.
    "apps/tenancy/tests/test_invariants.py",
    "apps/accounts/tests/test_consent_definitions.py",
    "apps/accounts/tests/test_consents.py",
}


def python_sources():
    """Pliki ``.py`` aplikacji – bez migracji i bez śmieci interpretera.

    Migracje są wyłączone świadomie: ``accounts.0024`` importuje ``DEFAULT_CONSENTS`` (dane, nie
    zachowanie – § 1.1.2) i jest wpisem **historycznym**, którego nie wolno zmieniać po wdrożeniu.
    Test zabraniający czegoś, czego nie da się poprawić, byłby alarmem bez adresata.
    """
    for path in sorted(APPS_ROOT.rglob("*.py")):
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if "/migrations/" in relative or "__pycache__" in relative:
            continue
        yield relative, path


def reads_the_constant(path: Path) -> bool:
    """Czy moduł **czyta** nazwę ``CONSENTS``: importem, przez atrybut albo wprost.

    Trzy postacie, bo tyle ich jest: ``from …consents import CONSENTS``, ``consents.CONSENTS``
    i gołe ``CONSENTS`` (w module, który już je zaimportował, oraz w samej stałej przy jej
    przypisaniu). Komentarz i docstring nie są żadną z nich – drzewo składni ich nie widzi.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == NAME for alias in node.names):
            return True
        if isinstance(node, ast.Attribute) and node.attr == NAME:
            return True
        if isinstance(node, ast.Name) and node.id == NAME:
            return True
    return False


def test_no_module_imports_consents_directly():
    """Poza modułem zgód i testami niezmienności nikt nie sięga po stałą ``CONSENTS``."""
    offenders = [
        relative
        for relative, path in python_sources()
        if relative not in ALLOWED and reads_the_constant(path)
    ]

    assert offenders == [], (
        "Zestaw zgód czyta się przez apps.accounts.consents.consent_set(competition), "
        f"a nie ze stałej CONSENTS. Do poprawy: {offenders}"
    )


def test_the_allow_list_has_no_stale_entries():
    """Każdy wyjątek musi być prawdziwy – lista, która przeżyła swój powód, przestaje coś znaczyć.

    Lista wyjątków jest jedynym miejscem, w którym wolno napisać „ten moduł czyta stałą”. Wpis,
    który przestał być prawdą, zamienia ją w listę życzeń: przy następnej podmianie nikt nie
    będzie wiedział, który wiersz wolno wykreślić, a który jeszcze coś trzyma.
    """
    stale = [relative for relative in sorted(ALLOWED) if not reads_the_constant(BACKEND_ROOT / relative)]

    assert stale == [], f"Te pliki już nie czytają CONSENTS – wykreślcie je z ALLOWED: {stale}"
