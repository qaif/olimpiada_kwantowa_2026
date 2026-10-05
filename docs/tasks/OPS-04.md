# OPS-04: Kontrola dymna po wdrożeniu i szybkie wycofanie

## 0. Cel i granice (polecenie organizatora, 5.10.2026)

Po każdym wdrożeniu **sprawdzić z zewnątrz**, że serwis odpowiada tak, jak odpowiadał, a gdy nie –
w ciągu minut wrócić do poprzednich obrazów aplikacji, bez budowania i bez dotykania danych.

Stan przed zadaniem:

| Luka | Skutek dziś |
|---|---|
| krok 5/8 czeka na `web=healthy`, ale po 5 min **nie kończy się błędem** | wdrożenie „zielone” przy kontenerze, który nie wstał |
| `/healthz/` sprawdza bazę i Redisa, nie strony | zepsuty szablon, brak plików statycznych, zła CSP, arkusz motywu 404 – nikt nie wie do pierwszego zgłoszenia |
| wycofanie = ręczne przestawienie `APP_VERSION` i `up -d` (opis w komentarzu „Porządki”) | w nocy, pod presją, z pamięci; obraz poprzedni bywa już skasowany (`docker image prune` po wdrożeniu tej samej wersji) |
| brak zapisu „co działało przed wdrożeniem” | nie wiadomo, do czego wracać ani czy wolno (migracje) |
| krok „dj.” kończy prawie każde wdrożenie błędem `Lista konkursów z API niedostępna: timeout` | czerwone wdrożenie przy działającym serwisie, ręczne ponawianie `sync_competitions` |

Czego zadanie **nie** robi:
- nie wycofuje **bazy** ani wolumenów – nigdy (odtworzenie z kopii przed migracjami to decyzja
  człowieka, OPERACJE § 2 i § 48.5),
- nie wycofuje kodu w `/opt/olimpiada` ani konfiguracji proxy – wraca wyłącznie obraz kontenerów
  `web`, `worker`, `beat` (i `djcms`, gdy włączony). Pełny powrót = wdrożenie poprzedniego tagu,
- nie loguje się do serwisu (kontrola wyłącznie odczytem: `GET`, bez kont, bez formularzy),
- nie zmienia stanu strony „Prace techniczne” (wycofanie nie włącza jej i nie wyłącza).

## 1. `scripts/smoke.sh` – kontrola dymna (tylko odczyt)

```
scripts/smoke.sh [--server KATALOG] [--resolve IP] [--expect-version V] [--insecure]
                 [--livekit URL] [--djcms] [--report PLIK] [URL…]
```

- **Z argumentami** (`https://olimpiadakwantowa.pl https://iqo.example`) – z dowolnej maszyny.
- **`--server /opt/olimpiada`** (tak woła wdrożenie) – hosty z `.env` (`SITE_DOMAIN`) i z
  `manage.py check_domains --hosts` (aktywne konkursy z własnym hostem; obraz bez tej opcji albo
  niedziałający `web` = `SITE_DOMAIN` + `EXTRA_DOMAINS`), żądania przez proxy **tego** serwera
  (`curl --resolve <host>:<port>:127.0.0.1` – bez DNS i bez zawracania przez publiczny adres),
  przepustka strony prac technicznych (nagłówek z `-K -`, nie w argumentach), LiveKit przy
  `LIVEKIT_PROXY=1`, djcms przy `DJCMS_ENABLED=1`.

Sprawdzenia dla każdego hosta (`B` = adres bazowy, z prefiksem ścieżki, jeśli podany):

| # | Żądanie | Warunek |
|---|---|---|
| 1 | `GET B/` (przekierowania ≤ 5, wyłącznie w obrębie hosta – przepustka nie wychodzi poza niego) | 200, nagłówek `Content-Security-Policy` |
| 2 | `GET B<strona>` dla `SMOKE_PAGES` (domyślnie `/status/ /password-reset/`) | 200 |
| 3 | `GET /healthz/` | 200 i `"status": "ok"` |
| 4 | `GET /status.json` | 200 i `"status": "ok"`; inna `version` niż `--expect-version` = **ostrzeżenie** (odpowiedź jest buforowana 30 s) |
| 5 | `GET B/login/` | 200, pole `csrfmiddlewaretoken`, ciasteczko `csrftoken`, nagłówek CSP |
| 6 | plik statyczny z hashem manifestu (`/static/….<12 hex>.css|js`) **z HTML-a logowania** | 200 |
| 7 | arkusze motywu (`<link rel="stylesheet">` z `/themes/` albo `/_theme/`) z HTML-a logowania | 200 każdy; brak motywu = informacja |
| 8 | `GET /api/competitions/editions/current/` (`SMOKE_API_PATH`) | 200 albo 404, `Content-Type: application/json` |

