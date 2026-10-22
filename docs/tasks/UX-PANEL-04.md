# UX-PANEL-04 — panel koordynatora: Start, paleta poleceń, okruszki, przypięte i ostatnio używane

Prośba organizatora z 21.10.2026: „popraw ergonomię konta koordynatora i zrób ładny, nowy panel
koordynatora”, spójny z nowymi panelami uczestnika (UX-PANEL-01), nauczyciela (UX-PANEL-02) i komisji
(UX-PANEL-03). Warstwa ergonomii i wyglądu: **żaden adres ani funkcja nie znika i nie zmienia działania**.

## 1. Audyt ergonomii (stan na `origin/main` z 21.10.2026)

Pomiar na Konkursie #1 z domyślnymi przełącznikami (cztery etapy, menu z `apps/web/coordinator_nav.py`):
menu ma **8 sekcji i ok. 60 odnośników** (sama sekcja „Etapy” – 20: każdy etap z czterema dziećmi,
wszystkie rozwinięte naraz), „Raporty” – 11–13 pozycji bez wspólnego mianownika (dyplomy, warsztaty,
RODO, lejek, eksport). Kliknięcia liczone od pulpitu, desktop 1440 × 900:

| Zadanie | Droga dziś | Kliknięcia | Problem |
|---|---|---|---|
| Wysłać komunikat e-mail | Komunikacja → Komunikaty | 1 + przewinięcie menu (pozycja ~36.) | pod „Etapami”; stanu zaplanowanych/nieudanych wysyłek nie widać nigdzie poza ekranem komunikatów |
| Zaświadczenia (warsztaty, opiekunowie, status ucznia, dyplomy) | Raporty → …, Uczestnicy i konta → Status ucznia | 1 + przewinięcie (~45.–50.) | cztery rodzaje „papieru” w dwóch sekcjach; zaświadczeń o statusie ucznia do weryfikacji nie liczy nic (świadomie: „pulpit nie płaci zapytania”) |
| Warsztaty (obecność, reguła zaświadczeń, materiały) | Raporty → … | 1 + przewinięcie | harmonogram warsztatów jest w CMS (`/cms/` → strony → „Warsztaty” → edycja: 4+ kliknięcia), z panelu nie ma do niego drogi; najbliższego warsztatu pulpit nie pokazuje |
| Uczestnicy / konkretna osoba | Uczestnicy i konta → Uczestnicy / pole szukaj | 1 | dobrze; wyszukiwarka nie zna jednak **ekranów** („lejek” nic nie znajduje) |
| Zdublowane konta | Uczestnicy i konta → Zdublowane konta | 1 | brak licznika – trzeba wejść, żeby wiedzieć, czy coś jest |
| Statystyki / lejek | Raporty → Lejek uczestnika | 1 + przewinięcie (~49.) | jw. |
| Wiadomości | Komunikacja → Wiadomości | 1 | dobrze (licznik) |
| Telefon: dowolna z powyższych | „Menu panelu” → przewinięcie ~60 wierszy → pozycja | 2 + długie przewijanie | zwinięte menu nie mówi, gdzie jesteś ani ile czeka |

Inne ustalenia:

- **Brak okruszków** – nad nagłówkiem stoi zawsze „Panel koordynatora”; na ekranach etapu (Zadania,
  Postęp…) nie widać, którego etapu i której sekcji dotyczą, poza podświetleniem w długim menu.
- **Brak skrótu klawiszowego** i palety – pole wyszukiwania jest jedynym „skrótem”, ale szuka wyłącznie
  obiektów (osoby, zadania, etapy, zgłoszenia).
- **Pulpit**: siedem kafli „co wymaga uwagi” zawsze, także z zerami (szum), brak podsumowania konkursu
  (uczestnicy, szkoły, etapy), brak „najbliższych terminów”; „Szybkie skróty” prowadzą głównie do
  ekranów RODO (retencja, rejestr), a nie do najczęstszych zadań.
- **Brak pozycji przypiętych / ostatnio używanych** – każdy koordynator ma swoje 3–4 ekrany dnia.
- **Wygląd**: arkusz `coordinator.css` sprzed nowych paneli (kanciaste kafle, brak ikon, inne focusy);
  w wysokim kontraście liczba na odznace menu jest biała na żółtym (ten sam błąd, który panel uczestnika
  naprawił regułą `:root[data-contrast="high"] .pp-badge`).

