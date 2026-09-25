#!/usr/bin/env bash
# Przejście bazy z PostgreSQL 16 na 18 (zrzut i odtworzenie) – i droga powrotu.
#
# Użycie (na serwerze, z katalogu /opt/olimpiada, PO wdrożeniu kodu z PostgreSQL 18):
#   scripts/upgrade_postgres18.sh --status            # na czym stoi baza, jakie są wolumeny
#   scripts/upgrade_postgres18.sh --dry-run           # kontrole wstępne + plan, niczego nie zmienia
#   scripts/upgrade_postgres18.sh                     # przejście 16 -> 18 (przerwa w działaniu serwisu)
#   scripts/upgrade_postgres18.sh --rollback --yes    # powrót na 16 (stary, nietknięty wolumen)
#   scripts/upgrade_postgres18.sh --pin-if-needed     # woła scripts/deploy.sh (krok 4/8), patrz niżej
#
# Opcje przejścia:
#   --allow-stale-backup    nie przerywaj, gdy ostatnia kopia nocna jest starsza niż 26 h
#   --recreate-pg18-volume  wolumen `pg18_data` już istnieje (np. po wycofaniu) – skasuj go i zacznij
#                           od zera. Wolumen po wycofaniu niesie dane zapisane na 18 – zrzut z nich
#                           robi `--rollback` (pg18-rollback-*.dump), więc skasowanie nie jest ślepe.
#   --no-maintenance        bez strony „Prace techniczne” (proxy sprzed tej funkcji) – przez przerwę
#                           serwis odpowiada wtedy 502. Domyślnie strona jest włączana i wymagana.
# Zmienne: MAINTENANCE_MESSAGE (komunikat na stronie), MAINTENANCE_MINUTES (planowany czas, dom. 10),
#          MAINTENANCE_CHECK_URL (adres kontroli przez proxy, dom. https://<SITE_DOMAIN>).
#
# Jak to działa (docs/OPERACJE.md § 19): usługa `db` w docker-compose.yml bierze obraz i wolumen
# ze zmiennych POSTGRES_IMAGE / POSTGRES_VOLUME; bez nich – PostgreSQL 18 na wolumenie `pg18_data`.
# Serwer, który przed tym wydaniem działał na 16, ma w .env przypięcie (wpisuje je `--pin-if-needed`
# z scripts/deploy.sh), więc samo wdrożenie niczego w bazie nie zmienia.
#
# Kolejność jest wymuszona (wymóg organizatora z 25.09.2026, OPERACJE § 19.4): odtwarzany jest
# WYŁĄCZNIE zrzut zrobiony PO włączeniu strony prac technicznych i zatrzymaniu aplikacji – każde
# odstępstwo przerywa skrypt:
#   0. kontrole wstępne – bez przerwy (wersja, wolumeny, miejsce, kopia nocna, obraz 18, proxy
#      widzi katalog strony, jest przepustka operatora),
#   1. strona „Prace techniczne” WŁĄCZONA (scripts/maintenance.sh on) – od tej chwili 503,
#   2. stop web/worker/beat; w bazie nie może zostać żaden klient poza tym skryptem
#      (pg_stat_activity; maruderzy po 30 s są rozłączani, a jeśli to nie pomoże – przerwanie),
#   3. stan bazy 16 (liczby wierszy KAŻDEJ tabeli, sekwencje, rozszerzenia, role, obiekty),
#   4. ZRZUTY KOŃCOWE: `pg_dump -Fc` klientem 16 (droga powrotu) oraz `pg_dumpall --roles-only`
#      i `pg_dump -Fc` klientem 18 (ten jest odtwarzany); przed każdym – kontrola, że strona wciąż
#      jest włączona, aplikacja stoi i nie ma klientów; znacznik czasu każdego zrzutu musi być
#      późniejszy niż włączenie strony; SHA-256 i rozmiar do dumps.sha256; po zrzutach ponowny
#      stan 16 – musi być identyczny (nikt nie pisał w trakcie),
#   5. stop 16, start 18 na nowym wolumenie (to samo polecenie, locale, hasło – z compose),
#   6. odtworzenie DOKŁADNIE tego zrzutu (SHA-256 sprawdzany tuż przed `pg_restore`), ANALYZE,
#   7. porównanie stanu 18 ze stanem zatrzymanej 16 – każda różnica przerywa skrypt,
#   8. zdjęcie przypięcia z .env, start aplikacji, /healthz/ + /status.json od środka i przez proxy
#      z przepustką operatora (X-Maintenance-Bypass),
#   9. strona „Prace techniczne” WYŁĄCZONA.
# Przebieg (czasy każdego etapu) trafia do timeline.txt w katalogu przebiegu.
# Błąd w krokach 2–7 (przed zdjęciem przypięcia) sam przywraca bazę 16 i aplikację – .env wciąż
# wskazuje 16, więc `docker compose up -d` wraca na stary wolumen, którego nic nie dotknęło.
# Po KAŻDYM błędzie po kroku 1 strona prac technicznych ZOSTAJE WŁĄCZONA (skrypt mówi to głośno):
# operator sprawdza serwis z przepustką i wyłącza ją sam (scripts/maintenance.sh off).
#
# Szacowany czas przerwy (baza ~1,4 MB gzip): 2–5 minut, z czego większość to start `web`
# (migracje + collectstatic w entrypoincie). Próba generalna: docs/OPERACJE.md § 19.3,
# test kolejności: scripts/tests/maintenance_pg18_rehearsal.sh.
set -Eeuo pipefail
# Każde nieobsłużone polecenie z błędem mówi, gdzie padło – `set -e` sam kończy skrypt po cichu.
trap 'printf "BŁĄD: polecenie w linii %s zakończyło się kodem %s\n" "$LINENO" "$?" >&2' ERR

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

BACKUP_DIR="${BACKUP_DIR:-/opt/olimpiada-backups}"
PG16_IMAGE="postgres:16-alpine"
PG16_VOLUME="pg_data:/var/lib/postgresql/data"
PG18_IMAGE="${PG18_IMAGE:-postgres:18-alpine}"
PG18_VOLUME="pg18_data:/var/lib/postgresql"
PIN_MARKER="# PostgreSQL: przypięcie do 16 do czasu przejścia na 18"
# Kopia nocna (scripts/backup.sh) starsza niż tyle godzin zatrzymuje przejście: skoro nocna kopia
# nie wyszła, to i kopia poza serwerem nie wyszła, a tego nie naprawi świeży zrzut lokalny.
BACKUP_MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"
WEB_HEALTH_TIMEOUT="${WEB_HEALTH_TIMEOUT:-420}"
STATUS_TIMEOUT="${STATUS_TIMEOUT:-300}"

MODE=upgrade
DRY_RUN=0
YES=0
ALLOW_STALE_BACKUP=0
RECREATE_PG18=0
MAINTENANCE=1
MAINTENANCE_MESSAGE="${MAINTENANCE_MESSAGE:-Aktualizacja bazy danych (PostgreSQL 18).}"
MAINTENANCE_MINUTES="${MAINTENANCE_MINUTES:-10}"
# Stan strony prac technicznych w tym przebiegu: 1 = włączył ją ten skrypt i jeszcze nie wyłączył.
MAINT_ON=0
MAINT_ON_AT=""

log() { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf 'UWAGA: %s\n' "$*" >&2; }
die() { printf 'BŁĄD: %s\n' "$*" >&2; exit 1; }
# Każde polecenie zmieniające stan idzie przez `run`: w trybie próbnym tylko się wypisuje, więc
# „co się stanie” i „co się dzieje” to ten sam ciąg poleceń (wzorzec z scripts/restore.sh).
run() {
    if [ "$DRY_RUN" = "1" ]; then
        printf '    [próba] %s\n' "$*"
    else
        printf '    $ %s\n' "$*"
        "$@"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --yes) YES=1 ;;
        --status) MODE=status ;;
        --rollback) MODE=rollback ;;
        --pin-if-needed) MODE=pin ;;
        --allow-stale-backup) ALLOW_STALE_BACKUP=1 ;;
        --recreate-pg18-volume) RECREATE_PG18=1 ;;
        --no-maintenance) MAINTENANCE=0 ;;
        -h|--help) sed -n '2,54p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "nieznany argument: $1 (pomoc: --help)" ;;
    esac
    shift
done

[ -f .env ] || die "brak pliku .env w $REPO_DIR"
[ -f docker-compose.yml ] || die "brak docker-compose.yml w $REPO_DIR"
grep -q 'POSTGRES_VOLUME' docker-compose.yml \
    || die "docker-compose.yml nie zna POSTGRES_VOLUME – najpierw wdróż kod z PostgreSQL 18 (scripts/deploy.sh)"

# .env czytamy grepem, a NIE przez `. ./.env`: załadowane do środowiska POSTGRES_IMAGE/VOLUME
# miałyby pierwszeństwo przed plikiem i `docker compose` po zdjęciu przypięcia nadal stawiałby 16.
# Z tego samego powodu czyścimy je z odziedziczonego środowiska.
unset POSTGRES_IMAGE POSTGRES_VOLUME
env_get() { sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r'; }
PG_USER="$(env_get POSTGRES_USER)"
PG_DB="$(env_get POSTGRES_DB)"
# `--pin-if-needed` nie łączy się z bazą (patrzy tylko na .env i wolumeny), więc nie wymaga kont.
if [ "$MODE" != "pin" ]; then
    [ -n "$PG_USER" ] || die "POSTGRES_USER nie ma w .env"
    [ -n "$PG_DB" ] || die "POSTGRES_DB nie ma w .env"
fi
SITE_DOMAIN="$(env_get SITE_DOMAIN)"

dc() { docker compose "$@"; }
# Nazwa projektu compose (prefiks wolumenów): COMPOSE_PROJECT_NAME albo to, co wylicza compose.
PROJECT="${COMPOSE_PROJECT_NAME:-$(dc config 2>/dev/null | sed -n 's/^name: //p' | head -1)}"
[ -n "$PROJECT" ] || die "nie ustaliłem nazwy projektu compose (docker compose config)"
VOL16="${PROJECT}_pg_data"
VOL18="${PROJECT}_pg18_data"

volume_exists() { docker volume inspect "$1" >/dev/null 2>&1; }
pinned() { grep -qE '^POSTGRES_VOLUME=' .env; }
db_running() { [ -n "$(dc ps -q --status running db 2>/dev/null)" ]; }
# psql w kontenerze `db` – działa na tym serwerze, który akurat stoi (16 albo 18).
dbq() { dc exec -T db psql -X -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -Atc "$1" </dev/null | tr -d '\r'; }
server_version_num() { dbq "SHOW server_version_num" 2>/dev/null || true; }

pin_write() {
    {
        echo
        echo "$PIN_MARKER (scripts/upgrade_postgres18.sh, docs/OPERACJE.md § 19)."
        echo "# Usuwa je sam skrypt po udanym przejściu; wpisuje je z powrotem --rollback."
        echo "POSTGRES_IMAGE=$PG16_IMAGE"
        echo "POSTGRES_VOLUME=$PG16_VOLUME"
    } >> .env
    chmod 600 .env
}
pin_remove() {
    sed -i -e "/^$PIN_MARKER/d" \
        -e '/^# Usuwa je sam skrypt po udanym przejściu; wpisuje je z powrotem --rollback\.$/d' \
        -e '/^POSTGRES_IMAGE=/d' -e '/^POSTGRES_VOLUME=/d' .env
}

wait_db_ready() {
    # `pg_isready -h 127.0.0.1` (TCP), a nie przez gniazdo: przy pierwszym starcie na pustym
    # wolumenie entrypoint obrazu stawia tymczasowy serwer **bez** TCP (initdb + POSTGRES_DB)
    # i restartuje go chwilę później. Gniazdo odpowiadałoby już wtedy – TCP dopiero po restarcie.
    local i
    for i in $(seq 1 90); do
        if dc exec -T db pg_isready -q -h 127.0.0.1 -U "$PG_USER" -d "$PG_DB" </dev/null 2>/dev/null; then
            return 0
        fi
        sleep 2
    done
    return 1
}

wait_web_healthy() {
    local i state
    for i in $(seq 1 $((WEB_HEALTH_TIMEOUT / 5))); do
        state="$(dc ps --format '{{.Service}}={{.Health}}' 2>/dev/null | tr '\n' ' ')"
        case " $state" in *" web=healthy"*) info "$state"; return 0 ;; esac
        sleep 5
    done
    info "$state"
    return 1
}

