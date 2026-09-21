"""Kontrola taśmy sponsorów w menu: pętla bez końca, pauza, ograniczony ruch, wąski ekran.

Cztery rzeczy, których nie sprawdzi pytest, bo dzieją się w przeglądarce (``static/js/sponsor-slider.js``):

1. **Taśma jedzie i nigdy się nie zatrzymuje**, kiedy logotypów jest więcej, niż mieści pudełko.
   Stan jest jawny w znaczniku (``data-sponsor-slider-state``: ``running``/``static``) – skrypt
   sam decyduje, w którym stanie być, więc kontrola czyta ten atrybut, a nie zgaduje po klasach.
   Jeśli logotypów jest więcej niż widocznych slotów, a stan mimo to jest ``static``, to jest
   **usterka**, nie powód do pominięcia testu.
2. **Najechanie i fokus zatrzymują taśmę** (dwa niezależne powody – patrz ``check_pause_
   independent``), zjechanie/utrata fokusu wznawiają ją.
3. **``prefers-reduced-motion: reduce`` wyłącza ruch całkowicie** – stan ma być ``static``, a
   transformacja taśmy ma się nie zmieniać przez cały interwał.
4. **Poniżej 900 px paska nie ma wcale** – to samo pytanie, co przy innych elementach nagłówka
   (``check_sticky_bar.py``, ``check_timeline_strip.py``).

Wymaga zawodów z ustawionym logotypem organizatora i co najmniej pięcioma partnerami z logotypem
(więcej, niż mieści jeden rząd o szerokości 1280 px – cztery sloty po ~132 px) – tyle, ile
zakłada ``manage.py seed_partners`` na środowisku deweloperskim.

Uruchamianie jak pozostałe kontrole (Chromium bez okna, w sieci compose):

    docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile e2e run --rm e2e \\
        sh -c "pip install -q -r requirements.txt && python check_sponsor_slider.py"
"""

import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://web:8000")
PATH = "/"

STATE = """() => {
  const root = document.querySelector('[data-sponsor-slider]');
  if (!root) return null;
  const track = root.querySelector('[data-sponsor-slider-track]');
  const items = [...track.querySelectorAll('.sponsor-slider__item')];
  const real = items.filter((item) => !item.hasAttribute('aria-hidden'));
  const style = getComputedStyle(root);
  return {
    interval: parseInt(root.getAttribute('data-interval'), 10),
    state: root.getAttribute('data-sponsor-slider-state'),
    itemCount: items.length,
    realCount: real.length,
    slotWidth: real.length ? real[0].getBoundingClientRect().width : 0,
    rootWidth: root.clientWidth,
    display: style.display,
    transform: getComputedStyle(track).transform,
  };
}"""


