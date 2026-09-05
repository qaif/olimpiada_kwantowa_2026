# Platforma Olimpiady

System do prowadzenia olimpiady przedmiotowej: część informacyjna (newsroom, zadania, archiwum,
wyniki) i pełny obieg zawodów – rejestracja uczestników, przyjmowanie rozwiązań z twardym
deadline'em, dwustopniowe ocenianie w skali 0/2/5/6, moderacja rozjazdów, reklamacje i publikacja
zanonimizowanych wyników.

Aplikacja jest jedna: **Django 5.1 + DRF + HTMX + Wagtail 6.3** w jednym procesie. Nie ma drugiego
systemu tożsamości ani drugiego panelu – redaktor, koordynator, recenzent i uczestnik logują się
tym samym kontem, a uprawnienia rozstrzygają grupy Django.

Architektura, model danych i uzasadnienia decyzji: **[`docs/PROJEKT.md`](docs/PROJEKT.md)**
(sekcje 1.2–1.4 – usługi, bezpieczeństwo uploadu, montaż CMS-a; sekcja 2.4 – workflow oceniania).
Stan prac i dług techniczny: [`docs/BACKLOG.md`](docs/BACKLOG.md).
Checklista bezpieczeństwa: [`docs/SECURITY_CHECKLIST.md`](docs/SECURITY_CHECKLIST.md).
Pokrycie testami: [`docs/COVERAGE.md`](docs/COVERAGE.md).

---

## 1. Wymagania

| Element | Wersja | Uwagi |
|---|---|---|
| Docker Engine / Docker Desktop | ≥ 24 | z wtyczką `docker compose` v2 |
| RAM | ≥ 6 GB dla Dockera | ClamAV trzyma bazę sygnatur w pamięci (~1,5 GB) |
| Dysk | ≥ 10 GB | obrazy, sygnatury ClamAV, wolumeny danych |
| Powłoka | Git Bash / WSL / dowolna POSIX-owa | skrypty w `scripts/` są bashowe |
| (opcjonalnie) Python 3.12 + `ruff` | – | wyłącznie do lintu poza kontenerem |

Systemu **nie da się** sensownie uruchomić bez Dockera: deadline, skan antywirusowy i prywatny
storage wymagają Postgresa, Redisa, MinIO i ClamAV-a, a nie ich atrap.

## 2. Uruchomienie środowiska deweloperskiego

Siedem poleceń od pustego katalogu do działającego systemu z danymi demonstracyjnymi:

```bash
git clone <adres-repozytorium> && cd olimpiada-clade                                    # 1
DC="docker compose -f docker-compose.yml -f docker-compose.dev.yml"                     # 2
cp .env.example .env                                                                    # 3
for k in DJANGO_SECRET_KEY POSTGRES_PASSWORD MINIO_ROOT_PASSWORD \
         S3_PUBLIC_SECRET_KEY S3_PRIVATE_SECRET_KEY; do \
  v=$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-40); sed -i "s|^$k=.*|$k=$v|" .env; done   # 4
$DC up -d --build web worker beat minio-init                                            # 5
$DC ps                                                                                  # 6 – czekamy na "web ... healthy"
$DC exec web python manage.py seed_demo && $DC exec web python manage.py seed_cms       # 7
```

Konto administracyjne poza `seed_demo` (opcjonalnie): `$DC exec web python manage.py createsuperuser`.

Uwagi:

- **Krok 2** to zwykła zmienna powłoki – dalej w tym pliku `docker compose …` znaczy
  `$DC …`. Zmienna `COMPOSE_FILE` też zadziała, ale ma pułapkę: separatorem listy plików jest
  `:` na Linuksie i **`;` w Windowsowym kliencie Dockera**, także wtedy, gdy wołasz go z Git Basha.
- **Krok 5** startuje tylko to, co w devie potrzebne. `docker compose up -d` bez listy usług
  wstałoby też z Caddym, który zajmuje porty 80 i 443 – w devie jest niepotrzebny, bo aplikacja
  odpowiada wprost na `localhost:8000`. Usługi `db`, `redis`, `minio` i `clamav` wstają same
  (`depends_on`). Podgląd poczty: dołóż `--profile dev` i usługę `mailpit`.