# Stan bazy do porównania 16 ↔ 18. Każdy plik to posortowane linijki „klucz wartość”, więc
# porównanie to `diff`, a różnica jest od razu czytelna w logu.
snapshot() {  # $1 = katalog wyjściowy
    local out="$1"
    mkdir -p "$out"
    # Dokładne count(*) każdej tabeli (nie reltuples – po odtworzeniu statystyk jeszcze nie ma).
    dbq "SELECT format('%I.%I', n.nspname, c.relname) || ' ' ||
                (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I',
                 n.nspname, c.relname), false, true, '')))[1]::text
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1" > "$out/rows.txt"
    dbq "SELECT format('%I.%I', schemaname, sequencename) || ' ' || coalesce(last_value::text, 'null')
           FROM pg_sequences ORDER BY 1" > "$out/sequences.txt"
    dbq "SELECT extname FROM pg_extension ORDER BY 1" > "$out/extensions.txt"
    dbq "SELECT rolname || ' super=' || rolsuper || ' login=' || rolcanlogin FROM pg_roles
          WHERE rolname !~ '^pg_' ORDER BY 1" > "$out/roles.txt"
    dbq "SELECT pg_encoding_to_char(encoding) || ' ' || datcollate || ' ' || datctype
           FROM pg_database WHERE datname = current_database()" > "$out/database.txt"
    dbq "SELECT coalesce(r.rolname, '*') || ' ' || coalesce(d.datname, '*') || ' ' ||
                array_to_string(s.setconfig, ',')
           FROM pg_db_role_setting s LEFT JOIN pg_roles r ON r.oid = s.setrole
           LEFT JOIN pg_database d ON d.oid = s.setdatabase ORDER BY 1" > "$out/settings.txt"
    # Obiekty schematu bez obiektów rozszerzeń (ich liczba zależy od wersji rozszerzenia, nie
    # od danych) i bez więzów NOT NULL: od 18 każde NOT NULL ma własny wiersz w pg_constraint
    # (contype 'n'), którego w 16 nie było – liczone razem dawałyby różnicę przy identycznym
    # schemacie. Kolumny NOT NULL porównuje osobna linijka z information_schema.
    dbq "SELECT 'relations ' || c.relkind::text || ' ' || count(*)
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'
            AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                            AND d.objid = c.oid AND d.deptype = 'e')
          GROUP BY c.relkind
         UNION ALL
         SELECT 'constraints ' || co.contype::text || ' ' || count(*)
           FROM pg_constraint co JOIN pg_namespace n ON n.oid = co.connamespace
          WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND co.contype <> 'n'
          GROUP BY co.contype
         UNION ALL
         SELECT 'not_null_columns ' || count(*) FROM information_schema.columns
          WHERE table_schema NOT IN ('pg_catalog', 'information_schema') AND is_nullable = 'NO'
         UNION ALL
         SELECT 'triggers ' || count(*) FROM pg_trigger WHERE NOT tgisinternal
         UNION ALL
         SELECT 'functions ' || count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
          WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
            AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass
                            AND d.objid = p.oid AND d.deptype = 'e')
         ORDER BY 1" > "$out/objects.txt"
    dbq "SELECT extname || ' ' || extversion FROM pg_extension ORDER BY 1" > "$out/extension_versions.txt"
    dbq "SHOW server_version" > "$out/server_version.txt"
}

compare_snapshots() {  # $1 = stan 16, $2 = stan 18
    local failed=0 f
    for f in rows sequences extensions roles database settings objects; do
        if diff -u "$1/$f.txt" "$2/$f.txt" > "$2/$f.diff"; then
            info "$(printf '%-11s' "$f") zgodne ($(wc -l < "$2/$f.txt") poz.)"
        else
            warn "$f – RÓŻNICA 16 ↔ 18:"
            cat "$2/$f.diff" >&2
            failed=1
        fi
    done
    info "tabel: $(wc -l < "$2/rows.txt"), wierszy razem: $(awk '{s+=$2} END {print s+0}' "$2/rows.txt")"
    info "wersje rozszerzeń na 18: $(tr '\n' ' ' < "$2/extension_versions.txt")"
    return $failed
}

status_json_ok() {
    # Najpierw z wnętrza kontenera web (niezależnie od DNS i certyfikatu), potem – jeśli jest
    # domena i curl – z zewnątrz, przez proxy, czyli tak, jak widzi to monitoring.
    dc exec -T -e SITE_DOMAIN="$SITE_DOMAIN" web python - </dev/null <<'PY'
import json, os, sys, urllib.request
req = urllib.request.Request("http://127.0.0.1:8000/status.json",
                             headers={"Host": os.environ.get("SITE_DOMAIN") or "localhost",
                                      "X-Forwarded-Proto": "https"})
try:
    data = json.load(urllib.request.urlopen(req, timeout=10))
except Exception as exc:  # noqa: BLE001
    print(f"    /status.json: {exc}")
    sys.exit(1)
print(f"    /status.json: status={data.get('status')} services={data.get('services')} version={data.get('version')}")
sys.exit(0 if data.get("status") == "ok" else 1)
PY
}

# --------------------------------------------------------------------------------------------
# Strona „Prace techniczne” i pilnowanie kolejności (OPERACJE § 19.4, § 20)
# --------------------------------------------------------------------------------------------
BYPASS_TOKEN="$(env_get MAINTENANCE_BYPASS_TOKEN | tr -d '\042\047')"
CHECK_URL="${MAINTENANCE_CHECK_URL:-}"
if [ -z "$CHECK_URL" ] && [ -n "$SITE_DOMAIN" ] && [ "$SITE_DOMAIN" != "localhost" ]; then
    CHECK_URL="https://${SITE_DOMAIN}"
fi
TIMELINE=""
now_ts() { date +%s.%N; }
# Znacznik etapu: „<zdarzenie> <epoch z nanosekundami> <ISO>” – z tego pliku test kolejności
# (scripts/tests/maintenance_pg18_rehearsal.sh) sprawdza, że zrzut powstał po włączeniu strony.
mark() {  # mark <zdarzenie> [<epoch>]
    local ts="${2:-$(now_ts)}"
    [ -n "$TIMELINE" ] && printf '%s %s %s\n' "$1" "$ts" "$(date -d "@${ts%.*}" -Iseconds)" >> "$TIMELINE"
    return 0
}
ts_after() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 > b + 0) }'; }  # a > b
# `test` w kontenerze proxy. MSYS_NO_PATHCONV tylko tutaj: w Git Bashu (lokalna próba) ścieżka
# kontenera zamieniłaby się w ścieżkę Windows; globalnie nie wolno, bo `--env-file` klienta 18
# potrzebuje właśnie tej zamiany. Na Linuksie zmienna nic nie robi.
proxy_test() { MSYS_NO_PATHCONV=1 docker compose exec -T proxy test "$@" </dev/null; }

maintenance_preflight() {
    [ "$MAINTENANCE" = "1" ] || { warn "--no-maintenance: przez całą przerwę serwis odpowiada 502 (bez strony prac technicznych)"; return 0; }
    [ -x scripts/maintenance.sh ] || [ -f scripts/maintenance.sh ] \
        || die "brak scripts/maintenance.sh – wdróż kod ze stroną prac technicznych albo użyj --no-maintenance"
    [ "${#BYPASS_TOKEN}" -ge 32 ] \
        || die "MAINTENANCE_BYPASS_TOKEN w .env pusty albo krótszy niż 32 znaki – bez niego nie sprawdzę serwisu przez proxy przed zdjęciem strony (scripts/deploy.sh generuje go sam; po dopisaniu ręcznym: docker compose up -d proxy)"
    [ -n "$(dc ps -q --status running proxy 2>/dev/null)" ] || die "kontener proxy nie działa – strona prac technicznych nie miałaby kto podać"
    proxy_test -d /srv/maintenance \
        || die "proxy nie ma montażu /srv/maintenance – najpierw wdrożenie z tą funkcją (proxy odtworzone), albo --no-maintenance"
    run bash scripts/maintenance.sh sync
    if [ -n "$CHECK_URL" ]; then
        info "kontrola przez proxy: $CHECK_URL (z przepustką operatora)"
    else
        warn "SITE_DOMAIN=${SITE_DOMAIN:-brak} i brak MAINTENANCE_CHECK_URL – kontrola przez proxy zostanie pominięta"
    fi
}

