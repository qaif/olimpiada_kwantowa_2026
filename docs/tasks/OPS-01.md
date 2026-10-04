# OPS-01: Conocny, automatyczny test odtwarzania kopii zapasowej

## 0. Cel i granice (polecenie organizatora, 4.10.2026)

Co noc **udowodnić**, że najnowsza kopia zapasowa daje się odtworzyć do działającej platformy –
i podnieść alarm, gdy się nie da albo gdy najnowszej kopii brakuje.

Stan przed zadaniem: `scripts/backup.sh` (cron 3:15) robi kopię, a `scripts/backup_verify.sh`
**raz w tygodniu** (niedziela 4:40) wstawia ją do tymczasowego Postgresa i liczy wiersze w pięciu
tabelach. Luki, które to zadanie zamyka:

| Luka | Skutek dziś |
|---|---|
| test raz w tygodniu, próg alarmu 10 dni | zepsuta kopia wychodzi po tygodniu, czyli po siedmiu zepsutych nocach |
| „wiersze &gt; 0” zamiast porównania z bazą żywą | zrzut obcięty do połowy przechodzi test |
| brak sprawdzenia migracji | zrzut z innej wersji kodu albo zrobiony w połowie migracji przechodzi test |
| brak sprawdzenia, że **kod** czyta odtworzoną bazę | rozjazd schematu wychodzi dopiero po odtworzeniu, w dniu awarii |
| brak próby odszyfrowania pól Fernet (logistyka finału) | kopia „dobra”, a paszporty delegacji nieczytelne (zmieniony `SECRET_KEY` bez fallbacku) |
| paczka plików (`files-*.tar.gpg`) nie jest sprawdzana w ogóle | baza mówi „praca oddana”, a pliku w kopii nie ma |
| nieudany test zostawia tylko notatkę w cache'u | nikt nie dostaje listu, dopóki nie minie 10 dni |
| zrzut odszyfrowany na dysk hosta (`/tmp`) | dane osobowe leżą jawnie na dysku przez czas testu |
| brak pomiaru czasu odtwarzania | RTO jest zgadywane |

Czego zadanie **nie** robi:
- nie zmienia samej kopii (`backup.sh`): formatów, szyfrowania, wysyłki, retencji,
- nie pobiera kopii z miejsca poza serwerem – sprawdza paczkę lokalną (ta sama paczka, sprawdzona
  sumą kontrolną po tamtej stronie przez `backup.sh`); test kopii **zdalnej** to `restore.sh --fetch`
  + ten skrypt z argumentem (§ 6),
- nie dokłada nowych obrazów Dockera: tymczasowy Postgres to obraz usługi `db`, sprawdzenia biegną
  w obrazie **wdrożonej** aplikacji (`web`),
- nie dokłada ekranu w panelu superkoordynatora: panel nie ma ekranu „stan systemu”, a stan kopii
  widać w `/healthz/`, `/status.json`, w liście alarmowym i w `manage.py restore_check show`.

## 1. Przebieg (co noc, cron hosta 4:40, po kopii z 3:15)

`scripts/backup_verify.sh` (nazwa zostaje – wpis crona, dokumentacja i `djcms_cutover.sh` jej używają):

1. **wybór paczki** – najnowsza `db-*.dump.gpg` w `BACKUP_DIR` (albo podana argumentem) i paczka
   plików z tej samej nocy (`files-<stamp>.tar.gpg`). Brak kopii = wynik nieudany (`no-backup`);
2. **bramka bezpieczeństwa** – cel odtworzenia to wyłącznie nowy kontener
   `olimpiada-restore-check-<pid>` na nowej sieci `--internal` (bez wyjścia na świat i bez dostępu
   do sieci compose), baza `restorecheck_main`. Skrypt odmawia, gdy nazwa celu nie ma tego
   przedrostka albo wskazuje kontener usługi `db`;
3. **odczyt żywej bazy** – `manage.py restore_check live-counts` w działającym `web` (wyłącznie
   `SELECT count(*)` na tabelach kluczowych);
4. **tymczasowy Postgres** – obraz usługi `db` (`POSTGRES_IMAGE`), dane na `tmpfs`, limity
   `--memory`, `--cpus`, `--cpu-shares`, hasło losowe i jednorazowe;
5. **rozszyfrowanie + `pg_restore` strumieniem** (`gpg | docker exec -i pg_restore`) – jawny zrzut
   nie dotyka dysku hosta; `--exit-on-error`; mierzony czas;
6. **paczka plików** – `gpg | tar -tf -` (pełny odczyt archiwum, lista obiektów do pliku roboczego,
   bez wypakowania); mierzony czas;
7. **wersja porównawcza dj.** – jak dotąd (osobna baza `restorecheck_djcms`, `cms_page ≥ 1`,
   czytelna paczka plików), wynik dołączony do sprawdzeń;
8. **sprawdzenia aplikacyjne** – jednorazowy kontener z obrazem działającego `web`, środowiskiem
   `web` (ten sam `SECRET_KEY`), `DATABASE_URL` → baza tymczasowa, `REDIS_URL` → adres martwy,
   sieć wyłącznie tymczasowa, `--read-only`, limity pamięci i CPU:
   `manage.py restore_check verify` (§ 2). Sesja bazy w trybie **tylko do odczytu**;
9. **meldunek** – `manage.py restore_check record` w działającym `web` (§ 3), wiersz w
   `BACKUP_DIR/restore-checks.jsonl` (historia na hoście, bez danych osobowych);
10. **sprzątanie bezwarunkowe** – kontenery, sieć, katalog roboczy (`trap EXIT`).

