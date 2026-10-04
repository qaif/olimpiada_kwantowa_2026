# PROC-01: Nadzór zdalny (proctoring) etapów online w LiveKit

## 0. Cel i granice

Koordynator może **dla wybranego etapu online** (forma `SUBMISSIONS` albo `QUIZ`, oba konkursy)
włączyć nadzór zdalny: przed rozpoczęciem pracy uczeń przechodzi sprawdzenie sprzętu i składa
wyraźną zgodę, potem jego przeglądarka nadaje obraz z kamery (niska rozdzielczość, mała liczba
klatek) i – gdy etap tego wymaga – udostępnia ekran do **pokoju nadzoru** w LiveKit. Nadzorujący
(koordynatorzy, komisja wskazana przez koordynatora, w IQO także opiekunowie drużyn – wyłącznie dla
swojej delegacji) widzą siatkę swoich uczniów, piszą do nich, zgłaszają incydenty, proszą o pokazanie
pokoju lub dokumentu i odnotowują obecność. Komisja dostaje raport incydentów ucznia do odwołań.

Czego zadanie **nie** robi:
- **żadnej automatycznej analizy obrazu** (AI, wykrywanie twarzy, śledzenie wzroku, nagrywanie
  ekranu „na wszelki wypadek”) – decyzje podejmuje człowiek; to jest element minimalizacji (§ 8),
- nie śledzi zachowania w przeglądarce (przełączanie kart, schowek, ruch myszy),
- nie blokuje przeglądarki (to nie jest „lockdown browser”),
- nie zmienia pokoju webinaru ani jego interfejsu – bierze z `apps.webinars` klienta LiveKit
  (`livekit.py`: podpis tokenów, Twirp, weryfikacja webhooków), magazyn i SDK z `static/vendor/`,
- nie wysyła listów (zgoda opiekuna – istniejący mechanizm `apps.accounts.guardian`, § 5),
- okna czasowe w strefach (TZ-01) powstają równolegle – tu jest wyłącznie adapter (§ 4).

## 1. Aplikacja i przełączniki

Nowa aplikacja **`apps.proctoring`** (modele, serwisy, adaptery, bramka, webhooki, zadania),
widoki `apps/web/views/proctoring.py`, adresy `apps/web/urls_proctoring.py`, tłumaczenia
w `apps/proctoring/locale/` (10 katalogów).

- Flaga konkursu **`proctoring`** (`FEATURE_DEFAULTS`, domyślnie `False`). Wyłączona: adresy 404,
  menu, panele i bramka bez zmian (bramka nie robi wtedy żadnego zapytania).
- Wymaga serwera LiveKit z WEB-01 (`LIVEKIT_URL`, klucz, sekret). Flaga bez LiveKit: ekran
  koordynatora mówi, czego brakuje; uczniowie – zachowanie „LiveKit niedostępny” z § 6.
- Ustawienia instalacji:

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `PROCTORING_LEAD_MINUTES` | 30 | ile minut przed otwarciem okna ucznia wolno już włączyć nadzór |
| `PROCTORING_GRACE_MINUTES` | 30 | ile minut po zamknięciu okna działa jeszcze pokój nadzorujących |
| `PROCTORING_RETENTION_DAYS` | 30 | nagrania, zdjęcia dokumentu, dziennik zdarzeń i wiadomości – dni po publikacji wyników (i po oknie reklamacji) |
| `PROCTORING_MAX_RETENTION_DAYS` | 180 | bezpiecznik: dni po końcu etapu, gdy wyniki nigdy nie zostały ogłoszone |
| `PROCTORING_WINDOW_ADAPTER` | pusty | ścieżka funkcji `(stage, participant) -> (opens, closes)` z TZ-01; pusty = okno globalne etapu |
| `PROCTORING_STORAGE_BACKEND` | S3 prywatny | magazyn zdjęć dokumentu i nagrań (testy: pamięć) |

## 2. Model danych (`apps/proctoring/models.py`)

- `ProctoringConfig` (1:1 z `Stage`, zakres konkursu przez `stage__edition__competition`):
  `enabled`, `require_screen_share` (False), `require_microphone` (False), `id_photo`
  (`off`/`optional`/`required`, domyślnie `off`), `record` (**False**), `on_unavailable`
  (`allow` – uczeń pracuje, sesja dostaje znacznik „bez nadzoru”, domyślnie; `block` – treść etapu
  zamknięta do decyzji koordynatora), `instructions` (dopisek koordynatora), `room_key` (losowy),
  `updated_by`/`updated_at`.
- `ProctorAssignment`: (etap, konto) unikalne; `kind` = `coordinator` / `committee` / `leader`;
  dla `leader` – `delegation_id` (migawka z adaptera, sprawdzana ponownie przy każdym tokenie).
