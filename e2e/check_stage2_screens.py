"""Kontrola etapu 2: Konkurs #1 bez zmian, konkurs drugi z kompletem ekranów za flagami.

Trzy pytania, w tej kolejności (``docs/UNIWERSALNY-ETAP-2.md`` § 5.8, zadanie T43):

1. **Konkurs #1 wygląda tak, jak wyglądał**, mimo że obok stoi konkurs z piętnastoma flagami:
   strony publiczne odpowiadają, formularz rejestracji ma cztery zgody w tej samej kolejności,
   pulpit koordynatora się otwiera, a menu nie zyskało ani jednej pozycji (punkty 14 i 20 listy
   kontrolnej § 0.5).
2. **Konkurs drugi ma komplet ekranów wydań E–K**: dwadzieścia adresów, każdy 200 i każdy
   z napisem, po którym poznaje go człowiek. Dwa z nich są czynnością, a nie stroną, i wchodzą
   jawnie: podgląd reguły przejścia i podgląd wgrania wykazu placówek — obie mają **nic nie
   zapisać**.
3. **Te same adresy w Konkursie #1 dają 404** (§ 2.1). 404, a nie 403: rola koordynatora jest
   tu tą samą osobą w obu konkursach, więc odpowiedź mówi wyłącznie o tym, czego w tej instalacji
   nie ma — a nie o tym, czego komuś nie wolno.

Kontrola jest **idempotentna**: niczego nie kasuje, a jedyny wiersz, który zakłada (pierwszy krok
toru w edytorze przebiegu), zakłada tylko wtedy, gdy go nie ma. Drugi przebieg na tym samym stanie
przechodzi tak samo jak pierwszy — inaczej nie nadawałaby się do kroku w ``scripts/e2e.sh``, który
bywa powtarzany po naprawie.

Wymaga środowiska przygotowanego przez ``scripts/e2e.sh`` (krok „Etap 2”): konkursu ``e2e-druga``
w trybie prefiksu ścieżki, z rolą koordynatora dla konta demonstracyjnego i z zapalonymi flagami.

Uruchomienie i kody wyjścia — patrz ``e2e/stage2.py``. 0 = wszystko na swoim miejscu.
"""

from __future__ import annotations

import re

from playwright.sync_api import sync_playwright

from stage2 import Report, first_id, login, visit

#: Ekrany niezależne od etapu: (adres, napis na stronie). Napis jest nagłówkiem panelu
#: (``{% block panel_title %}``), czyli tym, co koordynator widzi jako tytuł ekranu.
FLAT_SCREENS: tuple[tuple[str, str], ...] = (
    ("/coordinator/consents/", "Zgody konkursu"),
    ("/coordinator/documents/", "Szablony dokumentów"),
    ("/coordinator/regions/", "Regiony"),
    ("/coordinator/categories/", "Kategorie"),
    ("/coordinator/pipeline/", "Przebieg edycji"),
    ("/coordinator/teams/", "Drużyny"),
    ("/coordinator/fees/", "Wpisowe: cennik"),
    ("/coordinator/fees/register/", "Wpisowe: należności"),
    ("/coordinator/venues/", "Miejsca zawodów"),
    ("/coordinator/institutions/", "Słownik placówek"),
    ("/coordinator/institutions/import/", "Wgranie wykazu z pliku"),
    ("/coordinator/registration-profile/", "Profil rejestracji"),
)

#: Ekrany etapu: wzorzec adresu z podstawieniem identyfikatora etapu i napis.
STAGE_SCREENS: tuple[tuple[str, str], ...] = (
    ("/coordinator/stages/{stage}/components/", "Komponenty etapu"),
    ("/coordinator/stages/{stage}/interview-scores/", "Punkty z rozmowy"),
    ("/coordinator/stages/{stage}/tie-breaks/", "Rozstrzyganie remisów"),
    ("/coordinator/stages/{stage}/reviewer-roles/", "Role recenzenckie"),
    ("/coordinator/stages/{stage}/logistics/", "Przyjazdy i potrzeby"),
    ("/coordinator/stages/{stage}/attendance/", "Obecność"),
)

#: Zgody Konkursu #1 w kolejności z ``apps/accounts/consents.py`` – punkt 14 listy § 0.5.
CONSENT_FIELDS = ("terms_consent", "gdpr_consent", "guardian_consent", "publish_name_consent")

