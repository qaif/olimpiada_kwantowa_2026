# OPS-02: Monitoring błędów (GlitchTip) i alarmy dostępności

## 0. Cel i granice

Dziś o błędzie 500 wiemy z licznika watchdoga (`apps/core/alerts.py`: „≥ 10 odpowiedzi 5xx w 15 min”)
i z logu kontenera – bez śladu stosu, bez grupowania, bez informacji, który konkurs i które wydanie.
O niedostępności serwisu wiemy z Uptime Kumy (§ 3.1 OPERACJE), jeśli ktoś ją postawił i wyklikał.

Zadanie dokłada trzy rzeczy, każdą **wyłączoną domyślnie** (instalacja bez nowych wpisów w `.env`
działa i wygląda co do bajtu jak przed nim):

1. **Śledzenie błędów aplikacji** – klient `sentry-sdk` (zależność zatwierdzona przez użytkownika)
   w `web`, `worker` i `beat`, wysyłający zdarzenia do **własnej** instancji GlitchTip (otwarte
   oprogramowanie zgodne z protokołem Sentry) na tym samym serwerze. Żaden podmiot trzeci.
2. **GlitchTip** w profilu compose `monitoring` pod `errors.<SITE_DOMAIN>` (blok Caddy'ego za
   przełącznikiem `ERRORS_PROXY`, jak `live.` za `LIVEKIT_PROXY`), z własną bazą Postgresa
   w osobnej sieci, retencją 30 dni i pocztą przez relay serwisu (`mail:587`).
3. **Sprawdzanie dostępności z zewnątrz** – mały program bez zależności (`apps/monitoring/uptime.py`,
   wyłącznie biblioteka standardowa) w usłudze `uptime` tego samego profilu: oba serwisy, `/healthz/`,
   `/status.json`, LiveKit i GlitchTip (gdy włączone), certyfikaty TLS (< 14 dni), listy o awarii
   i powrocie z deduplikacją i wydłużającą się przerwą między przypomnieniami.
4. **Błędy JavaScriptu** (opcjonalnie, osobny przełącznik): własny, mały skrypt z naszego `static/`
   (bez CDN, bez SDK przeglądarkowego), wysyłający zdarzenie wprost do GlitchTip; CSP dostaje origin
   `errors.<domena>` w `connect-src` **wyłącznie** wtedy, gdy funkcja jest włączona.

Czego zadanie **nie** robi: nie zastępuje watchdoga (`apps/core/alerts.py`) ani Kumy – one widzą
inne rzeczy (dysk, kopie, kolejka). Nie dodaje metryk wydajności (APM): `SENTRY_TRACES_SAMPLE_RATE`
domyślnie 0. Nie wysyła nic poza serwer organizatora.

## 1. Zależności

- `sentry-sdk>=2.71,<2.72` w `backend/pyproject.toml` (zwykłe zależności – zdarzenia wysyła produkcja).
  Import **leniwy**: `config/settings/base.py` woła `apps.monitoring.sentry.init_sentry()` wyłącznie
  przy niepustym `SENTRY_DSN`; bez niego moduł `sentry_sdk` nie jest nawet importowany (test).
- Obraz `glitchtip/glitchtip:6.2.6@sha256:…` (oficjalny, przypięty tagiem i skrótem). Baza GlitchTipa
  na obrazie `postgres:18-alpine` – tym samym, którego używa już `db`, więc bez nowego obrazu.
  GlitchTip 6 nie potrzebuje Valkey/Redisa (kolejka i cache w Postgresie, `VALKEY_URL=""`), więc
  pytanie „czy współdzielić Redisa platformy” znika: **nie** – Redis platformy jest brokerem Celery
  i zostaje wyłącznie w sieci `cache`.
- Usługa `uptime` chodzi na obrazie aplikacji (`WEB_IMAGE`), bez `env_file` i bez sekretów.

## 2. Klient błędów (`apps/monitoring/sentry.py`)

`init_sentry(dsn, release, environment, sample_rate, traces_sample_rate)`:

- integracje: Django, Celery, Redis (+ domyślne, w tym logowanie: `logger.error` → zdarzenie),
- `send_default_pii=False`, `include_local_variables=False` (**globalnie** – zmienne lokalne
  w ramkach to dokładnie to miejsce, w którym leżą dane paszportowe, hasła i tokeny),
  `max_request_body_size="never"`, `attach_stacktrace=False`,
- `release = APP_VERSION`, `environment = SENTRY_ENVIRONMENT` (domyślnie `production`),
  `sample_rate = SENTRY_SAMPLE_RATE` (1.0), `traces_sample_rate = SENTRY_TRACES_SAMPLE_RATE` (0.0),
