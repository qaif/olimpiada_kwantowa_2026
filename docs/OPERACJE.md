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
| Baza `olimpiada_djcms` (`pg_dump -Fc`) – **tylko przy `DJCMS_ENABLED=1`** | usługa `db` | strony, wtyczki i wersje redakcji wersji porównawczej `dj.` (§ 22) |
| Wolumen `djcms_media` (`tar` z kontenera `djcms`) – **tylko przy `DJCMS_ENABLED=1`** | usługa `djcms` | obrazy wgrane przez redaktorów `dj.` (filer) |

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
4. wysyłka `rclone` **poza serwer** – do kubełka S3-kompatybilnego u innego dostawcy (§ 1.3) albo na
   **Dysk Google** konta Fundacji (§ 1.6) – do `daily/`, pierwszego dnia miesiąca także do
   `monthly/`; po wysyłce `rclone check` porównuje rozmiar i sumę kontrolną każdej paczki po tamtej
   stronie,
5. retencja: zdalnie 30 dni w `daily/` i 365 dni w `monthly/` (`REMOTE_DAILY_KEEP_DAYS`,
   `REMOTE_MONTHLY_KEEP_DAYS`), lokalnie 7 dni (`LOCAL_KEEP_DAYS`); zdalna rusza **wyłącznie** po
   udanej i sprawdzonej wysyłce,
6. meldunek do aplikacji: `manage.py record_backup_status --ok --offsite` (kopia jest też poza
   serwerem) albo `--ok` (kopia wyłącznie lokalna).

**Wersja porównawcza `dj.` (§ 22).** Przy `DJCMS_ENABLED=1` w `.env` i istniejącej bazie
`olimpiada_djcms` dochodzą podkroki: **1b** – `pg_dump -Fc` tej bazy (kontem aplikacji) →
`djcms-db-<stamp>.dump.gpg`, **2b** – `tar` wolumenu `djcms_media` z działającego kontenera `djcms`
→ `djcms-files-<stamp>.tar.gpg`. Obie paczki mają ten sam znacznik co kopia główna, to samo
szyfrowanie, tę samą wysyłkę (`daily/`, `monthly/`, `rclone check`) i retencję. Bez przełącznika
skrypt nie wykonuje **żadnego** polecenia djcms (także zapytania o bazę) – po wyłączeniu `dj.`
(§ 22.6) ostatnią kopią jego danych jest kopia z ostatniej nocy przed wyłączeniem. Awaria po
stronie `dj.` (djcms nie działa, zrzut albo `tar` nieudany) **nie zatrzymuje** kopii głównej –
ta powstaje i wyjeżdża – ale przebieg kończy się kodem 1 i meldunkiem `--failed` z notatką „kopia
główna … jest, kopia dj. NIE: …”, czyli po 36 h alarmem watchdoga. `DJCMS_ENABLED=1`, a bazy
jeszcze nie ma (przed pierwszym wdrożeniem z `dj.`) – tylko wpis w logu, bez błędu.

Miejsce wybiera `BACKUP_REMOTE_TYPE=s3|drive|none`; bez tej zmiennej: `s3`, gdy jest
`BACKUP_REMOTE_URL`, `drive`, gdy jest token Dysku (`secrets/rclone/rclone.conf` albo
`BACKUP_DRIVE_TOKEN`), w pozostałych przypadkach `none`. Kod: `scripts/lib/backup_offsite.sh`
(wspólny dla `backup.sh` i `restore.sh`).

**Bez kopii zdalnej skrypt robi wyłącznie kopię lokalną** i mówi o tym na stdout. Taka kopia
chroni przed „skasowałem nie tę edycję”, ale ginie razem z serwerem – czyli nie chroni przed tym,
przed czym kopie zapasowe mają chronić.

**Skonfigurowana, a nieudana wysyłka to nieudana kopia.** Skrypt kończy się kodem 1 i melduje
`--failed` z notatką („kopia lokalna … jest, poza serwer NIE dotarła: …”), więc znacznik ostatniej
kopii się nie przesuwa i po 36 h watchdog wysyła „brak świeżej kopii zapasowej” razem z tą
notatką. Kopia lokalna z tej nocy zostaje na dysku, a stare kopie zdalne nie są kasowane.

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

Kopia poza serwerem – **wariant S3** poniżej; **wariant Dysk Google** (konto Fundacji, bez
płatnego kubełka) opisuje § 1.6. Dla S3 cztery wartości wpisuje się ręcznie (deploy zostawia je
w `.env` zakomentowane):

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

Gdy obok sprawdzanej paczki leży `djcms-db-<ten sam stamp>.dump.gpg` (kopia z `dj.`), ten sam
tymczasowy Postgres dostaje drugą bazę: `pg_restore` i wymóg co najmniej jednej strony
w `cms_page`; paczka `djcms-files-<stamp>.tar.gpg` musi się rozszyfrować i dać przeczytać
w całości (`tar -tf`). Brak paczki plików przy obecnej bazie, zero stron albo nieudany
`pg_restore` = test nieudany (`--failed`, powód w notatce). Kopie przedwdrożeniowe
`djcms-db-pre-*.dump` nie biorą w tym udziału.

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

`/status.json` (publiczny) niesie `backup_last_ok`, `backup_last_verified` i `backup_offsite`
(ostatnia kopia wyjechała poza serwer i zgadza się tam suma kontrolna, nie starsza niż 36 h) jako
**wartości logiczne**. Dat tam nie ma świadomie: strona jest publiczna, a data ostatniej kopii mówi obcemu,
kiedy uderzenie zaboli najbardziej.


### 1.6. Kopia poza serwerem na Dysku Google (prośba organizatora z 25.09.2026)

Drugie – obok kubełka S3 z § 1.3 – miejsce na kopię poza serwerem: **Dysk Google konta Fundacji**
(Workspace `qaif.org`). Kopie są małe (ok. 50 MB na noc), więc płatny kubełek nie jest potrzebny.
Instrukcja krok po kroku dla organizatora (bez tła technicznego) jest też w
`PODRECZNIK-ADMINISTRATORA.md` § 6.4 – te same polecenia.

#### 1.6.1. Co leży na Dysku i kto może to przeczytać

| Folder na Dysku | Co | Jak długo |
|---|---|---|
| `Olimpiada-kopie-zapasowe/daily/` | co noc dwie paczki: `db-<data>.dump.gpg` (baza – konta, zgłoszenia, oceny, decyzje komisji, audyt) i `files-<data>.tar.gpg` (prace uczestników i pliki CMS-u) | 30 dni |
| `Olimpiada-kopie-zapasowe/monthly/` | te same paczki z pierwszego dnia każdego miesiąca | 365 dni |

Obie paczki są **zaszyfrowane na serwerze, przed wysyłką** (`gpg`, AES-256) hasłem
`BACKUP_PASSPHRASE`. Google przechowuje wyłącznie szyfrogram – nie przeczyta z niego ani jednego
nazwiska. Odwrotna strona: **bez tego hasła kopie są bezużyteczne** także dla nas. Hasło musi
leżeć w menedżerze haseł Fundacji (§ 1.3), osobno od konta Google – ktoś, kto ma dostęp do
Dysku, nie powinien mieć przy okazji hasła.

Miejsce na Dysku: ok. **50 MB × (30 kopii dziennych + 12 miesięcznych) ≈ 2 GB**. Paczka plików
rośnie razem z liczbą oddanych prac (jest co noc pełna) – w sezonie zawodów policz raczej
100–200 MB na noc, czyli 4–8 GB. Kosz nie jest używany (§ 1.6.5), więc to jest cała zajętość.

#### 1.6.2. Założenia (dlaczego tak, a nie inaczej)

- **Zakres `drive.file`** (najmniejszy możliwy): rclone widzi i może zmieniać **wyłącznie pliki
  i foldery, które sam założył**. Token, który wycieknie z serwera, nie otwiera reszty Dysku
  Fundacji – ani dokumentów, ani cudzych folderów. Konsekwencja: folderu **nie zakłada się ręcznie**
  w przeglądarce. Zakłada go rclone przy pierwszej wysyłce (`Olimpiada-kopie-zapasowe` w „Mój
  dysk”; inna nazwa: `BACKUP_DRIVE_FOLDER`). Folder założony ręcznie byłby dla rclone niewidoczny,
  więc `root_folder_id` przy tym zakresie nie ma zastosowania i skrypt go nie używa.
- **Token w pliku, nie w `.env`** – świadomy wyjątek od zasady „sekrety wyłącznie w `.env`”.
  Token OAuth to para: `access_token` (ważny godzinę) i `refresh_token` (długi). rclone przy
  każdym przebiegu wymienia go na świeży i **zapisuje wynik do swojego pliku konfiguracji**.
  Do zmiennej środowiskowej zapisać nie umie, a zmienna ma w rclone pierwszeństwo przed plikiem,
  więc token trzymany tylko w `.env` przykrywałby odświeżony. Poza tym `.env` trafia do
  kontenerów `web`/`worker`/`beat` (`env_file`) – token w pliku nie trafia nigdzie poza rclone.
  - plik: **`/opt/olimpiada/secrets/rclone/rclone.conf`**, właściciel `root`, katalogi `secrets/`
    i `secrets/rclone/` – `700`, plik – `600` (skrypt pilnuje tego przy każdym przebiegu);
  - do kontenera rclone montowany jest **katalog** `secrets/rclone` (rclone zapisuje przez plik
    tymczasowy i `rename`, co na pojedynczym zamontowanym pliku się nie udaje);
  - krok 2/8 wdrożenia omija `secrets/` tak samo jak `.env`; w repozytorium jest w `.gitignore`.
  - Alternatywa dla administratora: `BACKUP_DRIVE_TOKEN='{"access_token":…}'` w `.env` (koniecznie
    w apostrofach). Skrypt przepisuje go do pliku przy pierwszym przebiegu i ponownie **tylko**
    wtedy, gdy w `.env` pojawi się token z innym `refresh_token` (nowa autoryzacja).
- **Klient OAuth**: domyślnie wbudowany klient rclone (wspólny limit zapytań wszystkich
  użytkowników rclone – przy dwóch paczkach na noc bez znaczenia). Własny klient (§ 1.6.7) jest
  opcjonalny.

#### 1.6.3. Uruchomienie – zalecane: autoryzacja na serwerze, token nigdy niewyświetlany

Token powstaje na serwerze i trafia **potokiem prosto do pliku** – nie pojawia się na ekranie,
w schowku, w historii ani w żadnej wiadomości. Przeglądarka organizatora dociera do rclone na
serwerze tunelem SSH (rclone po zalogowaniu przekierowuje Google na `http://127.0.0.1:53682/`).

1. **Tunel** – na własnym komputerze (Windows: PowerShell; Mac/Linux: terminal), sesja zostaje
   otwarta do końca:

   ```bash
   ssh -L 53682:127.0.0.1:53682 root@olimpiadakwantowa.pl
   ```

2. **Autoryzacja** – w tej samej sesji, już na serwerze:

   ```bash
   cd /opt/olimpiada
   docker run --rm --network host rclone/rclone:1.69 authorize drive --drive-scope drive.file --auth-no-open-browser \
     | grep -o '{.*}' | scripts/backup.sh --drive-token
   ```

   Na ekranie pojawi się link `http://127.0.0.1:53682/auth?state=…` – otwórz go w przeglądarce
   **na swoim komputerze** (tunel zaprowadzi go na serwer). Dalej:
   - wybierz konto **Fundacji w domenie `qaif.org`** (to samo, na które ma trafiać kopia – nie
     prywatne konto Gmail),
   - ekran zgody Google: aplikacja „rclone” prosi o dostęp typu *„Wyświetlanie, edytowanie,
     tworzenie i usuwanie tylko tych plików z Dysku Google, których używasz w tej aplikacji”* –
     to jest właśnie zakres `drive.file`. Jeśli ekran prosi o dostęp do **wszystkich** plików,
     przerwij: w poleceniu zabrakło `--drive-scope drive.file`,
   - „Zezwól”. Przeglądarka pokaże „Success”, a terminal: `Zapisano token Dysku Google:
     /opt/olimpiada/secrets/rclone/rclone.conf`.

   `--network host` jest potrzebne, bo rclone nasłuchuje na `127.0.0.1` i opublikowany port
   kontenera by do niego nie doszedł. Port 53682 musi być na serwerze wolny (nic go nie używa).
   Jeśli Workspace ma zablokowane aplikacje zewnętrzne, Google pokaże „Dostęp zablokowany” –
   administrator Workspace zezwala na rclone w *Konsola administracyjna → Bezpieczeństwo → Dostęp
   do danych i kontrola → Ustawienia API → Zarządzaj dostępem aplikacji innych firm* (albo
   korzystamy z własnego klienta, § 1.6.7).

3. **Test** (bez czekania na noc) – plik próbny zapisany, wylistowany, odczytany i skasowany:

   ```bash
   scripts/backup.sh --offsite-test
   ```

   Oczekiwane zakończenie: `Test udany: zapis, lista, odczyt i kasowanie działają.`

4. **Pierwsza prawdziwa kopia i test odtwarzania** (kilka minut; kolejne robi cron o 3:15):

   ```bash
   scripts/backup.sh
   scripts/backup_verify.sh
   docker compose exec web python manage.py record_backup_status --show
   ```

   `backup.sh` kończy się `Gotowe: kopia lokalna + poza serwerem, zweryfikowana sumą kontrolną`,
   a `--show` pokazuje wiersz „ostatnia kopia poza serw.” z dzisiejszą datą. Zamknij sesję SSH
   (tunel jest potrzebny tylko w kroku 2).

5. **Podgląd na Dysku**: <https://drive.google.com> (konto Fundacji) → „Mój dysk” → folder
   `Olimpiada-kopie-zapasowe` → `daily/`. Pliki `.gpg` nie mają podglądu – to szyfrogram.
   Nie przenoś ani nie zmieniaj nazw tych plików: retencja szuka ich w tych miejscach.

#### 1.6.4. Wariant: autoryzacja na własnym komputerze

Gdy tunel SSH nie wchodzi w grę. Token przechodzi wtedy przez ekran i schowek organizatora –
wklej go wyłącznie w terminal serwera, nigdzie indziej (ani w maila, ani w czat, ani do asystenta).

1. Pobierz rclone dla Windows: <https://rclone.org/downloads/> → „Windows – Intel/AMD 64 bit”,
   rozpakuj ZIP (np. do `C:\rclone`).
2. W PowerShellu, w tym katalogu:

   ```powershell
   .\rclone.exe authorize "drive" --drive-scope drive.file
   ```

   Otworzy się przeglądarka – konto Fundacji i ekran zgody jak w § 1.6.3 krok 2.
3. W terminalu pojawi się `Paste the following into your remote machine --->`, pod spodem jedna
   linia `{"access_token":…}`, potem `<---End paste`. Skopiuj **tylko tę linię** (od `{` do `}`).
4. Na serwerze: `cd /opt/olimpiada && scripts/backup.sh --drive-token`, wklej linię, Enter.
5. Dalej jak w § 1.6.3, kroki 3–5. Zamknij okno PowerShella (token jest w jego historii
   przewijania) i skasuj katalog `C:\rclone`, jeśli nie będzie więcej potrzebny.

#### 1.6.5. Retencja i kosz

Retencja kasuje paczki starsze niż 30 dni (`daily/`) i 365 dni (`monthly/`) **na stałe, z pominięciem
kosza** (`use_trash=false`, zmienna `BACKUP_DRIVE_USE_TRASH`, domyślnie `false`). Powody:

- plik w koszu Dysku nadal zajmuje miejsce przez 30 dni – z koszem zajętość kopii dziennych się
  podwaja, a kosz konta Fundacji zapełnia się co miesiąc ~60 plikami `.gpg`, wśród których giną
  pliki skasowane przez ludzi,
- ochrona, którą dałby kosz, jest pozorna: przy zakresie `drive.file` ten sam token może skasować
  swoje pliki na stałe z pominięciem kosza, więc kosz nie chroni przed kimś, kto przejął serwer,
- przed błędem samej retencji chronią dwa bezpieczniki: retencja rusza **tylko po udanej
  i sprawdzonej sumą wysyłce** (dzisiejsza kopia na pewno jest po tamtej stronie), a wartości
  `REMOTE_*_KEEP_DAYS` muszą być liczbą ≥ 1 (`0` albo pusta wartość = odmowa, nic nie jest kasowane).

Ochrona przed przejęciem serwera, jeśli jest potrzebna: raz na kwartał skopiuj w przeglądarce jedną
paczkę z `monthly/` („Utwórz kopię”) do **innego** folderu. Kopia zrobiona ręcznie nie jest plikiem
rclone, więc token z serwera jej nie widzi i nie skasuje.

#### 1.6.6. Odtworzenie z Dysku

Z serwera (działającego albo nowego): `scripts/restore.sh --list`, potem
`scripts/restore.sh --fetch <plik>` (§ 2.2) – ściąga do `/opt/olimpiada-backups/` i sprawdza sumą.

Ręcznie, bez serwera: w przeglądarce pobierz parę `db-<data>.dump.gpg` i `files-<data>.tar.gpg`
z tej samej nocy, wgraj je na serwer do `/opt/olimpiada-backups/` (np. `scp`) i dalej jak w § 2.2
(`restore.sh --dry-run`, potem właściwe odtworzenie). Rozszyfrowanie wymaga `BACKUP_PASSPHRASE`.

#### 1.6.7. Opcjonalnie: własny klient OAuth w Google Cloud (Workspace `qaif.org`)

Po co: własny limit zapytań zamiast wspólnego limitu rclone, na ekranie zgody nazwa Fundacji
zamiast „rclone”, a administrator Workspace może dopuścić dokładnie tego klienta.

1. <https://console.cloud.google.com/> na koncie Fundacji → nowy projekt (np. `olimpiada-kopie`).
2. *Interfejsy API i usługi → Biblioteka* → „Google Drive API” → Włącz.
3. *Ekran zgody OAuth* (Google Auth Platform): typ użytkownika **Wewnętrzny** (*Internal*) – tylko
   konta `qaif.org`, bez weryfikacji przez Google i **bez 7-dniowego wygasania tokenów**, które
   dotyczy aplikacji zewnętrznych w stanie „Testowanie”. Zakres: `.../auth/drive.file`.
4. *Dane logowania → Utwórz dane logowania → Identyfikator klienta OAuth* → typ **Aplikacja
   komputerowa** → zapisz identyfikator klienta i tajny klucz klienta.
5. Do `/opt/olimpiada/.env`:

   ```ini
   BACKUP_DRIVE_CLIENT_ID=123456789-abc.apps.googleusercontent.com
   BACKUP_DRIVE_CLIENT_SECRET=GOCSPX-...
   ```

6. Autoryzacja jak w § 1.6.3, z identyfikatorem i kluczem w poleceniu (po `drive`):

   ```bash
   docker run --rm --network host rclone/rclone:1.69 authorize drive "$ID" "$SECRET" --drive-scope drive.file --auth-no-open-browser \
     | grep -o '{.*}' | scripts/backup.sh --drive-token
   ```

   (`ID`/`SECRET` ustaw wcześniej w tej sesji, np. `set -a; . ./.env; set +a;
   ID=$BACKUP_DRIVE_CLIENT_ID SECRET=$BACKUP_DRIVE_CLIENT_SECRET`).

**Uwaga przy zmianie klienta:** zakres `drive.file` jest przypisany do klienta OAuth. Pliki wysłane
wcześniej klientem rclone są dla nowego klienta **niewidoczne** – retencja ich nie skasuje,
a `restore.sh --list` ich nie pokaże. Po zmianie ustaw nowy folder (`BACKUP_DRIVE_FOLDER=…`),
a stary skasuj ręcznie w przeglądarce po 30 dniach (albo po roku, jeśli chcesz zachować `monthly/`).

#### 1.6.8. Zmienne

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `BACKUP_REMOTE_TYPE` | automatycznie | `s3`, `drive` albo `none`; potrzebna, gdy skonfigurowane są oba warianty |
| `BACKUP_DRIVE_FOLDER` | `Olimpiada-kopie-zapasowe` | folder w „Mój dysk” (albo na dysku współdzielonym); zakłada go rclone |
| `BACKUP_DRIVE_TEAM_DRIVE` | – | identyfikator dysku współdzielonego (końcówka adresu `drive.google.com/drive/folders/<ID>` dysku współdzielonego) zamiast „Mój dysk” |
| `BACKUP_DRIVE_CLIENT_ID` / `_CLIENT_SECRET` | klient rclone | własny klient OAuth (§ 1.6.7) |
| `BACKUP_DRIVE_USE_TRASH` | `false` | `true` = retencja przenosi do kosza (§ 1.6.5) |
| `BACKUP_DRIVE_TOKEN` | – | token w `.env` zamiast `--drive-token` (w apostrofach) |
| `BACKUP_DRIVE_SCOPE` | `drive.file` | nie zmieniać bez powodu |
| `REMOTE_DAILY_KEEP_DAYS` / `REMOTE_MONTHLY_KEEP_DAYS` | `30` / `365` | retencja zdalna (także S3) |

Dysk współdzielony (*shared drive*) ma tę zaletę, że kopie nie są własnością jednej osoby – przy
odejściu właściciela konta pliki zostają w organizacji. Wymaga uprawnienia „Menedżer treści” dla
konta, którym robiono autoryzację.

#### 1.6.9. Utrzymanie i awarie

- **Wygaśnięcie / cofnięcie dostępu.** Token przestaje działać, gdy ktoś usunie dostęp rclone
  na <https://myaccount.google.com/permissions>, konto Fundacji zostanie zawieszone, administrator
  Workspace zablokuje aplikację albo token nie będzie używany przez 6 miesięcy (cron używa go co
  noc). Objaw: `backup.sh` kończy się kodem 1 z `invalid_grant` w logu, po 36 h przychodzi alarm
  „brak świeżej kopii zapasowej” z notatką „poza serwer NIE dotarła”. Naprawa: § 1.6.3 kroki 1–3
  jeszcze raz (nowy token zastępuje stary; pliki na Dysku zostają widoczne, bo klient ten sam).
- **Zmiana hasła konta Google** nie unieważnia tokenu z zakresem Dysku.
- **Wyłączenie kopii na Dysk**: usuń `/opt/olimpiada/secrets/rclone/rclone.conf` (i ewentualne
  `BACKUP_DRIVE_TOKEN` z `.env`) albo ustaw `BACKUP_REMOTE_TYPE=none`, potem cofnij dostęp na
  stronie uprawnień konta Google.
- **Alarm „kopia zapasowa przestała wyjeżdżać poza serwer”**: nocna kopia lokalna się udaje, ale
  od ponad 36 h nic nie dotarło na Dysk – zwykle ktoś usunął plik tokenu albo zmienił
  `BACKUP_REMOTE_TYPE`. `/status.json` pokazuje wtedy `backup_offsite: false`.

#### 1.6.10. RODO

- **Google jest podmiotem przetwarzającym** Fundacji na podstawie umowy powierzenia Workspace
  (*Cloud Data Processing Addendum*). Sprawdź w *Konsola administracyjna → Konto → Ustawienia
  konta → Informacje prawne i zgodność*, że jest zaakceptowana, i dopisz „kopie zapasowe platformy
  (zaszyfrowane) – Google Workspace” do rejestru czynności przetwarzania (odbiorcy / miejsca
  przechowywania).
- Dane są **zaszyfrowane przed wysyłką** kluczem, którego Google nie ma (art. 32 ust. 1 lit. a
  RODO). Dla Google to nieczytelny szyfrogram; wyciek z Dysku nie jest wyciekiem danych osobowych
  w rozumieniu praktycznym, dopóki `BACKUP_PASSPHRASE` jest bezpieczne.
- **Region danych**: jeśli edycja Workspace Fundacji oferuje regiony danych (*Konsola
  administracyjna → Dane → Zgodność → Regiony danych*), ustaw „Europa” dla jednostki, w której
  jest konto robiące kopie. Jeśli edycja tego nie oferuje, dane mogą leżeć poza EOG – przekazanie
  obejmuje umowa powierzenia Google (standardowe klauzule umowne / EU-US Data Privacy Framework),
  a szyfrowanie po naszej stronie ogranicza ryzyko do minimum.
- **Retencja** kopii (30 dni / 12 miesięcy) jest ograniczona w czasie; dane usunięte z bazy
  (anonimizacja, żądanie usunięcia) znikają z kopii najpóźniej po roku – ten termin powinien być
  wpisany w politykę prywatności / rejestr.

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

Kopię zdalną (S3 albo Dysk Google – ta sama konfiguracja, którą wysyła `backup.sh`) trzeba
najpierw ściągnąć, bo skrypt czyta katalog lokalny. `--fetch` szuka pliku w `daily/`, potem
w `monthly/`, ściąga go do `/opt/olimpiada-backups/` i sprawdza sumą kontrolną:

```bash
./scripts/restore.sh --list
./scripts/restore.sh --fetch db-20260117T031500Z.dump.gpg --fetch files-20260117T031500Z.tar.gpg
```

Odtwarzanie na **nowym** serwerze (starego już nie ma): wdrożenie (`scripts/deploy.sh`), w
`/opt/olimpiada/.env` to samo `BACKUP_PASSPHRASE` z menedżera haseł, token Dysku jak w § 1.6
krok 2 (autoryzacja od nowa), potem powyższe `--list` / `--fetch`. Bez żadnego serwera paczki da
się też pobrać ręcznie z Dysku (§ 1.6.6).

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

### 2.4. Wersja porównawcza `dj.` (baza `olimpiada_djcms` i wolumen `djcms_media`)

Osobny przebieg `restore.sh` (nie łączy się z `--dump`/`--files`), ta sama zasada: nowa baza
i nowy katalog, nic „na miejsce”. Wymaga roli `olimpiada_djcms` w klastrze – na nowym serwerze
najpierw wdrożenie z `DJCMS_ENABLE=1` (§ 22.2), potem odtwarzanie.

```bash
cd /opt/olimpiada
./scripts/restore.sh --fetch djcms-db-20260117T031500Z.dump.gpg --fetch djcms-files-20260117T031500Z.tar.gpg   # gdy tylko poza serwerem
./scripts/restore.sh --dry-run --djcms-dump djcms-db-20260117T031500Z.dump.gpg --djcms-files djcms-files-20260117T031500Z.tar.gpg
./scripts/restore.sh --djcms-dump djcms-db-20260117T031500Z.dump.gpg --djcms-files djcms-files-20260117T031500Z.tar.gpg
```

Po przebiegu istnieje baza `olimpiada_djcms_restore_20260117_031500` (właścicielem jej i każdego
obiektu jest rola `olimpiada_djcms` – `createdb -O` i `pg_restore --role`; `CONNECT` dla PUBLIC
odebrany zaraz po `createdb`) i katalog `/opt/olimpiada-backups/djcms-media-restore-20260117_031500/`
(tylko root) z plikami redaktorów. `dj.` działa dalej na danych bieżących. Skrypt kończy się
wypisaniem poleceń podmiany z nazwami z tego serwera; ich postać:

```bash
docker compose stop djcms
docker compose exec -T db psql -U olimpiada -d postgres \
  -c 'ALTER DATABASE olimpiada_djcms RENAME TO olimpiada_djcms_przed_awaria' \
  -c 'ALTER DATABASE "olimpiada_djcms_restore_20260117_031500" RENAME TO olimpiada_djcms'
# wolumen: bieżąca zawartość do katalogu „…-przed”, potem pliki z kopii (właściciel uid 1000 = app)
docker run --rm -v olimpiada_djcms_media:/m \
  -v /opt/olimpiada-backups/djcms-media-restore-20260117_031500:/src:ro \
  -v /opt/olimpiada-backups/djcms-media-restore-20260117_031500-przed:/old postgres:18-alpine \
  sh -c 'cp -a /m/. /old/ && find /m -mindepth 1 -delete && cp -a /src/. /m/ && chown -R 1000:1000 /m'
docker compose up -d djcms
```

Bazę `olimpiada_djcms_przed_awaria` i katalog `…-przed` trzymaj tydzień, jak przy bazie głównej.
Sama baza bez plików jest dopuszczalna (`--djcms-dump` bez `--djcms-files`) – strony wrócą,
obrazy wgrane po ostatniej kopii plików nie.

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
| kopia przestała wyjeżdżać poza serwer | > 36 h od ostatniej kopii zdalnej, przy świeżej lokalnej | patrz § 1.6 (tylko gdy kopia zdalna kiedyś działała) |
| połączenia z Postgresem | ≥ 80 % / ≥ 95 % `max_connections` | patrz § 11.2 – „Alarm zajętości połączeń” |

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
docker compose exec web python manage.py db_connections     # połączenia z bazą: ile z ilu, kto trzyma
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
| `DEPLOY_SSH_KNOWN_HOSTS` | variable | linijki `known_hosts` serwera dla każdej nazwy wpisywanej w `target` (domena i/lub IP) – od v0.38.3 **wymagane** (§ 24.6) |

Dodatkowo załóż środowisko **`production`** (*Settings → Environments*) i włącz w nim
*Required reviewers*. Bez tego każdy z prawem zapisu w repozytorium wdraża produkcję jednym
kliknięciem. Środowisko musi istnieć z tą regułą **przed** pierwszym uruchomieniem: GitHub zakłada
nieistniejące środowisko sam, ale bez żadnej reguły.

Od v0.38.3 workflow ma `permissions: contents: read`, cel SSH (`target`) przechodzi przez zmienną
środowiskową i musi mieć postać `użytkownik@host` (inaczej workflow kończy się przed wdrożeniem),
a klucz hosta nie jest przyjmowany „przy pierwszym kontakcie”: krok „Klucz hosta” zapisuje
`DEPLOY_SSH_KNOWN_HOSTS` do `~/.ssh/known_hosts`, a `scripts/deploy.sh` dostaje
`SSH_STRICT_HOST_KEY_CHECKING=yes`. Na laptopie operatora domyślne zostaje `accept-new`.

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

Kolejność nie jest dowolna i od v0.38.4 (poprawki po audycie izolacji) **pilnuje jej kod**:
`create_competition` (komenda, ekran „Nowy konkurs” z § 6.5 i krok 6a wdrożenia) odmawia założenia
konkursu, dopóki którykolwiek **aktywny** konkurs ma wyłączone `memberships_enforced`. Powód: przy
wyłączonej fladze rolą jest globalna grupa Django, więc koordynator, recenzenci i komisja odwoławcza
istniejącego konkursu mieliby od pierwszej chwili role także w nowym (i odwrotnie). Odmowa przychodzi
przed jakimkolwiek zapisem, także przy `--dry-run`. Stąd kolejność: § 6.1 (pre-flight **i**
przełączenie flagi istniejącego konkursu), dopiero potem § 6.2.

Nowy konkurs dostaje `memberships_enforced` **włączone od założenia** (niezależnie od szablonu), a
koordynator wskazany przy zakładaniu – członkostwo w nim; dla niego pre-flight nie jest potrzebny.
Gdyby mimo to dwa aktywne konkursy liczyły role z grup (konkurs dopisany w `/admin/`, ponownie
włączony konkurs nieaktywny, ręcznie zdjęta flaga), zgłasza to kontrola systemowa `tenancy.E001`:
`docker compose exec -T web python manage.py check --database default`. Ta sama kontrola zatrzymuje
`migrate` (także start kontenera z `RUN_MIGRATIONS=1`); na czas naprawy: `migrate --skip-checks`.

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
- `info … członkostw bez grupy Django` — to nie jest rozjazd ról. Przed `scope_cms_access`
  (§ 6.7) oznacza brak dostępu do `/cms/` (panel redakcyjny wisi wtedy na uprawnieniach grupy
  `coordinator`, migracja `cms.0003_coordinator_permissions`) i naprawia się dodaniem do grupy
  w `/admin/ → Użytkownicy`. Po `scope_cms_access` dostęp do `/cms/` daje grupa `cms:<slug>`,
  do której wpisuje sam serwis, więc wpis ma znaczenie wyłącznie dla ról czytanych z grup.

Przy **jednym** konkursie w bazie komenda przyjmuje, że każdy członek globalnej grupy należy do
niego (bo innego nie ma). Od drugiego konkursu przypisuje wyłącznie osoby, które mają w konkursie
ślad: profil uczestnika, profil opiekuna szkolnego albo jakiekolwiek członkostwo. Członek grupy bez
takiego śladu nie jest przypisywany nigdzie — i to jest właściwa odpowiedź, bo zgadywanie dałoby
recenzentowi jednego konkursu wgląd w prace drugiego. Dlatego pre-flight i `--fix` robi się **przy
jednym konkursie**, zanim powstanie drugi.

Po zielonym wyniku (zero brakujących członkostw) **włącz `memberships_enforced` istniejącemu
konkursowi** – w panelu („Ustawienia konkursu” → „Role z członkostw w konkursie”) albo w `/admin/`
(§ 6.4) – i sprawdź logowanie jednej osoby z każdej roli. Bez tego kroku § 6.2 kończy się odmową:

```text
CommandError: Nie można założyć kolejnego konkursu: konkurs „kwantowa” ma wyłączony przełącznik
memberships_enforced, …
```

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
koordynatora w tym konkursie **i** dopisuje do grupy Django `coordinator`. Co to daje w `/cms/`,
zależy od tego, czy instalacja przeszła już `scope_cms_access` (§ 6.7): **przed** komendą grupa
`coordinator` ma prawa na całym drzewie stron i we wszystkich kolekcjach, więc nowy koordynator
redaguje wszystkie konkursy — dlatego drugi konkurs zakłada się **po** § 6.7. **Po** komendzie
grupa `coordinator` nie daje w `/cms/` niczego, a koordynator trafia do grupy `cms:<slug>` swojego
konkursu: widzi i edytuje wyłącznie jego strony, obrazy i dokumenty.

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
bash scripts/proxy_config.sh update && docker compose up -d web worker beat   # proxy: caddy reload
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
  jedna wartość, bez wdrożenia: flaga zostaje w kodzie jeden sezon właśnie po to. **Cofać wolno
  tylko w instalacji z jednym aktywnym konkursem** – przy dwóch wyłączona flaga to role jednego
  organizatora w panelach drugiego (kontrola `tenancy.E001`). Konkurs zakładany komendą albo
  z panelu dostaje ją włączoną sam (v0.38.4).
- **`competition_settings_page`** — pokazuje koordynatorowi ekran „Ustawienia konkursu”
  (`/coordinator/competition/`): marka, organizator, kontakt. Adresowania (witryna, identyfikator,
  tryb, prefiks) nie ma tam z założenia — zmiana domeny wymaga dostępu do serwera, więc należy do
  operatora platformy, nie do koordynatora.
