# Podręcznik administratora

Dla osoby, która **stawia i utrzymuje** serwis: instalacja, wdrożenie, DNS, poczta, kopie zapasowe,
aktualizacje, awarie, dane osobowe. Prowadzenie zawodów opisuje
[`PODRECZNIK-ORGANIZATORA.md`](PODRECZNIK-ORGANIZATORA.md); architektura i uzasadnienia decyzji —
[`PROJEKT.md`](PROJEKT.md); checklista bezpieczeństwa — [`SECURITY_CHECKLIST.md`](SECURITY_CHECKLIST.md).
Szczegóły każdej procedury (z wyjściami poleceń) są w [`../README.md`](../README.md), do którego ten
dokument odsyła numerami sekcji.

Platforma jest **jedną** aplikacją Django 5.1 (+ DRF, HTMX, Wagtail 6.3) uruchamianą przez Docker
Compose. Nie ma drugiego systemu tożsamości ani drugiego panelu: redaktor, koordynator, recenzent,
opiekun i uczestnik logują się tym samym kontem, a uprawnienia rozstrzygają grupy Django.

---

## 1. Czego potrzebujesz

| Element | Wymaganie | Uwagi |
|---|---|---|
| Docker Engine / Docker Desktop | ≥ 24, z `docker compose` v2 | bez Dockera systemu nie da się sensownie uruchomić |
| RAM | ≥ 6 GB dla Dockera | ClamAV trzyma bazę sygnatur w pamięci (~1,5 GB) |
| Dysk | ≥ 10 GB | obrazy, sygnatury ClamAV, wolumeny danych |
| Powłoka | Git Bash / WSL / dowolna POSIX-owa | skrypty w `scripts/` są bashowe |
| Serwer produkcyjny | Ubuntu z publicznym IPv4, porty 22/80/443/9000 | skrypt wdrożeniowy sam instaluje Dockera i `ufw` |
| Domena | własna, z dostępem do strefy DNS | potrzebne **co najmniej dwa** rekordy A (niżej) |
| Python 3.14 + `ruff` | opcjonalnie | wyłącznie do lintu poza kontenerem (ta sama wersja co obraz) |

Deadline, skan antywirusowy i prywatny magazyn plików wymagają Postgresa, Redisa, MinIO i ClamAV-a —
atrapy nie wystarczą, więc środowisko deweloperskie stoi na tych samych usługach co produkcja.

### Usługi w `docker-compose.yml`

`proxy` (Caddy: TLS, limit rozmiaru żądania), `web` (gunicorn + UvicornWorker), `worker` i `beat`
(Celery), `db` (PostgreSQL), `redis` (broker i cache), `minio` + `minio-init` (S3), `clamav`
(skan wgranych plików), `mail` (Postfix + OpenDKIM, relay tylko wewnętrzny). Nakładka
`docker-compose.dev.yml` dokłada `mailpit` (podgląd poczty na `:8025`), otwiera porty `5432`, `9000`,
`9001`, `8000` i montuje kod z hosta. Rozmowy kwalifikacyjne na własnym Jitsi to **osobny** projekt
compose (`deploy/jitsi/`).

Wolumeny z danymi: `pg_data`, `minio_data`, `redis_data`, `mail_dkim`, `mail_spool`, `caddy_data`,
`caddy_config`, `static_files`, `clamav_db`.

---

## 2. Instalacja

### 2.1 Środowisko deweloperskie

```bash
cp .env.example .env          # wystarczą wartości domyślne
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_demo           # konta i dane demonstracyjne
```

Serwis: `http://localhost:8000/`, poczta: `http://localhost:8025/`, konsola MinIO: `http://localhost:9001/`.
Konta demonstracyjne wypisuje `seed_demo` (README § 2). **Po zmianie ustawień w `.env` restartuj `web`**
— Django czyta konfigurację przy starcie procesu.

### 2.2 Produkcja — pierwsze wdrożenie

Najkrótsza droga to `scripts/deploy.sh`: wgrywa kod przez SSH, instaluje Dockera, generuje `.env`,
buduje obraz, uruchamia usługi, puszcza seedy i wypisuje rekordy DNS dla poczty.

```bash
SITE_DOMAIN=olimpiada.example.org \
ACME_EMAIL=admin@example.org \
COORDINATOR_EMAIL=koordynator@example.org \
COORDINATOR_PASSWORD='…' \
SSH_KEY=~/.ssh/olimpiada_deploy \
scripts/deploy.sh root@<adres-serwera>
```

Skrypt ma siedem kroków i po każdym mówi, co zrobił:

1. **Docker na serwerze** — `docker.io` + `docker-compose-v2` z pakietów Ubuntu, `ufw` otwiera
   22/80/443/9000 (reszta usług nie jest publikowana).
2. **Kod** — `git archive HEAD` rozpakowany do `/opt/olimpiada` (`REMOTE_DIR`). Katalog jest czyszczony
   **poza** `.env` i `e2e`, więc wdrożenie nie kasuje konfiguracji.
3. **`.env`** — tworzony **tylko przy pierwszym wdrożeniu**, z losowymi sekretami; przy kolejnych
   aktualizuje się w nim wyłącznie `APP_VERSION`. Zostaje znacznik `.first-deploy`.
4. **Build i start** — `db`, `redis`, `minio`, `minio-init`, `clamav`, `mail`, `web`, `worker`, `beat`, `proxy`.
   Migracje puszcza `entrypoint.sh` kontenera `web`.
5. **Oczekiwanie na `healthy`** dla `web` i `proxy` (do 5 minut).
6. **Seedy i konto koordynatora** — patrz niżej.
7. **Rekordy DNS dla poczty** — wypisane na ekran i zapisane do `<REMOTE_DIR>/mail-dns.txt` (§ 4.2).