- `before_send`, `before_send_transaction`, `before_breadcrumb` = jeden filtr (`scrub_event`):
  - `request`: zostaje metoda, ścieżka URL **bez zapytania** i lista dozwolonych nagłówków
    (`Host`, `Content-Type`, `Content-Length`, `Accept`, `Accept-Language`, `X-Forwarded-Proto`);
    znikają `data`, `cookies`, `query_string`, `env` (adres IP), pozostałe nagłówki
    (`Authorization`, `Cookie`, `X-Maintenance-Bypass`, `Referer` z tokenem…),
  - `user` – w całości (także identyfikator: konto ucznia to osoba niepełnoletnia),
  - klucze wrażliwe w `extra`, `contexts`, danych okruszków i `logentry.params` – po fragmencie
    nazwy: `passport`, `pesel`, `health`, `diet`, `allerg`, `medical`, `birth`, `phone`, `email`,
    `mail`, `address`, `file`, `password`, `secret`, `token`, `key`, `auth`, `cookie`, `session`,
    `csrf`, `name`, `guardian`, `parent`, `visa`, `document`, `iban`, `args`, `kwargs`, `data`,
    `body` → `[Filtered]` (zostają klucze strukturalne wydarzenia – filtr nie dotyka ramek stosu),
  - w **każdym** napisie zdarzenia (komunikat wyjątku, wiadomość logu, okruszek, SQL): adresy e-mail,
    ciągi 11 cyfr (PESEL), numery telefonu, wartości parametrów `token|key|code|secret|signature|
    password|sig|jwt` w adresach, `Bearer …`, `Key (kolumna)=(wartość)` z błędów Postgresa → `[Filtered]`,
  - ramki stosu: `vars` usuwane, nawet gdyby ktoś włączył zmienne lokalne,
- tag `competition` = **slug** konkursu (bez nazwy, bez identyfikatora), ustawiany przez
  `apps.monitoring.middleware.ErrorTrackingTagMiddleware` – middleware dokładany do `MIDDLEWARE`
  tylko przy włączonym DSN (za `CompetitionMiddleware`).

## 3. GlitchTip w compose (profil `monitoring`)

| Usługa | Obraz | Sieci | Limit pamięci |
|---|---|---|---|
| `glitchtip` (web + worker w jednym procesie, `SERVER_ROLE=all_in_one`) | `glitchtip/glitchtip:6.2.6@sha256:…` | `errors_front` (Caddy), `errors_ingest` (web/worker/beat, DSN wewnętrzny), `errors_egress` (wyjście, relay z jednym nadawcą), `errors` – **nie** `edge` (§ 9) | 768m |
| `glitchtip-db` | `postgres:18-alpine` | wyłącznie `errors` (`internal: true`) | 512m |

Odstępstwo od „web + worker”: GlitchTip 6 łączy obie role w jednym procesie i wycofuje podział
(`bin/start.sh` w gałęzi głównej projektu odrzuca `SERVER_ROLE=worker`). Dwa kontenery z rolami
byłyby konfiguracją, której następna wersja nie uruchomi.

- `GLITCHTIP_DOMAIN=https://errors.${SITE_DOMAIN}`, `ALLOWED_HOSTS` = ta nazwa + `glitchtip`
  + `127.0.0.1`, `CSRF_TRUSTED_ORIGINS=https://errors.${SITE_DOMAIN}`,
- `EMAIL_URL=smtp://mail:587`, `DEFAULT_FROM_EMAIL=glitchtip@${SITE_DOMAIN}` (relay przyjmuje kopertę
  wyłącznie z domeny serwisu),
- `GLITCHTIP_RETENTION_DAYS=30` (zdarzenia, transakcje, pliki, logi, uptime), `GLITCHTIP_ENABLE_LOGS=False`,
  `GLITCHTIP_ENABLE_UPTIME=False` (dostępność sprawdza `uptime`, § 4), `ENABLE_USER_REGISTRATION=False`,
  `ENABLE_ORGANIZATION_CREATION=False`, `ENABLE_ADMIN=False`, `ENABLE_OPENAPI=False`,
- sekrety: `GLITCHTIP_SECRET_KEY`, `GLITCHTIP_DB_PASSWORD` w `.env` (bez `${…:?}` – interpolacja
  obejmuje cały plik niezależnie od profilu; brak wykryje sam GlitchTip przy starcie),
- **bez `env_file: .env`** – GlitchTip nie widzi sekretów platformy,
- `read_only` nie (GlitchTip pisze `uploads/`), ale `no-new-privileges`, `cap_drop: [ALL]` dla `glitchtip`.