- **`path_prefix_routing`** — ustawiana **konkursowi platformy** (witryny domyślnej, u nas Olimpiadzie
  Kwantowej), a nie konkursowi pod prefiksem: otwiera jego domenę dla konkursów adresowanych
  prefiksem ścieżki (`/<prefiks>/…`, § 6.6). Wyłączona znaczy, że pod tą domeną `/<prefiks>/` jest
  zwykłym adresem jej drzewa stron (czyli 404). `create_competition --path-prefix` włącza ją sama
  i mówi o tym w wydruku. Wyłączenie zdejmuje z domeny **wszystkie** konkursy pod prefiksem naraz.
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
- **`webinars`** — webinary w LiveKit (WEB-01): ekran koordynatora `/coordinator/webinars/`, strona
  odbiorców `/webinars/`, pokój `/webinars/<id>/room/`, link gościa `/zaproszenie/webinar/…`, webhook
  `/integrations/livekit/webhook/`. Wyłączona znaczy, że tych adresów **nie ma** (404). Działa dopiero
  z serwerem LiveKit w `.env` (§ 36); flaga bez serwera pokazuje koordynatorowi „serwer LiveKit nie
  jest skonfigurowany”, a odbiorcom nic.

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

Ekran podlega tej samej odmowie, co komenda (§ 6, wstęp): dopóki konkurs, z którego się zakłada
(albo którykolwiek inny aktywny), ma wyłączone `memberships_enforced`, podgląd i potwierdzenie
kończą się komunikatem w formularzu i niczego nie zapisują. Najpierw § 6.1.

#### Jednorazowe przygotowanie (operator, raz na instalację)

1. **DNS: rekord wieloznaczny.** `*` → adres serwera, typ A, u operatora strefy (dla
   olimpiadakwantowa.pl: home.pl). Gotowy wpis i wyjaśnienie, czego ten rekord **nie** rusza
   (`www`, `s3`, `meet`, `mail`, sama domena, SPF/DKIM/DMARC): `deploy/dns-olimpiadakwantowa.pl.md`,
   sekcja „Rekord z gwiazdką”. Nic nie dzieje się automatycznie — rekord wpisuje człowiek w panelu
   rejestratora.
2. **Przełącznik w `/opt/olimpiada/.env`:**

   ```dotenv
   PLATFORM_SUBDOMAINS=1
   CADDY_CONFIG_DIR=./caddy   # wdrożenie ustawia to samo (§ 23)
   ```

3. **Wdrożenie** (`scripts/deploy.sh root@<host>`) albo, na miejscu, samo przegenerowanie proxy
   (render + `caddy validate` + `caddy reload`, bez restartu proxy – § 23):

   ```bash
   cd /opt/olimpiada && bash scripts/proxy_config.sh update && docker compose up -d web
   ```

   Krok 4/8 wdrożenia generuje wtedy konfigurację Caddy'ego z opcją globalną
   `on_demand_tls { ask http://web:8000/internal/tls-allowed }` i blokiem `*.<domena>`
   (`tls { on_demand }`). Na koniec wdrożenie wypisuje przypomnienie o rekordzie DNS — tylko wtedy,
   gdy przełącznik jest włączony. Bloki nazw stałych (`www.`, sama domena, `meet.`, `monitor.`,
   blok S3, `dj.`) dostają wtedy `tls { key_type p256 }` – wartość domyślną, ale zapisaną jawnie:
   dzięki niej ich certyfikaty są zwykłe (wystawiane przy starcie i odnawiane ~30 dni przed końcem),
   a nie on-demand z bloku `*.`, bo `/internal/tls-allowed` tych nazw nie zna i by ich odmówił.
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

### 6.6. Konkurs pod prefiksem ścieżki (`/<prefiks>/` na domenie platformy)

Tryb dla konkursu, który czeka na własny DNS (`docs/UNIWERSALNY-ETAP-1.md` § 2.3): adresy
`https://olimpiadakwantowa.pl/fizyczna/…`, bez wpisu w `.env`, bez Caddy'ego i bez DNS-u. Od zmiany
„drzewo CMS konkursu pod prefiksem” (`CHANGELOG.md`, uwaga T43) taki konkurs ma pod prefiksem
**własne drzewo stron CMS** — stronę główną, menu, dokumenty, przekierowania i ustawienia witryny
swojej, a nie platformy.

```bash
docker compose exec -T web python manage.py create_competition   --slug fizyczna --name "Olimpiada Fizyczna" --domain olimpiadafizyczna.pl   --path-prefix fizyczna --from-template przedmiotowa   --coordinator-email koordynator@example.org --dry-run      # potem bez --dry-run
```

- `--domain` jest domeną, **na którą konkurs czeka** — trafia do jego witryny Wagtaila
  (`/cms/ → Ustawienia → Witryny`). Do czasu DNS-u nic pod nią nie odpowiada i nie trzeba jej nigdzie
  wpisywać.
- Komenda zakłada witrynę konkursu z **własną stroną główną** i sekcjami z szablonu (drzewo obok
  drzewa platformy, nie pod nim) i włącza konkursowi platformy `path_prefix_routing` (§ 6.4) —
  wydruk mówi „włączono mu teraz” albo „był już włączony”. Bez konkursu platformy (brak aktywnego
  konkursu witryny domyślnej) komenda odmawia.
- Prefiks nie może być slugiem strony drugiego poziomu platformy ani adresem aplikacji
  (`Competition.clean`), a redaktor platformy nie założy potem strony o slugu równym prefiksowi
  (`CMSPage.clean`).

**Sprawdzenie po założeniu** (dwie minuty, w przeglądarce):

1. `https://<platforma>/fizyczna/` — strona główna **Olimpiady Fizycznej** (tytuł, menu z jej sekcji;
   logo i „Strona główna” prowadzą pod `/fizyczna/`).
2. `https://<platforma>/fizyczna/zadania/` — sekcja z jej drzewa; `https://<platforma>/fizyczna/<slug
   strony platformy>/` — **404** (strony platformy nie przeciekają pod prefiks, i odwrotnie).
3. W `/cms/` strona konkursu → „Podgląd” i „Zobacz na żywo” — adres pod `/fizyczna/`.
4. Wylogowany: `https://<platforma>/fizyczna/me/` przekierowuje na `/fizyczna/login/?next=…`.

**Czego ten tryb nie daje** — i dlatego nie jest domyślny: sesja i CSRF są **wspólne** z platformą
(jeden host, ciasteczka na ścieżce `/`), więc zalogowanie się w jednym konkursie loguje w drugim
(konto i tak jest jedno, § 3.8 etapu 1). Linki w listach wysyłanych poza żądaniem prowadzą pod
`https://<platforma>/fizyczna/…`.

**Przejście na własną domenę** (gdy DNS zadziała): § 6.3 dla domeny z `--domain`, potem w `/admin/ →
Konkursy` `routing_mode` = „własna domena” (prefiks można zostawić pusty). Drzewo stron zostaje to
samo — witryna już ma tę domenę. Stare adresy `/fizyczna/…` pod domeną platformy przestają wtedy
działać: jeśli były rozesłane, dopisz w `/cms/ → Ustawienia → Przekierowania` witryny **platformy**
przekierowanie `/fizyczna` na `https://olimpiadafizyczna.pl/` (Wagtail nie przekierowuje całych
poddrzew — tylko adresy dopisane z nazwy).

---

### 6.7. Uprawnienia `/cms/` per konkurs i superkoordynator (wydanie „uprawnienia CMS per konkurs”)

Do tego wydania globalna grupa `coordinator` ma prawa Wagtaila na **korzeniu** drzewa stron
i kolekcji mediów (`cms.0003`), więc koordynator drugiego konkursu edytowałby strony i media
Olimpiady Kwantowej. Wydanie niczego nie przestawia samo: po wdrożeniu `/cms/` działa **dokładnie
jak przed nim**, dopóki operator nie wykona dwóch komend poniżej. Kolejność jest jedna.

**Wdrożenie → superkoordynator → zawężenie → sprawdzenie.**

```bash
# na serwerze, w /opt/olimpiada — po zwykłym scripts/deploy.sh
# 1. obecni koordynatorzy dostają rolę platformy (wszystkie konkursy, całe /cms/, bez /admin/)
docker compose exec -T web python manage.py superkoordynator --all-current-coordinators --dry-run
docker compose exec -T web python manage.py superkoordynator --all-current-coordinators
docker compose exec -T web python manage.py superkoordynator --list

# 2. zawężenie /cms/ — najpierw na sucho, wydruk przeczytać do końca
docker compose exec -T web python manage.py scope_cms_access --dry-run
docker compose exec -T web python manage.py scope_cms_access
```

**Dlaczego superkoordynator idzie pierwszy.** Polecenie organizatora: „obecny koordynator ma nim
zostać”. `--all-current-coordinators` nadaje rolę każdemu **aktywnemu** kontu, które dziś ma rolę
koordynatora (grupa `coordinator` albo członkostwo z tą rolą) i wypisuje listę; konta nieaktywne
pomija z powodem. Uruchomiona **przed** zawężeniem sprawia, że nikt z obecnych koordynatorów ani
przez chwilę nie widzi mniej niż dziś. Komenda jest idempotentna, każde nadanie ma wpis audytu
`accounts.super_coordinator.granted` (`/coordinator/audit/`).

**Co robi `scope_cms_access`** (jedna transakcja, idempotentna):

1. zakłada grupę `superkoordynator` z prawami do korzenia drzewa i kolekcji oraz komunikatów
   i ustawień serwisu,
2. zakłada każdemu konkursowi grupę `cms:<slug>` (prawa do poddrzewa jego witryny) i kolekcję mediów,
3. przenosi obrazy i dokumenty z **korzenia** kolekcji do kolekcji konkursu — przy jednym konkursie
   sama; przy kilku tylko z `--root-media-to <slug>` (bez tej opcji pliki zostają w korzeniu i widzi
   je wyłącznie superkoordynator). Ograniczenie widoczności korzenia („tylko zalogowani”) przechodzi
   na kolekcję konkursu. Adresy obrazów i dokumentów na stronach się nie zmieniają,
4. zabiera grupie `coordinator` wszystkie uprawnienia `/cms/` (wiersze stron i kolekcji, uprawnienia
   modelowe Wagtaila i `apps.cms`); sama grupa **zostaje** — jest rolą koordynatora,
5. wpisuje koordynatorów każdego konkursu do jego `cms:<slug>` (dalej robi to serwis sam, przy
   każdej zmianie roli).

**Kontrola „przed i po” jest w komendzie.** Dla każdego koordynatora bez `is_superuser` komenda
liczy macierz możliwości (każda strona poniżej korzenia × 12 czynności, każdy obraz i dokument ×
zmiana/usunięcie/wybór, wgrywanie, komunikaty, ustawienia witryn) przed zmianą i po niej. Przy
**jednym** konkursie (dzisiejsza produkcja) macierze mają być równe — inaczej komenda wycofuje
całość, wypisuje różnicę i kończy się kodem 1. Najczęstsza przyczyna: strona wisząca w drzewie poza
witryną konkursu (bezpośrednio pod korzeniem). Wtedy przenieś ją pod stronę główną albo skasuj
i uruchom ponownie. Wydruk kończy się wierszem `bez zmian <e-mail>: te same możliwości w /cms/` dla
każdego koordynatora — to jest dowód dla organizatora.

**Sprawdzenie po komendzie** (5 minut):

- koordynator (konto bez roli superkoordynatora, jeżeli takie jest) loguje się do `/cms/`: widzi
  stronę główną swojego konkursu, bibliotekę obrazów i dokumentów w kolekcji „<nazwa konkursu>”;
  wgranie obrazu trafia do tej kolekcji,
- superkoordynator widzi całe drzewo, wszystkie kolekcje, komunikaty i ustawienia serwisu;
  w `/coordinator/` ma w menu sekcję „Konkursy platformy”,
- `docker compose exec -T web python manage.py scope_cms_access --dry-run` wypisuje „bez zmian”.

**Przed komendą nic się nie psuje.** Sygnały i zawężenie w `/cms/` rozpoznają stan „przed” po tym,
że grupa `coordinator` ma jeszcze prawa do korzenia — i wtedy nie robią niczego. Wydanie można więc
wdrożyć i zostawić bez komend; skutkiem jest wyłącznie dzisiejsze zachowanie.

**Wycofanie.** Komenda nie jest migracją, więc nie cofa się jej `migrate`. W
`/cms/ → Ustawienia → Grupy → coordinator` zaznacz ponownie „Dostęp do panelu”,
uprawnienia obrazów i dokumentów, prawa do strony „Root” (dodawanie, edycja, publikacja, blokowanie,
odblokowanie) i do kolekcji „Root”. Od chwili, w której grupa znów ma prawa do korzenia, instalacja
zachowuje się jak przed komendą. Grup `cms:<slug>` i `superkoordynator` nie trzeba kasować.

**Superkoordynator na co dzień.**

```bash
docker compose exec -T web python manage.py superkoordynator --grant adres@example.org
docker compose exec -T web python manage.py superkoordynator --revoke adres@example.org
docker compose exec -T web python manage.py superkoordynator --list
```

Albo w `/admin/ → Użytkownicy`: zaznacz konta i wybierz akcję „Nadaj rolę superkoordynatora” /
„Odbierz rolę superkoordynatora” — akcje widzi wyłącznie superużytkownik. Obie drogi zapisują wpis
audytu. Dopisanie grupy `superkoordynator` ręcznie w formularzu konta też działa, ale **bez** wpisu
audytu — nie rób tego. Rola nie daje `/admin/` (to zostaje dla `is_superuser`) ani zarządzania
kontami, grupami, witrynami i kolekcjami w `/cms/`.

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
(`apps/tenancy/tests/test_i18n.py`), uprawnienia grupy `coordinator` w `/cms/` przed i po `scope_cms_access`
(`apps/cms/tests/test_cms_scope.py`, `apps/cms/tests/test_cms_permissions_per_competition.py`), 404 na `/setup/` przy skonfigurowanej instalacji
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
| 3 | `scoped_cms_permissions` | od wydania „uprawnienia CMS per konkurs” flaga **nie jest potrzebna**, jeżeli instalacja przeszła § 6.7 (po `scope_cms_access` każdy konkurs jest zawężony). Przed § 6.7 flaga daje konkursowi grupę `cms:<slug>` **obok** grupy globalnej — czyli nic nie zawęża; nie zapalaj jej zamiast § 6.7 |
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
(obraz ma 3.12.14; od § 21 – 3.14) i **PostgreSQL-a ≥ 15** (compose stawiał wtedy `postgres:16-alpine`; od § 19 – `postgres:18-alpine`).
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
(`pytest-split`), a wymaganym statusem jest jeden: „pytest (wynik zbiorczy)”. Od 25.09.2026 każdy
shard biegnie dodatkowo równolegle (`-n auto`, pytest-xdist, własna baza na worker), a podział jest
na ciągłe kawałki zbioru według zmierzonych czasów (`backend/.test_durations`,
`--splitting-algorithm duration_based_chunks`). Postgres CI dostaje przed testami
`max_locks_per_transaction = 256` (jak produkcja i dev) i wyłączony `fsync`.

Uruchamianie lokalne (szybka pętla, xdist, markery), odświeżanie pliku czasów i zasady dopisywania
testów: **[`docs/TESTY.md`](TESTY.md)**.

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

### 11.2. Budżet połączeń z Postgresem i pula połączeń

**Pula (od wydania po v0.35.0).** `web` bierze połączenia z puli `psycopg_pool`
(`DATABASES["default"]["OPTIONS"]["pool"]`, `backend/config/settings/base.py`, reguły doboru
w `backend/config/dbpool.py`). Pula jest **jedna na proces** gunicorna i wspólna dla jego wątków:
połączenie wraca do niej na końcu każdego żądania, proces nigdy nie otworzy więcej niż
`DB_POOL_MAX_SIZE`, a bezczynny nadmiar ponad `DB_POOL_MIN_SIZE` pula zamyka sama, stopniowo – jedno
połączenie na każde 10 minut, w których nie było potrzebne (po szczycie ruchu proces wraca z 4 do 1
w ok. pół godziny; granicę górną pula trzyma zawsze).
To jest różnica wobec `CONN_MAX_AGE`, które ogranicza liczbę połączeń tylko pośrednio (tyle, ile
żyje wątków, i pod warunkiem, że każdy posprząta) – dokładnie to założenie pękło w incydencie
z 09.09.2026 (92 bezczynne połączenia, „too many clients already”).

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `DB_POOL` | `1` (w procesach Celery i w obrazie bez `psycopg_pool`: `0`) | pula włączona; compose ustawia `DB_POOL=0` dla `worker` i `beat` |
| `DB_POOL_MAX_SIZE` | `WEB_THREADS` (4) | najwięcej połączeń na proces `web`; mniej niż wątków = wątki czekają |
| `DB_POOL_MIN_SIZE` | `1` | połączenia trzymane bez ruchu, na proces |
| `DB_POOL_TIMEOUT` | `10` | sekundy czekania na wolne połączenie, potem błąd 500 (licznik 5xx watchdoga) |
| `DB_CONN_MAX_AGE` | `60` | **tylko** procesy bez puli (`worker`, `beat`); przy puli ignorowane |
| `DB_APPLICATION_NAME` | per usługa w compose | `pg_stat_activity.application_name`: `olimpiada-web`, `olimpiada-worker`, `olimpiada-beat` |

**Pula i `CONN_MAX_AGE` wykluczają się**: Django przy puli wymaga `CONN_MAX_AGE=0` (inaczej
`ImproperlyConfigured` przy pierwszym zapytaniu – każda strona 500). Ustawienia robią to same:
przy `DB_POOL=1` zero jest wpisywane niezależnie od `DB_CONN_MAX_AGE`.

**Dlaczego `worker` i `beat` bez puli.** Pula ma wątki tła, a wątki nie przeżywają `fork()` –
Celery 5.6 wie o tym i w workerze `prefork` **zamyka całą pulę przed i po każdym zadaniu**
(`DjangoWorkerFixup._close_database`). Pula w workerze to więc otwarcie i zamknięcie połączeń przy
każdym zadaniu – drożej niż jedno zwykłe połączenie. `beat` jest jednym wątkiem z jednym
połączeniem, więc pula nie miałaby tam czego współdzielić. Poza `DB_POOL=0` w compose ustawienia
rozpoznają proces Celery same (`celery …` w `sys.argv`) – nowa usługa Celery bez tej zmiennej też
nie dostanie puli. Tak samo obraz **bez** pakietu `psycopg_pool` (nieprzebudowany po aktualizacji):
domyślnie chodzi wtedy bez puli, jak przed nią, zamiast dawać 500 na pierwszym zapytaniu – dlatego
po wdrożeniu sprawdź `import psycopg_pool` (niżej).

**Budżet** przy `max_connections=100` (domyślne Postgresa – `docker-compose.yml` go nie zmienia,
bo zmiana polecenia usługi `db` to restart bazy przy wdrożeniu):

| Usługa | Wzór | Połączenia (maks.) | W spoczynku |
|---|---|---|---|
| `web` | `WEB_WORKERS × DB_POOL_MAX_SIZE` = 4×4 | 16 | `WEB_WORKERS × DB_POOL_MIN_SIZE` = 4 |
| `worker` | `CELERY_CONCURRENCY` (proces główny bazy zwykle nie trzyma) | 2–3 | 0–2 |
| `beat` | proces jednowątkowy | 1 | 0–1 |
| `manage.py` (entrypoint, shell, komendy operatora) | własna pula na proces | 1–2 na proces | 0 |
| kopia zapasowa (`pg_dump`), `psql` dyżurnego | – | 1–2 | 0 |
| `superuser_reserved_connections` | ustawienie Postgresa | 3 | 3 |
| **razem** | | **ok. 25–27** | **ok. 8–10** |

Zapas do 100 jest świadomie duży: chwila nakładania się starego i nowego procesu przy rotacji
`--max-requests` albo przy wdrożeniu nie może wypchnąć aplikacji z puli. Podnosząc `WEB_WORKERS`
albo `WEB_THREADS`, licz `WEB_WORKERS × DB_POOL_MAX_SIZE`: powyżej ok. 6×8 (48) budżet zbliża się
do progu ostrzeżenia (80) i trzeba podnieść `max_connections` razem z nim.

#### Alarm zajętości połączeń

`backend/apps/core/dbconnections.py`: jedno zapytanie do `pg_stat_activity` (połączenia klientów
całego serwera, pogrupowane po `application_name` i stanie) plus `max_connections`, wynik
buforowany 30 s we wspólnym cache'u – dowolnie częste pukanie w `/healthz/` to najwyżej jedno
zapytanie na pół minuty.

| Gdzie | Co widać | Dla kogo |
|---|---|---|
| `/healthz/` | `"db_connections"`: `ok` / `warn` / `critical` / `unknown` – **kod HTTP się nie zmienia** | orkiestrator, monitor zewnętrzny |
| `/status.json` | to samo pole, przedostatni klucz (za nim `backup_offsite`, § 1.5); **nie** wpływa na `"status"` | monitor zewnętrzny (§ 3.1) |
| list watchdoga (`ALERT_EMAILS`) | liczby, progi, podział na usługi i stany | dyżurny |
| `manage.py db_connections` | to samo co list, odczyt świeży; kod wyjścia 0/1/2/3 = ok/warn/critical/brak odczytu | operator na serwerze |

Publiczne odpowiedzi niosą **wyłącznie poziom**: liczba połączeń i nazwy usług mówiłyby obcemu,
ile brakuje do położenia serwisu. Progi: `DB_CONNECTIONS_WARN_PERCENT` (80) i
`DB_CONNECTIONS_CRITICAL_PERCENT` (95), włącznie. Ostrzeżenie i stan krytyczny mają **osobne**
klucze wyciszenia (`db-connections:warn`, `db-connections:critical`), więc eskalacja z 80 % na 95 %
w ciągu godziny daje drugi list. `unknown` (odczyt się nie udał) nie jest osobnym alarmem –
niedziałającą bazę zgłasza już `service:database`.

**Jak czytać alarm i co zrobić:**

```bash
# na serwerze, w /opt/olimpiada
docker compose exec web python manage.py db_connections
```

- trzyma **`olimpiada-web`** i jest go więcej niż `WEB_WORKERS × DB_POOL_MAX_SIZE` – coś omija pulę
  albo działa drugi komplet kontenerów `web` (np. zawieszone wdrożenie): `docker compose ps`,
  potem `docker compose restart web`,
- trzyma **`olimpiada-worker`** / **`olimpiada-beat`** – `docker compose restart worker beat`,
- dużo **`idle in transaction`** – żądanie albo zadanie trzyma transakcję otwartą (błąd w kodzie):
  logi `web`/`worker` z tej samej minuty, restart zwalnia połączenia doraźnie,
- dużo **`(bez nazwy)`** – nie aplikacja: ręczne `psql`, kopia zapasowa, narzędzie spoza compose'a;
  szczegóły: `docker compose exec db psql -U "$POSTGRES_USER" -c "select pid, usename, client_addr,
  backend_start, state from pg_stat_activity where application_name = ''"`,
- budżet po prostu wyrósł (podniesione `WEB_WORKERS`/`WEB_THREADS`) – obniż je albo podnieś
  `max_connections` (restart `db`, poza godzinami oddawania prac).

**Sprawdzenie po wdrożeniu**, że pula działa (liczby dla domyślnych 4×4):

```bash
docker compose exec web python -c "import psycopg_pool"    # obraz ma psycopg[pool]
docker compose exec web python manage.py db_connections    # olimpiada-web: od 4 do 16 (+1–2 samej komendy)
curl -s https://<domena>/healthz/                          # … "db_connections": "ok"
```

### 11.3. Rollback

`WEB_WORKERS` i `WEB_THREADS` to zmienne `.env` – awaryjny powrót do mniejszej współbieżności (np.
podejrzenie, że nowa wartość przeciąża bazę albo maszynę) nie wymaga wdrożenia:

```bash
# na serwerze, w /opt/olimpiada
sed -i 's/^WEB_WORKERS=.*/WEB_WORKERS=2/; s/^WEB_THREADS=.*/WEB_THREADS=2/' .env
docker compose up -d web
```

Tak samo bez wdrożenia wyłącza się **pulę połączeń** (§ 11.2) – np. przy podejrzeniu, że to ona
zwraca błędy `PoolTimeout`/500 pod obciążeniem. `web` wraca wtedy do trwałych połączeń per wątek
(`DB_CONN_MAX_AGE`, domyślnie 60 s), czyli do stanu sprzed puli:

