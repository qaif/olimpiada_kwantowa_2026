# Platforma Olimpiady

System do prowadzenia olimpiady przedmiotowej: część informacyjna (newsroom, zadania, archiwum,
wyniki) i pełny obieg zawodów – rejestracja uczestników, przyjmowanie rozwiązań z twardym
deadline'em, dwustopniowe ocenianie w skali 0/2/5/6, moderacja rozjazdów, reklamacje i publikacja
zanonimizowanych wyników.

Aplikacja jest jedna: **Django 6.1 + DRF + HTMX + Wagtail 8.0** w jednym procesie. Nie ma drugiego
systemu tożsamości ani drugiego panelu – redaktor, koordynator, recenzent i uczestnik logują się
tym samym kontem, a uprawnienia rozstrzygają grupy Django.

Architektura, model danych i uzasadnienia decyzji: **[`docs/PROJEKT.md`](docs/PROJEKT.md)**
(sekcje 1.2–1.4 – usługi, bezpieczeństwo uploadu, montaż CMS-a; sekcja 2.4 – workflow oceniania).
Stan prac i dług techniczny: [`docs/BACKLOG.md`](docs/BACKLOG.md).
Checklista bezpieczeństwa: [`docs/SECURITY_CHECKLIST.md`](docs/SECURITY_CHECKLIST.md).
Pokrycie testami: [`docs/COVERAGE.md`](docs/COVERAGE.md).

### Dokumentacja

Ten plik jest dokumentacją **techniczną**: opisuje, jak system działa i dlaczego tak. Podręczniki niżej
są napisane dla ludzi, którzy z niego korzystają, i dają się rozesłać w całości.

| Dokument | Dla kogo |
|---|---|
| [`docs/PODRECZNIK-ADMINISTRATORA.md`](docs/PODRECZNIK-ADMINISTRATORA.md) | instalacja, wdrożenie, DNS i poczta, kopie zapasowe, aktualizacje, awarie, dane osobowe |
| [`docs/PODRECZNIK-ORGANIZATORA.md`](docs/PODRECZNIK-ORGANIZATORA.md) | prowadzenie edycji: etapy, zadania, komitet, ocenianie, wyniki, dokumenty, RODO |
| [`docs/PODRECZNIK-UCZESTNIKA.md`](docs/PODRECZNIK-UCZESTNIKA.md) | rejestracja, panel, wysyłka rozwiązań, wyniki i reklamacje |
| [`docs/PODRECZNIK-RECENZENTA.md`](docs/PODRECZNIK-RECENZENTA.md) | kolejka pracy, ekran oceny, rubryka, szablony, terminy |
| [`docs/API.md`](docs/API.md) | integracje: klucze API i zakresy, `/api/v1/`, webhooki i weryfikacja podpisu, limity, wersjonowanie |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | historia wydań, po jednej linii na tag |

**Licencja:** [AGPL-3.0-or-later](LICENSE) — systemem wolno się posłużyć do prowadzenia własnej
olimpiady, pod warunkiem udostępnienia źródeł swojej wersji użytkownikom serwisu. Wybór, alternatywy
(MIT/Apache-2.0) i to, co organizator musi jeszcze rozstrzygnąć, opisuje
[`docs/LICENCJA-UZASADNIENIE.md`](docs/LICENCJA-UZASADNIENIE.md).

---

## 1. Wymagania

| Element | Wersja | Uwagi |
|---|---|---|
| Docker Engine / Docker Desktop | ≥ 24 | z wtyczką `docker compose` v2 |
| RAM | ≥ 6 GB dla Dockera | ClamAV trzyma bazę sygnatur w pamięci (~1,5 GB) |
| Dysk | ≥ 10 GB | obrazy, sygnatury ClamAV, wolumeny danych |
| Powłoka | Git Bash / WSL / dowolna POSIX-owa | skrypty w `scripts/` są bashowe |
| (opcjonalnie) Python 3.14 + `ruff` (venv przez `uv`) | – | wyłącznie do lintu poza kontenerem; tworzenie venv – § 7 |

Systemu **nie da się** sensownie uruchomić bez Dockera: deadline, skan antywirusowy i prywatny
storage wymagają Postgresa, Redisa, MinIO i ClamAV-a, a nie ich atrap.

## 2. Uruchomienie środowiska deweloperskiego

Siedem poleceń od pustego katalogu do działającego systemu z danymi demonstracyjnymi:

```bash
git clone <adres-repozytorium> && cd olimpiada-clade                                    # 1
DC="docker compose -f docker-compose.yml -f docker-compose.dev.yml"                     # 2
cp .env.example .env                                                                    # 3
for k in DJANGO_SECRET_KEY (min. 50 znaków; produkcja odmawia startu z krótszym lub domyślnym kluczem) POSTGRES_PASSWORD MINIO_ROOT_PASSWORD \
         S3_PUBLIC_SECRET_KEY S3_PRIVATE_SECRET_KEY; do \
  v=$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-40); sed -i "s|^$k=.*|$k=$v|" .env; done   # 4
$DC up -d --build web worker beat minio-init                                            # 5
$DC ps                                                                                  # 6 – czekamy na "web ... healthy"
$DC exec web python manage.py seed_demo && $DC exec web python manage.py seed_cms       # 7
$DC exec web python manage.py seed_regulamin                                            # 8
$DC exec web python manage.py seed_legacy_content && $DC exec web python manage.py seed_partners  # 9
$DC exec web python manage.py seed_schools                                              # 10
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
- **Krok 10** wgrywa słownik szkół ponadpodstawowych (8 118 pozycji z wykazu SIO) – bez niego
  wyszukiwarka szkół w `/register/` nie ma czego podpowiadać i zostaje sam wolny tekst.
  Szczegóły i procedura odświeżenia: 6.3b.
- **Krok 8** publikuje „Regulamin Olimpiady Kwantowej” pod `/dokumenty/regulamin/`: treść z
  `apps/cms/fixtures/regulamin/` trafia do strony CMS, oryginał `.docx` do biblioteki dokumentów
  Wagtaila. Komenda jest idempotentna, ale **nadpisuje treść strony** – po redakcji w `/cms/`
  drugi raz jej nie uruchamiamy.
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
| <http://localhost:8000/dokumenty/> | spis dokumentów organizatora (strona CMS) |
| <http://localhost:8000/dokumenty/regulamin/> | regulamin olimpiady (strona CMS + `.docx` do pobrania) |
| <http://localhost:8000/cms/> | panel redakcyjny Wagtaila (grupa `coordinator`) |
| <http://localhost:8000/admin/> | panel Django (`is_staff`) |
| <http://localhost:8000/api/docs/> | Swagger UI |
| <http://localhost:8000/healthz/> | healthcheck (`{"status":"ok","db":true,"redis":true,"db_connections":"ok"}`) |
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

Kod wpisuje się na `/register/committee/`. Adres prowadzi ze **stopki** („Rejestracja z kodem”),
a nie z paska konta: dotyczy kilkunastu osób na edycję, a pasek oglądają wszyscy odwiedzający
(uwaga organizatora z 16.09). Zapraszanie większej grupy naraz — bez przepisywania kodów ręcznie
— opisuje § 5.3.

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
7. **Poczta wychodząca (SMTP).** Bez niej nie działa reset hasła („Nie pamiętasz hasła?”) – jedyna
   droga odzyskania konta dla uczestnika, recenzenta, komisji i koordynatora. Domyślnie obsługuje ją
   usługa `mail` (własny Postfix z DKIM) – sekcja 4.1. Sama usługa nie wystarczy: bez rekordów
   SPF/DKIM/DMARC i PTR listy trafiają do spamu albo są odrzucane – sekcja 4.2.
8. **`EXTRA_DOMAINS`** – tylko wtedy, gdy na tej instalacji ma stanąć **więcej niż jeden konkurs**.
   Pusto (domyślnie) znaczy „jeden konkurs” i konfiguracja proxy jest wtedy bajt w bajt taka, jak
   `deploy/Caddyfile`. Procedura dołożenia konkursu: niżej, „Kolejny konkurs na tej samej instalacji”.

Certyfikat Let's Encrypt Caddy pobiera sam przy pierwszym starcie – wymaga otwartych portów 80 i 443
i poprawnego DNS-u dla obu nazw.

### Świeża instalacja z gotowego obrazu (bez budowania)

Wariant dla kogoś, kto stawia **własną** olimpiadę na własnym serwerze i nie ma powodu kompilować
niczego na miejscu: obraz aplikacji buduje CI i publikuje w GHCR (`.github/workflows/ci.yml`,
zadanie `image`; z gałęzi `main` i ze znaczników `v*`), a compose dostaje minimalny zestaw usług
przez nakładkę `docker-compose.operator.yml`.

```bash
git clone https://github.com/qaif/olimpiada_kwantowa_2026 && cd olimpiada_kwantowa_2026
cp .env.example .env                                   # 1 – uzupełnić jak w tabeli z sekcji 4
echo 'WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v0.24.0' >> .env                          # 2
DC="docker compose -f docker-compose.yml -f docker-compose.operator.yml"             # 3
$DC pull && $DC up -d                                  # 4 – osiem usług, bez ClamAV-a i MTA
$DC ps                                                 # 5 – czekamy na "web ... healthy"
```

- **Krok 2** jest tym, co odróżnia ten wariant od sekcji wyżej: bez `WEB_IMAGE` compose zbuduje
  obraz sam (kilkanaście minut i kompilator w wymaganiach). Lista wydań: *Packages* przy
  repozytorium; `:main` to stan gałęzi głównej, `:v…` – wydanie.
- **Krok 4** startuje `proxy`, `web`, `worker`, `beat`, `db`, `redis`, `minio`, `minio-init`.
  **Nie** startuje `clamav` (skan załączników – potrzebuje ~1,5 GB pamięci) ani `mail` (własny
  Postfix – bez rekordów DNS z sekcji 4.2 i tak nie doręczy poczty). Obie usługi dokłada ten sam
  zestaw plików z profilem: `$DC --profile full up -d`. Zestaw z profilem `full` jest **równy**
  zwykłemu `docker compose up -d` z samego `docker-compose.yml` – pilnuje tego
  `scripts/tests/compose_profiles_test.sh`. Przed otwarciem rejestracji włącz `full`: bez skanera
  nadesłane pliki zostają w stanie „oczekuje na skan”, a bez poczty nie działa reset hasła.
- **Krok po `up`:** wejdź na `/setup/` (kreator pierwszego uruchomienia – zakłada konto operatora
  i pierwszy konkurs). Kreator jest dostępny **wyłącznie** na instalacji, w której nie ma jeszcze
  ani konkursu, ani superużytkownika; na działającym serwisie ten adres odpowiada 404. Token
  wejściowy bierze się z `SETUP_TOKEN` w `.env`, a gdy zmiennej nie ma – kreator wypisuje go **raz**
  do logu kontenera `web` (`$DC logs web`). Runbook: [`docs/OPERACJE.md`](docs/OPERACJE.md) § 4.3.
- Aktualizacja takiej instalacji to podmiana tagu w `WEB_IMAGE` i `$DC pull && $DC up -d web worker
  beat`; migracje wykonuje entrypoint kontenera `web`, tak samo jak przy wdrożeniu skryptem.

### Aktualizacja działającej produkcji (`scripts/deploy.sh`)

`SITE_DOMAIN=… ACME_EMAIL=… scripts/deploy.sh root@<host>` wgrywa kod z `git archive HEAD`, buduje
obraz, uruchamia migracje (entrypoint `web`) i **nie rusza tego, co zmieniono na serwerze**:

- `.env` jest zachowywany (aktualizuje się tylko `APP_VERSION`),
- terminy i nazwy etapów należą do koordynatora – `seed_edition_kwantowa` tworzy wyłącznie
  brakujące etapy; przestawienie istniejących terminów wymaga jawnego `SYNC_STAGE_DATES=1`,
- strony CMS należą do redakcji – seedy treści (`seed_cms`, `seed_regulamin`, `seed_legacy_content`,
  `seed_partners`) uruchamiają się same **tylko przy pierwszym wdrożeniu** (znacznik `.first-deploy`
  obok `.env`); ponowny import treści z repozytorium wymaga `RUN_CONTENT_SEEDS=1` i nadpisuje
  poprawki zrobione w `/cms/`.

Kroki wdrożenia, o których warto wiedzieć:

- **4/8** składa konfigurację proxy (`scripts/render_caddyfile.sh`: `deploy/Caddyfile` +
  `EXTRA_DOMAINS`), buduje obraz i uruchamia **samą bazę**. Przy pustym `EXTRA_DOMAINS` wynik jest
  kopią `deploy/Caddyfile` co do bajtu – sprawdza to `scripts/tests/render_caddyfile_test.sh`.
  Obraz można **pobrać zamiast budować**: `WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v0.24.0
  scripts/deploy.sh root@<host>` robi w tym kroku `docker compose pull web` i zapisuje wartość
  w `.env` na serwerze (żeby widziały ją kolejne wywołania compose'a). **Bez** tej zmiennej krok
  wykonuje dokładnie to, co dotąd – `docker compose build --pull web` – i to jest droga domyślna
  dla Olimpiady Kwantowej; pilnuje tego `scripts/tests/deploy_image_source_test.sh`. Powrót do
  budowania: kolejne wdrożenie bez `WEB_IMAGE` (skrypt kasuje wtedy wpis z `.env`).
  Krok dokłada też do `.env` brakujące `EXTRA_DOMAINS=` i `CADDYFILE_PATH=`; istniejących wartości
  nie rusza.
- **4a/8 – kopia bazy przed migracjami.** `pg_dump -Fc` do
  `/opt/olimpiada-backups/pre-deploy-<data>-<wersja>.dump`, **zanim** entrypoint kontenera `web`
  wykona `migrate`. Niepowodzenie zatrzymuje wdrożenie: migracji bez kopii nie wykonujemy. Zostaje
  dziesięć ostatnich takich plików (kopie nocne z `scripts/backup.sh` to osobny zestaw).
  Odtworzenie: `docker compose exec -T db pg_restore -U olimpiada -d olimpiada --clean --if-exists < <plik>`.
- **4b/8** uruchamia komplet usług – i to tutaj wykonują się migracje.
- **6a/8 – nowy konkurs, krok opcjonalny.** Bez zmiennej `NEW_COMPETITION_SLUG` nie wykonuje ani
  jednego polecenia. Patrz niżej.
- na końcu wdrożenia `manage.py check_domains` wypisuje konkursy, których domena nie jest wpuszczona
  we wszystkich trzech konfiguracjach naraz. To jest **ostrzeżenie**, a nie bramka – wdrożenia nie
  zatrzymuje.

### Kolejny konkurs na tej samej instalacji

Platforma obsługuje wiele niezależnych konkursów z jednej bazy i jednego wdrożenia: konkurs to jedna
`wagtailcore.Site` (własne drzewo stron) plus jeden wiersz `tenancy.Competition`
(marka, organizator, adresowanie). Projekt i uzasadnienia: [`docs/UNIWERSALNY-ETAP-1.md`](docs/UNIWERSALNY-ETAP-1.md).

> **Na produkcji rób to z runbookiem: [`docs/OPERACJE.md`](docs/OPERACJE.md) § 6 („Drugi konkurs na
> tej samej instalacji”).** Ta sekcja opisuje **komendę**; runbook opisuje **kolejność** i to, czego
> tu nie ma: pre-flight `manage.py check_memberships` (kto straci dostęp, gdy o rolach przestanie
> rozstrzygać globalna grupa Django), przełączenie flag `memberships_enforced`
> i `competition_settings_page` w `/admin/` oraz jednorazowe zawężenie `/cms/` do konkursów
> (`docs/OPERACJE.md` § 6.7: `superkoordynator --all-current-coordinators`, potem `scope_cms_access`) — bez niego
> koordynator nowego konkursu dostałby razem z grupą `coordinator` dostęp do `/cms/` całej instalacji.

Co powstaje razem z konkursem: witryna i drzewo stron (puste, o właściwych adresach),
`cms.SiteSettings`, wiersz `tenancy.Competition` oraz **pierwsza edycja z etapami szablonu**.
Terminy etapów są wartością początkową odłożoną od pierwszego dnia następnego miesiąca — wyglądają
na zastępcze, bo są zastępcze, a harmonogram wpisuje koordynator w panelu.

**1. Załóż konkurs.** Komenda tworzy witrynę, drzewo stron (puste, o właściwych adresach),
`SiteSettings` i wiersz konkursu z szablonu — i **nie** uruchamia seedów treści, bo te wpisują
akapity Olimpiady Kwantowej:

```bash
docker compose exec web python manage.py create_competition \
  --slug fizyczna --name "Olimpiada Fizyczna" --domain olimpiadafizyczna.pl \
  --from-template przedmiotowa --organizer "Polskie Towarzystwo Fizyczne" \
  --contact-email biuro@example.org \
  --edition-label "I edycja 2026/2027" \
  --coordinator-email koordynator@example.org \
  --dry-run   # bez --dry-run zapisuje
```

`--edition-label` jest opcjonalne (domyślnie bieżący rocznik szkolny liczony od września).
`--coordinator-email` wymaga **istniejącego** konta — komenda kont nie zakłada i nieznany adres
jest dla niej błędem, a nie zaproszeniem do założenia konta bez wiedzy jego właściciela.

Szablony (`--from-template`, katalog w `backend/apps/tenancy/templates_catalog.py`):
`kwantowa` (struktura dzisiejszej konfiguracji: ELIM → wojewódzki → finał + trening, cztery formaty
plików, komplet zgód), `przedmiotowa` (trzy stopnie, sam PDF), `pusty` (szkielet serwisu).
Komenda odmawia, gdy identyfikator albo domena są zajęte, a `--dry-run` wykonuje całość i wycofuje
transakcję – sprawdza to, co sprawdzi baza, a nie to, co o niej pamiętamy.

To samo da się zrobić przy wdrożeniu (krok 6a, `--skip-existing`, więc wdrożenie da się powtórzyć):

```bash
NEW_COMPETITION_SLUG=fizyczna NEW_COMPETITION_NAME="Olimpiada Fizyczna" \
NEW_COMPETITION_DOMAIN=olimpiadafizyczna.pl NEW_COMPETITION_TEMPLATE=przedmiotowa \
scripts/deploy.sh root@<host>
```

**2. DNS.** Rekord A/AAAA `olimpiadafizyczna.pl` → adres serwera (i `www.olimpiadafizyczna.pl`,
jeżeli ta nazwa ma działać). Osobny rekord `s3.` **nie** jest potrzebny: bucket jest jeden i pliki
idą dalej przez `S3_PUBLIC_ADDRESS` domeny platformy.

**3. Domena w `/opt/olimpiada/.env`.** Dołożenie domeny musi zadziałać w trzech konfiguracjach
naraz, bo każdy brak milczy inaczej: brak w Caddym = brak certyfikatu i „no such site”, brak
w `ALLOWED_HOSTS` = 400 na każde żądanie, brak w `CSRF_TRUSTED_ORIGINS` = odmowa na każdym
formularzu. Dlatego wpisuje się ją **w jednym** miejscu:

```dotenv
EXTRA_DOMAINS=olimpiadafizyczna.pl www.olimpiadafizyczna.pl
DJANGO_ALLOWED_HOSTS=olimpiadakwantowa.pl,www.olimpiadakwantowa.pl,web,127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://olimpiadakwantowa.pl,https://www.olimpiadakwantowa.pl
```

`DJANGO_ALLOWED_HOSTS` i `DJANGO_CSRF_TRUSTED_ORIGINS` **nie muszą** wymieniać nowej domeny:
Django dokłada do obu list wszystko, co stoi w `EXTRA_DOMAINS` (`config/settings/base.py`).
Wpisanie ich wprost niczego nie psuje – wartości podane ręcznie zostają na początku list.

**4. Proxy.** `./scripts/render_caddyfile.sh && docker compose up -d proxy web worker beat`
(albo po prostu ponowne `scripts/deploy.sh`, które robi jedno i drugie). Caddy pobierze certyfikat
sam, gdy DNS już wskazuje serwer. Kontrola na koniec: `docker compose exec web python manage.py
check_domains --all`.

**5. Treść i terminy.** Regulamin, klauzulę RODO i pozostałe dokumenty wpisuje redakcja nowego
konkursu w `/cms/` – lista dokumentów, których wymaga wybrany szablon, jest w podsumowaniu komendy.
Etapy już są (z szablonu), ale ich **terminy są zastępcze**: koordynator poprawia je w panelu,
zanim otworzy rejestrację.

#### Wariant bez wdrożenia: subdomena platformy

Przy włączonym przełączniku `PLATFORM_SUBDOMAINS=1` konkurs zakładany przez koordynatora w panelu
dostaje adres `<slug>.<domena platformy>` (np. `fizyczna.olimpiadakwantowa.pl`) i działa **od razu**
— bez wdrożenia, bez wpisu w `.env` i bez nowego rekordu DNS, bo obsługuje je jeden rekord
wieloznaczny `*`, a certyfikat powstaje przy pierwszym wejściu (Caddy pyta aplikację, czy nazwa
należy do aktywnego konkursu). Wymaga jednorazowego przygotowania przez operatora i jest domyślnie
**wyłączony**: bez niego konfiguracja proxy jest co do bajtu ta, którą widać w `deploy/Caddyfile`.
Komplet — rekord DNS, przełącznik, flaga `competition_creation`, sprawdzenie, granice i wycofanie —
w [`docs/OPERACJE.md`](docs/OPERACJE.md) § 6.5 („Konkursy w subdomenach zakładane z panelu”).

#### Wariant bez własnej domeny: prefiks ścieżki

`create_competition --path-prefix fizyczna` daje konkurs pod `https://<domena platformy>/fizyczna/…`
bez rekordu DNS i bez zmian w Caddym. Trzy rzeczy, które trzeba wiedzieć, **zanim** się na to
zdecyduje (`docs/UNIWERSALNY-ETAP-1.md` § 2.3):

- **ciasteczka są wspólne.** Sesja i CSRF stoją na jednym haszczu ciasteczek dla całej domeny, więc
  zalogowanie w jednym konkursie loguje we wszystkich, które dzielą tę domenę. Konto jest jedno, więc
  nie jest to dziura – ale rozdziału sesji ten tryb nie daje i dlatego nie jest domyślny.
- **prefiks zajmuje pierwszy segment adresu.** Nie może to być slug zarezerwowany (`login`, `me`,
  `coordinator`, `api`, …) ani slug strony drugiego poziomu w innym konkursie – komenda odmawia.
- **gospodarz musi się zgodzić.** Prefiks działa wyłącznie pod domeną konkursu z przełącznikiem
  `path_prefix_routing` (konkurs witryny domyślnej – komenda włącza go sama i mówi o tym w wydruku).
  Pod prefiksem konkurs ma **własne drzewo stron CMS** (strona główna, menu, dokumenty,
  przekierowania), a linki – także w listach spoza żądania – prowadzą pod
  `https://<domena platformy>/fizyczna/…`. Runbook: [`docs/OPERACJE.md`](docs/OPERACJE.md) § 6.6.

### Własne Jitsi Meet do rozmów kwalifikacyjnych (`scripts/deploy_jitsi.sh`)

Rozmowy kwalifikacyjne mogą iść przez własną instancję Jitsi Meet pod `meet.<domena>` zamiast przez
publiczny `meet.jit.si` – dane rozmów nie opuszczają wtedy serwera organizatora. Instancja to osobny
projekt compose (`deploy/jitsi/docker-compose.jitsi.yml`: kontenery `jitsi/web`, `prosody`, `jicofo`,
`jvb`), uruchamiany po zwykłym wdrożeniu:

```bash
scripts/deploy_jitsi.sh root@169.58.242.197
```

Skrypt kopiuje pliki, tworzy jednorazowo `/opt/olimpiada/jitsi/.env` z sekretami, otwiera UDP 10000
(media), startuje kontenery i restartuje Caddy, który ma blok `meet.{$SITE_DOMAIN}` (`deploy/Caddyfile`)
i sam wystawi certyfikat, gdy tylko istnieje rekord DNS A `meet.<domena>` → adres serwera
(`deploy/dns-olimpiadakwantowa.pl.md`). Pokoje są otwarte, ale ich nazwy generuje portal losowo przy
zapisie na termin; włączona jest poczekalnia i strona „przed wejściem”. Po uruchomieniu ustaw w etapie
z rozmowami dostawcę wideo na własny serwer z adresem `https://meet.<domena>/`.

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
| `EXTRA_DOMAINS` | puste | domeny kolejnych konkursów, **rozdzielone spacjami**; wchodzą do bloków Caddy'ego (`scripts/render_caddyfile.sh`) oraz do `ALLOWED_HOSTS` i `CSRF_TRUSTED_ORIGINS` – patrz sekcja 3 |
| `CADDYFILE_PATH` | `./deploy/Caddyfile` | plik konfiguracji montowany do proxy; instalacja z `EXTRA_DOMAINS` używa `./deploy/Caddyfile.generated` |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `0` | w produkcji `1` |
| `WEB_WORKERS` | `3` | procesy gunicorna |
| `CELERY_CONCURRENCY` | `2` | wątki workera |
| `DB_POOL` | `1` w `web`, `0` w `worker`/`beat` (i bez pakietu `psycopg_pool`) | pula połączeń psycopg (`docs/OPERACJE.md` § 11.2) |
| `DB_POOL_MAX_SIZE` / `DB_POOL_MIN_SIZE` / `DB_POOL_TIMEOUT` | `WEB_THREADS` / `1` / `10` | rozmiar puli na worker gunicorna i czekanie na połączenie (s) |
| `DB_CONN_MAX_AGE` | `60` | trwałość połączenia – **tylko** w procesach bez puli (`worker`, `beat`) |
| `DB_CONNECTIONS_WARN_PERCENT` / `DB_CONNECTIONS_CRITICAL_PERCENT` | `80` / `95` | progi alarmu zajętości `max_connections` |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `olimpiada` / `olimpiada` / – | baza |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | – | konto administracyjne MinIO; backend go **nie** używa (tylko `minio-init`) |
| `S3_PUBLIC_ACCESS_KEY` / `S3_PUBLIC_SECRET_KEY` | `wagtail-media` / – | konto serwisowe bucketu `public-media` (media Wagtaila) |
| `S3_PRIVATE_ACCESS_KEY` / `S3_PRIVATE_SECRET_KEY` | `app-private` / – | konto serwisowe bucketu `submissions` (prace i treści zadań) |
| `S3_PUBLIC_ENDPOINT_URL` | `https://s3.<SITE_DOMAIN>` | adres MinIO widziany z przeglądarki |
| `S3_PRESIGNED_TTL_SECONDS` | `600` | ważność linku do pliku rozwiązania |
| `TRUSTED_PROXY_IPS` | podsieci compose | komu wolno podać `X-Real-IP` |
| `EMAIL_URL` | `consolemail://` (dev `.env`: `smtp://mailpit:1025`; produkcja z `deploy.sh`: `smtp://mail:587`) | poczta wychodząca – patrz 4.1 |
| `DEFAULT_FROM_EMAIL` | `noreply@localhost` (dev `.env`: `olimpiada@localhost`) | nadawca listów (także `SERVER_EMAIL`); domena musi mieć SPF/DKIM – patrz 4.2 |
| `EMAIL_TIMEOUT` | `10` | limit sekund na połączenie SMTP (wysyłka idzie w workerze Celery, kolejka `mail`) |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | puste | logowanie przez Google; puste = przycisk się nie pokazuje – patrz 4.4 |
| `FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET` | puste | logowanie przez Facebooka; puste = przycisk się nie pokazuje – patrz 4.4 |
| `E2E_MODE` | (nieustawiona) | **tylko dev**: odblokowuje `manage.py e2e_timeline`. W produkcji nigdy |

### 4.1 Poczta wychodząca (SMTP)

Bez działającej poczty nie działa reset hasła – **jedyna** droga odzyskania konta dla uczestnika,
recenzenta, komisji i koordynatora. W kontenerze aplikacyjnym nie ma MTA, a domyślne ustawienie
Django (`localhost:25`) skończyłoby się odmową połączenia w środku żądania POST. Są dwa warianty;
oba sprowadzają się do jednej zmiennej `EMAIL_URL`.

#### Wariant A (domyślny): własny Postfix w compose

Usługa `mail` (`boky/postfix`) jest **send-only relayem**: przyjmuje pocztę z sieci compose na
porcie 587 i doręcza ją wprost do serwerów MX odbiorców, bez pośrednika. `scripts/deploy.sh`
ustawia to jako domyślne przy pierwszym wdrożeniu:

```ini
EMAIL_URL=smtp://mail:587
DEFAULT_FROM_EMAIL=noreply@olimpiadakwantowa.pl
EMAIL_TIMEOUT=10
```

Co robi konfiguracja usługi (`docker-compose.yml`, sekcja `mail`):

| Ustawienie | Wartość | Po co |
|---|---|---|
| `image` | `boky/postfix:v5.1.0-alpine` | ostatnie wydanie z backendem OpenDKIM; v6 przechodzi na rspamd i zmienia ścieżkę wolumenu z kluczami |
| `ALLOWED_SENDER_DOMAINS` | `${SITE_DOMAIN}` | koperta przyjmowana wyłącznie dla nadawcy z domeny serwisu |
| `POSTFIX_mynetworks` | `127.0.0.0/8` + podsieci compose | klientem SMTP może być tylko kontener z `edge`/`internal` |
| `RELAYHOST` | (nieustawiony) | doręczanie wprost do MX odbiorcy, bez zewnętrznego dostawcy |
| `POSTFIX_smtp_tls_security_level` | `may` | STARTTLS, gdy odbiorca go ogłosi; `encrypt` odciąłby część odbiorców |
| `DKIM_AUTOGENERATE`, `DKIM_SELECTOR` | `true`, `olimpiada` | klucz RSA-2048 generowany przy pierwszym starcie |
| wolumen `mail_dkim` | `/etc/opendkim/keys` | klucz przeżywa `down`/`up`; inaczej po każdym restarcie trzeba by zmieniać DNS |
| `hostname`, `POSTFIX_myhostname` | `mail.${SITE_DOMAIN}` | HELO/EHLO; musi zgadzać się z rekordem PTR |
| brak `ports:` | – | relay nie jest osiągalny spoza sieci compose (sprawdzenie niżej) |
| `cap_drop: ALL` + 7 capabilities | `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`, `SYS_CHROOT`, `KILL` | minimum, przy którym master Postfiksa zrzuca uprawnienia i wchodzi do chroota `/var/spool/postfix` |

Sieci: `mail` jest w `internal` (żeby widziały ją `web`, `worker`, `beat`) **i** w `edge` – sieć
`internal` ma `internal: true`, czyli zero wyjścia na świat, a doręczenie do MX-a odbiorcy tego
wyjścia wymaga. `edge` nie publikuje portu 587 na hoście, więc z internetu relay jest niewidoczny.
Aplikacja **nie** ma `depends_on` na `mail`: awaria poczty nie może blokować startu serwisu.

