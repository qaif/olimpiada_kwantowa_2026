#!/usr/bin/env bash
# Conocny test odtwarzania (OPS-01): najnowsza kopia wjeżdża do TYMCZASOWEGO Postgresa, a wdrożona
# wersja aplikacji sprawdza, czy umiałaby na niej pracować.
#
# Uruchamiany przez crona hosta codziennie o 4:40, po kopii z 3:15 (wpis /etc/cron.d/olimpiada-backup
# zakłada scripts/deploy.sh; oba przebiegi pod jednym `flock`, więc test nigdy nie czyta paczki,
# którą backup.sh jeszcze pisze). Ręcznie: `cd /opt/olimpiada && scripts/backup_verify.sh [paczka]`.
# Opis dla operatora: docs/OPERACJE.md § 43, specyfikacja: docs/tasks/OPS-01.md.
#
# Po co osobny przebieg, skoro backup.sh kończy się bez błędu: „pg_dump zakończył się kodem 0”
# nie znaczy „z tej paczki da się odtworzyć olimpiadę”. Kopia potrafi być pusta (zrzut zrobiony
# w czasie, gdy baza nie odpowiadała), obcięta (skończyło się miejsce), zaszyfrowana hasłem,
# którego nikt już nie zna (ktoś zmienił BACKUP_PASSPHRASE i nie zmienił go w sejfie), nieczytelna
# dla pg_restore, niezgodna z wdrożonym kodem albo z kluczem, którym zaszyfrowano pola w bazie.
# Każdy z tych przypadków wychodzi dopiero przy odtwarzaniu – a dzień, w którym odtwarzanie jest
# potrzebne, jest najgorszym możliwym dniem na tę wiadomość.
#
# Droga jest ta sama, co w prawdziwej awarii: rozszyfruj -> pg_restore -> uruchom aplikację na
# odtworzonej bazie. Kroki:
#   1. paczka: najnowsza db-*.dump.gpg (albo argument) + files-<ten sam stamp>.tar.gpg,
#   2. liczności tabel kluczowych w ŻYWEJ bazie (`restore_check live-counts` w działającym `web`),
#   3. tymczasowy Postgres na nowej sieci --internal (bez wyjścia na świat i bez sieci compose),
#   4. gpg | pg_restore STRUMIENIEM – jawny zrzut nie dotyka dysku hosta,
#   5. gpg | tar -tf – pełny odczyt paczki plików, lista obiektów do katalogu roboczego,
#   5b. wersja porównawcza dj. (gdy jest jej paczka z tej samej nocy),
#   6. `restore_check verify` w jednorazowym kontenerze z obrazem i środowiskiem działającego `web`,
#      podłączonym WYŁĄCZNIE do bazy tymczasowej,
#   7. meldunek (`restore_check record` w `web`: cache, audyt, list przy porażce) i wiersz historii
#      w ${BACKUP_DIR}/restore-checks.jsonl.
# Każdy błąd po drodze kończy się meldunkiem nieudanym z nazwą kroku – nie cichym `set -e`.
#
# Ustawienia (zmienne środowiskowe albo .env): RESTORE_CHECK_PG_MEMORY (3g), RESTORE_CHECK_TMPFS
# (3g), RESTORE_CHECK_PG_CPUS (1), RESTORE_CHECK_APP_MEMORY (1g), RESTORE_CHECK_CPU_SHARES (256);
# progi sprawdzeń (RESTORE_CHECK_MAX_BACKUP_AGE_HOURS, …_MIN_RATIO, …) czyta aplikacja – § 43.5.
# Wyłącznie dla testu lokalnego: LIVE_WEB_CID (kontener „żywego” web zamiast `docker compose ps`)
# i RESTORE_CHECK_APP_VOLUME (montowanie kodu z drzewa roboczego w kontenerze sprawdzeń).
set -euo pipefail
# Pliki robocze (środowisko `web` z SECRET_KEY, lista obiektów) – tylko dla właściciela.
umask 077
# Liczby z kropką dziesiętną (czasy w JSON-ie, awk) niezależnie od lokalizacji powłoki operatora.
export LC_NUMERIC=C

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

