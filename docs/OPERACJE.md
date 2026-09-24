# Operacje: kopie zapasowe, monitoring, alarmy, CI/CD, 2FA, drugi konkurs

Dokument dla osoby, która utrzymuje działający serwis – nie dla programisty i nie dla
koordynatora. Odpowiada na pięć pytań, które padają w tej kolejności:

1. **czy przeżyjemy utratę serwera** (kopie zapasowe i odtwarzanie),
2. **skąd się dowiemy, że coś nie działa** (monitoring i alarmy),
3. **jak wjeżdża nowa wersja** (CI/CD),
4. **jak chronione są konta z dostępem do cudzych danych** (2FA),
5. **jak dołożyć drugi konkurs, nie ruszając pierwszego** (§ 6).

Dalej są dwie listy kontrolne: **incydentu** (§ 7) – do otwarcia wtedy, gdy nie ma czasu czytać
reszty – oraz **wdrożenia etapu 2** (§ 8), do przejścia po każdym wydaniu z serii E–K.

Adres produkcyjny: `olimpiadakwantowa.pl` (169.58.242.197), katalog `/opt/olimpiada`,
wdrożenie `scripts/deploy.sh` z kluczem `~/.ssh/olimpiada_deploy`.

---

## 1. Kopie zapasowe

### 1.1. Co jest kopiowane i dlaczego akurat to

| Co | Skąd | Do czego bez tego nie wrócimy |
|----|------|-------------------------------|
| Baza (`pg_dump -Fc`) | usługa `db` | konta, zgłoszenia, oceny, decyzje komisji, audyt |
| Kubełek `submissions` | MinIO | **prace uczestników** – w bazie są tylko ich metryki |
| Kubełek `public-media` | MinIO | obrazy i dokumenty z CMS-u |

Czego **nie** kopiujemy i dlaczego: obrazu aplikacji (odtwarza go `git` + `docker build`),
certyfikatów TLS (Caddy wystawia je na nowo w kilka sekund), kluczy DKIM (odtworzenie znaczy
wpis w DNS-ie, opisany w `mail-dns.txt` na serwerze) i wolumenu Redisa (to cache i broker –
po odtworzeniu odbudowuje się sam, a zadania w kolejce w scenariuszu awaryjnym i tak są
nieaktualne).

### 1.2. Jak to działa

`scripts/backup.sh`, uruchamiany przez crona hosta o **3:15** (wpis `/etc/cron.d/olimpiada-backup`
zakłada `scripts/deploy.sh` w kroku 8/8; przebieg loguje się do `/var/log/olimpiada-backup.log`):

1. `pg_dump -Fc` z kontenera `db` → plik lokalny,
2. `mc mirror` obu kubełków MinIO → katalog lokalny → `tar`,
3. **szyfrowanie** obu paczek: `gpg --symmetric --cipher-algo AES256` hasłem `BACKUP_PASSPHRASE`
   z `.env`,
4. wysyłka `rclone` do kubełka S3-kompatybilnego **u innego dostawcy** (`offsite:<BUCKET>/daily/`);
   pierwszego dnia miesiąca dodatkowo do `monthly/`,
5. retencja: zdalnie 30 dni w `daily/` i 365 dni w `monthly/`, lokalnie 7 dni,
6. meldunek do aplikacji: `manage.py record_backup_status --ok`.

**Bez `BACKUP_REMOTE_URL` skrypt robi wyłącznie kopię lokalną** i mówi o tym na stdout. Taka kopia
chroni przed „skasowałem nie tę edycję”, ale ginie razem z serwerem – czyli nie chroni przed tym,
przed czym kopie zapasowe mają chronić.

### 1.3. Konfiguracja (co wpisać w `.env` na serwerze)

`BACKUP_PASSPHRASE` generuje `scripts/deploy.sh` przy pierwszym przebiegu i **nie wypisuje go
w logu** (ten log bywa logiem GitHub Actions). Odczytaj je raz i zapisz w menedżerze haseł:

```bash
ssh -i ~/.ssh/olimpiada_deploy root@olimpiadakwantowa.pl 'grep BACKUP_PASSPHRASE /opt/olimpiada/.env'
```

> **Utrata tego hasła = utrata wszystkich kopii.** Nikt go nie odzyska: paczki są zaszyfrowane
> symetrycznie, a hasła nie ma nigdzie poza `.env` na serwerze i Twoim menedżerem haseł.
> Odwrotna strona tej samej monety: hasło leży na tym samym serwerze, co dane, więc chroni kopię
> **u dostawcy zewnętrznego**, a nie przed kimś, kto przejął serwer. To jest zamierzony zakres.

Cztery pozostałe wartości wpisuje się ręcznie (deploy zostawia je w `.env` zakomentowane):

```ini
BACKUP_REMOTE_URL=https://s3.eu-central-003.backblazeb2.com
BACKUP_ACCESS_KEY=...
BACKUP_SECRET_KEY=...
BACKUP_BUCKET=olimpiada-backup
# opcjonalne, część dostawców wymaga:
BACKUP_REMOTE_REGION=eu-central-003
BACKUP_REMOTE_PROVIDER=Other
```

Sprawdzeni dostawcy (wymagany wyłącznie interfejs zgodny z S3):

| Dostawca | Endpoint | Uwagi |
|----------|----------|-------|
| **Backblaze B2** | `https://s3.<region>.backblazeb2.com` | najtańszy w tej klasie; `BACKUP_REMOTE_PROVIDER=B2` |
| **Hetzner Object Storage** | `https://<region>.your-objectstorage.com` | serwerownia w UE (Falkenstein/Helsinki) |
| **Contabo Object Storage** | `https://<region>.contabostorage.com` | ten sam dostawca, co serwer – **wybierz inny region** niż ten, w którym stoi maszyna |

Klucz dostępowy załóż **tylko do zapisu i odczytu tego jednego kubełka**. Klucz z uprawnieniami do
kasowania po stronie dostawcy nie jest potrzebny do niczego poza retencją – a retencję można
zostawić regułom lifecycle dostawcy i odebrać kluczowi prawo `DeleteObject`. Wtedy ktoś, kto
przejmie serwer, nie skasuje kopii tym samym kluczem, którym je wysyłał.

### 1.4. Cotygodniowy test odtwarzania

`scripts/backup_verify.sh`, niedziela **4:40**: rozszyfrowuje najnowszą kopię, wstawia ją do
**tymczasowego** kontenera Postgresa (dane na `tmpfs`, kontener kasowany bezwarunkowo) i liczy
wiersze w `accounts_user`, `accounts_participant`, `competitions_stage`, `submissions_submission`
i `core_auditlog`. Wynik melduje przez `record_backup_status --verified` (albo `--failed`).

Po co, skoro `backup.sh` kończy się bez błędu: „`pg_dump` zwrócił 0” nie znaczy „z tej paczki da
się odtworzyć olimpiadę”. Kopia potrafi być pusta, obcięta albo zaszyfrowana hasłem, którego nikt
już nie zna – i każdy z tych przypadków wychodzi dopiero przy odtwarzaniu.

### 1.5. Podgląd stanu

```bash
# na serwerze
docker compose exec web python manage.py record_backup_status --show
ls -lh /opt/olimpiada-backups/
tail -50 /var/log/olimpiada-backup.log
```

`/status.json` (publiczny) niesie `backup_last_ok` i `backup_last_verified` jako **wartości
logiczne**. Dat tam nie ma świadomie: strona jest publiczna, a data ostatniej kopii mówi obcemu,
kiedy uderzenie zaboli najbardziej.

---

## 2. Odtwarzanie

### 2.1. Zasada

`scripts/restore.sh` odtwarza **do nowej bazy i nowego kubełka**, nigdy „na miejsce”. Odtwarza się
w sytuacji, w której nikt do końca nie wie, co się stało; nadpisanie działającej bazy zrzutem
sprzed doby zamienia wtedy jeden problem w drugi, nieodwracalny.

### 2.2. Przebieg

```bash
cd /opt/olimpiada
./scripts/restore.sh --list                 # co jest, lokalnie i u dostawcy
./scripts/restore.sh --dry-run              # czy hasło pasuje i czy paczki się otwierają
./scripts/restore.sh --dump db-20260117T031500Z.dump.gpg \
                     --files files-20260117T031500Z.tar.gpg
```

Po przebiegu istnieje baza `restore_20260117_031500` i kubełek
`submissions-restore-20260117-031500`. Serwis **nadal działa na danych bieżących**.

Kopię zdalną trzeba najpierw ściągnąć (skrypt czyta katalog lokalny):

```bash
docker run --rm -v /opt/olimpiada-backups:/data \
  -e RCLONE_CONFIG_OFFSITE_TYPE=s3 \
  -e RCLONE_CONFIG_OFFSITE_ENDPOINT="$BACKUP_REMOTE_URL" \
  -e RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID="$BACKUP_ACCESS_KEY" \
  -e RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY="$BACKUP_SECRET_KEY" \
  rclone/rclone:1.69 copy "offsite:$BACKUP_BUCKET/daily/db-20260117T031500Z.dump.gpg" /data/
```

### 2.3. Przełączenie serwisu na odtworzone dane (krok ręczny, z przerwą w działaniu)

Dopiero po sprawdzeniu, że w odtworzonej bazie jest to, czego szukamy:

```bash
cd /opt/olimpiada
docker compose stop web worker beat                      # nikt nic nie zapisuje
docker compose exec -T db psql -U olimpiada -d postgres -c \
  'ALTER DATABASE olimpiada RENAME TO olimpiada_przed_awaria'
docker compose exec -T db psql -U olimpiada -d postgres -c \
  'ALTER DATABASE restore_20260117_031500 RENAME TO olimpiada'
# kubełek: przepnij pliki na nazwę produkcyjną albo zmień S3_SUBMISSIONS_BUCKET w .env
docker compose up -d web worker beat
curl -s https://olimpiadakwantowa.pl/status.json
```

Starej bazy **nie kasuj** przez co najmniej tydzień. To jedyny ślad tego, co było przed awarią,
a pytanie „czy na pewno nic nie zginęło” pada zawsze po kilku dniach, nigdy od razu.

---

## 3. Monitoring

Dwie warstwy, bo widzą co innego.

### 3.1. Z zewnątrz: Uptime Kuma

Pełna instrukcja (uruchomienie, sześć monitorów, kanały powiadomień): **`deploy/monitoring/README.md`**.

```bash
docker compose --profile monitoring up -d monitor
docker compose restart proxy     # certyfikat dla monitor.<domena>
```

Wymaga rekordu DNS `monitor.<domena>` → adres serwera. Pierwsze wejście zakłada konto
administratora – zrób to od razu, bo do tego czasu pulpit jest otwarty.

**Ograniczenie, które trzeba znać:** ten monitor stoi na tej samej maszynie, co serwis. Awaria
hosta, sieci u dostawcy albo zasilania zabiera go razem z serwisem. Dlatego **co najmniej jeden**
monitor musi stać gdzie indziej: dowolna darmowa usługa odpytująca `https://<domena>/status.json`
co 5 minut i szukająca w treści `"status": "ok"`.

### 3.2. Od środka: watchdog aplikacyjny

`apps/core/alerts.py`, zadanie `apps.core.tasks.alerts_check` co **5 minut** (`CELERY_BEAT_SCHEDULE`).
Widzi to, czego nie widać z zewnątrz:

| Sprawdzenie | Próg | Co znaczy |
|-------------|------|-----------|
| podsystemy (baza, cache, magazyn, kolejka) | te same, co `/status/` | patrz niżej |
| wolne miejsce na dysku | < 10 % | za kilkanaście godzin stanie wszystko naraz |
| nieudane zadania Celery | ≥ 5 w 15 min | kolejka przyjmuje i gubi |
| odpowiedzi 5xx | ≥ 10 w 15 min | ktoś właśnie nie może oddać pracy |
| brak kopii zapasowej | > 36 h | patrz § 1 |
| brak testu odtwarzania | > 10 dni | patrz § 1.4 |

Włączenie: w `.env` na serwerze

```ini
ALERT_EMAILS=dyzurny@qaif.org,koordynator@qaif.org
```

Pusta wartość (domyślna) **wyłącza wysyłkę** – instalacja deweloperska nikogo nie budzi.
Po zmianie: `docker compose up -d web worker beat`.

Każdy rodzaj alarmu ma **godzinne wyciszenie**: trwająca awaria daje jeden list na godzinę, a nie
dwieście osiem dziennie. Wyciszenie jest per rodzaj, więc awaria magazynu plików nie zagłusza
informacji o kończącym się dysku. Każdy wysłany alarm zostaje w audycie jako `alert.sent`.

### 3.3. Ręczne sprawdzenie stanu

```bash
curl -s https://olimpiadakwantowa.pl/status.json | python3 -m json.tool
docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
docker compose exec web python manage.py record_backup_status --show
docker compose exec -T web python -c \
  "from apps.core.alerts import evaluate; print([a.title for a in evaluate()] or 'brak alarmów')"
```

---

## 4. CI/CD

### 4.1. `.github/workflows/ci.yml` – przy każdym push i pull requeście

Pięć zadań równolegle, żadne nie wymaga sekretów (dzięki temu działa też dla zgłoszeń z forka):

| Zadanie | Co robi | Czemu zapobiega |
|---------|---------|-----------------|
| `lint` | `ruff check` + `ruff format --check` | rozjazdom stylu i typowym pomyłkom |
| `migrations` | `makemigrations --check --dry-run` | wdrożeniu, po którym baza nie zgadza się z kodem |
| `translations` | `msgfmt --check` na każdym `.po` | nieudanemu **budowaniu obrazu** (Dockerfile woła `msgfmt`) |
| `tests` | pełny `pytest` z usługą Postgresa | regresjom |
| `image` | `docker build --target runtime` | nieudanemu budowaniu na produkcji w środku wdrożenia |

Redisa i MinIO w usługach CI nie ma świadomie: `config/settings/test.py` podmienia cache na
lokalny, magazyn plików na dyskowy, a Celery na tryb `eager`, więc byłyby usługami, które nic nie
sprawdzają i dokładają własne tryby awarii.

### 4.2. `.github/workflows/deploy.yml` – wyłącznie ręcznie

Uruchamiane z zakładki *Actions → Deploy → Run workflow*. Automatycznego wdrożenia po scaleniu
do `main` **nie ma i nie będzie**: etap trwa tygodniami i kończy się terminem, którego nie da się
przesunąć, więc nikt nie może zmienić serwisu przypadkiem.

Workflow woła ten sam `scripts/deploy.sh`, co wdrożenie z laptopa, a na końcu sprawdza
`/status.json` – wdrożenie, które zostawiło serwis w stanie `degraded`, kończy się czerwonym
krzyżykiem.

**Sekrety do ustawienia** (*Settings → Secrets and variables → Actions*):

| Nazwa | Rodzaj | Zawartość |
|-------|--------|-----------|
| `DEPLOY_SSH_KEY` | secret | **prywatny** klucz `~/.ssh/olimpiada_deploy` w całości, razem z liniami `-----BEGIN/END-----` |
| `COORDINATOR_EMAIL` | secret | adres pierwszego koordynatora (używany tylko przy pierwszym wdrożeniu) |
| `COORDINATOR_PASSWORD` | secret | jego hasło (jw.) |
| `SITE_DOMAIN` | variable | `olimpiadakwantowa.pl` |
| `ACME_EMAIL` | variable | adres do Let's Encrypt |

Dodatkowo załóż środowisko **`production`** (*Settings → Environments*) i włącz w nim
*Required reviewers*. Bez tego każdy z prawem zapisu w repozytorium wdraża produkcję jednym
kliknięciem.

Klucz wdrożeniowy ma na serwerze pełne uprawnienia roota. Jeśli kiedykolwiek wyciekł – wymień go:
`ssh-keygen -t ed25519 -f ~/.ssh/olimpiada_deploy`, wpisz nowy klucz publiczny do
`/root/.ssh/authorized_keys`, usuń stary, podmień sekret w GitHubie.

### 4.3. Obraz z GHCR / świeża instalacja