Zmienne opcjonalne: `S3_PUBLIC_ADDRESS` (domyślnie `<domena>:9000`), `MAKE_EDITION_CURRENT=1`,
`SYNC_STAGE_DATES=1`, `RUN_CONTENT_SEEDS=1`, `APP_VERSION`, `DMARC_RUA`, `MAIL_PUBLIC_IP`, `REMOTE_DIR`.

**Co robią seedy w kroku 6.** `seed_cms`, `seed_regulamin`, `seed_legacy_content`, `seed_partners` są
**narzędziami importującymi**: każdy przebieg nadpisuje strony CMS treścią z repozytorium. Dlatego
uruchamiają się same wyłącznie przy pierwszym wdrożeniu (znacznik `.first-deploy`); później treść
należy do redakcji w `/cms/` i ponowny import wymaga jawnego `RUN_CONTENT_SEEDS=1`.
`seed_edition_kwantowa` (bez `--sync-dates`) tworzy tylko brakujące etapy i nie rusza istniejących
terminów. `seed_schools` (słownik ok. 8100 szkół ponadpodstawowych z SIO/RSPO) chodzi **zawsze** — to
dane referencyjne, nie treść redakcyjna, a bez nich wyszukiwarka szkół w rejestracji nie ma czego pokazać.
`bootstrap_coordinator` zakłada pierwsze konto koordynatora (superuser + grupa `coordinator`).

### 2.3 Produkcja bez skryptu

```bash
cp .env.example .env      # i uzupełnić — patrz § 3
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Zanim to zadziała, muszą być spełnione warunki z § 3 i § 4 (domena, DNS, `S3_PUBLIC_ENDPOINT_URL`,
`TRUSTED_PROXY_IPS`, sekrety, poczta).

---

## 3. Zmienne środowiskowe (`.env`)

Pełny szablon z komentarzami: [`.env.example`](../.env.example). Wartości wchodzą do kontenerów przez
`env_file`/`environment`; **w obrazie nie ma żadnego sekretu**. Plik `.env` nie należy do repozytorium
(jest w `.gitignore`, pilnuje tego skan `gitleaks`) i ma prawa `600`.

| Zmienna | Domyślnie | Co znaczy i na co wpływa |
|---|---|---|
| `APP_VERSION` | `dev` | tag obrazu `olimpiada/web`; ta sama wartość jest w stopce serwisu i na `/status/` |
| `SITE_DOMAIN` | `localhost` | domena serwisu; używa jej Caddy (certyfikat) i `wagtailcore.Site` |
| `ACME_EMAIL` | – | adres do Let's Encrypt; bez niego Caddy nie wystawi certyfikatu |
| `S3_PUBLIC_ADDRESS` | `s3.<domena>` | publiczny adres MinIO obsługiwany przez Caddy |
| `MAX_UPLOAD_MB` | `25` | limit rozmiaru **żądania** na proxy. Musi być **większy** niż największe `Problem.max_file_mb`, inaczej proxy utnie upload, zanim aplikacja go zobaczy |
| `DJANGO_SECRET_KEY` | – | **wymagane w produkcji**, ≥ 50 losowych znaków; zmiana unieważnia sesje i podpisane linki (aktywacja, zgoda opiekuna) |
| `DJANGO_DEBUG` | `0` | `1` wyłącznie lokalnie |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,web` | lista hostów Django — z prawdziwą domeną i `www` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://localhost` | origin(y) **z protokołem**; brak wpisu = 403 przy każdym formularzu |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | = `not DEBUG` | w produkcji `1`; odkomentuj `0` tylko w devie bez TLS |
| `WEB_WORKERS` / `CELERY_CONCURRENCY` | `3` / `2` | procesy gunicorna i wątki workera |
| `DB_POOL` / `DB_POOL_MAX_SIZE` | `1` w `web` / `WEB_THREADS` | pula połączeń z bazą — patrz § 9.4 |
| `DB_CONN_MAX_AGE` | `60` | działa tylko w `worker`/`beat` (bez puli); w `web` ignorowane — § 9.4 |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `olimpiada` / `olimpiada` / – | baza |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | – | konto administracyjne MinIO; **backend go nie używa** (tylko `minio-init`) |
| `S3_PUBLIC_ACCESS_KEY` / `S3_PUBLIC_SECRET_KEY` | `wagtail-media` / – | konto serwisowe bucketu `public-media` (media Wagtaila) |
| `S3_PRIVATE_ACCESS_KEY` / `S3_PRIVATE_SECRET_KEY` | `app-private` / – | konto serwisowe bucketu `submissions` (prace uczestników i treści zadań) |
| `S3_PUBLIC_ENDPOINT_URL` | `https://s3.<SITE_DOMAIN>` | adres MinIO **widziany z przeglądarki**; ta sama wartość wchodzi do `img-src`/`media-src`/`connect-src` w CSP |
| `S3_PRESIGNED_TTL_SECONDS` | `600` | ważność podpisanego linku do pliku rozwiązania |
| `TRUSTED_PROXY_IPS` | podsieci compose | komu wolno podać `X-Real-IP`. **Nigdy `0.0.0.0/0`** — nagłówek od nieznanego nadawcy to dane od klienta, a nie fakt |
| `EMAIL_URL` | brak = log | `smtp://mailpit:1025` (dev), `smtp://mail:587` (własny relay), `smtp+tls://user:hasło@host:587` (dostawca zewnętrzny) |
| `DEFAULT_FROM_EMAIL` | `noreply@localhost` | nadawca listów (i `SERVER_EMAIL`); domena musi mieć SPF/DKIM |
| `EMAIL_TIMEOUT` | `10` | limit sekund na połączenie SMTP (wysyłka idzie w workerze Celery, kolejka `mail`) |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` | puste | logowanie Google; puste = przycisk się nie pokazuje |
| `FACEBOOK_APP_ID` / `_SECRET` | puste | logowanie Facebook; jw. |
| `SITE_URL` | – | opcjonalny adres bezwzględny dla wysyłek spoza żądania HTTP |
| `E2E_MODE` | nieustawiona | **tylko dev**: odblokowuje `manage.py e2e_timeline`. W produkcji nigdy |

Sekrety (`DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `S3_*_SECRET_KEY`) mają być
losowe, po ≥ 24 znaki. `scripts/deploy.sh` generuje je sam przy pierwszym wdrożeniu.

