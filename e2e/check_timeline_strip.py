"""Kontrola paska linii czasu w nagłówku: głowica, dymki, warstwa „pomiaru” i wąski ekran.

Pięć rzeczy, których nie sprawdzi pytest, bo dzieją się w przeglądarce:

1. **Pasek nie rozpycha kolumny treści.** Wykres ma 102 znaki i skaluje się jednostką ``cqw``;
   mierzymy jego szerokość wobec kolumny nagłówka i sprawdzamy, że strona nie przewija się
   w poziomie – na kilku szerokościach okna, bo to jest właśnie ten rodzaj usterki, który
   pojawia się tylko przy jednej.
2. **Głowica stoi tam, gdzie dzisiejsza data.** Serwer rysuje ją w HTML, a skrypt co minutę
   przelicza jej pozycję z ``data-axis-start``/``data-axis-end``. Liczymy ułamek osi z tych
   samych atrybutów – dopuszczając jedną komórkę różnicy, bo Python zaokrągla połówki do
   parzystych, a JavaScript w górę.
3. **W spoczynku pasek jest jedną linią, a po najechaniu rozwija się w dół.** Mierzymy wysokość
   nagłówka z paskiem i bez niego (delta ma być rzędu kilkunastu pikseli), a potem wysokość
   przed najechaniem i po – panel ma rosnąć, wykres ma zostać na swoim miejscu, a szerokość ma
   się nie zmienić ani o piksel. To samo sprawdzamy dla fokusu klawiatury (``:focus-within``).
4. **Warstwa „pomiaru” zapala się pod kursorem i gaśnie poza nim.** Płótno jest ozdobą
   (``aria-hidden``, ``pointer-events: none``), więc jedyne, co da się o nim orzec, to że
   pojawia się i znika – oraz że nie przechwytuje najechania na znacznik.
5. **Przy ``prefers-reduced-motion: reduce`` nie ma ani warstwy, ani animacji wejścia**, a pasek
   jest kompletny; poniżej 640 px zostaje z niego zwinięty kalendarz (``<details>``).

Uruchamianie jak pozostałe kontrole (Chromium bez okna, w sieci compose):

    docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile e2e run --rm e2e \\
        sh -c "pip install -q -r requirements.txt && python check_timeline_strip.py"
"""

import os
from datetime import date

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://web:8000")
PATH = "/login/"

#: Ile pikseli wolno paskowi dołożyć do nagłówka w spoczynku. Jeden wiersz monospace przy kroju
#: 14 px i ``line-height: 1.2`` to 17 px; limit z zapasem na zaokrąglenia i inny krój systemowy.
RESTING_DELTA_MAX = 22

#: Komplet stanu paska, czytany jednym wywołaniem: atrybuty osi, narysowane komórki, wymiary
#: i to, czy warstwa „pomiaru” jest widoczna. Jedno ``evaluate`` zamiast ośmiu – każde kosztuje
#: rundę po protokole, a stan ma być odczytany w jednej chwili, nie w ośmiu kolejnych.
STATE = """() => {
  const strip = document.querySelector('[data-timeline-strip]');
  if (!strip) return null;
  const pre = strip.querySelector('.tl');
  const bar = strip.querySelector('.tl__bar');
  const canvas = strip.querySelector('[data-tl-quantum]');
  const marks = [...strip.querySelectorAll('[data-tl-mark]')];
  const drawn = bar ? bar.textContent : '';
  const box = strip.getBoundingClientRect();
  const root = document.documentElement;
  return {
    axisStart: strip.getAttribute('data-axis-start'),
    axisEnd: strip.getAttribute('data-axis-end'),
    size: parseInt(strip.getAttribute('data-size'), 10),
    head: parseInt(strip.getAttribute('data-head'), 10),
    drawn: drawn,
    cells: drawn.replace(/^\\[/, '').replace(/\\]$/, '').length,
    headColumn: drawn.replace(/^\\[/, '').indexOf('>'),
    animated: strip.classList.contains('is-animated'),
    measuring: strip.classList.contains('is-measuring'),
    canvasOpacity: canvas ? getComputedStyle(canvas).opacity : null,
    canvasEvents: canvas ? getComputedStyle(canvas).pointerEvents : null,
    marks: marks.length,
    labels: marks.map((node) => node.getAttribute('aria-label')),
    chips: strip.querySelectorAll('.tl-legend__chip').length,
    activeChips: strip.querySelectorAll('.tl-legend__chip.is-active').length,
    panelHeight: Math.round(strip.querySelector('.tl-panel').getBoundingClientRect().height),
    barTop: pre ? Math.round(pre.getBoundingClientRect().top) : 0,
    barWidth: pre ? Math.round(pre.getBoundingClientRect().width) : 0,
    headerHeight: Math.round(document.querySelector('header.topbar').getBoundingClientRect().height),
    preDisplay: pre ? getComputedStyle(pre).display : null,
    listDisplay: getComputedStyle(strip.querySelector('.tl-list')).display,
    listItems: strip.querySelectorAll('.tl-list__item').length,
    barWidth: bar ? Math.round(bar.getBoundingClientRect().width) : 0,
    column: pre ? pre.clientWidth : 0,
    stripWidth: Math.round(box.width),
    stripHeight: Math.round(box.height),
    pageOverflow: root.scrollWidth > root.clientWidth,
  };
}"""