Dwie rzeczy, które dokłada etap 2 do tego, co wyżej: obraz aplikacji **publikowany** przez CI
i zestaw usług na pierwsze uruchomienie u kogoś, kto instaluje platformę u siebie. Dla produkcji
Olimpiady Kwantowej obie są **opcjonalne i domyślnie wyłączone** — wdrożenie bez zmiennej
`WEB_IMAGE` wykonuje te same polecenia, co przed etapem 2.

#### Co publikuje CI

Zadanie `image` w `.github/workflows/ci.yml` buduje obraz przy **każdym** zgłoszeniu (także z forka)
i **wypycha** go do GHCR wyłącznie z gałęzi `main` i ze znaczników `v*`:

| Zdarzenie | Tagi w `ghcr.io/qaif/olimpiada-web` | Publikacja |
|---|---|---|
| znacznik `v0.24.0` | `v0.24.0`, `sha-<7 znaków>` | tak |
| push do `main` | `main`, `sha-<7 znaków>` | tak |
| pull request | — | nie (sam build) |

Logowanie idzie wbudowanym `GITHUB_TOKEN`-em (uprawnienie `packages: write` nadane **tylko** temu
zadaniu) — w repozytorium nie ma i nie powstaje żaden sekret do rejestru. Obrazów nie podpisujemy.
Nie ma osobnych obrazów `worker` i `beat`: to ten sam obraz z innym poleceniem, więc trzy tagi
znaczyłyby trzy różne wersje kodu w jednym wdrożeniu.

Pakiet jest domyślnie **prywatny**. Żeby ktokolwiek spoza organizacji mógł go pobrać, trzeba raz
przestawić go na publiczny: *Packages → olimpiada-web → Package settings → Change visibility*.
Dopóki jest prywatny, `docker pull` wymaga zalogowania (`docker login ghcr.io` tokenem z zakresem
`read:packages`) — i to jest sensowny stan do czasu, aż licencja i skład obrazu zostaną przejrzane.

#### Wdrożenie produkcyjne z gotowego obrazu

```bash
WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v0.24.0 scripts/deploy.sh root@olimpiadakwantowa.pl
```

Krok 4/8 pobiera wtedy obraz (`docker compose pull web`) zamiast go budować i zapisuje wartość do
`/opt/olimpiada/.env`, bo każde późniejsze `docker compose` na serwerze musi widzieć ten sam obraz.
Pozostałe kroki — kopia bazy (4a), start usług (4b), seedy (6), konkurs (6a), DNS poczty (7), cron
kopii (8) — są **bez zmian**.

Powrót do budowania na serwerze: kolejne wdrożenie **bez** `WEB_IMAGE`; skrypt sam kasuje wpis
z `.env`. Sprawdzenie, co jest teraz źródłem obrazu:

```bash
ssh root@<host> "grep -E '^WEB_IMAGE=' /opt/olimpiada/.env || echo 'obraz budowany na serwerze'"
```

Kiedy to ma sens: gdy serwer nie ma pamięci albo czasu na budowanie (build ciągnie zależności
i kompiluje), gdy chcemy wdrożyć **dokładnie ten** obraz, który przeszedł CI, i gdy wdrożenie ma
być odtwarzalne co do bajtu. Pułapka jest jedna i trzeba ją znać: obraz z rejestru odpowiada
**znacznikowi**, a kod na serwerze — temu, co jest w `HEAD` (krok 2/8 wysyła `git archive HEAD`).
Wdrażaj z rejestru z **odpowiadającego** znacznika, inaczej migracje w obrazie mogą się rozjechać
z plikami w `/opt/olimpiada`.

#### Świeża instalacja u nowego operatora

Nakładka `docker-compose.operator.yml` daje minimalny zestaw ośmiu usług (`proxy`, `web`, `worker`,
`beat`, `db`, `redis`, `minio`, `minio-init`) — bez `clamav` i bez `mail`:

```bash
cp .env.example .env                                                     # uzupełnić
echo 'WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v0.24.0' >> .env
DC="docker compose -f docker-compose.yml -f docker-compose.operator.yml"
$DC pull && $DC up -d
$DC ps                                   # czekamy na web = healthy
```

Następny krok po `up` to **kreator `/setup/`**: zakłada konto operatora i pierwszy konkurs. Kreator
działa wyłącznie na instalacji bez konkursu i bez superużytkownika — na działającym serwisie ten
adres odpowiada 404 (punkt 22 listy kontrolnej produkcji). Token wejściowy bierze się z `SETUP_TOKEN`
w `.env`, a gdy zmiennej nie ma, kreator wypisuje go **raz** do logu kontenera `web` (`$DC logs web`)
— log wdrożenia bywa publiczny, więc token nie jest nigdzie wypisywany drugi raz.

Treść CMS-a (regulamin, dokumenty, harmonogram…) wgrywają komendy `manage.py seed_regulamin`
i `manage.py seed_legacy_content` — pełny przebieg tej drugiej nie chodzi po każdym wdrożeniu, bo
nadpisałby poprawki redakcji wprowadzone w `/cms/` (patrz jej docstring). Gdy trzeba tylko zdjąć
plik wycofany przez organizatora z instalacji, która go jeszcze ma (dziś: PDF ze składem
komitetów), bez dotykania treści i rewizji strony, służy do tego wąska komenda
`manage.py retire_legacy_files` (`--dry-run` tylko liczy, ile plików by usunęła).

Komplet usług (skaner i własny MTA) wraca profilem, bez zmiany plików:

```bash
$DC --profile full up -d
```

Zrób to **przed** otwarciem rejestracji: bez `clamav` nadesłane pliki zostają w stanie „oczekuje na
skan”, a bez `mail` (albo bez `EMAIL_URL` zewnętrznego dostawcy) nie działa aktywacja konta ani
reset hasła. Zestaw usług z profilem `full` jest **równy** temu, co startuje z samego
`docker-compose.yml` — pilnuje tego `scripts/tests/compose_profiles_test.sh`, a rozgałęzienia
w kroku 4/8 `scripts/tests/deploy_image_source_test.sh`. Produkcja Olimpiady Kwantowej **nie
używa** tej nakładki: `scripts/deploy.sh` startuje komplet usług, tak jak dotąd.

---

## 5. Logowanie dwuskładnikowe (2FA)

> ### Stan na tej instalacji: **WYŁĄCZONE**
>
> Decyzja organizatora („autoryzacja 2-etapowa wyłączona”). `TWO_FACTOR_ENABLED` jest domyślnie
> `0` i cała sekcja poniżej opisuje **funkcję uśpioną** – to, co się stanie, jeśli organizator
> kiedyś zdecyduje inaczej. Dopóki wyłącznik jest wyłączony:
>
> - `/account/2fa/`, `/account/2fa/codes/`, `/account/2fa/disable/`, `/login/2fa/` oraz reset
>   z panelu koordynatora odpowiadają **404**,
> - w interfejsie nie ma ani jednego odnośnika do drugiego składnika (profil konta, ekran konta
>   w panelu koordynatora),
> - warstwa wymuszająca przepuszcza **każde** żądanie bez jednego zapytania do bazy,
> - `TWO_FACTOR_REQUIRED_ROLES` nie znaczy nic.
>
> **Konta, które zdążyły włączyć 2FA wcześniej, logują się samym hasłem.** Ich urządzenia
> (`TwoFactorDevice`) zostają w bazie nietknięte – wyłącznik ich nie kasuje, więc ponowne
> włączenie przywraca stan sprzed wyłączenia zamiast zmuszać komitet do konfiguracji od nowa.
> Jeśli urządzenia mają zniknąć naprawdę, jest to osobna, świadoma czynność (§ 5.6).

### 5.1. Polityka (gdy funkcja jest włączona)

- **wymagane od kont, które widzą cudze dane**: koordynator, członek komitetu, komisja odwoławcza.
  Przejęcie takiego konta kosztuje wszystkie prace, wszystkie dane osobowe małoletnich i możliwość
  zmiany wyników,
- **zalecane opiekunom szkolnym**,
- **nieobowiązkowe dla uczestników** – i tak ma zostać. Uczestnik loguje się kilka razy w roku,
  często ze szkolnego komputera, a zgubiony drugi składnik w noc przed deadline'em kosztowałby go
  udział w zawodach.

### 5.1a. Włączenie i wyłączenie całej funkcji

```ini
# /opt/olimpiada/.env
TWO_FACTOR_ENABLED=1        # 0 (domyślnie) = funkcji nie ma
```

```bash
docker compose up -d web worker beat     # ustawienie czyta proces przy starcie
```

Kolejność przy **włączaniu** ma znaczenie i jest odwrotna, niż podpowiada odruch: najpierw
`TWO_FACTOR_ENABLED=1` z pustym `TWO_FACTOR_REQUIRED_ROLES` (komitet włącza 2FA dobrowolnie
i w swoim tempie), a dopiero potem § 5.3. Zrobione naraz, zamyka koordynatorowi drogę do
własnego panelu, zanim ktokolwiek zdąży zainstalować aplikację.

Przy **wyłączaniu** kolejności pilnować nie trzeba: wyłącznik główny wygrywa z listą ról, więc
`TWO_FACTOR_REQUIRED_ROLES` zostawione w `.env` po poprzedniej konfiguracji nie odeśle nikogo na
ekran, którego już nie ma.

### 5.2. Włączenie u siebie

`Twoje konto → Logowanie dwuskładnikowe` (`/account/2fa/`): kod QR, potwierdzenie sześciocyfrowym
kodem z aplikacji, dziesięć kodów zapasowych **pokazywanych raz**. Działa każda aplikacja TOTP
(Aegis, FreeOTP, Google Authenticator, menedżer haseł).

Kody zapasowe: wydrukować albo przepisać i schować **poza telefonem**. W bazie są wyłącznie ich
skróty, więc pokazać ich drugi raz nie sposób.

### 5.3. Wymuszenie dla ról (dopiero po tym, jak komitet ma już aplikacje)

```ini
TWO_FACTOR_REQUIRED_ROLES=coordinator,reviewer,appeals
```

Konto z tej grupy bez potwierdzonego urządzenia trafia na ekran konfiguracji i nie zrobi nic
innego, dopóki drugiego składnika nie włączy.

> **Kolejność ma znaczenie.** Włączenie tego w dniu wdrożenia zamyka koordynatorowi drogę do
> własnego panelu, zanim ktokolwiek zdąży zainstalować aplikację. Najpierw komitet włącza 2FA
> dobrowolnie, potem domykasz furtkę tą zmienną. Domyślna wartość jest pusta.

### 5.4. „Zgubiłem telefon”

1. **z kodem zapasowym** – właściciel wpisuje go w to samo pole, co kod z aplikacji, wchodzi na
   konto, wyłącza 2FA i włącza je od nowa na nowym urządzeniu,
2. **bez kodu zapasowego** – koordynator zdejmuje zabezpieczenie:
   `Panel → Konta → (konto) → Logowanie dwuskładnikowe → Zdejmij drugi składnik`.

   **Zanim to zrobisz, potwierdź tożsamość drogą inną niż e-mail z tego konta** (telefon do szkoły,
   rozmowa wideo). Prośba przysłana z przejętej skrzynki wygląda dokładnie tak samo jak prośba
   prawdziwa – a ten przycisk jest po to, żeby zdejmować zabezpieczenie, więc jest też najkrótszą
   drogą dla kogoś, kto chce je zdjąć cudzymi rękami. Reset zostaje w audycie pod Twoim nazwiskiem
   (`2fa.reset`).

Ślad audytowy: `2fa.enabled`, `2fa.disabled`, `2fa.reset`, `2fa.verified`, `2fa.failed`
(`Panel → Audyt`, filtr po akcji).

### 5.5. Rotacja `DJANGO_SECRET_KEY` unieważnia wszystkie drugie składniki

Sekrety TOTP są zaszyfrowane kluczem wyprowadzonym z `SECRET_KEY`. Po jego zmianie żaden kod nie
pasuje i żadne konto z 2FA się nie zaloguje. Jeśli musisz go wymienić:

1. **przed** zmianą: `TWO_FACTOR_REQUIRED_ROLES=` (pusta wartość),
2. wyczyść urządzenia:
   `docker compose exec web python manage.py shell -c "from apps.accounts.twofactor import TwoFactorDevice; TwoFactorDevice.objects.all().delete()"`,
3. zmień `SECRET_KEY`, zrestartuj `web worker beat`,
4. poproś komitet o ponowne włączenie 2FA, potem przywróć `TWO_FACTOR_REQUIRED_ROLES`.

Zmiana `SECRET_KEY` unieważnia przy okazji wszystkie sesje i linki resetu hasła – planuj ją poza
oknem zawodów.

Przy wyłączonym `TWO_FACTOR_ENABLED` rotacja `SECRET_KEY` nie wymaga żadnego z tych kroków: nikt
nie loguje się drugim składnikiem, więc nieczytelne sekrety niczego nie blokują. Znaczy to jednak,
że **po ponownym włączeniu funkcji urządzenia sprzed rotacji będą martwe** – wtedy trzeba je
skasować (§ 5.6) i poprosić komitet o konfigurację od nowa.

### 5.6. Skasowanie zapisanych urządzeń (osobno od wyłącznika)

Wyłącznik główny **nie kasuje** urządzeń i to jest zamierzone: wyłączenie ma być odwracalne jednym
wpisem w `.env`. Skasowanie ich jest inną decyzją – podejmuje się ją, gdy 2FA nie wróci albo gdy
sekrety i tak są już nieczytelne po rotacji `SECRET_KEY`:

```bash
docker compose exec web python manage.py shell -c \
  "from apps.accounts.twofactor import TwoFactorDevice; print(TwoFactorDevice.objects.count())"
docker compose exec web python manage.py shell -c \
  "from apps.accounts.twofactor import TwoFactorDevice; TwoFactorDevice.objects.all().delete()"
```

Operacja jest nieodwracalna i **nie zostawia wpisu w audycie** (idzie z powłoki, nie z panelu),
więc zanotuj ją sam: kto, kiedy i dlaczego. Sama utrata drugich składników nikogo nie odcina od
konta – hasło działa dalej.

---

## 6. Drugi konkurs na tej samej instalacji

Platforma prowadzi wiele niezależnych konkursów z jednej bazy i jednego wdrożenia
(`docs/UNIWERSALNY-ETAP-1.md`). Ten runbook jest kolejnością czynności **na produkcji** — opis
samej komendy i wariantu z prefiksem ścieżki jest w README § 3 („Kolejny konkurs na tej samej
instalacji”), a tutaj stoi to, czego README nie zna: co sprawdzić **przed** i czym przełączyć flagi.

Kolejność nie jest dowolna. Konkurs założony przed pre-flightem członkostw nadal zadziała, ale
przełącznik ról zostanie wtedy przestawiony na bazie, o której nikt nie sprawdził, czy backfill
jej nie pominął — a objaw tego wychodzi dopiero wtedy, gdy recenzent nie widzi przydziałów.

### 6.1. Pre-flight: czy wolno przełączyć role na członkostwa

O tym, czy ktoś jest recenzentem, rozstrzyga dziś **globalna grupa Django**; po przełączeniu flagi
`memberships_enforced` rozstrzyga **wiersz `accounts.Membership` konkursu**. Różnicę pokazuje:

```bash
# na serwerze, w /opt/olimpiada
docker compose exec -T web python manage.py check_memberships          # kod 1, gdy jest rozjazd
docker compose exec -T web python manage.py check_memberships --all    # także role bez rozjazdu
docker compose exec -T web python manage.py check_memberships --fix    # dopisz brakujące
```

Komenda nigdy nie kasuje członkostw — `--fix` wyłącznie dopisuje. Czytając wynik:

- `UWAGA … bez członkostwa N z M` — **te osoby stracą dostęp** po przełączeniu flagi. Uruchom
  `--fix`, a potem komendę jeszcze raz bez flagi: ma wyjść zero.
- `info … członkostw bez grupy Django` — to nie jest rozjazd ról, tylko brak dostępu do `/cms/`
  (panel redakcyjny wisi na uprawnieniach grupy `coordinator`, migracja `cms.0003_coordinator_permissions`).
  Dotyczy koordynatorów i naprawia się dodaniem do grupy w `/admin/ → Użytkownicy`.