## 2. Decyzje

| # | Decyzja | Uzasadnienie |
|---|---|---|
| D1 | Nowa aplikacja `apps/coordinator_panel` (Start, przypięte, okruszki, paleta); w `apps/web` – jednowierszowe haki (znacznik ramy, widok pulpitu, wyszukiwarka, `urls.py`). | AGENT-RULES: nowy kod we własnym module |
| D2 | **Struktura menu bez zmian** (sekcje, pozycje, kolejność, nazwy, warunki). Pilnują jej `test_coordinator_nav_flags.EXPECTED_MENU_WITHOUT_FLAGS` i złote testy (`UNIWERSALNY-ETAP-2.md` § 2.1 p. 1 – „menu bajt w bajt”). Ergonomia przychodzi **obok** sekcji: przypięte i ostatnio używane nad sekcjami, ikony nagłówków, zwinięte dzieci etapów poza bieżącym, licznik i bieżąca pozycja na zwiniętym „Menu panelu”. | zmiana podziału na sekcje wymaga osobnej decyzji organizatora (§ 6) |
| D3 | Dzieci etapu (`Zadania`, `Przydziały i oceny`…) w `<details>`: rozwinięte tylko przy etapie z pozycją bieżącą albo – gdy żadna nie jest bieżąca – przy etapie „w ognisku” (`focus_stage`). Bez JS działa natywnie. | sekcja „Etapy” z 20 do ~8 wierszy |
| D4 | **Start** zostaje pod `/coordinator/` (ten sam widok, kontekst `attention`, `edition`, `stage_rows` bez zmian, karty etapów bez zmian). Dochodzą: rozszerzone „Co wymaga uwagi” (zaplanowane wysyłki, nieudane wysyłki z 30 dni, zaświadczenia o statusie ucznia do weryfikacji – za flagą, zdublowane konta, wiadomości, forum – za flagą), kafle niezerowe na górze, zerowe zwinięte w jedną linię; „Najbliższe terminy” (etapy, warsztaty z harmonogramu CMS, zaplanowane komunikaty – 30 dni); „Konkurs w liczbach”; „Przypięte”; „Szybkie skróty” – najczęstsze zadania. | jedno pytanie na ekran: co teraz |
| D5 | Liczniki Startu: jedno zapytanie agregujące na rodzaj, pakiet w pamięci podręcznej **per konkurs** (60 s – sprawy; 5 min – liczby konkursu; 10 min – duplikaty, bo grupowanie idzie w Pythonie po profilach). Koszt nie rośnie z liczbą uczestników (`test_panel_query_count_does_not_grow_with_participants`). | budżet `/coordinator/` w `query_budgets.py` |
| D6 | **Paleta poleceń** (`Ctrl+K` / `⌘K` / `/` poza polem tekstowym, przycisk „Polecenia” w menu): okno `<dialog>` z plikiem statycznym `coordinator_panel/palette.js` – pozycje menu z DOM, przypięte, ostatnio używane i akcja „Szukaj „…” w panelu” (istniejąca wyszukiwarka). Wzorzec ARIA combobox + listbox, strzałki/Enter/Esc. Bez JS zostaje pole wyszukiwania. | bez inline JS (CSP) |
| D7 | **Wyszukiwarka** (`apps/web/coordinator_search.py`) dostaje grupę „Ekrany panelu” – pozycje menu pasujące do frazy (bez diakrytyków), te same adresy i warunki co menu. Zero nowych zapytań poza etapami. | rozbudowa istniejącego mechanizmu, nie drugi |
| D8 | **Okruszki** „Start › sekcja › pozycja (› ekran etapu)” z pozycji bieżącej menu (`<nav aria-label>` + `aria-current`). Ekran spoza menu (karta uczestnika) dostaje okruszki pozycji nadrzędnej, jeśli ta świeci; nadpisany `panel_eyebrow` zostaje. | to samo źródło prawdy co menu |
| D9 | **Przypięte** – w bazie, per konto i konkurs (`coordinator_panel.PinnedItem`: ścieżka względem prefiksu konkursu, kolejność, data), najwyżej 8. Wolno przypiąć **wyłącznie ekran będący pozycją menu**; podpis bierze się z menu w chwili wyświetlenia (bez danych osobowych w bazie, pozycja za wyłączoną flagą znika). Przycisk „Przypnij do skrótów” / „Odepnij” w nagłówku ekranu (formularz POST, bez JS), lista z „Odepnij” na Starcie. POST `/coordinator/pins/` – rola w serwisie, limit 60/min per konto, ścieżka walidowana `resolve()` (tylko `/coordinator/…`). | ma iść za kontem między komputerami |
| D10 | **Ostatnio używane** – w przeglądarce (`localStorage`, klucz z kontem i konkursem, 6 pozycji), zapis przy wejściu na ekran z pozycją menu. Nie w bazie: to historia przeglądania – minimalizacja danych. | RODO art. 5 ust. 1 lit. c |
| D11 | Wygląd: tokeny ról z `app.css` i ikony liniowe w `currentColor` (jak `pp-*`), focus `--focus`, cele dotyku ≥ 44 px na telefonie, właściwości logiczne (RTL), `forced-colors`, poprawka odznaki w wysokim kontraście. Klasy menu używane przez testy (`panel-nav__*`, `data-nav-group`, „Menu panelu”) zostają. | spójność z nowymi panelami, WCAG 2.2 AA |
| D12 | Napisy nowych elementów przez `gettext` (polskie msgid) z tłumaczeniami w `apps/coordinator_panel/locale` (10 języków). Istniejące ekrany i pozycje menu koordynatora zostają po polsku (dług I18N spoza zakresu). | AGENT-RULES |
| D13 | RODO: `PinnedItem` to ustawienie konta personelu – kasowane z kontem (kaskada) i przy anonimizacji (`accounts.profile`), w eksporcie danych (`skroty_panelu_koordynatora`), reguła scalania `STAYS`. Rejestr czynności **1.28**. | rejestr ma opisywać każdą nową daną |