W devie usługa się nie uruchamia – `docker-compose.dev.yml` nadaje jej nieużywany profil `never`,
a listy podglądamy w `mailpit` (`--profile dev`, <http://localhost:8025/>).

#### Wariant B: zewnętrzny dostawca poczty transakcyjnej

Wystarczy podmienić `EMAIL_URL` w `.env` i zrestartować procesy aplikacji; usługi `mail` można wtedy
nie uruchamiać.

```ini
# Port 587 + STARTTLS to wariant domyślny; dla implicit TLS na 465 użyj schematu smtps://.
EMAIL_URL=smtp+tls://uzytkownik:haslo@smtp.dostawca.example:587
DEFAULT_FROM_EMAIL=olimpiada@olimpiada.example.org
```

Obsługiwane schematy `EMAIL_URL` (`django-environ`): `smtp://host:port` (bez szyfrowania – mailpit
albo własny relay w sieci compose), `smtp+tls://` (STARTTLS), `smtps://` (TLS od pierwszego bajtu),
`consolemail://` (wypis do logu – wartość domyślna, gdy zmiennej nie ma) i `filemail://`. Hasło
z `@` albo `:` trzeba zakodować procentowo (`%40`, `%3A`).

### 4.2 Rekordy DNS dla poczty

Bez nich listy trafiają do spamu albo są odrzucane – i to niezależnie od wariantu. Wartości dla
własnego Postfiksa wypisuje `scripts/deploy.sh` (krok 7/7) i zapisuje do `/opt/olimpiada/mail-dns.txt`.
`<IP>` to publiczny adres serwera, `<selektor>` to `DKIM_SELECTOR` (domyślnie `olimpiada`).

| Typ | Nazwa | Wartość | Uwaga |
|---|---|---|---|
| TXT | `<domena>` (korzeń strefy, `@`) | `v=spf1 ip4:<IP> -all` | wariant B: `include:` dostawcy zamiast `ip4:` |
| TXT | `<selektor>._domainkey.<domena>` | `v=DKIM1; h=sha256; k=rsa; s=email; p=<klucz publiczny>` | odczyt: `docker compose exec mail cat /etc/opendkim/keys/<domena>.txt` (format strefy BIND – wartość trzeba skleić z fragmentów w cudzysłowach) |
| TXT | `_dmarc.<domena>` | `v=DMARC1; p=quarantine; rua=mailto:<adres raportów>; adkim=r; aspf=r; fo=1` | zacząć od `p=none`, jeśli chcesz najpierw pooglądać raporty |
| A | `mail.<domena>` | `<IP>` | nazwa z HELO musi się rozwiązywać |
| PTR | – | `<IP>` → `mail.<domena>` | **nie w strefie domeny**: ustawia się w panelu dostawcy serwera (rDNS). Bez tego Gmail i Outlook odrzucają pocztę mimo poprawnych SPF/DKIM |

Dwie pułapki przy wklejaniu:

- **Rekord DKIM jest dłuższy niż 255 znaków**, czyli więcej, niż mieści jeden ciąg TXT w protokole
  DNS. Panel operatora ma to podzielić sam – wklejamy jedną, nieprzerwaną wartość (tak, jak
  wypisuje ją `mail-dns.txt`), bez cudzysłowów, spacji i łamania wierszy w kluczu `p=`.
- Adres z `DEFAULT_FROM_EMAIL` musi być w tej samej domenie, co rekordy SPF i DKIM – inaczej
  uwierzytelnienie nie zadziała mimo poprawnych rekordów (wyrównanie DMARC).

### 4.3 Weryfikacja poczty po wdrożeniu

W logu startowym `web` **nie może** być ostrzeżenia `EMAIL_URL wskazuje localhost:25`
(`config/settings/production.py` – brak SMTP nie blokuje startu, ale zostawia ślad).

```bash
# 1. Usługa działa i ma klucz DKIM
docker compose ps mail
docker compose exec mail cat /etc/opendkim/keys/"$SITE_DOMAIN".txt

# 2. Wysyłka testowa (</dev/null: `exec` nie może czytać stdin skryptu)
docker compose exec -T web python manage.py shell -c   "from django.core.mail import send_mail; print(send_mail('Test SMTP', 'Treść testowa.', None, ['ty@example.org']))" </dev/null

# 3. Status doręczenia i podpis DKIM w logu relaya
docker compose logs mail --tail 30
#   opendkim[…]: <id>: DKIM-Signature field added (s=olimpiada, d=<domena>)
#   postfix/smtp[…]: Untrusted TLS connection established to <MX>:25: TLSv1.3 …
#   postfix/smtp[…]: <id>: to=<…>, relay=<MX>:25, …, status=sent (250 2.0.0 OK …)

# 4. Relay nie jest widoczny z internetu (musi odmówić połączenia)
nc -vz <publiczne-IP> 587
```

Statusy w logu: `status=sent` – MX odbiorcy przyjął list; `status=bounced` – odrzucił na stałe
(kod 5xx w nawiasie mówi dlaczego, zwykle brak SPF/DKIM/PTR); `status=deferred` – spróbuje ponownie
(kolejka: `docker compose exec mail postqueue -p`).

### 4.4 Logowanie przez Google i Facebooka (OAuth)

Funkcja jest **opcjonalna i domyślnie wyłączona**. Bez kluczy strony `/login/` i `/register/`
wyglądają dokładnie tak, jak przed jej dodaniem – nie ma sekcji „Lub kontynuuj z”, a adresy
`/accounts/…` zwracają 404.

Co daje włączenie:

- uczestnik loguje się kontem Google/Facebooka zamiast hasła; konto założone tą drogą **nie ma
  hasła** (gdyby chciał logować się także hasłem, ustawia je przez „Nie pamiętasz hasła?”),
- po pierwszym logowaniu trafia na stronę **dokończenia rejestracji** (`/rejestracja/dokoncz/`):
  szkoła, województwo, rok urodzenia, zgoda RODO. Bez zgody RODO konto **nie powstaje**.
  Województwo jest listą zamkniętą 16 pozycji (ten sam `<select>` co na `/register/`),
  przechowywaną jako slug ASCII (`mazowieckie`, `lodzkie`) – etykietę z diakrytykami dokłada
  warstwa prezentacji (`get_district_display`, pole `district_label` w API),
- konta komitetu tą drogą **nie powstają** – rejestracja recenzenta zostaje wyłącznie na kod
  zaproszenia. Istniejący członek komitetu może się natomiast zalogować Google'em.

Adresy powrotne (redirect URI), które trzeba wpisać u dostawcy:

```
https://<SITE_DOMAIN>/accounts/google/login/callback/
https://<SITE_DOMAIN>/accounts/facebook/login/callback/
```

#### Google Cloud Console

1. <https://console.cloud.google.com/> → utwórz projekt (np. „Olimpiada Kwantowa”).
2. **APIs & Services → OAuth consent screen**: typ **External**, opublikuj („Publish app”), inaczej
   zalogują się wyłącznie konta dopisane ręcznie jako testerzy. Wypełnij: nazwa aplikacji,
   e-mail wsparcia, logo, **link do polityki prywatności** `https://<SITE_DOMAIN>/dokumenty/rodo/`,
   link do regulaminu `https://<SITE_DOMAIN>/regulamin/`, domena autoryzowana `<SITE_DOMAIN>`.
3. **Zakresy**: wyłącznie `.../auth/userinfo.email`, `.../auth/userinfo.profile` i `openid`.
   To są zakresy „nieczułe” – aplikacja nie przechodzi wtedy weryfikacji bezpieczeństwa Google.
4. **Credentials → Create credentials → OAuth client ID**, typ **Web application**:
   - *Authorized JavaScript origins*: `https://<SITE_DOMAIN>`,
   - *Authorized redirect URIs*: `https://<SITE_DOMAIN>/accounts/google/login/callback/`.
5. Skopiuj **Client ID** i **Client secret** do `.env`:
   `GOOGLE_OAUTH_CLIENT_ID=…`, `GOOGLE_OAUTH_CLIENT_SECRET=…`.

#### Meta for Developers (Facebook)

1. <https://developers.facebook.com/apps/> → **Create app** → przypadek użycia
   **Authenticate and request data from users with Facebook Login** → typ **Consumer**.
2. Dodaj produkt **Facebook Login → Settings**:
   - *Valid OAuth Redirect URIs*: `https://<SITE_DOMAIN>/accounts/facebook/login/callback/`,
   - *Client OAuth Login*, *Web OAuth Login*: włączone; *Enforce HTTPS*: włączone.
3. **App settings → Basic**: *App Domains* = `<SITE_DOMAIN>`, *Site URL* = `https://<SITE_DOMAIN>`,
   **Privacy Policy URL** = `https://<SITE_DOMAIN>/dokumenty/rodo/` (Meta **wymaga** tego adresu do
   przełączenia aplikacji w tryb Live), *Terms of Service URL* = `https://<SITE_DOMAIN>/regulamin/`,
   *User data deletion* – adres kontaktowy albo `https://<SITE_DOMAIN>/kontakt/`, kategoria: Education.
4. **App Review → Permissions and Features**: uprawnienia `email` i `public_profile` są dostępne
   od razu (Advanced Access bez przeglądu). Innych nie prosimy.
5. Przełącz aplikację na **Live** (przełącznik u góry panelu). W trybie Development zalogują się
   wyłącznie osoby dodane w *App roles*.
6. Skopiuj **App ID** i **App secret** do `.env`: `FACEBOOK_APP_ID=…`, `FACEBOOK_APP_SECRET=…`.

#### Wdrożenie i weryfikacja

```bash
# Klucze wchodzą przez env_file – wystarczy odtworzyć procesy aplikacji.
docker compose up -d web worker beat

# Sekcja „Lub kontynuuj z” pojawia się tylko dla dostawcy z kompletem kluczy.
curl -s https://<SITE_DOMAIN>/login/ | grep -o 'accounts/[a-z]*/login/'

# Nagłówek CSP wymienia ekran zgody włączonego dostawcy w form-action.
curl -sI https://<SITE_DOMAIN>/login/ | grep -o "form-action [^;]*"
```

Wyłączenie funkcji: wyczyść zmienne w `.env` i odtwórz procesy. Konta założone przez dostawcę
zostają – ich właściciele odzyskują dostęp przez „Nie pamiętasz hasła?”, bo adres e-mail konta
jest ten sam, którego używali u dostawcy.

**Uwaga o Facebooku.** Facebook nie potwierdza, że przekazany adres e-mail należy do osoby, która
się loguje, więc logowanie przez Facebooka **nigdy** nie łączy się z kontem, które już istnieje
w serwisie – użytkownik dostaje wtedy stronę „konto istnieje, zaloguj się hasłem albo je zresetuj”.
Google robi to automatycznie, ale wyłącznie dla adresu oznaczonego przez niego jako zweryfikowany;
konsekwencje opisuje `docs/SECURITY_CHECKLIST.md` § 3.2.

### 4.5 Google Analytics 4

Analityki **nie włącza wdrożenie, tylko organizator** — identyfikator strumienia GA4 jest polem
w CMS-ie, a nie zmienną środowiskową: założenie usługi i wklejenie `G-…` należy do właściciela
serwisu, a przy zmiennej każde takie wklejenie byłoby deployem.

**Gdzie:** `/cms/` → Ustawienia → Dane serwisu → sekcja **Analityka** → „identyfikator Google
Analytics (G-…)”. Format jest walidowany (`^G-[A-Z0-9]{6,}$`), więc `UA-…` ani identyfikator
kontenera Tag Managera (`GTM-…`) nie przejdą — pomyłka objawiłaby się dopiero pustym raportem.

**Puste pole = analityki nie ma w ogóle.** Żaden skrypt Google'a nie wchodzi do strony, pasek
cookie zostaje informacją z jednym przyciskiem „Rozumiem”, w stopce nie ma „Ustawień cookies”,
a nagłówek CSP jest co do bajtu taki, jak przed dodaniem tej funkcji (pilnuje tego test
`apps/web/tests/test_analytics.py`).

**Z identyfikatorem** pasek zamienia się w pytanie o zgodę („Akceptuję wszystkie” / „Tylko
niezbędne”), bo cookie analityczne wolno zapisać dopiero po zgodzie uprzedniej (art. 173 Prawa
telekomunikacyjnego — odpowiednio Prawa komunikacji elektronicznej — oraz art. 6 ust. 1 lit. a
RODO). Tag Google stoi w `<head>` każdej strony dokładnie tak, jak każe instrukcja GA (decyzja
organizatora z 15 września 2026 – weryfikacja instalacji i raporty widzą tag na każdej odsłonie),
ale w **trybie zgody** (Consent Mode v2): do czasu decyzji `analytics_storage` jest `denied`, więc
biblioteka nie zapisuje cookie ani identyfikatorów i wysyła wyłącznie bezcookie'owe sygnały
techniczne. Po kliknięciu
„Akceptuję wszystkie” pomiar startuje od razu, bez przeładowania strony. Decyzja mieszka
w `localStorage` (`cookie-consent` = `all`/`necessary`, `cookie-consent-at` = znacznik czasu),
a odnośnik **„Ustawienia cookies”** w stopce otwiera pasek ponownie — wybór „Tylko niezbędne”
kasuje wtedy pliki `_ga*`. Loader (`backend/static/js/analytics.js`) wyłącza Google Signals
i personalizację reklam oraz deklaruje anonimizację IP.

**CSP:** hosty `googletagmanager.com`, `*.google-analytics.com` i `*.analytics.google.com`
dochodzą do `script-src`/`connect-src`/`img-src` **tylko wtedy**, gdy identyfikator jest wpisany
(`apps/web/middleware.py`). Odpowiedź „czy analityka jest włączona” jest pamiętana w procesie
przez 30 s (`apps/cms/analytics.py`), bo nagłówek powstaje także dla plików statycznych — zapis
ustawienia w `/cms/` unieważnia tę pamięć od razu w procesie, który go przyjął, a w pozostałych
workerach zmiana jest widoczna najpóźniej po pół minuty.

Weryfikacja po wdrożeniu:

```bash
# Z wpisanym identyfikatorem host Google'a jest w polityce; bez niego nie ma go wcale.
curl -sI https://<SITE_DOMAIN>/ | grep -o "googletagmanager[^ ;]*" | head -1

# Tag Google stoi w <head> (z nonce), a identyfikator na <html data-ga-id> i w <meta>.
curl -s https://<SITE_DOMAIN>/ | grep -c 'googletagmanager.com/gtag/js'   # 1 z identyfikatorem, 0 bez
```

Kontrola przeglądarkowa (dev, liczy prawdziwe żądania sieciowe): `e2e/check_consent.py` —
sposób uruchomienia w docstringu pliku.

**Po stronie usługi GA4 zostają dwie decyzje organizatora** (6.7, „Decyzje do podjęcia przez
właściciela”, punkt 16): ustawienie retencji danych zdarzeń i akceptacja warunków przetwarzania
danych Google'a.

## 5. Role i przepływ etapu

Pięć grup Django: `participant`, `reviewer`, `appeals`, `coordinator`, `supervisor`. Pełna macierz
uprawnień – `docs/PROJEKT.md` 2.3. Konto komitetu powstaje wyłącznie na kod zaproszenia; konto
uczestnika i konto opiekuna szkolnego – z otwartej rejestracji (`supervisor` nie daje samym
założeniem dostępu do niczyich danych, patrz 5.6).

| # | Krok | Kto | Ekran / endpoint |
|---|---|---|---|
| 0 | Terminy etapu i arkusz zadań | koordynator | `/coordinator/` → „Edytuj terminy”, „Zadania (n)” – patrz 6.3 |
| 1 | Rejestracja uczestnika | uczestnik | `/register/` → `POST /api/auth/register/participant/`; okno rejestracji ustawia koordynator — patrz 6.3a |
| 2 | Rejestracja członka komitetu na kod | recenzent / komisja | `/register/committee/`; kod z `manage.py create_invitation`, z panelu koordynatora albo z zaproszenia wysłanego e-mailem — patrz 5.3 |
| 3 | Zatwierdzenie konta `PENDING` | koordynator | `/coordinator/committee/` → „Oczekujący na zatwierdzenie” |
| 4 | Zapis do eliminacji | uczestnik | `/me/` → „Zgłoś się do etapu eliminacyjnego” |
| 5 | Upload rozwiązania (przed deadline) | uczestnik | `/me/`, karta zadania (HTMX) → `POST /api/stages/<id>/problems/<n>/submissions/` |
| 6 | Skan antywirusowy | Celery → ClamAV | status pliku w karcie zadania: `oczekuje na skan` → `czysty` |
| 7 | Zamknięcie etapu | `beat` po `deadline_at + grace_seconds`, albo koordynator ręcznie | `/coordinator/` → karta etapu → „Więcej” → „Zamknij etap” |
| 8 | Przydział 2 recenzentów (ślepy, bez konfliktu województwa) | koordynator | `/coordinator/` → karta etapu → „Więcej” → „Przydziel recenzentów”; ręcznie: „Przydziały i oceny” – patrz 6.4 |
| 9 | Dwie niezależne oceny | recenzenci | `/review/`, `/review/<id>/` (podgląd PDF + adnotacje) |
| 10 | Zgodne oceny → `FinalGrade(CONSENSUS)`; rozjazd → `MODERATION` | system | – |
| 11 | Rozstrzygnięcie rozjazdu | koordynator (posiedzenie) lub trzeci recenzent | `/coordinator/moderation/` |
| 12 | Otwarcie okna reklamacji | `beat` wg `appeal_window_opens_at` | – |
| 13 | Reklamacja na własną pracę | uczestnik | `/me/` → „Reklamacje” |
| 14 | Decyzja odwoławcza (bez autorów recenzji rundy 1) | komisja odwoławcza | `/appeals/` |
| 15 | Zamknięcie okna → `GRADED_PROVISIONAL` → `FINAL` | `beat` | – |
| 16 | Przeliczenie progów i publikacja | koordynator | `/coordinator/stages/<id>/results/` → „Przelicz wyniki (podgląd)”, potem „Opublikuj wyniki” |
| 17 | Ogłoszona tabela | wszyscy, bez logowania | `/results/<stage_id>/` oraz strona CMS `/wyniki/` |
| 18 | Własny wynik i informacja zwrotna | uczestnik | `/me/` → „Moje wyniki”, `/me/stages/<id>/feedback/` – patrz 5.5 |

Publikacja przed zamknięciem okna reklamacji kończy się `409 APPEAL_WINDOW_OPEN`; tryb `FULL`
(nazwiska) jest dopuszczony wyłącznie w finale, dla laureatów, za zgodą – patrz `PROJEKT.md` 2.4.

### 5.1 Aktywacja konta e-mailem

Każde nowe konto — z otwartej rejestracji, z API, z kodu zaproszenia i z logowania przez dostawcę,
który adresu **nie** potwierdził (Facebook) — powstaje z `is_active=False` i czeka na kliknięcie
linku wysłanego na podany adres. Konto z Google jest aktywne od razu, bo Google podaje
`email_verified`. Powód: adres e-mail jest loginem i jedyną drogą odzyskania konta — literówka
dawałaby konto bez powrotu, a cudzy adres dałoby się zająć kontem-widmem.

**Link jest ważny 24 godziny i tyle samo żyje nieaktywowane konto.** Po tym czasie
`apps.accounts.tasks.purge_unactivated_accounts` (beat, co 15 minut) kasuje je razem z profilem,
więc adres zwalnia się do ponownej rejestracji — inaczej uczeń, który nie doczekał listu,
odbijałby się na zawsze o „konto z tym adresem już istnieje”. Konta, do których odwołuje się
dokumentacja zawodów (zgłoszenie, praca, recenzja), nie są kasowane nigdy: trafiają do logu
workera. Ręcznie: `docker compose exec web python manage.py purge_unactivated_accounts`.

| Co | Gdzie |
|---|---|
| Link z listu | `/activate/<token>/` — aktywuje i pokazuje „Konto aktywne – zaloguj się” (bez automatycznego logowania) |
| Ponowna wysyłka | `/activate/resend/` — link stały na stronie logowania; odpowiedź jest zawsze ta sama (brak enumeracji kont), limit wspólny z resetem hasła (5/h) |
| Obejście organizatora | `/coordinator/activations/` → „Aktywuj ręcznie” / „Wyślij link ponownie” |

Sekcja w panelu koordynatora jest **obejściem na czas** problemów z dostarczalnością poczty: dopóki
domena nadawcy nie ma rekordów SPF/DKIM (§ 4.2), część listów nie dochodzi. Lista pokazuje czas
pozostały do skasowania konta, a ręczna aktywacja zostawia inny wpis audytowy
(`account.activated_by_coordinator`) niż kliknięcie linku (`account.activated`) — bo adres został
potwierdzony czym innym.

Wysyłka idzie przez kolejkę `mail` (`apps.core.tasks.send_mail_task`), po commicie. Link buduje się
z żądania (`request.build_absolute_uri`), więc za Caddy jest `https`; dla wysyłek spoza żądania
można ustawić opcjonalne `SITE_URL`.

### 5.2 Własne konto: edycja danych, adres e-mail, usunięcie

| Co | Gdzie | Uwagi |
|---|---|---|
| Edycja danych uczestnika | `/me/profile/` (link „Edytuj dane” w panelu) | imię, nazwisko, **telefon**, województwo, szkoła (ta sama wyszukiwarka SIO co w rejestracji), klasa, rocznik; audyt `participant.profile_updated` z listą zmienionych pól |
| Edycja danych pozostałych ról | `/account/profile/` | wyłącznie imię i nazwisko. Województwo członka komitetu (opcjonalne) zmienia **tylko** koordynator: to na nim opiera się reguła konfliktu interesów w przydziale recenzji |
| Zmiana adresu e-mail | `/account/email/` → link z `/account/email/confirm/<token>/` | do potwierdzenia obowiązuje adres dotychczasowy; unikalność sprawdzana bez względu na wielkość liter; stary adres dostaje powiadomienie. Audyt `account.email_changed` |
| Usunięcie konta | `/account/delete/` (link „Usuń konto” w panelu) | patrz niżej |
| Pobranie swoich danych (art. 20 RODO) | `/account/export/` (przycisk „Pobierz moje dane” w profilu) | paczka ZIP z `dane.json` i wgranymi plikami; jedna na 10 minut. Patrz 6.14 |
| API | `PATCH /api/auth/me/` | te same pola co formularz; bez adresu e-mail, hasła i `public_code` |
| Cudze konto (organizator) | `/coordinator/accounts/` | koordynator poprawia dane, blokuje logowanie i usuwa dowolne konto poza kontami koordynatorów — patrz 5.4 |

Hasła ten ekran nie zmienia — do tego służy „Nie pamiętasz hasła?” (§ 3.1 checklisty
bezpieczeństwa), bo ta droga potwierdza dostęp do skrzynki.

**Telefon** jest wymagany od każdego nowego uczestnika (kontakt organizacyjny). Profile sprzed
wprowadzenia pola numeru nie mają i nie są unieważniane. Zapis jest normalizowany
(`apps.accounts.phones`): dziewięciocyfrowy numer krajowy dostaje prefiks `+48`, separatory
i zapis `00` znikają. Numer widać wyłącznie w profilu właściciela i w podglądzie koordynatora —
do tabeli wyników nie wchodzi.

**Usunięcie konta (art. 17 RODO) ma dwie drogi**, bo prawo do usunięcia własnych danych nie jest
prawem do usunięcia dokumentacji zawodów:

- konto **ze śladem** w zawodach (zgłoszenie, praca, recenzja) jest **anonimizowane**: e-mail
  zmienia się na `deleted-<pk>@invalid.olimpiadakwantowa.pl`, imię, nazwisko, telefon, szkoła
  i rocznik znikają, hasło staje się nieużywalne, powiązania z Google/Facebookiem, tokeny API
  i sesje są usuwane, zgody dostają `withdrawn_at`. Zostaje `Participant.public_code`, więc
  ogłoszone tabele wyników dalej mają swój pseudonimowy wiersz. Audyt `account.anonymised`,
- konto **bez** takiego śladu jest kasowane w całości razem z profilem i zgodami (audyt
  `account.deleted`, w `diff` wyłącznie identyfikator), a adres zwalnia się do ponownej rejestracji.

POST wymaga aktualnego hasła; konto zakładane przez dostawcę zewnętrznego (bez użytecznego hasła)
potwierdza operację przepisaniem własnego adresu e-mail. Koordynator i superużytkownik tą drogą nie
przechodzą — ich konto jest jedynym wejściem do prowadzenia edycji.

### 5.3 Zaproszenia do komitetu e-mailem

Kod zaproszenia można rozdać dwiema drogami — obie prowadzą do tego samego formularza
`/register/committee/`:

| Droga | Gdzie | Co dostaje zapraszany |
|---|---|---|
| Pojedynczy kod „do ręki” | `/coordinator/committee/` → „Kod zaproszenia” (albo `manage.py create_invitation`) | kod pokazany koordynatorowi **raz**, do przekazania własnym kanałem; może mieć `max_uses > 1` |
| Wysyłka listem | `/coordinator/committee/` → „Zaproszenia e-mailem” | **własny, jednorazowy** kod w liście z linkiem, terminem ważności i opcjonalną dopiską koordynatora |

Koordynator wkleja listę adresów (nowe wiersze, przecinki, średniki albo spacje — parser przyjmuje
każdy z nich), najwyżej 200 na raz. Adresy są sprowadzane do małych liter i odsiewane z powtórzeń;
**błędny adres zatrzymuje całą wysyłkę** i jest wypisany z nazwy, bo przy częściowej wysyłce nie
dałoby się już stwierdzić, do kogo kod poszedł. Adresy, które mają już konto z profilem komitetu,
są pomijane z powodem („ma już konto komisji”).

Co zostaje w bazie: adres (`InvitationCode.email`), chwila wysyłki (`sent_at`), termin (`expires_at`),
parametry kodu i — jak dotąd — **wyłącznie sha256 kodu**. Kod jawny istnieje tylko w wysłanej
wiadomości; nikt, także organizator, nie odtworzy go z bazy ani z audytu (wpisy `invitation.sent`,
`invitation.resent`, `invitation.revoked` mają adres i parametry, nigdy kod ani treść listu).

Stąd semantyka przycisków w tabeli „Wysłane zaproszenia”:

- **Wyślij ponownie** — nie powtarza starego kodu (nie ma skąd), tylko **unieważnia go** i wystawia
  nowy, z tymi samymi parametrami i tak samo długą ważnością, liczoną od nowa. Dzięki temu list,
  który jednak dotarł po czasie, nie zostaje drugim ważnym poświadczeniem,
- **Unieważnij** — ustawia `revoked_at`; od tej chwili rejestracja kodu nie przyjmuje. Wiersz
  zostaje, bo to jedyna odpowiedź na pytanie „dlaczego ten adres dostał od nas list”.

Obu odmawiamy (409), gdy kod został już użyty — konta założonego na kod nie cofa się
unieważnieniem, tylko zawieszeniem członka komitetu. Stan w tabeli jest wyliczany, nie zapisany,
w kolejności: **użyte → unieważnione → wygasłe → wysłane**.

Wysyłka idzie tą samą drogą, co listy aktywacyjne: zadanie na kolejce `mail`, kolejkowane po
commicie (`transaction.on_commit`), adres bezwzględny z żądania albo z `SITE_URL`.

### 5.4 Konta w panelu koordynatora (`/coordinator/accounts/`)

Odnośnik **„Wszystkie konta”** stoi w menu panelu i w nagłówku kolejki `/coordinator/activations/`.
Sekcja pulpitu zostaje bez zmian i dotyczy wyłącznie kont, które nie dokończyły rejestracji
(„Aktywuj ręcznie”, „Wyślij link ponownie” — § 5.1); ten ekran obejmuje **wszystkie** konta.

Po co, skoro jest `/admin/`: panel administracyjny nie zna reguł tej domeny. Zmiana adresu e-mail
nie sprząta tam wpisów `allauth`, skasowanie konta uczestnika zabiera kaskadą jego zgłoszenia,
prace i recenzje (czyli protokół zawodów), a po żadnej z tych operacji nie zostaje wpis w audycie
razem z resztą historii sprawy.

| Co | Gdzie | Uwagi |
|---|---|---|
| Lista kont | `/coordinator/accounts/` | rola (uczestnik / członek komitetu / komisja odwoławcza / koordynator / bez roli), e-mail, imię i nazwisko, kod publiczny, stan, data założenia; wyszukiwarka `?q=` (e-mail, imię, nazwisko, kod publiczny), filtr `?role=` (`participant` / `committee` / `other`), 50 kont na stronę |
| Edycja konta | `/coordinator/accounts/<id>/` | imię, nazwisko, adres e-mail, „Konto aktywne”; dla uczestnika dodatkowo telefon, województwo, szkoła (ta sama wyszukiwarka SIO co w rejestracji), klasa, rocznik; dla członka komitetu status, komisja odwoławcza i województwo |
| Usunięcie konta | `/coordinator/accounts/<id>/delete/` | strona potwierdzenia mówi, co się stanie: anonimizacja albo skasowanie wiersza |

Trzy rzeczy, które ten ekran robi inaczej niż samoobsługa (§ 5.2):

- **adres e-mail zmienia się od razu**, bez listu potwierdzającego — dowodem jest decyzja
  organizatora, który zwykle właśnie dzwoni do uczestnika, bo do skrzynki z literówką nic nie
  dochodzi. Wpisy `allauth` ze starym adresem są kasowane tak samo jak przy potwierdzeniu, żeby
  konta nie dało się dalej połączyć z Google po adresie, który zaraz może należeć do kogoś innego.
  `email_verified_at` zostaje nietknięte — wyzerowanie wstawiłoby konto pod kosiarkę
  nieaktywowanych rejestracji (§ 5.1),
- **„Konto aktywne”** to wyłącznik logowania (`is_active`), odwracalny i nieniszczący: dane, prace
  i recenzje zostają. To pierwsze narzędzie przy sporze — usunięcia cofnąć się nie da,
- **status członka komitetu** ustawiony na „aktywny” przechodzi tą samą drogą, co przycisk
  „Zatwierdź” na pulpicie (`approve_committee_member`): status, data, kto zatwierdził i grupy
  `reviewer`/`appeals`. Zawieszenie grup nie zdejmuje — prawo do recenzowania rozstrzyga status
  profilu. Województwo jest opcjonalne; wartość od koordynatora dostaje `district_verified=True`,
  wyczyszczenie pola zdejmuje tę flagę (jak w „Województwa członków komitetu”).

**Konta koordynatora i superużytkownika są chronione**: widać je na liście z odznaką „chronione”,
ale otwierają się tylko do odczytu i nie mają przycisku usunięcia (serwis odmawia kodem
`COORDINATOR_PROTECTED`). Dwóch koordynatorów mogłoby się inaczej nawzajem zablokować jednym
kliknięciem, a `InvitationCode.created_by` jest na nich `PROTECT`. Własne imię i nazwisko
koordynator zmienia w `/account/profile/`, resztę — administrator w `/admin/`. Własnego konta nie
usunie też z tego ekranu (`SELF_DELETE`); do tego służy `/account/delete/` z potwierdzeniem
tożsamości.

Usunięcie ma **te same dwie drogi**, co żądanie właściciela (§ 5.2): konto ze śladem w zawodach
(zgłoszenie, praca, recenzja) jest anonimizowane, konto bez takiego śladu znika w całości, a adres
zwalnia się do ponownej rejestracji.

Audyt: `account.updated_by_coordinator` (jeden wpis na zapis formularza, w `diff` **nazwy**
zmienionych pól — dane osobowe wchodzą tam jako samo „zmienione”, bez wartości; zmiana adresu
dokłada `email_changed_without_confirmation`), `account.deleted_by_coordinator`
(`{result, had_footprint}`, bez danych osobowych) oraz — z rdzenia usuwania — `account.anonymised`
albo `account.deleted`, tym razem z koordynatorem jako wykonawcą.

**Karta uczestnika** (`/coordinator/participants/<id>/`, odnośnik „Karta” przy wierszu listy kont
i przycisk w nagłówku edycji konta) jest drugą stroną tej samej osoby: tamten ekran poprawia
**dane konta**, ten odpowiada na pytania o **przebieg zawodów**. W jednym miejscu stoją: dane
i stan konta razem ze zgodą opiekuna, rejestr zgód (`ConsentRecord` — co, w jakiej wersji
dokumentu, kiedy, którą drogą), etapy z progiem kwalifikacji i decyzją komitetu wraz
z uzasadnieniem, zapis na rozmowę kwalifikacyjną, każda wersja każdej pracy (data oddania, status
skanu, liczba stron, pobranie), recenzje najnowszej wersji z punktami i terminem, ocena końcowa
z trybem ustalenia, reklamacje z rozstrzygnięciem, wiersz z **ogłoszonej** tabeli wyników (miejsce
i suma zamrożone w chwili publikacji, nie bieżące), wystawione dyplomy, zgłoszenia do organizatora
oraz pięćdziesiąt ostatnich wpisów audytu, w których ta osoba jest wykonawcą albo przedmiotem.

Karta jest **wyłącznie odczytem i nie ma ani jednego własnego adresu zapisu**: przydział
recenzenta, korekta punktów recenzji, „Cofnij”/„Odbierz”, blokada pracy do oceny, korekta oceny
końcowej, aktywacja konta, eksport danych i usunięcie konta celują w istniejące widoki-akcje
koordynatora — te same, które obsługują ekran przydziałów etapu i listę kont. Po zapisie akcja
wraca tam, gdzie wraca zawsze (ekran przydziałów etapu albo pulpit), bo baza akcji nie czyta
`?next=` ani nagłówka `Referer`. Dane składa `apps.accounts.participant_card` w stałej liczbie
zapytań — niezależnej od liczby prac, wersji i recenzji (pilnuje tego test równościowy).

Sekcja „Zgłoszenia” renderuje się tylko wtedy, gdy aplikacja `apps.support` jest zainstalowana;
brak modułu znaczy „nie ma takiej funkcji” i różni się od pustej listy, która znaczy „ta osoba
niczego nie zgłaszała”. Inne ekrany panelu dowiązują kartę znacznikiem
`{% participant_card_url participant %}` z `apps.web.templatetags.coordinator_extras` — dla wiersza
bez uczestnika zwraca pusty napis, więc odnośnik po prostu znika.

**Karta członka komisji** (`/coordinator/members/<id>/`) jest odpowiednikiem karty uczestnika po
drugiej stronie stołu: zbiera w jednym miejscu to, co dotąd trzeba było pozbierać z pięciu ekranów
(konta, przydziały, postęp, kalibracja, zgłoszenia), i dokłada czynności, które z tej odpowiedzi
wynikają. Sekcje: **Dane** (nazwisko, adres, aktywacja konta, status w komitecie z przyciskiem
zatwierdzenia, województwo z formularzem ustalenia lub usunięcia, flaga komisji odwoławczej, grupy
uprawnień), **Obciążenie** (przydzielone / szkice / wystawione / anulowane / po terminie w rozbiciu
na etapy, razem ze zmierzonym czasem pracy, gdy licznik coś zmierzył), **Recenzje** (każda recenzja
tej osoby: etap, zadanie, kod uczestnika, status, punkty, termin z odznaką zaległości, data
poprawki, „Zmień punkty” i „Odbierz”), **Przydziel pracę** (prace bieżącej edycji będące w ocenianiu,
których ta osoba jeszcze nie ma — po jednym przycisku na pracę, do trzydziestu na etap; komplet
i przydział hurtowy zostają na ekranie przydziałów etapu), **Reguły** (reguły „to zadanie recenzuje
ta osoba” z usunięciem oraz dodanie reguły dla zadania etapu jeszcze niezamkniętego),
**Kalibracja** (wiersz tej osoby z zestawienia najnowszego etapu, w którym wystawiła oceny),
**Zgłoszone problemy** (jej `WorkIssue`), **Przypomnij e-mailem** (przycisk stoi tylko przy etapach,
w których jest o czym przypominać) i **Historia** (pięćdziesiąt ostatnich wpisów audytu o tym koncie
i o tym profilu).

Lista wszystkich członków stoi pod `/coordinator/members/`: status, województwo z odznaką
„niepotwierdzone”, flaga komisji odwoławczej, obciążenie, ostatnia aktywność i odnośnik do karty.
Wiersz dostaje **każdy** profil, także bez ani jednej recenzji — zero przydziałów jest informacją
o rozkładzie pracy. Kolejność jest kolejnością pilności (najpierw zalegający, potem najbardziej
obciążeni), a filtry (`?status=`, `?stage=`) jadą w adresie; etap zawęża **liczniki**, a nie listę
osób. Zaproszeń tu nie ma — wysyłka kodów została na ekranie „Komitet”, żeby nie było dwóch miejsc
do tej samej czynności.

Karta jest **wyłącznie odczytem i nie ma ani jednego własnego adresu zapisu**: zatwierdzenie,
województwo, „Odbierz”, korekta punktów, przydział pracy, reguły i przypomnienie celują
w istniejące widoki-akcje koordynatora, więc reguły domenowe (konflikt interesów, skala punktacji,
ogłoszone wyniki) obowiązują identycznie jak na ekranie przydziałów etapu. Dane składa
`apps.accounts.member_card` z **jednego** pobrania recenzji tej osoby — obciążenie, czas pracy,
zaległości i tabela recenzji to cztery widoki na ten sam zbiór, a nie cztery pytania do bazy
(pilnuje tego test równościowy). Sekcje „Kalibracja”, „Zgłoszone problemy”, czas pracy
i przypomnienie są zależne od obecności modułów `apps.grading.calibration`, `WorkIssue`,
`apps.grading.worklog` i `apps.grading.reports`: brak modułu znaczy „nie ma takiej funkcji” i sekcja
po prostu nie stoi, zamiast wywracać stronę.

### 5.5 Panel uczestnika: układ, status pracy, informacja zwrotna, kalendarz, archiwum

#### Układ `/me/`: nagłówek „Co teraz” i zakładki

Panel odpowiada na kilkanaście pytań (etap, zadania, wyniki, reklamacje, zgody), ale **pytanie,
z którym się na niego wchodzi, jest jedno**: co mam teraz zrobić i ile mam na to czasu. Stąd
podział na dwie warstwy.

**Nagłówek „Co teraz”** stoi nad zakładkami i jest na każdej z nich. Niesie cztery rzeczy: nazwę
i stan bieżącego etapu, **jedną** czynność do zrobienia, najbliższy termin z odliczaniem oraz
znaczniki stanu konta (*konto aktywne*, *zgody kompletne*, *opiekun potwierdził*). Tabela decyzyjna
mieszka w `apps/web/participant_now.py` — funkcji **czystej**, testowanej bez stawiania edycji,
etapu i zgłoszenia (`apps/web/tests/test_participant_ux.py`).

Kolejność czynności (pierwsza pasująca wygrywa) i jej uzasadnienie:

| # | Czynność | Kiedy | Dlaczego tutaj |
|---|---|---|---|
| 1 | podaj adres opiekuna | `guardian_state == missing` | jedyna rzecz, której uczestnik nie załatwi sam — po drugiej stronie jest dorosły czytający pocztę raz na kilka dni |
| 2 | zgłoś się do etapu | zapisy otwarte, brak wpisu | bez wpisu nie ma ani zadań, ani uploadu |
| 3 | wyślij rozwiązanie zadania *N* | upload otwarty, zadanie bez wersji | czynność, dla której ten panel istnieje |
| 4 | zapisz się na rozmowę | etap-rozmowa, brak zapisu | to samo, tylko w innej formie etapu |
| 5 | sprawdź ocenę (reklamacja) | okno odwoławcze otwarte | też się zamyka, ale dotyczy rzeczy już zrobionej |
| 6 | sprawdź wyniki | jakikolwiek etap ogłoszony | do przeczytania, nie do zrobienia |

Brak czynności jest **normalnym** stanem przez większą część roku i panel mówi to wprost („Na teraz
nic nie musisz robić”) — pusty nagłówek czytałby się jak awaria.

**Odliczanie** („za 3 dni, 14 godz.”) liczy i renderuje **serwer**, z czasu serwera, więc strona
niesie prawdziwą liczbę także bez JavaScriptu. `static/js/participant.js` odświeża ją co minutę,
korygując o różnicę zegara przeglądarki (`data-server-now`). Słowa („dni”, „godz.”, „termin minął”)
jadą do skryptu w atrybutach `data-word-*`: przeglądarka nie ma katalogu tłumaczeń, więc inaczej
angielski interfejs po minucie pisałby po polsku. Skład tekstu jest ten sam po obu stronach
(`participant_now.remaining_text` i `remainingText` w skrypcie).

**Zakładki** są rozstrzygane po stronie serwera i są zwykłymi odnośnikami z `aria-current`:

| Adres | Zawartość |
|---|---|
| `/me/` | **Zadania** (domyślna): karta etapu, zapis, karty zadań z uploadem albo wybór terminu rozmowy, trening |
| `/me/?tab=wyniki` | punkty, kwalifikacja i komentarze recenzentów po publikacji |
| `/me/?tab=reklamacje` | formularz przy pracy podlegającej reklamacji i lista własnych zgłoszeń |
| `/me/?tab=zgody` | zgoda opiekuna, historia zgód i przełącznik publikacji nazwiska |

Obok nich w tym samym pasku stoją ekrany, które mają własne adresy: `Kalendarz`, `Archiwum`,
`Dyplomy`, `Profil`.

- **widok liczy wyłącznie to, co renderuje.** Zakładka „Zadania” nie dotyka ani wyników, ani
  kolejki reklamacji; pilnuje tego test porównujący koszt żądania przed ogłoszeniem wyników i po
  nim (`test_tasks_tab_does_not_pay_for_results_and_appeals`),
- **zakładka ma adres**, więc da się ją otworzyć w nowej karcie, wysłać komuś i wrócić do niej
  przyciskiem „wstecz”; przełącznik w JavaScripcie i tak musiałby pójść po treść na serwer,
- nieznana wartość `?tab=` otwiera zakładkę domyślną, a nie 404: parametr bywa uszkodzony przez
  skrócenie linku albo autokorektę w komunikatorze,
- role ARIA `tab`/`tablist` **nie** są użyte świadomie — opisują panele przełączane bez
  przeładowania i obiecują obsługę strzałkami, której zwykłe odnośniki nie dają. To jest nawigacja
  po stronach i tak się przedstawia (`<nav>` + `aria-current="page"`),
- **karta zadania** ma hierarchię odpowiadającą kolejności pytań: tytuł i odznaka stanu → termin,
  formaty i limit → ścieżka oceniania → formularz wysyłki (ostrzeżenie „praca jest już w ocenie”
  stoi **w formularzu**, nad polem pliku, a błąd pola — przy tym polu) → ostatnia wersja ze
  skanem antywirusowym → zwinięta w `<details>` historia wysyłek (dopiero od drugiej wersji),
- **plik można przeciągnąć na kartę.** Ramka formularza wysyłki jest strefą upuszczania
  (`static/js/upload-dropzone.js`): plik upuszczony gdziekolwiek w jej obrębie ląduje w polu
  wyboru, a pod polem pojawia się zdanie „Wybrano: *nazwa* (*rozmiar*)” albo odmowa —
  „niedozwolony format” lub „plik za duży” — sprawdzona jeszcze przed wysłaniem bajtów pod górę.
  To **dodatek, nie zamiennik**: natywne `input[type=file]` zostaje widoczne i dostępne
  z klawiatury, skrypt nic nie wysyła (potwierdzenie „to rozwiązanie zadania *N*” dalej zaznacza
  człowiek), a formaty i limit przychodzą z `Problem.allowed_formats` i `Problem.max_file_mb`
  w `data-*` — skrypt nie zna żadnej z tych wartości. Ostatnie słowo ma serwer
  (`apps/submissions/validators.py` patrzy na treść pliku, nie na rozszerzenie). Bez JavaScriptu
  i w przeglądarce bez `DataTransfer` karta działa jak działała, bo upuszczenie pliku wprost na
  pole obsługuje sama przeglądarka,
- **wybór terminu rozmowy** jest listą pól wyboru pogrupowaną po dniach z jednym przyciskiem
  (`POST /me/interview/choose/`, termin w polu `slot_id`). Adres z identyfikatorem w ścieżce
  (`/me/interview-slots/<id>/book/`) zostaje nietknięty — jest w API i w linkach z listów.

#### Pięć rzeczy poza uploadem

Pięć rzeczy, które uczestnik dostaje poza samym uploadem. Wszystkie są **tylko do odczytu** (poza
powiadomieniami, które nic nie wyświetlają) i wszystkie stoją na regułach opisanych wyżej — żaden
z tych ekranów nie rozluźnia widoczności ani o krok.

**1. Status oceniania pracy** — karta zadania w `/me/` niesie ścieżkę czterech kroków:
`oddane → w ocenie → oceniona → wyniki`. Krok bierze się ze stanu ostatniej wersji pracy
(`SUBMITTED`/`SCANNING` → oddane, `LOCKED`/`IN_REVIEW`/`MODERATION` → w ocenie,
`GRADED_PROVISIONAL`/`APPEALED`/`FINAL` → oceniona), a ostatni zapala dopiero **publikacja wyników
etapu**. Reguła mieszka w `apps/submissions/status_track.py` i nigdzie się nie powtarza.

- **ścieżka nigdy nie pokazuje punktów.** Liczba należy do uczestnika dopiero po ogłoszeniu
  wyników i idzie osobną drogą (niżej); gdyby stała w karcie, każdy ekran renderujący kartę
  musiałby powtarzać bramę publikacji,
- `APPEALED` **nie cofa** pracy o krok: reklamację składa się na wystawioną ocenę,
- wersja odrzucona przez antywirusa kończy na pierwszym kroku, z jawnym „wyślij plik ponownie”,
- pod ścieżką stoi **„Ogłoszenie wyników: …”**. Po publikacji jest to jej data; przed publikacją —
  otwarcie okna reklamacji, podpisane jako *termin planowany*. `Stage` nie ma osobnego pola
  „planowane wyniki” (`results_published_at` nakłada dopiero publikacja), a okno reklamacji jest
  pierwszym momentem osi czasu, w którym uczestnik zna już ocenę. Etap treningowy nie dostaje
  żadnej daty — w bazie stoi tam wartownik z roku 2099.

**2. Komentarze i miejsce po publikacji** — `GET /me/stages/<id>/feedback/`, z odnośnikami z `/me/`
(sekcja „Moje wyniki”) i z ogłoszonej tabeli `/results/<id>/` (widoczny wyłącznie dla zalogowanego
uczestnika). Strona pokazuje punkty za każde zadanie, komentarze recenzentów, miejsce w tabeli oraz
próg kwalifikacji i decyzję.

- **przed publikacją adres zwraca 404**, nie 403 i nie pustą stronę: odpowiedź nie może
  potwierdzać, że wynik jest już policzony i czeka na ogłoszenie. To samo 404 dostaje ktoś, kto
  w tym etapie nie brał udziału,
- **recenzenci są anonimowi**: wychodzi `Review.comment_for_participant` i adnotacje z
  `public=True`, podpisane „Recenzent A/B” w kolejności wystąpienia. `comment_internal` nie
  opuszcza `apps/results/feedback.py` w żadnej postaci, tożsamość recenzenta również,
- **miejsce pochodzi z zamrożonego snapshotu**, a nie z przeliczenia na żywo — to samo miejsce,
  które widzi publiczność. Wiersze snapshotu są anonimowe, więc dowiązanie idzie przez
  `ResultsPublication.entry_totals` (mapa `{wpis: suma}` z chwili publikacji), a miejsce odczytuje
  się z wiersza o tej samej sumie (remisy mają ten sam `rank`),
- **punkty liczymy na żywo** z `FinalGrade`. Gdy komisja zmieniła ocenę po publikacji, strona
  pokazuje obie liczby i mówi wprost, która obowiązuje.

**3. Powiadomienia e-mail** (`apps/submissions/notifications.py`) — cztery listy, wszystkie przez
`apps.core.tasks.send_mail_task` kolejkowane **po commicie**, więc wycofana operacja nie wysyła
nic:

| Rodzaj (audyt `notification.sent`) | Kiedy | Co niesie |
|---|---|---|
| `submission.received` | po przyjęciu pliku (`create_submission`) | numer zadania, wersja, sha256, czas |
| `submission.infected` | gdy skan odrzuci plik | co odrzucono i „wyślij ponownie” (bez sygnatury wirusa) |
| `results.published` | `publish_results`, do każdego uczestnika etapu | odnośnik do tabeli i do własnej informacji zwrotnej |
| `appeal.decided` | `decide_appeal` | rozstrzygnięcie i uzasadnienie komisji |

- **czysty skan jest cichy.** Gdyby szedł po nim list, każda wysyłka dawałaby dwie wiadomości,
  a pierwsza przestałaby cokolwiek znaczyć,
- **w liście nie ma punktów.** Skrzynka pocztowa nie jest kanałem zabezpieczonym, a wiadomość
  zostaje w niej na lata — wynik jest w serwisie,
- **konto nieaktywne nie dostaje poczty** (`User.is_active`): nieaktywowana rejestracja i konto
  zablokowane przez koordynatora nie są adresem, pod którym ktoś czeka,
- w audycie zostaje **sam rodzaj** powiadomienia. Ogłoszenie wyników zostawia jeden wpis na całą
  publikację, a nie wpis na uczestnika — tysiąc wierszy różniących się adresatem wskazywałoby
  tysiąc konkretnych osób.

**4. Kalendarz osobisty** — `GET /me/calendar/` oraz `GET /me/calendar.ics`. Terminy pochodzą
z `apps.cms.timeline.timeline_events`, czyli z tego samego złożenia, na którym stoi pasek linii
czasu w nagłówku (etapy, wydarzenia koordynatora, okno rejestracji, warsztaty); kalendarz dokłada
do nich dokładnie jedną pozycję prywatną — **własny termin rozmowy kwalifikacyjnej**.

- **adresu pokoju wideo w pliku nie ma.** Link, pod którym wchodzi się bez logowania, jest de
  facto poświadczeniem, a `.ics` bywa synchronizowany do cudzych usług; link jest wyłącznie
  w `/me/`,
- plik `.ics` powstaje ręcznie (`apps/cms/calendar.py`, bez nowej zależności) i trzyma się
  RFC 5545: `CRLF`, zawijanie wierszy do 75 **oktetów** liczonych w UTF-8, cytowanie `\`, `;`, `,`
  i złamań wiersza, `PRODID`, wydarzenia całodniowe jako `VALUE=DATE` z **wyłącznym** `DTEND`,
  rozmowa w UTC z sufiksem `Z`,
- **`UID` jest stabilny między pobraniami** (skrót rodzaju, tytułu i dat), więc subskrypcja
  aktualizuje wpisy zamiast dokładać ich kopie. Klucza głównego użyć się nie da: warsztaty
  pochodzą z treści redakcyjnej, a okno rejestracji jest polem edycji,
- odpowiedź ma `Content-Type: text/calendar` i `Content-Disposition: attachment`.

**5. Materiały i zadania archiwalne** — `GET /me/archive/`: zadania **zakończonych edycji**
z treścią PDF, arkusz treningowy oraz harmonogram warsztatów.

- archiwum pokazuje wyłącznie etapy, które **już się otwarły** — tę samą bramę (`opens_at`)
  egzekwuje widok pliku `competitions:problem-statement`, a odnośnik do 404 też jest usterką,
- bieżąca edycja archiwum **nie jest**: zadania trwających zawodów mieszkają w `/me/`, razem
  z uploadem i terminem,
- zadania treningowe mają przycisk „Oddaj rozwiązanie na sucho”, który prowadzi na kartę
  treningową pulpitu (`/me/#trening`). Archiwum nie powiela formularza wysyłki — drugie miejsce
  z uploadem byłoby drugim miejscem, w którym trzeba pilnować skanu i terminu,
- warsztaty są **treścią redakcyjną** (blok `schedule` na `/warsztaty/`) i model nie ma w nich
  pola na załącznik, więc archiwum wypisuje terminy i odsyła na tamtą stronę, zamiast udawać
  listę plików. Gdy warsztat dostanie kiedyś własny załącznik, zmienia się jedna funkcja
  (`workshop_materials` w `apps/web/views/participant_tools.py`).

### 5.6 Panel opiekuna szkolnego (`/supervisor/`)

> **Rola jest domyślnie ukryta.** Decyduje o tym jeden przełącznik w `/cms/` → *Ustawienia →
> Dane serwisu → Rejestracja* → **„rejestracja opiekunów szkolnych”**
> (`cms.SiteSettings.supervisor_registration_enabled`, domyślnie **wyłączony**). Przy wyłączonym:
> `/register/supervisor/` zwraca **404**, nigdzie nie ma do niego odnośnika, a uczestnik nie widzi
> w profilu pola „adres e-mail opiekuna szkolnego” — bez nauczycieli z kontem byłoby to pytanie
> o adres, którego nikt nie użyje. Konto opiekuna zakłada się wtedy **na prośbę**: organizator
> włącza przełącznik na czas zapisów danej szkoły albo zakłada konto sam. FAQ wspomina o roli jako
> dostępnej na życzenie; treść wpisu redaguje organizator w `/cms/`, nie repozytorium.
>
> Czego przełącznik **nie** robi: nie odbiera panelu opiekunom, którzy konto już mają. Te konta
> powstały świadomie, a ukrycie drogi wejścia nie jest tym samym, co odebranie komuś dostępu do
> danych, które już ogląda. Adres opiekuna raz zapisany w profilu ucznia też zostaje — decyzji
> ucznia sprzed wyłączenia przełącznika formularz danych nie cofa bez jego wiedzy. Hurtowy import
> uczniów po stronie koordynatora (5.6a) działa niezależnie od przełącznika: to narzędzie
> organizatora, a nie część roli nauczyciela.
>
> Reszta tej sekcji opisuje stan **z włączonym** przełącznikiem.

Nauczyciel, który prowadzi uczniów do olimpiady, zakłada konto pod `/register/supervisor/` —
rejestracja jest **otwarta**, bez kodu zaproszenia, z tym samym blokiem antyspamowym, limitem
żądań i aktywacją adresu, co pozostałe. Samo konto nie daje wglądu w niczyje dane: pusty panel
zobaczy każdy, kogo żaden uczeń nie wskazał.

**Uprawnienie pochodzi od ucznia.** Uczestnik wpisuje adres opiekuna w swoim profilu
(`/me/profile/` → „Adres e-mail opiekuna szkolnego”, pole opcjonalne) i w każdej chwili może je
wyczyścić. Nie ma tu ani zatwierdzania przez koordynatora, ani przypisywania po nazwie szkoły —
oba wyglądają porządniej, ale oba znaczyłyby, że nauczyciel dostaje dostęp do danych ucznia bez
jego udziału. Dopasowanie idzie po **znormalizowanym** adresie (małe litery, bez spacji), bo
uczeń przepisuje go ze słuchu albo z tablicy.

Panel pokazuje: kod publiczny, imię i nazwisko, szkołę i klasę ucznia oraz — dla każdego etapu
bieżącej edycji — **ścieżkę statusu** „oddane → w ocenie → oceniona → wyniki”
(`apps/submissions/status_track.py`). Wiersz etapu podpisuje ta praca, która jest najdalej w tyle:
jedna w ocenie i dwie ocenione znaczą „w ocenie”.

Czego opiekun **nie** widzi: punktów przed ogłoszeniem wyników etapu (po publikacji jest to ta
sama liczba, którą każdy widzi w tabeli publicznej), prac, komentarzy recenzentów ani niczego
o uczniach, którzy go nie wskazali. Reguła stoi w serwisie `apps/accounts/supervisors.py`, nie
w szablonie.

**„Potwierdzam udział szkoły”** jest oświadczeniem o nauczycielu, nie o uczniach: niczyjego startu
nie warunkuje, ale to na jego podstawie organizator wystawia zaświadczenia dla opiekunów (6.12).
Jedno kliknięcie ustawia je dla bieżącej edycji, drugie wycofuje; oba są w audycie
(`school.participation_confirmed` / `…_withdrawn`). Własne zaświadczenie opiekun pobiera na dole
panelu.

Koordynator widzi opiekunów na liście kont (`/coordinator/accounts/?role=supervisor`) i w tabeli
wystawiania dokumentów.

### 5.6a Import uczniów: zaproszenie całej klasy z pliku (`/supervisor/import/`)

Zgłoszenie ze szkół brzmiało prosto: „mam dwudziestu uczniów, nie będę dwudziestu razy tłumaczyć,
jak się zarejestrować”. Naturalna odpowiedź — „nauczyciel zakłada konta i rozdaje hasła” — jest
jednak **wykluczona**, i to nie ze względów wygody:

- **zgody są oświadczeniem ucznia** (regulamin, RODO, a dla niepełnoletnich zgoda opiekuna).
  Nauczyciel nie może ich złożyć w cudzym imieniu, więc konto „pod klucz” byłoby kontem bez ani
  jednej ważnej zgody,
- **hasło rozdane przez osobę trzecią nie jest poświadczeniem.** Uczeń, którego hasło zna
  nauczyciel, nie ma konta — ma konto współdzielone.

Dlatego import dzieli się na dwie czynności wykonane przez **dwie różne osoby**.

**1. Nauczyciel wgrywa listę.** Plik CSV albo XLSX, pierwszy wiersz to nagłówki, maksymalnie **500
wierszy**. Kolejność kolumn jest dowolna — liczy się nazwa w nagłówku, rozpoznawana bez względu na
wielkość liter i diakrytyki („IMIĘ”, „imie”, „Rok urodzenia” obok „rocznik”):

```
imię;nazwisko;e-mail;rok urodzenia;klasa;telefon;e-mail opiekuna prawnego
```

Telefon i adres opiekuna prawnego są nieobowiązkowe. Szkoły w pliku **nie ma i nie będzie**: wchodzi
szkoła z profilu opiekuna albo wybrana raz dla całego pliku tą samą wyszukiwarką SIO, co
w rejestracji. Powód nie jest oszczędnością klików — nazwa szkoły wchodzi do grupowania
w publikowanych wynikach (próg k-anonimowości), więc dwadzieścia razy wpisana ręcznie dałaby
dwadzieścia wariantów tej samej placówki.

**Podgląd przed zapisem jest obowiązkowym krokiem**, a nie ozdobą: plik z arkusza szkolnego zawiera
literówki w adresach, stopki i uczniów już zarejestrowanych, a listu nie da się cofnąć. Każdy wiersz
dostaje jedno z trzech rozstrzygnięć:

| Podgląd mówi | Znaczy |
|---|---|
| **zaproszenie** | powstanie konto w stanie „zaproszony” i pójdzie list z linkiem |
| **dopisanie do listy** | adres należy już do konta uczestnika — dopisujemy tylko adres opiekuna, bez zakładania drugiego konta i bez ruszania zgód |
| **pominięty** | zły adres, brak imienia lub nazwiska, klasa spoza 1–5, adres powtórzony w pliku albo adres konta, które nie jest kontem uczestnika |

Adresy porównujemy **bez względu na wielkość liter**, a niepoprawny numer telefonu nie odrzuca
wiersza (zostaje pusty z notką). Uczeń niepełnoletni bez adresu opiekuna prawnego jest oznaczony,
ale **nie** odrzucony — zgodę opiekuna zbieramy od niego po przyjęciu zaproszenia (5.8).

**2. Uczeń przyjmuje zaproszenie.** List prowadzi pod podpisany link `/zaproszenie/<token>/`, ważny
**14 dni**. Konto założone importem jest do tego czasu nieaktywne i **nie ma używalnego hasła** —
nie zaloguje się ani hasłem, ani przez dostawcę OAuth. Pod linkiem uczeń ustawia własne hasło, podaje
województwo i telefon (dwie dane, których nie ma w liście klasowej) i **sam** zaznacza zgody —
dokładnie ten sam blok oświadczeń, co w rejestracji. Dopiero to aktywuje konto.

Konta z importu **nie podlegają kosiarce kont nieaktywowanych** (5.1): tamta kasuje po czterech
godzinach porzucone rejestracje, a zaproszenie żyje dwa tygodnie i jest wystawione świadomie.

**Nauczyciel widzi stan każdego ucznia** w `/supervisor/students/` — „zaproszony”, „aktywny” albo
„zapisał się sam” — i przy tych pierwszych ma przycisk **wyślij zaproszenie ponownie** (nowy link,
ten sam adres). Przycisk działa tylko dla uczniów, którzy mają w profilu jego adres.

**Koordynator ma ten sam import** pod `/coordinator/accounts/import/` (mały odnośnik nad listą
kont), z jedną dodatkową kolumną `e-mail opiekuna szkolnego` — dzięki niej jedno wgranie rozdziela
uczniów między kilku nauczycieli. Na liście kont konta z importu mają odznakę **„z importu”**
(zostaje także po aktywacji: opisuje pochodzenie konta, a nie jego stan), a w audycie powstają dwa
rodzaje wpisów — `accounts.students_imported` z samymi liczbami przebiegu i
`participant.invited_by_import` przy każdym koncie. **W żadnym z nich nie ma adresu ani nazwiska**:
kogo dotyczą, mówi identyfikator celu.

### 5.7 Lista kontrolna i podgląd wysłanego pliku

Dwie rzeczy na karcie zadania w `/me/`, obie odpowiadające na to samo pytanie z dwóch stron:
**przed** kliknięciem „czy wysyłam to, co trzeba”, a **po** — „czy w systemie leży to, co
wysłałem”.

**Potwierdzenie przed wysyłką.** Nad przyciskiem stoi jedno wymagane pole wyboru: *„Potwierdzam,
że to rozwiązanie zadania N i plik jest czytelny”*. Numer zadania jest w treści celowo — najczęstsza
pomyłka to plik wysłany pod zadanie obok, a druga to nieczytelny skan.

- bramką jest **serwer** (`SubmissionUploadForm.confirmed`), nie atrybut `required` w HTML: ten
  sam formularz da się wysłać bez przeglądarki. Bez pola karta wraca z komunikatem i nie powstaje
  żadna wersja,
- `POST /api/submissions/` (API) tego pola **nie ma**: tam po drugiej stronie jest klient
  programistyczny, a nie człowiek stojący nad telefonem.

**Podgląd ostatniej wersji.** Pod formularzem, nad historią wersji, i wyłącznie dla wersji
najnowszej — to ona pójdzie do oceny.

| Format | Co widać | Kto to liczy |
|---|---|---|
| PDF | liczba stron + pierwsza strona narysowana w przeglądarce | strony: `pypdf` po stronie serwera; obraz: pdf.js (`static/js/upload-preview.js`) |
| JPEG | wymiary w pikselach + samo zdjęcie | Pillow (ten sam, którego używa Wagtail) |
| `.py`, `.ipynb` | pierwsze 40 wierszy pliku | serwer (`apps/submissions/preview.py`) |

- **plik czytamy dopiero po czystym skanie antywirusowym.** Plik świeżo wgrany jest danymi od
  nieznanego nadawcy; parsowanie go tą samą biblioteką, którą potem zobaczy recenzent, byłoby
  wykonaniem roboty za napastnika. Do czasu werdyktu karta pisze „Podgląd pojawi się po
  zakończeniu skanu”, a plik odrzucony nie dostaje podglądu w ogóle,
- **liczba stron jest kolumną w bazie** (`SubmissionFile.page_count`, migracja
  `submissions.0004`). To jedyna metryka wymagająca przeczytania **całego** dokumentu (tablica
  stron leży na jego końcu), a czyta ją panel przy każdym wejściu — liczenie jej w locie znaczyłoby
  pobranie pliku z S3 na każde odświeżenie. Wypełnia ją `apply_scan_verdict` zaraz po werdykcie
  `CLEAN`; `NULL` znaczy „nie wiadomo” (inny format, plik sprzed wprowadzenia pola, dokument,
  którego biblioteka nie umiała otworzyć),
- **reszta metryk jedzie przez `django.core.cache`** pod kluczem z sumy `sha256` pliku. Suma jest
  naturalnym kluczem: zmiana treści to nowy plik i nowy klucz, więc wpis nie ma jak skłamać,
- **pierwszej strony PDF-a nie renderuje serwer.** Rysuje ją pdf.js w przeglądarce uczestnika —
  to jego własny plik, więc nie ma powodu, żeby serwer składał z niego obrazek i trzymał go
  w kolejnym buckecie. Biblioteka jedzie z pinowanej wersji na cdnjs, tą samą drogą co w panelu
  recenzenta (moduł z `nonce` + `import()`, `'strict-dynamic'` w CSP),
- **podgląd nigdy nie jest warunkiem niczego**: brak CDN-u, uszkodzony nagłówek albo niedostępny
  storage kończą się zdaniem w miejscu obrazka, a praca i tak jest już przyjęta.

Nowa zależność: `pypdf>=4,<6` w zwykłych zależnościach (`backend/pyproject.toml`) — liczbę stron
pokazuje panel, czyli produkcja. Po jej dodaniu: `docker compose build web`.

### 5.8 Zgoda opiekuna online (`/zgoda/<token>/`)

Zgoda rodzica albo opiekuna prawnego przestaje być **oświadczeniem dziecka o cudzej woli**
(pole wyboru w rejestracji) i wydrukiem do podpisania. Normalną drogą jest teraz podpisany link:

1. uczestnik niepełnoletni podaje w `/me/` (sekcja „Twoje zgody”) adres e-mail opiekuna —
   `Participant.guardian_email`,
2. system wysyła **na ten adres** list z linkiem podpisanym `django.core.signing`, ważnym
   **14 dni**,
3. opiekun otwiera `/zgoda/<token>/` — **bez logowania** — czyta treść zgody w obowiązującej
   wersji (`apps/accounts/consents.py`, ta sama, którą zapisuje dowód), widzi **imię dziecka
   i szkołę**, zaznacza pole i potwierdza,
4. powstaje `ConsentRecord` rodzaju `GUARDIAN` z `given_by_email`, `ip_address` i znacznikiem
   czasu; `Participant.guardian_consent` idzie na `True`,
5. uczestnik dostaje list, że zgoda wpłynęła.

- **dlaczego token, a nie konto dla opiekuna**: opiekun ma w systemie jedną sprawę, a zakładanie
  mu konta (z hasłem, aktywacją i prawem do usunięcia danych) byłoby zebraniem większego zbioru
  danych niż ten, po który przyszedł,
- **zmiana adresu unieważnia poprzedni link** — adres jest częścią podpisanej treści. To jedyna
  droga „odwołania” wysłanej prośby,
- **druga wizyta pod tym samym linkiem nic nie dokłada**: przy istniejącej aktywnej zgodzie serwis
  zwraca ten sam wpis. Rejestr zgód jest rejestrem zdarzeń, ale „kliknąłem dwa razy” zdarzeniem
  nie jest,
- **uczestnik nie może być własnym opiekunem** (`400 GUARDIAN_EMAIL_IS_OWN`), a od pełnoletniego
  zgody nie zbieramy w ogóle (`409 GUARDIAN_NOT_REQUIRED`; granicę wyznacza `consents.is_minor`,
  czyli sam rocznik),
- **w liście do opiekuna nie ma nazwiska** — imię i nazwa szkoły w zupełności wystarczają do
  rozpoznania własnego dziecka, a list bywa wysłany pod adres z literówką,
- w audycie zostają dwa wpisy: `participant.guardian_consent_requested` (bez adresu opiekuna —
  audyt czytają osoby, które nie muszą znać danych kontaktowych rodziny) i
  `participant.guardian_consent_confirmed`. Ten drugi ma aktora `None`: potwierdzenie składa osoba
  spoza systemu i podpisanie go kontem uczestnika byłoby nieprawdą.

Panel uczestnika pokazuje stan **brak / oczekuje / potwierdzona `<data>`** wraz z przyciskiem
„Wyślij ponownie” (ten sam adres, `POST /me/guardian/` — powtórna wysyłka jest zamierzona, bo listy
giną w spamie). Koordynator widzi ten sam stan, **tylko do odczytu**, na ekranie edycji konta
(`/coordinator/accounts/<id>/`): dowodem jest potwierdzenie z podpisanego linku, a nie kliknięcie
w panelu organizatora.

Migracja: `accounts.0015_guardian_consent_online` (`Participant.guardian_email`,
`ConsentRecord.given_by_email`, `ConsentRecord.ip_address`).

### 5.9 Język interfejsu i tryb wysokiego kontrastu

W pasku konta — na każdej stronie, także dla gościa — stoją dwa przełączniki: **język** i **wysoki
kontrast** (`backend/templates/web/_interface_prefs.html`). Oba są formularzami `POST`
na `/account/preferences/`; adres powrotu (`next`) jest sprawdzany po stronie serwera, bo
przełącznik z otwartym przekierowaniem byłby gotowym narzędziem do phishingu.

Oba są **ikonami bez napisu** — tak poprosił organizator: flaga języka, na który przełącza
przycisk (Union Jack albo biało-czerwona), i kółko wypełnione w połowie. SVG jest wpisany
w szablon (żadnych atrybutów `style` — CSP nie ma `'unsafe-inline'` dla stylów), kolory flag są
ich własne, a obrys i ikona kontrastu idą `currentColor`, więc tryb wysokiego kontrastu obsługuje
się sam. Rysunek bez tekstu nie ma nazwy, więc każdy przycisk dostaje ją osobno: język —
ukrytym tekstem w **języku docelowym** (`LANGUAGE_SWITCH_LABELS` w `apps/accounts/preferences.py`;
czyta go ktoś, kto nie czyta bieżącego języka strony, więc ta etykieta nie przechodzi przez
gettext), kontrast — `aria-label` opisującym skutek kliknięcia, przy stanie w `aria-pressed`.

Gdzie mieszka wybór (`apps/accounts/preferences.py`):

| Kto | Gdzie zapisujemy | Dlaczego |
|---|---|---|
| konto zalogowane | `accounts.UserPreference` (język + kontrast) | ustawienie jedzie za człowiekiem na drugie urządzenie |
| gość | ciasteczko języka + sesja (kontrast) | konta nie ma, a prawo do dużego kontrastu owszem |
| nikt nic nie ustawił | nagłówek `Accept-Language` | to ustawienie systemu, a nie zgadywanka serwera |

- **kolejność warstw pośrednich jest kontraktem**: `LocaleMiddleware` (za sesją, przed
  `CommonMiddleware`) rozstrzyga język z ciasteczka i nagłówka, a `PreferencesMiddleware` stoi
  **za** `AuthenticationMiddleware` i tylko nadpisuje to rozstrzygnięcie zapisem z konta. Odwrotna
  kolejność znaczyłaby, że wybór zalogowanego przegrywa z ustawieniem przeglądarki,
- **`i18n_patterns` świadomie nie ma.** Adresy trafiają do listów, do regulaminu i do pism
  (`/me/`, `/results/12/`, `/zgoda/<token>/`); prefiks języka zrobiłby z każdego z nich dwa adresy,
  z których jeden zawsze byłby wklejony nie tam, gdzie trzeba,
- **zakres tłumaczenia jest wąski z wyboru**: panel uczestnika, logowanie i rejestracja, ekrany
  konta, pasek konta i stopka, strona statystyk oraz listy do uczestników. Ekrany koordynatora
  zostają po polsku — to narzędzie pracy polskiego organizatora. Treść redakcyjna w `/cms/` ma
  własną drogę: redaktor pisze ją w edytorze, a nie w pliku `.po`,
- **listy składane poza żądaniem odbiorcy** (ogłoszenie wyników, nocne przypomnienie o rozmowie)
  idą przez `preferences.language_for(user)` — inaczej byłyby w języku koordynatora albo serwera.

**Treść zadań to nie interfejs.** `Problem.title_en` i `Problem.statement_pdf_en` wypełnia komitet
w formularzu zadania (`/coordinator/problems/<id>/edit/`); angielski interfejs pokazuje je, gdy są,
a w przeciwnym razie wydaje wersję polską — zadanie bez tłumaczenia ma być czytelne, a nie puste.
Bramka czasowa treści (`opens_at`) nie zmienia się ani na jotę: to ten sam dokument w drugim języku.

**Tryb wysokiego kontrastu** dokłada `data-contrast="high"` na `<html>`, a arkusz (`app.css`)
nadpisuje **tokeny**, nie komponenty — dzięki temu obejmuje też arkusze dokładane per ekran.
Paleta to czerń, biel i żółć (trzy kolory, nie dwadzieścia: każdy następny to kolejna para do
sprawdzenia, a żółć na czerni jest jedynym ostrzeżeniem czytelnym także przy deuteranopii),
obramowania idą na 2 px, cienie znikają, a fokus dostaje gruby żółty pierścień. To **nie** jest
drugi wariant ciemny: ciemny motyw odpowiada na pytanie „jakie mam światło w pokoju”, a ten — „czy
w ogóle widzę tę krawędź”.

**Katalogi tłumaczeń.** Źródło jest w `backend/locale/en/LC_MESSAGES/django.po` (w repozytorium,
bo tylko ono daje się czytać w diffie), a Django czyta wyłącznie skompilowane `.mo`. Kompiluje je
**budowanie obrazu** (`msgfmt` w `backend/Dockerfile`, pakiet `gettext` w warstwie apt), więc plik
binarny nie może rozjechać się ze źródłem. Po zmianie napisów:

```bash
docker compose exec web python manage.py makemessages -l en -i 'staticfiles/*' -i '.venv/*'
# uzupełnij msgstr w backend/locale/en/LC_MESSAGES/django.po, potem:
docker compose exec web python manage.py compilemessages
docker compose build web   # obraz produkcyjny kompiluje katalog sam
```

Migracja: `accounts.0016_user_interface_preferences` (`UserPreference`).

### 5.10 Panel koordynatora: układ, menu boczne i wyszukiwarka

Każdy ekran pod `/coordinator/` stoi w tej samej ramie (`templates/web/coordinator/base.html`):
menu boczne po lewej, nagłówek z jednym zdaniem „po co jest ta strona” i akcją główną, treść pod
spodem. Poniżej 900 px menu zwija się do rozwijanego **„Menu panelu”** na górze strony — całość
jest elementem `<details>` i nie ma w niej ani jednej linii JavaScriptu (polityka CSP nie
dopuszcza skryptów inline).

Menu opisuje `apps/web/coordinator_nav.py`, a nie szablon. Adres każdej pozycji jest **listą
kandydatów** rozwiązywaną przez `reverse()`: pozycja, której ekranu nie ma jeszcze w urlconfie,
po prostu znika z menu, zamiast wywracać wszystkie strony panelu. Pozycja aktywna wynika z nazwy
widoku (`request.resolver_match.url_name`), więc „Konta” świeci się także na ekranie edycji
jednego konta.

| Sekcja menu | Co w niej jest |
|---|---|
| Pulpit | „Co wymaga uwagi” — kafelki spraw czekających na decyzję |
| Etapy | jeden wpis na etap bieżącej edycji, a pod nim: zadania (albo rozmowy), przydziały i oceny, postęp, wyniki |
| Ocenianie | moderacja, zgłoszone problemy, kalibracja i podobieństwo (dla etapu, w którym trwa praca) |
| Uczestnicy i konta | uczestnicy, wszystkie konta, opiekunowie szkolni, aktywacje |
| Komitet | członkowie, zatwierdzenia, zaproszenia, województwa |
| Komunikacja | komunikaty, zgłoszenia, ogłoszenia |
| Raporty | eksport, audyt, symulacja, dyplomy, retencja, rejestr czynności |
| Ustawienia | rejestracja uczestników, wydarzenia linii czasu, skala punktacji |

Przy czterech pozycjach stoją **liczniki** (moderacja, aktywacje, komitet, zgłoszenia). Każdy to
jedno zapytanie agregujące, wszystkie razem trzymane w pamięci podręcznej przez 60 s i wspólne dla
menu oraz kafelków pulpitu — panel nie może pokazywać dwóch różnych odpowiedzi na to samo pytanie.
Zero nie rysuje badge'a: kropka przy pozycji, pod którą nic nie czeka, uczyłaby ignorować kropki.

**Wyszukiwarka** — pole na górze menu, wyniki pod `/coordinator/search/?q=`. Jedno pole na pięć
rodzajów obiektów, pogrupowane w wyniku: uczestnicy (kod publiczny, nazwisko, imię, e-mail,
szkoła), członkowie komisji, zadania (tytuł albo numer), etapy bieżącej edycji i zgłoszenia po
numerze. Po 20 trafień na grupę, jedno zapytanie na grupę, fraza krótsza niż dwa znaki nie szuka
niczego. Odnośnik prowadzi na kartę obiektu, a gdy karty nie ma — na ekran edycji.

**Pulpit** (`/coordinator/`) odpowiada na jedno pytanie: czym trzeba się teraz zająć. Kolejki
i formularze, które dotąd stały na nim jedna pod drugą, mają własne adresy:

| Ekran | Adres |
|---|---|
| Moderacja (rozjazdy ocen) | `/coordinator/moderation/` |
| Konta oczekujące na aktywację | `/coordinator/activations/` |
| Komitet: zatwierdzenia, województwa, zaproszenia | `/coordinator/committee/` |
| Wyniki etapu: przeliczenie i publikacja | `/coordinator/stages/<id>/results/` |

Czynności (POST) zostały na swoich dotychczasowych adresach; zmienił się wyłącznie powrót po nich.
`CoordinatorActionView` wraca na stronę, z której przyszło żądanie — pod warunkiem, że nagłówek
`Referer` wskazuje **ten sam serwer** i ścieżkę zaczynającą się od `/coordinator/`; w każdym innym
przypadku (także bez nagłówka) powrotem zostaje pulpit. Dzięki temu zatwierdzenie członka komisji
z listy nie wyrzuca z listy, a obcy adres w nagłówku nie jest drogą na inną stronę.

Karta etapu na pulpicie pokazuje stan, terminy, dwie liczby i **jedną** akcję główną wynikającą ze
stanu etapu (przygotowanie zadań → zablokowanie prac do oceny → przydziały → wyniki → ogłoszona
tabela). Reszta narzędzi etapu siedzi pod „Więcej”.

## 6. Procedury operacyjne

> **Runbook produkcyjny: [`docs/OPERACJE.md`](docs/OPERACJE.md).** Tam są rzeczy, które robi się
> na działającym serwisie i pod presją czasu: automatyczne kopie zapasowe poza serwer razem
> z cotygodniowym testem odtwarzania, monitoring i alarmy, wdrożenie z GitHub Actions, polityka
> logowania dwuskładnikowego oraz **lista kontrolna incydentu**. Sekcje niżej zostają jako opis
> pojedynczych czynności wykonywanych ręcznie.
>
> Skrót dla niecierpliwych:
>
> | Chcę | Polecenie / miejsce |
> |------|---------------------|
> | zrobić kopię teraz | `./scripts/backup.sh` (cron robi to co noc o 3:15) |
> | sprawdzić, czy kopie działają | `docker compose exec web python manage.py record_backup_status --show` |
> | odtworzyć kopię (bez ruszania produkcji) | `./scripts/restore.sh --dry-run`, potem bez `--dry-run` |
> | dostawać listy o awariach | `ALERT_EMAILS=` w `.env` (docs/OPERACJE.md § 3.2) |
> | postawić monitoring | `deploy/monitoring/README.md` |
>
> **Logowanie dwuskładnikowe (2FA) jest na tej instalacji wyłączone** – decyzja organizatora.
> `TWO_FACTOR_ENABLED` ma domyślnie wartość `0`, ekrany `/account/2fa/…` i `/login/2fa/`
> odpowiadają wtedy 404, w interfejsie nie ma do nich odnośników, a konta, które zdążyły włączyć
> drugi składnik wcześniej, logują się samym hasłem (ich urządzenia zostają zapisane w bazie –
> wyłącznik ich nie kasuje). Kod i opis włączenia: `docs/OPERACJE.md` § 5.

### 6.1 Kopia zapasowa

Ręczny wariant, przydatny przy jednorazowym zrzucie „przed czymś ryzykownym”. Wariantem
**produkcyjnym** jest `scripts/backup.sh` z crona: szyfruje paczki i wysyła je poza serwer, czego
poniższe polecenia nie robią (docs/OPERACJE.md § 1).

> **Kopia przed każdym wdrożeniem powstaje sama.** `scripts/deploy.sh` (krok 4a/8) robi
> `pg_dump -Fc` do `/opt/olimpiada-backups/pre-deploy-<data>-<wersja>.dump` **zanim** uruchomi
> kontener `web`, czyli zanim wykonają się migracje; niepowodzenie zatrzymuje wdrożenie. Te pliki
> są **nieszyfrowane i zostają na serwerze** (to kopia na kwadrans, nie kopia na wypadek pożaru) –
> trzymanych jest dziesięć ostatnich. Kopią „poza maszynę” jest nadal `scripts/backup.sh` z crona.

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

### 6.3 Zarządzanie etapami i zadaniami

Kalendarz edycji i arkusz zadań prowadzi koordynator z `/coordinator/` — bez wchodzenia do
`/admin/` i bez przeliczania godzin na UTC.

**Terminy etapu** — `/coordinator/` → karta etapu → **„Edytuj terminy”**
(`/coordinator/stages/<id>/edit/`):

| Pole | Uwagi |
|---|---|
| `opens_at` | Od tej chwili treści zadań są jawne publicznie i wolno oddawać rozwiązania. |
| `deadline_at` + `grace_seconds` | Upload zamyka się dopiero po `deadline_at + grace_seconds`; to ten moment zamyka etap (`beat`). |
| `review_deadline_at` | Termin recenzji. |
| `appeal_window_opens_at` / `appeal_window_closes_at` | Okno reklamacji. Publikacja wyników przed jego zamknięciem kończy się `409 APPEAL_WINDOW_OPEN`. |
| `location` | Puste = etap zdalny. Trafia na stronę główną i do terminarza na `/harmonogram/`. |
| `event_starts_on` / `event_ends_on` | **Dni wydarzenia** etapu stacjonarnego („Termin wydarzenia (od / do)”): dni pobytu, które portal ogłasza publicznie jako jeden zakres („4–7 czerwca 2027”). Wypełnia się je **razem** albo zostawia puste. To **inny fakt** niż `opens_at`/`deadline_at`: wpisanie dni pobytu nie zmienia okna, w którym system przyjmuje pliki (na finale sesja egzaminacyjna bywa kilkugodzinna w środku zjazdu), a okno uploadu nie zmienia terminu na stronie. Gdy dni są wpisane, oś czasu (`/`, `/harmonogram/`) pokazuje właśnie je; pulpit uczestnika i strona zadań pokazują **oba** terminy, bo uczestnik potrzebuje i dnia przyjazdu, i godziny oddania pracy. |

Godziny **podaje się i czyta w czasie polskim** (`Europe/Warsaw`); do bazy idzie UTC. Pola mają
dokładność do minuty — sekundy zapisane spoza panelu (admin, seed) zostają nietknięte, dopóki
nie zmienisz danego terminu. Kolejność terminów sprawdza `Stage.full_clean()`; błąd staje pod
polem i nic się nie zapisuje. Każdy zapis zostawia `stage.updated` w audycie z różnicą pól
(stara → nowa wartość, daty w ISO).

Trzy blokady, których formularz nie obejdzie:

1. **Termin oddania nie cofa się w przeszłość**, jeżeli do etapu wpłynęło choć jedno rozwiązanie
   (`409 STAGE_DEADLINE_IN_PAST`). Chcesz zamknąć etap wcześniej — użyj „Zamknij etap” (6.4).
2. **Etap zamknięty** (`closed_at`) przyjmuje już tylko `review_deadline_at`, okno reklamacji
   i `location` (`409 STAGE_CLOSED`).
3. **`results_published_at` jest nieedytowalne** — nakłada je i zdejmuje publikacja wyników (6.6).

**Nowy etap** — `/coordinator/stages/new/` (przycisk „Dodaj etap” przy nagłówku sekcji, widoczny,
dopóki edycja nie ma wszystkich trzech rodzajów). Etap powstaje przez `create_stage`, więc od razu
ma domyślną skalę 0/2/5/6 i próg kwalifikacji. **Skalę** zmienia się dalej na własnym ekranie
(6.3c), **próg kwalifikacji** — w `/admin/competitions/stage/<id>/change/` (link „Próg kwalifikacji
(admin)” na karcie etapu).

#### Karta zadania — `/coordinator/problems/<id>/`

Strona główna jednego zadania: wchodzi się na nią tytułem zadania z listy zadań etapu, z wiersza
tabeli przydziałów i z wyszukiwarki panelu. „Edytuj” jest jedną z czynności wykonywanych **z** tej
karty, a nie osobnym wejściem do zadania.

Sekcje:

- **Treść i ustawienia** — numer, tytuł (z wersją angielską, gdy jest), podgląd treści PDF, dozwolone
  formaty, limit rozmiaru pliku i uwagi dla recenzentów.
- **Skala i rubryka** — skala **obowiązująca** to zadanie razem z odpowiedzią na pytanie, skąd się
  wzięła: własne nadpisanie zadania albo skala odziedziczona po etapie (a więc: czy zmiana skali
  etapu to zadanie ruszy). Obok stoją kryteria rubryki i wspólne szablony komentarzy przypięte do
  zadania — prywatnych szablonów recenzenta nie widzi tu nikt.
- **Reguły przydziału** — reguły „to zadanie recenzuje ta osoba” z usunięciem i listą wyboru do
  dodania kolejnej (osoby, które regułę już mają, z listy wypadają).
- **Wzorcówka** — odnośnik do rozwiązania wzorcowego. Plik nie ma publicznego adresu i **nie staje
  się jawny po otwarciu etapu**: pobiera go wyłącznie komitet, przez widok panelu.
- **Prace** — po jednej, najnowszej wersji każdej pracy: kod uczestnika (odnośnik do jego karty),
  nazwisko, wersja, status, plik, recenzje z punktami i ocena końcowa. Czynności w wierszu:
  „Zablokuj do oceny”, „Przydziel”, „Zmień punkty”, „Cofnij”/„Odbierz” i korekta oceny końcowej
  z uzasadnieniem. Wyszukiwarka `?q=` działa po kodzie **i** po nazwisku.
- **Pobierz** — „Pobierz ZIP zadania” (`/coordinator/stages/<id>/download/?problem=<id>`), czyli tyle,
  ile komisja czyta za jednym posiedzeniem; nazwy plików w paczce są anonimowe (`kod_zadN_vM`).
  Obok stoi odnośnik do podobieństw rozwiązań — te liczą się dla **całego etapu**, bo podobieństwo
  powstaje między pracami, a nie w jednej z nich.
- **Statystyki** — rozkład ocen końcowych tego zadania jako słupki rysowane samym arkuszem stylów
  (strict CSP nie dopuszcza stylu w atrybucie, więc szerokość jest klasą z zamkniętej listy, skok co
  5%; dokładne liczby stoją obok i to one są odpowiedzią). Oś powstaje ze skali, więc wartość, której
  nie dostał nikt, zostaje na wykresie — „nikt nie dostał 6” jest informacją, a nie dziurą.

Karta, tak jak karta uczestnika i karta członka komisji, jest **wyłącznie odczytem**: wszystkie
formularze celują w istniejące widoki-akcje koordynatora, więc po zapisie wraca się tam, gdzie
akcja wraca zawsze (ekran przydziałów etapu). Dane składa `apps.competitions.problem_card`, a wiersze
prac — ten sam `stage_assignment_rows`, co ekran przydziałów, żeby dwa ekrany nie odpowiadały dwoma
warunkami na to samo pytanie „co wolno z tą pracą zrobić”. Liczba zapytań nie zależy od liczby prac
(pilnuje tego test równościowy), a sekcje „Rubryka” i „Szablony komentarzy” znikają, gdy odpowiednich
modułów w instalacji nie ma.

#### 6.3c Skala punktacji (etap i nadpisanie w zadaniu)

Ile punktów wolno wystawić i co która wartość znaczy, ustawia koordynator — bez `/admin/`.

**Skala etapu** — `/coordinator/` → karta etapu → **„Skala punktacji”**
(`/coordinator/stages/<id>/scale/`). Wartości wpisuje się w jednym polu tekstowym, po jednej
pozycji w wierszu, w postaci `wartość;opis`:

```text
0;brak istotnego postępu
2;istotny postęp, rozwiązanie niepełne
5;rozwiązanie pełne z drobnymi usterkami
6;rozwiązanie pełne i poprawne
```

Obok stoi **maksimum punktów** — osobne pole, które musi być równe największej wartości skali
(niezgodność to błąd pod polem, a nie ciche wyliczenie). Skala musi zawierać **0**, wartości muszą
być unikalne i rosnące (`ScoringScale.clean()`). Etap, któremu skali brakuje, dostaje ją z tego
ekranu — formularz startuje wtedy z domyślną 0/2/5/6. Pod formularzem jest podgląd: tak zobaczy
skalę recenzent. Każdy zapis zostawia `stage.scale_updated` w audycie z pełną skalą przed i po.

**Skala zadania** (nadpisanie) — formularz zadania (`/coordinator/problems/<id>/edit/`) ma te same
dwa pola: „Skala punktacji tego zadania” i „Maksimum punktów tego zadania”. **Puste = zadanie
punktuje skala etapu.** Wypełnia się je razem albo wcale. Pierwszeństwo ma zadanie:
`grading.services.allowed_scores(stage, problem)` pyta najpierw o `Problem.scoring_values`, a dopiero
w ich braku o `ScoringScale` etapu — i tę samą kolejność stosują wszystkie zapisy ocen
(`submit_review`, `revise_review`, `set_review_score`, `resolve_moderation`, `override_final_grade`,
decyzja reklamacyjna) oraz listy wyboru punktów w panelu recenzenta i na ekranie przydziałów.

**Reguła blokady (`409 SCALE_LOCKED`).** Dokładanie wartości i poprawianie opisów jest wolne
zawsze. **Usunięcie wartości, którą ktoś już wystawił** w recenzji (także anulowanej) albo w ocenie
końcowej — nie. Inaczej w bazie zostałyby oceny spoza skali: tabela wyników liczyłaby się z nich
dalej, a w formularzu nie dałoby się już wybrać tego, co faktycznie stoi w recenzji. Blokada
obowiązuje w obie strony: skala etapu patrzy na oceny zadań, które ją **dziedziczą** (zadania
z własną skalą jej nie blokują), a wyczyszczenie skali zadania jest sprawdzane względem skali
etapu, która wtedy zaczyna obowiązywać.

**Czego to nie zmienia:** przeliczanie wyników (`apps/results/services.py`) sumuje `FinalGrade.score`
i **nie używa** ani `ScoringScale.max_value`, ani `Problem.max_points` — maksimum jest deklaracją dla
ludzi i walidacją skali, a nie dzielnikiem w normalizacji. Progi kwalifikacji podaje się w punktach
bezwzględnych (`QualificationRule.min_points`), więc zmiana skali **nie przelicza** ich automatycznie
— po zmianie skali sprawdź próg.

#### 6.3a Rejestracja uczestników

Kto i kiedy może **założyć konto uczestnika**, ustawia koordynator z `/coordinator/` → **„Ustawienia
rejestracji”** (`/coordinator/registration/`). Stan („otwarta od … / rusza … / zamknięta … /
wyłączona”) stoi na pulpicie nad listą etapów.

| Pole | Znaczenie |
|---|---|
| `registration_enabled` | Wyłącznik awaryjny. Odznaczenie zamyka rejestrację natychmiast, niezależnie od terminów — i ich nie kasuje, więc po ponownym włączeniu okno wraca. |
| `registration_opens_at` | Puste = otwarta od zaraz. Data przed terminem jest **zapowiedzią**: strona główna i menu pokazują wtedy „Rejestracja rusza 8 września 2026” i prowadzą na `/register/`, gdzie stoi pełny komunikat z godziną. |
| `registration_closes_at` | Puste = do odwołania. Musi być po otwarciu (`Edition.full_clean()` + constraint w bazie; komunikat staje pod polem). |

Godziny podaje się i czyta w czasie polskim, dokładność do minuty — tak samo jak terminy etapów.
Każdy zapis zostawia `edition.registration_updated` w audycie z różnicą pól.

Bramka jest **w serwisie** (`apps.competitions.registration.ensure_registration_open`), więc
obowiązuje wszystkie trzy drogi naraz: formularz `/register/`, `POST /api/auth/register/participant/`
i rejestrację przez Google/Facebooka (ta ostatnia wraca na `/register/` z komunikatem i **nie**
zapisuje powiązania `SocialAccount`). Odmowa to `409 REGISTRATION_CLOSED`. Ukrycie przycisku na
stronie jest wyłącznie uprzejmością — żądanie wysłane skryptem dostaje ten sam kod. Stan czyta też
`GET /api/competitions/editions/current/` w polu `registration`
(`is_open`, `reason` ∈ `open`/`disabled`/`not_yet`/`closed`, `opens_at`, `closes_at`).

Czego to **nie** dotyczy: rejestracji komitetu na kod zaproszenia (`/register/committee/` — tam
regulatorem jest kod), kont już założonych oraz zapisów do etapów, które mają własne terminy (6.3).
Brak bieżącej edycji jest traktowany jak rejestracja wyłączona — nie ma wtedy do czego zakładać konta.

`manage.py seed_edition_kwantowa` ustawia przy **tworzeniu** edycji otwarcie na **8 IX 2026, 00:00**
(stała `REGISTRATION_OPENS`); istniejącej edycji nie rusza — także przy `--sync-dates`, bo po
pierwszej instalacji okno należy do koordynatora.

**Dane zbierane przy rejestracji uczestnika**: adres e-mail, imię i nazwisko, hasło (wyłącznie
jako hash), województwo, **szkoła** (wybrana ze słownika albo wpisana ręcznie — patrz 6.3b),
**klasa** (1–5), rok urodzenia oraz zgody (niżej). Zasada minimalizacji: w publicznych tabelach
wyników stoi wyłącznie kod uczestnika (`OLM-XXXXXX`).

**Zgody przy rejestracji.** Zestaw jest jeden dla wszystkich trzech dróg (`/register/`,
`POST /api/auth/register/participant/`, dokończenie rejestracji przez Google/Facebooka) i opisuje
go **jeden moduł**: `backend/apps/accounts/consents.py` — treść oświadczenia, dokument, wersja
i reguła wymagalności w jednym miejscu.

| Zgoda | Wymagana | Dokument | Wersja |
|---|---|---|---|
| akceptacja regulaminu (`terms_consent`) | zawsze | `/dokumenty/regulamin/` | 1.0 z 2 września 2026 |
| przetwarzanie danych osobowych (`gdpr_consent`) | zawsze | `/dokumenty/rodo/` | 1.0 z 22 lipca 2026 |
| zgoda rodzica lub opiekuna (`guardian_consent`) | gdy `bieżący rok − rocznik ≤ 18` | `/dokumenty/zgoda-opiekuna/` | 0.1 (projekt) |
| publikacja imienia i nazwiska (`publish_name_consent`) | nie | — | 1.0 |

Etykieta każdej zgody jest **linkiem do dokumentu** (nowa karta, `rel="noopener"`), a nazwa
organizatora pochodzi z `SiteSettings.organizer_name` — zmiana w `/cms/` przechodzi na formularz
bez wydania aplikacji.

**Link prowadzi do PDF-a, nie do podstrony** (`consents.document_link`, uwaga organizatora z 16.09).
Bierzemy **pierwszy załącznik PDF** strony dokumentu — ten sam, który karta „Do pobrania” pokazuje
przyciskiem głównym. Gdy przy dokumencie nie wisi jeszcze żaden PDF, etykieta prowadzi do strony
(`consents.document_url`): zgoda bez odnośnika do treści nie jest zgodą świadomą, więc brak pliku
nie może zostawić jej bez linku. **PDF-y wgrywa organizator w `/cms/`** (Strony → Dokumenty →
*pliki do pobrania*) — dopięcie pliku zmienia adres w formularzu natychmiast, bez wydania aplikacji.
`GET /api/auth/consents/` oddaje oba adresy: `document_url` (strona, do zacytowania w piśmie)
i `document_link` (to, pod co klika człowiek). Sama strona dokumentu ma przy tym **przycisk
„Pobierz PDF” nad treścią**, a nie tylko wiersz w karcie „Do pobrania” pod nią.

**Rocznik rozstrzyga o zgodzie opiekuna po obu stronach.** Regułę liczymy po roczniku
i zachowawczo: osoba urodzona osiemnaście lat temu może mieć jeszcze 17 lat, więc zgoda opiekuna
jest od niej wymagana. Rozstrzyga serwer (`ConsentFieldsMixin.clean` → `consents.is_minor`; błąd
staje **pod polem** zgody, bo wynika z innego pola tego samego formularza). W przeglądarce to samo
robi `backend/static/js/register-age.js`: odsłania albo chowa wiersz zgody opiekuna przy każdej
zmianie rocznika, ustawia na nim `required` i zdejmuje zaznaczenie z wiersza, który znika. Próg
i **rok bieżący** przychodzą z serwera atrybutami `data-*` na bloku zgód (`minor_rule`
w `apps/web/context_processors.py`) — skrypt nie ma własnej definicji „niepełnoletni”, a zegar
przeglądarki bywa przestawiony. Bez JavaScriptu wiersz jest widoczny zawsze, a pod polem „Rok
urodzenia” stoi zdanie, które mówi, czego się spodziewać.

**Gdzie mieszka dowód.** Każda wyrażona zgoda to wiersz `accounts.ConsentRecord`
(uczestnik, rodzaj, **wersja dokumentu**, data, droga: `web`/`api`/`social`/`panel`, ewentualne
`withdrawn_at`) plus jeden wpis audytowy `participant.consents_recorded`. Pola na profilu
(`terms_accepted_at`, `gdpr_consent_at`, `guardian_consent`, `publish_full_name`) są **projekcją**
stanu bieżącego — do szybkiego odczytu, nie do dowodzenia. Historię widać w `/admin/` przy
profilu uczestnika, w `GET /api/auth/me/` (`participant.consents`) i w panelu uczestnika.
Odmowa przy braku zgody wymaganej to `400 CONSENT_REQUIRED`.

**Zmiana wersji dokumentu** = zmiana stałej w `apps/accounts/consents.py` (`TERMS_VERSION`,
`PRIVACY_VERSION`, `GUARDIAN_VERSION`). Od tego momentu nowe zgody zapisują się pod nową wersją,
a stare wpisy dalej mówią prawdę o tym, co obowiązywało wtedy.

**Wycofanie zgody w portalu** dotyczy dokładnie jednej: publikacji imienia i nazwiska. Uczestnik
wyraża ją i wycofuje w `/me/` → sekcja **„Twoje zgody”** (`POST /me/consents/publish-name/`);
wycofanie nie kasuje wiersza, tylko stawia `withdrawn_at`, więc z historii dalej widać, kiedy
zgoda obowiązywała. Pozostałe trzy są warunkiem udziału albo oświadczeniem o zapoznaniu się
z dokumentem — ich wycofanie znaczy rezygnację z Olimpiady i jest sprawą do organizatora.

Treść zgód wydaje publicznie `GET /api/auth/consents/` (bez logowania): rodzaj, brzmienie w HTML
i czystym tekście, adres dokumentu, wersja i reguła wymagalności. Klient zewnętrzny ma dzięki
temu pokazać **to samo** oświadczenie, a nie własną parafrazę.

**Ochrona przed rejestracją maszynową (CAPTCHA, bez usług obcych).** Oba publiczne formularze
zakładania konta — `/register/` i `/register/committee/` — mają trzy niezależne zabezpieczenia
(`backend/apps/web/captcha.py`):

| Warstwa | Co robi |
|---|---|
| CAPTCHA obrazkowa | Działanie arytmetyczne („7 × 3 =”) rysowane przez Pillow i serwowane spod **naszego** adresu `/captcha/image/<klucz>/` (`django-simple-captcha`). Wyzwanie leży w naszej bazie (`captcha.CaptchaStore`), jest jednorazowe i wygasa po 10 minutach (`CAPTCHA_TIMEOUT`). |
| Pułapka (honeypot) | Pole `website` ukryte arkuszem stylów (klasa `hp-field`), a **nie** `type="hidden"` — bot wypełniający wszystkie pola odpada, człowiek pola nie widzi i nie dostaje na nim fokusu. |
| Minimalny czas wypełniania | Ukryty, **podpisany** znacznik czasu (`form_ts`); POST szybszy niż `ANTISPAM_MIN_FILL_SECONDS` (3 s) jest odrzucany. Podpis jest konieczny — niepodpisany znacznik bot przepisałby na dowolną wartość. |

Pułapka i próg czasu odrzucają **jednym, ogólnym** komunikatem: zdanie „wypełniłeś ukryte pole”
byłoby instrukcją obejścia. Limit prób (`register`, 10/h na adres) działa niezależnie i dalej.

Dlaczego nie reCAPTCHA/hCaptcha/Turnstile: polityka cookie (`/dokumenty/cookies/`) obiecuje brak
treści od podmiotów trzecich i brak profilowania odwiedzających — każdy z tych dostawców
wymagałby skryptu z obcej domeny (czyli rozluźnienia CSP) i przekazania mu danych o użytkowniku.
Tu do CSP nie trzeba było dopisać ani jednego hostu: obrazek mieści się w `img-src 'self'`.

Cena tej decyzji: **nie ma wersji dźwiękowej** wyzwania (`CAPTCHA_FLITE_PATH` nieustawione).
Podpowiedź pod polem podaje adres `contact@qaif.org` — kto obrazka nie widzi, pisze do
organizatora i konto jest zakładane ręcznie (`manage.py bootstrap_coordinator` / `/admin/`).

W testach i w scenariuszu E2E CAPTCHA przyjmuje odpowiedź `PASSED`: `CAPTCHA_TEST_MODE` jest
włączone w `config/settings/test.py` oraz — razem z wyzerowanym progiem czasu — przy `E2E_MODE=1`,
którego **w produkcji nie ustawia się nigdy** (nakładka `docker-compose.dev.yml` ustawia je dla
usługi `web`, więc w środowisku developerskim formularz też przyjmie `PASSED`).

#### 6.3b Słownik szkół (SIO/RSPO)

Pole „Szkoła” w `/register/` i w dokończeniu rejestracji przez Google/Facebooka to **wyszukiwarka
po rejestrze**, a nie wolny tekst. Uczestnik pisze fragment nazwy albo miejscowości, dostaje do
20 podpowiedzi zawężonych do wybranego województwa (`GET /api/schools/?q=&voivodeship=&limit=`,
bez logowania, throttle `schools`) i wybiera jedną. Wyszukiwanie jest odporne na diakrytyki –
„lodz” znajduje „ŁÓDŹ” – i wymaga trafienia **każdym** wpisanym słowem.

**Krok „Miejscowość”.** Nad polem szkoły stoi pole `Miejscowość`
(`GET /api/schools/cities/?q=&voivodeship=&limit=`, do 20 odrębnych par *miasto + województwo*,
dopasowanie **prefiksowe** i odporne na diakrytyki: „lod” znajduje „Łódź”). Po wskazaniu
miejscowości wyszukiwarka szkół pyta **wyłącznie** o nią, a **pusty tekst znaczy „pokaż wszystkie
szkoły tego miasta”** — lista doczytuje się przewijaniem (`offset` w zapytaniu, `has_more`
w odpowiedzi). Krok jest **nieobowiązkowy**: kto zna nazwę swojej szkoły, wpisuje ją jak dotąd,
a wartość pola nigdy nie trafia do serwisu (`SchoolChoiceMixin.clean` ją zdejmuje — opisuje sposób
szukania, a nie szkołę).

**Miasto, a nie „miejscowość z wykazu” (`School.city_parent`).** Wykaz SIO zapisuje pięć
największych miast **dzielnicami**: cztery jako `Miasto-Dzielnica` (`Wrocław-Krzyki`,
`Kraków-Nowa Huta`, `Łódź-Bałuty`, `Poznań-Grunwald`), a **Warszawę wyłącznie nazwami dzielnic**
(`Śródmieście`, `Wola`, `Mokotów`… — napisu „Warszawa” nie ma w nim ani razu). Pierwsza wersja
kroku „Miejscowość” brała te napisy dosłownie, więc „warszawa” nie znajdowało **niczego** mimo 333
stołecznych szkół, a „wro” dawało pięć pozycji, z których każda zawężała listę do jednej piątej
Wrocławia. Dlatego przy każdym wierszu stoi wyliczona **gmina** (`apps/schools/normalise.py`):

| reguła | warunek | przykład |
|---|---|---|
| (a) `Miasto-Dzielnica` → `Miasto` | człon przed myślnikiem jest jednym z pięciu miast z ustawowym podziałem na dzielnice (Kraków, Łódź, Poznań, Warszawa, Wrocław) | `Wrocław-Krzyki` → `Wrocław`; `Bielsko-Biała`, `Kędzierzyn-Koźle`, `Busko-Zdrój` zostają nietknięte |
| (b) dzielnica Warszawy → `Warszawa` | nazwa jest jedną z 18 dzielnic **i** województwo to `mazowieckie` **i** kod pocztowy jest z puli stolicy (`00-`…`04-`, dla Wesołej `05-07x`) | `Śródmieście` → `Warszawa`; `Wola` w małopolskiem albo z kodem `05-660` zostaje `Wolą` |

Kod pocztowy jest w regule (b) dlatego, że wykaz **nie ma** kolumny powiatu ani gminy (są:
województwo, miejscowość, kod pocztowy, ulica, typ podmiotu, kategoria uczniów), a „Wola”,
„Bielany” czy „Wilanów” to również nazwy wsi. Gdyby kolumna powiatu kiedyś doszła, to ona byłaby
warunkiem właściwym. Na wykazie 2025/2026 reguła przepisuje **903 wiersze** i nie rusza żadnego
innego: Warszawa 0 → 333, Wrocław 0 → 132, Kraków 0 → 170, Łódź 3 → 134, Poznań 5 → 142,
Gdańsk 84 → 84 (nierozbity w wykazie, więc bez zmian).

Oryginalne `School.city` **zostaje** — to adres szkoły z rejestru. Podpowiedź miast zwraca gminy
(„wro” → jeden `Wrocław`), wybranie miasta obejmuje wszystkie jego dzielnice, a pozycja listy
szkół pokazuje `city_label` w postaci **„Wrocław (Krzyki)”**. Działa też droga odwrotna:
„warszawa śródmieście” albo „wrocław krzyki” wpisane w pole „Szkoła” zawężają do jednej dzielnicy
(oba wyrazy są w `search_text`), a sama nazwa dzielnicy w kroku „Miejscowość” podpowiada jej
miasto („krzyki” → `Wrocław`).

Powód jest w uwagach organizatora z 16.09: „po wpisaniu «wrocław» nie widać liceów
ogólnokształcących, a «liceum» nie pokazuje odpowiedniej listy”. Obie obserwacje mają jedną
przyczynę — dwadzieścia trafień z całej Polski posortowanych alfabetycznie. Stąd druga zmiana:
**porządek zaczyna się od typu szkoły** (`apps/schools/models.py::KIND_ORDER` — licea
ogólnokształcące, technika, reszta), a dopiero w obrębie typu decyduje nazwa. Przy samym alfabecie
pierwsze dwadzieścia pozycji dużego miasta to szkoły branżowe i technika przy zespołach szkół.

Dopasowanie po miejscowości idzie po **wyliczonej kolumnie** `School.city_search` w postaci
`gmina|dzielnica` (bez diakrytyków, małymi literami: `wroclaw|krzyki`, `warszawa|srodmiescie`,
`gdansk`; indeks `schools_city_kind_idx`), tak samo jak wyszukiwanie szkół idzie po
`School.search_text` (nazwa + miejscowość z wykazu + gmina). Separatorem jest `|`, a nie spacja,
bo w wykazie są 43 pary gmin, w których jedna nazwa zaczyna nazwę drugiej (`Opole` i `Opole
Lubelskie`, `Brzeg` i `Brzeg Dolny`, `Nowe` i `Nowe Miasto`) — przy spacji wybranie „Opola”
dokładałoby szkoły z Opola Lubelskiego. Rozszerzenia Postgresa `unaccent` świadomie **nie**
zakładamy: wymaga uprawnień, których rola aplikacyjna na produkcji nie ma, a złożony raz napis
jest dla planisty tańszy od funkcji w warunku. Wszystkie trzy kolumny wyliczane (`city_parent`,
`city_search`, `search_text`) liczy **jedna** funkcja `apps/schools/normalise.py::derived_fields`,
a woła ją `School.save()`, `seed_schools` (jawnie, bo `bulk_create`/`bulk_update` omijają `save()`)
oraz backfill w migracjach `schools.0002` i `schools.0003` — te ostatnie dlatego, że na produkcji
słownik jest już wgrany, a migracje idą przed komendą seedującą.

Po co: wolny tekst nie grupuje. „II LO w Krakowie”, „2 LO Kraków” i „Liceum nr 2” to dla bazy
trzy różne szkoły, więc próg k-anonimowości w publikacji wyników (`INITIALS_SCHOOL`, 7.4) nie ma
czego zliczyć. Wybór ze słownika zapisuje w profilu nazwę **przepisaną z rejestru** oraz
dowiązanie `Participant.school_ref`.

**Szkoły spoza wykazu są dopuszczone i to jest świadome.** Checkbox „Mojej szkoły nie ma na
liście” odsłania pole „Nazwa szkoły” (min. 3 znaki) i profil powstaje bez dowiązania. Rejestr
ministerialny nie zna szkół zagranicznych ani placówek założonych po dacie wykazu, a jego
nieaktualność nie może zamykać drogi do olimpiady.

**Wyszukiwarka nie zależy od żadnej biblioteki ani od CDN-u.** `backend/static/js/school-picker.js`
to czysty JavaScript serwowany z własnego adresu (`<script defer nonce=…>`, bez kodu inline, bez
Alpine). Poprzednia wersja była komponentem Alpine'a ładowanym z `cdn.jsdelivr.net`: uczestnik,
któremu firmowe proxy albo wtyczka blokowały ten jeden adres, nie dostawał ani jednej podpowiedzi,
wpisywał nazwę szkoły w widoczne pole i słyszał od serwera „wybierz szkołę z listy”. Punkty
zaczepienia to atrybuty `data-picker` na widżetach (`apps/web/forms.py::SchoolChoiceMixin`);
skrypt startuje na `DOMContentLoaded` dla **każdego** `[data-school-picker]` na stronie
(rejestracja, dokończenie rejestracji przez dostawcę, edycja profilu, edycja konta w panelu
koordynatora). Ten sam skrypt obsługuje krok „Miejscowość” — obie listy używają tych samych klas
i tej samej obsługi klawiatury (strzałki, Enter, Escape).

**Reguła serwera nie jest twardym „albo/albo”** (`SchoolChoiceMixin.clean` + `_resolve_school`):

| co przyszło | co się dzieje |
|---|---|
| `school_city` (dowolne) | **nigdy nie dociera do serwisu** — jest zakresem wyszukiwania, nie daną o szkole |
| `school_id` wybrany | wygrywa; nazwa przepisana z rejestru, wolny tekst ignorowany |
| brak `school_id`, wolny tekst niepusty | **przyjęte jako szkoła spoza wykazu — nawet bez zaznaczonego checkboksa** (wpisany tekst jest jednoznaczną odpowiedzią; kratka służy do odsłonięcia pola, a nie do poświadczenia wpisu) |
| brak jednego i drugiego, ale coś wpisano w wyszukiwarkę | „Wybierz szkołę z podpowiedzi albo zaznacz „Mojej szkoły nie ma na liście” i wpisz jej nazwę.” |
| wszystko puste | „Wybierz szkołę z listy albo zaznacz, że nie ma jej na liście.” |

Strona działa też **bez JavaScriptu**: pole wolnego tekstu jest wtedy widoczne od początku i sam
wpis wystarczy. Kontrola w przeglądarce bez okna: `e2e/check_school_picker.py` (podpowiedzi
miejscowości — w tym „wroc”, „warszawa” i „krzyki” na **pełnym** słowniku z `seed_schools`, pełna
lista szkół miasta i jej doczytywanie przewijaniem, etykieta „Wrocław (Krzyki)” na pozycji listy,
zapis `school_id` po kliknięciu, widoczność pola wolnego tekstu przed i po zaznaczeniu kratki).

W API rejestracji (`POST /api/auth/register/participant/`) szkołę podaje się jako `school_id`
(wiersz słownika) **albo** `school` (nazwa). Klient sprzed wprowadzenia słownika, który zna tylko
`school`, działa bez zmian.

| Źródło | Wykaz szkół i placówek oświatowych wg stanu bazy SIO na 30.09.2025 — [dane.gov.pl, zbiór 839](https://dane.gov.pl/pl/dataset/839) |
|---|---|
| Zakres | licea, technika, szkoły branżowe I i II stopnia, szkoły artystyczne i specjalne przysposabiające do pracy; **bez** szkół dla dorosłych. Rok 2025/2026, **8 118 pozycji** |
| Plik w repozytorium | `backend/apps/schools/fixtures/szkoly-srednie-sio-2025.json` (≈ 1,9 MB, jeden obiekt na linię) — szczegóły w `fixtures/README.md` |
| Wgranie do bazy | `manage.py seed_schools` — idempotentne (upsert po numerze RSPO), uruchamiane przy **każdym** wdrożeniu (`scripts/deploy.sh`, krok 6/7), poza bramką `.first-deploy`: to dane referencyjne, a nie treść redakcyjna |

Odświeżenie raz na rok szkolny: pobierz nowy wykaz z dane.gov.pl, uruchom
`backend/.venv/Scripts/python.exe scripts/build_school_fixture.py Wykaz_szkol.xlsx`, sprawdź diff,
zacommituj — wdrożenie samo wywoła `seed_schools`. Reguła doboru wierszy siedzi w
`apps/schools/sio.py` (i tam jest testowana), skrypt jest tylko interfejsem wiersza poleceń.
Szkoła, której **nie ma** w nowym wykazie, dostaje `is_active=False` i znika z podpowiedzi — ale
wiersz zostaje, bo mogą na niego wskazywać profile sprzed roku (`on_delete=PROTECT`). Nazwy są
przepisane dosłownie, wersalikami, tak jak stoją w rejestrze.

**Zadania** — karta etapu → **„Zadania (n)”** (`/coordinator/stages/<id>/problems/`): lista
z numerem, tytułem, obecnością treści PDF, dopuszczonymi formatami rozwiązania, limitem rozmiaru
i liczbą oddanych prac, a pod nią formularz dodania.

- `number` jest unikalny w etapie (komunikat pod polem, nie zderzenie z bazą),
- `statement_pdf`: **PDF do 20 MB**, rozpoznawany po nagłówku `%PDF-`, a nie po rozszerzeniu ani
  `Content-Type` (Caddy przepuszcza 25 MB, patrz `MAX_UPLOAD_MB`). Plik idzie na storage
  `private_media` — przed otwarciem etapu nie ma publicznego adresu,
- `allowed_formats` to pola wyboru `pdf`/`ipynb`/`py`/**`jpg` („JPEG (zdjęcie rozwiązania)”)**
  — minimum jeden; `max_file_mb` to 1–100 MB. JPEG jest dla uczestnika bez skanera: fotografuje
  kartkę telefonem. Uczestnik może wysłać plik z rozszerzeniem `.jpg` **albo** `.jpeg` — serwer
  sprowadza je do jednej nazwy formatu (`jpg`), a o przyjęciu decyduje sygnatura `FF D8 FF`, nie
  nazwa ani `Content-Type` (PDF przemianowany na `.jpg` odpada z `400 INVALID_FILE_TYPE`). Limit
  rozmiaru jest ten sam, co dla PDF-a (`max_file_mb`). Recenzent dostaje wtedy w panelu podgląd
  zdjęcia zamiast pdf.js — z tą samą warstwą adnotacji (zdjęcie jest „stroną 1”),
- **podgląd treści przed otwarciem etapu widzi wyłącznie koordynator**
  (`GET /api/competitions/problems/<id>/statement/`); uczestnik, recenzent i anonim dostają 404,
- **podmiana treści po `opens_at`** wymaga zaznaczenia „Rozumiem, że uczestnicy już widzą treść”
  (bez tego 400). Po podmianie ogłoś erratę w aktualnościach — część zawodników rozwiązuje już
  poprzednią wersję,
- **usunięcie zadania** jest możliwe tylko, dopóki nie ma do niego rozwiązań (`409
  PROBLEM_HAS_SUBMISSIONS`); przycisk „Usuń” znika z wiersza, gdy licznik prac jest niezerowy.

Wszystkie operacje na zadaniach zostawiają ślad w audycie (`problem.created`, `problem.updated`
z różnicą pól, `problem.deleted`).

**Strona `/harmonogram/` czyta terminy z bazy.** Blok `stage_timeline` (StreamField, znacznik
`{{stage_timeline}}` w `apps/cms/fixtures/legacy/harmonogram.md`) renderuje etapy **bieżącej**
edycji: rodzaj, otwarcie, termin oddania, „Wyniki do”, okno reklamacji, miejsce i stan.
Nie ma tam ani jednej daty wpisanej ręcznie, więc zmiana w panelu jest widoczna od następnego
odświeżenia strony (żadnego cache). Bez bieżącej edycji lub bez etapów blok pokazuje „Terminy
zostaną ogłoszone”. Ta sama zasada obowiązuje oś czasu na stronie głównej.

Dwie rzeczy w podpisach rubryk są decyzją, a nie formatowaniem:

- **„Wyniki do” zamiast „Recenzje do”** (ta sama data, `Stage.review_deadline_at`). Czytelnikiem
  strony publicznej jest uczestnik, nie recenzent: nie ma wpływu na recenzowanie i nic z niego nie
  ma, a interesuje go najpóźniejszy moment, w którym dowie się wyniku. W panelu koordynatora
  i recenzenta ten sam termin pozostaje terminem recenzji, bo tam jest zobowiązaniem.
- **etap stacjonarny trwający kilka dni ma jeden „Termin”**, podany jako zakres („4–7 czerwca
  2027”), a nie „Otwarcie” i „Deadline” z godzinami. Na finał się przyjeżdża; dwa wiersze
  z godzinami sugerowały okno na wysyłkę pliku, którego na miejscu nie ma. Kryterium jest opisowe
  (`Stage.location` wypełnione **i** terminy w różnych dniach — `apps.cms.timeline.is_onsite_event`),
  a nie `kind == FINAL`: rodzaj etapu mówi, które to zawody w kolejności, a nie jak przebiegają,
  więc zjazd na etapie II w kolejnej edycji zachowa się właściwie bez poprawki w szablonie.
  Formatowanie zakresu ma jedno miejsce (`apps.cms.timeline.format_date_range`), wspólne dla
  `/harmonogram/` i strony głównej: wspólny miesiąc → „4–7 czerwca 2027”, różne miesiące →
  „30 maja – 2 czerwca 2027”, przełom roku → „30 grudnia 2026 – 2 stycznia 2027”.

Kolejność przy zakładaniu środowiska: `seed_edition_kwantowa --make-current` (etapy) →
`seed_legacy_content` (strony, w tym `/harmonogram/`). Odwrotna kolejność też działa — blok czyta
bazę przy każdym żądaniu, a nie przy imporcie treści.

**Warsztaty online mają własną stronę `/warsztaty/`** (pozycja menu zaraz za „Harmonogramem”,
źródło: `apps/cms/fixtures/legacy/warsztaty.md`). Wcześniej cały ich harmonogram był tabelą
w środku `/harmonogram/`: kto tam nie wszedł i nie przewinął strony do końca, nie dowiadywał się,
że warsztaty w ogóle są — mimo że są bezpłatne, otwarte i od nich zaczyna się przygotowanie.
Strona główna pokazuje **trzy najbliższe** terminy (sekcja „Warsztaty online” z przyciskiem do
pełnej tabeli) i czyta je z tej samej tabeli, więc harmonogram warsztatów istnieje w serwisie
dokładnie raz. Reguła „które są najbliższe” siedzi w `apps/cms/workshops.py`; sekcja znika w całości,
gdy nie ma już nadchodzących terminów. Żeby dało się je uszeregować bez parsowania polszczyzny przy
każdym żądaniu, wiersz harmonogramu ma obok tekstowego terminu opcjonalną **datę** (`date_value`
w `ScheduleRowBlock`, wypełniana przy imporcie przez `legacy_markdown.parse_polish_date`). Termin
nieostry („do potwierdzenia”) zostaje bez daty: stoi w tabeli, ale nie trafia do zapowiedzi.

#### 6.3b Linia czasu w nagłówku (pasek „terminalowy”)

Pod menu serwisu, na **każdej** stronie, stoi wąski pasek wzorowany na nagłówku `oi.edu.pl`:
bracketowany wykres postępu edycji `[|===>...............|......]`. `=` to czas miniony, `>` —
dzisiaj, `.` — to, co przed nami, a `|` to wydarzenie. Kolory znaczą stan: **niebieski** = minione,
**czerwony** = trwa, **zielony** = przed nami.

**W spoczynku to jedna linia i nic więcej** — pasek dokłada nagłówkowi 17 px (zmierzone: nagłówek
61 px bez paska, 78 px z paskiem przy oknie 1280 px), więc blok menu zachowuje swoją wysokość.
Wiersze „kodu” (`rok_szkolny(2026, 2027);`, `edycja(XV);`) i legenda terminów siedzą w panelu
zwiniętym do zera wysokości.

**Skąd biorą się terminy.** Pasek scala **cztery** źródła (`apps.cms.timeline.timeline_events`),
bo tyle jest w tej olimpiadzie rodzajów terminu i każdy mieszka gdzie indziej:

| źródło | co trafia na pasek |
| --- | --- |
| `competitions.Stage` | etapy zawodów bez treningowego; termin = dni wydarzenia (`event_range`), a bez nich okno `opens_at`…`deadline_at`. Odnośnik pojawia się dopiero po ogłoszeniu wyników |
| `competitions.EditionEvent` | wydarzenia dopisane przez koordynatora (patrz niżej) |
| `Edition.registration_opens_at` / `…closes_at` | „Rejestracja uczestników”, o ile rejestracja jest włączona i ma datę otwarcia |
| tabela na `/warsztaty/` | **każdy warsztat osobno** („Warsztaty: Kubity”), bo warsztat jest osobnym wydarzeniem, na które zapisuje się osobno. Wiersz bez odczytanej daty jest pomijany |

Oś idzie od najwcześniejszego początku do najpóźniejszego końca; edycja z mniej niż dwoma
terminami dostaje zamiast tego cały rok szkolny (1 IX – 31 VIII), bo oś długości jednego
wydarzenia nie niosłaby żadnej informacji.

**Rozwija się w dół.** Najechanie na pasek, wejście w niego klawiszem (`:focus-within`) albo —
na ekranie bez najechania — dotknięcie kreski rozwija pod wykresem panel (`grid-template-rows:
0fr → 1fr`, ~250 ms; przy `prefers-reduced-motion: reduce` natychmiast). W panelu są wiersze
„kodu”, warstwa „pomiaru” i **legenda**: zawijany rząd chipów z nazwą i terminem każdego
wydarzenia, w kolejności chronologicznej. Najechanie na kreskę podświetla jej chip i odwrotnie;
kreska, na której schodzi się kilka wydarzeń (na rocznej osi jedna komórka to blisko cztery dni),
podświetla wszystkie swoje chipy i niesie je wszystkie w `aria-label`.

Panel stoi **pod** wykresem, więc rozwinięcie nie rusza samej linii, a szerokość nie zmienia się
nigdy: wykres skaluje się jednostką `cqw` w kontenerze zapytań (`container-type: inline-size`),
a legenda jest zwykłym zawijanym rzędem w tej samej kolumnie. Poprzednia wersja pokazywała nazwy
w dymkach przypiętych do kresek — przy kilkunastu warsztatach trzeba było najechać na każdą
z osobna, więc dymki zastąpiła legenda. Poniżej 640 px wykres znika (102 znaki miałyby tam
wysokość dwóch pikseli), a kalendarz zostaje **zwiniętym** `<details>` z jednym zdaniem
(„co teraz”).

Wszystko liczy serwer — strona z wyłączonym JavaScriptem ma komplet terminów, właściwie ustawioną
głowicę i działające rozwijanie (robi je sam CSS). `static/js/timeline-strip.js` dokłada trzy
rzeczy: co minutę przesuwa głowicę (z `data-axis-start`/`data-axis-end`), żeby karta zostawiona
otwartą przez noc nie pokazywała wczorajszego stanu; rysuje pod kursorem warstwę „pomiaru” —
paczkę falową, która po najechaniu na wydarzenie zapada się w pik nad nim (`|ψ|²`, ~30 kl./s,
tylko pod kursorem); i wiąże kreskę z chipem legendy (są w różnych gałęziach drzewa, więc
selektor ich nie połączy). Na ekranie dotykowym ten sam kawałek obsługuje dotknięcie: pierwsze
otwiera panel, drugie w tę samą kreskę idzie już odnośnikiem. Animacja wejścia, migający kursor
i warstwa „pomiaru” znikają przy `prefers-reduced-motion: reduce` i na ekranach dotykowych.

**Bufor: 5 minut.** Pasek renderuje się przy każdym żądaniu HTML i kosztuje cztery zapytania, więc
wynik siedzi w `django.core.cache` pod kluczem `cms:timeline-strip:<edycja>`. Zapis z panelu
koordynatora **czyści go od razu** (`apps.competitions.events` → `invalidate_timeline_cache`),
więc dopisane wydarzenie widać natychmiast. Pięć minut opóźnienia dotyczy wyłącznie zmian robionych
inną drogą: terminów etapu, okna rejestracji i treści strony warsztatów.

**Koordynator dodaje wydarzenia** w `/coordinator/events/` (odnośnik „Wydarzenia (linia czasu)”
obok „Dodaj etap” na pulpicie). Wydarzenie ma nazwę, dzień początku, opcjonalny dzień końca
(pusty = jednodniowe), dopisek („online”, „Kraków, ICE”), opcjonalny odnośnik i wyłącznik
„Pokazuj na linii czasu” (wiersz schowany zostaje na liście koordynatora — inaczej nie dałoby się
go odsłonić). Godzin się tu nie podaje: to kalendarz **ogłaszany**, a nie egzekwowany — wydarzenie
niczego w systemie nie otwiera ani nie zamyka. Odnośnik musi być adresem `http(s)://…` albo ścieżką
w tym serwisie (`/warsztaty/`); `javascript:` i `//obcy.host/` są odrzucane, bo w ten odnośnik
klika publiczność. Każda operacja zostawia wpis audytowy (`event.created` / `event.updated`
z różnicą pól / `event.deleted` z pełną treścią skasowanego terminu).

#### 6.3c Etap treningowy (`seed_training_problems`)

Do czego jest: żeby przejść **całą** ścieżkę portalu na działającym serwisie — rejestracja →
zgłoszenie do etapu → upload → dwie recenzje ślepe → konsensus/moderacja → przeliczenie i publikacja
wyników — na prawdziwych zadaniach, **bez terminu** i **poza zawodami**. To piaskownica, a nie etap
olimpiady: osobny rodzaj etapu `TRAINING` („Trening”), jeden na edycję.

```bash
# Na produkcji, RAZ, świadomie (nie ma tego w scripts/deploy.sh):
docker compose exec -T web python manage.py seed_training_problems
```

Komenda tworzy etap „Zadania treningowe” na **bieżącej** edycji (bez niej kończy się błędem) i wgrywa
cztery zadania z `backend/apps/competitions/fixtures/training/`: „Stan kubitu i pomiar (proste)”,
„Splątanie z dwóch bramek (średnie)”, „Podsłuch w protokole BB84 (trudne)” i „Nierówność CHSH
i granica Tsirelsona (diabelnie trudne)”. Jest **idempotentna**: drugie uruchomienie nie duplikuje
ani etapu, ani zadań, a plik PDF podmienia wyłącznie wtedy, gdy zmieniły się jego bajty.

Czym trening różni się od etapu zawodów:

| Rzecz | Zachowanie |
|---|---|
| Termin oddania | **Nie ma go.** W bazie stoi data-wartownik `2099-12-31 23:59` (`TRAINING_DEADLINE`), bo oś czasu etapu musi być kompletna i uporządkowana. Interfejs pyta o `Stage.has_deadline` i pisze „bez terminu” — roku 2099 nigdzie nie pokazuje. `beat` nigdy sam tego etapu nie zamknie. |
| Kwalifikacja | Żadna. Trening nie ma następnego etapu i nie jest następnym etapem dla nikogo (nie ma go w `apps.results.services.STAGE_ORDER`). |
| Zapisy | Uczestnik zgłasza się **sam**, tak jak do eliminacji (przycisk „Zgłoś się do treningu” w `/me/`). |
| Publiczna oś czasu | Nie ma go ani na `/`, ani na `/harmonogram/`; „etapem bieżącym” zawsze zostaje etap zawodów. |
| Treści zadań | Jawne od chwili utworzenia etapu (`opens_at` = „teraz”) — widać je na `/zadania/` w sekcji „Zadania treningowe”. |
| Wyniki | Wolno przeliczyć i ogłosić: brama „okno reklamacji musi być zamknięte” treningu nie dotyczy (inaczej czekałaby na rok 2099). Tabela dostaje odznakę **„trening”** na `/wyniki/` i `/results/<id>/`. |

Usunięcie piaskownicy po testach: koordynator kasuje zadania z `/coordinator/stages/<id>/problems/`
(możliwe, dopóki nie ma do nich prac), a sam etap — z `/admin/competitions/stage/`. Kasowanie etapu
zabiera ze sobą wpisy, zgłoszenia i recenzje treningowe, i o to chodzi.

Treść zadań: pliki organizatora `fixtures/training/zadanie-P1.pdf` … `zadanie-P4.pdf` („Zadania
przykładowe”, od 25.09.2026 osobny PDF na każde zadanie). Podmiana pliku w repozytorium plus ponowne
uruchomienie komendy wgrywa nową wersję (tylko tym zadaniom, których bajty się zmieniły);
tytuły zadań są w `apps/competitions/training.py` (koordynator może je zmienić w panelu, ale kolejny
przebieg komendy przywróci te z kodu).

### 6.4 Zamknięcie etapu

Normalnie robi to `beat` (`apps.submissions.tasks.close_due_stages`, co 60 s) po
`deadline_at + grace_seconds`: najnowsza wersja każdego zgłoszenia dostaje status `LOCKED`,
a etap – znacznik `closed_at`.

Ręcznie (awaria beata, decyzja komitetu o wcześniejszym zamknięciu):

1. `/coordinator/` → karta etapu → **„Zamknij etap”**. Operacja jest idempotentna: powtórzenie daje
   `409 STAGE_ALREADY_CLOSED`, a nie ciche „nic się nie stało”. Do audytu trafia
   `stage.closed` z `manual: true`.
2. Potem **„Przydziel recenzentów”** (domyślnie 2 na pracę). Prace, dla których nie da się
   skompletować recenzentów bez konfliktu województwa, są wypisane jako pominięte – z pseudonimem, nie
   z nazwiskiem. Przydział jest szeregowany blokadą doradczą, więc dwa równoległe kliknięcia nie
   dają czterech recenzentów.

Przesunięcie samych terminów robi się w panelu (6.3), a nie przez zamknięcie etapu. Komenda
`manage.py e2e_timeline` jest **wyłącznie** dla środowiska testowego i bez `E2E_MODE=1` odmawia
działania.

#### Ocenianie przed zamknięciem etapu

Komitet nie musi czekać na deadline. Karta etapu ma obok „Zamknij etap” przycisk **„Zablokuj oddane
prace do oceny”** (`POST /coordinator/stages/<id>/lock-for-review/`, w API
`POST /api/submissions/stages/<id>/lock-for-review/`): najnowsza oddana wersja każdej pary
(uczestnik, zadanie) dostaje `LOCKED` i wchodzi do przydziału recenzentów, a etap **zostaje
otwarty** — `closed_at` się nie pojawia, okno uploadu działa dalej. Reguła wyboru wersji jest ta
sama, co przy zamknięciu etapu (`lockable_submission_ids`), więc oba przyciski nigdy nie wybiorą
innego zbioru prac: wersja odrzucona przez antywirusa jest pomijana na rzecz wcześniejszej, a wersja
w trakcie skanu zostaje skanowi. Operacja jest idempotentna (`locked: 0`, nie błąd) i na etapie już
zamkniętym nie ma po prostu czego robić. Audyt: `stage.locked_for_review` z licznikiem.

Na karcie etapu stoją dwa liczniki, po których widać, czy jest co blokować: **oddane
(niezablokowane)** i **w ocenie**. Pojedynczą pracę wciąga do oceniania przycisk **„Zablokuj do
oceny”** w tabeli „Rozwiązania i oceny” (`POST /coordinator/submissions/<id>/lock-for-review/`,
w API `POST /api/submissions/<id>/lock-for-review/`, audyt `submission.locked_for_review`). Odmowy
mają kody: `NOT_LATEST_VERSION` (jest nowsza wersja — do oceniania wchodzi wyłącznie najnowsza)
i `SUBMISSION_NOT_LOCKABLE` (praca nie jest „oddana”: trwa skan albo jest już w ocenie).

**Nowa wersja unieważnia rozpoczętą ocenę.** Skoro okno uploadu zostaje otwarte, uczestnik może
wysłać poprawkę pracy, którą komitet już czyta — i wtedy wygrywa uczestnik. Przy przyjęciu nowej
wersji (`grading.services.supersede_earlier_versions`) każda wcześniejsza wersja w stanie `LOCKED`,
`IN_REVIEW`, `MODERATION` albo `GRADED_PROVISIONAL`:

1. traci recenzje — wszystkie nieanulowane, **także wystawione**, przechodzą w `CANCELLED` z powodem
   `SUPERSEDED` (`Review.cancel_reason`); punkty i komentarze zostają jako historia,
2. traci ocenę końcową — `FinalGrade` jest kasowany (audyt `grade.withdrawn`, powód `SUPERSEDED`),
3. wraca do `SUBMITTED` — wiersz zostaje w historii, ale przestaje się liczyć; do oceniania wchodzi
   nowa wersja, którą trzeba zablokować ponownie.

Audyt całości: `submission.superseded` z `{old_id, new_id, cancelled_reviews, grade_withdrawn}`.
Uczestnik widzi przy uploadzie ostrzeżenie „Ta praca jest już w ocenie. Wysłanie nowej wersji
anuluje dotychczasową ocenę – zostanie oceniona od nowa”, a recenzent w panelu (sekcja „Recenzje
anulowane” i ekran recenzji) powód: „Uczestnik wysłał nową wersję rozwiązania…” zamiast
„Koordynator odebrał Ci tę pracę”. Praca **finalna, w reklamacji albo z ogłoszonymi wynikami** jest
poza zasięgiem tej reguły: upload kończy się wtedy `409 SUBMISSION_FINALISED`, zamiast po cichu
skasować rozstrzygnięcie, które uczestnik i komisja odwoławcza już znają (stan nieosiągalny przy
otwartym etapie — warunek jest bezpiecznikiem).

#### Przydziały ręczne

Automat równoważy obciążenie, ale nie zna podziału kompetencji w komitecie. Ekran
`/coordinator/stages/<id>/assignments/` („Przydziały ręczne”, obok „Przydziel recenzentów” na
karcie etapu) dokłada do niego dwa narzędzia:

1. **Zadania → recenzenci z góry.** Reguła „zadanie 3 sprawdza Kowalski” obowiązuje wszystkie
   rozwiązania tego zadania: prace już zablokowane dostają recenzenta od razu (w chwili dodania
   reguły), prace, które wejdą do oceniania później – przy najbliższym „Przydziel recenzentów”.
   Recenzenci z reguł zajmują miejsca z „recenzentów na pracę”, a automat dobiera wyłącznie resztę;
   gdy reguł jest więcej niż miejsc, przydzielani są wszyscy (decyzja organizatora wygrywa
   z liczbą w formularzu). **Usunięcie reguły nie kasuje recenzji**, które już z niej powstały –
   pojedynczy przydział cofa się przyciskiem „Cofnij”.
2. **Rozwiązania.** Tabela prac w obiegu oceniania: w jednym wierszu stoi uczestnik (kod i nazwisko,
   odnośnik do karty uczestnika), zadanie (odnośnik do karty zadania), wersja z plikiem i liczbą
   stron, status, recenzenci, ocena końcowa i czynności. Wszystko zmienia się **w wierszu**: punkty
   recenzji i ocena końcowa pod rozwijanym „Zmień punkty” / „Ocena końcowa”, recenzent listą wyboru
   z przyciskiem „Przydziel”, praca oddana przyciskiem „Zablokuj do oceny”, a odebranie – „Cofnij”
   albo „Odbierz” (patrz niżej).

Panel reguł stoi na górze **zwinięty**, z licznikiem w podsumowaniu: ustawia się je raz na etap,
a tabelę prac czyta się codziennie.

**Filtry i strony.** Nad tabelą stoją liczniki kroków obiegu (oddane / do przydziału / w ocenie /
moderacja / ocenione) policzone dla **całego etapu** – po jednej, najnowszej wersji na parę
(uczestnik, zadanie), tak jak liczy je tabela. Każdy licznik jest zarazem przełącznikiem filtra;
kliknięcie licznika już włączonego zdejmuje filtr. Filtry składają się ze sobą i są w adresie, więc
przefiltrowaną tabelę da się wysłać odnośnikiem: `?q=` (kod uczestnika albo nazwisko), `?problem=`,
`?status=` (`submitted`, `to_assign`, `in_review`, `moderation`, `graded`), `?reviewer=`
(prace, które ta osoba trzyma w ręku – recenzja nieanulowana), `?page=`. Wierszy jest **100 na
stronę**; filtr i numer strony wracają też po każdej akcji, więc poprawka punktów nie wyrzuca
z przefiltrowanej listy.

**Czynności zbiorcze.** Zaznaczenie wierszy jest jedno i obsługuje dwie rzeczy: paczkę ZIP
(„Pobierz zaznaczone”) i pasek czynności `POST /coordinator/stages/<id>/assignments/bulk/` z polem
`action`: `assign` i `unassign` (dla recenzenta wybranego z listy obok) oraz `lock`. Każda praca idzie
przez ten sam serwis, co pojedynczy wiersz, więc reguły domenowe obowiązują identycznie, a odmowa
dotycząca jednej pracy **nie przerywa reszty** – ląduje w komunikacie jako powód z listą kodów
(„Pominięto 3: …”). Pasek jest zwykłym formularzem: działa bez JavaScriptu.

**Historia przy wierszu.** Rozwijane „Historia” pokazuje ostatnie 20 wpisów audytu tej pracy **i jej
recenzji** w jednym ciągu – odpowiedź na pytanie „dlaczego ta praca ma tyle punktów” bez
przechodzenia do przeglądarki audytu. Wpisy dla całej strony pobiera jedno zapytanie; audyt z zasady
nie zawiera danych osobowych, więc jedyną osobą we wpisie jest wykonawca.

Ekran działa w całości **bez JavaScriptu** (strict CSP nie dopuszcza skryptów inline): `assignments.js`
dokłada wyłącznie licznik zaznaczenia i wysyłkę przydziału zaraz po wyborze recenzenta (z pytaniem
o potwierdzenie), a `select-all.js` – kratkę „zaznacz wszystkie”.

Konflikt interesów obowiązuje w obu narzędziach: recenzent z województwa uczestnika nie dostanie jego
pracy na etapie wojewódzkim, nawet gdy wskazuje go reguła – taka praca trafia na listę pominiętych
z powodem `RULE_REVIEWER_CONFLICT`. Województwo członka komitetu jest opcjonalne: recenzent, który go
nie ma, może oceniać prace ze wszystkich województw. Każda czynność zostawia
wpis w audycie (`review.rule_added`, `review.rule_removed`, `review.assigned_manually`,
`review.unassigned`, `review.withdrawn`). Odpowiedniki w API: `POST /api/grading/stages/<id>/problem-rules/`,
`DELETE /api/grading/stages/<id>/problem-rules/<rule_id>/`,
`POST /api/grading/submissions/<id>/assign/`, `POST /api/grading/reviews/<id>/unassign/`.

#### „Odbierz”: koordynator zabiera recenzentowi pracę

Przycisk przy każdej recenzji („Cofnij” dla nietkniętego przydziału, „Odbierz” dla szkicu i dla
oceny już wystawionej) przestawia recenzję w `CANCELLED`. Rekord zostaje — punkty i komentarze są
historią — ale **przestaje się liczyć**: `_settle_round_one` pomija recenzje anulowane, a recenzent
nie może już swojej oceny poprawić (widzi komunikat „Koordynator odebrał Ci tę pracę”).

Odebranie **wystawionej** oceny rundy 1 zdejmuje ocenę uzgodnioną konsensusem (audyt
`grade.withdrawn`, powód `REVIEW_WITHDRAWN`) i zawraca pracę do `IN_REVIEW`, żeby dało się
przydzielić kogoś na miejsce odebranego recenzenta. Runda 1 jest wtedy rozstrzygana od nowa
**tylko wtedy, gdy zostają co najmniej dwie recenzje**: z jedną pozostałą powstałaby „ocena
uzgodniona” z jednego głosu, a praca w `GRADED_PROVISIONAL` nie przyjmuje już żadnego przydziału —
koordynator nie miałby jak dać jej następnej osobie.

Czego „Odbierz” nie ruszy (odmowa z kodem i komunikatem): etapu z **ogłoszonymi wynikami**
(`RESULTS_PUBLISHED`), pracy w reklamacji albo finalnej (`SUBMISSION_CLOSED`) oraz oceny końcowej
rozstrzygniętej przez człowieka — moderacja, trzeci recenzent, korekta koordynatora, decyzja
reklamacyjna (`GRADE_DECIDED`). Takie oceny zmienia się formularzem „Ocena końcowa”, z obowiązkowym
uzasadnieniem. Ponowne odebranie tej samej recenzji to `ALREADY_CANCELLED`.

#### Poprawienie własnej oceny (recenzent)

Wystawiona recenzja nie jest już nieodwracalna. W `/review/<id>/` recenzent, który oddał ocenę,
widzi formularz wypełniony swoimi punktami i komentarzami oraz przycisk **„Popraw ocenę”**
(`POST /review/<id>/revise/`, w API `POST /api/grading/reviews/<id>/revise/` z tym samym ładunkiem,
co `submit/`). `submitted_at` zostaje nietknięte — chwila pierwszego wystawienia oceny jest faktem
procesowym — a poprawka dopisuje `revised_at` i wpis `review.revised` (wartość przed i po).

Po poprawce runda 1 jest rozstrzygana **od nowa**: ocena uzgodniona konsensusem znika
(`grade.withdrawn`, powód `REVIEW_REVISED`), praca wraca do `IN_REVIEW` i dopiero wtedy zapada
rozstrzygnięcie zgodne z nowym stanem — znów zgodne oceny dają `FinalGrade(CONSENSUS)`, rozjazd
kieruje pracę do moderacji. Gdy poprawka **kończy** rozjazd, wiszący przydział trzeciego recenzenta
jest anulowany (`review.cancelled`, powód `REVIEW_REVISED`), żeby nie wisiał w jego kolejce.
Poprawka oceny rundy 2 (rozjemczej) przepisuje ocenę końcową w miejscu — tryb i autor się nie
zmieniają, zmieniają się punkty i uzasadnienie (`grade.updated`).

Poprawić **nie wolno** — ekran pokazuje wtedy powód zamiast formularza:

| Sytuacja | Kod |
| --- | --- |
| koordynator odebrał pracę (recenzja `CANCELLED`) | `REVIEW_CANCELLED` |
| ocena nie została jeszcze wystawiona (jest zwykła ścieżka) | `REVIEW_NOT_SUBMITTED` |
| wyniki etapu są ogłoszone | `RESULTS_PUBLISHED` |
| praca w reklamacji albo finalna | `SUBMISSION_CLOSED` |
| ocenę rozstrzygnął człowiek: moderacja, trzeci recenzent, korekta, reklamacja | `GRADE_DECIDED` |

#### Panel recenzenta: kolejka pracy i ekran oceny

Panel ma dwa ekrany i każdy odpowiada na inne pytanie. `/review/` odpowiada na „co mam dziś
zrobić”, `/review/<id>/` — na „ile punktów ma ta praca”. Oba są kompletne **bez JavaScriptu**
(strict CSP, żadnego skryptu inline); skrypty dokładają wygodę, nigdy treść.

**Kolejka (`/review/`)** jest kolejką roboczą, a nie tabelą wszystkiego. Nad nią stoi pasek
podsumowania z trzema liczbami — **ile zostało**, **najbliższy termin** i **łączny czas pracy** —
oraz przycisk „Pobierz moje prace (ZIP)”. Niżej są cztery zakładki po stanie recenzji:
**„Do zrobienia”** (`ASSIGNED`), **„W toku”** (`DRAFT`), **„Wystawione”** (`SUBMITTED`)
i **„Anulowane”** (`CANCELLED`). Wewnątrz zakładki prace są dalej pogrupowane po etapie i zadaniu
z licznikiem „6 z 12 do zrobienia” — bo tak wygląda robota: jedno zadanie w wielu pracach.
Porządek wierszy idzie **po terminie**, a nie po chwili przydziału: zaczyna się od tego, co
przepadnie najwcześniej. Wiersz niesie kod pracy, odznakę terminu („po terminie”, „dziś”,
„za 3 dni”), postęp („nie zaczęta”, „szkic z punktami”, „otwarta …”), marker zgłoszonego problemu
i przycisk „Otwórz”; w zakładce „Anulowane” zamiast przycisku stoi **powód** i to jest tam
najważniejsza kolumna (odebranie pracy przez koordynatora to co innego niż nowa wersja od
uczestnika). Zwinięte „Jak oceniać — w pięciu zdaniach” jest pierwszą pomocą dla osoby, która
ocenia po raz pierwszy.

Wszystkie cztery zakładki wypełnia **serwer**; `static/js/reviewer-layout.js` chowa nieaktywne
i dokłada role `tab`/`tabpanel` (przełączanie strzałkami). Bez skryptu strona jest listą czterech
sekcji z odnośnikami do nich — nic nie znika. „Zakładka”, która bez JavaScriptu nie pokazuje
treści, byłaby ukryciem danych, a nie uporządkowaniem ich.

**Ekran oceny (`/review/<id>/`)** jest dwiema kolumnami: po lewej (≈65 %) rozwiązanie, po prawej
(≈35 %) panel oceny. Panel jest powyżej 1000 px **przyklejony** (`position: sticky`) i to jest cała
korzyść z tego układu — przy ośmiostronicowym PDF-ie formularz był dotąd tam, gdzie recenzent już
dawno nie patrzył. Kolejność w panelu odpowiada kolejności czynności: nagłówek (kod, etap, runda,
wersja, termin, „5 z 18 w tym zadaniu” z odnośnikami do sąsiednich prac) → wzorcówka w `<details>`
→ rubryka albo skala → komentarz dla uczestnika (z szablonami tuż pod polem) → komentarz wewnętrzny
→ **„Zapisz szkic”** i **„Wystaw ocenę”**. Wszystko, co nie jest wystawianiem oceny — porównanie
ocen, zgłoszenie problemu, własne szablony, czas pracy, skróty — stoi pod formularzem, zwinięte.
Poniżej 1000 px kolumny układają się jedna pod drugą, a do panelu prowadzi przyklejony u dołu
odnośnik **„Oceń”** (zwykła kotwica, działa bez skryptu).

Nad podglądem stoi pasek adnotacji: **„Dodaj zaznaczenie”** (da się wyłączyć, gdy recenzent chce
tylko czytać), **„Ukryj adnotacje”** i filtr **publiczne / wewnętrzne**. Lista adnotacji pod
podglądem ma przy każdej pozycji „przejdź” (przewija podgląd na jej stronę) i „usuń”. Skróty
klawiaturowe są udokumentowane tam, gdzie działają — w `<details>` „Skróty klawiaturowe”:
<kbd>n</kbd> / <kbd>p</kbd> to następna i poprzednia praca w serii, <kbd>s</kbd> zapisuje szkic.
Skróty milczą w trakcie pisania w polu tekstowym.

Przy rubryce `reviewer-layout.js` pokazuje **podgląd sumy** z kryteriów i mówi, czy mieści się
w skali zadania. Autorytetem zostaje serwer (`apps.grading.rubric`) — licznik tylko mówi to samo
wcześniej, żeby recenzent nie dowiadywał się o wyjściu poza skalę dopiero z komunikatu po wysłaniu
formularza. Arkusz obu ekranów to `backend/static/css/reviewer.css` (dołączany tylko na
`/review/…`, bez ani jednego koloru spoza tokenów `app.css`, więc tryb ciemny i wysoki kontrast
działają bez dodatkowych reguł).

#### Warsztat recenzenta: rubryka, wzorcówka, porównanie ocen, serie prac i terminy

Pięć rzeczy, o które prosił organizator. Wszystkie działają bez JavaScriptu (strict CSP) i żadna
nie zmienia tego, co widzi uczestnik.

**1. Rubryka oceniania (kryteria zadania).** Koordynator wpisuje kryteria przy zadaniu
(`/coordinator/problems/<id>/edit/`, pole „Rubryka oceniania”) — po jednym w wierszu, w postaci
`punkty;tytuł;opis` (opis nieobowiązkowy):

```text
2;Pomysł;jak uczestnik podszedł do zadania
4;Wykonanie;staranność rachunków i uzasadnień
```

Zadanie z rubryką pokazuje recenzentowi w `/review/<id>/` po jednym polu punktów (0…maksimum
kryterium) i komentarzu na kryterium — **zamiast** listy ocen ze skali. Sumę liczy serwer i to ona
trafia do `Review.score` (źródło prawdy zostaje jedno); punkty cząstkowe lądują w `Review.rubric`
(lista `{criterion_id, points, comment}`). Suma **musi** należeć do skali zadania (albo etapu, gdy
zadanie nie ma własnej): 4 punkty przy skali 0/2/5/6 kończą się odmową
`RUBRIC_TOTAL_NOT_IN_SCALE` z listą dopuszczalnych wartości — system **nie zaokrągla**, bo to
byłaby zmiana decyzji recenzenta. Szkic przyjmuje rubrykę niekompletną; ocena — nie
(`RUBRIC_INCOMPLETE`). Poprawienie tytułu kryterium nie zrywa powiązania z zapisanymi punktami
(kryteria są aktualizowane w miejscu, nie kasowane i tworzone od nowa). W API rubryka jest polem
`rubric` w `ReviewSerializer` oraz w ładunku `PATCH /api/grading/reviews/<id>/`,
`POST …/submit/` i `POST …/revise/`; jej brak znaczy „bez rubryki”, więc starsi klienci działają
bez zmian. Zadanie bez kryteriów ocenia się dokładnie jak dotąd.

**2. Rozwiązanie wzorcowe i uwagi dla recenzentów.** Ten sam ekran zadania ma pola „Rozwiązanie
wzorcowe (PDF)” (`Problem.model_solution_pdf`) i „Uwagi dla recenzentów”
(`Problem.reviewer_notes`). Plik leży na **prywatnym** storage (jak treść zadania) i nie ma
publicznego adresu: wydaje go wyłącznie `GET /review/problems/<id>/model-solution/` aktywnemu
członkowi komitetu albo koordynatorowi. Uczestnik dostaje 403 — i w odróżnieniu od treści zadania
wzorcówka **nie staje się jawna po `opens_at`**. W panelu recenzenta jest zwinięta w `<details>`
„Rozwiązanie wzorcowe i uwagi dla recenzentów”.

**3. Porównanie ocen po odsłonięciu.** Dopóki komplet ocen rundy 1 nie jest wystawiony, recenzent
nie widzi cudzych punktów (ocena ślepa). Gdy wszystkie są wystawione — albo praca jest
w moderacji/oceniona — na stronie oceny pojawia się sekcja **„Porównanie ocen”**: punkty drugiej
strony, komentarz dla uczestnika i różnica ze znakiem. Tożsamość zostaje ukryta („Recenzent B”,
litery nadawane po `(punkty, id)`, tak jak w materiale rozjemczym rundy 2). Pod spodem jest wątek
krótkich notatek (`grading.ReviewNote`, do 1000 znaków): piszą w nim **recenzenci tej pracy**,
czytają oni i koordynator (pulpit, sekcja „Moderacja”). Wątek zamyka się razem ze sprawą — praca
`FINAL` albo etap z ogłoszonymi wynikami to `SUBMISSION_FINAL` / `RESULTS_PUBLISHED`, a przed
odsłonięciem ocen `NOT_REVEALED`. Osobna strona z tym samym materiałem: `/review/<id>/compare/`;
dopisanie notatki — `POST /review/<id>/notes/` (audyt `review.note_added`, w `diff` sama długość).

**4. Prace jednego zadania seriami.** Strona oceny ma odnośniki **„← Poprzednia praca”** /
**„Następna praca →”** i licznik „5 z 18 w tym zadaniu”. Seria to własne otwarte recenzje
(`ASSIGNED`/`DRAFT`) **tego samego zadania**, uporządkowane po identyfikatorze przydziału; bieżąca
recenzja zostaje w serii także po wystawieniu oceny, żeby po powrocie było widać, co dalej.
Odnośniki stoją w nagłówku panelu oceny, czyli tam, skąd się do następnej pracy przechodzi —
po zapisaniu oceny, z dołu prawej kolumny. Kolejka `/review/` jest pogrupowana po etapie i zadaniu
wewnątrz zakładek, z licznikiem „6 z 12 do zrobienia”.

**5. Terminy recenzji i przypomnienia.** Etap ma pole **„Dni na jedną recenzję”**
(`Stage.review_deadline_days`, domyślnie 14, edytowalne także po zamknięciu etapu — bo ocenianie
zaczyna się właśnie wtedy). Przy przydziale recenzja dostaje własny termin `Review.due_at`:

> chwila przydziału + `review_deadline_days`, **przycięte** do `review_deadline_at` etapu, jeżeli
> ten wypada wcześniej — ale nigdy w przeszłość: praca przydzielona po terminie etapu (dosyłka,
> zastępstwo) dostaje pełne okno, bo termin „wczoraj” nie jest terminem. Sufit obejmuje zarazem
> okno reklamacji, bo `review_deadline_at ≤ appeal_window_opens_at` pilnuje constraint w bazie.

Termin jest **zapisywany**, a nie liczony przy każdym odczycie: późniejsza zmiana ustawień etapu nie
przesuwa terminów już przyznanych. Cały przebieg „Przydziel recenzentów” dostaje jeden termin, a
komunikat po przydziale podaje go wprost („Termin recenzji: …”). Kolejka recenzenta sortuje prace
po terminie i odznacza wiersze **„po terminie”**, **„dziś”** albo **„za N dni”**, a pasek
podsumowania podaje najbliższy termin z całej kolejki. Beat `apps.grading.tasks.remind_overdue_reviews` (raz na dobę,
`CELERY_BEAT_SCHEDULE`) wysyła **jeden list dziennie na recenzenta** z pracami po terminie i tymi
z terminem w ciągu 2 dni (kody publiczne prac, bez danych osobowych); powtórkom zapobiega
`Review.reminded_at`, a do audytu trafia `review.reminder_sent` z samym licznikiem. Prace, które
wyszły z oceniania, nie są przypominane.

#### Szablony komentarzy

Ocena trzydziestu prac z jednego zadania to w praktyce trzydzieści razy te same trzy zdania.
Szablon (`grading.CommentSnippet`) jest gotowym akapitem **do wstawienia i poprawienia** — system
nigdy nie wstawia go sam i nigdy nie zmienia tego, co recenzent już napisał.

Dwie drogi, jeden model. **Koordynator** wpisuje szablony wspólne przy zadaniu
(`/coordinator/problems/<id>/edit/`, pole „Szablony komentarzy dla recenzentów”) — po jednym
w wierszu, w postaci `tytuł;treść`, tak samo jak rubrykę:

```text
Brak jednostek;Wynik jest poprawny, ale nie podałeś jednostek.
Uzasadnienie;Brakuje uzasadnienia przejścia granicznego — bez niego wynik jest przypadkowy.
```

Średnik rozdziela **raz**, więc treść wolno pisać ze średnikami. Zapis aktualizuje szablony
w miejscu i **nie rusza** prywatnych notatników recenzentów (`owner IS NULL` w filtrze).

**Recenzent** dopisuje własne szablony wprost z ekranu oceny (`POST /review/snippets/`, kasowanie
`POST /review/snippets/<id>/delete/`) — z zaznaczeniem „tylko dla tego zadania” albo bez niego
(szablon ogólny, dostępny przy każdej pracy). Limit to 50 własnych pozycji (`SNIPPET_LIMIT`).
Widoczność: wspólne komitetu **przed** własnymi; cudzego prywatnego szablonu nie widzi nikt — ani
inny recenzent, ani koordynator, a próba skasowania go kończy się 404, nie 403.

Na ekranie oceny szablony stoją w **dwóch** miejscach i to nie jest powtórzenie. Zwinięte
„Szablony komentarzy (N)” tuż pod polem „Komentarz dla uczestnika” jest podpowiedzią **do tego
pola**: lista tytułów, treści i przycisk „Wstaw”. Zakładanie i kasowanie własnych szablonów jest
niżej, pod formularzem, w „Moje szablony komentarzy” — wstawienie zdania dotyczy **tej** pracy,
a założenie szablonu dwudziestu następnych. Rozdział ma też twardy powód techniczny: popover stoi
wewnątrz formularza oceny, a formularza nie wolno zagnieżdżać w formularzu.

Obie sekcje działają **bez JavaScriptu**: treść każdego szablonu stoi na ekranie do skopiowania.
`static/js/review-snippets.js` (nonce, delegacja zdarzeń, dane w `data-*`) dokłada przycisk
**„Wstaw”**, który dopisuje treść na końcu pola „Komentarz dla uczestnika” — nigdy nie nadpisuje
tego, co tam jest. Audyt: `snippet.problem_set` (tylko przy
faktycznej zmianie), `snippet.added`, `snippet.deleted` — w `diff` tytuł i długość, nigdy treść.

#### Czas pracy nad recenzją

Planowanie obciążenia komitetu opierało się dotąd na wyczuciu („zadanie 3 idzie wolno”). Licznik
(`grading.ReviewWorkLog`) zamienia je w liczbę: **„Czas pracy: 1 h 12 min”** przy recenzji
(`/review/<id>/`) i kolumna „Czas pracy” przy recenzencie na ekranie postępu etapu
(`/coordinator/stages/<id>/progress/`).

> **Prywatność.** Cel pomiaru to **planowanie obciążenia** komitetu — nic więcej. W bazie są trzy
> znaczniki czasu i jedna suma sekund. Nie zapisujemy ani tego, co recenzent pisał, ani gdzie
> klikał, ani kiedy dokładnie przerywał: ruch myszy i naciśnięcia klawiszy są wyłącznie sygnałem
> „ktoś jeszcze pracuje”, nie opuszczają przeglądarki i nigdzie nie są przechowywane. To wystarczy
> na pytanie „ile godzin zajmuje ocena zadania 3” i jest za mało na ocenę człowieka.

Jak liczy się czas. `static/js/review-worklog.js` wysyła pusty sygnał życia co **60 s** na
`POST /review/<id>/heartbeat/` (`fetch` z tokenem CSRF z ciasteczka; `navigator.sendBeacon` przy
chowaniu karty, żeby ostatnie minuty nie ginęły przy zamknięciu zakładki). Po **5 minutach** bez
ruchu myszy i klawiatury skrypt przestaje wysyłać cokolwiek. Serwer **nie przyjmuje od klienta
żadnej długości** — dolicza `min(czas od ostatniego sygnału, 90 s)`. Sufit jest sednem pomiaru:
bez niego karta zostawiona na noc dopisałaby osiem godzin, a przeglądarka ze zwolnionym timerem
w karcie w tle gubiłaby minuty. Pierwszy sygnał zakłada licznik i **nic nie dolicza** (nie wiadomo
jeszcze, od kiedy trwa praca); sygnał „z przeszłości” (przestawiony zegar, spóźniony `sendBeacon`)
nie odejmuje czasu. Cudza recenzja to 404. Licznik startuje wyłącznie przy recenzji, którą wolno
jeszcze zapisać — mierzenie czasu oglądania recenzji zamkniętej nie jest mierzeniem pracy.

Brak pomiaru (recenzja sprzed wprowadzenia licznika, ocena zrobiona z wydruku, wyłączony
JavaScript) jest pokazywany jako **„brak pomiaru”**, a nie jako „0 min” — zmierzone zero to co
innego niż brak pomiaru. Suma na ekranie postępu obejmuje **wszystkie** recenzje etapu, także
anulowane: czas nad pracą, którą koordynator potem odebrał, też był czasem pracy.

#### Adnotacje na obrazach i w kodzie

Rozwiązania w PDF-ie i **zdjęcia** (JPEG) mają warstwę prostokątów: `static/js/review-annotations.js`
rysuje stronę na `<canvas>` (pdf.js) albo zdjęcie w `<img>` — o wyborze decyduje typ MIME wyliczony
przez serwer przy uploadzie, nie rozszerzenie nazwy od uczestnika. Adnotacja jest w obu przypadkach
tym samym wpisem `{page, rect, text, public}` we współrzędnych ułamkowych; zdjęcie jest „stroną 1 z 1”.

Rozwiązania oddane jako **kod** (`.py`, `.ipynb`) nie mają czego zaznaczać prostokątem — recenzent
mówi o nich „linia 42”. Ekran oceny pokazuje więc listing renderowany **po stronie serwera**
(`apps.grading.code_view`): ponumerowane wiersze w `<pre>`, podświetlone tam, gdzie wisi uwaga.
Notatnik jest czytany jako JSON (bez `nbformat`/`nbconvert`), a jego **komórki kodu** są sklejane
nagłówkami `# --- komórka N ---`; komórki tekstowe i wyniki poprzednich uruchomień są pomijane.
Odczyt jest ograniczony do **400 kB** (nadmiar jest obcinany z widoczną adnotacją w listingu)
i dekodowany jako UTF-8 z podmianą znaków, więc plik w cp1250 jest czytelny zamiast pustej strony.

> **Kodu uczestnika nie uruchamiamy.** Plik jest odczytywany jako bajty, dekodowany i wypisywany
> jako tekst — nigdy importowany, nigdy wykonywany, nigdy przekazywany do podprocesu. Żadnej
> piaskownicy w systemie nie ma i nie jest przewidziana.

Uwagi do linii są drugim dopuszczalnym kształtem wpisu w `Review.annotations`: `{line, text, public}`
obok istniejącego `{page, rect, text, public}`. O kształcie rozstrzyga obecność klucza `line`, więc
adnotacje zapisane wcześniej i klienci API, którzy o uwagach do linii nie wiedzą, działają bez zmian.
Dopisanie idzie na `POST /review/<id>/line-note/` i przechodzi przez `save_draft`, czyli przez tę
samą bramkę, co szkic (`_assert_review_open`): do recenzji wystawionej, anulowanej ani do pracy poza
ocenianiem nie dopisze się tędy nic. Audyt: `review.line_note_added` (numer linii, jawność, długość —
nigdy treść). `static/js/review-code-notes.js` wpisuje numer klikniętej linii do formularza; bez
skryptu numer wpisuje się ręcznie i wszystko działa tak samo.

Uwaga oznaczona **„Pokaż uczestnikowi”** trafia po ogłoszeniu wyników na stronę informacji zwrotnej
(`/me/stages/<id>/feedback/`) jako „linia N: treść”, obok komentarza recenzenta. Numer linii odnosi
się do pliku tak, jak uczestnik go wysłał — w notatniku liczonego po sklejeniu samych komórek kodu,
dokładnie tak, jak widział go recenzent.

#### Zgłoszenie problemu z pracą

Recenzent, któremu trafi się skan nie do odczytania albo rozwiązanie zupełnie innego zadania, miał
dotąd jedną drogę: pocztę do koordynatora. Zgłoszenie (`grading.WorkIssue`) jest drogą **wewnątrz
systemu** — koordynator widzi je na własnym ekranie, z pracą i etapem w ręku, a ślad decyzji zostaje
przy recenzji, a nie w cudzej skrzynce.

Przycisk **„Zgłoś problem”** stoi na stronie oceny (`POST /review/<id>/issues/`). Rodzaje: praca
nieczytelna, rozwiązanie innego zadania, podejrzenie niesamodzielności, inne — plus obowiązkowy
opis (także przy „inne”). Jeden recenzent nie może mieć dwóch **otwartych** zgłoszeń do tej samej
recenzji (`ISSUE_ALREADY_OPEN`); po rozstrzygnięciu wolno zgłosić ponownie — to już inna sprawa.

**Zgłoszenie nie blokuje oceniania** i to jest decyzja, nie przeoczenie. Recenzent może uważać pracę
za nieczytelną i mimo to wystawić ocenę, jaką da się obronić; zablokowanie formularza zamieniłoby
sygnał w ultimatum i wypchnęło część komitetu z powrotem do poczty. Panel pokazuje więc baner
z otwartym zgłoszeniem, a lista `/review/` — marker „zgłoszony problem” przy wierszu.

Koordynator obsługuje kolejkę na `/coordinator/issues/`: otwarte na górze, filtr etapu jako
`?stage=<id>` (adres z filtrem ma dać się zapisać i wysłać dalej), domyślnie cała edycja. Przy
każdym otwartym zgłoszeniu stoją **skróty** do tego, co się po nim zwykle robi: odnośnik do
przydziałów etapu i **„Odbierz pracę”** (`POST /coordinator/issues/<id>/unassign/`), które woła
istniejące `unassign_reviewer` — te same bramki i ten sam wpis audytowy, co przycisk na ekranie
przydziałów. Odebranie pracy **nie zamyka** zgłoszenia: to czynność, a nie rozstrzygnięcie sprawy.

**„Rozwiąż”** (`POST /coordinator/issues/<id>/resolve/`) wymaga zdania uzasadnienia
(`ISSUE_RESOLUTION_REQUIRED`) — zamknięte zgłoszenie bez ani jednego słowa nie mówi w aktach nic
poza tym, że ktoś kliknął przycisk. Powtórne rozstrzygnięcie to `ISSUE_ALREADY_RESOLVED`, żeby
drugi koordynator nie nadpisał cudzego uzasadnienia własnym. Pulpit ma kafelek **„Zgłoszone
problemy: N”** prowadzący do kolejki (licznik obejmuje etapy bieżącej edycji, czyli zakres ekranu,
na który prowadzi). Audyt: `issue.opened` i `issue.resolved` — rodzaj, praca i długość opisu,
nigdy jego treść.

#### Korekta ocen przez koordynatora

Ten sam ekran („Przydziały i oceny”) pozwala poprawić **każdą** ocenę. Dwa poziomy:

1. **Punkty pojedynczej recenzji** – przy każdym recenzencie stoi lista wartości ze skali etapu
   i przycisk „Zapisz”. Działa niezależnie od stanu recenzji: koordynator poprawia ocenę już
   wystawioną i wpisuje ocenę za recenzenta, który jej nie oddał (recenzja przechodzi wtedy
   w „wystawiona”, a do komentarza wewnętrznego trafia adnotacja `[koordynator]`). Po zapisie
   system ponownie rozstrzyga rundę 1 – zgodne oceny dają `FinalGrade(CONSENSUS)`, rozjazd kieruje
   pracę do moderacji. Praca, która ma już ocenę końcową, nie jest przeliczana: tam służy punkt 2.
2. **Ocena końcowa** – formularz „Ocena końcowa” (punkty + **obowiązkowe** uzasadnienie, min. 10
   znaków) zapisuje `FinalGrade` w trybie `OVERRIDE` („korekta koordynatora”). Działa także dla
   pracy, której nikt nie recenzował – wtedy wpisana wartość *jest* oceną końcową, a niedokończone
   recenzje tej pracy (przydzielone i szkice) zostają anulowane, żeby nie wisiały w kolejkach.
   Wystawione recenzje zostają jako historia. Praca przechodzi do `GRADED_PROVISIONAL`; praca już
   `FINAL` finalna zostaje.

**Ogłoszona tabela wyników nie zmienia się sama.** Snapshot publikacji jest dokumentem z chwili
ogłoszenia, więc korekta po publikacji kończy się ostrzeżeniem „Wyniki tego etapu są już ogłoszone –
zmiana pojawi się dopiero po ponownym przeliczeniu i publikacji” (API zwraca `results_stale: true`).
Wejdzie do wyników po „Przelicz wyniki” i „Opublikuj wyniki” (6.6).

Audyt: `review.score_set_by_coordinator` (wartość przed i po) oraz `grade.overridden` (punkty
i tryb przed zmianą, liczba anulowanych recenzji, flaga nieaktualnych wyników). API:
`POST /api/grading/reviews/<id>/score/` `{score, rationale?}` i
`POST /api/grading/submissions/<id>/final-grade/` `{score, rationale}` – oba tylko dla koordynatora.

### 6.5 Przeniesienie treści zadań na prywatny storage (`migrate_statements`)

Pliki `Problem.statement_pdf` zapisane przed T-09 leżą na storage `default` (produkcyjnie: publiczny
bucket `public-media`). Idempotentna komenda przenosi je do `private_media` (prefiks
`problem-statements/` w prywatnym buckecie) i **nie kasuje** źródeł:

```bash
docker compose exec web python manage.py migrate_statements --dry-run   # plan
docker compose exec web python manage.py migrate_statements             # wykonanie
```

Po migracji treść zadania serwuje wyłącznie widok aplikacji
(`GET /api/competitions/problems/<id>/statement/`, 404 przed `Stage.opens_at`).

### 6.6 Publikacja wyników

1. `/coordinator/` → **„Przelicz wyniki (podgląd)”** – pełna tabela z danymi osobowymi, widoczna
   wyłącznie dla koordynatora, niczego nie ogłasza.
2. Po zamknięciu okna reklamacji i rozstrzygnięciu wszystkich spraw: **„Opublikuj wyniki”**
   z trybem anonimizacji (`CODE` – kod uczestnika; `INITIALS_SCHOOL` – inicjały i szkoła, z progiem
   k-anonimowości 3; `FULL` – tylko finał, tylko laureaci, tylko za zgodą).
3. Ponowna publikacja nadpisuje snapshot tego samego etapu i zostawia wpis w audycie.

### 6.7 Import treści starej strony

Treści serwisu WordPress „Olimpiada Kwantowa” są przeniesione do CMS-a dwiema komendami. Obie są
idempotentne, obie są **narzędziami importującymi**, a nie trybem pracy redakcyjnej: powtórny
przebieg nadpisuje treść stron tym, co jest w plikach źródłowych, więc kasuje poprawki wpisane
w międzyczasie w `/cms/`.

Kolejność przy wdrożeniu: `migrate` → `seed_regulamin` → `seed_legacy_content` → `seed_partners`
→ `seed_edition_kwantowa [--sync-dates]`. `seed_partners` musi stać **po** `seed_legacy_content`,
bo dopisuje się do strony `/partnerzy/`, którą tamta komenda zakłada; odwrotna kolejność kończy się
komunikatem o braku strony, a nie połową wgranych logotypów. Odwrotna zależność już nie istnieje:
`seed_legacy_content` nie zeruje listy partnerów (kiedyś zerowało, więc uruchomione jako ostatnie
kasowało logotypy). Druga komenda
zakłada sekcję `/dokumenty/`, przenosi pod nią dokumenty stojące jeszcze pod stroną główną
(zachowując ich identyfikatory, rewizje i odnośniki wewnętrzne), ustawia kolejność menu i tworzy
przekierowania ze starych adresów — działa tak samo na świeżej bazie i na produkcyjnej.
Kolejność ma znaczenie tylko dla przekierowania `/regulamin/` → `/dokumenty/regulamin/`: tworzy je
`seed_legacy_content`, a wskazuje na stronę, którą zakłada `seed_regulamin`. Uruchomione odwrotnie
komendy dadzą ten sam serwis, tylko bez tego jednego 301 do następnego przebiegu.

```bash
# Regulamin: treść strony /dokumenty/regulamin/ z konwersji .docx plus oba pliki do pobrania
# (PDF do druku i wersja źródłowa .docx) — wszystko z apps/cms/fixtures/regulamin/.
docker compose exec web python manage.py seed_regulamin

# Strony, dokumenty, aktualności, hasło, sekcja „O Olimpiadzie” i sekcja kroków na stronie głównej,
# kolejność menu, sekcja /dokumenty/, strona /partnerzy/, usunięcie stron wycofanych
# (/jak-zaczac/, /o-olimpiadzie/) i przekierowania ze starych adresów.
docker compose exec web python manage.py seed_legacy_content

# Formularz zgody opiekuna (PDF do wydruku) z fixtures/legacy/zgoda-opiekuna.md.
# Uruchamia się go PO zmianie treści wzoru, a plik wynikowy trafia do repozytorium —
# seed wgrywa do biblioteki Wagtaila plik z repozytorium, nie składa go na produkcji.
python manage.py build_guardian_consent_pdf

# Dołożenie POJEDYNCZEJ strony na działającym serwisie — bez nadpisywania pozostałych treści
# (pełny przebieg skasowałby poprawki wpisane w /cms/ od ostatniego importu).
# Tak wgrywa się na produkcję wzór zgody opiekuna razem z formularzem PDF:
docker compose exec web python manage.py seed_legacy_content --only zgoda-opiekuna

# Logotypy partnerów i organizatora z apps/cms/fixtures/partners/ (manifest partners.json):
# obrazy do biblioteki Wagtaila, wpisy na /partnerzy/, znak fundacji do SiteSettings.
docker compose exec web python manage.py seed_partners

# Edycja „I edycja 2026/2027” z trzema etapami wg harmonogramu organizatora.
# Bez --make-current edycja NIE staje się bieżąca (na devie bieżąca zostaje edycja z seed_demo).
# --sync-dates przestawia terminy i miejsce ISTNIEJĄCYCH etapów na plan z komendy; bez tej flagi
# oś czasu utworzonego już etapu należy do koordynatora i komenda jej nie rusza.
docker compose exec web python manage.py seed_edition_kwantowa [--make-current] [--sync-dates]

# Słownik szkół ponadpodstawowych (SIO/RSPO) — dane referencyjne, nie treść redakcyjna, więc
# wdrożenie uruchamia je ZAWSZE, a nie tylko przy pierwszym. Szczegóły: 6.3b.
docker compose exec web python manage.py seed_schools
```

Źródła treści leżą w `backend/apps/cms/fixtures/legacy/*.md`; inwentarz i pełne teksty starej
strony — w `docs/import/`. Import zmienia wyłącznie strukturę (nagłówek → blok `heading`, tabela
dwukolumnowa → lista definicji, wyróżniona ramka → blok `notice`), nie brzmienie zdań organizatora.

**Pasek nawigacji po imporcie** (kolejność = kolejność rodzeństwa w drzewie, `MENU_ORDER`):
Aktualności · Zadania · Harmonogram · Warsztaty · Dokumenty (lista rozwijana) · Archiwum · Wyniki ·
Partnerzy · Kontakt.

**Dwie pozycje menu zniknęły**, bo dublowały treść stojącą na stronie głównej — i obie zostawiły
po sobie trwałe przekierowanie (`OBSOLETE_PAGES` w komendzie), bo stare adresy wiszą w pismach do
szkół i w indeksach wyszukiwarek:

- `/jak-zaczac/` → `/`. Pięć kroków uczestnika dublowało sekcję „Jak zacząć w 3 krokach”, która
  jest na stronie głównej od początku. Dwie listy kroków w jednym serwisie rozjadą się przy
  pierwszej zmianie regulaminu.
- `/o-olimpiadzie/` → `/#o-olimpiadzie`. Odpowiedź na „co to jest i kto to organizuje” stała jedno
  kliknięcie za hasłem, które tę ciekawość wzbudza. Treść jest teraz sekcją strony głównej
  (pola `HomePage.about_title` i `about_body` — ten sam zestaw bloków, co strona treści), a plik
  `fixtures/legacy/o-olimpiadzie.md` **zostaje w repozytorium** jako jej źródło.

Komenda kasuje obie strony przy **każdym pełnym** przebiegu (nie tylko pierwszym) i jest w tym
idempotentna: przekierowanie zakłada także wtedy, gdy strony już nie było. Skasowanie nie jest
utratą treści — jedynym źródłem obu była zawartość plików w `fixtures/legacy/`.

Sekcja „O Olimpiadzie” na stronie głównej jest — obok listy partnerów — **drugą treścią, której
powtórny import nie nadpisuje**: plik jest punktem startowym, a po pierwszym imporcie właścicielem
sekcji jest redakcja w `/cms/`. Nadpisywanie kasowałoby jej poprawki, a w zamian przywracało tekst
ze starego WordPressa. Żeby zaimportować plik ponownie, trzeba najpierw wyczyścić pole w `/cms/`.

Wszystkie dokumenty organizatora mieszkają w jednej sekcji: `/dokumenty/` (`DocumentIndexPage`)
z kartą na dokument i jedną pozycją menu z listą rozwijaną. Stare adresy jednosegmentowe
(`/regulamin/`, `/rodo/`, `/standardy-ochrony-maloletnich/`, `/komitety/`) odpowiadają trwałym
przekierowaniem 301 na nowe — przekierowania trzyma `wagtail.contrib.redirects`, więc redakcja
widzi je i rozszerza w `/cms/`.

**Regulamin** (`/dokumenty/regulamin/`) to wersja **1.0 z 2 września 2026 r.** Trzy jego postacie —
treść strony (z konwersji `.docx`), PDF do druku i plik źródłowy `.docx` — leżą w jednym katalogu
`backend/apps/cms/fixtures/regulamin/` i wgrywa je jedna komenda, `seed_regulamin`. Wcześniej PDF
dokładał `seed_legacy_content` z katalogu plików starej strony; przy pierwszej aktualizacji
dokumentu dało to stronę z nowym tekstem i plik do pobrania ze starym. Wersja z 18 sierpnia 2026
jest wycofana z repozytorium (patrz `docs/import/assets.md`, w tym procedura budowy PDF-u —
`.docx` organizatora ma nieprzyjęte zmiany śledzone, które LibreOffice renderuje jako znacznik
korektorski).

Trzy dokumenty nie pochodzą już ze starego WordPressa, tylko z **podpisanych PDF-ów organizatora**
(`backend/apps/cms/fixtures/legacy/pdf/`): `/dokumenty/rodo/`,
`/dokumenty/standardy-ochrony-maloletnich/` i `/dokumenty/komitety/`. Ich pliki `.md` są przepisane
z PDF-u sekcja po sekcji, więc strona jest wersją HTML dokumentu, a nie jego streszczeniem — PDF
wisi przy niej do pobrania jako wersja źródłowa.
Wyciąg tekstu z PDF-ów leży obok nich w `fixtures/legacy/pdf-text/`; `apps/cms/tests/test_pdf_content.py`
porównuje z nim strony (komplet sekcji, spis rozdziałów, liczba słów ≥ 90 % dokumentu).

Nazwa serwisu, hasło i dane organizatora (nagłówek, stopka) siedzą w **Ustawienia → Dane serwisu**
w `/cms/` (`cms.SiteSettings`), a nie w szablonie — zmiana adresu czy numeru telefonu nie wymaga
wydania aplikacji.

**Partnerzy** (`/partnerzy/`, typ `PartnersPage`, pozycja menu przed „Kontaktem”). Strona powstaje
z pustą listą i **tylko ta jedna treść nie jest nadpisywana przy powtórnym imporcie**: partnerzy
przybywają razem z podpisywanymi umowami, więc `seed_legacy_content` ich nie kasuje. Stara strona
wymieniała trzy nazwy, z których jedna — „Uniwersytet Kwantowy” — to instytucja nieistniejąca,
a pozostałe dwie nie miały potwierdzonego patronatu; poprzedni import zostawiał je w treści
i chował całą stronę jako szkic (404). Żadna z nich nie wróciła. Każdy wpis ma poziom współpracy
(patronat honorowy, partner instytucjonalny/naukowy, sponsor diamentowy/platynowy/złoty, partner
medialny), który decyduje o grupie na stronie; logotyp i adres są opcjonalne — bez logotypu karta
pokazuje kółko z inicjałami. **Pas logotypów na stronie głównej pojawia się dopiero z pierwszym
wpisem** — przy pustej liście nie ma go wcale.

Logotypy przekazane przez organizatora wgrywa `seed_partners` z `backend/apps/cms/fixtures/partners/`
(manifest `partners.json`). Komenda normalizuje pliki przed wgraniem — `apps/cms/images.py`:
CMYK → RGB (logotyp Wydziału Fizyki UW przyszedł w przestrzeni drukarskiej), obcięcie pustego
marginesu wokół znaku, ograniczenie szerokości do 1600 px (logotyp PCSS-u miał 8082 px), zapis PNG
albo JPEG zależnie od kanału alfa. Tożsamość obrazu w bibliotece to jego **tytuł** = nazwa partnera,
a wpisy na stronie są dopasowywane po nazwie, więc powtórny przebieg nie tworzy duplikatów.
**Manifest ustawia poziom współpracy i opis tylko przy zakładaniu wpisu** — organizator ich nie
podał, więc wartości są wstępne (naukowy / instytucjonalny) i **redakcja zmienia poziom, opis
i kolejność w `/cms/`**; kolejny przebieg komendy tych zmian nie cofnie, odświeży wyłącznie logotyp
i adres. Jedyne, czego komenda nie umie, to **usunąć** partnera: wpis skasowany w `/cms/` wróci przy
najbliższym przebiegu, bo manifest opisuje stan docelowy — zakończenie współpracy jest skreśleniem
wpisu z `partners.json`, czyli zmianą z historią w repozytorium.
Logotyp organizatora nie jest partnerem: trafia do `SiteSettings.organizer_logo` (stopka).

**Logotypy rysują się większe** (uwaga organizatora z 16.09: „powiększenie logo lub napisu
Wydziału Informatyki PWr, bo jest nieczytelny”). Ten znak jest **prawie kwadratowy** (584×528),
więc ograniczała go wysokość kadru, a nie szerokość kolumny; nieczytelny był napis **wewnątrz
pliku** — nazwa wydziału złożona drobnym krojem i przeskalowana razem z całym znakiem. Kadr karty
urósł ze 112 na 150 px (obraz z 96 na 128), kadr w pasie na stronie głównej z 72 na 96 px,
a **nazwa instytucji stoi pod znakiem jako tekst** (0,875 rem w pasie, nagłówek karty na
`/partnerzy/`) — wcześniej w pasie logotyp był jedynym nośnikiem nazwy.

**Odnośnik obejmuje logotyp i nazwę**, jeden `<a>` na partnera (`target="_blank"`,
`rel="noopener noreferrer"`), a nie sam nagłówek: znak jest tym, w co człowiek celuje myszą.
Opis współpracy zostaje poza odnośnikiem, żeby nie wydłużać jego nazwy dostępnej o całe zdanie.

**Znak szerszy niż sam kadr dostaje dwie kolumny siatki.** To osobna sprawa od powiększenia kadru:
powiększenie pomaga znakom kwadratowym (ogranicza je wysokość), a pasom nie pomaga wcale.
Próg (`apps/cms/blocks.py::WIDE_LOGO_RATIO` = 3) nie jest gustem, tylko **proporcją kadru**
(2,1 na `/partnerzy/`, 3,3 w pasie): poniżej niej `object-fit: contain` wykorzystuje wysokość do
końca i dołożenie szerokości niczego nie zmienia; powyżej ogranicznikiem staje się szerokość — pas
PCSS-u (7,7) rysuje się w jednej kolumnie wysoki na 35 px przy kadrze 128 px. Siatka ma wtedy
`grid-auto-flow: dense`, żeby kafel podwójnej szerokości nie zostawiał za sobą pustej kratki.
O tym, który logotyp jest „pasem”, rozstrzyga **serwer** (`PartnerValue.is_wide`, liczone
z `Image.width`/`Image.height` — kolumn w bazie, nie odczytu pliku): przeglądarka nie zna proporcji
pliku, zanim go nie pobierze, a przy `loading="lazy"` układ musi stać wcześniej.

**Manifest wymienia też partnerów, których logotypów nie mamy.** Pięciu wpisów z produkcji
(`Wydział Informatyki i Telekomunikacji Politechniki Wrocławskiej`, `Wrocławskie Centrum
Superkomputerowo-Sieciowe`, `finQbit`, `IQM`, `EuroCC 3`) organizator dokonał wprost w `/cms/`:
ich znaki leżą w bibliotece Wagtaila, a plików źródłowych nie ma w repozytorium. Stoją więc
w `partners.json` **bez `file` i bez `url`** — oba pola są odtąd opcjonalne, a komenda przy ich
braku **nie rusza** ani logotypu, ani adresu. To nie jest wygoda, tylko warunek bezpieczeństwa:
gdyby „brak pliku” znaczyło „brak logotypu”, jeden przebieg `seed_partners` na produkcji skasowałby
znak i odnośnik, których nie da się odtworzyć z repozytorium. Na świeżej instalacji ci partnerzy
pokazują kółko z inicjałami. Dopisanie im znaku to wgranie pliku do
`backend/apps/cms/fixtures/partners/` i uzupełnienie `file` w manifeście; adresy uzupełnia
redakcja w `/cms/` albo klucz `url` w manifeście. Nazwy muszą być **identyczne** z produkcyjnymi —
dopasowanie idzie po nazwie, więc literówka utworzyłaby duplikat.

#### Decyzje do podjęcia przez właściciela

Import odtworzył treść, ale nie mógł rozstrzygnąć sprzeczności, które w niej były. Pełne
uzasadnienie każdego punktu: `docs/import/stara-strona-inwentarz.md`, sekcja 8.

1. **Dwa czy trzy etapy — rozstrzygnięte: trzy.** Regulamin w wersji 1.0 z 2 września 2026 r.
   ma podtytuł „Ogólnopolski, **trzyetapowy** konkurs edukacyjny” i trzy paragrafy etapowe:
   § 11 „Etap I – zawody zdalne”, § 12 „Etap II – rozmowa”, § 13 „Etap III – finał stacjonarny”.
   Zgadza się to z modelem portalu (`ELIM`/`DISTRICT`/`FINAL`) i z `seed_edition_kwantowa`, więc
   po stronie kodu nie ma nic do zmiany. **Zostaje redakcyjna sprzeczność w samym dokumencie**,
   której import nie tknie (nie redagujemy treści organizatora): § 10 ust. 1 i ramka „Status
   dokumentu” mówią jeszcze o „dwóch etapach”, a § 1 ust. 1 o konkursie „dwuetapowym”. Do
   poprawienia przy najbliższej wersji regulaminu — na stronie widać to wprost w § 10 i w ramce
   nad treścią.
2. **Skala ocen i łączenie ocen.** Regulamin § 9 opisuje średnią z ≥2 ocen z progiem 20 %;
   portal ma skalę 0/2/5/6 i konsensus z trzecim recenzentem. Obu naraz utrzymać się nie da.
3. **Terminy I edycji.** `seed_edition_kwantowa` uzupełnia brakujące terminy stałą regułą
   (otwarcie 00:00, oddanie 23:59, recenzje +14 dni, okno reklamacji +2/+9 dni po recenzjach),
   bo stara strona podaje **po jednej dacie na etap**. Godziny i okna wymagają potwierdzenia.
   Dotyczy to również **finału**: organizator przesunął III etap na **4–7 czerwca 2027,
   stacjonarnie w Krakowie** (dawniej 10 kwietnia 2027 w Warszawie) i podał same daty dzienne —
   komenda przyjmuje 4 czerwca 9:00 jako rozpoczęcie i 7 czerwca 18:00 jako zakończenie.
   Miejsce trzyma nowe pole `Stage.location`; pokazują je oś czasu na stronie głównej
   i `/harmonogram/`.
4. **Zatwierdzenie treści prawnych — rozstrzygnięte co do źródła, otwarte co do decyzji Zarządu.**
   `/dokumenty/rodo/` i `/dokumenty/standardy-ochrony-maloletnich/` nie są już „wersją
   demonstracyjną” ze starego
   WordPressa: treść obu stron jest przepisana z podpisanych PDF-ów organizatora (eksport
   z 7 września 2026), metryka mówi, z jakiego eksportu, a ramka na górze wskazuje PDF jako wersję
   źródłową. Do podjęcia zostaje to, o co proszą same dokumenty: § 11 polityki RODO zapowiada
   aktualizacje przy zmianie procesu, a § 10 standardów wymaga uchwały Zarządu (punkt 5 niżej).
5. **Wzór zgody rodzica lub opiekuna prawnego — projekt do akceptacji.**
   `/dokumenty/zgoda-opiekuna/` (źródło: `backend/apps/cms/fixtures/legacy/zgoda-opiekuna.md`)
   **nie pochodzi od organizatora** — powstał w repozytorium na podstawie polityki RODO
   i paragrafów RODO regulaminu (§ 2 ust. 4, § 4 ust. 2, § 19), żeby zgoda opiekuna w formularzu
   rejestracji miała do czego linkować. Treść wymaga akceptacji Fundacji i sprawdzenia przez
   radcę prawnego; do tego czasu strona nosi status „Wersja robocza do akceptacji organizatora”,
   ramkę z tym samym zdaniem nad treścią i wersję **0.1 (projekt)** — dzięki temu zgody zebrane
   przed zatwierdzeniem są w `ConsentRecord.document_version` odróżnialne od zebranych pod
   wersją ostateczną. **Formularz do wydruku jest** (organizator poprosił o szablon 15.09):
   `backend/apps/cms/fixtures/documents/zgoda-opiekuna.pdf` składa komenda
   `manage.py build_guardian_consent_pdf` z tego samego markdowna, z którego powstaje strona,
   `seed_legacy_content` przypina go do dokumentu jako „PDF do druku”, a etykieta zgody przy
   rejestracji prowadzi właśnie do niego (`consents.document_link`). Plik jest składany
   powtarzalnie (`invariant=1`), więc po każdej zmianie treści trzeba go **przebudować i wgrać
   do repozytorium** — pilnuje tego test w `apps/cms/tests/test_legacy_content.py`. Ramki
   „wersja robocza” w PDF-ie nie ma: zostaje na stronie, bo kartka do podpisu ma być formularzem,
   a nie projektem dokumentu ze stemplem „nie obowiązuje” — do decyzji organizatora, czy tak
   ma zostać. Do rozstrzygnięcia zostają też: czy skan na `contact@qaif.org` wystarcza jako droga
   dostarczenia oraz czy podpisany dokument ma być wymagany od wszystkich niepełnoletnich,
   czy dopiero na etapie stacjonarnym.
   Po zatwierdzeniu: podmiana pliku źródłowego, `GUARDIAN_VERSION`
   w `backend/apps/accounts/consents.py` i metryki w `seed_legacy_content`, potem
   `manage.py seed_legacy_content --only zgoda-opiekuna`.
6. **Osoby odpowiedzialne za ochronę małoletnich.** § 9 i § 10 standardów wymagają wskazania ich
   imiennie uchwałą Zarządu i przyjęcia wzoru karty interwencji.
7. **Skład komitetów — rozstrzygnięte.** Dokument `/dokumenty/komitety/` jest opublikowany
   z dwunastoma osobami w Komitecie Merytorycznym i siedmioma w Organizacyjnym, każda z tytułem
   i afiliacją; podwójne członkostwo dwóch osób (Paweł Gora, Grzegorz Czelusta) i nazewnictwo
   (bez „Komitetu Głównego”, którego nie ma ani w regulaminie, ani na stronie) są rozstrzygnięte.
   Źródłem jest bezpośrednio wiadomość organizatora z 21 września 2026, nie PDF: tego dnia
   organizator kazał **zdjąć plik do pobrania** ze składem (skład zmienia się częściej niż
   dokument, który ktoś by podpisywał), więc strona zostaje jedynym miejscem z tą listą —
   `manage.py build_guardian_consent_pdf --document komitety` nadal potrafi złożyć wydruk na
   żądanie, ale jego wynik nie leży już w repozytorium ani nie jest nigdzie przypięty
   (`retire_legacy_files`, `docs/OPERACJE.md`).
8. **Partnerzy — logotypy są, poziomy współpracy do potwierdzenia.** Organizator przekazał sześć
   logotypów (FUW, PCSS, CFT PAN, IF PAN, Uniwersytet Gdański, AIQLAB Institute) i adresy stron,
   ale **nie podał poziomu współpracy ani opisu**. `seed_partners` wpisuje wartości wstępne
   (uczelnie i instytuty jako „partner naukowy”, PCSS i AIQLAB jako „partner instytucjonalny”);
   ostateczny podział, opisy i kolejność ustawia redakcja w `/cms/` i kolejny przebieg komendy tego
   nie cofnie. Do decyzji zostają też progi sponsoringu oraz nazwy ze starej strony: czy
   Ministerstwo Edukacji i Polskie Towarzystwo Fizyczne to realne patronaty (trzecia nazwa,
   „Uniwersytet Kwantowy”, to instytucja nieistniejąca) — żadnej z nich na serwisie nie ma.
9. **ZOZ (Zasady Organizacji Zawodów) — projekt jest, decyzje organizatora nie.** Regulamin
   odwołuje się do ZOZ kilkanaście razy (§ 1 ust. 4 przenosi tam harmonogram, formę zadań, wykaz
   narzędzi, maksymalną liczbę finalistów, progi punktowe, literaturę i program merytoryczny),
   a dokumentu nie było — każde z tych odesłań prowadziło w pustkę. Powstał więc **projekt**:
   `/dokumenty/zoz/` (źródło: `backend/apps/cms/fixtures/legacy/zoz.md`), wersja **0.2 (projekt)**,
   status „projekt do akceptacji organizatora”, z ramką o tym samym brzmieniu nad treścią.
   Dokument opisuje wyłącznie to, co serwis naprawdę robi — formaty i limity uploadu, liczenie
   terminu po stronie serwera, skalę 0/2/5/6, dwie niezależne recenzje i rozjemcę, okno reklamacji,
   remisy *ex aequo*, anonimizację tabel wyników, zapisy na rozmowy Etapu II — i **nie powtarza
   terminów**: odsyła do `/harmonogram/`, żeby nie powstało drugie źródło dat obok `Stage`.
   Czego nie rozstrzyga, tego nie udaje: **na końcu dokumentu stoi jawna lista decyzji dla
   organizatora** (§ 16 ZOZ) — maksymalna liczba finalistów, progi punktowe poza „≥ 1 pkt”,
   sposób wyliczenia wyniku kwalifikacyjnego 80/20 z § 12 ust. 5 regulaminu, dodatkowy próg dla
   laureatów, reguła remisów do potwierdzenia, wykaz literatury, dozwolone narzędzia oraz liczba
   sesji, czas i wyposażenie finału, koszty przejazdu i zakwaterowania finalistów (§ 22 ust. 2:
   brak informacji = brak zobowiązania), okres poufności rozwiązań po etapie, formaty i limity per
   zadanie, nagrywanie rozmów oraz dwie sprzeczności regulaminu z punktów 1 i 2 tej listy.
   Po zatwierdzeniu: podmiana pliku źródłowego, metryki (`ZOZ_VERSION`, `ZOZ_STATUS`)
   w `seed_legacy_content` i `manage.py seed_legacy_content --only zoz`.
10. **Status prawny olimpiady.** Regulamin zastrzega, że tytuły finalisty i laureata są wewnętrzne
   i nie dają uprawnień ustawowych. Gdzie portal ma to komunikować?
11. **Kroki na stronie głównej — rozstrzygnięte co do miejsca, otwarte co do brzmienia.** Podstrony
    `/jak-zaczac/` już nie ma (dublowała sekcję „Jak zacząć w 3 krokach”, patrz 6.7), więc pięciu
    kroków ze starej strony nie ma gdzie poprawiać. Zostaje pytanie o brzmienie trzech kroków, które
    stoją na stronie głównej („Załóż konto”, „Rozwiąż zadania”, „Sprawdź wynik” —
    `HOME_STEPS` w `seed_legacy_content`): są naszym skrótem procedury, nie tekstem organizatora,
    a portal używa kodów zaproszeń dla komitetu i samodzielnej rejestracji uczestnika. Kroki są
    treścią redakcyjną, więc poprawia się je w `/cms/`, bez wydania aplikacji.
12. **Kanały kontaktu.** Jeden adres `contact@qaif.org` obsługuje sprawy ogólne, RODO i zgłoszenia
    dotyczące bezpieczeństwa małoletnich, rozróżniane tylko tematem wiadomości.
13. **Aktualności.** Trzy przeniesione wpisy to jednozdaniowe zapowiedzi bez dat (oryginał nie miał
    `post_date`) — mają datę importu i dopisek „Wpis przeniesiony ze starej strony”. Do decyzji,
    czy przepisać je z prawdziwymi datami, czy zacząć newsroom od zera.
14. **Logo, favicon, og:image.** Stara strona nie ma ani jednego pliku graficznego — identyfikację
    trzeba zaprojektować od zera. Logotyp organizatora (Fundacja Quantum AI) jest już w stopce:
    wgrywa go `seed_partners` do `SiteSettings.organizer_logo`.
15. **Harmonogram warsztatów.** Szesnaście warsztatów online (`/warsztaty/`) pochodzi z listy
    organizatora podanej w formacie amerykańskim. Jedna data jest niejednoznaczna: „Podstawy
    metrologii kwantowej” przyszła jako `09/01/2027`; w ciągu sobotnich terminów pasuje
    **9 stycznia 2027** i tak jest zapisana, ale wymaga potwierdzenia — podobnie jak godziny tego
    warsztatu, których organizator nie podał (tabela mówi „do potwierdzenia”). Do akceptacji jest
    też zdanie wprowadzające („Warsztaty online przygotowujące do zawodów; udział jest bezpłatny.
    Szczegóły i linki do spotkań ogłosimy w aktualnościach.”) — sformułowaliśmy je sami,
    nie pochodzi od organizatora.
16. **Google Analytics 4 — kod jest, usługa i dwie decyzje należą do właściciela.** Serwis umie
    zbierać statystykę odwiedzin za zgodą odwiedzającego (4.5), ale **nic się nie włączy, dopóki
    organizator nie wklei identyfikatora** `G-…` w `/cms/` → Ustawienia → Dane serwisu →
    Analityka. Po stronie samej usługi GA4 zostają dwie decyzje, których kod nie podejmie:
    **(a) retencja danych zdarzeń** — organizator wybrał **14 miesięcy** (decyzja z 15 września
    2026) i tyle stoi w polityce RODO (§ 3, „statystyka odwiedzin serwisu”); ustawienie trzeba
    przestawić ręcznie w usłudze GA4 (Administracja → Ustawienia danych → Przechowywanie danych),
    a każda zmiana wymaga poprawki tego zdania w `backend/apps/cms/fixtures/legacy/rodo.md`
    i ponownej publikacji;
    **(b) akceptacja warunków przetwarzania danych Google'a** (Google Ads Data Processing Terms
    w ustawieniach usługi) — bez niej Fundacja nie ma umowy powierzenia, której wymaga art. 28
    RODO, a polityki obiecują ją czytelnikowi. Zalecane pozostawienie funkcji reklamowych
    wyłączonych po stronie usługi: loader wyłącza je po stronie strony, ale dwa źródła prawdy
    lepiej mieć zgodne.
    Publikacja obu dokumentów na produkcji po zmianie treści:

    ```bash
    docker compose exec web python manage.py seed_legacy_content --only cookies --only rodo
    ```

    Uwaga: `--only` **nadpisuje treść wskazanych stron** plikami z repozytorium. Polityka cookie
    powstała i żyje w repozytorium, więc to jest jej właściwa droga na produkcję. Strona
    `/dokumenty/rodo/` bywa natomiast poprawiana w `/cms/` (jej wersją źródłową jest PDF
    organizatora) — przed przebiegiem sprawdź w panelu, czy nie ma tam zmian redakcyjnych
    do przeniesienia do pliku źródłowego, albo uruchom komendę z samym `--only cookies`
    i dopisz akapit o statystyce w `/cms/` ręcznie.

### 6.8 Rozmowy kwalifikacyjne (etap w formie rozmowy online)

Etap ma **formę** (`Stage.format`) niezależną od rodzaju: `rozwiązania pisemne` (domyślnie) albo
`rozmowa kwalifikacyjna online`. W I edycji rozmową jest etap II — tak stanowi regulamin § 10.
Formę ustawia się w `/coordinator/stages/<id>/edit/` (pole „Forma etapu”) i **nie da się jej
zmienić**, gdy do etapu wpłynęły już rozwiązania albo są zapisy na rozmowy
(`409 STAGE_FORMAT_LOCKED`). Tam samo stoi pole „Nazwa etapu”: puste = nazwa domyślna rodzaju
(Eliminacje / Wojewódzki / Finał), wpisana zastępuje ją na każdym ekranie — na harmonogramie,
stronie głównej, w panelach i w tabelach wyników. Nazwę wolno zmienić także po zamknięciu etapu.

Etap w formie rozmowy **nie ma zadań i nie przyjmuje plików**: `create_problem` odmawia
(`409 STAGE_NOT_ACCEPTING_PROBLEMS`), a upload — `409 STAGE_NOT_ACCEPTING_FILES`. Na karcie etapu
w panelu odnośnik „Zadania (n)” zastępuje **„Rozmowy (n)”**.

**Koordynator** — `/coordinator/stages/<id>/interviews/`:

1. Terminy powstają **serią**: „początek pierwszego terminu”, długość jednej rozmowy (1–480 min),
   ile terminów po kolei (1–50) i ile miejsc w każdym (1–20). Sloty idą jeden po drugim, bez przerw.
2. **Okno rozmów to okno etapu**: wszystkie terminy muszą się zmieścić między `opens_at`
   a `deadline_at` (`400 SLOT_OUTSIDE_STAGE`), a początek musi być w przyszłości
   (`400 SLOT_IN_PAST`). Chcesz rozmawiać w innych dniach — najpierw przesuń terminy etapu (6.3).
3. **Kolizji terminów nie sprawdzamy świadomie**: kilka komisji rozmawia równolegle. Rozróżnia je
   pole „Oznaczenie” (np. „komisja A”), a liczbę osób — „Miejsc w jednym terminie”.
4. **Link do rozmowy** (pole opcjonalne, można uzupełnić później) widzi wyłącznie osoba zapisana na
   dany termin. Na liście terminów uczestnika go nie ma — adres pokoju wideo, do którego wchodzi się
   bez logowania, jest de facto poświadczeniem.
5. Tabela terminów pokazuje **dane osobowe** zapisanych (kod, imię i nazwisko, e-mail) — to obok
   podglądu wyników jedyny taki ekran w serwisie, stąd odznaka „dane osobowe”.
6. **Usunięcie terminu** jest możliwe tylko, dopóki nikt się na niego nie zapisał
   (`409 SLOT_HAS_BOOKINGS`); przycisk „Usuń” znika z wiersza z zapisami.

**Uczestnik** — `/me/`:

1. Zapisać się może **wyłącznie osoba z wpisem w tym etapie**, czyli zakwalifikowana w poprzednim
   (`403 NOT_QUALIFIED`). Rejestracji otwartej do etapu rozmowy nie ma.
2. Zamiast odliczania do oddania rozwiązań karta etapu pokazuje okno rozmów, a po zapisie —
   własny termin z linkiem i przycisk „Zrezygnuj z terminu”.
3. Uczestnik ma w etapie **jeden** termin. Kliknięcie „Zmień na ten termin” **przenosi** zapis
   w jednej transakcji (`interview.booking_moved` w audycie) — nie trzeba najpierw rezygnować,
   więc między dwoma krokami nikt nie zajmie ostatniego wolnego miejsca.
4. Zapis i rezygnacja są możliwe **do chwili rozpoczęcia** rozmowy (`409 BOOKING_LOCKED`,
   `409 SLOT_STARTED`); pełny termin odmawia `409 SLOT_FULL`, powtórny zapis na ten sam —
   `409 ALREADY_BOOKED`.
5. Po zapisie idzie **e-mail z potwierdzeniem** (temat z nazwą etapu, treść z datą i godziną w
   czasie polskim oraz linkiem). Wysyłka jest zadaniem Celery `apps.core.tasks.send_mail_task`
   na kolejce `mail`, kolejkowanym dopiero po zatwierdzeniu transakcji — niedostępny MTA nie
   zamienia udanego zapisu na błąd.

Wszystkie operacje zostawiają ślad w audycie (`interview.slots_created`, `interview.slot_deleted`,
`interview.booked`, `interview.booking_moved`, `interview.cancelled`) — w `diff` idą wyłącznie
identyfikatory i liczniki, nigdy imiona, nazwiska ani adresy.

**Pokój wideo powstaje sam, w chwili zapisu** (`apps/competitions/video.py`). Etap ma dwa pola:
`Stage.video_provider` (`bez wideo` / `Jitsi Meet` / `własna instancja`) i `Stage.video_base_url`
(domyślnie `https://meet.jit.si/`) — oba w formularzu terminów etapu. Nazwa pokoju to
`olimpiada-<edycja>-<identyfikator terminu>-<6 losowych znaków>`, a adres ląduje na **zapisie**
(`InterviewBooking.meeting_url`, migracja `competitions.0016`).

- **sześć losowych znaków to jedyna część, która czyni pokój prywatnym.** Publiczna instancja
  Jitsi nie wymaga logowania, więc nazwa pokoju *jest* poświadczeniem; przewidywalna
  („olimpiada-2027-12”) byłaby zaproszeniem dla każdego, kto potrafi liczyć. Dlatego adres nadal
  nie stoi na wspólnej liście terminów, tylko przy własnym zapisie uczestnika,
- **edycja i termin są w nazwie dla człowieka**: adres bywa dyktowany przez telefon,
- **jeden pokój na termin, nie na osobę** — termin może mieć kilka miejsc, a komisja musi zastać
  wszystkich w tym samym miejscu. Pierwszeństwa: ręczny link przy terminie → pokój już przypisany
  sąsiadowi z tego samego terminu → nowy adres od dostawcy etapu,
- **adres zapisujemy na zapisie, a nie czytamy z terminu**: uczestnik ma widzieć ten adres, który
  dostał w liście, także wtedy, gdy koordynator zmienił później pole przy terminie. Przeniesienie
  zapisu na inny termin daje inny pokój,
- **„Sprawdź kamerę i mikrofon”** prowadzi do pustego pokoju o tej samej nazwie z sufiksem
  `-test`. Dostawcy wideo nie mają osobnej „strony testu sprzętu” — testem jest ekran powitalny
  pokoju, na którym przeglądarka pyta o kamerę i mikrofon, a użytkownik widzi własny podgląd.
  Zamiast linkować cudzą stronę pomocy, otwieramy pokój, w którym na pewno nikogo nie ma,
- **wideo jest wyłącznie linkiem.** Osadzenia na naszej stronie nie ma i nie będzie: ramka z obcej
  domeny wymagałaby rozluźnienia `frame-src` w CSP, a wideo w `<iframe>` prosi przeglądarkę
  o kamerę i mikrofon **w kontekście naszej domeny**.

**Przypomnienie dzień wcześniej** — zadanie beat `remind_interviews`
(`apps/competitions/tasks.py`, harmonogram w `config/settings/base.py`) wysyła raz na dobę list do
każdego, kogo rozmowa zaczyna się w ciągu najbliższych 24 godzin: termin, link do pokoju, link
testowy i krótka instrukcja („wejdź z komputera, w Chrome/Edge/Firefox…”). Drugi list blokuje
`InterviewBooking.reminder_sent_at`, stawiany **przed** kolejkowaniem — przy padzie workera wolimy
jedno przypomnienie mniej niż dwa te same. Treść składa się w języku odbiorcy (5.9).

**Czego jeszcze nie ma:** ocen z rozmowy. Etap w tej formie nie ma ścieżki oceniania w systemie —
punkty wpisuje koordynator poza nim (`docs/BACKLOG.md`).

### 6.9 Pobieranie prac (odnośniki i paczki ZIP)

Kto co pobiera i pod jaką nazwą — reguła jest jedna dla wszystkich dróg
(`apps/submissions/packaging.py`).

| Kto | Skąd | Co dostaje |
|---|---|---|
| Uczestnik | `/me/` → karta zadania → kolumna **„Plik”** → „Pobierz” | własną wersję, **pod własną nazwą pliku**; także wersję przed skanem antywirusowym (to jego plik) |
| Koordynator | `/coordinator/stages/<id>/assignments/` → kolumna **„Plik”** | pojedynczą pracę; przy pliku bez skanu stoi podpowiedź „skan w toku”, a nie odnośnik |
| Koordynator | karta etapu na `/coordinator/` albo nagłówek ekranu przydziałów → **„Pobierz wszystkie prace (ZIP)”** | `GET /coordinator/stages/<id>/download/` — po jednej, najnowszej przeskanowanej wersji każdej pary (wpis, zadanie) |
| Koordynator | tabela „Zadania → recenzenci z góry” → **„Pobierz ZIP zadania N”** | to samo, zawężone do jednego zadania (`?problem=<id>`) |
| Koordynator | kratki w tabeli prac → **„Pobierz zaznaczone (ZIP)”** | `POST /coordinator/stages/<id>/download/` z `submission_ids`; identyfikator spoza etapu nie wchodzi do paczki, puste zaznaczenie wraca z komunikatem „Nie zaznaczono żadnej pracy.” |
| Recenzent | `/review/` → **„Pobierz moje prace (ZIP)”** (`GET /review/download/`) oraz `GET /api/grading/reviews/download/` | wszystkie prace z jego **nieanulowanymi** recenzjami (`ASSIGNED`, `DRAFT`, `SUBMITTED`) |

**Nazwy plików w paczce są anonimowe** — także dla koordynatora: `<kod uczestnika>_zad<numer>_v<wersja>.<rozszerzenie>`
(np. `OLM-7PE5K2_zad2_v1.pdf`). `original_name` od uczestnika regularnie zawiera nazwisko albo
szkołę, a paczka wędruje dalej do komitetu i nikt jej po drodze nie przepakowuje. W archiwum jest
`README.txt` ze spisem treści: u koordynatora sama lista plików, u recenzenta wiersze
`recenzja <id> → <plik>`, po których odnajduje pracę w panelu. Nazwisk nie ma w żadnym z nich.

**Do paczki wchodzi wyłącznie plik po skanie** (`av_status=CLEAN`). Gdy najnowsza wersja skanu
jeszcze nie przeszła (albo jest zainfekowana), paczka etapu bierze **ostatnią wcześniejszą czystą**
wersję — tak samo jak przeliczanie wyników. Praca bez ani jednej czystej wersji jest pomijana bez
komunikatu; stan skanu widać przy pojedynczym wierszu. Pusty zakres to **404** z powodem
(„Brak prac do pobrania w tym zakresie.”, u recenzenta „Brak przydzielonych prac do pobrania.”),
a nie archiwum z samym spisem treści.

Archiwum jest budowane w pliku tymczasowym, bez kompresji (`ZIP_STORED` — PDF i `.ipynb` są już
skompresowane), a treść przechodzi ze storage do archiwum kawałkami, więc pamięć procesu nie rośnie
z liczbą prac. Audyt: `stage.downloaded_zip` (`{count, scope, problem}`) i `review.downloaded_zip`
(`{count}`) — same liczby i identyfikatory, nigdy nazwy plików ani pseudonimy.

Zaznaczanie wierszy działa **bez JavaScriptu**: kratki należą do formularza pobierania przez atrybut
`form="stage-zip"` (formularzy nie wolno zagnieżdżać, a w tabeli stoją już formularze ocen).
`static/js/select-all.js` dokłada wyłącznie kratkę „zaznacz wszystkie” w nagłówku tabeli — z nonce,
bo polityka CSP nie dopuszcza skryptów inline.

### 6.9 Narzędzia koordynatora

Pięć ekranów poza rytmem prowadzenia zawodów. Wszystkie są dostępne **wyłącznie dla koordynatora**
(`CoordinatorRequiredMixin`; każda inna zalogowana rola dostaje 403) i wszystkie działają bez
JavaScriptu: filtry i parametry jadą zwykłym formularzem GET, stronicowanie zwykłymi odnośnikami,
a rozwijane bloki to `<details>`. Wejście na pulpit: sekcja **Narzędzia** (komunikaty, eksport,
audyt) oraz odnośniki **Postęp oceniania** i **Symulacja kwalifikacji** na karcie etapu.

#### Postęp oceniania — `/coordinator/stages/<id>/progress/`

Odpowiada na dwa pytania zadawane w trakcie oceniania codziennie: „jak daleko jesteśmy” i „na kogo
czekamy”. Górna część to pasek segmentowy z liczbami: oddane, zablokowane, przydzielone,
w moderacji, reklamacja, ocenione, odrzucone przez antywirusa. Segmenty są **rozłączne i sumują się
do liczby wszystkich prac** — pasek, którego kawałki się nakładają, kłamałby o postępie. Obok stoją
liczby, które rozłączne nie są: prace z oceną końcową (praca po reklamacji ma ocenę, ale nie jest
w segmencie „ocenione”) oraz reklamacje.

Pasek jest rysowany **samym arkuszem stylów** (`static/css/coordinator-tools.css`): polityka
bezpieczeństwa nie dopuszcza stylu w atrybucie, więc szerokość segmentu jest klasą z zamkniętej
listy i skacze co 5 %. Dokładne liczby stoją pod paskiem i to one są odpowiedzią.

Niżej tabela aktywnych recenzentów: przydzielone / szkice / wystawione / po terminie, posortowana
zalegającymi do góry. **Definicja „po terminie” jest wybierana w czasie działania**: jeśli model
recenzji ma własny termin (`Review.due_at`), liczy się po nim; jeśli nie — „przydzielona ponad
7 dni temu i wciąż niewystawiona”. Ekran pisze wprost, której definicji użył, żeby nikt nie wziął
przybliżenia za termin regulaminowy.

Dwa przyciski wysyłają przypomnienia: **Przypomnij e-mailem** (jedna osoba) i **Przypomnij
wszystkim zaległym**. List jest kolejkowany po commicie (`apps.core.tasks.send_mail_task`) i nie
zawiera **ani pseudonimów prac, ani tytułów zadań** — recenzent widzi komplet po zalogowaniu,
a lista przydziałów w skrzynce byłaby wyciekiem tego, co ocenianie ślepe ma chronić. Osoba bez
niedokończonych recenzji listu nie dostaje. Audyt: `reviewer.reminded` z licznikami
(`{stage_id, reviewers, reviews, overdue, scope}`), nigdy z adresami.

Dane liczy `apps/grading/reports.py` — trzy zapytania agregujące na cały ekran, niezależnie od
liczby prac i recenzentów.

#### Komunikaty — `/coordinator/messages/`

List do grupy odbiorców. Grupy: uczestnicy bieżącej edycji, zapisani do etapu, zakwalifikowani do
etapu, członkowie komitetu, komitet jednego województwa, wklejona lista adresów. „Uczestnik edycji”
znaczy „ktoś z wpisem do któregokolwiek jej etapu”, a nie „ktoś, kto kiedykolwiek założył konto”.
**Z wysyłki wypadają konta zablokowane i te bez potwierdzonego adresu** (patrz 5.1).

Ekran jest **dwustopniowy**: „Podgląd” pokazuje liczbę odbiorców i treść tak, jak pójdzie w liście,
i dopiero „Wyślij” wysyła. To jedyny moment, w którym pomyłkę („uczestnicy edycji” zamiast
„zapisani do etapu”) da się jeszcze cofnąć. Adresów ekran nie pokazuje — koordynator sprawdza rząd
wielkości, a nie pojedyncze wpisy.

Każdy odbiorca dostaje **osobną kopertę**: lista adresów w `To:` pokazałaby każdemu uczestnikowi
adresy wszystkich pozostałych. Wysyłka idzie porcjami po 50 adresów (`apps.accounts.messaging`,
zadanie `send_broadcast_chunk` na kolejce `mail`), więc awaria jednej porcji nie kasuje reszty.

Każda wysyłka zostaje w rejestrze `accounts.MessageBroadcast` (autor, data, grupa, temat, treść,
liczba odbiorców, liczba listów przekazanych do kolejki, stan) i jest wypisana na tej samej stronie.
**Rejestr nie trzyma adresów.** Stan „przekazana do wysyłki” znaczy, że listy trafiły do kolejki —
o doręczeniu rozstrzyga serwer odbiorcy. Audyt: `broadcast.sent` z `{group, recipients, chunks}`.

#### Eksport danych — `/coordinator/export/`

Trzy zestawienia, każde w CSV i XLSX:

- **uczestnicy edycji** — kod publiczny, imię, nazwisko, e-mail, szkoła, klasa, województwo, rok
  urodzenia, telefon, stan konta oraz komplet zgód: stan, **wersja dokumentu** i data. Zgody idą
  z dowodów (`ConsentRecord`), a nie z projekcji na profilu — arkusz ma odpowiadać na pytanie „na
  co ta osoba się zgodziła i w jakiej wersji dokumentu”,
- **wyniki etapu** — miejsce, kod, imię i nazwisko, szkoła, województwo, punkty za każde zadanie,
  suma, status wpisu, kwalifikacja. Liczone na danych **bieżących** w trybie podglądu, więc eksport
  działa także w trakcie oceniania: praca bez oceny liczy się wtedy jako 0 punktów,
- **recenzje etapu** — id recenzji, kod uczestnika, zadanie, runda, adres recenzenta, stan, punkty,
  daty. Uczestnik wyłącznie pod kodem: ocenianie jest ślepe i zestawienie recenzji tego nie znosi.

CSV wychodzi strumieniem (`StreamingHttpResponse`), ze znacznikiem BOM i średnikiem jako
separatorem — tak, żeby polski Excel otworzył go bez kreatora importu. XLSX składa `openpyxl`
w trybie `write_only`. Audyt: `export.generated` z `{kind, format, rows}` — sam rodzaj eksportu
i liczba wierszy, **nigdy dane**. Wpis powstaje przed oddaniem pliku.

`openpyxl` jest od tej zmiany zwykłą zależnością w `backend/pyproject.toml`, a nie ekstrą `dev`:
wcześniej czytał go wyłącznie ręczny import słownika szkół, a eksport działa na produkcji.

#### Audyt — `/coordinator/audit/`

Stronicowana lista wpisów `core.AuditLog` (100 na stronę, od najnowszego) z filtrami: fragment
adresu e-mail wykonawcy, akcja (lista wyboru budowana **z danych**, nie ze spisu w kodzie), typ
obiektu, przedział dat. Przedział jest obustronnie domknięty — „do 14 marca” obejmuje cały ten
dzień. Nieparsowalna data jest traktowana jak brak filtra: adres z literówką ma pokazać listę,
a nie stronę błędu.

Wiersz pokazuje czas, wykonawcę, akcję, obiekt (`app.model#id`) i `diff` schowany pod `<details>`.
Ekran **nie dokłada ani jednej informacji spoza wpisu** — `diff` z założenia nie zawiera danych
osobowych (patrz `backend/apps/core/models.py`), a doklejenie nazwiska „dla czytelności”
zamieniłoby ślad techniczny w wyciąg z bazy osobowej. Wpisów nie da się zmienić ani usunąć.

#### Symulacja kwalifikacji — `/coordinator/stages/<id>/simulation/`

„Co by było, gdyby próg wyglądał tak”. Formularz z trybem (`MIN_POINTS`, `TOP_N`,
`TOP_N_PER_DISTRICT`, `HYBRID`) i jego liczbami; parametry jadą w adresie, więc wynik da się
odświeżyć, zapisać w zakładkach i wkleić w wiadomości do reszty komitetu. Bez parametrów ekran
pokazuje próg aktualnie zapisany przy etapie.

Wynik: liczba zakwalifikowanych, liczba poza progiem, **punkt odcięcia** (najniższa suma, która
jeszcze się kwalifikuje), rozkład po województwach (startujący / zakwalifikowani) i pełna tabela
z odznaką „kwalifikuje się”. Przy trybie „N na województwo” progów jest tyle, ile województw —
pokazany jest najniższy z nich i strona mówi o tym wprost.

Symulacja **niczego nie zapisuje**: żądanie GET nie zmienia statusów wpisów, nie zakłada wpisów
w następnym etapie i nie dotyka progu. Logika nie jest powielona — wiersze liczy
`results.services.compute_stage_results(preview=True)`, a próg rozstrzyga ta sama funkcja, którą
wywoła późniejsze `apply_qualification`. Tryb `preview` wyłącza bramę „ocenianie zakończone”
(symulacja z definicji biegnie w trakcie oceniania) **i** zapis `StageEntry.total_points`. Praca bez
oceny liczy się wtedy jako 0 punktów i ekran pisze, ilu prac jeszcze nie rozliczono.

Przycisk **Zastosuj tę regułę do etapu** (POST) zapisuje `QualificationRule` i nic poza tym:
nikogo nie kwalifikuje i niczego nie ogłasza. Wyniki przelicza się osobno, po zamknięciu okna
reklamacji (6.6). Audyt: `stage.rule_updated` z parametrami przed i po.

W tabeli symulacji stoi też **kolumna „Decyzja komitetu”** — kwalifikacja ręczna z uzasadnieniem,
opisana w 6.11.

### 6.10 Kalibracja recenzentów — `/coordinator/stages/<id>/calibration/`

Ekran czytany **po ocenianiu**, przy przygotowaniu instruktażu na kolejną edycję. Pojedynczy
rozjazd dwóch ocen nie mówi nic o żadnym z recenzentów — dwie osoby mogą w dobrej wierze
przeczytać rozwiązanie inaczej. Informacją jest dopiero rozkład rozjazdów po wszystkich pracach
jednej osoby.

Wiersz na recenzenta: liczba wystawionych recenzji rundy 1, **średnie odchylenie ze znakiem** od
oceny drugiego recenzenta tej samej pracy, to samo wobec oceny uzgodnionej, udział rozjazdów
(prac, które trafiły do moderacji) i podpis „surowy / łagodny / zgodny z komisją”. Znak jest tu
całą istotą: średnia z wartości bezwzględnych zrównałaby recenzenta chaotycznego (raz +2, raz −2)
z konsekwentnie surowym (za każdym razem −2), a to są dwie różne rozmowy.

Co **nie** wchodzi do rachunku: szkice, recenzje anulowane (odebrane albo unieważnione nową wersją
pracy) i cała runda rozjemcza — trzeci recenzent wie już, że poprzednicy się nie zgodzili, więc
jego ocena nie jest niezależna. Tendencji nie przypisujemy poniżej 5 recenzji i przy odchyleniu
mniejszym niż 0,5 punktu; wtedy tabela pisze „za mało danych”.

Sortowanie: `?sort=<kolumna>` (`reviewer`, `reviews`, `vs_peer`, `vs_final`, `disagreements`),
minus odwraca kierunek. Nieznana nazwa cicho wraca do porządku domyślnego. Puste odchylenia
(„etap jeszcze się ocenia”) sortują się zawsze na koniec — wiersz bez danych nie jest ani
najlepszy, ani najgorszy.

Ekran jest **wyłącznie odczytem**: nie zmienia ocen i nie wystawia recenzentom not. Dane liczy
`apps/grading/calibration.py` — trzy zapytania na cały ekran, niezależnie od liczby prac.
Odnośnik: karta etapu na pulpicie oraz stopka ekranu „Postęp oceniania”.

### 6.11 Podobieństwo rozwiązań i kwalifikacja ręczna

#### Podobieństwo — `/coordinator/stages/<id>/similarity/`

Dotyczy **wyłącznie zadań oddawanych jako kod** (`py`, `ipynb`). Przy dowodzie w PDF-ie plagiat
widać gołym okiem; przy programie wystarczy zmienić nazwy zmiennych i dopisać komentarze, żeby
dwa pliki przestały być podobne dla człowieka, a pozostały tym samym rozwiązaniem.

Jak liczymy: z pliku zostaje ciąg tokenów (komentarze znikają, napisy i liczby stają się jednym
symbolem, każdy identyfikator — symbolem `ID`; notatnik sklejamy z samych komórek kodu), a wynik
pary to **większa** z dwóch miar — Jaccarda na 5-gramach tokenów (widzi przestawione bloki)
i `difflib` (widzi dopisany albo usunięty fragment). Porównujemy każdą parę najnowszych, czystych
prac w zadaniu.

Przycisk **Przelicz** zleca zadanie Celery na kolejce domyślnej (`recompute_similarity`) — przy
komplecie prac finału liczenie idzie w tysiącach par i nie może blokować żądania. Plik każdej
pracy czytamy **raz**, strumieniem, z limitem 2 MB, i natychmiast zamieniamy na tokeny; w pamięci
nigdy nie ma kompletu plików zadania. Na jedno zadanie porównujemy najwyżej **5000 par** — po
przekroczeniu limitu przeliczenie przerywa się dla tego zadania i mówi o tym w podsumowaniu,
zamiast po cichu oddać niekompletną listę.

W bazie (`submissions.SubmissionSimilarity`) lądują pary od **0,6** w górę; tabela pokazuje te
powyżej progu z pola `?threshold=` (domyślnie **0,8**). Kolejne przeliczenie zastępuje wyniki
zadania, ale **przenosi znacznik „zgłoszone do komitetu”** — to decyzja człowieka, a nie wynik
obliczenia.

`…/similarity/<pair_id>/` pokazuje obie prace obok siebie (`difflib.HtmlDiff`). Treść pliku od
uczestnika przechodzi przez dwie bariery: escapowanie w bibliotece i białą listę znaczników
(`sanitise_html`). Styl tabeli jest w osobnym arkuszu (`static/css/similarity-diff.css`), bo
polityka bezpieczeństwa nie dopuszcza stylów inline — dlatego nie używamy `HtmlDiff.make_file`.

Przycisk **Zgłoś do komitetu** jest zakładką na parze do obejrzenia na posiedzeniu; da się go
cofnąć, a oba kliknięcia zostają w audycie (`similarity.reported`, `similarity.report_withdrawn`).
Wysoki wynik jest **przesłanką, nie dowodem** — przy zadaniu z jednym oczywistym algorytmem dwie
uczciwe prace potrafią wyjść bardzo podobnie. Audyt przeliczenia (`similarity.recomputed`) niesie
wyłącznie liczniki.

#### Kwalifikacja ręczna — `POST /coordinator/entries/<id>/manual-qualification/`

Regulamin zna sytuacje, których próg punktowy nie opisuje: zerwane łącze w trakcie rozmowy
kwalifikacyjnej, praca oddana poza systemem na polecenie organizatora, wynik unieważniony za
naruszenie zasad mimo wysokiej sumy. Dopóki tej drogi nie było, jedynym wyjściem było majstrowanie
przy punktach — czyli wpisanie do protokołu nieprawdy o tym, jak pracę oceniono.

Formularz stoi **w wierszu tabeli symulacji progu** (decyzja zapada, patrząc na te same liczby):
decyzja (`QUALIFIED` / `NOT_QUALIFIED` / pusta) plus uzasadnienie. **Uzasadnienie jest obowiązkowe
i musi mieć co najmniej 10 znaków** — pilnuje tego serwis `apps/results/manual.py` oraz constraint
w bazie. Pusta decyzja znaczy „niech rozstrzyga próg” i uzasadnienia nie wymaga.

Decyzja **bije regułę punktową** przy każdym przeliczeniu i w symulacji (`qualified_with_manual`),
ale **nie podnosi zdyskwalifikowanego** — dyskwalifikacja jest osobną decyzją i wymaga cofnięcia,
a nie obejścia drugą decyzją. W ogłoszonej tabeli wiersz dostaje odznakę **„kwalifikacja decyzją
komitetu”** (`snapshot[*].manual`): wynik niezgodny z progiem, którego nie widać, wygląda z zewnątrz
jak błąd rachunkowy albo jak protekcja.

Jeśli wyniki etapu są już ogłoszone, panel ostrzega, że zmiana wejdzie do tabeli dopiero po
ponownym przeliczeniu i publikacji — ten sam wzorzec, co przy korekcie oceny końcowej. Audyt:
`entry.manual_qualification` z decyzją przed i po oraz **długością** uzasadnienia (nigdy jego
treścią — bywa opisem zdarzenia z życia konkretnego ucznia).

### 6.12 Dyplomy i zaświadczenia — `/coordinator/stages/<id>/certificates/`

Rodzaju dokumentu nie wyliczamy z punktów — o tym, kto jest laureatem, rozstrzyga komitet, a próg
tytułu bywa inny niż próg kwalifikacji. Rodzajów jest pięć (niżej: „Rodzaje dokumentów”); ten ekran
obsługuje dyplomy uczestników jednego etapu, a szablony graficzne, zaświadczenia dla opiekunów
i zaświadczenia z warsztatów mają własne ekrany opisane w dalszej części tej sekcji.

W bazie (`results.Certificate`) jest **rejestr, nie plik**: edycja, odbiorca (wpis do etapu albo
opiekun — dokładnie jeden z nich), rodzaj, numer `OK/<rok>/<kolejny>`, losowy kod weryfikacyjny,
data i wystawiający. PDF powstaje **przy każdym pobraniu** (`apps/results/certificates.py`,
`reportlab`), bo cała jego treść wynika z tych faktów; kopia binarna znaczyłaby jedynie tyle, że
poprawka szablonu nie dotyczy dokumentów już wystawionych.

Kroje **DejaVu Sans** leżą w repozytorium (`backend/static/fonts/DejaVuSans.ttf`,
`DejaVuSans-Bold.ttf` wraz z `DejaVu-LICENSE.txt`). Wbudowane w `reportlab` fonty Type1 obsługują
WinAnsi, czyli nie mają `ą`, `ę`, `ł`, `ś`, `ż`, `ź` ani `ń` — dyplom dla Łucji Śniadeckiej
składałby się częściowo z pustych prostokątów.

Przyciski: **Wystaw** przy wierszu (rodzaj z listy) i **Wystaw wszystkim (ZIP)** dla całego etapu.
Obie drogi są **idempotentne**: wpis, który ma już dokument tego rodzaju, zachowuje swój numer —
uczestnik z dwoma numerami na ten sam tytuł miałby problem przy pierwszej rekrutacji, w której
trzeba ten numer podać. Nazwy plików w paczce to numery dokumentów, nigdy nazwiska.

Sekcja **Opiekunowie szkolni** wymienia wyłącznie tych, którzy potwierdzili udział szkoły w edycji
(5.6) — sam adres wpisany przez ucznia jest przesłanką, a nie oświadczeniem nauczyciela.

Uczestnik widzi swoje dokumenty w `/me/certificates/` (link „Dyplomy” w panelu) i pobiera je stamtąd;
opiekun — na dole swojego panelu. Każdy dokument da się sprawdzić **bez logowania** pod
`/dyplomy/<kod>/`: strona potwierdza rodzaj, edycję, numer i datę, a **imienia i nazwiska nie
pokazuje**, dopóki odbiorca nie wyraził zgody na publikację pełnych danych (sprawdzany jest aktywny
`ConsentRecord`, nie samo pole profilu). Zaświadczenie opiekuna nie pokazuje nazwiska nigdy —
opiekun nie przechodzi przez blok zgód uczestnika, a milczenie nie jest zgodą. Nieznany kod nie
daje 404: strona wygląda tak samo i mówi „takiego dokumentu nie ma”, bo rozróżnienie kodem HTTP
zamieniłoby ten adres w narzędzie do sprawdzania kodów maszynowo.

#### Rodzaje dokumentów

Pięć: **laureat**, **finalista**, **uczestnik**, **opiekun** i **warsztaty**. Ostatni poświadcza
fakt spoza toru zawodów — obecność na warsztatach online — i dlatego jest osobnym rodzajem, a nie
„uczestnikiem” z dopiskiem: zdanie na papierze jest tam inne, a pod nim stoi wyliczenie tematów,
terminów i prowadzących.

#### Szablon graficzny — `/coordinator/certificates/templates/`

Dotąd wygląd dyplomu był **kodem**: zmiana winiety przed galą znaczyła poprawkę w
`apps/results/certificates.py` i wdrożenie, a organizator z gotowym projektem z drukarni nie miał
go gdzie wgrać. `results.CertificateTemplate` przenosi tę decyzję do panelu i zostawia w kodzie
wyłącznie skład.

Szablon ma: **tło** (PNG/JPG albo **PDF** — projekt z drukarni bywa w CMYK-u ze spadami i
przerobienie go na obrazek kosztuje jakość; PDF nakładamy przez `pypdf`, skalując złożoną stronę
do formatu projektu), **logo**, do **trzech podpisów** (grafika + imię i nazwisko + funkcja) oraz
**układ** — słownik JSON z położeniem i stopniem pisma każdego bloku.

Dopasowanie do dokumentu idzie od szczegółu do ogółu: **(rodzaj, edycja) → (rodzaj, wszystkie
edycje) → (wszystkie rodzaje, edycja) → (wszystkie, wszystkie) → układ wbudowany**. Rodzaj jest
przed edycją, bo dyplom laureata ma prawo wyglądać inaczej niż reszta dokumentów tego rocznika.
**Brak jakiegokolwiek szablonu jest stanem poprawnym** i przez większość roku normalnym — dokumenty
składają się wtedy układem wbudowanym, tym samym co od pierwszej edycji.

Układ (`apps/results/certificate_layout.py`) liczy `y` od **górnej** krawędzi kartki (842 × 595 pkt,
A4 poziomo), bo tak czyta się dokument i tak mierzy się go linijką na wydruku. Brak `x` znaczy
wyśrodkowanie; `show: false` gasi blok (np. nagłówek nadrukowany już na tle). Szablon zapisuje
**tylko to, co zmienia** — wartości scalają się blok po bloku z domyślnymi, więc dodanie nowego
bloku w kolejnej wersji serwisu nie unieważnia szablonów zapisanych wcześniej.

Przycisk **Podgląd PDF** składa kartę z danymi przykładowymi (numer `OK/<rok>/000`, kod
`PRZYKLADOWY1`) — obejrzenie układu nie zużywa numeru z puli i nie zostawia dyplomu w rejestrze.
**Ustaw jako domyślny dla rodzaju** włącza szablon i wyłącza pozostałe o tym samym zakresie; osobnej
flagi „domyślny” nie ma z premedytacją, bo byłaby drugą prawdą obok `is_active`.

Pliki idą do **prywatnego** storage (ten sam alias `private_media`, co treści zadań). Tło dyplomu
nie jest tajemnicą, ale publiczny bucket to adres, który da się podlinkować — a czysta karta dyplomu
olimpiady krążąca po sieci jest gotowym materiałem do podrobienia.

Na dokumencie stoi też **kod QR** z adresem strony weryfikacji (`reportlab.graphics.barcode.qr`,
bez nowej zależności). Nie zastępuje kodu w stopce i nie może: kod bywa przepisywany do wniosku
ręcznie, a QR jest skrótem drogi dla tych, którzy dostali dyplom jako zdjęcie w telefonie.

#### Zaświadczenia hurtowe: opiekunowie — `/coordinator/supervisors/`

Osobny ekran obok listy kont z filtrem `?role=supervisor`, i to nie jest powtórzenie: tamta lista
odpowiada na „jakie mamy konta”, ta na „komu należy się zaświadczenie”. Pokazuje **liczbę uczniów
w edycji** (bez niej „Wystaw” byłoby decyzją na ślepo) i stan dokumentu. Krąg odbiorców to
opiekunowie, których uczniowie mają w edycji wpis do **któregokolwiek** etapu — zaświadczenie
poświadcza pracę z uczniami, a nie złożenie oświadczenia o udziale szkoły. Przyciski: **Wystaw**
przy wierszu i **Wystaw zaświadczenia wszystkim opiekunom (ZIP)**; obie drogi idempotentne.

#### Obecność na warsztatach i zaświadczenia z warsztatów — `/coordinator/workshops/attendance/`

Warsztaty są jedyną częścią olimpiady, o której baza nie wie nic sama z siebie: harmonogram jest
treścią redakcyjną (blok `schedule` na `/warsztaty/`), a zajęcia odbywają się na platformie wideo.
`cms.WorkshopAttendance` trzyma więc tylko to, czego treść redakcyjna nie umie zapamiętać — **kto
był** — i wiąże to z wierszem harmonogramu przez `workshop_key` = `<data>-<slug tematu>`
(`2026-11-12-kubity-i-bramki-kwantowe`).

Klucz jest z **daty i tematu**, a nie z pozycji wiersza: redaktor dopisze wcześniejszy termin na
początku tabeli albo poprawi godziny, a odhaczone obecności mają zostać przy swoich zajęciach. Cena
jest jawna — zmiana tematu albo daty tworzy nowy klucz, a stara obecność przestaje pasować
(koordynator widzi to jako pustą kolumnę i może ją odhaczyć ponownie).

Ekran to tabela **uczestnicy × warsztaty** z kratkami, stronicowana po 100 osób, z filtrem po
nazwisku i kodzie oraz przyciskiem „zaznacz kolumnę” (osobny plik `static/js/workshop-attendance.js`
— strict CSP nie dopuszcza skryptu inline; bez tego pliku strona działa, tylko odhacza się
pojedynczo). **Zapis obejmuje wyłącznie widoczną stronę**: formularz przysyła same kratki
zaznaczone, więc bez ograniczenia zakresu zapisanie drugiej strony kasowałoby obecności z pierwszej.

Import **CSV** (`kod,warsztat`) dokłada obecności i nigdy ich nie kasuje — listę uczestników
spotkania eksportuje platforma wideo, a przepisywanie trzystu kratek ręcznie po każdych zajęciach
to praca, po której tabela zostaje pusta. Kod uczestnika, nie e-mail: adres jest daną osobową,
która nie ma po co krążyć w plikach po dyskach szkół. Nieznany kod i nieznany klucz są pomijane.

**Wystaw zaświadczenia z warsztatów (ZIP)** wystawia dokument każdemu, kto ma co najmniej jedną
obecność **i** wpis do etapu w tej edycji (dokument musi się do czego przypiąć — warsztat nie jest
etapem, a `Certificate` zna dwa rodzaje odbiorcy: wpis do etapu albo opiekuna). Progu „połowa zajęć”
nie ma: dokument wylicza konkretne tematy i daty, więc sam mówi, ile tego było. Zaświadczenia
z warsztatów widać w panelu uczestnika razem z resztą dokumentów i sprawdza się je tym samym
adresem `/dyplomy/<kod>/`.

Wiersz harmonogramu ma od tej zmiany opcjonalne pole **prowadzący** — na stronie „Warsztaty” jest
kolumną (znika, gdy nikt jej nie wypełnił), a na zaświadczeniu treścią dokumentu.

#### Pieczęć elektroniczna (PAdES)

Kod weryfikacyjny odpowiada na „czy taki dokument wystawiono”. Pieczęć odpowiada na „czy **ten
plik** jest tym, co wystawiono” — uczelnia, która dostaje PDF pocztą, sprawdza go czytnikiem, a nie
przepisywaniem kodu ze zdjęcia. Obie drogi zostają: kod działa na papierze, pieczęć na pliku.

Moduł `apps/results/signing.py` (biblioteka `pyhanko`, zależność zwykła — podpisuje produkcja)
podpisuje dokument w wariancie **PAdES** przy każdym składzie. Konfiguracja przez środowisko
(`.env`, opis w `.env.example`):

| Zmienna | Znaczenie |
| --- | --- |
| `CERT_SIGN_P12_PATH` | ścieżka **w kontenerze** do pliku PKCS#12 z kluczem pieczęci. Pusta = podpisywanie wyłączone |
| `CERT_SIGN_P12_PASSWORD` | hasło do tego pliku |
| `CERT_SIGN_TSA_URL` | adres znacznika czasu (RFC 3161). Bez niego podpis niesie czas z zegara serwera |
| `CERT_SIGN_REASON`, `CERT_SIGN_LOCATION` | powód i miejsce złożenia pieczęci, widoczne we właściwościach podpisu |

Dwie reguły, które są tu ważniejsze od kryptografii:

- **brak konfiguracji nie jest błędem.** Bez `CERT_SIGN_P12_PATH` dokumenty wychodzą niepodpisane —
  to jest stan domyślny i poprawny. Klucz pieczęci to materiał kryptograficzny organizacji; nie ma
  go ani na laptopie dewelopera, ani w testach,
- **awaria podpisu nie wstrzymuje dokumentu.** Wygasły certyfikat, brak pliku na wolumenie,
  milczące TSA — wszystko trafia do logu, a uczestnik dostaje PDF bez pieczęci. Odwrotna decyzja
  znaczyłaby, że pomyłka w konfiguracji zatrzymuje wydawanie dyplomów w dniu gali.

Stan pieczęci zapisuje się przy dokumencie (`Certificate.signed`, `signed_at`, `signer_name`) —
PDF powstaje przy **każdym** pobraniu, więc pieczęć jest własnością chwili składu, a nie rejestru.
Panel uczestnika i strona `/dyplomy/<kod>/` mówią o niej wprost, razem z nazwą pieczętującego
(z podmiotu certyfikatu: nazwa organizacji, a w jej braku CN — pieczęć należy do **podmiotu**, nie
do osoby, i to jej różnica wobec podpisu).

**Skąd wziąć kwalifikowaną pieczęć.** W Polsce wydają ją kwalifikowani dostawcy usług zaufania
z rejestru NCCert prowadzonego przez Ministerstwo Cyfryzacji (m.in. KIR — Szafir, Asseco — Certum,
Eurocert, CenCert, PWPW — Sigillum). Zamawia się **pieczęć elektroniczną dla podmiotu** (a nie
podpis dla osoby): wnioskodawcą jest organizacja, a w certyfikacie stoi jej nazwa i NIP/REGON.
Do użycia po stronie serwera potrzebny jest klucz w postaci pliku **PKCS#12** (`.p12`/`.pfx`) —
przy zamawianiu trzeba to powiedzieć wprost i wybrać wariant „w pliku programowym” (*soft
certificate*), a nie na karcie.

**Ograniczenie, o którym trzeba wiedzieć przed zakupem:** kwalifikowana pieczęć wydana na **karcie
kryptograficznej albo tokenie USB** nie da się użyć po stronie serwera. Klucz nie opuszcza karty,
a karta wymaga obecności przy każdym podpisie — serwer podpisuje bez człowieka, przy każdym
pobraniu dyplomu. Do takiego scenariusza dostawcy mają osobną usługę **podpisu/pieczęci w chmurze**
(zdalne HSM z API); jej podłączenie to inna integracja niż ta i ten moduł jej nie obsługuje.
Jeśli pieczęć kwalifikowana nie wchodzi w grę, zostaje **pieczęć niekwalifikowana** (zwykły
certyfikat do podpisu z pliku): czytnik PDF-a nadal wykrywa naruszenie pliku, tylko bez skutku
prawnego równoważnego pieczęci kwalifikowanej.

Plik `.p12` montuje się do kontenera wolumenem (np. `./secrets/pieczec.p12:/run/secrets/pieczec.p12:ro`)
i **nigdy** nie trafia do repozytorium ani do obrazu.

### 6.13 Statystyki edycji (`/statystyki/`)

Publiczna strona z liczbami o **ogłoszonych** etapach: liczba uczestników w tabeli, rozkład punktów
w każdym zadaniu, średnia i mediana sumy, najwyższy wynik, liczba zakwalifikowanych, próg
kwalifikacji i rozbicie na województwa. Odnośnik stoi w stopce każdej strony.

Odpowiada na pytanie, którego tabela wyników nie obsługuje: tamta mówi „jak wypadłem ja”, ta —
„jak wypadła olimpiada”. Dotąd każdy, kto chciał to wiedzieć (uczeń przed zgłoszeniem, nauczyciel,
dziennikarz), musiał przeliczyć kilkaset wierszy ręcznie albo napisać do organizatora.

- **dane pochodzą wyłącznie z zamrożonego snapshotu** (`ResultsPublication.snapshot`), z tego
  samego JSON-a, który czyta publiczna tabela. Nie z `FinalGrade` i nie ze `StageEntry`. Powody:
  snapshot jest już zanonimizowany i **z definicji** nie zawiera nic, czego nie wolno pokazać;
  liczby zgadzają się z tabelą co do sztuki; etap bez publikacji nie ma tu ani jednego wiersza,
- **próg kwalifikacji liczymy z tabeli**, jako najniższą sumę wśród zakwalifikowanych — nie
  z `QualificationRule`. Reguła bywa hybrydowa („min. 20 pkt **oraz** najlepszych 200”), bywa
  zmieniana po przeliczeniu, a przy trybie „N na województwo” nie ma jednej liczby. Z ogłoszonej
  tabeli wynika natomiast zdanie zrozumiałe bez znajomości regulaminu,
- **województwa pojawiają się tylko przy anonimizacji „kod uczestnika”** — bo tylko wtedy niesie
  je snapshot. Przy inicjałach ze szkołą i przy pełnych nazwiskach okręg celowo z tabeli wypada
  (mnożyłby cechy quasi-identyfikujące), a statystyka nie ma prawa odtwarzać go z innych tabel:
  strona pisze wtedy wprost, że tego rozbicia nie publikuje,
- **wykresy są CSS-em, bez jednej linii JavaScriptu.** Słupek to `<div>` z klasą szerokości
  z zamkniętej listy (skok co 5 %), bo polityka bezpieczeństwa nie dopuszcza `style="width: …"`
  w dokumencie — ten sam zabieg, co przy pasku postępu koordynatora. Obok każdego słupka stoi
  liczba i ta sama tabela z liczbami: czytnik ekranu nie odczyta szerokości `<div>`-a,
- **widok jest wzorcem w `config/urls.py`**, a nie stroną w drzewie CMS-a (tak samo jak
  `/results/<id>/`): treść jest w całości wyliczana, a redaktor nie ma w niej niczego do napisania.
  Adres nie może zależeć od tego, czy ktoś tę stronę utworzył i gdzie ją przeniósł,
- wynik jest **pamiętany przez 10 minut** (`django.core.cache`). Strona bywa linkowana z mediów,
  a policzenie jej to przejście po kilkuset wierszach JSON-a na etap. Ponowna publikacja wyników
  nie unieważnia wpisu natychmiast — dziesięć minut rozbieżności kosztuje mniej niż liczenie
  na każde wejście.

### 6.14 RODO: retencja danych, rejestr czynności, eksport danych

Trzy narzędzia odpowiadające na trzy pytania, które organizatorowi zadaje ktoś z zewnątrz:
„jak długo trzymacie moje dane”, „proszę o rejestr czynności przetwarzania” i „proszę o kopię
moich danych”. Wszystkie trzy istnieją po to, żeby odpowiedź nie była robiona ręcznie.

#### Retencja danych — `/coordinator/retention/`

Okres retencji jest **ustawieniem edycji** (`Edition.data_retention_months`, domyślnie 24 miesiące)
i zmienia się go na ekranie ustawień edycji (`/coordinator/registration/`, obok okna rejestracji).
Termin liczy się od **ostatniego deadline'u etapu tej edycji** plus ten okres — nie od daty
utworzenia edycji (bo edycja żyje rok) i nie od publikacji wyników (bo tę się przesuwa i cofa).
Zero miesięcy wyłącza automat dla rocznika.

Po upływie terminu zadanie okresowe `apps.accounts.retention.anonymise_expired_editions`
(raz na dobę, wpis `anonymise-expired-editions` w `CELERY_BEAT_SCHEDULE`) anonimizuje konta
uczestników tej edycji **tą samą funkcją**, co żądanie z art. 17 (`anonymise_account`): imię,
nazwisko, adres e-mail, telefon, szkoła i rocznik znikają, zostaje pseudonimowy `public_code`,
województwo i cała dokumentacja zawodów. Wykonawcą jest `None` (nikt tego nie żądał — upłynął
termin), a każde konto dostaje wpis `account.anonymised_by_retention` z identyfikatorem edycji:
bez niego w aktach zostawałby sam skutek, bez powodu.

**Czego automat nie rusza** (te przypadki widać na ekranie razem z powodem):

| Powód | Znaczenie |
|---|---|
| `later_edition` | uczestnik startuje w edycji, której retencja jeszcze nie minęła (także bieżącej) |
| `open_appeal` | reklamacja w toku — sprawa jest sama w sobie podstawą przetwarzania |
| `unpublished_results` | etap bez `results_published_at`: zawody nie zostały domknięte |
| `already_anonymised` | konto przeszło już anonimizację (adres w domenie `.invalid`) |

Edycja **bieżąca** nie wchodzi do przebiegu bezwarunkowo, a nie przez sam termin: pomyłka
w ustawieniu (retencja krótsza niż kalendarz rocznika) nie może anonimizować startujących.
Konta komitetu i koordynatora są poza zakresem — to konta funkcyjne, żyją między edycjami.

**Dry-run przed skutkiem.** Anonimizacja jest nieodwracalna, więc obie drogi pokazują plan, zanim
cokolwiek zrobią:

```bash
docker compose exec web python manage.py retention_report   # nic nie zmienia, nie ma flagi, która by to zmieniła
```

Raport wypisuje **kody publiczne**, nigdy adresów e-mail: to wykaz osób, których dane mają
zniknąć, a lista ich adresów byłaby dokładnie tą daną, którą retencja usuwa. Ekran
`/coordinator/retention/` pokazuje to samo (jedna funkcja `retention.plan` zasila oba) i dokłada
przycisk **„Wykonaj teraz”** — ten sam przebieg, synchronicznie, z potwierdzeniem: operacja jest
nieodwracalna, więc jej wynik ma wrócić w odpowiedzi na kliknięcie, a nie do logu workera.

#### Rejestr czynności przetwarzania — `/coordinator/processing-register/`

Dokument wymagany art. 30 ust. 1 RODO, prowadzony **jako dane w kodzie**
(`apps/accounts/processing_register.py`), a nie jako arkusz. Konsekwencje są dwie i to one są
powodem tej decyzji: zmiana w systemie, która zmienia przetwarzanie (nowy odbiorca, nowa kategoria
danych, inny okres retencji), jest zmianą w tym pliku i przechodzi przez tę samą recenzję co kod —
a treść rejestru da się sprawdzić testem (`apps/accounts/tests/test_processing_register.py`
pilnuje, żeby żaden wiersz nie zgubił elementu wymaganego przepisem).

Rejestr obejmuje dziewięć czynności: konta uczestników, dowody zgód, przyjmowanie i ocenianie prac,
ogłaszanie wyników i dokumenty, reklamacje, rozmowy kwalifikacyjne, konta komitetu, zgłoszenia
i pomoc oraz utrzymanie serwisu. Odbiorcy są wymienieni wprost: hosting (Contabo, Niemcy), dostawca
poczty wychodzącej, Google Analytics **wyłącznie po zgodzie** i — przy rozmowach — informacja, że
odbiorcy zewnętrznego nie ma, bo Jitsi Meet stoi na własnym serwerze.

Dane administratora (nazwa, adres, KRS, kontakt) **nie** są w treści rejestru: dokłada je widok
z `cms.SiteSettings`, czyli z tego samego miejsca, co stopka i strona „Kontakt”. `?format=csv`
oddaje ten sam dokument jako plik (BOM UTF-8, średnik — otwiera się w polskim Excelu).

Polityka RODO (`/dokumenty/rodo/`, sekcja 8) wskazuje rejestr jako dostępny od organizatora na
żądanie.

#### Eksport danych uczestnika — `/account/export/`

Prawo do przenoszenia danych (art. 20 RODO) w postaci paczki ZIP: `dane.json` plus katalog
`pliki/` z rozwiązaniami, które ta osoba wgrała. JSON, bo jest odczytywalny maszynowo i zarazem
czytelny dla człowieka bez narzędzi; ZIP, bo dane bez plików nie byłyby kompletem.

W `dane.json` są: konto (bez hasła), profil uczestnika / komitetu / opiekuna szkolnego, **zgody
razem z wersjami dokumentów** i datami wyrażenia oraz wycofania, stan zgody opiekuna, zgłoszenia
do etapów wraz z pracami i metryką plików (`sha256`, rozmiar, typ, wynik skanu, numer wersji),
ogłoszone wyniki (własny wiersz tabeli: suma, miejsce, decyzja), oceny końcowe z komentarzami
recenzentów napisanymi do uczestnika oraz ustawienia interfejsu.

Trzy granice, których paczka nie przekracza:

- **nigdy cudze dane** — ogłoszona tabela jest sprowadzona do własnego wiersza; cała jest jawna
  pod własnym adresem i nie ma powodu, żeby wyjeżdżała z prośby jednej osoby,
- **nigdy komentarze wewnętrzne komitetu** — do paczki wchodzi wyłącznie to, co uczestnik widzi
  w panelu (`apps.results.feedback`), a recenzenci zostają podpisani „Recenzent A/B”,
- **nigdy poświadczenia** — ani hasła (nawet w postaci skrótu), ani tokenów, ani powiązań OAuth.

Limit: **jedna paczka na 10 minut na konto** (nie na adres IP — cała pracownia szkolna wychodzi
spod jednego adresu). Przekroczenie kończy się 429 z `Retry-After`. Audyt: `account.exported`
z samymi liczbami, bez treści.

Koordynator wydaje tę samą paczkę dowolnego konta przyciskiem na `/coordinator/accounts/<pk>/`
(wniosek z art. 20 przychodzi też listem — od osoby, która akurat nie może się zalogować).
Zakres danych jest identyczny; różni się wyłącznie akcja w audycie:
`account.exported_by_coordinator`.

### 6.15 Zgłoszenia i pomoc (support desk) oraz FAQ

Na dole każdej strony stoi adres organizatora, ale poczta gubi kontekst i gubi sprawę: uczestnik
pisze „nie mogę wysłać pracy”, a organizator nie wie ani kim jest nadawca (adres prywatny bywa
inny niż adres konta), ani w którym etapie jest zapisany, ani co robił minutę wcześniej — i musi
to odpytać zwrotnie. Dwa dni później nikt nie pamięta, czy odpowiedź poszła.

| Kto | Gdzie | Co |
|---|---|---|
| Każdy zalogowany | „Zgłoś problem” w pasku konta → `/support/` | lista własnych spraw |
| Każdy zalogowany | `/support/new/` | nowe zgłoszenie (bez CAPTCHY — konto jest już tym kosztem) |
| **Bez konta** | `/support/new/` (odnośnik w stopce) | to samo plus pole adresu e-mail i blok antyspamowy rejestracji |
| Zgłaszający | `/support/<id>/` | wątek sprawy z formularzem dopisku |
| Koordynator | `/coordinator/support/` | kolejka: otwarte na górze, w obrębie stanu od najstarszego; filtry `?status=`, `?category=` |
| Koordynator | `/coordinator/support/<id>/` | wątek, odpowiedź (z opcją zamknięcia) i „Zamknij bez odpowiedzi” |

Kategorie: konto i logowanie, wysyłka pracy, wyniki, rejestracja, inne. Stany: **otwarte →
odpowiedziane → zamknięte**, przy czym dopisek zgłaszającego **wraca sprawę do „otwarte”** — bez
tej reguły sprawa, do której ktoś napisał „to nadal nie działa”, znikałaby z kolejki na zawsze.
Zgłoszenie zamknięte nie przyjmuje już wypowiedzi z żadnej strony: zamknięcie jest decyzją
organizatora i ma zostać decyzją.

**Kontekst techniczny** zbiera się automatycznie i jest widoczny **tylko dla organizatora**: adres
strony, z której przyszło zgłoszenie, nazwa przeglądarki, język interfejsu, kod publiczny
uczestnika, identyfikatory etapów, w których jest zapisany, oraz **nazwa** ostatniej czynności
z dziennika zdarzeń z ostatnich 10 minut. Nie wchodzą tam: ciasteczka, nagłówki uwierzytelnienia,
tokeny, zawartość formularzy ani treść wpisu audytowego. Formularz mówi o tym wprost — zbieramy
dane, o które nikt nas nie prosił.

**Poczta.** Nowe zgłoszenie idzie powiadomieniem na adres kontaktowy organizatora z
`cms.SiteSettings` (Ustawienia → Dane serwisu; `DEFAULT_FROM_EMAIL` jest adresem **nadawcy**
`noreply@…`, więc list lądowałby w skrzynce, której nikt nie czyta). Odpowiedź organizatora idzie
na adres zgłaszającego. **Żaden z tych listów nie niesie treści** — tylko informację, że sprawa
albo odpowiedź jest, i odnośnik: zgłoszenie bywa opisem cudzego problemu z danymi, a skrzynka nie
jest miejscem na jego kopię. Obie wysyłki jadą przez `queue_mail` (po commicie), więc niedostępny
MTA nie zamienia zgłoszonej sprawy w błąd 500.

Audyt: `ticket.opened` (kategoria, długość opisu, czy anonimowe), `ticket.answered` (długość),
`ticket.closed` (kategoria) — nigdy treść. Pulpit koordynatora ma licznik otwartych zgłoszeń;
jest **bez zakresu edycji**, bo najdłużej czeka zwykle ktoś, kto nie ma jeszcze ani konta, ani
wpisu w żadnym etapie.

Skasowanie konta w całości (art. 17, konto bez śladu w zawodach) zabiera jego zgłoszenia
(`CASCADE`) — to jego korespondencja, nie dokument zawodów. Anonimizacja ich nie rusza: wiersz
konta zostaje, tylko bez danych osobowych.

#### FAQ — `/faq/`

Typ strony Wagtaila (`cms.FAQPage`) z listą pytań pogrupowanych w sekcje. Każde pytanie jest
elementem `<details>` z **trwałą kotwicą** (`#pytanie-<id>`, z identyfikatora wiersza, nie
z treści), więc odpowiedź na zgłoszenie może odesłać do konkretnego pytania, a odnośnik przeżyje
poprawkę sformułowania. Rozwijanie działa klawiaturą i bez JavaScriptu — przy polityce CSP bez
kodu inline to jedyne rozwiązanie, które nie wymaga własnego pliku skryptu.

Treść zakłada `manage.py seed_legacy_content` (dziesięć pytań: aktywacja i spam, literówka
w adresie, zgoda opiekuna, formaty plików razem z JPEG-iem dla zdjęć rozwiązań pisanych ręcznie,
nowa wersja pracy zastępująca poprzednią w ocenianiu, skan antywirusowy, terminy wyników,
reklamacje, brak nazwisk w tabeli, rola opiekuna szkolnego). Obowiązują te same reguły ochrony,
co przy stronach treści: strona **zredagowana** albo **skasowana w /cms/** zostaje nietknięta,
a `--force` je wyłącza. Redakcja dopisuje pytania w `/cms/`.

FAQ jest w pełnym menu serwisu i w stopce, ale **nie** w przyklejonym pasku nawigacji
(`PRIMARY_MENU_SLUGS`): pasek trzyma to, czego szuka się w trakcie zawodów. Formularz zgłoszenia
zaczyna się od odnośnika „Zanim zgłosisz: FAQ”.

### 6.16 Baner komunikatów i strona statusu

#### Komunikaty — `/coordinator/announcements/`

Pasek pod nagłówkiem, na **każdej** stronie serwisu, także dla niezalogowanych. To inna wiadomość
niż aktualność: aktualność czyta ten, kto wejdzie na `/aktualnosci/`, a komunikat („przedłużamy
termin do piątku”, „logowanie przez Google nie działa”) musi zobaczyć każdy, kto jest w serwisie.

Komunikat to ≤ 500 znaków **zwykłego tekstu** (autoescapowanego) plus opcjonalny odnośnik jako
**para pól** (adres + etykieta; sam adres bez etykiety się nie pokazuje). Trzy wagi: `info`,
`warning`, `danger` — ta ostatnia dostaje `role="alert"`, więc czytnik ekranu przeczyta ją od razu.
Widoczność wyznacza okno czasowe (`starts_at` / `ends_at`, puste „do” = do wyłączenia) oraz
wyłącznik `is_active` jako hamulec awaryjny. Puste „od” znaczy „od zaraz”.

`dismissible` decyduje, czy czytelnik może baner zamknąć; wybór pamięta `localStorage`
(`static/js/announcements.js`, klucz per identyfikator komunikatu), a nie cookie ani konto — to
preferencja widoku na tym urządzeniu. **Bez JavaScriptu baner jest w pełni sprawny**: serwer rysuje
go od razu, a przycisk zamknięcia ma w HTML-u `hidden` i odsłania go dopiero skrypt (przycisk,
który nic nie robi, byłby atrapą). Komunikat niezamykalny przycisku nie dostaje w ogóle.

Odczyt jest pamiętany **60 sekund** (`django.core.cache`) — baner renderuje się na każdej stronie —
a zapis i skasowanie unieważniają tę pamięć sygnałem, więc ogłoszenie jest widoczne natychmiast.
Panel redakcyjny (`/cms/`) i panel Django baneru nie dostają: mają własną ramę. Model jest też
zarejestrowany jako snippet Wagtaila (`/cms/` → Komunikaty) — redakcja bywa kimś innym niż
organizator zawodów. Audyt: `announcement.created` / `announcement.updated` /
`announcement.deleted` z wagą i stanem, nigdy z treścią.

#### Strona statusu — `/status/` i `/status.json`

Publiczna, bez logowania, buforowana 30 sekund. Odpowiada na pytanie zadawane o 23:40 przed
deadline'em: „nie mogę wysłać pracy — to u was, czy u mnie?”. To co innego niż `/healthz/`, które
odpowiada **orkiestratorowi** kodem HTTP; tu odpowiedź jest dla człowieka i ma treść.

Pokazuje: stan czterech podsystemów, **czas na serwerze w czasie polskim** (to on rozstrzyga
o przyjęciu pliku, a nie zegarek na telefonie), stan rejestracji, bieżący etap z terminem oddania
oraz komunikaty organizatora.

| Podsystem | Sprawdzenie |
|---|---|
| Baza danych | `SELECT 1` |
| Sesje i pamięć podręczna | zapis i natychmiastowy odczyt klucza kontrolnego |
| Magazyn wgranych prac | `HeadBucket` na S3/MinIO (lokalnie: istnienie katalogu) |
| Kolejka zadań | wiek pulsu z `apps.core.tasks.heartbeat` (beat co minutę, próg 3 minuty) |

Kolejka jest sprawdzana **pulsem**, a nie synchronicznym `inspect ping`: takie pytanie czeka na
odpowiedź, więc strona wisiałaby dokładnie wtedy, gdy worker nie żyje — czyli w jedynym przypadku,
dla którego istnieje. Zadanie `heartbeat` (wpis `heartbeat` w `CELERY_BEAT_SCHEDULE`) zapisuje
znacznik czasu w cache'u; żeby wpis powstał, musi zadziałać **cała** droga: beat → broker → worker
→ cache.

Na stronie **nie ma** nazw hostów, wersji bibliotek ani treści błędów — jedyną informacją
o infrastrukturze jest binarne „działa / nie działa”. Werdykt ogólny wymaga **wszystkich**
podsystemów: uczestnikowi z niedziałającym magazynem nie pomaga to, że baza ma się dobrze.

`/status.json` oddaje ten sam stan dla monitoringu zewnętrznego (jedna funkcja `snapshot`, dwa
renderery — rozjazd znaczyłby, że jedno z dwojga kłamie). Kod odpowiedzi jest **zawsze 200**,
a werdykt niesie pole `status` (`ok` / `degraded`): monitor, który dostaje 503, uznaje zwykle, że
niedostępna jest sama strona statusu, i przestaje czytać jej treść. Osobny adres, a nie
`?format=json`, bo monitory konfiguruje się adresem — ten sam powód, co przy `/me/calendar.ics`.

### 6.17 Testy online (etap w formie `QUIZ`)

Trzecia forma etapu obok rozwiązań pisemnych i rozmowy kwalifikacyjnej (`StageFormat.QUIZ`,
aplikacja `apps.quiz`). Różni się od pozostałych tym, czym one różnią się od siebie: **skąd biorą
się punkty**. W etapie pisemnym wystawia je recenzent, w rozmowie — komisja, a tutaj nie ma ich kto
wystawić: liczy je serwer w chwili zakończenia podejścia. Dlatego etap w tej formie nie ma zadań do
oddania ani przydziałów recenzenckich.

Wszystko jest kluczowane **etapem** (`Quiz.stage` jeden-do-jednego) i **wpisem do etapu**
(`QuizAttempt.entry`), nigdy uczestnikiem wprost — to jedyna postać, która przetrwa planowany
podział serwisu na wiele konkursów bez przenumerowania danych.

#### Ekrany koordynatora

| Adres | Do czego |
|---|---|
| `/coordinator/stages/<id>/quiz/` | ustawienia testu (zakłada test, jeśli etap go nie ma) |
| `/coordinator/stages/<id>/quiz/questions/` | lista pytań w pulach, dodanie, zmiana, usunięcie |
| `/coordinator/stages/<id>/quiz/import/` | import pytań z Markdowna albo CSV (format opisany na stronie) |
| `/coordinator/stages/<id>/quiz/preview/` | „Podgląd jako uczestnik” — arkusz bez zakładania podejścia |
| `/coordinator/stages/<id>/quiz/results/` | wyniki per uczestnik, statystyka pytań, eksport CSV, „Przelicz punkty” |

Ekran ustawień działa także dla etapu, który **nie** ma jeszcze formy `QUIZ`: arkusz przygotowuje
się zwykle przed decyzją regulaminową o formie zawodów. Zapis testu formy etapu nie przestawia —
to osobna decyzja, na ekranie terminów etapu; inaczej jedno wejście „na próbę” zabierałoby
uczestnikom upload rozwiązań.

Najważniejsze ustawienia i to, co z nich wynika:

- **czas trwania** — licznik jednego podejścia. Termin podejścia to wcześniejszy z dwóch:
  `start + czas trwania` i koniec okna testu, więc kto zaczyna pięć minut przed zamknięciem,
  dostaje pięć minut, a nie pełną godzinę,
- **okno testu** — puste pola znaczą „jak etap”. Osobne terminy są po to, żeby sesja testowa mogła
  być krótszym wycinkiem etapu (finał trwa cztery dni, test dwie godziny drugiego dnia),
- **pytań z każdej puli** — losowanie zestawów. Pula to temat nadawany przy pytaniu; z każdej puli
  ciągnie się zadaną liczbę pytań. Losowanie z jednego worka potrafiłoby dać komuś pięć pytań
  z jednego tematu i ani jednego z drugiego, czyli dwa nieporównywalne testy przy tej samej liczbie
  pytań. Ekran ostrzega, gdy pula jest mniejsza od limitu albo gdy pytania w losowanej puli są
  warte różnie (wtedy o maksimum decyduje losowanie, a nie wiedza),
- **podłoga punktów ujemnych** — `QUESTION` (domyślnie): błąd w jednym pytaniu nie zabiera punktów
  z innego; `QUIZ`: ujemne przenoszą się między pytaniami, a zera pilnuje dopiero suma. **Brak
  odpowiedzi nigdy nie jest karany** — inaczej punkty ujemne karałyby za to zachowanie, do którego
  mają zachęcać, gdy uczestnik nie zna odpowiedzi,
- **kiedy pokazać wynik** — `NEVER` (tylko w ogłoszonej tabeli etapu), `AFTER_CLOSE`, `IMMEDIATELY`.

Rodzaje pytań: jednokrotny wybór, wielokrotny wybór (z oceną „wszystko albo nic” lub proporcjonalną
— trafienia **minus** pomyłki, bo bez odejmowania pomyłek „zaznacz wszystko” dawałoby komplet
punktów), krótka odpowiedź tekstowa (lista uznawanych zapisów + flagi normalizacji: wielkość liter,
spacje, polskie znaki) i odpowiedź liczbowa (tolerancja bezwzględna **i** względna, działające
alternatywnie — wystarczy zmieścić się w jednej). Pytanie otwarte, które musi przeczytać człowiek,
nie jest testem, tylko zadaniem, i idzie zwykłą ścieżką.

**Zestawu pytań nie da się zmienić po pierwszym podejściu** (`QUIZ_HAS_ATTEMPTS`): zestawy są
losowane i zapisane przy starcie, więc dopisanie pytania zmieniałoby to, co wylosują następne
osoby, a skasowanie zostawiałoby w cudzym zapisanym zestawie identyfikator, którego już nie ma.
Poprawka **klucza odpowiedzi** jest natomiast dozwolona i to jest osobna droga: klucz bywa błędny
i wychodzi to dopiero z wyników. Po poprawce trzeba kliknąć **„Przelicz punkty”** — wynik nie
zmienia się sam, bo podejścia są ocenione w chwili zakończenia. Przeliczenie zostawia wpis audytowy
`quiz.regraded` z liczbą podejść i liczbą zmienionych wyników (bez danych osobowych); to jedyny
dokument, którym organizator wytłumaczy, dlaczego wyniki wyglądają inaczej niż wczoraj.

Ekran wyników podaje przy każdym pytaniu **trudność** (odsetek odpowiedzi w pełni poprawnych wśród
udzielonych) i **moc różnicującą** (różnica trudności między lepszą i słabszą połową uczestników).
Ujemna moc różnicująca prawie zawsze znaczy błąd w kluczu albo dwuznaczną treść — bez tej kolumny
wyszłoby to dopiero z reklamacji.

#### Ścieżka uczestnika

Wejście z zakładki „Zadania” w panelu (`/me/`), gdy etap bieżący jest w formie testu. Dalej:
strona startowa z zasadami (`/me/stages/<id>/test/`) → arkusz (`/me/test/<id>/`) → podsumowanie
(`/me/test/<id>/wynik/`). Rozpoczęcie jest **POST-em**, nie odnośnikiem: jedno wejście zużywa
podejście i uruchamia licznik, a prefetch przeglądarki albo podgląd linku w komunikatorze nie może
komuś rozpocząć zawodów.

Arkusz to **jedna strona ze wszystkimi pytaniami** i przyklejonym licznikiem. Strona z jednym
pytaniem wymagałaby żądania między pytaniami, więc na słabym łączu każdy powrót kosztowałby czas
z licznika, a zerwane połączenie zostawiałoby uczestnika w środku testu bez drogi dalej.

Odpowiedzi zapisują się przez `fetch` co 20 sekund i po każdej zmianie (`static/js/quiz.js`, nonce
CSP, token CSRF z ciasteczka). **Bez JavaScriptu arkusz działa jako zwykły formularz**: odpowiedzi
idą na serwer razem z przyciskiem „Zakończ test”, a licznik pokazuje czas z chwili wczytania strony
(wpisuje go serwer). To jest wymaganie, nie ambicja — część zawodów odbywa się w pracowniach
szkolnych z zablokowanymi skryptami.

O czasie rozstrzyga **wyłącznie serwer**. Po `deadline_at` + 30 s tolerancji sieciowej zapis jest
odrzucany (`QUIZ_ATTEMPT_EXPIRED`), a podejście domykane ze statusem „czas minął” — **razem z tym,
co zdążyło się zapisać**. Tolerancja nie jest przedłużeniem testu, tylko uznaniem, że między
kliknięciem a dotarciem żądania upływa czas. Podejście porzucone (zamknięty laptop) domyka
`finalise_overdue` przy najbliższym wejściu na test, na ekranie wyników koordynatora i przy
przeliczaniu wyników etapu — bez tego praca kogoś, komu padło łącze, weszłaby do protokołu jako zero.

Zabezpieczenia podstawowe: jedno aktywne podejście na osobę i test (częściowy indeks unikalny
w bazie, więc dwie karty przeglądarki nie otworzą dwóch), zestaw i kolejność wariantów losowane raz
i **zapisane**, warianty tasowane po stronie serwera, a klucz odpowiedzi nie trafia do HTML-a
uczestnika w ogóle — kontekst szablonu dostaje pytania jako słowniki bez `is_correct`
i bez `settings`, więc nie ma go czym wypisać nawet przez nieostrożną pętlę.

#### Jak punkty wchodzą do wyników etapu

Jednym, wąskim szwem. `apps.quiz.services.stage_scores(stage) -> {entry_id: punkty}` jest jedynym
wejściem testów do tabeli wyników, a `apps.results.services.compute_stage_results` pyta o nie
tylko wtedy, gdy `stage.format == QUIZ` (hook `_quiz_scores`, import lokalny — zależność ma być
widoczna i łatwa do odcięcia). Suma z testu **zastępuje** sumę z zadań, a kolumny zadań zostają
puste: kolumnami tabeli wyników są zadania, a pytania testu są ich zbyt drobnym odpowiednikiem —
rozbicie na pytania stoi na własnym ekranie.

Trzy reguły zapisane w `stage_scores`:

- liczy się **najlepsze** podejście (przy `attempts_allowed > 1` kolejne podejście ma sens tylko
  wtedy, gdy może poprawić wynik),
- podejścia przeterminowane liczą się normalnie; pomijane są wyłącznie te wciąż trwające,
- wynik jest **zaokrąglany do pełnych punktów** (w górę przy połówce), bo `StageEntry.total_points`
  jest polem całkowitym wspólnym dla wszystkich form etapu. Wynik dokładny, z częściami setnymi,
  zostaje na ekranie wyników testu.

Dalej etap zachowuje się jak każdy inny: próg kwalifikacji, symulacja, publikacja i anonimizacja
idą tą samą drogą co przy etapie pisemnym. Podgląd (`preview=True`, symulacja progu) niczego nie
zapisuje — także dla etapu w formie testu.

## 7. Testy i kontrola jakości

> Te same kroki wykonuje automatycznie **`.github/workflows/ci.yml`** przy każdym push i pull
> requeście (ruff, `makemigrations --check`, `msgfmt --check`, pełny pytest z Postgresem,
> `docker build` obrazu produkcyjnego). Wdrożenie produkcyjne jest osobnym, **ręcznie**
> uruchamianym workflow – opis razem z listą sekretów: [`docs/OPERACJE.md`](docs/OPERACJE.md) § 4.

```bash
# Testy jednostkowe i integracyjne (w kontenerze – tak jak w CI)
docker compose exec -T web pytest -q

# Pokrycie
docker compose exec -T web pytest -q --cov=apps --cov-report=term-missing:skip-covered

# Lint i formatowanie (host, wirtualne środowisko w backend/.venv – Python 3.14, jak obraz).
# Venv (także po zmianie wersji Pythona – stary trzeba skasować; za proxy TLS: --system-certs):
#   cd backend && rm -rf .venv && uv venv --python 3.14 .venv && uv pip install --python .venv -r pyproject.toml --extra dev
cd backend && .venv/Scripts/ruff.exe format . && .venv/Scripts/ruff.exe check .
# Linux/WSL: cd backend && ruff format . && ruff check .

# Spójność migracji
docker compose exec -T web python manage.py makemigrations --check --dry-run

# Konfiguracja compose (składnia, zmienne, montowania) i generator konfiguracji Caddy'ego.
# Ten drugi jest testem powłoki, a nie pytestem: kontekstem budowania obrazu jest backend/,
# więc katalogu scripts/ w kontenerze nie ma – narzędzie sprawdza się tam, gdzie działa.
docker compose config -q
bash scripts/tests/render_caddyfile_test.sh

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
scripts/            deploy.sh, backup.sh, restore.sh, render_caddyfile.sh, e2e.sh
scripts/tests/      testy powłoki (render_caddyfile_test.sh) – uruchamiane na hoście, nie w obrazie
agent/              szkielet pętli agentycznej (LangGraph) – patrz PROJEKT.md 3
docker-compose.yml          produkcja/staging
docker-compose.dev.yml      nakładka developerska (kod z hosta, porty, profil e2e)
.gitleaks.toml              konfiguracja skanu sekretów wraz z uzasadnieniem wyjątków
```