maintenance_on() {  # maintenance_on "<komunikat>"
    [ "$MAINTENANCE" = "1" ] || return 0
    MAINT_ON_AT="$(now_ts)"
    # Niepowodzenie `on` cofa flagę (proxy jej nie widzi = strony nie ma), więc stan jest „wyłączone”.
    bash scripts/maintenance.sh on --message "$1" --in "$MAINTENANCE_MINUTES" \
        || die "nie udało się włączyć strony prac technicznych (proxy jej nie widzi?) – NIC jeszcze nie zatrzymano"
    MAINT_ON=1
    mark maintenance_on "$MAINT_ON_AT"
}

maintenance_off() {
    [ "$MAINTENANCE" = "1" ] || return 0
    bash scripts/maintenance.sh off || die "strona prac technicznych: wyłączenie nie potwierdzone przez proxy (scripts/maintenance.sh status)"
    MAINT_ON=0
    mark maintenance_off
}

maintenance_left_on_banner() {
    printf '\n' >&2
    printf '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n' >&2
    printf '!!! STRONA „PRACE TECHNICZNE” JEST NADAL WŁĄCZONA – serwis odpowiada wszystkim 503.\n' >&2
    printf '!!! Sprawdź serwis z przepustką:  curl -H "X-Maintenance-Bypass: <token z .env>" %s/status.json\n' "${CHECK_URL:-https://<domena>}" >&2
    printf '!!! Gdy wszystko działa:          scripts/maintenance.sh off\n' >&2
    printf '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n' >&2
}

client_count() {
    dbq "SELECT count(*) FROM pg_stat_activity WHERE datname IS NOT NULL AND pid <> pg_backend_pid() AND backend_type = 'client backend'"
}

wait_no_clients() {
    # Aplikacja stoi – w bazie nie może zostać żaden klient poza tym skryptem. 30 s na rozejście się
    # połączeń, potem rozłączenie maruderów (pg_terminate_backend) i ostatnia kontrola.
    local others i
    for i in $(seq 1 30); do
        others="$(client_count)"
        [ "$others" = "0" ] && { info "brak klientów bazy poza tym skryptem"; return 0; }
        sleep 1
    done
    warn "po 30 s wciąż $others klient(ów): $(dbq "SELECT string_agg(DISTINCT coalesce(nullif(application_name, ''), '?') || '@' || coalesce(client_addr::text, 'socket'), ', ') FROM pg_stat_activity WHERE datname IS NOT NULL AND pid <> pg_backend_pid() AND backend_type = 'client backend'") – rozłączam"
    dbq "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname IS NOT NULL AND pid <> pg_backend_pid() AND backend_type = 'client backend'" >/dev/null
    for i in $(seq 1 10); do
        others="$(client_count)"
        [ "$others" = "0" ] && { info "maruderzy rozłączeni – brak klientów bazy poza tym skryptem"; return 0; }
        sleep 1
    done
    die "do bazy wciąż ktoś się łączy ($others) mimo rozłączenia – przerywam, zanim powstanie zrzut"
}

assert_quiesced() {  # assert_quiesced "<czego dotyczy>" – warunki zrobienia zrzutu KOŃCOWEGO
    local what="$1" running
    if [ "$MAINTENANCE" = "1" ]; then
        { [ "$MAINT_ON" = "1" ] && [ -f "$(maintenance_dir)/on" ]; } \
            || die "$what: strona prac technicznych nie jest włączona – zrzut do odtworzenia wolno zrobić wyłącznie po jej włączeniu"
        proxy_test -f /srv/maintenance/on \
            || die "$what: proxy nie widzi flagi prac technicznych – przerywam"
        ts_after "$(now_ts)" "$MAINT_ON_AT" || die "$what: znacznik czasu nie jest późniejszy niż włączenie strony ($MAINT_ON_AT)"
    fi
    running="$(dc ps --status running --services 2>/dev/null | grep -xE 'web|worker|beat' | tr '\n' ' ' || true)"
    [ -z "$running" ] || die "$what: działa jeszcze: $running– zrzut wolno zrobić wyłącznie przy zatrzymanej aplikacji"
    [ "$(client_count)" = "0" ] || die "$what: w bazie są klienci poza tym skryptem – przerywam"
}

