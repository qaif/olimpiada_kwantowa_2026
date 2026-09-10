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
7. **Poczta wychodząca (SMTP).** Bez niej nie działa reset hasła („Nie pamiętasz hasła?”) – jedyna
   droga odzyskania konta dla uczestnika, recenzenta, komisji i koordynatora. Domyślnie obsługuje ją
   usługa `mail` (własny Postfix z DKIM) – sekcja 4.1. Sama usługa nie wystarczy: bez rekordów
   SPF/DKIM/DMARC i PTR listy trafiają do spamu albo są odrzucane – sekcja 4.2.

Certyfikat Let's Encrypt Caddy pobiera sam przy pierwszym starcie – wymaga otwartych portów 80 i 443
i poprawnego DNS-u dla obu nazw.

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
| `EMAIL_URL` | `consolemail://` (dev `.env`: `smtp://mailpit:1025`; produkcja z `deploy.sh`: `smtp://mail:587`) | poczta wychodząca – patrz 4.1 |
| `DEFAULT_FROM_EMAIL` | `noreply@localhost` (dev `.env`: `olimpiada@localhost`) | nadawca listów (także `SERVER_EMAIL`); domena musi mieć SPF/DKIM – patrz 4.2 |
| `EMAIL_TIMEOUT` | `10` | limit sekund na połączenie SMTP (wysyłka jest synchroniczna w żądaniu) |
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

## 5. Role i przepływ etapu

Cztery grupy Django: `participant`, `reviewer`, `appeals`, `coordinator`. Pełna macierz uprawnień –
`docs/PROJEKT.md` 2.3. Konto komitetu powstaje wyłącznie na kod zaproszenia; konto uczestnika –
z otwartej rejestracji.

| # | Krok | Kto | Ekran / endpoint |
|---|---|---|---|
| 0 | Terminy etapu i arkusz zadań | koordynator | `/coordinator/` → „Edytuj terminy”, „Zadania (n)” – patrz 6.3 |
| 1 | Rejestracja uczestnika | uczestnik | `/register/` → `POST /api/auth/register/participant/`; okno rejestracji ustawia koordynator — patrz 6.3a |
| 2 | Rejestracja członka komitetu na kod | recenzent / komisja | `/register/committee/`; kod z `manage.py create_invitation` albo z panelu koordynatora |
| 3 | Zatwierdzenie konta `PENDING` | koordynator | `/coordinator/` → „Komitet – oczekujący na zatwierdzenie” |
| 4 | Zapis do eliminacji | uczestnik | `/me/` → „Zgłoś się do etapu eliminacyjnego” |
| 5 | Upload rozwiązania (przed deadline) | uczestnik | `/me/`, karta zadania (HTMX) → `POST /api/stages/<id>/problems/<n>/submissions/` |
| 6 | Skan antywirusowy | Celery → ClamAV | status pliku w karcie zadania: `oczekuje na skan` → `czysty` |
| 7 | Zamknięcie etapu | `beat` po `deadline_at + grace_seconds`, albo koordynator ręcznie | `/coordinator/` → „Zamknij etap” |
| 8 | Przydział 2 recenzentów (ślepy, bez konfliktu województwa) | koordynator | `/coordinator/` → „Przydziel recenzentów” |
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
ma domyślną skalę 0/2/5/6 i próg kwalifikacji. **Skalę i próg** zmienia się dalej w
`/admin/competitions/stage/<id>/change/` (link „Skala i próg (admin)” na karcie etapu) — to
konfiguracja oceniania, którą rusza się raz na edycję, a nie kalendarz.

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
bez wydania aplikacji. Regułę wieku liczymy po roczniku i zachowawczo: osoba urodzona osiemnaście
lat temu może mieć jeszcze 17 lat, więc zgoda opiekuna jest od niej wymagana.

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

#### 6.3b Słownik szkół (SIO/RSPO)