```bash
# na serwerze, w /opt/olimpiada
echo 'DB_POOL=0' >> .env
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
**Po przejściu na PostgreSQL 18 (§ 19.4) nie wdrażaj w ten sposób kodu sprzed v0.37.0** – jego
`docker-compose.yml` nie zna `POSTGRES_IMAGE`/`POSTGRES_VOLUME` i postawi 16 na starym `pg_data`
(stan sprzed przejścia, zapisy z 18 znikają z widoku; po sprzątnięciu `pg_data` z § 19.6 – pusta
baza). Starszą wersję aplikacji uruchamia się wtedy wyłącznie przez tag obrazu (`APP_VERSION`,
wyżej) albo `WEB_IMAGE=…` wdrażane kodem v0.37.0 lub nowszym.

## 12. Wyszukiwarka szkół: rozszerzenie `pg_trgm` (v0.31.0)

Migracja `schools.0006_pg_trgm_search_indexes` wymaga rozszerzenia PostgreSQL **`pg_trgm`**
(indeksy GIN pod `search_text`/`city_search`, klasa operatorów `gin_trgm_ops` – zastąpiły trzy
indeksy B-tree bez ani jednego skanu na produkcji, patrz `docs/CHANGELOG.md` v0.31.0). Rozszerzenie
zakłada sama migracja (`django.contrib.postgres.operations.TrigramExtension`,
`CREATE EXTENSION IF NOT EXISTS pg_trgm`) — nic nie trzeba robić ręcznie przed wdrożeniem, o ile
spełniony jest jeden warunek środowiska:

- **obraz bazy ma zawierać `pg_trgm`.** Obraz `postgres:18-alpine`, którego używa
  `docker-compose.yml` i produkcja (§ 19; wcześniej `postgres:16-alpine` – oba mają `pg_trgm` 1.6),
  zawiera go w pakiecie `contrib` domyślnie — nie trzeba doinstalowywać żadnego pakietu systemowego,
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
  `log` – podpisane adresy nie lądują w logu dostępu. Od v0.38.3 blok odmawia (404) API MinIO spod
  `/minio/*` i dokłada `nosniff`, HSTS oraz CSP `sandbox` (§ 24.5) – ścieżek bucketów, `Range`,
  CORS ani `ETag` to nie dotyka (`scripts/tests/s3_proxy_test.sh`), a CSP odpowiedzi nie dotyczy
  `PUT` części ani `<video>` (CSP działa na dokument budowany z odpowiedzi, nie na `fetch`).
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
uv pip install --system-certs --target sdk --python-platform x86_64-manylinux_2_28 --python-version 3.14 \
  "anthropic>=1.8,<2" "openai>=3.19,<4" "google-genai>=2.25,<3"
docker run --rm --user root -v "$PWD/backend:/app" -v "$PWD/sdk:/sdk:ro" -w /app --network olimpiadaclade_internal \
  -e DATABASE_URL=postgres://…@db:5432/olimpiada_ai --entrypoint "" olimpiada/web:dev \
  sh -c 'echo /sdk > /opt/venv/lib/python3.14/site-packages/zz_sdk.pth; pytest -q apps/ai_grading'
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

### 18.4. Uzupełnienie: ułamki w rubrykach i teście (po v0.35.0)

Dwie migracje, obie **bezstratne** i na małych tabelach – bez okna serwisowego:

| Migracja | Tabela, kolumny | Zmiana | Rząd wielkości | Czas |
|---|---|---|---|---|
| `grading.0012_rubric_decimal_points` | `grading_rubriccriterion.max_points` | `smallint` → `numeric(7,2)`; więz `…_max_points_positive` z `>= 1` na `> 0` (ta sama nazwa) | dziesiątki – setki wierszy | pomijalny |
| `ai_grading.0003_points_precision` | `ai_grading_aiassessment.proposed_points`, `max_points` | `numeric(6,2)` → `numeric(7,2)` (jak maksimum zadania) | setki – tysiące | < 1 s |

Każda istniejąca wartość zostaje tą samą liczbą (4 → 4.00, 4.50 → 4.50). Punkty za kryteria
zapisanych recenzji (`grading_review.rubric`, JSON) **nie są** przepisywane – liczby całkowite zostają
w nich liczbami całkowitymi. Żadnej flagi, zmiany `.env`, zadania beat ani nowej zależności.

**Zmiany zachowania, które warto zapowiedzieć organizatorowi**:

- w etapie z **dowolnymi wartościami** wynik **testu online** wchodzi do tabeli wyników co do 0,01
  (dotąd zawsze do pełnych punktów). Etapy „tylko ze skali” – bez zmian. Tabele **już ogłoszone**
  (snapshoty) się nie zmieniają; zmienia się dopiero kolejne przeliczenie etapu testowego w trybie
  dowolnym – sprawdź przed wdrożeniem, czy taki etap jest w toku:

  ```bash
  docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT s.id FROM competitions_stage s JOIN competitions_scoringscale sc ON sc.stage_id = s.id WHERE s.format = 'QUIZ' AND sc.free_values;"
  ```

- kwota za pytanie testu z oceną częściową jest zaokrąglana **połówka w górę** (0,125 → 0,13; dotąd
  bankierskie 0,12). Zapisane wyniki podejść się nie zmieniają; „Przelicz punkty” po wdrożeniu może
  przesunąć wynik podejścia o 0,01 – wpis audytu `quiz.regraded` poda liczbę zmienionych podejść,
- eksport CSV wyników testu pisze punkty bez zbędnych zer (`7.5`, `3`), a eksport danych uczestnika
  – punkty sugestii AI jako liczby JSON (`6`, `4.5`) zamiast tekstu („6.00”).

Po wdrożeniu:

```bash
docker compose exec -T web python manage.py showmigrations grading ai_grading | tail -n 3
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT table_name, column_name, numeric_precision, numeric_scale FROM information_schema.columns WHERE (table_name, column_name) IN (('grading_rubriccriterion','max_points'),('ai_grading_aiassessment','proposed_points'),('ai_grading_aiassessment','max_points'));"
```

Oczekiwane: trzy wiersze `7`, `2`. **Rollback** (`migrate grading 0011`, `migrate ai_grading 0002`)
wolno wykonać, dopóki żadne kryterium nie ma ułamkowego maksimum i żadna sugestia AI nie przekracza
9 999,99 pkt – inaczej baza zaokrągli maksimum kryterium albo odmówi zawężenia kolumny:

```bash
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM grading_rubriccriterion WHERE max_points <> trunc(max_points);"
```

musi dać `0`.

## 19. PostgreSQL 16 → 18 (zrzut i odtworzenie, `scripts/upgrade_postgres18.sh`)

Usługa `db` przechodzi z `postgres:16-alpine` (produkcja: 16.15) na `postgres:18-alpine`
(18.6 w chwili przejścia). Aplikacja nie zmienia się ani o linijkę: pełny zestaw testów przechodzi
na 18 bez zmian, migracje od zera też. Zmienia się **gdzie leżą dane** i **jak na nie przejść** –
stąd osobny skrypt i ten rozdział zamiast zwykłego wdrożenia.

### 19.1. Co się zmienia

- **Nowy wolumen `pg18_data`, montowany w `/var/lib/postgresql`.** Obraz 18 ma PGDATA zależne od
  wersji (`/var/lib/postgresql/18/docker`) i deklaruje VOLUME `/var/lib/postgresql` – nie
  `/var/lib/postgresql/data` jak 16 (sprawdzone na obrazie: `docker image inspect postgres:18-alpine`,
  i w dokumentacji obrazu). Pliki klastra 16 są dla serwera 18 nieczytelne, więc przejście to zrzut
  i odtworzenie do **nowego** wolumenu; stary `pg_data` zostaje nietknięty jako droga powrotu.
  Montaż całego `/var/lib/postgresql`, a nie `…/18/docker`: przy następnej wersji głównej dane 19
  wylądują obok 18 na tym samym wolumenie, co pozwoli na `pg_upgrade --link`, gdy baza urośnie za
  duża na zrzut.
- **Obraz i wolumen są parametrami compose** (`POSTGRES_IMAGE`, `POSTGRES_VOLUME` w `.env`). Bez nich
  – 18 na `pg18_data`. Przypięcie do 16 (`POSTGRES_IMAGE=postgres:16-alpine`,
  `POSTGRES_VOLUME=pg_data:/var/lib/postgresql/data`) wpisuje `scripts/deploy.sh` (krok 4/8 – pierwsza czynność, przed
  buildem obrazu; przez `scripts/upgrade_postgres18.sh --pin-if-needed`) na serwerze, który ma wolumen `pg_data` i nie ma
  `pg18_data` – **samo wdrożenie tej wersji niczego w bazie nie zmienia**, kontener `db` nie jest
  nawet odtwarzany. Bez tego zabezpieczenia pierwsze `up -d db` postawiłoby pustą bazę 18,
  a entrypoint `web` zmigrowałby ją od zera – serwis wstałby pusty.
- **Porządek sortowania bez zmian.** Obraz alpine (musl) nie ma locale libc: `--locale=pl_PL.utf8`
  z `POSTGRES_INITDB_ARGS` przechodzi, ale porównanie tekstu jest w obu wersjach bajtowe (kolejność
  „Ala, Lublin, Zz, Ząb, ala, cebula, zebra, ó, ćma, Łódź, żaba” – identyczna na 16 i 18,
  sprawdzone). `upper()`/`lower()` polskich liter działa w obu tak samo. Aplikacja nie używa
  jawnych collation (ICU jest w obrazie, ale nieużywane). Indeksy są budowane od nowa przy
  odtwarzaniu, więc nie ma ryzyka indeksu zbudowanego pod inną kolejność.
- **Sumy kontrolne stron danych włączone.** PostgreSQL 18 domyślnie robi `initdb --data-checksums`
  (16 – nie). Klaster po przejściu ma `data_checksums = on`: cicha korupcja pliku na dysku kończy
  się błędem zapytania zamiast złej odpowiedzi. Koszt CPU przy tej bazie pomijalny.
- **Hasła: SCRAM, bez zmian.** Konto aplikacji ma hasło w SCRAM-SHA-256 od początku (domyślne
  `password_encryption` od PostgreSQL 14), `pg_hba.conf` obrazu 18 to nadal
  `host all all all scram-sha-256`. Ostrzeżenia 18 o wycofywaniu MD5 nas nie dotyczą.
- **Rozszerzenia:** jedyne to `pg_trgm` (`schools.0006`, § 12) – wersja 1.6 w obu obrazach.
- **Narzędzia kopii zapasowych.** `pg_dump`/`pg_restore` biegną **w kontenerze `db`**
  (`backup.sh`, `restore.sh`, `deploy.sh` krok 4a, `pull_prod_data.sh`) – klient ma więc zawsze
  wersję serwera; obraz aplikacji nie ma `postgresql-client` i nie potrzebuje. `backup_verify.sh`
  stawia tymczasowy Postgres w wersji z `.env` (`POSTGRES_IMAGE`, domyślnie `postgres:18-alpine`)
  i montuje tmpfs tam, gdzie obraz deklaruje VOLUME (16 i 18 mają to w innym miejscu).
  **Uwaga dla `pull_prod_data.sh`:** zrzut `-Fc` z produkcji na 18 ma format archiwum, którego
  `pg_restore` 16 nie przeczyta („unsupported version (1.16) in file header”) – lokalne środowisko
  musi być na 18 (§ 19.7) albo odtwarzać klientem 18.
- **CI**: usługa `postgres` w `.github/workflows/ci.yml` – `postgres:18-alpine`.

### 19.2. Dlaczego zrzut i odtworzenie, a nie `pg_upgrade`

Baza ma ok. 1,4 MB w zrzucie gzip (kilkadziesiąt MB na dysku, ~175 tabel). `pg_dump` + `pg_restore`
trwa przy tym rozmiarze sekundy, a daje rzeczy, których `pg_upgrade` nie daje:

- stary klaster zostaje **nietknięty** (`pg_upgrade --link` go unieważnia; bez `--link` i tak trzeba
  dwóch kopii) – wycofanie to przestawienie dwóch zmiennych, nie odtwarzanie,
- nie wymaga binariów **obu** wersji w jednym kontenerze (oficjalny obraz ma jedną; `pg_upgrade`
  w Dockerze to osobny obraz społeczności i ręczne montowanie obu katalogów),
- klaster 18 powstaje od nowa z domyślnymi ustawieniami 18 (sumy kontrolne – `pg_upgrade` wymaga
  zgodności ustawienia z klastrem 16, czyli przeniósłby „off”),
- indeksy i tabele są zbudowane od nowa (bez rozdęcia), a porównanie liczby wierszy **każdej** tabeli
  16 ↔ 18 jest prostym, pełnym dowodem, że nic nie zginęło.

Ceną jest przestój na czas zrzutu i odtworzenia – przy tej bazie pomijalny w porównaniu ze startem
`web`. `pg_upgrade --link` ma sens dopiero przy bazie rzędu dziesiątek GB; układ wolumenu
(`/var/lib/postgresql`) jest na to przygotowany na przyszłość.

Zrzut do odtworzenia robi **klient 18** (`pg_dump` z obrazu 18 łączący się z serwerem 16 w sieci
compose) – tak zaleca dokumentacja PostgreSQL. Obok powstaje zrzut **klientem 16** (w kontenerze
`db`), bo tylko ten przeczyta `pg_restore` 16 przy ewentualnym powrocie.

### 19.3. Próba generalna (lokalnie, 25.09.2026)

Izolowany projekt compose (`pg18rh`, własne podsieci, bez `clamav`/`mail`/`proxy`) z kodem tej
gałęzi, produkcyjnymi ustawieniami (`config.settings.production`), bazą 16 na `pg_data`
zmigrowaną i zasianą (`seed_cms`, `seed_regulamin`, `seed_edition_kwantowa`, `seed_schools`,
`seed_demo`: 175 tabel, 9 634 wiersze, 32 MB):

1. `--pin-if-needed` wpisał przypięcie, drugie wywołanie – „bez zmian”, `docker compose up -d` nie
   odtworzył kontenera `db` (ten sam identyfikator, nadal 16.15),
2. `backup.sh` + `backup_verify.sh` na przypiętej 16 – test odtwarzania na `postgres:16-alpine` OK,
3. `--dry-run` – kontrole wstępne i plan, nic nie zmienione,
4. **awaria wstrzyknięta** (`PG18_IMAGE=postgres:17-alpine`: nowa baza nie wstaje w kroku 5) –
   skrypt sam wrócił na 16, skasował utworzony w tym przebiegu `pg18_data`, podniósł aplikację,
5. przejście właściwe: wszystkie 7 porównań zgodne (175 tabel, 157 sekwencji, rozszerzenia, role,
   obiekty), `/status.json` → `ok` (database/cache/storage/queue: true), `db_connections` → `ok`;
   **przerwa 1 min 8 s**, cały skrypt z kontrolami wstępnymi 109 s,
6. na 18: `manage.py check`, `showmigrations` (0 niezastosowanych), `makemigrations --check`,
   `db_connections`, `scope_cms_access --dry-run`, `check_memberships`, wyszukiwarka szkół
   (plan zapytania używa `schools_search_trgm_idx`), strony publiczne i panel,
7. `backup.sh` + `backup_verify.sh` na 18 (tymczasowy `postgres:18-alpine`) – OK,
8. konto założone na 18, potem `--rollback --yes` (cały przebieg 42 s) → 16 na `pg_data`
   ze stanem sprzed przejścia (konta nie ma – zgodnie z § 19.5, jest w zrzucie
   `pg18-rollback-*.dump`), aplikacja zdrowa; drugie `--rollback --yes` – „nic do zrobienia”;
   ponowne przejście odmówiło bez `--recreate-pg18-volume`, z flagą – przeszło (przerwa 47 s);
   kolejne uruchomienie – „przejście zostało zrobione wcześniej” (kod 0).

Niezależnie: pełny zestaw testów na `postgres:18-alpine` 18.6 z tym samym `command` i locale co
produkcja – **6152 passed, 0 failed**; `migrate` od zera (378 migracji, `pg_trgm` 1.6), `check`,
`makemigrations --check` – czyste.

### 19.3a. Próba kolejności ze stroną prac technicznych (`scripts/tests/maintenance_pg18_rehearsal.sh`)

Wymóg organizatora (25.09.2026): na 18 trafia wyłącznie zrzut zrobiony po włączeniu strony prac
technicznych i zatrzymaniu aplikacji. Skrypt próby stawia osobny projekt compose (`olimpmaint`,
własne podsieci, proxy na `127.0.0.1:18443`) z bazą 16, uruchamia **pisarza** (zapis znacznika do
bazy co ~0,2 s aż do pojawienia się flagi `maintenance/on`) i **sondę** (`/healthz/` i `/` przez
proxy co ~0,5 s), po czym puszcza `upgrade_postgres18.sh` i sprawdza:

- kolejność etapów z `timeline.txt` (strona → stop → zero klientów → zrzut 16 → zrzut 18 →
  odtworzenie → porównanie → przełączenie → healthy → kontrole → strona wyłączona),
- start obu zrzutów **po** włączeniu strony, SHA-256 odtworzonego pliku = zapisany w `dumps.sha256`,
- **każdy** potwierdzony zapis pisarza jest w 18, w tym ostatni sprzed włączenia strony,
- ani jednej odpowiedzi 502/504; w przerwie 503 + JSON `maintenance` i 503 + strona HTML; strona
  tylko między włączeniem a wyłączeniem; na końcu 200 i flaga zdjęta,
- potem `--rollback --yes`: ta sama kolejność (strona → stop → zrzut 18 → 16 → kontrole → strona
  wyłączona), baza 16 z przypięciem, wszystkie zapisy sprzed przejścia na miejscu, 0 × 502.

Wynik lokalny (25.09.2026, 176 tabel): 44 z 44 kontroli (dwa pełne przebiegi przejścia, jeden
z wycofaniem); ostatni zapis 0,02–0,63 s przed włączeniem strony jest w 18 (26 zapisów, 0
brakujących); zrzut do odtworzenia zaczęty ~15 s po włączeniu strony; ~216 próbek proxy na
przebieg – ~37 × 200, ~180 × 503, 0 × 502; przerwa 2 min 2 s (aplikacja healthy po 49 s, reszta to
czekanie na `status=ok` kolejki); wycofanie ok. 40 s. Uruchomienie:
`scripts/tests/maintenance_pg18_rehearsal.sh` (`--keep` zostawia stos, `REHEARSAL_WEB_IMAGE=`
wskazuje obraz aplikacji). W Git Bashu brak bazy stref czasowych – planowana godzina końca jest
wtedy pomijana (na serwerze Ubuntu: CET/CEST).

### 19.4. Przejście na produkcji

**Kiedy:** poza godzinami zgłoszeń i oceniania, nie w oknie kopii nocnej (3:15, w niedzielę też
4:40 – skrypt odmówi, gdy kopia trwa). Dzień wcześniej koordynator może wystawić komunikat na
stronie („przerwa techniczna ok. 5 minut o …”). Przez czas przerwy proxy podaje stronę
**„Prace techniczne”** (503, § 20) z planowaną godziną końca – włącza ją i wyłącza sam skrypt.

**Kolejność jest wymuszona** (wymóg organizatora z 25.09.2026): na 18 trafia **wyłącznie zrzut
zrobiony po włączeniu strony prac technicznych i zatrzymaniu aplikacji** – i dokładnie ten zrzut.
Wcześniejsze kopie (nocna, `pre-deploy-*` z wdrożenia) są dobre jako zabezpieczenie, ale nigdy
nie są odtwarzane na 18. Skrypt przerywa przebieg, gdy którykolwiek warunek nie jest spełniony:

1. kontrole wstępne – serwis działa normalnie (w tym: proxy widzi katalog strony, jest przepustka
   `MAINTENANCE_BYPASS_TOKEN`),
2. **strona prac technicznych WŁĄCZONA** (`scripts/maintenance.sh on`; proxy potwierdza flagę),
3. stop `web`/`worker`/`beat`; w `pg_stat_activity` nie może zostać żaden klient poza skryptem –
   30 s na rozejście się, potem `pg_terminate_backend` maruderów, a jeśli ktoś wciąż się łączy –
   przerwanie,
4. **zrzuty końcowe** (ten do odtworzenia klientem 18 + droga powrotu klientem 16): przed każdym
   ponowna kontrola „strona włączona, aplikacja stoi, zero klientów”; znacznik startu zrzutu musi
   być późniejszy niż włączenie strony; SHA-256 i rozmiar do `dumps.sha256`; ponowny stan 16 po
   zrzutach musi być identyczny ze stanem sprzed nich,
5. odtworzenie **tego** zrzutu na 18 – SHA-256 sprawdzany tuż przed `pg_restore`,
6. porównanie liczby wierszy każdej tabeli (i sekwencji, ról, obiektów) z **zatrzymaną** 16, do
   której nikt już nie może pisać,
7. przełączenie, start aplikacji, kontrole od środka i **przez proxy z przepustką operatora**,
8. **strona prac technicznych WYŁĄCZONA**.

Każdy etap ma znacznik czasu w `timeline.txt` katalogu przebiegu. Po błędzie od kroku 2 strona
**zostaje włączona** (skrypt mówi to głośno ramką `!!!`) – operator sprawdza serwis z przepustką
i wyłącza ją sam. Dowód kolejności na prawdziwym stosie: `scripts/tests/maintenance_pg18_rehearsal.sh`
(§ 19.3a).

**Szacowany przestój: 2–5 minut** (próba lokalna: 1 min 8 s; na produkcji dłuższy start `web`
– entrypoint robi `migrate` i `collectstatic`, a VPS traci część CPU na rzecz hosta, § 11 / notatka
o kradzieży vCPU). Zrzut i odtworzenie tej bazy to kilka–kilkanaście sekund.

Kolejność (z komputera operatora, potem na serwerze):

```bash
# 0. Wdrożenie kodu z PostgreSQL 18 – NIE zmienia bazy (krok 4/8 wpisuje przypięcie do 16)
SSH_KEY=~/.ssh/olimpiada_deploy scripts/deploy.sh root@169.58.242.197
#    w logu kroku 4/8: „PostgreSQL: dane są na 16 (wolumen olimpiada_pg_data) … przypinam 16 w .env”

ssh -i ~/.ssh/olimpiada_deploy root@169.58.242.197
cd /opt/olimpiada
scripts/upgrade_postgres18.sh --status       # przypięcie 16, pg_data jest, pg18_data brak, 16.15
scripts/upgrade_postgres18.sh --dry-run      # kontrole wstępne + plan; kod 0 = można
scripts/maintenance.sh status                # proxy widzi katalog strony, przepustka ustawiona
docker pull postgres:18-alpine               # (robi to też skrypt, przed przerwą)

# 1. Przejście (przerwa = strona „Prace techniczne” od kroku 1/9 do 9/9)
MAINTENANCE_MINUTES=10 scripts/upgrade_postgres18.sh
#    koniec: „Gotowe: PostgreSQL 18.x na wolumenie olimpiada_pg18_data.” + czas przerwy
#    ślad: /opt/olimpiada-backups/pg18-upgrade-<data>/{upgrade.log,timeline.txt,dumps.sha256}

# 2. Po przejściu
scripts/upgrade_postgres18.sh --status       # bez przypięcia, 18.x
curl -fsS https://olimpiadakwantowa.pl/status.json
docker compose exec -T web python manage.py db_connections
scripts/backup.sh && scripts/backup_verify.sh   # pierwsza kopia z 18 i test jej odtworzenia
```

Co robi skrypt (każdy krok drukuje polecenia i wyniki; log, stany, zrzuty, `timeline.txt`
i `dumps.sha256` w `/opt/olimpiada-backups/pg18-upgrade-<data>/`):

| Krok | Co | Zatrzymuje się, gdy |
|---|---|---|
| 0 | kontrole: przypięcie 16, wolumeny, wersja serwera = 16, miejsce (≥ 5× baza, min. 2 GB) na katalogu kopii i Dockera, kopia nocna ≤ 26 h (`--allow-stale-backup` świadomie), nie trwa `backup.sh`, `docker pull` obrazu 18, każde rozszerzenie bazy jest w obrazie 18, proxy widzi `/srv/maintenance`, `MAINTENANCE_BYPASS_TOKEN` ≥ 32 znaki | cokolwiek się nie zgadza – **przed** przerwą |
| 1 | `scripts/maintenance.sh on` (komunikat + planowany koniec = teraz + `MAINTENANCE_MINUTES`) | proxy nie potwierdza flagi – nic jeszcze nie zatrzymane |
| 2 | `stop web worker beat`, czeka aż zniknie każde połączenie klienta; po 30 s `pg_terminate_backend` maruderów | po rozłączeniu wciąż ktoś podłączony |
| 3 | stan 16: `count(*)` każdej tabeli, `last_value` każdej sekwencji, rozszerzenia, role, kodowanie/locale, ustawienia ról/bazy, liczby obiektów schematu | – |
| 4 | **zrzuty końcowe**: `pg_dump -Fc` klientem 16 (powrót), `pg_dumpall --roles-only` i `pg_dump -Fc` klientem 18 (do odtworzenia); przed każdym: strona włączona (host i proxy), aplikacja stoi, zero klientów, czas > włączenia strony; SHA-256 + rozmiar do `dumps.sha256`; spis treści przez `pg_restore -l` 18; ponowny stan 16 = stan sprzed zrzutów | którykolwiek warunek / pusty zrzut / nieczytelny spis / stan 16 się zmienił |
| 5 | `stop db` (16), `up -d db` z obrazem 18 na `pg18_data` (ten sam `command`, locale, hasło – z compose); czeka na TCP (nie na gniazdo – patrz komentarz w skrypcie) | serwer nie wstaje / wersja ≠ 18 / baza nie jest pusta |
| 6 | `sha256sum -c dumps.sha256`, role, `pg_restore --exit-on-error --single-transaction` **tego** zrzutu (właściciele bez zmian), `ANALYZE` | suma się nie zgadza / pierwszy błąd odtwarzania |
| 7 | stan 18 i `diff` ze stanem zatrzymanej 16 | **jakakolwiek** różnica |
| 8 | zdjęcie przypięcia z `.env` (kopia `env.before-upgrade`), `up -d db web worker beat`, `web` healthy, `db_connections`, `showmigrations`, `/status.json` = `ok` od środka (do 5 min – kolejka wstaje ostatnia) i `https://<domena>/status.json` **przez proxy z nagłówkiem `X-Maintenance-Bypass`** | web nie wstaje / status nie `ok` |
| 9 | `scripts/maintenance.sh off` | proxy nie potwierdza wyłączenia |

**Błąd w krokach 2–7 sam przywraca bazę sprzed przejścia:** `.env` wciąż przypina 16, więc skrypt
robi `up -d db` (16 na `pg_data`), kasuje `pg18_data` utworzony w tym przebiegu (niesie najwyżej
częściowe odtworzenie; zrzuty zostają w katalogu przebiegu), podnosi aplikację i kończy się kodem
≠ 0. Po usunięciu przyczyny – uruchomić ponownie. Błąd w kroku 8 (po zdjęciu przypięcia) **nie**
wycofuje sam: baza jest już na 18 i mogła przyjąć zapisy – decyzja należy do operatora (§ 19.5).
**Po każdym błędzie od kroku 1 strona prac technicznych zostaje włączona** (ramka `!!!` na końcu
logu): sprawdzić serwis z przepustką (`curl -H "X-Maintenance-Bypass: <token>"
https://olimpiadakwantowa.pl/status.json` albo przeglądarką przez
`/__maintenance/bypass?token=<token>`) i dopiero wtedy `scripts/maintenance.sh off`.
`--no-maintenance` wyłącza stronę w tym skrypcie (np. proxy sprzed tej funkcji) – przerwa jest
wtedy gołym 502, ale kolejka zatrzymanie → zero klientów → zrzut → odtworzenie zostaje ta sama.

### 19.5. Wycofanie (powrót na 16)

```bash
cd /opt/olimpiada
scripts/upgrade_postgres18.sh --rollback --dry-run   # plan
scripts/upgrade_postgres18.sh --rollback --yes
```

Włącza stronę prac technicznych, zatrzymuje aplikację (zero klientów bazy), robi zrzut bazy 18
(`pg18-rollback-<data>/pg18-rollback-*.dump`, SHA-256 w `dumps.sha256`), wpisuje przypięcie 16 do
`.env`, stawia 16 na **starym** wolumenie `pg_data`, sprawdza wersję, podnosi aplikację, sprawdza
`/status.json` od środka i przez proxy z przepustką, wyłącza stronę (przestój ~1–3 min). Wolumen
`pg18_data` zostaje nietknięty. Błąd po włączeniu strony zostawia ją włączoną (jak wyżej).

**Zapisy wykonane na 18 od chwili przejścia nie wracają same** – baza 16 ma stan z chwili przejścia.
Są w zrzucie `pg18-rollback-*.dump`; przeniesienie ich na 16 jest ręczne (np. `pg_restore -t
<tabela>` klientem 18 do bazy pomocniczej i `INSERT … SELECT`), dlatego decyzję o wycofaniu
podejmuje się **zaraz** po przejściu (w pierwszych godzinach), a nie po tygodniu. Bez skryptu,
ręcznie: dopisać do `.env` dwie linijki `POSTGRES_IMAGE=postgres:16-alpine`
i `POSTGRES_VOLUME=pg_data:/var/lib/postgresql/data`, potem `docker compose up -d db web worker beat`.

Ponowne przejście po wycofaniu: `scripts/upgrade_postgres18.sh --recreate-pg18-volume` (bez flagi
skrypt odmawia, bo `pg18_data` niesie dane z pierwszego podejścia).

### 19.6. Sprzątanie: stary wolumen `pg_data`

Najwcześniej **14 dni** po przejściu bez wycofania, i dopiero gdy: `scripts/upgrade_postgres18.sh
--status` pokazuje 18 bez przypięcia, w tym czasie przeszło co najmniej jedno niedzielne
`backup_verify.sh` na 18 (`/status.json`: `backup_last_verified: true`), a zrzut klientem 16
z przejścia (`/opt/olimpiada-backups/pg18-upgrade-*/db-pg16-*.dump`) jest skopiowany poza serwer:

```bash
cd /opt/olimpiada
scripts/upgrade_postgres18.sh --status
docker volume rm olimpiada_pg_data           # nieodwracalne – koniec drogi powrotu na 16
```

Wpis `pg_data:` w `docker-compose.yml` może zostać (compose nie zakłada wolumenu, którego nic nie
montuje) – usunąć go razem z wariantem 16 w komentarzach przy następnej porządkowej zmianie.
Katalogi `pg18-upgrade-*`/`pg18-rollback-*` nie podlegają retencji `backup.sh` (ta sprząta tylko
`*.gpg`) – skasować ręcznie po tym samym terminie, bo zawierają niezaszyfrowane zrzuty i kopię `.env` (`env.before-upgrade`).

### 19.7. Środowisko deweloperskie

Lokalny stos na 16 po pobraniu tej wersji bez przygotowania wstałby na **pustym** `pg18_data`.
Dwie drogi:

```bash
# a) przejść tak jak produkcja (dane zostają; zrzuty poza repozytorium)
export COMPOSE_FILE="docker-compose.yml;docker-compose.dev.yml"   # Windows; Linux/macOS: ':'
bash scripts/upgrade_postgres18.sh --pin-if-needed
# --no-maintenance: lokalny stos zwykle nie ma proxy, a bez niego nie ma kto podać strony (§ 20)
BACKUP_DIR="$HOME/olimpiada-pg18-upgrade" bash scripts/upgrade_postgres18.sh --allow-stale-backup --no-maintenance
# b) zostać na 16: wpisać przypięcie do .env (jak w § 19.5) i przejść później
```

`scripts/e2e.sh` (reset) kasuje oba wolumeny – `pg18_data` i `pg_data`.

## 20. Strona „Prace techniczne” (`scripts/maintenance.sh`, prośba organizatora z 25.09.2026)

Zamiast gołego **502** z Caddy'ego – w czasie wdrożenia, przejścia na PostgreSQL 18 czy awarii
`web` – uczestnik widzi stronę „Prace techniczne – serwis wróci za kilka minut” (PL + jedno zdanie
EN, kontakt contact@qaif.org, odświeżanie co 45 s, jasny/ciemny motyw). Stronę podaje **Caddy**, nie
Django, więc działa, gdy `web` i baza leżą. Odpowiedź: **503**, `Retry-After: 60`,
`Cache-Control: no-store`, własne CSP (bez skryptów, bez zasobów z zewnątrz). Dla `/status.json`,
`/healthz/` i `/api/*` – JSON `{"status":"maintenance","retry_after":60}` z tym samym 503, żeby
monitoring i klienci API rozumieli, co się dzieje (Uptime Kuma zgłosi przerwę – to prawda).

### 20.1. Dwa tryby

| Tryb | Kiedy | Kto włącza |
|---|---|---|
| **planowy** | istnieje plik `/opt/olimpiada/maintenance/on` | `scripts/maintenance.sh on`, `upgrade_postgres18.sh`, `deploy.sh --maintenance` |
| **nieplanowy** | `web` nie odpowiada: Caddy dostaje błąd połączenia/timeout (502/503/504) | nikt – działa zawsze (`handle_errors` w `deploy/Caddyfile`) |

Tryb nieplanowy zasłania też krótką przerwę przy restarcie `web` w **zwykłym** wdrożeniu (krok 4b).
Odpowiedzi, które wysłała sama aplikacja (także jej własne 500/503), przechodzą bez zmian.

Zakres: domena główna, domeny z `EXTRA_DOMAINS` i subdomeny platformy (`import maintenance`
w każdym bloku aplikacji; generator `scripts/render_caddyfile.sh`). **Nie** dotyczy: `meet.`
(Jitsi), `monitor.` (monitoring ma działać właśnie wtedy) i endpointu S3 (`:9000`/`s3.`) –
przerwane wgrywanie ma się skończyć zwykłym błędem, a nie stroną HTML. Wyzwanie ACME
(`/.well-known/acme-challenge/*`) nigdy nie jest przechwytywane. W trybie planowym strona
zasłania także `/static/*` (strona i tak niczego stamtąd nie wczytuje).

### 20.2. Polecenia (na serwerze, w `/opt/olimpiada`)

```bash
scripts/maintenance.sh on --message "Aktualizacja bazy danych." --until "21:30"   # czas polski
scripts/maintenance.sh status      # stan, od kiedy, komunikat; co widzi proxy; kody HTTP
scripts/maintenance.sh off
```

Przełączenie to utworzenie/skasowanie pliku – Caddy sprawdza go przy **każdym** żądaniu (matcher
`file`), więc nie ma przeładowania ani restartu (sprawdzone na działającym proxy: odpowiedź zmienia
się przy następnym żądaniu). Skrypt po każdej zmianie pyta kontener proxy, czy widzi to samo co
host; jeśli nie – kod 2 i podpowiedź `docker compose up -d --force-recreate proxy` (katalog
skasowany i utworzony od nowa po starcie proxy). `--message` (do 300 znaków) i `--until` trafiają na
stronę; znaki HTML i klamry są zamieniane na encje (plik przechodzi przez `templates` Caddy'ego).

**Przepustka operatora** – żeby obejrzeć serwis przed zdjęciem strony. Token
`MAINTENANCE_BYPASS_TOKEN` w `.env` (≥ 32 znaki; generuje go `deploy.sh`, krok 4/8):

```bash
TOKEN="$(sed -n 's/^MAINTENANCE_BYPASS_TOKEN=//p' /opt/olimpiada/.env)"
curl -H "X-Maintenance-Bypass: $TOKEN" https://olimpiadakwantowa.pl/status.json
# przeglądarka: https://olimpiadakwantowa.pl/__maintenance/bypass?token=<token>
#   -> ciasteczko olimpiada_maintenance_bypass (Secure, HttpOnly, 12 h) i przekierowanie na /
```

Token pusty albo krótszy niż 32 znaki nie przepuszcza **nikogo**. Zmiana tokenu: nowa wartość w
`.env`, potem `docker compose up -d proxy` (proxy czyta go ze środowiska). Adres z tokenem zostaje
w historii przeglądarki – po przerwie można go zmienić.

### 20.3. Wdrożenie z `--maintenance`

```bash
SSH_KEY=~/.ssh/olimpiada_deploy scripts/deploy.sh --maintenance root@169.58.242.197
# opcjonalnie: MAINTENANCE_MESSAGE="…" MAINTENANCE_MINUTES=15
```

Bez flagi wdrożenie przebiega jak dotąd (kopia `pre-deploy-*` przy działającej aplikacji, restart
`web` zasłonięty trybem nieplanowym). Z flagą – ta sama zasada co przy PostgreSQL 18 (§ 19.4):
po zbudowaniu obrazu **strona włączona** → stop `web`/`worker`/`beat` → zero klientów bazy (30 s,
potem rozłączenie maruderów, inaczej przerwanie) → **kopia przed migracjami** (krok 4a; wolno ją
zrobić wyłącznie przy włączonej stronie i zatrzymanej aplikacji, jej znacznik musi być późniejszy
niż włączenie strony; SHA-256 obok pliku: `pre-deploy-*.dump.sha256`) → start z migracjami (4b) →
`web` healthy i `https://<domena>/healthz/` = 200 **z przepustką** → **strona wyłączona** (krok
5a). Błąd w którymkolwiek miejscu zostawia stronę włączoną i kończy wdrożenie ramką `!!!` z
poleceniami `status`/`off`. `--maintenance` wymaga proxy, które już ma montaż `/srv/maintenance` –
pierwsze wdrożenie tej wersji robi się **bez** flagi.

### 20.4. Jak to jest zbudowane

- Treść: `deploy/maintenance/index.html` (jeden plik, CSS i logo w środku, zero zewnętrznych żądań).
- Stan: `/opt/olimpiada/maintenance/` (w repozytorium – `.gitignore`), montowany do proxy tylko do
  odczytu jako `/srv/maintenance` (`MAINTENANCE_DIR` zmienia położenie). Zawiera `on` (flaga +
  kto/od kiedy/komunikat), `info.html` (komunikat na stronę) i `page/` (kopia strony).
- Dlaczego kopia, a nie montaż `deploy/maintenance`: krok 2/8 `deploy.sh` kasuje katalogi z kodem
  i rozpakowuje je od nowa, a działający kontener widziałby wtedy stary, skasowany (pusty) katalog
  aż do restartu – czyli strona zniknęłaby właśnie po wdrożeniu. Katalog `maintenance/` krok 2/8
  omija, a `scripts/maintenance.sh sync` (krok 4/8) nadpisuje w nim pliki w miejscu.
- Caddy 2.8: `handle_errors` **bez** listy kodów i własny matcher kodu – wariant `handle_errors 502
  503 504` nadpisuje w 2.8.4 matchery zagnieżdżonych `handle` (sprawdzone `caddy adapt`; JSON łapał
  wtedy każde żądanie). Pilnuje tego `scripts/tests/render_caddyfile_test.sh` (także `caddy
  validate` i kolejność tras po `caddy adapt`, gdy jest Docker).

### 20.5. Na serwerze po pierwszym wdrożeniu tej wersji

Nic ręcznie: krok 2/8 zostawia `maintenance/`, krok 4/8 dopisuje `MAINTENANCE_BYPASS_TOKEN` do
`.env` i kopiuje stronę, krok 4b odtwarza proxy (nowy montaż i zmienna – kilka sekund bez HTTPS,
jednorazowo). Sprawdzenie:

```bash
cd /opt/olimpiada
scripts/maintenance.sh status          # „wyłączone”, proxy: flaga off, strona: jest
scripts/maintenance.sh on --message "Test strony prac technicznych." && sleep 5 && scripts/maintenance.sh off
```

Zapisz token (`grep MAINTENANCE_BYPASS_TOKEN .env`) w menedżerze haseł organizatora razem z
`BACKUP_PASSPHRASE`.

## 21. Python 3.14 (interpreter obrazu)

### 21.1. Co się zmieniło

| Gdzie | Było | Jest |
|---|---|---|
| obraz (`backend/Dockerfile`, oba etapy) | `python:3.12-slim-bookworm` (3.12.14, Debian 12) | `python:3.14-slim-trixie` (3.14.7, Debian 13) |
| CI (`.github/workflows/ci.yml`, `PYTHON_VERSION`) | `3.12` | `3.14` |
| `backend/pyproject.toml` | `requires-python = ">=3.12"`, ruff `py312` | `">=3.14"`, ruff `py314` |

Baza Debiana: `trixie`, bo na niej stoją tagi bez sufiksu (`3.14-slim`, `3-slim`) oficjalnych
obrazów (`docker-library/python`, sprawdzone 25.09.2026); `bookworm` jest już `oldstable`. Pakiety
z warstwy apt (`libpq5`, `curl`, `procps`, `gettext`) są w trixie pod tymi samymi nazwami –
Dockerfile poza linijkami `FROM` się nie zmienił. Kroje dla reportlaba (DejaVu) leżą
w repozytorium (`backend/static/fonts/`), `psycopg-binary` ma własny `libpq`, a klient ClamAV to
nasz kod na gołym gnieździe (`apps.submissions.antivirus`) – żadna z tych rzeczy nie zależy od
pakietów systemu obrazu.

Zależności: rozwiązanie `pyproject.toml` dla 3.12 i dla 3.14 daje **identyczny** zestaw wersji,
a każdy pakiet binarny instaluje się z gotowego koła `cp314`/`abi3` (nic nie buduje się ze źródeł).
Podniesione są tylko dwie dolne granice: `psycopg[binary,pool]>=3.2.10` (starsze wydania nie mają
koła dla 3.14, a `psycopg-binary` nie wychodzi jako źródła) i `ruff>=0.12` (starszy nie zna `py314`).

Kod: bez zmian zachowania. `ruff` pod `py314` zdjął cudzysłowy z dwóch adnotacji (odwołania
w przód są w 3.14 leniwe – PEP 649), a `ruff format` zapisuje wyjątki bez nawiasów:
`except (A, B):` → `except A, B:` (PEP 758 – składnia 3.14 o tym samym znaczeniu; przy `as`
nawiasy zostają). Dlatego `requires-python` jest twarde: na 3.12 te pliki się nie parsują.

### 21.2. Wdrożenie

Nic ponad zwykłe `scripts/deploy.sh`: krok 4/8 robi `docker compose build --pull web`, czyli
pobiera nowy obraz bazowy i instaluje zależności od zera. Pierwszy build po zmianie trwa dłużej
(pobranie `python:3.14-slim-trixie`). Migracji nie ma. Po wdrożeniu:

```bash
docker compose exec -T web python -VV          # Python 3.14.x
docker compose ps                              # web, worker, beat: healthy
docker compose logs --since 10m worker beat | grep -iE 'error|traceback' || echo czysto
```

### 21.3. Rollback

Wydanie nie zmienia bazy, więc powrót to sam obraz – bez odtwarzania kopii. `scripts/deploy.sh`
zostawia na serwerze tag poprzedniej wersji (§ 11.3), zbudowany jeszcze na 3.12:

```bash
# na serwerze, w /opt/olimpiada
docker compose stop web worker beat
sed -i 's/^APP_VERSION=.*/APP_VERSION=<poprzednia-wersja>/' .env
docker compose up -d web worker beat
```

Gdy tamtego tagu już nie ma i baza jest **jeszcze na 16**: `scripts/deploy.sh` z commitu sprzed
zmiany (`git checkout <poprzedni-tag>`). **Po przejściu na PostgreSQL 18 (§ 19.4) nie wdrażaj kodu
sprzed v0.37.0** – jego `docker-compose.yml` nie zna `POSTGRES_IMAGE`/`POSTGRES_VOLUME` i postawi
16 na starym `pg_data` (stan sprzed przejścia; zapisy z 18 znikają z widoku, a po § 19.6 – pusta
baza). Powrót Pythona wyłącznie przez tag obrazu (`APP_VERSION`, wyżej) albo `WEB_IMAGE=…`
z kodem v0.37.0 lub nowszym. Cache w Redisie (strony, sesje) jest serializowany `pickle`, którego protokół
jest wspólny dla 3.12 i 3.14 – przełączenie w żadną stronę nie wymaga czyszczenia cache.

### 21.4. Lokalne środowisko (lint poza kontenerem)

Venv w `backend/.venv` trzeba **utworzyć od nowa** – interpretera w istniejącym venvie się nie
podmienia. `uv` sam pobierze Pythona 3.14, jeśli nie ma go w systemie; za firmowym proxy TLS
dopisz `--system-certs` do obu poleceń `uv`:

```bash
cd backend
rm -rf .venv
uv venv --python 3.14 .venv
uv pip install --python .venv -r pyproject.toml --extra dev
.venv/Scripts/ruff.exe check . && .venv/Scripts/ruff.exe format --check .   # Linux: .venv/bin/ruff
```

---

## 22. Wersja porównawcza na django CMS (`dj.<domena>`, docs/tasks/DJ-01.md)

Równoległa, publiczna, ale **nieindeksowana** wersja części informacyjnej serwisu pod
`dj.olimpiadakwantowa.pl`, redagowana w django CMS – do porównania z Wagtailem (`/cms/`). Treść
redakcyjna żyje w osobnej bazie `olimpiada_djcms` (stan początkowy z importu drzewa Wagtaila), dane
zawodów (terminy, zadania, wyniki, komunikaty) djcms pobiera na żywo z wewnętrznego API aplikacji
głównej (`/internal/djcms/v2/`, per konkurs, token w nagłówku; API v1 z DJ-01 usunięte w DJ-02k).
Logowanie, rejestracja i panele zostają w aplikacji głównej.

**Domyślnie wyłączone.** Bez `DJCMS_ENABLED=1` w `/opt/olimpiada/.env` konfiguracja proxy,
`docker compose config` i przebieg `scripts/deploy.sh` są co do polecenia takie jak przed DJ-01
(pilnują tego `scripts/tests/render_caddyfile_test.sh`, `compose_profiles_test.sh`
i `deploy_djcms_test.sh`). **Włączenie na produkcji wymaga zgody organizatora** (DJ-01 § 11 p. 6).

### 22.1. Co robi przełącznik `DJCMS_ENABLED=1`

| Miejsce | Zmiana |
|---|---|
| Caddy (`scripts/render_caddyfile.sh`) | od DJ-02: w **każdym** bloku aplikacji (domena główna, `EXTRA_DOMAINS`, `*.`) sekcja tras djcms – `/djcms/media/*` z wolumenu `djcms_media`, `/djcms/*` do `djcms:8000`, adresy aplikacji do `web`, strony publiczne wg `DJCMS_PRIMARY` i ciasteczka `djcms_view` (§ 22.8); blok `dj.{$SITE_DOMAIN}` już tylko przekierowuje (302) na `/djcms/preview/` domeny głównej; odmowa `/internal/*` (404) w każdym bloku |
| `.env` (krok 4/8 wdrożenia, tylko dopisuje) | `DJCMS_SECRET_KEY` (64), `DJCMS_DB_PASSWORD` (32), `DJCMS_INTERNAL_TOKEN` (48), `DJCMS_SSO_KEY` (64, § 22.3) – istniejących nie rusza; `DJCMS_INITIAL_IMPORT=pending`; `COMPOSE_FILE=docker-compose.yml:docker-compose.djcms.yml`; `COMPOSE_PROFILES=djcms` |
| compose | usługa `djcms` (profil `djcms`) i nakładka `docker-compose.djcms.yml` – montaż `djcms_media` do `proxy` tylko do odczytu i stały adres `proxy` w sieci `internal` (`DJCMS_PROXY_IP`, domyślnie 172.30.2.250 – jedyny adres, od którego djcms przyjmuje `X-Real-IP` i `X-Djcms-Mode`). Oba przez `COMPOSE_FILE`/`COMPOSE_PROFILES` w `.env`, więc **każde** `docker compose …` w `/opt/olimpiada` (także ręczne i `scripts/backup.sh`) widzi djcms |
| baza | rola i baza `olimpiada_djcms` w tym samym kontenerze `db` (`scripts/djcms_db.sh`, idempotentnie, przy każdym wdrożeniu) |
| wdrożenie | build obrazu `djcms` (albo `DJCMS_IMAGE` z rejestru), kopia `djcms-db-pre-<stamp>.dump` obok `pre-deploy-*` (10 ostatnich), start `djcms` w 4b (migracje w entrypoincie), czekanie na `djcms=healthy`, na końcu krok „dj.”: grupy redakcji (`setup_djcms_groups`, § 22.3), konto administratora (gdy podano), rejestr konkursów (`sync_competitions --import-missing` – treść dla witryn bez stron, § 22.8), **jednorazowy** import treści |

Pliki redaktorów (`/djcms/media/*`, od DJ-02 na każdym hoście konkursu) podaje Caddy z nagłówkiem
`X-Content-Type-Options: nosniff`, a wszystko poza PDF-em dodatkowo z
`Content-Security-Policy: default-src 'none'; …; sandbox` – wgrany SVG albo HTML otwarty wprost nie
wykona skryptu w origin aplikacji (DJ-01 § 7 reguła 12, DJ-02 S9).

### 22.2. Włączenie (jednorazowo, z komputera operatora)

1. **DNS.** Rekord `*` (albo `dj`) → adres serwera. Dla olimpiadakwantowa.pl `*` już istnieje
   (`deploy/dns-olimpiadakwantowa.pl.zone`); sprawdzenie: `dig +short dj.olimpiadakwantowa.pl`.
2. **Wdrożenie z przełącznikiem i kontem administratora dj.** (konto tylko djcms – osobne od kont
   aplikacji głównej; hasło przechodzi przez stdin ssh, nie przez argumenty procesów):

   ```bash
   DJCMS_ENABLE=1 DJCMS_ADMIN_EMAIL=redakcja@qaif.org DJCMS_ADMIN_PASSWORD='…' \
     SSH_KEY=~/.ssh/olimpiada_deploy scripts/deploy.sh root@169.58.242.197
   ```

   `DJCMS_ENABLE=1` dopisuje `DJCMS_ENABLED=1` do `.env` – kolejne wdrożenia idą już bez tej
   zmiennej (i bez `DJCMS_ADMIN_*`, chyba że trzeba dołożyć konto). Inna wartość niż `1/true`
   zatrzymuje wdrożenie, zanim cokolwiek dotknie serwera – wyłączenia nie robi się tą zmienną
   (§ 22.6).
3. Co zobaczysz w logu: `dj.: wygenerowano DJCMS_…` (krok 4/8), budowanie obrazu djcms,
   `djcms_db: rola i baza olimpiada_djcms gotowe`, kopię `djcms-db-pre-*.dump` (4a), a na końcu
   krok `==> dj. Wersja porównawcza django CMS…` z importem i adresem.
   **Pierwsze włączenie odtwarza kontener `proxy`** (nowy montaż) – kilka sekund bez HTTPS na
   wszystkich domenach, jak przy każdej zmianie konfiguracji proxy; certyfikat `dj.` (HTTP-01)
   powstaje zaraz po jego starcie.
4. **Sprawdzenie:**

   ```bash
   curl -sI https://dj.olimpiadakwantowa.pl/zadania/ | grep -iE '^(HTTP|location)'  # 302 → https://olimpiadakwantowa.pl/djcms/preview/?next=/zadania/
   curl -sI https://olimpiadakwantowa.pl/ | grep -i x-djcms-mode                    # nic (PRIMARY=0, bez ciasteczka – Wagtail)
   curl -sI -H 'Cookie: djcms_view=dj' https://olimpiadakwantowa.pl/ | grep -iE '^(x-djcms-mode|x-robots-tag)'   # preview, noindex
   curl -s -o /dev/null -w '%{http_code}\n' https://olimpiadakwantowa.pl/djcms/healthz/                 # 200
   curl -s -o /dev/null -w '%{http_code}\n' https://olimpiadakwantowa.pl/internal/djcms/v2/competitions     # 404
   curl -s -o /dev/null -w '%{http_code}\n' https://dj.olimpiadakwantowa.pl/internal/djcms/v2/competitions  # 404
   # na serwerze, w /opt/olimpiada:
   docker compose ps djcms                     # healthy
   grep '^DJCMS_INITIAL_IMPORT=' .env          # done
   ```

Gdy import się nie uda (najczęściej: brak konta superusera djcms, bo nie podano `DJCMS_ADMIN_*`),
wdrożenie kończy się kodem ≠ 0 **po** wszystkich krokach głównego serwisu, a `.env` zostaje
z `DJCMS_INITIAL_IMPORT=pending` – kolejne wdrożenie (z `DJCMS_ADMIN_*`) spróbuje ponownie.

### 22.3. Redaktorzy: logowanie z `/cms/` (SSO) i uprawnienia per konkurs

**Redaktorzy nie mają w djcms kont zakładanych ręcznie ani haseł** (DJ-02 D6, decyzja organizatora
z 26.09.2026). Kto redaguje który konkurs, rozstrzyga aplikacja główna – tym samym pytaniem, co
dostęp do `/cms/` – i przekazuje wynik jednorazowym tokenem przy każdym wejściu.

Jak wchodzi redaktor (instrukcja dla redakcji: docs/PODRECZNIK-ORGANIZATORA.md § 7.3a):

1. `/cms/` **swojego** konkursu, na jego hoście (`https://fizyczna.olimpiadakwantowa.pl/cms/`,
   konkurs pod prefiksem: `https://olimpiadakwantowa.pl/druga/cms/`),
2. w menu Wagtaila **„Edytuj w django CMS”** (`/cms/django-cms/`) → przycisk przejścia (POST z CSRF),
3. przeglądarka sama wysyła formularz z tokenem na `/djcms/sso/` **tego samego** hosta i trafia do
   listy stron witryny konkursu w `/djcms/admin/`.

Pozycja menu jest widoczna tylko przy ustawionym kluczu i dla konta, które może edytować korzeń
drzewa stron witryny konkursu. Konto bez tego prawa dostaje na `/cms/django-cms/` stronę odmowy
(403), a przy pustym kluczu – stronę „przejście wyłączone” (503).

**Konto w djcms** powstaje przy pierwszym wejściu: `web:<id konta w aplikacji>`, „W zespole”, bez
hasła, nigdy superużytkownik; e-mail, imię i nazwisko aktualizuje każde wejście. **Hasłem loguje się
wyłącznie techniczny superużytkownik** (`bootstrap_djcms_admin`, `DJCMS_ADMIN_*` przy wdrożeniu) –
konto personelu z hasłem ustawionym ręcznie w panelu i tak się nim nie zaloguje (liczy się jak
nieudana próba). Po 5 nieudanych próbach na parę (IP, login) logowanie jest blokowane na 15 minut.
Hasło superużytkownika zmienione w panelu **nie** jest nadpisywane kolejnym wdrożeniem
z `DJCMS_ADMIN_*` (ręcznie: `bootstrap_djcms_admin --reset-password`).

**Grupy** – przy każdym wejściu **zastępowane** listą z tokenu (także grupa dopisana ręcznie
w panelu djcms znika, uprawnienia indywidualne konta są czyszczone):

| Prawo w `/cms/` (korzeń witryny konkursu) | Grupa w djcms | Zasięg |
|---|---|---|
| edycja i publikacja | `redakcja:<slug>` | strony witryny konkursu: dodawanie, zmiana, usuwanie, przenoszenie, publikacja |
| edycja bez publikacji | `redakcja:<slug>:bez-publikacji` | to samo bez publikacji |
| konto bez ograniczeń w `/cms/` (superużytkownik, superkoordynator, grupa z prawami do korzenia drzewa) **i** edycja z publikacją w każdym aktywnym konkursie | `redakcja:platforma` | wszystkie witryny i wszystkie foldery |

Każda grupa konkursu ma `GlobalPagePermission` zawężone do witryny konkursu (nigdy uprawnienia na
pojedynczych stronach – bufor uprawnień django CMS nie rozróżnia witryn) i uprawnienia modeli
treści (strony, wtyczki, wersje, pliki, przekierowania); **bez** zarządzania uprawnieniami, kontami
i grupami. Redaktor widzi panel wyłącznie pod hostami swoich konkursów: witryna spoza zasięgu
(także przez `?site=` w drzewie stron), obiekt innej witryny (strona, wtyczka, wersja,
przekierowanie) i folder filera innego konkursu → 403 (DJ-02 S12).

**Pliki (filer)**: każdy konkurs ma folder najwyższego poziomu `Konkurs: <nazwa> (<slug>)` (tam też
trafiają obrazy z importu) – odczyt, zmiana i podfoldery dla obu jego grup; konkurs domeny głównej
dodatkowo folder importu sprzed DJ-02 („Import z Wagtaila”). Folder **„Wspólne”** – do odczytu dla
każdego redaktora, zapis: `redakcja:platforma` i superużytkownik. Uprawnienia folderów porządkują
bibliotekę redakcji, a **nie** ukrywają plików: każdy plik filera jest publiczny pod
`/djcms/media/…` (DJ-01 § 7 reguła 12, przełącznik „prywatny” jest ukryty i zablokowany).

**`setup_djcms_groups`** zakłada i aktualizuje grupy `redakcja:*`, ich uprawnienia i uprawnienia
folderów **przy każdym wdrożeniu** (idempotentnie; zestaw uprawnień grupy wynika z kodu – ręczna
zmiana w panelu zniknie). Przy pierwszym uruchomieniu po DJ-02g usuwa grupę **„Redaktorzy”** z DJ-01
(przy `CMS_PERMISSION = True` i tak nie dawała żadnej strony). Grupy nowego konkursu powstają też
same przy pierwszym wejściu jego redaktora. Ręcznie: `docker compose exec -T djcms python manage.py
setup_djcms_groups`.

**Klucz i sesja:**

- `DJCMS_SSO_KEY` – klucz HMAC tokenu, **ten sam** w `web` (czyta `.env`) i w `djcms` (compose
  przekazuje ten sam wpis). Generuje go wdrożenie (krok 4/8, 64 znaki); ręcznie: co najmniej
  32 znaki, inny niż `DJCMS_INTERNAL_TOKEN`, `DJANGO_SECRET_KEY` i `DJCMS_SECRET_KEY` (ostrzeżenia
  `cms.W013` w `web` i `dj_sites.W001` w djcms). Pusty albo krótszy = przejście wyłączone. Po
  zmianie klucza w `.env`: `docker compose up -d` (odtwarza `web` i `djcms`; oba muszą mieć tę samą
  wartość – inaczej każde wejście kończy się „Link logowania jest nieważny”).
- `DJCMS_SSO_SESSION_SECONDS` (opcjonalnie, domyślnie `7200` = 2 h) – najdłuższa sesja po wejściu,
  liczona **od logowania**, nie od ostatniego kliknięcia. Po niej djcms wylogowuje, a redaktor wchodzi
  ponownie przez `/cms/` (uprawnienia liczone od nowa). Wylogowanie z aplikacji głównej (strona,
  `/cms/`) kończy sesję djcms na tym samym hoście od razu.
- Token: ważny 60 s, jednorazowy (nonce), tylko dla hosta, na którym go wystawiono, wyłącznie `POST`
  z nagłówkiem `Origin` tego hosta; nie trafia do adresu ani do dzienników (format: docs/API.md § 8.6).

**Odebranie uprawnień** działa w djcms przy **najbliższym wejściu** przez `/cms/` (grupy
zastępowane), a w sesji otwartej wcześniej – **najpóźniej po `DJCMS_SSO_SESSION_SECONDS`**.
Natychmiastowa blokada konta (np. wyciek, odejście z redakcji):

1. superużytkownik djcms: `https://<domena>/djcms/admin/` → Użytkownicy → `web:<id>` → odznacz
   „Aktywny” → Zapisz. Sesja tego konta przestaje działać od następnego żądania, a kolejne wejście
   z `/cms/` kończy się „Konto w django CMS jest zablokowane” – SSO konta **nie** odblokowuje
   (odblokowanie: zaznacz „Aktywny” z powrotem),
2. w aplikacji głównej odbierz prawa w `/cms/` (grupa, konto) – inaczej po odblokowaniu konto
   wróciłoby z dotychczasowym zasięgiem.

Identyfikator konta (`<id>`) jest w dzienniku djcms (`SSO djcms: web:<id> zalogowany (konkurs …)`)
i w dzienniku `web` (`SSO do django CMS: konto #<id>, host …`).

### 22.4. Import treści z Wagtaila

Wdrożenie importuje **raz** (`DJCMS_INITIAL_IMPORT=pending` → `done`), komendą
`import_cms_bundle --from-api --if-empty` – istniejących stron dj. nigdy nie nadpisuje. Pełny
ponowny import (kasuje strony dj. i obrazy z folderu „Import z Wagtaila”, **cała redakcja w dj.
przepada**) – wyłącznie ręcznie, po kopii:

```bash
cd /opt/olimpiada
docker compose exec -T djcms python manage.py import_cms_bundle --from-api --dry-run   # raport bez zapisu
docker compose exec -T djcms python manage.py import_cms_bundle --from-api --replace
```

Zmieniając szablon w `backend/templates/cms/`, zmień też port w `djcms/templates/dj/` – wygląd
dwóch wersji nie synchronizuje się sam (DJ-01 § 13, ryzyko 3).

### 22.5. Kopie

Przed każdą migracją wdrożenie robi `djcms-db-pre-<stamp>.dump` w katalogu kopii (10 ostatnich);
polecenie odtworzenia wypisuje log kroku 4a.

Kopia nocna (`scripts/backup.sh`, § 1.2) przy `DJCMS_ENABLED=1` obejmuje bazę
(`djcms-db-<stamp>.dump.gpg`) i wolumen plików (`djcms-files-<stamp>.tar.gpg`) – zaszyfrowane,
wysyłane i sprzątane razem z kopią główną; cotygodniowy test odtwarzania sprawdza je razem z nią
(§ 1.4). Kopia plików wymaga **działającego** kontenera `djcms` – zatrzymany `djcms` w nocy daje
przebieg nieudany (kopia główna mimo to powstaje). Odtwarzanie: § 2.4.

```bash
ls -lh /opt/olimpiada-backups/djcms-*                         # paczki djcms z ostatnich 7 dni
grep -E '1b/5|2b/5|UWAGA' /var/log/olimpiada-backup.log | tail  # co zrobiła ostatnia noc
```

### 22.6. Wyłączenie i usunięcie

Wyłączenie (dane zostają – ponowne `DJCMS_ENABLE=1` wraca do tego samego stanu; sekrety bazy
i klucz zostają w `.env`, token API trzeba wtedy wygenerować od nowa – wdrożenie zrobi to samo):

```bash
cd /opt/olimpiada
docker compose stop djcms && docker compose rm -f djcms       # póki COMPOSE_* jeszcze są w .env
sed -i 's/^DJCMS_ENABLED=.*/DJCMS_ENABLED=0/' .env
sed -i '/^COMPOSE_FILE=docker-compose.yml:docker-compose.djcms.yml$/d; /^COMPOSE_PROFILES=djcms$/d' .env
sed -i '/^DJCMS_INTERNAL_TOKEN=/d' .env                        # brak tokenu = API wyłączone w web
bash scripts/proxy_config.sh update && docker compose up -d web
```

Od tej chwili konfiguracja proxy i compose'a jest ta sama co przed DJ-01, a wdrożenia nie wykonują
żadnego polecenia djcms. `COMPOSE_FILE`/`COMPOSE_PROFILES`, które operator zmienił ręcznie (np.
dopisany profil `monitoring`), popraw ręcznie zamiast drugiego `sed`.

Po wyłączeniu kopia nocna nie obejmuje już `dj.` (§ 1.2) – ostatnie paczki `djcms-*` to te
z nocy przed wyłączeniem (lokalnie 7 dni, poza serwerem 30 dni w `daily/`, miesięczne rok).

Usunięcie danych (po decyzji organizatora, po ostatniej kopii z § 22.5):

```bash
cd /opt/olimpiada
docker compose exec -T db psql -U olimpiada -d olimpiada -c 'DROP DATABASE olimpiada_djcms' -c 'DROP ROLE olimpiada_djcms'
docker volume rm olimpiada_djcms_media
sed -i '/^DJCMS_/d' .env                                       # po wyłączeniu wyżej
docker image ls 'olimpiada/djcms' -q | xargs -r docker rmi
```

### 22.7. Jak to jest zbudowane (dla utrzymującego skrypty)

- `scripts/render_caddyfile.sh` czyta `DJCMS_ENABLED` jak `PLATFORM_SUBDOMAINS` (środowisko > `.env`,
  `1|true|yes|on` / `''|0|false|no|off`, inna wartość = błąd). Przy obu włączonych odmowa
  `/internal/*` trafia do bloku głównego **raz**; `dj.` jako nazwa dosłowna wygrywa z `*.`.
- CSP `sandbox` dla `/media/*` poza PDF-em, a nie dla listy rozszerzeń z DJ-01 § 8.8: `*` w środku
  wzorca `path` Caddy'ego nie przechodzi przez `/`, więc `path /media/*.svg` nie pasował do żadnego
  pliku filera (`/media/filer_public/…/x.svg`) – sprawdzone na działającym caddy:2.8 w teście.
- Montaż mediów w nakładce, a nie w `docker-compose.yml`: wpis w pliku podstawowym zmieniałby
  konfigurację `proxy` na każdej instalacji (i odtwarzał proxy), także bez dj. Serwowanie mediów
  przez samego djcms odrzucone – `static.serve` przez gunicorna nie jest do produkcji, a WhiteNoise
  nie widzi plików wgranych po starcie procesu.
- `scripts/deploy.sh` czyta przełącznik z `.env` serwera raz, po kroku 4/8 (jedno `ssh … sed`, bez
  dockera); przy wyłączonym każde kolejne polecenie jest znak w znak dzisiejsze. Krok „dj.” stoi
  na samym końcu (DJ-01 § 8.10 przewidywał krok 6), żeby błąd wersji porównawczej nie zatrzymał
  kroków 6a–8/8 głównego serwisu.
- `--maintenance`: `djcms` jest zatrzymywany razem z `web/worker/beat` (inaczej kontrola „zero
  klientów bazy” by nie przeszła). Od DJ-02 trasy djcms są w blokach aplikacji, więc przerwę
  (planową i 502/503/504 z djcms) zasłania ta sama strona „Prace techniczne”; `dj.` tylko przekierowuje.
- Konfiguracja proxy (trasy djcms, tryb `DJCMS_PRIMARY`) jest w `caddy/Caddyfile` i trafia do
  działającego Caddy'ego przy **każdym** wdrożeniu (krok 4c/8, `caddy reload`) – § 23. Dawny krok
  „dj.” porównujący sumy i odtwarzający `proxy` (tylko przy `DJCMS_ENABLED=1`) został zastąpiony
  tym mechanizmem. Generator i przełącznik piszą plik **w miejscu** (`cat >`).

### 22.8. Serwis publiczny na django CMS: trasy, podgląd, przełącznik `DJCMS_PRIMARY` (DJ-02)

djcms odpowiada na **prawdziwych hostach konkursów** (domena główna, subdomeny platformy,
`EXTRA_DOMAINS`, konkursy pod prefiksem ścieżki). Która aplikacja podaje daną ścieżkę, rozstrzyga
Caddy (docs/tasks/DJ-02.md § 3); `web` nie widzi żadnego nowego hosta.

| Ścieżka (w każdym bloku aplikacji) | `DJCMS_PRIMARY=0` | `DJCMS_PRIMARY=1` |
|---|---|---|
| `/internal/*` | 404 | 404 |
| `/static/*` | pliki `web` | pliki `web` |
| `/djcms/media/*` | pliki redaktorów (CSP `sandbox` poza PDF, `nosniff`, `max-age=86400`) | to samo |
| `/djcms/*` (na domenie głównej także `/<prefiks>/djcms/*`) | djcms, `X-Djcms-Mode: preview` | djcms, `primary` |
| adresy aplikacji – `APP_RE` z `backend/djcms_contract/app_routes.env` (na domenie głównej także `APP_RE_PREFIXED`: `/<prefiks>/login/` …) | `web` | `web` |
| pozostałe (strony, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`) | `web`; z ciasteczkiem `djcms_view=dj` – djcms (`preview`, noindex) | djcms (`primary`); z `djcms_view=wagtail` – `web` |
| `dj.<domena>/*` | 302 → `https://<domena>/djcms/preview/?next=<adres>` | 302 → `https://<domena><adres>` |

- `X-Djcms-Mode` ustawia wyłącznie Caddy (`request_header -X-Djcms-Mode` zdejmuje wartość od
  klienta z każdego żądania, `header_up` nadaje ją przy djcms); djcms ufa mu tylko od adresu
  `proxy` (`TRUSTED_PROXY_IPS` = `DJCMS_PROXY_IP`), w każdym innym przypadku przyjmuje `preview`.
- Odpowiedzi poza statykami mają `Vary: Cookie` (ta sama ścieżka – różna treść zależnie od
  ciasteczka `djcms_view`).
- Podgląd: ciasteczko `djcms_view` ustawia djcms (`/djcms/preview/` na hoście konkursu, formularz
  POST z CSRF, `HttpOnly`, `Secure`, `SameSite=Lax`, 8 h) – jest host-only, więc każdy host
  włącza się osobno; strona `/djcms/preview/` domeny głównej wymienia wszystkie konkursy.
  Ciasteczko nie jest zabezpieczeniem (treść publiczna); roboty go nie wysyłają.
- Nowy adres aplikacji w `web` = `manage.py djcms_routes --write` (CI pilnuje `--check`) i wdrożenie
  – generator wkleja wyrażenia do każdego bloku przy każdym wdrożeniu.

Przełącznik (na serwerze, `cd /opt/olimpiada`; **na produkcji wyłącznie po zgodzie organizatora**,
zwykle przez `scripts/djcms_cutover.sh` – zamrożenie Wagtaila, import, weryfikacja, `on`):

```bash
bash scripts/djcms_switch.sh status   # .env, plik wygenerowany, co widzi proxy, zamrożenie Wagtaila
bash scripts/djcms_switch.sh on       # strony publiczne z djcms (DJCMS_PRIMARY=1)
bash scripts/djcms_switch.sh off      # powrót do Wagtaila (~2 s), działa też przy leżącym djcms
bash scripts/djcms_switch.sh check    # sama kontrola dymna trybu z .env
```

`on`/`off`: plik kandydujący + `caddy validate` w kontenerze proxy (błąd = nic nie zmienione) →
`DJCMS_PRIMARY` w `.env` → render w miejscu + sprawdzenie, że proxy widzi tę treść → `caddy reload`
(bez restartu `web`/`djcms`, bez zrywania połączeń) → kontrola dymna przez `https://<host>` na
127.0.0.1 dla hostów z `sync_competitions --list-hosts` (`/` i `/<prefiks>/` z djcms albo z `web`,
`/login/` z `web`, `/static/css/app.css` 200, `/internal/tls-allowed` 404, `/robots.txt` przy `on`).
Porażka po `on` = automatyczny powrót do `DJCMS_PRIMARY=0` i kod 1. `off` nie wymaga zdrowego djcms
(hosty wtedy z `SITE_DOMAIN`/`EXTRA_DOMAINS`), a proxy ze starą treścią pliku odtwarza samo.
Wycofanie jest stratne: Wagtail pokazuje treść z chwili zamrożenia; edycję w `/cms/` odblokowuje
dopiero `docker compose exec -T web python manage.py cms_freeze off` (po decyzji).
Wdrożenie trybu nie zmienia: przy `DJCMS_PRIMARY=1` kończy się `djcms_switch.sh check`.

Rejestr witryn przy każdym wdrożeniu: `sync_competitions --import-missing` – witryna nowego
konkursu i treść dla **każdej** aktywnej witryny, która nie ma ani jednej strony (drzewo startowe
z eksportu tego konkursu). Witryn ze stronami wdrożenie nie rusza. Obraz djcms sprzed tej flagi
(np. `DJCMS_IMAGE` w starszej wersji) dostaje samo `sync_competitions` – wdrożenie sprawdza flagę
w `--help`.

Zachowanie przełącznika przy awariach: `on` dopisuje do `.env` `DJCMS_EVER_PRIMARY=<czas>` (raz,
przed zmianą trybu – znacznik „djcms był publiczny” dla `djcms_cutover.sh`); przerwanie `on` sygnałem
(Ctrl-C, zerwane ssh, `kill`) od zmiany `.env` do końca kontroli dymnej wraca do Wagtaila jak
porażka kontroli. Kontrola dymna traktuje błąd uścisku TLS / certyfikatu (curl 35, 60) jako
ostrzeżenie dla tej nazwy, a nie błąd tras – ale bez ani jednego sprawdzonego hosta nie przechodzi.
Przepustka prac technicznych idzie do curla konfiguracją na stdin (`-K -`), nie w argumentach
widocznych w `ps`. Wdrożenie z `.env` w innym trybie niż konfiguracja w działającym proxy
(przerwane przełączenie, ręczna zmiana `DJCMS_PRIMARY`) staje w kroku 4/8 (§ 23). Wdrożenie przy
`DJCMS_PRIMARY=1` porównuje też kontrakt tras na hoście (`backend/djcms_contract/app_routes.env`)
z obrazem `web` – rozjazd (np. `WEB_IMAGE` w innej wersji niż kod) to kod ≠ 0 z podpowiedzią `off`.

### 22.9. Przełączenie serwisu publicznego na django CMS (`scripts/djcms_cutover.sh`)

Jedno polecenie na serwerze robi całe przejście: kopia → zamrożenie edycji stron w Wagtailu →
końcowy import z Wagtaila → weryfikacja → przełącznik. **Na produkcji wyłącznie po zgodzie
organizatora** (DJ-02 § 12 p. 7). Treść djcms do chwili przełączenia jest jednorazowa – końcowy
import ją **zastępuje** (DJ-02 D8); od chwili przełączenia źródłem prawdy jest djcms.

Przed (dzień wcześniej):

1. Uprzedź redakcje: od chwili przełączenia strony w `/cms/` są tylko do odczytu; edycja w djcms
   (`/cms/` konkursu → „Edytuj w django CMS”, § 22.3). Zmiany zrobione w djcms **w czasie
   podglądu** zostaną nadpisane – chyba że konkurs pójdzie z `--skip <slug>` (jego treść djcms
   zostaje, bez importu).
2. Próba bez zmian (kilka minut – z testem odtwarzania ostatniej kopii):

   ```bash
   cd /opt/olimpiada
   bash scripts/djcms_cutover.sh --check                         # same kontrole, kod 0/1
   bash scripts/djcms_cutover.sh --dry-run [--skip fizyczna]      # kontrole + plan z dokładnymi poleceniami
   ```

   Kontrole (każda `ok`/`FAIL`, porażki zebrane w jednym przebiegu): `DJCMS_ENABLED=1`, proxy montuje
   katalog `caddy/` (`CADDY_CONFIG_DIR=./caddy`, § 23) i widzi bieżącą treść `caddy/Caddyfile`, `web` i `djcms` healthy, `cms_freeze status`
   odpowiada, `djcms_routes --check` w `web` i `backend/djcms_contract/app_routes.env` na hoście =
   kontrakt z obrazu `web`, brak aliasów językowych aktywnych konkursów (D12), API v2 i rejestr
   (`sync_competitions --dry-run`), `import_cms_bundle --all --replace --dry-run` (każda paczka
   pobrana i zaimportowana w wycofanej transakcji), wolne miejsce (≥ 2048 MB w katalogu instalacji
   i w `/opt/olimpiada-backups`, `DJCMS_CUTOVER_MIN_FREE_MB`), `backup_verify.sh` ostatniej kopii
   (`--no-backup-verify` pomija – szybka próba).

Przełączenie – **w `tmux`/`screen`** (albo `setsid`), nie wprost w sesji ssh: zerwane połączenie
wysyła SIGHUP i przerywa przebieg w środku (skrypt kończy się wtedy ramką `!!!` ze stanem, a
przerwany przełącznik wraca do Wagtaila – ale kopia, import i weryfikacja zostają do powtórzenia):

```bash
tmux new -s cutover            # po zerwaniu: tmux attach -t cutover
cd /opt/olimpiada
bash scripts/djcms_cutover.sh [--skip SLUG …]      # pyta: wpisz PRZEŁĄCZ; bez terminala: --yes
# bez tmux: setsid -w bash scripts/djcms_cutover.sh --yes [--skip …] </dev/null   (dziennik – niżej)
```

| Krok | Co | Błąd = |
|---|---|---|
| 0 | kontrole jak `--check` (bez testu odtwarzania) | nic nie zmienione |
| 1/7 | `scripts/backup.sh` (baza główna, baza i pliki djcms) + `scripts/backup_verify.sh` **tej** kopii | nic w serwisie nie zmienione |
| 2/7 | `cms_freeze on --message "Edycja treści przeniesiona do django CMS" --wait` (`--wait`: 12 s, aż zamrożenie zobaczą wszystkie workery `web` – zapis strony tuż przed nim nie minie importu) | stan zamrożenia niepewny – `djcms_switch.sh status` |
| 3/7 | `sync_competitions` (djcms) | Wagtail zamrożony, publicznie dalej Wagtail |
| 4/7 | `import_cms_bundle --from-api --all --replace [--skip …]` – każdy konkurs we własnej transakcji | jw.; konkursy z błędem mają poprzednią treść djcms |
| 5/7 | `verify_cutover` – tabela per konkurs (strony djcms/paczka, adresy 200, przekierowania) | jw.; porażka konkursu z `--skip` to tylko ostrzeżenie (liczba stron z definicji inna) |
| 6/7 | `DJCMS_CUTOVER_DONE=<czas>` w `.env` (PRZED przełącznikiem – przerwanie po nim nie gubi znacznika), potem `bash scripts/djcms_switch.sh on` (§ 22.8) | przełącznik sam wraca do `DJCMS_PRIMARY=0`; Wagtail zamrożony; ponowienie samego przełączenia: `djcms_switch.sh on` (pełny przebieg – tylko z `--force-reimport`) |
| 7/7 | podsumowanie | – |

Przy każdym błędzie skrypt kończy się kodem ≠ 0 i ramką `!!!` z opisem stanu i dwiema drogami
dalej. Strony publiczne **nigdy** nie zostają w stanie pośrednim: do kroku 6 podaje je Wagtail.
Zamrożenia skrypt sam **nie** zdejmuje (decyzja operatora, DJ-02 § 10.1 p. 5):

- poprawka i ponowienie – bezpieczne (zamrożenie idempotentne, `--replace` daje ten sam stan):
  `bash scripts/djcms_cutover.sh [--skip …]`,
- rezygnacja – `bash scripts/djcms_cutover.sh --rollback --unfreeze` (edycja w `/cms/` znów otwarta).

Ponowne uruchomienie po udanym przełączeniu (`DJCMS_PRIMARY=1`) nic nie robi (kod 0) – import
skasowałby redakcję djcms – **o ile Wagtail jest zamrożony**: `DJCMS_PRIMARY=1` przy otwartej edycji
w `/cms/` (np. po ręcznym `djcms_switch.sh on`) to kod 1 z poleceniem `cms_freeze on` (skrypt nie
zamraża sam). Pełny przebieg odmawia bez `--force-reimport` także wtedy, gdy w `.env` jest
`DJCMS_EVER_PRIMARY` – ślad po `djcms_switch.sh on` wywołanym ręcznie, poza tym skryptem.
Dziennik całego przebiegu: `/var/log/olimpiada-djcms-cutover-<data>.log` (`DJCMS_CUTOVER_LOG_DIR`).

Jedna blokada zmian serwisu publicznego – `caddy/.lock` (katalog stanu, § 23): biorą ją ten skrypt,
`djcms_switch.sh`, `proxy_config.sh` i **wdrożenie** (od kroku 2/8 do końca). Przełączenie w trakcie
wdrożenia (albo wdrożenie w trakcie przełączenia) odmawia od razu (`--rollback` i `djcms_switch.sh
off` czekają do 2 min); przełącznik wołany przez ten skrypt dziedziczy blokadę.

Po przełączeniu: `bash scripts/djcms_switch.sh status`, w przeglądarce kilka stron każdego konkursu,
`/robots.txt`, `/sitemap.xml`, logowanie i panel (`/login/`, `/me/`, `/coordinator/`). Porównanie
z Wagtailem: `/djcms/preview/` na hoście konkursu (ciasteczko `djcms_view=wagtail`).

W czasie zamrożenia: konkurs założony po przełączeniu dostaje w Wagtailu strony startowe (zamrożone,
bez edycji), a djcms buduje jego drzewo startowe sam, kilka sekund po pierwszej wizycie na jego
adresie – stron-danych (warsztaty, partnerzy) taki konkurs nie ma do DJ-03. Strony-dane – jedyny
wyjątek od zamrożenia – edytuje się i publikuje w `/cms/` dalej, ale nie zdejmuje z publikacji
(dla aplikacji to ich usunięcie), a
`publish_scheduled` platforma nie uruchamia – nie uruchamiaj go ręcznie, dopóki Wagtail jest zamrożony.

### 22.10. Wycofanie (powrót do Wagtaila)

```bash
cd /opt/olimpiada
bash scripts/djcms_cutover.sh --rollback              # = djcms_switch.sh off (~2 s), Wagtail zostaje zamrożony
bash scripts/djcms_cutover.sh --rollback --unfreeze   # dodatkowo cms_freeze off – dopiero po decyzji
```

`--rollback` nie wymaga zdrowego djcms (to droga ratunkowa – przełącznik sam odtwarza proxy ze starą
treścią pliku), niczego nie importuje i nie kasuje. Woła `djcms_switch.sh off` **zawsze** (przy
`DJCMS_ENABLED=1`), także gdy `.env` mówi już `DJCMS_PRIMARY=0` – po przerwanym przełączeniu `.env`,
`caddy/Caddyfile` i konfiguracja załadowana w proxy mogą się różnić, a `off` jest idempotentne. **Wycofanie jest stratne**: Wagtail pokazuje
treść z chwili zamrożenia, a zmiany zrobione w djcms po przełączeniu do Wagtaila **nie wracają**
(D8). Dopóki Wagtail jest zamrożony, redakcja nie ma gdzie poprawiać stron publicznych – odmrażaj
wyłącznie, gdy powrót ma potrwać dłużej.

Ponowne przejście na djcms po wycofaniu:

- **bez utraty zmian z djcms** (zwykła droga – treść djcms jest ta sama, co przed `off`):
  `bash scripts/djcms_switch.sh on`,
- **od nowa z Wagtaila** (np. redakcja pracowała w odmrożonym `/cms/`): `bash scripts/djcms_cutover.sh
  --force-reimport [--skip SLUG …]` – bez `--force-reimport` skrypt odmawia, bo w `.env` jest
  `DJCMS_CUTOVER_DONE` (albo `DJCMS_EVER_PRIMARY`) i ponowny import skasowałby redakcję djcms.

Monitoring trybu (zalecane po przełączeniu): w Uptime Kuma monitor HTTP(s) na `https://<domena>/`,
który sprawdza **nagłówek odpowiedzi** `X-Djcms-Mode: primary` (monitor „HTTP(s) – Keyword” czyta
treść, nie nagłówki – najprościej monitor typu „Push” zasilany z crona sondą
`curl -sI https://<domena>/ | grep -qi '^x-djcms-mode: primary'`). Brak nagłówka przy
`DJCMS_PRIMARY=1` znaczy, że djcms przestał ufać proxy (np. proxy straciło stały adres w sieci
`internal` z nakładki docker-compose.djcms.yml) i podaje strony jak w podglądzie – z `noindex`.
Znane ograniczenie: ciasteczka djcms (`djcms_view`, sesja) nie mają prefiksu `__Host-` – środowisko
deweloperskie działa po HTTP, a prefiks wymaga `Secure`.

## 23. Konfiguracja proxy: `caddy reload` przy każdym wdrożeniu (`scripts/proxy_config.sh`)

### 23.1. Błąd, który to naprawia

Krok 2/8 `scripts/deploy.sh` kasuje katalog `deploy/` na serwerze i rozpakowuje go od nowa. Proxy
montowało wygenerowany plik `deploy/Caddyfile.generated` jako **pojedynczy plik** – bind mount
trzyma i-węzeł z chwili startu kontenera, więc po wdrożeniu Caddy widział skasowaną, **starą**
treść. `docker compose up -d` w kroku 4b odtwarza kontener wyłącznie przy zmianie jego
konfiguracji compose'a (obraz, montaże, środowisko), a nie treści pliku – nowe nagłówki, trasy
i domeny nie docierały na produkcję aż do ręcznego `docker compose up -d --force-recreate proxy`.
Na gałęzi djcms naprawione było to tylko przy `DJCMS_ENABLED=1` (porównanie sum i odtworzenie).

### 23.2. Jak jest teraz

| Element | Stan |
|---|---|
| Plik konfiguracji | `/opt/olimpiada/caddy/Caddyfile` – składany z `deploy/Caddyfile` i `.env` (`EXTRA_DOMAINS`, `PLATFORM_SUBDOMAINS`, `DJCMS_*`) przez `scripts/render_caddyfile.sh` |
| Montaż w `proxy` | **katalog** `${CADDY_CONFIG_DIR:-./deploy}` → `/etc/caddy` (tylko do odczytu); serwer ma w `.env` `CADDY_CONFIG_DIR=./caddy`, środowisko deweloperskie montuje `deploy/` z plikiem źródłowym |
| Krok 2/8 | omija `caddy/` (jak `maintenance/`, `secrets/`, `.env`) – katalog, który montuje działający kontener, nigdy nie znika |
| Krok 4/8 | `bash scripts/proxy_config.sh render`: plik obok (`caddy/Caddyfile.next`) → `caddy validate` w działającym proxy → dopiero wtedy zapis do `caddy/Caddyfile` (w miejscu). Odrzucony = wdrożenie staje **przed** budowaniem i przed stroną prac technicznych; plik i proxy bez zmian |
| Krok 4c/8 | `bash scripts/proxy_config.sh apply` po starcie usług: kontener widzi ten sam plik → `caddy reload` (bez restartu, bez zrywania połączeń; niezmieniona konfiguracja = no-op). Kontener nie działa albo widzi inną treść → `up -d --force-recreate --no-deps proxy` (kilka sekund bez HTTPS) i ponowne sprawdzenie sumy |
| Punkty powrotu | pierwszy `render` na serwerze zaczyna od kopii tego, co widzi działające proxy (katalog `caddy/` montowany po zmianie `.env` nigdy nie jest pusty); każda nowa treść zostawia poprzednią w `caddy/Caddyfile.prev`. Reload odrzucony albo Caddy niewstający po odtworzeniu → `caddy/Caddyfile` wraca do `.prev` (przy odtworzeniu proxy startuje z niej ponownie), kod 1 |

Rozważone warianty: (a) przy każdym wdrożeniu porównywać sumę pliku z tym, co widzi kontener,
i przy różnicy odtwarzać `proxy` – proste, ale **każda** zmiana konfiguracji to kilka sekund bez
HTTPS na wszystkich domenach (zerwane wgrywania, połączenia odrzucone jeszcze przed stroną prac
technicznych); (b) montaż katalogu, który krok 2/8 omija, i `caddy reload` – bez przerwy.
Wybrane (b), z (a) jako drogą awaryjną w 4c/8. Sam montaż **katalogu** `deploy/` niczego by nie
naprawił: krok 2/8 kasuje także ten katalog, a bind mount katalogu też trzyma stary i-węzeł –
dlatego konfiguracja mieszka w katalogu stanu poza kodem.

Kolejność z `--maintenance` (§ 20.3) się nie zmienia: walidacja w 4/8 jest przed włączeniem strony
(odrzucona konfiguracja = strona w ogóle nie jest włączana), `caddy reload` w 4c/8 jest po starcie
usług i **przed** kontrolą z przepustką (5a), więc `https://<domena>/healthz/` przechodzi już przez
nową konfigurację. Błąd reloadu po włączeniu strony zostawia ją włączoną z komunikatem pułapki –
jak każdy inny błąd po 4/8.

### 23.3. Polecenia (na serwerze, `cd /opt/olimpiada`)

```bash
bash scripts/proxy_config.sh status   # CADDY_CONFIG_DIR, czy plik zgadza się z .env, co widzi kontener
bash scripts/proxy_config.sh update   # po zmianie EXTRA_DOMAINS / PLATFORM_SUBDOMAINS w .env: render + reload
bash scripts/proxy_config.sh render   # sam render z walidacją (bez przeładowania)
bash scripts/proxy_config.sh apply    # samo przeładowanie (albo odtworzenie kontenera, gdy trzeba)
```

- Generator czyta **wyłącznie** `.env` – zmienna z powłoki operatora nie wygrywa z plikiem.
- `scripts/djcms_switch.sh on|off` zmienia ten sam plik; blokadę `caddy/.lock` biorą `proxy_config.sh`,
  `djcms_switch.sh`, `djcms_cutover.sh` i wdrożenie – to ostatnie od kroku 2/8 do końca (sesja ssh
  w tle; `proxy_config.sh` w krokach wdrożenia dziedziczy ją przez `OLIMPIADA_PROXY_LOCK=held`).
  Wdrożenie czeka na cudzą zmianę do 2 min, potem odmawia – zanim cokolwiek skasuje (§ 22.9).
- Przy `DJCMS_ENABLED=1` `render` porównuje tryb z `.env` (`DJCMS_PRIMARY`) z trybem konfiguracji
  w **działającym** proxy (`header_up X-Djcms-Mode …`): różne = przerwane przełączenie albo ręczna
  zmiana `.env` – odmowa (kod 1, nic nie zapisane) z poleceniem `djcms_switch.sh on|off`. Tryb
  zmienia wyłącznie przełącznik (kontrola dymna, powrót przy porażce), nigdy wdrożenie.
- `caddy validate` idzie w obrazie i środowisku **działającego** kontenera. Wydanie, które zmienia
  wersję obrazu `caddy` albo dokłada zmienną środowiskową proxy, sprawdza nowy plik starszym
  Caddym; taki kontener i tak jest odtwarzany w 4b (zmiana konfiguracji compose'a), a ewentualny
  błąd pokaże `docker compose logs proxy` i krok 5/8.
- Nie edytuj `caddy/Caddyfile` ręcznie – kolejne wdrożenie złoży go od nowa z `deploy/Caddyfile`
  i `.env`. Zmiana konfiguracji = zmiana `deploy/Caddyfile` w repozytorium albo `.env`.

### 23.4. Pierwsze wdrożenie tej wersji

Nic ręcznie. Krok 4/8 usuwa z `.env` dawny wpis `CADDYFILE_PATH=./deploy/Caddyfile.generated`
(z jego dwulinijkowym komentarzem) i dopisuje `CADDY_CONFIG_DIR=./caddy`; `render` kopiuje do
`caddy/Caddyfile` konfigurację, którą widzi stary kontener (punkt powrotu), a walidacja nowej idzie
jeszcze w starym kontenerze. W 4b `up -d` **jednorazowo** odtwarza `proxy` (zmienił się montaż –
kilka sekund bez HTTPS, po tym, jak `web` jest healthy), a 4c/8 kończy się `caddy reload` bez
zmian. Gdyby nowy Caddy nie wstał, 4c/8 wraca do skopiowanej konfiguracji i odtwarza proxy z niej.
`CADDYFILE_PATH` wpisany ręcznie z inną wartością (własny plik proxy) zatrzymuje wdrożenie przed
budowaniem – przenieś zmiany do `deploy/Caddyfile` i usuń tę linijkę. Sprawdzenie po wdrożeniu:

```bash
cd /opt/olimpiada
bash scripts/proxy_config.sh status   # „zgodny”, „kontener proxy: widzi caddy/Caddyfile”
docker compose config proxy | grep -A3 'target: /etc/caddy'   # source: /opt/olimpiada/caddy
```

Testy: `scripts/tests/proxy_config_test.sh` (skrypt na atrapie dockera) i
`scripts/tests/deploy_djcms_test.sh` część 10 (całe wdrożenie na atrapach ssh/dockera: zmiana
`deploy/Caddyfile` dochodzi do proxy przez reload, stary montaż, migracja `.env`, odrzucona
walidacja i odrzucony reload – także z `--maintenance`).

---

## 24. Utwardzenie infrastruktury po audycie bezpieczeństwa (v0.38.3, 1.10.2026)

Pakiet „infrastruktura” audytu: sieci compose'a, Redis, ClamAV, MinIO, blok S3 w Caddym, obrazy
usług i workflow wdrożenia. Każda zmiana jest zgodna wstecz z działającym serwerem – wdrożenie
robi wszystko samo (§ 24.7), a ręcznie zostaje tylko konfiguracja GitHuba i kontrole po wdrożeniu.
Wiersze checklisty: `docs/SECURITY_CHECKLIST.md` 2.4, 2.9, 5.8–5.10, 9.2, 9.5–9.8, 11.4.

### 24.1. Sieci compose'a

| Sieć | Podsieć | `internal` | Członkowie | W `TRUSTED_PROXY_IPS` i `mynetworks` |
|------|---------|------------|------------|--------------------------------------|
| `edge` | 172.30.1.0/24 | nie | proxy, web, mail, monitor (+ `jitsi-web` z osobnego projektu) | tak |
| `internal` | 172.30.2.0/24 | tak | proxy, web, worker, beat, db, minio, minio-init, clamav, mail, monitor, djcms | tak |
| `cache` | 172.30.3.0/24 | tak | **redis**, web, worker, beat | **nie** |
| `clamav_egress` | 172.30.4.0/24 | nie | **clamav** | **nie** |

- Redis wyszedł z `internal`: był tam osiągalny dla każdej usługi (minio, clamav, poczta, djcms,
  monitor), a jest i cache'em z serializacją `pickle` (zapis = wykonanie kodu przy odczycie), i brokerem
  Celery (zapis = zadanie w kolejce workera). Teraz sięgają go wyłącznie jego klienci.
- `clamav_egress` istnieje po to, żeby `freshclam` miał wyjście do internetu (§ 24.3). Nie `edge`,
  bo `edge` jest na liście zaufanych proxy aplikacji i klientów relaya poczty.
- Obu nowych sieci **nie** dopisujemy do `TRUSTED_PROXY_IPS` (`.env`) – proxy w nich nie stoi.
- Projekty testowe obok deweloperskiego przesuwają podsieci: `docker-compose.e2e-djcms.yml`
  (172.31.1–4.0/24, zmienne `E2E_*_SUBNET`) i `scripts/tests/maintenance_pg18_rehearsal.sh`
  (172.30.81–84.0/24, `REHEARSAL_SUBNET_*`). Docker nie założy dwóch sieci na tej samej podsieci.
- Pilnuje tego `scripts/tests/compose_profiles_test.sh` (przypadki 12–17).

### 24.2. Redis z hasłem

`REDIS_PASSWORD` w `.env` (wyłącznie `[A-Za-z0-9]`, ≥ 16 znaków – wklejane do adresu bez kodowania).
`docker-compose.yml` przekazuje je Redisowi jako `--requirepass` i dokleja `:<hasło>@` do `REDIS_URL`
i `CELERY_BROKER_URL` – **tylko wtedy, gdy jest niepuste**: pusta wartość (albo brak linijki) daje
Redisa bez hasła i adresy co do znaku sprzed zmiany, więc serwer z dawnym `.env` wstaje bez zmian.
Hasło generuje `scripts/deploy.sh` (nowa instalacja – krok 3/8, istniejąca – krok 4/8, raz; wartości
istniejącej nie rusza, wartość spoza `[A-Za-z0-9]` zatrzymuje wdrożenie przed budowaniem).

Healthcheck uwierzytelnia się (`REDISCLI_AUTH`) i sprawdza odpowiedź `PONG` – samo `redis-cli ping`
kończy się kodem 0 także przy `NOAUTH`, więc dawny healthcheck nie odróżniał „działa” od „odmawia”.

Serializator cache'a zostaje `pickle` – audyt pokazał, że cache przechowuje obiekty modeli,
renditions Wagtaila, odpowiedzi HTTP, `bytes`, `Decimal`/`datetime` i słowniki z kluczami `int`, których
`JSONSerializer` nie przeniesie (lista z miejscami: komentarz przy `CACHES` w
`backend/config/settings/base.py`). Ochroną przed wstrzyknięciem `pickle` jest więc to, kto może
pisać do Redisa: hasło i sieć `cache`.

Zmiana hasła (rotacja): nowa wartość w `.env`, potem `docker compose up -d redis web worker beat`
(wszystkie cztery naraz – kto zostanie ze starym adresem, dostaje `NOAUTH`). Cache startuje pusty
(strony publiczne chwilę idą bez bufora); zadania Celery czekające w kolejce zostają – leżą
w wolumenie `redis_data`, nie w haśle.

Sprawdzenie na serwerze:

```bash
cd /opt/olimpiada
docker compose ps redis                                   # (healthy)
docker compose exec redis redis-cli ping                  # NOAUTH Authentication required.
docker compose exec -T web python manage.py shell -c "from django.core.cache import cache; cache.set('audyt', 1, 5); print(cache.get('audyt'))"   # 1
docker compose ps worker beat                             # worker (healthy): jego healthcheck to `celery inspect ping` przez broker z hasłem
```

### 24.3. ClamAV: aktualizacje sygnatur

Do v0.38.3 `clamav` stał wyłącznie w `internal` (`internal: true`, bez wyjścia na świat), więc
`freshclam` od startu kontenera nie dosięgał mirrorów – na produkcji 1.10.2026 baza miała 25 dni.
Teraz kontener jest też w `clamav_egress` (§ 24.1) i ma `no-new-privileges` (entrypoint `/init` obrazu
`clamav/clamav:1.4` biegnie jako root i niczego nie uruchamia z bitem setuid; `freshclam --user=clamav`
i clamd zrzucają uprawnienia same – sprawdzone 1.10.2026 na tym obrazie: freshclam i clamd działają
jako nie-root, `NoNewPrivs: 1`, `clamdcheck.sh` → „Clamd is up”, freshclam połączył się z mirrorem
i zgłosił nowszą bazę `daily`). Po wdrożeniu:

```bash
docker compose logs clamav --since 2h | grep -iE 'daily|updated|up-to-date|fail|connect'   # bez „Can't connect”
docker compose exec clamav sh -c 'sigtool --info /var/lib/clamav/daily.c[lv]d | grep -E "Build time|Version"'
```

`freshclam` sprawdza aktualizacje raz na dobę (`FRESHCLAM_CHECKS=1` – domyślna wartość obrazu).
Pierwsze sprawdzenie idzie zaraz po starcie kontenera, więc kilkudziesięciomegabajtowe pobranie
zaległej bazy `daily` i przeładowanie clamd (chwilowo ~2 GB pamięci – limit `3g`) zdarzy się
w trakcie wdrożenia.

### 24.4. MinIO: bez listowania `public-media`, typ pliku z rozszerzenia

**Polityka anonimowa.** `mc anonymous set download` (do v0.38.3) daje `s3:GetObject` **i**
`s3:ListBucket` – `https://<domena>:9000/public-media/` zwracało każdemu XML z listą wszystkich
obiektów (także dokumentów, do których strona nigdzie nie linkuje). Teraz `minio-init` ustawia
`deploy/minio/policy-anonymous-public-media.json` (`mc anonymous set-json`): wyłącznie
`s3:GetObject` na `public-media/*`. `set-json` zastępuje politykę w całości, `minio-init` biegnie przy
każdym wdrożeniu (krok 4b wymienia go z nazwy), więc nic ręcznie. Sprawdzone 1.10.2026 na tym
samym obrazie MinIO i `mc`: po zmianie anonimowy GET obiektu działa, listowanie – „Access Denied”.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://olimpiadakwantowa.pl:9000/public-media/      # 403 (było 200 z listą)
curl -s -o /dev/null -w '%{http_code}\n' "https://olimpiadakwantowa.pl:9000/public-media/<ścieżka obrazu ze strony>"   # 200
```

**Typ pliku.** Media zapisywane przez django-storages (alias `default` – obrazy i dokumenty Wagtaila
w `public-media`; `private_media` – treści zadań) dostają `Content-Type` z rozszerzenia nazwy,
a nie z nagłówka przeglądarki, i `Content-Disposition: attachment` dla wszystkiego poza obrazem,
filmem, dźwiękiem i PDF-em (SVG też `attachment`) – `backend/apps/core/storage.py`. Dotyczy plików
wgranych **po** wdrożeniu. Obiekty starsze mają typ, jaki podała wtedy przeglądarka; przed skryptem
w nich chroni już CSP `sandbox` z bloku S3 (§ 24.5), ale warto je przejrzeć. Lista obiektów
`public-media` z typem innym niż obraz/film/dźwięk/PDF (**tylko odczyt**; `run --no-deps` zakłada
jednorazowy kontener `mc`, nie dotyka działających usług):

```bash
cd /opt/olimpiada
docker compose run --rm --no-deps -T --entrypoint sh minio-init -c \
  'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc stat --recursive --json local/public-media' \
  | grep -oE '"name":"[^"]*"|"Content-Type":"[^"]*"' | paste - - \
  | grep -vE '"Content-Type":"(image/(png|jpeg|gif|webp|avif|x-icon|vnd\.microsoft\.icon)|video/[^"]*|audio/[^"]*|application/pdf)"'
# Najgroźniejsze z nich (aktywna treść): dopisz na końcu  | grep -iE 'html|svg|xml|javascript'
```

Wynik to pary `"name":"public-media/<klucz>"  "Content-Type":"<typ>"`. Zwykłe dokumenty (`.docx`,
`.xlsx`) z poprawnym typem są niegroźne. Obiekt z typem aktywnym, który nie pasuje do rozszerzenia,
zgłoś przed zmianą – poprawka to ponowne wgranie pliku w `/cms/` albo, po uzgodnieniu,
`mc cp --attr "Content-Type=<typ>;Content-Disposition=attachment" local/public-media/<klucz> local/public-media/<klucz>`
(kopia w miejscu z nowymi metadanymi; poza tym poleceniem nic tu nie zmienia danych).

### 24.5. Blok S3 w Caddym (`{$S3_PUBLIC_ADDRESS}`)

Produkcja ma S3 pod **`olimpiadakwantowa.pl:9000`** (brak rekordu DNS `s3.`), czyli pod tą samą
nazwą co serwis – a ciasteczka nie rozróżniają portów. Dlatego blok (`deploy/Caddyfile`):

- odpowiada **404** na `/minio/*` (API administracyjne, metryki v2/v3/prometheus, KMS, kworum
  `/minio/health/cluster`) – poza `/minio/health/live` i `/minio/health/ready`, które sprawdza
  monitor nr 4 (`deploy/monitoring/README.md`; na produkcji adres
  `https://olimpiadakwantowa.pl:9000/minio/health/live`). Nazwa bucketu `minio` jest w MinIO
  zarezerwowana, więc żaden obiekt nie leży pod tym prefiksem;
- dokłada `X-Content-Type-Options: nosniff`, `Strict-Transport-Security: max-age=31536000` (MinIO
  wysyła własne z `includeSubDomains` – zastąpione) i `Content-Security-Policy: default-src 'none';
  img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; sandbox` dla wszystkiego poza
  PDF-em (wbudowane przeglądarki PDF nie działają pod `sandbox`) – z `defer`, czyli po nagłówkach
  MinIO, każdy dokładnie raz;
- nie rusza ścieżek bucketów, zapytań (podpis, `uploadId`, `partNumber`), `Range`, CORS ani `ETag`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://olimpiadakwantowa.pl:9000/minio/admin/v3/info     # 404
curl -s -o /dev/null -w '%{http_code}\n' https://olimpiadakwantowa.pl:9000/minio/health/live       # 200
curl -sI "https://olimpiadakwantowa.pl:9000/public-media/<ścieżka obrazu>" | grep -iE 'content-security|nosniff|strict-transport'
```

Testy: `scripts/tests/render_caddyfile_test.sh` § 25 (treść bloku we wszystkich wariantach
generatora) i `scripts/tests/s3_proxy_test.sh` (żywy Caddy z atrapą MinIO: 25 żądań, w tym warianty
`/MINIO/`, `%2F`, `//`, `/./` i `Range` → 206).

### 24.6. Wdrożenie: świeże obrazy i przypięty klucz hosta

- Krok 4/8 wykonuje `docker compose pull --ignore-buildable --quiet` (po zbudowaniu `web`, przed
  stroną prac technicznych): caddy, postgres, redis, minio, mc, clamav, postfix i – przy profilu –
  pozostałe obrazy bez `build:`. Dotąd obrazy cudze nie były nigdy pobierane ponownie: tag pływający
  (`caddy:2.8`, `redis:7-alpine`, `clamav/clamav:1.4`, `postgres:18-alpine`) stał na łatce z dnia
  instalacji. Niedostępny rejestr = ostrzeżenie „UWAGA: nie udało się pobrać obrazów…”, a wdrożenie
  idzie dalej na obrazach z serwera. `--ignore-buildable` wymaga Compose ≥ 2.x z tą flagą – sprawdzenie
  (tylko odczyt): `docker compose pull --help | grep ignore-buildable`; bez niej pobranie kończy się
  tym samym ostrzeżeniem przy każdym wdrożeniu.
- Gdy pobranie zbiorcze się nie uda, krok pobiera **każdą usługę osobno** (`docker compose config
  --services`, potem `pull --ignore-buildable --quiet <usługa>`), a ostrzeżenie wymienia tylko te,
  których obrazu rejestr odmówił. Powód: `docker compose pull` przerywa całe pobieranie na pierwszej
  odmowie. Tak było przy wdrożeniu v0.38.3 (2.10.2026): `minio/minio` nie jest już do pobrania
  z Docker Hub („repository does not exist”), więc nie odświeżył się żaden obraz. Ostrzeżenie
  „…(minio)” jest od tej pory **stanem oczekiwanym**, dopóki MinIO nie zostanie zastąpione; serwer
  działa na obrazie, który ma lokalnie – nie usuwać go (`docker image prune -a`), bo nowa instalacja
  ani serwer po utracie obrazu go nie pobiorą.
- Workflow GitHuba (§ 4.2): przypięty klucz hosta (`DEPLOY_SSH_KNOWN_HOSTS`) i
  `SSH_STRICT_HOST_KEY_CHECKING=yes`; `scripts/deploy.sh` przyjmuje wyłącznie `yes` albo `accept-new`.

### 24.7. Pierwsze wdrożenie tej wersji na produkcji – co się stanie

Zalecane `scripts/deploy.sh --maintenance root@olimpiadakwantowa.pl` (zmienia się Redis i może
zmienić się obraz Postgresa – punkt 1); bez flagi też zadziała, z krótkimi błędami w trakcie
(zapytania w chwili restartu bazy albo Redisa).

1. **Krok 4/8:** do `.env` dopisuje się `REDIS_PASSWORD` (w logu: „Redis: wygenerowano
   REDIS_PASSWORD…”); konfiguracja proxy z nowym blokiem S3 przechodzi `caddy validate` w działającym
   proxy; `pull --ignore-buildable` pobiera nowsze łatki obrazów (kilka minut; to normalne).
   `up -d db` odtworzy Postgresa **tylko**, jeśli pobrał się nowszy `postgres:18-alpine` – kilka
   sekund bez bazy (przy `--maintenance` aplikacja już wtedy stoi).
2. **Krok 4b/8 (`up -d`):** odtwarzane są `redis` (polecenie, środowisko, sieć – cache startuje
   pusty, kolejka Celery zostaje w wolumenie), `web`/`worker`/`beat` (nowe adresy Redisa i sieć `cache`;
   jak przy każdym wdrożeniu), `clamav` (sieci, `no-new-privileges`; clamd wczytuje bazę 1–2 min,
   a `worker` czeka na jego `healthy` – skany z tego czasu czekają w kolejce), `minio-init` (nowa
   polityka), a `proxy`, `mail`, `minio` – tylko przy nowym obrazie z kroku 4/8. Docker zakłada dwie
   nowe sieci; stara `internal` zostaje. Krok 4b trwa o 1–2 min dłużej niż zwykle (czekanie na ClamAV).
3. **Krok 4c/8:** `caddy reload` ładuje nowy blok S3 bez restartu proxy.
4. **Ręcznie po wdrożeniu (tylko odczyt):** kontrole z § 24.2–24.5; lista obiektów z § 24.4.
5. **Ręcznie w GitHubie, przed następnym użyciem workflow:** zmienna `DEPLOY_SSH_KNOWN_HOSTS`
   i środowisko `production` z *Required reviewers* (§ 4.2). Bez zmiennej workflow kończy się błędem
   w kroku „Klucz hosta” – nic nie dotyka serwera.

**Wycofanie** (wdrożenie poprzedniej wersji): `REDIS_PASSWORD` zostaje w `.env`, ale stary
`docker-compose.yml` go nie czyta – Redis i adresy wracają do wersji bez hasła, spójnie. Sieci `cache`
i `clamav_egress` zostają jako nieużywane (nieszkodliwe; `docker network prune` je zdejmie). Stary
`minio-init` przywraca politykę `download` (z listowaniem).

## 25. Jitsi tylko z przepustką platformy (JWT, v0.39.0, „wariant A”)

Do v0.38 własne Jitsi (`meet.<domena>`, `deploy/jitsi/`) było otwarte: każdy, kto otworzył
`meet.<domena>/<cokolwiek>`, zakładał pokój. Ochroną pokoju rozmowy była wyłącznie losowa końcówka
nazwy. Od v0.39.0 Prosody wpuszcza **wyłącznie** z tokenem JWT (HS256) podpisanym sekretem, który zna
portal. Token powstaje w chwili kliknięcia „Dołącz” w panelu albo „Dołącz” na stronie
linku-zaproszenia – po sprawdzeniu, kto wchodzi i czy wolno mu teraz wejść.

### 25.1. Co się zmienia dla ludzi

- **Uczestnik:** w panelu (karta „Rozmowa kwalifikacyjna”) zamiast adresu pokoju są dwa przyciski:
  „Dołącz do rozmowy” i „Sprawdź kamerę i mikrofon”. Rozmowa otwiera się `JITSI_JWT_LEAD_MINUTES`
  (15) minut przed terminem i działa do `JITSI_JWT_GRACE_MINUTES` (60) minut po jego końcu. Listy
  (potwierdzenie, przypomnienie) niosą adres widoku wejścia w panelu, a nie adres pokoju.
  **Adresu pokoju nie da się już podyktować przez telefon** – bez przepustki nie zadziała.
- **Koordynator:** na ekranie terminów („Rozmowy”) w kolumnie „Link” jest „dołącz jako gospodarz”
  (moderator) i „test sprzętu”. Ekran „Komunikacja → Pokoje wideo” – pokoje bez terminu (§ 25.8).
- **Komisja prowadzi rozmowy:** karta „Rozmowy kwalifikacyjne” w panelu recenzenta i komisji
  odwoławczej (terminy z zapisami na 14 dni, uczestnicy jako imię i inicjał) z „Dołącz jako gospodarz”
  (`/review/interview-slots/<id>/join/`, okno i prawa jak koordynatora, audyt `interview.joined`
  z rolą `committee`).
- **Komisja:** karta „Pokoje wideo komisji” w panelu recenzenta i komisji odwoławczej (pokoje
  udostępnione przez koordynatora); z uprawnieniem od koordynatora – własny ekran
  `/review/video-rooms/`.
- Pokój **poza** naszym Jitsi (publiczne `meet.jit.si`, BBB uczelni wpisane ręcznie przy terminie)
  i etap bez dostawcy wideo działają dokładnie jak przed v0.39.0.

### 25.2. Konfiguracja

Portal (`/opt/olimpiada/.env`, czyta `web`; szczegóły w `.env.example`):

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `JITSI_JWT_APP_SECRET` | pusty = funkcja wyłączona | sekret HS256; ≥ 32 znaki (skrypt generuje 64); ten sam co `JWT_APP_SECRET` w `jitsi/.env` |
| `JITSI_JWT_APP_ID` | `olimpiada` | claim `iss` = `JWT_APP_ID` Jitsi |
| `JITSI_JWT_HOST` | `meet.<SITE_DOMAIN>` | przepustki dostają wyłącznie pokoje pod tym hostem |
| `JITSI_JWT_AUDIENCE` / `JITSI_JWT_SUBJECT` | `jitsi` / `meet.jitsi` | claimy `aud` i `sub` (`XMPP_DOMAIN`) |
| `JITSI_JWT_LEAD_MINUTES` / `GRACE_MINUTES` | 15 / 60 | okno wejścia na rozmowę |
| `JITSI_JWT_PRECHECK_MINUTES` | 30 | przepustka do pokoju „na próbę” (`…-test`) |
| `JITSI_JWT_SESSION_MINUTES` | 180 | wejście z panelu do pokoju bez terminu |
| `JITSI_JWT_GATEWAY_MINUTES` | 10 | wejście linkiem-zaproszeniem (czas na przejście do pokoju) |
| `JITSI_JWT_ROOM_MAX_DAYS` / `COMMITTEE_ROOM_MAX_DAYS` | 60 / 30 | najdłuższa ważność pokoju bez terminu |

Kontrola `manage.py check`: `competitions.W001` – sekret niepusty, ale krótszy niż 32 znaki (działa
jak pusty) albo równy `SECRET_KEY` / `DJCMS_INTERNAL_TOKEN` / `DJCMS_SSO_KEY`.

Jitsi (`/opt/olimpiada/jitsi/.env`, czyta `docker-compose.jitsi.yml`): `ENABLE_AUTH` (1 = przepustki),
`JWT_APP_SECRET`, `JWT_APP_ID`, `JWT_ACCEPTED_AUDIENCES`, `ENABLE_AUTO_OWNER` (0), `JITSI_IMAGE_VERSION`
(**przypięte** `stable-11031` zamiast pływającego `stable`), opcjonalnie `TOKEN_AUTH_URL` (adres,
na który Jitsi odsyła wejście bez tokenu – domyślnie pusty, niesprawdzony). Compose ustawia na stałe
`AUTH_TYPE=jwt`, `ENABLE_GUESTS=0`, `JWT_ALLOW_EMPTY=0`, `XMPP_MUC_MODULES=token_affiliation`,
w jicofo `JICOFO_ENABLE_AUTH=0`.

**Moderator** (sprawdzone na stable-11031, § 25.6): nadaje go wyłącznie `mod_token_affiliation`
tokenowi z `context.user.moderator = true` (komisja, koordynator, link gospodarza). Jicofo musi mieć
**wyłączone** i „pierwszy zostaje moderatorem” (`ENABLE_AUTO_OWNER=0`), i własne uwierzytelnianie
(`JICOFO_ENABLE_AUTH=0`): jicofo z `authentication.type = JWT` po kilku sekundach nadawał właściciela
**każdemu** zalogowanemu tokenem – także uczestnikowi rozmowy.

**Gdzie jest token:** we fragmencie adresu (`https://meet…/<pokój>#jwt="<token>"`), nie w zapytaniu
– fragment nie jedzie do serwera (nginx w `jitsi-web`, Caddy), a front Jitsi stable-11031 czyta go
w pierwszej kolejności (sprawdzone w przeglądarce). Caddy nie ma access logu, blok `meet.` dostał
`Referrer-Policy: no-referrer`, a odpowiedzi platformy z przepustką są `no-store` i `no-referrer`.

### 25.3. Wdrożenie na produkcji – kolejność, która nikogo nie zamyka

1. `scripts/deploy.sh root@olimpiadakwantowa.pl` – nowy kod (migracje `accounts.0035`,
   `competitions.0033`: jedna kolumna z wartością domyślną i jedna nowa tabela, bez blokad na
   dłużej niż chwila) i nowy `deploy/Caddyfile` (krok 4/8 składa i przeładowuje konfigurację proxy
   – blok `meet.` dostaje `Referrer-Policy`). Sekretu jeszcze nie ma: portal zachowuje się **dokładnie**
   jak v0.38.
2. `scripts/deploy_jitsi.sh root@olimpiadakwantowa.pl`:
   - krok 2/5: generuje `JITSI_JWT_APP_SECRET` w `/opt/olimpiada/.env` (raz; potem nie rusza),
     przepisuje go do `jitsi/.env` (`JWT_APP_SECRET`, przy każdym przebiegu – rozjazd naprawia się
     sam), dopisuje brakujące `JWT_APP_ID`, `JWT_ACCEPTED_AUDIENCES`, `ENABLE_AUTO_OWNER=0`, zamienia
     `JITSI_IMAGE_VERSION=stable` na `stable-11031`. Sekret nie jest nigdzie wypisywany.
   - krok 3/5: sprawdza (po SHA-256), czy działający `web` widzi ten sam sekret; jeśli nie –
     `docker compose up -d --no-deps web worker beat` (kilka sekund przerwy, zasłania ją strona
     zastępcza proxy) i ponowne sprawdzenie. **Dopiero potem** dopisuje `ENABLE_AUTH=1` do `jitsi/.env`.
     Gdy `web` nie widzi sekretu, skrypt kończy się błędem, a Jitsi zostaje otwarte – nikt nie traci
     wejścia. Od tej chwili portal wystawia przepustki, które otwarte jeszcze Jitsi ignoruje.
   - krok 4/5: `pull` (obrazy stable-11031 – kilka minut przy pierwszym razie) i `up -d`: prosody,
     jicofo, web, jvb są **odtwarzane**. **Trwające rozmowy zostają przerwane** (kilkadziesiąt
     sekund); po starcie wejście tylko przez platformę. Uruchamiaj poza godzinami rozmów.
3. Koordynator nic nie przestawia: etapy z `video_base_url = https://meet.<domena>/` dostają
   przepustki same; zapisy sprzed wdrożenia też (adres pokoju jest ten sam, zmienia się droga wejścia).

Uczestnik, który ma w skrzynce list sprzed wdrożenia z gołym adresem pokoju, po kliknięciu zobaczy
w Jitsi prośbę o zalogowanie – wchodzi z panelu. Przypomnienie dzień przed rozmową, wysłane już po
wdrożeniu, niesie adres panelu.

### 25.4. Jak sprawdzić (wyłącznie odczyt)

```sh
cd /opt/olimpiada/jitsi
# Prosody: uwierzytelnianie tokenem, identyfikator aplikacji, moduły MUC (wartość sekretu zakryta).
docker compose -p olimpiada-jitsi exec -T prosody grep -nE 'authentication|app_id|asap_accepted|token_' \
  /config/conf.d/jitsi-meet.cfg.lua | sed 's/app_secret = .*/app_secret = <ukryty>/'
