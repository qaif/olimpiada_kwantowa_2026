# WEB-01: Webinary w LiveKit z panelu koordynatora

## 0. Cel i granice

Koordynator planuje i prowadzi **webinary** (wykład, konsultacje przed etapem, omówienie zadań),
a uczestnicy, komisja i (opcjonalnie) goście wchodzą do **pokoju na platformie** – z naszym
interfejsem, w motywie konkursu, w 11 językach (RTL). Wideo przenosi **LiveKit** (Apache 2.0,
serwer własny): platforma wystawia krótko żyjące tokeny dostępu, wydaje polecenia serwerowe
(„daj głos”, nagrywanie, zamknięcie pokoju) i przyjmuje podpisane webhooki (stan pokoju, obecność,
koniec nagrania). Wcześniejszy wariant z BigBlueButton (BBB-01) zastąpiła decyzja organizatora
z 4.10.2026 – kod BBB nie wszedł do repozytorium.

Czego zadanie **nie** robi:
- nie zmienia pokoi Jitsi (`apps.competitions.video`, `video_rooms`, `jitsi_jwt`) ani rozmów
  kwalifikacyjnych – webinar to osobna funkcja (§ 1),
- nie osadza cudzej strony w `<iframe>` – pokój to nasza strona; CSP dostaje wyłącznie origin
  LiveKit w `connect-src` (§ 7),
- nie tłumaczy ekranów koordynatora (zakres I18N-01); tłumaczone są strona odbiorców, pokój,
  link gościa i listy (10 katalogów),
- faza 2 (poza zakresem): pytania i odpowiedzi (Q&A) z głosowaniem, ankiety, sala oczekiwania,
  napisy na żywo, zaświadczenia o udziale z listy obecności.

## 1. Dlaczego nowy model, a nie dostawca w `VideoRoom`

`VideoRoom` (Jitsi) to pokój **bez terminu** z parą linków-zaproszeń. Webinar ma termin, czas
trwania, **odbiorców** (grupy konkursu), role w pokoju nadawane tokenem, listę obecności z webhooków
i nagrania w naszym magazynie. Wspólne zasady bierzemy z modułów Jitsi wprost: `encode_hs256`
i `short_name` (`jitsi_jwt`), `clean_display_name` i `committee_member` (`video_rooms`), odpowiedzi
`no-store` + `Referrer-Policy`. Nowa aplikacja **`apps.webinars`** (modele, `livekit.py`, serwisy,
magazyn, listy, zadania), widoki `apps/web/views/webinars.py`, adresy `apps/web/urls_webinars.py`.

## 2. Konfiguracja instalacji i flaga konkursu

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `LIVEKIT_URL` | pusty = wyłączone | sygnalizacja dla przeglądarki, **`wss://`** (`ws://` tylko przy `DEBUG`) |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | puste = wyłączone | para kluczy z `keys:` w `livekit.yaml` |
| `LIVEKIT_API_URL` | pusty = `https` z `LIVEKIT_URL` | API serwera z sieci compose (`http://livekit:7880`) |
| `LIVEKIT_TOKEN_TTL_SECONDS` | 600 | ważność tokenu wejścia |
| `LIVEKIT_TIMEOUT_SECONDS` | 8 | limit czasu poleceń serwerowych |
| `LIVEKIT_RECORDINGS_BUCKET` | pusty = `S3_SUBMISSIONS_BUCKET` | bucket nagrań (prefiks `webinars/`) |
| `WEBINAR_JOIN_LEAD_MINUTES` / `GRACE` | 15 / 30 | okno wejścia odbiorcy |
| `WEBINAR_REMINDER_MINUTES` | 60 | przypomnienie e-mailem przed startem |
| `LIVEKIT_PROXY` | 0 | `render_caddyfile.sh` dokłada blok `live.<domena>` (wariant na tym hoście) |

- Flaga konkursu **`webinars`** (`FEATURE_DEFAULTS`, domyślnie `False`, ustawia operator).
  Wyłączona: adresy 404, menu i panele bez zmian.
- Flaga bez LiveKit: pozycja „Komunikacja → Webinary” jest, ekran mówi „serwer LiveKit nie jest
  skonfigurowany” (bez formularza); odbiorcy, pokój, gość, webhook – 404.
- Sekret API nigdy nie trafia do szablonu, JS, logu ani audytu.

## 3. Model danych (`apps/webinars/models.py`)

