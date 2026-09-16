"""Kontrola podstaw dostępności na stronach publicznych.

**Dlaczego nie axe-core.** Standardowy sposób (wstrzyknięcie ``axe.min.js`` z CDN-a i wywołanie
``axe.run()``) jest tu niewykonalny z dwóch powodów naraz: polityka bezpieczeństwa treści serwisu
nie dopuszcza skryptu inline ani obcego ``script-src`` (patrz ``config/settings/base.py``), a sieć
compose i tak nie ma wyjścia na CDN zza firmowego proxy. Zamiast obchodzić własną CSP w teście –
czyli sprawdzać stronę w konfiguracji, która nigdy nie trafi do ludzi – sprawdzamy pięć rzeczy
ręcznie. Nie zastępują one audytu, ale łapią regresje, które zdarzają się najczęściej przy
dopisywaniu szablonów:

  1. **``alt`` na każdym ``<img>``** – także pusty (``alt=""``) przy obrazku dekoracyjnym; brak
     atrybutu to nie to samo, co pusty i czytnik ekranu przeczyta wtedy nazwę pliku,
  2. **etykieta przy każdej kontrolce formularza** – ``<label for>``, ``aria-label``,
     ``aria-labelledby`` albo opakowujący ``<label>``; pola ukryte i ``type="hidden"`` pomijamy,
  3. **dokładnie jeden ``<h1>``** na stronę,
  4. **skip link** jako pierwszy element strony, prowadzący do istniejącego identyfikatora
     i **widoczny po sfokusowaniu** (odnośnik odsunięty poza ekran, który nie wraca na Tab, jest
     gorszy niż jego brak: obiecuje skrót, którego nie ma),
  5. **sensowna kolejność fokusu** – pierwsze ``Tab`` trafia w skip link, a kolejne kilkanaście
     przystanków idzie zgodnie z kolejnością w DOM (czyli nikt nie wstawił ``tabindex`` dodatniego,
     który przestawia kolejność czytania względem kolejności widzenia).

Uzupełniająco: każda ``<nav>`` ma nazwę (``aria-label``/``aria-labelledby``) – na stronie jest ich
pięć i bez nazw lista landmarków jest bezużyteczna.

    docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm -T \
        -e E2E_BASE_URL=http://web:8000 e2e python /e2e/check_a11y.py

UWAGA: jak ``check_mobile.py`` – przebieg wymaga sieci compose i obrazu ``e2e``; zza firmowego
proxy uruchomić się nie da, więc skrypt jest napisany „na sucho”.
"""

import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://web:8000").rstrip("/")

PATHS = ("/", "/login/", "/register/", "/zadania/", "/statystyki/")

AUDIT = """() => {
  const out = { images: [], controls: [], h1: 0, navs: [], skip: null };

  for (const img of document.querySelectorAll('img')) {
    if (!img.hasAttribute('alt')) {
      out.images.push((img.getAttribute('src') || '?').split('/').pop());
    }
  }

  const labelled = (el) => {
    if (el.getAttribute('aria-label')) return true;
    if (el.getAttribute('aria-labelledby')) return true;
    if (el.closest('label')) return true;
    if (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) return true;
    // Przycisk opisuje własna treść, a pole wyszukiwania bywa opisane placeholderem – ten drugi
    // przypadek NIE jest etykietą (znika po wpisaniu znaku), więc go tu nie honorujemy.
    if (el.tagName === 'BUTTON') return el.textContent.trim().length > 0;
    if (el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type)) {
      return Boolean(el.value);
    }
    return false;
  };

  for (const el of document.querySelectorAll('input, select, textarea, button')) {
    if (el.type === 'hidden') continue;
    // Pułapka na boty (.hp-field) jest celowo poza ekranem i celowo bez etykiety – nie jest
    // polem dla człowieka.
    if (el.classList.contains('hp-field') || el.closest('.hp-field')) continue;
    if (el.closest('[hidden]')) continue;
    if (!labelled(el)) {
      out.controls.push(`${el.tagName.toLowerCase()}[name=${el.name || '?'}]`);
    }
  }

  out.h1 = document.querySelectorAll('h1').length;

  for (const nav of document.querySelectorAll('nav')) {
    if (!nav.getAttribute('aria-label') && !nav.getAttribute('aria-labelledby')) {
      out.navs.push((nav.getAttribute('class') || 'nav').slice(0, 40));
    }
  }

  const skip = document.querySelector('a.skip-link');
  if (skip) {
    const href = skip.getAttribute('href') || '';
    out.skip = {
      first: document.body.firstElementChild === skip,
      href,
      targetExists: href.startsWith('#') && Boolean(document.getElementById(href.slice(1))),
    };
  }
  return out;
}"""