BACKUP_DIR="${BACKUP_DIR:-/opt/olimpiada-backups}"
# Obraz tymczasowego Postgresa: ta sama wersja główna, na której stoi produkcja – test ma iść
# drogą prawdziwej awarii, a w niej zrzut wraca na tę samą wersję. Bez PG_IMAGE bierzemy
# POSTGRES_IMAGE z .env (serwer przypięty do 16 przed przejściem albo po wycofaniu – docs/OPERACJE.md
# § 19), a bez niego postgres:18-alpine, czyli to, co stawia docker-compose.yml. Wartość ustalamy
# po wczytaniu .env (niżej).
PG_IMAGE_OVERRIDE="${PG_IMAGE:-}"

log() { printf '==> %s\n' "$*"; }
die() { printf 'BŁĄD: %s\n' "$*" >&2; exit 1; }

[ -f .env ] || die "brak pliku .env w $REPO_DIR"
set -a
# shellcheck disable=SC1091
. ./.env
set +a
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE musi być w .env}"
PG_IMAGE="${PG_IMAGE_OVERRIDE:-${POSTGRES_IMAGE:-postgres:18-alpine}}"

# Limity: test biegnie w nocy obok działającej produkcji i nie może jej zabrać ani pamięci, ani
# procesora. tmpfs bazy liczy się do pamięci kontenera, więc RESTORE_CHECK_PG_MEMORY musi go mieścić
# (odtworzona baza produkcyjna zajmuje dziś kilkaset MB – § 43.4).
PG_MEMORY="${RESTORE_CHECK_PG_MEMORY:-3g}"
PG_TMPFS="${RESTORE_CHECK_TMPFS:-3g}"
PG_CPUS="${RESTORE_CHECK_PG_CPUS:-1}"
APP_MEMORY="${RESTORE_CHECK_APP_MEMORY:-1g}"
CPU_SHARES="${RESTORE_CHECK_CPU_SHARES:-256}"
# Procesy hosta (gpg, tar) z najniższym priorytetem procesora i dysku – tam, gdzie są te narzędzia.
NICE=()
if command -v nice >/dev/null 2>&1; then NICE+=(nice -n 19); fi
if command -v ionice >/dev/null 2>&1; then NICE+=(ionice -c3); fi

# Cel odtworzenia. Nazwy są umową z apps.core.restore_check.guard_isolated – komenda `verify`
# odmówi pracy na bazie bez przedrostka `restorecheck_` i na hoście `db`.
CONTAINER="olimpiada-restore-check-$$"
NETWORK="olimpiada-restore-check-net-$$"
RESTORE_DB="restorecheck_main"
RESTORE_USER="restorecheck"

now_s() { local t="${EPOCHREALTIME:-$(date +%s)}"; printf '%s' "${t/,/.}"; }
elapsed() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.1f", b - a }'; }
json_str() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; printf '"%s"' "$s"; }

