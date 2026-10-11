"""Kontrole, których axe nie robi: klawiatura, widoczny fokus, menu bez JavaScriptu, tryb wysokiego
kontrastu, kierunek RTL, przepływ treści przy 320 px i powiększeniu 200 % (WCAG 2.1: 2.1.1, 2.4.1,
2.4.3, 2.4.7, 1.4.10, 1.4.4, 1.3.2).

axe sprawdza drzewo DOM w jednej chwili; nie naciska klawiszy i nie zmienia szerokości okna. Te testy
robią to, co zrobiłaby osoba bez myszy albo z powiększeniem – i przewracają się na tym, co by ją
zatrzymało.
"""

from __future__ import annotations

import pytest

from conftest import DESKTOP, shot

#: Opis elementu z fokusem – do komunikatów błędu.
ACTIVE = """() => {
  const a = document.activeElement;
  if (!a || a === document.body) return 'body';
  const cls = (a.getAttribute('class') || '').split(/\\s+/).filter(Boolean).slice(0, 2).join('.');
  const text = (a.innerText || a.value || a.getAttribute('aria-label') || '').trim().slice(0, 30);
  return `${a.tagName.toLowerCase()}${cls ? '.' + cls : ''} „${text}”`;
}"""

#: Dwie klatki i jedno zadanie: strona zdąży odpowiedzieć na przesunięcie fokusu (obserwatory, style).
SETTLE = (
    "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 0))))"
)

#: Czy element z fokusem ma widoczny wskaźnik: obrys albo cień (pierścień), który znika bez fokusu.
#:
#: Pomiar czeka dwie klatki: Playwright naciska Tab szybciej niż człowiek, a strona reaguje na
#: przesunięcie fokusu asynchronicznie (przewinięcie → IntersectionObserver przyklejonego paska,
#: przeglądarka zdejmuje fokus z elementu, który właśnie zniknął). Bez czekania test mierzył stan
#: w pół drogi – element już schowany (0×0), a opis brany osobnym wywołaniem pokazywał ``body``.
#: Opis elementu idzie tu, w tym samym pomiarze, żeby komunikat dotyczył tego, co zmierzono.
#:
#: ``body`` po Tabie: jeśli dokument stracił fokus (``hasFocus() === false``), Tab wyszedł za
#: ostatni przystanek do przeglądarki – to zawinięcie, nie błąd strony. Jeśli dokument ma fokus,
#: a aktywny jest ``body``, strona **zgubiła** fokus (element zniknął spod kursora) – to błąd.
FOCUS_STYLE = (
    """async () => {
  await ("""
    + SETTLE
    + """)();
  const a = document.activeElement;
  const describe = """
    + ACTIVE
    + """;
  if (!a || a === document.body || a === document.documentElement) {
    if (!document.hasFocus()) return {ok: true, skip: true};
    return {ok: false, visible: false, el: 'body (fokus zgubiony)', outline: '-', shadow: '-'};
  }
  const cs = getComputedStyle(a);
  const outline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2;
  const shadow = cs.boxShadow && cs.boxShadow !== 'none';
  const rect = a.getBoundingClientRect();
  return {ok: outline || shadow, visible: rect.width > 0 && rect.height > 0, el: describe(),
          size: Math.round(rect.width) + '×' + Math.round(rect.height),
          outline: cs.outlineStyle + ' ' + cs.outlineWidth + ' ' + cs.outlineColor, shadow: cs.boxShadow};
}"""
)

OVERFLOW = """() => {
  const doc = document.scrollingElement || document.documentElement;
  const wide = [];
  if (doc.scrollWidth > window.innerWidth + 1) {
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      const framed = el.closest('.scroll, .table-scroll, [data-scroll]');
      if (r.right > window.innerWidth + 1 && r.width > 0 && !framed) {
        const cs = getComputedStyle(el);
        if (cs.position === 'fixed' || cs.visibility === 'hidden') continue;
        const cls = (el.getAttribute('class') || '').split(' ')[0];
        wide.push(`${el.tagName.toLowerCase()}.${cls} (${Math.round(r.right)}px)`);
        if (wide.length >= 5) break;
      }
    }
  }
  return {scrollWidth: doc.scrollWidth, viewport: window.innerWidth, wide};
}"""