Caddy: `ERRORS_PROXY=1` → `scripts/render_caddyfile.sh` dokłada na końcu blok `errors.{$SITE_DOMAIN}`
→ `glitchtip:8000` (HSTS, `nosniff`, `X-Frame-Options DENY`, `Referrer-Policy same-origin`, limit
żądania 10 MB, bez `import maintenance` – zgłoszenia mają przechodzić także w czasie przerwy).
Wyłączony (domyślnie) = wynik bajt w bajt jak dotąd; przy `PLATFORM_SUBDOMAINS=1` – zwykły certyfikat
(przypięcie polityki TLS jak w `live.`).

## 4. Sprawdzanie dostępności (`apps/monitoring/uptime.py`, usługa `uptime`)

Wyłącznie biblioteka standardowa (urllib, ssl, smtplib, json): ten sam plik da się skopiować na
**inną** maszynę i uruchomić z crona (`python3 uptime.py --once`) – i to jest zalecane, bo monitor
na tym samym serwerze nie zauważy śmierci serwera (§ 44.5 OPERACJE).

- Cele domyślne (z `SITE_DOMAIN`, `EXTRA_DOMAINS` bez `www.`, `LIVEKIT_URL`, `ERRORS_PROXY`):
  `https://<domena>/` (2xx/3xx), `https://<domena>/healthz/` (200 i `"status": "ok"`),
  `https://<SITE_DOMAIN>/status.json` (`"status": "ok"`), `https://live.<…>/` (200),
  `https://errors.<SITE_DOMAIN>/_health/` (200); plus `UPTIME_EXTRA_URLS` (np. notebook).
  `UPTIME_URLS` podane wprost zastępuje listę domyślną.
- TLS: dla każdego hosta HTTPS – dni do wygaśnięcia; `< UPTIME_TLS_WARN_DAYS` (14) = alarm.
- Logika alarmów (stan w pliku JSON na wolumenie, zapis atomowy):
  - awaria = `UPTIME_FAIL_THRESHOLD` (3) kolejnych porażek; jeden list „AWARIA”,
  - przypomnienia z przerwą rosnącą 1 h → 2 h → 4 h … do 24 h (`UPTIME_REMIND_*`),
  - powrót = `UPTIME_RECOVER_THRESHOLD` (2) kolejne sukcesy; list „POWRÓT” **tylko**, jeśli wcześniej
    poszedł list o awarii (krótkie potknięcie nie daje żadnego listu),
  - certyfikat: list przy pierwszym wykryciu, potem raz na 24 h; odnowienie – list „certyfikat OK”,
  - wszystkie zmiany jednego przebiegu = **jeden** list (śmierć hosta to jeden list, nie dziesięć),
  - twardy limit `UPTIME_MAX_MAILS_PER_HOUR` (6) – nadmiar odkładany do następnego przebiegu.
- Poczta: `UPTIME_ALERT_EMAILS` (domyślnie `ALERT_EMAILS`), relay `mail:587` bez uwierzytelnienia
  (sieć `edge` jest w `mynetworks` Postfiksa), nadawca `uptime@<SITE_DOMAIN>`. Pusta lista
  odbiorców = sprawdzanie i log bez wysyłki.

## 5. Błędy JavaScriptu (`SENTRY_BROWSER=1`)

- `static/monitoring/errors.js` (~100 linii, bez zależności): `window.onerror`
  i `unhandledrejection` → koperta Sentry (`/api/<projekt>/envelope/?sentry_key=…`) wysyłana `fetch`
  z `keepalive` i `Content-Type: text/plain` (bez preflightu CORS); najwyżej 5 zdarzeń na stronę,
  duplikaty pomijane; adres strony bez zapytania i fragmentu; komunikat przez ten sam filtr
  (e-mail, PESEL, telefon, tokeny); tag `competition` ze slugu.
- Znacznik `<script nonce src=…>` z szablonu `base.html` (`{% error_tracking_loader %}`) – pusty napis,
  gdy funkcja wyłączona, więc strona bez niej jest co do bajtu ta sama.
- DSN przeglądarki: `SENTRY_BROWSER_DSN` albo – gdy pusty – `SENTRY_DSN`. Origin z DSN dochodzi
  do `connect-src` wyłącznie przy włączonej funkcji (test: nagłówek bez funkcji niezmieniony).

## 6. RODO

Rejestr czynności 1.21: wiersz warunkowy „Monitorowanie błędów aplikacji” (`apps.monitoring.register`)
– wyłącznie przy niepustym `SENTRY_DSN`. Odbiorca: **wewnętrzny** podmiot przetwarzający (własna
instancja GlitchTip na serwerze organizatora, hosting jak wszędzie – Contabo, Niemcy), bez przekazania
do państwa trzeciego. Retencja 30 dni. Dane: techniczne (ślad stosu bez zmiennych, ścieżka, wydanie,
slug konkursu), bez IP, ciasteczek, treści żądań i identyfikatora konta.