def visible_slots(state: dict) -> int:
    if not state["slotWidth"]:
        return state["realCount"]
    return max(1, int(state["rootWidth"] // state["slotWidth"]))


def check_rendering(page) -> dict:
    page.goto(BASE + PATH, wait_until="networkidle")
    state = page.evaluate(STATE)
    assert state is not None, "slider sponsorów musi stać w menu (organizator albo partner z logo)"
    print(f"slider: {state['realCount']} plansz, interwał {state['interval']}s, stan={state['state']}")
    assert state["interval"] > 0, "atrybut data-interval musi być dodatnią liczbą sekund"
    assert state["state"] in ("running", "static"), "data-sponsor-slider-state ma mieć jedną z dwóch wartości"
    return state


def check_state_matches_capacity(state: dict) -> None:
    """Za dużo logotypów na jeden rząd → stan MUSI być ``running``. To jest twarda asercja."""
    slots = visible_slots(state)
    print(f"plansz: {state['realCount']}, widocznych slotów: {slots}")
    if state["realCount"] > slots:
        assert state["state"] == "running", (
            f"jest {state['realCount']} logotypów na {slots} widocznych slotów, "
            "a slider nie jedzie (data-sponsor-slider-state != 'running')"
        )
    else:
        assert state["state"] == "static", "logotypy mieszczą się naraz – slider nie ma czego przewijać"


def check_endless_loop(page, state: dict) -> None:
    """Po jednym okrążeniu taśma wraca na początek bez skoku i jedzie dalej – bez końca."""
    if state["state"] != "running":
        return
    interval_ms = state["interval"] * 1000
    seen_transforms = set()
    rounds = state["realCount"] * 2 + 1
    for _ in range(rounds):
        page.wait_for_timeout(interval_ms + 150)
        seen_transforms.add(page.evaluate(STATE)["transform"])
    print(f"transformacje zaobserwowane w {rounds} krokach: {len(seen_transforms)}")
    assert len(seen_transforms) >= 2, "taśma faktycznie się przesuwa"

    before = page.evaluate(STATE)["transform"]
    page.wait_for_timeout(interval_ms + 150)
    after = page.evaluate(STATE)["transform"]
    assert before != after, "taśma stanęła zamiast jechać dalej – pętla ma być bez końca"


def check_pause_independent(page, state: dict) -> None:
    """Hover i fokus to dwa niezależne powody pauzy – żaden nie może „zjeść” drugiego."""
    if state["state"] != "running":
        print("slider statyczny – pomijam test pauzy (nic tu nie jedzie)")
        return
    interval_ms = state["interval"] * 1000
    root = page.locator("[data-sponsor-slider]")
    link = root.locator("a").first

    # Najechanie zatrzymuje.
    root.hover()
    before = page.evaluate(STATE)["transform"]
    page.wait_for_timeout(interval_ms + 300)
    during_hover = page.evaluate(STATE)["transform"]
    assert before == during_hover, "najechanie na slider ma zatrzymać przewijanie"

    # Fokus na odnośniku W ŚRODKU sliderа, mysz nadal na sliderze: pauza ma trwać po zjechaniu
    # myszą, dopóki fokus tam stoi (dwa niezależne powody, nie jeden wspólny flag).
    link.focus()
    page.mouse.move(1, 1)  # zjeżdżamy kursorem poza slider, ale fokus zostaje w środku
    page.wait_for_timeout(interval_ms + 300)
    still_paused = page.evaluate(STATE)["transform"]
    assert during_hover == still_paused, "utrata najechania nie może wznowić taśmy, dopóki trwa fokus"

    # Dopiero zdjęcie fokusu (poza slider) wznawia.
    page.keyboard.press("Tab")
    page.wait_for_timeout(interval_ms + 300)
    resumed = page.evaluate(STATE)["transform"]
    assert resumed != still_paused, "zdjęcie fokusu i myszy razem ma wznowić przewijanie"


def check_reduced_motion(browser) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(BASE + PATH, wait_until="networkidle")
    state = page.evaluate(STATE)
    if state is None:
        context.close()
        return
    print(f"reduced-motion: stan={state['state']}")
    assert state["state"] == "static", "prefers-reduced-motion ma wyłączyć ruch całkowicie"
    before = page.evaluate(STATE)["transform"]
    page.wait_for_timeout((state["interval"] + 1) * 1000)
    after = page.evaluate(STATE)["transform"]
    assert before == after, "przy reduced-motion taśma nie ma się ruszyć wcale"
    context.close()


def check_narrow_screen(page) -> None:
    """Poniżej 900 px slidera w menu nie ma wcale – to samo pytanie, co przy innych elementach."""
    page.set_viewport_size({"width": 700, "height": 800})
    page.goto(BASE + PATH, wait_until="networkidle")
    display = page.evaluate(
        "() => { const el = document.querySelector('.sponsor-slider'); "
        "return el ? getComputedStyle(el).display : 'none'; }"
    )
    print(f"700px: display={display}")
    assert display == "none", "poniżej 900px slider sponsorów ma zniknąć z menu"
    page.set_viewport_size({"width": 1280, "height": 800})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    initial_state = check_rendering(page)
    check_state_matches_capacity(initial_state)
    check_endless_loop(page, initial_state)
    check_pause_independent(page, initial_state)
    check_reduced_motion(browser)
    check_narrow_screen(page)
    browser.close()
    print("OK")