- `ProctoringSession`: (etap, uczestnik) unikalne – stan ucznia: wynik sprawdzenia (JSON z
  wartościami logicznymi i rodziną przeglądarki, bez odcisku sprzętu), zgoda (FK), zdjęcie dokumentu
  (klucz w magazynie, czas), `group` (pokój: `m` albo `d<id delegacji>`), `proctor` (FK przydziału),
  `started_at` (serwer potwierdził nadawanie kamery), `unproctored_at` (praca bez nadzoru wg
  `on_unavailable=allow`), `connected`/`camera_live`/`last_seen_at` (webhooki i puls), obecność
  (`unknown`/`present`/`absent`, kto, kiedy), **alternatywa** dla ucznia bez kamery (prośba, powód
  z listy + krótka uwaga, decyzja koordynatora, ustalenie), `hold` (komisja wstrzymuje usunięcie
  nagrań do wyjaśnienia), `purged_at`.
- `ProctoringConsent`: dowód zgody – sesja, wersja tekstu, skrót SHA-256 treści, czas, IP,
  `guardian_record` (wpis `ConsentRecord` zgody opiekuna, na którym oparto zgodę niepełnoletniego),
  `withdrawn_at`.
- `ProctoringEvent`: dziennik – sesja, rodzaj (zamknięta lista), źródło (`client`/`webhook`/
  `proctor`/`system`), czas, autor (konto nadzorującego albo puste), `detail` (mały JSON bez treści
  wiadomości i bez danych przeglądarki ponad rodzinę).
- `ProctoringMessage`: wiadomość nadzorującego do ucznia (`text` / `show_room` / `show_id`), treść
  ≤ 500 znaków, wysłana kanałem danych (tak/nie), potwierdzona przez ucznia (czas).
- `ProctoringIncident`: sesja, zgłaszający, `occurred_at` (domyślnie teraz), kategoria (zamknięta
  lista), waga (`info`/`warning`/`serious`), notatka ≤ 2000 znaków.
- `ProctoringRecording`: sesja, `egress_id`, `track_sid`, stan, klucz pliku (`proctoring/…`),
  rozmiar, długość, `purged_at`.

## 3. Role, zakres i pokoje (serwis `apps.proctoring.services`)

**Pokój = etap × grupa.** Grupa ucznia to jego delegacja (`d<id>`) w konkursie z delegacjami
albo `m` (pozostali). Nazwa: `proc-<slug>-<room_key>-<grupa>`.

**Dlaczego osobne pokoje, a nie uprawnienia subskrypcji per ścieżka.** W LiveKit token niesie
uprawnienia do **pokoju** (`room`, `canSubscribe`), a uprawnienia subskrypcji pojedynczych ścieżek
(`setTrackSubscriptionPermissions`) ustawia **nadający** – czyli przeglądarka ucznia. Izolacja
oparta na nich zależałaby od kodu po stronie klienta, którego serwer nie kontroluje. Osobny pokój na
delegację sprawia, że opiekun drużyny dostaje token **wyłącznie** do pokoju swojej delegacji –
serwer LiveKit odrzuci subskrypcję ucznia innego kraju, bo ten uczeń w ogóle nie jest w tym pokoju.
Koordynator wybiera grupę w siatce (token na jeden pokój naraz).

| Rola | Kto | Widzi | Token |
|---|---|---|---|
| koordynator | rola `coordinator` | wszystkich uczniów etapu, każdą grupę | dowolna grupa etapu |
| komisja | aktywny członek komisji z przydziałem do etapu | uczniów przydzielonych sobie | grupy swoich uczniów |
| opiekun drużyny | rola `team_leader` + aktywny wiersz opiekuna delegacji + przydział | uczniów **swojej** delegacji | wyłącznie `d<swoja delegacja>` |

- W grupie `m` członek komisji technicznie mógłby zasubskrybować ucznia przydzielonego komuś
  innemu (ten sam pokój) – granicą jest tu przydział organizacyjny i lista z serwera; to świadoma
  decyzja (komisja jest stroną organizatora), opisana w podręczniku.
- **Token ucznia**: `identity` = pseudonim HMAC (`p-…`, osobny na etap), **pusta nazwa**,
  `canPublish` z `canPublishSources` = `camera` (+ `screen_share`, + `microphone` gdy wymagane),
  **`canSubscribe=false`**, `canPublishData=false`, `canUpdateOwnMetadata=false`. Inni uczniowie
  w pokoju widzą wyłącznie pseudonimy; obrazu nie odbierają.
- **Token nadzorującego**: `identity` = `x-…`, `hidden=true` (uczniowie nie widzą nadzorujących),
  `canSubscribe=true`, `canPublish=false`, `canPublishData=false`.