---

## 4. DNS, poczta, S3 i wideo

### 4.1 Rekordy, bez których serwis nie wstanie

| Rekord | Nazwa | Po co |
|---|---|---|
| A / AAAA | `<domena>` | sam serwis |
| A / AAAA | `s3.<domena>` | **obowiązkowy**: presigned URL-e do MinIO są podpisywane hostem (SigV4 obejmuje `Host`), więc nie da się ich schować pod podścieżką domeny głównej |
| A | `www.<domena>` | przekierowanie na domenę główną (Caddy) |
| A | `mail.<domena>` | nazwa z HELO/EHLO relaya; część odbiorców jej wymaga |
| A | `meet.<domena>` | tylko przy własnym Jitsi (§ 4.4) |

Dopóki nie ma rekordu `s3.<domena>`, wdrożenie stawia publiczny adres S3 na porcie: `<domena>:9000`
(zmienna `S3_PUBLIC_ADDRESS`). Po dodaniu rekordu wpisz `S3_PUBLIC_ADDRESS=s3.<domena>` i
`S3_PUBLIC_ENDPOINT_URL=https://s3.<domena>` w `.env`, po czym odtwórz `proxy`, `web`, `worker`, `beat`.
Gotowa strefa dla wdrożenia referencyjnego: `deploy/dns-olimpiadakwantowa.pl.zone` i
`deploy/dns-olimpiadakwantowa.pl.md`.

### 4.2 Poczta wychodząca

**Bez działającej poczty nie działa reset hasła** — a to jedyna droga odzyskania konta dla uczestnika,
recenzenta, komisji odwoławczej i koordynatora. Nie działają też: aktywacja konta, zaproszenia do
komitetu, zgoda opiekuna, powiadomienia o pracach i wynikach, przypomnienia dla recenzentów, komunikaty
do grup i support desk.

Dwa warianty, jedna zmienna `EMAIL_URL`:

- **A (domyślny)** — własny Postfix z usługi `mail`: `EMAIL_URL=smtp://mail:587`. Port nie jest
  publikowany, relay widzi wyłącznie sieć compose, więc brak TLS/auth na tym odcinku nie ma znaczenia.
  Domena adresu w `DEFAULT_FROM_EMAIL` **musi** być równa `SITE_DOMAIN` (`ALLOWED_SENDER_DOMAINS`).
- **B** — dostawca zewnętrzny: `EMAIL_URL=smtp+tls://użytkownik:hasło@smtp.dostawca.example:587`
  (albo `smtps://` dla portu 465). Znaki `@` i `:` w haśle koduj jako `%40` i `%3A`.

Wariant A wymaga **czterech rekordów w DNS-ie plus PTR**; wypisuje je krok 7/7 wdrożenia i zapisuje do
`<REMOTE_DIR>/mail-dns.txt`:

1. **SPF** — TXT w korzeniu strefy: `v=spf1 ip4:<adres serwera> -all`
2. **DKIM** — TXT `olimpiada._domainkey.<domena>` (selektor `olimpiada`); klucz powstaje przy pierwszym
   starcie usługi `mail` i leży na wolumenie `mail_dkim`, więc jest stały
3. **DMARC** — TXT `_dmarc.<domena>`: `v=DMARC1; p=quarantine; rua=mailto:<adres>; adkim=r; aspf=r; fo=1`
4. **A** — `mail.<domena>` → adres serwera
5. **PTR (rDNS)** — ustawia się **w panelu dostawcy serwera**, nie w strefie domeny:
   `<adres serwera>` → `mail.<domena>`. **Bez tego Gmail i Outlook odrzucają pocztę niezależnie od
   SPF i DKIM.**

Weryfikacja po wdrożeniu — README § 4.3 (klucz DKIM, wysyłka testowa, `status=sent` w logu relaya oraz
sprawdzenie, że relay **nie** odpowiada z internetu).

### 4.3 MinIO / S3

Dwa buckety i dwa konta serwisowe zakładane raz przez `minio-init`:
`public-media` (media Wagtaila, konto `wagtail-media`) i `submissions` (prace uczestników, treści zadań,
rozwiązania wzorcowe; konto `app-private`). Backend **nie używa** konta administracyjnego
`MINIO_ROOT_*`. Pliki prywatne wychodzą wyłącznie przez widoki aplikacji albo przez presigned URL
o żywotności `S3_PRESIGNED_TTL_SECONDS`.

Rotacja kluczy serwisowych jest ręczna (`minio-init` tworzy konta tylko raz) — procedura w README § 6.2.
Zmiana `MINIO_ROOT_*` wymaga restartu usługi `minio` i nie dotyka aplikacji.

### 4.4 Jitsi Meet (rozmowy kwalifikacyjne)

