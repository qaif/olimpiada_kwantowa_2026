# Monitoring zewnętrzny (Uptime Kuma)

Ten katalog jest **instrukcją**, a nie konfiguracją do skopiowania. Uptime Kuma trzyma monitory
w swojej bazie SQLite i nie czyta żadnego pliku konfiguracyjnego przy starcie – nie da się więc
wstawić gotowych monitorów do repozytorium tak, żeby pojawiły się same. API do ich zakładania
istnieje, ale wymaga zalogowania się tokenem, który i tak trzeba najpierw wyklikać. Skrypt, który
by to robił, byłby więc dłuższy od tej instrukcji i psułby się przy każdym wydaniu Kumy.

Zamiast tego: **sześć monitorów i dwa kanały powiadomień, do założenia raz, w piętnaście minut.**

---

## 1. Uruchomienie

Usługa `monitor` stoi w `docker-compose.yml` pod profilem `monitoring`, więc zwykłe `docker compose
up` jej nie dotyka. Na serwerze (`/opt/olimpiada`):

```bash
docker compose --profile monitoring up -d monitor
docker compose restart proxy          # Caddy podnosi certyfikat dla monitor.<domena>
```

Wymagany rekord DNS: `monitor.<domena>` typu A na adres serwera. Dopóki go nie ma, blok w
`deploy/Caddyfile` tylko czeka i niczego nie psuje.

Pierwsze wejście na `https://monitor.<domena>/` zakłada **konto administratora**. Zrób to od razu:
do czasu założenia konta pulpit jest otwarty dla każdego, kto trafi na adres.

### Czego ten monitoring **nie** zauważy

Stoi na tej samej maszynie, co serwis. Awaria hosta, sieci u dostawcy albo zasilania zabiera więc
monitor razem z serwisem i nikt nie dostaje żadnego listu. To nie jest wada konfiguracji, tylko
cena wariantu „wszystko na jednym serwerze”, i trzeba ją znać. Dwie rzeczy, które ją nadrabiają:

- **watchdog aplikacyjny** (`apps/core/alerts.py`, list co najwyżej raz na godzinę na `ALERT_EMAILS`)
  – widzi to, czego nie widać z zewnątrz: wolne miejsce na dysku, nieudane zadania w tle, serię
  odpowiedzi 500, brak kopii zapasowej,
- **jeden monitor spoza tej maszyny**. Dowolna darmowa usługa (UptimeRobot, Better Stack, druga
  instancja Kumy u innego dostawcy) odpytująca `https://<domena>/status.json` co 5 minut i szukająca
  w treści `"status": "ok"`. To jest jedyne sprawdzenie, które przeżyje śmierć serwera.

---

## 2. Monitory do założenia

Wszystkie z interwałem **60 s** (poza kopiami zapasowymi – tam 15 minut wystarcza) i z powtórzeniem
przed alarmem (`Retries = 2`), żeby pojedyncza zgubiona odpowiedź nie budziła nikogo w nocy.

| # | Nazwa | Typ | Cel | Warunek powodzenia |
|---|-------|-----|-----|--------------------|
| 1 | Strona główna | HTTP(s) | `https://<domena>/` | kod 200 |
| 2 | Status serwisu | HTTP(s) – Keyword | `https://<domena>/status.json` | zawiera `"status": "ok"` |
| 3 | Logowanie | HTTP(s) – Keyword | `https://<domena>/login/` | zawiera `Zaloguj` |
| 4 | Magazyn plików (S3) | HTTP(s) | `https://s3.<domena>/minio/health/live` | kod 200 |
| 5 | Rozmowy (Jitsi) | HTTP(s) | `https://meet.<domena>/` | kod 200 |
| 6 | Poczta wychodząca | TCP Port | host `mail`, port `587` | połączenie nawiązane |

Dlaczego akurat te sześć:

- **1 i 3** to dwie strony, bez których olimpiada nie istnieje: wejście i logowanie. Monitor nr 3
  jest słowem kluczowym, a nie samym kodem 200, bo strona logowania potrafi odpowiedzieć 200
  i wyświetlić pustkę, gdy padnie warstwa szablonów albo pliki statyczne,
- **2** jest najważniejszy i sprawdza cztery rzeczy naraz: bazę, cache, magazyn plików i kolejkę
  zadań. `"status": "ok"` zamienia się na `"degraded"`, gdy **którykolwiek** podsystem nie
  odpowiada – i robi to bez zmiany kodu HTTP, dokładnie po to, żeby monitor czytał treść, a nie
  zgadywał po 503, czy niedostępna jest sama strona statusu,
- **4** dotyczy prac uczestników. Adresy `presigned` idą wprost do MinIO, z pominięciem aplikacji,
  więc awaria tej drogi nie zapala nic innego, a uczestnik nie wgra pliku,