- Wiadomość do ucznia idzie **przez serwer**: zapis w bazie i dzienniku → `RoomService/SendData`
  z `destination_identities=[uczeń]` (temat `proctoring`). Konsola ucznia dodatkowo odpytuje serwer
  co 20 s, więc wiadomość dociera także przy zerwanym kanale danych.
- Okno: token ucznia od `opens − LEAD` do `closes` (okno efektywne, § 4); token nadzorującego
  do `closes + GRACE`. Poza oknem – zdanie dla człowieka.
- Cudzy etap, etap bez nadzoru, osoba bez roli – 404. Limit: `proctoring` 120/h per konto.

## 4. Adaptery

- **Okno ucznia** (`apps.proctoring.windows.effective_window(stage, participant)`): funkcja ze
  `PROCTORING_WINDOW_ADAPTER`, gdy ustawiona (TZ-01 poda okno w strefie ucznia), inaczej
  `(stage.opens_at, stage.submission_deadline)`. Błąd adaptera = okno globalne + log.
- **Delegacje** (`apps.proctoring.delegations`): `participant_delegation_id`, `leader_delegation_id`,
  `delegation_label`. Czytają modele DEL-01 (`Participant.delegation_id`,
  `delegation_services.leader_for`), gdy są w instalacji; bez nich – brak delegacji (jedna grupa `m`,
  opiekunów nie ma).

## 5. Uczeń (`/me/proctoring/<etap>/`)

Konsola nadzoru – jedna strona z krokami liczonymi przez serwer:
1. **Informacja i zgoda** – klauzula (kto, po co, co jest nagrywane lub nie, kto widzi, jak długo,
   prawa, kontakt), pole wyboru, wersja `PROCTORING_CONSENT_VERSION`. Niepełnoletni: wymagana
   **potwierdzona online zgoda opiekuna** (`guardian_status == confirmed`); bez niej – odnośnik do
   „Poproś opiekuna o zgodę”. Wycofanie zgody – przycisk; skutek: sesja bez nadzoru wymaga decyzji
   koordynatora (alternatywa).
2. **Sprawdzenie sprzętu** (JS): obsługa WebRTC, kamera (podgląd), mikrofon (gdy wymagany, poziom),
   uprawnienie do udostępnienia ekranu (gdy wymagane). Wynik idzie POST-em; serwer zapisuje wartości
   logiczne i rodzinę przeglądarki.
3. **Zdjęcie dokumentu** (gdy włączone): klatka z kamery → JPEG ≤ 300 KB, sprawdzenie sygnatury,
   prywatny bucket. „Opcjonalne” można pominąć.
4. **Start**: token → publikacja kamery 320×240, 10 kl./s, ≤ 150 kb/s, bez simulcastu (+ ekran
   1280×720, 2 kl./s, ≤ 300 kb/s); serwer **sprawdza w LiveKit** (`GetParticipant`), że ścieżka
   kamery jest, i dopiero wtedy otwiera etap. Konsola zostaje otwarta w osobnej karcie; strona
   etapu otwiera się w nowej karcie.
- Zerwanie strumienia: czerwony komunikat w konsoli, pasek ostrzeżenia na stronie etapu
  (`BroadcastChannel` między kartami), zdarzenie w dzienniku (klient + webhook), ponowne łączenie.
- **Bez kamery**: przycisk „Nie mogę użyć kamery” → prośba o alternatywę (powód z listy, krótka
  uwaga bez danych o zdrowiu) → koordynator zatwierdza (np. nadzór telefoniczny) albo odrzuca.
- **Bramka** (`ProctoringGateMiddleware`, `process_view`): treść zadania (PDF), wysyłka rozwiązania
  (WWW i API), start i strona podejścia testu – w etapie z nadzorem, w oknie ucznia – wymagają sesji
  „gotowej”: zgoda + (start potwierdzony **albo** alternatywa zatwierdzona **albo** praca bez
  nadzoru przy `allow`). Autozapis testu nie jest bramkowany nigdy (odpowiedzi nie giną).
  Wyłączona flaga, etap bez nadzoru, osoba bez zgłoszenia do etapu – bramka przepuszcza bez pytań.

## 6. LiveKit niedostępny

`on_unavailable=allow` (domyślnie): gdy token albo połączenie się nie udaje, uczeń klika
„Kontynuuj bez nadzoru”, serwer zapisuje `unproctored_at` + zdarzenie `livekit_unavailable`,
koordynator i komisja widzą znacznik w siatce i raporcie. `block`: treść etapu zostaje zamknięta,
uczeń widzi prośbę o kontakt, koordynator może zatwierdzić alternatywę ręcznie.