Domyślnie pokoje powstają na publicznym `https://meet.jit.si/`. Własna instancja pod `meet.<domena>`
sprawia, że dane rozmów nie opuszczają serwera organizatora:

```bash
scripts/deploy_jitsi.sh root@<adres-serwera>
```

Skrypt tworzy jednorazowo `/opt/olimpiada/jitsi/.env`, otwiera UDP 10000, startuje kontenery
(`jitsi/web`, `prosody`, `jicofo`, `jvb`) i restartuje Caddy, który ma blok `meet.{$SITE_DOMAIN}`
i sam wystawi certyfikat — **gdy tylko istnieje rekord DNS A `meet.<domena>`**. Po uruchomieniu
koordynator ustawia w etapie z rozmowami dostawcę wideo na „własna instancja” z adresem
`https://meet.<domena>/`. Wideo jest **wyłącznie linkiem** — osadzenia w `<iframe>` nie ma i nie będzie
(wymagałoby rozluźnienia `frame-src` w CSP i proszenia o kamerę w kontekście naszej domeny).

---

## 5. Aktualizacje i migracje

```bash
SITE_DOMAIN=… ACME_EMAIL=… scripts/deploy.sh root@<adres-serwera>
```

Co wdrożenie **zachowuje**: `.env` (aktualizuje wyłącznie `APP_VERSION`), terminy i nazwy etapów
(należą do koordynatora), strony CMS (należą do redakcji). Migracje bazy puszcza `entrypoint.sh`
kontenera `web` przy starcie — nie ma osobnego kroku.

Ręcznie, gdy wdrażasz bez skryptu:

```bash
docker compose build --pull web
docker compose up -d --remove-orphans
docker compose exec web python manage.py migrate
docker compose exec web python manage.py showmigrations | grep '\[ \]'   # nic nie powinno zostać
```

Zasady, które warto znać przed aktualizacją:

- **kolejność seedów przy odtwarzaniu treści od zera**: `migrate` → `seed_regulamin` →
  `seed_legacy_content` → `seed_partners` → `seed_edition_kwantowa`. `seed_partners` **po**
  `seed_legacy_content`, bo dopisuje się do strony `/partnerzy/`, którą tamta komenda zakłada,
- **`RUN_CONTENT_SEEDS=1` nadpisuje poprawki zrobione w `/cms/`** — używaj świadomie,
- **`SYNC_STAGE_DATES=1` przestawia terminy istniejących etapów** na plan z komendy,
- po dołożeniu zależności w `backend/pyproject.toml` konieczny jest `docker compose build web`,
- po zmianie tłumaczeń: `msgfmt` biegnie w budowaniu obrazu — zduplikowany `msgid` w
  `backend/locale/*/LC_MESSAGES/django.po` **wywraca build** (tak było w v0.18.0),
- **spójność migracji** sprawdza `python manage.py makemigrations --check --dry-run`.

Wycofanie wydania: wdróż wcześniejszy tag (`APP_VERSION=v0.17.1 scripts/deploy.sh …` z wcześniejszego
`HEAD`). Migracji danych zwykle **nie da się** cofnąć automatycznie — przy zmianie schematu wycofanie
oznacza odtworzenie bazy z kopii (§ 6).

---

## 6. Kopie zapasowe, monitoring, rotacja

> Szczegółowe procedury operacyjne (harmonogram kopii, próbne odtworzenia, dyżury) mają trafić do
> `docs/OPERACJE.md`. Dopóki tego pliku nie ma, obowiązuje ten rozdział i README § 6.1–6.2.

### 6.1 Kopia zapasowa

Dwie części — baza i buckety — muszą pochodzić z **tego samego momentu**: snapshot ogłoszonych wyników
odwołuje się do plików w MinIO.

```bash
mkdir -p backup
set -a && . ./.env && set +a

# 1. Baza (format custom — pozwala na selektywny restore)
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  > "backup/olimpiada-$(date +%F).dump"

# 2. Buckety
MSYS_NO_PATHCONV=1 docker compose run --rm --entrypoint sh -v "$PWD/backup:/backup" minio-init -c '
  mc alias set src http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" &&
  mc mirror --overwrite src/submissions  /backup/submissions &&
  mc mirror --overwrite src/public-media /backup/public-media'
```

Odtworzenie: `pg_restore … --clean --if-exists` plus `mc mirror` w drugą stronę. Kopia **nie zawiera
i nie powinna zawierać** `.env` (sekrety trzymamy w menedżerze sekretów) ani wolumenu `clamav_db`
(sygnatury pobierają się same). `MSYS_NO_PATHCONV=1` jest potrzebne wyłącznie w Git Bashu.

**Kopię trzeba próbnie odtworzyć** — niesprawdzona kopia jest założeniem, nie kopią. Najtańszy test:
`pg_restore` do świeżej bazy na maszynie deweloperskiej i wejście na `/coordinator/`.

### 6.2 Monitoring

Dostępne dziś, bez dodatkowych narzędzi:

| Sygnał | Adres | Dla kogo |
|---|---|---|
| Health check kontenera | `/healthz/` | orkiestrator; odpowiada kodem HTTP |
| Strona statusu | `/status/` | **człowiek**: baza, cache, magazyn prac, kolejka zadań, czas serwera, stan rejestracji, bieżący etap |
| Ten sam stan maszynowo | `/status.json` | monitor zewnętrzny; **kod zawsze 200**, werdykt w polu `status` (`ok` / `degraded`) |
| Stan usług | `docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'` | administrator |
| Logi | `docker compose logs -f web worker beat proxy mail` | administrator |

