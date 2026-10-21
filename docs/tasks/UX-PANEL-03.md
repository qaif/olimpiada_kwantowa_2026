# UX-PANEL-03 — panel członka komisji: menu, Start, „Do oceny”, powiadomienia

Prośba organizatora z 21.10.2026: „ładny panel członka komisji” (komitet oceniający / recenzenci,
komisja odwoławcza), spójny z nowym panelem uczestnika (UX-PANEL-01).

## 1. Inwentaryzacja (stan na `origin/main` z 21.10.2026)

| Funkcja | Adres | Flaga / konfiguracja | Kto widzi dziś | Gdzie był odnośnik |
|---|---|---|---|---|
| Kolejka recenzenta (zakładki Do zrobienia / W toku / Wystawione / Anulowane, pasek podsumowania, ZIP) | `/review/`, `/review/download/` | – | rola `reviewer` + **aktywny** `CommitteeMember` tego konkursu (`ReviewerRequiredMixin` → `active_reviewer_profile`) | pasek konta „Recenzje”, przycisk w nagłówku strony głównej, cel po zalogowaniu |
| Ekran oceny (dwie kolumny: praca + panel oceny, skala/rubryka, szkic, adnotacje, licznik czasu, seria „5 z 18”) | `/review/<id>/` (+ `draft/`, `submit/`, `revise/`, `heartbeat/`, `line-note/`, `issues/`) | rubryka: `RubricCriterion` zadania; role recenzenckie: flaga `reviewer_roles` | jw., tylko własne przydziały (`grading.services.reviews_for_reviewer`) | wiersz kolejki |
| Runda rozjemcza (druga ocena przy rozbieżności) | ten sam ekran, `Review.round = ROUND_TIEBREAK` (2) | – (rozjemcę wyznacza koordynator, `assign_third_reviewer`) | rozjemca | wiersz kolejki (kolumna „Runda”) |
| Porównanie ocen i notatki | `/review/<id>/compare/`, `notes/` | odsłonięcie ocen (`grading.comparison`) | jw. | ekran oceny |
| Sugestia AI | panel na ekranie oceny | flaga `ai_grading` (`ai_grading.services.reviewer_context`) | jw. | ekran oceny |
| Rozwiązanie wzorcowe, uwagi dla recenzentów, szablony komentarzy | `/review/problems/<id>/model-solution/`, `Problem.reviewer_notes`, `CommentSnippet` | plik / tekst przy zadaniu | aktywny recenzent albo koordynator (`CommitteeMaterialsMixin`) | tylko ekran oceny |
| Zgłoszenie problemu z pracą | `/review/<id>/issues/` | – | jw. | ekran oceny |
| Komisja odwoławcza (kolejka reklamacji, decyzja) | `/appeals/`, `/appeals/<id>/decide/` | flaga `appeals` („Procedura odwoławcza”, włączona z założenia) | rola `appeals` + aktywny profil z `is_appeals_committee` (`AppealsCommitteeRequiredMixin`); konflikt interesów wyklucza sprawę z kolejki | pasek konta „Reklamacje”, cel po zalogowaniu |
| Rozmowy kwalifikacyjne (etap ustny) i pokoje wideo udostępnione komisji | karty na `/review/` i `/appeals/`, wejście `committee-interview-slot-join` | przepustki Jitsi (`jitsi_jwt.jwt_enabled()`) | członek komisji (`video_rooms.committee_member`) | karty w kolejkach |
| Własne pokoje wideo | `/review/video-rooms/` | uprawnienie `CommitteeMember.video_room_issuer` + przepustki | członek komisji z uprawnieniem | karta „Pokoje wideo komisji” |
| Webinary | `/webinars/`, karta w kolejkach | flaga `webinars` + LiveKit (`webinars.services.available`) | odbiorcy webinaru | karta w kolejkach |
| Forum | `/forum/` | flaga `participant_forum` | koordynator, recenzent, komisja, uczestnik (`can_use_forum`) | pasek konta |
| Materiały z warsztatów | `/warsztaty/materialy/` | flaga `workshop_materials` | także recenzent i komisja | pasek konta |
| Przegląd tłumaczeń interfejsu (L10N-01) | `/translations/` | nadanie `TranslatorGrant` | każdy z nadaniem (`translation_review.services.is_translator`) – niezależnie od roli | stopka |
| Tłumaczenia treści zadań (TR-01) | `/delegation/translations/…` | delegacje | opiekun drużyny, koordynator – **nie** komisja | – |
| Okręg / województwo członka | `CommitteeMember.district`, `region` | podział terytorialny konkursu | – (reguła konfliktu interesów `grading.services.has_district_conflict`; adresat komunikatów `COMMITTEE_DISTRICT`) | – |
| Zaproszenie do komitetu | kod zaproszenia przy rejestracji | – | – | list od koordynatora |
| Konto: profil, hasło, 2FA | `/account/profile/`, `/account/password/`, `/account/2fa/` | 2FA: `TWO_FACTOR_ENABLED`; wymóg dla personelu – polityka SEC-01 (`apps/staff_mfa`, warstwa pośrednia) | zalogowany | pasek konta (adres e-mail) |
| Przypomnienia o terminie oceniania | list e-mail (beat `grading.tasks.remind_overdue_reviews`, przycisk koordynatora) | – | recenzent | e-mail |
| Powiadomienia w panelu | – | – | **tylko uczestnicy** (UX-PANEL-01 D9) | – |
| Czat komitetu | – | – | brak (czat CZ-01 to uczestnik ↔ organizator) | – |