Przy **jednym** konkursie w bazie komenda przyjmuje, że każdy członek globalnej grupy należy do
niego (bo innego nie ma). Od drugiego konkursu przypisuje wyłącznie osoby, które mają w konkursie
ślad: profil uczestnika, profil opiekuna szkolnego albo jakiekolwiek członkostwo. Członek grupy bez
takiego śladu nie jest przypisywany nigdzie — i to jest właściwa odpowiedź, bo zgadywanie dałoby
recenzentowi jednego konkursu wgląd w prace drugiego.

### 6.2. Założenie konkursu — najpierw na sucho

```bash
docker compose exec -T web python manage.py create_competition \
  --slug fizyczna --name "Olimpiada Fizyczna" --domain olimpiadafizyczna.pl \
  --from-template przedmiotowa --organizer "Polskie Towarzystwo Fizyczne" \
  --contact-email biuro@example.org --coordinator-email koordynator@example.org \
  --dry-run
```

`--dry-run` wykonuje **całość** i wycofuje transakcję, więc sprawdza to, co sprawdzi baza
(unikalność identyfikatora i domeny, więzy edycji, walidację modeli), a nie to, co o niej pamiętamy.
Powtórz bez `--dry-run`, gdy wydruk się zgadza.

Komenda zakłada przy okazji **pierwszą edycję** (bieżącą) i etapy z szablonu. Ich terminy są
wartością początkową odłożoną od pierwszego dnia następnego miesiąca — mają wyglądać na zastępcze,
bo są zastępcze. Harmonogram wpisuje koordynator w panelu; `--edition-label` nadpisuje domyślne
oznaczenie rocznika (`I edycja <rok>/<rok+1>`, liczone od września).

`--coordinator-email` wymaga **istniejącego** konta: komenda kont nie zakłada. Nadaje rolę
koordynatora w tym konkursie **i** dopisuje do grupy Django `coordinator` — ta grupa jest globalna,
więc daje dostęp do `/cms/` całej instalacji. Jeżeli redakcje mają być rozdzielone, ogranicz temu
kontu uprawnienia do stron w `/cms/ → Ustawienia → Grupy`.

### 6.3. `.env`, wdrożenie, DNS

Dołożenie domeny musi zadziałać w **trzech** konfiguracjach naraz, bo każdy brak milczy inaczej:
brak w Caddym = brak certyfikatu i „no such site”, brak w `ALLOWED_HOSTS` = 400 na każde żądanie,
brak w `CSRF_TRUSTED_ORIGINS` = odmowa na każdym formularzu. Wpisuje się ją **w jednym** miejscu:

```dotenv
# /opt/olimpiada/.env
EXTRA_DOMAINS=olimpiadafizyczna.pl www.olimpiadafizyczna.pl
```

Django dokłada stąd hosty do `DJANGO_ALLOWED_HOSTS` i origins `https://…` do
`DJANGO_CSRF_TRUSTED_ORIGINS` samo (`config/settings/base.py`) — wpisanie ich wprost niczego nie
psuje, wartości ręczne zostają na początku list. Potem:

```bash
./scripts/render_caddyfile.sh && docker compose up -d proxy web worker beat
docker compose exec -T web python manage.py check_domains --all       # kontrola trzech miejsc
```

**DNS** jest ostatni, bo dopiero po nim Caddy może pobrać certyfikat: rekord A/AAAA
`olimpiadafizyczna.pl` → adres serwera (i `www.`, jeżeli ta nazwa ma działać). Osobny rekord `s3.`
nie jest potrzebny — bucket jest jeden i pliki idą przez `S3_PUBLIC_ADDRESS` domeny platformy.
Po zmianie DNS-u powtórz `check_domains` i otwórz stronę główną konkursu.

Ten sam konkurs da się założyć wdrożeniem (krok 6a, z `--skip-existing`, więc wdrożenie da się
powtórzyć):

```bash
NEW_COMPETITION_SLUG=fizyczna NEW_COMPETITION_NAME="Olimpiada Fizyczna" \
NEW_COMPETITION_DOMAIN=olimpiadafizyczna.pl NEW_COMPETITION_TEMPLATE=przedmiotowa \
NEW_COMPETITION_EDITION_LABEL="I edycja 2026/2027" \
NEW_COMPETITION_COORDINATOR_EMAIL=koordynator@example.org \
scripts/deploy.sh root@<host>
```

### 6.4. Przełączniki konkursu w `/admin/`

Flagi siedzą w polu `feature_flags` wiersza konkursu (`/admin/ → Konkursy → <konkurs>`), jako JSON
z **różnicami** wobec wartości domyślnych. Pusty słownik `{}` znaczy „jak dziś”.

```json
{"memberships_enforced": true, "competition_settings_page": true}
```

- **`memberships_enforced`** — przełącza autoryzację z globalnych grup Django na `Membership` tego
  konkursu. Przełączaj **wyłącznie po zielonym `check_memberships`** (§ 6.1). Cofnięcie to ta sama
  jedna wartość, bez wdrożenia: flaga zostaje w kodzie jeden sezon właśnie po to.
- **`competition_settings_page`** — pokazuje koordynatorowi ekran „Ustawienia konkursu”
  (`/coordinator/competition/`): marka, organizator, kontakt. Adresowania (witryna, identyfikator,
  tryb, prefiks) nie ma tam z założenia — zmiana domeny wymaga dostępu do serwera, więc należy do
  operatora platformy, nie do koordynatora.
- **`participant_forum`** — otwiera forum uczestników (`/forum/`) i jego moderację
  (`/coordinator/forum/`). Wyłączona znaczy, że tych adresów **nie ma** (404) i że w żadnym menu nie
  przybywa ani jedna pozycja. Ta flaga różni się od pozostałych jednym: jej zapalenie nie jest
  decyzją techniczną, tylko **zobowiązaniem organizatora do dyżuru moderacyjnego**. Pod tym adresem
  piszą publicznie osoby niepełnoletnie, a domyślny tryb „przed publikacją” znaczy, że nieobsłużona
  kolejka to forum, które milczy. Nie zapalaj jej „na próbę” ani przed uzgodnieniem z organizatorem,
  kto i jak często zagląda do `/coordinator/forum/`. Pierwszy krok **po** zapaleniu: założyć co
  najmniej jeden dział (`/coordinator/forum/categories/`) — bez działu nikt nie napisze ani słowa.
  Szczegóły moderacji: `PODRECZNIK-ORGANIZATORA.md` § 6.4.
- **`student_status_certificate`** — zaświadczenia o statusie ucznia (v0.34.0): strona uczestnika
  `/me/status-ucznia/`, ekran koordynatora `/coordinator/student-status/`, wybór „tylko uczniowie
  z potwierdzonym statusem” przy paczkach ZIP. Wyłączona znaczy, że adresów **nie ma** (404). Zapalenie
  jest decyzją organizatora o **nowej kategorii danych** (skan dokumentu z datą urodzenia i podpisem
  dyrektora szkoły) — rejestr czynności dostaje przy niej własny wiersz. Szczegóły: § 15.
- **`workshop_materials`** — materiały z warsztatów (v0.34.0): wgrywanie przez koordynatora
  (`/coordinator/workshops/materials/`) i oglądanie po zalogowaniu (`/warsztaty/materialy/`).
  Wyłączona znaczy, że tych adresów **nie ma** (404), a strona „Warsztaty” i menu wyglądają jak dotąd.
  **Przed zapaleniem** trzy kroki operatora z § 16: polityka MinIO z uprawnieniami wgrywania
  wieloczęściowego, sprawdzenie miejsca na dysku i świadomość, że materiały nie wchodzą do kopii nocnej.
