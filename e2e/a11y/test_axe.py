"""axe-core (WCAG 2.1 A/AA) na głównych ekranach obu konkursów i obu motywów.

Każdy ekran z ``pages.SCREENS`` to jeden test. Test przewraca się na naruszeniu o wpływie
``critical``/``serious``, którego nie ma w ``baseline.json`` (klucz ``<ekran>|<reguła>``); naruszenia
``moderate``/``minor`` i przyjęte trafiają wyłącznie do raportu ``e2e/artifacts/a11y/report.md``.
"""

from __future__ import annotations

import pytest
from axe import run_axe
from pages import SCREENS, Screen

from conftest import DESKTOP, login, record, shot, totp


def _prepare(page, screen: Screen, base: str, data: dict) -> None:
    if screen.action in {"submit-empty", "bad-login"}:
        form = page.locator("main form:has(button[type=submit])").first
        if screen.action == "bad-login":
            page.fill("#id_username", "nie-ma-takiego@example.com")
            page.fill("#id_password", "zle-haslo-123")
        # Walidacja przeglądarki (``required``) zatrzymałaby wysyłkę przed serwerem – a badamy
        # właśnie stan z komunikatami błędów serwera (czytnik ekranu i stare przeglądarki).
        form.evaluate("f => { f.noValidate = true; }")
        form.locator("button[type=submit]").first.click()
        page.wait_for_load_state()
    elif screen.action in {"twofa", "twofa-bad"}:
        page.fill("#id_username", data["twofa_email"])
        page.fill("#id_password", data["password"])
        page.locator("main form button[type=submit]").first.click()
        page.wait_for_load_state()
        assert "/login/2fa/" in page.url, f"konto z 2FA nie trafiło na drugi krok ({page.url})"
        if screen.action == "twofa-bad":
            wrong = "000000" if totp(data["totp_secret"]) != "000000" else "111111"
            page.fill("input[name=code]", wrong)
            page.locator("main form button[type=submit]").first.click()
            page.wait_for_load_state()
    elif screen.action == "twofa-codes":
        # Włączenie 2FA: klucz z ekranu, kod TOTP, potwierdzenie → ekran kodów zapasowych.
        secret = page.locator("code.code-block").first.inner_text().replace(" ", "").strip()
        page.fill("#id_code", totp(secret))
        page.locator("main form button[type=submit]").first.click()
        page.wait_for_load_state()
        assert "/account/2fa/codes/" in page.url, f"po włączeniu 2FA brak ekranu kodów ({page.url})"
    elif screen.action == "high-contrast":
        page.locator("button[name=high_contrast]").first.click()
        page.wait_for_load_state()
        assert page.evaluate("() => document.documentElement.dataset.contrast") == "high"


@pytest.mark.parametrize("screen", SCREENS, ids=[s.id for s in SCREENS])
def test_axe(screen: Screen, browser, base, data, contexts, accepted):
    fresh = screen.role == "anon"
    if fresh:
        context = browser.new_context(viewport=DESKTOP, locale="pl-PL")
        if screen.cookies:
            context.add_cookies([{"name": k, "value": v, "url": base} for k, v in screen.cookies.items()])
    else:
        context = contexts(screen.role)
    page = context.new_page()
    try:
        path = screen.path.format(**data)
        response = page.goto(f"{base}{path}", wait_until="networkidle")
        assert response is not None and response.status < 400, f"{path}: HTTP {response and response.status}"
        _prepare(page, screen, base, data)
        page.wait_for_load_state("networkidle")
        result = run_axe(page)
        failing = record(screen.id, path, result, accepted)
        if failing:
            shot(page, f"FAIL-{screen.id}")
        details = "\n".join(
            f"  {v['impact']} {v['id']} ×{v['count']}: {v['help']}\n"
            + "\n".join(f"      {n['target']}  {n['summary'][:220]}" for n in v["nodes"][:4])
            for v in failing
        )
        assert not failing, f"{screen.id} ({page.url}) – nowe naruszenia WCAG 2.1 A/AA:\n{details}"
    finally:
        page.close()
        if fresh:
            context.close()


def test_login_helper_handles_2fa(browser, base, data):
    """Logowanie konta z 2FA kodem TOTP kończy się w serwisie, a nie na drugim kroku (sprawdza helper)."""
    context = browser.new_context(viewport=DESKTOP)
    try:
        login(context, base, data["twofa_email"], data["password"], secret=data["totp_secret"])
    finally:
        context.close()
