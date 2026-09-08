"""Zrzuty ekranu całego serwisu – materiał do oceny wizualnej szaty graficznej.

Uruchomienie (ta sama ścieżka, co scenariusz E2E – kontener widzi aplikację pod ``web:8000``)::

    docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile e2e \
        run --rm e2e python screenshots.py

Skrypt nie jest testem: niczego nie asertuje i nie zmienia stanu bazy poza logowaniem. Chodzi po
kolejnych rolach (gość, uczestnik, recenzent, koordynator, komisja) i zapisuje pełnostronicowe PNG
do ``e2e/artifacts/screens/``. Katalog jest bind-mountowany, więc pliki są na hoście od razu.

Uwagi implementacyjne:

- ``command`` usługi ``e2e`` w compose robi ``pip install -r requirements.txt`` przed pytestem;
  przy ``run ... python screenshots.py`` ten krok nie zachodzi, więc skrypt sam sprawdza, czy
  Playwright jest dostępny, i doinstalowuje zależności, gdy go brakuje,
- odliczanie do deadline'u i skan antywirusowy są asynchroniczne, więc przed zrzutem czekamy na
  ``networkidle`` plus krótką chwilę na dorysowanie warstwy pdf.js,
- wariant ciemny to ``color_scheme="dark"`` w kontekście przeglądarki – aplikacja nie ma
  przełącznika motywu, cały wariant siedzi w ``@media (prefers-color-scheme: dark)``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

LOGGER = logging.getLogger("screens")

BASE_URL = os.environ.get("E2E_BASE_URL", "http://web:8000").rstrip("/")
OUTPUT_DIR = Path(__file__).parent / "artifacts" / "screens"
REQUIREMENTS = Path(__file__).parent / "requirements.txt"

DEMO_PASSWORD = "Demo12345!"
PARTICIPANT_EMAIL = "uczestnik1@example.com"
REVIEWER_EMAILS = ("recenzent1@example.com", "recenzent2@example.com")
COORDINATOR_EMAIL = "koordynator@example.com"
APPEALS_EMAIL = "komisja1@example.com"

DESKTOP = {"width": 1366, "height": 900}
MOBILE = {"width": 390, "height": 844}

#: Adres pierwszej recenzji wyciągamy z listy przydziałów – identyfikatory zależą od przebiegu E2E.
REVIEW_LINK = re.compile(r'href="(/review/\d+/)"')
RESULTS_LINK = re.compile(r'href="(/results/\d+/)"')
#: Identyfikator etapu z karty na pulpicie koordynatora – ekrany terminów i zadań są pod nim.
STAGE_EDIT_LINK = re.compile(r'href="/coordinator/stages/(\d+)/edit/"')
#: Adres konta komisji zakładanego przez ``e2e/test_full_cycle.py`` (losowy sufiks, stałe hasło).
E2E_COMMITTEE_EMAIL = re.compile(r"komisja-[0-9a-f]+@example\.test")
E2E_COMMITTEE_PASSWORD = "Komisja-Odwolawcza-2026"  # noqa: S105 - konto testowe z e2e/test_full_cycle.py


def ensure_playwright() -> None:
    """Doinstalowuje zależności, jeśli obraz nie ma jeszcze modułu ``playwright``."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        LOGGER.info("Instaluję zależności z %s", REQUIREMENTS)
        # Argumenty są stałymi z tego modułu – nic tu nie pochodzi z zewnątrz.
        subprocess.check_call(  # noqa: S603
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)]
        )