- **`ai_grading`** — ocena AI (sugestia punktów dla komitetu liczona przez Claude'a). Wyłączona
  znaczy, że `/coordinator/ai-grading/…` odpowiada 404, a panele wyglądają jak dziś. Zapalenie
  jest **decyzją prawną organizatora** (umowa powierzenia z Anthropic, polityka prywatności,
  regulamin), a nie techniczną — nie zapalaj jej przed jej potwierdzeniem. Szczegóły serwerowe: § 17.

Po każdym przestawieniu flagi: zaloguj się na konto jednej osoby z każdej roli i sprawdź, że widzi
to, co widziała. Flaga jest odwracalna w minutę, ale tylko wtedy, gdy ktoś zauważy w tej minucie.

### 6.5. Konkursy w subdomenach zakładane z panelu

Wszystko powyżej wymaga **operatora przy serwerze**: wpisu w `.env`, wdrożenia i rekordu DNS na
każdy konkurs. Ten tryb zdejmuje ten wymóg z konkursów, które mieszkają pod domeną platformy:
koordynator zakłada konkurs w panelu, a ten działa pod `<slug>.<domena platformy>` (np.
`fizyczna.olimpiadakwantowa.pl`) **od razu** — bez wdrożenia, bez edycji `.env` i bez nowego
rekordu DNS.

Konkurs z **własną** domeną (`olimpiadafizyczna.pl`) idzie nadal drogą z § 6.3 — tej ten tryb nie
zastępuje ani nie zmienia.

#### Jednorazowe przygotowanie (operator, raz na instalację)

1. **DNS: rekord wieloznaczny.** `*` → adres serwera, typ A, u operatora strefy (dla
   olimpiadakwantowa.pl: home.pl). Gotowy wpis i wyjaśnienie, czego ten rekord **nie** rusza
   (`www`, `s3`, `meet`, `mail`, sama domena, SPF/DKIM/DMARC): `deploy/dns-olimpiadakwantowa.pl.md`,
   sekcja „Rekord z gwiazdką”. Nic nie dzieje się automatycznie — rekord wpisuje człowiek w panelu
   rejestratora.
2. **Przełącznik w `/opt/olimpiada/.env`:**

   ```dotenv
   PLATFORM_SUBDOMAINS=1
   CADDYFILE_PATH=./deploy/Caddyfile.generated   # wdrożenie ustawia to samo
   ```

3. **Wdrożenie** (`scripts/deploy.sh root@<host>`) albo, na miejscu, samo przegenerowanie proxy:

   ```bash
   cd /opt/olimpiada && ./scripts/render_caddyfile.sh && docker compose up -d proxy web
   ```

   Krok 4/8 wdrożenia generuje wtedy konfigurację Caddy'ego z opcją globalną
   `on_demand_tls { ask http://web:8000/internal/tls-allowed }` i blokiem `*.<domena>`
   (`tls { on_demand }`). Na koniec wdrożenie wypisuje przypomnienie o rekordzie DNS — tylko wtedy,
   gdy przełącznik jest włączony.
4. **Flaga `competition_creation`** na konkursie, **którego** koordynatorzy mają zakładać kolejne
   (`/admin/ → Konkursy → <konkurs> → feature_flags`, § 6.4):

   ```json
   {"competition_creation": true}
   ```

   Flaga jest przy konkursie, a nie globalna, celowo: uprawnienie do zakładania konkursów dostaje
   komitet, który już jedną olimpiadę prowadzi, a nie każdy koordynator w instalacji.

#### Co się dzieje przy zakładaniu konkursu

Koordynator wypełnia formularz w panelu; konkurs powstaje razem z witryną, drzewem stron i pierwszą
edycją (tak samo jak przy `create_competition`). Adres `<slug>.<domena>` odpowiada od razu, bo
rekord DNS `*` już istnieje. **Certyfikat powstaje przy pierwszym wejściu na ten adres**: Caddy
pyta aplikację pod `/internal/tls-allowed?domain=<host>`, dostaje 200 dla domeny aktywnego konkursu
i dopiero wtedy prosi Let's Encrypt. Pierwsze wejście trwa więc kilka sekund dłużej niż kolejne.

Adres `/internal/*` nie jest publiczny: odpowiada wyłącznie na wewnętrzną nazwę `web:8000` w sieci
compose, a konfiguracja proxy dodatkowo oddaje na niego 404 z każdej nazwy publicznej.

#### Sprawdzenie

```bash
curl -sI https://fizyczna.olimpiadakwantowa.pl/ | head -3      # 200/301 = działa, z certyfikatem
docker compose logs proxy | grep -i "certificate obtained"     # kiedy i dla jakiej nazwy
docker compose logs proxy | grep -i "on-demand\|permission"    # gdy certyfikatu nie ma
```

Odmowa w logu (`no OCSP…`, `permission denied`) znaczy, że aplikacja **nie** potwierdziła nazwy —
najczęściej dlatego, że konkurs jeszcze nie jest aktywny albo slug w adresie nie zgadza się z tym
w bazie. To jest odpowiedź poprawna, a nie awaria proxy.

#### Wyłączenie konkursu

`is_active=False` na wierszu konkursu (`/admin/ → Konkursy`). Od tej chwili aplikacja odpowiada na
tej nazwie 404, a endpoint zgody odmawia — więc **odnowienie** certyfikatu też nie nastąpi
i po wygaśnięciu nazwa przestaje mieć TLS. Certyfikat już wystawiony żyje do końca ważności; to
jest właściwość ACME, nie przeoczenie. Konkurs wraca do życia tą samą jedną wartością.

#### Granice tego trybu

- **Limit Let's Encrypt: 50 certyfikatów na domenę zarejestrowaną tygodniowo.** Liczy się cała
  `olimpiadakwantowa.pl`, razem z subdomenami. Przy kilku konkursach rocznie to limit niewidoczny;
  przy masowym zakładaniu i kasowaniu konkursów — jedyny, o który da się uderzyć.
- **Jeden poziom nazwy.** `fizyczna.olimpiadakwantowa.pl` tak, `i.fizyczna.olimpiadakwantowa.pl`
  nie: ani rekord `*`, ani blok `*.<domena>` nie schodzą głębiej.
- **Nazwy zarezerwowane** (`www`, `s3`, `mail`, `meet`, `monitor`, …) są odrzucane po stronie
  aplikacji — lista jest w kodzie, a nie w konfiguracji proxy, bo to aplikacja wie, co już zajęte.
  Rekordy jawne w DNS-ie i tak wygrywają z wieloznacznym, więc nawet slug przepuszczony przez
  pomyłkę nie przejmie `meet.` ani `mail.`.
- **Bloki dosłowne mają pierwszeństwo.** Caddy wybiera witrynę po najbardziej szczegółowym
  dopasowaniu nazwy, więc `meet.`, `monitor.`, `s3.` i domeny z `EXTRA_DOMAINS` działają dalej
  dokładnie tak, jak działały.

#### Wycofanie

`PLATFORM_SUBDOMAINS=0` (albo skasowanie linijki) w `/opt/olimpiada/.env` i wdrożenie — generator
wypuszcza wtedy konfigurację proxy **co do bajtu** taką, jaka jest dzisiaj, bez on-demand TLS
i bez bloku wieloznacznego (pilnuje tego `scripts/tests/render_caddyfile_test.sh`). Konkursy
z własnymi domenami i `EXTRA_DOMAINS` działają niezmiennie; te w subdomenach tracą adres do czasu,
aż przełącznik wróci. Rekord DNS `*` może zostać — sam z siebie niczego nie obsługuje.

---

## 7. Lista kontrolna incydentu

Otwórz, gdy przyszedł alarm albo telefon „nie działa”. Kolejność jest od najtańszego do
najdroższego i ma jeden cel: **nie zrobić niczego nieodwracalnego w pierwszych pięciu minutach.**

### Krok 1 – co widzi uczestnik (30 sekund)

```
https://olimpiadakwantowa.pl/status/
```

Ta strona mówi, który podsystem nie odpowiada, i jest tą samą stroną, którą trzeba podać
uczestnikom. Jeśli sama się nie otwiera – przejdź do kroku 2.

### Krok 2 – co widzi host (2 minuty)

```bash
ssh -i ~/.ssh/olimpiada_deploy root@olimpiadakwantowa.pl
cd /opt/olimpiada
docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
df -h /                     # wolne miejsce – najczęstsza przyczyna „nagle wszystko stanęło”
docker compose logs --tail 100 <usługa-która-nie-jest-healthy>
```

### Krok 3 – zatrzymaj utratę danych, zanim zaczniesz naprawiać

Jeśli awaria dotyczy **bazy albo magazynu plików**, a etap jest otwarty:

```bash
docker compose stop web        # uczestnicy widzą błąd zamiast po cichu gubić prace
```

Lepszy jest jawny brak dostępu niż serwis, który przyjmuje pracę i jej nie zapisuje. Zaraz potem
ogłoszenie w CMS-ie (`/cms/`, baner) – ten sam tekst zobaczą na `/status/`.

### Krok 4 – typowe przyczyny, w kolejności częstości

| Objaw | Najpierw sprawdź |
|-------|------------------|
| `queue` na czerwono | `docker compose logs worker beat --tail 100`; brak pulsu = nie żyje worker albo beat |
| `storage` na czerwono | `docker compose logs minio --tail 50`; `df -h` |
| `database` na czerwono | `df -h` (pełny dysk zatrzymuje zapisy), `docker compose logs db --tail 100` |
| wszystko na czerwono | `df -h`, `free -m`, `uptime` – host, nie aplikacja |
| dużo błędów 500 | `docker compose logs web --tail 200 \| grep -i traceback` |
| listy nie wychodzą | `docker compose logs mail --tail 100`; rekordy z `/opt/olimpiada/mail-dns.txt` |
| certyfikat wygasł | `docker compose logs proxy --tail 100`; rekord DNS domeny |

### Krok 5 – restart, od najwęższego

```bash
docker compose restart <jedna-usługa>
docker compose up -d --force-recreate <jedna-usługa>
docker compose down && docker compose up -d       # ostateczność; wolumeny zostają
```

`docker compose down -v` **kasuje wolumeny razem z bazą i pracami uczestników.** Tego polecenia
nie ma w tym runbooku i nie ma go po co znać.

### Krok 6 – po incydencie

1. sprawdź kopię: `docker compose exec web python manage.py record_backup_status --show`,
2. sprawdź, co zostało w audycie (`Panel → Audyt`),
3. jeśli uczestnicy stracili czas – przesunięcie terminu etapu robi koordynator w panelu,
   a nie nikt inny i nie w bazie,
4. zapisz w `docs/BACKLOG.md`, czego zabrakło, żeby wykryć to szybciej.

### Kiedy odtwarzać z kopii

Dopiero gdy dane są **nieodwracalnie** uszkodzone albo skasowane – nie po awarii, którą naprawia
restart. Odtworzenie do nowej bazy (§ 2) nic nie psuje i można je zrobić równolegle do diagnozy;
przełączenie serwisu na odtworzone dane (§ 2.3) jest osobną decyzją i kosztuje wszystko, co
zapisano od momentu wykonania kopii.

---

## 8. Lista kontrolna po wdrożeniu etapu 2 (v0.24.0)

Etap 2 (`docs/UNIWERSALNY-ETAP-2.md`) dokłada siedem obszarów konfiguracji i **żaden z nich nie ma
zmienić niczego w Olimpiadzie Kwantowej**. Ta sekcja zamienia listę z § 0.5 tamtego dokumentu na
komendy, które operator wykonuje po każdym wydaniu z serii E–K (`v0.24.0`–`v0.30.0`), w kolejności
wykonywania. Punkty 1–13 z etapu 1 (§ 0.3 `UNIWERSALNY-ETAP-1.md`) **obowiązują bez zmian** i idą
przed tymi.

**Co jest już sprawdzone testem, a czego tu nie ma.** Część listy § 0.5 pilnuje suita i nie ma
powodu powtarzać jej ręcznie: katalog przełączników i menu koordynatora bez flag
(`apps/tenancy/tests/test_golden_single_competition.py`, `apps/web/tests/test_coordinator_nav_flags.py`),
kontrakt `/status.json` razem z kolejnością kluczy, jeden `Locale` i brak prefiksu języka
(`apps/tenancy/tests/test_i18n.py`), uprawnienia grupy `coordinator` w `/cms/`
(`apps/cms/tests/test_cms_scope.py`), 404 na `/setup/` przy skonfigurowanej instalacji
(`apps/tenancy/tests/test_setup.py`) oraz przebieg dwóch konkursów obok siebie
(`apps/web/tests/test_e2e_two_competitions.py`, `e2e/check_stage2_*.py`). **Tutaj stoi to, czego
test sprawdzić nie może**: porównanie z produkcją sprzed wdrożenia i stan konkretnej bazy.

### 8.1. Przed wdrożeniem: materiał porównawczy

Bez tego kroku punkty 16–19 są niesprawdzalne — nie ma z czym porównywać. Wykonuje się go **przed**
`scripts/deploy.sh`, z katalogu roboczego poza repozytorium (brudnopis operatora), bo pliki niosą
dane osobowe i nie wolno ich commitować.

```bash
# na serwerze, w /opt/olimpiada
TAG=v0.24.0
mkdir -p /opt/olimpiada-backups/przed-${TAG} && cd /opt/olimpiada-backups/przed-${TAG}

# 1. strony złote: dokładnie te adresy, które wymienia § 0.5 (punkty 14, 16, 19, 21)
for path in / /register/ /wyniki/ /dokumenty/regulamin/ /dokumenty/rodo/ /status.json; do
  curl -sS -D nagl$(echo "$path" | tr '/' '_').txt \
       -o tresc$(echo "$path" | tr '/' '_').html "https://olimpiadakwantowa.pl$path"
done

# 2. każda ogłoszona tabela wyników – po jednym pliku na publikację (punkt 16)
docker compose exec -T web python manage.py shell -c "
from apps.results.models import ResultsPublication
print('\n'.join(str(row.stage_id) for row in ResultsPublication.objects.all()))
" | while read -r stage; do
  curl -sS -o wyniki-${stage}.html "https://olimpiadakwantowa.pl/results/${stage}/"
done

# 3. snapshoty publikacji jako dane (wydania I i J – § 3 planu etapu 2)
docker compose exec -T web python manage.py dumpdata results.ResultsPublication \
    --indent 2 > publikacje-przed-${TAG}.json
```

### 8.2. Po wdrożeniu: komendy

Kolejność jest istotna: najpierw pytamy, czy baza jest w stanie, w jakim miała być (migracje,
domeny, członkostwa), a dopiero potem porównujemy to, co widzi uczestnik. Odwrotna kolejność każe
diagnozować różnicę w HTML-u, której przyczyną jest niewykonana migracja.

```bash
# na serwerze, w /opt/olimpiada

# 1. żadnej migracji do wykonania – pusty wynik znaczy „wszystkie zastosowane”
docker compose exec -T web python manage.py showmigrations | grep '\[ \]'

# 2. modele zgodne z migracjami – wdrożenie z niezacommitowaną zmianą modelu kończy się tutaj
docker compose exec -T web python manage.py makemigrations --check --dry-run

# 3. role kontra członkostwa (§ 6.1) – ma wyjść zero rozjazdów
docker compose exec -T web python manage.py check_memberships

# 4. domena w trzech miejscach naraz: Caddy, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS
docker compose exec -T web python manage.py check_domains --all

# 5. wersja odpowiadającej aplikacji – ma być tagiem, który właśnie wjechał
curl -sS https://olimpiadakwantowa.pl/status.json | python3 -m json.tool | grep -E '"(version|setup_pending|status)"'
```

Punkt 22 listy § 0.5 sprawdza się jedną linijką i ma dać **404**: kreator pierwszego uruchomienia
nie istnieje na instalacji, która ma konkurs i superużytkownika.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://olimpiadakwantowa.pl/setup/     # oczekiwane: 404
```

### 8.3. Zapytania kontrolne do bazy

Wchodzi się przez `docker compose exec -T db psql -U olimpiada -d olimpiada -c "…"`. Każde
zapytanie ma **jedną** oczekiwaną odpowiedź i jest wypisana obok — wynik inny niż ta odpowiedź
zatrzymuje wdrożenie, a nie zaczyna dyskusję.

```sql
-- 1. przełączniki każdego konkursu. Olimpiada Kwantowa (slug „kwantowa”) ma mieć {} albo
--    wyłącznie flagi etapu 1 (memberships_enforced, competition_settings_page). Każda flaga
--    etapu 2 w tym wierszu to funkcja zapalona u działającej olimpiady bez decyzji organizatora.
select slug, routing_mode, path_prefix, feature_flags from tenancy_competition order by id;

-- 2. definicje zgód Konkursu #1: dokładnie 4 (regulamin, RODO, opiekun, publikacja nazwiska).
--    Wiersz nadmiarowy albo brakujący znaczy, że migracja przepisująca zgody zrobiła co innego
--    niż kopię stałej – a to jest jedyne miejsce, w którym treść oświadczenia mogłaby się zmienić.
select count(*) from accounts_consentdefinition
 where competition_id = (select id from tenancy_competition order by id limit 1);

-- 3. szablony dokumentów Konkursu #1: dokładnie 5 (laureat, finalista, uczestnik, opiekun,
--    warsztaty), wszystkie w tej samej wersji.
select version, count(*) from tenancy_documenttemplate
 where competition_id = (select id from tenancy_competition order by id limit 1)
 group by version;

-- 4. regiony Konkursu #1: 18 (kraj + 16 województw + „poza Polską”). Liczba inna znaczy, że
--    migracja z Voivodeship policzyła województwa drugi raz albo pominęła korzeń.
select count(*) from accounts_region
 where competition_id = (select id from tenancy_competition order by id limit 1);

-- 5. uczestnik należy do jednego konkursu i ma kod z jego prefiksu – zero wierszy w odpowiedzi.
select p.id, p.public_code from accounts_participant p
  join tenancy_competition c on c.id = p.competition_id
 where p.public_code not like c.public_code_prefix || '%';

-- 6. jeden język treści (punkt 20–21 listy) – ma być dokładnie jeden wiersz.
select count(*) from wagtailcore_locale;
```

### 8.4. Porównanie stron złotych

To jest punkt 16 listy § 0.5 wykonany narzędziem zamiast okiem. Wykonuje się go **po** § 8.2,
z tego samego katalogu brudnopisu, w którym leżą pliki z § 8.1.

```bash
TAG=v0.24.0
cd /opt/olimpiada-backups/przed-${TAG}
mkdir -p ../po-${TAG} && cd ../po-${TAG}

for path in / /register/ /wyniki/ /dokumenty/regulamin/ /dokumenty/rodo/; do
  curl -sS -o tresc$(echo "$path" | tr '/' '_').html "https://olimpiadakwantowa.pl$path"
