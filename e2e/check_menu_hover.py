"""Kontrola rozwijanego menu „Dokumenty”: lista ma zostać widoczna, gdy kursor przechodzi
z nagłówka przez odstęp na listę (regresja: przerwa gasiła :hover)."""

import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://web:8000")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1366, "height": 800})
    page.goto(BASE + "/", wait_until="networkidle")
    summary = page.locator(".nav-menu > summary").first
    lst = page.locator(".nav-menu > .nav-menu__list").first
    sb = summary.bounding_box()
    cx = sb["x"] + sb["width"] / 2
    page.mouse.move(cx, sb["y"] + sb["height"] / 2)
    page.wait_for_timeout(200)
    print("nad naglowkiem:", lst.is_visible())
    lb = lst.bounding_box()
    gap_y = (sb["y"] + sb["height"] + lb["y"]) / 2 if lb else sb["y"] + sb["height"] + 6
    for y in range(int(sb["y"] + sb["height"]) + 1, int(gap_y) + 1, 2):
        page.mouse.move(cx, y)
    page.wait_for_timeout(200)
    list_top = lb["y"] if lb else -1
    print(f"w przerwie (y={gap_y:.0f}, list top={list_top:.0f}):", lst.is_visible())
    if lb:
        for y in range(int(gap_y), int(lb["y"]) + 20, 2):
            page.mouse.move(cx, y)
        page.wait_for_timeout(200)
        print("na liscie:", lst.is_visible())
        page.screenshot(path="artifacts/menu-hover.png", clip={"x": 0, "y": 0, "width": 1366, "height": 420})
    browser.close()
