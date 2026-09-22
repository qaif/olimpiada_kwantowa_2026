"""Kontrola izolacji: uczestnik konkursu drugiego jest niewidoczny z Konkursu #1 i odwrotnie.

To jest trzeci przebieg z zadania T43 i jedyny, który zakłada **dane osoby**: rejestruje konto
uczestnika w konkursie drugim przez ten sam publiczny formularz, z którego korzysta człowiek,
a potem pyta obydwa panele, kogo widzą.

**Dlaczego rejestracja, a nie wiersz wstawiony do bazy.** Reguła izolacji (``docs/UNIWERSALNY-ETAP-1.md``
§ 3.5) dotyczy drogi, którą dane **wchodzą** do konkursu, a nie tylko tej, którą się je czyta.
Uczestnik dopisany wprost do tabeli miałby konkurs ustawiony przez kontrolę, czyli sprawdzałby jej
własne założenie. Uczestnik zarejestrowany formularzem dostaje konkurs od serwisu — i to jest
przedmiot pytania.

**Co dokładnie porównujemy.** Kolejka aktywacji (``/coordinator/activations/``) i lista kont
(``/coordinator/accounts/?role=participant``), w **obie** strony: wyciek bywa kierunkowy, więc
kontrola sprawdzająca tylko jedną stronę przeszłaby przy zakresowaniu zepsutym w drugą.

**Idempotencja.** Adres e-mail nowego konta ma w sobie losowy znacznik, więc kolejny przebieg
zakłada kolejne konto i niczego nie nadpisuje. Kontrola nie aktywuje konta i nie kasuje go —
konta uczestników są danymi osobowymi, a kasowanie ich z przebiegu automatycznego byłoby
czynnością nieodwracalną wykonywaną bez decyzji człowieka.

Wymaga środowiska przygotowanego przez ``scripts/e2e.sh`` (krok „Etap 2”) oraz wykazu szkół
(``manage.py seed_schools``) — formularz rejestracji pyta o szkołę z wyszukiwarki.

Uruchomienie i kody wyjścia — patrz ``e2e/stage2.py``. 0 = konkursy się nie widzą.
"""

from __future__ import annotations

import uuid

from playwright.sync_api import sync_playwright

from stage2 import COORDINATOR_EMAIL, Report, login, url, visit

#: Hasło zakładanego konta – spełnia politykę Django (długość, brak podobieństwa do loginu).
PASSWORD = "Olimpiada-Testowa-2026"

#: Odpowiedź CAPTCHY przyjmowana przy ``E2E_MODE=1`` (``apps/web/captcha.py``). Pole zostaje
#: w scenariuszu jawnie: gdyby zabezpieczenie zniknęło z formularza, krok ma się wywalić.
CAPTCHA_E2E_RESPONSE = "PASSED"

#: Fragment wpisywany w wyszukiwarkę szkół. Ten sam, co w scenariuszu pełnego cyklu — słownik SIO
#: zna tę szkołę i podpowiada ją dla województwa mazowieckiego.
SCHOOL_QUERY = "XIV Liceum"


