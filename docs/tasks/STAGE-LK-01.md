# STAGE-LK-01: Rozmowy etapu w LiveKit jako alternatywa dla Jitsi (z opcjonalnym nadzorem)

## 0. Cel i granice

Drugi etap Olimpiady Kwantowej (rozmowa kwalifikacyjna, `StageFormat.INTERVIEW`) odbywa się dziś
w pokojach Jitsi z przepustkami platformy („wariant A”, `docs/OPERACJE.md` § 25). Koordynator może
teraz **dla wybranego etapu** wybrać dostawcę **LiveKit** – ten sam serwer, co webinary (WEB-01).
Jitsi zostaje domyślne i **niezmienione** (testy Jitsi przechodzą bez zmian). Rozmowa w LiveKit może
być dodatkowo objęta **nadzorem zdalnym** (PROC-01).

Czego zadanie **nie** robi: nie zmienia pokoi bez terminu ani linków-zaproszeń (`VideoRoom`, § 25.8 –
zostają na Jitsi), nie dodaje nagrywania pokoi rozmów (Jitsi go nie ma – `ENABLE_RECORDING=0`), nie
włącza nadzoru nikomu (flaga `proctoring` zostaje domyślnie wyłączona).

## 1. Wybór dostawcy

- `VideoProvider.LIVEKIT = "livekit"` (migracja `competitions.0034`, zmiana listy wyboru). Formularz
  etapu pokazuje tę opcję **wyłącznie** przy skonfigurowanym LiveKit (`LIVEKIT_URL`, klucz, sekret)
  albo gdy etap już ją ma; bez serwera wybór LiveKit to błąd pola. Instalacja bez LiveKit widzi
  listę dostawców taką, jak przed zmianą.
- Adres pokoju terminu: `livekit://<nazwa>` (ta sama nazwa, co pokoje Jitsi:
  `olimpiada-<edycja>-<termin>-<losowe>`, `video.build_meeting_url`). To **identyfikator**, nie link –
  wchodzi się widokami wejścia platformy. Ręczny link przy terminie dalej wygrywa (jak w Jitsi).

## 2. Uprawnienia – jedna reguła dla obu dostawców

Nowy moduł `apps.competitions.room_access` przejął regułę „kto, kiedy, z jaką rolą” z widoków Jitsi
(bez zmiany zachowania) i oddaje rozstrzygnięcie `RoomPass` (adres, rodzaj, rola, moderator, nazwa,
okno). Widoki wejścia (`interview-join`, `interview-precheck`, `coordinator-interview-slot-join`/
`-precheck`, `committee-interview-slot-join`/`-precheck`) pytają tę regułę, a potem wydają bilet
dostawcy: Jitsi – JWT Prosody we fragmencie adresu (bez zmian), LiveKit – przekierowanie na stronę
pokoju na platformie, gdzie JS pobiera token POST-em; widok tokenu **ponownie** pyta tę samą regułę.

| Kto | Jitsi | LiveKit |
|---|---|---|
| uczestnik – własny, niezdyskwalifikowany zapis, okno terminu (`LEAD`/`GRACE`) | przepustka bez `moderator` | `canPublish`, `canSubscribe`, `canPublishData`, bez `roomAdmin`; usunięty przez moderatora – odmowa (403), bez głosu – bez `canPublish` |
| uczestnik – próba sprzętu, do końca okna, `PRECHECK_MINUTES` | przepustka bez `moderator`, pokój `…-test` | jak wyżej, **osobny** pokój `…-b<zapis>-test` |
| koordynator konkursu / aktywny członek komisji – rozmowa w oknie terminu | `moderator: true` | jak uczestnik, **bez `roomAdmin`** (przegląd L-1); rola `presenter` w odpowiedzi; „odbierz/oddaj głos”, „usuń”, „wpuść ponownie” przez platformę (`POST …/interview-slots/<id>/room-control/`, limit `interview_control`), audyt `interview.room_control` z pseudonimem osoby |
| koordynator / komisja – próba sprzętu, bez okna | bez `moderator` | bez `roomAdmin`, pokój `…-s<konto>-test` |
| cudzy termin, cudzy konkurs, brak roli, zawieszona komisja, niezalogowany | 404 / 403 / logowanie | to samo (te same mixiny widoków) |