## 7. Nadzorujący (`/proctoring/…`)

- Lista etapów z nadzorem dla tej osoby; siatka `/proctoring/<etap>/`: strony po 12/16/24 kafle
  (lista z serwera, już zawężona do zakresu), połączenie z `autoSubscribe=false` i subskrypcja
  **tylko kafli widocznej strony** (zmiana strony = odsubskrybowanie poprzednich); ekran ucznia –
  dopiero po kliknięciu „Pokaż ekran”.
- Czynności (POST, audyt, dziennik): wiadomość, „pokaż pokój”, „pokaż dokument”, incydent (czas,
  kategoria, waga, notatka), obecny/nieobecny. Wyłącznie dla ucznia w zakresie – inaczej 404.
- **Raport ucznia** `/proctoring/<etap>/s/<sesja>/` (HTML do wydruku) i **eksport** CSV etapu
  (incydenty) – koordynator i komisja odwoławcza; audyt `proctoring.report_viewed` / `exported`.
- Koordynator (`/coordinator/proctoring/…`): ustawienia etapu, przydziały, „rozdziel uczniów”,
  zmiana nadzorującego ucznia, decyzje o alternatywie, nagrania, wstrzymanie usunięcia.

## 8. Nagrywanie, retencja, RODO

- Nagrywanie **domyślnie wyłączone**. Włączone: po webhooku `track_published` (kamera) zadanie
  Celery woła `Egress/StartTrackEgress` – zapis **surowej ścieżki bez transkodowania** (WebM/VP8,
  ~150 kb/s) do `proctoring/<konkurs>/<pokój>/<pseudonim>/…` w prywatnym buckecie; egress ma prawo
  zapisu wyłącznie do `webinars/` i `proctoring/`. Ekranu nie nagrywamy.
- Odtwarzanie: koordynator i komisja odwoławcza, adres podpisany na 15 min, audyt
  `proctoring.recording_viewed`.
- **Retencja**: nagrania, zdjęcia dokumentu, dziennik zdarzeń i wiadomości – usuwane przez beat
  (`purge_expired`, raz dziennie) `PROCTORING_RETENTION_DAYS` po późniejszej z dat: publikacja
  wyników, koniec okna reklamacji; bezpiecznik `MAX_RETENTION_DAYS` po końcu etapu; `hold` wstrzymuje.
  Incydenty, obecność i zgody zostają jako dokumentacja zawodów (retencja edycji).
- Rejestr czynności: wiersz warunkowy „Nadzór zdalny etapów online” (flaga). Eksport danych konta:
  sekcja `nadzor_zdalny`. Anonimizacja konta: kasuje zdjęcia i nagrania, wycofuje zgody.
- Ocena skutków (DPIA): `docs/PODRECZNIK-ORGANIZATORA.md` – nota dla organizatora.

## 9. Bezpieczeństwo i CSP

- Izolacja konkursów (querysety zawężone, 404), rola przy każdym tokenie i czynności (serwis).
- Audyt: `proctoring.config_updated`, `assigned`, `unassigned`, `distributed`, `consent_given`,
  `consent_withdrawn`, `started`, `unproctored`, `joined` (nadzorujący, grupa),
  `message_sent`, `incident_created`, `attendance`, `alternative_requested`/`decided`,
  `recording_viewed`, `report_viewed`, `exported`, `hold_set`/`hold_released`, `purged` –
  bez tokenów i bez treści wiadomości.
- CSP bez zmian względem WEB-01 (`connect-src` z originem LiveKit). Kod tylko w plikach statycznych.
- Webhook: wspólny adres WEB-01; zdarzenia pokoi `proc-` i egressów nadzoru przekazuje
  `apps.webinars.services.handle_webhook` do `apps.proctoring.webhooks.handle_event`.

## 10. Testy (`apps/proctoring/tests/`, bez sieci)

Tokeny i uprawnienia per rola (w tym izolacja opiekuna – pokój tylko swojej delegacji, 404 na
cudzego ucznia), bramka (zgoda, start, alternatywa, `allow`/`block`, flaga wyłączona = brak
zapytań), zgoda niepełnoletniego bez zgody opiekuna, wiadomość (SendData + zapis), incydent i raport,
webhooki (połączenie, ścieżki, egress), nagrania (start egress tylko przy `record`), retencja
(usuwa po terminie, `hold` wstrzymuje), eksport, przekłady w 10 katalogach.

## 11. Definicja ukończenia

ruff format + check; `makemigrations --check`; testy nowej i zmienionych aplikacji; `msgfmt --check`
dla 10 katalogów aplikacji; kontrakt tras djcms (`proctoring` jako nowy pierwszy segment).