done
diff -r ../przed-${TAG} . --exclude='nagl*' --exclude='*.json'
```

Czego `diff` **ma** nie pokazać: ani jednej różnicy w `/register/` (brzmienie czterech zgód
i odnośniki do `/dokumenty/regulamin/` oraz `/dokumenty/rodo/` — punkt 14), ani jednej w tabelach
wyników (punkt 16). Czego pokazanie jest w porządku: znacznika czasu w `/status.json` i numeru
wersji zasobu statycznego (`?v=…`), jeżeli wydanie przebudowało arkusz stylów.

Punkty, których nie da się porównać plikiem i które zostają ręczne: pobranie dyplomu wystawionego
**przed** wdrożeniem (ten sam numer, kod weryfikacyjny i linia podpisu — punkt 18), `/kalendarz.ics`
uczestnika (`PRODID`, `X-WR-CALNAME`, `UID` — punkt 19) oraz wejście do `/cms/` na koncie
koordynatora (drzewo stron, kolekcje mediów, brak nowego wyboru języka — punkt 20). Każdy z nich
wymaga zalogowanego konta, więc robi je człowiek, a nie `curl`.

### 8.5. Kolejność zapalania flag u drugiego konkursu

Flagi zapala się **pojedynczo i w tej kolejności**, wpisując je do `feature_flags` w
`/admin/ → Konkursy → <konkurs>` (§ 6.4). Kolejność nie jest dowolna: każda grupa zakłada, że
poprzednia już stoi, a zapalenie wszystkiego naraz zamienia pierwszą usterkę w piętnaście
podejrzanych.

| # | Flagi | Co sprawdzić, zanim zapalisz następną |
|---|---|---|
| 1 | `per_competition_consents`, `document_templates` | `/coordinator/consents/` i `/coordinator/documents/` otwierają się; `/register/` konkursu pokazuje **te same** zgody, co przed zapaleniem (definicje są kopią zestawu domyślnego) |
| 2 | `competition_branding_in_mail` | list aktywacyjny z rejestracji testowej ma temat i podpis tego konkursu, a nie platformy |
| 3 | `scoped_cms_permissions` | `manage.py scope_cms_access --competition <slug> --dry-run`, a po przeczytaniu wydruku bez `--dry-run`; koordynator widzi w `/cms/` wyłącznie swoje poddrzewo, a koordynator Konkursu #1 — swoje bez zmian |
| 4 | `custom_regions` | `/coordinator/regions/` pokazuje 18 wierszy startowych; lista województw w rejestracji nie zmienia się, dopóki regiony nie zostaną poprawione |
| 5 | `institution_types`, `custom_school_directory` | `/coordinator/registration-profile/` i `/coordinator/institutions/`; **podgląd** wgrania wykazu (bez potwierdzenia) przed pierwszym prawdziwym importem |
| 6 | `process_editor`, `categories` | `/coordinator/pipeline/` — konkurs założony komendą ma **pusty tor**: kroki dopisuje się przyciskiem „Dopisz krok”, po jednym na etap, zanim ktokolwiek policzy kwalifikację |
| 7 | `weighted_scoring`, `reviewer_roles`, `team_entries` | wagi i remisy na ekranie etapu; przeliczenie etapu próbnego daje tę samą tabelę, co przed zapaleniem, dopóki wagi są `1/1` |
| 8 | `fees`, `onsite_logistics` | `/coordinator/fees/` i `/coordinator/venues/`; rejestr należności **pusty**, dopóki cennik nie zostanie naliczony |
| 9 | `content_translations` | dopiero po ustawieniu `WAGTAIL_I18N_ENABLED` i drugiego `Locale` — flaga bez nich nie ma czego włączyć |
| 10 | `participant_forum` | **dopiero po uzgodnieniu dyżuru moderacyjnego** (§ 6.4): `/coordinator/forum/categories/` — założyć pierwszy dział, `/coordinator/forum/settings/` — potwierdzić tryb „przed publikacją”, a potem napisać wpis z konta uczestnika i sprawdzić, że odznaka przy „Forum uczestników” w menu panelu urosła o jeden |

Po każdej grupie: zaloguj się na konto jednej osoby z każdej roli **tego** konkursu i sprawdź, że
widzi to, co widziała. Cofnięcie jest tą samą jedną wartością w `feature_flags` i nie wymaga
wdrożenia — ale tylko wtedy, gdy ktoś zauważy w tej samej minucie.

Konkursu #1 ta tabela **nie dotyczy**: Olimpiada Kwantowa zostaje z `feature_flags` zawierającym
wyłącznie flagi etapu 1 i tak ma zostać przez cały sezon (decyzja D8, `docs/UNIWERSALNY-ETAP-2.md`
§ 6).

---

## 9. Aktualizacja frameworka (v0.26.0)

### 9.1. Co się zmieniło

Wydanie v0.26.0 nie dokłada ani jednej funkcji — podnosi stos, na którym stoi wszystko pozostałe:

| Pakiet | Było (v0.25.0) | Jest (v0.26.0) | Dlaczego |
|---|---|---|---|
| `django` | 5.1.15 | 6.1.1 | prośba organizatora; 5.1 wychodzi z obsługi bezpieczeństwa |
| `wagtail` | 6.3.8 | 8.0 | pierwsza linia zgodna z Django 6.1 (6.3 wymaga Django < 5.2) |
| `djangorestframework` | 3.15 | 3.18.1 | 3.15 nie deklaruje Django 6 |
| `django-redis` | 5.4 | 7.0.0 | jw. (`Django>=5.2` w metadanych) |
| `drf-spectacular` | 0.28 | 0.30.0 | jw. |
| `django-simple-captcha` | 0.6 | 0.7.0 | jw. |
| `django-environ` | 0.12 | 0.14.0 | jw. |
| `celery` | 5.5 | 5.6.3 | zgodność z `django-celery-beat` 2.9 |
| `django-celery-beat` | 2.8.1 | 2.9.0 | najnowsze wydanie — **z obejściem**, patrz § 9.2 |
| `django-allauth` | 65.x | 65.19.4 | jw. |

Zmian w kodzie aplikacji ta aktualizacja wymagała **jednej**: do `INSTALLED_APPS` doszło
`django.contrib.postgres`. Aplikacja nie wnosi tabel ani migracji — rejestruje `SearchVectorField`
i `GinIndex`, z których zbudowany jest model `wagtailsearch.IndexEntry` bazodanowej wyszukiwarki
Wagtaila. Django od 6.0 sprawdza to jawnie (`postgres.E005`), więc bez tego wpisu `manage.py check`
kończy się sześcioma błędami. Żadna migracja **naszych** aplikacji nie powstała
(`makemigrations --check --dry-run` jest czysty), żaden test nie został złagodzony.

Wymagania środowiska, które trzeba znać przed wdrożeniem: Django 6.1 wymaga **Pythona ≥ 3.12**
(obraz ma 3.12.14) i **PostgreSQL-a ≥ 15** (compose stawia `postgres:16-alpine`, produkcja ma 16).
Obie granice są spełnione — ale gdyby ktoś kiedyś cofnął bazę do 14, aplikacja nie wstanie.

### 9.2. Obejście `django-celery-beat` (i kiedy je usunąć)

`django-celery-beat` 2.9.0 — najnowsze wydanie na 18.09.2026 — deklaruje w metadanych
`Django<6.1,>=2.2`. Bez obejścia `uv pip install -r pyproject.toml` w `backend/Dockerfile` kończy
się `ResolutionImpossible`, czyli **obraz produkcyjny w ogóle się nie buduje**.

Obejście jest jedną sekcją w `backend/pyproject.toml`:

```toml
[tool.uv]
override-dependencies = ["django>=6.1,<6.2"]
```

Nadpisywana jest deklaracja, która blokuje (wymaganie na `django`), a nie pakiet, który ją napisał.
Zakres override'a jest identyczny z pinem w `dependencies`, więc niczego nie poszerza. Sprawdzone:
uv **0.4.30** (ta wersja jest przypięta w `backend/Dockerfile`) czyta `[tool.uv]` z `pyproject.toml`
także dla `uv pip install -r pyproject.toml` — z sekcją resolver kończy pracę („Resolved 126
packages”), bez niej wypisuje wprost konflikt z `django-celery-beat`. Dockerfile nie wymagał zmiany.

Że deklaracja jest przesadną ostrożnością, a nie faktem, potwierdzone czterema dowodami na
Django 6.1.1:

1. wszystkie 21 migracji `django_celery_beat` aplikuje się na pustej bazie bez błędu,
2. `celery -A config beat --scheduler django_celery_beat.schedulers:DatabaseScheduler` startuje
   i chodzi (zatrzymany dopiero limitem czasu), bez jednego tracebacku,
3. do **pustej** bazy scheduler wpisał komplet zadań okresowych (8 pozycji z
   `CELERY_BEAT_SCHEDULE` + `celery.backend_cleanup`) i zaczął je wysyłać,
4. strony zadań okresowych w `/admin/` renderują się (200).

**Warunek usunięcia obejścia — jeden i sprawdzalny:** pierwsze wydanie `django-celery-beat`, które
w metadanych dopuszcza Django 6.1 (`Django<6.2` albo bez górnej granicy). Sprawdzenie zajmuje
chwilę:

```bash
docker compose exec -T web pip index versions django-celery-beat
docker compose exec -T web python -c "import importlib.metadata as m; print([r for r in m.requires('django-celery-beat') if r.lower().startswith('django')])"
```

Gdy warunek jest spełniony, znika **sekcja `[tool.uv]` i ostrzegawczy komentarz przy pinie
`django-celery-beat` w `dependencies`** — obie rzeczy naraz, bo opisują to samo.

### 9.3. Wycofanie (rollback)

**Migracji Wagtaila nie da się w praktyce cofnąć.** Aktualizacja aplikuje osiem migracji, z czego
siedem to Wagtail i `wagtailsearch` (`wagtailcore.0095`–`0098`, `wagtailadmin.0006`,
`wagtailsearch.0010`, `wagtailusers.0015`). Wagtail nie utrzymuje odwracalności migracji między
liniami głównymi, a `migrate wagtailcore <stara>` na produkcji jest operacją bez pokrycia testowego.
Dlatego **jedyną** przewidzianą drogą powrotu jest odtworzenie bazy z kopii, a nie migracja wstecz.

Procedura, w tej kolejności:

1. **Zatrzymaj aplikację, zostaw bazę.** `docker compose stop web worker beat` — proxy i `db`
   zostają, żeby nikt nie zapisał niczego w połowie odtwarzania.
2. **Weź kopię sprzed migracji.** Robi ją automatycznie krok `4a/8` w `scripts/deploy.sh`:
   `/opt/olimpiada-backups/pre-deploy-<data>-<wersja>.dump` (format `pg_dump -Fc`). Interesuje Cię
   ta, której znacznik niesie **poprzednie** wydanie: `ls -1t /opt/olimpiada-backups/pre-deploy-*.dump`.
3. **Odtwórz bazę** według § 2 („Odtwarzanie”) — ta sama procedura, ten sam plik, żadnego nowego
   narzędzia.
4. **Cofnij obraz.** W `.env` ustaw `APP_VERSION` na poprzednie wydanie (albo `WEB_IMAGE` na jego
   tag w GHCR) i `docker compose up -d web worker beat`. Obraz poprzedniej wersji nadal ma
   Django 5.1 i Wagtail 6.3, więc do **odtworzonej** bazy pasuje dokładnie.
5. **Sprawdź** `/status.json`, `/`, `/cms/` i `/admin/` oraz `docker compose ps` (trzy usługi
   `healthy`).

Czego **nie** robić: uruchamiać starego obrazu na nowej bazie. Baza po migracjach Wagtaila 8 ma
kolumny i tabele, o których Wagtail 6.3 nie wie — część panelu jeszcze się otworzy i to jest
najgorszy możliwy wariant, bo awaria wyjdzie dopiero przy zapisie strony.

### 9.4. Co sprawdzić po wdrożeniu

Poza listą z § 8.2 (ta obowiązuje nadal) — cztery rzeczy, które dotyczą wyłącznie tej aktualizacji:

| # | Sprawdzenie | Oczekiwane |
|---|---|---|
| 1 | `docker compose exec -T web python manage.py check` | „System check identified no issues” (gdyby wróciło `postgres.E005`, z `INSTALLED_APPS` wypadło `django.contrib.postgres`) |
| 2 | `curl -sI https://<domena>/ \| grep -ci '^content-security-policy'` | **1** — nagłówek jest nadal nasz (`apps/web/middleware.py`, z nonce'em). Django 6.0 ma **własny** `ContentSecurityPolicyMiddleware`; nie włączamy go i `SECURE_CSP` zostaje nieustawione, bo dwa nagłówki znaczą przecięcie polityk, a nie sumę |
| 3 | `docker compose logs beat --since 5m` | wpisy `Scheduler: Sending due task …`, zero tracebacków |
| 4 | `/cms/` → obraz w dowolnym artykule | rendition renderuje się. Wagtail 8 **przestał** automatycznie konwertować AVIF i WebP do PNG; gdyby redakcja miała takie źródła, wraca się do starego zachowania przez `WAGTAILIMAGES_FORMAT_CONVERSIONS` (dziś w repozytorium nieustawione, bo biblioteka jest w JPEG/PNG) |

### 9.5. Poczta: `EMAIL_*` → `MAILERS` (dług spłacony)

Django 6.1 oznaczyło wszystkie ustawienia `EMAIL_*` jako przestarzałe (znikają w 7.0) na rzecz
słownika `MAILERS` — i dawało z tego powodu **dziewięć** ostrzeżeń `RemovedInDjango70Warning` przy
każdym uruchomieniu. Konfiguracja jest już przeniesiona i tych ostrzeżeń nie ma.

**Dla operatora nie zmienia się nic.** Wejściem nadal są te same zmienne w `/opt/olimpiada/.env`:
`EMAIL_URL` (adres relaya razem z poświadczeniami i szyfrowaniem) oraz `EMAIL_TIMEOUT`.
`config/settings/base.py` składa z nich `MAILERS = {"default": {"BACKEND": …, "OPTIONS": {…}}}` —
te same wartości, które dotąd stały w `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`/`EMAIL_USE_SSL` i `EMAIL_TIMEOUT`, tylko w jednym słowniku
zamiast w siedmiu ustawieniach. `.env` nie wymaga **żadnej** edycji, a ostrzeżenie startowe
„`EMAIL_URL` wskazuje localhost:25” mówi dalej to samo i o tej samej zmiennej.

Czego pilnują testy, żeby to została prawda: `backend/apps/core/tests/test_mailers_config.py`.
Sprawdza cztery rzeczy naraz — że każda droga wysyłki kończy się w `mail.outbox` (w policzalnej
liczbie sztuk), że pod ustawieniami produkcyjnymi z podanym `EMAIL_URL` powstaje dokładnie ten
nadajnik SMTP, którego się spodziewamy, że żaden moduł ustawień nie definiuje już nazwy `EMAIL_*`
(moduł z jedną i drugą naraz Django odrzuca wyjątkiem, więc objawem byłby nieuruchamiający się
kontener) i że wysyłka nie wywołuje ostrzeżeń o wycofaniu.

**Drugi nadajnik — organizator z własną domeną.** Gdyby konkurs dołożony do platformy miał wysyłać
listy własnym relayem (bo SPF/DKIM jego domeny nie obejmują naszego), dopisuje się do `MAILERS`
drugi alias obok `"default"` — np. `"fizyczna"` z własnym hostem, użytkownikiem i hasłem z osobnej
zmiennej środowiskowej — a w miejscu wysyłki wskazuje się go argumentem `using="fizyczna"`
(`EmailMessage.send(using=…)`, `send_mail(…, using=…)`). Kosztem nie jest sam słownik, tylko
**wybór**: dzisiaj żadna droga wysyłki nie pyta o konkurs w tej sprawie, więc alias musiałby
dojść tam, gdzie dziś dochodzi nadawca — do `apps/core/tasks.py::mail_from` i do zadania
`send_mail_task`, jako kolejny prosty argument obok adresu nadawcy. To jest osobna decyzja
organizatora, razem z `Reply-To` i prefiksem tematu (`docs/UNIWERSALNY-ETAP-2.md` § 0.1), a nie
skutek uboczny tej migracji.

**Jedna pułapka na zapas**, gdyby ktoś przełączył `EMAIL_URL` na dostawcę zewnętrznego
z uwierzytelnieniem (README § 4.1, wariant B): powiadomienia **Wagtaila** (obieg redakcyjny w
`/cms/`) wysyłają listy przez przestarzałe `get_connection(username=None, password=None, …)`,
a Django przy włączonym `MAILERS` traktuje takie `None` jak jawne „bez poświadczeń” i nadpisuje
nimi to, co stoi w `OPTIONS`. Naszej poczty to nie dotyczy (przez `MAILERS` idzie wszystko,
co wysyła aplikacja), a dzisiejszego relaya w compose też nie, bo `smtp://mail:587` żadnego
logowania nie wymaga. Przy dostawcy z hasłem powiadomienie redakcyjne dostałoby jednak odmowę
`530` — objaw głośny, nie cichy. Znika to razem z Django 7.0, które `get_connection()` skasuje.

### 9.6. reportlab 5.x

Do v0.26.0 `backend/pyproject.toml` trzymał `reportlab>=4,<5`, więc produkcja składała wszystkie
dokumenty biblioteką **4.5.1**, podczas gdy obraz deweloperski niósł już **5.0.1** — rozjazd
starszy niż sama aktualizacja frameworka i niewygodny z jednego powodu: testy PDF-ów sprawdzały
bibliotekę, której produkcja nie uruchamiała. Od v0.26.1 pin brzmi `reportlab>=5.0,<6` i obie
strony mają 5.0.1 (najnowsze wydanie na dzień 18.09.2026; linia 5.x ma dotąd tylko 5.0.0 i 5.0.1).
Nic w drzewie zależności reportlaba nie ogranicza — w szczególności **nie** robi tego `pyhanko`,
który składu PDF-ów w ogóle nie dotyka: w rozwiązaniu `uv pip compile --extra dev` wiersz
`reportlab==5.0.1` ma jedno źródło, `olimpiada (pyproject.toml)`.

Przejście jest małe i to jest sedno decyzji. Wobec 4.5.1 wydanie 5.0 zmienia w kodzie biblioteki
jedenaście plików i niesie dokładnie jedną zmianę zachowania, która mogłaby nas dotyczyć:
`rl_config.trustedHosts = None` znaczy teraz „żaden host nie jest zaufany”, a nie „wszystkie”
(dotyczy `open_for_read`, czyli wczytywania zasobu **adresem**). Nas nie dotyczy, bo obrazy
podajemy zawsze bajtami — `ImageReader(BytesIO(...))` w `apps/results/certificates.py` — a kroje
ścieżką w repozytorium; ani jedno wywołanie nie wychodzi do sieci. Poza tym znikły dwa zaplecza
opcjonalne (`rl_renderPM`, `pyRXP`), a `Canvas.getpdfdata()` koduje latin1 zamiast utf-8 — żadnej
z tych trzech rzeczy nie używamy (`grep` po `apps/` i `config/` nie daje trafienia).

Sprawdzone porównaniem dokumentów, a nie deklaracją. Dwanaście dokumentów — pięć rodzajów dyplomu
w układzie wbudowanym, dyplom na szablonie graficznym (tło, logo, trzy podpisy, kod QR), wzór zgody
opiekuna, protokół etapu, trzy listy logistyczne i rachunek za wpisowe — złożono **obiema** wersjami
z tych samych danych (zamrożony zegar, `rl_config.invariant = 1`, baza testowa zakładana od zera,
ustalone ziarno fabryk i generatora kodów publicznych). Wszystkie dwanaście par wyszło **bajt
w bajt** identycznie: ta sama paginacja, ten sam format strony, ten sam tekst razem z polskimi
znakami, te same kroje (`DejaVuSans`, `DejaVuSans-Bold`), tyle samo obrazów i zerowe przesunięcie
przebiegów tekstu (porównanie pypdf, macierz `tm` przebieg po przebiegu). Że porównanie mierzy
wersję biblioteki, a nie szum przebiegu, potwierdza próba kontrolna: dwa uruchomienia na 5.0.1
dają pliki identyczne.

Osobno warty odnotowania skutek dotyczy `backend/apps/cms/fixtures/documents/zgoda-opiekuna.pdf` —
pliku składanego komendą `build_guardian_consent_pdf`, trzymanego w repozytorium i porównywanego
w testach **bajt w bajt** (`test_guardian_consent_pdf_is_rebuilt_byte_for_byte`). Z 5.0.1 wychodzi
dokładnie ten plik, który w repozytorium leży (to samo sha256), więc regeneracji nie było potrzeby
i jej nie zrobiono.

**Wycofanie.** Jedną linią i przebudową obrazu: w `backend/pyproject.toml` wróć do
`reportlab>=4,<5`, zbuduj obraz od nowa (lokalnie `docker compose build web`, na produkcji zwykłą
drogą z § 4.2) i odtwórz `web`, `worker` oraz `beat`. Migracji ani danych to nie dotyka: reportlab
niczego nie zapisuje w bazie, a dokumenty powstają od nowa przy każdym pobraniu
(`apps/results/certificates.py`), więc wycofanie jest natychmiastowe i bezstratne.

## 10. CI: podział testów na shardy (v0.27.3)

Zadanie `pytest` w `.github/workflows/ci.yml` idzie w pięciu równoległych shardach
(`pytest-split`), a wymaganym statusem jest jeden: „pytest (wynik zbiorczy)”. Podział jest po
**zmierzonym czasie** – czasy testów leżą w `backend/.test_durations` i to według nich
`--splitting-algorithm least_duration` układa shardy.

