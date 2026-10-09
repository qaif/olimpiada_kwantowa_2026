# Historia zmian

Jedna sekcja na wydanie (od v0.34.0), najnowsze na górze; starsze wydania – po jednej linii w tabeli
„Wydania” na końcu (treść z opisu commitu oznaczonego tagiem, `git log --tags`). Numeracja jest
`v<major>.<minor>.<patch>`, a tag wydania jest zarazem wartością `APP_VERSION` wpisywaną przez
`scripts/deploy.sh`, więc numer widoczny w stopce serwisu i na `/status/` odpowiada dokładnie jednej
sekcji albo jednemu wierszowi tabeli.

Nowa zmiana trafia na górę jako `## [Unreleased] – Poczta przychodząca: postmaster@, abuse@ i zdalne odbicia (MAIL-03)

- Relay `mail` przyjmuje pocztę na porcie 25 (prośba organizatora z 9.10.2026 – potwierdzenie IP
  w Microsoft SNDS/JMRP po blokadzie `S3150`): wyłącznie postmaster@ i abuse@ domen nadawcy,
  przekierowane na `MAIL_INBOUND_FORWARD`, oraz zdalne odbicia na `noreply@` (skrzynka MAIL-02).
  Usługa portu 25 bez relaya (`reject_unauth_destination`, własne `mynetworks`), port domyślnie tylko
  na `127.0.0.1` (`MAIL_INBOUND_BIND`). Skrypt `deploy/mail/docker-init.d/60-inbound.sh`, test
  `scripts/tests/mail_inbound_test.sh` (w CI razem z `mail_bounces_test.sh`), OPERACJE § 49.9.

## [Unreleased] – <tytuł>`; przy tagowaniu blok staje się
podsekcją `### <tytuł>` wydania `## v<x.y.z> – <data tagu> – <opis tagu>`.

Pełny opis każdej funkcji: [`../README.md`](../README.md). Stan prac i dług techniczny:
[`BACKLOG.md`](BACKLOG.md).

## [Unreleased] – Szybsze CI: grupy xdist, jedna migracja na shard, czasy z CI (CI-SPEED-01)

- **Testy migracji przewijały bazę raz na test, a nie raz na moduł** (prośba organizatora z 8.10.2026
  „testy trwają za długo”): `conftest.py` przełączał `-n` na `--dist loadgroup` tylko w procesie
  sterującym, workery xdist o tym nie wiedziały i nie grupowały testów – moduł migracji rozjeżdżał się
  po workerach, a każdy jego test płacił 1,5–3 min za przewinięcie. Decyzja idzie teraz do workerów
  (`pytest_configure_node`), a pytest-split widzi czasy testów migracji także pod identyfikatorem z grupą.
- **Bazy testowe w CI:** migracje raz na shard (`backend/ci_test_db.py`) i kopie dla workerów
  (`CREATE DATABASE … TEMPLATE`, `--reuse-db`) zamiast czterech migracji naraz w każdym shardzie.
- **Podział na shardy:** `backend/.test_durations` przeliczony do proporcji CI (ok. 40 % testów nie
  miało w nim czasu); każdy shard wystawia artefakt `test-durations-<n>`, a
  `scripts/refresh_test_durations.py <run-id>` składa z nich nowy plik (docs/TESTY.md § 5).
- `test_docs_section_refs` 30–40 s → ok. 1 s (pliki i wiersze bez „§” pomijane, ten sam wynik).
- **Losowy test usunięty u źródła:** długość przepustki Jitsi liczona od `nbf` zamiast od `iat`
  (`test_video_join.py`, `test_video_rooms.py`) – na granicy sekundy wychodziło 1799 zamiast 1800.
- **Usunięte duplikaty (3 testy, 8 przypadków):** w `cms/tests/test_djcms_api_v2.py` dwa testy
  identyczne co do adresu, danych i asercji z testami w `test_djcms_api.py`; w
  `themes/tests/test_review_fixes.py` test zawarty w `test_install.py::test_delete_unused_version_removes_files`.

## [Unreleased] – Komunikaty z datą przyszłą (MSG-SCHED-01)

- **„Wyślij później”** na `/coordinator/messages/` (prośba organizatora z 8.10.2026): komunikat da się
  zaplanować na datę i godzinę (czas polski, 5 min – 90 dni naprzód). Rejestr trzyma grupę i jej
  parametry (nowe pole `parameters`), termin (`scheduled_for`) i stan `SCHEDULED` – **bez adresów**;
  odbiorców liczy zadanie beat `dispatch-scheduled-broadcasts` (co minutę) **w chwili wysyłki**, tą samą
  drogą co wysyłka natychmiastowa (bieżąca edycja z chwili wysyłki). Dokładnie raz:
  `select_for_update(skip_locked=True)` + warunek stanu. Nowe stany: zaplanowana, anulowana,
  przeterminowana (> 24 h po terminie – nie wysyłamy), bez odbiorców. Sekcja „Zaplanowane” z przyciskiem
  „Anuluj” (POST, tylko koordynator konkursu, cudzy komunikat → 404), kolumna „Termin” w historii, podgląd
  z liczbą „na teraz” i dopiskiem o ponownym liczeniu; termin wchodzi do podpisu podglądu. Wklejonej listy
  adresów nie da się zaplanować. Serwis `schedule_broadcast(...)` do użycia także z `manage.py shell`
  (OPERACJE § 53). Audyt: `broadcast.scheduled`, `broadcast.cancelled`, `broadcast.sent` (aktor = autor
  planu), `broadcast.expired`, `broadcast.empty`. Migracja `accounts.0041`. Specyfikacja:
  `docs/tasks/MSG-SCHED-01.md`.

## [Unreleased] – Ogłoszenia organizatora w Wiadomościach (CZ-ANN-01)

- **Blok „Ogłoszenia organizatora”** nad listą rozmów w `/me/messages/` i na pulpicie `/me/` (prośba
  organizatora z 8.10.2026: link do warsztatów „na wszystkich kontach w sekcji wiadomości, także tych, co
  dopiero się zarejestrują”). Nowy model `chat.OrganizerAnnouncement` (migracja `chat.0003`) – jawny wpis
  konkursu czytany w chwili wyświetlenia, a nie wiadomość w rozmowie, więc widzi go każdy uczestnik, także
  zarejestrowany po publikacji, niezależnie od rozmów między uczestnikami; pulpit pokazuje go także przy
  wyłączonych Wiadomościach. Treść jako zwykły tekst (escape + klikalne adresy, `rel="nofollow noopener
  noreferrer"`).
- **Panel koordynatora** `/coordinator/inbox-announcements/` (menu „Komunikacja → Ogłoszenia w
  Wiadomościach”): dodaj (szkic albo „Zapisz i opublikuj”), edytuj, „Opublikuj / Wyłącz”, usuń, opcjonalne
  okno „widoczne od/do”; tylko koordynator konkursu, cudze ogłoszenie 404, audyt `chat.announcement.*`.
- Serwis `apps.chat.announcements.publish_announcement(*, competition, title, body, actor)` do publikacji
  z `manage.py shell`. Budżet zapytań `/me/` +1 (jedno zapytanie po indeksie). Bez nowych danych osobowych
  (brak odczytów per konto) – bez wpisu w rejestrze czynności. Specyfikacja: `docs/tasks/CZ-ANN-01.md`.

## [Unreleased] – Komunikaty: grupa „uczestnicy bez zaświadczenia o statusie ucznia”

- Nowa grupa odbiorców na `/coordinator/messages/` (prośba organizatora z 8.10.2026): uczestnicy bieżącej
  edycji bez zaświadczenia o statusie ucznia – brak pliku albo plik odrzucony (plik czekający na weryfikację
  i zaakceptowany zwalniają). Ta sama lista osób, co na ekranie zaświadczeń; migracja `accounts.0040`
  zmienia wyłącznie listę wyboru.

## [Unreleased] – Komunikaty: eksport odbiorców do Excela (MSG-EXPORT-01)

- **„Eksportuj do Excela”** na `/coordinator/messages/` (prośba organizatora z 8.10.2026): plik `.xlsx`
  z kolumnami Imię, Nazwisko, E-mail dla aktualnie wybranej grupy z jej parametrami – np. do zaproszenia
  na warsztaty spoza platformy. Odbiorców liczy ta sama ścieżka co wysyłkę (`recipient_users` w
  `apps/accounts/messaging.py`: zakres konkursu i edycji, tylko konta aktywne z potwierdzonym adresem,
  jeden wiersz na adres). Tylko koordynator; walidacja tym samym formularzem co podgląd; wklejona lista
  adresów bez eksportu; audyt `export.generated` (grupa, parametr, liczba wierszy – bez danych);
  ochrona przed formułami w komórkach. Bez migracji. Specyfikacja: `docs/tasks/MSG-EXPORT-01.md`.

## [Unreleased] – Walidacja Caddyfile w nowym obrazie bez sieci compose'a (DEP-02a)