T0="$(now_s)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/olimpiada-verify-XXXXXX")"
chmod 700 "$WORK_DIR"
# Sprzątanie bezwarunkowe: nieudany test nie może zostawić na maszynie działającego Postgresa
# z kompletem danych osobowych ani pliku ze środowiskiem aplikacji.
cleanup() {
    docker rm -f -v "$CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# --- Żywy `web`: jedyne miejsce, przez które wynik trafia do aplikacji ------------------------
LIVE_WEB="${LIVE_WEB_CID:-$(docker compose ps -q web </dev/null 2>/dev/null || true)}"
[ -n "$LIVE_WEB" ] || die "kontener web nie działa – nie ma ani obrazu do sprawdzeń, ani komu zameldować wyniku"
web_manage() { docker exec -i "$LIVE_WEB" python manage.py "$@"; }

DUMP_NAME=""
append_history() {  # wiersz historii na hoście – bez danych osobowych, same nazwy i liczby
    printf '%s\n' "$1" >>"${BACKUP_DIR}/restore-checks.jsonl" 2>/dev/null \
        || printf 'UWAGA: nie udało się dopisać historii do %s/restore-checks.jsonl\n' "$BACKUP_DIR" >&2
}
fail() {  # $1 = nazwa kroku, $2 = opis (bez danych z bazy – trafia do listu i audytu)
    printf 'TEST ODTWARZANIA NIEUDANY: %s – %s\n' "$1" "$2" >&2
    web_manage restore_check record --failure "$1" --detail "$2" --backup "$DUMP_NAME" </dev/null \
        || printf 'UWAGA: nie udało się zameldować wyniku w aplikacji (watchdog zgłosi brak testu po 36 h).\n' >&2
    append_history "{\"version\":1,\"status\":\"failed\",\"failed\":[$(json_str "$1")],\"finished_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"backup\":{\"name\":$(json_str "$DUMP_NAME")},\"detail\":$(json_str "$2")}"
    exit 1
}

# --- 1. Paczka -------------------------------------------------------------------------------
DUMP_PATH="${1:-$(ls -1t "$BACKUP_DIR"/db-*.dump.gpg 2>/dev/null | head -1 || true)}"
if [ -z "$DUMP_PATH" ] || [ ! -f "$DUMP_PATH" ]; then
    fail no-backup "nie znalazłem kopii bazy w ${BACKUP_DIR}"
fi
DUMP_NAME="$(basename "$DUMP_PATH")"
DUMP_SIZE="$(stat -c %s "$DUMP_PATH")"
DUMP_MTIME="$(stat -c %Y "$DUMP_PATH")"
BACKUP_STAMP="$(printf '%s' "$DUMP_NAME" | sed -nE 's/^db-([0-9]{8}T[0-9]{6}Z)\.dump\.gpg$/\1/p')"
FILES_PATH=""
[ -z "$BACKUP_STAMP" ] || FILES_PATH="$(dirname "$DUMP_PATH")/files-${BACKUP_STAMP}.tar.gpg"
log "Sprawdzam: $DUMP_PATH ($(du -h "$DUMP_PATH" | cut -f1))"

# --- 2. Baza żywa: liczności, obraz i środowisko `web` -----------------------------------------
log "1/6 Liczności tabel kluczowych w bazie żywej"
LIVE_COUNTS="$(web_manage restore_check live-counts </dev/null | tr -d '\r')" || fail live-counts "restore_check live-counts w kontenerze web nie powiodło się"
case "$LIVE_COUNTS" in "{"*"}") ;; *) fail live-counts "restore_check live-counts nie oddało JSON-a" ;; esac
# Obraz po identyfikatorze, a nie po tagu: sprawdzać ma DOKŁADNIE ta wersja, która działa.
WEB_IMAGE_ID="$(docker inspect -f '{{.Image}}' "$LIVE_WEB")" || fail app-image "nie udało się odczytać obrazu kontenera web"
WEB_CREATED="$(docker inspect -f '{{.Created}}' "$LIVE_WEB")" || fail app-image "nie udało się odczytać kontenera web"
# Środowisko działającego `web` (to samo SECRET_KEY i SECRET_KEY_FALLBACKS, te same ustawienia),
# bez adresów bazy, Redisa i brokera – te wskazują produkcję i zostają podmienione niżej.
APP_ENV="${WORK_DIR}/app.env"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$LIVE_WEB" \
    | grep -Ev '^(DATABASE_URL|REDIS_URL|CELERY_BROKER_URL|DB_POOL|DB_APPLICATION_NAME|RESTORE_CHECK_ISOLATED)=' \
    | grep -v '^$' >"$APP_ENV" || true
grep -q '=' "$APP_ENV" || fail app-env "nie udało się odczytać środowiska kontenera web"

