"""Kontrola bramki zgody: czy przed kliknięciem naprawdę nic nie leci do Google'a.

Tego jednego nie sprawdzi pytest: cała obietnica polityki cookie („zanim klikniesz, przeglądarka
nie wysyła do Google'a żadnego żądania”) jest zdaniem o **przeglądarce**, a nie o odpowiedzi
serwera. Szablon może być bez zarzutu, a jedno nieuważne ``async`` w loaderze zamieni bramkę
w ozdobnik. Dlatego liczymy tu żądania sieciowe, a nie znaczniki w HTML-u.

Trzy stany, trzy asercje:

1. **wejście bez decyzji** – pasek widoczny, zero żądań do ``googletagmanager.com``,
2. **„Akceptuję wszystkie”** – żądanie do ``googletagmanager.com/gtag/js`` **wychodzi od razu**,
   bez przeładowania strony, a w ``localStorage`` stoi ``cookie-consent = "all"``. Sprawdzamy, że
   żądanie zostało **wystawione**, a nie że się powiodło: kontener e2e nie ma wyjścia do internetu
   i pobranie skryptu kończy się błędem sieci – co dla tego testu jest bez znaczenia,
3. **„Tylko niezbędne”** (świeży kontekst przeglądarki) – dalej zero żądań, a decyzja zapisana.

Uruchomienie (dev): najpierw wpisz identyfikator testowy, potem odpal kontrolę, na końcu wyczyść.
Identyfikator jest fikcyjny – żadne dane nigdzie nie dojdą, bo usługa ``G-TESTTEST1`` nie istnieje::

    docker compose exec -T web python manage.py shell -c \\
      "from apps.cms.models import SiteSettings; SiteSettings.objects.update(ga_measurement_id='G-TESTTEST1')"
    MSYS_NO_PATHCONV=1 docker compose -f docker-compose.yml -f docker-compose.dev.yml \\
      --profile e2e run --rm -T e2e sh -c "pip install -q -r requirements.txt; python check_consent.py"
    docker compose exec -T web python manage.py shell -c \\
      "from apps.cms.models import SiteSettings; SiteSettings.objects.update(ga_measurement_id='')"

Nagłówek CSP wypisujemy, ale na nim nie asertujemy: hosty Google'a dochodzą do polityki z pamięci
podręcznej o krótkim TTL (apps/cms/analytics.py), a ``QuerySet.update()`` z osobnego procesu jej
nie unieważnia. Samo wstrzyknięcie skryptu i tak działa – przenosi je ``'strict-dynamic'``.
"""

import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://web:8000")

GOOGLE_HOSTS = ("googletagmanager.com", "google-analytics.com", "analytics.google.com")

CONSENT_STATE = """(() => {
  const bar = document.querySelector('[data-cookie-notice]');
  const style = bar ? window.getComputedStyle(bar) : null;
  return {
    barVisible: bar !== null && style.display !== 'none' && bar.getBoundingClientRect().height > 0,
    consent: window.localStorage.getItem('cookie-consent'),
    consentAt: window.localStorage.getItem('cookie-consent-at'),
    metaId: (document.querySelector('meta[name="ga-measurement-id"]') || {}).content || null,
    loaderPresent: [...document.scripts].some(s => s.src.includes('analytics.js')),
    googleScripts: [...document.scripts].map(s => s.src).filter(s => s.includes('googletagmanager')),
  };
})()"""


def google_requests(requests: list[str]) -> list[str]:
    return [url for url in requests if any(host in url for host in GOOGLE_HOSTS)]


def open_page(browser, requests: list[str], errors: list[str]):
    """Świeży kontekst = pusty ``localStorage``: każdy scenariusz startuje bez decyzji."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.on("request", lambda request: requests.append(request.url))
    page.on(
        "console",
        lambda msg: errors.append(f"{msg.type}: {msg.text}") if msg.type in ("error", "warning") else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    return context, page


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        # --- 1. wejście bez decyzji ---------------------------------------------------------
        requests: list[str] = []
        errors: list[str] = []
        context, page = open_page(browser, requests, errors)
        response = page.goto(BASE + "/", wait_until="networkidle")
        print("CSP:", (response.headers.get("content-security-policy") or "")[:400])

        state = page.evaluate(CONSENT_STATE)
        print("przed decyzja:", state)
        assert state["metaId"], "brak <meta name=ga-measurement-id> – ustaw identyfikator w SiteSettings"
        assert state["loaderPresent"], "szablon nie dolaczyl static/js/analytics.js"
        assert state["barVisible"], "pasek zgody ma byc widoczny przed decyzja"
        assert state["consent"] is None, "decyzja nie moze istniec przed klknieciem"
        assert google_requests(requests) == [], f"zadanie do Google przed zgoda: {google_requests(requests)}"

        # --- 2. „Akceptuję wszystkie” --------------------------------------------------------
        page.click('[data-cookie-consent="all"]')
        page.wait_for_timeout(1500)

        after = page.evaluate(CONSENT_STATE)
        print("po zgodzie:", after)
        print("zadania do Google:", google_requests(requests))
        assert after["consent"] == "all", "zgoda nie zapisala sie w localStorage"
        assert after["consentAt"], "brak znacznika czasu decyzji"
        assert after["barVisible"] is False, "po decyzji pasek ma zniknac"
        loaded = [url for url in google_requests(requests) if "gtag/js" in url]
        # Bez przeładowania strony: pierwsza odsłona też ma być policzona.
        assert loaded, "po zgodzie nie wyszlo zadanie do googletagmanager.com/gtag/js"
        assert after["metaId"] in loaded[0], "adres gtag/js nie niesie identyfikatora z ustawien"
        context.close()

        # --- 3. „Tylko niezbędne” w świeżej przeglądarce -------------------------------------
        requests = []
        errors_necessary: list[str] = []
        context, page = open_page(browser, requests, errors_necessary)
        page.goto(BASE + "/", wait_until="networkidle")
        page.click('[data-cookie-consent="necessary"]')
        page.wait_for_timeout(1500)

        refused = page.evaluate(CONSENT_STATE)
        print("po odmowie:", refused)
        assert refused["consent"] == "necessary", "odmowa nie zapisala sie w localStorage"
        assert refused["barVisible"] is False, "po decyzji pasek ma zniknac"
        assert google_requests(requests) == [], f"zadanie do Google mimo odmowy: {google_requests(requests)}"

        # Wycofanie zgody: „Ustawienia cookies” w stopce otwiera pasek ponownie.
        page.click("[data-cookie-settings]")
        page.wait_for_timeout(300)
        reopened = page.evaluate(CONSENT_STATE)
        print("po ponownym otwarciu:", reopened)
        assert reopened["barVisible"], "„Ustawienia cookies” nie otwieraja paska"

        print("bledy konsoli:", (errors + errors_necessary) or "brak")
        context.close()
        browser.close()


main()