- **Poprawka po przerwanym wdrożeniu v0.48.5:** `scripts/proxy_config.sh render` przy zmianie obrazu
  proxy walidował plik przez `docker compose run proxy`, który nie wstawał – stałe `ipv4_address` proxy
  zajmuje działające proxy („Address already in use”), a komunikat mówił o złej konfiguracji. Teraz
  `docker run --rm --network none` z plikiem tylko do odczytu i zmiennymi proxy z `docker compose config`;
  „walidacja nie wystartowała” (błąd Dockera) jest odróżniona od „caddy validate odrzucił” (błąd
  Caddy'ego), oba z wypisanym błędem. Produkcja bez zmian – wdrożenie zatrzymało się przed czymkolwiek.

## [Unreleased] – Obrazy usług: Caddy 2.10, ClamAV 1.5 (DEP-02)

- **Obrazy compose (Dependabot `compose-images`):** `caddy` 2.8 → 2.10, `clamav/clamav` 1.4 → 1.5,
  `louislam/uptime-kuma` 1.23.16 → 1.23.17 (łatka bezpieczeństwa), `axllent/mailpit` v1.24 → v1.30 (dev);
  loadtest na tych samych wersjach Caddy'ego i ClamAV.
- **Caddy 2.10 a certyfikaty:** 2.10 pomija certyfikaty nazw pokrytych przez blok `*.<domena>` – przy
  `PLATFORM_SUBDOMAINS=1` `www.`, `meet.`, `monitor.`, `dj.`, `live.`, `lab.`, `errors.` zostałyby bez
  certyfikatu. Generator dopisuje im `tls force_automate` (wymaga 2.10; powrót do 2.8 = cofnięcie
  całego PR-u). Wieloznacznego certyfikatu (DNS-01) Caddy nie próbuje – on-demand dalej per host.
- **`scripts/proxy_config.sh render`:** przy zmianie obrazu proxy walidacja w jednorazowym kontenerze
  nowego obrazu zamiast w działającym starym (inaczej krok 4/8 odrzuciłby składnię 2.10).
- Test `scripts/security/tests` czyta przypięty tag z compose zamiast `caddy:2.8` na sztywno.
  Lista kontrolna po wdrożeniu: `docs/OPERACJE.md` § 47.8.

## [Unreleased] – Aktualizacje zależności z Dependabota (DEP-01)

- **Wchodzą z następnym wdrożeniem (obraz web i djcms):** gunicorn 23 → 26.2 (backend i djcms) z flagą
  `--no-control-socket` w poleceniach (docker-compose.yml, oba Dockerfile'e, loadtest) – od 25.1 gunicorn
  zakłada gniazdo sterujące w `~/.gunicorn/`, co w kontenerze `read_only` dawało ERROR przy każdym
  starcie; psycopg 3.2 → 3.3 (backend i djcms); redis-py 5.x → 6.4 (granica kombu 5.6: `<6.5`);
  uvicorn do 0.54 (zainstalowany, nieużywany – serwis chodzi pod WSGI/gthread).
- **CI:** actions/setup-python 7, actions/cache 6, docker/build-push-action 7, docker/setup-buildx-action 4,
  docker/login-action 4 (wszystkie przypięte SHA, Node 24).
- **Nie weszły:** numpy 2.5 (linia Pyodide w notebook-lab), Uptime Kuma 2 (migracja danych), grupa obrazów
  compose (Playwright musi iść z `e2e/requirements.txt`; Caddy 2.10 i ClamAV 1.5 czekają na decyzję
  operatora). Reguły `ignore` w `.github/dependabot.yml`, `docs/OPERACJE.md` § 47.6.

## [Unreleased] – Literówki w adresach e-mail i odbicia poczty (MAIL-02)

- **„Czy chodziło Ci o …?”** w polach adresu (rejestracja uczestnika, komitetu i opiekuna szkolnego,
  zmiana adresu, konto w panelu koordynatora, zaproszenie opiekuna drużyny, uczeń i rodzic dodawani
  przez opiekuna drużyny): literówka w domenie (`gmial.com`, `o2.plo`, `.con`, `.pll` …; ~85 dostawców
  z Polski i świata, IDN, plus-adresy) – podpowiedź po opuszczeniu pola (skrypt statyczny z nonce, bez
  inline JS) i jedno pytanie po wysłaniu, z polem „Użyj adresu …”; ponowne wysłanie zostawia adres.
- **Twarda blokada** domen bez MX i A (oraz „null MX”) – pytanie DNS z `web` (klient z MAIL-01, 1,5 s,
  cache, najwyżej 4 naraz), **dopiero po** całej pozostałej walidacji (CAPTCHA, antyspam, hasła); każdy
  błąd DNS przepuszcza adres. Wyłącznik `EMAIL_DOMAIN_DNS_CHECK`.
- **Odbicia poczty**: odmowy relaya zapisywane w chwili wysyłki (twarde wg tablicy kodów – bez trzech
  ponowień; polityka/konfiguracja relaya, np. `554 5.7.1`, dalej jako błąd z ponowieniami, bez zapisu
  przy adresie), zawiadomienia o niedoręczeniu (DSN) – `MAIL_BOUNCE_TARGET=capture`
  (nowa domyślna w compose): agent `virtual` Postfiksa → Maildir na wolumenie `mail_bounces` → worker
  co 5 min (parser RFC 3464 – tylko części najwyższego poziomu i `Reporting-MTA` naszego relaya; relay
  odrzuca pusty nadawcę od klientów SMTP, więc DSN-a nie da się podrzucić z sieci compose; pliki kasowane
  po przetworzeniu). Klasyfikacja wyłącznie po kodzie rozszerzonym (5.0.350 Microsoftu nie jest twarde). Nowa aplikacja `apps.email_delivery`, model `DeliveryStatus` (klucz = adres).
- **Baner** „Nie możemy dostarczyć poczty na adres …” z „Zmień adres” i „Mój adres jest poprawny”;
  **lista koordynatora** „Adresy niedoręczalne” (Raporty, CSV, „Oznacz jako doręczalny”, audyt);
  **wstrzymanie listów nieobowiązkowych** (forum, czat, webinary, absolwenci, komunikaty grupowe);
  reset przy zmianie adresu, potwierdzeniu i usunięciu konta.
- RODO: rejestr czynności 1.22 (wiersz warunkowy), sekcja eksportu `doreczalnosc_poczty`, retencja
  365 dni. Tłumaczenia w 10 językach (`apps/email_delivery/locale`). Przełącznik `EMAIL_BOUNCE_TRACKING`
  (compose: wł.). Bez nowych zależności. Operator: `docs/OPERACJE.md` § 52.

## [Unreleased] – Fokus w przyklejonym pasku konta (A11Y)

- **Poprawka dostępności:** pozycja menu w przyklejonym pasku konta (`.nav--primary`,
  `static/js/sticky-bar.js`) nie znika już spod fokusu, gdy menu serwisu wraca na ekran – pasek
  odkleja się dopiero, gdy fokus z niego wyjdzie. Wcześniej przewinięcie do góry z fokusem w pasku
  (albo Tab szybszy od obserwatora) gubił fokus na `<body>` (WCAG 2.4.3, 2.4.7).
- **Test `test_every_tab_stop_has_visible_focus`** mierzy po ustaleniu strony (dwie klatki), opisuje
  element w tym samym pomiarze i liczy `body` z fokusem dokumentu jako błąd (zawinięcie Taba do
  przeglądarki – nie). Nowy test `test_sticky_bar_keeps_focused_item_when_menu_returns`.

## [Unreleased] – Uzupełnienie zgód po zalogowaniu (CONS-01)

- **Bramka zgód uczestnika** (`apps.consent_gate`, `ConsentGateMiddleware` między 2FA a bramką nadzoru):
  brak wymaganej zgody (regulamin, RODO, od małoletniego oświadczenie o zgodzie opiekuna) albo zgoda
  pod **inną wersją dokumentu niż bieżąca** (stała `TERMS_VERSION` itd. albo `ConsentDefinition.version`)
  → przed obszarem uczestnika (`/me/…`, forum, webinary, warsztaty, płatności, API zgłoszeń, zadań
  i reklamacji) ekran **„Uzupełnij zgody”** (`/me/consents/complete/`). Przepuszczane: `/account/…`
  (eksport danych, usunięcie konta, hasło, język i kontrast), wylogowanie, profil, prośba do opiekuna,
  autozapis testu, klient nadzoru, strony publiczne, `/zgoda/<token>/`. Personel i anonim – zero
  zapytań; uczestnik – cache (Redis, 5 min), przy chybieniu jedno zapytanie; unieważnianie sygnałami.
  HTMX – 403 z `HX-Redirect`, API – 403 `CONSENTS_REQUIRED`. Wyłącznik `CONSENT_GATE_ENABLED`.
- **Ekran** w motywie konkursu i w 10 językach: wyłącznie brakujące zgody z etykietą jak w rejestracji,
  zdanie „dokument się zmienił: obowiązuje wersja X, Twoja zgoda dotyczyła Y”, dla małoletnich stan
  zgody opiekuna online i „Wyślij ponownie” (istniejący przepływ i limit; brak potwierdzenia online jak
  dotąd nie blokuje panelu), wyjścia RODO. Zapis: `ConsentRecord` (wersja, czas, droga `panel`, IP),
  projekcje na profilu wyłącznie „w górę”, audyt `participant.consents_completed` z językiem i SHA-256
  treści.
- **Koordynator:** kafelek „Uczestnicy z brakującymi zgodami” na pulpicie i CSV
  `/coordinator/consents/missing.csv` (audyt `consent_gate.exported`); ekran zmiany wersji zgody
  ostrzega, że wymusi ponowną zgodę. Komenda `consent_gate_report` (liczby bez danych osobowych).
- **Poprawka:** potwierdzenie zgody opiekuna online zapisuje wersję wzoru z zestawu konkursu, a nie ze
  stałej. Budżet zapytań pulpitu koordynatora 53 → 54. `docs/OPERACJE.md` § 51.
- **Po przeglądzie #97:** zmiana wersji w trakcie etapu nie blokuje pracy w toku – arkusz
  i „Zakończ” testu, upload (WWW i API), reklamacja i notatnik przechodzą przy ponowieniu zgody
  z banerem / nagłówkiem `X-Consents-Required` (H1); wersja dokumentu jedzie z formularzem i jest
  sprawdzana pod blokadą (L3); ekran bez braków czyści nieświeży stan cache'a (L4); `next` dla HTMX
  z `HX-Current-URL` tego samego serwisu (L5). Ten sam wyjątek dla pracy w toku obejmuje też zgodę
  **nową** (np. przestawioną na wymaganą w trakcie etapu), gdy uczestnik ma wpis w trwającym etapie
  (jedno zapytanie wyłącznie na tej ścieżce); konto bez takiego wpisu dalej nie oddaje pracy.

## v0.47.2 – 2026-10-05 – Dostępność WCAG 2.1 AA, deklaracja dostępności, motyw IQO 1.1.2 (A11Y-01)

- **Suita e2e dostępności** (`e2e/a11y/`, `scripts/a11y.sh`, job CI `a11y`): axe-core 4.13.0
  (MPL-2.0, w `e2e/vendor/` – poza obrazem, suma z rejestru npm) na ~70 ekranach obu konkursów
  i obu motywów (publiczne, rejestracja z błędami, logowanie, 2FA, reset/zmiana hasła, panel
  uczestnika, upload, test, czat, panel koordynatora i ciężkie tabele, recenzent, opiekun drużyny,
  weryfikacja listu wizowego, arabski RTL, wysoki kontrast) + kontrole klawiatury, widocznego
  fokusu, menu bez JS, reflow 320 px / 200 % i powiązania błędów z polami. CSP strony bez zmian
  (axe przez protokół DevTools). Nowe naruszenie critical/serious spoza `baseline.json` przewraca CI.
- **Poprawki:** 25 podpowiedzi pól bez `id` (wiszące `aria-describedby` – czytnik nie czytał
  podpowiedzi, m.in. w rejestracji); CAPTCHA powiązana z podpowiedzią i błędem; błąd kodu 2FA
  powiązany z polem; wysoki kontrast – `.btn--accent` czarny na żółci (było 1,3:1); menu panelu
  koordynatora otwarte atrybutem `open` od 900 px.
- **Motyw IQO Quantum 1.1.2:** paski ramy aplikacji na granacie (kontrast napisów 2,3–4,3:1 → AA),
  odnośnik „Deklaracja dostępności” w stopce; `min_app_version` 0.47.0.
- **Deklaracja dostępności:** nowa aplikacja `apps.accessibility`, komenda
  `seed_accessibility_statement <slug>` – projekt strony `/dokumenty/deklaracja-dostepnosci/` (PL/EN)
  do zatwierdzenia przez organizatora; odnośnik w stopce obu motywów **dopiero po publikacji** strony
  w danym konkursie (pamięć podręczna unieważniana sygnałami Wagtaila; budżety zapytań +1 na zimno).
  `docs/OPERACJE.md` § 50.

## v0.47.1 – 2026-10-05 – Kontrola dymna po wdrożeniu i szybkie wycofanie (OPS-04)

Tagu `v0.47.0` nie ma – numer pominięty przy wydaniu.

- **`scripts/smoke.sh`** – kontrola dymna wyłącznie odczytem (GET, bez logowania): dla każdego hosta
  konkursu strona główna z CSP, kluczowe strony, `/healthz/`, `/status.json` (wersja – ostrzeżenie),
  logowanie z CSRF (pole + ciasteczko) i CSP, plik statyczny z hashem manifestu i arkusze motywu (IQO)
  ze strony logowania, API (`editions/current/`), LiveKit `live.` i djcms (`/djcms/healthz/`,
  `/djcms/preview/`), gdy włączone; czasy, ponowienia, podsumowanie, kod 0/1/2. Tryb `--server`
  (hosty z `.env` i `check_domains --hosts`, przez proxy serwera `--resolve 127.0.0.1`, przepustka
  prac technicznych przez `-K -`).
- **`scripts/rollback.sh`** (na serwerze): `snapshot` (tag `olimpiada/web:previous` / `djcms:previous`
  z obrazu działającego kontenera, migracje z `django_migrations`), `decide` (auto / manual przy nowych
  migracjach albo nieznanym stanie / impossible), `run [--yes] [--allow-migrations]` (tylko `web worker
  beat [djcms]`, `--no-deps --no-build`, w `.env` wyłącznie `APP_VERSION`/`WEB_IMAGE`/`DJCMS_IMAGE`,
  baza i wolumeny nietknięte), `auto` (wycofanie + ponowna kontrola + list do `ALERT_EMAILS`, albo list
  z procedurą ręczną), `record-success`, `status`. Stan w `/opt/olimpiada/deploy-state/`.
- **`scripts/deploy.sh`**: krok 2/8 omija `deploy-state`; nowy krok **2a/8** (migawka) i **5b/8**
  (kontrola dymna → `record-success` albo `rollback.sh auto` i kod 1, bez kroków 6–8); furtka
  `DEPLOY_SMOKE=warn|0`; podpowiedź wycofania w pułapce EXIT po starcie nowych kontenerów; porządki
  nie kasują tagu `:previous`; `sync_competitions` w kroku „dj.” ponawiane (4 próby, 5/10/20 s) przy
  „Lista konkursów z API niedostępna”.
- **Przyczyna czerwonych wdrożeń „dj.”**: `MainApi.fetch_competitions()` (komendy djcms) korzystał
  z limitu odsłony strony (1 s na gniazdo) tuż po restarcie `web` – teraz `COMMAND_TIMEOUT_SECONDS = 30`
  (`djcms/apps/live/client.py`); odsłony stron bez zmian.
- `manage.py check_domains --hosts` – same hosty aktywnych konkursów z własnym hostem (dla kontroli dymnej).
- **Bufor całych stron po wdrożeniu**: gość przez do 120 s po wdrożeniu dostawał z bufora HTML poprzedniej
  wersji z odnośnikami do plików statycznych skasowanych przez `collectstatic --clear` (strona bez stylów).
  Teraz klucz bufora zawiera wydanie (`APP_VERSION`, `apps/web/page_cache.py`), a wdrożenie (krok 5b/8)
  i wycofanie wołają `page_cache_clear` przed kontrolą dymną.
- Testy: `scripts/tests/smoke_test.sh`, `scripts/tests/rollback_test.sh`, `scripts/tests/deploy_djcms_test.sh`
  (część 11 i zaktualizowane listy poleceń), `djcms/apps/live/tests/test_client.py`,
  `apps/tenancy/tests/test_check_domains.py`. Dokumentacja: `docs/OPERACJE.md` § 48 (i § 4.2),
  `docs/tasks/OPS-04.md`.

## v0.46.5 – 2026-10-05 – Test obciążenia i poprawki gorących ścieżek (PERF-01)

- **Narzędzie**: `scripts/loadtest/` – osobny, lokalny stos compose `olimpiada-loadtest` (sieć bez
  wyjścia na świat) i generator scenariusza dnia zawodów w Pythonie (asyncio + `httpx` z obrazu):
  logowanie przed T0, wejście wszystkich w T0 z PDF-ami treści, czat, autozapis testu, wysyłki skanów,
  koordynatorzy z eksportem CSV, goście; p50/p95/p99, błędy, req/s per adres i faza, raport
  CSV/Markdown, wiele procesów generatora, bezpiecznik hosta (produkcja odrzucana zawsze) i kryteria
  przerwania. `manage.py loadtest_seed` – odmawia bez `DEBUG`/`--i-know-this-is-not-prod` **i** bez
  `loadtest` w nazwie bazy.
- **Poprawki gorących ścieżek**: profil uczestnika pamiętany na czas żądania (`/me/` 38 → 30 zapytań,
  był czytany 9×), autozapis testu hurtem (95 → 15 zapytań), cache stron obejmuje linki z `utm_*`,
  `/results/<id>/` i strony > 512 KiB (kompresja `zlib`, format wpisu w kluczu – ~160 ms → ~6 ms CPU
  dla tabeli 3000 wierszy), limit wysyłek per konto zamiast per IP (sala za NAT-em), PDF treści
  kawałkami 64 KiB.
- **Nowe zmienne `.env`** (domyślnie bez zmian zachowania): `WEB_MAX_REQUESTS`/`_JITTER`
  (rotacja workera pod obciążeniem zrywała żądania w toku – 500/502 na wysyłkach), `WEB_MEM_LIMIT`,
  `CHAT_POLL_SECONDS`. Wyniki, ekstrapolacja na VPS (z kradzieżą CPU), konfiguracja na dzień zawodów,
  procedura testu na serwerze i rekomendacja: `docs/OPERACJE.md` § 42, `docs/tasks/PERF-01.md`.

## v0.46.4 – 2026-10-05 – Laboratorium na osobnym hoście (QC-02), skanowanie zależności (SEC-02)

### Laboratorium notatników na osobnym hoście (QC-02)

- **`NOTEBOOK_LAB_HOST`** (opcjonalne, `.env`): JupyterLite i notatnik startowy wyłącznie pod
  osobnym hostem (`lab.<SITE_DOMAIN>` albo osobna domena). Caddy (`scripts/render_caddyfile.sh`):
  blok hosta laboratorium (pliki laboratorium z polityką z QC-01, `/notebook-starter/*` do `web`,
  reszta 404, `Referrer-Policy: strict-origin`), a bloki serwisu – fragment `notebook_lab_moved`
  (ścieżka laboratorium → 302 na host laboratorium, `/notebook-starter/*` → 404). Pusta zmienna =
  konfiguracja proxy bajt w bajt jak dotąd.
- Django: `NotebookLabHostMiddleware` (ten sam rozdział hostów, przed WhiteNoise), notatnik startowy
  na hoście laboratorium bez sesji (token z osobną solą, 2 h, bramki na bieżącym stanie konta,
  nadzór zdalny), strażnik odrzucający na hostach serwisu żądania z `Origin`/`Referer` laboratorium
  poza nawigacją GET – przed CSRF, także wobec `https://*.<SITE_DOMAIN>`; host w `ALLOWED_HOSTS`,
  nie w `CSRF_TRUSTED_ORIGINS`; sprawdzenie `notebooks.E002`; etykieta `lab` zarezerwowana.
- Ciasteczka aplikacji potwierdzone jako host-only (test). Kompromisy subdomena vs osobna domena
  i kroki operatora: `docs/OPERACJE.md` § 40.7, `docs/tasks/QC-02.md`.
- **Po przeglądzie (podrzucanie ciasteczek z `lab.<domena>`):** w produkcji z laboratorium ciasteczka
  `__Host-sessionid`/`__Host-csrftoken` (jednorazowe wylogowanie przy włączeniu; skrypty czytające
  token obsługują obie nazwy), wygaszanie zdublowanych ciasteczek sesji/CSRF/języka na domenie
  nadrzędnej (przekierowanie 302/307, potem żądanie bez obu kopii), ten sam strażnik i wygaszanie
  w djcms (`NOTEBOOK_LAB_HOST` w compose), 403 dla żądań same-site z `Origin: null` albo bez
  nagłówków pochodzenia, `notebooks.E002` także dla hostów usług (`dj.`, `live.`, `meet.`, `monitor.`,
  `errors.`, S3, Jitsi) i konkursów. Zalecany wariant: osobna domena rejestrowalna.

### Skanowanie zależności i obrazów w CI (SEC-02)

- **CI `pip-audit`**: zależności Pythona backendu (rozwiązanie jak w obrazie, `uv pip compile`), djcms
  (`uv.lock`) i narzędzi budowy JupyterLite (QC-01) przez OSV; czerwono tylko przy podatności z wydaną poprawką, wyjątki z terminem
  i uzasadnieniem w `.security/pip-audit-ignore.toml` (`scripts/security/pip_audit_gate.py`).
- **CI `trivy (obraz web/djcms)`** po jobie `image`: bramka CRITICAL/HIGH z poprawką, SARIF do code
  scanning + artefakt, baza Trivy w cache'u, wyjątki z `expired_at` w `.security/trivyignore.yaml`.
  djcms jest teraz budowany w CI (własny zakres cache'u).
- **Co tydzień** (`security-scan.yml`): Trivy na obrazach usług z plików compose i na
  `olimpiada-web:main` z GHCR – raport w jednym zgłoszeniu `security-scan`.
- **Dependabot** (`.github/dependabot.yml`): pip (backend), uv (djcms), docker, docker-compose,
  github-actions; co tydzień, grupowane, cooldown 7 dni, etykiety.
- **Vendor JS**: rejestr `.security/vendor.toml`, `vendor_check.py check` w CI (skrót każdego pliku,
  nic spoza rejestru; KaTeX dostał `SHA256SUMS` krojów) i comiesięczne porównanie z npm/OSV/SRI
  (`vendor-upstream.yml`, zgłoszenie `vendor-js`, bez automatycznych aktualizacji).
- **Akcje GitHuba przypięte pełnym SHA** (także istniejące w `ci.yml`/`deploy.yml`), pilnuje
  `policy_check.py`; `ci.yml` z domyślnym `permissions: contents: read`. Dokumentacja:
  `docs/tasks/SEC-02.md`, `docs/OPERACJE.md` § 47.

## v0.46.3 – 2026-10-05 – Poczta z domeny konkursu i koniec pętli zwrotów (MAIL-01)

- **Druga domena nadawcy w relayu `mail`** (IQO: `iqo-official.org`): `scripts/mail_add_domain.sh <domena>`
  na serwerze – idempotentnie dopisuje domenę do `ALLOWED_SENDER_DOMAINS`, generuje klucz DKIM w kontenerze
  `mail` (nigdy nie nadpisuje istniejącego), odtwarza tylko `mail` i wypisuje rekordy do wklejenia
  (`mail-dns-<domena>.txt`): SPF **scalony** z obecnym (rekord ochronny `v=spf1 -all` → zmiana, nigdy drugi
  `v=spf1`), DKIM, DMARC bez drugiego rekordu (brak = `p=none` z planem na `quarantine`), MX bez zmian;
  `--check` (`opendkim-testkey` + `check_mail_dns`), `--print`. Podpis wielu domen robi obraz (bez zmiany wersji).
- **`manage.py check_mail_dns <domena>`** (nowa aplikacja `apps.mail_domains`, migracja `0001`): SPF
  oceniany jak u odbiorcy (include/redirect/a/mx, limit 10 zapytań), DKIM z porównaniem klucza relaya,
  DMARC; klient DNS na bibliotece standardowej (bez nowych zależności). Wynik w `SenderDomain`.
- **Ostrzeżenie dla koordynatora** na pulpicie i w „Ustawieniach konkursu”, gdy nadawca listów konkursu
  jest spoza relaya albo jego domena nie przeszła `check_mail_dns` (bez zapytań DNS w żądaniu).
- **Poczta zwrotna bez pętli** („loops back to myself”): odbicia na `noreply@` domen, nadawców monitoringu
  i `postmaster@` relaya → `discard` albo skrzynka operatora (`MAIL_BOUNCE_TARGET`,
  `deploy/mail/docker-init.d/50-bounces.sh`); restrykcje OPS-02 nietknięte.
- **Wdrożenie (krok 7/8)** ostrzega, gdy klucz DKIM którejś domeny różni się od opublikowanego.
- Dokumentacja: `docs/tasks/MAIL-01.md`, `docs/OPERACJE.md` § 49 (krok po kroku dla Squarespace), § 9.7.

## v0.46.2 – 2026-10-05 – Monitoring błędów (OPS-02) i monitoring z zewnątrz (OPS-03)

### Monitoring błędów i dostępności (OPS-02)

- **Śledzenie błędów** (nowa aplikacja `apps.monitoring`): klient `sentry-sdk` 2.71.x (integracje Django,
  Celery, Redis) wysyłający do **własnego** GlitchTipa; wyłączony bez `SENTRY_DSN` (bez importu pakietu,
  bez zmian w warstwach, CSP i HTML). Filtr danych osobowych przed wysyłką: bez treści żądań, ciasteczek,
  zapytań, IP, konta i zmiennych lokalnych; e-mail, PESEL, telefon, tokeny i wartości z błędów Postgresa
  → `[Filtered]`. Tag `competition` (slug), `release` = `APP_VERSION`, próbkowanie konfigurowalne.
- **GlitchTip 6.2.6** (obraz przypięty skrótem) w profilu compose `monitoring`: `glitchtip` (web + worker
  w jednym procesie) i `glitchtip-db` (osobny Postgres w izolowanej sieci `errors`), bez Redisa, bez
  `.env` platformy, limity pamięci i CPU, retencja 30 dni, poczta przez relay, rejestracja wyłączona.
  Blok Caddy'ego `errors.<domena>` przy `ERRORS_PROXY=1` (`scripts/render_caddyfile.sh`); slug `errors`
  zarezerwowany.
- **Monitor dostępności** (`apps/monitoring/uptime.py`, usługa `uptime`): oba serwisy, `/healthz/`,
  `/status.json`, LiveKit i GlitchTip, certyfikaty TLS (< 14 dni); listy o awarii i powrocie z
  deduplikacją, rosnącymi przypomnieniami (1 h → 24 h), jednym listem na przebieg i limitem 6/h. Sama
  biblioteka standardowa – kopia działa z crona na innej maszynie.
- **Błędy JavaScriptu** (opcjonalnie, `SENTRY_BROWSER=1`): własny loader z `/static/` (bez SDK i CDN),
  origin `errors.<domena>` w `connect-src` tylko przy włączonej funkcji.
- Poprawki po przeglądzie (`docs/tasks/OPS-02.md` § 9): tokeny w ścieżkach adresów (wzorzec trasy
  albo maska), linie `DETAIL:` Postgresa, adresy IP, Redis bez kluczy, zamknięta lista integracji,
  wyrażenia liniowe; GlitchTip poza `edge` – sieci `errors_front`/`errors_ingest`/`errors_egress`,
  relay z jednym nadawcą (`MAIL_CLIENT_NETWORKS` oddzielone od `TRUSTED_PROXY_IPS`); opcjonalne
  `ERRORS_UI_ALLOW`; ostrzeżenie w `deploy.sh`; wyciszenie `uptime` w przerwie planowej.
- RODO: rejestr czynności 1.21 – wiersz warunkowy „Monitorowanie błędów aplikacji” (podmiot wewnętrzny,
  bez państwa trzeciego). Dokumentacja: `docs/OPERACJE.md` § 44, `docs/tasks/OPS-02.md`, rekord DNS
  `errors` w `deploy/dns-olimpiadakwantowa.pl.md`.

### Monitoring z zewnątrz (OPS-03)

- **`.github/workflows/uptime.yml`**: GitHub Actions co 10 minut (+ ręcznie) sprawdza z zewnątrz
  `olimpiadakwantowa.pl` i `iqo-official.org` (`/` – kod 200 i czas, `/healthz/`, `/status.json` –
  `status` i `backup_restore_check`), LiveKit (`live.` → `OK`) i ważność certyfikatów TLS (ostrzeżenie
  < 14 dni, awaria < 7). Wykrywa śmierć całego serwera, której watchdog i Uptime Kuma z tego samego
  hosta nie zobaczą. Zero kosztów, zero kont, tylko `GITHUB_TOKEN` (`contents: read`, `issues: write`),
  jedyna akcja (`actions/checkout`) przypięta pełnym SHA.
- Alarm: **jedno** zgłoszenie z etykietą `awaria` przy awarii potwierdzonej w dwóch próbach (2 min
  odstępu), komentarz tylko przy zmianie zestawu awarii, automatyczne zamknięcie po powrocie; GitHub
  wysyła listy obserwującym repozytorium.
- `scripts/uptime_external.py` – sama biblioteka standardowa, składnia Pythona 3.10; testy bez sieci
  `scripts/tests/test_uptime_external.py`, nowy job CI `uptime-script`.
- Dokumentacja: `docs/tasks/OPS-03.md`, `docs/OPERACJE.md` § 46 (w tym opóźnienia crona i wyłączanie
  po 60 dniach bez commitów), `deploy/monitoring/README.md` § 5 – darmowy pinger jako druga opinia.

## v0.46.1 – 2026-10-05 – 2FA personelu (SEC-01), notatniki kwantowe (QC-01)

### Logowanie dwuskładnikowe dla personelu (SEC-01)

- **Polityka wymogu** (`apps/staff_mfa`): role platformy `TWO_FACTOR_REQUIRED_ROLES` (nowa wartość
  domyślna `superkoordynator,admin`) i polityka konkursu – tryb automatyczny (konkurs z delegacjami,
  `fees`, `onsite_logistics` albo `proctoring` wymaga 2FA od koordynatorów, opiekunów drużyn
  i przydziałów logistyki) albo wybrane role. Uczestnika nie da się objąć wymogiem.
- **Okres przejściowy** (`TWO_FACTOR_GRACE_DAYS`, domyślnie 14 dni, jednorazowy) z banerem na każdej
  stronie (także w motywie IQO), potem poczekalnia konfiguracji dla całej sesji, API i tokenów.
- **Ekran `/coordinator/security/2fa/`**: polityka (zmienia wyłącznie superkoordynator, audyt
  `2fa.policy_changed`) i lista personelu ze stanem 2FA i terminem.
- **Odzyskiwanie i kody**: nowy komplet kodów zapasowych (`/account/2fa/codes/regenerate/`) i wyłączenie
  2FA z hasłem **i** kodem; reset 2FA konta personelu wyłącznie przez superkoordynatora (wyjątek:
  instalacja bez superkoordynatora); blokada konta po 5 złych kodach na 15 min (`429 TWO_FACTOR_LOCKED`);
  jednorazowość kodu TOTP i kodu zapasowego odporna na równoległe żądania.
- **„Zapamiętaj to urządzenie”** (`TWO_FACTOR_REMEMBER_DAYS`, domyślnie 7; podpisane ciasteczko
  unieważniane zmianą hasła, wyłączeniem i resetem; polityka konkursu może je wyłączyć).
- **Listy do właściciela konta** (włączenie, wyłączenie, nowe kody, użycie kodu zapasowego, reset,
  blokada) i audyt `2fa.locked`, `2fa.remembered`, `2fa.codes_regenerated`, `2fa.grace_started`.
- Ekrany 2FA z `Cache-Control: private, no-store`; tłumaczenia w `apps/staff_mfa/locale` (10 języków).
- Poprawki po przeglądzie: personel do resetu liczony w całej platformie i także gdy konto jest
  zablokowane; zmiana adresu konta z 2FA albo konta personelu – tylko superkoordynator; list o resecie
  także na poprzedni adres (30 dni); wąski wyjątek bez superkoordynatora i komenda `reset_2fa`;
  ostrzeżenia `staff_mfa.W001`/`W002`; termin ról platformy tylko z `TWO_FACTOR_GRACE_DAYS`; znacznik
  zwolnienia z TTL 10 min i wersją podbijaną przy zmianie ról; zamykanie innych sesji przy włączeniu,
  wyłączeniu i resecie; „Zapomnij wszystkie urządzenia”; ciasteczko zaufania związane z konkursem;
  licznik prób przed sprawdzeniem kodu; lista personelu tylko dla superkoordynatora (bez N+1).
- `TWO_FACTOR_ENABLED=0` (domyślnie) – zachowanie bez zmian. Migracje `staff_mfa.0001`–`0002`
  (cztery puste tabele). Operator: `docs/OPERACJE.md` § 41.

### Notatniki kwantowe w przeglądarce (QC-01)

- **Notatnik przy zadaniu:** JupyterLite 0.8.5 z jądrem Pyodide 314.0.7 hostowany u nas
  (`/static/notebook-lab/<BUILD_ID>/`, bez CDN w czasie działania, wersje i skróty przypięte, etap
  `notebook-lab` obrazu, licencja każdego koła w manifeście). Strona `/me/notebooks/<zadanie>/`
  otwiera laboratorium w nowej karcie; notatnik startowy z testami widocznymi (podpisany adres
  `/notebook-starter/…`) otwiera się sam (`fromURL`). Oddanie – zwykła wysyłka `.ipynb`.
- **qclab:** własny symulator z API podzbioru Qiskita (`from qiskit import QuantumCircuit` działa
  w przeglądarce i na serwerze; Qiskit 2.x nie ma kół dla Pyodide). Zgodność z Qiskitem 2.5.2
  sprawdzana testami parzystości (poza CI – Qiskit nie jest zależnością).
- **Sprawdzanie automatyczne:** testy widoczne (w przeglądarce) i ukryte (wyłącznie serwer) w JSON-ie
  (`statevector`, `probabilities`, `counts`, `unitary`, `value`, `circuit`), punkty za test, wyniki
  z CSV, „Przelicz wszystko”, sprawdzenie na notatniku wzorcowym, panel w ekranie recenzenta.
  Podpowiedź dla komisji, nie ocena. Ocena w budżecie symulacji (obwód ponad budżet – błąd zamiast
  minut CPU), odporna na NaN/±inf; tolerancja zliczeń ucznia skalowana liczbą strzałów.
- **Piaskownica:** kontener `notebook-runner` (profil compose `notebooks`, `network_mode: none`, bez
  sekretów, losowy UID na zadanie ze sprzątaniem procesów i plików, `setrlimit`, hak audytowy jako
  obrona w głąb, healthcheck), wymiana przez wolumen `notebook_spool` z nowym workerem
  `notebook-worker` (kolejka Celery `notebooks`, krótkie limity czasu).
- **Bezpieczeństwo:** osobna polityka CSP ścieżki laboratorium ze źródłami zawężonymi do ścieżki
  (Caddy `(notebook_lab)` i middleware w dev), COOP/COEP, strażnik żądań z laboratorium; reszta
  serwisu bez zmian. Laboratorium wyłącznie dla kont bez roli personelu – personel ogląda notatniki
  w podglądzie tylko do odczytu (bez wykonywania, bez HTML/JS z wyjść); recenzent bez komunikatów
  testów ukrytych.
- Flaga konkursu `quantum_notebooks` (domyślnie wyłączona), nowa zależność `numpy>=2.4,<2.5`,
  katalog tłumaczeń `apps/notebooks/locale` (10 języków). Wdrożenie: `docs/OPERACJE.md` § 40
  (`COMPOSE_PROFILES=notebooks` podnosi `notebook-runner` i `notebook-worker`); podręczniki:
  organizatora § 10n, recenzenta § 3b, uczestnika § 3a.
- **Nadzór zdalny (PROC-01):** strona laboratorium (`web:participant-notebook`) stoi w `GATED_VIEWS`,
  a notatnik startowy z tokenem sprawdza bramkę nadzoru w widoku (403 bez gotowej sesji).

## v0.46.0 – 2026-10-05 – Zmiana hasła (AUTH-01b), test odtwarzania kopii (OPS-01)

Tag wskazuje **ten sam commit** co `v0.45.1` (`7e7f022`, scalenie PR #70) – zmiany opisuje sekcja
v0.45.1 niżej. Opis tagu wymienia też notatniki kwantowe (QC-01) – ten kod wszedł do `main` później
(PR #68) i wydany został w v0.46.1.

## v0.45.1 – 2026-10-05 – Zmiana hasła w panelu, test odtwarzania kopii (AUTH-01b, OPS-01)

### Zmiana hasła w panelu konta (AUTH-01b)

- **Ekran „Zmień hasło”** (`/account/password/`) dla każdej roli: aktualne hasło + nowe dwa razy,
  walidatory `AUTH_PASSWORD_VALIDATORS`, nowe ≠ aktualne. Nowa aplikacja `apps.password_change`
  (bez migracji); reguły w serwisie, `never_cache`, CSRF, limit `password_change` 10/h na konto.
- Po zmianie: bieżąca sesja zostaje (także znacznik 2FA), **pozostałe urządzenia wylogowane**, tokeny
  API skasowane, audyt `password.changed` (nieudane próby `password.change_failed`), list
  bezpieczeństwa „Hasło do konta zostało zmienione” w języku i pod hostem konkursu (z prefiksem
  ścieżki), z linkiem do „Nie pamiętasz hasła?”, bez hasła.
- **Konto bez hasła** (Google/Facebook): zamiast formularza przycisk „Wyślij mi link do ustawienia
  hasła” – zwykły list resetu na własny adres konta (publiczny formularz resetu takich kont nie obsługuje),
  audyt `password.set_link_sent`. Bez ustawiania hasła w samej sesji.
- **Pasek konta:** adres e-mail jest odnośnikiem do ustawień konta (`web/_account_who.html`, także dla
  nagłówka motywu); ekran edycji danych ma sekcję „Hasło”. Motyw `iqo-quantum` 1.1.1 dołącza ten fragment.
- Poprawki po przeglądzie (PR #70): **zmiana adresu e-mail wymaga aktualnego hasła** (konto bez hasła
  ustawia je najpierw); w `/cms/account/` nie ma już paneli hasła i e-maila
  (`WAGTAIL_PASSWORD_MANAGEMENT_ENABLED`/`WAGTAIL_EMAIL_MANAGEMENT_ENABLED = False`), a
  `/admin/password_change/` przekierowuje na `/account/password/`; 5 kolejnych złych haseł w sesji kończy
  sesję (`apps.accounts.reauth`, wspólne dla obu ekranów); podniesienie skrótu hasła nie wylogowuje;
  list odporny na awarię brokera, z godziną w strefie ucznia/konkursu (`Asia/Tokyo, UTC+09:00`);
  uczciwe zdanie o sesjach edytora django CMS; limit zmiany adresu per konto, komunikat „na tym koncie”;
  IQO 1.1.1 wymaga aplikacji 0.45.0 (fikstura paczki w testach).
- Tłumaczenia w 10 katalogach `apps/password_change/locale`. Dokumentacja: `docs/tasks/AUTH-01b.md`,
  `docs/OPERACJE.md` § 45, podręcznik uczestnika (§ 1) i podręcznik organizatora (§ 9.3).

### Conocny, automatyczny test odtwarzania kopii zapasowej (OPS-01)

- **`scripts/backup_verify.sh` codziennie o 4:40** (dotąd w niedzielę), z kopią z 3:15 pod jednym
  `flock` (wpis crona zakłada `scripts/deploy.sh`). Tymczasowy Postgres na nowej sieci `--internal`
  z limitami pamięci i CPU, dane na `tmpfs`; zrzut rozszyfrowany **strumieniem** do `pg_restore`
  (jawny zrzut nie dotyka dysku); paczka plików czytana w całości (`gpg | tar -tf`); `nice`/`ionice`
  dla procesów hosta.
- **Sprawdzenia aplikacji** (`apps/core/restore_check.py`, `manage.py restore_check verify`) w
  jednorazowym kontenerze z obrazem i środowiskiem działającego `web`, podłączonym wyłącznie do bazy
  tymczasowej: wiek kopii (26 h), migracje względem wdrożonej wersji, liczności tabel kluczowych w
  widełkach względem bazy żywej, czytelność każdego modelu, sekwencje kluczy, superużytkownik,
  odszyfrowanie pól Fernet (logistyka) bieżącym kluczem, próbka plików prac i mediów CMS w paczce,
  `dj.` jak dotąd. Pomiar czasów (`db_restore_s`, `files_list_s`, `checks_s`, `total_s`).
- **Wynik i alarmy**: `restore_check record` (cache, audyt `backup.restore_check`, natychmiastowy list
  do `ALERT_EMAILS` przy porażce), watchdog: alarm `backup-restore-check` do pierwszego udanego testu,
  próg „brak udanego testu” 36 h (dotąd 10 dni, `MAX_VERIFY_AGE_HOURS`); historia na hoście
  `/opt/olimpiada-backups/restore-checks.jsonl`; `restore_check show`.
- **`/healthz/` i `/status.json`**: nowy klucz `backup_restore_check` (`ok|failed|stale|unknown`, na
  końcu kontraktu, bez dat; nie zmienia kodu odpowiedzi ani `status`).
- Bezpieczeństwo: podwójna bramka izolacji (skrypt + komenda: przedrostek `restorecheck_`, host różny
  od `db`, `RESTORE_CHECK_ISOLATED=1`), sesja tylko do odczytu, hasło kopii przez deskryptor, wynik bez
  wartości pól; każdy błąd przebiegu = meldunek nieudany z nazwą kroku.
- Testy: `apps/core/tests/test_restore_check.py`, `scripts/tests/backup_offsite_test.sh` (przypadki 14,
  16), nowy `scripts/tests/restore_check_e2e.sh` (pełny cykl na lokalnym Dockerze, pomiar RTO).
- Dokumentacja: `docs/OPERACJE.md` § 43 (oraz § 1.4, § 3.2), `docs/tasks/OPS-01.md`.

## v0.45.0 – 2026-10-05 – Nadzór zdalny i rozmowy w LiveKit (PROC-01, STAGE-LK-01), poprawki resetu hasła (AUTH-01a)

### Nadzór zdalny etapów online (PROC-01)

- **Nadzór zdalny** (`apps.proctoring`, flaga konkursu `proctoring`, domyślnie wyłączona; włączany per
  etap online): konsola ucznia `/me/proctoring/<etap>/` – wersjonowana informacja i zgoda (niepełnoletni:
  wymagana potwierdzona online zgoda opiekuna), sprawdzenie sprzętu, opcjonalne zdjęcie dokumentu,
  kamera 320×240/10 kl./s (+ ekran/mikrofon, gdy etap wymaga) do pokoju LiveKit; start otwiera etap
  dopiero, gdy serwer LiveKit potwierdzi nadawanie. Zerwanie strumienia: komunikat, pasek na stronie
  etapu, dziennik. „Nie mogę użyć kamery” → alternatywa zatwierdzana przez koordynatora.
- **Bramka treści etapu** (`ProctoringGateMiddleware`): PDF zadania, wysyłka (WWW i API), start
  i strona testu (oraz tłumaczenia TR-01) w oknie etapu – wyłącznie uczeń z gotową sesją i personel;
  niezalogowany i osoba bez zgłoszenia – odmowa; token API przed sesją; druga linia obrony
  w `create_submission` i `start_attempt`; autozapis testu nigdy. LiveKit niedostępny: `block`
  (domyślnie) albo `allow` – tylko przy awarii serwera albo po N nieudanych połączeniach, z powodem
  w siatce, raporcie i CSV; odmowa kamery = prośba o alternatywę.
- Zgoda wiąże się z ustawieniami etapu (zmiana = nowa zgoda); niepełnoletni – zgoda opiekuna
  sprawdzana przy każdym wejściu i oświadczenie o nadzorze. Wycofanie zgody, anonimizacja, odpięcie
  nadzorującego i odwołanie opiekuna (DEL-01) wypraszają z pokoju; okna TZ-01 brane automatycznie.
- **Nadzorujący** `/proctoring/<etap>/`: siatka 12/16/24 kafli, subskrypcja tylko widocznej strony,
  wiadomości (serwer → `SendData` + odpytanie), „pokaż pokój/dokument”, incydenty, obecność.
  Pokoje per delegacja / przydział: opiekun drużyny dostaje token wyłącznie do pokoju swojej delegacji.
- **Komisja**: raport ucznia, eksport incydentów CSV, nagrania (domyślnie wyłączone; Track Egress bez
  transkodowania) z audytem; retencja nośników 30 dni po wynikach i oknie reklamacji (beat), wstrzymanie.
- RODO: wiersz rejestru czynności, sekcja `nadzor_zdalny` w eksporcie konta, anonimizacja kasuje
  nośniki, nota DPIA w podręczniku organizatora. Katalogi tłumaczeń aplikacji (`apps/*/locale/`)
  kompilowane w `Dockerfile` i sprawdzane testami. Opis: `docs/tasks/PROC-01.md`, `docs/OPERACJE.md` § 39.

### Rozmowy etapu w LiveKit jako alternatywa dla Jitsi (STAGE-LK-01)

- Dostawca wideo etapu **„LiveKit (pokój na platformie)”** (`VideoProvider.LIVEKIT`, migracja
  `competitions.0034`; opcja widoczna tylko przy skonfigurowanym LiveKit). Jitsi domyślne i bez zmian.
- **Jedna reguła uprawnień** dla obu dostawców: `apps.competitions.room_access` (przeniesiona z widoków
  Jitsi bez zmiany zachowania) – uczestnik, koordynator i komisja wchodzą tymi samymi widokami, w tych
  samych oknach, z tymi samymi rolami (moderator Jitsi = rola `presenter`, polecenia przez platformę –
  bez `roomAdmin` w przeglądarce; pokoje zakładane `CreateRoom` przed tokenem; decyzje moderatora
  przeżywają ponowne wejście; osobny pokój próby na zapis; nagranie rozmowy z nadzorem tylko ze zgodą); test parytetu uruchamia
  tę samą macierz ról na obu dostawcach.
- Pokój na platformie (interfejs webinarów), token POST-em, polecenia moderatora przez platformę
  (odbierz/oddaj głos, usuń; audyt `interview.room_control`). Bez drugiej integracji LiveKit.
- Opcjonalnie z **nadzorem zdalnym** (PROC-01): zgoda i sprzęt przed rozmową, dziennik połączeń,
  nagranie kamery przy `record`. Rejestr czynności 1.20. Opis: `docs/tasks/STAGE-LK-01.md`,
  `docs/OPERACJE.md` § 25.9 i § 39.5.

### Reset hasła: nadawca konkursu, konta bez hasła i konta nieuruchomione (AUTH-01a)

- **Nadawca listu resetu** to nadawca konkursu żądania (`Competition.from_email`), jak przy aktywacji
  i zaproszeniach – do tej pory zawsze `DEFAULT_FROM_EMAIL` (IQO dostawało list od nadawcy OK).
- **Konto bez hasła platformy** (Google/Facebook, hasło wyczyszczone przez allauth) dostaje link
  resetu i ustawia nim hasło – Django po cichu pomijało takie konta, wbrew obietnicy z ekranów.
- **Konto nieuruchomione** (zaproszony uczeń z importu lub delegacji, rejestracja bez aktywacji)
  dostaje z „Nie pamiętasz hasła?” zaproszenie albo link aktywacyjny zamiast ciszy; odpowiedź strony
  bez zmian (brak enumeracji). Reset nie aktywuje konta.
- **Zaproszony uczeń nie uruchomi konta linkiem aktywacyjnym** (z pominięciem zgód): „Wyślij link
  ponownie” wysyła mu zaproszenie, a `activate_with_token` odmawia takiego konta.
- Testy całej drogi pod domeną IQO, domeną OK i prefiksem ścieżki (host linku, język, nadawca, token
  wygasły, pamięć stron, CSRF, motyw IQO); `docs/tasks/AUTH-01a.md`, `docs/OPERACJE.md` § 9.7
  (kontrola nadawcy w relayu na produkcji).
- Poprawki po przeglądzie: koordynator nie aktywuje ręcznie konta z niezaakceptowanym zaproszeniem
  (przycisk i „Konto aktywne”); konto przed aktywacją dostaje link resetu, którego zapis zastępuje hasło
  z rejestracji i aktywuje konto; konto bez hasła – tylko z adresem potwierdzonym (allauth `verified`)
  i nie z zaproszenia bez zgód; nadawca konkursu spoza `ALLOWED_SENDER_DOMAINS` (nowa zmienna, wspólna
  z relayem, domyślnie `SITE_DOMAIN`) → `DEFAULT_FROM_EMAIL`; limit resetu także per adresat; zaproszenie
  z formularzy publicznych najwyżej raz na 10 min, z audytem i z linkiem do konkursu ucznia; reset
  koordynatora według tej samej reguły, co samoobsługa.

## v0.44.1 – 2026-10-04 – LiveKit: jeden port UDP z multipleksacją

- **Zmienione:** wariant „LiveKit na tym hoście” (OPERACJE § 36) – media przez jeden port UDP 7882
  z multipleksacją (`rtc.udp_port`) zamiast zakresu 50000–50100; TCP 7881 bez zmian. Jedna reguła
  zapory i jeden docker-proxy zamiast 101. Istniejący `livekit/livekit.yaml` na serwerze trzeba
  dopasować (`udp_port: 7882`, bez `port_range_*`).

## v0.44.0 – 2026-10-04 – Logistyka finału, listy wizowe, absolwenci, menu i kolory motywu, IQO Quantum 1.1.0

### Logistyka finału dla delegacji (LOG-01)

- **Nowa aplikacja `apps.delegation_logistics`** za flagą `onsite_logistics` w trybie `DELEGATIONS`:
  formularz opiekuna drużyny dla każdej osoby delegacji (dokument podróży, przyjazd/wyjazd,
  zakwaterowanie, wyżywienie i zdrowie za decyzją D21 i wyraźną zgodą, koszulka, kontakt alarmowy,
  zdjęcie ze skanem ClamAV), goście/obserwatorzy delegacji, terminy per sekcja z blokadą i audytem
  zmian (nazwy pól, bez wartości).
- **Oficer logistyki i obsługa rejestracji** – przydziały ponad rolę koordynatora; dane osób widzi
  wyłącznie oficer. Ekrany: przegląd kompletności, osoby, tablica przylotów/odlotów, lista pokoi
  (zasady: niepełnoletni nigdy z dorosłym, płeć pokoju, pojemność), wyżywienie, koszulki, eksporty CSV,
  przypomnienia e-mail w języku opiekuna.
- **Listy zapraszające do wizy** – PDF z rejestrem numerów `PREFIKS/ROK/NNNN`, zaszyfrowana migawka
  danych, nowy rodzaj szablonu dokumentu `VISA_INVITATION` (`tenancy.0015`), podpisy i pieczęć jak dyplomy.
- **Identyfikatory** PDF (A6, zdjęcie, kraj, rola, QR z losowym tokenem bez danych osobowych) i ekran
  odhaczania na telefonie (punkty kontroli, wyszukiwarka, skaner w przeglądarce z `BarcodeDetector`).
- **RODO:** szyfrowanie pól wrażliwych (Fernet z obsługą `SECRET_KEY_FALLBACKS`), retencja dobowa po
  końcu finału, sekcja w eksporcie danych konta, usuwanie przy usunięciu konta, warunkowa czynność
  w rejestrze (wersja 1.17).
- **i18n:** katalogi tłumaczeń w aplikacji (`apps/<nazwa>/locale`) – kompilowane przez `Dockerfile`,
  CI i `conftest.py`, sprawdzane przez `test_translations.py`; 125 nowych napisów w 10 językach
  (maszynowe, do przeglądu).
- **Poprawki po przeglądzie:** reguły pokoi sprawdzane po zmianie danych osoby (zdjęcie przydziału)
  i daty finału (oznaczenie naruszeń, kolumna CSV), niepełnoletni z płcią „inna” w pokoju
  jednoosobowym; dane paszportowe i o zdrowiu tylko przy znanym końcu finału, po retencji żadnych
  zapisów; dieta szyfrowana (`delegation_logistics.0002`); zdjęcia przekodowane bez EXIF, limit pikseli,
  identyfikatory per kraj; usunięcie członka czyści migawki listów; obsługę rejestracji nadaje oficer;
  przypomnienia tylko oficer; zapisy `update_fields`; numeracja listów pod blokadą (konkurs, rok);
  eksport danych konta z obecnością i listami.
- Dokumentacja: `docs/OPERACJE.md` § 31, `docs/PODRECZNIK-ORGANIZATORA.md` § 10d,
  `docs/PODRECZNIK-OPIEKUNA-DRUZYNY.md` § 7a, `docs/tasks/LOG-01.md`.

### Listy zapraszające do wizy: wnioski, weryfikacja, unieważnienie (VISA-01)

- **Wnioski opiekuna drużyny** o list imienny (`/delegation/logistics/letters/`) dla osób z kompletnym
  dokumentem podróży, z wyborem języka; wycofanie oczekującego wniosku. Jeden oczekujący wniosek na osobę.
- **Decyzje oficera logistyki** (`/coordinator/logistics/letter-requests/`): filtry kraj/stan,
  zatwierdzenie (= list imienny, poprzedni ważny list osoby unieważniony) i odrzucenie z powodem –
  pojedynczo i hurtowo; CSV bez danych paszportowych; e-mail do opiekunów w ich języku (zbiorczy).
- **Kod weryfikacyjny i QR** na każdym liście, **publiczna strona** `/visa/verify/<kod>/` (dane minimalne,
  bez numeru paszportu, limit `visa_verify` 60/h na IP, `no-store`, `noindex`), nowy segment `visa`
  w `RESERVED_SLUGS` i kontrakcie djcms.
- **Unieważnienie listu** z powodem (audyt, strona weryfikacji „unieważniony”, PDF nie do pobrania).
- **Język listu**: en, pl, es, fr, pt, ru, id (teksty w `letter_texts.py`); migawka wydarzenia w rejestrze.
- RODO: wnioski w eksporcie danych konta i w retencji/usuwaniu razem z osobą; rejestr czynności 1.18
  (nowy odbiorca – osoba znająca kod listu). Migracja `delegation_logistics.0003`.
- Poprawki po przeglądzie: list imienny zastępuje wcześniejszy tylko przy zmianie numeru paszportu,
  nazwiska albo obywatelstwa (ostrzeżenie u opiekuna, „unieważni list …” u oficera, ta sama reguła
  przy wystawieniu z karty osoby; list delegacji z nieaktualnymi danymi oznaczony w rejestrze);
  wypisanie osoby z delegacji unieważnia jej listy imienne; dane usunięte przed wydarzeniem = list
  nieważny na stronie weryfikacji; strona weryfikacji za bramką „konkurs ma listy” (niezależnie od
  flagi), adres weryfikacji zapamiętany na liście (`verification_base_url`) i komenda
  `visa_letter_redirects`; bez Google Analytics na `/visa/verify/` i `/dyplomy/<kod>/`; limit także dla
  HEAD, oficer bez limitu; daty w formacie języka listu, zdania pl/ru niezależne od przypadka nazwy
  organizatora, przy szablonie z bazy list tylko po angielsku.
- Dokumentacja: `docs/OPERACJE.md` § 31.8, `docs/PODRECZNIK-ORGANIZATORA.md` § 10d,
  `docs/PODRECZNIK-OPIEKUNA-DRUZYNY.md` § 7a, `docs/tasks/VISA-01.md`.

### Sieć absolwentów i mentoring (ALUM-01)

- **Absolwenci (`apps.alumni`, flaga `alumni`, domyślnie wyłączona):** byli uczestnicy z
  osiągnięciem w zakończonej edycji (próg ustawia koordynator: laureat / finalista / awans /
  każdy uczestnik) dołączają do sieci **za osobną zgodą** (wersjonowany dowód zgody, wycofanie
  jednym przyciskiem usuwa profil). Tylko osoby pełnoletnie. Osiągnięcia liczone na żywo
  wyłącznie z ogłoszonych wyników i wystawionych dyplomów; rejestr źródeł na medale.
- **Profil:** podpis „Imię N.” (pełne nazwisko tylko z wyborem i zgodą na publikację nazwiska),
  uczelnia, kierunek, miasto, kraj, bio, zainteresowania z listy zamkniętej, LinkedIn/GitHub
  (tylko https, tylko te serwisy, `rel=nofollow`), gotowość do mentoringu z tematami i liczbą miejsc.
  Katalog dla zalogowanych `/me/alumni/directory/`, publiczna ściana `/alumni/` (tylko profile
  oznaczone jako publiczne, minimalne pola).
- **Mentoring przez Wiadomości:** prośba → akceptacja → zwykła rozmowa P2P czatu. Nowy punkt
  rozszerzenia czatu (`PeerPolicy`, `register_peer_policy`, `ensure_peer_conversation`): mentee
  małoletni przy zasadzie „ta sama grupa wiekowa” albo wyłączonych rozmowach uczestników – każda
  wiadomość czeka na akceptację organizatora (kanał nadzorowany); przy „bez ograniczeń” – co
  najmniej postmoderacja. Zakończenie relacji zamyka rozmowę do odczytu. Nadzór koordynatora
  `/coordinator/alumni/mentoring/` (zakończenie z notatką, zgłoszenia problemów – list do
  koordynatorów od razu).
- **Zaproszenia** `/coordinator/alumni/invitations/` (warsztaty, webinary, jury; filtry: edycja,
  poziom, zainteresowania, mentorzy) w języku odbiorcy, z wypisem jednym kliknięciem (RFC 8058).
  **Statystyki** „gdzie są teraz” z progiem k-anonimowości 5.
- **RODO:** czynność `ALUMNI_ACTIVITY` w rejestrze (wersja 1.19, tylko przy fladze), sekcja
  `absolwenci` w eksporcie danych konta, czyszczenie przy anonimizacji, wstrzymanie automatu
  retencji na czas ważnej zgody (`BLOCKED_ALUMNI`). Nowy zakres limitu `alumni` (30/h, per konto).
- **Po przeglądzie krytyka:** notatka prośby małoletniego i opis mentora dla małoletnich dopiero po
  akceptacji koordynatora, automatyczne zgłoszenia (wzorce danych kontaktowych, zmiana daty urodzenia
  w trakcie relacji, rozmowa szyfrowana pod wymuszoną moderacją); data urodzenia mentee zapisana przy
  akceptacji i potwierdzona pełnoletność mentora; pierwsze 5 wiadomości nowej pary dorosły–małoletni
  w premoderacji także przy „bez ograniczeń”; ukrycie profilu kończy relacje mentora; minimalizacja
  zamiast pełnej anonimizacji przy wstrzymanej retencji; wycofanie zgody przy wyłączonej fladze;
  dowód zgody z językiem i skrótem treści i odnowienie zgody po zmianie wersji; polityki rozmowy
  łączone po najostrzejszej; statystyki bez komórek komplementarnych; limity próśb.
- **Medale (MED-01) jako osiągnięcia absolwentów:** ogłoszony medal albo wyróżnienie widać na profilu
  („2025/2026: złoty medal”) i liczy się do progu dołączenia (medal = laureat).

### Zarządzanie motywem z panelu i IQO Quantum 1.1.0 (THEME-02)

- **Menu serwisu** (`/coordinator/competition/theme/menu/`): kolejność, ukrycie, nazwy per język
  interfejsu, własne odnośniki (tylko `http(s)` i ścieżki serwisu; strona serwisu wyłącznie z drzewa
  tego konkursu), grupy rozwijane jednego poziomu; nakładane na menu z drzewa stron bez zapytania dla
  konkursu bez nadpisań (Olimpiada Kwantowa co do bajtu).
- **Kolory i opcje motywu** (`/coordinator/competition/theme/customize/`): schemat jasny/ciemny/systemowy,
  wariant logo i para krojów (nowe pola manifestu `logos`, `fonts`), kolory tokenów z `tokens.json`
  z kontrolą kontrastu WCAG AA blokującą zapis, podgląd, „Przywróć domyślne”; dostosowanie pamiętane
  per wersja motywu; arkusz `/_theme/custom.css` z podpisanego zestawu opcji (CSP bez zmian).
- Audyt (`theme.menu_saved`, `theme.menu_reset`, `theme.customized`, `theme.customization_reset`),
  limit POST `theme_settings` (120/h na konto), unieważnienie cache gościa po zapisie.
- **Slot `nav`** (menu serwisu) – motyw może przerysować samo menu; kontekst szablonów paczek
  dostał `sponsor_slider` (taśma sponsorów we własnym miejscu motywu); dostosowanie obejmuje też
  promienie `radius-*` z `tokens.json`.
- **IQO Quantum 1.1.0** (`themes/iqo-quantum/`): nowy wygląd odchodzący od Olimpiady Kwantowej
  (nagłówek nad planszą, typografia, karty, sekcje, stopka, motywy orbitali/fal), tryb jasny, warianty
  logo i krojów. Wgranie: `docs/OPERACJE.md` § 30.7.
- Migracja `themes.0003` (dwie nowe tabele).

## v0.43.0 – 2026-10-04 – Medale (MED-01) i płatności online (PAY-01)

### Medale olimpiady międzynarodowej, dyplomy w języku ucznia i ranking krajów (MED-01)

- **Medale z rankingu** (`apps.medals`, flaga konkursu `medals`, domyślnie wyłączona): schemat per etap
  (domyślnie IPhO 8/17/25 %, polityka remisu, wyróżnienie za ≥ X % najlepszego wyniku albo pełne
  zadanie), podgląd z `compute_stage_results`, ręczne zmiany z uzasadnieniem (audyt bez treści),
  ogłoszenie zamrażające nagrody po publikacji wyników (bramka zgodności sum), odmrożenie z uzasadnieniem.
  Ekran `/coordinator/medals/` (menu „Raporty → Medale”).
- **Dyplomy w języku ucznia:** rodzaje `MEDAL_GOLD`/`MEDAL_SILVER`/`MEDAL_BRONZE`/`HON_MENTION`
  (`results.0008`, `tenancy.0015`), zaświadczenie o udziale w konkursie z medalami; skład wielopismowy
  (`apps/medals/typesetting.py`: kierunek RTL, kroje Noto Arabic/Devanagari/Bengali i Droid Sans Fallback
  w repozytorium, kształtowanie HarfBuzz) wpięty w `render_pdf` (`register_composer`); język zamrażany
  przy wystawieniu; odwrót na angielski, gdy pisma nie da się złożyć. Nowa zależność: `uharfbuzz`.
- **Publiczne strony** `/results/<etap>/medals/` (filtr kraju, zgody jak w tabeli wyników) i
  `/results/<etap>/countries/` (nieoficjalny ranking krajów, tylko agregaty); eksport CSV i lista na galę
  (PDF) dla koordynatora, w audycie.
- Olimpiada Kwantowa bez zmian: formularz „Wystaw” bez rodzajów medalowych, brak menu i odnośników.
- **RODO:** czynność „Medale, dyplomy medalowe i ranking krajów” (warunkowa), sekcja `medale` w eksporcie
  danych konta. **i18n:** 37 napisów w katalogu aplikacji `apps/medals/locale` (10 języków, maszynowe);
  `Dockerfile` i `test_translations` obejmują katalogi aplikacji.
- Po przeglądzie: kraj przy wierszu tylko w `CODE` i przy nazwisku za zgodą; cyfry arabsko-indyjskie
  w kolejności LTR; ranking krajów z sumą/średnią tylko od 3 wyników; bramka ogłoszenia porównuje też
  wpisy i stany; dyplom niezgodny z nagrodą nieaktualny (weryfikacja, „Moje dyplomy”); język przypinany
  przy wystawieniu, brak kształtowania przy pobraniu – błąd zamiast cichego angielskiego; `uharfbuzz`
  przypięty do 0.56.
- Dokumentacja: `docs/OPERACJE.md` § 37, `docs/PODRECZNIK-ORGANIZATORA.md` § 10k, przewodnik opiekuna
  drużyny § 5b, podręcznik uczestnika § 7.

### Płatności online za udział: Stripe, Przelewy24, przelew, faktury (PAY-01)

- **Nowa aplikacja `apps.payments`** za flagą `fees` (Olimpiada Kwantowa bez zmian): cennik delegacji per
  edycja (delegacja, uczeń, opiekun, obserwator; ceny „early”/„late”; waluta), zamówienia liczone na
  serwerze z pokryciem składu (dopisany uczeń = nowe zamówienie tylko na przyrost), zniżki i zwolnienia
  delegacji z uzasadnieniem i audytem.
- **Faktura pro forma i faktura** (PDF, ReportLab jak dyplomy) z numeracją ciągłą per konkurs/rodzaj/rok
  (`IQO/FV/2026/0001`), migawka danych sprzedawcy (pola organizatora + `PaymentSettings`) i nabywcy
  (instytucja albo osoba, VAT ID opcjonalnie). D15 zmieniona: numeracja tak, rejestr VAT/korekty – nie.
- **Operatorzy płatności** za wspólnym interfejsem: Stripe Checkout + webhook z weryfikacją podpisu
  (bez SDK), Przelewy24 (rejestracja, powiadomienie SHA-384, `verify`, zwrot), przelew z kodem
  referencyjnym i zapisem koordynatora (dowód wpłaty skanowany ClamAV). Idempotentne webhooki
  (`ProviderEvent`), porównanie kwoty i waluty, „do wyjaśnienia” przy rozbieżności i podwójnej wpłacie.
- **Zwroty** przez API operatora (albo zapis zwrotu przelewu), częściowe i pełne; pełny zwrot uczestnika
  trafia do rejestru wpisowego (`record_refund`). Potwierdzenia wpłaty i zwrotu e-mailem w języku płacącego.
- **Ekrany:** opiekun `/delegation/payments/`, strona zamówienia `/payments/orders/<id>/`, uczestnik
  „Zapłać online” na kaflu „Wpisowe” (`/me/fees/pay/`), koordynator `/coordinator/payments/` (sumy per
  waluta, delegacje, zamówienia, eksport CSV dla księgowości, cennik i ustawienia).
- **Bezpieczeństwo:** kwota nigdy z formularza, podpis webhooka obowiązkowy (brak sekretu = 404), sekrety
  wyłącznie ze środowiska (`STRIPE_*`, `P24_*`), limity `checkout`/`payments_admin`, przekierowanie tylko na
  hosty operatora, panel `/admin/` płatności tylko do odczytu.
- **Po przeglądzie:** dostęp tylko czynnego opiekuna, zwroty pozycjami (zastępca płaci), warunkowy zapis
  sesji Checkout i `GET` sesji po nieudanym `expire`, sprzątanie beatem `payments-sweep` (porzucone sesje,
  zgubione webhooki, ponawianie zwrotów tym samym kluczem), przelew zapisywany pod blokadą i tylko na
  zamówienie otwarte, wpłata na anulowane → „do wyjaśnienia”, `livemode`, limit `payment_webhooks`.
- **RODO:** czynność „Płatności” w rejestrze (wersja 1.16, warunkowa), sekcja w eksporcie danych konta
  (z profilami delegacji edytowanymi przez konto); anonimizacja kasuje profil nabywcy uczestnika.
- **i18n:** katalog aplikacji `apps/payments/locale` (113 napisów, 10 języków, maszynowe); `Dockerfile`,
  `conftest.py` i `test_translations.py` obejmują katalogi aplikacji.
- Dokumentacja: `docs/OPERACJE.md` § 35, `docs/PODRECZNIK-ORGANIZATORA.md` § 10h,
  `docs/PODRECZNIK-OPIEKUNA-DRUZYNY.md` § 6, `docs/PODRECZNIK-UCZESTNIKA.md` § 2.

## v0.42.0 – 2026-10-04 – Webinary LiveKit, statystyki szkół, okna czasowe, tłumaczenia zadań, przegląd tłumaczeń

### Webinary w LiveKit (WEB-01)

- **Webinary** (`apps.webinars`, flaga konkursu `webinars`, domyślnie wyłączona): koordynator planuje
  webinar (termin, odbiorcy: konkurs / edycja / etap / komisja / kapitanowie, współprowadzący,
  nagrywanie, link dla gości), odbiorcy wchodzą do **pokoju na platformie** (`/webinars/<id>/room/`:
  siatka i widok prelegenta, ekran, mikrofon/kamera, lista uczestników, ręka, czat; motyw konkursu,
  11 języków, RTL). Widz bez nadawania – „Daj głos” przez `UpdateParticipant`.
- **LiveKit** (własny serwer, Apache 2.0): tokeny HS256 na 10 min z serwera (bez sekretu w HTML/JS),
  webhook `/integrations/livekit/webhook/` z obowiązkowym podpisem i ochroną przed powtórką (stan
  pokoju, **lista obecności**, koniec nagrania), nagrania przez Egress do prywatnego bucketu (MP4,
  publikacja, adres podpisany na 2 h), transmisja RTMP na YouTube (klucz niezapisywany).
- **Infrastruktura:** `deploy/livekit/` (nakładka compose z profilem `livekit`, przykłady `livekit.yaml`
  i `egress.yaml`, polityka MinIO egress), `LIVEKIT_PROXY` w `render_caddyfile.sh` (blok `live.`),
  `scripts/vendor_livekit_client.sh` (SDK z npm ze sprawdzeniem sumy, bez CDN). CSP: origin LiveKit
  w `connect-src` tylko przy konfiguracji. Nowe segmenty `webinars`, `integrations` w kontrakcie tras.
- Listy: zaproszenie (raz) i przypomnienie (beat co 5 min), z wyłączeniem; rejestr czynności
  „Webinary online (LiveKit)” przy fladze. Opis: `docs/tasks/WEB-01.md`, `docs/OPERACJE.md` § 36.

### Statystyki szkół i opiekunów szkolnych (STAT-01)

- **Flaga `school_statistics`** (domyślnie wyłączona), nowa aplikacja `apps.school_stats` bez modeli.
  Opiekun szkolny (`/supervisor/statistics/`): jego uczniowie
  w edycjach i etapach (zapis, oddanie, termin; punkty i awans **wyłącznie** z ogłoszonych publikacji,
  lista „tylko awansujący” bez punktów osób spoza listy), porównanie ze szkołą (tylko szkoła z wykazu
  zweryfikowana przez organizatora), województwem i całością z progiem k-anonimowości 5 i regułą
  dopełnienia, wykres SVG postępu przez edycje (bez JS), raport PDF szkoły dla dyrektora (same
  agregaty). Koordynator (`/coordinator/school-stats/`, menu „Raporty”): ranking szkół z porównaniem
  rok do roku, województwa, „szkoły do odzyskania”, eksport CSV, raport PDF dowolnej szkoły. Agregaty
  edycji w pamięci podręcznej z odciskiem publikacji (2 zapytania na edycję). Rejestr czynności 1.12
  (wiersz warunkowy). Katalogi tłumaczeń aplikacji (`apps/<nazwa>/locale`) kompilowane w obrazie
  i sprawdzane testem (`docs/tasks/STAT-01.md`, `docs/OPERACJE.md` § 29).
- **Poprawki po przeglądzie:** reguła zagnieżdżenia (szkoła ⊂ województwo ⊂ całość; województwo minus
  pokazane szkoły), dopełnienie wobec uczniów wszystkich opiekunów szkoły w CSV/PDF, średnia od 5
  wyników, przynależność wpisów zamrażana przy publikacji (`FrozenMembership`, migracja
  `school_stats.0001`), konta zanonimizowane poza szkołami, profil opiekuna z innego konkursu nie działa
  w tym konkursie (`supervisor_profile`), CSV szkół do odzyskania, CI sprawdza katalogi `apps/*/locale`.

### Okna czasowe etapu według stref czasowych (TZ-01)

- **Tryb okien** etapu zdalnego (nowa aplikacja `apps.time_windows`, flaga konkursu `stage_time_windows`,
  domyślnie wyłączona): N okien o stałym czasie pracy, przydział krajów domyślnie ze strefy stolicy
  (poprawka strefy kraju, przydział ręczny), wyjątki uczniów (inne okno, dodatkowy czas z powodem).
  Zmiany tylko przed startem okien, każda w audycie.
- **Egzekwowanie po stronie serwera:** upload (HTML i API) i `is_late` z okna ucznia; treść zadań uczniowi
  od startu jego okna, publicznie (strona „Zadania”, API, archiwum) po końcu ostatniego; test online
  w oknie ucznia; premoderacja forum i czatu przez cały czas okien; publikacja wyników po ujawnieniu;
  rama etapu nie może wyciąć okien.
- **Panel ucznia:** karta „Twoje okno”, odliczanie do własnego startu i terminu, godziny w strefie ucznia
  (strefę ustawia opiekun drużyny – nie zmienia okna), własne okno w kalendarzu osobistym.
- **Ekrany:** „Okna czasowe” pod etapem w panelu koordynatora (oś czasu z liczbami na żywo, kraje,
  wyjątki, kto w którym oknie) i „Okna czasowe drużyny” u opiekuna. RODO: czynność w rejestrze, eksport,
  anonimizacja. Katalogi tłumaczeń aplikacji (`apps/*/locale`) kompilowane w obrazie, CI i testach.
- Dokumentacja: `docs/OPERACJE.md` § 32, `docs/PODRECZNIK-ORGANIZATORA.md` § 10e,
  `docs/PODRECZNIK-UCZESTNIKA.md` § 3, `docs/PODRECZNIK-OPIEKUNA-DRUZYNY.md` § 5a.

### Tłumaczenia zadań przez delegacje krajowe („noc tłumaczeń”, TR-01)

- **Nowa aplikacja `apps.problem_translations`** (tylko konkursy w trybie `DELEGATIONS`): okno tłumaczeń
  etapu (zamyka się najpóźniej z otwarciem etapu), tryb osobny/wspólny dla delegacji jednego języka,
  wersja oficjalna jako tekst Markdown + LaTeX z numerem wersji, 1–2 języki delegacji i język ucznia
  (domyślny + nadpisanie przez opiekuna).
- **Opiekun** (`/delegation/translations/`): edytor obok wersji oficjalnej z autozapisem (HTMX) i
  podglądem wzorów (KaTeX zwendorowany; htmx i Alpine strony bazowej nadal z CDN-ów z SRI), alternatywnie PDF (skan antywirusowy), wysłanie do
  akceptacji, cofnięcie, aktualizacja po zmianie wersji oficjalnej z różnicami źródła.
- **Komisja** (`/coordinator/translations/`): kolejka, przegląd z różnicami wersji, zatwierdzenie
  (blokada) i zwrot z komentarzem; zmiana wersji oficjalnej (także PDF-u z ekranu zadań) oznacza
  tłumaczenia jako nieaktualne i wysyła listy; eksport do druku per język (PDF i widok do druku).
- **Uczeń:** po otwarciu etapu „Treść w języku: …” na karcie zadania obok wersji oficjalnej.
- **Poufność:** źródło tylko w oknie i tylko dla opiekunów z delegacją w bieżącej edycji, `no-store`,
  audyt każdego wglądu i pobrania, znak wodny kraju na PDF-ach opiekunów.
- **Wspólne:** obraz kompiluje katalogi tłumaczeń aplikacji (`apps/*/locale`), test katalogów obejmuje
  je; scope throttlingu `translation`; rejestr czynności 1.13; sekcja `tlumaczenia_zadan` w eksporcie
  danych konta. Dokumentacja: `docs/tasks/TR-01.md`, `OPERACJE.md` § 34,
  `PODRECZNIK-ORGANIZATORA.md` § 10g, `PODRECZNIK-OPIEKUNA-DRUZYNY.md` § 6a, `PODRECZNIK-UCZESTNIKA.md` § 3.

### Przegląd tłumaczeń przez native speakerów (L10N-01)

- **Panel tłumacza** `/translations/` (nowa aplikacja `apps.translation_review`): napisy jednego języka
  z katalogu projektu i katalogów aplikacji – tekst polski, angielski jako odniesienie, obecne
  tłumaczenie, kontekst z `.po` (miejsca w kodzie, uwagi, `msgctxt`, formy mnogie); filtry
  „bez tłumaczenia / maszynowe / przejrzane / z propozycją”, wyszukiwanie, propozycje z głosami
  (anonimowe wobec innych tłumaczy), decyzje recenzenta (zatwierdź, odrzuć, potwierdź, cofnij).
- **Role:** tłumacz (nadaje koordynator konkursu z >1 językiem interfejsu, tylko osobom z konkursu –
  `/coordinator/translators/`) i recenzent tłumaczeń (wyłącznie superkoordynator). Superkoordynator
  jest recenzentem każdego języka.
- **Nakładka w czasie działania:** zatwierdzone tłumaczenie wchodzi do gettext jako pierwszy katalog
  (`trans_real.translation` owinięte w `ready()`); w cache'u sam numer wersji per język, każdy proces
  przebudowuje nakładkę z bazy po zmianie wersji (≤ 5 s), bufor stron gości czyszczony przy zmianie;
  `TRANSLATION_OVERRIDES_ENABLED` wyłącza bez wydania. Potwierdzenia („obecne jest dobre”) nigdy nie
  trafiają do gettext, a poprawka podjęta wobec starszego `msgstr` ustępuje nowemu tekstowi z wydania
  (znacznik „do ponownego przeglądu”; migracja `translation_review.0002`).
- **Zasięg nadania:** rola nadana przez koordynatora należy do jego konkursu (widzą ją i odbierają
  wszyscy koordynatorzy konkursu) i działa tylko, dopóki osoba jest z konkursem związana; koordynator
  nadaje tylko w językach interfejsu swojego konkursu.
- **Bezpieczeństwo poprawek:** tłumaczenie to zwykły tekst – te same placeholdery, **dokładnie** te same
  znaczniki HTML co w `msgid`, żadnego nowego `<`/`>` ani prostego cudzysłowu (napisy bywają
  w atrybutach, a `{% translate %}` nie escapuje), bez znaków sterujących i bidi override; ponowna
  walidacja przy zatwierdzeniu i przy imporcie z JSON-a.
- **`manage.py export_translations`:** nakładki → `msgstr` w `.po` (diff wyłącznie poprawionych wpisów +
  `# l10n-reviewed`; potwierdzenie – sam znacznik; konflikt, gdy katalog zmienił się od decyzji),
  `--to-json`/`--from-json` (produkcja bez gita), zapis do `.po` tylko w checkoucie (`DEBUG` + `.git`,
  inaczej `--force`), `--prune` dopiero gdy tekst jest w skompilowanym `.mo`, `--prune-stale`,
  `--dry-run` (`docs/OPERACJE.md` § 33).
- **„Zgłoś tłumaczenie” w stopce** dla zalogowanego tłumacza (konkurs wielojęzyczny, strona nie po
  polsku): ścieżka strony bez parametrów + fraza + uwaga; lista zgłoszeń dla recenzenta.
- **Audyt** `translation.*`, throttling `translations` (120/h na konto), rejestr czynności **1.11**
  (wiersz warunkowy „Przegląd tłumaczeń interfejsu”), sekcja `tlumaczenia` w eksporcie danych konta,
  anonimizacja usuwa rolę, głosy i zgłoszenia. Obraz i testy kompilują teraz także katalogi aplikacji
  (`apps/*/locale`). Ocena wariantu Weblate: `docs/tasks/L10N-01.md` § 1.

## v0.41.1 – 2026-10-04 – Motyw na stronach z cache'u gościa (CSP)

- **Poprawka:** strona podana z cache'u stron gościa nie przechodziła przez `{% theme_head %}`, więc
  CSP nie wpuszczał arkuszy motywu z bucketu (`style-src`/`font-src`) – od drugiego wejścia gościa
  IQO wyglądało jak motyw klasyczny. Wpis cache'u pamięta teraz, że strona dołączyła motyw.
- Katalogi tłumaczeń aplikacji (`apps/*/locale/`) kompilują `Dockerfile`, CI i `conftest`;
  `test_translations.py` sprawdza je tymi samymi regułami co katalog główny.

## v0.41.0 – 2026-10-04 – Delegacje krajowe, motywy wizualne (classic + IQO Quantum)

### Delegacje krajowe: rejestracja przez opiekunów drużyn narodowych (DEL-01)

- **Tryb rejestracji konkursu** `Competition.registration_mode`: `OPEN` (domyślnie – każdy istniejący
  i nowy konkurs) albo `DELEGATIONS` (olimpiada międzynarodowa). W `DELEGATIONS` samodzielna rejestracja
  jest zamknięta na każdej drodze (formularz, API, Google/Facebook, import listy, rejestracja opiekuna
  szkolnego) z osobnym powodem `delegations` i adresem kontaktowym organizatora na `/register/`.
  `create_competition --registration open|delegations` (domyślnie `open`).
- **Delegacje** (`accounts.Delegation`, `DelegationLeader`, `DelegationInvitation`, rola `team_leader`):
  ekran koordynatora `/coordinator/delegations/` (zaproszenia opiekunów, limit, zamknięcie, eksport CSV),
  przyjęcie zaproszenia `/delegation/accept/<token>/`, panel opiekuna `/delegation/` (kilku opiekunów
  jednego kraju prowadzi jedną drużynę). Uczeń dostaje konto „zaproszone” i sam ustawia hasło oraz zgody.
- **RODO:** nowa czynność w rejestrze (wersja 1.11, tylko konkursy w trybie delegacji), sekcja opiekuna
  w eksporcie danych konta, sprzątanie roli i zaproszeń przy usunięciu/anonimizacji konta, trzeci
  właściciel dowodu zgody (`ConsentRecord.team_leader`).
- **i18n:** 70 nowych napisów ekranów opiekuna i listów w 10 katalogach (maszynowe, do przeglądu).
- Dokumentacja: `docs/OPERACJE.md` § 28, `docs/PODRECZNIK-ORGANIZATORA.md` § 10b,
  `docs/PODRECZNIK-OPIEKUNA-DRUZYNY.md`.

### Motywy wizualne wgrywane paczkami (THEME-01)

- **Tokeny motywu:** arkusze (`static/css/*.css`) czytają kolory, kroje, promienie i odstępy przez
  zmienne `--t-*`; wartości domyślne to wbudowany motyw „Klasyczny” (wygląd co do wartości wyliczonej
  sprzed zmiany, Olimpiada Kwantowa co do bajtu HTML i zapytań). Tryb wysokiego kontrastu wygrywa
  z każdym motywem.
- **Paczki ZIP** (`manifest.json`, `theme.css`, `tokens.json`, `assets/`, sloty `templates/theme/*.html`):
  walidacja przy wgraniu (ZIP-slip, bomba ZIP, typy plików, CSS parserem `tinycss2` bez `@import`
  i zewnętrznych `url()`, SVG oczyszczane, lint szablonów i kompilacja ograniczonym silnikiem, tokeny
  z ostrzeżeniami kontrastu, ClamAV), wersje niezmienne pod `themes/<slug>/<wersja>-<sha8>/`.
- **Sloty:** `header`, `brand`, `footer`, `page_wrapper`, `home_hero`, `news_card`, `page_header`;
  `base.html` zostaje właścicielem `<head>` (nonce CSP, skrypty, skip-link). CSP: origin bucketu
  w `style-src`/`font-src` wyłącznie na stronach z motywem.
- **Panele:** katalog motywów superkoordynatora (`/coordinator/platform/themes/`) i „Motyw serwisu”
  koordynatora (`/coordinator/competition/theme/`, flaga `themes`) z podglądem tylko dla koordynatora,
  wariantami układów, akcentem marki i aktywacją (audyt). Komenda `manage.py theme_install <zip|->
  [--activate <slug>]` (`docs/OPERACJE.md` § 30.1).
- **Wymaga przebudowy obrazu** (nowa zależność `tinycss2`).

## v0.40.3 – 2026-10-04 – Menu w języku interfejsu, og:image z pełnego adresu

- **Poprawka:** standardowe tytuły stron menu (zakładane z szablonu konkursu) idą przez gettext do
  języka żądania – menu IQO nie zostaje po polsku; tytuły zmienione przez redakcję bez zmian.
- **Poprawka:** `og:image` z rendycji w S3 nie dostaje drugi raz schematu i hosta serwisu.

## v0.40.2 – 2026-10-04 – Logo, favikona i obraz udostępniania per konkurs

- **Nowe:** pola `Competition.site_logo` i `social_image` (migracja `tenancy.0012`, obok istniejącej
  `favicon`) w „Ustawieniach konkursu”; puste = pliki statyczne jak dotąd (Konkurs #1 bez zmian).
  Komenda `competition_brand_images` wgrywa komplet do kolekcji konkursu (ZIP ze stdin).

## v0.40.1 – 2026-10-04 – seed_edition_kwantowa tylko w konkursie kwantowa

- **Poprawka:** po założeniu IQO z szablonu „kwantowa” `seed_edition_kwantowa` trafiał na dwie edycje
  o tej samej nazwie i zatrzymywał krok 6/8 wdrożenia; `--make-current` zdejmował znacznik bieżącej
  edycji we wszystkich konkursach. Teraz działa wyłącznie w konkursie `kwantowa`.

## v0.40.0 – 2026-10-04 – Wielojęzyczność per konkurs (I18N-01) i kraje zamiast województw (REG-01)

- **Języki interfejsu per konkurs:** `Competition.interface_languages` obok `default_language`
  zastępuje przełącznik `SiteSettings.english_interface_enabled` (migracja `tenancy.0011`
  przepisuje stan, także z witryn-aliasów; kolumna zostaje nieczytana i znika w następnym wydaniu –
  `docs/OPERACJE.md` § 26.5). Instalacja zna 11 języków: polski oraz
  `en`, `zh-hans`, `hi`, `es`, `ar` (RTL), `fr`, `bn`, `pt`, `ru`, `id`. Olimpiada Kwantowa zostaje
  wyłącznie po polsku (brak przełącznika, polski mimo `Accept-Language`). Rozstrzyganie: konto →
  ciasteczko → `Accept-Language` (warianty `zh-CN`, `pt-BR`) → język domyślny konkursu. Menu języków
  (`<details>`, bez JS) przy więcej niż dwóch językach. Edycja: „Ustawienia konkursu”, `/admin/`,
  komenda `competition_languages` (`docs/OPERACJE.md` § 26).
- **RTL i kroje:** `<html dir>`, arkusze uczestnika na właściwościach logicznych CSS, systemowe stosy
  krojów dla arabskiego, dewanagari, bengalskiego, chińskiego i cyrylicy (bez CDN – CSP bez zmian).
- **Napisy:** oznaczone do tłumaczenia przepływy uczestnika i strony publiczne (rejestracja,
  logowanie, reset hasła, `/me/`, czat, forum, biuro wsparcia, napisy szablonów CMS, komunikaty
  serwisów, listy). Listy poza żądaniem adresata idą przez `language_for`.
- **Tłumaczenia:** komplet 1709 napisów dla 10 języków – **maszynowe**, do przeglądu przez native
  speakerów (`docs/OPERACJE.md` § 26.3). Testy kompilacji, kompletności i zgodności placeholderów.
- **Kraje (REG-01):** lista 199 państw, `manage.py regions_countries --competition <slug>`,
  `create_competition --regions countries`; formularze przy `custom_regions` pokazują aktywne regiony
  konkursu (etykieta „Kraj”), wyświetlanie nazwy kraju zamiast kodu, konflikt interesów na krajach
  (`docs/OPERACJE.md` § 27). Olimpiada Kwantowa bez zmian.
- **Poprawki po przeglądzie:** jedna reguła `resolve_district` (aktywny region konkursu albo lista
  województw) we wszystkich drogach zapisu – profil `/me/`, `PATCH /api/auth/me/`, karta koordynatora,
  edycja komitetu, kody zaproszeń, rejestracja komitetu, przyjęcie zaproszenia z importu, API
  rejestracji (lista wartości `district` w API per konkurs); region zapisywany w profilu; import nie
  przypisuje wycofanych regionów; `PreferencesMiddleware` zdejmuje język wątku po odpowiedzi;
  nazwy kraju w eksporcie i wynikach bez zapytania na wiersz; nazwa konkursu w treści listów
  do uczestnika przez `branding.brand_names` (Olimpiada Kwantowa co do bajtu bez zmian).

## v0.39.1 – 2026-10-02 – Formularze pokoi wideo przechodzą CSRF

- **Poprawka:** ekran „Pokoje wideo” i strona linku-zaproszenia miały `Referrer-Policy: no-referrer`,
  przez co przeglądarka wysyłała formularz z `Origin: null`, a „Utwórz pokój”, „Pokaż linki”
  i „Dołącz” kończyły się stroną „Formularz wymaga odświeżenia”. Strony z formularzem mają teraz
  `same-origin`; przekierowanie z przepustką zostaje przy `no-referrer`. Test naśladujący
  przeglądarkę (CSRF + `Origin`) w `apps/web/tests/test_video_rooms.py`.

## v0.39.0 – 2026-10-02 – Rozmowy wideo tylko z przepustką platformy (Jitsi JWT)

- **Bezpieczeństwo (decyzja właściciela, „wariant A”):** własne Jitsi (`meet.<domena>`) wpuszcza
  wyłącznie z przepustką JWT (HS256) wystawianą przez platformę w chwili kliknięcia – na jeden pokój
  (`room` nigdy `*`), z oknem `nbf`/`exp`, nazwą wyświetlaną (imię i inicjał) i bez e-maila.
  Uczestnik wchodzi przyciskami **„Dołącz do rozmowy”** (od 15 min przed terminem do 60 min po nim)
  i **„Sprawdź kamerę i mikrofon”** w panelu, koordynator – „dołącz jako gospodarz” (moderator) na
  ekranie terminów. **Rozmowy prowadzi komisja:** aktywni recenzenci i członkowie komisji odwoławczej
  konkursu mają w panelu kartę „Rozmowy kwalifikacyjne” (terminy z zapisami na 14 dni, uczestnicy
  wyłącznie jako imię i inicjał) z „Dołącz jako gospodarz” (`/review/interview-slots/<id>/join/`,
  to samo okno i te same prawa, co koordynator). Moderatora nadaje wyłącznie token (`token_affiliation`; jicofo bez
  auto-właściciela i bez własnego uwierzytelniania – z nim uczestnik dostawał moderatora). Token we
  fragmencie adresu (`#jwt=`), odpowiedzi `no-store` i `no-referrer`, blok `meet.` w Caddym
  z `Referrer-Policy: no-referrer`. Listy (potwierdzenie, przypomnienie) niosą adres widoku wejścia
  w panelu, a nie adres pokoju. Obrazy Jitsi przypięte do `stable-11031`. Zachowanie sprawdzone
  uruchomieniem obrazów (`docs/OPERACJE.md` § 25.6).
- **Bez sekretu (`JITSI_JWT_APP_SECRET`) i dla pokoi poza naszym Jitsi** (publiczne `meet.jit.si`,
  BBB wpisane ręcznie, etap bez wideo) wszystko działa jak w v0.38.7. Nowa kontrola
  `competitions.W001` (sekret krótszy niż 32 znaki albo równy innemu sekretowi).
- **Pokoje wideo bez terminu** (`/coordinator/video-rooms/`, Komunikacja → Pokoje wideo): koordynator
  zakłada pokój (etykieta, ważność 1/7/30/60 dni), dostaje link gospodarza i gościa – adresy **na
  platformie** (`/zaproszenie/wideo/<klucz>/`), które przy wejściu proszą o imię i wystawiają przepustkę
  na 10 minut. Zamknięcie pokoju i „Wygeneruj nowy link” działają od razu; „Pokaż linki” na żądanie
  (z wpisem w dzienniku). Pokój może być udostępniony komisji – recenzenci i komisja odwoławcza
  wchodzą z panelu przyciskiem „Dołącz”. Koordynator może nadać członkowi komisji prawo zakładania
  własnych pokoi (ważność do 30 dni, `/review/video-rooms/`); odebranie zamyka mu ekran od razu.
- **Wdrożenie:** najpierw `scripts/deploy.sh` (migracje `accounts.0035`, `competitions.0033`), potem
  `scripts/deploy_jitsi.sh` – generuje sekret w `.env` portalu, przepisuje go do `jitsi/.env`,
  odtwarza `web worker beat`, sprawdza, że `web` go widzi, i dopiero wtedy przełącza Jitsi na
  przepustki (trwające rozmowy zostają przerwane – poza godzinami rozmów). Sprawdzenie, rotacja
  sekretu, wycofanie (`ENABLE_AUTH=0`, `ENABLE_AUTO_OWNER=1`): `docs/OPERACJE.md` § 25.

## v0.38.7 – 2026-10-02 – Zgoda ucznia na opiekuna, oceny dopiero po ogłoszeniu wyników

- **Poprawka (bezpieczeństwo, decyzja właściciela):** import listy uczniów nie dopisuje już opiekuna
  szkolnego do **istniejącego** konta. Uczeń dostaje list z prośbą o zgodę (link 14 dni,
  `/opiekun/zgoda/<token>/`, wymaga zalogowania jako ten uczeń, przyciski „Zgadzam się” / „Nie zgadzam
  się”, strona mówi, co nauczyciel zobaczy i że zastąpi obecnego opiekuna); adres zapisuje dopiero zgoda
  (`apps/accounts/supervisor_consent.py`). Dotyczy importu nauczyciela i koordynatora; pusta kolumna
  opiekuna u koordynatora nie czyści już adresu ucznia. Podgląd mówi o każdym zajętym adresie jednym
  zdaniem (bez rozróżniania ucznia od recenzenta/koordynatora), komunikat po zapisie podaje trzy liczby.
  Najwyżej jedna prośba na parę (uczeń, opiekun) na dobę; import nauczyciela ma limit żądań (`upload`).
  Konta **zakładane** importem – bez zmian (zgodą jest przyjęcie zaproszenia). Dowiązania sprzed tej
  wersji zostają w bazie bez zmian.
- **Poprawka (bezpieczeństwo, decyzja właściciela):** uczeń widzi oceny dopiero po ogłoszeniu wyników
  etapu także w API – `GET /api/me/submissions/` (`final_grade.score`, `decided_at`,
  `appeal.new_score`) i `GET /api/competitions/me/entries/` (`total_points`, zapisywane już przez
  podgląd koordynatora) oddają `null` do `Stage.results_published_at` (ten sam sygnał, co panel; bez
  wyjątku dla treningu). `final_grade.method` ma dla uczestnika wartości `REVIEW`/`APPEAL` – bez
  `THIRD_REVIEW`/`MODERATION` zdradzających rozbieżność recenzentów. Klucze odpowiedzi bez zmian
  (`docs/API.md` § 6.3). List o decyzji w sprawie reklamacji nie obiecuje już „aktualnej punktacji”
  w panelu. **Skutek dla reklamacji:** okno reklamacyjne zamyka się przed ogłoszeniem wyników, więc
  uczestnik składa reklamację, nie znając punktów (w panelu było tak już wcześniej).

## v0.38.6 – 2026-10-02 – Drugi składnik logowania obejmuje API

- **Poprawka:** przy włączonym `TWO_FACTOR_ENABLED` `POST /api/auth/login/` wymaga od konta
  z potwierdzonym urządzeniem pola `code` (kod z aplikacji albo zapasowy) i wydaje nowy token;
  token starszy niż potwierdzenie urządzenia oraz token konta, które drugi składnik musi mieć,
  a go nie ma, nie uwierzytelnia (`apps/accounts/authentication.py`). Włączenie drugiego składnika
  kasuje wcześniejsze tokeny. Dotąd token wydany po samym haśle omijał drugi składnik w całym
  `/api/`. Przy wyłączonej funkcji bez zmian. `docs/API.md` § 6.1c.

## v0.38.5 – 2026-10-02 – Poprawki po audycie bezpieczeństwa (pakiet 5)

- **Zmiana niezgodna wstecz (API):** `POST /api/auth/register/participant/` i `…/committee/`
  wymagają pary CAPTCHY (`captcha_key` + `captcha_value`, wyzwanie z `GET /captcha/refresh/`);
  odmowa `400 CAPTCHA_INVALID`. `POST /api/auth/login/` przyjmuje wyłącznie `application/json`
  (inny typ → `415`). Opis: `docs/API.md` § 6.1a–6.1b.
- **Poprawka:** limit prób formularzy jest atomowy (`cache.add`, bez wyścigu `check`/`consume`);
  logowanie rezerwuje miejsce przed sprawdzeniem hasła; awaria Redisa nie wyłącza limitów po cichu
  (błąd w logu raz na minutę); czat i forum liczone per konto zamiast per adres IP.
- **Poprawka:** prośba o zgodę opiekuna (`/me/guardian/`) ma limit `password_reset` – wspólny budżet
  listów na cudze adresy.
- **Poprawka:** reset hasła i zablokowanie konta przez koordynatora kasują token API konta.
- **Poprawka (webhooki):** wyłącznie adresy publiczne (zapis i każde doręczenie, IPv4 i IPv6, bez
  sieci compose'a), bez przekierowań; „ostatni błąd” bez treści wyjątku.
- **Poprawka:** tytuły zadań etapu przed jego otwarciem nie wychodzą w API bieżącej edycji ani na
  pulpicie uczestnika; strona weryfikacji dyplomu nie pokazuje nazwiska małoletniego bez zgody
  opiekuna; rachunek PDF nie interpretuje imienia ani szkoły jako znaczników; treść zgody edytowana
  w panelu jest escapowana (HTML-em jest tylko odnośnik do dokumentu).
- **Poprawka (testy online):** zdyskwalifikowany wpis nie rozpoczyna testu ani nie zapisuje
  odpowiedzi; zapis, zakończenie i domknięcie podejścia pod blokadą wiersza.
- **Poprawka:** odznaka „Organizator” w czacie jest osobnym elementem (nie da się jej udać imieniem);
  imię i nazwisko bez kropki środkowej `·`.
- **Poprawka:** losowy placeholder per wpis w cache'u stron (treść nie wyłudzi tokenu CSRF), audyt
  odczytu danych osobowych przez klucz API (`apikey.pii_read`), limity wyszukiwarki szkół (100 znaków,
  6 wyrazów, offset ≤ 5000), głęboko zagnieżdżony notatnik to odmowa 400, a nie 500, ścieżka „/\…”
  w odnośniku wydarzenia odrzucana.

## v0.38.4 – 2026-10-01 – Poprawki po audycie bezpieczeństwa (izolacja konkursów)

Audyt izolacji między konkursami jednej instalacji. Produkcja prowadzi dziś **jeden** konkurs
z wyłączonym `memberships_enforced`, więc żadna z luk nie była osiągalna – ale każda otwierała się
z chwilą założenia drugiego konkursu. Zachowanie instalacji jednokonkursowej (role z globalnych grup
Django) się nie zmienia.

- **Bezpieczeństwo:** pula recenzentów (`apps.grading.services.reviewer_pool`) bierze konkurs
  obowiązkowo – przydział automatyczny, reguły zadań, raport postępu, karty zadania i uczestnika
  nie podsuwają już członków komitetu innego konkursu; przydział ręczny, reguła i trzeci recenzent
  odmawiają takiej osoby w serwisie (`REVIEWER_NOT_ELIGIBLE`).
- **Bezpieczeństwo:** API komitetu (`/api/auth/committee/pending/`, `…/<id>/approve/`,
  `…/<id>/verify-district/`) zawężone do konkursu żądania – członek innego konkursu to 404; serwisy
  zatwierdzenia i województwa sprawdzają konkurs członka także same.
- **Bezpieczeństwo:** profil komitetu musi należeć do konkursu, w którym działa
  (`accounts.services.committee_profile_in`): bramki recenzenta i komisji odwoławczej (API i HTML),
  kolejka i decyzja reklamacji, widoczność prac komisji w `Submission.objects.for_user` i wzorcówka
  zadania. Rolę koordynatora w `Submission`/`StageEntry.for_user`, w moderacji
  (`grading.services.is_coordinator`) i przy wzorcówce rozstrzyga `has_role`, a nie globalna grupa.
- **Bezpieczeństwo:** lista i ekrany kont koordynatora liczą profil komitetu i profil opiekuna jako
  dowód własności konta – oczekujący członek komitetu albo opiekun innego konkursu nie jest już
  „niczyj” (edycja, aktywacja, reset hasła, usunięcie dają 404).
- **Bezpieczeństwo:** karta uczestnika (zgłoszenia pomocy, audyt, pula recenzentów) i karta członka
  komisji (recenzje, reguły, zgłoszenia, audyt) pokazują wyłącznie dane swojego konkursu; pulpit
  opiekuna szkolnego – uczniów swojego konkursu (`students_of`); retencja nie anonimizuje konta, które
  ma rolę albo profil w innym konkursie (nowa przeszkoda `other_competition`).
- **Zmiana:** nowy konkurs (komenda `create_competition`, ekran „Nowy konkurs”, kreator `/setup/`)
  powstaje z `memberships_enforced` włączonym; założenie konkursu obok aktywnego konkursu z tą flagą
  wyłączoną jest odmawiane z instrukcją (`check_memberships --fix`, potem flaga). Nowa kontrola
  systemowa `tenancy.E001` (`manage.py check --database default` i `migrate`): więcej niż jeden
  aktywny konkurs, a któryś liczy role z grup. `OPERACJE.md` § 6.1–6.2, § 6.5,
  `SECURITY_CHECKLIST.md` § 3 i § 3.3.

## v0.38.3 – 2026-10-01 – Poprawki po audycie bezpieczeństwa (infrastruktura)

- **Poprawka (Redis):** hasło (`REDIS_PASSWORD` – `scripts/deploy.sh` generuje je i dopisuje do
  istniejącego `.env`; puste = bez hasła, jak dotąd) i własna sieć `cache` tylko z web/worker/beat
  zamiast wspólnej `internal`; healthcheck uwierzytelnia się i sprawdza `PONG`. Serializator cache'a
  zostaje `pickle` (cache trzyma obiekty modeli, `bytes`, odpowiedzi HTTP – uzasadnienie w `base.py`).
- **Poprawka (ClamAV):** `freshclam` ma wyjście do internetu przez osobną sieć `clamav_egress` – na
  produkcji sygnatury nie aktualizowały się od startu kontenera (25 dni); `no-new-privileges`.
- **Poprawka (MinIO):** `public-media` anonimowo wyłącznie `s3:GetObject` – koniec listowania całego
  bucketu (`deploy/minio/policy-anonymous-public-media.json`, `mc anonymous set-json`).
- **Poprawka (Caddy, blok S3):** 404 dla API MinIO spod `/minio/*` (poza `/minio/health/live|ready`),
  `nosniff`, HSTS i CSP `sandbox` (poza PDF-em); test na żywym Caddym `scripts/tests/s3_proxy_test.sh`.
- **Poprawka:** media Wagtaila i treści zadań w S3 dostają `Content-Type` z rozszerzenia, nie od
  przeglądarki, i `Content-Disposition: attachment` poza obrazem/filmem/dźwiękiem/PDF-em
  (`apps/core/storage.py`); produkcja nie startuje z sekretami `change-me…` z `.env.example` ani
  z `E2E_MODE` przy `DJANGO_DEBUG=0`.
- **Poprawka (wdrożenie):** `docker compose pull --ignore-buildable` przed startem usług (obrazy
  cudze dotąd nigdy nieodświeżane); workflow `deploy.yml` z `permissions: contents: read`, walidacją
  celu SSH i przypiętym kluczem hosta (`vars.DEPLOY_SSH_KNOWN_HOSTS` – do ustawienia w GitHubie);
  porty `docker-compose.dev.yml` i mailpita tylko na `127.0.0.1`. Kroki operatora: `docs/OPERACJE.md` § 24.

## v0.38.2 – 2026-10-01 – Poprawki po audycie bezpieczeństwa (pakiet 1)

- **Poprawka:** `/cms/login/` i `/admin/login/` nie przyjmują hasła – odsyłają na `/login/`, jedyny
  formularz z limitem prób; reset hasła Wagtaila (`/cms/password_reset/`) wyłączony (404).
- **Poprawka (testy online):** odpowiedź liczbowa o skrajnym wykładniku (`9e1000000`) jest brakiem
  odpowiedzi, a nie wyjątkiem w ocenianiu; `finalise_overdue` domyka każde podejście osobno, a strona
  startowa testu – tylko podejścia wchodzącego; lista wariantów z autozapisu ma limit długości.
- **Poprawka:** eksporty CSV/XLSX zapisują tekst zaczynający się od `=`, `+`, `-`, `@` z apostrofem
  (nie jako formułę) i usuwają znaki sterujące (`apps/core/exports.py`).
- **Poprawka:** cache stron publicznych nie zapisuje odpowiedzi oznaczonej przez widok
  `Cache-Control: private/no-store/no-cache` (strona Wagtaila z hasłem).
- **Poprawka (forum):** ekran „Zgłoś wpis” nie pokazuje wpisów z wątku ukrytego albo odrzuconego.
- **Poprawka:** `pypdf` 6.x; globalny limit czasu zadań Celery (`CELERY_TASK_SOFT_TIME_LIMIT` 30 min,
  `CELERY_TASK_TIME_LIMIT` 35 min).

## v0.38.1 – 2026-10-01 – Status ucznia: liczniki wszystkich uczestników

- **Poprawka:** lista „Status ucznia” w edycji bieżącej obejmuje wszystkich uczestników konkursu
  (bez kont po anonimizacji), a nie tylko zapisanych do etapu – na produkcji liczyła 92 osoby z 297.

## v0.38.0 – 2026-10-01 – Wiadomości (czat)

### Wiadomości (czat)


- **Nowe:** Wiadomości 1:1 na platformie (`apps/chat`, zadanie CZ-01) – lista rozmów i wątek jak
  w komunikatorze LinkedIn, odświeżanie wątku co 15 s i wysyłka bez przeładowania (htmx, bez skryptu
  w treści strony). Kanał **organizator ↔ uczestnik** (wspólna skrzynka koordynatorów, podpis
  „Organizator · Imię N.”, „Napisz wiadomość” na karcie uczestnika) jest domyślnie włączony i nigdy
  nie jest moderowany; kanał **uczestnik ↔ uczestnik** ma tryby wyłączone (domyślnie) / premoderacja /
  postmoderacja / bez moderacji, a w czasie etapu przyjmującego rozwiązania zaostrza się do premoderacji
  (reguła forum, `stage_forcing_pre_moderation`).
- **Nowe:** katalog uczestników opt-in (tylko „Imię N.” i województwo, token zamiast identyfikatora
  w adresie), blokowanie i zgłaszanie wiadomości, kolejka moderacji `/coordinator/chat/moderation/`
  (akceptacja, odrzucenie z notatką, ukrycie, „przejrzane”, akceptacja zbiorcza), ustawienia
  `/coordinator/chat/settings/`; każda decyzja w audycie (`chat.*`) bez treści.
- **Nowe:** odznaka „Komunikacja → Wiadomości” (nieprzeczytane rozmowy + kolejka, jedno zapytanie)
  i licznik nieprzeczytanych w pasku konta i panelu `/me/` (jedno zapytanie; budżety `/me/` 51,
  `/coordinator/` 52).
- **Nowe:** list „masz nową wiadomość” bez treści (`ChatNotificationSettings`, domyślnie włączony),
  najwyżej jeden na rozmowę na 3 h i kolejny dopiero po otwarciu wątku, żaden do osoby, która czytała
  wątek w ostatnich 10 minutach (odczyt liczy się tylko z widocznej karty); wiadomość do organizatora –
  do każdego koordynatora wg jego ustawienia. Ustawienia (katalog, list) w sekcji „Wiadomości” ekranu
  „Edycja danych”.
- **Nowe:** opcjonalne szyfrowanie end-to-end rozmów między uczestnikami (tylko w trybie „bez
  moderacji”): WebCrypto ECDH P-256 → HKDF-SHA-256 → AES-GCM, kopia klucza prywatnego chroniona
  PBKDF2-SHA-256 (600 000 iteracji) hasłem do wiadomości, którego serwer nie zna (`static/js/chat-e2e.js`,
  `chat-ui.js`; testy `node --test backend/js_tests`). Rozmowy szyfrowane tylko do odczytu w czasie
  etapu; zgłoszenie niesie kopię odszyfrowaną przez zgłaszającego. AAD wiąże szyfrogram z kluczem
  publicznym nadawcy (nie z kontem), więc historia przeżywa usunięcie konta; odblokowany klucz żyje
  w przeglądarce najwyżej 12 h i znika po wylogowaniu albo wygaśnięciu sesji.
- **Nowe (§ 12):** szablony odpowiedzi koordynatora (`/coordinator/chat/templates/`, znacznik
  `{imie}`), stan (otwarta / czeka na uczestnika / zamknięta) i przypisanie rozmów organizatora
  z automatycznymi przejściami, „Odpowiedz i zamknij” i filtrami skrzynki z licznikami; rozmowy
  uczestników tylko w tej samej grupie wiekowej (domyślnie; pełnoletność ostrożnie z rocznika,
  sprawdzana także przy każdej wiadomości); dzienny limit nowych rozmów (domyślnie 5, okno 24 h);
  test przeglądarkowy obiegu szyfrowanego `e2e/test_chat_e2e.py`.
- **RODO:** rejestr czynności 1.10 – nowy wiersz „Wiadomości na platformie”, doprecyzowane zdanie forum
  o wiadomościach prywatnych; eksport konta: `wiadomosci_wyslane`, `ustawienia_wiadomosci`;
  anonimizacja zostawia wiadomości („Użytkownik usunięty”) i usuwa profil katalogu, klucz, blokady
  i ustawienia.

### Konfiguracja proxy przy każdym wdrożeniu (`caddy reload`)

- **Poprawka:** zmiany `deploy/Caddyfile` (nagłówki, trasy, domeny) nie docierały na produkcję –
  krok 2/8 kasował `deploy/`, a proxy trzymało montaż pojedynczego pliku ze starym i-węzłem, którego
  `up -d` nie odtwarzało. Proxy montuje teraz katalog stanu `caddy/` (`CADDY_CONFIG_DIR=./caddy`),
  który krok 2/8 omija; nowy `scripts/proxy_config.sh` składa plik z walidacją (`caddy validate`
  przed budowaniem) i ładuje go `caddy reload` w nowym kroku 4c/8 – bez restartu proxy. Odtworzenie
  kontenera zostaje drogą awaryjną. `.env` migrowany sam (`CADDYFILE_PATH` → `CADDY_CONFIG_DIR`);
  pierwsze wdrożenie jednorazowo odtwarza `proxy`. Punkty powrotu: kopia konfiguracji działającego
  proxy przy pierwszym renderze, `caddy/Caddyfile.prev` przy odrzuconym reloadzie albo niewstającym
  Caddym. `OPERACJE.md` § 23.
- **Poprawka:** przy `PLATFORM_SUBDOMAINS=1` nazwy dosłowne (`www.`, `dj.`, `meet.`, `monitor.`,
  `s3.`) mogły trafić do polityki TLS on-demand bloku `*.<domena>`, której `ask` (`/internal/tls-allowed`)
  odmawia – bez certyfikatu. Adapter Caddy'ego 2.8 wcina ich politykę do domyślnej (stojącej za `*.`),
  gdy istnieje polityka domyślna – np. przy `local_certs` (E2E); przy produkcyjnym ACME z `email`
  nazwy mają dziś własną politykę. `scripts/render_caddyfile.sh` przypina im jawnie `tls { key_type p256 }`
  (wartość domyślna – ten sam certyfikat), więc polityka jest osobna i stoi przed `*.`;
  `render_caddyfile_test.sh` sprawdza polityki po `caddy adapt` (ACME i `local_certs`, S3 pod `s3.`
  i pod `<domena>:9000`), a `djcms_routing_test.sh` – na żywym Caddym z domeną `olimpiada.test`
  i `ask` odmawiającym nazwom stałym (dawniej atrapa zgadzała się na wszystko, a `localhost` Caddy
  traktuje jak nazwę wewnętrzną – błąd nie wychodził).
- **Bezpieczeństwo (djcms, proxy):** odmowa `/<prefiks>/internal/…` w bloku domeny głównej (kontrakt
  tras kierował ją do web, chroniła tylko bramka hosta); przepustka prac technicznych do curla przez
  stdin (`-K -`), nie argumenty widoczne w `ps` (`djcms_switch.sh`, krok 5a wdrożenia).
- **Przełączenie djcms – odporność:** jedna blokada zmian serwisu `caddy/.lock` (wdrożenie od kroku
  2/8 do końca, `djcms_switch.sh`, `djcms_cutover.sh`, `proxy_config.sh`; dziedziczona przez
  `OLIMPIADA_PROXY_LOCK=held`); `djcms_switch.sh on` wraca do Wagtaila po przerwaniu sygnałem,
  zapisuje `DJCMS_EVER_PRIMARY`, a błąd TLS hosta w kontroli dymnej to ostrzeżenie; `off`
  i `--rollback` czekają na blokadę do 2 min; `render` (wdrożenie) odmawia, gdy tryb w `.env` ≠ tryb
  w działającym proxy; `djcms_cutover.sh`: przy `DJCMS_PRIMARY=1` wymaga zamrożonego Wagtaila,
  znacznik `DJCMS_CUTOVER_DONE` przed krokiem 6, `DJCMS_EVER_PRIMARY` też wymaga `--force-reimport`,
  `--rollback` zawsze woła `djcms_switch.sh off`; wdrożenie przy `DJCMS_PRIMARY=1` porównuje kontrakt
  tras hosta z obrazem web. `OPERACJE.md` § 22.8–22.10, § 23.

### Serwis publiczny na django CMS dla wszystkich konkursów (DJ-02)

djcms z DJ-01 staje się **pełnym zamiennikiem publicznej części Wagtaila dla każdego konkursu**
platformy (domena główna, subdomeny, `EXTRA_DOMAINS`, konkursy pod prefiksem ścieżki), na ich
prawdziwych hostach. Które strony podaje djcms, a które Wagtail, rozstrzyga jedna zmienna
`DJCMS_PRIMARY` przełączana `scripts/djcms_switch.sh on|off` (render Caddyfile'a i `caddy reload`,
bez restartu usług); adresy aplikacji (logowanie, panele, `/cms/`, `/api/…`) zawsze obsługuje `web`.
**Domyślnie nic się nie zmienia:** przy `DJCMS_ENABLED=0` konfiguracja Caddy'ego, compose'a
i wdrożenia jest ta sama co przed DJ-01; przełączenie na produkcji wyłącznie po zgodzie
organizatora (`scripts/djcms_cutover.sh`, `OPERACJE.md` § 22.9). Specyfikacja:
[`tasks/DJ-02.md`](tasks/DJ-02.md) (odchylenia przy realizacji – § 15).

- **Trasy z jednego źródła** (DJ-02a): `manage.py djcms_routes --write|--check` zapisuje adresy
  aplikacji z urlconfu `web` do `backend/djcms_contract/app_routes.{json,env}` (Caddy i djcms
  czytają te same pliki; CI pilnuje `--check`). `RESERVED_SLUGS` uzupełnione do pełnej listy
  pierwszych segmentów urlconfu i `djcms`; `djcms` zarezerwowane też jako etykieta subdomeny.
- **API v2 per konkurs** (DJ-02b): `GET /internal/djcms/v2/competitions` (hosty, tryb, adres
  publiczny, `linked_paths`, `fingerprint`, także konkursy nieaktywne) i
  `/internal/djcms/v2/c/<slug>/{chrome,stages,problems,results,editions,editions/<id>/results,
  workshops,partners,export}` – te same bramki co w DJ-01; paczka `olimpiada-cms-bundle` **v2**
  (konkurs, strony-dane, przekierowania Wagtaila). Wspólne wektory rozstrzygania hosta
  `backend/djcms_contract/resolution_cases.json` testowane w obu projektach.
- **Zamrożenie edycji Wagtaila** (DJ-02c): `manage.py cms_freeze on|off|status` (wiersz w bazie, bez
  restartu) – strony w `/cms/` tylko do odczytu, egzekwowane po stronie serwera, z banerem;
  wyjątki: strona „warsztaty” i strona partnerów (dane aplikacji pokazywane w djcms na żywo) –
  edycja i publikacja tak, zdjęcie z publikacji nie; akcje API panelu (`/cms/api/main/pages/…/action/`)
  zamrożone w całości; `cms_freeze on --wait` czeka, aż stan zobaczą wszystkie workery `web`.
- **djcms – wiele witryn** (DJ-02d–f): witryna django CMS per konkurs (bez `SITE_ID`), rejestr
  konkursów z API (`sync_competitions`, leniwe założenie witryny nowego konkursu z drzewem
  startowym), wszystkie adresy djcms pod `/djcms/` (admin, statyki, media, `healthz`, `preview`,
  `sso`), import wielowitrynowy (`import_cms_bundle --competition/--all/--skip`, paczka v1 i v2),
  `verify_cutover`; tryb `preview`/`primary` z nagłówka `X-Djcms-Mode` od zaufanego proxy, noindex
  tylko w podglądzie, canonical/og/GA jak w Wagtailu, `robots.txt` i `sitemap.xml` per witryna,
  przekierowania starych adresów (`dj_seo.Redirect`, import i automatyczne po zmianie adresu),
  404 w ramie konkursu, podgląd ciasteczkiem `djcms_view` (`/djcms/preview/`).
- **Caddy i skrypty** (DJ-02h, DJ-02i): sekcja tras djcms w każdym bloku aplikacji, `dj.` już tylko
  przekierowuje (302), `scripts/djcms_switch.sh on|off|status|check` z automatycznym powrotem przy
  nieudanej kontroli, `scripts/djcms_cutover.sh` (`--check`, `--dry-run`, `--skip`, `--rollback`).
- **Redaktorzy przez SSO z `/cms/`, uprawnienia per konkurs** (DJ-02g): pozycja menu Wagtaila
  „Edytuj w django CMS” (`/cms/django-cms/`) wystawia jednorazowy token HMAC (60 s, nonce, host,
  lista konkursów z prawem edycji/publikacji) wysyłany formularzem `POST` na `/djcms/sso/` tego
  samego hosta; djcms zakłada konto `web:<id>` bez hasła i **zastępuje** jego grupy grupami
  `redakcja:<slug>`, `redakcja:<slug>:bez-publikacji` albo `redakcja:platforma` (bez publikacji:
  usuwanie i przenoszenie tylko stron bez opublikowanej wersji, jak w Wagtailu). Sesja z SSO trwa
  najwyżej `DJCMS_SSO_SESSION_SECONDS` (domyślnie 2 h), a wylogowanie z aplikacji głównej kończy ją
  od razu. Lokalnych kont redaktorów w djcms nie ma –
  hasłem loguje się wyłącznie techniczny superużytkownik. `manage.py setup_djcms_groups` (każde
  wdrożenie) zakłada grupy i foldery filera per konkurs (+ folder „Wspólne” do odczytu) i usuwa
  grupę „Redaktorzy” z DJ-01 (`OPERACJE.md` § 22.3).
- **Konfiguracja djcms**: `CMS_PERMISSION = True` (było `False`) – `GlobalPagePermission` zawężone
  do witryny konkursu, nigdy uprawnienia na pojedynczych stronach; `FILER_ENABLE_PERMISSIONS = True`
  (było `False`) – folder `Konkurs: <nazwa> (<slug>)` należy do redakcji konkursu. Pliki dalej są
  wyłącznie publiczne.
- **System checki**:
  - `dj_blocks.E001` – **zmiana znaczenia**: było „`FILER_ENABLE_PERMISSIONS` musi być wyłączone”,
    jest „pliki filera wyłącznie publiczne” (`FILER_IS_PUBLIC_DEFAULT = True` i ukryty przełącznik
    „prywatny” w panelu filera, który przy włączonych uprawnieniach filer sam by pokazał),
  - `dj_blocks.E002` (nowy) – `FILER_ENABLE_PERMISSIONS` musi być włączone,
  - `dj_sites.E001` (nowy) – `CMS_PERMISSION` musi być włączone,
  - `dj_sites.W001` (nowy) – `DJCMS_SSO_KEY` krótszy niż 32 znaki albo równy innemu sekretowi djcms,
  - `cms.W013` (nowy, `web`) – to samo dla `DJCMS_SSO_KEY` w aplikacji głównej,
  - `cms.W011` (nowy) – adres aplikacji bez wpisu w `RESERVED_SLUGS`,
  - `cms.W012` (nowy w DJ-02a, **rozszerzony** w DJ-02g) – opublikowana strona Wagtaila pod adresem
    aplikacji **albo djcms**: `/djcms/…` pod każdym hostem i `/<cokolwiek>/djcms/…` pod `SITE_DOMAIN`
    (Caddy kieruje te adresy do djcms, więc strona zniknęłaby po włączeniu),
  - `dj_pages.W003` (nowy) – ścieżka z `linked_paths` bez opublikowanej strony w djcms.
- **Reguła `linked_paths`** (lista stron, do których linkuje aplikacja; sprawdzają ją `dj_pages.W003`
  i `verify_cutover`): dokumenty zgód i literały z szablonów aplikacji (`/dokumenty/rodo/`, `/faq/`,
  `/harmonogram/`, `/warsztaty/`) trafiają na listę **tylko wtedy, gdy stoi pod nimi opublikowana,
  publiczna strona Wagtaila** – adres, który dziś w Wagtailu daje 404 (np. `/faq/` konkursu
  założonego z szablonu), nie blokuje przełączenia; konkurs pod prefiksem ścieżki nie dziedziczy
  literałów szablonów (te prowadzą do konkursu-gospodarza).
- **Usunięte (DJ-02k)**: wewnętrzne API **v1** (`/internal/djcms/v1/*` – teraz ta sama pusta 404 co
  każdy nieznany adres gałęzi), eksport paczki v1 (`export_cms_bundle --bundle-version`; komenda
  buduje zawsze v2, bez `--competition` – aktywny konkurs witryny domyślnej), ustawienie
  `DJCMS_COMPETITION_SLUG` (konkurs stoi w ścieżce API) i `DJCMS_MAIN_PUBLIC_URL` **w djcms**
  (adresy aplikacji z `public_base` konkursu, bez niego – względne na tym samym hoście; w `web`
  zmienna zostaje – opisuje adres konkursu domeny głównej). Importer djcms dalej przyjmuje paczki v1
  (kopie sprzed DJ-02). Wpisy `DJCMS_COMPETITION_SLUG` w `.env` serwera są ignorowane – można je usunąć.
- Nowe zmienne `.env`: `DJCMS_PRIMARY`, `DJCMS_SSO_KEY` (generuje wdrożenie), `DJCMS_WORKERS`,
  `DJCMS_THREADS`, opcjonalnie `DJCMS_PROXY_IP`, `DJCMS_SSO_SESSION_SECONDS`.

**Zmiany decyzji DJ-01** (DJ-02 § 1.3):

| DJ-01 | DJ-02 |
|---|---|
| `dj.` serwuje treść, admin pod `dj./admin/`, media pod `dj./media/` | `dj.` = przekierowanie; djcms na każdym hoście konkursu pod `/djcms/*` |
| noindex zawsze | noindex tylko w trybie `preview` |
| stopka „Ta strona w wersji Wagtail” | w `preview`: pasek „Wyłącz podgląd”; w `primary`: brak |
| `SITE_ID = 1`, `CMS_PERMISSION = False`, `FILER_ENABLE_PERMISSIONS = False` | brak `SITE_ID`, `True`, `True` |
| API dla jednego konkursu (`DJCMS_COMPETITION_SLUG`) | API v2 per konkurs; v1 i `DJCMS_COMPETITION_SLUG` usunięte |
| jedna grupa „Redaktorzy”, konta redaktorów zakłada administrator | grupy per konkurs, redaktorzy wyłącznie przez SSO z `/cms/` |
| tabela warsztatów redakcyjna (import) | na żywo z API (także partnerzy) |

### Wersja porównawcza na django CMS (`dj.<domena>`, DJ-01)

Równoległa, publiczna, **nieindeksowana** wersja części informacyjnej serwisu pod
`dj.olimpiadakwantowa.pl`, redagowana w django CMS – do porównania z Wagtailem (`/cms/`) przed
decyzją, który CMS zostaje. **Domyślnie wyłączona:** bez `DJCMS_ENABLED=1` konfiguracja Caddy'ego,
`docker compose config`, przebieg wdrożenia i kopii zapasowych są co do polecenia takie jak
dotąd. Włączenie na produkcji wymaga zgody organizatora (`OPERACJE.md` § 22.2). Specyfikacja:
[`tasks/DJ-01.md`](tasks/DJ-01.md).

- **Wewnętrzne API aplikacji głównej** `GET /internal/djcms/v1/{chrome,stages,problems,results,editions,workshops,export}`
  (`apps/cms/djcms_api/`; w DJ-02 zastąpione API v2 per konkurs, v1 usunięte – wyżej): bramka hosta wewnętrznego, tokenu
  `DJCMS_INTERNAL_TOKEN` (≥ 32 zn., nagłówek `X-Djcms-Token`) i metody `GET` – każda porażka to
  pusta 404; wyniki przez białą listę kluczy, zadania dopiero po `opens_at`. Dane zawodów liczą te
  same funkcje co strony Wagtaila (`apps/cms/live_data.py`, refaktoryzacja bez zmiany wyglądu).
  Eksport drzewa Wagtaila do paczki `olimpiada-cms-bundle` v1 (`manage.py export_cms_bundle`;
  od DJ-02k paczka v2).
- **Projekt `djcms/`** (Django 6.1, django CMS 5.1.3, osobny obraz i usługa `djcms` w profilu
  compose'a, osobna baza `olimpiada_djcms` i rola bez dostępu do bazy głównej – `scripts/djcms_db.sh`):
  wtyczki treści odpowiadające blokom Wagtaila, wtyczki danych na żywo z API (bufor 60 s + kopia
  awaryjna 600 s; przy niedostępnym `web` strony odpowiadają 200 z komunikatem), import treści
  z Wagtaila (`import_cms_bundle`), CSP z `nonce`, `noindex` na każdej odpowiedzi, blokada prób
  logowania w bazie.
- **Proxy i wdrożenie** za przełącznikiem: blok `dj.` w Caddyfile, odmowa `/internal/*` w każdym
  bloku publicznym, pliki redaktorów z CSP `sandbox`; `DJCMS_ENABLE=1 scripts/deploy.sh …` włącza
  i generuje sekrety, buduje obraz, zakłada bazę, robi kopię przed migracjami i jednorazowy import.
- **Kopie zapasowe** (DJ-01i): przy `DJCMS_ENABLED=1` nocna kopia obejmuje bazę
  (`djcms-db-<stamp>.dump.gpg`) i wolumen plików redaktorów (`djcms-files-<stamp>.tar.gpg`) –
  szyfrowanie, wysyłka poza serwer, retencja jak kopia główna; awaria po stronie `dj.` nie zatrzymuje
  kopii głównej, ale kończy przebieg meldunkiem `--failed`. Cotygodniowy test odtwarzania sprawdza
  bazę djcms (`cms_page` > 0) i czytelność paczki plików. `scripts/restore.sh --djcms-dump …
  [--djcms-files …]` odtwarza do nowej bazy (właściciel `olimpiada_djcms`, bez `CONNECT` dla
  PUBLIC) i nowego katalogu, z wypisanymi poleceniami podmiany (`OPERACJE.md` § 2.4). Baza
  odtworzeniowa aplikacji głównej też traci `CONNECT` dla PUBLIC zaraz po `createdb`.
- **Import paczki** (`import_cms_bundle --from-api` i `-`): plik tymczasowy na wolumenie
  `djcms_media` zamiast `/tmp` kontenera (tmpfs 64 MB przy limicie paczki 500 MB), bez nazwy
  w katalogu mediów; import przerywa się czytelnym błędem, zanim dysk (wspólny z bazą) zejdzie
  poniżej 256 MB wolnego miejsca.
- `dj` jest zarezerwowanym slugiem konkursu. Nowe zmienne `.env`: `DJCMS_ENABLED`,
  `DJCMS_SECRET_KEY`, `DJCMS_DB_PASSWORD`, `DJCMS_INTERNAL_TOKEN`, `DJCMS_MAIN_PUBLIC_URL`,
  opcjonalnie `DJCMS_COMPETITION_SLUG` (usunięte w DJ-02k), `DJCMS_IMAGE`, `EXTRA_CA_FILE` (dev).
- Testy: `docker compose exec -T djcms pytest -q` (job `djcms` w CI), `test_live_data.py`,
  `test_djcms_api.py`, `test_export_bundle.py`; skrypty: `render_caddyfile_test.sh`,
  `compose_profiles_test.sh`, `djcms_db_test.sh`, `deploy_djcms_test.sh`,
  `backup_offsite_test.sh` (przypadki 12–15; z `BACKUP_BASELINE_REF=<rewizja>` porównanie
  przebiegu bez `dj.` ze skryptami sprzed zmiany).

## v0.37.2 – 2026-09-25 – Kopie zapasowe na Dysku Google

Prośba organizatora z 25.09.2026: nocna kopia (`scripts/backup.sh`) może wyjeżdżać poza serwer na
**Dysk Google konta Fundacji** (Workspace `qaif.org`) – obok dotychczasowego kubełka S3. Dziś
produkcja robi kopię wyłącznie lokalną. Bez migracji, bez przebudowy obrazu; po wdrożeniu
organizator raz wykonuje `OPERACJE.md` § 1.6.3 (albo `PODRECZNIK-ADMINISTRATORA.md` § 6.4).

- **Wybór miejsca** `BACKUP_REMOTE_TYPE=s3|drive|none` (bez niej: s3 przy `BACKUP_REMOTE_URL`,
  drive przy tokenie Dysku, inaczej none). Wspólny kod wysyłki i ściągania:
  `scripts/lib/backup_offsite.sh`.
- **Dysk Google przez rclone z zakresem `drive.file`** – rclone widzi wyłącznie pliki, które sam
  założył (folder `Olimpiada-kopie-zapasowe/` z `daily/` i `monthly/`, zakłada go rclone). Token
  w `/opt/olimpiada/secrets/rclone/rclone.conf` (`600`, katalog `700`), bo rclone zapisuje tam
  odświeżony token – świadomy wyjątek od „sekrety tylko w `.env`”; krok 2/8 wdrożenia omija
  `secrets/`. Opcjonalnie: własny klient OAuth, dysk współdzielony, inny folder.
- **`scripts/backup.sh --drive-token`** – token ze stdin (zalecane: `rclone authorize` na serwerze
  przez tunel SSH, wynik potokiem prosto do pliku – token nie pojawia się na ekranie);
  **`--offsite-test`** – plik próbny: zapis, lista, odczyt, kasowanie, bez czekania na noc.
- **Weryfikacja wysyłki** `rclone check --one-way` (rozmiar + MD5/ETag) dla S3 i Dysku.
- **Nieudana wysyłka = nieudana kopia**: kod 1, meldunek `--failed` z notatką (watchdog alarmuje
  po 36 h), kopia lokalna zostaje, retencja zdalna nie rusza. Wcześniej błąd wysyłki S3 przerywał
  skrypt przed meldunkiem i przed retencją lokalną.
- **Retencja** 30 dni / 365 dni (`REMOTE_DAILY_KEEP_DAYS`, `REMOTE_MONTHLY_KEEP_DAYS`, wartości
  < 1 odrzucane), na Dysku bez kosza (`BACKUP_DRIVE_USE_TRASH=false`, uzasadnienie w § 1.6.5).
- **`scripts/restore.sh --fetch <plik>`** – ściągnięcie paczki z S3 albo Dysku (z `daily/` lub
  `monthly/`) i sprawdzenie sumą; `--list` pokazuje też kopie na Dysku.
- **Aplikacja**: `record_backup_status --ok --offsite`, znacznik `backup:last_offsite_at`,
  `/status.json` z nowym ostatnim kluczem **`backup_offsite`** (wartość logiczna), wiersz w `--show`
  i alarm watchdoga `backup-offsite` („kopia przestała wyjeżdżać poza serwer” – tylko gdy kopia
  zdalna kiedyś działała, a lokalna jest świeża).
- Testy: `scripts/tests/backup_offsite_test.sh` (66 sprawdzeń na atrapach docker/gpg),
  `scripts/tests/backup_offsite_e2e_test.sh` (prawdziwy obraz `rclone/rclone:1.69`, lokalny
  katalog w miejscu Dysku), pytest: `test_backup_status`, `test_alerts`, kontrakt `/status.json`.

## v0.37.1 – 2026-09-25

`scripts/deploy.sh`: zmienne przekazywane przez SSH do kroku 4/8 są cytowane (`printf %q`) – domyślny
komunikat strony „Prace techniczne” („Aktualizacja serwisu.”) zawiera spację, którą powłoka zdalna
rozcinała na dwa słowa, więc pierwsze wdrożenie v0.37.0 zatrzymało się na początku kroku 4/8 (serwis
działał dalej na v0.36.1; przypięcie PostgreSQL 16 wykonane ręcznie przed ponownym wdrożeniem).

## v0.37.0 – 2026-09-25

Wydanie infrastrukturalne z 25.09.2026 – cztery zmiany, żadna nie zmienia zachowania aplikacji dla
użytkownika, żadnej migracji: **Python 3.14** (obraz do przebudowy), **PostgreSQL 18** (kod gotowy,
samo wdrożenie zostawia bazę na 16 – przejście to osobny krok operatora), **strona „Prace
techniczne”** (prośba organizatora) i **szybsza, mniej krucha suita testów** z nowym podziałem
w CI. Przy okazji: **9 nowych angielskich tematów listów** – warianty z marką konkursu
(`*_SUBJECT_TEMPLATE`, używane przy `competition_branding_in_mail`) nie miały dotąd tłumaczenia
i przy języku EN szły po polsku.

Kolejność na produkcji (szczegóły: `OPERACJE.md` § 20.5, § 21.2, § 19.4):
1. `scripts/deploy.sh` **bez** `--maintenance` – przebudowa obrazu na 3.14, jednorazowe odtworzenie
   proxy (montaż `maintenance/`, `MAINTENANCE_BYPASS_TOKEN`), baza zostaje na 16 (przypięcie
   w `.env`);
2. kontrole po wdrożeniu: `python -VV` w `web`, `scripts/maintenance.sh status` i próba on/off;
3. osobno, w oknie serwisowym: `scripts/upgrade_postgres18.sh` (strona prac technicznych włączana
   i wyłączana przez skrypt), potem kopia i `backup_verify.sh` na 18.

**Po przejściu na PostgreSQL 18 nie wdrażaj kodu sprzed v0.37.0** – jego `docker-compose.yml` nie
zna `POSTGRES_IMAGE`/`POSTGRES_VOLUME` i postawi 16 na starym `pg_data` (stan sprzed przejścia).
Powrót aplikacji wyłącznie tagiem obrazu (`APP_VERSION`) albo `WEB_IMAGE=…` z kodem v0.37.0+
(`OPERACJE.md` § 11.3, § 21.3).

### Python 3.14

Interpreter 3.12.14 → **3.14.7**: obraz `python:3.12-slim-bookworm` → `python:3.14-slim-trixie`
(Debian 13 – bieżąca baza oficjalnych obrazów; na niej stoi też tag `3.14-slim`), CI
`PYTHON_VERSION` 3.14, `requires-python = ">=3.14"`, ruff `target-version = "py314"`. Wszystkie
zależności mają koła dla CPythona 3.14 (rozwiązanie zależności na 3.12 i 3.14 identyczne,
żadnego budowania ze źródeł); podniesione dolne granice: `psycopg[binary,pool]>=3.2.10` (pierwsze
koła `cp314`), `ruff>=0.12` (pierwszy, który zna `py314`). Kod: dwie adnotacje bez cudzysłowów
(PEP 649) i `ruff format` pod `py314` – `except (A, B):` → `except A, B:` (PEP 758, 34 linie,
znaczenie bez zmian). Bez migracji i bez zmian zachowania. Wdrożenie: zwykłe `scripts/deploy.sh`
(przebudowuje obraz); powrót: poprzedni tag obrazu – `OPERACJE.md` § 21.

### PostgreSQL 18

Baza: `postgres:16-alpine` → `postgres:18-alpine` (18.6), zrzutem i odtworzeniem do **nowego**
wolumenu `pg18_data` (obraz 18 ma PGDATA `/var/lib/postgresql/18/docker` i VOLUME
`/var/lib/postgresql`); stary wolumen `pg_data` zostaje nietknięty jako droga powrotu. Kod
aplikacji bez zmian, zero migracji; pełny zestaw testów na 18.6 z produkcyjnym `command` i locale –
6152 passed. `docker-compose.yml`: obraz i wolumen usługi `db` z `POSTGRES_IMAGE` /
`POSTGRES_VOLUME` (domyślnie 18 na `pg18_data`), `max_locks_per_transaction=256` bez zmian.
**Samo wdrożenie niczego w bazie nie zmienia:** `scripts/deploy.sh` (krok 4/8, jako pierwsza czynność –
przed buildem, żeby nieudany build nie zostawił serwera z nowym compose bez przypięcia) wpisuje
do `.env` przypięcie do 16, gdy serwer ma `pg_data` bez `pg18_data`. Przejście to osobna czynność operatora:
`scripts/upgrade_postgres18.sh` (kontrole wstępne, `--dry-run`, zrzuty klientem 16 i 18 do
`/opt/olimpiada-backups/pg18-upgrade-*/`, porównanie liczby wierszy każdej tabeli, sekwencji,
rozszerzeń, ról i obiektów 16 ↔ 18, `ANALYZE`, `/status.json`; błąd przed przełączeniem sam wraca na
16), wycofanie `--rollback --yes`, stan `--status`. Przestój 2–5 min (próba lokalna: 1 min 8 s).
Nowy klaster ma sumy kontrolne stron (`data_checksums = on`, domyślne w 18); porządek sortowania
(musl, bajtowy) i `pg_trgm` 1.6 – bez zmian. `backup_verify.sh` stawia tymczasowy Postgres
w wersji z `.env` (domyślnie 18) i montuje tmpfs tam, gdzie obraz deklaruje VOLUME; CI testuje na
`postgres:18-alpine`; `e2e.sh` (reset) kasuje `pg18_data` i `pg_data`. Zrzut `-Fc` z produkcji na 18
nie da się odtworzyć `pg_restore` 16 – lokalne środowisko też przechodzi na 18 (`OPERACJE.md`
§ 19.7). Runbook, próba generalna, wycofanie i skasowanie `pg_data` po 14 dniach: `OPERACJE.md` § 19.

### Strona „Prace techniczne”

Prośba organizatora z 25.09.2026: zamiast gołego 502 z Caddy'ego – strona „Prace techniczne – serwis
wróci za kilka minut” (`deploy/maintenance/index.html`: PL + zdanie EN, contact@qaif.org, logo
i CSS w pliku, zero zewnętrznych żądań, jasny/ciemny motyw, odświeżanie co 45 s). Podaje ją Caddy
(fragment `(maintenance)` w `deploy/Caddyfile`, `import` w bloku domeny głównej, w domenach
z `EXTRA_DOMAINS` i w bloku `*.` – nie w `meet.`, `monitor.` ani S3): **503**, `Retry-After: 60`,
`Cache-Control: no-store`, własne CSP; dla `/status.json`, `/healthz/`, `/api/*` – JSON
`{"status":"maintenance"}`. Tryb planowy: flaga `/opt/olimpiada/maintenance/on`
(`scripts/maintenance.sh on|off|status [--message] [--until]`, bez przeładowania proxy), z wyjątkiem
wyzwania ACME i operatora z przepustką `MAINTENANCE_BYPASS_TOKEN` (nagłówek `X-Maintenance-Bypass`
albo ciasteczko z `/__maintenance/bypass?token=`; token generuje `deploy.sh`). Tryb nieplanowy:
`handle_errors` dla 502/503/504 z upstreamu – także krótka przerwa przy restarcie `web` w zwykłym
wdrożeniu. `scripts/upgrade_postgres18.sh`: wymuszona kolejność (wymóg organizatora) – kontrole →
strona włączona → stop aplikacji i zero klientów bazy (maruderzy rozłączani) → zrzuty końcowe (czas
startu > włączenia strony, SHA-256, stan 16 niezmieniony w trakcie) → odtworzenie **tego** zrzutu
(SHA-256 przed `pg_restore`) → porównanie z zatrzymaną 16 → start i kontrole przez proxy
z przepustką → strona wyłączona; po błędzie strona zostaje włączona (ramka `!!!`), `timeline.txt`
z czasem każdego etapu, `--no-maintenance`. `scripts/deploy.sh --maintenance`: strona na czas
kopii przed migracjami (dopiero po zatrzymaniu aplikacji), migracji i podmiany kontenerów; bez flagi
bez zmian poza tym, że krok 2/8 omija katalog `maintenance/`, a krok 4/8 dopisuje token i kopiuje
stronę. Testy: `scripts/tests/render_caddyfile_test.sh` (zakres importu, `caddy validate`,
kolejność tras po `caddy adapt`), `scripts/tests/maintenance_pg18_rehearsal.sh` (próba na stosie
compose, 44/44 z wycofaniem `--rollback`). Caddy 2.8.4: `handle_errors` z listą kodów nadpisuje
zagnieżdżone matchery – stąd matcher kodu w środku. Ramka „strona włączona” po błędzie
`deploy.sh --maintenance` tylko wtedy, gdy flaga na serwerze naprawdę istnieje. `OPERACJE.md` § 19.4 (kolejność), § 20; `PODRECZNIK-ADMINISTRATORA.md` § 5.1.

### Testy i CI

Pełny przebieg w jednym procesie: 40:31 → ok. 20 min; `-n auto` (xdist) kilka minut; szybka pętla
`-m "not slow"` ok. 3 min (`docs/TESTY.md`). Testy migracji (70 % czasu) przewijają bazę raz na
moduł w jednej transakcji wycofywanej na końcu (`apps/core/tests/migration_helpers.py`, marker
`migrations`, każdy test w savepoincie) zamiast migrate + flush na każdy test; po teście
transakcyjnym baza wraca z migawki z początku sesji (Konkurs #1 i drzewo stron nie znikają,
wynik nie zależy od kolejności); ClamAV podstawiony na poziomie gniazda (prawdziwy tylko
z markerem `clamav`); `.mo` kompiluje sam pytest; testy AI bez SDK dostawców. Asercje per
element zamiast ręcznie liczonych sum: tematy listów wykrywane w kodzie (3 nowo zamrożone:
status ucznia przyjęty/odrzucony, zaproszenie ucznia przez nauczyciela; wymóg wersji EN dla
każdego tematu – stąd 9 tłumaczeń wyżej), flagi etapu 2, wersja rejestru czynności, budżety
zapytań w jednej tabeli (`apps/core/tests/query_budgets.py`). CI: `-n auto` w każdym z 5 shardów,
podział `duration_based_chunks` (ciągłe kawałki), Postgres testowy z `max_locks_per_transaction
= 256` i bez `fsync`, bez osobnych kroków `.mo`/`collectstatic`; `backend/.test_durations`
odświeżone. Nazwy wymaganych checków bez zmian. Na tej wersji (Python 3.14 + PostgreSQL 18):
6157 passed, 1 skipped (clamd) z 6158 – `-n auto` (32 workery) 4:53; podział jak w CI
(5 shardów × `-n 4`, `duration_based_chunks`): 888 + 128 + 464 + 1844 + 2833 passed + 1 skipped,
shardy 3:12–4:07.

## v0.36.1 – 2026-09-25

Zadania treningowe: każde z czterech zadań (P1–P4) ma własny plik z treścią
(`apps/competitions/fixtures/training/zadanie-P1.pdf` … `zadanie-P4.pdf`) zamiast wspólnego arkusza
`zadania-przykladowe.pdf` (nowe pliki organizatora z 25.09.2026, m.in. nowy rysunek funkcji falowej
w P1). Tytuły bez zmian. Na produkcji: `manage.py seed_training_problems` podmienia treść
wszystkich czterech zadań.

Postgres: `max_locks_per_transaction` 64 → 256 (`docker-compose.yml`, usługa `db`, decyzja
organizatora) – migracje i operacje hurtowe na wielu tabelach nie kończą się już „out of shared
memory”. Wdrożenie restartuje kontener bazy (kilkanaście sekund przerwy).

## v0.36.0 – 2026-09-25

Wydanie zbiorcze z próśb organizatora i długu technicznego z 25.09.2026 – sześć zmian. Bez flagi
i od wdrożenia działają: **responsywne tabele**, **ułamki w rubrykach i teście** (za istniejącym
przełącznikiem etapu „dowolna wartość”, czyli dla etapu, którego nikt nie przełączył, bez zmian),
**pula połączeń z Postgresem** z alarmem zajętości i **reset hasła w tle**. **Drzewo CMS konkursu
pod prefiksem** dotyczy wyłącznie konkursów `routing_mode=PATH` (produkcja z jednym konkursem – bez
zmian co do bajtu). **Uprawnienia `/cms/` per konkurs i superkoordynator** nie zmieniają niczego do
chwili uruchomienia dwóch komend operatora. **Powiadomienia z forum** działają wyłącznie w konkursie
z włączoną flagą `participant_forum` (domyślnie wyłączona).

Migracje: `grading.0012_rubric_decimal_points`, `ai_grading.0003_points_precision` (obie bezstratne,
małe tabele – `OPERACJE.md` § 18.4), `tenancy.0010_path_prefix_routing_on_platform` (dane; baza bez
konkursu `PATH` – bez zmian), `forum.0002_forum_notifications` (4 nowe tabele). Nowa zależność:
ekstra `pool` przy `psycopg` (`psycopg[binary,pool]` → `psycopg-pool`) – **obraz trzeba
przebudować**. Nowe zadania beat: `forum-notifications` (co 2 min) i `forum-daily-digest`
(codziennie o `FORUM_DAILY_DIGEST_HOUR_UTC`, domyślnie 5:00 UTC). `send_mail_task` ma dwa nowe
opcjonalne argumenty (`html_message`, `headers`), których stary worker nie zna – **`web`, `worker`
i `beat` wdrażać razem** (to jeden obraz). Rejestr czynności przetwarzania **1.9** (wiersz forum:
nowy odbiorca – dostawca poczty – i nowa kategoria danych, bez nowego celu).

Kroki operatora po `scripts/deploy.sh`, w tej kolejności: sprawdzenie puli (`OPERACJE.md` § 11.2:
`import psycopg_pool`, `manage.py db_connections`, `/healthz/`), potem § 6.7: `superkoordynator
--all-current-coordinators` (`--dry-run`, właściwe, `--list`) → `scope_cms_access --dry-run` →
`scope_cms_access` → drugi `--dry-run` z wynikiem „bez zmian”.

### Responsywne tabele (bez flagi)

Prośba organizatora z 25.09.2026: **„Popraw responsywność, szczególnie w panelu koordynatora, jeśli
chodzi o tabele”**. Bez migracji, bez zmian w widokach i bez flagi – zmiana dotyczy arkusza
(`app.css` § 6.4), szablonów i jednego nowego skryptu (`js/table-scroll.js`, plik zewnętrzny
z nonce jak pozostałe; CSP bez zmian). Wzorzec i wybór między przewijaniem a kartami:
[`UI.md`](UI.md) § 2.10.

- **Kolumny nie są już ściskane do pojedynczych liter.** `overflow-wrap: anywhere` w komórkach
  zerował minimalną szerokość kolumny, więc tabela nigdy nie sięgała po przewijanie: lista kont
  na 1024 px miała wiersze po 361 px (e-mail i imię łamane co kilka liter), przydziały na 360 px –
  po 1900 px. W ramce `.scroll` kolumna nie schodzi poniżej najdłuższego słowa, a ciąg bez spacji
  ma sufit `--cell-max`. Tabela, która się i tak nie mieści, dostaje kolumny w szerokości treści
  i przewija się w ramce (listy kont i komitetu, macierz, ranking, przydziały – modyfikatorem;
  pozostałe – przez skrypt, `data-fit="wide"`). Lista kont na 1024 px: 361 → 98 px na wiersz.
- **Poziomego suwaka strony nie ma na żadnej ze sprawdzonych 72 stron** (360, 390, 768, 1024,
  1280 px). Naprawione źródła: tabele bez ramki (zgłoszenia problemów, komunikaty w serwisie,
  `/status/`), `.visually-hidden` w komórce liczone od dokumentu zamiast od ramki (przydziały 1211 px,
  karta uczestnika 577 px, karta zadania 783 px przy ekranie 360 px), niejawna kolumna siatki na
  `/warsztaty/` (491 px), e-mail w `<h1>` edycji konta (658 px na 360, 784 px na 768), długa odznaka
  w nagłówku karty dostawcy AI (387 px).
- **Macierz obecności na warsztatach** (`.table--matrix`): nagłówki-tematy łamią się w kolumnie
  7–11 rem zamiast stać w jednej linii – tabela z 17 warsztatami 9487 → 3297 px, kolumna uczestnika
  przyklejona i na telefonie nie szersza niż 45 % ekranu (556 → 162 px).
- **Przyklejona pierwsza kolumna** (`.table--sticky-first`) na listach kont i uczestników, komitetu,
  kalibracji, obecności i przyjazdów etapu, wynikach testu; **ranking** (`.table--sticky-rank`)
  trzyma miejsce i uczestnika na podglądzie wyników i w publicznych wynikach (`/results/…`, strona
  wyników w CMS). Nagłówek kolumn klei się w ramkach `.scroll--tall` (listy kont, komitet, macierz,
  obecność, podgląd wyników, przydziały).
- **Karty na telefonie** (`.table--stack` + `data-label`, poniżej 640 px): dziennik zdarzeń,
  zgłoszenia problemów, komunikaty w serwisie, wysłane komunikaty, zgody konkursu, zaświadczenia
  o statusie ucznia.
- **Ramka jest regionem** z nazwą i przystankiem klawiatury: w kluczowych tabelach panelu
  (`role="region" tabindex="0" aria-label`) wprost w szablonie, w pozostałych – ze skryptu,
  który przystanek dokłada **tylko** tam, gdzie ramka naprawdę się przewija. Krawędź, za którą są
  jeszcze kolumny, wygasa (`data-scroll`, maska – działa w trybie ciemnym i wysokiego kontrastu).
- `<th scope="row">` w treści tabeli wygląda jak nazwa wiersza, a nie jak drugi rząd nagłówków
  kolumn, i nie klei się do góry ramki; daty, liczby, kody i odznaki w tabelach nie łamią się
  w środku wartości (`.nowrap`, `time`, `.num`, `.code-chip`, `.badge`).
- Wydruk: tabela wraca do szerokości kartki, bez maski i przyklejonych kolumn.

### Ułamki w rubrykach i teście (za przełącznikiem etapu)

Dopełnienie wydania 0.35.0: przełącznik etapu **„dowolna wartość od min do max (co 0,01)”**
obejmuje teraz także **rubryki oceniania** i **test online**, które dotąd liczyły w pełnych punktach.
Etap „tylko ze skali” – czyli każdy, którego organizator nie przełączył – zachowuje się jak dotąd.

Migracje: `grading.0012_rubric_decimal_points` (`RubricCriterion.max_points` `smallint` →
`numeric(7,2)`, więz „maksimum dodatnie” jako `> 0`), `ai_grading.0003_points_precision`
(`AiAssessment.proposed_points`/`max_points` `numeric(6,2)` → `numeric(7,2)`, jak maksimum zadania);
obie bezstratne, na małych tabelach – `OPERACJE.md` § 18.4.

- **Rubryka w etapie dowolnym**: maksimum kryterium może być ułamkiem (`2,5;Pomysł`, 0,01–1000),
  a recenzent wpisuje punkty za kryterium co 0,01 z przecinkiem albo kropką (pole tekstowe
  z klawiaturą dziesiętną, zakres pod polem, licznik sumy „4,25 pkt” z werdyktem „w zakresie / poza
  zakresem zadania”). Punkty kryterium sprawdza jedna reguła – `competitions.scoring.criterion_rule`,
  ta sama klasa `ScoreRule`, co ocena zadania; czyta je `grading.rubric.criterion_points` dla API,
  szkicu i formularza panelu. Odmowy jak dotąd: `INVALID_RUBRIC` (kształt, także trzy miejsca po
  przecinku), `RUBRIC_POINTS_OUT_OF_RANGE`, `RUBRIC_TOTAL_NOT_IN_SCALE` (suma dokładna, bez
  zaokrąglania). `Review.rubric[].points` to liczba JSON: `int`, gdy całkowita, inaczej `float`
  o ≤ 2 miejscach (`points_json`). W etapie skali ułamek przy kryterium odpada jak dotąd,
  a ułamkowe maksimum w formularzu zadania – z wyjaśnieniem, że wymaga trybu dowolnego.
- **Powrót etapu do trybu skali** jest odmawiany (`409 FREE_VALUES_IN_USE`) także wtedy, gdy któreś
  kryterium rubryki ma ułamkowe maksimum (nowy licznik `criteria` w `free_values_blockers`).
- **Test online słucha przełącznika etapu**: `quiz.services.stage_scores` oddaje wynik najlepszego
  podejścia jako `Decimal` – w etapie skali zaokrąglony do pełnych punktów, połówka w górę (jak
  dotąd), w etapie dowolnym co 0,01 – do tej samej kolumny `StageEntry.total_points`, tabeli
  wyników, snapshotu i komponentu testowego. Każde zaokrąglenie w teście idzie przez
  `apps.core.points.round_points` (`ROUND_HALF_UP` do 0,01): kwota za pytanie z oceną częściową
  (dotąd bankierskie `ROUND_HALF_EVEN` – 0,125 dawało 0,12, teraz 0,13; zmienia się wyłącznie przy
  ponownej ocenie podejścia) i suma podejścia.
- **Punkty pytania z przecinkiem**: edytor pytań (`PointsField`, „0,5”, najwyżej dwa miejsca;
  dotąd `forms.DecimalField` odrzucał przecinek) i import (`[pkt: 0,5]`; trzecie miejsce po
  przecinku to błąd wiersza, a nie ciche zaokrąglenie w bazie).
- **Wyświetlanie filtrem `points`**: maksimum i punkty kryterium (panel recenzenta, karta zadania,
  formularz rubryki bez „4.00”), punkty pytań, maksimum testu, wynik podejścia i tabela wyników testu
  („0,5”, a nie „0,50”). Prompt oceny AI podaje maksimum kryterium bez zbędnych zer (`2.5`, `4`) –
  tekst dla rubryk całkowitych nie zmienia się ani o znak (pamięć podręczna promptu zostaje).
- **Eksporty**: CSV wyników testu z kropką i bez zbędnych zer (`7.5`, `3` zamiast `7.50`, `3.00`);
  eksport danych uczestnika (art. 15/20 RODO) niesie `proponowane_punkty` i `maksimum` sugestii AI
  jako liczby JSON (`6`, `4.5`) zamiast tekstu z kolumny („6.00”), jak `suma_punktow` obok.

### Pula połączeń i reset hasła w tle (bez flagi)

Dwie pozycje długu technicznego z `BACKLOG.md`. Bez migracji. Nowa zależność: ekstra `pool` przy
`psycopg` (`psycopg[binary,pool]` – dociąga `psycopg-pool`), więc **obraz trzeba przebudować**.
`web`, `worker` i `beat` wdrażać razem (to jeden obraz): `send_mail_task` ma nowy argument
`html_message`, którego stary worker nie zna. Kroki operatora: `OPERACJE.md` § 11.2.

#### Pula połączeń z Postgresem i alarm zajętości

- **Pula psycopg w `web`** (`DATABASES["default"]["OPTIONS"]["pool"]`, reguły w
  `backend/config/dbpool.py`): jedna pula na proces gunicorna, `DB_POOL_MAX_SIZE` domyślnie
  `WEB_THREADS`, `DB_POOL_MIN_SIZE=1`, `DB_POOL_TIMEOUT=10`. Przy puli `CONN_MAX_AGE` jest zawsze
  0 (wymóg Django), a `DB_CONN_MAX_AGE` działa już tylko w procesach bez puli.
- **`worker` i `beat` bez puli** (`DB_POOL=0` w `docker-compose.yml`, a ustawienia rozpoznają proces
  Celery także same): Celery 5.6 w workerze `prefork` zamyka pulę przed i po każdym zadaniu.
- **`application_name` per usługa** (`DB_APPLICATION_NAME`: `olimpiada-web`, `olimpiada-worker`,
  `olimpiada-beat`) – w `pg_stat_activity` widać, kto trzyma połączenia.
- **Alarm zajętości** (`apps/core/dbconnections.py`): jedno zapytanie do `pg_stat_activity`,
  buforowane 30 s. `/healthz/` i `/status.json` dostają pole `db_connections`
  (`ok|warn|critical|unknown`, wyłącznie poziom – bez liczb; `/status.json` dokłada je jako ostatni
  klucz, a kod `/healthz/` się nie zmienia). Watchdog pisze na `ALERT_EMAILS` od 80 %
  (`db-connections:warn`) i od 95 % (`db-connections:critical`) z podziałem na usługi i stany.
  Nowa komenda `manage.py db_connections` (kod wyjścia = poziom). Progi:
  `DB_CONNECTIONS_WARN_PERCENT`, `DB_CONNECTIONS_CRITICAL_PERCENT`.
- Budżet przy domyślnych wartościach: ok. 20 połączeń aplikacji ze 100 (`web` 4×4 z puli, `worker`
  2, `beat` 1) – `max_connections` bez zmian.

#### Reset hasła w tle

- `POST /password-reset/` i `/coordinator/accounts/<pk>/password-reset/` nie wysyłają już listu
  w żądaniu: `QueuedPasswordResetForm` (`apps/accounts/password_reset.py`) renderuje ten sam list
  (szablony, kontekst, token – bez zmian) i kolejkuje go w `send_mail_task` na kolejce `mail` po
  commicie. Treść, temat, nadawca i część HTML są identyczne z listem wysyłanym dotąd (test porównuje
  oba). Odpowiedź, limit `password_reset` i brak enumeracji kont bez zmian – a czas odpowiedzi nie
  zależy już od rozmowy z MTA; błąd brokera jest połykany i logowany po kluczu konta.
- `send_mail_task` przyjmuje opcjonalne `html_message` (alternatywa `text/html`).

### Drzewo CMS konkursu pod prefiksem (uwaga T43)

Zamyka uwagę T43: konkurs adresowany prefiksem ścieżki (`https://<platforma>/<prefiks>/…`,
`routing_mode=PATH`) ma pod prefiksem **własne drzewo stron CMS**. Do tej pory Wagtail wybierał
witrynę po hoście, więc pod `/<prefiks>/` serwował stronę główną, menu i dokumenty konkursu-gospodarza.
Konkurs z własną domeną (Olimpiada Kwantowa) – bez zmian co do bajtu (testy złote, budżety zapytań).

- **Serwowanie:** `CompetitionMiddleware` pod prefiksem podstawia witrynę konkursu jako witrynę żądania
  (`request._wagtail_site`) – strony, menu, `SiteSettings`, przekierowania i analityka pochodzą z jego
  drzewa. Strona gospodarza pod prefiksem = 404, strona konkursu pod prefiksem bez prefiksu = 404.
- **Adresy stron:** `Page.get_url_parts` opakowany w `apps/tenancy/page_urls.py` – `pageurl`,
  `page.url`, `full_url`, podgląd i „Zobacz na żywo” w `/cms/` niosą prefiks **strony** (a nie
  żądania) i adres platformy zamiast domeny, na którą konkurs czeka. Mapa „witryna → prefiks”
  w pamięci podręcznej (unieważniana sygnałami), czytana wyłącznie przy linkach między witrynami
  i bez żądania; instalacja z jedną witryną nie płaci nic.
- **`path_prefix_routing` ma czytelnika:** jest bramką **gospodarza** – prefiks rozstrzyga konkurs
  wyłącznie pod hostem, którego konkurs ma tę flagę (ciasteczka sesji są wtedy wspólne, więc to jego
  wybór). Bez dodatkowego zapytania. `create_competition --path-prefix` włącza ją konkursowi witryny
  domyślnej (i mówi o tym w wydruku; bez konkursu platformy – odmowa), migracja danych
  `tenancy.0010_path_prefix_routing_on_platform` robi to na bazach, w których konkurs `PATH` już stoi
  (baza z jednym konkursem – bez zmian). Prefiks pod domeną innego konkursu przestaje działać.
- **Przekierowania** (`apps.cms.redirects.CompetitionRedirectMiddleware` zamiast warstwy Wagtaila):
  pod prefiksem dopasowanie po adresie bez prefiksu, cel względny dostaje prefiks, bezwzględny bez zmian.
- **Nie wyprowadza z prefiksu:** logo, „Strona główna”, linki RODO/cookies w stopce i stronach błędów
  (`site_root` z procesora `apps.tenancy.context_processors.competition`), domek w menu, pasek
  harmonogramu, `LOGIN_URL`/`LOGIN_REDIRECT_URL`/`LOGOUT_REDIRECT_URL` (leniwe `reverse`),
  wylogowanie, odnośniki zgód do dokumentów (teraz z drzewa **tego** konkursu), linki w listach spoza
  żądania (`https://<platforma>/<prefiks>/…`).
- **Pamięć podręczna:** klucz pamięci stron anonimowych i klucz paska harmonogramu dostają człon
  prefiksu – wyłącznie pod prefiksem, więc klucze konkursu z domeną są te same.
- Testy `apps/tenancy/tests/test_path_prefix_cms.py`; runbook `OPERACJE.md` § 6.6 i flaga w § 6.4;
  `UNIWERSALNY-ETAP-2.md` § 3.1 (uwaga T43 zamknięta).

### Uprawnienia `/cms/` per konkurs i superkoordynator (bez migracji, komendy operatora)

Domknięcie luki z `UNIWERSALNY-ETAP-2.md` § 1.1.5 („grupa `coordinator` jest globalna”) i prośba
organizatora o **superkoordynatora**. Koordynator konkursu A redaguje w `/cms/` wyłącznie strony,
obrazy i dokumenty konkursu A (grupa `cms:<slug>`: prawa na korzeniu witryny i na kolekcji
konkursu), a okna wyboru stron/obrazów/dokumentów/komunikatów, wyszukiwarka, raporty „Zablokowane
strony”, „Starzejące się strony”, „Historia serwisu” (także `ModelLogEntry`), API panelu i lista
komunikatów nie pokazują obiektów konkursu B; raport „Użycie typów stron” przy kilku witrynach jest
dla redaktora z ograniczeniami zamknięty. Nowa rola platformy **superkoordynator** (grupa
`superkoordynator`): koordynator każdego konkursu w `/coordinator/` (przełącznik „Konkursy platformy”
w menu) i całe `/cms/`, bez `/admin/`; `manage.py superkoordynator --grant|--revoke|--list|
--all-current-coordinators [--dry-run]` i akcje w `/admin/` dla superużytkownika, z wpisem audytu.
`manage.py scope_cms_access` działa teraz na całą instalację (bez `--competition`): zakłada grupy
i kolekcje, przenosi media z korzenia kolekcji do kolekcji konkursu (`--root-media-to` przy kilku
konkursach), zabiera grupie `coordinator` uprawnienia `/cms/` i porównuje macierz możliwości każdego
koordynatora przed i po — przy jednym konkursie różnica wycofuje całość. **Bez migracji.** Do
chwili uruchomienia komend nic się nie zmienia. Kroki operatora: `OPERACJE.md` § 6.7 —
wdrożenie → `superkoordynator --all-current-coordinators` → `scope_cms_access --dry-run` →
`scope_cms_access` → sprawdzenie.

### Powiadomienia z forum (flaga `participant_forum`)

Prośba organizatora z 25.09.2026: forum (flaga `participant_forum`) dostaje powiadomienia e-mail,
**zbiorcze i z limitami** – żaden list nie wychodzi „za wpis”. Trzy rodzaje: **list o kolejce moderacji**
do koordynatorów tego konkursu (pierwszy po 10 min czekania najstarszej pozycji, kolejne najwyżej co
3 godz., same liczby i odnośnik), **nowe odpowiedzi w obserwowanych wątkach** (automatyczna obserwacja po
napisaniu, przyciski „Obserwuj wątek” / „Przestań obserwować”, najwyżej jeden list o wątku co 4 godz.,
osobny temat, gdy odpowiedział organizator albo komitet) i **decyzja moderatora** o wpisie z kolejki
(zatwierdzenie zbiorcze = jeden list na autora, odrzucenie z uzasadnieniem). Listy **nie niosą treści
wpisów** ani niczego nieopublikowanego; stan sprawdzany jest w chwili wysyłki; konta nieaktywne,
niepotwierdzone, zanonimizowane i bez roli w konkursie nie dostają nic. Ustawienia konta na ekranie
„Edycja danych” (na bieżąco / raz dziennie / nigdy, u koordynatora także listy o kolejce), wypis jednym
kliknięciem bez logowania w każdym liście (podpisany token, `List-Unsubscribe` + `List-Unsubscribe-Post`,
RFC 8058). Tematy z prefiksem konkursu (`[Olimpiada Kwantowa] Forum: …`), język odbiorcy. Nowe:
migracja `forum.0002_forum_notifications` (4 tabele), zadania beatu `forum-notifications` (co 2 min)
i `forum-daily-digest` (codziennie, `FORUM_DAILY_DIGEST_HOUR_UTC`), ustawienia
`FORUM_MODERATION_DIGEST_DELAY_MINUTES` / `_INTERVAL_HOURS`, `FORUM_THREAD_NOTIFY_INTERVAL_HOURS`;
`send_mail_task` i `queue_mail` przyjmują opcjonalne nagłówki (bez nich list idzie dotychczasową drogą).
Rejestr czynności **1.9**: wiersz forum z nowym odbiorcą (dostawca poczty) i kategorią danych – bez
nowego celu przetwarzania.

### Poprawki przy scalaniu wydania

- **Przełącznik „Konkursy platformy” a konkurs pod prefiksem**: link do panelu konkursu `PATH` jest
  bezwzględny, od hosta platformy (`http(s)://<platforma>/<prefiks>/coordinator/`), a nie względny
  `/<prefiks>/coordinator/`, który pod domeną innego konkursu trafiał w host nierozstrzygający
  prefiksu. Przy zamkniętej bramce `path_prefix_routing` konkurs pod prefiksem nie ma adresu i nie
  trafia na listę. Bez dodatkowego zapytania (`select_related("site")`).
- **Ekran „Moje konkursy”** (`/coordinator/competitions/`, u superkoordynatora – wszystkie konkursy)
  linkuje konkurs pod prefiksem tą samą regułą (`apps.web.coordinator_nav.competition_base_urls`),
  a nie pod domeną, na którą konkurs dopiero czeka.
- **Linki w listach konkursu pod prefiksem składanych w żądaniu bez `request`** (np. decyzja
  koordynatora o zaświadczeniu, przekazanie pracy): `absolute_url` nie dokleja prefiksu drugi raz,
  gdy ścieżka z `reverse()` już go niesie (`https://<platforma>/<prefiks>/…`, a nie `/<prefiks>/<prefiks>/…`).
- **`send_mail_task`**: nagłówki `List-Unsubscribe` forum idą do zadania słowem kluczowym
  (`headers=`) – pozycyjnie trafiały w nowy argument `html_message` z resetu hasła. Zadanie składa
  list z nagłówkami i – jeśli podana – częścią HTML naraz.
- **Eksport danych konta** (art. 15/20 RODO) dostaje sekcję `powiadomienia_z_forum`: ustawienia
  powiadomień (albo wartości domyślne, `zmienione: null`), obserwowane wątki ze wszystkich konkursów
  (temat tylko wątku opublikowanego albo własnego) i decyzje moderatora czekające na list.
  **Anonimizacja konta** kasuje ten sam stan (`apps.forum.notifications.erase_for_user`); wpisy
  zostają bez podpisu jak dotąd.
- `OPERACJE.md`: runbook uprawnień `/cms/` i superkoordynatora to **§ 6.7** (oba runbooki miały § 6.6);
  odwołania w README, podręczniku administratora, `UNIWERSALNY-ETAP-2.md` i komendzie
  `superkoordynator` poprawione.

## v0.35.0 – 2026-09-24

Wydanie zbiorcze z dwóch próśb organizatora z 24.09.2026. **Dowolne wartości ocen i różne maksima
zadań** działają bez flagi konkursu, ale za przełącznikiem **przy etapie**, który dla każdego
istniejącego i nowego etapu stoi w dotychczasowym trybie „tylko wartości ze skali” – do chwili
przełączenia żadna liczba, ekran ani odpowiedź API etapu się nie zmienia. **Inni dostawcy AI**
(OpenAI, Google, Meta) i tryb testowy rozszerzają ocenę AI, więc stoją za tą samą, domyślnie
wyłączoną flagą `ai_grading`. Obie zmiany spotykają się w jednym miejscu: propozycję punktów
**każdego** dostawcy serwer przycina do maksimum zadania z reguły oceny
(`competitions.scoring.ScoreRule`, maksimum ułamkowe, np. 12,5), a w etapie z dowolnymi wartościami
przycisk „punkty AI” wpisuje propozycję sprowadzoną do 0,01 zamiast najbliższej wartości skali.

Migracje: `competitions.0032_free_scores`, `grading.0011_decimal_scores`,
`appeals.0003_decimal_new_score` (typ kolumn punktów – przepisanie tabel pod blokadą, `OPERACJE.md`
§ 18), `ai_grading.0002_providers`. Nowe zależności: `openai>=3.19,<4`, `google-genai>=2.25,<3`
(import leniwy; `anthropic>=1.8,<2` bez zmian). Nowa trasa Celery `apps.ai_grading.tasks.scan_ai_test_work`
→ kolejka `scan`. Rejestr czynności przetwarzania **1.8**. Kroki operatora: `OPERACJE.md` § 18
(przed wdrożeniem – liczność tabel, wdrożenie poza godzinami oceniania) oraz § 17.2 (zależności).
**Po wdrożeniu żaden dostawca AI, także Anthropic, nie ma potwierdzonej umowy powierzenia** – przed
kolejną oceną AI prac uczestników koordynator potwierdza ją osobiście w panelu (§ 17.6; komenda
`confirm_ai_provider_dpa` tylko na sytuacje wyjątkowe).

### Dowolne wartości ocen i różne maksima zadań (bez flagi; przełącznik etapu)

Prośby organizatora z 24.09.2026: **„Pozwól na dowolne wartości ocen”** i **„zadania mogą mieć różną
ilość punktów”**. Etap w dotychczasowym trybie „tylko wartości ze skali” wygląda jak przed wydaniem:
oceny całkowite wyświetlają się jak dotąd – „5”, a nie „5,00” – a snapshoty i odpowiedzi API niosą je
jako `int`.

Migracje: `competitions.0032_free_scores`, `grading.0011_decimal_scores`,
`appeals.0003_decimal_new_score` – kolumny punktów `Review.score`, `FinalGrade.score`,
`AppealDecision.new_score`, `InterviewScore.points`, `Problem.max_points` (`numeric(7,2)`),
`StageEntry.total_points`, `QualificationRule.min_points`, `TransitionRule.min_points`
(`numeric(10,2)`); rzutowanie bezstratne, więzy `>= 0` jawnie w miejsce `Positive*`; przepisanie tabel
pod blokadą – szacunek i rollback w `OPERACJE.md` § 18.

- **Przełącznik etapu** na `/coordinator/stages/<id>/scale/` („Jakie oceny wolno wystawić”):
  „tylko wartości ze skali” albo „dowolna wartość od min do max (co 0,01)” (`ScoringScale.free_values`,
  audyt `stage.scale_updated` z trybem przed i po). W trybie dowolnym ocena jest dowolną liczbą między
  najniższą a najwyższą wartością skali zadania (albo etapu) z najwyżej dwoma miejscami po przecinku;
  wartości skali i opisy zostają przy polu jako podpowiedź. Powrót do trybu skali jest odmawiany
  (`409 FREE_VALUES_IN_USE`, z licznikami), dopóki istnieje ocena spoza skali (recenzje, oceny końcowe,
  nowe punktacje z reklamacji, punkty z rozmów) albo zadanie z samym maksimum.
- **Jedna reguła oceny** (`apps.competitions.scoring.score_rule` / `ScoreRule.clean`) dla każdej drogi
  zapisu: recenzja i jej poprawka, szkic (tylko kształt liczby), korekta punktów recenzji, ocena końcowa
  koordynatora, rozstrzygnięcie rozjazdu, trzeci recenzent, reklamacja, punkty z rozmowy, rubryka (suma
  w zakresie), API. Tekst z przecinkiem („4,25”) normalizowany po stronie serwera; trzecie miejsce po
  przecinku to odmowa `SCORE_INVALID`, a nie zaokrąglenie; poza skalą/zakresem – `SCORE_NOT_IN_SCALE`
  (brzmienie w trybie skali bez zmian). Zgodność ocen rundy 1 to nadal równość (4,25 = 4,25; 4,25 ≠ 4,26
  → moderacja) – oceny końcowej nie liczy się średnią.
- **Samo maksimum zadania** (tryb dowolny): formularz zadania przyjmuje „Maksimum punktów tego zadania”
  bez listy wartości, także ułamkowe (np. 12,5); zadanie ocenia się wtedy od 0 do maksimum co 0,01,
  bez przesunięcia skali etapu. Maksima widać w liście zadań (z sumą maksimów etapu), na karcie zadania,
  przy polu oceny recenzenta („Punkty (max 12,5)”), w nagłówkach tabeli wyników („Zad. 3 (max 12,5)”,
  „Razem (max 40)”) i w podglądzie wyników koordynatora.
- **Sumy i progi w `Decimal` od kolumny do tabeli** (bez `int()` i bez `float`): suma etapu dokładna;
  suma **ważona** zaokrąglana raz, na końcu, **połówka w górę** – do 0,01 w etapie z dowolnymi
  wartościami, do pełnego punktu (jak dotąd) w etapie skali (`StageScoring.quantum`, ta sama reguła
  w sumie komponentów). Próg kwalifikacji, reguły przejścia i symulacja przyjmują ułamek („38,5”).
  Test online bez zmian (wynik nadal do pełnych punktów).
- **Wyświetlanie**: jeden filtr `points` (`apps.core.points.format_points`) we wszystkich szablonach –
  „5”, „4,25”, „3,5”, po angielsku z kropką; protokół etapu (PDF) tą samą funkcją. **CSV z kropką**
  dziesiętną niezależnie od języka (maszynowo czytelny), XLSX z liczbami. Recenzent w trybie dowolnym
  ma `<input type="number" step="0.01" min max inputmode="decimal">` z podpowiedzią skali, koordynator
  – to samo pole na przydziałach, karcie zadania, karcie recenzenta i karcie uczestnika
  (`web/coordinator/_score_input.html`); przycisk „punkty AI” wpisuje wtedy propozycję przyciętą do
  zakresu, a prompt oceny AI mówi modelowi o zakresie.
- **JSON i API**: snapshot wyników, `entry_totals`, audyt, zrzut edycji i każde pole punktów API
  (`apps.core.points_api.PointsField`) – liczba JSON: `int` dla całkowitej, liczba z ≤ 2 miejscami dla
  ułamkowej (nigdy tekst `"5.00"`); wejście API przyjmuje liczbę albo tekst z przecinkiem. Ogłoszone
  tabele sprzed wydania nie są przepisywane i renderują się jak dotąd (`API.md` § 6.2).
- Przy okazji: ekran recenzenta porównuje i wysyła ocenę w postaci **wystawionej** (przesunięcie skali
  z punktami ujemnymi dokłada widok) – wcześniej lista radio skali z punktami ujemnymi wysyłała liczbę
  bez przesunięcia.
- Podręczniki: organizatora § 2.3 („Dowolne wartości ocen”, „Zadania mogą mieć różną liczbę punktów”),
  recenzenta § 4; `API.md` § 6.2; `OPERACJE.md` § 18.

### Inni dostawcy AI i tryb testowy (flaga `ai_grading`)

Prośby organizatora z 24.09.2026: „Pozwól też na użycie innych dostawców AI, jak OpenAI, Google
i Meta.” oraz „włącz wszystkich dostawców dla testów”. Ocena AI (flaga `ai_grading`) przestaje być
wyłącznie Claude'em: koordynator wybiera dostawcę i model przy każdym zleceniu, a tę samą pracę może
ocenić kilkoma modelami, żeby je porównać.

- **Dostawcy** (`apps.ai_grading.providers`): wspólny kontrakt – jedno neutralne wejście (prompt
  systemowy, materiały zadania, praca), jeden wynik (JSON wg tego samego schematu, zużycie z cache,
  identyfikator żądania, odmowa/ucięcie w słowniku Anthropic) i rodzaje błędów (auth / rate limit /
  transient / permanent / refusal / too large), od których zależy ponowienie. **Anthropic** – żądanie
  bajt w bajt jak w v0.34.0 (`client.py` bez zmian w wywołaniu). **OpenAI** – Responses API
  (`responses.stream`, `text.format` json_schema strict, `store: false`, `reasoning.effort: high`,
  PDF `input_file`, obraz `input_image`, `prompt_cache_key`). **Google** – `google-genai`
  (`generate_content`, `response_json_schema`, `thinking_level: high` dla `gemini-3*`, pliki inline,
  blokady `SAFETY`/`PROHIBITED_CONTENT`… jako odmowa). **Meta** – Meta Model API przez SDK OpenAI
  (Chat Completions, `response_format` json_schema, PDF jako część `file`); dawne Llama API Meta
  wyłączyła 6.07.2026. Limity plików per dostawca sprawdzane przed wysyłką (Google 20 MB, Meta PDF do
  50 stron). Modele z list (stan 24.09.2026) plus „inny identyfikator modelu”; warstwa Meta
  `-contributor` odrzucana.
- **Klucze i umowy powierzenia per dostawca** (`AiProviderAccount`): klucz tylko do zapisu (ten sam
  Fernet), „Sprawdź klucz” bez kosztu. Umowę powierzenia potwierdza **koordynator osobiście**, w dwóch
  krokach: strona z informacją o dostawcy (`apps.ai_grading.disclosures`: dane, odbiorca, transfer
  poza EOG, DPA, retencja, trenowanie, brak retencji; **ostrzeżenie 18+ przy Google i Mecie**, zmiana
  Llama API → Meta Model API), wymagane oświadczenie i „Potwierdzam”; zapis z datą, kontem i wersją
  (skrótem) pokazanej informacji. Bez potwierdzenia dostawca **nie dostaje prac uczestników**;
  wycofanie (jedno kliknięcie z pytaniem) zatrzymuje prace czekające w kolejce przed wysyłką.
- **Tryb testowy** (`apps.ai_grading.sandbox`): praca testowa koordynatora (PDF/JPG/PNG/py/ipynb,
  walidacja i skan jak prace uczestników, oświadczenie o braku danych uczestników, odmowa pliku
  identycznego z pracą uczestnika) oceniana **każdym dostawcą z kluczem, także bez umowy**. Oceny
  testowe widzi tylko koordynator (plakietka TEST), nie wchodzą do eksportu ani statystyk, liczą się
  do zużycia i limitu wydatków, można je usunąć.
- **Porównanie**: kluczem oceny jest (wersja pracy, dostawca, model); panel recenzenta pokazuje
  osobne panele „Ocena AI – <dostawca> <model> (sugestia, niewiążąca)”, najnowszy pierwszy; karta
  zadania – zgodność z oceną końcową osobno dla każdego modelu; uczestnik (gdy włączone) – najnowszą.
- **Ceny**: tabela cen per model w ustawieniach (domyślne z cenników z 24.09.2026, do nadpisania).
  Model bez ceny – koszt „nieznany”, liczone tokeny i licznik wywołań bez ceny; przy ustawionym limicie
  wydatków taki model jest odrzucany (`AI_PRICE_UNKNOWN`).
- **RODO**: rejestr czynności **1.8** – odbiorcy wiersza „ocena AI” liczeni dynamicznie (tylko dostawcy
  z kluczem i potwierdzoną umową); eksport danych uczestnika wymienia dostawcę i podmiot przetwarzający
  przy każdej ocenie; podręcznik organizatora § 4.12 – co sprawdzić u każdego dostawcy (DPA, SCC,
  retencja, trenowanie, brak retencji, **ograniczenia wieku w warunkach Google i Mety**).
- **Operator**: komenda `confirm_ai_provider_dpa` (OPERACJE § 17.6) – wyłącznie na sytuacje
  wyjątkowe, idempotentna, bez wersji informacji. **Po wdrożeniu żaden dostawca, także Anthropic, nie
  ma potwierdzonej umowy** – migracja celowo tego nie domniemywa, a zgodnie z decyzją organizatora
  potwierdza koordynator w panelu (nie operator komendą).

Migracje: `ai_grading.0002_providers` (klucz Anthropic przeniesiony do `AiProviderAccount` bez
odszyfrowania, `AiAssessment` z konkursem, dostawcą i modelem zamówionym, `AiTestWork`). Nowe
zależności: `openai>=3.19,<4`, `google-genai>=2.25,<3` (import leniwy). Nowa trasa Celery:
`apps.ai_grading.tasks.scan_ai_test_work` → kolejka `scan`.

## v0.34.0 – 2026-09-24

Wydanie zbiorcze z próśb i zgłoszeń organizatora z 24.09.2026. Dwie zmiany działają od wdrożenia,
w każdym konkursie i bez flagi: **listy koordynatora** (konta usunięte schowane, sortowanie kolumn)
i **wysyłka komunikatów do grup uczestników**. Trzy nowe funkcje stoją za flagami konkursu
**domyślnie wyłączonymi** – `student_status_certificate`, `workshop_materials`, `ai_grading` – więc
konkurs z domyślnymi przełącznikami nie zmienia się o ani jeden adres, pozycję menu ani zapytanie
(budżety zapytań w `test_invariants.py` bez zmian). Zapalenie każdej z nich jest decyzją organizatora
poprzedzoną krokiem operatora: `OPERACJE.md` § 6.4 oraz § 15 (status ucznia), § 16 (materiały
z warsztatów), § 17 (ocena AI).

Migracje: `accounts.0033_broadcast_target`, `accounts.0034_clear_anonymised_profile_data` (dane),
`tenancy.0009_document_kind_student_status`, `student_status.0001_initial`,
`workshop_materials.0001_initial`, `ai_grading.0001_initial`. Nowe zadania beat:
`student-status-purge-expired-scans` (doba), `workshop-materials-cleanup` (godzina), `ai-grading-pump`
(5 min). Nowa zależność `anthropic>=1.8,<2` (import leniwy). Rejestr czynności przetwarzania
**1.7** – jedna wersja z trzema wierszami **warunkowymi** (każdy widoczny wyłącznie przy włączonej
fladze): „Weryfikacja statusu ucznia”, „Statystyka wyświetleń materiałów z warsztatów”, „Pomocnicza
ocena prac uczestników przez model językowy”.

### Listy koordynatora: konta usunięte i sortowanie (bez flagi)

Zgłoszenie organizatora: „Koordynator widzi skasowanych użytkowników jako ‚deleted’ – to błąd.
Koordynator przeglądając uczestników powinien mieć możliwość ich sortowania po różnych polach.”

- **Konta usunięte schowane domyślnie.** Jedna reguła rozpoznania konta po anonimizacji
  (`apps.accounts.anonymised`: domena `@invalid.`, a nie puste imię ani `is_active`) – w Pythonie
  (`is_anonymised`) i w zapytaniu (`anonymised_q`, `User.objects.exclude_anonymised()`,
  `Participant.objects.exclude_anonymised()`); retencja korzysta z tej samej funkcji. Odsiewają ją:
  lista kont i uczestników, wyszukiwarka panelu, lista członków komisji, tabela obecności na
  warsztatach, arkusz „Uczestnicy edycji”, a od tego wydania także **przyjazdy i potrzeby** oraz
  **obecność na etapie stacjonarnym** (razem z liczbami do zamówienia i listami PDF, które idą za
  przełącznikiem) – każda z przyciskiem **„Pokaż usunięte konta (N)”** / „Ukryj usunięte konta”
  (`?usuniete=1`, przeżywa stronicowanie, sortowanie i filtry; N to liczba schowanych na bieżącej
  liście). Bez przełącznika, bo przeglądania tam nie ma: lista „Status ucznia” i jej liczniki, listy
  wyboru szkół i klas przy komunikatach, kolejki ekranu „Komitet” i ich plakietka w menu, naliczanie
  wpisowego.
- **„Konto usunięte” zamiast `deleted-…@invalid.…`** tam, gdzie wiersz musi zostać: przydziały,
  moderacja, kalibracja, zgłoszenia, karta problemu, rozmowy, dyplomy, karta uczestnika i członka
  komisji, audyt, zgłoszenia pomocy, nagłówki edycji i usunięcia konta, eksport recenzji, historia
  komunikatów, decyzje o zaświadczeniach, ustawienia oceny AI (filtry szablonu `person`,
  `account_email`, `is_deleted_account` w `coordinator_extras`). Wyniki zostają pod kodem publicznym.
- **Sortowanie kolumn** listy kont i listy uczestników (`?role=participant`, nowe kolumny: szkoła,
  województwo, klasa, zgoda opiekuna, prace w bieżącej edycji): nagłówki z `aria-sort` i strzałką,
  sortowanie po stronie serwera wyłącznie po kluczach z listy dopuszczonych (`apps.web.list_controls`,
  nieznany klucz = porządek domyślny, bez 500), remis rozstrzyga `pk`, stan w adresie przeżywa
  stronicowanie i filtry. Liczba prac – jedno zapytanie zbiorcze na stronę; przy okazji lista kont
  przestała robić dwa zapytania na wiersz (`is_protected` czyta grupy z prefetchu).
- Pasek konta w nagłówku pokazuje zalogowanego (`request.user`), a nie konto z kontekstu widoku
  (karta członka komisji wypisywała tam adres oglądanej osoby).

### Usunięcie konta czyści resztę danych profilu (bez flagi)

Decyzja organizatora z 24.09.2026. `anonymise_account` wyciera odtąd także adres e-mail rodzica
(`guardian_email`), adres opiekuna szkolnego (`supervisor_email`), nazwę placówki wpisaną ręcznie
(`institution_name`) i dowiązanie do słownika placówek organizatora (`custom_institution_ref`), uwagę
tekstową i potrzeby szczególne (dieta, dostępność) z formularzy przyjazdu oraz pseudonimy widza
materiałów z warsztatów; zaświadczenia o statusie ucznia i oceny AI tej osoby znikają razem z kontem
(z plikami). Skutek widoczny: **usunięty uczeń znika z panelu nauczyciela „Moi uczniowie”** i z jego
liczników (dopasowanie szło po adresie opiekuna; `students_of` filtruje też `exclude_anonymised()`
dla profili wytartych wcześniej), a zaświadczenia z warsztatów nie są wystawiane kontom usuniętym.
Migracja danych `accounts.0034` wyrównuje do tej reguły profile zanonimizowane przed wydaniem.

### Wysyłka komunikatów do grup uczestników (bez flagi)

Prośba organizatora: „koordynator dostaje funkcję wysyłania maili do poszczególnych grup uczestników,
w tym do wszystkich”. Rozbudowa ekranu **`/coordinator/messages/`** (Komunikacja → Komunikaty):

- **nowe grupy odbiorców** (`BroadcastGroup`): **„wszyscy uczestnicy konkursu”** – pierwsza na liście,
  uczestnicy bieżącej edycji także bez wpisu do etapu; „zapisani do etapu, bez wysłanej pracy” (wpis
  zarejestrowany/zakwalifikowany bez żadnej pracy w etapie, praca odrzucona przez antywirusa się nie
  liczy – przypomnienie przed terminem); uczestnicy z wybranego **województwa** (przy fladze
  `custom_regions` – **regionu**, łącznie z profilami sprzed flagi); z wybranej **szkoły** (lista
  wyłącznie szkół, z których są uczestnicy tego konkursu, z liczbą w nawiasie; wykaz SIO, słownik
  organizatora i nazwa wpisana ręcznie jako osobne pozycje); z wybranej **klasy**; **obecni na
  wybranym warsztacie** (`cms.WorkshopAttendance`, warsztaty z harmonogramu tego konkursu);
  **opiekunowie szkolni** (profil `SchoolSupervisor` tego konkursu + rola `supervisor`, z członkostwami
  przy `memberships_enforced`). Dotychczasowa grupa edycyjna zmienia etykietę na „uczestnicy bieżącej
  edycji (zapisani do etapu)”;
- **domyślnie bieżąca edycja** (decyzja organizatora): „wszyscy uczestnicy konkursu” oraz grupy
  województwa/regionu, szkoły i klasy obejmują wyłącznie uczestników bieżącej edycji – profil tego
  konkursu **i** (wpis do etapu bieżącej edycji **albo** konto założone nie wcześniej niż
  `Edition.created_at`; `apps.accounts.messaging.current_edition_participants`). Pole „także uczestnicy
  poprzednich edycji” (domyślnie odznaczone) zdejmuje zawężenie; wybór wchodzi do podpisu podglądu,
  do `MessageBroadcast.target` i do audytu. Bez bieżącej edycji te grupy są puste, dopóki pole nie
  jest zaznaczone;
- **parametr grupy w historii i audycie**: nowe pole `MessageBroadcast.target` (JSON, migracja
  `accounts.0033_broadcast_target`) – identyfikator i etykieta etapu/regionu/szkoły/klasy/warsztatu
  z chwili wysyłki; kolumna „Grupa” w „Wysłanych komunikatach”, podgląd („Odbiorcy: …”) i wpis
  `broadcast.sent`. Adresów nadal nigdzie nie zapisujemy; pole wypełnione, ale nienależące do
  wybranej grupy, jest ignorowane;
- **zakres konkursu**: każda grupa liczona w obrębie `request.competition`, a konto wybierane wyłącznie
  po identyfikatorze profilu z tego konkursu. Przy okazji naprawione dwa przecieki: grupa „członkowie
  komitetu” nie miała zakresu konkursu w ogóle, a wiersz rejestru brał konkurs z odwrotu
  `default_competition` zamiast z żądania; nieużywane `recent_broadcasts()` wymaga odtąd konkursu;
- **podpis podglądu**: „Wyślij” przechodzi wyłącznie z ukrytym podpisem (HMAC) grupy, jej parametru,
  tematu i treści z ostatniego podglądu – zmiana czegokolwiek po podglądzie niczego nie wysyła, tylko
  pokazuje podgląd na nowo (wcześniej „Wyślij” z poprzedniego podglądu wysyłał to, co akurat stało
  w formularzu);
- formularz pokazuje wyłącznie pole wymagane przez wybraną grupę (`static/js/broadcast-groups.js`;
  bez JavaScriptu widać wszystkie pola); odbiorcy – jedno zapytanie z półzłączeniem na grupę, listy
  wyboru szkół i klas – po jednym zapytaniu grupującym (bez kont usuniętych).

Świadomie **bez** załączników, bez kopii do adresu opiekuna prawnego (`guardian_email` służy wyłącznie
zgodzie – RODO), bez grupy „rocznik”, bez grupy „zapisani na warsztat, ale nieobecni” i bez wpisów
drużynowych w grupach etapowych. Podręcznik organizatora § 6.1.

### Zaświadczenie o statusie ucznia (flaga `student_status_certificate`)

Nowa aplikacja `apps.student_status` (model `StudentStatusCertificate` – wersje per uczestnik
**i edycja**, bo zaświadczenie potwierdza rok szkolny; migracje `student_status.0001_initial`
i `tenancy.0009_document_kind_student_status`).

- **Uczestnik** (`/me/status-ucznia/`): imienny wzór PDF do podstemplowania (`wzor.pdf` – imię
  i nazwisko, data urodzenia, szkoła, rok szkolny z edycji, puste miejsca na klasę, pieczątkę szkoły,
  datę i podpis dyrektora/sekretarza; skład ReportLab na krojach DejaVu, tekst z nowego rodzaju
  dokumentu `STUDENT_STATUS` w „Szablonach dokumentów” ze znacznikami `{birth_date}`
  i `{school_year}`), wgranie skanu PDF/JPG/PNG do 10 MB (format po treści, prywatny storage
  `student-status/…`, skan ClamAV kolejką `scan`, zainfekowany – odrzucony i usunięty), ponowne
  wgranie zastępuje oczekujące/odrzucone (plik poprzedni usuwany, historia zostaje), stan brak /
  oczekuje / zaakceptowane / odrzucone z powodem; przypomnienie na pulpicie do czasu akceptacji –
  **nie blokuje** oddawania prac.
- **Koordynator** (`/coordinator/student-status/`, *Uczestnicy i konta* → „Status ucznia”): liczniki
  i filtry oczekujące / zaakceptowane / odrzucone / brak, wybór edycji, wyszukiwarka, podgląd skanu
  (po czystym skanie; `nosniff`, obrazy z `CSP: sandbox`; audyt `student_status.viewed`),
  **Akceptuj** / **Odrzuć z powodem** (e-mail do uczestnika, audyt `student_status.accepted/rejected`
  bez treści powodu), sekcja „Status ucznia” na karcie uczestnika.
- **Paczki ZIP**: przy każdym pobraniu prac koordynatora (etap, zadanie, zaznaczone) i komitetu
  (`/review/download/` oraz `GET /api/grading/reviews/download/`) parametr `students=all|verified` –
  „wszystkie prace” (domyślnie) albo „tylko uczniowie z potwierdzonym statusem ucznia”; nazwy plików
  dalej anonimowe, recenzent nigdy nie widzi skanu; przy wyłączonej fladze `verified` to 404
  z powodem, a przyciski wyglądają jak dotąd.
- **RODO**: wiersz rejestru „Weryfikacja statusu ucznia (zaświadczenie ze szkoły)” (§ 9.2), eksport
  danych z sekcją `zaswiadczenia_statusu_ucznia` i plikami, anonimizacja i usunięcie konta kasują
  wiersze i pliki, zadanie beat `student-status-purge-expired-scans` usuwa pliki edycji po terminie
  retencji; storage dostaje `delete()`. Tłumaczenia EN panelu uczestnika i listów.
- Dokumentacja: PODRĘCZNIK-UCZESTNIKA § 5a, PODRĘCZNIK-ORGANIZATORA § 4.1, 7.2a, 9.1, 9.2, 10a,
  PODRĘCZNIK-RECENZENTA § 2, OPERACJE § 6.4 i § 15.

### Materiały z warsztatów (flaga `workshop_materials`)

Prośba organizatora: „Koordynator dostaje możliwość wgrywania materiałów z warsztatów, w tym filmów.
Filmy powinny być możliwe do obejrzenia tylko na stronie po zalogowaniu.” Nowa aplikacja
`apps.workshop_materials` (migracja `workshop_materials.0001_initial`: modele `WorkshopMaterial`
i `WorkshopMaterialViewer`).

- **Koordynator** – `/coordinator/workshops/materials/` (Raporty → Materiały z warsztatów, odnośnik
  także z ekranu obecności): lista warsztatów z harmonogramu (blok `schedule` strony „Warsztaty”), pod
  każdym materiały – **film** (MP4/WebM do 4 GB), **plik** (PDF, PPTX, DOCX, XLSX, ODP/ODT/ODS, ZIP,
  IPYNB, PNG/JPG do 100 MB) albo **odnośnik** (`https://`); tytuł, opis, kolejność w obrębie
  warsztatu, publikacja, podgląd szkicu, usunięcie razem z obiektem w magazynie; audyt
  `workshop_material.*` bez tytułu i nazwy pliku. Materiał jest przypięty kluczem warsztatu (data +
  temat, jak obecność) z **migawką** tematu i daty – po zmianie wiersza w harmonogramie materiał nie
  znika u widzów, a koordynator widzi sekcję „Materiały bez warsztatu w harmonogramie” z przepięciem
  całej grupy.
- **Wgrywanie bez gunicorna**: przeglądarka wysyła plik częściami po 16 MB prosto do MinIO na adresy
  podpisane przez serwer (`static/js/workshop-material-upload.js`, trzy części naraz, ponowienia,
  pasek postępu, „Przerwij”); krok „zakończ” składa plik, sprawdza rozmiar i **format po treści**
  (MP4 z marką ISO BMFF albo WebM; MOV/MKV odrzucane z podpowiedzią przepakowania), pod blokadą
  wiersza. Pliki – skan ClamAV (kolejka `scan`, zagrożenie → obiekt skasowany, materiał
  „odrzucony”); filmy bez ClamAV (uzasadnienie w `apps/workshop_materials/tasks.py`). Bez
  transkodowania.
- **Oglądanie po zalogowaniu**: `/warsztaty/materialy/` (lista po warsztatach),
  `/warsztaty/materialy/<id>/` (odtwarzacz `<video controlslist="nodownload">` z adresem podpisanym na
  2 h, przewijanie `Range`), `/warsztaty/materialy/<id>/pobierz/` (plik – przekierowanie na podpis na
  5 min; odnośnik – na adres zewnętrzny). Widzi każde konto z rolą **w tym konkursie**; inne konto –
  403, anonim – logowanie. Wszystkie odpowiedzi `no-store`, żadna z tych ścieżek nie jest na
  allow-liście pamięci stron. Gość na `/warsztaty/` widzi ramkę „zaloguj się, aby obejrzeć” z liczbą
  materiałów, bez adresów.
- **Odnośniki**: „Materiały z warsztatów” w pasku konta i kafel na pulpicie uczestnika `/me/` – tylko
  przy włączonej fladze i co najmniej jednym opublikowanym, gotowym materiale (pamięć podręczna per
  konkurs, kasowana sygnałem; przy wyłączonej fladze zero zapytań – nowy test w `test_invariants.py`).
- **Statystyki**: wyświetlenia i liczba różnych widzów na materiał; widz zapisany wyłącznie jako
  pseudonim HMAC pary (materiał, konto), kasowany po 12 miesiącach albo przy usunięciu konta;
  koordynator nie jest liczony. Wiersz rejestru „Statystyka wyświetleń materiałów z warsztatów”.
- **Infrastruktura**: `deploy/minio/policy-submissions.json` + uprawnienia wgrywania wieloczęściowego
  (na produkcji: `docker compose run --rm minio-init`); `scripts/backup.sh` pomija prefiks
  `workshop-materials/` w kopii nocnej; zadanie beat `workshop-materials-cleanup` (co godzinę:
  porzucone wgrywania > 24 h, pseudonimy > 12 mies.); ustawienia `WORKSHOP_VIDEO_MAX_MB`,
  `WORKSHOP_FILE_MAX_MB`. Caddy i CSP bez zmian.
- Dokumentacja: `OPERACJE.md` § 16; `PODRECZNIK-ORGANIZATORA.md` § 4.11;
  `PODRECZNIK-UCZESTNIKA.md` § 7.

### Ocena AI – sugestia punktów dla komitetu (flaga `ai_grading`)

Nowa aplikacja `apps.ai_grading` (modele `AiGradingSettings`, `AiStageVisibility`, `AiAssessment`,
migracja `ai_grading.0001_initial`), zależność `anthropic>=1.8,<2`. Ocenę wystawia wyłącznie
człowiek; sugestia jest niewiążąca.

- **Klucz API per konkurs** na ekranie `/coordinator/ai-grading/` (Ocenianie → Ocena AI): zaszyfrowany
  w bazie (Fernet z `DJANGO_SECRET_KEY`, własna etykieta), tylko do zapisu – ekran pokazuje
  „ustawiony, kończy się na …abcd”; zastąp / usuń / „Sprawdź klucz” (`models.retrieve`, bez kosztu);
  klucz administracyjny odrzucany; nigdy w logach, audycie, argumentach zadań Celery ani
  w szablonach. Wybór modelu `claude-opus-5` (domyślny) / `claude-sonnet-5`, limit wydatków w USD,
  liczniki zużycia i szacowany koszt (Opus 5: 5/25 USD, Sonnet 5: 2/10 USD za MTok, odczyt cache
  0,1×, zapis 1,25×).
- **Zlecenie z karty zadania** (`/coordinator/problems/<id>/`, sekcja „Ocena AI”): dla wszystkich
  najnowszych wersji prac bez oceny AI (opcja „wygeneruj ponownie także istniejące”) albo dla jednej
  pracy; dwustopniowe – podgląd z liczbą prac i szacowanym kosztem, potem „Zleć”. Idempotentne,
  blokada doradcza per zadanie; stany oczekuje / w toku / gotowa / błąd, sekcja odświeżana htmx,
  zgodność AI z oceną końcową (średnia różnica, % zgodnych, % w granicy 1 pkt).
- **Kolejka z ogranicznikiem**: do brokera trafia najwyżej `AI_GRADING_MAX_CONCURRENCY` (domyślnie 1)
  zadań naraz – koniec oceny wypuszcza następną; beat `ai-grading-pump` (5 min) domyka oceny
  osierocone przez restart workera. Ponowienia wyłącznie 429/5xx/sieć (maks. 4, `retry-after`
  przycięty do 15 min); odmowa i `max_tokens` nie są ponawiane; twardy limit zadania 16 min; limit
  wydatków zatrzymuje kolejkę bez wołania API.
- **Żądanie do modelu**: `client.beta.messages.stream(...)` + `get_final_message()`, `max_tokens`
  32000, `thinking: adaptive`, `output_config` z `effort: high` i schematem JSON, beta
  `server-side-fallback-2026-07-01` z `fallbacks: "default"`; materiały zadania przed pracą,
  `cache_control` na ostatnim stałym bloku; praca jako dokument PDF, obraz albo tekst, bez nazwy pliku
  i danych uczestnika; limity API sprawdzane przed wysyłką. Prompt po polsku z osłoną przed
  wstrzyknięciem poleceń; odpowiedź walidowana, punkty przycinane do skali, dane osobowe autora
  wymazywane; `stop_reason` sprawdzany przed treścią, `request_id` w logu.
- **Panel recenzenta**: zwinięty panel „Ocena AI (sugestia, niewiążąca)” – wyłącznie przy
  przydzielonej wersji pracy, bez danych uczestnika; formularz nigdy nie wypełnia się sam, przycisk
  „Wstaw punkty AI jako punkt wyjścia” tylko zaznacza najbliższą wartość skali (przy rubryce go nie ma).
- **Uczestnik**: przełącznik etapu **„Pokaż uczestnikom ocenę AI”, domyślnie wyłączony** (decyzja
  organizatora); po włączeniu i ogłoszeniu wyników – podsumowanie i proponowane punkty w osobnej
  sekcji informacji zwrotnej z podpisem „sugestia AI”. Przy wyłączonym nic o ocenie AI nie trafia na
  ekrany uczestnika, do tabel wyników, dyplomów, reklamacji ani API uczestnika (test).
- **RODO**: wiersz rejestru „Pomocnicza ocena prac uczestników przez model językowy” (Anthropic jako
  podmiot przetwarzający, przekazanie poza EOG, brak decyzji zautomatyzowanej); eksport danych konta –
  sekcja `oceny_ai`; anonimizacja konta kasuje oceny AI prac tej osoby. Audyt `ai_grading.*` bez
  wartości klucza.
- Dokumentacja: `PODRECZNIK-ORGANIZATORA.md` § 4.12 (z listą warunków prawnych przed włączeniem),
  `PODRECZNIK-RECENZENTA.md` § 3a, `PODRECZNIK-UCZESTNIKA.md` § 6, `OPERACJE.md` § 6.4 i § 17.

## Wydania (do v0.37.0 – skrót)

| Wersja | Data | Zmiana |
|---|---|---|
| **v0.37.0** | 2026-09-25 | **wydanie infrastrukturalne z 25.09.2026** (pełny opis w sekcji „v0.37.0 – 2026-09-25” wyżej): **Python 3.14** (`python:3.14-slim-trixie`, CI 3.14, `requires-python >=3.14`, ruff `py314`, `psycopg>=3.2.10`; obraz do przebudowy; `OPERACJE.md` § 21); **PostgreSQL 18** (`postgres:18-alpine` na nowym wolumenie `pg18_data`, `POSTGRES_IMAGE`/`POSTGRES_VOLUME`, wdrożenie przypina 16 do czasu `scripts/upgrade_postgres18.sh` – zrzut i odtworzenie z porównaniem 16 ↔ 18, `--rollback`; § 19); **strona „Prace techniczne”** (Caddy 503 w trybie planowym – `scripts/maintenance.sh on|off|status`, przepustka `MAINTENANCE_BYPASS_TOKEN` – i nieplanowym przy 502/503/504, `deploy.sh --maintenance`, wymuszona kolejność przejścia na 18; § 20); **szybsza suita testów** (migracje w jednej wycofywanej transakcji na moduł, migawka po testach transakcyjnych, atrapa clamd, markery `slow`/`migrations`/`clamav`, budżety zapytań w jednej tabeli, xdist w każdym z 5 shardów CI, `docs/TESTY.md`); **9 angielskich tematów listów** z marką konkursu i 3 nowo zamrożone tematy; bez migracji |
| **v0.36.0** | 2026-09-25 | **wydanie zbiorcze z 25.09.2026** (pełny opis w sekcji „v0.36.0 – 2026-09-25” wyżej): **responsywne tabele** w panelu koordynatora i na stronach publicznych (ramki przewijane z regionem i przystankiem klawiatury, przyklejona pierwsza kolumna i ranking, karty na telefonie, `js/table-scroll.js`; bez poziomego suwaka strony na 72 sprawdzonych stronach); **ułamki w rubrykach i teście** w etapie „dowolna wartość” (maksimum kryterium i punkty co 0,01, wynik testu co 0,01, połówka w górę; migracje `grading.0012`, `ai_grading.0003`, `OPERACJE.md` § 18.4); **pula połączeń psycopg** w `web` (`psycopg[binary,pool]`, `worker`/`beat` bez puli, `application_name` per usługa, alarm zajętości 80/95 % w `/healthz/`, `/status.json`, watchdogu i `manage.py db_connections`; `OPERACJE.md` § 11.2) i **reset hasła w tle** (kolejka `mail`, ten sam list); **drzewo CMS konkursu pod prefiksem** (uwaga T43: własne strony, menu, przekierowania i adresy pod `/<prefiks>/`, `path_prefix_routing` jako bramka gospodarza, migracja danych `tenancy.0010`; § 6.6); **uprawnienia `/cms/` per konkurs i rola superkoordynatora** (grupa `cms:<slug>` na korzeniu witryny i kolekcji konkursu, przełącznik „Konkursy platformy”, komendy `superkoordynator` i `scope_cms_access` z kontrolą macierzy przed/po; § 6.7); **powiadomienia e-mail z forum** (flaga `participant_forum`: list o kolejce moderacji, obserwowane wątki na bieżąco/raz dziennie/nigdy, decyzje moderatora, wypis jednym kliknięciem z `List-Unsubscribe`, migracja `forum.0002`, zadania beat `forum-notifications` i `forum-daily-digest`, stan powiadomień w eksporcie danych i kasowany przy anonimizacji); rejestr czynności **1.9**; obraz do przebudowy, `web`+`worker`+`beat` razem |
| **v0.35.0** | 2026-09-24 | **wydanie zbiorcze z 24.09.2026** (pełny opis w sekcji „v0.35.0 – 2026-09-24” wyżej): **dowolne wartości ocen i różne maksima zadań** – przełącznik etapu „tylko wartości ze skali” / „dowolna wartość od min do max (co 0,01)” na ekranie skali (domyślnie – także dla nowych etapów – tryb skali; powrót odmawiany `409 FREE_VALUES_IN_USE` przy ocenach spoza skali albo zadaniach z samym maksimum); jedna reguła oceny `competitions.scoring.ScoreRule` dla recenzji, korekt, moderacji, reklamacji, rozmów, rubryki i API (przecinek normalizowany, trzecie miejsce po przecinku = `SCORE_INVALID`); zadanie z samym maksimum (np. 12,5) i maksima w liście zadań, u recenzenta i w nagłówkach tabel wyników; kolumny punktów `numeric(p,2)` (migracje `competitions.0032`, `grading.0011`, `appeals.0003`, `OPERACJE.md` § 18); sumy w `Decimal`, suma ważona połówka w górę do 0,01 (tryb dowolny) albo do pełnego punktu (tryb skali); filtr `points` („5”, „4,25”), CSV z kropką, JSON/API jako liczby (`API.md` § 6.2); **inni dostawcy AI** (flaga `ai_grading`): OpenAI (Responses API), Google (`google-genai`) i Meta (Meta Model API przez SDK OpenAI) obok Anthropic, wybór dostawcy i modelu przy zleceniu i porównanie kilku modeli na tej samej pracy (osobne panele u recenzenta), klucz i umowa powierzenia per dostawca (`AiProviderAccount`), potwierdzana przez koordynatora w dwóch krokach – strona informacji o dostawcy (`apps.ai_grading.disclosures`, ostrzeżenie 18+ przy Google i Mecie) i „Potwierdzam” z zapisem wersji informacji (bez potwierdzenia brak prac uczestników; komenda `confirm_ai_provider_dpa` tylko awaryjnie, `OPERACJE.md` § 17.6), tryb testowy z pracą testową koordynatora dla każdego dostawcy z kluczem, tabela cen per model (`AI_PRICE_UNKNOWN` przy limicie), rejestr czynności 1.8, migracja `ai_grading.0002_providers`, zależności `openai>=3.19,<4` i `google-genai>=2.25,<3`; propozycja punktów każdego dostawcy przycinana do maksimum z `ScoreRule` |
| **v0.34.0** | 2026-09-24 | **wydanie zbiorcze z 24.09.2026** (pełny opis w sekcji „v0.34.0 – 2026-09-24” wyżej): **listy koordynatora** – konta usunięte schowane domyślnie za przyciskiem „Pokaż usunięte konta (N)” na każdej liście osób (także przyjazdy i obecność na etapie stacjonarnym), „Konto usunięte” zamiast `deleted-…@invalid`, sortowanie kolumn listy kont i uczestników; **usunięcie konta** czyści też adres rodzica, adres opiekuna szkolnego, placówkę i dane szczególne logistyki – usunięty uczeń znika z panelu „Moi uczniowie” (migracja danych `accounts.0034`); **wysyłka komunikatów do grup** (wszyscy uczestnicy, bez pracy w etapie, województwo/region, szkoła, klasa, obecni na warsztacie, opiekunowie; domyślnie bieżąca edycja; podpis podglądu; `accounts.0033`); za flagami **domyślnie wyłączonymi**: **zaświadczenie o statusie ucznia** (`student_status_certificate`, filtr paczek ZIP „tylko z potwierdzonym statusem”; `OPERACJE.md` § 15), **materiały z warsztatów** (`workshop_materials`, filmy i pliki dla zalogowanych, wgrywanie częściami prosto do MinIO; § 16) i **ocena AI** (`ai_grading`, sugestia punktów Claude'a dla komitetu, przełącznik etapu „Pokaż uczestnikom ocenę AI” domyślnie wyłączony, zależność `anthropic`; § 17); rejestr czynności **1.7** z trzema wierszami warunkowymi |
| **v0.33.0** | 2026-09-23 | **plakaty zgrupowane w karty** (prośba organizatora z 23.09.2026: „jedna karta na format, kilka przycisków” zamiast osobnej karty na każdy plik „A3 (JPG)”, „A3 (PDF)”, „A3 (PDF ze spadem 3 mm)”…): `PromoMaterial` dostaje dwa pola (migracja `promo.0002_group_variant_label`) – **`group`** („Karta (grupa plików)”, np. „A3 · 297×420 mm”: pliki jednego konkursu z identyczną, niepustą grupą stają na `/plakaty/` na **jednej karcie** – nagłówek to grupa, podgląd to pierwszy podgląd w grupie, opis pierwszy niepusty, pod spodem przycisk na każdy plik w kolejności koordynatora; karta stoi tam, gdzie jej pierwszy plik) i **`variant_label`** („Napis na przycisku”, np. „PDF ze spadem 3 mm”; puste = sam format JPG/PNG/PDF). Przycisk „Pobierz JPG · 1,7 MB” prowadzi do **własnego** adresu pobrania pliku, więc liczenie pobrań, limit, pseudonim IP i statystyki zostają per plik; nazwa dostępna przycisku niesie grupę („Pobierz A3 · 297×420 mm – PDF ze spadem 3 mm”). Plik bez grupy wygląda jak dotąd. Karty składa Python z tej samej jednej listy (`apps.promo.cards.build_cards`) – liczba zapytań `/plakaty/` bez zmian (test). Ekran koordynatora: oba pola w formularzu (z podpowiedzią `<datalist>` grup tego konkursu), linia „Karta: … · przycisk „…”” pod tytułem w tabeli, dwie nowe kolumny w eksporcie CSV („karta (grupa)”, „przycisk”); podręcznik organizatora § 4.10 |
| **v0.32.0** | 2026-09-23 | **plakaty do pobrania** (prośba organizatora z 23.09.2026): nowa aplikacja `apps.promo` (modele `PromoMaterial` i `PromoDownload`, migracja promo.0001), strona publiczna **`/plakaty/`** (siatka kart: podgląd, tytuł, opis, format i rozmiar, „Pobierz”; 404, gdy konkurs nie ma opublikowanych plakatów; na allow-liście pamięci stron, unieważnianej przy każdym zapisie plakatu) i pobranie `/plakaty/<id>/pobierz/` (plik z prywatnego storage jako załącznik przez aplikację, `Cache-Control: no-store`, nigdy w pamięci stron); plik PDF/JPG/PNG do 50 MB rozpoznawany **po treści** (sygnatury `%PDF-`, `FF D8 FF`, PNG), miniatura JPG/PNG robiona automatycznie (Pillow), dla PDF-a opcjonalny własny podgląd albo ikona; odnośnik „Plakaty do pobrania” w stopce każdej strony i przycisk w panelu opiekuna szkolnego – tylko gdy jest opublikowany plakat (flaga w Redisie, unieważniana przy zapisie; budżety zapytań `/`, `/me/`, `/coordinator/` +1 na zimno, na ciepło zero). Ekran koordynatora **`/coordinator/posters/`** (Ustawienia → Plakaty do pobrania): dodanie, edycja, publikacja, kolejność, usunięcie (plakat z pobraniami trafia do archiwum ze statystykami), eksport CSV, audyt `promo.*`; statystyki **podwójne** – pobrania i **unikalne adresy IP** w oknach 7 dni / 30 dni / od początku (unikalność w całym oknie i w sumie między plakatami), kafelki, wykres dzienny obu szeregów (CSS, bez JS), eksport z tymi samymi kolumnami. Nie liczymy robotów, podglądów linków, `HEAD` ani koordynatora; podwójne kliknięcie (ten sam plakat i adres w 10 s) to jedno pobranie, a pobieranie ma limit 30/min na adres IP (scope `poster_download`, 429 bez zapisu pobrania); `HEAD` na plik brakujący w storage daje 404 jak `GET`. Adresu IP nie zapisujemy: zostaje **pseudonim** HMAC-SHA256 z kluczem z `SECRET_KEY`, zerowany po 12 miesiącach nowym zadaniem beat `promo-clear-expired-ip-hashes`; rejestr czynności przetwarzania 1.6 – nowa czynność „Statystyka pobrań materiałów promocyjnych” (art. 6 ust. 1 lit. f) |
| **v0.31.2** | 2026-09-22 | strona rejestracji opiekuna szkolnego (`/register/supervisor/`) bez zaszytego w szablonie wstępu nad formularzem (organizator, 22.09.2026: „usuń tylko ten tekst nad formularzem”; „czy to intro mogę edytować z poziomu CMS”) – w jego miejsce pole `SiteSettings.supervisor_registration_intro` (`/cms/` → Ustawienia → Dane serwisu, sekcja „Rejestracja”; migracja cms.0027): domyślnie puste, czyli akapitu nie ma, a wpisany tekst (pogrubienie, kursywa, odnośnik) pojawia się nad formularzem bez wdrożenia; wyjaśnienie, skąd bierze się lista uczniów, zostaje w pustym stanie pulpitu opiekuna |
| **v0.31.1** | 2026-09-22 | osobna pozycja głównego menu „Dla nauczycieli” (prośba organizatora z 22.09.2026): odnośnik do `/register/supervisor/` stoi teraz jako ostatnia pozycja menu, za drzewem CMS (albo za listą zapasową), a nie tylko na `/register/` i na `/login/` jak dotąd; widoczna wyłącznie niezalogowanemu czytelnikowi i wyłącznie na witrynie, która ma dziś włączony przełącznik `SiteSettings.supervisor_registration_enabled` (`apps/cms/context_processors.py::_supervisor_menu_item`) – ten sam warunek, co reszta odnośników do tej roli. Budżet zapytań strony głównej (`apps/tenancy/tests/test_invariants.py::QUERY_BUDGET["/"]`) rośnie o jedno zapytanie: menu, w przeciwieństwie do leniwego procesora `supervisor_registration`, musi znać wynik przełącznika od razu, żeby wiedzieć, czy w ogóle dołożyć pozycję (nadal trzydziestosekundowa pamięć podręczna na proces, nie zapytanie na żądanie)
| **v0.31.0** | 2026-09-22 | wydajność: test obciążeniowy z 22.09.2026 pokazał, że pod ASGI (gunicorn + `UvicornWorker`) w 100% synchroniczna aplikacja serializowała widoki na jeden wątek na proces – 3 workery dawały maks. 3 równoległe żądania, ok. **7 req/s** w nasyceniu przy p95 **2,3 s** (5 użytkowników), 300–600% CPU z samego przełączania wątków. Usługa `web` przechodzi na **WSGI + worker `gthread`** (`config.wsgi`, `--workers`/`--threads`, domyślnie 4×4 – concurrency procesu to teraz iloczyn, nie sama liczba workerów; ta sama zmiana w `backend/Dockerfile`, żeby `docker run` bez compose zgadzał się z compose); żaden widok, zadanie ani middleware nie wymagał ASGI (bez `async def`, bez `channels`, bez websocketów). Wątek roboczy `gthread` żyje w puli workera zamiast ginąć po żądaniu, więc trwałe połączenia z bazą znów mają sens: `DB_CONN_MAX_AGE` wraca z 0 na **60 s** i dochodzi `CONN_HEALTH_CHECKS` (budżet: 4×4 web + 2 worker + 1 beat ≈ 19–20 z 100 możliwych połączeń Postgresa – `WEB_THREADS` dochodzi do `.env.example` i do szablonu `.env` w `scripts/deploy.sh`, istniejące `.env` na produkcji zostaje nietknięte, `WEB_WORKERS=3` × domyślne `WEB_THREADS=4` daje 12 równoległych żądań bez żadnej ręcznej zmiany). Pliki statyczne (`{% static %}`, WhiteNoise `CompressedManifestStaticFilesStorage`, nazwy z odciskiem treści) dostają w Caddy'm `Cache-Control: public, max-age=31536000, immutable` na `/static/*`. Nowe zadanie Celery `apps.core.tasks.captcha_clean` (godzinowe) sprząta wygasłe wiersze `captcha.CaptchaStore`, których `django-simple-captcha` samo nigdy nie kasuje. Higiena kontenerów: log każdej usługi w compose ograniczony do 50 MB × 5 plików (`json-file`, wspólna kotwica YAML), limity pamięci (`mem_limit`, bo `deploy.resources` nie działa poza Swarmem) – `web` 2g (cztery workery plus stary i nowy naraz przy rotacji `--max-requests`), `clamav` 3g (przy przeładowaniu sygnatur ClamAV trzyma przez chwilę dwie bazy), `worker` 768m; bez limitów CPU (dławienie rdzeni tylko wydłużyłoby czas odpowiedzi pod szczytem ruchu). `scripts/deploy.sh` dostaje po kroku 8/8 nowy, ostatni krok „Porządki: stare obrazy”: po wdrożeniu zostają tylko bieżący i poprzedni tag `olimpiada/web` (rollback bez ponownego budowania) plus `docker image prune -f` dla warstw bez tagu. Opis pełnego budżetu współbieżności i połączeń oraz kroków rollbacku: `docs/OPERACJE.md` § 11 **Wyszukiwarka szkół:** wyszukiwarka szkół (`GET /api/schools/`, `GET /api/schools/cities/`) po indeksach GIN + `pg_trgm` zamiast pełnego przejścia po tabeli: statystyki produkcji (22.09.2026, okno 15 dni) pokazały 864 sekwencyjne skany `schools_school` (7,0 mln przeczytanych wierszy) wobec 247 tys. skanów indeksowych, bo `search_text__contains`/`city_search__contains` (koniunkcja tokenów, dzielnica po separatorze) to dopasowanie **w środku** napisu, którego zwykły B-tree nie obsłuży – stąd 130–185 ms na zapytanie. Migracja `schools.0006_pg_trgm_search_indexes` włącza rozszerzenie `pg_trgm` (`TrigramExtension`, bez uprawnień superużytkownika – zaufane od PostgreSQL 13) i zakłada `schools_search_trgm_idx`/`schools_city_trgm_idx` (`GinIndex`, `gin_trgm_ops`); prefiks (`city_search__startswith`) zostaje przy istniejącym indeksie `varchar_pattern_ops`. Przy okazji zdjęte trzy indeksy z zerem skanów na produkcji w tym samym oknie: `schools_city_kind_idx` (porządek listy liczy wyrażenie `Case` w Pythonie, nie kolumnę `kind` – indeks nigdy nie mógł posłużyć sortowaniu) oraz para spod `db_index=True` na `search_text` (`schools_school_search_text_…` i jej bliźniak `_like`), zastąpiona przez indeks trigramowy. Wyniki, kolejność i ranking wyszukiwarki bez zmian – zmienił się wyłącznie plan zapytania (dowód: `apps/schools/tests/test_search_indexes.py`, porównanie wierszy między planem z indeksem a wymuszonym `Seq Scan`); zmierzone lokalnie na pełnym wykazie (8118 wierszy): `search_text__contains='lice'` 5,1 ms/547 buforów → 3,9 ms/220 buforów **Cache stron publicznych:** cache całych stron publicznych dla anonimowych GET-ów (profilowanie produkcji z 22.09.2026: 250–500 ms CPU na odsłonę, głównie renderowanie szablonu, przy identycznej treści dla każdego anonimowego gościa danej witryny) – nowa warstwa `apps.web.page_cache.PageCacheMiddleware`, ostatnia przed widokiem, wyłącznie dla adresów z allow-listy (`/`, `/harmonogram/`, `/warsztaty/`, `/dokumenty/…`, `/faq/`, `/partnerzy/`, `/kontakt/`, `/aktualnosci/…`, `/wyniki/`, `/archiwum/…`, `/statystyki/`); nonce CSP i token CSRF (ten drugi w `hx-headers` na **każdej** stronie, patrz `templates/base.html`) trzymane w cache'u jako placeholder i podmieniane na świeże przy każdym trafieniu, więc żaden skrypt nie traci nonce'u, a HTMX nie dostaje nieważnego tokenu; klucz niesie wersję (globalną i witryny konkursu – `INCR`, bez wyliczania wpisów), język interfejsu, ścieżkę i `?page=`; TTL 120 s (`PAGE_CACHE_SECONDS`, `0` wyłącza), włącznik `PAGE_CACHE_ENABLED` (domyślnie włączony poza `DEBUG`, wyłączony w testach); nigdy nie cache'uje zalogowanych, żądań spoza `GET`/`HEAD`, odpowiedzi z `Set-Cookie`, sesji zmienionej w trakcie obsługi (przełącznik kontrastu gościa), komunikatu organizatora **wyświetlonego** w tym żądaniu (sprawdzenie niezależne od `session.modified`, bo `MessageMiddleware` zapisuje skonsumowaną kolejkę do sesji dopiero w swojej fazie odpowiedzi, czyli już po tej warstwie) ani odpowiedzi większej niż 512 KiB; `Cache-Control: private, no-store` na każdej odpowiedzi HIT/MISS z tej warstwy, żeby ewentualny przyszły CDN przed Caddym nigdy nie zbuforował materializowanego nonce'u/tokenu; awaria Redisa degraduje do normalnego renderowania zamiast pięćsetki (`IGNORE_EXCEPTIONS` w `CACHES["default"]` plus własne opakowanie wywołań cache'a w warstwie); unieważnianie przy publikacji/wycofaniu/przeniesieniu/skasowaniu strony, zapisie `SiteSettings`, komunikacie organizatora, zmianie edycji/etapu/wydarzenia i ogłoszeniu wyników; `manage.py page_cache_clear` do ręcznego gaszenia; nagłówek `X-Page-Cache: HIT/MISS/BYPASS` wyłącznie do weryfikacji **Koordynator:** koordynator resetuje hasło cudzego konta z karty `/coordinator/accounts/<id>/` — przycisk „Wyślij link do zmiany hasła” (`CoordinatorPasswordResetView`) wysyła dokładnie ten sam list, co samoobsługowy formularz „Nie pamiętasz hasła?” (`PasswordResetForm.save()` z tymi samymi szablonami i kontekstem listu), a koordynator nie widzi ani hasła, ani treści linku; odmowa bez wysyłki i bez wpisu audytowego dla konta jeszcze nieaktywowanego, zablokowanego, bez hasła platformy (logowanie przez zewnętrznego dostawcę) i dla konta własnego koordynatora (od tego jest „Nie pamiętasz hasła?” na stronie logowania); throttle `password_reset` (dla anonima z formularza publicznego) tego żądania nie dotyczy — koordynator jest już zalogowany; wpis audytowy `password.reset_sent` bez adresu i bez tokenu |
| **v0.30.1** | 2026-09-22 | rejestracja opiekuna szkolnego (prośba organizatora z 22.09.2026: rola istniała od wydania z 19.09, ale bez żadnego odnośnika) staje się **widoczna**, gdy przełącznik `supervisor_registration_enabled` jest włączony **na tej witrynie**: pole „Jesteś nauczycielem?” na `/register/` i na `/login/`, odnośnik powrotny „Jesteś uczniem?” na `/register/supervisor/` (bez zapytania na stronach, które go nie pokazują – leniwa wartość w `apps.web.context_processors.supervisor_registration`, per witryna jak `apps.cms.analytics`); formularz rejestracji zbiera odtąd też **zgody** (regulamin, RODO – te same dokumenty i wersje, co u uczestnika), zapisywane jako `ConsentRecord` (kolumna `supervisor`, migracja `accounts.0032`, ograniczenie „dokładnie jeden właściciel wpisu”); ten sam dowód wchodzi do eksportu danych konta (art. 20 RODO) i do anonimizacji (art. 17) – szkoła, telefon i zgody opiekuna znikają, a potwierdzenia udziału szkoły w edycji (`SchoolParticipation`) liczą się teraz do „śladu w zawodach”, więc samoobsługowe usunięcie takiego konta anonimizuje, a nie kasuje wiersza; strona „Konto zostało założone” tłumaczy nauczycielowi, co dalej (aktywacja, uczniowie wpisują jego adres w profilu, panel „Moi uczniowie”); lista `/coordinator/accounts/` pokazuje przy roli „opiekun szkolny” też nazwę szkoły, a `/admin/` dostał ekran opiekunów z podglądem dowodów zgód. Automat retencji (`/coordinator/retention/`) świadomie **nie** obejmuje jeszcze opiekunów (dług udokumentowany w `apps/accounts/retention.py` i w podręczniku organizatora § 9.1). Rejestr czynności przetwarzania w wersji **1.5**. Wdrożenie: okres rozruchu healthchecku `web` wydłużony z 40 s do 180 s (migracje i collectstatic przed startem gunicorna przekraczały go po większych wydaniach i `deploy.sh` przerywał się na „web unhealthy”) |
| **v0.30.0** | 2026-09-22 | rejestracja pyta o **pełną datę urodzenia** zamiast samego rocznika (zgłoszenie organizatora) i z niej rozstrzyga pełnoletność: dorosły = ma już za sobą dzień osiemnastych urodzin, liczony datą lokalną Europe/Warsaw, 29 lutego → 1 marca. Jedno miejsce reguły (`accounts.consents.is_minor`) obsługuje obie postacie danych: pełną datę dokładnie, a sam rocznik – starą, zachowawczą regułą „rok bieżący − rocznik ≤ 18”, bo profile sprzed tej zmiany dnia urodzin nie mają i nie będą miały (`Participant.birth_date` nullowalne, migracja `accounts.0031` bez backfillu; `birth_year` zostaje `NOT NULL` i jest liczony z daty przy każdym zapisie). Pole „Data urodzenia” (`<input type="date">`, zapis ISO i `DD.MM.RRRR`) w obu formularzach rejestracji, w edycji profilu i na ekranie koordynatora; `register-age.js` odsłania zgodę opiekuna według daty z serwera (`data-current-date`); API rejestracji przyjmuje `birth_date` i nadal `birth_year`; import listy klasowej przyjmuje kolumnę „data urodzenia” (ISO albo `DD.MM.RRRR`) i po staremu „rok urodzenia”; eksport koordynatora ma obie kolumny, eksporty dla podmiotów zewnętrznych nadal nie niosą wieku w ogóle; panel uczestnika prosi o uzupełnienie brakującej daty (audyt `participant.birth_date_completed`), anonimizacja czyści ją całą; `RegistrationProfile.require_birth_year` wreszcie działa i znaczy „data urodzenia wymagana” – wyłączony zostawia wiek nieznany, czyli traktuje każdego jak osobę niepełnoletnią. Rejestr czynności przetwarzania w wersji **1.3**. Na produkcji: `seed_legacy_content --only zgoda-opiekuna` (wzór oświadczenia prosi teraz o datę, a nie o rocznik) |
| **v0.29.2** | 2026-09-22 | link aktywacyjny i nieaktywowane konto żyją **24 godziny** zamiast czterech (`ACTIVATION_MAX_AGE`; decyzja organizatora) – list, komunikaty, FAQ, rejestr czynności i podręczniki mówią to samo. Na produkcji FAQ wymaga `seed_legacy_content --only faq` (o ile strona nie była redagowana w /cms/) |
| **v0.29.1** | 2026-09-22 | logotypy w sliderze mieszczą się w plakietkach (jednakowe plakietki 120×36 px, obraz skalowany w dół z zachowaniem proporcji); przeciąganie taśmy myszą/palcem przesuwa ją zamiast zaczynać natywne „przeciągnij i upuść” obrazka lub odnośnika, a kliknięcie po przeciągnięciu nie otwiera strony partnera |
| **v0.29.0** | 2026-09-21 | slider sponsorów w menu, po prawej stronie „FAQ” (uwaga organizatora z 21.09.2026, „jak na Olimpiadzie Biologicznej”): taśma logotypów (organizator zawsze pierwszy, potem partnerzy z `/partnerzy/` z logotypem, w kolejności strony) przesuwa się o jeden co `sponsor_slider_seconds` sekund, w nieskończonej pętli, bez skoku po okrążeniu (`static/js/sponsor-slider.js`, klasyczna sztuczka podwójnej taśmy, zero bibliotek); włącznik `sponsor_slider_enabled` i filtr poziomów współpracy `sponsor_slider_levels` na `cms.SiteSettings` (migracja `cms.0026`), ekran koordynatora `/coordinator/sponsor-slider/` (checkbox włącznika, sekundy, poziomy z liczbą partnerów przy każdym, podgląd oznaczający wpisy pominięte i dlaczego, audyt `site.sponsor_slider_updated`); partner pod tym samym adresem co organizator nie dubluje się w taśmie; ładunek z pamięcią podręczną per witryna (5 minut, unieważniana przy publikacji „Partnerzy” i przy zapisie ustawień) – warstwa zapytań kosztuje zero przy odświeżeniu w tym samym oknie; poniżej 900 px i przy `prefers-reduced-motion: reduce` slider nie przewija się (statyczny rząd pierwszych logotypów) |
| **v0.28.1** | 2026-09-21 | plik PDF ze składem komitetów **usunięty** (decyzja organizatora: strona `/dokumenty/komitety/` zostaje bez zmian, plik do pobrania i dokument w bibliotece Wagtaila znikają); `apps.cms.attachments.retire_documents` – funkcja współdzielona przez `seed_legacy_content` i nową komendę `manage.py retire_legacy_files` (zdejmuje plik na produkcji **bez** ponownego seedowania treści strony i bez nowej rewizji, `--dry-run` tylko liczy); `build_guardian_consent_pdf --document komitety` zostaje – składa wydruk na żądanie, ale wynik nie leży już w repozytorium ani nie jest nigdzie przypięty |
| **v0.28.0** | 2026-09-21 | **forum uczestników moderowane przez koordynatora** (prośba organizatora z 21.09.2026), za flagą `participant_forum` **domyślnie wyłączoną**: uczestnik dostaje `/forum/` (działy, wątek po 20 wpisów, nowy wątek, odpowiedź, zgłoszenie wpisu) i `/forum/mine/` – jedyne miejsce, w którym autor dowiaduje się o odrzuceniu i czyta uzasadnienie; koordynator `/coordinator/forum/` (kolejka wątków, wpisów i zgłoszeń, zbiorcze zatwierdzanie, spis wątków, wątek z każdym stanem, działy, ustawienia). Wypowiedź jest **zwykłym tekstem** (bez HTML-a i załączników, `linebreaksbr` + `urlize` z `rel="nofollow noopener noreferrer"`), podpisem jest **imię i inicjał nazwiska** – nigdy adres e-mail, szkoła ani kod `OLM-…` (klucz anonimowego oceniania). **Dopóki którykolwiek etap przyjmuje rozwiązania, obowiązuje moderacja wstępna niezależnie od ustawienia konkursu** (regulamin § 10 ust. 2 i § 17), a formularz pisania niesie ostrzeżenie z nazwą etapu. Własny wpis poprawialny przez 15 minut (poprawka opublikowanego wraca do kolejki), usunięcie miękkie (`HIDDEN`), limit `forum` 30/h; każda decyzja moderatora zostawia zdarzenie `forum.*` w audycie **bez kopii treści**. RODO: wiersz forum w rejestrze czynności (wersja **1.2**, warunkowy – wchodzi wyłącznie konkursom z włączoną flagą), wpisy w paczce `/account/export/`, anonimizacja konta zdejmuje podpis („Użytkownik usunięty”), treść zostaje częścią rozmowy. Bez powiadomień e-mail – sygnałem jest odznaka w menu panelu. Konkurs z domyślnymi przełącznikami nie zmienia się o ani jeden adres i ani jedną pozycję menu. Działy startowe jednym poleceniem: `manage.py seed_forum_categories --competition <slug>` zakłada dział „Ogólne” i po jednym dziale na każdy warsztat z tabeli harmonogramu strony „Warsztaty” (nazwa bez dopisku prowadzącego, termin i prowadzący w opisie) – idempotentne, rozpoznaje istniejące działy po slugu i nie nadpisuje redakcji koordynatora |
| **v0.27.4** | 2026-09-21 | uwagi organizatora z 21.09: **menu** w ustalonej kolejności – domek (strona główna), Komitety, Partnerzy, Harmonogram, Zadania, Wyniki, Warsztaty, Dokumenty, Kontakt, FAQ (`cms/context_processors.py`: `MENU_ORDER`; newsroom i archiwum zdjęte z menu, skład komitetów wyniesiony z listy „Dokumenty”, z której zniknęła też pozycja „Wszystkie dokumenty”); **aktualności** jako stały panel strony głównej z odnośnikiem do wszystkich; **linia czasu** przeniesiona z nagłówka na dół strony, nad stopkę (`.timeline-dock`); **stopka** bez „Dokumenty”, „Najczęstsze pytania”, „Statystyki” i „Rejestracja z kodem” (adresy działają dalej); **„Zgłoś problem”** – ten sam odnośnik do formularza w pasku konta (także dla niezalogowanych) i w stopce; **skład komitetów** z tytułami i afiliacjami (12 + 7 osób), PDF do pobrania składany z treści strony (`build_guardian_consent_pdf --document komitety`) |
| **v0.27.3** | 2026-09-21 | protokół etapu (eksport PDF) podpisuje **Komitet Sterujący** – przewodniczący, sekretarz i członek oraz zdanie o zgodności zestawienia (domknięcie v0.26.5: „Komitet Główny” nie występuje już w żadnym dokumencie); CI: shardy `pytest` układane po zmierzonym czasie (`backend/.test_durations`, `--splitting-algorithm least_duration`, odświeżanie: `docs/OPERACJE.md` § 10) |
| **v0.27.2** | 2026-09-21 | CI zielone i szybsze: zadanie `pytest` dostało środowisko obrazu (skompilowane katalogi tłumaczeń, `collectstatic`, klucz ≥ 50 znaków, poświadczenia S3/MinIO) – znika 12 stałych niepowodzeń i 4 błędy zależne od środowiska; `msgfmt` instaluje gettext; testy w 5 równoległych shardach (`pytest-split`) z jednym statusem zbiorczym „pytest (wynik zbiorczy)” – ok. 17 min zamiast ok. 30 (shardy jeszcze nierówne: brak pliku czasów). Bez zmian w aplikacji |
| **v0.27.1** | 2026-09-21 | regulamin – dwie poprawki brzmienia na polecenie organizatora: „Ministra właściwego ds. Edukacji” w ramce statusu i domknięty cudzysłów „ZOZ” w § 1 ust. 4 (strona, .docx, wyciąg tekstowy; PDF złożony z poprawionego .docx w Wordzie – 14 stron). W dokumencie Google obie zmiany stoją jako sugestie do zaakceptowania |
| **v0.27.0** | 2026-09-20 | przekazywanie przyjętych rozwiązań na skrzynkę organizatora: ekran `/coordinator/submission-forwarding/` (do pięciu adresów per konkurs, puste pole = wyłączone, audyt `competition.forwarding_updated` bez adresów), list z metryczką pracy i plikiem w załączniku wysyłany **po czystym skanie antywirusowym** z zadania Celery na kolejce `mail` (`apps/submissions/forwarding.py`), znacznik `SubmissionFile.forwarded_at` przeciw duplikatom, granica załącznika `SUBMISSION_FORWARD_MAX_ATTACHMENT_MB` (domyślnie 20 MB; powyżej list bez pliku i z odnośnikiem do panelu), limit koperty relaya podniesiony z 10 MB do 40 MiB, wiersz o tej drodze w rejestrze czynności przetwarzania (wersja 1.1) |
| **v0.26.5** | 2026-09-20 | dyplomy i zaświadczenia wystawia **Komitet Sterujący**: linia podpisu „Przewodniczący Komitetu Sterującego Olimpiady Kwantowej” (`SIGNATURE_LINE`, wartość początkowa nowych konkursów; migracja `tenancy.0007` podmienia nazwę komitetu w istniejących szablonach dokumentów). Dokumenty składają się przy pobraniu, więc już wystawione też dostają nowy podpis |
| **v0.26.4** | 2026-09-20 | uwagi organizatora z 20.09: **regulamin** w wersji z 20 września 2026 r. (eksport z Dokumentów Google: strona, PDF i .docx bez roboczych komentarzy; importer czyta tabele w `<th>` i sekcję „Status Olimpiady Kwantowej”; dokument bez metryki dostaje datę 20.09.2026; `TERMS_VERSION` = „z 20 września 2026” + migracja `accounts.0030` dla definicji zgody); **harmonogram i strona główna bez okna reklamacji**; komunikat o rejestracji „Zakończenie rejestracji: 28.02.2027” (migracja `cms.0025` podmienia tylko niezmienioną wartość domyślną); zakres Komitetu Merytorycznego + „rozpatrywanie odwołań”; instrukcja zgody opiekuna „Pobierz i wydrukuj formularz.”; komenda `replace_page_text` – poprawka jednego sformułowania na stronie prowadzonej w /cms/ (raport bez `--apply`, nowa rewizja + publikacja, odmowa przy szkicu) |
| **v0.26.3** | 2026-09-20 | wyszukiwarka szkół: lista podpowiedzi (szkół i miejscowości) rozwija się pod swoim polem – opakowania pól to `<div class="field">` zamiast `<p>`, bo parser HTML wyrzucał `<ul>` poza akapit i lista rozciągała się na całą szerokość strony |
| **v0.26.2** | 2026-09-19 | logo olimpiady na dyplomach i zaświadczeniach: znak z `static/img/logo-olimpiada-kwantowa@2x.png` w lewym górnym rogu każdego dokumentu (blok `logo` układu); logo szablonu graficznego ma pierwszeństwo, `show: false` je gasi, konkurs z własną marką dostaje swój logotyp (`competition_logo`) |
| **v0.26.1** | 2026-09-19 | poczta na `MAILERS` (Django 6.1) zamiast wycofywanych `EMAIL_*` – te same zmienne `.env` (`EMAIL_URL`, `EMAIL_TIMEOUT`), zero ostrzeżeń `RemovedInDjango70Warning`, testy kontrolne skrzynki (197 listów przed = 197 po, test po teście); `reportlab` 5.x (pin `>=5.0,<6`) – 12 rodzajów dokumentów bajt w bajt identycznych z 4.5.1; stopka dyplomu cofa się przed kodem QR (w domyślnym układzie kod zasłaniał końcówkę „Data wystawienia”) |
| **v0.26.0** | 2026-09-18 | aktualizacja frameworka: Django 5.1 → **6.1.1**, Wagtail 6.3 → **8.0**, DRF 3.15 → 3.18, celery 5.6, django-redis 7.0, drf-spectacular 0.30, django-environ 0.14, django-simple-captcha 0.7; `django.contrib.postgres` w `INSTALLED_APPS` (wymóg sprawdzenia `postgres.E005` dla indeksu wyszukiwania Wagtaila); ograniczenie `Django<6.1` w `django-celery-beat` 2.9.0 nadpisane w `[tool.uv] override-dependencies` (harmonogram sprawdzony: migracje, `DatabaseScheduler`, panel zadań, synchronizacja 8 wpisów); żadnej nowej migracji naszych aplikacji, budżety zapytań i złote testy bez zmian, 4623 testy; opis i wycofanie: `OPERACJE.md` § 9 |
| **v0.25.0** | 2026-09-18 | konkursy w subdomenach zakładane z panelu koordynatora (`/coordinator/competitions/new/` za flagą `competition_creation` i przełącznikiem `PLATFORM_SUBDOMAINS`, podgląd przed założeniem, twórca zostaje koordynatorem, certyfikat TLS na żądanie w Caddy za zgodą `/internal/tls-allowed`, nieznana subdomena = 404, usługa `apps/tenancy/provisioning.py`); uwagi organizatora z 18.09: karty partnerów i pas logotypów w równym rozmiarze, ZOZ ukryty (`LegacyPage.hidden`, seed nie publikuje strony wycofanej w /cms/), serwis tylko po polsku – przełącznik języka za opcją witryny `english_interface_enabled` (domyślnie wyłączona, polski niezależnie od przeglądarki, zapisane wybory zostają w bazie) |
| **v0.24.0** | 2026-09-18 | etap 2 (wydania E–K scalone): marka i dokumenty jako konfiguracja (`apps/tenancy/branding.py`, nadawca i podpisy listów z konkursu, `ConsentDefinition`, `DocumentTemplate` + `Certificate.template_version`, kalendarz/CAPTCHA/domena anonimowa, adres na stronie 500 z `ERROR_PAGE_CONTACT_EMAIL`), `/cms/` per konkurs (`cms:<slug>`, `scope_cms_access`), regiony jako drzewo per konkurs z 16 województwami i konfliktem interesów bez zmiany, typy placówek, profil rejestracji, słownik własny organizatora z importem CSV, wyszukiwarka dwóch słowników, import hurtowy z regionem/kategorią/placówką, edytor przebiegu (`PipelineStep`, `TransitionRule`, kategorie, komponenty etapu, punkty z rozmowy, drużyny, wagi i przesunięcie skali, remisy, role recenzenckie), kreator `/setup/` z tokenem, obrazy z CI do GHCR i `WEB_IMAGE`, profil compose dla operatora, Wagtail i18n z aliasami witryn, język listów per konkurs, wpisowe z rejestrem należności i webhookiem płatności, logistyka etapów stacjonarnych; 15 nowych flag konkursu domyślnie wyłączonych, Olimpiada Kwantowa bajt w bajt; E2E z drugim konkursem; 4508+ testów |
| **v0.23.0** | 2026-09-17 | etap 1, wydanie D (domknięcie): `NOT NULL` na kluczach `competition`, uczestnik per konkurs (`Participant.user` jako klucz obcy, `participant_for`), kod publiczny i numer dyplomu z prefiksami konkursu (`OLM-`/`OK` bez zmian dla Konkursu #1), jedna edycja bieżąca i unikalny rocznik per konkurs, kolumny konkursu w zgłoszeniach pomocy, audycie, kluczach API, webhookach, szablonach dyplomów i szablonach komentarzy, `create_competition` zakłada edycję, etapy i koordynatora, `check_memberships`, runbook drugiego konkursu; 3235 testów, 0 xfail |
| **v0.22.0** | 2026-09-17 | etap 1, wydanie C: odczyty w panelach zakresowane do konkursu (`for_competition`, `current_edition(competition)`), CMS per witryna Wagtaila, komunikaty z kolumną konkursu, strona „Ustawienia konkursu” za flagą `competition_settings_page`, 14 z 15 testów izolacji zielonych |
| **v0.21.0** | 2026-09-17 | etap 1, wydanie B: `accounts.Membership` i role per konkurs (za flagą `memberships_enforced`), nullowalne klucze obce `competition` z backfillem do Konkursu #1, `Caddyfile` generowany z `EXTRA_DOMAINS`, `pg_dump` przed migracjami w `deploy.sh`, testy izolacji i niezmienniczości |
| **v0.20.0** | 2026-09-17 | etap 1, wydanie A: model `tenancy.Competition` 1:1 z witryną Wagtaila, `CompetitionMiddleware` i `current_competition()`, Konkurs #1 utworzony z istniejącej witryny, `create_competition` |
| **v0.19.0** | 2026-09-17 | integracje (API, webhooki), testy online (`apps/quiz`), dyplomy 2.0 z pieczęcią PAdES, kopie zapasowe i monitoring, CI, 2FA za wyłączonym przełącznikiem, import grupowy uczniów, okręgi szkolne, dokumentacja i licencja |
| **v0.18.0** | 2026-09-17 | scalony zduplikowany `msgid` („wersja %(version)s”), który wywracał `msgfmt` przy budowaniu obrazu |
| **v0.17.1** | 2026-09-17 | wersja aplikacji w stopce i na `/status/` pochodzi z `APP_VERSION` (tag wdrożenia), a nie ze sztywnego „1.0” |
| **v0.17.0** | 2026-09-16 | przebudowa układu paneli, narzędzia RODO, zgłoszenia i pomoc (support desk), FAQ, ogłoszenia i strona statusu |
| **v0.16.0** | 2026-09-16 | drugi zestaw 15 funkcji paneli (koordynator, recenzent, uczestnik) |
| **v0.15.0** | 2026-09-16 | pobieranie prac i paczki ZIP, edytowalne skale punktacji, rozwiązania w JPEG oraz 15 funkcji paneli |
| **v0.14.0** | 2026-09-16 | ocenianie przed zamknięciem etapu („Zablokuj oddane prace do oceny”) |
| **v0.13.1** | 2026-09-16 | pasek linii czasu spoczywa jako cienka linia i rozwija się w dół po najechaniu; każdy warsztat jest osobnym wydarzeniem |
| **v0.13.0** | 2026-09-16 | pasek linii czasu w nagłówku i wydarzenia zarządzane przez koordynatora |
| **v0.12.0** | 2026-09-15 | recenzent poprawia własną recenzję, koordynator odbiera recenzje i zarządza wszystkimi kontami |
| **v0.11.1** | 2026-09-15 | województwo członka komitetu jest opcjonalne i nie warunkuje już przydziału |
| **v0.11.0** | 2026-09-15 | zaproszenia do komitetu e-mailem, z indywidualnym kodem dla każdego adresu |
| **v0.10.0** | 2026-09-15 | ręczny przydział recenzentów i korekty ocen przez koordynatora |
| **v0.9.4** | 2026-09-15 | polskie strony błędów: widok odmowy CSRF wyjaśniający przypadek nieaktualnego formularza (logowanie w innej karcie) z odnośnikiem ponowienia, plus 403/404/500 |
| **v0.9.3** | 2026-09-15 | tag Google w `<head>` na każdej stronie (z nonce, bez wyjątku w CSP) z Consent Mode v2: `analytics_storage` odmówione do czasu zgody |
| **v0.9.2** | 2026-09-15 | etap treningowy niesie wyłącznie przykładowe zadania organizatora (jeden wspólny PDF); generator PDF-ów wycofany |
| **v0.9.1** | 2026-09-15 | retencja danych zdarzeń Google Analytics ustalona na 14 miesięcy (decyzja organizatora) |
| **v0.9.0** | 2026-09-15 | Google Analytics 4 wyłącznie za wyraźną zgodą (pasek zgody, wycofanie ze stopki, anonimizacja IP, funkcje reklamowe wyłączone); polityka cookies 1.1 |
| **v0.8.6** | 2026-09-15 | plik weryfikacyjny Google Search Console serwowany spod własnego adresu |
| **v0.8.5** | 2026-09-15 | przykładowe zadania organizatora jako zadania treningowe 1–4, strona „sprawdź skrzynkę” po rejestracji, uporządkowana strona główna, TikTok i YouTube w odnośnikach społecznościowych |
| **v0.8.4** | 2026-09-13 | wyszukiwarka szkół przepisana na czysty JavaScript (bez zależności z CDN), łagodniejsza reguła szkoły spoza wykazu, oznaczenia pól wymaganych, odnośniki społecznościowe w ustawieniach serwisu |
| **v0.8.3** | 2026-09-12 | porządki w skrypcie testu e2e (długa asercja rozbita na dwie) |
| **v0.8.2** | 2026-09-12 | pozycje menu głównego (Zadania, Harmonogram, Warsztaty) przenoszą się do przyklejonego paska konta |
| **v0.8.1** | 2026-09-12 | logo w pasku konta, pasek przyklejony do góry okna (statyczny na telefonach), nawigacja serwisowa poniżej |
| **v0.8.0** | 2026-09-12 | aktywacja konta e-mailem (link 4 h, automatyczne czyszczenie nieaktywowanych kont, ręczna aktywacja przez koordynatora), telefon w profilu, edycja danych ze zmianą adresu, samodzielne usunięcie konta z anonimizacją; własna CAPTCHA, pułapka i minimalny czas wypełniania; etap treningowy |
| **v0.7.0** | 2026-09-12 | „Termin” stacjonarny pokazywany wyłącznie dla etapów z jawnymi dniami wydarzenia; seed treści nie odtwarza stron i aktualności skasowanych w `/cms/` |
| **v0.6.0** | 2026-09-10 | wersjonowane, linkowane zgody rejestracyjne (regulamin, RODO, zgoda opiekuna dla niepełnoletnich, publikacja nazwiska) z dowodem `ConsentRecord` i audytem; wzór zgody opiekuna do wydruku; `GET /api/auth/consents/` |
| **v0.5.2** | 2026-09-10 | koordynator steruje rejestracją uczestników (włącznik, godzina otwarcia i zamknięcia) jedną bramką dla formularza, API i logowania zewnętrznego |
| **v0.5.1** | 2026-09-10 | etapy nazwane Etap I/II/III, a nazewnictwo „okręg” zastąpione „województwem” w całym interfejsie |
| **v0.5.0** | 2026-09-08 | edycja terminów i zakładanie etapów w panelu (z blokadami domenowymi i audytem), zarządzanie zadaniami (treść PDF, formaty, limity), podgląd treści przed otwarciem etapu; harmonogram renderowany z bazy |
| **v0.4.1** | 2026-09-08 | logotypy partnerów, blok harmonogramu warsztatów, miejsce etapu (`Stage.location`) |
| **v0.4.0** | 2026-09-08 | logowanie przez Google i Facebooka (rejestracja przez adapter, ze zgodami RODO) oraz własna usługa poczty wychodzącej (Postfix + OpenDKIM, relay tylko wewnętrzny) z rekordami DNS wypisywanymi przez skrypt wdrożeniowy |
| **v0.3.3** | 2026-09-08 | reset hasła e-mailem dla wszystkich ról (limit prób, brak enumeracji kont, token jednorazowy 24 h, audyt), konfiguracja `EMAIL_URL`, mailpit w devie |
| **v0.3.2** | 2026-09-07 | oficjalny logotyp w nagłówku, favicon, ikona dotykowa i `og:image` składane z pliku organizatora |
| **v0.3.1** | 2026-09-07 | regulamin v1.0 z 2 września 2026 (model trzech etapów, PDF + DOCX), strona „Partnerzy” z poziomami partnerstwa |
| **v0.3.0** | 2026-09-07 | sekcja „Dokumenty”: strona indeksu, rozwijane menu bez JavaScriptu, wszystkie dokumenty organizatora pod `/dokumenty/`, trwałe przekierowania ze starych adresów |
| **v0.2.1** | 2026-09-07 | polityka RODO i standardy ochrony małoletnich przepisane 1:1 z PDF-ów organizatora jako HTML, uzupełniona strona komitetów |
| **v0.2.0** | 2026-09-06 | import treści starego serwisu: ustawienia marki, typ strony treści, strony informacyjne, polityki jako dokumenty, aktualności, sekcja kroków na stronie głównej, `seed_edition_kwantowa` |
| **v0.1.1** | 2026-09-06 | system projektowy: tokeny kolorów z wariantem ciemnym, samodzielnie serwowane kroje pisma, komponenty (karty, odznaki, tabele, linia czasu, odliczanie, segmenty punktów, strefa upuszczania), przebudowa szablonów wszystkich paneli |
| **v0.1.0** | 2026-09-05 | zamknięcie pierwszej fazy: API administracyjne Caddy'ego wyłącznie lokalnie, bezpiecznik produkcyjny dla klucza i poświadczeń S3, bezpieczne domyślne ciasteczka |

Nie zlecone: edytor przebiegu przenoszący „przypisz kategorie” do warstwy serwisów (drzewo CMS
konkursu pod prefiksem ścieżki, uwaga T43 – zrobione w v0.36.0). Forum w wersji pierwszej świadomie **nie miało**
powiadomień e-mail (doszły w v0.36.0, zbiorcze) i nadal nie ma wiadomości prywatnych, załączników, polubień ani
rankingów — uzasadnienie każdej z tych decyzji stoi w `PODRECZNIK-ORGANIZATORA.md` § 6.4.

## Tagi zadań

Poza wydaniami repozytorium niesie tagi `task/T-01` … `task/T-10` (z wariantami `-fix`) — punkty
kontrolne kolejnych zadań z `docs/tasks/`. Nie są wydaniami i nie należy ich podstawiać jako
`APP_VERSION`.