Kolejka zadań jest sprawdzana **pulsem** (`apps.core.tasks.heartbeat` co minutę, próg 3 minuty), a nie
synchronicznym pytaniem do workera — inaczej strona statusu wisiałaby dokładnie wtedy, gdy worker nie
żyje. Żeby puls powstał, musi zadziałać cała droga: beat → broker → worker → cache.

Na `/status/` **nie ma** nazw hostów, wersji bibliotek ani treści błędów — jedyną informacją
o infrastrukturze jest binarne „działa / nie działa”.

### 6.3 Rotacja i higiena sekretów

- klucze serwisowe MinIO — README § 6.2 (`mc admin user add` na istniejącym koncie **nadpisuje** sekret;
  po zmianie `docker compose up -d --force-recreate web worker beat`),
- `DJANGO_SECRET_KEY` — zmiana wylogowuje wszystkich i unieważnia podpisane linki (aktywacja konta,
  zgoda opiekuna). Rób ją świadomie, najlepiej poza oknem rejestracji i deadline'em,
- hasła OAuth i SMTP wchodzą przez `env_file` — wystarczy odtworzyć procesy aplikacji,
- skan sekretów w katalogu roboczym i w historii gita: `gitleaks` (README § 7).

---

## 7. Model bezpieczeństwa

Pełna checklista: [`SECURITY_CHECKLIST.md`](SECURITY_CHECKLIST.md). W skrócie:

**Role.** Pięć grup Django: `participant`, `reviewer`, `appeals`, `coordinator`, `supervisor`.
Uprawnienia rozstrzygają **serwisy i miksiny widoków** (`CoordinatorRequiredMixin` i rodzeństwo), nie
szablony — menu ukrywa pozycje wyłącznie po to, żeby nie prowadzić do ekranu, którego nie ma. Konto
komitetu powstaje **wyłącznie na kod zaproszenia**; konto uczestnika i opiekuna szkolnego — z otwartej
rejestracji. Konta koordynatora i superużytkownika są w panelu **chronione**: widać je, ale nie da się
ich usunąć ani edytować z `/coordinator/accounts/` (kod `COORDINATOR_PROTECTED`).

**Superkoordynator** (grupa `superkoordynator`, `apps/accounts/super_coordinator.py`) to rola
**platformy**, nie konkursu: koordynator **każdego** konkursu instalacji — panel `/coordinator/` pod
adresem dowolnego konkursu (w menu sekcja „Konkursy platformy” z adresami paneli) i całe `/cms/`
(wszystkie strony, kolekcje, komunikaty i ustawienia serwisu). Dane panelu są dalej zawężone do
konkursu z adresu. Rola **nie** daje `/admin/` (to zostaje dla `is_superuser`) ani zarządzania
kontami, grupami, witrynami i kolekcjami w `/cms/`. Nadaje i odbiera ją wyłącznie operator:
`manage.py superkoordynator --grant|--revoke <e-mail>` (`--list` wypisuje obecnych) albo akcja
„Nadaj/Odbierz rolę superkoordynatora” w `/admin/ → Użytkownicy`, widoczna tylko dla
superużytkownika. Obie drogi zapisują wpis audytu (`accounts.super_coordinator.granted` /
`.revoked`); ręczne dopisanie grupy w formularzu konta działa, ale bez śladu w audycie. Na liście
`/coordinator/accounts/` konto ma rolę „superkoordynator”.

**`/cms/` per konkurs.** Po jednorazowym `manage.py scope_cms_access` (`OPERACJE.md` § 6.7)
koordynator konkursu redaguje w `/cms/` wyłącznie poddrzewo stron swojej witryny i kolekcję mediów
swojego konkursu — przez grupę `cms:<slug>`, do której wpisuje i z której wypisuje go serwis przy
każdej zmianie roli. Globalna grupa `coordinator` zostaje rolą, ale nie daje w `/cms/` niczego.
Okna wyboru stron, obrazów, dokumentów i komunikatów, wyszukiwarka, raporty, API panelu i dziennik
zdarzeń pokazują wyłącznie obiekty z zasięgu redaktora; cudzy komunikat pod znanym numerem to 404.
Grupy `cms:<slug>` i `superkoordynator` są **systemowe**: serwis odtwarza ich uprawnienia, więc
redaktor spoza roli koordynatora dostaje własną grupę założoną w `/cms/ → Ustawienia → Grupy`.

**CSP.** `script-src` **bez** `'unsafe-inline'` i `'unsafe-eval'`, z nonce'ami i `'strict-dynamic'`;
`default-src 'self'`, `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'` (w `/cms/`:
`'self'`, bo Wagtail osadza podgląd własnej strony). Publiczny host MinIO dochodzi do `connect-src`,
`img-src` i `media-src`. Hosty Google dochodzą **tylko** wtedy, gdy ustawiono identyfikator GA4; ekrany
zgody dostawców OAuth trafiają do `form-action` **tylko** dla dostawcy z kompletem kluczy.
Praktyczny skutek dla utrzymania: **żaden ekran nie wymaga JavaScriptu** i nie wolno dopisywać skryptów
ani stylów inline — szerokości pasków postępu są klasami z zamkniętej listy, a nie `style="width:…"`.

**Upload.** Każdy plik idzie do prywatnego bucketu i przechodzi skan ClamAV (Celery). O przyjęciu
decyduje **treść** pliku, nie rozszerzenie (`apps/submissions/validators.py`). Podgląd (liczba stron
PDF, wymiary JPEG, pierwsze wiersze kodu) liczy się **dopiero po czystym skanie**.

