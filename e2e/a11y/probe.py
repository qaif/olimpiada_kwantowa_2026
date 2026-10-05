"""Szybki podgląd naruszeń axe dla podanych adresów (narzędzie autora, nie test).

    python a11y/probe.py /login/ /register/           # gość
    A11Y_LOGIN=koordynator@example.com python a11y/probe.py /coordinator/

Wypisuje reguły z wpływem, liczbą węzłów i trzema pierwszymi selektorami. Do przeglądu ręcznego
przy naprawianiu szablonów – przebieg CI idzie przez ``pytest a11y`` (scripts/a11y.sh).
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
from axe import run_axe  # noqa: E402

BASE = os.environ.get("E2E_BASE_URL", "http://web:8000").rstrip("/")


def main(paths: list[str]) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        login = os.environ.get("A11Y_LOGIN")
        if login:
            page.goto(f"{BASE}{os.environ.get('A11Y_LOGIN_PATH', '/login/')}")
            page.fill("input[name=username], input[name=login], input[type=email]", login)
            page.fill("input[type=password]", os.environ.get("A11Y_PASSWORD", "Demo12345!"))
            page.locator("main form button[type=submit]").first.click()
            page.wait_for_load_state()
            print("po logowaniu:", page.url)
        for path in paths:
            response = page.goto(f"{BASE}{path}", wait_until="networkidle")
            result = run_axe(page)
            status = response.status if response else "?"
            print(f"\n=== {path} [{status}] -> {page.url}")
            for v in result["violations"]:
                print(f"  {v['impact']:9} {v['id']} x{v['count']}: {v['help']}")
                for node in v["nodes"][:3]:
                    print(f"      {node['target']}  ::  {node['html'][:140]}")
                    if v["id"] == "color-contrast":
                        print(f"        {node['summary'][:200]}")
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:] or ["/"])
