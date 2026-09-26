"""E2E przełączenia serwisu publicznego Wagtail ⇄ django CMS (docs/tasks/DJ-02.md § 11, DJ-02j).

Jedna faza scenariusza na uruchomienie; kolejność faz, przełączenia (``djcms_switch.sh``,
``djcms_cutover.sh``) i stos (osobny projekt compose z prawdziwym Caddym) prowadzi
``scripts/tests/djcms_primary_e2e.sh``. Skrypt chodzi w kontenerze Playwrighta w sieci ``edge``
stosu E2E: każda nazwa ``*.olimpiada.test`` rozwiązuje się na adres kontenera ``proxy`` (requests –
podmienione ``getaddrinfo``; Chromium – ``--host-resolver-rules``), więc żądania idą przez Caddy'ego
z wygenerowanym plikiem, a SNI i ``Host`` są prawdziwe. Certyfikaty z lokalnego CA Caddy'ego.

Trzy konkursy – trzy sposoby adresowania (DJ-02 § 0):
- główny (konkurs domyślny) – ``https://olimpiada.test/``,
- subdomena platformy – ``https://fizyczna.olimpiada.test/``,
- prefiks ścieżki – ``https://olimpiada.test/druga/``.

Fazy:
- ``preview``  (DJCMS_PRIMARY=0): strony publiczne z web (bez ``X-Djcms-Mode``); ciasteczko
  ``djcms_view=dj`` i przycisk na ``/djcms/preview/`` → djcms ``preview`` z noindex; ``dj.`` → 302;
  adresy aplikacji (logowanie, panele uczestnika i koordynatora, ``/cms/``, ``/api/``, ``/static/``,
  ``/documents/``) z web niezależnie od ciasteczka; ``/internal/*`` 404; ``sitemap.xml`` w podglądzie 404;
- ``primary``  (DJCMS_PRIMARY=1, Wagtail zamrożony): ``/``, ``/zadania/``, ``/wyniki/`` z djcms
  ``primary``, bez noindex, canonical na własny host i prefiks; ``robots.txt``/``sitemap.xml`` z hostem
  konkursu; ``/djcms/static/`` i ``/static/`` 200; ``djcms_view=wagtail`` → Wagtail; logowanie i panele
  z web; ``/cms/`` z banerem zamrożenia; ``/regulamin/`` → 301 (przekierowanie z importu);
- ``wagtail``  (po wycofaniu, edycja odmrożona): jak ``preview`` w skrócie + ``/cms/`` bez banera;
- ``load``     pętla GET ``/`` i ``/login/`` trzech konkursów do pliku ``--stop-file`` (≥ 200 żądań,
  zero 5xx i zerwań) – ruch w tle przełączenia;
- ``new-competition`` / ``disabled-competition`` ``--slug``: nowy konkurs w subdomenie platformy po
  przełączeniu – pierwsze wejście = strona djcms; po wyłączeniu – 404 (rejestr djcms).

Kod wyjścia: 0 – wszystkie kontrole fazy przeszły, 1 – którakolwiek nie (lista na wyjściu).
Zrzuty ekranu: ``artifacts/djcms/<faza>-*.png``.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import requests
import urllib3
from playwright.sync_api import sync_playwright

DOMAIN = os.environ.get("E2E_SITE_DOMAIN", "olimpiada.test")
PROXY_IP = socket.gethostbyname(os.environ.get("E2E_PROXY_HOST", "proxy"))
PASSWORD = os.environ.get("E2E_DEMO_PASSWORD", "Demo12345!")  # konta z manage.py seed_demo
PARTICIPANT = os.environ.get("E2E_PARTICIPANT_EMAIL", "uczestnik1@example.com")
COORDINATOR = os.environ.get("E2E_COORDINATOR_EMAIL", "koordynator@example.com")
ARTIFACTS = Path("artifacts/djcms")
MODE = "X-Djcms-Mode"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# requests/urllib3: *.olimpiada.test → adres proxy (SNI i Host zostają z adresu – jak curl --resolve).
_getaddrinfo = socket.getaddrinfo


def _resolve_to_proxy(host, *args, **kwargs):
    if isinstance(host, str) and (host == DOMAIN or host.endswith("." + DOMAIN)):
        host = PROXY_IP
    return _getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _resolve_to_proxy


@dataclass(frozen=True)
class Competition:
    label: str
    origin: str
    prefix: str  # "/" albo "/druga/"
    root_host: bool  # czy prefix == "/" (adresy hosta: robots.txt, /cms/, /static/)
    brand: str  # nazwa konkursu – marka w ramie stron aplikacji
    member: bool  # czy uczestnik demo (seed_demo) jest uczestnikiem tego konkursu

    def url(self, path: str = "") -> str:
        return f"{self.origin}{self.prefix}{path.lstrip('/')}"

    @property
    def host(self) -> str:
        return self.origin.split("://", 1)[1]


COMPETITIONS = [
    Competition("główny", f"https://{DOMAIN}", "/", True, "Olimpiada Kwantowa", True),
    Competition("subdomena", f"https://fizyczna.{DOMAIN}", "/", True, "Olimpiada Fizyczna", False),
    Competition("prefiks", f"https://{DOMAIN}", "/druga/", False, "Olimpiada Druga", False),
]


class Report:
    def __init__(self, phase: str):
        self.phase = phase
        self.failures: list[str] = []
        self.passed = 0

    def check(self, condition: bool, message: str, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"ok   [{self.phase}] {message}")
        else:
            self.failures.append(message)
            print(f"FAIL [{self.phase}] {message}{(' – ' + detail) if detail else ''}")
        return condition

    def finish(self) -> int:
        print(f"== {self.phase}: {self.passed} ok, {len(self.failures)} FAIL")
        return 1 if self.failures else 0


# --- HTTP -------------------------------------------------------------------------------------------


def get(url: str, cookie: str | None = None, **kwargs) -> requests.Response:
    headers = {"Accept": "text/html,*/*", "User-Agent": "djcms-e2e/1"}
    if cookie:
        headers["Cookie"] = cookie
    kwargs.setdefault("allow_redirects", False)
    try:
        # Lokalne CA Caddy'ego (local_certs) – certyfikatu nie ma czym sprawdzić; to test tras, nie TLS.
        return requests.get(url, headers=headers, verify=False, timeout=30, **kwargs)  # noqa: S501
    except requests.RequestException as exc:
        # Błąd połączenia/TLS to wynik kontroli (FAIL z opisem), a nie koniec przebiegu fazy.
        failed = requests.Response()
        failed.status_code = 0
        failed._content = f"{exc.__class__.__name__}: {exc}"[:300].encode()
        failed.url = url
        return failed


def mode(response: requests.Response) -> str:
    return response.headers.get(MODE, "") or "web"


def describe(response: requests.Response) -> str:
    if response.status_code == 0:
        return f"brak odpowiedzi – {response.text}"
    loc = response.headers.get("Location", "")
    return f"{response.status_code}, {mode(response)}{', → ' + loc if loc else ''}"


def meta_noindex(html: str) -> bool:
    return bool(re.search(r"<meta[^>]+name=[\"']robots[\"'][^>]+noindex", html, re.I))


def header_noindex(response: requests.Response) -> bool:
    return "noindex" in response.headers.get("X-Robots-Tag", "").lower()


def canonical(html: str) -> str:
    found = re.search(r"<link[^>]+rel=[\"']canonical[\"'][^>]*href=[\"']([^\"']+)", html, re.I) or re.search(
        r"<link[^>]+href=[\"']([^\"']+)[\"'][^>]*rel=[\"']canonical", html, re.I
    )
    return found.group(1) if found else ""


def first_link(html: str, pattern: str) -> str:
    found = re.search(pattern, html)
    return found.group(0) if found else ""


def check_common(report: Report, comp: Competition, *, primary: bool) -> None:
    """Adresy, które w obu trybach mają zachowanie niezależne od trybu i ciasteczka."""
    tag = comp.label
    # Adresy aplikacji: zawsze web (bez nagłówka trybu), także z ciasteczkiem podglądu.
    for cookie in ("djcms_view=dj", "djcms_view=wagtail"):
        r = get(comp.url("login/"), cookie)
        report.check(
            r.status_code == 200 and mode(r) == "web" and 'name="username"' in r.text,
            f"{tag}: {comp.prefix}login/ [{cookie}] → formularz logowania z web",
            describe(r),
        )
    r = get(comp.url("me/"))
    report.check(
        r.status_code in (301, 302) and mode(r) == "web" and "login" in r.headers.get("Location", ""),
        f"{tag}: {comp.prefix}me/ bez sesji → przekierowanie do logowania (web)",
        describe(r),
    )
    if comp.root_host:
        r = get(comp.url("static/css/app.css"), "djcms_view=dj")
        report.check(
            r.status_code == 200 and "text/css" in r.headers.get("Content-Type", ""),
            f"{tag}: /static/css/app.css 200",
            describe(r),
        )
        r = get(comp.url("api/schema/"), "djcms_view=dj")
        report.check(
            r.status_code < 500 and r.status_code != 404 and mode(r) == "web",
            f"{tag}: /api/schema/ z web",
            describe(r),
        )
        r = get(comp.url("api/competitions/"), "djcms_view=dj")
        report.check(
            r.status_code < 500 and mode(r) == "web", f"{tag}: /api/competitions/ z web", describe(r)
        )
        r = get(comp.url("cms/"), "djcms_view=dj")
        report.check(
            r.status_code in (301, 302) and mode(r) == "web" and "login" in r.headers.get("Location", ""),
            f"{tag}: /cms/ bez sesji → logowanie Wagtaila (web)",
            describe(r),
        )
        r = get(comp.url("documents/999999/nie-ma.pdf"), "djcms_view=dj")
        report.check(
            r.status_code == 404 and mode(r) == "web",
            f"{tag}: /documents/… z web (nieistniejący → 404 web)",
            describe(r),
        )
        for path in (
            "internal/djcms/v2/competitions",
            "internal/tls-allowed?domain=x." + DOMAIN,
            "internal/djcms/v1/chrome",
        ):
            r = get(comp.url(path), "djcms_view=dj")
            report.check(
                r.status_code == 404 and mode(r) == "web" and not r.text.strip(),
                f"{tag}: /{path} → pusta 404 z proxy",
                describe(r),
            )
    # Strona włączania podglądu – zawsze djcms, w trybie proxy.
    want = "primary" if primary else "preview"
    r = get(comp.url("djcms/preview/"))
    report.check(
        r.status_code == 200 and mode(r) == want,
        f"{tag}: {comp.prefix}djcms/preview/ → djcms ({want})",
        describe(r),
    )


def find_document(report: Report, comp: Competition, html_pages: list[str]) -> None:
    """Prawdziwy dokument Wagtaila (``/documents/<id>/<plik>``) z treści stron – z web, 200."""
    for html in html_pages:
        link = first_link(html, r"/documents/\d+/[^\"'\s<>]+")
        if link:
            r = get(comp.origin + link, "djcms_view=dj")
            report.check(
                r.status_code == 200 and mode(r) == "web",
                f"{comp.label}: dokument {link} 200 z web",
                describe(r),
            )
            return
    print(f"     ({comp.label}: brak odnośnika /documents/ w sprawdzonych stronach – pominięte)")


# --- przeglądarka -----------------------------------------------------------------------------------


def browser_context(playwright):
    browser = playwright.chromium.launch(
        args=[f"--host-resolver-rules=MAP {DOMAIN} {PROXY_IP}, MAP *.{DOMAIN} {PROXY_IP}"]
    )
    return browser


def shot(page, name: str) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(ARTIFACTS / f"{name}.png"), full_page=False)
    except Exception as exc:  # noqa: BLE001 - zrzut jest pomocą, nie kontrolą
        print(f"     (zrzut {name} nie powstał: {exc})")


def login(page, comp: Competition, email: str) -> None:
    page.goto(comp.url("login/"), wait_until="domcontentloaded")
    page.fill("#id_username", email)
    page.fill("#id_password", PASSWORD)
    page.get_by_role("button", name="Zaloguj").click()
    page.wait_for_load_state("domcontentloaded")


def check_logins(report: Report, browser, comp: Competition, phase: str, *, frozen: bool) -> None:
    tag = comp.label
    slug = comp.label.replace("ó", "o").replace("ł", "l")
    # Uczestnik: logowanie formularzem web, panel uczestnika, wylogowanie.
    ctx = browser.new_context(ignore_https_errors=True)
    page = ctx.new_page()
    try:
        login(page, comp, PARTICIPANT)
        response = page.goto(comp.url("me/"), wait_until="domcontentloaded")
        status = response.status if response else 0
        headers = response.headers if response else {}
        # Uczestnik konkursu domyślnego w cudzym konkursie: sesja działa (bez powrotu do logowania),
        # a panel odmawia (403 „Brak dostępu”) – to izolacja konkursów, nie trasa proxy.
        want_status = 200 if comp.member else 403
        report.check(
            status == want_status and "/login/" not in page.url and MODE.lower() not in headers,
            f"{tag}: uczestnik zalogowany – {comp.prefix}me/ {want_status} z web"
            + ("" if comp.member else " (nie jest uczestnikiem tego konkursu)"),
            f"status {status}, adres {page.url}",
        )
        report.check(
            comp.brand in page.content(), f"{tag}: {comp.prefix}me/ z marką „{comp.brand}”", page.title()
        )
        print(f"     {tag}: {comp.prefix}me/ – tytuł „{page.title()}”")
        shot(page, f"{phase}-{slug}-me")
        # Po zalogowaniu strona publiczna dalej według trybu (sesja nie zmienia trasy).
        response = page.goto(comp.url(), wait_until="domcontentloaded")
        want = "primary" if phase == "primary" else ""
        got = response.headers.get(MODE.lower(), "") if response else "?"
        report.check(
            got == want,
            f"{tag}: zalogowany – strona główna z {'djcms' if want else 'web'}",
            f"X-Djcms-Mode={got!r}",
        )
        home_has_logout = page.locator("form[action$='logout/']").count() > 0
        print(
            f"     {tag}: strona główna ({'djcms' if want else 'Wagtail'}) "
            f"{'ma' if home_has_logout else 'NIE ma'} formularza wylogowania"
        )
        # Wylogowanie z panelu (web) – rama stron publicznych djcms nie musi znać sesji aplikacji.
        page.goto(comp.url("me/"), wait_until="domcontentloaded")
        logout = page.locator("form[action$='logout/'] button, form[action$='logout/'] [type=submit]")
        if logout.count():
            logout.first.click()
            page.wait_for_load_state("domcontentloaded")
            response = page.goto(comp.url("me/"), wait_until="domcontentloaded")
            report.check(
                "/login/" in page.url,
                f"{tag}: wylogowanie – {comp.prefix}me/ znów wymaga logowania",
                page.url,
            )
        else:
            print(f"     ({tag}: brak formularza wylogowania na {page.url} – pominięte)")
    finally:
        ctx.close()
    # Koordynator: panel koordynatora; na hoście z /cms/ – panel Wagtaila i baner zamrożenia.
    ctx = browser.new_context(ignore_https_errors=True)
    page = ctx.new_page()
    try:
        login(page, comp, COORDINATOR)
        response = page.goto(comp.url("coordinator/"), wait_until="domcontentloaded")
        status = response.status if response else 0
        report.check(
            status == 200 and "/login/" not in page.url,
            f"{tag}: koordynator – {comp.prefix}coordinator/ 200",
            f"status {status}, adres {page.url}",
        )
        shot(page, f"{phase}-{slug}-coordinator")
        if comp.root_host:
            response = page.goto(comp.url("cms/"), wait_until="domcontentloaded")
            status = response.status if response else 0
            in_admin = status == 200 and "/cms/" in page.url and "login" not in page.url
            report.check(
                in_admin, f"{tag}: koordynator – /cms/ (Wagtail) 200", f"status {status}, adres {page.url}"
            )
            if in_admin:
                try:
                    page.wait_for_selector("[data-cms-freeze-banner]", timeout=5000 if frozen else 1500)
                    banner = True
                except Exception:  # noqa: BLE001 - brak banera = TimeoutError
                    banner = False
                report.check(
                    banner == frozen,
                    f"{tag}: /cms/ – baner zamrożenia {'widoczny' if frozen else 'nieobecny'}",
                    f"baner {'jest' if banner else 'nie ma'}",
                )
                shot(page, f"{phase}-{slug}-cms")
    finally:
        ctx.close()


def check_preview_cookie_flow(report: Report, browser, comp: Competition, *, primary: bool) -> None:
    """Przycisk na ``/djcms/preview/`` ustawia ``djcms_view`` na tym hoście; strona główna zmienia źródło."""
    tag = comp.label
    slug = comp.label.replace("ó", "o").replace("ł", "l")
    ctx = browser.new_context(ignore_https_errors=True)
    page = ctx.new_page()
    try:
        page.goto(comp.url("djcms/preview/"), wait_until="domcontentloaded")
        on_label, on_value, off_label = (
            ("Pokaż wersję Wagtail", "wagtail", "Wróć do django CMS")
            if primary
            else ("Włącz podgląd django CMS", "dj", "Wyłącz podgląd")
        )
        with page.expect_navigation():
            page.get_by_role("button", name=on_label).click()
        cookies = {c["name"]: c for c in ctx.cookies(comp.origin)}
        cookie = cookies.get("djcms_view")
        report.check(
            cookie is not None
            and cookie["value"] == on_value
            and cookie["domain"].lstrip(".") == comp.host
            and cookie["secure"]
            and cookie["httpOnly"],
            f"{tag}: /djcms/preview/ „{on_label}” → ciasteczko djcms_view={on_value}"
            " (host-only, Secure, HttpOnly)",
            str(cookie),
        )
        response = page.goto(comp.url(), wait_until="domcontentloaded")
        got = response.headers.get(MODE.lower(), "") if response else "?"
        want = "" if primary else "preview"
        report.check(
            got == want,
            f"{tag}: z ciasteczkiem {on_value} strona główna z {'web' if primary else 'djcms (preview)'}",
            f"X-Djcms-Mode={got!r}",
        )
        if not primary:
            report.check(
                page.locator("meta[name=robots][content*=noindex]").count() > 0,
                f"{tag}: podgląd – meta robots noindex",
                "",
            )
        shot(page, f"{'primary' if primary else 'preview'}-{slug}-cookie-{on_value}")
        page.goto(comp.url("djcms/preview/"), wait_until="domcontentloaded")
        with page.expect_navigation():
            page.get_by_role("button", name=off_label).click()
        cookies = {c["name"]: c["value"] for c in ctx.cookies(comp.origin)}
        report.check(
            "djcms_view" not in cookies or cookies["djcms_view"] not in ("dj", "wagtail"),
            f"{tag}: „{off_label}” kasuje ciasteczko",
            str(cookies),
        )
        response = page.goto(comp.url(), wait_until="domcontentloaded")
        got = response.headers.get(MODE.lower(), "") if response else "?"
        want = "primary" if primary else ""
        report.check(
            got == want,
            f"{tag}: bez ciasteczka strona główna znów z {'djcms' if primary else 'web'}",
            f"X-Djcms-Mode={got!r}",
        )
    finally:
        ctx.close()


# --- fazy -------------------------------------------------------------------------------------------


def phase_preview(report: Report, *, after_rollback: bool = False) -> None:
    for comp in COMPETITIONS:
        tag = comp.label
        pages = []
        for path in ("", "zadania/", "wyniki/"):
            r = get(comp.url(path))
            pages.append(r.text)
            report.check(
                r.status_code == 200
                and mode(r) == "web"
                and not header_noindex(r)
                and "cookie" in r.headers.get("Vary", "").lower(),
                f"{tag}: {comp.prefix}{path} → Wagtail (web, bez noindex, Vary: Cookie)",
                describe(r),
            )
            r = get(comp.url(path), "djcms_view=dj")
            report.check(
                r.status_code == 200 and mode(r) == "preview" and header_noindex(r) and meta_noindex(r.text),
                f"{tag}: {comp.prefix}{path} [djcms_view=dj] → djcms preview z noindex (nagłówek i meta)",
                describe(r),
            )
            r = get(comp.url(path), "djcms_view=wagtail")
            report.check(
                r.status_code == 200 and mode(r) == "web",
                f"{tag}: {comp.prefix}{path} [djcms_view=wagtail] → web",
                describe(r),
            )
        if comp.root_host:
            r = get(comp.url("robots.txt"))
            report.check(
                r.status_code < 500 and mode(r) == "web",
                f"{tag}: /robots.txt bez ciasteczka z web",
                describe(r),
            )
            r = get(comp.url("robots.txt"), "djcms_view=dj")
            report.check(
                r.status_code == 200
                and mode(r) == "preview"
                and re.search(r"(?m)^Disallow: /\s*$", r.text) is not None,
                f"{tag}: /robots.txt [dj] → djcms preview „Disallow: /”",
                describe(r),
            )
            r = get(comp.url("sitemap.xml"), "djcms_view=dj")
            report.check(
                r.status_code == 404 and mode(r) == "preview",
                f"{tag}: /sitemap.xml [dj] → 404 w podglądzie",
                describe(r),
            )
        check_common(report, comp, primary=False)
        find_document(report, comp, pages)
    r = get(f"https://dj.{DOMAIN}/zadania/?a=1")
    report.check(
        r.status_code == 302
        and r.headers.get("Location") == f"https://{DOMAIN}/djcms/preview/?next=/zadania/?a=1",
        f"dj.{DOMAIN}/zadania/?a=1 → 302 na stronę włączenia podglądu",
        describe(r),
    )
    phase = "wagtail" if after_rollback else "preview"
    with sync_playwright() as playwright:
        browser = browser_context(playwright)
        for comp in COMPETITIONS:
            check_preview_cookie_flow(report, browser, comp, primary=False)
            check_logins(report, browser, comp, phase, frozen=False)
        browser.close()


def phase_primary(report: Report) -> None:
    for comp in COMPETITIONS:
        tag = comp.label
        for path in ("", "zadania/", "wyniki/"):
            r = get(comp.url(path))
            html = r.text
            want_canonical = comp.url(path)
            report.check(
                r.status_code == 200
                and mode(r) == "primary"
                and "cookie" in r.headers.get("Vary", "").lower(),
                f"{tag}: {comp.prefix}{path} → djcms primary (Vary: Cookie)",
                describe(r),
            )
            report.check(
                not header_noindex(r) and not meta_noindex(html),
                f"{tag}: {comp.prefix}{path} – bez noindex (nagłówek i meta)",
                r.headers.get("X-Robots-Tag", ""),
            )
            report.check(
                canonical(html) == want_canonical,
                f"{tag}: {comp.prefix}{path} – canonical {want_canonical}",
                canonical(html) or "brak",
            )
            r = get(comp.url(path), "djcms_view=wagtail")
            report.check(
                r.status_code == 200 and mode(r) == "web",
                f"{tag}: {comp.prefix}{path} [djcms_view=wagtail] → Wagtail (web)",
                describe(r),
            )
        home = get(comp.url())
        link = first_link(home.text, r"/djcms/static/[^\"'\s<>?]+\.css")
        if report.check(bool(link), f"{tag}: strona djcms linkuje /djcms/static/…css", ""):
            r = get(comp.origin + link)
            report.check(
                r.status_code == 200 and "immutable" in r.headers.get("Cache-Control", ""),
                f"{tag}: {link} 200 (immutable)",
                describe(r),
            )
        robots_url = comp.url("robots.txt")
        r = get(robots_url)
        sitemap_url = comp.url("sitemap.xml")
        if comp.root_host:
            report.check(
                r.status_code == 200
                and mode(r) == "primary"
                and f"Sitemap: {sitemap_url}" in r.text
                and re.search(r"(?m)^Disallow: /\s*$", r.text) is None,
                f"{tag}: /robots.txt → djcms primary z „Sitemap: {sitemap_url}”, bez „Disallow: /”",
                describe(r) + " " + r.text[:200].replace("\n", " | "),
            )
        r = get(sitemap_url)
        report.check(
            r.status_code == 200 and mode(r) == "primary" and f"<loc>{comp.url()}" in r.text,
            f"{tag}: {comp.prefix}sitemap.xml → 200 z adresami {comp.url()}",
            describe(r) + " " + r.text[:160].replace("\n", " "),
        )
        check_common(report, comp, primary=True)
    main = COMPETITIONS[0]
    r = get(main.url("regulamin/"))
    report.check(
        r.status_code in (301, 308)
        and r.headers.get("Location", "").rstrip("/").endswith("/dokumenty/regulamin")
        and mode(r) == "primary",
        "główny: /regulamin/ → 301 /dokumenty/regulamin/ (przekierowanie z importu, djcms)",
        describe(r),
    )
    r = get(f"https://dj.{DOMAIN}/zadania/?a=1")
    report.check(
        r.status_code == 302 and r.headers.get("Location") == f"https://{DOMAIN}/zadania/?a=1",
        f"dj.{DOMAIN}/zadania/?a=1 → 302 na ten sam adres domeny głównej",
        describe(r),
    )
    r = get(main.url("to-nie-istnieje-e2e/"))
    report.check(
        r.status_code == 404 and mode(r) == "primary",
        "główny: nieznana strona → 404 z djcms (z ramą)",
        describe(r),
    )
    with sync_playwright() as playwright:
        browser = browser_context(playwright)
        for comp in COMPETITIONS:
            check_preview_cookie_flow(report, browser, comp, primary=True)
            check_logins(report, browser, comp, "primary", frozen=True)
        browser.close()


def phase_load(report: Report, stop_file: Path, minimum: int) -> None:
    targets = []
    for comp in COMPETITIONS:
        targets += [comp.url(), comp.url("login/")]
    statuses: Counter = Counter()
    modes: Counter = Counter()
    errors: list[str] = []
    transitions = 0
    last_home_mode = {}
    session = requests.Session()
    count = 0
    started = time.monotonic()
    while count < minimum or not stop_file.exists():
        url = targets[count % len(targets)]
        count += 1
        try:
            r = session.get(
                url,
                verify=False,
                timeout=30,
                allow_redirects=False,
                headers={"User-Agent": "djcms-e2e-load/1"},
            )
            statuses[r.status_code] += 1
            m = mode(r)
            modes[m] += 1
            if not url.endswith("login/"):
                if last_home_mode.get(url) not in (None, m):
                    transitions += 1
                last_home_mode[url] = m
            if r.status_code >= 500:
                errors.append(f"{url}: {r.status_code}")
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc.__class__.__name__}")
            session = requests.Session()
        if time.monotonic() - started > 900:
            errors.append("pętla przerwana po 15 min bez pliku-stopu")
            break
    elapsed = time.monotonic() - started
    print(
        f"     {count} żądań w {elapsed:.0f} s, kody {dict(statuses)}, upstream {dict(modes)},"
        f" zmian źródła strony głównej: {transitions}"
    )
    for line in errors[:20]:
        print(f"     błąd: {line}")
    report.check(count >= minimum, f"ruch: co najmniej {minimum} żądań", str(count))
    report.check(not errors, "ruch: zero odpowiedzi 5xx i zerwanych połączeń", f"{len(errors)} błędów")


def phase_new_competition(report: Report, slug: str, *, disabled: bool) -> None:
    url = f"https://{slug}.{DOMAIN}/"
    deadline = time.monotonic() + 150  # rejestr djcms + bufory API/zgody TLS w web (60 s)
    r = None
    while time.monotonic() < deadline:
        try:
            r = get(url)
            if disabled and r.status_code == 404:
                break
            if not disabled and r.status_code == 200 and mode(r) == "primary":
                break
        except requests.RequestException as exc:
            print(f"     {url}: {exc.__class__.__name__} – ponawiam")
        time.sleep(2)
    print(
        f"     {slug}: wynik po {150 - max(0.0, deadline - time.monotonic()):.0f} s"
        f" ({describe(r) if r is not None else 'brak'})"
    )
    if disabled:
        report.check(
            r is not None and r.status_code == 404,
            f"{slug}: po wyłączeniu konkursu {url} → 404",
            describe(r) if r is not None else "brak odpowiedzi",
        )
        return
    ok = r is not None and r.status_code == 200 and mode(r) == "primary"
    report.check(
        ok,
        f"{slug}: pierwsze wejście na {url} → strona djcms (primary, drzewo startowe)",
        describe(r) if r is not None else "brak odpowiedzi",
    )
    if ok:
        title = re.search(r"<title>([^<]*)</title>", r.text)
        print(f"     {slug}: tytuł „{title.group(1).strip() if title else '?'}”")
        report.check(canonical(r.text) == url, f"{slug}: canonical {url}", canonical(r.text) or "brak")
        r2 = get(f"https://{slug}.{DOMAIN}/login/")
        report.check(r2.status_code == 200 and mode(r2) == "web", f"{slug}: /login/ z web", describe(r2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--phase",
        required=True,
        choices=["preview", "primary", "wagtail", "load", "new-competition", "disabled-competition"],
    )
    parser.add_argument("--stop-file", default="artifacts/djcms/load.stop")
    parser.add_argument("--min-requests", type=int, default=200)
    parser.add_argument("--slug", default="ekologiczna")
    args = parser.parse_args()
    report = Report(args.phase)
    print(f"== faza {args.phase}: proxy {PROXY_IP}, domena {DOMAIN}")
    if args.phase == "preview":
        phase_preview(report)
    elif args.phase == "wagtail":
        phase_preview(report, after_rollback=True)
    elif args.phase == "primary":
        phase_primary(report)
    elif args.phase == "load":
        phase_load(report, Path(args.stop_file), args.min_requests)
    else:
        phase_new_competition(report, args.slug, disabled=args.phase == "disabled-competition")
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