# Jicofo: bez własnego uwierzytelniania i bez auto-właściciela.
docker compose -p olimpiada-jitsi exec -T jicofo grep -nE 'enable-auto-owner|authentication' /config/jicofo.conf
# Portal widzi sekret (skrót, nie wartość) i ten sam skrót ma Jitsi:
cd /opt/olimpiada && docker compose exec -T web python -c \
  'import hashlib,os;print(hashlib.sha256(os.environ["JITSI_JWT_APP_SECRET"].encode()).hexdigest())'
printf '%s' "$(sed -n 's/^JWT_APP_SECRET=//p' jitsi/.env | tail -n1)" | sha256sum
docker compose exec -T web python manage.py check --tag security   # bez competitions.W001
```

Oczekiwane: `authentication = "token"`, `app_id = "olimpiada"`, `asap_accepted_issuers = { "olimpiada" }`,
`asap_accepted_audiences = { "jitsi" }`, w komponencie MUC `token_affiliation` i `token_verification`;
w jicofo `enable-auto-owner = false` i brak bloku `authentication {`; dwa identyczne skróty.
Ręcznie: `https://meet.<domena>/test-bez-tokenu` w przeglądarce prosi o logowanie i nie otwiera pokoju;
koordynator na ekranie „Rozmowy” klika „dołącz jako gospodarz” przy terminie w oknie – wchodzi
z prawami moderatora; konto testowe uczestnika wchodzi bez nich.

### 25.5. Rotacja sekretu

Zmiana sekretu unieważnia **wszystkie** wydane przepustki (rozmowy, próby, wejścia z panelu
i z linków-zaproszeń – każda żyje najwyżej kilka godzin, więc szkoda jest mała). Linki-zaproszenia
pokoi bez terminu **nie** są tokenami (§ 25.8) i rotacja ich nie dotyczy – działają dalej.

1. Poza godzinami rozmów. W `/opt/olimpiada/.env` usuń linię `JITSI_JWT_APP_SECRET=…`.
2. `scripts/deploy_jitsi.sh root@…` – wygeneruje nowy sekret, przepisze go do `jitsi/.env`, odtworzy
   `web worker beat` (bo widzą stary) i odtworzy kontenery Jitsi z nowym `JWT_APP_SECRET`.
3. Kontrole z § 25.4 (dwa identyczne skróty).

Między krokiem 3/5 a 4/5 skryptu (kilkadziesiąt sekund) portal wystawia przepustki z nowym sekretem,
a Jitsi zna jeszcze stary – wejścia w tej chwili się nie udadzą; po kroku 4/5 trzeba kliknąć „Dołącz”
jeszcze raz. Dlatego poza godzinami rozmów.

### 25.6. Co sprawdzono uruchomieniem (2.10.2026, lokalnie, obrazy stable-11031)

Osobny projekt compose (`-p jitsi-jwt-test`, własna sieć zamiast `edge`, porty tylko na 127.0.0.1),
klient XMPP po WebSocket (uwierzytelnienie SASL z tokenem w adresie, jak robi to front Jitsi), potem
`down -v`:

- odmowa uwierzytelnienia: bez tokenu („token required”), zły podpis, `exp` w przeszłości, `nbf`
  w przyszłości, obce `aud`, obce `iss`,
- odmowa wejścia do pokoju („room-mismatch”): token na pokój A w pokoju B, token pokoju w pokoju
  `…-test` i odwrotnie; wejście z tokenem na właściwy pokój – przyjęte,
- role: token z `context.user.moderator = true` → `owner`/`moderator`; bez – `member`/`participant`,
  także po kilku sekundach samotności w pokoju i po wejściu moderatora (z `JICOFO_ENABLE_AUTH=0`;
  z uwierzytelnianiem jicofo uczestnik dostawał `owner` – stąd ta zmienna),
- tokeny wystawione **kodem platformy** (`apps.competitions.jitsi_jwt.issue`) – przyjęte, role jak wyżej,
- front: `…/pokój#jwt="<token>"` – stan aplikacji Jitsi ma token i nazwę z `context.user.name`,
- wycofanie `ENABLE_AUTH=0` – wejście bez tokenu znowu działa.

Nie sprawdzono uruchomieniem: pełnej rozmowy z mediami (JVB), aplikacji mobilnej Jitsi (przejście
z przeglądarki telefonu do aplikacji z tokenem we fragmencie) i `TOKEN_AUTH_URL`.

### 25.7. Wycofanie

Otwarte pokoje jak przed v0.39.0, bez cofania kodu portalu:

```sh
cd /opt/olimpiada/jitsi
sed -i 's/^ENABLE_AUTH=.*/ENABLE_AUTH=0/; s/^ENABLE_AUTO_OWNER=.*/ENABLE_AUTO_OWNER=1/' .env
docker compose -p olimpiada-jitsi --env-file .env -f docker-compose.jitsi.yml up -d
```

`ENABLE_AUTO_OWNER=1` przywraca „pierwszy w pokoju zostaje moderatorem” – bez tokenów nikt inny
moderatora by nie nadał. Portal dalej przekierowuje z przepustką (otwarte Jitsi ją ignoruje), więc
nic po jego stronie nie trzeba zmieniać. Pełne wyłączenie po stronie portalu: usuń
`JITSI_JWT_APP_SECRET` z `.env` i odtwórz `web worker beat` – panel pokazuje wtedy znowu adresy
pokoi, a ekrany „Pokoje wideo” znikają (wiersze pokoi zostają w bazie).

### 25.8. Pokoje bez terminu i linki-zaproszenia

Model `competitions.VideoRoom` (konkurs, etykieta, nazwa pokoju, dwa klucze linków, kto i w jakiej
roli założył, ważność, dostęp komisji, zamknięcie). **Tokenów w bazie nie ma.** Link-zaproszenie to
adres na platformie, `https://<domena>/zaproszenie/wideo/<klucz>/` (klucz: 192 bity z `secrets`),
osobny dla gospodarza i gościa. Bramka: GET pokazuje stronę z etykietą i polem nazwy (nic nie
wystawia – podglądy linków i skanery poczty nie otwierają pokoju), POST „Dołącz” (CSRF, limit
`video_gateway` 120/h na adres IP) wystawia przepustkę na `JITSI_JWT_GATEWAY_MINUTES` minut.
Skutki:

- **zamknięcie pokoju, wygaśnięcie i „Wygeneruj nowy link” działają od razu** dla każdego, kto
  jeszcze nie wszedł; kto jest w trwającej rozmowie, zostaje do jej opuszczenia (Jitsi nie pyta
  platformy drugi raz),
- klucze są przechowywane jawnie (koordynator ma widzieć linki także później) – traktujemy je jak
  poświadczenie: nie trafiają do audytu, logów ani eksportów; pokazanie linków to osobna czynność
  (POST, `no-store`, wpis `video.room_links_viewed`),
- uprawnienie członka komisji do zakładania pokoi (`CommitteeMember.video_room_issuer`) nadaje
  i odbiera koordynator. Odebranie albo zawieszenie członka od razu zamyka mu ekran i wejście
  z panelu do jego pokoi; jego linki-zaproszenia działają, dopóki koordynator nie zamknie jego pokoi
  (przycisk „Zamknij pokoje tej osoby” w tym samym wierszu) – to celowo osobna decyzja,
- limity: zakładanie pokoi i wymiana linków `video_rooms` 10/h na konto, wejścia z panelu `video`
  60/h na konto.

Audyt (bez tokenów, kluczy i nazw gości): `video.room_created`, `video.room_links_viewed`,
`video.room_link_rotated`, `video.room_closed`, `video.room_joined` (rola: `coordinator`,
`committee`, `creator`, `host_link`, `guest_link`), `video.issuer_granted`, `video.issuer_revoked`;
wejścia na rozmowy: `interview.joined` (`participant`/`coordinator`, `interview`/`precheck`).

**Retencja:** wiersze `VideoRoom` są danymi operacyjnymi; jedyną daną osobową jest `created_by`
(znika razem z kontem – `SET_NULL`). Etykieta pokoju jest tekstem koordynatora – podręcznik prosi,
żeby nie wpisywać w nią nazwisk gości.

## 26. Języki interfejsu per konkurs (I18N-01, `docs/tasks/I18N-01.md`)

Od tego wydania **konkurs** decyduje, w jakich językach mówi jego interfejs:
`tenancy.Competition.interface_languages` (zbiór) obok `default_language` (język domyślny). Przełącznik
„angielska wersja interfejsu” w `/cms/` → Ustawienia → Dane serwisu **zniknął** – migracja
`tenancy.0011` przepisała go do zbioru (włączony – na witrynie konkursu albo jego aliasu – →
`["pl", "en"]`, wyłączony → `[język domyślny]`). Kolumna zostaje w tym wydaniu, nieczytana (§ 26.5).

Instalacja zna 11 języków (`settings.LANGUAGES`): polski oraz dziesięć najczęściej używanych języków
świata – `en`, `zh-hans`, `hi`, `es`, `ar` (od prawej do lewej), `fr`, `bn`, `pt`, `ru`, `id`.

### 26.1. Co ustawić

- **Olimpiada Kwantowa (`kwantowa`) – nic.** Migracja zostawia `["pl"]`: brak przełącznika języka,
  strona po polsku niezależnie od przeglądarki, ciasteczka i zapisu na koncie.
- **International Quantum Olympiad (`iqo`)** – po wdrożeniu, jedną z trzech dróg:
  ```sh
  docker compose exec web python manage.py competition_languages iqo \
      en zh-hans hi es ar fr bn pt ru id pl --default en
  ```
  albo ekran koordynatora `/coordinator/competition/` („Języki interfejsu”, „Język domyślny”),
  albo `/admin/` → Konkursy. Komenda bez kodów tylko pokazuje stan; zapis zostawia wpis audytu
  `competition.languages_changed`. Dopiero po tym kroku `iqo` pokazuje menu języków (glob w pasku
  konta), a gość bez ustawień przeglądarki dostaje angielski.
- **`iqo` – marka w listach:** treść i tematy listów do uczestników niosą nazwę konkursu dopiero przy
  przełączniku `competition_branding_in_mail` (`/admin/` → Konkursy → `iqo` → „przełączniki”:
  `"competition_branding_in_mail": true`). Bez niego listy `iqo` mówią „Quantum Olympiad” (przekład
  marki Olimpiady Kwantowej). Olimpiada Kwantowa ma go wyłączonego i tak ma zostać.

### 26.2. Jak serwis wybiera język

Zapis na koncie → ciasteczko `django_language` → `Accept-Language` (warianty: `zh-CN` → `zh-hans`,
`pt-BR` → `pt`) → `default_language` konkursu → pierwszy język zbioru. Każde źródło jest przycięte
do zbioru konkursu. Listy do uczestnika idą w jego języku (`language_for`), a gdy go nie wybrał –
w języku domyślnym konkursu.

### 26.3. Tłumaczenia – maszynowe, do przeglądu

Katalogi `backend/locale/<kod>/LC_MESSAGES/django.po` dla `zh_Hans`, `hi`, `es`, `ar`, `fr`, `bn`,
`pt`, `ru`, `id` są **tłumaczeniem maszynowym** (model językowy, z polskiego z angielskim jako
odniesieniem). Przed szeroką komunikacją do uczestników z danego kraju warto dać plik `.po` do
przeglądu native speakerowi (każdy edytor PO, np. Poedit). Po poprawkach: `django-admin
compilemessages` (obraz robi to przy budowaniu; testy `apps/core/tests/test_translations.py`
pilnują kompilacji, kompletu tłumaczeń i zgodności placeholderów). Przegląd **w serwisie** przez
wolontariuszy (kierowników delegacji) bez plików i bez gita: § 33.

### 26.4. Czego nie tłumaczymy

- **Treść stron w `/cms/`** – jest jednojęzyczna (dla `iqo`: angielska). Interfejs dookoła niej
  (menu, stopka, formularze, panel uczestnika) mówi językiem wybranym przez czytelnika. Drugie
  drzewo treści w innym języku to istniejący mechanizm aliasu witryny (`content_translations`,
  `WAGTAIL_I18N_ENABLED`, osobna subdomena) – bez zmian w kodzie, ale z DNS-em i Caddym.
- **Ekrany koordynatora, komisji i operatora** – zostają po polsku.
- **Napisy w skryptach JS** (podgląd wgrywanego pliku, wybór szkoły, szyfrowanie czatu) – po polsku;
  lista w `docs/tasks/I18N-01.md` § 14.
- Treść zadań: pola `title_en`/`statement_pdf_en` obsługują **każdy** język poza polskim.
- Nazwa „Olimpiada Kwantowa” w dyplomach PDF, protokołach i nagłówkach kilku stron publicznych
  (zgoda opiekuna, przyjęcie zaproszenia) – do osobnego zadania marki.

### 26.5. Kolumna `cms.SiteSettings.english_interface_enabled` – usunięcie w następnym wydaniu

Kolumna jest od tego wydania **nieczytana** i nie ma jej w panelu. Nie kasujemy jej w tym samym
wydaniu, bo w chwili `migrate` stare procesy `worker` i `beat` jeszcze działają na starym kodzie
i czytają ją przy każdej wysyłce listu – skasowana wywracałaby ich zapytania do czasu restartu.
Następne wydanie dokłada migrację `RemoveField` (i usuwa pole z modelu); do tego czasu kolumna
jest martwa, a jej wartość niczego nie zmienia.

## 27. Kraje zamiast województw (REG-01, `docs/tasks/REG-01.md`)

Konkurs międzynarodowy (`iqo`) dzieli uczestników na **kraje**. Przestawienie to jedna komenda –
idempotentna, w jednej transakcji, z wpisem audytu `regions.countries_enabled`:

```sh
docker compose exec web python manage.py regions_countries --competition iqo --dry-run   # podgląd
docker compose exec web python manage.py regions_countries --competition iqo
```

Komenda włącza flagę `custom_regions`, zakłada brakujące kraje z `apps/accounts/countries.py`
(199 pozycji, kody ISO 3166-1 alfa-2 małymi literami, nazwy angielskie), **dezaktywuje** 16 województw
i „poza Polską” (wiersze zostają – mogą na nie wskazywać profile), a region `pl` przemianowuje na
„Poland”, o ile nikt wcześniej nie zmienił jego nazwy. Wydruk podaje liczbę uczestników, których region
jest teraz nieaktywny – tych trzeba przypisać do kraju ręcznie w karcie uczestnika.

Nowy konkurs od razu z krajami: `create_competition … --regions countries` (domyślnie
`voivodeships` – bez zmiany zachowania). **Olimpiada Kwantowa nie wymaga niczego** (flaga
`custom_regions` wyłączona, formularze i wydruki co do bajtu jak dotąd).

Kolejność dla `iqo` po wdrożeniu: § 26.1 (języki) i ta komenda – niezależne od siebie.

## 30. Motywy wizualne (THEME-01, `docs/tasks/THEME-01.md`)

Wygląd konkursu zmienia się **paczką motywu** (ZIP: `manifest.json`, `theme.css`, `tokens.json`,
`assets/`, opcjonalnie `templates/theme/*.html` i `screenshot.png`), bez wydania aplikacji. Paczka nie
wykonuje kodu Pythona i nie dokłada JavaScriptu; przy wgraniu przechodzi walidację (ścieżki ZIP, bomba
ZIP, typy i magiczne bajty plików, CSS przez parser, SVG oczyszczane, lint i kompilacja szablonów,
tokeny) i skan ClamAV. Konkurs bez motywu (Olimpiada Kwantowa) nie zmienia się ani o bajt HTML.

### 30.1. Wgranie i aktywacja na produkcji

```sh
# 1. paczka na serwer (z laptopa)
scp -i ~/.ssh/olimpiada_deploy iqo-quantum-1.0.0.zip deploy@<serwer>:/tmp/
# 2. wgranie (walidacja + ClamAV + publikacja do bucketu public-media) i aktywacja w konkursie
ssh -i ~/.ssh/olimpiada_deploy deploy@<serwer>
cd /opt/olimpiada
docker compose exec -T web python manage.py theme_install - --activate iqo < /tmp/iqo-quantum-1.0.0.zip
```

Kod wyjścia ≠ 0 = paczka odrzucona (błędy na ekranie, wersja „odrzucona” z raportem w katalogu).
Bez `--activate` motyw czeka w katalogu; wybiera go koordynator konkursu w panelu
(**„Motyw serwisu”**, przełącznik konkursu `themes` – włącza operator jak każdą flagę, § 6) albo
superkoordynator w `/coordinator/platform/themes/` (katalog: wgranie przez przeglądarkę, raport,
usunięcie nieużywanej wersji). Aktywacja czyści pełnostronicowy cache gościa tego konkursu sama.

**Cofnięcie:** wybór poprzedniej wersji (albo „Klasyczny”) w panelu. Z konsoli (z wpisem audytu):

```sh
docker compose exec -T web python manage.py shell -c "from apps.tenancy.models import Competition; from apps.themes.services import activate; activate(Competition.objects.get(slug='iqo'), None)"
```

Wersje zostają, dopóki operator ich nie usunie; wersji używanej przez konkurs nie da się usunąć
(`PROTECT`).

**Awaryjnie** (motyw psuje stronę): superkoordynator dopisuje do adresu `?theme=off` – strona
renderuje się bez motywu tylko dla niego; ekran „Motyw serwisu” i katalog motywów są zawsze bez
motywu, więc przycisk przywrócenia „Klasycznego” jest zawsze widoczny. Szablony slotów z paczki
działają wyłącznie na stronach publicznych (CMS, statystyki, plakaty, wyniki, weryfikacja dyplomu);
panele, logowanie i formularze mają zawsze ramę aplikacji (tokeny i arkusz motywu – tak).

### 30.2. Pliki w buckecie, CSP, CORS

- Pliki publiczne leżą w `public-media` pod **niezmiennym** prefiksem `themes/<slug>/<wersja>-<sha8>/`
  z `Cache-Control: public, max-age=31536000, immutable` (nowa wersja = nowy prefiks). Szablony
  i manifest nie trafiają do bucketu (są w bazie), paczka ZIP – do bucketu prywatnego.
- **CSP:** strona z motywem dostaje origin bucketu (`S3_PUBLIC_ENDPOINT_URL`) także w `style-src`
  i `font-src` (w `img-src` był już wcześniej). `script-src` nie zmienia się nigdy; strona bez motywu
  ma politykę co do bajtu dawną.
- **CORS dla krojów:** przeglądarka pobiera `woff2` z innego originu (`:9000`) w trybie CORS. MinIO
  odpowiada `Access-Control-Allow-Origin` z originem żądania dla każdego originu (ustawienie domyślne
  `MINIO_API_CORS_ALLOW_ORIGIN=*`, § 16.3), a blok S3 w Caddy nagłówków CORS nie rusza – sprawdzone
  w devie (`curl -H "Origin: https://olimpiadakwantowa.pl" -I …/public-media/themes/…/x.woff2`).
  **Jeżeli kiedyś zawęzicie CORS MinIO**, dopiszcie do listy domeny wszystkich konkursów – inaczej
  motyw cicho spadnie na kroje systemowe.
- Arkusz motywu odwołuje się do swoich plików adresami **względnymi** (`url("assets/…")`), więc
  przeniesienie bucketu pod własną domenę S3 nie wymaga ponownego wgrywania motywów.

### 30.3. Nowa zależność

`tinycss2` (parser CSS) jest w `backend/pyproject.toml` – obraz `web`/`worker` musi być **przebudowany**
(CI buduje go z pyproject). Bez niej import walidatora się nie powiedzie dopiero przy wgraniu paczki;
render stron z już aktywnym motywem jej nie potrzebuje.

### 30.4. Menu serwisu z panelu (THEME-02, `docs/tasks/THEME-02.md` § 1)

Koordynator konkursu z flagą `themes` ustawia menu stron publicznych w **„Motyw serwisu → Menu
serwisu”** (`/coordinator/competition/theme/menu/`): kolejność, ukrycie, nazwy per język interfejsu,
własne odnośniki (`https://…`, `http://…` albo ścieżka `/…`; nic innego – `javascript:`, `data:`,
`//host` są odrzucane) i grupy rozwijane (jeden poziom). Działa z każdym motywem, także z „Klasycznym”.

- **Dane:** tabela `themes_sitemenu` (wiersz na konkurs, lista JSON + rewizja). Rewizja jest powielona
  w `Competition.theme_options["menu"]`; bez tego klucza menu buduje się jak dotąd i **bez zapytania**
  (Olimpiada Kwantowa bez nadpisań – bez zmian). Zapis unieważnia cache gościa konkursu.
- **Audyt:** `theme.menu_saved` (przed/po), `theme.menu_reset`.
- **Cofnięcie:** przycisk „Przywróć menu domyślne” albo z konsoli:
  ```sh
  docker compose exec -T web python manage.py shell -c "from apps.tenancy.models import Competition; from apps.themes.services import reset_menu; reset_menu(Competition.objects.get(slug='iqo'))"
  ```
- Nowa strona dodana w `/cms/` po zapisaniu menu pojawia się **na końcu** menu (nic nie znika po cichu);
  własny odnośnik do strony wycofanej z publikacji znika z menu sam.

### 30.5. Kolory, schemat, logo i kroje z panelu (THEME-02 § 2)

**„Motyw serwisu → Kolory i opcje motywu”** (`/coordinator/competition/theme/customize/[?version=<id>]`):
schemat (jasny/ciemny/systemowy – gdy `tokens.json` motywu ma obie palety), wariant logo i para krojów
(nowe, opcjonalne pola manifestu `logos`/`fonts`), warianty układów i kolor każdego tokenu palety
z `tokens.json`. Kontrast liczony na serwerze: para poniżej WCAG AA (4.5:1 tekst, 3:1 obwódka fokusu),
którą zmienił koordynator, **blokuje zapis**.

- **Dane:** `themes_themecustomization` (konkurs + wersja motywu); kopia opcji wersji aktywnej
  w `Competition.theme_options` (`scheme`, `logo`, `font`, `colors`). Powrót do wcześniejszej wersji
  w galerii przywraca jej kolory.
- **Arkusz:** `/_theme/custom.css?s=<podpis>` z własnej domeny (CSP `'self'` – **polityka bez zmian**),
  dołączany **po** `theme.css` (kolejność: `tokens.css` → `theme.css` → `custom.css` → akcent marki),
  `Cache-Control: immutable`; podpis (`SECRET_KEY`, sól `apps.themes.custom`) obejmuje konkurs, wersję
  i opcje, więc adres nie generuje arkuszy z dowolnymi kolorami ani dla cudzego konkursu.
  **Zmiana `SECRET_KEY`** unieważnia te adresy: strony w cache gościa (≤ `PAGE_CACHE_SECONDS`)
  przez chwilę linkują arkusz 404 (motyw bez dostosowania), potem renderują się z nowym podpisem.
- **Audyt:** `theme.customized` (przed/po, wersja, czy aktywna), `theme.customization_reset`.
- **Cofnięcie:** „Przywróć domyślne” na ekranie albo:
  ```sh
  docker compose exec -T web python manage.py shell -c "from apps.tenancy.models import Competition; from apps.themes.models import ThemeVersion; from apps.themes.services import reset_customization; c = Competition.objects.get(slug='iqo'); reset_customization(c, c.theme_version)"
  ```
- Tryb wysokiego kontrastu (`data-contrast="high"`) dalej wygrywa – dostosowanie zmienia tokeny
  `--t-*`, a tryb kontrastu nadpisuje role.

### 30.6. Limit i uprawnienia

Oba ekrany: wyłącznie koordynator konkursu, którego domeną przyszło żądanie (inny konkurs – 403),
flaga `themes` (wyłączona – 404), POST-y limitem `theme_settings` = **120/h na konto**
(`REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`). Ekrany renderują się zawsze **bez** motywu.

### 30.7. Nowy slot `nav` i paczka IQO Quantum 1.1.0

Lista slotów ma nowy, ósmy slot `nav` (samo menu serwisu – `templates/theme/nav.html`); domyślny
nagłówek woła go w miejscu dawnego `<nav class="nav nav--cms">` (Olimpiada Kwantowa co do bajtu).
Paczka `themes/iqo-quantum/` w wersji **1.1.0** korzysta z `nav`, `logos`, `fonts` i obu palet,
więc wymaga aplikacji z THEME-02 – manifest ma `min_app_version` **0.44.0** (wgranie na starszej
wersji: błąd „Motyw wymaga wersji aplikacji 0.44.0…”; `APP_VERSION=dev` pomija porównanie).

Wdrożenie (po wdrożeniu aplikacji z THEME-02 – migracja `themes.0003` idzie w `scripts/deploy.sh`):

```sh
python themes/iqo-quantum/build_zip.py                     # → themes/iqo-quantum/dist/iqo-quantum-1.1.0.zip
scp -i ~/.ssh/olimpiada_deploy themes/iqo-quantum/dist/iqo-quantum-1.1.0.zip deploy@<serwer>:/tmp/
ssh -i ~/.ssh/olimpiada_deploy deploy@<serwer>
cd /opt/olimpiada
docker compose exec -T web python manage.py theme_install - --activate iqo < /tmp/iqo-quantum-1.1.0.zip
```

`--activate` przenosi z wersji dotychczasowej **wyłącznie** warianty układów (te, które 1.1.0 też ma),
akcent marki, schemat, logo i kroje (o ile 1.1.0 je deklaruje) oraz menu – **bez** kolorów i promieni
dostosowania (tokeny nowej wersji mogą znaczyć co innego). Kolory zapisane wcześniej dla samej 1.1.0
wracają, o ile przechodzą kontrolę kontrastu; odrzucone komenda wypisuje jako „kolory dostosowania
pominięte (kontrast)”. **Uwaga:** `iqo` ma dziś `footer=compact` z 1.0.0, a ten wariant
istnieje też w 1.1.0 – zostanie, choć nowym domyślnym jest `columns`; nagłówek `minimal` w 1.1.0 nie
istnieje, więc wraca do domyślnego `split`. Po aktywacji: galeria → karta 1.1.0 → stopka `columns` →
„Zapisz opcje” (albo „Kolory i opcje motywu”). Bez `--activate` wersja czeka w galerii („Podgląd”,
potem „Aktywuj”). Sprawdzenie po wgraniu: strona główna gościa w en i ar (`curl -s https://iqo-official.org/
| grep -o 'data-theme="[^"]*"'` → `iqo-quantum`), `/coordinator/competition/theme/customize/` pokazuje
obie palety. Cofnięcie: aktywacja 1.0.0 w galerii (wersja zostaje w katalogu).

Kontekst szablonów paczek dostał w THEME-02 także `sponsor_slider` (same napisy i liczby – IQO 1.1.0
stawia taśmę sponsorów w stopce), a dostosowanie – promienie `radius-*` z `tokens.json`
(0–48 px albo 0–3 rem; IQO: `radius-leaf`, kształt przycisków).


## 28. Delegacje krajowe – rejestracja przez opiekunów drużyn (DEL-01, `docs/tasks/DEL-01.md`)

W konkursie w trybie **`DELEGATIONS`** uczniów zgłaszają opiekunowie drużyn narodowych (team leaders)
zaproszeni przez koordynatora. Samodzielna rejestracja uczestnika jest wtedy zamknięta **na każdej
drodze**: formularz `/register/`, `POST /api/auth/register/participant/`, Google/Facebook (konto nowe),
import listy klasowej (opiekun szkolny i koordynator) oraz rejestracja opiekuna szkolnego
(`/register/supervisor/` → 404). Logowanie istniejących kont działa normalnie.

**Domyślnie każdy konkurs ma tryb `OPEN`** (migracja `tenancy.0013` wpisuje `OPEN` wszystkim
istniejącym konkursom; `create_competition`, ekran „Nowy konkurs” i kreator `/setup/` zakładają
`OPEN`, a katalog szablonów nie ma tego pola). Olimpiada Kwantowa nie wymaga niczego.

### 28.1. Przestawienie `iqo` (kolejność)

1. Kraje (§ 27): `docker compose exec web python manage.py regions_countries --competition iqo`.
2. Bieżąca edycja `iqo` musi istnieć, a jej okno rejestracji (`/coordinator/registration/`) **obowiązuje
   opiekunów**: dodanie ucznia wymaga `registration_enabled = tak` oraz daty „teraz” między otwarciem
   a zamknięciem (puste daty = bez ograniczenia). Przy zamkniętym oknie opiekun nie doda ucznia, a pulpit
   koordynatora pokazuje „przez delegacje krajowe – okno dla opiekunów drużyn zamknięte”.
   Tryb `DELEGATIONS` da się zapisać dopiero, gdy konkurs ma aktywne kraje (walidacja modelu).
3. Tryb rejestracji – jedna z dróg:
   - panel: `/coordinator/competition/` (ekran „Ustawienia konkursu”, flaga `competition_settings_page`)
     → „Tryb rejestracji uczestników” = „przez delegacje krajowe”, opcjonalnie „Domyślny limit uczniów
     delegacji” (domyślnie 6); zapis zostawia wpis audytu `competition.registration_mode_changed`,
   - `/admin/` → Konkursy → `iqo` → te same dwa pola,
   - powłoka (bez panelu):
     ```sh
     docker compose exec web python manage.py shell -c "from apps.tenancy.models import Competition; c = Competition.objects.get(slug='iqo'); c.registration_mode = 'DELEGATIONS'; c.save(update_fields=['registration_mode'])"
     ```
4. W menu panelu `iqo` pojawia się „Uczestnicy i konta → Delegacje” (`/coordinator/delegations/`).
   „Zaproś opiekuna”: adres e-mail + kraj. Delegacja kraju powstaje przy pierwszym zaproszeniu; kolejny
   opiekun tego kraju dołącza do niej. List idzie w języku domyślnym konkursu (dla `iqo` – angielskim).

Nowy konkurs od razu w tym trybie: `create_competition … --regions countries --registration delegations`
(delegacje wymagają podziału na kraje; domyślnie `--registration open`).

### 28.2. Zaproszenie, konto opiekuna, uczniowie

- Zaproszenie: ważne 14 dni, jednorazowe, w bazie tylko skrót SHA-256 tokenu; „Wyślij ponownie” wymienia
  token (stary link przestaje działać); „Cofnij” unieważnia. Adres prowadzący już delegację innego kraju
  w tej edycji dostaje odmowę.
- Przyjęcie (`/delegation/accept/<token>/`): adres bez konta zakłada je od razu aktywne (kliknięcie linku
  potwierdza adres) i składa zgody (regulamin, RODO); adres z kontem musi się zalogować – zaproszenie nie
  zmienia hasła; zalogowany na inne konto dostaje odmowę.
- Panel opiekuna `/delegation/`: uczniowie kraju, współopiekunowie, „Dodaj ucznia”. Uczeń dostaje list
  z linkiem `/zaproszenie/<token>/` (ten sam mechanizm, co import listy klasowej): sam ustawia hasło
  i składa zgody, kraj jest krajem delegacji. Limit delegacji liczony pod blokadą wiersza.
- Wypisanie ucznia przez opiekuna (do startu pierwszego etapu): konto **nieuruchomione** jest usuwane;
  konto **uruchomione** zostaje – opiekun tylko odpina je od delegacji, uczeń dostaje list, a ekran
  delegacji pokazuje go w sekcji „Wypisani przez opiekuna – czekają na decyzję”. Usunięcie takiego konta
  należy do koordynatora (karta konta w „Uczestnicy i konta”).
- Odwołanie opiekuna zostawia jego wiersz ze znacznikiem `removed_at` (dowody zgód zostają); opiekun
  bez delegacji w bieżącej edycji widzi pod `/delegation/` wyjaśnienie, a nie błąd.
- Zamknięcie delegacji (ekran delegacji) zamraża listę uczniów. Eksport CSV: przycisk na liście delegacji.
- Opiekun drużyny **nie** ma dostępu do wiadomości (`apps/chat`) ani do prac i ocen.

### 28.3. Kontrakt adresów

Nowy pierwszy segment adresu aplikacji: `delegation/` (`RESERVED_SLUGS`, `backend/djcms_contract/` –
zaktualizowane w tym wydaniu). Wdrożenie przez `scripts/deploy.sh` przenosi kontrakt; jeśli Caddyfile
jest renderowany z `app_routes.env` osobno, trzeba go wyrenderować ponownie.

### 28.4. Wycofanie

Przestawienie trybu z powrotem na `OPEN` otwiera samodzielną rejestrację i ukrywa ekrany delegacji (404);
dane delegacji, opiekunów i uczniów zostają w bazie. Migracje `accounts.0036`–`0038` i `tenancy.0013` są
odwracalne (nowe tabele i kolumny nullowalne albo z wartością domyślną).

## 31. Logistyka finału dla delegacji (LOG-01, `docs/tasks/LOG-01.md`)

Aplikacja `apps.delegation_logistics`: dane pobytu członków delegacji (paszport do wizy, przylot,
pokój, dieta, koszulka, kontakt alarmowy, zdjęcie), listy zapraszające do wizy z rejestrem numerów,
identyfikatory z kodem QR i odhaczanie obsługi na telefonach. Działa **wyłącznie** w konkursie
w trybie `DELEGATIONS` (§ 28) **z** flagą `onsite_logistics`. Olimpiada Kwantowa nie widzi niczego.

### 31.1. Włączenie dla `iqo` (kolejność)

1. Migracje wydania: `delegation_logistics.0001`–`0002` (nowe tabele; dieta szyfrowana) i
   `tenancy.0015_document_kind_visa_invitation` (nowy rodzaj szablonu dokumentu „list zapraszający
   (wiza)” – sama lista wyboru, po `tenancy.0014_merge_20261004_1935`). `scripts/deploy.sh` je wykona.
2. Flaga konkursu (jedna z dróg):
   - `/admin/` → Konkursy → `iqo` → `feature_flags`: dopisać `"onsite_logistics": true`,
   - powłoka:
     ```sh
     docker compose exec web python manage.py shell -c "from apps.tenancy.models import Competition; c = Competition.objects.get(slug='iqo'); c.feature_flags = {**(c.feature_flags or {}), 'onsite_logistics': True}; c.save(update_fields=['feature_flags'])"
     ```
   Flaga włącza też istniejące ekrany logistyki etapu (`/coordinator/venues/`, przyjazdy i obecność
   etapu) – w `iqo` nieszkodliwe, a przełącznik „zbieraj potrzeby szczególne” na ekranie
   `/coordinator/venues/` jest **tą samą** decyzją D21 dla danych o zdrowiu w logistyce finału.
3. Panel `iqo` → „Uczestnicy i konta → Logistyka finału” (`/coordinator/logistics/`) → „Ustawienia
   i dostęp”: nazwa, miasto, daty finału, terminy pięciu sekcji, retencja (dni po ostatnim dniu,
   domyślnie 30), prefiks numeru listów (np. `IQO`). **Bez ostatniego dnia finału serwis nie przyjmuje
   danych paszportowych ani o zdrowiu** – retencja nie miałaby od czego liczyć terminu usunięcia.
4. **Oficer logistyki** – przydział „oficer logistyki” dla 1–2 koordynatorów (pierwszy przydział może
   nadać dowolny koordynator; kolejne – superkoordynator albo oficer). Tylko oficer widzi dane osób.
5. **Obsługa rejestracji** – przydział „obsługa rejestracji” dla kont wolontariuszy (konto musi
   istnieć; rola w konkursie niepotrzebna). Nadaje go **oficer** (albo superkoordynator), nigdy samemu
   sobie. Przypomnienia o brakach wysyła również wyłącznie oficer. Punkty kontroli („Przyjazd”, „Ceremonia otwarcia”…)
   w tej samej zakładce.
6. Dane o zdrowiu (dieta, alergie, uwagi medyczne) – dopiero po decyzji organizatora: `/coordinator/venues/`
   → „zbieraj potrzeby szczególne”. Bez niej sekcja „Wyżywienie i zdrowie” nie istnieje.
7. Tekst listu wizowego: przy fladze `document_templates` – „Szablony dokumentów” → „list zapraszający
   (wiza)” (znaczniki `{event}`, `{event_dates}`, `{city}`, `{venue}`, `{country}`, `{number}`, `{date}`,
   `{organizer}`). Bez szablonu obowiązuje tekst wbudowany po angielsku. Podpisy: bloki podpisu
   z szablonu graficznego dyplomów (rodzaj „wszystkie”); pieczęć elektroniczna – jak dyplomy
   (`CERT_SIGN_P12_PATH`).

### 31.2. Szyfrowanie i klucz

Numer, data ważności i nazwisko z paszportu, data urodzenia, dane o zdrowiu (z dietą) i kontakt alarmowy są
szyfrowane w bazie (Fernet, klucz wyprowadzony z `SECRET_KEY` z etykietą `delegation-logistics`).
**Rotacja `SECRET_KEY`**: stary klucz **musi** zostać w `SECRET_KEY_FALLBACKS` do końca retencji
finału – inaczej zapisane dane stają się nieczytelne (ekran pokaże puste pola, w logu ostrzeżenie
„nie udało się odszyfrować pola”). Kopia zapasowa bazy bez `SECRET_KEY` nie odsłania tych danych.

### 31.3. Zdjęcia i skan

Zdjęcia do identyfikatorów idą do prywatnego magazynu rozwiązań (`final-badges/…`) i przez ClamAV
(kolejka `scan`, zadanie `apps.delegation_logistics.tasks.scan_badge_photo`) – worker `scan` musi
działać. Zdjęcie jest widoczne dopiero po czystym skanie; zainfekowane jest usuwane. Po czystym skanie
jest przekodowywane (Pillow) do JPEG-a najwyżej 600×800 bez metadanych EXIF; obraz ponad 40 Mpx jest
odrzucany już przy wgraniu, a plik nieczytelny dla Pillow kończy jak błąd skanu. Skan porzucony po
wyczerpaniu ponowień (ClamAV niedostępny) też kończy się błędem – opiekun widzi prośbę o ponowne wgranie.

### 31.4. Retencja

Zadanie dobowe `delegation-logistics-purge-expired` (`CELERY_BEAT_SCHEDULE`, `DatabaseScheduler`
dopisze je przy starcie beat) usuwa po `ends_on + retencja` wszystkie dane członków delegacji edycji
(z plikami zdjęć) i migawki osób z listów; rejestr listów zostaje (numer, kraj, data, liczba osób).
Ręcznie (np. test na kopii):
```sh
docker compose exec web python manage.py shell -c "from apps.delegation_logistics.privacy import purge_expired; print(purge_expired())"
```

### 31.5. Obsługa na miejscu

Identyfikatory: „Osoby” → wybór kraju → „Identyfikatory PDF (ten kraj)” albo karta osoby (A4, cztery
karty A6). Wydruku wszystkich naraz nie ma – kilkaset kart ze zdjęciami w jednym żądaniu WWW to
pamięć i limit czasu workera. Kod QR zawiera wyłącznie adres
`/coordinator/logistics/checkin/<token>/` – bez danych osobowych; aparat telefonu otwiera go sam
(obsługa musi być zalogowana). Zgubiona karta: karta osoby → „Wydaj nowy identyfikator” (stary kod
przestaje działać). Limity żądań: `onsite_logistics` 600/h i `onsite_checkin` 3000/h na konto.

### 31.6. Wycofanie

Wyłączenie flagi ukrywa ekrany (404) i pozycję menu; dane zostają do retencji albo do ręcznego
`purge_event`. Migracje są odwracalne (nowe tabele; `tenancy.0015` zmienia wyłącznie listę wyboru).

### 31.7. Pokoje po zmianie danych albo daty finału

Zmiana płci, daty urodzenia albo „bez noclegu” u osoby z pokojem **zdejmuje przydział**, gdy osoba
przestaje spełniać zasady pokoju (wpis audytu `logistics.room_unassigned`, komunikat dla zapisującego).
Zmiana pierwszego dnia finału niczego nie przenosi sama – pokoje z naruszeniem są oznaczone na
zakładce „Pokoje” i w kolumnie „naruszenie zasad pokoju” rooming listy CSV, a komunikat po zapisie
ustawień podaje ich liczbę. Niepełnoletni z płcią „inna” mieszka w pokoju jednoosobowym.

### 31.8. Listy zapraszające – wnioski, weryfikacja, unieważnienie (VISA-01, `docs/tasks/VISA-01.md`)

Ekrany wniosków stoją na tej samej bramce (flaga `onsite_logistics` + tryb `DELEGATIONS`) – **nic do
włączenia** poza krokami § 31.1. Strona weryfikacji ma bramkę własną: istnieje w konkursie, który
wystawił choć jeden list – także po wyłączeniu logistyki albo zmianie trybu rejestracji (list leży
w konsulacie dłużej niż trwa logistyka finału). Wdrożenie:

0. **Przed wdrożeniem – slug `visa` na produkcji.** Adres `/visa/…` należy od tego wydania do aplikacji;
   strona CMS o slugu `visa` na drugim poziomie drzewa stałaby się nieosiągalna pod `/visa/verify/…`.
   W repozytorium (seed, fikstury) takiej strony nie ma; na produkcji sprawdź:
   ```sh
   docker compose exec web python manage.py shell -c "from wagtail.models import Page; print(list(Page.objects.filter(slug='visa').values_list('url_path', flat=True)))"
   ```
   Wynik z `…/visa/` na trzecim poziomie (`/home/visa/`) – zmień slug strony przed wdrożeniem.
1. Migracja `delegation_logistics.0003_visa_letter_workflow` (`scripts/deploy.sh`): nowa tabela
   wniosków, nowe kolumny rejestru listów; listy wystawione wcześniej dostają kod weryfikacyjny
   i migawkę wydarzenia z ustawień finału (adres weryfikacji tych listów liczy się z bieżącego
   adresowania). **Nie cofaj tej migracji po wystawieniu pierwszego listu**: cofnięcie usuwa kolumnę
   z kodami, a ponowne zastosowanie nadaje kody **nowe** – kody wydrukowane na listach przestają działać.
2. **Kontrakt tras:** nowy pierwszy segment adresu `visa/` (`/visa/verify/`, `/visa/verify/<kod>/`) –
   `RESERVED_SLUGS` i `backend/djcms_contract/` zaktualizowane w tym wydaniu. Jeśli Caddyfile jest
   renderowany z `app_routes.env` osobno, wyrenderuj go ponownie (inaczej po przełączeniu na djcms
   adres weryfikacji trafi do djcms i kod QR na listach przestanie działać).
3. Limit żądań `visa_verify` – 60/h na adres IP (`REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`).
   Konsulat sprawdzający kolejkę wnioskodawców za jednym adresem mieści się z zapasem.
4. **Teksty listów w 7 językach** (`apps/delegation_logistics/letter_texts.py`: en, pl, es, fr, pt, ru,
   id) są tłumaczeniem maszynowym poza angielskim – przed pierwszym listem w danym języku daj tekst do
   przejrzenia prawnikowi organizatora. Chiński, hindi, bengalski i arabski nie są dostępne (krój
   DejaVu nie ma tych znaków). Szablon z bazy (`document_templates`, „list zapraszający (wiza)”) jest
   jednojęzyczny i ma pierwszeństwo – wtedy język zmienia tylko etykiety tabeli i ramkę weryfikacji;
   w szablonie można użyć `{code}` (kod weryfikacyjny).
5. Przydział oficera logistyki (§ 31.1 p. 4) jest warunkiem decyzji – zwykły koordynator dostaje 403.
   Oficer klikający kody w rejestrze listów nie zużywa limitu `visa_verify`.
6. Strony `/visa/verify/…` i `/dyplomy/<kod>/` nie ładują tagu Google Analytics (`no_analytics`
   w `base.html`) – kod z dokumentu w adresie nie trafia do statystyk.

**Reguły, które warto znać przy obsłudze zgłoszeń:**
- nowy list imienny (z wniosku albo wystawiony przez oficera z karty osoby) unieważnia wcześniejszy
  list tej osoby **tylko**, gdy zmienił się numer paszportu, nazwisko z paszportu albo obywatelstwo;
  przy tych samych danych oba listy zostają ważne. Ekran wniosków pokazuje „unieważni list …” przed
  zatwierdzeniem; list **delegacji** z nieaktualnymi danymi nie jest unieważniany sam – rejestr listów
  oznacza go „nieaktualne dane: …”,
- wypisanie osoby z delegacji (uczeń odpięty, opiekun odwołany, gość usunięty) unieważnia jej listy
  imienne z powodem „osoba wypisana z delegacji”; usunięcie konta przed końcem wydarzenia czyści dane
  osoby z listu, a strona weryfikacji pokazuje list jako „nieważny”. Retencja po finale tylko czyści
  dane (i powody unieważnień) – strona mówi wtedy „dane usunięte”.

**Zmiana domeny albo prefiksu konkursu po wystawieniu listów.** Kod QR niesie adres z chwili wystawienia
(`InvitationLetter.verification_base_url`), także w PDF-ie pobranym ponownie. Po zmianie adresowania:
```sh
docker compose exec web python manage.py visa_letter_redirects iqo --dry-run   # ile listów ma stary adres
docker compose exec web python manage.py visa_letter_redirects iqo             # przekierowania Wagtaila
```
Komenda zakłada przekierowania ze starej ścieżki z kodem (np. `/stary-prefiks/visa/verify/<kod>/`) na
dzisiejszy adres listu; działa, dopóki stary host trafia na nasz serwer. Gdy stara **domena** trafia do
innego konkursu z listami, widok weryfikacji przekierowuje sam (po zapamiętanym adresie listu). Domena
porzucona całkiem (DNS wskazuje gdzie indziej) – kody działają już tylko przez nowy adres i formularz
`/visa/verify/` (kod przepisany ręcznie).

Sprawdzenie po wdrożeniu (na `iqo`, z oficerem): wystaw list próbny z karty osoby → pobierz PDF → zeskanuj
QR telefonem (ma otworzyć `https://<domena iqo>/visa/verify/<kod>/` ze stanem „ważny”) → „Unieważnij”
z powodem „test” → strona pokazuje „unieważniony”.

Wycofanie: wyłączenie flagi ukrywa ekrany wniosków i rejestru, ale **nie** strony weryfikacji (te
istnieją, dopóki konkurs ma wystawione listy). Migracja jest odwracalna schematem (nowa tabela, nowe
kolumny nullowalne albo z wartością domyślną) – z zastrzeżeniem kodów z punktu 1.

## 36. Webinary w LiveKit (WEB-01, `docs/tasks/WEB-01.md`)

Koordynator planuje webinary w panelu (`Komunikacja → Webinary`); uczestnicy, komisja i (opcjonalnie)
goście wchodzą do **pokoju na platformie** (`/webinars/<id>/room/`, nasz interfejs, motyw konkursu,
11 języków). Obraz i dźwięk przenosi **LiveKit** (Apache 2.0, serwer własny). Platforma: wystawia
tokeny wejścia (10 min), wydaje polecenia serwerowe (głos, usunięcie, zamknięcie pokoju, nagrywanie
i transmisja – Egress), przyjmuje podpisane webhooki (stan pokoju, obecność, koniec nagrania).
Bez konfiguracji i bez flagi `webinars` (§ 6.4) nic się nie zmienia – także polityka CSP.

### 36.1. Gdzie postawić LiveKit – dwa warianty

- **(a) Osobna maszyna – zalecane przy dużych wydarzeniach.** VPS produkcyjny traci 12–37 % CPU na
  „steal” (ukryte podkradanie procesora przez hosta), a serwer mediów i egress (Chrome składający
  nagranie) to najbardziej wrażliwe na opóźnienia procesy, jakie mamy. Maszyna 4–8 vCPU (najlepiej
  dedykowane rdzenie), Ubuntu 22.04/24.04, Docker, rekord DNS `live.<domena>` na jej adres.
  Instalacja wg dokumentacji LiveKit („Deploy → VM”: generator `livekit/generate` daje compose z
  Caddy, Redisem, egressem i TURN/TLS) albo z plików `deploy/livekit/` (nakładka jako wzór).
  Portal dostaje wyłącznie `LIVEKIT_URL=wss://live.<domena>` i klucze; `LIVEKIT_PROXY=0`.
  Egress zapisuje nagrania do S3 platformy przez adres publiczny (`S3_PUBLIC_ENDPOINT_URL`, dziś
  `https://<domena>:9000`) – w `egress.yaml` `endpoint: <ten adres>`.
- **(b) Ten sam host – małe spotkania (do kilkudziesięciu osób).** Nakładka compose z profilem
  `livekit` (`deploy/livekit/docker-compose.livekit.yml`: `livekit`, `livekit-egress`, `livekit-redis`),
  sygnalizacja przez Caddy pod `live.<domena>` (`LIVEKIT_PROXY=1`), media UDP 7882 (jeden port z multipleksacją) i TCP 7881
  prosto do kontenera. TURN wyłączony (port 443 zajmuje Caddy) – uczestnicy za zaporami, które
  przepuszczają wyłącznie HTTPS, nie połączą się; dla nich wariant (a) z TURN/TLS na 443.

### 36.2. Wdrożenie wariantu (b) – kroki operatora (na serwerze, `cd /opt/olimpiada`)

1. `scripts/deploy.sh root@olimpiadakwantowa.pl` – kod, migracje `webinars.0001`–`0002` (nowe tabele,
   bez blokad), zadanie beat `webinars-reminders` (co 5 min: przypomnienia, uzgadnianie wiszących nagrań,
   retencja; bez flagi nie robi nic), kontrakt tras djcms (nowe segmenty `webinars`, `integrations`).
   Compose dokłada do `proxy` sieć `livekit_signal` (172.30.5.0/24) – przy pierwszym wdrożeniu Caddy
   jest odtwarzany (kilka sekund, zasłania to strona zastępcza). Sieć jest w pliku podstawowym, a nie
   w nakładce, bo zwykłe wdrożenie odtwarzałoby proxy bez niej i odcinało sygnalizację LiveKit.
2. SDK przeglądarkowe (jednorazowo, na laptopie z repozytorium, wynik commitowany):
   `scripts/vendor_livekit_client.sh` – pobiera `livekit-client` z rejestru npm, **sprawdza sumę
   paczki z rejestrem**, kopiuje `livekit-client.umd.js` + `LICENSE` do `backend/static/vendor/livekit-client/`
   i zapisuje `VERSION` i `SHA384`. Bez tego pliku pokój mówi „brakuje komponentu wideo”, zamiast działać.
   Skrypt **usuwa ostatni wiersz paczki** (`//# sourceMappingURL=…map`) – mapy nie dostarczamy, a
   `collectstatic` z manifestem kończyłby się na nim błędem i `web` by nie wstał (VERSION to odnotowuje,
   pilnuje tego `apps/webinars/tests/test_static.py`).
3. DNS: rekord `A live.<domena>` → adres serwera. **Zapora:** porty 7881/tcp i 7882/udp publikuje
   Docker, a Docker wpisuje własne reguły iptables **przed** ufw – `ufw allow` jest tu dokumentacją,
   a `ufw deny` niczego nie zamknie. Zamyka się je zdjęciem `ports:` z nakładki albo regułą w łańcuchu
   `DOCKER-USER` (np. `iptables -I DOCKER-USER -p udp --dport 7882 -j DROP` na czas wyłączenia).
   Sieć mostkowa, a nie `network_mode: host`: host dałby LiveKitowi wszystkie usługi hosta i porty
   compose'a na 127.0.0.1, a ceną mostka jest jeden docker-proxy na porcie UDP 7882 (multipleksacja – wszyscy uczestnicy na jednym porcie).
4. Klucze: `openssl rand -hex 32` (sekret) i dowolny klucz (np. `APIolimp1`). W `.env`:
   `LIVEKIT_URL=wss://live.<domena>`, `LIVEKIT_API_KEY=…`, `LIVEKIT_API_SECRET=…`,
   `LIVEKIT_API_URL=http://livekit:7880`, `LIVEKIT_PROXY=1`.
5. Konto MinIO dla egress (zapis wyłącznie do `submissions/webinars/*`). Egress zapisuje przez
   **publiczny** adres S3 (`S3_PUBLIC_ENDPOINT_URL`) – nie stoi w sieci `internal`, więc nie widzi bazy,
   `web`, poczty ani MinIO od środka (sieci: `livekit` wewnętrzna + `livekit_signal`):

   ```sh
   docker compose cp deploy/livekit/policy-egress.json minio:/tmp/policy-egress.json
   docker compose exec minio mc admin policy create local webinars-egress /tmp/policy-egress.json
   docker compose exec minio mc admin user add local livekit-egress '<hasło 32+ znaki>'
   docker compose exec minio mc admin policy attach local webinars-egress --user livekit-egress
   ```

   (alias `local` w kontenerze `minio`: `mc alias set local http://localhost:9000 <root> <hasło>`).
6. Konfiguracja LiveKit: `mkdir -p livekit && cp deploy/livekit/livekit.yaml.example livekit/livekit.yaml &&
   cp deploy/livekit/egress.yaml.example livekit/egress.yaml && chmod 600 livekit/*.yaml`, podmienić
   `<…>` (klucz i sekret jak w `.env`, `SITE_DOMAIN`, konto egress z kroku 5, `endpoint` = publiczny
   adres S3). `livekit.yaml` ma `room.auto_create: false`: pokój zakłada wyłącznie platforma przed
   wystawieniem tokenu, więc token sprzed „Zakończ” nie otworzy pokoju na nowo. Obok położyć profil
   seccomp piaskownicy Chrome dla egressu: `livekit/chrome-sandboxing-seccomp-profile.json` z
   repozytorium `livekit/egress` w tagu `v1.14.1` (README egress, „Chrome sandboxing”); bez niego
   `livekit-egress` nie wystartuje. Polecenia `docker compose` z tego paragrafu – z `/opt/olimpiada`
   (ścieżki w nakładce są względne).

   Wersje przypięte w nakładce (zmienne `LIVEKIT_SERVER_VERSION`, `LIVEKIT_EGRESS_VERSION`):
   serwer **v1.13.7**, egress **v1.14.1**; SDK przeglądarkowe **livekit-client 2.22.3**
   (`scripts/vendor_livekit_client.sh`). Aktualizacja = zmiana pinów i próba z kroku 10.
7. Start: `docker compose -f docker-compose.yml -f deploy/livekit/docker-compose.livekit.yml --profile livekit up -d livekit livekit-egress livekit-redis`,
   potem `bash scripts/proxy_config.sh update` (blok `live.` w Caddy) i
   `docker compose up -d --no-deps web worker beat` (nowe zmienne `.env`).
8. Sprawdzenie (tylko odczyt): `curl -s https://live.<domena>/` → `OK`; w przeglądarce
   `/coordinator/webinars/` nie mówi już „nie jest skonfigurowany”.
9. Flaga konkursu: `/admin/ → Konkursy → <konkurs> → feature_flags` → `{"webinars": true}`.
10. Próba (staging, potem produkcja): webinar testowy na za 5 minut, „Rozpocznij i wejdź do pokoju”
    (koordynator), drugie konto uczestnika – „Dołącz”, podniesienie ręki, „Daj głos”, „Usuń z pokoju”
    (powrót ma zostać odrzucony) i „Wpuść ponownie”, nagranie 1 min → po kilku minutach wiersz „gotowe”
    (webhook `egress_ended`) i odtwarzanie. **Nagrywanie sprawdza łączność egressu z pokojem**: egress
    dołącza jak uczestnik pod adres ogłaszany przez `use_external_ip` (publiczny adres serwera), czyli
    wymaga, żeby ruch z kontenera do własnego publicznego adresu wracał do hosta (hairpin NAT). Gdy
    nagranie kończy się stanem „błąd” po ~30 s, a `docker compose logs livekit-egress` mówi o ICE/timeout,
    ustaw w `livekit.yaml` `rtc.node_ip: <adres publiczny>` i dopisz `rtc.ips.includes: [172.30.5.0/24]`
    (kandydaci z sieci `livekit_signal` dla egressu), restart `livekit`, próba jeszcze raz. Na koniec
    w panelu: lista obecności z czasami (webhooki dochodzą).

### 36.3. TURN/TLS i duże wydarzenia

Uczestnik w sieci, która przepuszcza tylko HTTPS (część szkół), potrzebuje TURN na 443/TLS. W wariancie
(a): `turn.enabled: true`, `domain: turn.<domena>`, `tls_port: 443`, certyfikat (generator LiveKit
robi to sam). Pojemność: jedna maszyna 8 vCPU obsługuje setki widzów jednego prowadzącego (widzowie
nie nadają); egress room composite zajmuje 2–4 vCPU na nagranie – na wariancie (b) nagrywaj tylko małe
spotkania. Kilkuset uczestników = wariant (a), ewentualnie kilka węzłów z Redisem (dokumentacja LiveKit).

### 36.4. Działanie i bezpieczeństwo

- Token wejścia: `identity` = pseudonim HMAC (bez e-maila i `pk`), `name` = „Imię N.”, prowadzący
  `canPublish`/`roomAdmin`, widz `canPublish=false` (+ czat i ręka po kanale danych). Głos daje
  prowadzący przez platformę (`UpdateParticipant`). Token w odpowiedzi JSON `no-store`, nigdy w HTML-u.
- Webhook `/integrations/livekit/webhook/`: podpis obowiązkowy (HS256 sekretem, `iss` = klucz,
  skrót treści), powtórki odcina identyfikator zdarzenia i wiek (15 min). Bez podpisu – 401.
- Nagrania: `submissions/webinars/<konkurs>/<pokój>/<czas>.mp4`; platforma tylko podpisuje odczyt
  (2 h) i kasuje. Kopia nocna – jak materiały z warsztatów (§ 16): bucket `submissions` w kopii jest.
- Transmisja YouTube: klucz strumienia nie jest zapisywany (idzie tylko do egress).
- CSP: `connect-src` + `wss://live.<domena>` i `https://live.<domena>` – tylko przy konfiguracji.
- Limity: `webinar_join` 60/h na konto (tokeny), `webinar_control` 600/h na konto (polecenia
  prowadzącego – osobny kubełek, żeby sesja pytań nie odcinała prowadzącego), `webinar_guest` 120/h
  na IP. Odmowa dla pokoju to JSON 429 z `Retry-After` (pokój pokazuje komunikat).
- „Usuń z pokoju” zapamiętuje osobę (`removed_at`) – nowego tokenu nie dostanie, dopóki koordynator
  nie kliknie „Wpuść ponownie” na liście obecności. Gość z nową sesją ma nowy identyfikator – wtedy
  „Wygeneruj nowy link” (stary od razu 404; ponowne włączenie linku też daje nowy).
- Nagranie „nagrywa” bez webhooka `egress_ended` uzgadnia zadanie beat (`ListEgress`) po 10 minutach;
  koordynator ma też „Sprawdź / oznacz jako nieudane”.
- Retencja: nagrania (plik i wiersz) i lista obecności znikają `WEBINAR_RETENTION_DAYS` (365) dni po
  webinarze; usunięcie konta kasuje obecność tej osoby; eksport danych konta ma sekcję `webinary`.
  Przed wejściem do pokoju webinaru z nagrywaniem jest informacja o nagrywaniu, a w trakcie – stały
  znacznik „Trwa nagrywanie”.

### 36.5. Wyłączenie i rotacja

Awaryjnie bez wdrożenia: zdjąć flagę `webinars` (adresy 404) albo wyczyścić `LIVEKIT_URL` i odtworzyć
`web worker beat`. Rotacja sekretu: nowy wpis w `keys:` i `webhook.api_key` w `livekit.yaml`,
`egress.yaml`, `.env`; restart `livekit livekit-egress` i `web worker beat` (trwające połączenia
przerywa restart serwera LiveKit – poza godzinami webinarów). Zatrzymanie wariantu (b):
`docker compose -f docker-compose.yml -f deploy/livekit/docker-compose.livekit.yml --profile livekit stop livekit livekit-egress livekit-redis`
i `LIVEKIT_PROXY=0` + `scripts/proxy_config.sh update`.

## 29. Statystyki szkół (STAT-01, flaga `school_statistics`)

Funkcja liczy agregaty z istniejących danych; jedyna tabela to `school_stats_frozenmembership`
(migracja `school_stats.0001_initial`, odwracalna) – przynależność wpisów do szkół zamrożona przy
publikacji wyników (`docs/tasks/STAT-01.md` § 10, M3). Wdrożenie nie wymaga kroku ręcznego poza
zwykłym `migrate`; etapy ogłoszone wcześniej zamrażają się same przy pierwszym wejściu na ekran.
Flaga jest domyślnie **wyłączona** (adresy `/supervisor/statistics/…`
i `/coordinator/school-stats/…` dają 404, menu i pulpit opiekuna bez zmian).

**Zapalenie** (`/admin/ → Konkursy → <konkurs> → feature_flags`, § 6.4):

```json
{"school_statistics": true}
```

Razem z flagą rejestr czynności konkursu dostaje wiersz „Statystyki szkół i opiekunów szkolnych”
(wersja 1.12) – zapalenie jest więc decyzją organizatora o nowym celu przetwarzania (opiekun widzi
przebieg ucznia przez edycje), nie skutkiem wdrożenia. Przed zapaleniem warto zweryfikować opiekunów
(`SchoolSupervisor.verified` + szkoła z wykazu) – bez tego opiekun widzi swoich uczniów, województwo
i całość, ale nie agregat szkoły i nie pobierze raportu PDF.

**Pamięć podręczna** (Redis): klucze `school_stats:v2:<oś>:<edycja>:<odcisk publikacji>`; doba dla
edycji zamkniętej publikacjami albo nie bieżącej, 5 minut dla bieżącej w toku. Ponowna publikacja zmienia
odcisk, więc nic nie trzeba czyścić ręcznie. W kluczach są wyłącznie agregaty (bez identyfikatorów
osób).

**Tłumaczenia:** napisy aplikacji mają własny katalog `backend/apps/school_stats/locale/` (maszynowe,
jak § 26.3). Obraz kompiluje od tego wydania także katalogi aplikacji (`apps/*/locale`), a test
`apps/core/tests/test_translations.py` sprawdza je tą samą miarą co katalog wspólny.

**IQO:** oś grupowania to dziś szkoła z profilu uczestnika (`apps/school_stats/grouping.py`). Oś
`delegation` (delegacje krajowe z § 28, region = kraj) jest przygotowanym punktem zaczepienia
(`axis_for`) – dołożenie jej nie zmienia ekranów ani reguł progu.

## 32. Okna czasowe etapu według stref (TZ-01, `docs/tasks/TZ-01.md`)

Etap zdalny konkursu z flagą **`stage_time_windows`** może pracować w kilku oknach czasowych (np. trzy
starty co 8 h, każdy po 5 h) z przydziałem krajów według strefy. Bez flagi (Olimpiada Kwantowa) nic się
nie zmienia: żadna bramka okien nie pyta bazy, ekranu nie ma (404), menu i panel uczestnika są te same.

### 32.1. Włączenie dla `iqo`

1. Wdrożenie zakłada tabele aplikacji `time_windows` (migracja `time_windows.0001`, same nowe tabele –
   żadna istniejąca tabela się nie zmienia). Nowych segmentów adresów nie ma (`coordinator/…`,
   `delegation/…` są już w kontrakcie).
2. Flaga – `/admin/` → Konkursy → `iqo` → „Feature flags”: dopisz `"stage_time_windows": true`, albo:
   ```sh
   docker compose exec web python manage.py shell -c "from apps.tenancy.models import Competition; c = Competition.objects.get(slug='iqo'); c.feature_flags = {**(c.feature_flags or {}), 'stage_time_windows': True}; c.save(update_fields=['feature_flags'])"
   ```
3. Koordynator ustawia okna **przed otwarciem etapu**: „Etapy → <etap> → Okna czasowe”
   (`PODRECZNIK-ORGANIZATORA.md` § 10e). Rama etapu (otwarcie – termin oddania) musi obejmować wszystkie
   okna razem z dodatkowym czasem uczniów; beat zamyka etap (`LOCKED`) dopiero po ramie.

### 32.2. Czego nie robić

- **Nie wyłączaj flagi, dopóki trwają okna** (od startu pierwszego okna do końca ostatniego z dodatkowym
  czasem – „moment ujawnienia” na ekranie okien). Bez flagi etap wraca do jednej ramy: treść zadań staje
  się jawna dla wszystkich od otwarcia ramy, a premoderacja forum/czatu trzyma się samej ramy.
- Nie zmieniaj okien przez `/admin/` – modele są tam tylko do odczytu, bo reguły „po starcie nie wolno”
  i audyt są w serwisie.

### 32.3. Co pilnuje serwer

Upload (HTML i `POST /api/submissions/…`), `is_late`, PDF treści (`/api/competitions/problems/<id>/statement/`),
lista zadań w API bieżącej edycji, strona „Zadania” w CMS (i jej API dla django CMS), archiwum, test
online (start podejścia, termin podejścia, wynik „po zamknięciu”), premoderacja forum i czatu, publikacja
wyników (`WINDOWS_NOT_FINISHED`), zmiana ramy etapu (`STAGE_WINDOWS_OUTSIDE`). Strefę czasową ucznia
aktywuje warstwa `apps.time_windows.middleware.ParticipantTimezoneMiddleware` (tylko konkurs z flagą
i zalogowany uczestnik bez roli personelu, wyłącznie w widokach panelu uczestnika – panele koordynatora,
recenzenta, `/admin/` i `/cms/` zostają w czasie polskim; podpis „czas polski” zamienia się wtedy na nazwę
strefy). Od startu pierwszego okna przydział domyślny krajów jest zapisywany w bazie, więc aktualizacja
`tzdata` albo mapy stref w trakcie zawodów nie przenosi kraju do innego okna. Migracja `time_windows.0002`
zmienia wyłącznie zachowanie kluczy obcych (`RESTRICT`).

### 32.4. RODO i tłumaczenia

Nowa czynność w rejestrze „Okna czasowe etapu” (tylko konkursy z flagą), sekcja `okna_czasowe` w eksporcie
danych konta; anonimizacja usuwa strefę ucznia i powód wyjątku (okno i dodatkowy czas zostają jako
dokumentacja warunków pracy). Katalogi tłumaczeń aplikacji (`backend/apps/*/locale`) kompilują obraz
(`backend/Dockerfile`), CI i `backend/conftest.py`.

### 32.5. Wycofanie

Usunięcie planu (ekran okien, przed otwarciem etapu) przywraca etapowi jedną ramę. Migracja
`time_windows.0001` jest odwracalna (`migrate time_windows zero` usuwa wyłącznie tabele tej aplikacji).

## 34. Tłumaczenia zadań przez delegacje (TR-01, `docs/tasks/TR-01.md`)

Funkcja istnieje wyłącznie w konkursie w trybie **`DELEGATIONS`** (§ 28) – w Olimpiadzie Kwantowej
nie ma ani ekranów (404), ani pozycji menu, ani odnośnika na karcie zadania. Nowa aplikacja
`apps.problem_translations` (migracje `problem_translations.0001`–`0002`, same nowe tabele i kolumny – odwracalne).

### 34.1. Wdrożenie

- `scripts/deploy.sh` jak zwykle (migracja + `collectstatic`). KaTeX jest **zwendorowany**
  (`apps/problem_translations/static/problem_translations/vendor/katex/`, wersja 0.19.0, MIT) – CSP bez
  zmian (KaTeX nie idzie z CDN-u; htmx i Alpine strony bazowej – jak w całym serwisie – z CDN-ów przypiętych
  SRI, bez treści zadania w żądaniu). Wersja, skróty i sposób przycięcia CSS: `vendor/katex/VERSION`.
- Obraz kompiluje teraz także katalogi tłumaczeń aplikacji (`apps/*/locale/*/LC_MESSAGES/django.po`,
  `backend/Dockerfile`) – bez przebudowy obrazu ekrany opiekuna byłyby po polsku.
- Nowy scope throttlingu `translation` (1200/h na konto) – bez zmian w `.env`.
- Wgranie PDF-u tłumaczenia skanuje clamd **synchronicznie**; gdy clamd nie odpowiada, wgranie jest
  odrzucane (komunikat „spróbuj ponownie”), edytor tekstowy działa dalej. Przed nocą tłumaczeń:
  `docker compose ps clamav` (healthy).

### 34.2. Przebieg (koordynator)

1. „Etapy → Tłumaczenia zadań” (`/coordinator/translations/`) → etap → **okno tłumaczeń** (otwarcie,
   zamknięcie ≤ otwarcie etapu) i tryb: *osobne* (każda delegacja tłumaczy sama) albo *wspólne* (jedno
   tłumaczenie na język). Trybu nie da się zmienić, gdy w etapie są już tłumaczenia.
2. Wersja oficjalna: tytuł i PDF – jak dotąd na ekranie zadań etapu; **tekst** (Markdown + LaTeX) –
   „Tekst oficjalny” przy zadaniu. Każda zmiana tekstu, tytułu albo PDF-u podnosi wersję; tłumaczenia
   oparte na starszej dostają znacznik „nieaktualne”, a opiekunowie – list.
3. Opiekunowie deklarują języki (`/delegation/translations/`) i w oknie tłumaczą (edytor z autozapisem
   albo PDF), potem „Wyślij do akceptacji”.
4. Kolejka „Do przeglądu” → „Zatwierdź” albo „Zwróć do poprawy” (komentarz obowiązkowy). Zatwierdzone
   jest zablokowane; nieaktualnego nie da się zatwierdzić.
5. Po otwarciu etapu uczeń ma na karcie zadania „Treść w języku: …” (zatwierdzona wersja) obok wersji
   oficjalnej.
6. Finał stacjonarny: ekran etapu → „Eksport do druku” → PDF (serwer) albo „Widok do druku”
   (przeglądarka → „Zapisz jako PDF”; konieczny dla wzorów i pism CJK/indyjskich/arabskich).

### 34.3. Poufność i dziennik

Źródło przed otwarciem etapu widzi koordynator i opiekun z delegacją w bieżącej edycji – **tylko
w otwartym oknie**. Odpowiedzi mają `Cache-Control: no-store`. Dziennik (`/coordinator/audit/`,
akcje `translation.*`): `source_viewed`, `source_downloaded`, `file_downloaded`, `reviewed`,
`file_reviewed`, `student_viewed`, `student_downloaded`, `exported`, `submitted`, `withdrawn`,
`reopened`, `approved`, `returned`, `pdf_uploaded`, `languages_declared`, `student_language_set`,
`window_set`, `source_changed`. Kto pobrał arkusz przed zawodami:

```sh
docker compose exec web python manage.py shell -c "from apps.core.models import AuditLog; [print(a.at, a.actor_id, a.action, a.target_id, a.diff) for a in AuditLog.objects.filter(action__in=['translation.source_downloaded','translation.file_downloaded','translation.source_viewed']).order_by('at')]"
```

PDF-y pobrane przez opiekuna mają znak wodny: kod kraju, „CONFIDENTIAL”, data i id konta.

### 34.4. Wycofanie

Wyłączenie trybu delegacji ukrywa wszystkie ekrany (404); dane zostają. Wycofanie kodu: `migrate
problem_translations zero` (usuwa tabele tłumaczeń – najpierw eksport do druku, jeśli potrzebny).

## 33. Przegląd tłumaczeń przez native speakerów (L10N-01, `docs/tasks/L10N-01.md`)

Wolontariusze z rolą **tłumacza** (np. kierownicy delegacji `iqo`) przeglądają napisy interfejsu
w swoim języku pod `/translations/`, proponują poprawki i głosują; **recenzent tłumaczeń** zatwierdza.
Zatwierdzona poprawka działa bez wydania (nakładka z bazy na katalogi gettext), a do repozytorium
trafia komendą `export_translations` jako zwykły PR. Kiedy ją widać: proces, który ją zatwierdził –
od razu; pozostałe procesy `web`/`worker` – po najwyżej 5 s (`TRANSLATION_OVERRIDES_CHECK_SECONDS`);
bufor stron dla gości (`apps.web.page_cache`, 120 s) jest czyszczony przy każdej zmianie. Dlaczego
nie Weblate: spec § 1 (nowy serwer albo zasoby produkcji, klucz z prawem zapisu do repozytorium,
drugi system kont). Serwis publiczny na django CMS (`djcms`) to osobny proces – nakładka go nie
obejmuje.

### 33.1. Role

- **Tłumacz** (proponuje, głosuje, zgłasza błąd ze stopki) – nadaje koordynator konkursu z więcej niż
  jednym językiem interfejsu: „Ustawienia → Tłumacze interfejsu” (`/coordinator/translators/`),
  wyłącznie osobom związanym z konkursem (członkostwo albo profil uczestnika) i wyłącznie w językach
  interfejsu tego konkursu.
- **Nadanie koordynatora należy do konkursu**: widzi je i odbiera każdy koordynator tego konkursu
  (także po odejściu nadającego), a działa **tylko dopóki** osoba jest z konkursem związana – po
  wypisaniu, odebraniu roli albo usunięciu profilu rola tłumacza przestaje działać sama (wiersz
  zostaje na liście koordynatora do usunięcia).
- **Recenzent tłumaczeń** (zatwierdza, odrzuca, cofa, potwierdza, zamyka zgłoszenia) – nadaje
  **wyłącznie superkoordynator** (ten sam ekran, pod adresem dowolnego konkursu); jego nadania są
  platformowe (bez konkursu). Superkoordynator jest recenzentem każdego języka.
- Każde nadanie, odebranie i każda decyzja – wpis audytu `translation.*` (bez treści zgłoszeń).

### 33.2. Decyzje recenzenta – co trafia do serwisu

- **Poprawka** (zatwierdzona propozycja) – trafia do gettext, ale tylko dopóki `msgstr` w katalogu
  jest ten sam, co w chwili decyzji. Jeśli wydanie zmieni go w międzyczasie, wygrywa katalog,
  a napis ma na liście znacznik „do ponownego przeglądu”.
- **Potwierdzenie** („Obecne tłumaczenie jest poprawne”) – **nigdy** nie trafia do gettext; to sam
  znacznik „przejrzane”, który eksport zapisuje jako `# l10n-reviewed`.

### 33.3. Z bazy do repozytorium (po serii poprawek)

```sh
# produkcja – zrzut zatwierdzonych decyzji (sam tekst tłumaczeń, bez danych osób)
docker compose exec -T web python manage.py export_translations --to-json - > overrides.json
scp olimpiada:/opt/olimpiada/overrides.json backend/overrides.json   # do checkoutu dewelopera

# checkout dewelopera (DEBUG=1, montowany backend, .git podpięty do kontenera) – zapis do .po, potem PR
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm -v "$PWD/.git:/.git:ro" \
    web python manage.py export_translations --from-json /app/overrides.json --dry-run
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm -v "$PWD/.git:/.git:ro" \
    web python manage.py export_translations --from-json /app/overrides.json
rm backend/overrides.json

# produkcja, PO wdrożeniu tego PR-a – usunięcie nakładek, które są już w skompilowanych katalogach
docker compose exec web python manage.py export_translations --prune
```

- Zapis do `.po` jest **odmawiany** poza checkoutem dewelopera (`DEBUG` i katalog `.git` w `backend`
  albo nad nim – stąd podpięte `.git` w poleceniu wyżej); w kontenerze produkcyjnym trafiłby do
  warstwy obrazu i rozjechał z `.mo`. Świadome obejście: `--force`.
- Eksport zmienia wyłącznie linie `msgstr` poprawek i dopisuje `# l10n-reviewed` (potwierdzenie:
  sam znacznik). Tekst z JSON-a przechodzi tę samą walidację, co w panelu; poprawka podjęta wobec
  innego `msgstr` niż dzisiejszy jest wypisana jako **konflikt** i nie nadpisuje nowszego tekstu;
  wpis, którego nie ma już w katalogach – jako „nieaktualny”.
- `--prune` usuwa poprawkę tylko wtedy, gdy **skompilowany** katalog (`.mo` – to on trafia do
  gettext) oddaje już dokładnie jej tekst, a potwierdzenie – gdy wpis ma znacznik. Przed wdrożeniem
  nie usunie niczego. Nakładki napisów usuniętych z kodu tylko wypisuje; usuwa je `--prune-stale`.

### 33.4. Wyłączenie i awarie

- `TRANSLATION_OVERRIDES_ENABLED=0` w `.env` + restart `web`, `worker`, `beat` – serwis wraca do samych
  katalogów z repozytorium; decyzje zostają w bazie. Cofnięcie pojedynczej decyzji: „Przywróć
  tłumaczenie z katalogu” na ekranie napisu (recenzent).
- W Redisie stoi tylko numer wersji nakładki (bez terminu ważności); każdy proces po zmianie wersji
  buduje nakładkę z bazy sam (jedno zapytanie). Po restarcie Redisa – nowa wersja i to samo. Błąd
  nakładki nigdy nie psuje strony – log `apps.translation_review.runtime` i katalog z repozytorium.
- Limit POST-ów w panelu tłumacza: scope `translations` (120/h na konto).

### 33.5. Wdrożenie tej wersji

`migrate` (`translation_review.0001`–`0002`, tylko nowe tabele i kolumny) – bez kroków ręcznych.
Obraz kompiluje teraz także katalogi aplikacji (`apps/*/locale`). Zmienił się manifest adresów
(`/translations/` – `backend/djcms_contract/app_routes.*`), więc konfiguracja proxy z § 23 musi
zostać przeładowana (robi to `deploy.sh`). Odnośnik „Zgłoś tłumaczenie” stoi w domyślnej stopce
(`templates/theme/footer.html`); paczka motywu, która nadpisuje slot `footer`, dołącza go tym samym
fragmentem: `{% include "web/_translation_report_link.html" with css_class="footer__link" %}`. Olimpiada Kwantowa
(sam polski) nie widzi żadnej zmiany: brak pozycji w menu, brak odnośnika w stopce, brak wiersza
w rejestrze czynności.

## 37. Medale olimpiady międzynarodowej, dyplomy w języku ucznia i ranking krajów (MED-01, `docs/tasks/MED-01.md`)

Złoto, srebro, brąz i wyróżnienia liczone z rankingu etapu (domyślnie jak IPhO: 8 % / kolejne 17 % /
kolejne 25 %), ręczne zmiany z uzasadnieniem, ogłoszenie (zamrożenie), dyplomy medalowe i zaświadczenia
o udziale **w języku ucznia**, publiczna strona medali i nieoficjalny ranking krajów. Cała funkcja stoi
za flagą konkursu **`medals`** (domyślnie wyłączona) – Olimpiada Kwantowa nie wymaga niczego i nie widzi
żadnej zmiany (tytuł laureata, dyplomy i tabela wyników bez zmian).

### 37.1. Włączenie dla `iqo`

1. Wdrożenie (migracje `medals.0001`, `results.0008`, `tenancy.0015_documenttemplate_award_kinds` – nowe tabele i same listy wyboru,
   bez zmiany danych).
2. Flaga: `/admin/` → Konkursy → `iqo` → `feature_flags` → dopisz `"medals": true`, albo powłoka:
   ```sh
   docker compose exec web python manage.py shell -c "from apps.tenancy.models import Competition; c = Competition.objects.get(slug='iqo'); c.feature_flags = {**(c.feature_flags or {}), 'medals': True}; c.save(update_fields=['feature_flags'])"
   ```
3. W panelu `iqo` pojawia się „Raporty → Medale” (`/coordinator/medals/`). Ekran pokazuje też **stan składu
   dokumentów dla każdego z 11 języków** – wszystkie mają mieć „składany”.

### 37.2. Zależność `uharfbuzz` (kształtowanie pisma)

Arabski, hindi (dewanagari) i bengalski wymagają kształtowania (HarfBuzz) – nowa zależność
`uharfbuzz>=0.56,<0.57` w `backend/pyproject.toml` (koło abi3, bez kompilacji; wąski przedział, bo skład
korzysta z wnętrza ReportLaba – kontrakt pilnuje test `test_reportlab_shaping_internals_are_still_there`).
**Obraz `olimpiada/web` trzeba przebudować** (robi to CI/`deploy.sh`). Bez niej:

- przy **wystawieniu** dokument ucznia z arabskim, hindi albo bengalskim dostaje przypięty angielski, a raport
  „Wystaw dokumenty” wypisuje numery takich dokumentów (ostrzeżenie dla koordynatora, wpis `WARNING`),
- przy **pobraniu** dokumentu już przypiętego do jednego z tych języków serwer **odmawia** (wpis `ERROR`
  „Dokumentu … nie da się złożyć w języku ar”, uczeń widzi komunikat, ZIP koordynatora – błąd z numerem),
  zamiast po cichu wydać ten sam numer w innym języku. Ekran medali pokazuje wtedy ostrzeżenie z listą
  języków. Naprawa: przebudowa obrazu z `uharfbuzz`.

Chiński, rosyjski i języki łacińskie kształtowania nie wymagają.

Kroje są w repozytorium (`backend/apps/medals/fonts/`, licencje SIL OFL 1.1 i Apache 2.0, źródła
w `SOURCES.txt`) i są osadzane w PDF-ie jako podzbiory – serwer ani czytelnik nie potrzebują fontów
systemowych. Znak spoza wszystkich krojów (np. emoji w nazwisku) staje się `?` z wpisem w logu.

### 37.3. Przebieg na zawodach

1. Wyniki etapu – jak zawsze (`/coordinator/stages/<id>/results/`, publikacja w trybie `CODE` albo
   `FULL_ALL`; nazwiska wyłącznie za zgodą).
2. `/coordinator/medals/<etap>/`: progi, podgląd (pule, progi punktowe, rzeczywiste odsetki), ręczne
   zmiany z uzasadnieniem → „Ogłoś medale”. Ogłoszenie wymaga **opublikowanych** wyników, a bieżąca
   tabela musi być tą ogłoszoną: te same wpisy, te same sumy i te same liczności stanów (zakwalifikowani,
   niezakwalifikowani, zdyskwalifikowani – porównanie z wpisem audytu `results.qualification_applied`
   publikacji). Dyskwalifikacja albo nowy wpis po publikacji → 409 „opublikuj wyniki ponownie”.
3. „Wystaw dokumenty” (dyplomy medalowe + opcjonalnie zaświadczenia o udziale) → „Pobierz paczkę ZIP”.
   Język dokumentu: język ucznia z konta (o ile konkurs go oferuje), inaczej język domyślny konkursu;
   **przypinany przy wystawieniu** (zaświadczenie wystawione z dawnego panelu – przy pierwszym pobraniu). Przed galą warto pobrać po jednym dokumencie w `ar`, `hi`, `bn`,
   `zh-hans` i obejrzeć je – tłumaczenia są maszynowe.
4. „Lista na galę (PDF)” i „Eksport CSV” – z nazwiskami, każde pobranie w audycie (`medals.exported`).
5. Publiczne strony: `/results/<etap>/medals/` (filtr `?country=`; kraj przy wierszu tylko w trybie
   `CODE` albo przy nazwisku opublikowanym za zgodą w trybie imiennym – nie przy „inicjałach i szkole”)
   i `/results/<etap>/countries/` (`?sort=medals`; suma i średnia punktów tylko dla krajów z co najmniej
   3 wynikami, przy publikacji „tylko awansujący” – wyłącznie z wyników nagrodzonych); odnośniki pojawiają
   się na `/results/<etap>/` po ogłoszeniu.

Korekta po ogłoszeniu: „Odmroź medale” (z uzasadnieniem w audycie) → zmiany → ponowne ogłoszenie.
Dyplom medalowy, którego rodzaj nie zgadza się z ogłoszoną nagrodą (albo gdy medale są odmrożone),
jest **nieaktualny**: strona `/dyplomy/<kod>/` mówi to wprost, a w „Moich dyplomach” ucznia go nie ma
(pobranie – 404). Wiersz rejestru zostaje, a „Wystaw dokumenty” wypisuje numery takich dyplomów.

### 37.4. Limit żądań i wycofanie

Czynności ekranu medali (także usunięcie ręcznej zmiany oraz pobrania CSV, PDF i ZIP) mają limit `medals`
(120/h, `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`).
Wyłączenie flagi ukrywa ekrany i strony publiczne (404) i przywraca polski skład zaświadczeń
`UCZESTNIK`; dane (`MedalScheme`, `MedalOverride`, `CertificateLanguage`) zostają. Migracje są
odwracalne.

## 35. Płatności online za udział – Stripe, Przelewy24, przelew, faktury (PAY-01, `docs/tasks/PAY-01.md`)

Opłaty za udział płacone online: przez **delegacje** (IQO, cennik delegacji w EUR) i – w konkursach
z rejestracją otwartą – przez **uczestników** (należność z ekranu „Wpisowe”, zwykle PLN). Wszystko za
flagą konkursu **`fees`** (domyślnie wyłączona – Olimpiada Kwantowa nie widzi ani adresu, ani pozycji
menu). Aplikacja `apps.payments`, migracja `payments.0001` (nowe tabele, odwracalna).

### 35.1. Zmienne środowiskowe (`.env`, usługi `web` i `worker`)

| Zmienna | Wartość | Uwagi |
|---|---|---|
| `STRIPE_SECRET_KEY` | `sk_test_…` (test) / `sk_live_…` | Stripe → Developers → API keys → Secret key. Może być *restricted key* z prawem zapisu do Checkout Sessions i Refunds. |
| `STRIPE_WEBHOOK_SECRET` | `whsec_…` | Signing secret endpointu webhooka; kilka po przecinku (rotacja, kilka endpointów). |
| `P24_MERCHANT_ID` | liczba | Panel Przelewy24 → Moje dane → Dane API. |
| `P24_POS_ID` | liczba | Zwykle = merchant ID. |
| `P24_API_KEY` | napis | „Klucz do raportów” (REST API). |
| `P24_CRC` | napis | Klucz CRC (podpis SHA-384). |
| `P24_SANDBOX` | `true`/`false` | `true` = `sandbox.przelewy24.pl` (osobne konto sandbox). |

Pusty klucz = operator wyłączony: przycisk płatności się nie pokazuje, a jego webhook odpowiada **404**.
Sekrety nie trafiają do bazy ani do audytu. Po zmianie `.env`: `docker compose up -d web worker`
(restart, nie reload). Ekran `/coordinator/payments/prices/` pokazuje, czy operator jest skonfigurowany
i czy Stripe jest w **trybie testowym**.

### 35.2. Stripe – konfiguracja panelu (najpierw tryb testowy)

1. Stripe Dashboard → przełącznik **Test mode** → Developers → API keys → skopiuj *Secret key* do
   `STRIPE_SECRET_KEY`.
2. Developers → **Webhooks** → *Add endpoint*: URL `https://<domena-konkursu>/payments/webhooks/stripe/`
   (jeden endpoint na instalację – płatność odnajdujemy po identyfikatorze sesji, nie po domenie; może
   to być domena dowolnego konkursu z tej instalacji). Zdarzenia: `checkout.session.completed`,
   `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`,
   `checkout.session.expired`, `refund.updated`, `refund.failed`. *Signing secret* → `STRIPE_WEBHOOK_SECRET`.
3. Settings → Payment methods: karty (opcjonalnie inne metody; metody odroczone, np. SEPA, kończą się
   `async_payment_succeeded` i są obsługiwane). Settings → Branding: nazwa i logo organizatora.
4. Próba: konkurs z `fees`, cennik, opiekun wystawia pro formę → „Zapłać kartą” → karta testowa
   `4242 4242 4242 4242` (dowolna przyszła data, dowolny CVC) → po kilku sekundach zamówienie „zapłacone”,
   faktura `…/FV/<rok>/0001`, list do płacącego. W panelu Stripe → Webhooks → endpoint: odpowiedzi 200.
   Lokalnie: `stripe listen --forward-to https://<host>/payments/webhooks/stripe/` (CLI poda własny `whsec_`).
5. Zwrot próbny z ekranu zamówienia koordynatora („Zleć zwrot”) – w Stripe pojawia się Refund.
6. **Produkcja**: wyłącz Test mode, powtórz kroki 1–2 z kluczami live (endpoint live ma inny `whsec_`),
   wpisz `sk_live_…`, restart, jedna płatność kontrolna i jej zwrot.

### 35.3. Przelewy24 – konfiguracja panelu (tylko PLN)

1. Konto sandbox (`sandbox.przelewy24.pl`) → Moje dane → Dane API: merchant ID, POS ID, klucz do
   raportów, klucz CRC → `P24_*`, `P24_SANDBOX=true`.
2. Adres powiadomień (`urlStatus`) wysyłamy przy rejestracji każdej transakcji:
   `https://<domena-konkursu>/payments/webhooks/przelewy24/` (zwroty: `…/przelewy24/refund/`). W panelu
   P24 nie trzeba go wpisywać; jeśli konto ma listę dozwolonych adresów powiadomień – dopisz oba.
3. Wpłata jest zapisywana dopiero po udanym `PUT /transaction/verify` – nieudany verify daje 503 i P24
   ponawia powiadomienie. Limit transakcji 15 min: nowa próba tego samego zamówienia jest możliwa po
   20 min (ochrona przed podwójną zapłatą).
4. **Stan:** adapter P24 jest zaimplementowany i przetestowany na atrapie HTTP (podpisy z dokumentacji
   REST v1), **nie** na sandboxie – przed włączeniem na produkcji zrób płatność i zwrot w sandboxie.

### 35.4. Włączenie w konkursie

1. Flaga: `/admin/` → Konkursy → `feature_flags` → `"fees": true` (albo powłoką jak w § 28.1).
2. `/coordinator/payments/prices/`: **Sprzedawca, rachunek i dokumenty** – NIP/VAT ID, IBAN, SWIFT, bank,
   prefiks numeracji (domyślnie slug, np. `IQO/FV/2026/0001`), adnotacja VAT, uwagi, termin pro formy,
   metody płatności. Nazwa, adres i dane rejestrowe sprzedawcy pochodzą z pól organizatora konkursu.
3. Cennik delegacji edycji (konkurs w trybie delegacji): waluta, „cena wczesna do”, „cena późna od”,
   siatka cen (delegacja, uczeń, opiekun, obserwator × wczesna/podstawowa/późna).
4. Konkurs z rejestracją otwartą: cennik i naliczenie należności na ekranie „Wpisowe” (`/coordinator/fees/`)
   – uczestnik dostaje przycisk „Zapłać online” na kaflu „Wpisowe”.
5. **Wzór faktury** (pro forma i faktura, PDF) zatwierdza księgowa organizatora przed pierwszym konkursem
   z opłatami: system numeruje dokumenty ciągle (per konkurs, rodzaj i rok), ale nie liczy VAT, nie
   prowadzi rejestru VAT/JPK i nie wystawia korekt (decyzja D15 po zmianie z 4.10.2026).

### 35.5. Przelew tradycyjny, dowody wpłat, eksport

- Płacący widzi IBAN i **kod referencyjny** (tytuł przelewu). Koordynator na ekranie zamówienia
  „Wpływ przelewu”: data wpływu, notatka, opcjonalnie dowód (PDF/JPG/PNG ≤ 10 MB) – plik idzie do bucketu
  prac (prefiks `payments/`) i do skanu ClamAV (kolejka `scan`); do pobrania dopiero po werdykcie „czysty”,
  zawsze jako załącznik. Plik zainfekowany jest usuwany, wpłata zostaje. Wpłatę zapisuje się
  **wyłącznie na zamówienie otwarte** – przelew z kodem zamówienia anulowanego zwraca się płacącemu
  w banku (poza systemem) albo zalicza po wystawieniu przez opiekuna nowej pro formy.
- **Zwroty** wskazuje się **pozycjami i ilościami** (np. 1 × uczeń); kwotę liczy system. Zwrócone miejsca
  przestają być opłacone. Wpłata „do wyjaśnienia” (podwójna, rozbieżna, po anulowaniu) wraca w całości.
  Brak odpowiedzi operatora przy zwrocie → zwrot zostaje „w toku” i jest ponawiany automatycznie z tym
  samym kluczem idempotencji (bez ryzyka podwójnego zwrotu); odmowa operatora → „nieudany”.
- `/coordinator/payments/export.csv?edition=<id>` – jeden wiersz na zamówienie (nabywca, VAT ID, kwota,
  waluta, stan, metoda, identyfikator transakcji, zwroty, numery pro formy i faktury). Zdarzenie w audycie.

### 35.6. Kontrakt adresów i limity

Nowy pierwszy segment `payments/` (`RESERVED_SLUGS`, `backend/djcms_contract/` – zaktualizowane). Webhooki
`/payments/webhooks/*` są **bez** sesji i CSRF (podpis), limit `payment_webhooks` (600/min per IP; stub
z wydania K zostaje przy `payments`, 60/min). Nowe stawki
`checkout` (20/h per konto: „Wystaw pro formę”, „Zapłać”) i `payments_admin` (120/h, czynności koordynatora).
Stub `/api/v1/payments/<slug>/` z wydania K zostaje bez zmian.

**Sprzątanie (beat `payments-sweep`, co 15 min, `apps.payments.tasks.sweep_payments`)** – wymaga
działającego `beat` i `worker`: próba Stripe starsza niż czas życia sesji (60 min + 10) → `GET` sesji
(wygasła → przerwana, zapłacona a webhook zginął → wpłata rozliczona jak ze zdarzenia); próba bez
identyfikatora sesji starsza niż 30 s → przerwana; P24 starsza niż 80 min → przerwana; zwrot „w toku”
bez identyfikatora operatora starszy niż 2 min → zlecony ponownie. Bez flagi `fees` w żadnym konkursie
zadanie robi dwa puste zapytania.

### 35.7. Diagnoza i wycofanie

- Dziennik doręczeń: `/admin/` → Płatności → „Doręczenia od dostawców” (panel płatności w `/admin/` jest
  tylko do odczytu – zmiany stanu wyłącznie przez ekrany koordynatora, z audytem).
  `outcome`: `succeeded`, `mismatch` (kwota/waluta inna niż zamówienie – pulpit „Do wyjaśnienia”),
  `unknown_payment`, `duplicate` (nie zapisywane – odpowiedź), `ignored`, `mode_mismatch` (zdarzenie live
  przy kluczu `sk_test_…` albo odwrotnie – pominięte; sprawdź, czy endpoint i klucz są z tego samego trybu).
- 400 w panelu Stripe = zły `STRIPE_WEBHOOK_SECRET` (albo endpoint test/live pomylony); 404 = brak klucza
  w `.env` usługi `web`.
- Wycofanie: wyłączenie flagi `fees` ukrywa ekrany (404); dane zostają. Migracje `payments.0001`–`0002` są
  odwracalna, ale **dokumenty księgowe** trzeba przed tym wyeksportować (5 lat przechowywania).

## 38. Sieć absolwentów i mentoring (ALUM-01, `docs/tasks/ALUM-01.md`)

Funkcja jest za flagą konkursu **`alumni`** (domyślnie wyłączona) i nie ma jej w ekranie
„Ustawienia konkursu” – to nowa czynność przetwarzania na podstawie zgody i kontakt dorosłych
mentorów z małoletnimi, więc włącza ją operator po decyzji organizatora (jak forum, § 6.4):

```sh
docker compose exec web python manage.py shell -c "from apps.tenancy.models import Competition as C; c=C.objects.get(slug='kwantowa'); c.feature_flags={**(c.feature_flags or {}), 'alumni': True}; c.save(update_fields=['feature_flags'])"
```

Po włączeniu koordynator ustawia w `/coordinator/alumni/`: kto może dołączyć (domyślnie finaliści),
mentoring (domyślnie wyłączony) i publiczną ścianę (domyślnie wyłączona). Mentoring wymaga
włączonych Wiadomości (`/coordinator/chat/settings/`).

**Wdrożenie:** migracje `alumni.0001_initial` i `alumni.0002_review_safeguards` (nowe tabele
i kolumny wyłącznie w `alumni_*`, bez zmian w istniejących), nowy segment
adresu `/alumni/` w kontrakcie tras (`backend/djcms_contract/app_routes.*` – generator Caddy'ego
wkleja go przy wdrożeniu, § 23), nowy zakres limitu `alumni` w `REST_FRAMEWORK`. Nic do zrobienia
ręcznie poza ewentualnym włączeniem flagi.

**Retencja:** aktywna zgoda absolwenta (profil nieukryty, konto aktywne) wstrzymuje **pełną**
anonimizację konta (`apps.accounts.retention`, przeszkoda „należy do sieci absolwentów (zgoda)” na
ekranie `/coordinator/retention/`, sprawdzana **po** reklamacjach i nieogłoszonych wynikach). Przebieg
retencji robi wtedy **minimalizację** (`apps.alumni.services.minimise_participant`, wpis audytu
`account.minimised_by_retention`): czyści telefon, szkołę (nazwę i powiązania ze słownikami), region
i województwo, klasę, adresy opiekuna szkolnego i rodzica oraz dzień urodzenia (zostaje rocznik).
Zostają imię, nazwisko, adres e-mail, kod publiczny, wpisy do etapów i dyplomy (z nich liczą się
osiągnięcia) i wiersze sieci. Pełnoletność potrzebna regułom mentoringu jest zapisana na profilu
absolwenta (`adult_confirmed_at`). Wycofanie zgody, ukrycie profilu albo wyłączenie flagi przywraca
zwykłą retencję przy najbliższym przebiegu nocnym.

**Co dzieje się z danymi po wyłączeniu flagi (dokładnie):** adresy `/alumni/…`, katalog, prośby,
zaproszenia i ekrany koordynatora dają 404; `/me/alumni/` zostaje **wyłącznie** dla osób z profilem
i pokazuje jedno – wycofanie zgody (działa także przy wyłączonej fladze). Profile, dowody zgody,
relacje i zgłoszenia zostają w bazie bez zmian, ale nikomu nie są pokazywane ani używane (nie ma
zaproszeń, statystyk ani katalogu). Rozmowy mentorskie w Wiadomościach są tylko do odczytu („Organizator
wstrzymał mentoring”) – sprawdzenie kosztuje jedno zapytanie przy wiadomości P2P wyłącznie w konkursie,
który flagę **kiedyś** zapisał (konkurs, który jej nigdy nie włączał, nie płaci nic). Wstrzymanie
retencji przestaje działać – przy najbliższym przebiegu przeterminowane konta są anonimizowane, a wraz
z nimi znikają profile absolwentów (`erase_for_user`). Ponowne włączenie flagi przywraca wszystko
w stanie sprzed wyłączenia.

**Dokumentacja bezpieczeństwa mentoringu:** strony relacji, daty, kanał, powód zakończenia, notatka
organizatora, zgłoszenia (także automatyczne: wzorce danych kontaktowych w notatce, zmiana daty
urodzenia osoby w otwartej relacji, rozmowa szyfrowana pod wymuszoną moderacją) i wpisy dziennika
zdarzeń zostają do anonimizacji kont stron. Przy anonimizacji znika notatka prośby, treść zgłoszeń tej
osoby i zapisana przy akceptacji data urodzenia mentee. **Zakończonej relacji nie da się wznowić**
(także koordynatorowi): mentee wysyła nową prośbę, a po akceptacji rozmowa sprzed relacji znów
przyjmuje wiadomości na zasadach mentoringu.

**Zmiana treści zgody:** podbicie `ALUMNI_CONSENT_VERSION` (`apps/alumni/models.py`) usypia profile
z poprzednią wersją (znikają z katalogu, ściany i zaproszeń) do czasu potwierdzenia nowej treści na
`/me/alumni/`. Dowód zgody zapisuje wersję, język i skrót SHA-256 pokazanej treści.

**Moderacja mentoringu:** wiadomości rozmów mentorskich trafiają do istniejącej kolejki
`/coordinator/chat/moderation/` (premoderacja przy małoletnim mentee i zasadzie „ta sama grupa
wiekowa” albo przy wyłączonych rozmowach uczestników; przy „bez ograniczeń” premoderacja pierwszych
5 wiadomości nowej pary, potem postmoderacja). Notatki próśb małoletnich i opisy mentorów widoczne dla
małoletnich czekają na akceptację w `/coordinator/alumni/mentoring/` i `/coordinator/alumni/`. Przy
włączonym mentoringu z małoletnimi organizator musi mieć dyżur moderacyjny.

**Definitywne wycofanie funkcji:** wyłączenie flagi (skutki wyżej) i – bo zgoda dotyczyła działającej
sieci – usunięcie profili (`AlumniProfile.objects.filter(participant__competition=c).delete()`).

## 39. Zmiana hasła w panelu konta (AUTH-01b, `docs/tasks/AUTH-01b.md`)

Nowa aplikacja `apps.password_change` – **bez migracji, bez zmiennych środowiskowych, bez flagi**:
ekran `/account/password/` działa po wdrożeniu dla każdego zalogowanego konta, we wszystkich konkursach
(także pod prefiksem ścieżki).

- **Limit:** `password_change` – 10 POST-ów na godzinę **na konto** (`REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`,
  licznik `apps.web.throttle` w Redisie). Przycisk „Wyślij mi link do ustawienia hasła” (konto bez hasła)
  liczy się w scope `password_reset` (5/h, też na konto).
- **Sesje:** zmiana hasła wylogowuje pozostałe sesje konta (skrót hasła w sesji Django) i kasuje tokeny
  API; bieżąca sesja i znacznik 2FA zostają. Nie trzeba nic czyścić ręcznie (`clearsessions` jak dotąd).
- **Poczta:** list „Hasło do konta zostało zmienione” idzie kolejką `mail` (worker) w języku żądania,
  od nadawcy konkursu, z linkiem do `/password-reset/` pod hostem konkursu. Brak listu przy działającej
  zmianie = sprawdź workera i relay, jak przy innych listach.
- **Audyt:** `password.changed`, `password.change_failed`, `password.set_link_sent` – bez sekretów.
- **Motyw IQO:** w pasku konta adres e-mail jest teraz odnośnikiem do ustawień konta (fragment
  `web/_account_who.html`). Panele mają to od razu; na **stronach publicznych** z motywem `iqo-quantum`
  odnośnik pojawi się po wgraniu paczki **1.1.1** (zmiana wyłącznie nagłówka i jednej reguły CSS):

```sh
python themes/iqo-quantum/build_zip.py   # → themes/iqo-quantum/dist/iqo-quantum-1.1.1.zip (laptop)
scp -i ~/.ssh/olimpiada_deploy themes/iqo-quantum/dist/iqo-quantum-1.1.1.zip deploy@<serwer>:/tmp/
docker compose exec -T web python manage.py theme_install - --activate iqo < /tmp/iqo-quantum-1.1.1.zip
```

  Bez tego kroku nic się nie psuje – 1.1.0 pokazuje adres jako zwykły tekst. Cofnięcie: aktywacja 1.1.0
  (§ 30.1).
- **Wycofanie funkcji:** usunięcie wiersza `apps.password_change` z `INSTALLED_APPS` i rozwinięcia
  wzorców w `apps/web/urls.py` oraz sekcji „Hasło” w `web/account/profile.html` (danych do sprzątania
  nie ma – funkcja niczego nie przechowuje poza `accounts.User.password` i audytem).