**Anonimowość.** Ocenianie jest ślepe (PROJEKT.md 2.2, docstring `apps/web/views/reviewer.py`):
w kontekście ekranów komisji nie ma imienia, nazwiska, e-maila ani szkoły uczestnika – jedynym
identyfikatorem pracy jest kod publiczny (`public_code`). Komisja odwoławcza widzi ten sam kod
i oceny rundy 1 bez tożsamości recenzentów. Ta reguła zostaje bez zmian i obejmuje nowe ekrany
(Start pokazuje wyłącznie liczby, lista „Do oceny” – kod publiczny).

## 2. Decyzje

| # | Decyzja | Uzasadnienie |
|---|---|---|
| D1 | Nowa aplikacja `apps/committee_panel`: menu w Pythonie (`nav.py`, wzorem `participant_panel/nav.py`), znacznik `{% committee_nav %}`, rama `committee_panel/base.html`. | jedno źródło prawdy; nierozwiązany adres = pozycja znika |
| D2 | **Bez wydzielania wspólnego komponentu.** Rama komisji używa arkusza `participant_panel/panel.css` i arkusza ikon `participant_panel/_icons.html` **bez ich zmiany** (klasy `pp-*`); własne dodatki w `committee_panel/committee.css`. | równolegle powstaje panel nauczyciela – zmiana plików panelu uczestnika groziłaby konfliktem; ten krok jest w pełni wstecznie zgodny |
| D3 | Podpis zwiniętego menu: „Menu komisji” (koordynator: „Menu panelu”, uczestnik: „Menu uczestnika”); nawigacja „Nawigacja panelu komisji”. | złote testy nawigacji rozpoznają panele po podpisie |
| D4 | Widoczność pozycji wyłącznie z istniejących warunków: `is_reviewer`, `is_appeals_committee` (procesor `roles`), `jwt_enabled()`, `video_room_issuer`, `webinars.available`, `can_use_forum`, `workshop_materials_available`, `is_translator`, `twofactor.is_enabled`. „Druga ocena” – gdy recenzent ma choć jedną recenzję rundy 2. Żadnych nowych flag. | spójność; menu nie jest zabezpieczeniem |
| D5 | Nowe ekrany: Start `/committee/`, Materiały dla komisji `/committee/materials/`, Powiadomienia `/committee/notifications/`. Dostęp: członek komisji tego konkursu (`video_rooms.committee_member` – aktywny recenzent **albo** komisja odwoławcza); Materiały – tylko recenzent. | te same definicje ról, co bramki istniejących paneli |
| D6 | Cel po zalogowaniu recenzenta i członka komisji odwoławczej: Start (`/committee/`) zamiast `/review/` / `/appeals/`. | jak uczestnik: wejście na pulpit; kolejka jest jednym kliknięciem dalej |
| D7 | Kolejka `/review/` dostaje w ramie: `?tab=` (zakładka otwierana przy wejściu – pozycje menu „Do oceny” / „Ocenione”), filtr `?stage=` / `?problem=` / `?round=` (formularz GET, bez JS). Filtr zawęża zakładki; pasek podsumowania i ZIP liczą całość. Logika oceniania, zapisy i walidacja – bez zmian. | prośba o filtr po zadaniu/etapie; zakładki bez JS dalej są sekcjami |
| D8 | Ekran oceny `/review/<id>/` **zostaje bez ramy**: jest już dwukolumnowy (praca + przyklejony panel oceny od 1001 px), a kolumna menu zabrałaby podglądowi PDF 16 rem. | ekran oceny nie jest surowy – dopracowania nie wymaga |
| D9 | Komisja odwoławcza i własne pokoje wideo dostają ramę; kolejka reklamacji i formularz decyzji – napisy przez gettext (dotąd tylko po polsku), bez zmiany decyzji i walidacji. | komisja IQO bywa międzynarodowa |
| D10 | Profil `/account/profile/` członka komisji w ramie komisji (wybór ramy w jednym wierszu `extends`). | spójność menu „Konto” |
| D11 | Powiadomienia dla komisji – rozszerzenie D9 z UX-PANEL-01: cztery nowe rodzaje w `apps/notifications` (nowe prace do oceny, ocena rozjemcza, termin oceniania, reklamacja do rozpatrzenia), haki w `apps/committee_panel/notifications.py` wołane jedną linijką z `grading.services` (przydział automatyczny, ręczny, z reguły zadania, rozjemca), `grading.tasks.remind_overdue_reviews` i `appeals.services.file_appeal`. Odbiorcy: wyłącznie członkowie komisji tego konkursu; reklamacja – członkowie komisji odwoławczej **bez konfliktu interesów** (ta sama reguła, co kolejka). | dziś jedynym sygnałem jest e-mail |
| D12 | Treść powiadomień komisji: nazwa etapu albo liczba prac – **nigdy** kod publiczny, imię ani punkty. Klucz scalania na etap (`review-assigned:<etap>`), kolejkę (`appeals-queue`) i termin (`review-deadline`) – dzwonek mówi „coś czeka”, nie liczy prac. | anonimowość, D6 z UX-PANEL-01 |
| D13 | RODO: to dane pracownicze (członkowie komisji) – ta sama retencja 90 dni, kasowanie przy anonimizacji i usunięciu konta, eksport (`powiadomienia_w_panelu` już obejmuje każde konto). Rejestr czynności **1.26**: nowa kategoria w wierszu „komitet”. Jeśli równoległa zmiana (panel nauczyciela) też podbije wersję, przy scalaniu bierze się kolejny numer i obie notki. | rejestr ma opisywać każdą nową daną |
| D14 | Migracja `notifications/0002_committee_kinds` – wyłącznie `choices` pola `kind` (bez zmian w bazie). | `makemigrations --check` |