- **Krok 6 nie jest ozdobą.** `up -d` wraca, gdy kontenery **wystartowały**, a nie gdy aplikacja
  jest gotowa; migracje robi entrypoint `web`. `seed_demo` uruchomione zbyt wcześnie trafia na
  pustą bazę (`relation "competitions_edition" does not exist`).
- Pierwsze uruchomienie **ClamAV** pobiera sygnatury – kilka minut. Do tego czasu upload działa,
  ale plik zostaje w stanie „oczekuje na skan”.

### Adresy

| Adres | Co to jest |
|---|---|
| <http://localhost:8000/> | strona główna (Wagtail) |
| <http://localhost:8000/me/> | panel uczestnika |
| <http://localhost:8000/review/> | panel recenzenta |
| <http://localhost:8000/coordinator/> | panel koordynatora |
| <http://localhost:8000/appeals/> | panel komisji odwoławczej |
| <http://localhost:8000/wyniki/> | publiczne wyniki (strona CMS) |
| <http://localhost:8000/cms/> | panel redakcyjny Wagtaila (grupa `coordinator`) |
| <http://localhost:8000/admin/> | panel Django (`is_staff`) |
| <http://localhost:8000/api/docs/> | Swagger UI |
| <http://localhost:8000/healthz/> | healthcheck (`{"status":"ok","db":true,"redis":true}`) |
| <http://localhost:9001/> | konsola MinIO (`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`) |
| <http://localhost:8025/> | Mailpit – tylko przy `--profile dev` |

### Konta demonstracyjne (`seed_demo`)

Hasło do wszystkich: **`Demo12345!`**

| E-mail | Rola |
|---|---|
| `koordynator@example.com` | koordynator (i superużytkownik) |
| `recenzent1@example.com`, `recenzent2@example.com`, `recenzent3@example.com` | recenzenci `ACTIVE` |
| `uczestnik1@example.com` … `uczestnik5@example.com` | uczestnicy zapisani do eliminacji |

`seed_demo` wypisuje na stdout **jednorazowy kod zaproszenia** do komitetu (w bazie zostaje tylko
sha256 – nie da się go odtworzyć). Komenda jest idempotentna i odmawia startu przy `DEBUG=False`
bez `--force`, bo zakłada konta z jawnym hasłem.

Konta komisji odwoławczej `seed_demo` **nie** tworzy. Nowe zaproszenie:

```bash
docker compose exec web python manage.py create_invitation \
  --email koordynator@example.com --appeals --max-uses 1
```

Kod wpisuje się na `/register/committee/`.

## 3. Uruchomienie produkcyjne

Produkcja to sam `docker-compose.yml` (bez nakładki `dev`): Caddy z automatycznym TLS, brak portów
usług danych na hoście, `DEBUG=0`, kontenery `read_only` bez uprawnień.

```bash
cp .env.example .env      # i uzupełnić – patrz tabela w sekcji 4
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Co trzeba ustawić **zanim** to zadziała:

1. **Domena i DNS.** `SITE_DOMAIN=olimpiada.example.org`. W DNS muszą wskazywać na host **dwa**
   rekordy A/AAAA: `olimpiada.example.org` **oraz** `s3.olimpiada.example.org`. Drugi jest
   obowiązkowy – presigned URL-e do MinIO są podpisywane hostem (SigV4 obejmuje nagłówek `Host`),
   więc nie da się ich schować pod podścieżką głównej domeny (`deploy/Caddyfile`).
2. **`S3_PUBLIC_ENDPOINT_URL=https://s3.<domena>`.** To adres, pod którym MinIO widzi
   **przeglądarka**. Wewnętrzny `http://minio:9000` rozwiązuje się wyłącznie w sieci compose.
   Ta sama wartość wchodzi do `img-src`/`media-src`/`connect-src` polityki CSP – bez niej produkcja
   blokuje każdy obraz redakcyjny i podgląd PDF u recenzenta.
3. **`TRUSTED_PROXY_IPS`.** Adresy (lub sieci CIDR) proxy, którym backend wierzy w nagłówku
   `X-Real-IP`. Domyślnie podsieci compose (`172.30.1.0/24,172.30.2.0/24`). Pusta wartość znaczy
   „ufaj tylko `REMOTE_ADDR`” – wtedy w audycie zostaje adres Caddy'ego, nie klienta. Nigdy nie
   wpisuj tu `0.0.0.0/0`: nagłówek od nieznanego nadawcy to dane od klienta, a nie fakt.