Plik nie musi być aktualny co do testu (nowy test dostaje czas średni). Odświeża się go, gdy shardy
znów wyraźnie się rozjadą – np. najdłuższy trwa dwa razy dłużej niż najkrótszy:

```bash
docker compose exec -T web python -m pytest -q --create-db -p ci_durations_plugin -p no:cacheprovider
```

Polecenie uruchamia cały zbiór w lokalnym środowisku (ok. pół godziny) i nadpisuje
`backend/.test_durations`; wtyczka `backend/ci_durations_plugin.py` sumuje czas przygotowania,
wykonania i sprzątania każdego testu. Plik commituje się jak każdy inny. Czasy z maszyny lokalnej
różnią się od czasów w CI co do wartości, ale nie co do proporcji – a podział zależy tylko od nich.

## 11. Wydajność: WSGI, wątki, połączenia (v0.31.0)

Test obciążeniowy z 22.09.2026 zmierzył linię bazową: **~7 req/s** w nasyceniu, p95 **2,3 s**
przy 5 użytkownikach jednocześnie. Przyczyna: usługa `web` chodziła pod ASGI (gunicorn +
`UvicornWorker`), a aplikacja jest w 100% synchroniczna – Django wykonuje wtedy każdy widok
w **jednym wątku puli na proces** (`ThreadSensitiveContext`), więc `--workers 3` dawało dokładnie
trzy równoległe żądania, niezależnie od tego, ile CPU i RAM-u stało bezczynnie obok.

### 11.1. Model współbieżności

`web` (`docker-compose.yml`, `backend/Dockerfile`) chodzi teraz pod **WSGI + worker `gthread`**:

```
gunicorn config.wsgi:application --workers ${WEB_WORKERS:-4} --threads ${WEB_THREADS:-4} \
  --worker-class gthread --timeout 120 --graceful-timeout 30 \
  --max-requests 2000 --max-requests-jitter 200
```

Concurrency procesu to iloczyn `WEB_WORKERS × WEB_THREADS`, nie sama liczba workerów – przy
domyślnych 4×4 to **16** równoległych żądań na instancję `web`. `--max-requests` z rozrzutem
(`--max-requests-jitter`) restartuje worker po ok. 2000±200 żądaniach: łata powolny wyciek
pamięci w pojedynczym procesie bez wspólnego, widocznego restartu wszystkich naraz.

Reguła doboru `WEB_WORKERS`/`WEB_THREADS`: **workery** skalują z liczbą rdzeni (serwer produkcyjny
ma 6 vCPU – 4 workery zostawiają margines pozostałym usługom: `worker`, `beat`, `db`, `redis`,
`minio`, `clamav`), **wątki** skalują z udziałem czasu żądania spędzanym na I/O (baza, S3, SMTP) –
podnoszenie ich ponad ok. 8 przestaje pomagać, bo GIL i tak serializuje część pracy w Pythonie.

### 11.2. Budżet połączeń z Postgresem

`max_connections=100`. Przy domyślnych wartościach:

| Usługa | Wzór | Połączenia |
|---|---|---|
| `web` | `WEB_WORKERS × WEB_THREADS` = 4×4 | 16 |
| `worker` | `CELERY_CONCURRENCY` | 2 |
| `beat` | proces jednowątkowy | 1 |
| **razem** | | **ok. 19–20** |

Zapas do 100 jest świadomie duży: administracyjne połączenia (`manage.py shell`, `psql` ręcznie
w trakcie incydentu) i chwila nakładania się dwóch wdrożeń (stary kontener kończy żądania, nowy już
przyjmuje) nie mogą wypchnąć aplikacji z puli. Podnoszenie `WEB_WORKERS`/`WEB_THREADS` powyżej ok.
6×8 zbliża budżet do granicy i wymaga podniesienia `max_connections` w Postgresie razem z tym.

`DB_CONN_MAX_AGE=60` (`config/settings/base.py`) i `CONN_HEALTH_CHECKS=True` są bezpieczne właśnie
dzięki `gthread`: wątek roboczy **żyje w puli workera** (nie ginie po żądaniu, jak wątek pod ASGI),
więc trwałe połączenie ma kto zamknąć przy wygaśnięciu. Incydent, który kiedyś to wyłączył (92
bezczynne połączenia z `web` po dobie, „too many clients already”), miał inną przyczynę – wątek
ASGI ginął, a jego połączenie zostawało otwarte aż do wygaśnięcia po stronie Pythona. Pełna historia
stoi w komentarzu przy `DATABASES["default"]["CONN_MAX_AGE"]`.

### 11.3. Rollback

`WEB_WORKERS` i `WEB_THREADS` to zmienne `.env` – awaryjny powrót do mniejszej współbieżności (np.
podejrzenie, że nowa wartość przeciąża bazę albo maszynę) nie wymaga wdrożenia:

```bash
# na serwerze, w /opt/olimpiada
sed -i 's/^WEB_WORKERS=.*/WEB_WORKERS=2/; s/^WEB_THREADS=.*/WEB_THREADS=2/' .env
docker compose up -d web
```

Powrót do poprzedniej **wersji** obrazu (nie tylko konfiguracji) korzysta z tagu, który
`scripts/deploy.sh` (ostatni krok, „Porządki: stare obrazy”) świadomie zostawia obok bieżącego –
każde wdrożenie kasuje tagi `olimpiada/web` starsze niż bieżący i poprzedni, więc jeden krok wstecz
nie wymaga ponownego budowania:

```bash
# na serwerze, w /opt/olimpiada – docker images pokazuje zostawione tagi
docker compose stop web worker beat
sed -i 's/^APP_VERSION=.*/APP_VERSION=<poprzednia-wersja>/' .env
docker compose up -d web worker beat
```

Dwa kroki wcześniej (obraz już skasowany) wymaga ponownego budowania z odpowiedniego commitu –
`git checkout <tag>` na kopii repozytorium i `scripts/deploy.sh` bez `WEB_IMAGE`.

## 12. Wyszukiwarka szkół: rozszerzenie `pg_trgm` (v0.31.0)

Migracja `schools.0006_pg_trgm_search_indexes` wymaga rozszerzenia PostgreSQL **`pg_trgm`**
(indeksy GIN pod `search_text`/`city_search`, klasa operatorów `gin_trgm_ops` – zastąpiły trzy
indeksy B-tree bez ani jednego skanu na produkcji, patrz `docs/CHANGELOG.md` v0.31.0). Rozszerzenie
zakłada sama migracja (`django.contrib.postgres.operations.TrigramExtension`,
`CREATE EXTENSION IF NOT EXISTS pg_trgm`) — nic nie trzeba robić ręcznie przed wdrożeniem, o ile
spełniony jest jeden warunek środowiska:

- **obraz bazy ma zawierać `pg_trgm`.** Obraz `postgres:16-alpine`, którego używa
  `docker-compose.yml` i produkcja (§ 9.1: PostgreSQL ≥ 15, produkcja ma 16), zawiera go w pakiecie
  `contrib` domyślnie — nie trzeba doinstalowywać żadnego pakietu systemowego,
- **rola aplikacyjna nie potrzebuje uprawnień superużytkownika.** `pg_trgm` jest rozszerzeniem
  *zaufanym* (*trusted*) od PostgreSQL 13 — właściciel bazy (rola, na której działa aplikacja) może
  je założyć sam, tak jak każdą inną migrację. Gdyby instalacja kiedyś trafiła na PostgreSQL < 13
  albo na zarządzaną usługę, która nie oznacza `pg_trgm` jako zaufane, migracja przerwie się na
  `CREATE EXTENSION` z błędem uprawnień — rozwiązaniem jest jednorazowe `CREATE EXTENSION pg_trgm;`
  wykonane przez administratora bazy przed `python manage.py migrate`.

`CREATE EXTENSION IF NOT EXISTS` jest idempotentne, więc migracja jest bezpieczna do ponownego
uruchomienia (kolejne wdrożenie, przywrócenie z kopii zapasowej) i bezpieczna na bazie testowej —
`pytest-django` zakłada testową bazę tym samym mechanizmem migracji co produkcję. Czas blokady:
`CREATE INDEX` na ośmiu tysiącach wierszy `schools_school` to ułamek sekundy, więc migracja nie
wymaga osobnego okna serwisowego.

## 13. Cache całych stron publicznych (v0.31.0)

Anonimowe odsłony stron części informacyjnej (strona główna, harmonogram, warsztaty, dokumenty,
FAQ, partnerzy, kontakt, aktualności, wyniki, archiwum) i `/statystyki/` kosztowały na produkcji
250–500 ms CPU na odsłonę (profilowanie 22.09.2026) – głównie renderowanie szablonu, nie zapytania
(1–3 ms). Odpowiedź jest identyczna dla każdego anonimowego gościa tej samej witryny, więc
`apps.web.page_cache.PageCacheMiddleware` trzyma ją w Redisie (ten sam `CACHES["default"]`, co
reszta serwisu) przez `PAGE_CACHE_SECONDS` (domyślnie 120 s).

**Co jest cache'owane.** Wyłącznie allow-lista adresów (kod źródłowy w `apps/web/page_cache.py`,
stałe `ALLOWED_PATHS`/`ALLOWED_PREFIXES`), wyłącznie `GET`/`HEAD`, wyłącznie gość (niezalogowany,
bez sesji zmienionej w trakcie obsługi – przełącznik wysokiego kontrastu, i bez komunikatu
organizatora **wyświetlonego** w tym żądaniu – sprawdzenie niezależne od `session.modified`, patrz
niżej), wyłącznie odpowiedź 200 z `Content-Type: text/html` bez `Set-Cookie`, bez `Vary` i nie
większa niż 512 KiB. Parametr zapytania: tylko `?page=<liczba>`, każdy inny wyłącza cache dla tego
żądania.

**Czego cache nigdy nie obejmuje i dlaczego:** panel koordynatora, konto, API, `/cms/`, `/admin/`,
formularze rejestracji – każdy z nich renderuje coś zależnego od tożsamości albo przyjmuje POST,
a allow-lista (nie deny-lista) sprawia, że nowy adres jest bezpieczny z definicji, dopóki ktoś
świadomie nie dopisze go do listy. Nonce CSP i token CSRF (ten drugi wstrzykiwany do **każdej**
strony przez `templates/base.html`, atrybut `hx-headers`) nie są nigdy przechowywane – w cache'u
leży placeholder, a świeżą wartość dostaje każde żądanie osobno (patrz docstring modułu za pełne
uzasadnienie).

**Uwaga o komunikatach organizatora:** `request.session.modified` **nie wystarcza** jako sygnał
„komunikat został pokazany” – `MessageMiddleware` konsumuje kolejkę i zapisuje ją z powrotem do
sesji dopiero w swojej fazie odpowiedzi, a `PageCacheMiddleware` stoi niżej w łańcuchu (patrz
docstring modułu), więc widzi sesję **przed** tym zapisem. Warstwa sprawdza więc magazyn
komunikatów wprost (`request._messages.used`/`.added_new`) – ustawiany już w trakcie renderowania
szablonu (`{% if messages %}`).

**Odporność na awarię Redisa.** `CACHES["default"]["OPTIONS"]["IGNORE_EXCEPTIONS"]` każe
`django-redis` połykać błędy połączenia; sama warstwa dodatkowo opakowuje własne wywołania cache'a
(`_safe_get`/`_safe_set`/`_safe_incr`) w drugie, niezależne zabezpieczenie. Skutek: gdy Redis nie
odpowiada, strona renderuje się normalnie (BYPASS albo MISS bez zapisu) zamiast kończyć się
pięćsetką.

**Cache-Control.** Każda odpowiedź HIT i MISS z tej warstwy dostaje `Cache-Control: private,
no-store` (``setdefault`` – widok, który sam ustawił ten nagłówek, wygrywa). To jest wyłącznie
zaprzeczenie w drugą stronę: cache jest po stronie serwera, a nagłówek pilnuje, żeby żaden
pośredniczący proxy/CDN nie zbuforował po swojej stronie materializowanego nonce'u/tokenu CSRF.

**Klucz** niesie: wersję globalną, wersję witryny konkursu, identyfikator konkursu, język
interfejsu, ścieżkę i `?page=`. Wersje to liczniki (`INCR`) – unieważnienie nigdy nie wylicza
istniejących wpisów, tylko podbija licznik, więc stare wpisy po prostu przestają być trafiane
i wygasają same po TTL.

**Unieważnianie jest automatyczne** przy: publikacji/wycofaniu/przeniesieniu/skasowaniu strony
Wagtaila, zapisie `cms.SiteSettings`, komunikacie organizatora (`cms.Announcement` – z konkursem:
tylko jego witryna, bez konkursu: wszystkie witryny naraz), zmianie edycji/etapu/wydarzenia
(`competitions.Edition`/`Stage`/`EditionEvent`), ogłoszeniu wyników (`results.ResultsPublication`)
i zapisie/skasowaniu plakatu do pobrania (`promo.PromoMaterial` – lista `/plakaty/` i odnośnik
w stopce, § 14). Ręczne wyczyszczenie (np. po imporcie z ominięciem sygnałów Django):

```bash
docker compose exec -T web python manage.py page_cache_clear
```

**Weryfikacja.** Nagłówek `X-Page-Cache: HIT|MISS|BYPASS` na każdej odpowiedzi (wyłącznie do
diagnozy – klient nic z niego nie wnioskuje). `BYPASS` na allow-liście najczęściej znaczy: gość
zalogowany, parametr zapytania spoza `?page=`, albo `PAGE_CACHE_ENABLED=False` w `.env`.

**Wyłączenie w razie incydentu** (np. redaktor zgłasza „strona nie aktualizuje się”, a sygnał
unieważnienia z jakiegoś powodu nie doszedł): `PAGE_CACHE_ENABLED=False` w `.env` i restart `web`,
albo doraźnie `PAGE_CACHE_SECONDS=0` – oba wyłączniki są od razu widoczne w `X-Page-Cache: BYPASS`.
Cache zostaje **wyłączony domyślnie** w środowisku testowym (`config/settings/test.py`), więc
budżety zapytań (`apps/tenancy/tests/test_invariants.py`) mierzą kod, nie trafienia bufora.

## 14. Plakaty do pobrania i statystyka pobrań (v0.32.0)

Ekran koordynatora `/coordinator/posters/`, strona publiczna `/plakaty/`, pobranie
`/plakaty/<id>/pobierz/` (aplikacja `apps.promo`, opis dla organizatora:
`PODRECZNIK-ORGANIZATORA.md` § 4.10). Dla operatora ważne są cztery rzeczy:

**Gdzie leżą pliki.** Plik plakatu idzie na storage `private_media` – na produkcji bucket
`submissions` pod prefiksem `problem-statements/promo/<id konkursu>/` (ten sam alias, co treści
zadań; prefiks `problem-statements` jest ustawieniem aliasu w `config/settings/production.py`).
Prywatny, bo każde pobranie ma przejść przez licznik – plik z publicznym adresem w buckecie dałoby
się pobierać z pominięciem statystyk. Miniatury leżą w `public-media` pod `promo/previews/`. Oba
buckety są już w kopii zapasowej (§ 1) – nie trzeba nic dopisywać.

**Pobranie idzie przez aplikację** (`FileResponse`, załącznik), jak dokumenty Wagtaila i treść
zadań. Plik do 50 MB zajmuje na czas wysyłki jeden wątek `gthread` (§ 11). Przy dzisiejszym ruchu
(kilkadziesiąt pobrań dziennie) to pomijalne; gdyby plakat zaczął być pobierany setkami na
godzinę, pierwszą dźwignią jest mniejszy plik (PDF do druku rzadko potrzebuje więcej niż 10 MB),
a nie konfiguracja serwera. Jeden adres IP może pobrać najwyżej 30 plików na minutę (scope
`poster_download` w `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`, licznik `apps.web.throttle`,
odpowiedź 429 z `Retry-After`); cała pracownia za jednym NAT-em mieści się w tym z zapasem.