## 3. Menu

- **Start**: Pulpit, Powiadomienia (licznik).
- **Ocenianie** (recenzent): Do oceny (licznik otwartych), Ocenione, Druga ocena (licznik otwartych, gdy są recenzje rundy 2), Materiały dla komisji.
- **Komisja odwoławcza** (komisja): Reklamacje do rozpatrzenia (licznik).
- **Spotkania**: Rozmowy i pokoje komisji* (kotwica na Starcie), Moje pokoje wideo*, Webinary*.
- **Wiadomości i materiały**: Forum*, Materiały z warsztatów*, Tłumaczenia interfejsu*, Zgłoś problem.
- **Konto**: Profil i dane, Hasło, Weryfikacja dwuetapowa*.

`*` – pozycja warunkowa (D4).

## 4. Start

Kafle (tylko liczby, bez danych uczestników): „Do oceny” per etap (otwarte, po terminie, najbliższy termin
przydziału i termin oceniania etapu, przycisk „Oceniaj” z filtrem etapu), „Postęp” (wystawione / przydzielone,
`<progress>` z opisem tekstowym), „Druga ocena”, „Reklamacje do rozpatrzenia”, „Najbliższe terminy”
(termin przydziału, termin oceniania etapu, najbliższa rozmowa, najbliższy webinar), „Twoja rola”
(recenzent / komisja odwoławcza, województwo i region), „Na skróty” (ZIP, materiały, „Jak oceniać”, zgłoś problem).
Poniżej istniejące karty rozmów kwalifikacyjnych, pokoi i webinarów (kotwica `#spotkania`).

## 5. Koszt

Menu komisji: licznik przydziałów (jedno `aggregate` po recenzjach), licznik kolejki reklamacji (jedno `COUNT`,
tylko dla komisji odwoławczej), licznik powiadomień (jedno `COUNT`) – zapamiętane na żądaniu, więc Start
ich nie powtarza. Start: dodatkowo zestawienie per etap (jedno `GROUP BY`) i etapy (jedno zapytanie).
Budżet `committee/start` w `apps/core/tests/query_budgets.py`; liczba zapytań nie rośnie z liczbą prac.

## 6. Poza zakresem

Zmiana logiki oceniania, rund i konfliktu interesów; czat komitetu; tłumaczenie kart rozmów i pokoi wideo
(`_committee_interviews.html`, `_committee_rooms.html`, `video_rooms.html` – istniejący dług I18N, tylko po polsku);
powiadomienia o rozmowach kwalifikacyjnych i webinarach (webinary mają własne listy).