4. **`DJANGO_ALLOWED_HOSTS` i `DJANGO_CSRF_TRUSTED_ORIGINS`** – z prawdziwą domeną
   (`https://olimpiada.example.org`), oraz `SESSION_COOKIE_SECURE=1`, `CSRF_COOKIE_SECURE=1`.
5. **`MAX_UPLOAD_MB`** – limit rozmiaru żądania w Caddym. Musi być **większy** niż największe
   `Problem.max_file_mb`, inaczej proxy odetnie upload, zanim aplikacja zdąży go ocenić.
6. **Sekrety.** `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`,
   `S3_PUBLIC_SECRET_KEY`, `S3_PRIVATE_SECRET_KEY` – losowe, po ≥ 24 znaki. `.env` nie należy do
   repozytorium (jest w `.gitignore`; sprawdzane skanem `gitleaks`).

Certyfikat Let's Encrypt Caddy pobiera sam przy pierwszym starcie – wymaga otwartych portów 80 i 443
i poprawnego DNS-u dla obu nazw.

## 4. Zmienne środowiskowe

Pełny szablon: [`.env.example`](.env.example). Wartości wchodzą do kontenerów przez `env_file`
i `environment` w compose; w obrazie nie ma żadnego sekretu.

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `APP_VERSION` | `dev` | tag obrazu `olimpiada/web` |
| `SITE_DOMAIN` | `localhost` | domena serwisu; Caddy i `wagtailcore.Site` |
| `MAX_UPLOAD_MB` | `25` | limit rozmiaru żądania na proxy |
| `DJANGO_SECRET_KEY` | – | **wymagane w produkcji**, ≥ 50 losowych znaków |
| `DJANGO_DEBUG` | `0` | `1` wyłącznie lokalnie (nakładka `dev` ustawia to sama) |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,web` | lista hostów Django |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://localhost` | origin(y) z protokołem |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `0` | w produkcji `1` |
| `WEB_WORKERS` | `3` | procesy gunicorna |
| `CELERY_CONCURRENCY` | `2` | wątki workera |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `olimpiada` / `olimpiada` / – | baza |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | – | konto administracyjne MinIO; backend go **nie** używa (tylko `minio-init`) |
| `S3_PUBLIC_ACCESS_KEY` / `S3_PUBLIC_SECRET_KEY` | `wagtail-media` / – | konto serwisowe bucketu `public-media` (media Wagtaila) |
| `S3_PRIVATE_ACCESS_KEY` / `S3_PRIVATE_SECRET_KEY` | `app-private` / – | konto serwisowe bucketu `submissions` (prace i treści zadań) |
| `S3_PUBLIC_ENDPOINT_URL` | `https://s3.<SITE_DOMAIN>` | adres MinIO widziany z przeglądarki |
| `S3_PRESIGNED_TTL_SECONDS` | `600` | ważność linku do pliku rozwiązania |
| `TRUSTED_PROXY_IPS` | podsieci compose | komu wolno podać `X-Real-IP` |
| `EMAIL_URL` | `smtp://mailpit:1025` | zapis w formacie `django-environ` |
| `DEFAULT_FROM_EMAIL` | `olimpiada@localhost` | nadawca powiadomień |
| `E2E_MODE` | (nieustawiona) | **tylko dev**: odblokowuje `manage.py e2e_timeline`. W produkcji nigdy |

## 5. Role i przepływ etapu

Cztery grupy Django: `participant`, `reviewer`, `appeals`, `coordinator`. Pełna macierz uprawnień –
`docs/PROJEKT.md` 2.3. Konto komitetu powstaje wyłącznie na kod zaproszenia; konto uczestnika –
z otwartej rejestracji.