def _context(browser, base, *, cookies=None, **kwargs):
    options = {"viewport": DESKTOP, "locale": "pl-PL", **kwargs}
    context = browser.new_context(**options)
    if cookies:
        context.add_cookies([{"name": k, "value": v, "url": base} for k, v in cookies.items()])
    return context


def _tab_until(page, selector: str, limit: int = 40) -> bool:
    for _ in range(limit):
        page.keyboard.press("Tab")
        if page.evaluate("(sel) => document.activeElement && document.activeElement.matches(sel)", selector):
            return True
    return False


def _autofocused(page) -> bool:
    """Strona z ``autofocus``: kursor stoi od razu w polu, które musi leżeć w ``<main>``."""
    state = page.evaluate(
        "() => { const a = document.activeElement; if (!a || a === document.body) return null;"
        " return !!document.querySelector('#tresc')?.contains(a); }"
    )
    if state is None:
        return False
    assert state, f"autofocus poza <main>: {page.evaluate(ACTIVE)}"
    return True


SKIP_PAGES = (
    ("anon", "/"),
    ("anon", "/login/"),
    ("anon", "/iqo/"),
    ("anon", "/iqo/login/"),
    ("participant", "/me/"),
    ("coordinator", "/coordinator/"),
    ("leader", "/iqo/delegation/"),
)


@pytest.mark.parametrize(("role", "path"), SKIP_PAGES, ids=[f"{r}{p}" for r, p in SKIP_PAGES])
def test_skip_link_is_first_visible_and_moves_focus_to_main(browser, base, contexts, role, path):
    context = _context(browser, base) if role == "anon" else contexts(role)
    page = context.new_page()
    try:
        page.goto(f"{base}{path}", wait_until="networkidle")
        if _autofocused(page):
            # Formularz logowania stawia kursor w pierwszym polu (``autofocus``) – to świadomy skrót,
            # a pole jest w <main>. Skip link sprawdzamy wtedy po fokusie nadanym wprost.
            page.focus(".skip-link")
        else:
            page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.classList.contains('skip-link')"), (
            f"{path}: pierwszy Tab trafia w {page.evaluate(ACTIVE)}, a nie w „Przejdź do treści”"
        )
        box = page.evaluate(
            "() => { const r = document.activeElement.getBoundingClientRect();"
            " return {x: r.x, y: r.y, w: r.width, h: r.height, vw: innerWidth, vh: innerHeight}; }"
        )
        assert box["w"] > 0 and box["h"] > 0 and box["x"] >= 0 and box["y"] >= 0, (
            f"{path}: skip link poza ekranem {box}"
        )
        assert box["x"] + box["w"] <= box["vw"] + 1, f"{path}: skip link wystaje poza ekran {box}"
        page.keyboard.press("Enter")
        page.keyboard.press("Tab")
        inside = page.evaluate("() => !!document.querySelector('#tresc')?.contains(document.activeElement)")
        assert inside, f"{path}: po skip linku następny Tab trafia w {page.evaluate(ACTIVE)}, poza <main>"
    finally:
        page.close()
        if role == "anon":
            context.close()


FOCUS_PAGES = (
    ("anon", "/", 30),
    ("anon", "/register/", 40),
    ("anon", "/iqo/", 30),
    ("anon", "/iqo/login/", 25),
    ("participant", "/me/", 30),
    ("coordinator", "/coordinator/", 40),
    ("leader", "/iqo/delegation/", 30),
)


