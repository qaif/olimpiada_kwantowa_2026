"""Kontrola dwóch konkursów na jednej instalacji: marki są różne, a linki krzyżowe dają 404.

Uzupełnienie scenariusza z ``docs/UNIWERSALNY-ETAP-1.md`` § 7.4. Istniejący przebieg
(``e2e/test_full_cycle.py``, ``scripts/e2e.sh``) zostaje **bez zmian** i chodzi po Konkursie #1 –
ten skrypt jest drugim, krótszym przebiegiem i sprawdza dokładnie to, czego tamten sprawdzić nie
może: że drugi konkurs stojący obok jest osobnym serwisem, a nie drugim widokiem tego samego.

**Czego ten skrypt wymaga i dlaczego nie chodzi w zwykłym przebiegu testów.** Potrzebuje żywego
środowiska compose z drugim konkursem założonym komendą::

    docker compose exec web python manage.py create_competition \\
        --slug drugi --name "Olimpiada Druga" --domain drugi.localhost

Adres ``drugi.localhost`` nie wymaga wpisu w DNS (rozwiązuje się na 127.0.0.1 w większości
systemów), ale **wymaga** obecności na liście ``DJANGO_ALLOWED_HOSTS`` usługi ``web``. Playwright
i tak wysyła nagłówek ``Host`` sam – tutaj ustawiamy go jawnie (``extra_http_headers``), żeby
przebieg dało się wykonać także spod adresu, którego system nie rozwiązuje.

Skrypt jest **poza** zwykłą suitą (nazwa nie pasuje do ``python_files``) z tego samego powodu, co
reszta ``e2e/check_*.py``: uruchamia przeglądarkę i wymaga infrastruktury. Uruchomienie::

    docker compose exec web python /e2e/check_two_competitions.py

Kody wyjścia: 0 – obie reguły spełnione, 1 – którakolwiek złamana (szczegół na wyjściu).
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

#: Adres instalacji widziany z wnętrza sieci compose. Ten sam domyślny, co w ``e2e/conftest.py``.
BASE = os.environ.get("E2E_BASE_URL", "http://web:8000").rstrip("/")

#: Hosty obu konkursów. Pierwszy jest hostem Konkursu #1 (``SITE_DOMAIN``), drugi – konkursu
#: założonego komendą ``create_competition``. Oba da się nadpisać, bo na maszynie dewelopera
#: i na stagingu nazywają się inaczej.
HOST_ONE = os.environ.get("E2E_HOST_ONE", "localhost")
HOST_TWO = os.environ.get("E2E_HOST_TWO", "drugi.localhost")

#: Ścieżka, której obecność sprawdzamy „na krzyż”. Panel uczestnika wymaga logowania, więc nie
#: odróżniłby konkursów; strona główna renderuje markę i jest publiczna.
HOME = "/"


def brand_of(page, host: str) -> str:
    """Nazwa konkursu z tytułu strony głównej pod danym hostem."""
    page.set_extra_http_headers({"Host": host})
    page.goto(f"{BASE}{HOME}", wait_until="domcontentloaded")
    return page.title()


def status_of(page, host: str, path: str) -> int:
    """Kod odpowiedzi dla adresu pobranego pod wskazanym hostem."""
    page.set_extra_http_headers({"Host": host})
    response = page.goto(f"{BASE}{path}", wait_until="domcontentloaded")
    return response.status if response is not None else 0


def main() -> int:
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        brand_one = brand_of(page, HOST_ONE)
        brand_two = brand_of(page, HOST_TWO)
        print(f"marka {HOST_ONE}: {brand_one!r}")
        print(f"marka {HOST_TWO}: {brand_two!r}")
        if brand_one == brand_two:
            failures.append("obie domeny pokazują tę samą markę – konkursy nie są rozdzielone")

        # Strona drugiego konkursu otwarta spod domeny pierwszego ma **nie istnieć**. 404, a nie
        # 403: istnienie tej strony nie jest informacją czytelnika konkursu pierwszego.
        cross = status_of(page, HOST_ONE, "/drugi/")
        print(f"link krzyżowy /drugi/ spod {HOST_ONE}: {cross}")
        if cross != 404:
            failures.append(f"adres drugiego konkursu spod domeny pierwszego oddał {cross}, a nie 404")

        browser.close()

    for line in failures:
        print(f"BŁĄD: {line}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