# --- 3. Tymczasowy Postgres ------------------------------------------------------------------
# Hasło losowe i jednorazowe: kontener żyje kilka minut, a hasło produkcyjne nie ma po co
# pojawiać się w `docker inspect` ani w liście procesów.
TEST_PASSWORD="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
log "2/6 Tymczasowy Postgres (${PG_IMAGE}, pamięć ${PG_MEMORY}, ${PG_CPUS} CPU, sieć bez wyjścia)"
docker image inspect "$PG_IMAGE" >/dev/null 2>&1 || docker pull -q "$PG_IMAGE" >/dev/null || fail postgres "brak obrazu ${PG_IMAGE}"
# tmpfs w miejscu, które obraz deklaruje jako VOLUME – a to zależy od wersji: 16 ma
# /var/lib/postgresql/data, 18 ma /var/lib/postgresql (PGDATA /var/lib/postgresql/18/docker).
# Odczytane z obrazu, a nie wpisane na sztywno: ścieżka wpisana dla 16 dawałaby na 18 dane na
# dysku, w anonimowym wolumenie, który przeżywa kontener – z kompletem danych osobowych.
PG_VOLUME_PATH="$(docker image inspect -f '{{range $path, $_ := .Config.Volumes}}{{$path}}{{"\n"}}{{end}}' "$PG_IMAGE" | head -1)"
PG_VOLUME_PATH="${PG_VOLUME_PATH:-/var/lib/postgresql/data}"
# `--internal`: sieć bez bramy na świat. W tej sieci są wyłącznie dwa kontenery tego testu –
# żaden z nich nie widzi ani bazy produkcyjnej, ani Redisa, ani MinIO.
docker network create --internal "$NETWORK" >/dev/null || fail postgres "nie udało się założyć sieci ${NETWORK}"
# `max_locks_per_transaction` jak w usłudze `db` – ta sama konfiguracja, co serwer, z którego zrzut.
docker run -d --name "$CONTAINER" --network "$NETWORK" \
    --memory "$PG_MEMORY" --memory-swap "$PG_MEMORY" --cpus "$PG_CPUS" --cpu-shares "$CPU_SHARES" \
    -e POSTGRES_PASSWORD="$TEST_PASSWORD" -e POSTGRES_USER="$RESTORE_USER" -e POSTGRES_DB="$RESTORE_DB" \
    --tmpfs "${PG_VOLUME_PATH}:rw,size=${PG_TMPFS}" \
    "$PG_IMAGE" -c max_locks_per_transaction=256 -c maintenance_work_mem=256MB >/dev/null \
    || fail postgres "tymczasowy Postgres nie wystartował"
# Bramka po stronie skryptu: cel odtworzenia nie może być kontenerem usługi `db`. Przy nazwach
# z PID-em to się nie zdarzy – ale to jest jedyne miejsce, w którym pomyłka kosztowałaby produkcję.
LIVE_DB_CID="$(docker compose ps -q db </dev/null 2>/dev/null || true)"
TARGET_CID="$(docker inspect -f '{{.Id}}' "$CONTAINER" 2>/dev/null || true)"
if [ -n "$LIVE_DB_CID" ] && [ "$TARGET_CID" = "$LIVE_DB_CID" ]; then
    fail guard "cel odtworzenia wskazuje kontener usługi db – odmowa"
fi
# `-h 127.0.0.1` (TCP): przy pierwszym starcie entrypoint obrazu stawia na chwilę serwer bez TCP
# (initdb, zakładanie bazy) i restartuje go – gniazdo odpowiadałoby już wtedy.
for _ in $(seq 1 60); do
    docker exec "$CONTAINER" pg_isready -h 127.0.0.1 -U "$RESTORE_USER" -d "$RESTORE_DB" >/dev/null 2>&1 && break
    sleep 1
done
docker exec "$CONTAINER" pg_isready -h 127.0.0.1 -U "$RESTORE_USER" -d "$RESTORE_DB" >/dev/null 2>&1 \
    || fail postgres "tymczasowy Postgres nie odpowiada"