| # | Krok | Kto | Ekran / endpoint |
|---|---|---|---|
| 1 | Rejestracja uczestnika | uczestnik | `/register/` → `POST /api/auth/register/participant/` |
| 2 | Rejestracja członka komitetu na kod | recenzent / komisja | `/register/committee/`; kod z `manage.py create_invitation` albo z panelu koordynatora |
| 3 | Zatwierdzenie konta `PENDING` | koordynator | `/coordinator/` → „Komitet – oczekujący na zatwierdzenie” |
| 4 | Zapis do eliminacji | uczestnik | `/me/` → „Zgłoś się do etapu eliminacyjnego” |
| 5 | Upload rozwiązania (przed deadline) | uczestnik | `/me/`, karta zadania (HTMX) → `POST /api/stages/<id>/problems/<n>/submissions/` |
| 6 | Skan antywirusowy | Celery → ClamAV | status pliku w karcie zadania: `oczekuje na skan` → `czysty` |
| 7 | Zamknięcie etapu | `beat` po `deadline_at + grace_seconds`, albo koordynator ręcznie | `/coordinator/` → „Zamknij etap” |
| 8 | Przydział 2 recenzentów (ślepy, bez konfliktu okręgu) | koordynator | `/coordinator/` → „Przydziel recenzentów” |
| 9 | Dwie niezależne oceny | recenzenci | `/review/`, `/review/<id>/` (podgląd PDF + adnotacje) |
| 10 | Zgodne oceny → `FinalGrade(CONSENSUS)`; rozjazd → `MODERATION` | system | – |
| 11 | Rozstrzygnięcie rozjazdu | koordynator (posiedzenie) lub trzeci recenzent | `/coordinator/` → „Moderacja (rozjazdy ocen)” |
| 12 | Otwarcie okna reklamacji | `beat` wg `appeal_window_opens_at` | – |
| 13 | Reklamacja na własną pracę | uczestnik | `/me/` → „Reklamacje” |
| 14 | Decyzja odwoławcza (bez autorów recenzji rundy 1) | komisja odwoławcza | `/appeals/` |
| 15 | Zamknięcie okna → `GRADED_PROVISIONAL` → `FINAL` | `beat` | – |
| 16 | Przeliczenie progów i publikacja | koordynator | `/coordinator/` → „Przelicz wyniki (podgląd)”, potem „Opublikuj wyniki” |
| 17 | Ogłoszona tabela | wszyscy, bez logowania | `/results/<stage_id>/` oraz strona CMS `/wyniki/` |
| 18 | Własny wynik i informacja zwrotna | uczestnik | `/me/` → „Moje wyniki” |

Publikacja przed zamknięciem okna reklamacji kończy się `409 APPEAL_WINDOW_OPEN`; tryb `FULL`
(nazwiska) jest dopuszczony wyłącznie w finale, dla laureatów, za zgodą – patrz `PROJEKT.md` 2.4.

## 6. Procedury operacyjne

### 6.1 Kopia zapasowa

Dwie części: baza i buckety. Obie muszą pochodzić z **tego samego momentu** – snapshot wyników
odwołuje się do plików w MinIO.

```bash
mkdir -p backup
set -a && . ./.env && set +a          # POSTGRES_USER / POSTGRES_DB do zmiennych powłoki

# 1. Baza (format custom – pozwala na selektywny restore)
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  > "backup/olimpiada-$(date +%F).dump"

# 2. Buckety – `mc mirror` z kontenera, który ma już poświadczenia i sieć MinIO
MSYS_NO_PATHCONV=1 docker compose run --rm --entrypoint sh -v "$PWD/backup:/backup" minio-init -c '
  mc alias set src http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" &&
  mc mirror --overwrite src/submissions  /backup/submissions &&
  mc mirror --overwrite src/public-media /backup/public-media'
```

(`MSYS_NO_PATHCONV=1` jest potrzebne wyłącznie w Git Bashu – bez niego MSYS przepisuje `/backup`
na ścieżkę windowsową.)

Odtworzenie:

```bash
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists \
  < backup/olimpiada-2026-09-05.dump
# oraz mc mirror w drugą stronę (/backup/... → dst/...)
```

Czego kopia **nie** zawiera i nie powinna: `.env` (sekrety trzymamy w menedżerze sekretów, nie
w kopii bazy) i wolumenu `clamav_db` (sygnatury pobierają się same).

### 6.2 Rotacja kluczy serwisowych MinIO

`minio-init` tworzy konta serwisowe **tylko raz** (`mc admin user info … || mc admin user add …`),
więc sama podmiana wartości w `.env` nic nie rotuje – nowe poświadczenia po prostu przestaną pasować
do istniejącego konta. Rotacja jest ręczna:

```bash
NEW=$(python -c "import secrets;print(secrets.token_urlsafe(24))")
# 1. Ustaw nowy sekret istniejącemu kontu (przykład: konto prywatne)
docker compose run --rm --entrypoint sh minio-init -c "
  mc alias set local http://minio:9000 \$MINIO_ROOT_USER \$MINIO_ROOT_PASSWORD &&
  mc admin user add local \$S3_PRIVATE_ACCESS_KEY $NEW &&
  mc admin policy attach local submissions-rw --user \$S3_PRIVATE_ACCESS_KEY"
# 2. Wpisz $NEW do .env jako S3_PRIVATE_SECRET_KEY
# 3. Zrestartuj procesy aplikacji (klient S3 czyta poświadczenia przy starcie)
docker compose up -d --force-recreate web worker beat
```

`mc admin user add` na istniejącym koncie **nadpisuje** sekret, nie zgłasza konfliktu – procedura
została przećwiczona na środowisku deweloperskim (po rotacji i restarcie presigned URL do bucketu
`submissions` dalej zwraca 200). Alternatywa dla instalacji, które chcą
kluczy krótkożyciowych: konta serwisowe MinIO (`mc admin user svcacct add/rm`) przypięte do konta
nadrzędnego – wtedy rotacja to `svcacct rm` + `svcacct add` bez dotykania polityk. Automatyzacja
tej procedury jest w [`docs/BACKLOG.md`](docs/BACKLOG.md) (dług T-09).

Zmiana **konta administracyjnego** (`MINIO_ROOT_*`) wymaga restartu usługi `minio`; backend go nie
używa (patrz `docs/SECURITY_CHECKLIST.md` 5.5), więc nie ma to wpływu na aplikację.

### 6.3 Zamknięcie etapu

Normalnie robi to `beat` (`apps.submissions.tasks.close_due_stages`, co 60 s) po
`deadline_at + grace_seconds`: najnowsza wersja każdego zgłoszenia dostaje status `LOCKED`,
a etap – znacznik `closed_at`.

Ręcznie (awaria beata, decyzja komitetu o wcześniejszym zamknięciu):

1. `/coordinator/` → karta etapu → **„Zamknij etap”**. Operacja jest idempotentna: powtórzenie daje
   `409 STAGE_ALREADY_CLOSED`, a nie ciche „nic się nie stało”. Do audytu trafia
   `stage.closed` z `manual: true`.
2. Potem **„Przydziel recenzentów”** (domyślnie 2 na pracę). Prace, dla których nie da się
   skompletować recenzentów bez konfliktu okręgu, są wypisane jako pominięte – z pseudonimem, nie
   z nazwiskiem. Przydział jest szeregowany blokadą doradczą, więc dwa równoległe kliknięcia nie
   dają czterech recenzentów.

Przesunięcie samych terminów robi się w `/admin/competitions/stage/<id>/change/` (z audytem
Django). Komenda `manage.py e2e_timeline` jest **wyłącznie** dla środowiska testowego i bez
`E2E_MODE=1` odmawia działania.

### 6.4 Przeniesienie treści zadań na prywatny storage (`migrate_statements`)

Pliki `Problem.statement_pdf` zapisane przed T-09 leżą na storage `default` (produkcyjnie: publiczny
bucket `public-media`). Idempotentna komenda przenosi je do `private_media` (prefiks
`problem-statements/` w prywatnym buckecie) i **nie kasuje** źródeł:

```bash
docker compose exec web python manage.py migrate_statements --dry-run   # plan
docker compose exec web python manage.py migrate_statements             # wykonanie
```

Po migracji treść zadania serwuje wyłącznie widok aplikacji
(`GET /api/competitions/problems/<id>/statement/`, 404 przed `Stage.opens_at`).

### 6.5 Publikacja wyników

1. `/coordinator/` → **„Przelicz wyniki (podgląd)”** – pełna tabela z danymi osobowymi, widoczna
   wyłącznie dla koordynatora, niczego nie ogłasza.
2. Po zamknięciu okna reklamacji i rozstrzygnięciu wszystkich spraw: **„Opublikuj wyniki”**
   z trybem anonimizacji (`CODE` – kod uczestnika; `INITIALS_SCHOOL` – inicjały i szkoła, z progiem
   k-anonimowości 3; `FULL` – tylko finał, tylko laureaci, tylko za zgodą).
3. Ponowna publikacja nadpisuje snapshot tego samego etapu i zostawia wpis w audycie.