Pole „Szkoła” w `/register/` i w dokończeniu rejestracji przez Google/Facebooka to **wyszukiwarka
po rejestrze**, a nie wolny tekst. Uczestnik pisze fragment nazwy albo miejscowości, dostaje do
20 podpowiedzi zawężonych do wybranego województwa (`GET /api/schools/?q=&voivodeship=&limit=`,
bez logowania, throttle `schools`) i wybiera jedną. Wyszukiwanie jest odporne na diakrytyki –
„lodz” znajduje „ŁÓDŹ” – i wymaga trafienia **każdym** wpisanym słowem.

Po co: wolny tekst nie grupuje. „II LO w Krakowie”, „2 LO Kraków” i „Liceum nr 2” to dla bazy
trzy różne szkoły, więc próg k-anonimowości w publikacji wyników (`INITIALS_SCHOOL`, 7.4) nie ma
czego zliczyć. Wybór ze słownika zapisuje w profilu nazwę **przepisaną z rejestru** oraz
dowiązanie `Participant.school_ref`.

**Szkoły spoza wykazu są dopuszczone i to jest świadome.** Checkbox „Mojej szkoły nie ma na
liście” odsłania pole „Nazwa szkoły” (min. 3 znaki) i profil powstaje bez dowiązania. Rejestr
ministerialny nie zna szkół zagranicznych ani placówek założonych po dacie wykazu, a jego
nieaktualność nie może zamykać drogi do olimpiady. Strona działa też **bez JavaScriptu**: pole
wolnego tekstu jest wtedy widoczne od początku.

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
- `allowed_formats` to pola wyboru `pdf`/`ipynb`/`py` (minimum jedno), `max_file_mb` to 1–100 MB,
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
edycji: rodzaj, otwarcie, termin oddania, termin recenzji, okno reklamacji, miejsce i stan.
Nie ma tam ani jednej daty wpisanej ręcznie, więc zmiana w panelu jest widoczna od następnego
odświeżenia strony (żadnego cache). Bez bieżącej edycji lub bez etapów blok pokazuje „Terminy
zostaną ogłoszone”. Ta sama zasada obowiązuje oś czasu na stronie głównej.

Kolejność przy zakładaniu środowiska: `seed_edition_kwantowa --make-current` (etapy) →
`seed_legacy_content` (strony, w tym `/harmonogram/`). Odwrotna kolejność też działa — blok czyta
bazę przy każdym żądaniu, a nie przy imporcie treści.

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

# Strony, dokumenty, aktualności, hasło i sekcja kroków na stronie głównej, kolejność menu,
# sekcja /dokumenty/, strona /partnerzy/ i przekierowania ze starych adresów dokumentów.
docker compose exec web python manage.py seed_legacy_content

# Dołożenie POJEDYNCZEJ strony na działającym serwisie — bez nadpisywania pozostałych treści
# (pełny przebieg skasowałby poprawki wpisane w /cms/ od ostatniego importu).
# Tak wgrywa się na produkcję wzór zgody opiekuna:
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
   wersją ostateczną. Do rozstrzygnięcia zostają też: czy organizator chce PDF-a do wydruku obok
   strony, czy skan na `contact@qaif.org` wystarcza jako droga dostarczenia oraz czy podpisany
   dokument ma być wymagany od wszystkich niepełnoletnich, czy dopiero na etapie stacjonarnym.
   Po zatwierdzeniu: podmiana pliku źródłowego, `GUARDIAN_VERSION`
   w `backend/apps/accounts/consents.py` i metryki w `seed_legacy_content`, potem
   `manage.py seed_legacy_content --only zgoda-opiekuna`.
6. **Osoby odpowiedzialne za ochronę małoletnich.** § 9 i § 10 standardów wymagają wskazania ich
   imiennie uchwałą Zarządu i przyjęcia wzoru karty interwencji.