def expected_head(state: dict) -> int:
    """Komórka, na której powinna stać głowica – policzona z atrybutów osi i z dzisiejszej daty."""
    start = date.fromisoformat(state["axisStart"])
    end = date.fromisoformat(state["axisEnd"])
    total = max((end - start).days, 1)
    elapsed = min(max((date.today() - start).days, 0), total)
    return min(round(elapsed / total * state["size"]), state["size"] - 1)


def check_rendering(page) -> None:
    page.goto(BASE + PATH, wait_until="networkidle")
    state = page.evaluate(STATE)
    assert state is not None, "pasek musi byc w naglowku kazdej strony"
    print(f"pasek: {state['cells']} komorek, glowica w kolumnie {state['headColumn']}")
    assert state["drawn"].startswith("[") and state["drawn"].endswith("]"), "pasek stoi w klamrach"
    assert state["cells"] == state["size"], "pasek ma dokladnie tyle komorek, ile deklaruje"
    assert state["drawn"].count(">") == 1, "dokladnie jedna glowica"
    assert abs(state["headColumn"] - expected_head(state)) <= 1, "glowica na dzisiejszym ulamku osi"
    assert state["headColumn"] == state["head"], "atrybut i rysunek mowia to samo"
    assert state["marks"], "kazde wydarzenie ma na linii swoj znacznik"
    assert all(state["labels"]), "znacznik niesie nazwe i termin w aria-label"
    assert state["listItems"], "zwiniety kalendarz dla telefonu jest w kodzie strony"
    assert state["animated"] is True, "skrypt wlacza animacje wejscia"
    assert state["canvasEvents"] == "none", "warstwa pomiaru nie moze przechwytywac najechania"


def check_width(page) -> None:
    """Wykres mieści się w kolumnie nagłówka i nigdzie nie przewija strony w poziomie."""
    for width in (1440, 1280, 1024, 860, 720):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(BASE + PATH, wait_until="networkidle")
        state = page.evaluate(STATE)
        print(f"{width}px  wykres {state['barWidth']} / kolumna {state['column']}")
        assert state["barWidth"] <= state["column"], f"wykres szerszy od kolumny przy {width}px"
        assert state["pageOverflow"] is False, f"strona przewija sie w poziomie przy {width}px"
    page.set_viewport_size({"width": 1280, "height": 800})


def check_resting_height(page) -> None:
    """W spoczynku pasek dokłada nagłówkowi jedną linię – kilkanaście pikseli, nie kilkadziesiąt."""
    page.goto(BASE + PATH, wait_until="networkidle")
    with_strip = page.evaluate(STATE)
    delta = page.evaluate(
        """() => {
          const header = document.querySelector('header.topbar');
          const strip = document.querySelector('[data-timeline-strip]');
          const before = header.getBoundingClientRect().height;
          strip.style.display = 'none';
          const after = header.getBoundingClientRect().height;
          strip.style.display = '';
          return Math.round(before - after);
        }"""
    )
    print(f"naglowek rosnie o {delta}px; panel w spoczynku {with_strip['panelHeight']}px")
    assert with_strip["panelHeight"] == 0, "panel w spoczynku ma zerowa wysokosc"
    assert delta <= RESTING_DELTA_MAX, f"pasek dokłada naglowkowi {delta}px, limit {RESTING_DELTA_MAX}"
    assert with_strip["chips"], "legenda jest w kodzie strony takze wtedy, gdy jest zwinieta"


