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
  szkoła, okręg, rok urodzenia, zgoda RODO. Bez zgody RODO konto **nie powstaje**,
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

### 6.6 Import treści starej strony

Treści serwisu WordPress „Olimpiada Kwantowa” są przeniesione do CMS-a dwiema komendami. Obie są
idempotentne, obie są **narzędziami importującymi**, a nie trybem pracy redakcyjnej: powtórny
przebieg nadpisuje treść stron tym, co jest w plikach źródłowych, więc kasuje poprawki wpisane
w międzyczasie w `/cms/`.

Kolejność przy wdrożeniu: `migrate` → `seed_regulamin` → `seed_legacy_content`. Druga komenda
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

# Edycja „I edycja 2026/2027” z trzema etapami wg harmonogramu starej strony.
# Bez --make-current edycja NIE staje się bieżąca (na devie bieżąca zostaje edycja z seed_demo).
docker compose exec web python manage.py seed_edition_kwantowa [--make-current]
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

**Partnerzy** (`/partnerzy/`, typ `PartnersPage`, pozycja menu przed „Kontaktem”) są opublikowani
z **pustą** listą. Stara strona wymieniała trzy nazwy, z których jedna — „Uniwersytet Kwantowy” —
to instytucja nieistniejąca, a pozostałe dwie nie mają potwierdzonego patronatu; poprzedni import
zostawiał je w treści i chował całą stronę jako szkic (404). Teraz jest odwrotnie: strona żyje,
sekcja „Zostań partnerem” jest dostępna, a lista partnerów zaczyna się pusta i wypełnia ją
redakcja w `/cms/` po podpisaniu umów. Każdy wpis ma poziom współpracy (patronat honorowy,
partner instytucjonalny/naukowy, sponsor diamentowy/platynowy/złoty, partner medialny), który
decyduje o grupie na stronie; logotyp i adres są opcjonalne — bez logotypu karta pokazuje kółko
z inicjałami. **Pas logotypów na stronie głównej pojawia się dopiero z pierwszym wpisem** — przy
pustej liście nie ma go wcale.

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
4. **Zatwierdzenie treści prawnych — rozstrzygnięte co do źródła, otwarte co do decyzji Zarządu.**
   `/dokumenty/rodo/` i `/dokumenty/standardy-ochrony-maloletnich/` nie są już „wersją
   demonstracyjną” ze starego
   WordPressa: treść obu stron jest przepisana z podpisanych PDF-ów organizatora (eksport
   z 7 września 2026), metryka mówi, z jakiego eksportu, a ramka na górze wskazuje PDF jako wersję
   źródłową. Do podjęcia zostaje to, o co proszą same dokumenty: § 11 polityki RODO zapowiada
   aktualizacje przy zmianie procesu, a § 10 standardów wymaga uchwały Zarządu (punkt 5 niżej).
5. **Osoby odpowiedzialne za ochronę małoletnich.** § 9 i § 10 standardów wymagają wskazania ich
   imiennie uchwałą Zarządu i przyjęcia wzoru karty interwencji.
6. **Skład komitetów.** Szesnaście nazwisk i zakresy odpowiedzialności potwierdza PDF organizatora,
   więc dokument `/dokumenty/komitety/` jest opublikowany. Do decyzji zostają: funkcje i afiliacje członków,
   podwójne członkostwo dwóch osób (Paweł Gora, Grzegorz Czelusta figurują w obu komitetach)
   i nazewnictwo — „Komitet Główny” ze starej strony głównej nie istnieje ani w regulaminie,
   ani w PDF-ie.
7. **Partnerzy i patroni — miejsce gotowe, treść do potwierdzenia.** `/partnerzy/` jest
   opublikowana z pustą listą i sekcją „Zostań partnerem”; poziomy współpracy są w modelu.
   Do decyzji zostaje to, czego nie da się wywnioskować: czy Ministerstwo Edukacji i Polskie
   Towarzystwo Fizyczne to realne patronaty (trzecia nazwa ze starej strony, „Uniwersytet
   Kwantowy”, to instytucja nieistniejąca) oraz logotypy i progi sponsoringu. Do czasu decyzji
   ani na `/partnerzy/`, ani na stronie głównej nie ma ani jednej nazwy.
8. **ZOZ (Zasady Organizacji Zawodów).** Regulamin odwołuje się do nich kilkanaście razy,
   a dokument nie istnieje — bez niego brakuje progów, liczby finalistów i reguł remisów.
9. **Status prawny olimpiady.** Regulamin zastrzega, że tytuły finalisty i laureata są wewnętrzne
   i nie dają uprawnień ustawowych. Gdzie portal ma to komunikować?
10. **Krok 2 na `/jak-zaczac/`.** Tekst mówi „Załóż konto uczestnika w czasie rejestracji”, a portal
    używa kodów zaproszeń dla komitetu i samodzielnej rejestracji uczestnika — brzmienie do
    potwierdzenia przez organizatora (import nie redaguje treści).
11. **Kanały kontaktu.** Jeden adres `contact@qaif.org` obsługuje sprawy ogólne, RODO i zgłoszenia
    dotyczące bezpieczeństwa małoletnich, rozróżniane tylko tematem wiadomości.
12. **Aktualności.** Trzy przeniesione wpisy to jednozdaniowe zapowiedzi bez dat (oryginał nie miał
    `post_date`) — mają datę importu i dopisek „Wpis przeniesiony ze starej strony”. Do decyzji,
    czy przepisać je z prawdziwymi datami, czy zacząć newsroom od zera.
13. **Logo, favicon, og:image.** Stara strona nie ma ani jednego pliku graficznego — identyfikację
    trzeba zaprojektować od zera.

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