decrypt() {  # $1 = paczka -> jawna treść na stdout; hasło przez deskryptor, nie w argumencie
    printf '%s' "$BACKUP_PASSPHRASE" | "${NICE[@]}" gpg --batch --quiet \
        --pinentry-mode loopback --passphrase-fd 0 --decrypt "$1"
}

# --- 4. Rozszyfrowanie i pg_restore strumieniem ------------------------------------------------
log "3/6 Rozszyfrowanie i pg_restore (strumieniem)"
T1="$(now_s)"
# `--exit-on-error` świadomie: częściowo odtworzona baza wygląda na kompletną i właśnie dlatego
# jest najgorszym możliwym wynikiem tego testu. Treść błędów pg_restore zostaje w logu crona (root),
# a do listu idzie wyłącznie kod – komunikat Postgresa potrafi zacytować wartość z wiersza.
set +e
decrypt "$DUMP_PATH" | docker exec -i "$CONTAINER" pg_restore -U "$RESTORE_USER" -d "$RESTORE_DB" --no-owner --exit-on-error
CODES=("${PIPESTATUS[@]}")
set -e
[ "${CODES[0]}" -eq 0 ] || fail decrypt "rozszyfrowanie nie powiodło się (BACKUP_PASSPHRASE z .env nie pasuje albo paczka uszkodzona)"
[ "${CODES[1]}" -eq 0 ] || fail pg_restore "pg_restore zakończył się kodem ${CODES[1]} (szczegóły: /var/log/olimpiada-backup.log)"
DB_S="$(elapsed "$T1" "$(now_s)")"
log "    odtworzone w ${DB_S} s"

# --- 5. Paczka plików --------------------------------------------------------------------------
# Pełny odczyt archiwum (`tar -t` przechodzi przez każdy nagłówek, więc obcięta paczka wychodzi
# tutaj), bez wypakowania: lista obiektów wystarcza, żeby aplikacja sprawdziła próbkę plików
# z odtworzonej bazy. Prace uczestników nie lądują na dysku hosta w postaci jawnej.
FILES_STATUS=missing
FILES_ENTRIES=0
FILES_S=null
FILES_SIZE=null
LISTING="${WORK_DIR}/files.txt"
: >"$LISTING"
if [ -n "$FILES_PATH" ] && [ -f "$FILES_PATH" ]; then
    log "4/6 Paczka plików: $(basename "$FILES_PATH") ($(du -h "$FILES_PATH" | cut -f1))"
    FILES_SIZE="$(stat -c %s "$FILES_PATH")"
    T2="$(now_s)"
    if decrypt "$FILES_PATH" | "${NICE[@]}" tar -tf - >"$LISTING"; then
        FILES_STATUS=ok
        FILES_ENTRIES="$(grep -c . "$LISTING" || true)"
    else
        FILES_STATUS=unreadable
        : >"$LISTING"
    fi
    FILES_S="$(elapsed "$T2" "$(now_s)")"
    log "    ${FILES_STATUS}, wpisów: ${FILES_ENTRIES}, ${FILES_S} s"
else
    log "4/6 Paczka plików: BRAK files-${BACKUP_STAMP:-?}.tar.gpg"
fi