7. **Skład komitetów.** Szesnaście nazwisk i zakresy odpowiedzialności potwierdza PDF organizatora,
   więc dokument `/dokumenty/komitety/` jest opublikowany. Do decyzji zostają: funkcje i afiliacje członków,
   podwójne członkostwo dwóch osób (Paweł Gora, Grzegorz Czelusta figurują w obu komitetach)
   i nazewnictwo — „Komitet Główny” ze starej strony głównej nie istnieje ani w regulaminie,
   ani w PDF-ie.
8. **Partnerzy — logotypy są, poziomy współpracy do potwierdzenia.** Organizator przekazał sześć
   logotypów (FUW, PCSS, CFT PAN, IF PAN, Uniwersytet Gdański, AIQLAB Institute) i adresy stron,
   ale **nie podał poziomu współpracy ani opisu**. `seed_partners` wpisuje wartości wstępne
   (uczelnie i instytuty jako „partner naukowy”, PCSS i AIQLAB jako „partner instytucjonalny”);
   ostateczny podział, opisy i kolejność ustawia redakcja w `/cms/` i kolejny przebieg komendy tego
   nie cofnie. Do decyzji zostają też progi sponsoringu oraz nazwy ze starej strony: czy
   Ministerstwo Edukacji i Polskie Towarzystwo Fizyczne to realne patronaty (trzecia nazwa,
   „Uniwersytet Kwantowy”, to instytucja nieistniejąca) — żadnej z nich na serwisie nie ma.
9. **ZOZ (Zasady Organizacji Zawodów).** Regulamin odwołuje się do nich kilkanaście razy,
   a dokument nie istnieje — bez niego brakuje progów, liczby finalistów i reguł remisów.
10. **Status prawny olimpiady.** Regulamin zastrzega, że tytuły finalisty i laureata są wewnętrzne
   i nie dają uprawnień ustawowych. Gdzie portal ma to komunikować?
11. **Krok 2 na `/jak-zaczac/`.** Tekst mówi „Załóż konto uczestnika w czasie rejestracji”, a portal
    używa kodów zaproszeń dla komitetu i samodzielnej rejestracji uczestnika — brzmienie do
    potwierdzenia przez organizatora (import nie redaguje treści).
12. **Kanały kontaktu.** Jeden adres `contact@qaif.org` obsługuje sprawy ogólne, RODO i zgłoszenia
    dotyczące bezpieczeństwa małoletnich, rozróżniane tylko tematem wiadomości.
13. **Aktualności.** Trzy przeniesione wpisy to jednozdaniowe zapowiedzi bez dat (oryginał nie miał
    `post_date`) — mają datę importu i dopisek „Wpis przeniesiony ze starej strony”. Do decyzji,
    czy przepisać je z prawdziwymi datami, czy zacząć newsroom od zera.
14. **Logo, favicon, og:image.** Stara strona nie ma ani jednego pliku graficznego — identyfikację
    trzeba zaprojektować od zera. Logotyp organizatora (Fundacja Quantum AI) jest już w stopce:
    wgrywa go `seed_partners` do `SiteSettings.organizer_logo`.
15. **Harmonogram warsztatów.** Szesnaście warsztatów online (`/harmonogram/`) pochodzi z listy
    organizatora podanej w formacie amerykańskim. Jedna data jest niejednoznaczna: „Podstawy
    metrologii kwantowej” przyszła jako `09/01/2027`; w ciągu sobotnich terminów pasuje
    **9 stycznia 2027** i tak jest zapisana, ale wymaga potwierdzenia — podobnie jak godziny tego
    warsztatu, których organizator nie podał (tabela mówi „do potwierdzenia”). Do akceptacji jest
    też zdanie wprowadzające („Warsztaty online przygotowujące do zawodów; udział jest bezpłatny.
    Szczegóły i linki do spotkań ogłosimy w aktualnościach.”) — sformułowaliśmy je sami,
    nie pochodzi od organizatora.

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

**Czego jeszcze nie ma:** ocen z rozmowy. Etap w tej formie nie ma ścieżki oceniania w systemie —
punkty wpisuje koordynator poza nim (`docs/BACKLOG.md`).

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