## 7. Testy

- `apps/monitoring/tests/`: brak DSN = brak importu `sentry_sdk` (podproces), filtr (paszport, dane
  zdrowotne, token, e-mail, PESEL, nagłówki, ciasteczka, zapytanie, zmienne ramek, `celery-job`),
  inicjalizacja z DSN (podstawiony transport – nic nie wychodzi z hosta, zdarzenie przefiltrowane),
  tag konkursu, CSP bez funkcji niezmienione i z funkcją z originem, szablon bez funkcji bez znacznika,
  rejestr warunkowy, logika `uptime` (próg, przypomnienia, powrót, TLS, jeden list na przebieg, limit).
- `scripts/tests/render_caddyfile_test.sh`: `ERRORS_PROXY` (bajt w bajt przy 0, blok przy 1, przypięcie
  TLS przy subdomenach, błąd przy wartości spoza listy).
- `scripts/tests/compose_profiles_test.sh`: profil `monitoring` dokłada dokładnie `glitchtip`,
  `glitchtip-db`, `uptime`; `glitchtip-db` wyłącznie w `errors`; nikt nowy w `cache`.

## 8. Dokumentacja

OPERACJE § 44 „Monitoring błędów i dostępności”, `.env.example`, CHANGELOG `[Unreleased]`.

## 9. Poprawki po przeglądzie (krytyk, PR #72)

| ID | Zmiana |
|---|---|
| H1 | Adres żądania = wzorzec trasy Django (`transaction`, źródło `route`); bez trasy – `mask_path`: segmenty-tokeny (≥ 12 znaków base64url z cyfrą/wielką literą/`_`/`=`) i wszystko po `reset/`, `zgoda/`, `zaproszenie/`, `activate/`, `unsubscribe/`, `verify/`, `dyplomy/`, `new/` …; to samo w okruszkach (`data.url`, webhooki), spanach, `next=`, `repr` żądania i w JS (`maskPath`). |
| H2 | Cała linia `DETAIL:` i `Failing row contains (…)` → `[Filtered]`. |
| H3 | Sieć `errors_ingest` (web, worker, beat, glitchtip) – DSN wewnętrzny działa z każdego procesu. |
| H4 | GlitchTip poza `edge`: `errors_front` (proxy), `errors_ingest`, `errors_egress` (wyjście), `errors` (baza). Relay: `mynetworks` z własnej zmiennej `MAIL_CLIENT_NETWORKS` (domyślnie dotychczasowa wartość) + podsieć `errors_egress`, z której przechodzi **tylko** nadawca `glitchtip@<SITE_DOMAIN>` (Postfix `smtpd_sender_restrictions` z tabelami inline). |
| H5 | Napis przycięty do 2 KB, wyrażenia zaczynają na granicy ciągu (spojrzenie wstecz), powtórzenia ograniczone; test czasu (< 50 ms). JS: bez spojrzeń wstecz (stare Safari), przycięcie 1000 znaków. |
| M1 | `default_integrations=False`, `auto_enabling_integrations=False`; jawnie: logging, stdlib, excepthook, dedupe, atexit, threading, Django, Celery, Redis. |
| M2 | Redis: sama nazwa polecenia; IPv4/IPv6 w `scrub_text`; uczciwy opis wiersza rejestru (pseudonimizacja). |
| M3 | `uptime.py` bez `except A, B:` (stała krotki), test `ast.parse(feature_version=(3, 10))`. |
| M4 | Limit zdarzeń klucza w GlitchTipie – obowiązkowy krok OPERACJE § 44.2; Caddy 2.8 bez modułu limitu (udokumentowane); dysk – alarm watchdoga. |
| M5 | Wiersz rejestru przy `SENTRY_DSN` **albo** działającym loaderze przeglądarki. |
| L1 | Kontrola liczby kont przed `ERRORS_PROXY=1`; `deploy.sh` ostrzega, gdy kont brak. |
| L2 | TOTP w GlitchTipie; opcjonalne `ERRORS_UI_ALLOW` (403 dla panelu spoza listy); konto dyżurnego bez hasła w powłoce. |
| L3 | `sys.argv`, `request`, `query`, `fragment` w filtrze kluczy; `ArgvIntegration` wyłączona. |
| L4 | DSN przeglądarki tylko `https` i nazwa z kropką. |
| L5 | `UPTIME_MAINTENANCE_FILE` (plik `on` przerwy planowej) wycisza porażki HTTP. |
| L7 | Sprawdzone w obrazie 6.2.6: `GLITCHTIP_RETENTION_DAYS=30` ustawia retencję zdarzeń, transakcji, plików, logów i uptime; `/code/uploads` zapisywalny (uid 5000). |