# --- 5b. Wersja porównawcza dj. (tylko gdy jest jej paczka z tej samej nocy) -------------------
# Para po znaczniku czasu, a nie „najnowsza djcms-db-*”: test ma sprawdzić kopię JEDNEJ nocy.
# Wzorzec `djcms-db-<stamp>.dump.gpg` celowo nie łapie `djcms-db-pre-*.dump` z kroku 4a wdrożenia
# (jawne, nieszyfrowane zrzuty przed migracjami – osobny zestaw plików). Wyniki idą do aplikacji
# jako dodatkowe sprawdzenia (`extra_checks`) – jeden wynik i jeden meldunek na noc.
EXTRA=()
DJCMS_DUMP_PATH=""
[ -z "$BACKUP_STAMP" ] || DJCMS_DUMP_PATH="$(dirname "$DUMP_PATH")/djcms-db-${BACKUP_STAMP}.dump.gpg"
extra() { EXTRA+=("{\"name\":$(json_str "$1"),\"status\":$(json_str "$2"),\"detail\":$(json_str "$3")}"); }
if [ -n "$DJCMS_DUMP_PATH" ] && [ -f "$DJCMS_DUMP_PATH" ]; then
    log "4b/6 Wersja porównawcza dj.: $(basename "$DJCMS_DUMP_PATH")"
    # Osobna baza w tym samym kontenerze: tabele djcms nie mieszają się z tabelami bazy głównej
    # (obie mają np. `auth_user`), a drugi kontener to drugie tyle czasu i pamięci.
    set +e
    docker exec "$CONTAINER" createdb -U "$RESTORE_USER" restorecheck_djcms >/dev/null
    created=$?
    decrypt "$DJCMS_DUMP_PATH" | docker exec -i "$CONTAINER" pg_restore -U "$RESTORE_USER" -d restorecheck_djcms --no-owner --exit-on-error
    DJ_CODES=("${PIPESTATUS[@]}")
    set -e
    if [ "${DJ_CODES[0]}" -ne 0 ]; then
        extra djcms_db fail "rozszyfrowanie paczki djcms nie powiodło się"
    elif [ "$created" -ne 0 ] || [ "${DJ_CODES[1]}" -ne 0 ]; then
        extra djcms_db fail "pg_restore bazy djcms nie powiódł się"
    else
        # Próg: co najmniej jedna strona. Wersja porównawcza bez stron (import nie ruszył) nie ma
        # czego pokazać – taka kopia nie odtworzy dj., nawet jeśli pg_restore przeszedł.
        pages="$(docker exec "$CONTAINER" psql -U "$RESTORE_USER" -d restorecheck_djcms -Atc "SELECT count(*) FROM cms_page" 2>/dev/null | tr -d '\r' || true)"
        if [ -z "$pages" ]; then
            extra djcms_db fail "brak tabeli cms_page"
        elif [ "$pages" -lt 1 ]; then
            extra djcms_db fail "cms_page=0"
        else
            extra djcms_db ok "cms_page=${pages}"
        fi
    fi
    # Pliki redaktorów: rozszyfrowanie i pełny odczyt archiwum. Brak paczki przy obecnej bazie =
    # kopia dj. niepełna (backup.sh zgłosił to już tamtej nocy, test mówi to samo).
    DJCMS_FILES_PATH="$(dirname "$DUMP_PATH")/djcms-files-${BACKUP_STAMP}.tar.gpg"
    if [ ! -f "$DJCMS_FILES_PATH" ]; then
        extra djcms_files fail "brak paczki djcms-files-${BACKUP_STAMP}.tar.gpg"
    elif members="$(decrypt "$DJCMS_FILES_PATH" | tar -tf - | wc -l | tr -d ' ')"; then
        extra djcms_files ok "wpisów tar: ${members}"
    else
        extra djcms_files fail "paczka plików djcms nieczytelna"
    fi
fi