**Pseudonim adresu IP i jego retencja.** Zdarzenie pobrania (`promo.PromoDownload`) niesie
`ip_hash` = HMAC-SHA256 adresu klienta z kluczem wyprowadzonym z `DJANGO_SECRET_KEY` (kontekst
`promo-ip-hash`). Adresu IP ani nagłówka przeglądarki w bazie nie ma. Adres bierze
`apps.core.models.client_ip` – `X-Real-IP` wyłącznie od `TRUSTED_PROXY_IPS`, jak w audycie.
Zadanie beat `promo-clear-expired-ip-hashes` (raz na dobę, `apps.promo.tasks.clear_expired_ip_hashes`)
zeruje skrót w zdarzeniach starszych niż 12 miesięcy; zdarzenia zostają. Ręcznie:

```bash
docker compose exec -T web python manage.py shell -c "from apps.promo.tasks import clear_expired_ip_hashes; print(clear_expired_ip_hashes())"
```

**Zmiana `DJANGO_SECRET_KEY`** zmienia klucz skrótu: pobrania sprzed i po zmianie z tego samego
adresu liczą się jako dwa unikalne adresy (tak samo, jak ta zmiana unieważnia sekrety 2FA, § 5).
Statystyka pobrań łącznie nie zmienia się.

**Odnośnik w stopce** czyta „czy konkurs ma opublikowany plakat” z Redisa (klucz
`promo:available:<id konkursu>`, TTL godzina, unieważniany i od razu przeliczany przy każdym zapisie
plakatu). Po imporcie z ominięciem sygnałów wystarczy `page_cache_clear` i odczekanie TTL albo
restart Redisa.

## 15. Zaświadczenia o statusie ucznia (v0.34.0)

Aplikacja `apps.student_status`, opis dla organizatora: `PODRECZNIK-ORGANIZATORA.md` § 10a. Funkcja
stoi za przełącznikiem konkursu **`student_status_certificate`** (domyślnie wyłączonym) — wdrożenie
wersji niczego nie zmienia, dopóki operator go nie zapali.

**Włączenie dla konkursu** (tu: Olimpiada Kwantowa, `slug=kwantowa`) — w `/admin/ → Konkursy →
<konkurs> → przełączniki` dopisać `"student_status_certificate": true` do JSON-a albo z powłoki:

```bash
docker compose exec -T web python manage.py shell -c "from apps.tenancy.models import Competition; c = Competition.objects.get(slug='kwantowa'); c.feature_flags = {**(c.feature_flags or {}), 'student_status_certificate': True}; c.save(update_fields=['feature_flags']); print(c.has_feature('student_status_certificate'))"
```

Wyłączenie — ta sama linijka z `False`. Wyłączenie **nie kasuje** wgranych skanów (zostają w storage
do retencji edycji albo usunięcia konta), tylko zamyka wszystkie adresy. Po zapaleniu: konto uczestnika
→ pulpit ma kafel „Zaświadczenie o statusie ucznia”, `/me/status-ucznia/wzor.pdf` pobiera PDF; konto
koordynatora → menu *Uczestnicy i konta* ma pozycję „Status ucznia”; `/coordinator/processing-register/`
ma wiersz „Weryfikacja statusu ucznia”.

**Migracje:** `student_status.0001_initial` (nowa pusta tabela) i `tenancy.0009_document_kind_student_status`
(wyłącznie lista wyboru rodzaju dokumentu, bez zmiany kolumny). Obie są addytywne — wycofanie wersji
nie wymaga cofania migracji.

**Gdzie leżą pliki.** Storage rozwiązań (`SUBMISSION_STORAGE_BACKEND`, produkcyjnie bucket `submissions`
na koncie `app-private`) pod prefiksem `student-status/<id konkursu>/<id edycji>/<kod uczestnika>/<uuid>/<sha256>.<ext>`.
Nazwa pliku od uczestnika nie jest zapisywana nigdzie. Bucket jest już w kopii zapasowej (§ 1).
Skany **są kasowane** przez aplikację (`DeleteObject`): po zastąpieniu nowszą wersją, po wykryciu wirusa,
przy anonimizacji/usunięciu konta i przez zadanie retencji — polityka `deploy/minio/policy-submissions.json`
ma to uprawnienie od początku, nic nie trzeba zmieniać.

**Skan antywirusowy** idzie tą samą kolejką `scan` i tym samym clamd, co rozwiązania
(`apps.student_status.tasks.scan_certificate_file`, ponowienia przy niedostępnym ClamAV jak w § o
rozwiązaniach). Plik czeka na skan w stanie „trwa skan antywirusowy” — koordynator nie może go
obejrzeć ani zaakceptować przed czystym wynikiem. Limit pliku 10 MB (poniżej `StreamMaxLength`).

**Retencja.** Zadanie beat `student-status-purge-expired-scans` (raz na dobę,
`apps.student_status.tasks.purge_expired_scans`) usuwa pliki edycji, którym upłynął okres retencji
(ten sam termin, co anonimizacja kont, § 9.1 podręcznika organizatora). Ręcznie:

```bash
docker compose exec -T web python manage.py shell -c "from apps.student_status.services import purge_expired_scans; print(purge_expired_scans())"
```

**Harmonogram beat** jest w bazie (`django_celery_beat`, `DatabaseScheduler`) — wpis z
`CELERY_BEAT_SCHEDULE` trafia tam przy starcie `beat`; po wdrożeniu sprawdź w `/admin/ → Periodic tasks`,
że `student-status-purge-expired-scans` istnieje i jest włączony.

## 16. Materiały z warsztatów (flaga `workshop_materials`)

Ekran koordynatora `/coordinator/workshops/materials/`, strona dla zalogowanych
`/warsztaty/materialy/` (aplikacja `apps.workshop_materials`, opis dla organizatora:
`PODRECZNIK-ORGANIZATORA.md` § 4.11). Flaga jest domyślnie **wyłączona**; zapalenie w `/admin/`
(§ 6.4) po krokach niżej.

### 16.1. Droga pliku – bez gunicorna, bez transkodowania

Film (do 4 GB) **nie przechodzi przez aplikację**. Przeglądarka koordynatora wysyła go częściami
po **16 MB** prosto do MinIO (`PUT` na adresy podpisane przez serwer, wgrywanie wieloczęściowe S3),
przez Caddy na `S3_PUBLIC_ADDRESS` (u nas `olimpiadakwantowa.pl:9000`). Serwer tylko zakłada
wgrywanie, podpisuje części (po 20 na żądanie, ważne godzinę) i w kroku „zakończ” pyta MinIO
o listę części (`ListParts`), składa plik, sprawdza rozmiar (`HeadObject`) i pierwsze 4 KB
(sygnatura MP4/WebM). Worker gunicorna jest zajęty milisekundy, nie minuty; pamięć `web` nie rośnie.
**Serwer niczego nie transkoduje** (6 vCPU z dużym *steal*, § 11) – widz dostaje plik tak, jak go
wgrano, a przewijanie działa żądaniami `Range` bezpośrednio do MinIO (odpowiedź 206).

Pliki (PDF, prezentacje, do 100 MB) idą tą samą drogą, a po złożeniu – przez ClamAV (zadanie
`apps.workshop_materials.tasks.scan_material`, kolejka `scan`). Filmy przez ClamAV **nie** idą:
`StreamMaxLength` clamd to 100 MB, a skan gigabajtów to kilkanaście minut rdzenia; bramką filmu jest
sygnatura kontenera, a adres dla widza wymusza `Content-Type: video/*` (uzasadnienie:
`apps/workshop_materials/tasks.py`).

Obiekty leżą w bucketcie **`submissions`** pod prefiksem **`workshop-materials/<id konkursu>/`**
(losowe nazwy, bez nazwy pliku od przesyłającego), dostęp wyłącznie przez podpis konta `S3_PRIVATE_*`.
Oglądanie: adres podpisany na **2 h** (film, osadzony w `<video>`) albo **5 min** (plik, przekierowanie).

### 16.2. Przed zapaleniem flagi (jednorazowo)

1. **Polityka MinIO.** Konto `app-private` potrzebuje trzech nowych uprawnień: `s3:AbortMultipartUpload`,
   `s3:ListMultipartUploadParts` (na obiektach) i `s3:ListBucketMultipartUploads` (na buckecie) –
   dopisane w `deploy/minio/policy-submissions.json`. `minio-init` nadpisuje politykę przy każdym
   przebiegu, więc po wdrożeniu wystarczy:

   ```bash
   # na serwerze, w /opt/olimpiada
   docker compose run --rm minio-init        # „Created policy `submissions-rw` successfully.”
   ```

   Bez tego wgrywanie kończy się błędem „Magazyn plików nie odpowiada” na kroku „zakończ”.

2. **Miejsce na dysku.** Nagranie godzinnych zajęć z platformy wideo to zwykle 0,5–1 GB (720p) albo
   1–2 GB (1080p); cykl 16 warsztatów to **10–30 GB** w wolumenie `minio_data`, plus chwilowo drugie
   tyle na części w trakcie wgrywania. Sprawdź `df -h /var/lib/docker` przed zapaleniem flagi i dopisz
   ten wolumen do obserwacji (watchdog alarmuje o wolnym miejscu – § 3.2). Limit pojedynczego filmu:
   `WORKSHOP_VIDEO_MAX_MB` w `.env` (domyślnie 4096), pliku: `WORKSHOP_FILE_MAX_MB` (domyślnie 100,
   i tak przycinane do limitu ClamAV).

3. **Kopia zapasowa.** `scripts/backup.sh` **pomija** prefiks `workshop-materials/`
   (`mc mirror --exclude "workshop-materials/*"`). Kopia nocna jest pełna (lustro → tar → gpg, 7 dni
   lokalnie, 30 dni poza serwerem), więc 20 GB filmów znaczyłoby ~60 GB chwilowo na dysku co noc
   i ~600 GB u dostawcy kopii. Po odtworzeniu z kopii wiersze materiałów zostają, a pliku nie ma –
   odtwarzacz pokaże błąd; koordynator usuwa materiał i wgrywa oryginał ponownie. Jednorazowa kopia
   materiałów, jeśli organizator jej chce:

   ```bash
   docker run --rm --network olimpiada_internal -v /opt/olimpiada-backups/materialy:/backup \
     -e MC_HOST_src="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000" \
     minio/mc mirror --overwrite src/submissions/workshop-materials /backup
   ```

### 16.3. Caddy, CSP, CORS

- **Caddy** (`deploy/Caddyfile`, blok `{$S3_PUBLIC_ADDRESS}`): `request_body max_size {$MAX_UPLOAD_MB}MB`
  dotyczy **jednej części** (16 MB), nie całego filmu – `MAX_UPLOAD_MB` musi zostać **> 16** (domyślnie
  25). Bez zmian w konfiguracji. Bez `encode` w tym bloku (kompresja psułaby odpowiedzi 206) i bez
  `log` – podpisane adresy nie lądują w logu dostępu.
- **CSP** (`apps/web/middleware.py`): `connect-src` (PUT części) i `media-src` (`<video>`) zawierają
  origin `S3_PUBLIC_ENDPOINT_URL` od dawna (ta sama reguła co pdf.js przy rozwiązaniach) – bez zmian.
  Sprawdzenie na produkcji: w nagłówku `Content-Security-Policy` strony
  `/warsztaty/materialy/<id>/` musi stać `https://olimpiadakwantowa.pl:9000` w `media-src`.
- **CORS**: przeglądarka wysyła `PUT` z `https://olimpiadakwantowa.pl` na `…:9000` (inny origin).
  MinIO domyślnie odpowiada na preflight `OPTIONS` dla każdego originu (`MINIO_API_CORS_ALLOW_ORIGIN`
  domyślnie `*`); nagłówka `ETag` z odpowiedzi skrypt **nie** potrzebuje (serwer bierze ETagi
  z `ListParts`), więc nie trzeba ustawiać `Expose-Headers`. Jeżeli kiedyś zawęzicie CORS MinIO,
  dopiszcie origin serwisu.

### 16.4. Sprzątanie i porzucone wgrywania

- Zadanie beat **`workshop-materials-cleanup`** (co godzinę, `apps.workshop_materials.tasks.cleanup`)
  kasuje materiały w stanie „wgrywanie” starsze niż **24 h**: porzuca wgrywanie w MinIO (części
  znikają od razu), kasuje obiekt i wiersz. To samo zadanie kasuje pseudonimy widzów starsze niż
  12 miesięcy (rejestr czynności 1.7, wiersz warunkowy „Statystyka wyświetleń materiałów z warsztatów”).
- Niezależnie od tego **MinIO sam** usuwa niezłożone części po dobie (`api stale_uploads_expiry`,
  domyślnie 24 h) – porzucone wgrywanie bez wiersza w bazie (np. awaria między założeniem wgrywania
  a zapisem wiersza) też nie zostaje na zawsze.
- Usunięcie materiału w panelu kasuje obiekt od razu. Obiekt, którego nie udało się skasować, zostaje
  w logu `web` (`Nie udało się skasować obiektu workshop-materials/…`) – do ręcznego `mc rm`.
- Plik, który utknął w „sprawdzaniu antywirusowym” (ClamAV leżał dłużej niż ponowienia zadania),
  koordynator odblokowuje przyciskiem „Sprawdź ponownie”; ręcznie:

  ```bash
  docker compose exec -T web python manage.py shell -c "from apps.workshop_materials.tasks import scan_material; scan_material.delay(<id>)"
  ```

### 16.5. Czego ta funkcja nie chroni

Podpisany adres filmu jest ważny 2 h dla **każdego**, kto go ma – zalogowany widz może go wyciągnąć
z narzędzi przeglądarki i przekazać dalej (działa do wygaśnięcia) albo nagrać ekran.
`controlsList="nodownload"` zdejmuje tylko przycisk w odtwarzaczu. To jest ochrona przed stałym
linkiem krążącym w sieci, nie DRM – organizator wie o tym z podręcznika (§ 4.11).

## 17. Ocena AI (`apps.ai_grading`, prośba organizatora z 24.09.2026)

Sugestia punktów dla komitetu liczona przez model językowy jednego z czterech dostawców: Anthropic
(Claude), OpenAI (GPT), Google (Gemini) albo Meta (Meta Model API, modele Muse Spark). Opis funkcji
dla organizatora: `PODRECZNIK-ORGANIZATORA.md` § 4.12; tutaj to, co dotyczy serwera.

### 17.1. Przełącznik i warunek jego zapalenia

Flaga konkursu **`ai_grading`**, domyślnie wyłączona (§ 6.4). **Nie zapalaj jej przed potwierdzeniem
przez organizatora warunków prawnych** z § 4.12 podręcznika organizatora (polityka prywatności,
regulamin, podstawa prawna). Po zapaleniu koordynator widzi w menu „Ocenianie → Ocena AI”. Od wersji
z dostawcami flaga **nie** wystarcza do wysyłania prac uczestników: każdy dostawca potrzebuje jeszcze
**klucza API** i **potwierdzenia umowy powierzenia** (koordynator w panelu; komenda z § 17.6 tylko
wyjątkowo). Bez nich dostawca może ocenić co najwyżej **pracę testową** koordynatora (§ 17.7).

```json
{"ai_grading": true}
```

### 17.2. Zależności

Zależności Pythona (oficjalne SDK dostawców, w zwykłych `dependencies` `pyproject.toml`):

| Pakiet | Dostawca | Uwagi |
|---|---|---|
| `anthropic>=1.8,<2` | Anthropic | ciągnie `httpx2`, `httpcore2`, `pydantic`, `jiter`, `anyio`, `truststore`, `docstring-parser` |
| `openai>=3.19,<4` | OpenAI **i Meta** | Meta Model API jest zgodne z SDK OpenAI (adres `https://api.meta.ai/v1`); linia 3.x używa `httpx2` jak `anthropic` |
| `google-genai>=2.25,<3` | Google | ciągnie `httpx`, `google-auth`, `requests`, `websockets`, `tenacity` |

Pakietu `llama-api-client` **nie** dokładamy: rozmawia z Llama API, które Meta wyłączyła 6.07.2026.