- `Webinar` (`competition_scoped_manager`): tytuł, opis, `starts_at`, `duration_minutes` (15–480);
  odbiorcy `audience` (`competition`, `edition`, `stage` + `stage`, `committee`, `captains` – tylko
  przy `team_entries`), `include_committee`; `public_link` (domyślnie **False**) + `public_key`
  (192 bity); `record` (nagrywanie dozwolone), `email_reminder`; `co_moderators` (koordynatorzy
  i aktywna komisja); `room_key` (losowy; pokój `olimp-<slug>-<room_key>`); stan: `started_at`,
  `ended_at`, `cancelled_at`, `announced_at`, `reminder_sent_at`, `live_started_at` /
  `live_finished_at` (z webhooków), `stream_egress_id`.
- `WebinarAttendee`: (webinar, `identity`) unikalne; konto (lub gość), nazwa, rola, pierwszy token,
  pierwsze/ostatnie wejście, wyjście, sekundy obecności – **lista obecności** (pod przyszłe
  zaświadczenia).
- `WebinarRecording`: `egress_id`, stan (`active`/`complete`/`failed`), klucz pliku, rozmiar, długość,
  publikacja.
- `WebinarWebhookEvent`: identyfikatory przetworzonych zdarzeń (powtórki), sprzątane po 7 dniach.
- `WebinarNotificationSettings`: `email_on_webinar` (brak wiersza = tak).

## 4. Role, tokeny, okno (serwis `apps.webinars.services`)

- **Prowadzący** (`presenter`): koordynator konkursu, autor, współprowadzący z aktywną rolą
  (sprawdzane przy każdym tokenie). Token: `canPublish`, `canPublishData`, `canSubscribe`,
  `roomAdmin`. Pobranie tokenu przez prowadzącego **rozpoczyna** webinar.
- **Widz** (`viewer`): osoba z grupy odbiorców; gość z linku. Token: `canSubscribe`,
  `canPublishData` (czat, ręka), **`canPublish=false`**. Głos daje prowadzący: `POST
  /webinars/<id>/control/` → `RoomService/UpdateParticipant` (uprawnienia) – tylko dla identyfikatora,
  który dostał token do **tego** webinaru.
- `identity` = pseudonim HMAC konta (`u-…`) albo gościa z sesji (`g-…`); bez e-maila i `pk`.
  `name` = „Imię N.”. TTL tokenu 10 min (LiveKit sam utrzymuje sesję).
- Okno widza: od `starts_at − LEAD` do końca + `GRACE`, po „Rozpocznij”, przed „Zakończ”. Poza
  oknem – zdanie dla człowieka (JSON `detail`, pokazywany w pokoju), nie błąd.
- Brak prawa do webinaru albo webinar innego konkursu – **404**; brak tokenu dla nie-odbiorców.
- Limity: `webinar_join` 60/h per konto (token, polecenia prowadzącego, czynności panelu),
  `webinar_guest` 120/h per IP (formularz i token gościa).

## 5. Ekrany

**Koordynator** (`/coordinator/webinars/…`): lista + formularz; szczegóły (stan, liczba osób w pokoju,
„Rozpocznij i wejdź do pokoju”, „Zakończ” – `DeleteRoom`, odwołanie, zaproszenie e-mailem raz,
link gościa, **lista obecności**, nagrania: nagrywaj/zatrzymaj, publikuj/wycofaj/usuń z
potwierdzeniem, transmisja RTMP); edycja.

**Odbiorcy** (`/webinars/`): nadchodzące (godzina w strefie konkursu + czas lokalny przeglądarki),
„Dołącz” → pokój, nagrania opublikowane (`/webinars/<id>/recordings/<pk>/` → 302 na adres podpisany
na 2 h), przełącznik listów. Pozycja w pasku `/me/` i karta w panelu recenzenta/komisji – przy fladze
i konfiguracji.

**Pokój** (`/webinars/<id>/room/`, gość: `/zaproszenie/webinar/<klucz>/room/`): siatka / widok
prelegenta, udostępnianie ekranu, mikrofon/kamera (gdy wolno nadawać), lista uczestników
(prowadzący: daj/odbierz głos, usuń), podniesiona ręka i czat po kanale danych LiveKit, nagrywanie
(prowadzący). Token pobiera JS `POST`-em – w HTML-u nie ma poświadczeń. SDK `livekit-client` (UMD)
z `static/vendor/livekit-client/` (przypięta wersja, licencja, `VERSION` z sumą npm, `SHA384`;
skrypt `scripts/vendor_livekit_client.sh`) – **bez CDN**; brak pliku = komunikat zamiast błędu 500.

**Gość** (`/zaproszenie/webinar/<klucz>/`): tytuł, termin, pole nazwy; POST zapisuje pseudonim
i nazwę w sesji i prowadzi do pokoju gościa.