@pytest.mark.parametrize(("role", "path", "stops"), FOCUS_PAGES, ids=[f"{r}{p}" for r, p, _ in FOCUS_PAGES])
def test_every_tab_stop_has_visible_focus(browser, base, contexts, role, path, stops):
    context = _context(browser, base) if role == "anon" else contexts(role)
    page = context.new_page()
    failures = []
    try:
        page.goto(f"{base}{path}", wait_until="networkidle")
        for _ in range(stops):
            page.keyboard.press("Tab")
            state = page.evaluate(FOCUS_STYLE)
            if state.get("skip"):
                continue
            if not state["ok"] or not state["visible"]:
                failures.append(
                    f"{state['el']} ({state.get('size', '0×0')}) – outline {state.get('outline')},"
                    f" cień {state.get('shadow')}"
                )
        if failures:
            shot(page, f"FAIL-focus-{role}{path.replace('/', '_')}")
        assert not failures, f"{path}: przystanki bez widocznego fokusu:\n  " + "\n  ".join(failures)
    finally:
        page.close()
        if role == "anon":
            context.close()


def test_sticky_bar_keeps_focused_item_when_menu_returns(browser, base):
    """Pozycja przyklejonego paska z fokusem nie znika, gdy menu serwisu wraca na ekran.

    Pozycje ``.nav--primary`` są widoczne tylko w przyklejonym pasku (static/js/sticky-bar.js).
    Przewinięcie do góry odkleja pasek; gdyby fokus stał na jednej z tych pozycji, przeglądarka
    zgubiłaby go na ``<body>``. Pasek ma czekać z odklejeniem, aż fokus z niego wyjdzie.
    """
    context = _context(browser, base, viewport={"width": 1280, "height": 500})
    page = context.new_page()
    try:
        page.goto(f"{base}/iqo/login/", wait_until="networkidle")
        page.evaluate("() => window.scrollTo(0, document.scrollingElement.scrollHeight)")
        page.wait_for_selector(".topbar--account.is-stuck", state="attached")
        link = page.locator(".topbar--account .nav--primary a").first
        link.focus()
        page.evaluate("() => window.scrollTo(0, 0)")
        page.evaluate(SETTLE)
        page.evaluate(SETTLE)
        state = page.evaluate(
            "() => { const a = document.activeElement; const r = a.getBoundingClientRect();"
            " return {primary: !!a.closest('.nav--primary'), w: r.width, h: r.height}; }"
        )
        assert state["primary"] and state["w"] > 0 and state["h"] > 0, (
            f"po przewinięciu do góry fokus z paska trafił w {page.evaluate(ACTIVE)} ({state})"
        )
        page.keyboard.press("Shift+Tab")
        page.wait_for_selector(".topbar--account:not(.is-stuck)", state="attached")
        assert page.evaluate(
            "() => document.activeElement.matches('.topbar--account a, .topbar--account summary')"
        ), f"Shift+Tab z paska trafia w {page.evaluate(ACTIVE)}"
    finally:
        context.close()


@pytest.mark.parametrize("path", ["/", "/iqo/"], ids=["classic", "iqo"])
def test_nav_dropdown_opens_from_keyboard(browser, base, path):
    """Rozwijana grupa menu („Dokumenty”) otwiera się klawiszem i jej pozycje dostają fokus."""
    context = _context(browser, base)
    page = context.new_page()
    try:
        page.goto(f"{base}{path}", wait_until="networkidle")
        assert _tab_until(page, "nav[aria-label] summary.nav-menu__summary"), (
            f"{path}: Tab nie dochodzi do rozwijanej grupy menu"
        )
        page.keyboard.press("Enter")
        assert page.evaluate("() => document.activeElement.parentElement.open"), (
            f"{path}: Enter nie otwiera grupy"
        )
        page.keyboard.press("Tab")
        state = page.evaluate(
            "() => { const a = document.activeElement; const r = a.getBoundingClientRect();"
            " return {link: a.matches('.nav-menu__link'), w: r.width, h: r.height}; }"
        )
        assert state["link"] and state["w"] > 0 and state["h"] > 0, (
            f"{path}: po otwarciu grupy fokus trafia w {page.evaluate(ACTIVE)} ({state})"
        )
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Enter")
        assert not page.evaluate("() => document.activeElement.parentElement.open"), (
            f"{path}: Enter nie zamyka grupy"
        )
    finally:
        context.close()


