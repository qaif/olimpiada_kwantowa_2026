"""Kontrola wyszukiwarki szkół w rejestracji: podpowiedzi, wybór z listy, ukryte ``school_id``.

Uruchamiana przeciw dowolnemu adresowi (``E2E_BASE_URL``), także produkcyjnemu – zgłoszenie
organizatora brzmiało „podałem szkołę z listy, a system kazał wybrać z listy”, czyli wybór nie
zapisał ``school_id``. Przyczyną był komponent Alpine ładowany z CDN-u: kiedy jednego adresu nie
było, nie działał cały blok. Skrypt jest teraz czystym JS-em serwowanym z własnego adresu, więc
ta kontrola pilnuje dwóch rzeczy naraz: że podpowiedzi w ogóle są i że nic tu nie zależy od
zewnętrznej biblioteki (asercja na ``window.Alpine`` celowo **nie** istnieje – Alpine może być
na stronie dla innych widoków i wyszukiwarce nic do tego).

Zbieramy też błędy konsoli (CSP, 404 na skrypcie) – wypisujemy je zawsze, bo bywają jedynym
śladem po zablokowanym zasobie.
"""

import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://web:8000")

#: Widoczność mierzona **rysowaniem**, a nie atrybutem ``hidden``. Pierwsza wersja tej kontroli
#: pytała o ``el.hidden`` i przepuściła prawdziwą usterkę: atrybut był ustawiony, a reguła
#: ``.form p { display: flex }`` i tak rysowała pole (``[hidden]`` z arkusza przeglądarki przegrywa
#: z każdym selektorem klasy). Wysokość zero i brak ``offsetParent`` biorą i atrybut, i arkusz.
FREE_VISIBLE = """(() => {
  const f = document.querySelector('.school-picker__free');
  if (!f) return null;
  return f.offsetParent !== null && f.getBoundingClientRect().height > 0;
})()"""


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []

        def note_console(msg) -> None:
            if msg.type in ("error", "warning"):
                errors.append(f"{msg.type}: {msg.text}")

        page.on("console", note_console)
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
        page.goto(BASE + "/register/", wait_until="networkidle")
        page.wait_for_timeout(500)

        state = page.evaluate(
            """() => ({
              pickerEl: !!document.querySelector('[data-school-picker]'),
              alpineAttrs: document.querySelector('.school-picker').outerHTML.includes('x-ref'),
              freeVisible: FREE_VISIBLE(),
              freeHiddenAttr: document.querySelector('.school-picker__free').hidden,
              scripts: [...document.scripts].map(s => s.src.split('/').pop()).filter(Boolean),
            })""".replace("FREE_VISIBLE()", FREE_VISIBLE)
        )
        print("stan poczatkowy:", state)
        assert state["pickerEl"], "brak korzenia [data-school-picker]"
        assert state["alpineAttrs"] is False, "w bloku zostaly atrybuty Alpine'a"
        # Skrypt wystartował i schował wolny tekst – bez JS-u pole jest widoczne od początku.
        assert state["freeHiddenAttr"] is True, "skrypt nie ustawil atrybutu hidden"
        assert state["freeVisible"] is False, "przed zaznaczeniem kratki wolny tekst ma byc schowany"

        page.select_option("#id_district", "mazowieckie")
        page.fill("#id_school_query", "liceum")
        page.wait_for_timeout(1200)
        items = page.locator("#school-suggestions li")
        count = items.count()
        print("podpowiedzi:", count)
        assert count > 0, "wyszukiwarka nie zwrocila ani jednej podpowiedzi"
        print("pierwsza:", items.first.inner_text().strip()[:80])
        items.first.click()
        page.wait_for_timeout(300)

        after = page.evaluate(
            """() => ({
              school_id: document.getElementById('id_school_id').value,
              query: document.getElementById('id_school_query').value,
            })"""
        )
        print("po wyborze:", after)
        assert after["school_id"], "klikniecie w podpowiedz nie zapisalo school_id"
        assert after["query"], "klikniecie w podpowiedz nie wypelnilo pola widocznego"

        # Kratka „mojej szkoły nie ma na liście” odsłania wolny tekst i blokuje wyszukiwarkę.
        page.check("#id_school_custom")
        page.wait_for_timeout(200)
        toggled = page.evaluate(
            """() => ({
              freeVisible: FREE_VISIBLE(),
              queryDisabled: document.getElementById('id_school_query').disabled,
              school_id: document.getElementById('id_school_id').value,
            })""".replace("FREE_VISIBLE()", FREE_VISIBLE)
        )
        print("po zaznaczeniu kratki:", toggled)
        assert toggled["freeVisible"] is True, "po zaznaczeniu kratki wolny tekst ma byc widoczny"
        assert toggled["queryDisabled"] is True, "wyszukiwarka ma byc wylaczona w trybie wolnego tekstu"
        assert toggled["school_id"] == "", "zaznaczenie kratki ma uniewaznic wczesniejszy wybor"

        print("bledy konsoli:", errors or "brak")
        browser.close()


main()