Obraz produkcyjny instaluje wszystko przy zwykłym budowaniu (`uv pip install -r pyproject.toml`).
Import każdego SDK jest leniwy – wewnątrz `apps.ai_grading.providers.<dostawca>` – więc konkurs bez
oceny AI bibliotek w ogóle nie ładuje, a **brak pakietu wyłącza jednego dostawcę** (panel pokazuje
„niedostępny – brak pakietu …”, zlecenie kończy się `AI_PROVIDER_UNAVAILABLE`), a nie serwis.

Na stacji deweloperskiej za firmowym proxy TLS `pip`/`docker compose build web` potrafi nie pobrać
pakietów. Testy z prawdziwymi klasami SDK uruchamia się wtedy z pakietami zainstalowanymi obok,
w katalogu roboczym, i dołączonymi na **koniec** `sys.path` (plik `.pth`, żeby nie przesłonić wersji
z obrazu):

```bash
uv pip install --system-certs --target sdk --python-platform x86_64-manylinux_2_28 --python-version 3.12 \
  "anthropic>=1.8,<2" "openai>=3.19,<4" "google-genai>=2.25,<3"
docker run --rm --user root -v "$PWD/backend:/app" -v "$PWD/sdk:/sdk:ro" -w /app --network olimpiadaclade_internal \
  -e DATABASE_URL=postgres://…@db:5432/olimpiada_ai --entrypoint "" olimpiada/web:dev \
  sh -c 'echo /sdk > /opt/venv/lib/python3.12/site-packages/zz_sdk.pth; pytest -q apps/ai_grading'
```

Bez pakietów testy wymagające SDK same się pomijają (`importorskip`); reszta testów oceny AI z SDK nie
korzysta (model jest podmieniony na poziomie dyspozytora `services.call_model`).

### 17.3. Klucze API

Klucze wpisuje **koordynator** w panelu, osobno dla każdego dostawcy – nie ma ich w `.env` ani w
żadnym ustawieniu instalacji. Leżą w `ai_grading_aiprovideraccount.api_key_encrypted` (wiersz na
konkurs i dostawcę) jako token Fernet z kluczem wyprowadzonym z `DJANGO_SECRET_KEY` (etykieta
`ai-grading-api-key`, ten sam zabieg co przy 2FA, § 5.5). Migracja `ai_grading.0002_providers`
przeniosła tam klucz Anthropic z v0.34.0 bez odszyfrowywania. Konsekwencje:

- **rotacja `DJANGO_SECRET_KEY` unieważnia zapisane klucze** – panel pokaże „wpisz klucz ponownie”,
  a zlecone oceny skończą się błędem `key_unreadable` (bez wywołania API),
- klucz **nie** jedzie przez Redisa: zadanie Celery dostaje wyłącznie identyfikator oceny i czyta
  klucz z bazy samo,
- klucz nie trafia do logów ani do audytu (wpis `ai_grading.key_set` ma tylko
  `{"replaced": …, "provider": …}`); komunikaty błędów są **nasze** – treść wyjątku SDK (która bywa
  cytatem odpowiedzi HTTP) nie trafia ani do bazy, ani do logu, loguje się kod, klasa wyjątku
  i identyfikator żądania. Klucz Google idzie nagłówkiem `x-goog-api-key`, nie w adresie.
  **Nie** ustawiaj na produkcji trybów diagnostycznych SDK (`ANTHROPIC_LOG=debug`,
  `OPENAI_LOG=debug`) ani poziomu `DEBUG` dla loggerów `httpx`/`httpx2` – logują szczegóły żądań.

### 17.4. Celery: kolejka z ogranicznikiem

Jedna ocena = jedno zadanie `apps.ai_grading.tasks.run_ai_assessment` na kolejce `default`. Zlecenie
**nie** wrzuca wszystkich zadań naraz: oceny czekają w bazie jako `PENDING`, a do Celery trafia ich
tyle, ile mieści `AI_GRADING_MAX_CONCURRENCY` (domyślnie **1** w całej instalacji). Koniec każdej
oceny wypuszcza następną. Powód: worker ma dwa miejsca (`CELERY_CONCURRENCY=2`), wywołanie modelu
trwa minuty, a drugie miejsce musi zostać dla skanu antywirusowego i poczty.

| Ustawienie (`.env`) | Domyślnie | Znaczenie |
|---|---|---|
| `AI_GRADING_MAX_CONCURRENCY` | 1 | ile ocen liczy się naraz; podnosić **razem** z `CELERY_CONCURRENCY`, nigdy do jego wartości |
| `AI_GRADING_STALE_MINUTES` | 30 | po ilu minutach ocena „w locie” jest uznana za zgubioną (> twardy limit zadania 16 min) |
| `AI_GRADING_REQUEST_TIMEOUT` | 600 | limit czasu jednego żądania HTTP do API (s) |
| `AI_GRADING_SDK_MAX_RETRIES` | 2 | ponowienia wewnątrz SDK (429, 5xx, sieć) w obrębie jednego wywołania |
| `AI_GRADING_MAX_TOKENS` | 32000 | górna granica odpowiedzi – bezpiecznik kosztu jednej oceny |
| `AI_GRADING_MAX_BATCH` | 500 | najwięcej prac w jednym zleceniu |

Ponowienia są niezależne od dostawcy: każdy dostawca tłumaczy wyjątki swojego SDK na jeden z rodzajów
(`apps.ai_grading.providers.base.FailureKind`): **auth**, **rate_limit**, **transient**, **permanent**,
**refusal**, **too_large**. Ponawiane są wyłącznie `rate_limit` (429) i `transient` (5xx, sieć, czas)
– do 4 razy z wykładniczym opóźnieniem (60 s, 120 s, … maks. 15 min; `retry-after` z odpowiedzi ma
pierwszeństwo, przycięte do 15 min). 429 oznaczający **wyczerpane środki** u dostawcy
(`insufficient_quota` i pokrewne u OpenAI, 402 u Google i Mety) nie jest ponawiany. Odpowiedź, którą
API **oddało** (także odmowa, blokada filtra bezpieczeństwa i ucięcie na limicie), nie jest ponawiana
automatycznie – jest policzona i kończy się błędem z komunikatem dla koordynatora. Zadanie ma twardy
limit 16 minut (`soft_time_limit` 15 min).

Siatka asekuracyjna: zadanie beat **`ai-grading-pump`** (co 5 min, `pump_ai_assessments`) zamienia
oceny `RUNNING` starsze niż `AI_GRADING_STALE_MINUTES` w błąd „przerwana” (a nie w ponowienie –
wywołanie mogło zostać policzone po stronie dostawcy, zanim worker padł) i wypuszcza oceny,
których zadanie zniknęło z brokera. `DatabaseScheduler` dopisze wpis sam przy starcie beatu.

Podgląd kolejki:

```bash
docker compose exec -T web python manage.py shell -c "from apps.ai_grading.models import AiAssessment as A; from django.db.models import Count; print(list(A.objects.values('provider', 'status').annotate(n=Count('id'))))"
```

Awaryjne zatrzymanie wszystkiego bez wdrożenia: zdjąć flagę `ai_grading` (oceny czekające w kolejce
skończą się błędem `disabled` bez wywołania API) albo ustawić koordynatorowi limit wydatków 0. Jednego
dostawcy – koordynator usuwa jego klucz albo wycofuje potwierdzenie umowy (prace czekające w kolejce
kończą się błędem `no_key` / `dpa` przed wysyłką).

### 17.5. Logi i koszt

Każde wywołanie loguje dostawcę, model, `stop_reason` i identyfikator żądania (Anthropic
`request-id`, OpenAI i Meta `x-request-id`, Google `response_id`) – po nim wsparcie dostawcy znajduje
żądanie. Treści pracy ani odpowiedzi w logach nie ma. Zużycie tokenów i szacowany koszt liczy
aplikacja: stawki domyślne w `apps.ai_grading.catalog.DEFAULT_PRICES` (stan z 24.09.2026, źródła
w docstringu modułu), nadpisywane przez koordynatora w tabeli cen (`AiGradingSettings.price_overrides`);
mnożniki cache są własnością dostawcy (`providers.<dostawca>.cache_*_multiplier`). Model bez ceny ma
koszt „nieznany”: tokeny się liczą, kwota nie, a licznik `total_unpriced_calls` rośnie – przy
ustawionym limicie wydatków taki model jest odrzucany przed wysyłką (`AI_PRICE_UNKNOWN`). Przy zmianie
cennika dostawcy poprawić `DEFAULT_PRICES` (wydanie) albo tabelę w panelu (od razu). Fakturę wystawia
dostawca organizatorowi, na którego jest klucz.

### 17.6. Potwierdzenie umowy powierzenia komendą (`confirm_ai_provider_dpa`)

Potwierdzenie umowy powierzenia (DPA) z dostawcą jest **oświadczeniem organizatora**, więc żadna
migracja go nie wpisuje – także dla Anthropic z v0.34.0. Po wdrożeniu wersji z dostawcami **żaden
dostawca nie dostaje prac uczestników**, dopóki go nie potwierdzi **koordynator osobiście w panelu**
(`/coordinator/ai-grading/` → „Potwierdź umowę powierzenia” → strona z informacją o dostawcy →
oświadczenie → „Potwierdzam”; zapis niesie wersję pokazanej informacji). **To jest zwykła droga –
decyzja organizatora z 24.09.2026: operator nie wpisuje potwierdzeń za koordynatora.** Komenda niżej
zostaje wyłącznie na sytuacje wyjątkowe (np. panel niedostępny, a organizator potwierdził umowę
na piśmie) i zapisuje potwierdzenie **bez** wersji informacji – karta dostawcy pokazuje wtedy
„wpisane komendą operatora”:

```bash
docker compose exec -T web python manage.py confirm_ai_provider_dpa \
  --competition kwantowa --provider all \
  --confirmed-by <e-mail konta organizatora> \
  --note "potwierdzone przez organizatora w rozmowie 24.09.2026"
```

- `--provider` – `anthropic`, `openai`, `google`, `meta` albo `all`,
- `--confirmed-by` – adres **istniejącego, aktywnego** konta; to ono figuruje jako potwierdzający
  (konto bez roli koordynatora w tym konkursie dostaje ostrzeżenie, ale zapis przechodzi),
- zapis jest dokładnie taki jak z panelu: data, konto i wpis `ai_grading.dpa_confirmed` w dzienniku
  zdarzeń konkursu z uwagą i `"via": "command"`,
- komenda jest **idempotentna**: umowa już potwierdzona zostaje z pierwotną datą i osobą, bez nowego
  wpisu; nieznany konkurs albo adres kończy się błędem bez żadnego zapisu,
- dostawca bez klucza API zostaje potwierdzony, ale działa dopiero po dodaniu klucza (komenda to
  wypisuje).

Wycofanie potwierdzenia – wyłącznie w panelu (świadoma decyzja koordynatora, też w dzienniku).

### 17.7. Tryb testowy (praca testowa koordynatora)

Koordynator może wgrać na karcie zadania **pracę testową** (własny przykład: PDF, JPG, PNG, `.py`,
`.ipynb`) i ocenić ją **każdym dostawcą z kluczem – bez potwierdzonej umowy powierzenia**. Po stronie
serwera:

- plik przechodzi walidację treści prac uczestników (`apps.submissions.validators`; PNG – sygnatura)
  i skan antywirusowy zadaniem `apps.ai_grading.tasks.scan_ai_test_work` na kolejce **`scan`**
  (plik zainfekowany jest od razu usuwany ze storage'u),
- leży w buckecie prac (`S3_SUBMISSIONS_BUCKET`) pod prefiksem **`ai-test/<konkurs>/<zadanie>/`** –
  żaden mechanizm prac uczestników (paczki ZIP, przekazywanie, retencja) go nie czyta; usunięcie
  pracy testowej w panelu kasuje obiekt po zatwierdzeniu transakcji,
- plik o tym samym SHA-256 co plik pracy uczestnika konkursu jest odrzucany (`AI_TEST_IS_SUBMISSION`),
- oceny testowe idą tą samą kolejką (ten sam ogranicznik) i liczą się do zużycia i limitu wydatków;
  w bazie to wiersze `AiAssessment` z `test_work` zamiast `submission`, więc nie ma ich w panelu
  recenzenta, uczestnika, eksporcie danych ani statystykach.

## 18. Dowolne wartości ocen: migracja kolumn punktów (v0.35.0)

Wydanie zmienia typ kolumn punktów z liczb całkowitych na `numeric(p, 2)` (oceny 4,25) i dokłada
przełącznik etapu `ScoringScale.free_values`. Migracje: `competitions.0032_free_scores`,
`grading.0011_decimal_scores`, `appeals.0003_decimal_new_score`. Żadnej flagi, żadnej zmiany `.env`,
żadnego nowego zadania beat. Każdy istniejący etap zostaje w trybie „tylko wartości ze skali” –
przełącza go dopiero organizator na ekranie skali.

### 18.1. Co robi migracja z danymi i ile trwa

`ALTER TABLE … ALTER COLUMN … TYPE numeric(7|10, 2)` jest **rzutowaniem bezstratnym** (5 → 5.00),
ale PostgreSQL **przepisuje przy nim całą tabelę** pod blokadą `ACCESS EXCLUSIVE` – na czas
przepisania ani odczyt, ani zapis tej tabeli nie przejdzie. Dotknięte tabele:

| Tabela | Kolumny | Rząd wielkości (produkcja) | Szacowany czas |
|---|---|---|---|
| `grading_review` | `score` | ~2 recenzje × prace edycji: dziesiątki tysięcy | < 1–3 s |
| `grading_finalgrade` | `score` | liczba prac: tysiące – dziesiątki tysięcy | < 1 s |
| `competitions_stageentry` | `total_points` | wpisy do etapów: tysiące – dziesiątki tysięcy | < 1 s |
| `competitions_problem`, `…_qualificationrule`, `…_transitionrule`, `…_interviewscore`, `appeals_appealdecision` | maksima, progi, punkty | dziesiątki – setki | pomijalny |

Szacunek przy przepustowości przepisania rzędu 50–100 tys. wierszy/s na tym VPS-ie (z zapasem na
kradzież CPU hosta, patrz § 11); dokłada się do tego walidacja nowych więzów `CHECK (… >= 0)` – jedno
przejście po tabeli, bez blokady dłuższej niż samo przepisanie. Wdrożenie mimo to **poza godzinami
oceniania** (recenzent zapisujący ocenę w trakcie przepisania `grading_review` dostanie czekanie
zakończone zapisem albo – po `statement_timeout` – błąd z prośbą o ponowienie).

Przed wdrożeniem, na produkcji, sprawdź liczność tabel (odczyt, bez blokad):

```bash
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE relname IN ('grading_review','grading_finalgrade','competitions_stageentry') ORDER BY 1;"
```

Powyżej ~1 mln wierszy w którejś z nich zaplanuj okno serwisowe.

### 18.2. Po wdrożeniu

```bash
docker compose exec -T web python manage.py showmigrations competitions grading appeals | tail -n 5
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT data_type, numeric_precision, numeric_scale FROM information_schema.columns WHERE table_name='grading_review' AND column_name='score';"
```

Oczekiwane: `numeric`, `7`, `2`. Ogłoszone tabele wyników (snapshoty JSON) **nie są** przepisywane –
liczby całkowite zostają w nich liczbami całkowitymi i renderują się jak dotąd.

### 18.3. Rollback

Cofnięcie migracji (`migrate grading 0010`, `migrate appeals 0002`, `migrate competitions 0031`)
zamienia kolumny z powrotem na całkowite; **ocena ułamkowa wystawiona po wdrożeniu zostałaby wtedy
zaokrąglona przez bazę**, a zadanie z samym maksimum 12,5 – ucięte. Cofać wolno wyłącznie, dopóki
żaden etap nie został przełączony na dowolne wartości:

```bash
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM competitions_scoringscale WHERE free_values;"
```

musi dać `0`. Jeśli nie daje – nie cofaj, napraw w przód: kod v0.34.0 na kolumnach dziesiętnych
co prawda wystartuje, ale oceny ułamkowej nie przyjmie ani poprawnie nie pokaże („5,00”).