**Audyt.** `core.AuditLog` — wpisów nie da się zmienić ani usunąć. Przeglądarka: `/coordinator/audit/`
(100 na stronę, filtry: fragment adresu wykonawcy, akcja, typ obiektu, przedział dat). **W `diff`
z zasady nie ma danych osobowych** — zamiast wartości pól idą ich nazwy, zamiast treści uzasadnienia
jego długość, zamiast adresów liczniki. To jest reguła, a nie przypadek: audyt czytają osoby, które nie
muszą znać danych kontaktowych uczestników.

**Ograniczanie żądań.** Reset hasła i ponowna wysyłka linku aktywacyjnego: 5/h; rejestracja: 10/h na
adres; eksport własnych danych: 1 na 10 minut na konto. Publiczne formularze rejestracji mają trzy
warstwy antyspamowe **bez usług obcych**: własna CAPTCHA arytmetyczna (Pillow, obrazek spod naszego
adresu), pułapka `honeypot` i minimalny czas wypełniania z podpisanym znacznikiem. Odmowa jest zawsze
jednym, ogólnym komunikatem — zdanie „wypełniłeś ukryte pole” byłoby instrukcją obejścia.

**RODO — narzędzia w panelu.** Retencja (`/coordinator/retention/`), rejestr czynności przetwarzania
(`/coordinator/processing-register/`, art. 30 RODO, prowadzony jako dane w kodzie i pilnowany testem),
eksport danych osoby (`/account/export/`, a dla koordynatora przycisk na karcie konta).
Szczegóły — [`PODRECZNIK-ORGANIZATORA.md`](PODRECZNIK-ORGANIZATORA.md) § 9.

---

## 8. Gdzie leżą dane i jak je usunąć

### 8.1 Mapa danych

| Co | Gdzie | Uwagi |
|---|---|---|
| Konta, profile, zgody (`ConsentRecord`), zgłoszenia, recenzje, oceny, audyt, snapshoty wyników | PostgreSQL, wolumen `pg_data` | jedyne źródło prawdy o zawodach |
| Prace uczestników, treści zadań, rozwiązania wzorcowe | MinIO, bucket `submissions` (wolumen `minio_data`) | brak publicznych adresów; wyłącznie widoki aplikacji i presigned URL |
| Media redakcyjne (obrazy, PDF-y dokumentów) | MinIO, bucket `public-media` | publiczne z założenia |
| Sesje, pamięć podręczna, kolejki Celery | Redis (`redis_data`) | dane ulotne |
| Kolejka i klucz DKIM poczty | `mail_spool`, `mail_dkim` | klucz DKIM jest **stały** — skasowanie wolumenu unieważnia rekord w DNS-ie |
| Certyfikaty TLS | `caddy_data` | odtwarzalne |
| Sekrety | `.env` na serwerze (`600`) | **poza** kopią zapasową bazy |
| Zdarzenia Google Analytics | u Google, **wyłącznie po zgodzie**, retencja 14 miesięcy | bez identyfikatora GA4 nie ma ani skryptu, ani hostów w CSP |

Dyplomy i zaświadczenia są w bazie jako **rejestr, nie plik** (`results.Certificate`): PDF powstaje przy
każdym pobraniu. Kopia binarna oznaczałaby tylko tyle, że poprawka szablonu nie dotyczy dokumentów już
wystawionych.

### 8.2 Usuwanie danych osobowych

Trzy drogi, wszystkie przez **jedną** funkcję `anonymise_account`, więc skutek jest zawsze ten sam:

| Droga | Kto | Gdzie |
|---|---|---|
| Żądanie właściciela (art. 17) | uczestnik, recenzent, opiekun | `/account/delete/` (POST wymaga aktualnego hasła; konto z logowaniem zewnętrznym potwierdza przepisaniem adresu) |
| Decyzja organizatora | koordynator | `/coordinator/accounts/<id>/delete/` |
| Upływ terminu retencji | automat | `apps.accounts.retention.anonymise_expired_editions` (raz na dobę) oraz „Wykonaj teraz” na `/coordinator/retention/` |

**Reguła jest jedna: prawo do usunięcia własnych danych nie jest prawem do usunięcia dokumentacji
zawodów.**

- konto **ze śladem** w zawodach (zgłoszenie, praca, recenzja) jest **anonimizowane**: adres zmienia się
  na `deleted-<pk>@invalid.<domena>`, znikają imię, nazwisko, telefon, szkoła i data urodzenia (rocznik idzie na wartość jawnie nieprawdziwą, bo kolumna jest `NOT NULL`), hasło staje się
  nieużywalne, powiązania OAuth, tokeny i sesje są kasowane, zgody dostają `withdrawn_at`. Zostaje
  pseudonimowy `Participant.public_code`, więc ogłoszone tabele wyników dalej mają swój wiersz.
  Audyt: `account.anonymised` (albo `account.anonymised_by_retention` z identyfikatorem edycji),
- konto **bez** takiego śladu jest kasowane w całości razem z profilem, zgodami i zgłoszeniami do
  support desku; adres zwalnia się do ponownej rejestracji. Audyt: `account.deleted`.

Konta koordynatora i superużytkownika tą drogą nie przechodzą. Konta komitetu są poza zakresem retencji
— to konta funkcyjne, żyją między edycjami.

**Anonimizacja jest nieodwracalna**, więc obie drogi automatyczne pokazują plan, zanim cokolwiek zrobią:

```bash
docker compose exec web python manage.py retention_report   # nic nie zmienia; nie ma flagi, która by to zmieniła
```

Raport wypisuje **kody publiczne**, nigdy adresów e-mail — wykaz adresów byłby dokładnie tą daną, którą
retencja usuwa.