def shoot(page, path: str, name: str, scroll: str = "bottom") -> bool:
    """Otwiera adres i zapisuje pełnostronicowy PNG. Zwraca ``False`` przy odpowiedzi 4xx/5xx.

    ``scroll`` rozstrzyga, gdzie na zrzucie wyląduje warstwa ``position: sticky`` – patrz niżej.
    Strony z przyklejonym paskiem akcji chcą ``"bottom"``, strony z przyklejonym spisem treści
    (``/dokumenty/regulamin/``) ``"top"``, bo tam sticky jest nawigacją, a nie stopką.
    """
    response = page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
    status = response.status if response is not None else 0
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception as exc:  # noqa: BLE001 - pdf.js trzyma połączenie; zrzut i tak ma sens
        LOGGER.debug("networkidle nie nastąpiło dla %s: %s", path, exc)
    # Przewinięcie na sam dół przed zrzutem: elementy ``position: sticky`` (pasek akcji recenzenta)
    # przy zrzucie pełnostronicowym renderują się tam, gdzie zastała je pozycja przewinięcia –
    # bez tego pasek wisiałby w połowie strony, nad treścią formularza.
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(600)
    if scroll == "top":
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    target = OUTPUT_DIR / f"{name}.png"
    page.screenshot(path=str(target), full_page=True)
    LOGGER.info("%-34s %s -> %s", path, status, target.name)
    return 200 <= status < 400


def shoot_open_menu(page, name: str) -> None:
    """Zrzut nagłówka z rozwiniętą pozycją „Dokumenty”.

    Na desktopie lista otwiera się też najechaniem, ale ``hover`` nie utrzymuje się do zrzutu –
    klikamy więc ``<summary>``, czyli tę samą drogę, którą ma czytelnik klawiatury i dotyku.
    Zrzut nie jest pełnostronicowy: lista wisi pod nagłówkiem i na pełnej stronie ginęłaby
    w skali.
    """
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
    summary = page.locator("summary.nav-menu__summary").first
    if summary.count() == 0:
        LOGGER.warning("Brak rozwijanej pozycji menu – pomijam zrzut %s.", name)
        return
    summary.click()
    page.wait_for_timeout(400)
    target = OUTPUT_DIR / f"{name}.png"
    page.screenshot(
        path=str(target), clip={"x": 0, "y": 0, "width": page.viewport_size["width"], "height": 520}
    )
    LOGGER.info("%-34s %s -> %s", "/ (menu rozwinięte)", 200, target.name)


def login(page, email: str, password: str = DEMO_PASSWORD) -> None:
    page.goto(f"{BASE_URL}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", email)
    page.fill("#id_password", password)
    page.get_by_role("button", name="Zaloguj").click()
    page.wait_for_load_state("domcontentloaded")


def is_logged_in(page) -> bool:
    """Po udanym logowaniu w nagłówku jest formularz wylogowania, a nie link „Zaloguj”."""
    return page.locator("form.nav__logout").count() > 0


def new_context(browser, viewport: dict, color_scheme: str = "light"):
    return browser.new_context(
        viewport=viewport,
        locale="pl-PL",
        color_scheme=color_scheme,
        ignore_https_errors=True,
    )