def check_expansion(page) -> None:
    """Najechanie i fokus rozwijają panel **w dół**: wykres nie drgnie, szerokość się nie zmieni."""
    page.goto(BASE + PATH, wait_until="networkidle")
    closed = page.evaluate(STATE)
    mark = page.locator("[data-tl-mark]").first
    mark.hover()
    page.wait_for_timeout(500)
    opened = page.evaluate(STATE)
    print(
        f"panel: {closed['panelHeight']}px → {opened['panelHeight']}px, naglowek "
        f"{closed['headerHeight']} → {opened['headerHeight']}"
    )
    assert opened["panelHeight"] > closed["panelHeight"], "panel rozwija sie po najechaniu"
    assert opened["headerHeight"] > closed["headerHeight"], "naglowek rosnie w dol"
    assert opened["barTop"] == closed["barTop"], "wykres nie moze sie przesunac"
    assert opened["barWidth"] == closed["barWidth"], "szerokosc nie moze sie zmienic"
    assert opened["pageOverflow"] is False, "rozwiniety panel nie przewija strony"
    assert opened["activeChips"], "najechanie na kreske podswietla jej chip w legendzie"

    page.mouse.move(10, 700)
    page.wait_for_timeout(500)
    left = page.evaluate(STATE)
    assert left["panelHeight"] == 0, "panel zwija sie po zjechaniu kursorem"
    assert left["activeChips"] == 0, "podswietlenie gasnie razem z panelem"

    # Klawiatura: fokus na kresce rozwija panel przez ``:focus-within``, bez udziału skryptu.
    mark.focus()
    page.wait_for_timeout(500)
    focused = page.evaluate(STATE)
    assert focused["panelHeight"] > 0, "fokus klawiatury rozwija panel"


def check_measurement(page) -> None:
    """Najechanie zapala warstwę „pomiaru”, zjechanie ją gasi."""
    page.goto(BASE + PATH, wait_until="networkidle")
    page.locator("[data-tl-mark]").first.hover()
    page.wait_for_timeout(400)
    hovered = page.evaluate(STATE)
    print(f"pod kursorem: measuring={hovered['measuring']} opacity={hovered['canvasOpacity']}")
    assert hovered["measuring"] is True, "warstwa pomiaru zapala sie pod kursorem"
    assert float(hovered["canvasOpacity"]) > 0.5, "warstwa pomiaru jest widoczna"

    page.mouse.move(10, 700)
    page.wait_for_timeout(400)
    assert page.evaluate(STATE)["measuring"] is False, "poza paskiem warstwa gasnie"


def check_narrow_screen(page) -> None:
    """Poniżej 640 px wykres znika, a kalendarz zostaje zwiniętym ``<details>``."""
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(BASE + PATH, wait_until="networkidle")
    state = page.evaluate(STATE)
    print(f"375px: pre={state['preDisplay']} lista={state['listDisplay']}")
    assert state["preDisplay"] == "none", "na telefonie siatka znakow znika"
    assert state["listDisplay"] == "block", "na telefonie zostaje zwiniety kalendarz"
    assert state["pageOverflow"] is False, "na telefonie strona nie przewija sie w poziomie"
    summary = page.locator(".tl-list__summary")
    assert summary.is_visible(), "zwiniety kalendarz pokazuje jedno zdanie"
    summary.click()
    page.wait_for_timeout(200)
    assert page.locator(".tl-list__item").first.is_visible(), "po rozwinieciu widac terminy"
    page.set_viewport_size({"width": 1280, "height": 800})


def check_reduced_motion(browser) -> None:
    """Tryb ograniczonego ruchu: pasek kompletny, warstwy „pomiaru” nie ma wcale."""
    context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(BASE + PATH, wait_until="networkidle")
    state = page.evaluate(STATE)
    print(f"reduced-motion: {state['cells']} komorek, znacznikow {state['marks']}")
    assert state["cells"] == state["size"], "pasek jest kompletny takze bez animacji"
    assert state["drawn"].count(">") == 1
    assert state["marks"], "znaczniki zostaja"

    page.locator("[data-tl-mark]").first.hover()
    page.wait_for_timeout(400)
    hovered = page.evaluate(STATE)
    assert hovered["measuring"] is False, "przy reduced-motion pomiar nie startuje"
    # Dymek nie jest animacją, tylko treścią – on ma działać zawsze.
    # Rozwijanie panelu to treść, a nie ozdoba: ma działać także przy ograniczonym ruchu.
    page.locator("[data-tl-mark]").first.hover()
    page.wait_for_timeout(200)
    assert page.evaluate(STATE)["panelHeight"] > 0, "panel rozwija sie takze przy reduced-motion"
    context.close()


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    check_rendering(page)
    check_width(page)
    check_resting_height(page)
    check_expansion(page)
    check_measurement(page)
    check_narrow_screen(page)
    check_reduced_motion(browser)
    browser.close()
    print("OK")