#: Plik, którym sprawdzamy podgląd wgrania wykazu. Dwie kolumny i jeden wiersz: kontrola pyta
#: o **drogę**, a nie o parser (ten ma własne testy jednostkowe).
INSTITUTIONS_CSV = "nazwa,miejscowosc\nPlacowka E2E,Warszawa\n"


def competition_one(report: Report, page) -> None:
    """Konkurs #1 obok konkursu drugiego: strony publiczne, zgody, pulpit, menu."""
    for path in ("/", "/register/", "/status.json", "/dokumenty/", "/wyniki/"):
        status = visit(page, path)
        report.check(status == 200, f"Konkurs #1: {path} oddał {status}, a nie 200")

    visit(page, "/register/")
    html = page.content()
    order = [name for name in CONSENT_FIELDS if f'name="{name}"' in html]
    report.check(
        order == list(CONSENT_FIELDS),
        f"Konkurs #1: zestaw zgód w /register/ to {order}, a miał być {list(CONSENT_FIELDS)}",
    )

    status = visit(page, "/coordinator/")
    report.check(status == 200, f"Konkurs #1: pulpit koordynatora oddał {status}, a nie 200")
    menu = page.content()
    for path, _ in FLAT_SCREENS:
        report.check(
            f'href="{path}"' not in menu,
            f"Konkurs #1: w menu stoi odnośnik do ekranu za flagą ({path})",
        )
    report.note(f"Konkurs #1: {len(FLAT_SCREENS)} adresów spoza jego konfiguracji nieobecnych w menu")


def ensure_step(report: Report, page) -> int | None:
    """Identyfikator pierwszego kroku toru konkursu drugiego – zakładany, gdy toru jeszcze nie ma.

    ``create_competition`` zakłada dziś kroki toru razem z etapami (``apps.competitions.pipeline``),
    więc zwykle wystarczy odczytać pierwszy z nich. Gałąź z „Dopisz krok” zostaje dla konkursu
    założonego przed tą zmianą albo z szablonu bez etapów: kontrola przechodzi wtedy tą samą drogą,
    co koordynator, i dlatego jest **częścią** scenariusza, a nie przygotowaniem danych.
    """
    visit(page, "/coordinator/pipeline/", second=True)
    step = first_id(page, r"/coordinator/pipeline/(\d+)/rules/")
    if step is not None:
        return step

    button = page.get_by_role("button", name="Dopisz krok")
    if not button.count():
        report.check(False, "Konkurs drugi: pusty tor i brak formularza „Dopisz krok”")
        return None
    # Lista etapów otwiera się na „— wybierz etap —”, więc wskazujemy pierwszy prawdziwy wpis
    # (``index=1``). Wysłanie formularza bez wyboru kończy się kodem 400 – i tak ma być.
    page.select_option("#id_stage", index=1)
    button.click()
    page.wait_for_load_state("domcontentloaded")
    step = first_id(page, r"/coordinator/pipeline/(\d+)/rules/")
    report.check(step is not None, "Konkurs drugi: „Dopisz krok” nie założył kroku toru")
    return step


def second_competition(report: Report, page) -> tuple[int | None, int | None]:
    """Komplet ekranów konkursu drugiego. Oddaje identyfikatory etapu i kroku do dalszych kontroli."""
    status = visit(page, "/coordinator/", second=True)
    report.check(status == 200, f"Konkurs drugi: pulpit oddał {status}, a nie 200")
    stage = first_id(page, r"/coordinator/stages/(\d+)/")
    report.check(stage is not None, "Konkurs drugi: na pulpicie nie ma ani jednego etapu")

    step = ensure_step(report, page)
    screens = list(FLAT_SCREENS)
    if step is not None:
        screens.append((f"/coordinator/pipeline/{step}/rules/", "Reguły przejścia"))
    if stage is not None:
        screens += [(path.format(stage=stage), label) for path, label in STAGE_SCREENS]

    for path, label in screens:
        status = visit(page, path, second=True)
        if not report.check(status == 200, f"Konkurs drugi: {path} oddał {status}, a nie 200"):
            continue
        report.check(label in page.content(), f"Konkurs drugi: na {path} brakuje napisu „{label}”")
    report.note(f"Konkurs drugi: {len(screens)} ekranów odpowiedziało kodem 200")
    return stage, step