#: Po sfokusowaniu skip link musi wjechać w kadr. Mierzymy jego prostokąt – ujemne ``left``
#: znaczy, że dalej stoi poza ekranem.
SKIP_RECT = """() => {
  const skip = document.querySelector('a.skip-link');
  skip.focus();
  const rect = skip.getBoundingClientRect();
  return {
    focused: document.activeElement === skip,
    left: Math.round(rect.left),
    top: Math.round(rect.top),
    width: Math.round(rect.width),
  };
}"""

#: Kolejność fokusu. Zbieramy pozycję każdego przystanku w kolejności DOM; rosnący ciąg znaczy,
#: że kolejność Tab pokrywa się z kolejnością czytania.
FOCUS_ORDER = """() => {
  const all = [...document.querySelectorAll('*')];
  const el = document.activeElement;
  return {
    index: all.indexOf(el),
    tag: el ? el.tagName.toLowerCase() : null,
    cls: el ? (el.getAttribute('class') || '').slice(0, 40) : null,
    positiveTabindex: Number(el && el.getAttribute('tabindex')) > 0,
  };
}"""

TAB_STOPS = 15


def audit(page, path: str) -> int:
    response = page.goto(BASE + path, wait_until="networkidle")
    if response is not None and response.status == 404:
        print(f"  {path:<14} POMINIETE (404 – brak strony w tym srodowisku)")
        return 0
    problems = 0
    state = page.evaluate(AUDIT)

    if state["images"]:
        print(f"  {path}: obrazki bez alt: {state['images']}")
        problems += 1
    if state["controls"]:
        print(f"  {path}: kontrolki bez etykiety: {state['controls']}")
        problems += 1
    if state["h1"] != 1:
        print(f"  {path}: liczba <h1> = {state['h1']} (ma byc 1)")
        problems += 1
    if state["navs"]:
        print(f"  {path}: <nav> bez aria-label: {state['navs']}")
        problems += 1

    skip = state["skip"]
    if skip is None:
        print(f"  {path}: brak odnosnika .skip-link")
        problems += 1
    else:
        if not skip["first"]:
            print(f"  {path}: skip link nie jest pierwszym elementem <body>")
            problems += 1
        if not skip["targetExists"]:
            print(f"  {path}: skip link prowadzi do {skip['href']}, ktorego nie ma na stronie")
            problems += 1
        rect = page.evaluate(SKIP_RECT)
        if rect["left"] < 0 or rect["width"] == 0:
            print(f"  {path}: skip link po sfokusowaniu nadal poza kadrem: {rect}")
            problems += 1

    # Kolejność fokusu: startujemy od początku dokumentu, żeby pierwszy Tab trafił w skip link.
    page.evaluate("() => document.body.focus()")
    page.keyboard.press("Tab")
    first = page.evaluate(FOCUS_ORDER)
    if "skip-link" not in (first["cls"] or ""):
        print(f"  {path}: pierwszy Tab trafia w {first['tag']}.{first['cls']}, a nie w skip link")
        problems += 1

    previous = first["index"]
    for _ in range(TAB_STOPS):
        page.keyboard.press("Tab")
        stop = page.evaluate(FOCUS_ORDER)
        if stop["positiveTabindex"]:
            print(f"  {path}: dodatni tabindex na {stop['tag']}.{stop['cls']} – przestawia kolejnosc")
            problems += 1
        if stop["index"] < previous:
            print(f"  {path}: fokus cofa sie w DOM na {stop['tag']}.{stop['cls']}")
            problems += 1
            break
        previous = stop["index"]

    if problems == 0:
        print(f"  {path:<14} OK")
    return problems


def main() -> None:
    failures = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        for path in PATHS:
            failures += audit(page, path)
        browser.close()
    assert failures == 0, f"{failures} usterek dostepnosci"
    print("OK")


main()