Globalnie: `https://live.<domena>/` < 500 (LiveKit), `/djcms/healthz/` = 200 i `/djcms/preview/`
< 500 (djcms). Plik statyczny i motyw – ze strony logowania, a nie z głównej: strona główna bywa
w buforze całych stron (OPERACJE § 13, 120 s) z odnośnikami do plików sprzed `collectstatic --clear`.

Każde sprawdzenie: do `SMOKE_RETRIES` (3) prób co `SMOKE_RETRY_DELAY` (5 s), limit `SMOKE_TIMEOUT`
(15 s), czas w wydruku. Błąd TLS (curl 35/60) hosta innego niż `SITE_DOMAIN` = ostrzeżenie
(certyfikat nie jest sprawą wydania). Kod wyjścia: 0 – przeszło, 1 – co najmniej jeden błąd,
2 – złe wywołanie. Podsumowanie: liczba sprawdzeń, błędów, ostrzeżeń, czas i lista błędów.

## 2. Migawka i wycofanie (`scripts/rollback.sh`, na serwerze)

Stan w `<REMOTE_DIR>/deploy-state/` (krok 2/8 go omija, katalog `700`):

| Plik | Kto pisze | Treść |
|---|---|---|
| `previous.env` | `snapshot` (krok 2a/8) | wersja i commit sprzed wdrożenia, `WEB_IMAGE`/`DJCMS_IMAGE` z `.env`, identyfikatory obrazów działających kontenerów |
| `migrations-before.txt`, `djcms-migrations-before.txt` | `snapshot` | zastosowane migracje (`app.nazwa`) |
| `deployed.env` | `record-success` (po udanej kontroli) | wersja, commit, obrazy, czas |
| `last-rollback.env`, `env.before-rollback` | `run` | co wycofano; `.env` sprzed wycofania (`600`) |
| `last-smoke.txt` | `smoke.sh --report` | wydruk ostatniej kontroli (treść listu) |

- `snapshot` – czyści poprzednią migawkę (stara nie może posłużyć do wycofania), taguje obraz
  **działającego** kontenera `web` jako `olimpiada/web:previous` (i `djcms` jako
  `olimpiada/djcms:previous`) – po identyfikatorze, więc przebudowa tej samej wersji i `image prune`
  go nie usuną; zapisuje zastosowane migracje.
- **Migracje** czytane z tabeli `django_migrations` (`psql` w kontenerze `db`, `SELECT`) – to te same
  wiersze, które `showmigrations --plan` pokazuje jako `[X]`, ale odczyt nie wymaga działającego
  `web`, a po nieudanym wdrożeniu nowy `web` zwykle nie działa. Odstępstwo od „diff
  `showmigrations --plan`” z polecenia – świadome.
- `decide` – `auto` (kod 0), `manual` (3: nowe migracje albo stan migracji nieznany), `impossible`
  (4: brak migawki / obrazu `:previous`).
- `run [--yes] [--allow-migrations]` – kopia `.env`, `docker tag …:previous` na tag poprzedniej wersji
  (albo poprzedni `WEB_IMAGE`), w `.env` wyłącznie `APP_VERSION`, `WEB_IMAGE`, `DJCMS_IMAGE`,
  `docker compose up -d --no-deps --no-build web worker beat [djcms]`, czekanie na `healthy`.
  **Bez** `down`, bez `-v`, bez `db`/`redis`/`minio`/`proxy`. Entrypoint `web` poprzedniego obrazu
  robi `migrate` (no-op – jego migracje są zastosowane) i `collectstatic --clear` (pliki statyczne
  poprzedniej wersji – wolumen `static_files` jest pochodną obrazu).
- `auto` (woła wdrożenie po nieudanej kontroli) – `decide`; przy `auto`: `run --yes`, ponowna
  kontrola z oczekiwaną poprzednią wersją, list; przy `manual`/`impossible`: **bez** wycofania,
  strona prac technicznych bez zmian (wyłączona), list i instrukcja ręczna na ekranie.
  Kody: 10 – wycofano i kontrola przeszła, 12 – wycofano, kontrola nadal nie przechodzi,
  11 – nie wycofano.
- List do `ALERT_EMAILS` (`send_mail` w działającym `web`; gdy nie działa – jednorazowy kontener
  obrazu `web` z `RUN_MIGRATIONS=0 RUN_COLLECTSTATIC=0`). Bez sekretów w treści.

## 3. Zmiany w `scripts/deploy.sh`

- krok 2/8 omija `deploy-state`; nowy krok **2a/8** `rollback.sh snapshot` (przed zmianą
  `APP_VERSION` w kroku 3/8 i przed budowaniem). Nieudana migawka nie zatrzymuje wdrożenia, tylko
  wyłącza wycofanie automatyczne (komunikat),