def rules_preview(report: Report, page, step: int) -> None:
    """Podgląd reguły przejścia: liczy skład i **nie zapisuje reguły**.

    Podgląd jest jedynym miejscem edytora, w którym koordynator widzi skutek reguły przed jej
    zapisaniem. Kontrola porównuje liczbę reguł przed i po — „podgląd”, który zaczyna zapisywać,
    jest usterką, której w liczniku 200-ek nie widać.
    """
    path = f"/coordinator/pipeline/{step}/rules/"
    visit(page, path, second=True)
    before = (
        page.content().count("coordinator-pipeline-rule-delete")
        + page.locator("button", has_text="Usuń").count()
    )

    page.select_option("#id_mode", "HYBRID")
    page.fill("#id_min_points", "60")
    page.fill("#id_top_n", "20")
    page.get_by_role("button", name="Przelicz podgląd").click()
    page.wait_for_load_state("domcontentloaded")

    report.check("Reguły przejścia" in page.content(), "Konkurs drugi: podgląd reguły nie oddał ekranu reguł")

    visit(page, path, second=True)
    after = (
        page.content().count("coordinator-pipeline-rule-delete")
        + page.locator("button", has_text="Usuń").count()
    )
    report.check(after == before, f"Konkurs drugi: podgląd reguły zapisał regułę ({before} → {after})")


def institutions_preview(report: Report, page) -> None:
    """Podgląd wgrania wykazu placówek: pokazuje liczniki i **nie zakłada ani jednej placówki**."""
    visit(page, "/coordinator/institutions/import/", second=True)
    page.set_input_files(
        "#id_file",
        files=[{"name": "wykaz-e2e.csv", "mimeType": "text/csv", "buffer": INSTITUTIONS_CSV.encode()}],
    )
    page.get_by_role("button", name="Pokaż podgląd").click()
    page.wait_for_load_state("domcontentloaded")
    report.check(
        "Wgranie wykazu z pliku" in page.content(),
        "Konkurs drugi: podgląd wykazu nie oddał ekranu wgrania",
    )

    visit(page, "/coordinator/institutions/", second=True)
    report.check(
        "Placowka E2E" not in page.content(),
        "Konkurs drugi: podgląd wgrania wykazu zapisał placówkę, a miał wyłącznie policzyć",
    )


def cross_addresses(report: Report, page, stage: int | None, step: int | None) -> None:
    """Te same adresy pod Konkursem #1: **404**, co do jednego."""
    paths = [path for path, _ in FLAT_SCREENS]
    if step is not None:
        paths.append(f"/coordinator/pipeline/{step}/rules/")
    if stage is not None:
        paths += [path.format(stage=stage) for path, _ in STAGE_SCREENS]

    for path in paths:
        status = visit(page, path)
        report.check(status == 404, f"Konkurs #1: {path} oddał {status}, a miał oddać 404")
    report.note(f"Konkurs #1: {len(paths)} adresów ekranów za flagą oddało 404")


def main() -> int:
    report = Report("Etap 2: ekrany dwóch konkursów")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        console: list[str] = []
        page.on("console", lambda message: console.append(f"{message.type.upper()} {message.text}"))

        login(page)
        report.check(
            re.search(r"/(me|coordinator)/", page.url) is not None or "Wyloguj" in page.content(),
            f"Logowanie koordynatora nie powiodło się (adres po wysłaniu: {page.url})",
        )

        competition_one(report, page)
        stage, step = second_competition(report, page)
        if step is not None:
            rules_preview(report, page, step)
        institutions_preview(report, page)
        cross_addresses(report, page, stage, step)

        # Błędy skryptu strony są usterką; ostrzeżenia przeglądarki o pochodzeniu adresu – nie.
        # W sieci compose serwis chodzi po ``http://web:8000``, więc Chromium odrzuca nagłówek
        # ``Cross-Origin-Opener-Policy`` i wypisuje to przy każdej odsłonie. Na produkcji (HTTPS)
        # tego komunikatu nie ma i nie ma go po co ścigać tutaj.
        errors = [
            line
            for line in console
            if line.startswith("PAGEERROR")
            or (line.startswith("ERROR") and "Cross-Origin-Opener-Policy" not in line)
        ]
        for line in dict.fromkeys(errors):
            report.note(f"konsola: {line}")
        report.check(
            not [line for line in errors if line.startswith("PAGEERROR")],
            "Wyjątek JavaScriptu na stronie panelu",
        )
        browser.close()

    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main())
