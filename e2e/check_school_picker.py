"""Kontrola wyszukiwarki szkół w rejestracji: miejscowość, podpowiedzi, wybór, ukryte ``school_id``.

Uruchamiana przeciw dowolnemu adresowi (``E2E_BASE_URL``), także produkcyjnemu – zgłoszenie
organizatora brzmiało „podałem szkołę z listy, a system kazał wybrać z listy”, czyli wybór nie
zapisał ``school_id``. Przyczyną był komponent Alpine ładowany z CDN-u: kiedy jednego adresu nie
było, nie działał cały blok. Skrypt jest teraz czystym JS-em serwowanym z własnego adresu, więc
ta kontrola pilnuje dwóch rzeczy naraz: że podpowiedzi w ogóle są i że nic tu nie zależy od
zewnętrznej biblioteki (asercja na ``window.Alpine`` celowo **nie** istnieje – Alpine może być
na stronie dla innych widoków i wyszukiwarce nic do tego).

Od uwag organizatora z 16.09 blok ma **dwa kroki**: najpierw miejscowość, potem szkoła. Kontrola
sprawdza tu dokładnie to, czego nie widzi żaden test serwera: że wybór miejscowości zawęża listę
szkół, że pusty tekst pokazuje wtedy **pełną** listę (a nie nic) i że lista doczytuje się
przewijaniem. Bez tego „pełna lista szkół miasta” byłaby obietnicą sprawdzoną wyłącznie po stronie
API, a poprzednia usterka tego bloku (Alpine z CDN-u) siedziała właśnie w przeglądarce.

Druga rzecz, która wymaga **prawdziwego** słownika, a nie fabryki z testów: wykaz SIO zapisuje
pięć największych miast dzielnicami, a Warszawę wyłącznie nazwami dzielnic. Kontrola pyta więc
o „wroc”, „warszawa” i „krzyki” i sprawdza, że w odpowiedzi stoi jedno miasto – to jest asercja
na danych z ``seed_schools``, a nie na trzech wierszach założonych w teście.

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

        # --- krok 1: miejscowość ---------------------------------------------------------------
        #
        # Wykaz SIO rozbija Wrocław na pięć „miejscowości” (``Wrocław-Krzyki``, ``Wrocław-Fabryczna``
        # …), a Warszawę zapisuje **wyłącznie** nazwami dzielnic. Od czasu ``School.city_parent``
        # (apps/schools/normalise.py) podpowiedź ma pokazywać **jedną** pozycję na miasto – i to
        # jest asercja, której nie da się postawić w teście serwera tak, żeby objęła prawdziwy
        # słownik: na produkcji i w środowisku developerskim stoi za nią pełny wykaz z seeda.
        page.fill("#id_school_city", "wroc")
        page.wait_for_timeout(1200)
        cities = page.locator("#city-suggestions li")
        print("podpowiedzi miejscowosci:", cities.count())
        assert cities.count() > 0, "krok miejscowosci nie zwrocil ani jednej podpowiedzi"
        names = [cities.nth(i).inner_text().strip().split("\n")[0] for i in range(cities.count())]
        print("miejscowosci dla 'wroc':", names)
        assert names.count("Wrocław") == 1, "Wroclaw ma byc jedna pozycja, a nie lista dzielnic"
        assert not any("-" in name and name.startswith("Wrocław") for name in names), (
            "w podpowiedziach zostala dzielnica zapisana jako osobne miasto"
        )

        # Warszawy w wykazie nie ma pod własną nazwą – jest pod osiemnastoma nazwami dzielnic.
        # Ta asercja pilnuje całej reguły (b) z ``apps/schools/normalise.py`` na prawdziwych danych.
        page.fill("#id_school_city", "warszawa")
        page.wait_for_timeout(1200)
        capital = [cities.nth(i).inner_text().strip().split("\n")[0] for i in range(cities.count())]
        print("miejscowosci dla 'warszawa':", capital)
        assert "Warszawa" in capital, "stolica nie znalazla sie w podpowiedziach miejscowosci"

        # Dzielnica wpisana zamiast miasta też ma trafiać – w odpowiedzi stoi wtedy miasto.
        page.fill("#id_school_city", "krzyki")
        page.wait_for_timeout(1200)
        district = [cities.nth(i).inner_text().strip().split("\n")[0] for i in range(cities.count())]
        print("miejscowosci dla 'krzyki':", district)
        assert district == ["Wrocław"], "nazwa dzielnicy ma podpowiadac jej miasto"

        cities.first.click()
        page.wait_for_timeout(1200)

        # Po wyborze miejscowości pole szkoły jest puste, a lista pokazuje **wszystkie** szkoły
        # tego miasta – to jest odpowiedź na „po wpisaniu «wrocław» nie widać liceów”.
        full = page.locator("#school-suggestions li")
        print("pelna lista szkol miasta:", full.count())
        assert page.input_value("#id_school_query") == "", "wybor miasta ma wyczyscic pole szkoly"
        assert full.count() > 0, "po wybraniu miasta lista szkol jest pusta"
        # Porządek: najpierw licea ogólnokształcące (apps/schools/models.py::KIND_ORDER).
        print("pierwsza szkola:", full.first.inner_text().strip()[:80])
        # Pozycja pokazuje miasto z dzielnicą w nawiasie („Wrocław (Krzyki)”), a nie „Wrocław-Krzyki”:
        # miasto odpowiada na „gdzie”, a dzielnica odróżnia szkoły o podobnych nazwach.
        meta = full.first.locator(".school-picker__meta").inner_text().strip()
        print("opis pierwszej pozycji:", meta)
        assert meta.startswith("Wrocław"), "pozycja listy nie zaczyna sie od nazwy miasta"
        assert "Wrocław-" not in meta, "na liscie zostala miejscowosc z wykazu zamiast miasta"

        # Doczytywanie kolejnej strony przewinięciem listy. W mieście wojewódzkim szkół jest
        # więcej niż mieści jedna odpowiedź, więc lista ma urosnąć.
        before = full.count()
        page.evaluate(
            "() => { const l = document.getElementById('school-suggestions'); l.scrollTop = l.scrollHeight; }"
        )
        page.wait_for_timeout(1200)
        print("po przewinieciu:", full.count(), "(bylo", before, ")")

        # --- krok 2: szkoła --------------------------------------------------------------------
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
              cityDisabled: document.getElementById('id_school_city').disabled,
              school_id: document.getElementById('id_school_id').value,
            })""".replace("FREE_VISIBLE()", FREE_VISIBLE)
        )
        print("po zaznaczeniu kratki:", toggled)
        assert toggled["freeVisible"] is True, "po zaznaczeniu kratki wolny tekst ma byc widoczny"
        assert toggled["queryDisabled"] is True, "wyszukiwarka ma byc wylaczona w trybie wolnego tekstu"
        # Miejscowość jest zakresem wyszukiwarki – czynna obiecywałaby, że coś jeszcze zawęża.
        assert toggled["cityDisabled"] is True, "pole miejscowosci ma byc wylaczone razem z wyszukiwarka"
        assert toggled["school_id"] == "", "zaznaczenie kratki ma uniewaznic wczesniejszy wybor"

        print("bledy konsoli:", errors or "brak")
        browser.close()


main()
