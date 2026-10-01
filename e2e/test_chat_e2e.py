"""Wiadomości szyfrowane end-to-end – pełny obieg w prawdziwej przeglądarce (zadanie CZ-01, § 12.5).

Testy jednostkowe sprawdzają osobno serwer (``apps/chat/tests/test_e2e.py``) i kryptografię w Node
(``backend/js_tests``). Tutaj sprawdzamy to, czego żaden z nich nie widzi: że **przeglądarka**
naprawdę szyfruje przed wysłaniem, odszyfrowuje u drugiej strony, odmawia złego hasła, przekazuje
kopię jawną przy zgłoszeniu i sprząta klucz przy wylogowaniu – i że jawna treść nie pojawia się
w żadnym żądaniu, w żadnej odpowiedzi serwera ani w zapisanym wierszu wiadomości.

Scenariusz (dwa osobne konteksty przeglądarki dla uczestników i trzeci dla koordynatora):

1. koordynator włącza „bez moderacji” + szyfrowanie (a jeśli trwa etap przyjmujący rozwiązania,
   który wymusza premoderację, na czas testu przesuwa go w przyszłość przez panel admina),
2. uczestnik B zapisuje się do katalogu, A i B tworzą klucze,
3. A zaczyna rozmowę szyfrowaną i wysyła wiadomość,
4. B odblokowuje klucz złym hasłem (błąd), potem dobrym – i widzi treść,
5. B zgłasza wiadomość – koordynator widzi kopię jawną z dopiskiem o autentyczności,
6. B się wylogowuje – baza IndexedDB z kluczem znika.

Na końcu (także po błędzie) koordynator przywraca ustawienia Wiadomości i oś czasu etapu.

Uruchomienie na stosie dev (bez pełnego ``scripts/e2e.sh``):

    docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile e2e run --rm --no-deps e2e \\
        sh -c "pip install -q -r requirements.txt && pytest -q --browser chromium test_chat_e2e.py"

Wymaga kont z ``manage.py seed_demo`` (koordynator jest superużytkownikiem – panel admina służy wyłącznie
do przesunięcia etapu). Na stosie dev test zostawia klucze i rozmowę uczestników demo; przed kolejnym
przebiegiem trzeba je usunąć (rozmowa jawna między tą samą parą zablokowałaby rozmowę szyfrowaną).
"""

from __future__ import annotations

import io
import json
import logging
import re
import socket
import threading
import uuid
import zipfile
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from conftest import COORDINATOR_EMAIL, DEMO_PASSWORD, RoleSession

LOGGER = logging.getLogger("e2e")

PARTICIPANT_A = "uczestnik1@example.com"
PARTICIPANT_B = "uczestnik2@example.com"
PASSPHRASE_A = "Haslo-do-wiadomosci-A-2026"
PASSPHRASE_B = "Haslo-do-wiadomosci-B-2026"

#: Pola osi czasu etapu w formularzu admina (data + godzina) – kolejność wymagana przez więzy etapu.
STAGE_FIELDS = (
    "opens_at",
    "deadline_at",
    "review_deadline_at",
    "appeal_window_opens_at",
    "appeal_window_closes_at",
)
FUTURE_OFFSETS_DAYS = (200, 214, 228, 230, 237)


# --- bezpieczny kontekst przeglądarki -------------------------------------------------------------------


