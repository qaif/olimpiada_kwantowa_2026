"""Kontrola układu na wąskich ekranach: strona nigdy nie przewija się w poziomie.

Poziomy suwak na **całej stronie** jest na telefonie awarią, a nie kosmetyką: treść ucieka poza
kadr, gest przewijania w pionie zahacza o bok, a przyklejony pasek konta rozjeżdża się z resztą.
W tym serwisie poziomo wolno przewijać się wyłącznie temu, co ma własną ramkę i własny suwak:
tabeli w ``.scroll``, pasowi zakładek (``.tabs``) i nawigacji panelu (``.panel__nav``).

Skrypt ładuje pięć adresów publicznych w trzech szerokościach (360 – iPhone SE, 768 – tablet,
1280 – laptop) i dla każdego sprawdza dwie rzeczy:

  1. ``documentElement.scrollWidth <= clientWidth`` (z tolerancją 1 px na zaokrąglenia
     subpikselowe przy skalowaniu czcionek),
  2. gdy warunek nie jest spełniony – wypisuje **konkretne** elementy wystające poza kadr,
     z pominięciem tych, które leżą w kontenerze z własnym przewijaniem. Bez tej listy komunikat
     „strona jest za szeroka o 40 px” nie mówi, co poprawić.

Dodatkowo na 360 px sprawdzamy pasek konta: ma się mieścić i zostawać klikalny (przyciski
„Zaloguj się” / „Zarejestruj się” w kadrze, o wysokości co najmniej 32 px – pole dotyku).

    docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm -T \
        -e E2E_BASE_URL=http://web:8000 e2e python /e2e/check_mobile.py

UWAGA: skryptu nie da się uruchomić zza firmowego proxy bez dostępu do sieci compose – Playwright
i przeglądarka mieszkają w obrazie ``e2e``. Kod jest napisany „na sucho”, więc pierwszy przebieg
w czystym środowisku jest jednocześnie jego pierwszym sprawdzeniem.
"""

import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://web:8000").rstrip("/")

#: Adresy publiczne – żaden nie wymaga logowania. ``/zadania/`` i ``/statystyki/`` są stronami
#: redakcyjnymi Wagtaila albo widokiem zależnym od ogłoszonych etapów: brak takiej strony w danym
#: środowisku nie jest błędem układu, więc 404 pomijamy z komunikatem zamiast wywracać przebieg.
PATHS = ("/", "/login/", "/register/", "/zadania/", "/statystyki/")

WIDTHS = (360, 768, 1280)

#: Tolerancja: 1 px zaokrąglenia przy skalowaniu czcionki i przy ``border`` w wartościach ułamkowych.
SLACK_PX = 1

OVERFLOW = """(slack) => {
  const doc = document.documentElement;
  const limit = doc.clientWidth + slack;
  const offenders = [];
  for (const el of document.querySelectorAll('body *')) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    if (rect.right <= limit && rect.left >= -slack) continue;
    // Element w kontenerze z własnym suwakiem ma prawo wystawać – suwak dostaje ramka,
    // a nie strona. Szukamy pierwszego przodka, który przewija się w poziomie.
    let scroller = el.parentElement;
    let inside = false;
    while (scroller && scroller !== document.body) {
      const overflowX = getComputedStyle(scroller).overflowX;
      if (overflowX === 'auto' || overflowX === 'scroll') { inside = true; break; }
      scroller = scroller.parentElement;
    }
    if (inside) continue;
    offenders.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.getAttribute('class') || '').slice(0, 70),
      left: Math.round(rect.left),
      right: Math.round(rect.right),
    });
    if (offenders.length >= 8) break;
  }
  return {
    scrollWidth: doc.scrollWidth,
    clientWidth: doc.clientWidth,
    offenders,
  };
}"""

ACCOUNT_BAR = """() => {
  const bar = document.querySelector('.topbar--account');
  if (!bar) return null;
  const rect = bar.getBoundingClientRect();
  const controls = [...bar.querySelectorAll('a.btn, button, a.account-bar__link')].map(el => {
    const r = el.getBoundingClientRect();
    return {
      text: el.textContent.trim().slice(0, 24),
      right: Math.round(r.right),
      height: Math.round(r.height),
    };
  });
  return { right: Math.round(rect.right), controls };
}"""


def check_overflow(page, path: str, width: int) -> int:
    """Ładuje adres i zwraca liczbę znalezionych usterek (0 = w porządku)."""
    page.set_viewport_size({"width": width, "height": 800})
    response = page.goto(BASE + path, wait_until="networkidle")
    if response is not None and response.status == 404:
        print(f"  {width:>4}px {path:<14} POMINIETE (404 – brak strony w tym srodowisku)")
        return 0
    # Pasek cookie jest pozycjonowany ``fixed`` i na 360 px zajmuje pół ekranu; przewijanie do
    # dołu upewnia się, że mierzymy układ w stanie, w jakim człowiek go widzi po chwili czytania.
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(200)
    state = page.evaluate(OVERFLOW, SLACK_PX)
    excess = state["scrollWidth"] - state["clientWidth"]
    if excess <= SLACK_PX and not state["offenders"]:
        print(f"  {width:>4}px {path:<14} OK  (scrollWidth={state['scrollWidth']})")
        return 0
    print(f"  {width:>4}px {path:<14} PRZEPELNIENIE o {excess}px")
    for item in state["offenders"]:
        print(f'           <{item["tag"]} class="{item["cls"]}"> {item["left"]}..{item["right"]}')
    return 1


def check_account_bar(page) -> int:
    """Pasek konta na 360 px: mieści się w kadrze, a przyciski mają pole dotyku."""
    page.set_viewport_size({"width": 360, "height": 800})
    page.goto(BASE + "/", wait_until="networkidle")
    state = page.evaluate(ACCOUNT_BAR)
    if state is None:
        print("  pasek konta: BRAK elementu .topbar--account")
        return 1
    problems = 0
    if state["right"] > 360 + SLACK_PX:
        print(f"  pasek konta wystaje poza kadr (right={state['right']})")
        problems += 1
    for control in state["controls"]:
        if control["right"] > 360 + SLACK_PX:
            print(f"  kontrolka poza kadrem: {control}")
            problems += 1
        # 32 px to minimum, przy którym trafia się kciukiem bez celowania.
        if control["height"] < 32:
            print(f"  kontrolka za niska (<32px): {control}")
            problems += 1
    if problems == 0:
        print(f"  pasek konta 360px: OK ({len(state['controls'])} kontrolek w kadrze)")
    return problems


def main() -> None:
    failures = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        for width in WIDTHS:
            print(f"--- {width} px ---")
            for path in PATHS:
                failures += check_overflow(page, path, width)
        print("--- pasek konta ---")
        failures += check_account_bar(page)
        browser.close()
    assert failures == 0, f"{failures} usterek ukladu na waskim ekranie"
    print("OK")


main()