- **5** ma sens wyłącznie w okresie rozmów kwalifikacyjnych; poza nim wycisz go, zamiast kasować,
- **6** jest jedynym sprawdzeniem poczty wychodzącej, jakie da się zrobić bez wysyłania listów.
  Nie powie, czy list dotarł – powie, czy relay przyjmuje połączenia. Dostarczalność sprawdza się
  inaczej (`docs/OPERACJE.md`, sekcja o poczcie).

### Monitor kopii zapasowych (opcjonalny, zalecany)

`https://<domena>/status.json` niesie też dwa pola logiczne: `backup_last_ok` i
`backup_last_verified`. Kumie zakłada się na nie monitor typu **JSON Query** z interwałem 15 minut:

- Query: `$.backup_last_ok`, oczekiwana wartość `true`,
- drugi monitor: `$.backup_last_verified`, oczekiwana wartość `true`.

Dat tam nie ma i nie będzie – `/status.json` jest publiczny, a data ostatniej kopii mówi obcemu,
kiedy uderzenie zaboli najbardziej. Konkretne znaczniki czasu pokazuje
`docker compose exec web python manage.py record_backup_status --show`.

### Monitor połączeń z bazą (opcjonalny, zalecany)

Ostatnie pole `/status.json` – `db_connections` – mówi, jak blisko Postgres jest limitu połączeń
(`max_connections`): `ok`, `warn` (od 80 %), `critical` (od 95 %) albo `unknown` (odczyt się nie
udał). Monitor **JSON Query** z interwałem 5 minut:

- Query: `$.db_connections`, oczekiwana wartość `ok`.

To jest drugi, niezależny sygnał obok listu watchdoga (`docs/OPERACJE.md` § 11.2): watchdog chodzi
w workerze Celery, a ten monitor – z zewnątrz. Liczb tu nie ma celowo (strona jest publiczna); ile
z ilu i kto trzyma połączenia, pokazuje `docker compose exec web python manage.py db_connections`.

---

## 3. Kanały powiadomień

Załóż **dwa**, nie jeden. Pojedynczy kanał ma tę właściwość, że awaria, która go zabiera (poczta),
zabiera też informację o sobie samej.

### 3.1. E-mail (SMTP przez relay serwisu)

W Kumie: *Settings → Notifications → Setup Notification → Email (SMTP)*.

| Pole | Wartość |
|------|---------|
| Hostname | `mail` |
| Port | `587` |
| Security | `None / STARTTLS` (relay stoi w sieci compose, port nie jest publikowany) |
| From | `noreply@<domena>` – ta sama domena, co `ALLOWED_SENDER_DOMAINS` usługi `mail` |
| To | ten sam adres, co w `ALERT_EMAILS` |

Adres nadawcy **musi** być w domenie serwisu: Postfix z usługi `mail` przyjmuje kopertę wyłącznie
dla nadawcy z `ALLOWED_SENDER_DOMAINS` i odrzuci każdą inną (nie jest open relayem nawet dla
kontenera stojącego obok).

### 3.2. Telegram

Kanał zapasowy, bo nie zależy od naszej poczty ani od naszego serwera pocztowego:

1. napisz do `@BotFather` → `/newbot` → zapisz token,
2. dodaj bota do grupy dyżurnych (albo napisz do niego wprost),
3. identyfikator czatu: otwórz `https://api.telegram.org/bot<TOKEN>/getUpdates` po wysłaniu
   pierwszej wiadomości,
4. w Kumie: *Setup Notification → Telegram*, wklej token i identyfikator czatu.

Token bota jest sekretem: kto go ma, może pisać w imieniu bota. Nie trafia do repozytorium ani do
`.env` – żyje wyłącznie w bazie Kumy (wolumen `monitor_data`).

### 3.3. Ustawienia wspólne

- **oba kanały przypnij do każdego monitora** (Kuma ma na to „Default enabled” przy kanale),
- **Resend Notification if Down X times**: `60` (czyli przypomnienie raz na godzinę przy
  interwale 60 s) – ta sama zasada, co wyciszenie w watchdogu aplikacyjnym. Bez tego trwająca
  awaria wysyła jeden list i znika z pola widzenia albo wysyła ich dwieście i uczy je kasować,
- **Certificate Expiry Notification**: włączone. Caddy odnawia certyfikaty sam, ale awaria
  odnowienia jest cicha aż do dnia wygaśnięcia.

---

## 4. Co zrobić, gdy monitor zapali się na czerwono

Kolejność jest zawsze ta sama i jest opisana w `docs/OPERACJE.md` w sekcji „Lista kontrolna
incydentu”. W skrócie: najpierw `https://<domena>/status/` (co widzi uczestnik), potem
`docker compose ps` (co widzi host), potem logi tej jednej usługi, która nie jest `healthy`.
