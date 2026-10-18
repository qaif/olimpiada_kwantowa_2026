# CAL-01: Publiczny kalendarz ICS – warsztaty i terminy edycji

Zgłoszenie organizatora z 18.10.2026 (pkt 9): „kalendarz do zasubskrybowania z warsztatami
i terminami”. Dotychczasowy plik `/me/calendar.ics` wymaga zalogowania, więc Google Calendar,
Outlook i Apple Calendar **nie umieją go subskrybować** (ich serwery nie mają sesji uczestnika).

## 0. Cel i granice

- Publiczny kanał `GET /kalendarz.ics` – osobny dla każdego konkursu (konkurs z żądania, cudzego nie ma
  jak zobaczyć), bez logowania, do subskrypcji i do jednorazowego pobrania.
- Przyciski „Dodaj do Google Calendar”, „Dodaj do Outlook / Apple” (`webcal://`), „Pobierz .ics” na
  stronie `/warsztaty/` i na ekranie kalendarza w panelu uczestnika (`/me/calendar/`); przy każdym
  warsztacie z datą – odnośnik „dodaj ten warsztat” do Google Calendar (szablon wydarzenia).
- Czego zadanie **nie** robi: nie zmienia `/me/calendar.ics` (zostaje prywatnym plikiem z własnym
  terminem rozmowy i oknem czasowym), nie zmienia harmonogramu w CMS-ie (żadnego nowego pola), nie
  dodaje zależności (iCalendar składamy ręcznie, jak `apps.cms.calendar`; `icalendar` nie jest w deps).
- **Bez linku Meet / pokoju wideo.** Link do spotkania jest tylko dla zalogowanych (ogłoszenia
  w Wiadomościach); kanał publiczny bywa synchronizowany do cudzych usług i przesyłany dalej.

## 1. Treść kanału

Nowa aplikacja `apps.calendar_feed` (bez modeli i migracji). Źródła – te same, co pasek linii czasu,
żeby kalendarz nie ogłaszał innych terminów niż strona:

| Wydarzenie | Źródło | Postać | `UID` |
|---|---|---|---|
| „Warsztaty: <temat>” | blok `schedule` strony `/warsztaty/` **tego konkursu** (`workshops_page`), wiersze z `date_value` | godziny z pola „godziny” (`11:00–14:00`, `11.00-14.00`, sama godzina startu = 90 min); bez godzin albo „do potwierdzenia” → całodniowe | `workshop-c<id konkursu>-<workshop_key>@<domena>` |
| „<etap>” (dni wydarzenia) | `Stage.event_starts_on/ends_on` (etap stacjonarny) | całodniowe, `LOCATION` = miejsce | `stage-<id>-event@…` |
| „<etap>: otwarcie” / „<etap>: termin oddania prac” (forma „rozmowa”: „zamknięcie etapu”) | `Stage.opens_at`, `Stage.deadline_at` (etapy bez dni wydarzenia, bez treningowego) | moment (`DTSTART` bez `DTEND`) | `stage-<id>-opens@…`, `stage-<id>-deadline@…` |
| „Rejestracja uczestników: start / koniec” | `Edition.registration_opens_at/closes_at` (gdy rejestracja włączona) | moment | `edition-<id>-registration-opens@…`, `…-closes@…` |
| wydarzenia koordynatora | `EditionEvent` z `show_on_timeline` | całodniowe | `event-<id>@…` |

- `DESCRIPTION` warsztatu: prowadzący (jeśli wpisany), „online”, zdanie „link do spotkania podajemy
  zalogowanym uczestnikom w Wiadomościach” i bezwzględny adres `/warsztaty/` (także w `URL`).
- Webinary LiveKit (`apps.webinars`) **nie** wchodzą: mają grupę odbiorców (edycja/etap), a nie są
  ogłoszeniem publicznym. Rozszerzenie – § 4.
- Terminy etapów pochodzą wyłącznie z bieżącej edycji konkursu (`current_edition`). Warsztaty –
  wszystkie wiersze z datą (także minione: kalendarz subskrybowany nie powinien ich usuwać po fakcie).
- Napisy w języku domyślnym konkursu (`Competition.default_language`), nie w języku żądania: serwer
  kalendarza nie ma preferencji językowych, a treść subskrypcji ma być stała.

## 2. Format (RFC 5545)

- `VCALENDAR` z `VERSION`, `PRODID`, `CALSCALE`, `METHOD:PUBLISH`, `X-WR-CALNAME` (marka konkursu jak
  w `apps.cms.calendar`), `X-WR-TIMEZONE`, `REFRESH-INTERVAL;VALUE=DURATION:PT6H` i
  `X-PUBLISHED-TTL:PT6H` (podpowiedź częstotliwości odświeżania).