Ważność biletu: `nbf`/`exp` = okno terminu (Jitsi i LiveKit), próba sprzętu – `PRECHECK_MINUTES`.
Okna TZ-01 nie dotyczą etapów w formie rozmowy (`time_windows.access.plan_for` – `None`), więc
obowiązuje to samo okno terminu u obu dostawców. Nazwa w pokoju – „Imię N.” (komisja bez imienia –
„Komisja”), `identity` – pseudonim konta z WEB-01 (`u-…`).

Ekrany i listy rozpoznają pokój platformy jedną funkcją `room_access.is_platform_room` (Jitsi
z przepustkami **albo** LiveKit): karta rozmowy uczestnika, kolumna „Link” koordynatora, karta komisji,
listy potwierdzenia i przypomnienia – bez zmian w szablonach.

**Poprawki po przeglądzie (4.10.2026).** Przed każdym tokenem `CreateRoom` (serwer ma
`auto_create: false`); decyzje moderatora zapisane przy terminie (`proctoring.InterviewRoomBlock`) –
przeżywają ponowne wejście; `livekit://` zawsze pokojem platformy (bez serwera – 502); nagranie kamery
w rozmowie z nadzorem wyłącznie ucznia z ważną zgodą i bez zatwierdzonej alternatywy (`may_record`,
także w zadaniu Celery), sesji nadzoru nie zakłada webhook; zmiana dostawcy z LiveKit przy włączonym
nadzorze – odmowa w formularzu, a `config_for` ignoruje nadzór etapu, który przestał się do niego nadawać.
Sesja nadzoru zapisuje pseudonim konta (`account_identity`) – webhook pokoju rozmowy trafia do niej
jednym zapytaniem.

## 3. Infrastruktura

Bez drugiej integracji: token i polecenia – klient WEB-01 (`apps.webinars.livekit`), webhook – wspólny
adres WEB-01 (zdarzenia pokoi rozmów trafiają do `apps.proctoring.webhooks`, gdy etap ma nadzór),
interfejs pokoju – `webinar-room.js` i SDK z `static/vendor/`. Kod dostawcy: `apps.proctoring.stage_rooms`,
widoki: `apps.web.views.stage_rooms` (adresy w `urls_proctoring.py`, pod istniejącymi segmentami
`me`, `coordinator`, `review`). Limit: scope `video` per konto (GET i POST).

## 4. LiveKit + nadzór zdalny

Przy fladze konkursu `proctoring` koordynator może włączyć nadzór także dla etapu-rozmowy **w LiveKit**
(`services.proctorable`; rozmowy na Jitsi – nie, pokój nie jest na naszym serwerze):

- uczeń dostaje token rozmowy dopiero ze zgodą na nadzór, sprawdzonym sprzętem (i zdjęciem dokumentu,
  gdy wymagane) albo z zatwierdzoną alternatywą (`interview_ready`); próba sprzętu – bez warunku,
- konsola nadzoru dla rozmowy kończy się krokiem „rozmowa” (osobnego nadawania nie ma – pokój rozmowy
  **jest** obrazem); karta na pulpicie mówi, co zrobić,
- webhooki pokoju rozmowy (połączenie, kamera) trafiają do dziennika sesji ucznia; przy `record`
  kamera ucznia jest nagrywana (Track Egress) z retencją, dostępem i audytem PROC-01,
- incydenty, obecność i raport – jak w PROC-01 (siatka nadzoru służy tu do notatek).

Bez flagi `proctoring` konfiguracja nadzoru etapu jest ignorowana (zero zapytań).

## 5. Testy (`apps/proctoring/tests/test_stage_rooms.py`)

Ta sama macierz ról uruchamiana na etapie Jitsi i etapie LiveKit (uczestnik, próba, przed oknem,
zdyskwalifikowany, bez zapisu, uczestnik na adresie koordynatora, koordynator, próba koordynatora,
komisja, zawieszona komisja, niezalogowany) z porównaniem wyniku i ważności biletu; wybór dostawcy;
opcja w formularzu; izolacja konkursów; polecenia moderatora i odmowa dla uczestnika; połączenie
z nadzorem; flaga wyłączona; webhooki i nagranie. Testy Jitsi (`test_video_*`, `test_jitsi_jwt`,
`test_interviews`) – bez zmian.