**Konta nieaktywowane** kasuje się same: link aktywacyjny żyje 24 godziny i tyle samo żyje konto, po czym
`purge_unactivated_accounts` (beat, co 15 minut) usuwa je razem z profilem, żeby adres wrócił do puli.
Konta, do których odwołuje się dokumentacja zawodów, nie są kasowane nigdy.

---

## 9. Rozwiązywanie problemów

### 9.1 „403 — nie udało się zweryfikować formularza” zaraz po zalogowaniu

**Objaw.** Użytkownik wypełnia formularz, wysyła i dostaje 403 z polskim wyjaśnieniem
(`templates/403_csrf.html`, widok `apps/web/views/errors.py`).

**Najczęstsza przyczyna.** W międzyczasie **zalogowano się lub wylogowano w innej karcie tej samej
przeglądarki**, albo strona z formularzem stała otwarta bardzo długo — token CSRF formularza jest wtedy
nieaktualny. Strona błędu mówi to wprost i daje odnośnik „odśwież”, który prowadzi pod **ten sam adres**
metodą GET, więc nie trzeba szukać formularza od nowa.

**Kiedy to jednak jest usterka wdrożenia.** Gdy 403 wraca dla **każdego** formularza i dla każdego
użytkownika, sprawdź po kolei:

- `DJANGO_CSRF_TRUSTED_ORIGINS` — musi zawierać origin **z protokołem** (`https://olimpiada.example.org`,
  także wariant `www`),
- `CSRF_COOKIE_SECURE=1` przy serwisie podawanym po `http` — ciasteczko wtedy nie dojdzie,
- proxy, które gubi nagłówek `Origin`/`Referer` albo przepisuje `Host`,
- użytkownik blokujący ciasteczka dla domeny (strona błędu rozpoznaje ten przypadek osobnym zdaniem).

### 9.2 Listy lądują w spamie albo wracają odrzucone

Kolejność sprawdzania — od najczęstszej przyczyny:

1. **PTR (rDNS)** — ustawiany w panelu dostawcy serwera, nie w strefie domeny. Bez niego Gmail i Outlook
   odrzucają pocztę **niezależnie od SPF i DKIM**. To jest przyczyna numer jeden.
2. **SPF** w korzeniu strefy z **prawdziwym** adresem wyjściowym (gdy serwer wychodzi przez inny IP,
   podaj go przez `MAIL_PUBLIC_IP` przy wdrożeniu).
3. **DKIM** — czy rekord `olimpiada._domainkey.<domena>` w DNS-ie jest tym, co w kontenerze:
   `docker compose exec mail cat /etc/opendkim/keys/<domena>.txt` (panele DNS chcą **jednego** ciągu —
   skrypt wdrożeniowy sam skleja fragmenty z formatu BIND).
4. **Domena nadawcy.** W wariancie A domena z `DEFAULT_FROM_EMAIL` musi być równa `SITE_DOMAIN`.
5. **Log relaya**: `docker compose logs mail | tail -50` — szukasz `DKIM-Signature field added`
   i `status=sent (250 …)`. `status=bounced` niesie powód od odbiorcy.
6. **Treść.** Listy aktywacyjne celowo **nie** zawierają podpowiedzi o spamie — jest ona na stronie
   po rejestracji (`/register/done/`), bo odbiorca, który listu nie dostał, i tak jej w nim nie przeczyta.

Podgląd poczty w devie: `http://localhost:8025/` (mailpit). W produkcji **nie** wystawiaj portu 587 na
świat — relay ma być widoczny wyłącznie z sieci compose (sprawdzenie: README § 4.3 punkt 4).

### 9.3 Certyfikat nie chce się wystawić

Caddy pobiera certyfikat Let's Encrypt sam przy pierwszym starcie. Wymaga **otwartych portów 80 i 443**
oraz poprawnego DNS-u dla **każdej** nazwy w `Caddyfile` — a to są co najmniej dwie (`<domena>`,
`s3.<domena>`), plus `www.<domena>` i `meet.<domena>`, jeśli ich używasz.

Typowy przebieg: wdrożenie kończy się sukcesem, ale strona nie działa po HTTPS, bo rekord
`s3.<domena>` jeszcze się nie rozpropagował. Objaw w logach: `docker compose logs proxy` powtarza próby
ACME dla tej jednej nazwy. **Co zrobić:** albo poczekać na propagację (do 24 h), albo — na czas
oczekiwania — zostawić `S3_PUBLIC_ADDRESS=<domena>:9000` (tak właśnie robi wdrożenie domyślnie) i
przełączyć na `s3.<domena>` dopiero wtedy, gdy `dig +short s3.<domena>` zwraca adres serwera.
Uwaga na limity Let's Encrypt: powtarzanie wdrożenia „aż się uda” wyczerpuje pulę prób na tydzień.

### 9.4 Po dobie pracy każda strona daje 500 („too many clients already”)

**Incydent z 9 września 2026** (opisany w [`BACKLOG.md`](BACKLOG.md)). Przy `CONN_MAX_AGE=60` proces
`web` po dobie trzymał 92 bezczynne połączenia do Postgresa: aplikacja chodzi pod ASGI (gunicorn +
`UvicornWorker`), a trwałe połączenie jest przypięte do wątku żądania, który ginie bez
`close_old_connections`. Postgres odpowiadał „too many clients already” i **każda** strona dawała 500.