def guest_pages(browser) -> None:
    context = new_context(browser, DESKTOP)
    page = context.new_page()
    for path, name in (
        ("/", "01-strona-glowna"),
        ("/aktualnosci/", "02-aktualnosci"),
        ("/zadania/", "03-zadania"),
        ("/wyniki/", "04-wyniki"),
        ("/archiwum/", "05-archiwum"),
        ("/login/", "06-logowanie"),
        ("/register/", "07-rejestracja"),
        ("/password-reset/", "06a-reset-hasla"),
    ):
        shoot(page, path, name)

    # Treści przeniesione ze starej strony (``manage.py seed_legacy_content``). Strony ze spisem
    # sekcji zrzucamy od góry – spis jest tam ``position: sticky``, tak samo jak w regulaminie.
    for path, name, scroll in (
        ("/o-olimpiadzie/", "01a-o-olimpiadzie", "top"),
        ("/kontakt/", "01b-kontakt", "top"),
        ("/harmonogram/", "01c-harmonogram", "top"),
        ("/dokumenty/rodo/", "01d-rodo", "top"),
        # Skład komitetów: dokument z kartą „Do pobrania” nad treścią (PDF organizatora).
        ("/dokumenty/komitety/", "01e-komitety", "top"),
        # Standardy ochrony małoletnich: drugi dokument przepisany z PDF-u organizatora.
        ("/dokumenty/standardy-ochrony-maloletnich/", "01f-standardy", "top"),
        # Partnerzy: strona jest opublikowana z pustą listą, więc zrzut pokazuje przede wszystkim
        # pusty stan i sekcję „Zostań partnerem” – to one są tu całą treścią.
        ("/partnerzy/", "01g-partnerzy", "top"),
    ):
        shoot(page, path, name, scroll=scroll)

    # Spis dokumentów organizatora (``DocumentIndexPage``) i rozwinięta pozycja menu, która do
    # niego prowadzi – razem pokazują całą drogę czytelnika do dokumentu.
    shoot(page, "/dokumenty/", "02a-dokumenty", scroll="top")
    shoot_open_menu(page, "02a1-menu-dokumenty")

    # Regulamin (manage.py seed_regulamin): spis rozdziałów jest ``position: sticky``, więc
    # zrzut robimy od góry – inaczej spis wylądowałby na dole obrazu, obok stopki.
    shoot(page, "/dokumenty/regulamin/", "02c-regulamin", scroll="top")

    # Artykuł: pierwszy wpis z newsroomu, jeśli w ogóle jakiś jest.
    page.goto(f"{BASE_URL}/aktualnosci/", wait_until="domcontentloaded")
    article = page.locator(".news-card h2 a").first
    if article.count() > 0:
        href = article.get_attribute("href")
        if href:
            shoot(page, href, "02b-aktualnosc")

    # Publiczna tabela wyników: preferujemy /results/1/, a gdy nie ma publikacji – pierwszy
    # odnośnik ze strony „Wyniki”.
    if not shoot(page, "/results/1/", "08-wyniki-etapu"):
        page.goto(f"{BASE_URL}/wyniki/", wait_until="domcontentloaded")
        match = RESULTS_LINK.search(page.content())
        if match:
            shoot(page, match.group(1), "08-wyniki-etapu")
    context.close()


def participant_pages(browser) -> None:
    context = new_context(browser, DESKTOP)
    page = context.new_page()
    login(page, PARTICIPANT_EMAIL)
    if not is_logged_in(page):
        LOGGER.warning("Nie udało się zalogować jako %s – pomijam panel uczestnika.", PARTICIPANT_EMAIL)
        context.close()
        return
    shoot(page, "/me/", "09-panel-uczestnika")
    context.close()


def reviewer_pages(browser) -> None:
    for email in REVIEWER_EMAILS:
        context = new_context(browser, DESKTOP)
        page = context.new_page()
        login(page, email)
        if not is_logged_in(page):
            LOGGER.warning("Nie udało się zalogować jako %s.", email)
            context.close()
            continue
        shoot(page, "/review/", "10-lista-recenzji")
        match = REVIEW_LINK.search(page.content())
        if match:
            shoot(page, match.group(1), "11-ocena-pracy")
            context.close()
            return
        LOGGER.warning("Lista przydziałów %s jest pusta – próbuję kolejnego recenzenta.", email)
        context.close()


def coordinator_pages(browser) -> None:
    context = new_context(browser, DESKTOP)
    page = context.new_page()
    login(page, COORDINATOR_EMAIL)
    if not is_logged_in(page):
        LOGGER.warning("Nie udało się zalogować jako %s.", COORDINATOR_EMAIL)
        context.close()
        return
    shoot(page, "/coordinator/", "12-panel-koordynatora")
    # Terminy etapu i zadania: identyfikator bierzemy z karty na pulpicie, bo zależy od seeda.
    match = STAGE_EDIT_LINK.search(page.content())
    if match:
        stage_id = match.group(1)
        shoot(page, f"/coordinator/stages/{stage_id}/edit/", "12a-terminy-etapu", scroll="top")
        shoot(page, f"/coordinator/stages/{stage_id}/problems/", "12b-zadania-etapu", scroll="top")
    else:
        LOGGER.warning("Pulpit koordynatora nie ma kart etapów – pomijam terminy i zadania.")
    context.close()


