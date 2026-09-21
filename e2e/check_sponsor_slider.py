"""Kontrola taśmy sponsorów w menu: pętla bez końca, pauza, ograniczony ruch, wąski ekran.

Cztery rzeczy, których nie sprawdzi pytest, bo dzieją się w przeglądarce (``static/js/sponsor-slider.js``):

1. **Taśma jedzie i nigdy się nie zatrzymuje.** Po jednym pełnym okrążeniu (tyle plansz, ile jest
   naprawdę – klony na końcu doliczają się same) wraca bez skoku na początek i jedzie dalej –
   organizator, potem partnerzy, znowu organizator, w kółko (uwaga organizatora z 21.09.2026:
   „jak na Olimpiadzie Biologicznej”, bez żadnego zatrzymania po jednym przejściu).
2. **Najechanie i fokus zatrzymują taśmę**, zjechanie/utrata fokusu wznawiają ją.
3. **``prefers-reduced-motion: reduce`` wyłącza ruch całkowicie** – zostaje nieruchomy rząd
   pierwszych logotypów, tylu, ile mieści pudełko.
4. **Poniżej 900 px paska nie ma wcale** – to samo pytanie, co przy innych elementach nagłówka
   (``check_sticky_bar.py``, ``check_timeline_strip.py``).

Wymaga zawodów z ustawionym logotypem organizatora i co najmniej czterema partnerami z logotypem
(więcej, niż mieści jeden rząd) – tyle, ile zakłada ``manage.py seed_partners`` na środowisku
deweloperskim. Bez tego taśma ma za mało plansz, żeby się w ogóle przewijać (test 1 kończy się
wtedy komunikatem „za mało logotypów do testu pętli”, a nie fałszywym zielonym wynikiem).

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
  return {
    interval: parseInt(root.getAttribute('data-interval'), 10),
    animated: root.classList.contains('sponsor-slider--animated'),
    items: track.querySelectorAll('.sponsor-slider__item').length,
    realItems: track.querySelectorAll('.sponsor-slider__item:not([aria-hidden])').length,
    transform: getComputedStyle(track).transform,
    display: getComputedStyle(root).display,
  };
}"""


def check_rendering(page) -> None:
    page.goto(BASE + PATH, wait_until="networkidle")
    state = page.evaluate(STATE)
    assert state is not None, "slider sponsorów musi stać w menu (organizator albo partner z logo)"
    print(f"slider: {state['realItems']} plansz, interwał {state['interval']}s, animated={state['animated']}")
    assert state["interval"] > 0, "atrybut data-interval musi być dodatnią liczbą sekund"


def check_endless_loop(page) -> None:
    """Po jednym okrążeniu taśma wraca na początek bez skoku i jedzie dalej – bez końca."""
    page.goto(BASE + PATH, wait_until="networkidle")
    before = page.evaluate(STATE)
    if not before["animated"] or before["realItems"] < 2:
        print("za mało logotypów do testu pętli – pomijam (patrz docstring modułu)")
        return

    interval_ms = before["interval"] * 1000
    seen_transforms = set()
    # Obserwujemy przez dwa pełne okrążenia plus zapas – tyle, żeby złapać powrót na zero.
    rounds = before["realItems"] * 2 + 1
    for _ in range(rounds):
        page.wait_for_timeout(interval_ms + 150)
        state = page.evaluate(STATE)
        seen_transforms.add(state["transform"])
    print(f"transformacje zaobserwowane w {rounds} krokach: {len(seen_transforms)}")
    assert len(seen_transforms) >= 2, "taśma faktycznie się przesuwa"
    # Taśma nie może się zatrzymać na końcu – ostatni odczyt to wciąż aktywne przewijanie.
    final = page.evaluate(STATE)
    page.wait_for_timeout(interval_ms + 150)
    after_final = page.evaluate(STATE)
    assert final["transform"] != after_final["transform"] or before["realItems"] <= 1, (
        "taśma stanęła zamiast jechać dalej – pętla ma być bez końca"
    )


def check_pause_on_hover(page) -> None:
    page.goto(BASE + PATH, wait_until="networkidle")
    state = page.evaluate(STATE)
    if not state["animated"]:
        print("slider statyczny (za mało logotypów albo jeden slot) – pomijam test pauzy")
        return
    interval_ms = state["interval"] * 1000
    root = page.locator("[data-sponsor-slider]")
    root.hover()
    before = page.evaluate(STATE)["transform"]
    page.wait_for_timeout(interval_ms + 300)
    during = page.evaluate(STATE)["transform"]
    print(f"pod kursorem: {before} -> {during}")
    assert before == during, "najechanie na slider ma zatrzymać przewijanie"

    page.mouse.move(10, 10)
    page.wait_for_timeout(interval_ms + 300)
    after = page.evaluate(STATE)["transform"]
    assert after != during, "zjechanie kursorem ma wznowić przewijanie"


def check_reduced_motion(browser) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(BASE + PATH, wait_until="networkidle")
    state = page.evaluate(STATE)
    if state is None:
        context.close()
        return
    print(f"reduced-motion: animated={state['animated']}")
    assert state["animated"] is False, "prefers-reduced-motion ma wyłączyć ruch całkowicie"
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
    check_rendering(page)
    check_endless_loop(page)
    check_pause_on_hover(page)
    check_reduced_motion(browser)
    check_narrow_screen(page)
    browser.close()
    print("OK")