## 6. Webhooki, nagrania, transmisja

- `POST /integrations/livekit/webhook/` (CSRF wyłączony, zamiast niego podpis): JWT w
  `Authorization` – HS256 sekretem API, `iss` = klucz, claim `sha256` = base64(SHA-256 treści),
  `exp`/`nbf`. Bez podpisu, z innym kluczem, przeterminowany, z inną treścią albo z `alg: none` –
  401. Powtórki: unikalne `id` zdarzenia (drugie = `duplicate`), zdarzenie starsze niż 15 min –
  `stale`. Obsługa: `room_started`/`room_finished` (stan), `participant_joined`/`left` (obecność),
  `egress_ended` (nagranie gotowe/błąd, rozmiar, długość; klucz pliku tylko w prefiksie webinaru).
- Nagrywanie: `Egress/StartRoomCompositeEgress` (layout `speaker`, MP4) z **samą ścieżką**
  `webinars/<konkurs>/<pokój>/<czas>.mp4`; poświadczenia magazynu zna tylko egress (osobne konto
  MinIO z zapisem wyłącznie do `webinars/`, `deploy/livekit/policy-egress.json`). Odtwarzanie:
  adres podpisany kontem prywatnym platformy na 2 h, po sprawdzeniu odbiorcy i publikacji.
- Transmisja RTMP (YouTube): koordynator wkleja klucz albo adres `rtmp(s)://`. **Klucza nie
  przechowujemy** – idzie wyłącznie w żądaniu do egress (klucz zapisany byłby kolejnym sekretem do
  szyfrowania i rotacji, a koordynator ma go w panelu YouTube); audyt ma tylko host docelowy.

## 7. Bezpieczeństwo i CSP

- Izolacja konkursów: zawężone querysety (404), rola przy każdym tokenie i poleceniu.
- Audyt: `webinar.created`, `updated`, `cancelled`, `started`, `ended`, `joined` (rola),
  `announced`, `speaker_granted`/`revoked`, `participant_removed`, `recording_started`/`stopped`/
  `published`/`unpublished`/`deleted`, `stream_started`/`stopped` – bez tokenów, kluczy i nazw gości.
- CSP: przy skonfigurowanym LiveKit `connect-src` dostaje `wss://host` (sygnalizacja) i
  `https://host` (`/rtc/validate`); `media-src`/`worker-src`/`frame-src` bez zmian (media przez
  WebRTC i `srcObject`, bez E2EE, bez ramek). Bez LiveKit – polityka bez zmian (test).
- Nowe pierwsze segmenty `webinars` i `integrations`: `RESERVED_SLUGS`, kontrakt tras djcms
  (`manage.py djcms_routes --write`), `robots.txt` djcms (`webinars` w `PRIVATE_PREFIXES`).

## 8. Infrastruktura (`deploy/livekit/`, opis: `docs/OPERACJE.md` § 28)

- (a) **osobna maszyna** – zalecane przy dużych wydarzeniach (VPS produkcyjny traci 12–37 % CPU
  na „steal”); portal dostaje tylko `LIVEKIT_URL` i klucze;
- (b) **ten sam host** – małe spotkania: nakładka `deploy/livekit/docker-compose.livekit.yml`
  (profil `livekit`: `livekit`, `livekit-egress`, `livekit-redis`), `LIVEKIT_PROXY=1` (blok Caddy
  `live.<domena>`), zapora: UDP 50000–50100, TCP 7881; TURN/TLS – opis.

## 9. Testy (`apps/webinars/tests/`, bez sieci)

Fałszywy serwer LiveKit (Twirp; weryfikacja tokenu serwerowego PyJWT i wymaganego uprawnienia),
magazyn w pamięci. Pokrycie: claimy i uprawnienia tokenów dla ról; konfiguracja; API serwera
(UpdateParticipant, RemoveParticipant, DeleteRoom, Egress start/stop); podpis webhooka
(poprawny / brak / inny sekret / inny klucz / przeterminowany / inna treść / `alg: none`),
powtórki i stare zdarzenia; reguły odbiorców; izolacja; flaga i konfiguracja; daj głos; nagrania
(egress + webhook + publikacja + adres podpisany + usunięcie, prefiks); RTMP bez zapisu klucza;
lista obecności; CSP; listy; przekłady w 10 katalogach.

## 10. Definicja ukończenia

ruff format + check czyste; `makemigrations --check` czyste; testy zmienionych aplikacji zielone;
`msgfmt --check` dla 10 katalogów; kontrakt tras djcms aktualny.
