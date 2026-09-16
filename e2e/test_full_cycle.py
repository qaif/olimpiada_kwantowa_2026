"""Scenariusz E2E: pełny cykl etapu eliminacyjnego przez interfejs WWW (T-10).

Przebieg (każdy krok kończy się asercją na stanie widocznym w UI):

1. rejestracja nowego uczestnika → logowanie → zapis do eliminacji,
2. upload PDF do zadania 1 → oczekiwanie na prawdziwy werdykt ClamAV (``CLEAN``),
3. koordynator: przesunięcie osi czasu na fazę „po deadline”, zamknięcie etapu, przydział recenzentów,
4. recenzent 1 wystawia 6, recenzent 2 – 5 → rozjazd (``MODERATION``),
5. koordynator rozstrzyga na 5 (ocena wstępna),
6. otwarcie okna reklamacji → uczestnik składa reklamację,
7. komisja odwoławcza (konto założone w trakcie testu na kod zaproszenia ``--appeals``)
   uwzględnia reklamację i przyznaje 6,
8. zamknięcie okna reklamacji → koordynator publikuje wyniki w trybie ``CODE``,
9. publiczna tabela (``/results/<id>/`` i strona CMS ``/wyniki/``) pokazuje kod i 6 punktów,
   a **nie** pokazuje nazwiska; uczestnik w ``/me/`` widzi 6 i komentarz recenzenta.

Cały scenariusz jest jednym testem świadomie: to jedna maszyna stanów, a rozbicie na osobne testy
pytest udawałoby niezależność, której nie ma (krok 7 nie znaczy nic bez kroku 4). Numerację kroków
widać w logu i w nazwach zrzutów ekranu w ``e2e/artifacts/``.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import requests
from playwright.sync_api import expect

# ``conftest`` i ``timeline`` to moduły z tego katalogu – pytest wkłada ``e2e/`` na początek
# ``sys.path`` (domyślny tryb importu `prepend`), więc import po nazwie jest poprawny.
from conftest import COORDINATOR_EMAIL, DEMO_PASSWORD, REVIEWER_1_EMAIL, REVIEWER_2_EMAIL
from timeline import PHASE_APPEALS_CLOSED, PHASE_APPEALS_OPEN, PHASE_CLOSED, set_phase

LOGGER = logging.getLogger("e2e")

#: Minimalny, ale prawdziwy PDF (nagłówek ``%PDF-`` decyduje o walidacji magic bytes,
#: reszta struktury sprawia, że plik otwiera się w przeglądarce recenzenta).
PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R"
    b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 62>>stream\n"
    b"BT /F1 14 Tf 20 120 Td (Rozwiazanie zadania 1 - E2E) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
)

#: Odpowiedź, którą CAPTCHA przyjmuje w trybie testowym. Aplikacja włącza go razem z ``E2E_MODE``
#: (config/settings/base.py) – w produkcji ta zmienna nie jest ustawiana, więc obrazek trzeba
#: naprawdę odczytać.
CAPTCHA_E2E_RESPONSE = "PASSED"

#: Skan antywirusowy idzie przez Celery do ClamAV – kilka sekund przy rozgrzanym demonie,
#: więcej przy pierwszym pliku po starcie kontenera.
AV_SCAN_TIMEOUT_S = 180
AV_POLL_INTERVAL_S = 3

REVIEWER_1_SCORE = "6"
REVIEWER_2_SCORE = "5"
MODERATION_SCORE = "5"
APPEAL_SCORE = "6"
REVIEWER_1_FEEDBACK = "Rozwiązanie pełne i poprawne, uzasadnienie nierówności bez luk."
REVIEWER_2_FEEDBACK = "Rozumowanie poprawne, brakuje omówienia przypadku równości."
APPEAL_ARGUMENT = (
    "Przypadek równości jest omówiony w akapicie trzecim na stronie pierwszej, "
    "więc obniżenie punktacji o jeden punkt uważam za nieuzasadnione."
)


# --- pomocnicze kroki na UI --------------------------------------------------------------------


def login(session, email: str, password: str) -> None:
    session.goto("/login/")
    session.page.fill("#id_username", email)
    session.page.fill("#id_password", password)
    session.page.get_by_role("button", name="Zaloguj").click()
    # Nagłówek pokazuje adres zalogowanego konta – to najkrótsza asercja „jestem zalogowany”.
    expect(session.page.locator("span.who")).to_have_text(email)


def messages_text(page) -> str:
    return page.locator("ul.messages").inner_text() if page.locator("ul.messages").count() else ""


def problem_card(page, number: int):
    """Karta zadania o danym numerze – w panelu uczestnika i w odpowiedzi HTMX po uploadzie."""
    return page.locator("article.problem-card").filter(
        has=page.locator("h3", has_text=re.compile(rf"^Zadanie {number}:"))
    )


def stage_card(page, kind_label: str):
    """Karta etapu w panelu koordynatora (``Eliminacje`` / ``Okręgowy`` / ``Finał``)."""
    return page.locator("article.card").filter(
        has=page.locator("h3", has_text=re.compile(rf"^{kind_label}$"))
    )


def code_card(page, public_code: str):
    """Karta pracy identyfikowanej kodem publicznym (moderacja, kolejka reklamacji)."""
    return page.locator("article.card").filter(has=page.locator("h2, h3", has_text=public_code))


def wait_for_clean_scan(session, problem_number: int) -> None:
    """Odpytuje panel uczestnika, aż ClamAV wyda werdykt. Skan jest prawdziwy, nie zamockowany."""
    deadline = time.monotonic() + AV_SCAN_TIMEOUT_S
    last = ""
    while time.monotonic() < deadline:
        session.goto("/me/")
        last = problem_card(session.page, problem_number).inner_text()
        if "czysty" in last:
            return
        assert "zainfekowany" not in last, f"ClamAV odrzucił plik testowy:\n{last}"
        assert "błąd skanu" not in last, f"Skan antywirusowy zakończył się błędem:\n{last}"
        time.sleep(AV_POLL_INTERVAL_S)
    raise AssertionError(
        f"Plik nie doczekał się statusu CLEAN w {AV_SCAN_TIMEOUT_S} s. Ostatni stan karty:\n{last}"
    )


def fetch_through_presigned_url(session, base_url: str, download_path: str, rewrite) -> bytes:
    """Sprawdza, że link recenzenta faktycznie oddaje plik – z podmianą hosta MinIO.

    Presigned URL jest podpisany hostem publicznym (``S3_PUBLIC_ENDPOINT_URL``), którego w sieci
    compose nie ma. Podmieniamy sam adres sieciowy, a nagłówek ``Host`` zostawiamy oryginalny –
    podpis SigV4 obejmuje ``Host``, więc bez tego MinIO odpowiedziałby 403.
    """
    cookies = {cookie["name"]: cookie["value"] for cookie in session.context.cookies()}
    redirect = requests.get(f"{base_url}{download_path}", cookies=cookies, allow_redirects=False, timeout=30)
    assert redirect.status_code in (200, 302), f"Pobranie pliku: HTTP {redirect.status_code}"
    if redirect.status_code == 200:  # backend lokalny (poza compose) oddaje plik wprost
        return redirect.content

    target = redirect.headers["Location"]
    headers = {}
    if rewrite is not None:
        public_host, internal_host = rewrite
        parts = urlsplit(target)
        if parts.netloc == public_host:
            headers["Host"] = public_host
            target = urlunsplit((parts.scheme, internal_host, parts.path, parts.query, ""))
    response = requests.get(target, headers=headers, timeout=60)
    assert response.status_code == 200, f"Presigned URL zwrócił HTTP {response.status_code}"
    return response.content


# --- scenariusz -------------------------------------------------------------------------------


def test_pelny_cykl_etapu_od_rejestracji_do_publikacji(
    sessions, base_url, participant_identity, s3_host_rewrite
):
    uczestnik = sessions("uczestnik")
    koordynator = sessions("koordynator")

    # --- 1. Rejestracja uczestnika -------------------------------------------------------------
    uczestnik.goto("/register/")
    page = uczestnik.page
    page.fill("#id_email", participant_identity["email"])
    page.fill("#id_password", participant_identity["password"])
    # Powtórzenie hasła: literówka przy rejestracji jest w praktyce nieodwracalna, bo konto
    # powstaje z hasłem, którego nikt nie zna, a reset hasła wymaga adresu jeszcze
    # niepotwierdzonego.
    page.fill("#id_password2", participant_identity["password"])
    page.fill("#id_first_name", participant_identity["first_name"])
    page.fill("#id_last_name", participant_identity["last_name"])
    page.fill("#id_phone", participant_identity["phone"])
    # Województwo jest listą zamkniętą (``Voivodeship``), więc wybór, a nie wpisanie tekstu.
    # Musi być **przed** szkołą: podpowiedzi zawężają się do wybranego okręgu.
    page.select_option("#id_district", participant_identity["district"])
    # Szkoła: prawdziwa wyszukiwarka słownika SIO (fetch na /api/schools/, komponent Alpine).
    # Nazwa, którą zapisze serwer, przychodzi z rejestru – zapamiętujemy ją do asercji
    # „w publicznej tabeli nie ma danych osobowych” (krok 11).
    page.fill("#id_school_query", participant_identity["school_query"])
    podpowiedz = page.locator("#school-suggestions li").first
    expect(podpowiedz).to_be_visible()
    participant_identity["school"] = podpowiedz.locator(".school-picker__name").inner_text().strip()
    podpowiedz.click()
    expect(page.locator("#id_school_id")).not_to_have_value("")
    page.select_option("#id_grade", participant_identity["grade"])
    page.fill("#id_birth_year", participant_identity["birth_year"])
    # Zgody: regulamin i RODO są wymagane od każdego, zgoda opiekuna – od niepełnoletniego
    # (reguła po roczniku, patrz apps/accounts/consents.py). Etykiety niosą odnośniki do
    # dokumentów, więc pole zaznaczamy po identyfikatorze, a nie po tekście etykiety.
    page.check("#id_terms_consent")
    page.check("#id_gdpr_consent")
    page.check("#id_guardian_consent")
    # CAPTCHA jest własna i obrazkowa (apps/web/captcha.py). Przeglądarka testu obrazka nie
    # odczyta, więc aplikacja uruchomiona z ``E2E_MODE=1`` przyjmuje odpowiedź „PASSED” i nie
    # pilnuje minimalnego czasu wypełniania. Pole zostaje w scenariuszu **jawnie**: gdyby
    # zabezpieczenie zniknęło z formularza, ten krok ma się wywalić, a nie cicho przejść.
    page.fill("#id_captcha_1", CAPTCHA_E2E_RESPONSE)
    page.get_by_role("button", name="Załóż konto").click()
    expect(page).to_have_url(re.compile(r"/login/$"))
    # Konto powstaje **nieaktywne**: adres e-mail jest loginem i jedyną drogą odzyskania konta,
    # więc trzeba go potwierdzić. Komunikat musi o tym mówić, inaczej uczestnik idzie prosto na
    # formularz logowania i dostaje „nieprawidłowy e-mail lub hasło”.
    expect(page.locator("ul.messages")).to_contain_text("Sprawdź skrzynkę e-mail")
    uczestnik.step("1-rejestracja")

    # --- 1b. Aktywacja konta przez panel koordynatora ------------------------------------------
    # Scenariusz nie czyta skrzynki pocztowej: klika w to samo obejście, z którego korzysta
    # organizator, dopóki dostarczalność poczty nie jest pewna (SPF/DKIM, README § 4.2).
    # Dzięki temu E2E sprawdza także tę ścieżkę, a nie tylko link z listu.
    login(koordynator, COORDINATOR_EMAIL, DEMO_PASSWORD)
    koordynator.goto("/coordinator/activations/")
    oczekujace = koordynator.page.locator("tr", has_text=participant_identity["email"])
    expect(oczekujace).to_have_count(1)
    oczekujace.get_by_role("button", name="Aktywuj ręcznie").click()
    expect(koordynator.page.locator("ul.messages")).to_contain_text("Konto zostało aktywowane")
    koordynator.step("1b-aktywacja-konta")

    # --- 2. Logowanie i zapis do eliminacji ----------------------------------------------------
    login(uczestnik, participant_identity["email"], participant_identity["password"])
    expect(page).to_have_url(re.compile(r"/me/$"))
    public_code = page.locator("p.lead strong").first.inner_text().strip()
    assert public_code.startswith("OLM-"), f"Nieoczekiwany kod publiczny: {public_code!r}"
    LOGGER.info("Kod publiczny uczestnika: %s", public_code)

    page.get_by_role("button", name="Zgłoś się do tego etapu").click()
    expect(page.locator("ul.messages")).to_contain_text("Zgłoszenie do etapu zostało przyjęte")
    expect(page.locator("main")).to_contain_text("Status Twojego udziału: zarejestrowany")
    uczestnik.step("2-zapis-do-elim")

    # Identyfikator etapu bierzemy z akcji formularza uploadu – nie zgadujemy go i nie
    # zaglądamy do bazy; UI jest jedynym źródłem prawdy dla scenariusza E2E.
    upload_action = problem_card(page, 1).locator("form.upload").get_attribute("action")
    stage_id = int(re.search(r"/me/stages/(\d+)/problems/", upload_action).group(1))
    LOGGER.info("Etap eliminacyjny: id=%s", stage_id)

    # --- 3. Upload rozwiązania i prawdziwy skan antywirusowy -----------------------------------
    card = problem_card(page, 1)
    card.locator("input[type=file]").set_input_files(
        files=[{"name": "rozwiazanie.pdf", "mimeType": "application/pdf", "buffer": PDF_BYTES}]
    )
    # Lista kontrolna przed wysyłką: pole jest wymagane także po stronie serwera
    # (``apps.web.forms.SubmissionUploadForm.confirmed``), więc scenariusz musi je zaznaczyć.
    card.locator("input[name=confirmed]").check()
    card.get_by_role("button", name="Wyślij rozwiązanie").click()
    # Odpowiedź HTMX podmienia kartę zadania – nowa wersja pojawia się w tabeli „Wysłane wersje”.
    expect(problem_card(page, 1)).to_contain_text("wersja 1")
    uczestnik.step("3-upload")

    wait_for_clean_scan(uczestnik, 1)
    expect(problem_card(page, 1)).to_contain_text("czysty")
    expect(problem_card(page, 1)).to_contain_text("oddane")
    uczestnik.step("3-skan-clean")

    # --- 4. Koordynator: faza „po deadline”, zamknięcie etapu, przydział ------------------------
    # Koordynator jest zalogowany od kroku 1b (ręczna aktywacja konta uczestnika) – drugie
    # logowanie odbiłoby się o ``redirect_authenticated_user`` na ``/login/``.
    set_phase(koordynator, stage_id, PHASE_CLOSED)

    koordynator.goto("/coordinator/")
    elim = stage_card(koordynator.page, "Eliminacje")
    # Karta etapu pokazuje jedną akcję główną, a resztę narzędzi trzyma pod „Więcej” – zamknięcie
    # etapu i przydział recenzentów są właśnie tam, więc scenariusz najpierw rozwija ten blok.
    elim.locator("details.stage-card__more > summary").click()
    elim.get_by_role("button", name="Zamknij etap").click()
    # Beat (``close_due_stages``, co 60 s) może zamknąć etap w tej samej minucie – wtedy przycisk
    # odpowiada ``STAGE_ALREADY_CLOSED``. Liczy się stan etapu, nie to, kto zdążył pierwszy.
    expect(koordynator.page.locator("ul.messages")).to_contain_text(
        re.compile(r"Etap zamknięty|Etap jest już zamknięty")
    )
    expect(stage_card(koordynator.page, "Eliminacje")).to_contain_text("zamknięty")
    koordynator.step("4-etap-zamkniety")

    elim = stage_card(koordynator.page, "Eliminacje")
    elim.locator("details.stage-card__more > summary").click()
    elim.locator("input[name=per_submission]").fill("2")
    elim.get_by_role("button", name="Przydziel recenzentów").click()
    expect(koordynator.page.locator("ul.messages")).to_contain_text("Przydzielono 2 recenzji dla 1 rozwiązań")
    koordynator.step("4-przydzial")

    # --- 5. Dwie niezależne oceny: 6 i 5 → rozjazd ---------------------------------------------
    download_path = None
    for email, score, feedback in (
        (REVIEWER_1_EMAIL, REVIEWER_1_SCORE, REVIEWER_1_FEEDBACK),
        (REVIEWER_2_EMAIL, REVIEWER_2_SCORE, REVIEWER_2_FEEDBACK),
    ):
        recenzent = sessions(email.split("@")[0])
        login(recenzent, email, DEMO_PASSWORD)
        expect(recenzent.page).to_have_url(re.compile(r"/review/$"))
        row = recenzent.page.locator("tr", has_text=public_code)
        expect(row).to_contain_text("przydzielona")
        row.get_by_role("link", name="otwórz").click()

        detail = recenzent.page
        expect(detail.locator("h1")).to_contain_text(public_code)
        # Ocenianie jest ślepe: w panelu recenzenta nie ma ani nazwiska, ani adresu uczestnika.
        body = detail.locator("body").inner_text()
        assert participant_identity["last_name"] not in body
        assert participant_identity["email"] not in body
        # Plik jest dostępny dopiero po werdykcie CLEAN – link musi istnieć.
        download_link = detail.get_by_role("link", name="Pobierz plik")
        expect(download_link).to_have_count(1)
        download_path = download_link.get_attribute("href")
        recenzent.step("5-panel-recenzenta")

        detail.locator(f"input[name=score][value='{score}']").check()
        detail.fill("#id_comment_internal", f"Ocena robocza {score}/6 (E2E).")
        detail.fill("#id_comment_for_participant", feedback)
        detail.get_by_role("button", name="Wystaw ocenę").click()
        expect(detail).to_have_url(re.compile(r"/review/$"))
        expect(detail.locator("ul.messages")).to_contain_text("Ocena została wystawiona")
        expect(detail.locator("tr", has_text=public_code)).to_contain_text("wystawiona")
        recenzent.step("5-ocena-wystawiona")

        if email == REVIEWER_1_EMAIL:
            # Presigned URL nie działa spod przeglądarki w sieci compose (host `localhost:9000`),
            # więc pobranie sprawdzamy poza nią, z podmianą hosta – patrz docstring funkcji.
            content = fetch_through_presigned_url(recenzent, base_url, download_path, s3_host_rewrite)
            assert content.startswith(b"%PDF-"), "Presigned URL nie oddał pliku PDF"
            LOGGER.info("Pobranie pliku przez recenzenta: %s bajtów", len(content))

    # --- 6. Koordynator rozstrzyga rozjazd na 5 ------------------------------------------------
    koordynator.goto("/coordinator/moderation/")
    moderacja = code_card(koordynator.page, public_code)
    expect(moderacja).to_contain_text("zadanie 1")
    koordynator.step("6-moderacja")
    moderacja.locator("input[name=score]").fill(MODERATION_SCORE)
    moderacja.locator("textarea[name=rationale]").fill(
        "Posiedzenie komisji: brak omówienia przypadku równości to drobna usterka."
    )
    moderacja.get_by_role("button", name="Rozstrzygnij (posiedzenie komisji)").click()
    expect(koordynator.page.locator("ul.messages")).to_contain_text("Rozjazd rozstrzygnięty")
    expect(koordynator.page.locator("main")).to_contain_text("Bez rozjazdów")
    koordynator.step("6-rozjazd-rozstrzygniety")

    # --- 7. Konto komisji odwoławczej na kod zaproszenia ---------------------------------------
    koordynator.goto("/coordinator/committee/")
    koordynator.page.check("#id_is_appeals")
    koordynator.page.get_by_role("button", name="Wygeneruj kod").click()
    expect(koordynator.page.locator("ul.messages")).to_contain_text("Kod zaproszenia")
    invitation_code = re.search(r"nie da się go odtworzyć\):\s*(\S+)", messages_text(koordynator.page)).group(
        1
    )
    koordynator.step("7-kod-zaproszenia")

    komisja = sessions("komisja")
    komisja_email = f"komisja-{uuid.uuid4().hex[:10]}@example.test"
    komisja.goto("/register/committee/")
    komisja.page.fill("#id_email", komisja_email)
    komisja.page.fill("#id_password", "Komisja-Odwolawcza-2026")
    komisja.page.fill("#id_password2", "Komisja-Odwolawcza-2026")
    komisja.page.fill("#id_first_name", "Anna")
    komisja.page.fill("#id_last_name", "Odwolawska")
    komisja.page.fill("#id_invitation_code", invitation_code)
    komisja.page.fill("#id_captcha_1", CAPTCHA_E2E_RESPONSE)
    komisja.page.get_by_role("button", name="Załóż konto").click()
    expect(komisja.page).to_have_url(re.compile(r"/login/$"))
    expect(komisja.page.locator("ul.messages")).to_contain_text("Konto zostało założone")
    komisja.step("7-rejestracja-komisji")

    # Kod zaproszenia dowodzi zaproszenia, a nie tego, że wpisany adres należy do tej osoby –
    # konto komisji przechodzi tę samą aktywację co uczestnik.
    koordynator.goto("/coordinator/activations/")
    oczekujaca_komisja = koordynator.page.locator("tr", has_text=komisja_email)
    expect(oczekujaca_komisja).to_have_count(1)
    oczekujaca_komisja.get_by_role("button", name="Aktywuj ręcznie").click()
    expect(koordynator.page.locator("ul.messages")).to_contain_text("Konto zostało aktywowane")

    # --- 8. Okno reklamacji: uczestnik składa reklamację ---------------------------------------
    set_phase(koordynator, stage_id, PHASE_APPEALS_OPEN)

    # Reklamacje są zakładką panelu (``/me/?tab=reklamacje``) – formularz stoi przy karcie pracy,
    # której dotyczy, a nie na wspólnej, przewijanej stronie.
    uczestnik.goto("/me/?tab=reklamacje")
    appeal_form = (
        page.locator("article.card").filter(has=page.locator("h3", has_text="Zadanie 1:")).locator("form")
    )
    expect(appeal_form).to_have_count(1)
    appeal_form.locator("textarea[name=argument]").fill(APPEAL_ARGUMENT)
    appeal_form.get_by_role("button", name="Złóż reklamację").click()
    expect(page.locator("ul.messages")).to_contain_text("Reklamacja została złożona")
    expect(page.locator("table", has_text="Złożone reklamacje")).to_contain_text("złożona")
    uczestnik.step("8-reklamacja-zlozona")

    # --- 9. Komisja odwoławcza przyznaje 6 -----------------------------------------------------
    login(komisja, komisja_email, "Komisja-Odwolawcza-2026")
    # Konto komisji jest jednocześnie w grupie ``reviewer`` (``_grant_reviewer_groups``), więc
    # logowanie ląduje na panelu recenzenta – do reklamacji wchodzimy odnośnikiem z nagłówka,
    # dokładnie tak, jak zrobiłby to człowiek.
    komisja.page.get_by_role("link", name="Reklamacje").click()
    expect(komisja.page).to_have_url(re.compile(r"/appeals/$"))
    sprawa = code_card(komisja.page, public_code)
    expect(sprawa).to_contain_text("Ocena wstępna: 5")
    expect(sprawa).to_contain_text(APPEAL_ARGUMENT)
    komisja.step("9-kolejka-komisji")

    sprawa.locator("select[name=status]").select_option("ACCEPTED")
    sprawa.locator("input[name=new_score]").fill(APPEAL_SCORE)
    sprawa.locator("textarea[name=justification]").fill(
        "Przypadek równości jest omówiony – rozwiązanie jest pełne i poprawne."
    )
    sprawa.get_by_role("button", name="Zapisz decyzję").click()
    expect(komisja.page.locator("ul.messages")).to_contain_text("Reklamacja została rozstrzygnięta")
    expect(komisja.page.locator("main")).to_contain_text("Brak reklamacji do rozpatrzenia")
    komisja.step("9-decyzja-komisji")

    # --- 10. Publikacja wyników ----------------------------------------------------------------
    set_phase(koordynator, stage_id, PHASE_APPEALS_CLOSED)

    # Przeliczenie i publikacja mają własny ekran etapu: wejście na niego niczego nie liczy,
    # a obie czynności stoją obok siebie, bo robi się je jedna po drugiej.
    koordynator.goto(f"/coordinator/stages/{stage_id}/results/")
    koordynator.page.get_by_role("button", name="Przelicz wyniki (podgląd)").click()
    expect(koordynator.page.locator("main")).to_contain_text("Podgląd wyników etapu")
    koordynator.page.locator("select[name=anonymization]").select_option("CODE")
    koordynator.page.get_by_role("button", name="Opublikuj wyniki").click()
    expect(koordynator.page.locator("ul.messages")).to_contain_text("Opublikowano wyniki etapu")
    koordynator.step("10-publikacja")

    # --- 11. Publiczna tabela: kod i 6 punktów, bez nazwiska -----------------------------------
    gosc = sessions("gosc")
    gosc.goto(f"/results/{stage_id}/")
    wiersz = gosc.page.locator("tr", has_text=public_code)
    expect(wiersz).to_have_count(1)
    # Kolumna „Razem” – jedyny ``<strong>`` w wierszu tabeli wyników.
    expect(wiersz.locator("td strong")).to_have_text("6")
    publiczna = gosc.page.locator("body").inner_text()
    assert participant_identity["last_name"] not in publiczna
    assert participant_identity["email"] not in publiczna
    assert participant_identity["school"] not in publiczna
    gosc.step("11-tabela-publiczna")

    # Ta sama tabela w części informacyjnej (Wagtail, ``/wyniki/``) – bez logowania.
    gosc.goto("/wyniki/")
    cms_wiersz = gosc.page.locator("tr", has_text=public_code)
    expect(cms_wiersz).to_have_count(1)
    expect(cms_wiersz.locator("td strong")).to_have_text("6")
    assert participant_identity["last_name"] not in gosc.page.locator("body").inner_text()
    gosc.step("11-wyniki-cms")

    # --- 12. Uczestnik widzi 6 i komentarz recenzenta ------------------------------------------
    uczestnik.goto("/me/?tab=wyniki")
    wyniki = page.locator("section", has=page.get_by_role("heading", name="Moje wyniki"))
    expect(wyniki).to_contain_text("razem 6 pkt")
    expect(wyniki).to_contain_text(REVIEWER_1_FEEDBACK)
    # ``comment_internal`` nie jest informacją dla uczestnika – ani tożsamość recenzenta.
    tresc = page.locator("body").inner_text()
    assert "Ocena robocza" not in tresc
    assert REVIEWER_1_EMAIL not in tresc
    uczestnik.step("12-wynik-uczestnika")