## 3. Menu (po zmianie)

Nad sekcjami: wyszukiwarka + przycisk „Polecenia (Ctrl+K)”, **Przypięte** (z bazy, gdy są), **Ostatnio
używane** (z przeglądarki, gdy są i działa JS). Sekcje i pozycje – bez zmian (D2), nagłówki z ikoną,
dzieci etapów zwinięte poza bieżącym (D3). Zwinięte „Menu panelu” (telefon): bieżąca pozycja i suma
liczników.

## 4. Koszt

`/coordinator/` na zimno: rama menu jak dotąd + przypięte (1) + pakiet spraw (1 agregat komunikatów,
1 zaświadczenia – za flagą, 1 duplikaty) + liczby konkursu (3) + terminy (1 strona „Warsztaty”).
Na ciepło (pamięć podręczna) – przypięte i pakiety bez zapytań. Inne ekrany panelu: +1 (przypięte, na
zimno). Budżet zmierzony i wpisany w `apps/core/tests/query_budgets.py`.

## 5. Testy

`apps/coordinator_panel/tests/` – Start (kafle, liczniki, izolacja konkursów, budżet i stały koszt),
przypinanie (rola, walidacja ścieżki, limit, cudzy konkurs, odpinanie), okruszki, dzieci etapów,
wyszukiwarka ekranów, paleta (plik statyczny, brak inline JS), eksport/anonimizacja. Strażnicy:
`test_coordinator_shell`, `test_coordinator_nav_flags`, `test_merge_rules`, `proctoring/test_gate`,
złote i niezmienniki `apps/tenancy/tests`, `test_docs_section_refs`, `test_translations`.

## 6. Poza zakresem

Przebudowa podziału menu na sekcje (np. wydzielenie „Warsztaty” i „Dokumenty” z „Raportów”) – wymaga
decyzji organizatora i zmiany `EXPECTED_MENU_WITHOUT_FLAGS` oraz złotego testu sekcji; tłumaczenie
istniejących ekranów i pozycji menu koordynatora; przypinanie ekranów spoza menu (karty osób – podpis
byłby daną osobową); zmiana harmonogramu warsztatów i danych.