- nowy krok **5b/8** po 5/8 (i 5a/8): `smoke.sh --server … --expect-version $APP_VERSION --report …`;
  porażka → `rollback.sh auto` → ramka w logu, kod 1, kroki 6–8 i „dj.” się nie wykonują. Sukces →
  `rollback.sh record-success` (z `git rev-parse HEAD`). Furtka operatora: `DEPLOY_SMOKE=warn`
  (porażka = ostrzeżenie, wdrożenie idzie dalej, bez wycofania) i `DEPLOY_SMOKE=0` (bez kontroli) –
  na wypadek, gdy kontrola myli się z powodu niezwiązanego z wydaniem,
- pułapka EXIT: błąd po starcie nowych kontenerów (4b) a przed kontrolą – podpowiedź
  `rollback.sh run`,
- „Porządki”: tag `olimpiada/web:previous` nie liczy się do dwóch zostawianych tagów (i nie jest
  kasowany),
- krok „dj.”: `sync_competitions` ponawiany przy `Lista konkursów z API niedostępna` (4 próby,
  przerwy `DJCMS_SYNC_RETRY_DELAYS`, domyślnie 5/10/20 s). Inny błąd (np. importu) – bez ponawiania.

## 4. Przyczyna limitu czasu `sync_competitions`

`MainApi.fetch_competitions()` (komendy: `sync_competitions`, `import_cms_bundle`) korzystał z tej
samej ścieżki co odsłona strony: 1 s na operację gniazda, 2 s na całość (`DJCMS_API_TIMEOUT`, § 2
DJ-01 – strona djcms nie może czekać). Krok „dj.” biegnie tuż po restarcie `web`: pierwsze żądanie
do świeżych procesów gunicorna (leniwe importy, zimny bufor, pula połączeń) trwa dłużej niż 1 s,
więc komenda kończyła się `timeout`, a ręczne ponowienie minutę później przechodziło. Poprawka:
komendy dostają własny limit `COMMAND_TIMEOUT_SECONDS = 30` (gniazdo i całość); odsłony stron –
bez zmian. Ponawianie w `deploy.sh` zostaje jako druga linia (restart `web` w trakcie kroku).

## 5. Bezpieczeństwo

- kontrola wyłącznie `GET`, bez kont i haseł; przepustka prac technicznych przez `-K -` (nie w `ps`),
- wycofanie nie dotyka bazy ani wolumenów danych; `.env` zmieniany tylko w trzech kluczach, kopia
  `600` w katalogu `700`; obraz z rejestru nie jest pobierany (`--no-build`, obraz lokalny po ID),
- automat **nigdy** nie wraca do kodu starszego niż schemat bazy: każda nowa migracja (także djcms)
  albo nieznany stan migracji = wyłącznie człowiek (`run --allow-migrations` albo odtworzenie kopii),
- list alarmowy bez sekretów (wersje, host, wydruk kontroli – same adresy i kody HTTP).

## 6. Testy

- `scripts/tests/smoke_test.sh` – atrapa `curl` z odpowiedziami z katalogu: wydobycie pliku
  statycznego z hashem, arkuszy motywu (`&amp;`, adresy względne i bezwzględne), CSRF, CSP, JSON
  `/healthz/` i `/status.json`, ostrzeżenie wersji, ponowienia, błąd TLS jako ostrzeżenie, kody
  wyjścia i podsumowanie, tryb `--server` (hosty z `check_domains --hosts`, `--resolve`, przepustka
  poza argumentami, LiveKit, djcms),
- `scripts/tests/rollback_test.sh` – atrapa `docker`: migawka (tag po ID, czyszczenie starej),
  decyzja (bez migracji / nowa migracja główna / nowa migracja djcms / stan nieznany / brak migawki),
  `run` (tylko trzy klucze `.env`, polecenia dockera bez `db`/`-v`/`down`), `auto` (kody 10/11/12,
  list), `--allow-migrations`,
- `scripts/tests/deploy_djcms_test.sh` – pełny przebieg `deploy.sh` z nowymi krokami; kontrola
  nieudana → wycofanie; kontrola nieudana po migracji → bez wycofania, kod 1; ponawianie
  `sync_competitions`,
- pytest: `djcms/apps/live/tests/test_client.py` (limit komend), `apps/tenancy/tests/test_check_domains.py`
  (`--hosts`).

## 7. Kroki operatora na produkcji

Opisane w `docs/OPERACJE.md` § 48. Już pierwsze wdrożenie tej wersji robi migawkę (krok 2a/8
biegnie skryptem z właśnie wysłanego kodu, a kontener `web` poprzedniej wersji działa), więc
wycofanie automatyczne obejmuje także je.