def _pipe(source, target) -> None:
    try:
        while chunk := source.recv(65536):
            target.sendall(chunk)
    except OSError:
        pass
    finally:
        for end in (source, target):
            try:
                end.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _forward(host: str, port: int) -> tuple[socket.socket, int]:
    """Przekaźnik TCP ``127.0.0.1:<losowy port>`` → ``host:port`` w wątkach tła."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(64)

    def accept() -> None:
        while True:
            try:
                client, _address = server.accept()
                upstream = socket.create_connection((host, port))
            except OSError:
                return
            threading.Thread(target=_pipe, args=(client, upstream), daemon=True).start()
            threading.Thread(target=_pipe, args=(upstream, client), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    return server, server.getsockname()[1]


@pytest.fixture(scope="module")
def secure_base_url():
    """Adres aplikacji widziany przez przeglądarkę jako **bezpieczny kontekst**.

    WebCrypto (``crypto.subtle``) istnieje wyłącznie w bezpiecznym kontekście: HTTPS albo
    ``localhost``. Produkcja stoi za HTTPS, a scenariusz w sieci compose chodzi po czystym
    ``http://web:8000`` – tam przeglądarka nie dałaby czym szyfrować (flaga Chromium
    ``--unsafely-treat-insecure-origin-as-secure`` nie działa w trybie headless). Dlatego przeglądarka
    rozmawia z ``http://localhost:<port>``, a lokalny przekaźnik TCP w kontenerze testu przekazuje
    ruch do aplikacji. Gdy adres bazowy już jest ``localhost``, przekaźnika nie ma.
    """
    import os

    raw = os.environ.get("E2E_BASE_URL", "http://web:8000").rstrip("/")
    parts = urlsplit(raw)
    if parts.hostname in ("localhost", "127.0.0.1") or parts.scheme == "https":
        yield raw
        return
    server, port = _forward(parts.hostname, parts.port or 80)
    try:
        yield f"http://localhost:{port}"
    finally:
        server.close()


@pytest.fixture
def secure_sessions(request, browser, secure_base_url, browser_context_args):
    """Jak ``sessions`` z ``conftest.py``, tylko pod adresem z :func:`secure_base_url`."""
    created: list[RoleSession] = []

    def make(name: str) -> RoleSession:
        session = RoleSession(browser, secure_base_url, name, browser_context_args)
        created.append(session)
        return session

    yield make

    failed = getattr(request.node, "rep_call", None) is not None and request.node.rep_call.failed
    for session in created:
        if failed:
            session.dump("stan-koncowy")
        session.close()


def login(session, email: str) -> None:
    session.goto("/login/")
    session.page.fill("#id_username", email)
    session.page.fill("#id_password", DEMO_PASSWORD)
    session.page.get_by_role("button", name="Zaloguj").click()
    expect(session.page.locator("span.who")).to_have_text(email)


# --- oś czasu etapu (tylko gdy etap wymusza premoderację) ---------------------------------------------


def forcing_stage_ids(session) -> list[int]:
    session.goto("/coordinator/chat/settings/")
    return [
        int(value)
        for value in session.page.locator("[data-forcing-stage]").evaluate_all(
            "nodes => nodes.map(node => node.dataset.forcingStage)"
        )
    ]


def move_stage_to_future(session, stage_id: int) -> dict:
    """Przesuwa etap w przyszłość przez admina; oddaje oryginalne wartości pól do przywrócenia."""
    from datetime import datetime, timedelta

    session.goto(f"/admin/competitions/stage/{stage_id}/change/")
    page = session.page
    expect(page.locator("#id_deadline_at_0")).to_be_visible()
    original = {
        f"{field}_{part}": page.locator(f"#id_{field}_{part}").input_value()
        for field in STAGE_FIELDS
        for part in (0, 1)
    }
    original["grace_seconds"] = page.locator("#id_grace_seconds").input_value()
    sample = original["opens_at_0"]
    fmt = "%d.%m.%Y" if re.match(r"^\d{2}\.\d{2}\.\d{4}$", sample) else "%Y-%m-%d"
    now = datetime.now()
    for field, offset in zip(STAGE_FIELDS, FUTURE_OFFSETS_DAYS, strict=True):
        page.fill(f"#id_{field}_0", (now + timedelta(days=offset)).strftime(fmt))
        page.fill(f"#id_{field}_1", "12:00:00")
    page.click('input[name="_save"]')
    expect(page.locator(".errornote")).to_have_count(0)
    return original


def restore_stage(session, stage_id: int, original: dict) -> None:
    session.goto(f"/admin/competitions/stage/{stage_id}/change/")
    page = session.page
    # Najpierw pola późniejsze, potem wcześniejsze nie mają znaczenia – formularz zapisuje komplet naraz.
    for key, value in original.items():
        page.fill(f"#id_{key}", value)
    page.click('input[name="_save"]')
    expect(page.locator(".errornote")).to_have_count(0)


# --- ustawienia Wiadomości ------------------------------------------------------------------------------


def read_settings(session) -> dict:
    session.goto("/coordinator/chat/settings/")
    page = session.page
    return {
        "enabled": page.locator("#id_enabled").is_checked(),
        "peer_mode": page.locator('input[name="peer_mode"]:checked').get_attribute("value"),
        "e2e": page.locator("#id_e2e_enabled").is_checked(),
    }


def write_settings(session, *, enabled: bool, peer_mode: str, e2e: bool) -> None:
    session.goto("/coordinator/chat/settings/")
    page = session.page
    page.locator("#id_enabled").set_checked(enabled)
    page.locator(f'input[name="peer_mode"][value="{peer_mode}"]').check()
    page.locator("#id_e2e_enabled").set_checked(e2e)
    page.get_by_role("button", name="Zapisz ustawienia").click()
    expect(page.locator("ul.messages")).to_contain_text("zapisane")


# --- kroki uczestników -----------------------------------------------------------------------------------


def join_directory(session) -> None:
    session.goto("/me/profile/#wiadomosci")
    page = session.page
    page.locator("#id_discoverable").check()
    page.locator("#id_email_on_message").check()
    page.get_by_role("button", name="Zapisz ustawienia wiadomości").click()
    expect(page.locator("ul.messages")).to_contain_text("Ustawienia wiadomości zostały zapisane")


def create_key(session, passphrase: str) -> None:
    """Klucz szyfrowania – pierwszy albo nowy („Zapomniałem hasła”), jeśli konto już go ma."""
    session.goto("/me/messages/key/")
    page = session.page
    if page.get_by_role("heading", name="Twój klucz").count():
        page.get_by_text("Zapomniałem hasła do wiadomości").click()
    form = page.locator("form[data-e2e-setup]:visible").first
    fields = form.locator("[data-e2e-new-passphrase]")
    fields.nth(0).fill(passphrase)
    fields.nth(1).fill(passphrase)
    form.locator("button[type=submit]").click()
    expect(page.get_by_text("Rozmowy szyfrowane są odblokowane na tym urządzeniu.")).to_be_visible()
    expect(page.get_by_role("heading", name="Twój klucz")).to_be_visible()


def export_payload(session) -> tuple[dict, bytes] | None:
    """Paczka danych konta – zapisany wiersz wiadomości tak, jak go ma serwer.

    ``None`` przy 429: eksport ma własny limit żądań, a powtórzony w krótkim odstępie przebieg na
    stosie dev go wyczerpuje. Wtedy ta jedna asercja jest pomijana z ostrzeżeniem – odpowiedź HTML
    wątku jest sprawdzana niezależnie.
    """
    response = session.page.request.get(f"{session.base_url}/account/export/")
    if response.status == 429:
        LOGGER.warning("Eksport konta ograniczony limitem (429) – pomijam asercję na wierszu wiadomości.")
        return None
    assert response.ok, response.status
    with zipfile.ZipFile(io.BytesIO(response.body())) as archive:
        return json.loads(archive.read("dane.json").decode("utf-8")), response.body()


def indexeddb_names(page) -> list[str]:
    return page.evaluate("async () => (await indexedDB.databases()).map(db => db.name)")


# --- scenariusz ----------------------------------------------------------------------------------------


def test_szyfrowana_rozmowa_uczestnikow_od_klucza_do_zgloszenia(secure_sessions):
    secret = f"Sekret-E2E-{uuid.uuid4().hex[:12]} <b>nie HTML</b>"
    coordinator = secure_sessions("koordynator")
    alice = secure_sessions("uczestnik-a")
    bob = secure_sessions("uczestnik-b")
    login(coordinator, COORDINATOR_EMAIL)
    original_settings = read_settings(coordinator)
    moved: dict[int, dict] = {}
    try:
        # 1. Etap wymuszający premoderację wstrzymałby rozmowy szyfrowane – na czas testu w przyszłość.
        for stage_id in forcing_stage_ids(coordinator):
            moved[stage_id] = move_stage_to_future(coordinator, stage_id)
        assert forcing_stage_ids(coordinator) == []
        write_settings(coordinator, enabled=True, peer_mode="NONE", e2e=True)
        coordinator.step("wiadomosci-bez-moderacji-z-szyfrowaniem")

        # 2. Katalog i klucze.
        login(bob, PARTICIPANT_B)
        join_directory(bob)
        create_key(bob, PASSPHRASE_B)
        bob.step("klucz-b")
        login(alice, PARTICIPANT_A)
        create_key(alice, PASSPHRASE_A)
        alice.step("klucz-a")

        # 3. A zaczyna rozmowę szyfrowaną i wysyła wiadomość. Każde żądanie A jest zapisywane –
        #    jawna treść nie może pojawić się w żadnym z nich.
        sent_bodies: list[str] = []
        alice.page.on("request", lambda request: sent_bodies.append(request.post_data or ""))
        alice.goto("/me/messages/new/")
        alice.page.locator("li.chat-directory__item", has_text="Uczestnik2").get_by_role(
            "link", name="Napisz"
        ).click()
        start_button = alice.page.get_by_role("button", name="Rozpocznij rozmowę szyfrowaną")
        if start_button.count():
            start_button.click()
        expect(alice.page.locator(".chat-thread__title-row")).to_contain_text("szyfrowana end-to-end")
        thread_path = re.sub(r"^https?://[^/]+", "", alice.page.url)
        alice.page.locator("[data-e2e-input]").fill(secret)
        alice.page.get_by_role("button", name="Wyślij").click()
        expect(alice.page.locator("#chat-messages")).to_contain_text(secret)
        alice.step("wyslana-wiadomosc")
        assert all(secret not in body for body in sent_bodies), "jawna treść wyszła w żądaniu"
        assert any("ciphertext=" in body for body in sent_bodies)

        # Odpowiedź serwera (HTML wątku) i wiersz wiadomości (eksport danych konta A) – bez jawnej treści.
        raw_thread = alice.page.request.get(f"{alice.base_url}{thread_path}").text()
        assert secret not in raw_thread
        assert "data-ciphertext=" in raw_thread
        exported = export_payload(alice)
        if exported is not None:
            data, raw_export = exported
            assert secret.encode() not in raw_export
            encrypted_rows = [row for row in data["wiadomosci_wyslane"] if row.get("szyfrogram")]
            assert encrypted_rows and encrypted_rows[-1]["tresc"] is None

        # 4. B: najpierw zablokowany klucz, złe hasło, dobre hasło.
        bob.goto(thread_path)
        lock = bob.page.get_by_role("button", name="Zablokuj wiadomości")
        if lock.is_visible():
            lock.click()
        unlock = bob.page.locator("form[data-e2e-unlock]")
        expect(unlock).to_be_visible()
        unlock.locator("[data-e2e-passphrase]").fill("zupelnie-zle-haslo")
        unlock.get_by_role("button", name="Odblokuj rozmowy szyfrowane").click()
        expect(unlock.locator("[data-e2e-error]")).to_contain_text("Nieprawidłowe hasło")
        expect(bob.page.locator("#chat-messages")).not_to_contain_text(secret)
        unlock.locator("[data-e2e-passphrase]").fill(PASSPHRASE_B)
        unlock.get_by_role("button", name="Odblokuj rozmowy szyfrowane").click()
        expect(bob.page.locator("#chat-messages")).to_contain_text(secret)
        bob.step("odszyfrowana-u-b")
        # Odszyfrowana treść jest tekstem – znacznik z wiadomości nie stał się elementem strony.
        assert bob.page.locator("#chat-messages b", has_text="nie HTML").count() == 0

        # 5. Zgłoszenie z kopią jawną – widzi ją koordynator, z dopiskiem o autentyczności.
        message = bob.page.locator("li.chat-msg", has_text=secret)
        message.locator("summary", has_text="Zgłoś").click()
        message.locator("textarea[name=reason]").fill("Test zgłoszenia E2E")
        message.get_by_role("button", name="Wyślij zgłoszenie").click()
        expect(bob.page.locator("ul.messages")).to_contain_text("Zgłoszenie trafiło do organizatora")
        coordinator.goto("/coordinator/chat/moderation/")
        expect(coordinator.page.locator("main")).to_contain_text(secret)
        expect(coordinator.page.locator("main")).to_contain_text(
            "serwer nie może potwierdzić jej autentyczności"
        )
        coordinator.step("zgloszenie-w-kolejce")

        # 6. Wylogowanie czyści IndexedDB z kluczem.
        assert "olimpiada-chat" in indexeddb_names(bob.page)
        bob.page.get_by_role("button", name="Wyloguj").click()
        bob.page.wait_for_load_state("domcontentloaded")
        for _attempt in range(20):
            if "olimpiada-chat" not in indexeddb_names(bob.page):
                break
            bob.page.wait_for_timeout(250)
        assert "olimpiada-chat" not in indexeddb_names(bob.page)
    finally:
        # Ustawienia i oś czasu wracają do stanu sprzed testu – także po błędzie w połowie.
        write_settings(coordinator, enabled=original_settings["enabled"], peer_mode="NONE", e2e=False)
        write_settings(
            coordinator,
            enabled=original_settings["enabled"],
            peer_mode=original_settings["peer_mode"] or "OFF",
            e2e=original_settings["e2e"] and original_settings["peer_mode"] == "NONE",
        )
        for stage_id, original in moved.items():
            restore_stage(coordinator, stage_id, original)