- Czas: `DTSTART;TZID=<strefa konkursu>:YYYYMMDDTHHMMSS` + **`VTIMEZONE`** wygenerowany z bazy IANA
  (`zoneinfo`) dla lat, w których są wydarzenia – jawne przejścia `STANDARD`/`DAYLIGHT` z
  `TZOFFSETFROM/TO` i `TZNAME`; strefa bez zmian czasu → jedno `STANDARD`. Domyślnie Europe/Warsaw
  (CET/CEST); poprawne po obu stronach zmiany czasu.
- Całodniowe: `DTSTART;VALUE=DATE` + wyłączny `DTEND;VALUE=DATE` (dzień po ostatnim).
- `UID` stabilny (tabela wyżej) – klucz warsztatu (`workshop_key`: data + temat) albo klucz główny,
  nigdy pozycja wiersza. Zmiana tematu albo daty warsztatu = nowe wydarzenie (stare znika z kanału).
- `LAST-MODIFIED` i `SEQUENCE` dla warsztatów: chwila ostatniej publikacji strony `/warsztaty/`;
  `SEQUENCE` = sekundy od 1.01.2026 do tej chwili (rośnie przy każdej publikacji, bez nowej tabeli).
  Etapy i wydarzenia nie mają znacznika zmiany w bazie → bez `LAST-MODIFIED`, `SEQUENCE:0`.
- `DTSTAMP` = chwila złożenia pliku. Wiersze CRLF, zawijanie do 75 **oktetów** UTF-8 (bez cięcia
  znaku), escapowanie `\`, `;`, `,` i nowych linii – wspólne funkcje z `apps.cms.calendar`.

## 3. HTTP, pamięć podręczna, unieważnianie

- `Content-Type: text/calendar; charset=utf-8`, `Content-Disposition: inline; filename="<slug>.ics"`,
  `Cache-Control: public, max-age=900`, `ETag` (skrót treści bez `DTSTAMP`) i `304` na
  `If-None-Match`. Brak konkursu w żądaniu → 404. Konkurs bez edycji i bez warsztatów → poprawny,
  pusty kalendarz (200).
- **Pamięć stron (`apps.web.page_cache`) – nie na allow-liście.** Ta warstwa przyjmuje wyłącznie
  `text/html` i istnieje po to, żeby bezpiecznie podmieniać nonce CSP i token CSRF – w pliku ICS nie
  ma ani jednego, a kanał kosztuje kilka zapytań. Zamiast tego kanał ma własny wpis w Redisie
  (15 min) z **tymi samymi licznikami wersji** co pamięć stron (wydanie, wersja globalna, wersja
  witryny konkursu): publikacja/wycofanie strony `/warsztaty/`, zapis edycji, etapu i wydarzenia
  koordynatora już dziś podbijają wersję witryny, więc zmiana harmonogramu unieważnia kanał bez
  żadnego nowego sygnału. Wpis działa tylko przy włączonej pamięci stron (`PAGE_CACHE_ENABLED`).
- Strona `/warsztaty/` z przyciskami nadal leży w pamięci stron: przyciski są identyczne dla każdego
  gościa (adres z hosta żądania, bez danych osobowych).

## 4. Prywatny kanał uczestnika z linkiem Meet – rozszerzenie (nie w tym zadaniu)

`/me/kalendarz/<token>.ics` z linkiem z bieżącego ogłoszenia **nie** powstaje teraz:

- link Meet jest dziś wolnym tekstem w treści ogłoszenia (`chat.OrganizerAnnouncement.body`), bez
  związku z konkretnym warsztatem – kanał musiałby zgadywać, który adres należy do których zajęć,
- token w adresie subskrypcji jest poświadczeniem, które kalendarz uczestnika synchronizuje do
  Google/Apple i udostępnia razem z całym kalendarzem; link Meet przestałby być „tylko dla zalogowanych”.

Minimalny projekt na później: pole „link do spotkania” w wierszu harmonogramu widoczne tylko w widokach
dla zalogowanych; model `CalendarToken(participant, token_hash, created_at, revoked_at)` (token
32 bajty, w bazie skrót), przycisk „Unieważnij i wygeneruj nowy” w `/me/calendar/`, treść bez danych
osobowych, rejestr czynności RODO i eksport/usunięcie konta.

## 5. Testy

`apps/calendar_feed/tests/`: parser w teście (rozwinięcie wierszy, pary BEGIN/END, wymagane pola),
zawijanie ≤ 75 oktetów z polskimi znakami, escapowanie, `VTIMEZONE` i godziny po obu stronach zmiany
czasu (październik/marzec), parsowanie godzin, stabilne `UID`, brak linku Meet/pokoju, izolacja dwóch
konkursów (warsztaty i etapy), nagłówki, ETag/304, unieważnienie wpisu po publikacji strony.