# --- 6. Sprawdzenia aplikacji ------------------------------------------------------------------
log "5/6 Sprawdzenia aplikacji (obraz działającego web, baza tymczasowa)"
{
    printf 'DATABASE_URL=postgres://%s:%s@%s:5432/%s\n' "$RESTORE_USER" "$TEST_PASSWORD" "$CONTAINER" "$RESTORE_DB"
    # Adres martwy (port 1 na pętli kontenera): sprawdzenia nie dotykają cache'u, a gdyby któryś
    # kod jednak spróbował, ma dostać natychmiastowy błąd, a nie Redisa produkcji.
    printf 'REDIS_URL=redis://127.0.0.1:1/0\nCELERY_BROKER_URL=redis://127.0.0.1:1/1\n'
    printf 'DB_POOL=0\nDB_APPLICATION_NAME=olimpiada-restore-check\nRESTORE_CHECK_ISOLATED=1\n'
    # Progi sprawdzeń z .env serwera (§ 43.5) – środowisko `web` ich nie zna, bo to zmienne testu.
    env | grep -E '^RESTORE_CHECK_(MAX_BACKUP_AGE_HOURS|MIN_RATIO|MAX_RATIO|SLACK_ROWS|MEDIA_SAMPLE|MEDIA_MAX_MISSING_RATIO)=' || true
} >>"$APP_ENV"
EXTRA_JSON="$(IFS=,; printf '%s' "${EXTRA[*]:-}")"
HEADER="{\"backup\":{\"name\":$(json_str "$DUMP_NAME"),\"size_bytes\":${DUMP_SIZE},\"mtime_epoch\":${DUMP_MTIME},\"files_name\":$(json_str "$(basename "${FILES_PATH:-brak}")"),\"files_size_bytes\":${FILES_SIZE}},\"timings\":{\"db_restore_s\":${DB_S},\"files_list_s\":${FILES_S}},\"live_counts\":${LIVE_COUNTS},\"files_status\":\"${FILES_STATUS}\",\"files_entries\":${FILES_ENTRIES},\"web_created_at\":$(json_str "$WEB_CREATED"),\"pg_image\":$(json_str "$PG_IMAGE"),\"started_at\":$(json_str "$STARTED_AT"),\"script_started_epoch\":${T0},\"extra_checks\":[${EXTRA_JSON}]}"
RESULT="${WORK_DIR}/result.json"
# Kontener sprawdzeń: tylko sieć tymczasowa, system plików tylko do odczytu, bez uprawnień jądra,
# z limitem pamięci i CPU. `--entrypoint python`: entrypoint obrazu robi `migrate`, a migracje na
# odtworzonej bazie zamazałyby dokładnie to, co sprawdza `migrations`.
# RESTORE_CHECK_APP_VOLUME – wyłącznie test lokalny: kod z drzewa roboczego zamiast kodu obrazu.
APP_EXTRA=()
[ -z "${RESTORE_CHECK_APP_VOLUME:-}" ] || APP_EXTRA=(-v "$RESTORE_CHECK_APP_VOLUME")
set +e
{ printf '%s\n' "$HEADER"; cat "$LISTING"; } | docker run --rm -i --network "$NETWORK" --env-file "$APP_ENV" \
    --memory "$APP_MEMORY" --memory-swap "$APP_MEMORY" --cpus 1 --cpu-shares "$CPU_SHARES" \
    --read-only --tmpfs /tmp:size=64m --cap-drop ALL --security-opt no-new-privileges:true \
    "${APP_EXTRA[@]}" --entrypoint python "$WEB_IMAGE_ID" manage.py restore_check verify >"$RESULT"
VERIFY_RC=${PIPESTATUS[1]}
set -e
rm -f "$APP_ENV"
case "$(head -c 1 "$RESULT" 2>/dev/null)" in
    "{") ;;
    *) fail checks "kontener sprawdzeń zakończył się kodem ${VERIFY_RC} bez wyniku" ;;
esac

# --- 7. Meldunek -------------------------------------------------------------------------------
log "6/6 Meldunek do aplikacji i historia (${BACKUP_DIR}/restore-checks.jsonl)"
web_manage restore_check record <"$RESULT" \
    || printf 'UWAGA: nie udało się zameldować wyniku w aplikacji (watchdog zgłosi brak testu po 36 h).\n' >&2
append_history "$(cat "$RESULT")"
chmod 600 "${BACKUP_DIR}/restore-checks.jsonl" 2>/dev/null || true

if [ "$VERIFY_RC" -ne 0 ]; then
    printf 'TEST ODTWARZANIA NIEUDANY – szczegóły wyżej i w: docker compose exec web python manage.py restore_check show\n' >&2
    exit 1
fi
log "Test odtwarzania zakończony powodzeniem ($(elapsed "$T0" "$(now_s)") s)."