**Stan obowiązujący (od wydania po v0.35.0):** `web` bierze połączenia z **puli psycopg** — jedna pula na proces
gunicorna, najwyżej `DB_POOL_MAX_SIZE` połączeń (domyślnie tyle, ile wątków: `WEB_THREADS`), a nadmiar
bezczynnych połączeń pula zamyka sama, stopniowo (jedno na 10 minut bezczynności). `worker` i `beat` chodzą bez puli, z
`DB_CONN_MAX_AGE=60`. Przy domyślnych wartościach cała aplikacja trzyma najwyżej ok. 20 ze 100
połączeń (rachunek: [`OPERACJE.md`](OPERACJE.md) § 11.2).

Objaw nie przychodzi już bez ostrzeżenia: powyżej **80 %** `max_connections` watchdog wysyła list na
`ALERT_EMAILS` z podziałem na usługi, a `/healthz/` i `/status.json` pokazują
`"db_connections": "warn"` (95 % — `"critical"`).

Szybka diagnostyka, gdy objaw wróci:

```bash
docker compose exec web python manage.py db_connections   # ile z ilu, kto trzyma, w jakim stanie
docker compose restart web          # gdy trzyma `olimpiada-web` – doraźnie zwalnia połączenia
docker compose restart worker beat  # gdy `olimpiada-worker` / `olimpiada-beat`
```

Połączenia `(bez nazwy)` to nie aplikacja — to `psql`, kopia zapasowa albo coś spoza compose'a.

### 9.5 Pozostałe typowe sytuacje

| Objaw | Przyczyna i co zrobić |
|---|---|
| Upload dużego pliku kończy się błędem proxy | `MAX_UPLOAD_MB` mniejsze niż `Problem.max_file_mb` — podnieś i odtwórz `proxy` |
| Obrazy redakcyjne i podglądy PDF nie ładują się w produkcji | zły `S3_PUBLIC_ENDPOINT_URL`: ta wartość wchodzi do CSP, więc przeglądarka blokuje wszystko z MinIO. W konsoli przeglądarki widać naruszenie CSP |
| W audycie jest adres proxy zamiast adresu klienta | pusta albo zła `TRUSTED_PROXY_IPS` |
| Prace stoją w „oczekuje na skan” | `docker compose logs clamav` — kontener potrzebuje ~1,5 GB RAM i pobiera sygnatury przy pierwszym starcie |
| `/status/` pokazuje „kolejka zadań: nie działa” | nie żyje `beat` albo `worker` (puls starszy niż 3 minuty) — `docker compose ps`, potem logi |
| Seedy nie chcą się wykonać przez SSH | polecenia `docker compose exec` muszą mieć `-T` i `</dev/null` (inaczej połykają strumień skryptu) |
| Wdrożenie „cofnęło” treść stron | ktoś uruchomił wdrożenie z `RUN_CONTENT_SEEDS=1` — treść odtwarza się z kopii bazy, seedy jej nie odtworzą |
| Build obrazu pada na `msgfmt` | zduplikowany `msgid` w katalogu tłumaczeń — scal wpisy w `django.po` |
| Git Bash przepisuje ścieżki w `docker compose run -v` | dopisz `MSYS_NO_PATHCONV=1` |

---

## 10. Funkcje w przygotowaniu

Poniższe elementy były **dopiero w budowie**, gdy powstawał ten podręcznik (gałąź `main`, wrzesień 2026).
Opis pochodzi z ich zamówienia, **nie z działającego kodu** — zanim się na nich oprzesz, sprawdź, co
faktycznie trafiło do repozytorium, i uzupełnij ten rozdział o prawdziwe polecenia i adresy.

**Skrypt kopii zapasowej `scripts/backup.sh` (w przygotowaniu).** Ma zamknąć procedurę z § 6.1
w jednym poleceniu uruchamianym z crona na serwerze: zrzut bazy w formacie `custom`, lustro obu bucketów
MinIO, wspólny znacznik czasu dla obu części, rotacja starszych kopii i niezerowy kod wyjścia, gdy
którakolwiek część się nie powiodła. Do tego czasu obowiązują polecenia wypisane wyżej — i obowiązuje
zasada, że kopia niesprawdzona próbnym odtworzeniem nie jest kopią.

**Monitoring `deploy/monitoring` (w przygotowaniu).** Ma dołożyć do compose gotowy zestaw zbierający
metryki i alerty (m.in. liczbę połączeń do Postgresa — patrz incydent § 9.4 — wiek pulsu kolejki, stan
usług i wolne miejsce na wolumenach) oraz podpiąć się pod istniejące `/healthz/` i `/status.json`.
Dopóki go nie ma, zewnętrzny monitor konfiguruje się adresem `/status.json` i czyta pole `status`;
kod odpowiedzi jest tam **zawsze 200**, więc alert trzeba oprzeć na treści, a nie na kodzie HTTP.

**Uwierzytelnianie dwuskładnikowe `apps/accounts/twofactor.py` (w przygotowaniu).** Ma dołożyć drugi
składnik (TOTP) dla kont funkcyjnych — koordynatora i komitetu — czyli dla tych, które widzą dane
osobowe uczestników i mogą zmieniać oceny. Do czasu jego wprowadzenia jedynym zabezpieczeniem tych kont
jest hasło i skrzynka pocztowa, więc warto wymusić na nich długie, unikatowe hasła i trzymać liczbę kont
koordynatora przy minimum.

**Integracje `apps/integrations` (w przygotowaniu).** Aplikacja na wymianę danych z systemami zewnętrznymi
organizatora. Z punktu widzenia utrzymania oznacza to nowe sekrety w `.env`, prawdopodobnie nowe hosty
w `connect-src` polityki CSP i nowy wiersz „odbiorcy danych” w rejestrze czynności przetwarzania —
wszystkie trzy trzeba będzie uzupełnić razem z kodem.