## 7. Testy i kontrola jakości

```bash
# Testy jednostkowe i integracyjne (w kontenerze – tak jak w CI)
docker compose exec -T web pytest -q

# Pokrycie
docker compose exec -T web pytest -q --cov=apps --cov-report=term-missing:skip-covered

# Lint i formatowanie (host, wirtualne środowisko w backend/.venv)
cd backend && .venv/Scripts/ruff.exe format . && .venv/Scripts/ruff.exe check .
# Linux/WSL: cd backend && ruff format . && ruff check .

# Spójność migracji
docker compose exec -T web python manage.py makemigrations --check --dry-run

# Skan sekretów (katalog roboczy oraz historia gita)
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest detect -s /repo --no-git -v
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest detect -s /repo -v
```

### Scenariusz end-to-end

`e2e/` to jeden scenariusz Playwrighta (Python), który przechodzi **cały** cykl etapu na żywym
środowisku: rejestracja → upload PDF → prawdziwy skan ClamAV → zamknięcie etapu → dwie oceny (6 i 5)
→ moderacja → reklamacja → decyzja komisji (6) → publikacja → tabela publiczna bez nazwiska.

```bash
./scripts/e2e.sh              # reset danych + seed + scenariusz (Git Bash / WSL)
./scripts/e2e.sh --no-reset   # bez resetu – tylko dla środowiska, w którym etap eliminacyjny
                              # nie został jeszcze zamknięty (np. po awarii w połowie przebiegu)
```

Skrypt kasuje wolumeny `pg_data`, `minio_data` i `redis_data`, a **zostawia** `clamav_db` – pierwsze
pobranie sygnatur trwa kilka minut i nie ma powodu robić go przy każdym przebiegu.

Zmierzony czas (Docker Desktop na Windows 11, rozgrzany ClamAV i cache obrazów): **~60 s** na cały
`scripts/e2e.sh` – z czego reset środowiska i seed zajmują ~40 s, a sam scenariusz Playwrighta
**~9 s**. Przy pierwszym uruchomieniu doliczyć budowanie obrazu i pobranie sygnatur ClamAV.

Ręcznie, bez skryptu:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile e2e run --rm e2e
```

Artefakty diagnostyczne (zrzuty ekranu każdego kroku, HTML i log konsoli przy błędzie) lądują
w `e2e/artifacts/` – katalog jest ignorowany przez gita.

Dwie rzeczy, o których warto wiedzieć przy pisaniu nowych scenariuszy:

- **Presigned URL nie działa spod przeglądarki w sieci compose.** Link jest podpisywany hostem
  `S3_PUBLIC_ENDPOINT_URL` (`localhost:9000` w devie), którego w sieci kontenerów nie ma. Scenariusz
  sprawdza więc istnienie odnośnika w UI, a samo pobranie robi biblioteką `requests` z podmianą
  hosta wg `E2E_S3_HOST_REWRITE` i zachowanym nagłówkiem `Host` (podpis SigV4 obejmuje `Host`).
- **Oś czasu etapu** przesuwa się przez panel admina (`e2e/timeline.py`). Odpowiednik z wiersza
  poleceń, dla CI i uruchomień z hosta:

  ```bash
  docker compose exec web python manage.py e2e_timeline --stage 1 --phase appeals_open
  ```

  Komenda działa wyłącznie przy `E2E_MODE=1` (ustawiane w `docker-compose.dev.yml`); bez tej
  zmiennej odmawia.

## 8. Struktura repozytorium

```
backend/            aplikacja Django (apps/: accounts, competitions, submissions,
                    grading, appeals, results, cms, web, core), Dockerfile, testy
deploy/             Caddyfile i polityki MinIO
docs/               PROJEKT.md (architektura), BACKLOG.md, SECURITY_CHECKLIST.md,
                    COVERAGE.md, tasks/ (specyfikacje T-01…T-10)
e2e/                scenariusz Playwrighta i jego zależności
scripts/            e2e.sh
agent/              szkielet pętli agentycznej (LangGraph) – patrz PROJEKT.md 3
docker-compose.yml          produkcja/staging
docker-compose.dev.yml      nakładka developerska (kod z hosta, porty, profil e2e)
.gitleaks.toml              konfiguracja skanu sekretów wraz z uzasadnieniem wyjątków
```
