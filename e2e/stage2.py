"""Wspólna obsługa dwóch kontroli etapu 2: adresy obu konkursów, logowanie, licznik usterek.

Moduł jest pomocniczy i nie uruchamia niczego sam — dokładnie tak, jak ``timeline.py`` dla
scenariusza pełnego cyklu. Stoi osobno, bo te same trzy rzeczy potrzebne są obu kontrolom
(``check_stage2_screens.py``, ``check_stage2_isolation.py``), a trzecia kopia adresu konkursu
byłaby trzecią okazją, żeby jedna kontrola chodziła po innym serwisie niż druga.

**Dlaczego prefiks ścieżki, a nie druga domena.** Konkurs drugi stoi w trybie ``PATH``
(``docs/UNIWERSALNY-ETAP-1.md`` § 2.3): żądanie idzie na **ten sam** host, co Konkurs #1, a
o konkursie rozstrzyga pierwszy segment ścieżki (``/druga/…``). Dzięki temu przebieg nie wymaga
ani wpisu w DNS-ie, ani drugiej pozycji w ``DJANGO_ALLOWED_HOSTS`` — czyli da się go uruchomić
na maszynie dewelopera dokładnie tak samo, jak na stagingu.

**Granica tego trybu, zapisana tu, żeby nikt nie odkrył jej w połowie przebiegu.** Pod prefiksem
rozstrzyga się **konkurs**, ale nie witryna Wagtaila: ``Site.find_for_request`` dopasowuje nadal
po nagłówku ``Host``, więc drzewo stron (strona główna, dokumenty, aktualności) jest drzewem
platformy. Kontrole chodzą więc po **aplikacji** konkursu drugiego (panel, rejestracja, konta),
a nie po jego CMS-ie.

Uruchomienie (z katalogu głównego repozytorium, przy stojącym środowisku)::

    docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile e2e run --rm e2e \\
        sh -c "pip install -q -r requirements.txt && python check_stage2_screens.py"

Kolejność czynności, które muszą się wykonać **przed** kontrolami (robi to ``scripts/e2e.sh``):
założenie konkursu ``create_competition --slug e2e-druga --path-prefix druga`` i zapalenie mu
flag etapu 2.
"""

from __future__ import annotations

import os
import re
import sys

#: Adres instalacji widziany z wnętrza sieci compose – ten sam domyślny, co w ``e2e/conftest.py``.
BASE = os.environ.get("E2E_BASE_URL", "http://web:8000").rstrip("/")

#: Prefiks ścieżki konkursu drugiego. Ta sama wartość, co ``--path-prefix`` w ``scripts/e2e.sh``
#: i co ``SECOND_PREFIX`` w ``backend/apps/web/tests/test_e2e_two_competitions.py``.
PREFIX = "/" + os.environ.get("E2E_SECOND_PREFIX", "druga").strip("/")

#: Konto koordynatora z ``manage.py seed_demo``. Rola koordynatora w konkursie drugim nadaje mu
#: ``create_competition --coordinator-email`` — ta sama osoba wchodzi więc do obu paneli, dzięki
#: czemu różnica „200 tam, 404 tu” pochodzi wyłącznie z flagi konkursu, a nie z braku uprawnień.
COORDINATOR_EMAIL = os.environ.get("E2E_COORDINATOR_EMAIL", "koordynator@example.com")
DEMO_PASSWORD = os.environ.get("E2E_DEMO_PASSWORD", "Demo12345!")


class Report:
    """Zbieracz usterek: kontrola idzie do końca i dopiero potem mówi, co jest nie tak.

    Przerwanie na pierwszej usterce dawałoby przebieg, w którym każdy nawrót pokazuje jeden nowy
    błąd — a przy dwudziestu ekranach znaczy to dwadzieścia przebiegów po kilka minut.
    """

    def __init__(self, title: str):
        self.title = title
        self.failures: list[str] = []

    def check(self, condition: bool, message: str) -> bool:
        if not condition:
            self.failures.append(message)
        return condition

    def note(self, line: str) -> None:
        print(f"    {line}")

    def finish(self) -> int:
        print(f"== {self.title}: {len(self.failures)} usterek")
        for line in self.failures:
            print(f"BŁĄD: {line}", file=sys.stderr)
        return 1 if self.failures else 0


def url(path: str, *, second: bool = False) -> str:
    """Pełny adres: ``url("/coordinator/")`` → Konkurs #1, ``second=True`` → konkurs drugi."""
    return f"{BASE}{PREFIX if second else ''}{path}"


def visit(page, path: str, *, second: bool = False) -> int:
    """Otwiera adres i oddaje kod odpowiedzi (``0``, gdy przeglądarka nie dostała nagłówków)."""
    response = page.goto(url(path, second=second), wait_until="domcontentloaded")
    return response.status if response is not None else 0


def login(page, email: str = COORDINATOR_EMAIL, password: str = DEMO_PASSWORD) -> None:
    """Logowanie formularzem Konkursu #1.

    Logujemy się **raz i pod prefiksem pierwszego konkursu**, bo w trybie ``PATH`` ciasteczka są
    wspólne dla całej domeny (§ 2.3) — to jest opisane ograniczenie tego trybu, a nie niedopatrzenie
    kontroli. Człowiek robi dokładnie to samo: loguje się raz i przechodzi między konkursami.
    """
    page.goto(url("/login/"), wait_until="domcontentloaded")
    page.fill("#id_username", email)
    page.fill("#id_password", password)
    page.get_by_role("button", name="Zaloguj").click()
    page.wait_for_load_state("domcontentloaded")


def first_id(page, pattern: str) -> int | None:
    """Pierwszy identyfikator pasujący do wzorca w HTML-u bieżącej strony.

    Identyfikatory bierzemy **ze strony**, a nie z bazy: kontrola ma chodzić po tym, co widzi
    koordynator, i nie ma prawa wiedzieć więcej niż jego przeglądarka.
    """
    found = re.search(pattern, page.content())
    return int(found.group(1)) if found else None