def find_e2e_committee_account(browser) -> tuple[str, str] | None:
    """Konto komisji założone przez scenariusz E2E ma losowy adres – szukamy go u koordynatora.

    ``seed_demo`` nie tworzy konta komisji odwoławczej: powstaje ono dopiero w ``test_full_cycle``
    (na kod zaproszenia ``--appeals``), z losowym adresem i stałym hasłem. Lista członków komitetu
    w panelu koordynatora jest jedynym miejscem, z którego da się ten adres odczytać.
    """
    context = new_context(browser, DESKTOP)
    page = context.new_page()
    try:
        login(page, COORDINATOR_EMAIL)
        if not is_logged_in(page):
            return None
        page.goto(f"{BASE_URL}/coordinator/", wait_until="domcontentloaded")
        match = E2E_COMMITTEE_EMAIL.search(page.content())
        return (match.group(0), E2E_COMMITTEE_PASSWORD) if match else None
    finally:
        context.close()


def appeals_pages(browser) -> None:
    """Panel komisji – konto powstaje dopiero w scenariuszu E2E, więc bywa, że go nie ma."""
    candidates = [(APPEALS_EMAIL, DEMO_PASSWORD)]
    found = find_e2e_committee_account(browser)
    if found is not None:
        candidates.append(found)

    for email, password in candidates:
        context = new_context(browser, DESKTOP)
        page = context.new_page()
        login(page, email, password)
        if is_logged_in(page):
            shoot(page, "/appeals/", "13-panel-komisji")
            context.close()
            return
        context.close()
    LOGGER.warning("Nie znalazłem konta komisji odwoławczej – pomijam panel komisji.")


def mobile_pages(browser) -> None:
    context = new_context(browser, MOBILE)
    page = context.new_page()
    shoot(page, "/", "m01-strona-glowna-mobile")
    shoot(page, "/login/", "m02-logowanie-mobile")
    # Menu ma dziewięć pozycji – na 390 px sprawdzamy, jak się zawija.
    shoot(page, "/o-olimpiadzie/", "m04-o-olimpiadzie-mobile", scroll="top")
    # Poniżej 900 px lista dokumentów rozwija się w przepływie, a nie jako warstwa nad treścią.
    shoot(page, "/dokumenty/", "m05-dokumenty-mobile", scroll="top")
    shoot_open_menu(page, "m06-menu-dokumenty-mobile")
    login(page, PARTICIPANT_EMAIL)
    if is_logged_in(page):
        shoot(page, "/me/", "m03-panel-uczestnika-mobile")
    context.close()


def dark_pages(browser) -> None:
    context = new_context(browser, DESKTOP, color_scheme="dark")
    page = context.new_page()
    shoot(page, "/", "d01-strona-glowna-dark")
    login(page, PARTICIPANT_EMAIL)
    if is_logged_in(page):
        shoot(page, "/me/", "d02-panel-uczestnika-dark")
    context.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUTPUT_DIR.glob("*.png"):
        stale.unlink()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            guest_pages(browser)
            participant_pages(browser)
            reviewer_pages(browser)
            coordinator_pages(browser)
            appeals_pages(browser)
            mobile_pages(browser)
            dark_pages(browser)
        finally:
            browser.close()

    saved = sorted(path.name for path in OUTPUT_DIR.glob("*.png"))
    LOGGER.info("Zapisano %d zrzutów w %s:", len(saved), OUTPUT_DIR)
    for name in saved:
        LOGGER.info("  %s", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