Każdy błąd po drodze (złe hasło, obcięta paczka, `pg_restore`, kontener sprawdzeń) kończy się
**meldunkiem nieudanym z nazwą kroku**, a nie cichym wyjściem `set -e`.

## 2. Sprawdzenia (`apps.core.restore_check.verify`)

| Nazwa | Nieudane, gdy | Ostrzeżenie, gdy |
|---|---|---|
| `backup_age` | kopia starsza niż `RESTORE_CHECK_MAX_BACKUP_AGE_HOURS` (26 h) | – |
| `migrations` | brak tabeli `django_migrations`, historia niespójna, **brak migracji, które działająca wersja miała już w chwili zrzutu** | brak migracji wdrożonych **po** zrzucie (kontener `web` utworzony później niż kopia); migracje nieznane kodowi |
| `row_counts` | tabela kluczowa poza widełkami względem żywej bazy (domyślnie 90 %–105 % ± 20 wierszy) albo brak tabeli; `accounts_user` &lt; 1 | – |
| `models_readable` | którykolwiek model aplikacji nie daje się odczytać z odtworzonej bazy (`SELECT … LIMIT 1` po wszystkich kolumnach) | – |
| `sequences` | sekwencja klucza głównego za `max(id)` (pierwszy zapis po odtworzeniu skończyłby się błędem) | – |
| `superuser` | – | brak aktywnego superużytkownika |
| `fernet` | szyfrogram pola `EncryptedTextField` nie odszyfrowuje się kluczami aplikacji | odszyfrowuje się wyłącznie kluczem z `SECRET_KEY_FALLBACKS`; brak wierszy = pominięte |
| `media_sample` | z losowej próbki (20 + 20) plików prac (`clean`) i mediów CMS brakuje w paczce więcej niż 10 % (min. 1) | – |
| `files_archive` | brak paczki plików albo nieczytelna | – |
| `djcms_*` | jak dotąd (§ 22) | – |

Tabele kluczowe: konta, uczestnicy, konkursy, witryny Wagtaila, etapy, prace, pliki prac, audyt,
członkowie delegacji. Widełki są zmiennymi środowiskowymi skryptu (§ 43.5 OPERACJE).

Wynik to jeden dokument JSON (wersja 1): `status` (`ok|failed`), backup (nazwa, rozmiar, wiek),
czasy (`db_restore_s`, `files_list_s`, `checks_s`, `total_s`), lista sprawdzeń, wersja aplikacji,
obraz Postgresa. Treść **nie zawiera** wartości pól, haseł ani kluczy – tylko liczby i nazwy.

## 3. Wynik i alarmy

- `record`: wynik w cache'u (`backup:restore_check`, TTL jak pozostałe znaczniki), znacznik
  `backup:last_verified_at` **tylko** przy wyniku `ok`, wpis audytu `backup.restore_check`
  (historia w bazie), przy wyniku `failed` – **natychmiastowy list** do `ALERT_EMAILS`
  (ten sam mechanizm i wyciszenie co watchdog, klucz `backup-restore-check`).
- watchdog (`apps.core.alerts`, co 5 min): ostatni wynik `failed` → alarm `backup-restore-check`
  (co godzinę, aż do udanego testu); brak udanego testu dłużej niż **36 h** → alarm
  `backup-verify` (dotąd 10 dni). Kopia starsza niż 36 h – bez zmian (`backup`).
- `/healthz/` i `/status.json`: nowy klucz `backup_restore_check` = `ok|failed|stale|unknown`
  (**na końcu** kontraktu, wyłącznie poziom – bez dat i liczb; nie zmienia kodu odpowiedzi ani `status`).
- `manage.py restore_check show` – ostatni wynik ze szczegółami dla dyżurnego.

## 4. Bezpieczeństwo

- cel odtworzenia: nowy kontener i nowa sieć `--internal`; podwójna bramka (skrypt + komenda:
  nazwa bazy z przedrostkiem `restorecheck_`, host różny od `db`, nazwa różna od `POSTGRES_DB`,
  zmienna `RESTORE_CHECK_ISOLATED=1`); sesja `READ ONLY`,
- hasło kopii przez deskryptor (`--passphrase-fd`), hasło tymczasowej bazy losowe; środowisko `web`
  w pliku roboczym `600` w katalogu `700`, kasowane bezwarunkowo; żadna wartość sekretu w logu,
- jawny zrzut nie dotyka dysku (strumień), lista obiektów z paczki plików – w katalogu roboczym,
- limity: Postgres `--memory 3g --cpus 1 --cpu-shares 256`, kontener sprawdzeń `--memory 1g
  --cpus 1`, procesy hosta pod `nice -n 19` i `ionice -c3`; nakładanie się z kopią – `flock` w cronie.

## 5. Testy

- pytest (`apps/core/tests/test_restore_check.py`): bramka izolacji, widełki liczności, werdykt
  migracji, Fernet (bieżący klucz / fallback / zły klucz), próbka mediów, wiek kopii, `record`
  (znacznik, audyt, list), watchdog, `/healthz/` i `/status.json`,
- `scripts/tests/backup_offsite_test.sh` (atrapy): kolejność i treść poleceń dockera, bramka,
  meldunek nieudany z nazwą kroku,
- `scripts/tests/restore_check_e2e.sh`: pełny cykl na lokalnym Dockerze (prawdziwy Postgres, obraz
  aplikacji): zrzut → test → `ok`; zrzut uszkodzony → `failed` + list; kopia stara → `failed`.

## 6. Kroki operatora na produkcji (po wdrożeniu, za zgodą organizatora)

Opisane w `docs/OPERACJE.md` § 43.7.