maintenance_dir() {
    local d
    d="${MAINTENANCE_DIR:-$(env_get MAINTENANCE_DIR | tr -d '\042\047')}"
    d="${d:-./maintenance}"
    case "$d" in /*) printf '%s' "$d" ;; *) printf '%s/%s' "$REPO_DIR" "${d#./}" ;; esac
}

proxy_status_ok() {
    # /status.json przez proxy (tak, jak widzi go monitoring) z przepustką operatora – strona prac
    # technicznych jest jeszcze włączona, więc bez przepustki odpowiedź to 503 {"status":"maintenance"}.
    [ -n "$CHECK_URL" ] || { warn "kontrola przez proxy pominięta (brak adresu – MAINTENANCE_CHECK_URL)"; return 0; }
    command -v curl >/dev/null 2>&1 || { warn "kontrola przez proxy pominięta (brak curl)"; return 0; }
    local body code
    if [ "$MAINTENANCE" = "1" ]; then
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$CHECK_URL/status.json" || true)"
        info "$CHECK_URL/status.json bez przepustki: $code (oczekiwane 503 – strona wciąż włączona)"
        body="$(curl -fsS --max-time 15 -H "X-Maintenance-Bypass: $BYPASS_TOKEN" "$CHECK_URL/status.json" 2>&1)" || { info "$body"; return 1; }
    else
        body="$(curl -fsS --max-time 15 "$CHECK_URL/status.json" 2>&1)" || { info "$body"; return 1; }
    fi
    info "$CHECK_URL/status.json przez proxy: $(printf '%s' "$body" | tr -d '\n' | cut -c1-200)"
    printf '%s' "$body" | grep -qE '"status" *: *"ok"'
}

# --------------------------------------------------------------------------------------------
# --pin-if-needed: woła scripts/deploy.sh przed `docker compose up -d db`. Serwer z danymi na 16
# (wolumen pg_data jest, pg18_data nie ma) i bez przypięcia dostaje je w .env – inaczej pierwsze
# `up -d db` po wdrożeniu postawiłoby PUSTĄ bazę 18, a entrypoint web zmigrowałby ją od zera.
# --------------------------------------------------------------------------------------------
if [ "$MODE" = "pin" ]; then
    if pinned; then
        echo "PostgreSQL: .env przypina $(env_get POSTGRES_IMAGE) – bez zmian."
    elif volume_exists "$VOL16" && ! volume_exists "$VOL18"; then
        pin_write
        echo "PostgreSQL: dane są na 16 (wolumen $VOL16), a 18 jeszcze nie ma – przypinam 16 w .env."
        echo "            Przejście na 18 to osobna czynność: scripts/upgrade_postgres18.sh (docs/OPERACJE.md § 19)."
    else
        echo "PostgreSQL: bez przypięcia – usługa db stoi na $PG18_IMAGE (wolumen $VOL18)."
    fi
    exit 0
fi

# --------------------------------------------------------------------------------------------
if [ "$MODE" = "status" ]; then
    log "Projekt compose: $PROJECT"
    if pinned; then
        info ".env: przypięcie do $(env_get POSTGRES_IMAGE) ($(env_get POSTGRES_VOLUME))"
    else
        info ".env: bez przypięcia (docker-compose.yml: $PG18_IMAGE, $PG18_VOLUME)"
    fi
    for v in "$VOL16" "$VOL18"; do
        if volume_exists "$v"; then
            info "wolumen $v: jest (utworzony $(docker volume inspect -f '{{.CreatedAt}}' "$v"))"
        else
            info "wolumen $v: brak"
        fi
    done
    if db_running; then
        info "działająca baza: $(dbq 'SELECT version()' 2>/dev/null || echo '(brak odpowiedzi)')"
    else
        info "usługa db nie działa"
    fi
    ls -1dt "$BACKUP_DIR"/pg18-upgrade-* "$BACKUP_DIR"/pg18-rollback-* 2>/dev/null | head -5 \
        | sed 's/^/    ślad przebiegu: /' || true
    exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="${BACKUP_DIR}/pg18-${MODE}-${STAMP}"

# --------------------------------------------------------------------------------------------
# --rollback: z powrotem na 16, na wolumenie sprzed przejścia.
# --------------------------------------------------------------------------------------------
if [ "$MODE" = "rollback" ]; then
    log "Wycofanie na PostgreSQL 16 (projekt $PROJECT)"
    volume_exists "$VOL16" || die "brak wolumenu $VOL16 – nie ma do czego wracać (skasowany po okresie próbnym?)"
    if pinned && db_running && [ "$(server_version_num | cut -c1-2)" = "16" ]; then
        info "baza już stoi na 16 z przypięciem w .env – nic do zrobienia."
        exit 0
    fi
    warn "Dane zapisane na 18 od chwili przejścia NIE wrócą na 16 same. Skrypt zrobi ich zrzut"
    warn "(${WORK}/pg18-rollback-*.dump) – przeniesienie ich na 16 to praca ręczna (OPERACJE § 19.5)."
    if [ "$YES" != "1" ] && [ "$DRY_RUN" != "1" ]; then
        die "wycofanie wymaga --yes (albo --dry-run, żeby zobaczyć plan)"
    fi
    run mkdir -p "$WORK"
    [ "$DRY_RUN" = "1" ] || chmod 700 "$WORK"
    [ "$DRY_RUN" = "1" ] || TIMELINE="$WORK/timeline.txt"
    # Błąd po włączeniu strony prac technicznych zostawia ją włączoną – i mówi to głośno.
    rollback_exit() {
        local rc=$?
        if [ "$rc" -ne 0 ] && [ "$MAINT_ON" = "1" ]; then
            printf '
!!! Wycofanie przerwane (kod %s).
' "$rc" >&2
            maintenance_left_on_banner
        fi
    }
    trap rollback_exit EXIT
    log "0/6 Kontrole: strona prac technicznych"
    maintenance_preflight
    log "1/6 Strona „Prace techniczne” – WŁĄCZENIE"
    if [ "$DRY_RUN" = "1" ]; then
        info "[próba] scripts/maintenance.sh on --message \"$MAINTENANCE_MESSAGE\" (od tej chwili 503)"
    else
        maintenance_on "Przywracanie poprzedniej wersji bazy danych."
    fi
    log "2/6 Zatrzymanie aplikacji (web, worker, beat)"
    run dc stop web worker beat
    mark app_stopped
    if db_running; then
        [ "$DRY_RUN" = "1" ] || wait_no_clients
        log "3/6 Zrzut bazy 18 przed wycofaniem"
        if [ "$DRY_RUN" = "1" ]; then
            info "[próba] pg_dump -Fc > ${WORK}/pg18-rollback-${STAMP}.dump"
        else
            assert_quiesced "zrzut bazy 18 przed wycofaniem"
            mark dump_pg18_rollback_start
            dc exec -T db pg_dump -U "$PG_USER" -d "$PG_DB" -Fc </dev/null > "${WORK}/pg18-rollback-${STAMP}.dump"
            chmod 600 "${WORK}/pg18-rollback-${STAMP}.dump"
            [ -s "${WORK}/pg18-rollback-${STAMP}.dump" ] || die "zrzut bazy 18 jest pusty – przerywam, NIC nie zostało przełączone"
            mark dump_pg18_rollback_end
            ( cd "$WORK" && sha256sum "pg18-rollback-${STAMP}.dump" ) | tee "$WORK/dumps.sha256" | sed 's/^/    sha256 /'
            ls -lh "${WORK}/pg18-rollback-${STAMP}.dump"
        fi
    else
        warn "usługa db nie działa – wycofuję bez zrzutu bazy 18"
    fi
    log "4/6 Przypięcie 16 w .env i start bazy na $VOL16"
    if [ "$DRY_RUN" = "1" ]; then
        info "[próba] cp .env ${WORK}/env.before-rollback; dopisanie POSTGRES_IMAGE=$PG16_IMAGE, POSTGRES_VOLUME=$PG16_VOLUME"
    else
        cp -p .env "${WORK}/env.before-rollback"
        pinned || pin_write
    fi
    run dc up -d --no-deps db
    if [ "$DRY_RUN" != "1" ]; then
        wait_db_ready || die "PostgreSQL 16 nie odpowiada – sprawdź: docker compose logs db"
        v="$(server_version_num)"
        [ "${v:0:2}" = "16" ] || die "po wycofaniu baza zgłasza wersję $v, a nie 16"
        info "baza: $(dbq 'SELECT version()')"
    fi
    log "5/6 Start aplikacji i kontrole (przez proxy z przepustką operatora)"
    run dc up -d web worker beat
    if [ "$DRY_RUN" != "1" ]; then
        wait_web_healthy || die "web nie jest healthy po ${WEB_HEALTH_TIMEOUT}s – docker compose logs web"
        mark app_healthy
        dc exec -T web python manage.py db_connections </dev/null || true
        ok=0
        for _ in $(seq 1 $((STATUS_TIMEOUT / 10))); do
            if status_json_ok && proxy_status_ok; then ok=1; break; fi
            sleep 10
        done
        [ "$ok" = "1" ] || die "/status.json nie wróciło do status=ok w ${STATUS_TIMEOUT}s (od środka albo przez proxy)"
        mark checks_ok
    fi
    log "6/6 Strona „Prace techniczne” – WYŁĄCZENIE"
    if [ "$DRY_RUN" = "1" ]; then
        info "[próba] scripts/maintenance.sh off"
    else
        maintenance_off
    fi
    log "Wycofanie zakończone: PostgreSQL 16, wolumen $VOL16. Wolumen $VOL18 został nietknięty."
    exit 0
fi

# --------------------------------------------------------------------------------------------
# Przejście 16 -> 18
# --------------------------------------------------------------------------------------------
log "0/9 Kontrole wstępne – bez przerwy (projekt $PROJECT, katalog kopii $BACKUP_DIR)"
command -v docker >/dev/null 2>&1 || die "brak dockera"
info "$(docker compose version)"

if ! pinned; then
    if db_running && [ "$(server_version_num | cut -c1-2)" = "18" ]; then
        info "baza już stoi na PostgreSQL 18 bez przypięcia – przejście zostało zrobione wcześniej."
        info "$(dbq 'SELECT version()')"
        exit 0
    fi
    die ".env nie przypina 16 (POSTGRES_VOLUME), a baza nie stoi na 18 – stan nieznany, sprawdź --status"
fi
[ "$(env_get POSTGRES_VOLUME)" = "$PG16_VOLUME" ] || die "nieoczekiwane przypięcie POSTGRES_VOLUME=$(env_get POSTGRES_VOLUME)"
volume_exists "$VOL16" || die "brak wolumenu $VOL16 z danymi 16"
db_running || die "usługa db (16) nie działa – uruchom ją (docker compose up -d db) i spróbuj ponownie"
V16="$(server_version_num)"
[ "${V16:0:2}" = "16" ] || die "działająca baza zgłasza wersję ${V16:-?}, oczekiwałem 16"
info "działa: $(dbq 'SELECT version()')"

if volume_exists "$VOL18"; then
    if [ "$RECREATE_PG18" = "1" ]; then
        warn "wolumen $VOL18 istnieje i zostanie SKASOWANY (--recreate-pg18-volume)"
    else
        die "wolumen $VOL18 już istnieje (poprzednie przejście, potem wycofanie?). Jeśli ma zniknąć: --recreate-pg18-volume"
    fi
fi

DB_BYTES="$(dbq "SELECT pg_database_size(current_database())")"
info "rozmiar bazy: $((DB_BYTES / 1024 / 1024)) MB"
# Miejsce: zrzuty trafiają do BACKUP_DIR, a nowy klaster do katalogu Dockera. Zapas: 5× rozmiar
# bazy (dwa zrzuty + nowy klaster + WAL odtwarzania), nie mniej niż 2 GB.
NEED_KB=$(( DB_BYTES * 5 / 1024 ))
[ "$NEED_KB" -ge 2097152 ] || NEED_KB=2097152
mkdir -p "$BACKUP_DIR"
DOCKER_ROOT="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
for path in "$BACKUP_DIR" "$DOCKER_ROOT"; do
    avail_kb="$(df -Pk "$path" 2>/dev/null | awk 'NR==2 {print $4}' || true)"
    [ -n "$avail_kb" ] || { warn "nie odczytałem wolnego miejsca dla $path"; continue; }
    info "wolne miejsce $path: $((avail_kb / 1024)) MB (wymagane $((NEED_KB / 1024)) MB)"
    [ "$avail_kb" -ge "$NEED_KB" ] || die "za mało miejsca na $path"
done

LATEST_BACKUP="$(ls -1t "$BACKUP_DIR"/db-*.dump.gpg 2>/dev/null | head -1 || true)"
if [ -z "$LATEST_BACKUP" ]; then
    msg="w $BACKUP_DIR nie ma żadnej kopii nocnej (db-*.dump.gpg)"
    if [ "$ALLOW_STALE_BACKUP" = "1" ]; then warn "$msg – idę dalej (--allow-stale-backup)"; else die "$msg (świadomie: --allow-stale-backup)"; fi
else
    age_h=$(( ( $(date +%s) - $(stat -c %Y "$LATEST_BACKUP") ) / 3600 ))
    info "ostatnia kopia nocna: $(basename "$LATEST_BACKUP") (${age_h} h temu)"
    if [ "$age_h" -gt "$BACKUP_MAX_AGE_HOURS" ]; then
        msg="kopia nocna ma ${age_h} h (limit ${BACKUP_MAX_AGE_HOURS} h)"
        if [ "$ALLOW_STALE_BACKUP" = "1" ]; then warn "$msg – idę dalej (--allow-stale-backup)"; else die "$msg (świadomie: --allow-stale-backup)"; fi
    fi
fi
dc exec -T web python manage.py record_backup_status --show </dev/null 2>/dev/null | sed 's/^/    /' || true
if pgrep -f 'scripts/backup(_verify)?\.sh' >/dev/null 2>&1; then
    die "trwa kopia zapasowa (scripts/backup.sh / backup_verify.sh) – poczekaj, aż się skończy"
fi

# Obraz 18 pobieramy PRZED przerwą – pobieranie nie ma prawa wydłużać przestoju.
if [ "$DRY_RUN" = "1" ]; then
    docker image inspect "$PG18_IMAGE" >/dev/null 2>&1 \
        && info "obraz $PG18_IMAGE jest lokalnie" || info "[próba] docker pull $PG18_IMAGE"
else
    run docker pull -q "$PG18_IMAGE"
fi
if docker image inspect "$PG18_IMAGE" >/dev/null 2>&1; then
    info "obraz: $(docker run --rm "$PG18_IMAGE" postgres --version)"
    for ext in $(dbq "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql'"); do
        docker run --rm "$PG18_IMAGE" sh -c "test -f \"\$(pg_config --sharedir)/extension/${ext}.control\"" \
            || die "obraz $PG18_IMAGE nie ma rozszerzenia $ext"
        info "rozszerzenie $ext: dostępne w $PG18_IMAGE"
    done
fi

NETWORK="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' \
    "$(dc ps -q db)" | grep -E '_internal$' | head -1)"
[ -n "$NETWORK" ] || die "nie znalazłem sieci compose 'internal' kontenera db"

# Strona prac technicznych: proxy musi widzieć katalog i musi istnieć przepustka operatora – inaczej
# krok 8 nie sprawdziłby serwisu przez proxy przed zdjęciem strony. Sprawdzane PRZED przerwą.
maintenance_preflight

if [ "$DRY_RUN" = "1" ]; then
    cat <<PLAN
==> Plan (tryb próbny – nic nie zostało zmienione):
    1/9 scripts/maintenance.sh on --message "$MAINTENANCE_MESSAGE"   (od tej chwili 503 „Prace techniczne”)
    2/9 docker compose stop web worker beat; w bazie zero klientów poza skryptem (maruderzy rozłączani po 30 s)
    3/9 stan bazy 16 -> ${WORK}/state-16/
    4/9 ZRZUTY KOŃCOWE (każdy: strona włączona, aplikacja stoi, zero klientów, czas > włączenia strony)
        pg_dump -Fc (klient 16)            -> ${WORK}/db-pg16-${STAMP}.dump   (droga powrotu)
        pg_dumpall --roles-only (klient 18) -> ${WORK}/roles-${STAMP}.sql
        pg_dump -Fc (klient 18, sieć $NETWORK) -> ${WORK}/db-for-pg18-${STAMP}.dump   (TEN jest odtwarzany)
        SHA-256 + rozmiar -> ${WORK}/dumps.sha256; ponowny stan 16 musi być identyczny
    5/9 docker compose stop db   (16; wolumen $VOL16 zostaje nietknięty)
        POSTGRES_IMAGE=$PG18_IMAGE POSTGRES_VOLUME=$PG18_VOLUME docker compose up -d --no-deps db
    6/9 SHA-256 zrzutu = zapisany w kroku 4, role + pg_restore --single-transaction --exit-on-error, ANALYZE
    7/9 stan bazy 18 -> ${WORK}/state-18/ i porównanie z zatrzymaną 16 (wiersze, sekwencje, rozszerzenia, role, obiekty)
    8/9 zdjęcie przypięcia z .env (kopia: ${WORK}/env.before-upgrade), docker compose up -d db web worker beat,
        /healthz/ + /status.json + manage.py db_connections, ${CHECK_URL:-<adres>}/status.json z przepustką operatora
    9/9 scripts/maintenance.sh off
    Błąd w krokach 2–7: automatyczny powrót na 16 (.env nadal przypina 16).
    Po każdym błędzie od kroku 1: strona prac technicznych ZOSTAJE włączona (wyłączenie ręczne).
PLAN
    exit 0
fi

# Jeden przebieg naraz – drugi, uruchomiony równolegle z drugiego terminala, zdublowałby zrzuty
# i rozjechał stan.
exec 9>"$BACKUP_DIR/.upgrade_postgres18.lock"
if command -v flock >/dev/null 2>&1; then
    flock -n 9 || die "inny przebieg scripts/upgrade_postgres18.sh trwa (blokada $BACKUP_DIR/.upgrade_postgres18.lock)"
fi

mkdir -p "$WORK"
chmod 700 "$WORK"
exec > >(tee -a "$WORK/upgrade.log") 2>&1
TIMELINE="$WORK/timeline.txt"
T0="$(date +%s)"
CREATED_PG18=0
COMMITTED=0
PGENV="$WORK/.pgenv"

on_exit() {
    local rc=$?
    rm -f "$PGENV"
    if [ "$rc" -ne 0 ] && [ "$COMMITTED" = "0" ]; then
        printf '\n!!! Przejście przerwane (kod %s) – przywracam PostgreSQL 16 i aplikację.\n' "$rc" >&2
        # .env nadal przypina 16, więc `up -d db` odtwarza kontener 16 na starym wolumenie.
        docker compose up -d --no-deps db || true
        wait_db_ready || printf 'BŁĄD: baza 16 nie wstała – docker compose logs db\n' >&2
        if [ "$CREATED_PG18" = "1" ]; then
            # Wolumen utworzony w TYM przebiegu niesie najwyżej częściowe odtworzenie zrzutu, który
            # leży w $WORK – kasujemy go, żeby ponowne uruchomienie zaczynało od czystej karty.
            docker volume rm "$VOL18" >/dev/null 2>&1 \
                && printf '    wolumen %s (utworzony w tym przebiegu) skasowany\n' "$VOL18" >&2 || true
        fi
        docker compose up -d web worker beat || true
        printf '!!! Stan: PostgreSQL 16 na %s, aplikacja uruchomiona. Log i zrzuty: %s\n' "$VOL16" "$WORK" >&2
    elif [ "$rc" -ne 0 ]; then
        printf '\n!!! Przejście przerwane (kod %s) PO przełączeniu – baza stoi na 18. Log: %s\n' "$rc" "$WORK" >&2
    fi
    if [ "$rc" -ne 0 ] && [ "$MAINT_ON" = "1" ]; then
        mark failed_maintenance_left_on
        maintenance_left_on_banner
    fi
}
trap on_exit EXIT

log "1/9 Strona „Prace techniczne” – WŁĄCZENIE (od tej chwili serwis odpowiada 503)"
maintenance_on "$MAINTENANCE_MESSAGE"

log "2/9 Zatrzymanie web, worker, beat i kontrola klientów bazy"
run dc stop web worker beat
mark app_stopped
# Połączenia aplikacji muszą zniknąć, zanim zrobimy zrzut: po nim nic nie może już pisać.
wait_no_clients
mark no_clients

log "3/9 Stan bazy 16 (zatrzymanej – nikt poza skryptem nie jest podłączony)"
snapshot "$WORK/state-16"
info "tabel: $(wc -l < "$WORK/state-16/rows.txt"), wierszy razem: $(awk '{s+=$2} END {print s+0}' "$WORK/state-16/rows.txt"), rozszerzenia: $(tr '\n' ' ' < "$WORK/state-16/extensions.txt")"

log "4/9 Zrzuty KOŃCOWE do $WORK (po włączeniu strony i zatrzymaniu aplikacji)"
DUMP16="$WORK/db-pg16-${STAMP}.dump"
assert_quiesced "zrzut klientem 16"
mark dump16_start
dc exec -T db pg_dump -U "$PG_USER" -d "$PG_DB" -Fc </dev/null > "$DUMP16"
mark dump16_end
[ -s "$DUMP16" ] || die "zrzut klientem 16 jest pusty"
# Hasło do klienta 18 przez plik z uprawnieniami 600 (--env-file), a nie w argumencie `-e`:
# argumenty procesu widać w `ps`, a plik znika w on_exit.
( umask 077; printf 'PGPASSWORD=%s\n' "$(env_get POSTGRES_PASSWORD)" > "$PGENV" )
pg18_client() { docker run --rm -i --network "$NETWORK" --env-file "$PGENV" "$PG18_IMAGE" "$@"; }
ROLES="$WORK/roles-${STAMP}.sql"
DUMP18="$WORK/db-for-pg18-${STAMP}.dump"
assert_quiesced "zrzut klientem 18 (ten będzie odtwarzany)"
DUMP18_STARTED="$(now_ts)"
mark dump18_start "$DUMP18_STARTED"
pg18_client pg_dumpall -h db -U "$PG_USER" --roles-only </dev/null > "$ROLES"
pg18_client pg_dump -h db -U "$PG_USER" -d "$PG_DB" -Fc </dev/null > "$DUMP18"
mark dump18_end
[ -s "$DUMP18" ] || die "zrzut klientem 18 jest pusty"
chmod 600 "$DUMP16" "$DUMP18" "$ROLES"
# Znacznik zrzutu do odtworzenia musi być późniejszy niż włączenie strony – to jest ten warunek,
# który wymusza cała kolejność. Przy --no-maintenance porównanie ze stopem aplikacji.
if [ "$MAINTENANCE" = "1" ]; then
    ts_after "$DUMP18_STARTED" "$MAINT_ON_AT" \
        || die "zrzut do odtworzenia ($DUMP18_STARTED) nie jest późniejszy niż włączenie strony ($MAINT_ON_AT)"
    info "zrzut do odtworzenia zaczęty $(awk -v a="$DUMP18_STARTED" -v b="$MAINT_ON_AT" 'BEGIN { printf "%.1f", a - b }') s po włączeniu strony prac technicznych"
fi
# SHA-256 i rozmiar: krok 6 sprawdza sumę tuż przed `pg_restore`, więc odtwarzany jest dokładnie
# ten plik, a nie wcześniejszy zrzut (np. z kopii nocnej albo kroku 4a wdrożenia) o tej samej nazwie.
( cd "$WORK" && sha256sum "$(basename "$DUMP18")" "$(basename "$ROLES")" "$(basename "$DUMP16")" ) > "$WORK/dumps.sha256"
sed 's/^/    sha256 /' "$WORK/dumps.sha256"
ls -l "$DUMP16" "$DUMP18" "$ROLES" | awk '{print "    rozmiar", $5, $NF}'
# Kontrola czytelności: spis treści zrzutu przez pg_restore 18 (ten sam, który będzie odtwarzał).
TOC_ENTRIES="$(pg18_client pg_restore -l < "$DUMP18" | grep -vc '^;')"
info "spis treści zrzutu dla 18: ${TOC_ENTRIES} pozycji"
# Ponowny stan 16 PO zrzutach: identyczny = nikt nie pisał do bazy w trakcie zrzucania.
snapshot "$WORK/state-16-after-dump"
for f in rows sequences; do
    diff -u "$WORK/state-16/$f.txt" "$WORK/state-16-after-dump/$f.txt" >&2 \
        || die "stan bazy 16 zmienił się w trakcie zrzutów ($f) – ktoś pisał mimo zatrzymanej aplikacji"
done
info "stan 16 po zrzutach identyczny ze stanem sprzed nich"

log "5/9 Zatrzymanie PostgreSQL 16 (wolumen $VOL16 zostaje nietknięty) i start 18 na $VOL18"
run dc stop db
if volume_exists "$VOL18"; then
    run dc rm -f -s db
    run docker volume rm "$VOL18"
fi
CREATED_PG18=1
pg18() { env POSTGRES_IMAGE="$PG18_IMAGE" POSTGRES_VOLUME="$PG18_VOLUME" docker compose "$@"; }
run pg18 up -d --no-deps db
wait_db_ready || die "PostgreSQL 18 nie odpowiada po inicjalizacji – docker compose logs db"
V18="$(server_version_num)"
[ "${V18:0:2}" = "18" ] || die "nowa baza zgłasza wersję ${V18:-?}, a nie 18"
info "działa: $(dbq 'SELECT version()')"
info "checksums: $(dbq 'SHOW data_checksums'), locale: $(dbq "SELECT datcollate FROM pg_database WHERE datname = current_database()")"
[ "$(dbq "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'")" = "0" ] \
    || die "baza $PG_DB na 18 nie jest pusta – to nie jest świeży klaster"

log "6/9 Odtworzenie zrzutu końcowego: role, baza, ANALYZE"
( cd "$WORK" && sha256sum -c --quiet dumps.sha256 ) \
    || die "suma SHA-256 zrzutu nie zgadza się z zapisaną w kroku 4 – nie odtwarzam"
info "SHA-256 zgodne: $(cut -c1-16 < "$WORK/dumps.sha256" | head -1)… $(basename "$DUMP18")"
mark restore_start
# Konto aplikacji zakłada entrypoint obrazu (POSTGRES_USER z .env); zrzut ról zakłada **pozostałe**
# i ustawia atrybuty wszystkich. Linijka CREATE ROLE konta aplikacji wypada, bo rola już istnieje,
# a psql z ON_ERROR_STOP zatrzymałby się na niej.
grep -v -E "^CREATE ROLE \"?${PG_USER}\"?;$" "$ROLES" \
    | dc exec -T db psql -X -q -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 >/dev/null
# --single-transaction + --exit-on-error: albo cała baza, albo nic (nigdy „prawie cała”).
# Właściciele obiektów zostają (bez --no-owner) – te same role co na 16.
dc exec -T db pg_restore -U "$PG_USER" -d "$PG_DB" --exit-on-error --single-transaction < "$DUMP18"
mark restore_end
info "pg_restore zakończony"
dbq "ANALYZE" >/dev/null
info "ANALYZE zakończony (statystyki planisty – zrzut ich nie przenosi)"

log "7/9 Porównanie stanu 18 ze stanem zatrzymanej 16"
snapshot "$WORK/state-18"
compare_snapshots "$WORK/state-16" "$WORK/state-18" || die "stan bazy 18 różni się od 16 (szczegóły wyżej, pliki w $WORK)"
mark compare_ok

log "8/9 Przełączenie: zdjęcie przypięcia z .env, start aplikacji, kontrole z przepustką operatora"
cp -p .env "$WORK/env.before-upgrade"
pin_remove
pinned && die "nie udało się zdjąć przypięcia z .env"
COMMITTED=1
mark switched
# Bez przypięcia compose sam wskazuje 18 na pg18_data – to jest ten sam kontener co w kroku 5,
# więc `up -d` go nie odtwarza (brak zmian w konfiguracji).
run dc up -d db web worker beat
wait_web_healthy || die "web nie jest healthy po ${WEB_HEALTH_TIMEOUT}s. Baza stoi na 18. Diagnoza: docker compose logs web. Wycofanie: scripts/upgrade_postgres18.sh --rollback --yes"
mark app_healthy
T_UP="$(date +%s)"
dc exec -T web python manage.py db_connections </dev/null | sed 's/^/    /' || warn "manage.py db_connections nie zadziałało"
UNAPPLIED="$(dc exec -T web python manage.py showmigrations --plan </dev/null | grep -c '^\[ \]' || true)"
[ "${UNAPPLIED:-0}" = "0" ] || warn "niezastosowane migracje: $UNAPPLIED (entrypoint web powinien był je wykonać)"
ok=0
for _ in $(seq 1 $((STATUS_TIMEOUT / 10))); do
    # „queue” bywa false przez pierwszą minutę – dopóki worker nie zamelduje się po restarcie.
    if status_json_ok; then ok=1; break; fi
    sleep 10
done
[ "$ok" = "1" ] || die "/status.json nie wróciło do status=ok w ${STATUS_TIMEOUT}s. Baza stoi na 18. Wycofanie: scripts/upgrade_postgres18.sh --rollback --yes"
# Przez proxy – tak, jak zobaczą to uczestnicy po zdjęciu strony. Z przepustką, bo strona jeszcze
# wisi; niepowodzenie zostawia ją włączoną (on_exit).
proxy_status_ok || die "$CHECK_URL/status.json przez proxy (z przepustką) nie daje status=ok – strona prac technicznych zostaje"
mark checks_ok

log "9/9 Strona „Prace techniczne” – WYŁĄCZENIE"
maintenance_off
T_END="$(date +%s)"
info "przerwa w działaniu serwisu: ok. $(( (T_END - T0) / 60 )) min $(( (T_END - T0) % 60 )) s (aplikacja healthy po $(( T_UP - T0 )) s)"

log "Gotowe: PostgreSQL $(dbq 'SHOW server_version') na wolumenie $VOL18."
info "Ślad przejścia (log, stany, zrzuty, timeline.txt, dumps.sha256): $WORK"
info "Stary wolumen $VOL16 zostaje nietknięty – droga powrotu: scripts/upgrade_postgres18.sh --rollback --yes"
info "Następnie: scripts/backup.sh && scripts/backup_verify.sh (pierwsza kopia z 18 i test jej odtworzenia)"
info "Skasowanie $VOL16 najwcześniej po 14 dniach bez wycofania – docs/OPERACJE.md § 19.6"