def register_in_the_second_competition(report: Report, page) -> dict[str, str]:
    """Rejestracja uczestnika pod prefiksem konkursu drugiego. Oddaje tożsamość nowego konta."""
    token = uuid.uuid4().hex[:8]
    identity = {
        "email": f"e2e-izolacja-{token}@example.test",
        "last_name": f"Izolacki{token[:4].upper()}",
    }

    status = visit(page, "/register/", second=True)
    if not report.check(status == 200, f"Konkurs drugi: /register/ oddał {status}, a nie 200"):
        return identity

    page.fill("#id_email", identity["email"])
    page.fill("#id_password", PASSWORD)
    page.fill("#id_password2", PASSWORD)
    page.fill("#id_first_name", "Jan")
    page.fill("#id_last_name", identity["last_name"])
    page.fill("#id_phone", "600 100 200")
    # Województwo przed szkołą: podpowiedzi zawężają się do wybranego okręgu.
    page.select_option("#id_district", "mazowieckie")
    page.fill("#id_school_query", SCHOOL_QUERY)
    suggestion = page.locator("#school-suggestions li").first
    suggestion.wait_for(state="visible", timeout=15_000)
    suggestion.click()
    page.select_option("#id_grade", "3")
    page.fill("#id_birth_date", "2008-12-31")
    for consent in ("#id_terms_consent", "#id_gdpr_consent", "#id_guardian_consent"):
        if page.locator(consent).count():
            page.check(consent)
    page.fill("#id_captcha_1", CAPTCHA_E2E_RESPONSE)
    page.get_by_role("button", name="Załóż konto").click()
    page.wait_for_load_state("domcontentloaded")

    # Rejestracja kończy się stroną „sprawdź skrzynkę” **pod prefiksem konkursu drugiego**.
    # Sprawdzamy prefiks, a nie konkretny adres docelowy: przedmiotem jest to, że uczestnik
    # został w swoim konkursie, a nie to, którą stroną serwis kończy rejestrację (ta należy do
    # scenariusza pełnego cyklu i ma tam własną asercję).
    report.check(
        page.url.startswith(url("/", second=True)) and "/register/" in page.url,
        f"Konkurs drugi: po rejestracji przeglądarka wylądowała poza konkursem (adres: {page.url})",
    )
    report.note(f"Konkurs drugi: założono konto {identity['email']}")
    return identity


def sees(page, path: str, needle: str, *, second: bool) -> bool:
    """Czy pod tym adresem (w tym konkursie) widać ten napis."""
    visit(page, path, second=second)
    return needle in page.content()


def isolation(report: Report, page, identity: dict[str, str]) -> None:
    """Kolejka aktywacji i lista kont: każdy konkurs widzi swoich i tylko swoich."""
    email = identity["email"]

    report.check(
        sees(page, "/coordinator/activations/", email, second=True),
        f"Konkurs drugi nie widzi własnego zgłoszenia {email} w kolejce aktywacji",
    )
    report.check(
        not sees(page, "/coordinator/activations/", email, second=False),
        f"WYCIEK: Konkurs #1 widzi zgłoszenie {email} z konkursu drugiego",
    )

    accounts = "/coordinator/accounts/?role=participant"
    report.check(
        not sees(page, accounts, email, second=False),
        f"WYCIEK: konto {email} z konkursu drugiego stoi na liście uczestników Konkursu #1",
    )

    # Druga strona: uczestnicy demonstracyjni Konkursu #1 (``manage.py seed_demo``) nie mogą
    # pojawić się na liście konkursu drugiego. Adres bierzemy **z listy** Konkursu #1, a nie ze
    # stałej: seed bywa poprawiany, a kontrola ma pytać o to, co w bazie naprawdę jest.
    visit(page, accounts, second=False)
    # Adres czytamy z **komórki tabeli** (``<th scope="row">``), a nie z całego HTML-a: w nagłówku
    # strony stoi polityka CSP z napisami wyglądającymi jak adres (``csp@3.17.1``), więc wyrażenie
    # regularne po całym dokumencie brałoby pierwszy z nich i pytało o konto, którego nie ma.
    rows = page.locator('table th[scope="row"] a')
    ours = [
        text
        for text in (rows.nth(index).inner_text().strip() for index in range(rows.count()))
        if "@" in text and text not in {COORDINATOR_EMAIL, email}
    ]
    if report.check(bool(ours), "Konkurs #1 nie ma ani jednego uczestnika – seed_demo nie wykonał się"):
        report.check(
            not sees(page, accounts, ours[0], second=True),
            f"WYCIEK: konto {ours[0]} z Konkursu #1 stoi na liście uczestników konkursu drugiego",
        )


def main() -> int:
    report = Report("Etap 2: izolacja uczestników dwóch konkursów")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        identity = register_in_the_second_competition(report, page)
        # Rejestracja loguje wyłącznie konto uczestnika (i to nieaktywne), więc do paneli wchodzimy
        # jako koordynator dopiero teraz — świeżym kontekstem, żeby sesja uczestnika nie mieszała.
        page.context.clear_cookies()
        login(page)
        isolation(report, page, identity)

        browser.close()
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main())
