"""Kontrola przyklejonego paska: pozycje menu pojawiają się w pasku dopiero po przewinięciu.

Sprawdzamy w Chromium bez okna (IntersectionObserver nie budzi się w ukrytej karcie, więc podgląd
w panelu przeglądarki nie nadaje się do tej kontroli): na górze strony pasek jest krótki i bez
pozycji menu, po przewinięciu poniżej menu serwisu pasek zostaje u góry i pokazuje pozycje,
a po powrocie na górę znów je chowa. Na wąskim ekranie (poniżej 561 px) pasek nie jest
przyklejony i pozycji nie pokazuje nigdy.
"""

import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://web:8000")
PATH = "/dokumenty/zoz/"

STATE = """() => {
  const bar = document.querySelector('.topbar--account');
  const nav = bar.querySelector('.nav--primary');
  return {
    stuck: bar.classList.contains('is-stuck'),
    navDisplay: getComputedStyle(nav).display,
    barTop: Math.round(bar.getBoundingClientRect().top),
    position: getComputedStyle(bar).position,
    links: [...nav.querySelectorAll('a')].map(a => a.textContent.trim()),
  };
}"""


def check(page, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(BASE + PATH, wait_until="networkidle")
    page.wait_for_timeout(300)
    top = page.evaluate(STATE)
    page.mouse.wheel(0, 1500)
    page.wait_for_timeout(500)
    scrolled = page.evaluate(STATE)
    page.mouse.wheel(0, -1500)
    page.wait_for_timeout(500)
    back = page.evaluate(STATE)
    print(f"{width}px  top: {top}")
    print(f"{width}px  scrolled: {scrolled}")
    print(f"{width}px  back: {back}")
    if width > 560:
        assert top["position"] == "sticky"
        assert top["stuck"] is False and top["navDisplay"] == "none", "na gorze pasek ma byc krotki"
        assert scrolled["stuck"] is True and scrolled["navDisplay"] == "flex", "po przewinieciu pozycje w pasku"
        assert scrolled["barTop"] == 0, "pasek przyklejony u gory"
        assert back["stuck"] is False and back["navDisplay"] == "none", "po powrocie pasek znow krotki"
    else:
        assert top["position"] == "static"
        assert scrolled["stuck"] is False and scrolled["navDisplay"] == "none"


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    for width in (1280, 700, 375):
        check(page, width)
    browser.close()
    print("OK")
