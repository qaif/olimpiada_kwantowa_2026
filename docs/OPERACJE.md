# Operacje: kopie zapasowe, monitoring, alarmy, CI/CD, 2FA, drugi konkurs

Dokument dla osoby, która utrzymuje działający serwis – nie dla programisty i nie dla
koordynatora. Odpowiada na pięć pytań, które padają w tej kolejności:

1. **czy przeżyjemy utratę serwera** (kopie zapasowe i odtwarzanie),
2. **skąd się dowiemy, że coś nie działa** (monitoring i alarmy),
3. **jak wjeżdża nowa wersja** (CI/CD),
4. **jak chronione są konta z dostępem do cudzych danych** (2FA),
5. **jak dołożyć drugi konkurs, nie ruszając pierwszego** (§ 6).

Na końcu jest lista kontrolna incydentu – do otwarcia wtedy, gdy nie ma czasu czytać reszty.

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

Po każdym przestawieniu flagi: zaloguj się na konto jednej osoby z każdej roli i sprawdź, że widzi
to, co widziała. Flaga jest odwracalna w minutę, ale tylko wtedy, gdy ktoś zauważy w tej minucie.

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