def test_iqo_mobile_menu_works_without_javascript(browser, base):
    """Przycisk „Menu” motywu IQO to ``<details>``: działa klawiaturą przy wyłączonym JavaScripcie."""
    context = _context(browser, base, viewport={"width": 375, "height": 800}, java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(f"{base}/iqo/")
        nav = page.locator(".iqo-header__nav .iqo-nav")
        assert not nav.is_visible(), "na 375 px menu IQO powinno być zwinięte za przyciskiem"
        assert _tab_until(page, ".iqo-menu__toggle", 15), "Tab nie dochodzi do przycisku „Menu”"
        page.keyboard.press("Enter")
        assert nav.is_visible(), "Enter na „Menu” nie pokazuje nawigacji (bez JS)"
        page.keyboard.press("Tab")
        assert page.evaluate("() => !!document.activeElement.closest('.iqo-header__nav')"), (
            f"po otwarciu menu Tab trafia w {page.evaluate(ACTIVE)}, a nie w pozycję menu"
        )
    finally:
        context.close()


def test_classic_mobile_nav_reachable_without_javascript(browser, base):
    """Motyw klasyczny nie chowa menu na telefonie – pozycje są widoczne i osiągalne Tabem bez JS."""
    context = _context(browser, base, viewport={"width": 375, "height": 800}, java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(f"{base}/")
        links = page.locator("nav.nav--cms a.nav__link")
        assert links.count() > 0 and links.first.is_visible(), "na 375 px bez JS nie widać menu serwisu"
        assert _tab_until(page, "nav.nav--cms a.nav__link", 25), "Tab nie dochodzi do menu serwisu"
    finally:
        context.close()


def test_coordinator_menu_reachable_from_keyboard(contexts, base):
    """Kolumna menu koordynatora (``<details>`` otwierane arkuszem/skryptem) – pozycje dostają fokus."""
    page = contexts("coordinator").new_page()
    try:
        page.goto(f"{base}/coordinator/", wait_until="networkidle")
        assert page.evaluate("() => document.querySelector('details.panel-nav').open"), (
            "na szerokim ekranie menu panelu ma być otwarte (atrybut open) – czytnik ekranu i axe"
        )
        assert _tab_until(page, ".panel-nav__link", 40), "Tab nie dochodzi do pozycji menu panelu"
    finally:
        page.close()


@pytest.mark.parametrize("path", ["/", "/iqo/"], ids=["classic", "iqo"])
def test_high_contrast_toggle_from_keyboard(browser, base, path):
    context = _context(browser, base)
    page = context.new_page()
    try:
        page.goto(f"{base}{path}", wait_until="networkidle")
        assert _tab_until(page, "button[name=high_contrast]", 25), (
            "Tab nie dochodzi do przełącznika kontrastu"
        )
        label = page.evaluate("() => document.activeElement.getAttribute('aria-label')")
        assert label, "przełącznik kontrastu bez nazwy dostępnej"
        with page.expect_navigation():
            page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("() => document.documentElement.dataset.contrast") == "high"
        pressed = page.locator("button[name=high_contrast]").first.get_attribute("aria-pressed")
        assert pressed == "true", f"po włączeniu aria-pressed={pressed!r}"
        shot(page, f"high-contrast{path.replace('/', '_')}")
    finally:
        context.close()


@pytest.mark.parametrize("path", ["/iqo/", "/iqo/login/", "/iqo/visa/verify/"])
@pytest.mark.parametrize("width", [375, 1280])
def test_arabic_is_rtl_without_horizontal_scroll(browser, base, path, width):
    context = _context(
        browser, base, cookies={"django_language": "ar"}, viewport={"width": width, "height": 900}
    )
    page = context.new_page()
    try:
        page.goto(f"{base}{path}", wait_until="networkidle")
        attrs = page.evaluate("() => [document.documentElement.lang, document.documentElement.dir]")
        assert attrs == ["ar", "rtl"], f"{path}: <html lang/dir> = {attrs}"
        state = page.evaluate(OVERFLOW)
        assert state["scrollWidth"] <= state["viewport"] + 1, (
            f"{path} ({width}px, RTL): przewijanie w poziomie {state}"
        )
        if _autofocused(page):
            page.focus(".skip-link")
        else:
            page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.classList.contains('skip-link')")
        box = page.evaluate("() => document.activeElement.getBoundingClientRect().toJSON()")
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (
            f"{path}: skip link poza ekranem w RTL {box}"
        )
        if width == 375:
            shot(page, f"rtl{path.replace('/', '_')}")
    finally:
        context.close()


REFLOW_PAGES = (
    ("anon", "/"),
    ("anon", "/login/"),
    ("anon", "/register/"),
    ("anon", "/dokumenty/deklaracja-dostepnosci/"),
    ("anon", "/iqo/"),
    ("anon", "/iqo/login/"),
    ("anon", "/iqo/visa/verify/"),
    ("participant", "/me/"),
    ("leader", "/iqo/delegation/"),
)


@pytest.mark.parametrize(("role", "path"), REFLOW_PAGES, ids=[f"{r}{p}" for r, p in REFLOW_PAGES])
@pytest.mark.parametrize(
    "viewport",
    [{"width": 320, "height": 640}, {"width": 640, "height": 450}],
    ids=["320px", "zoom200"],
)
def test_reflow_without_horizontal_scroll(browser, base, data, role, path, viewport):
    """WCAG 1.4.10: przy 320 px (i 1280 px powiększonym do 200 %) strona nie przewija się w bok.

    Tabele przewijają się we własnej ramce (``.scroll``) – to jest dozwolone (wyjątek dla treści
    dwuwymiarowych), dlatego elementy w ramce nie są liczone. Kontekst ról jest tu własny, bo
    szerokość okna należy do kontekstu, a konteksty ról z ``conftest`` są desktopowe.
    """
    from conftest import login

    context = _context(
        browser, base, viewport=viewport, device_scale_factor=2 if viewport["width"] == 640 else 1
    )
    try:
        if role == "participant":
            login(context, base, "uczestnik1@example.com", data["password"])
        elif role == "leader":
            login(context, base, data["iqo"]["leader_email"], data["password"], prefix=data["iqo_prefix"])
        page = context.new_page()
        page.goto(f"{base}{path}", wait_until="networkidle")
        state = page.evaluate(OVERFLOW)
        if state["scrollWidth"] > state["viewport"] + 1:
            shot(page, f"FAIL-reflow-{viewport['width']}{path.replace('/', '_')}")
        assert state["scrollWidth"] <= state["viewport"] + 1, f"{path} @ {viewport['width']} px: {state}"
    finally:
        context.close()


ERROR_FORMS = (
    ("anon", "/register/"),
    ("anon", "/password-reset/"),
    ("participant", "/account/password/"),
    ("participant", "/account/2fa/"),
    ("leader", "/iqo/delegation/students/add/"),
)

LINKED = r"""() => {
  const invalid = [...document.querySelectorAll('[aria-invalid="true"]')];
  const broken = [];
  for (const el of invalid) {
    const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
    const text = ids.map((id) => document.getElementById(id)?.textContent.trim() || '').join(' ');
    if (!text) broken.push(el.name || el.id);
  }
  return {invalid: invalid.length, broken};
}"""


@pytest.mark.parametrize(("role", "path"), ERROR_FORMS, ids=[f"{r}{p}" for r, p in ERROR_FORMS])
def test_form_errors_are_linked_to_fields(browser, base, contexts, role, path):
    """WCAG 3.3.1/1.3.1: pole z błędem ma ``aria-invalid`` i ``aria-describedby`` do treści błędu."""
    context = _context(browser, base) if role == "anon" else contexts(role)
    page = context.new_page()
    try:
        page.goto(f"{base}{path}", wait_until="networkidle")
        form = page.locator("main form:has(button[type=submit])").first
        form.evaluate("f => { f.noValidate = true; }")
        form.locator("button[type=submit]").first.click()
        page.wait_for_load_state("networkidle")
        state = page.evaluate(LINKED)
        assert state["invalid"] > 0, f"{path}: po wysłaniu pustego formularza żadne pole nie ma aria-invalid"
        assert not state["broken"], (
            f"{path}: pola z błędem bez opisu błędu (aria-describedby): {state['broken']}"
        )
    finally:
        page.close()
        if role == "anon":
            context.close()
