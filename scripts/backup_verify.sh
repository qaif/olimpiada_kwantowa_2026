#!/usr/bin/env bash
# Cotygodniowy test odtwarzania: najnowsza kopia wjeżdża do TYMCZASOWEGO Postgresa i jest liczona.
#
# Uruchamiany przez crona hosta (wpis /etc/cron.d/olimpiada-backup zakłada scripts/deploy.sh),
# ręcznie: `cd /opt/olimpiada && scripts/backup_verify.sh`.
#
# Po co osobny przebieg, skoro backup.sh kończy się bez błędu: „pg_dump zakończył się kodem 0”
# nie znaczy „z tej paczki da się odtworzyć olimpiadę”. Kopia potrafi być pusta (zrzut zrobiony
# w czasie, gdy baza nie odpowiadała), obcięta (skończyło się miejsce), zaszyfrowana hasłem,
# którego nikt już nie zna (ktoś zmienił BACKUP_PASSPHRASE i nie zmienił go w sejfie), albo
# nieczytelna dla pg_restore. Każdy z tych przypadków wychodzi dopiero przy odtwarzaniu – a dzień,
# w którym odtwarzanie jest potrzebne, jest najgorszym możliwym dniem na tę wiadomość.
#
# Dlatego test jest pełny i idzie tą samą drogą, co prawdziwa awaria: rozszyfruj -> pg_restore ->
# policz wiersze w tabelach, bez których zawody nie istnieją. Kontener jest jednorazowy i nie
# dotyka ani produkcyjnej bazy, ani sieci compose – stawiamy go na własnej, tymczasowej sieci.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

BACKUP_DIR="${BACKUP_DIR:-/opt/olimpiada-backups}"
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
# Ile wierszy w accounts_user wystarcza, żeby uznać zrzut za „nie pusty”. Jeden: konto koordynatora
# istnieje zawsze, także przed pierwszą rejestracją uczestnika. Wyższy próg kazałby przestawiać
# skrypt przed startem edycji, czyli dokładnie wtedy, gdy nikt na niego nie patrzy.
MIN_USERS="${MIN_USERS:-1}"

#: Tabele, których pustka znaczy, że kopia jest bezwartościowa. Nie „wszystkie” i nie „największe”:
#: to są cztery odpowiedzi na pytanie „czy da się z tego odtworzyć zawody” – kto startował
#: (accounts_user, accounts_participant), w czym (competitions_stage) i co oddał
#: (submissions_submission). Audyt (core_auditlog) dochodzi, bo bez niego nie da się obronić
#: żadnej decyzji komisji.
TABLES="accounts_user accounts_participant competitions_stage submissions_submission core_auditlog"

log() { printf '==> %s\n' "$*"; }
die() { printf 'BŁĄD: %s\n' "$*" >&2; exit 1; }

[ -f .env ] || die "brak pliku .env w $REPO_DIR"
set -a
# shellcheck disable=SC1091
. ./.env
set +a
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE musi być w .env}"

DUMP_PATH="${1:-$(ls -1t "$BACKUP_DIR"/db-*.dump.gpg 2>/dev/null | head -1 || true)}"
[ -n "$DUMP_PATH" ] && [ -f "$DUMP_PATH" ] || die "nie znalazłem kopii bazy w ${BACKUP_DIR}"
log "Sprawdzam: $DUMP_PATH"

CONTAINER="olimpiada-restore-test-$$"
NETWORK="olimpiada-restore-test-net-$$"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/olimpiada-verify-XXXXXX")"
chmod 700 "$WORK_DIR"
# Sprzątanie bezwarunkowe: nieudany test nie może zostawić na maszynie działającego Postgresa
# z kompletem danych osobowych i hasłem z tego skryptu.
cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# --- 1. Rozszyfrowanie -----------------------------------------------------------------------
log "1/4 Rozszyfrowanie"
DUMP_PLAIN="${WORK_DIR}/db.dump"
printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --yes --quiet \
    --pinentry-mode loopback --passphrase-fd 0 --decrypt --output "$DUMP_PLAIN" "$DUMP_PATH"
[ -s "$DUMP_PLAIN" ] || die "po rozszyfrowaniu paczka jest pusta"

# --- 2. Tymczasowy Postgres ------------------------------------------------------------------
# Hasło losowe i jednorazowe: kontener żyje kilkadziesiąt sekund, a hasło produkcyjne nie ma po co
# pojawiać się w `docker inspect` ani w liście procesów.
TEST_PASSWORD="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
log "2/4 Tymczasowy Postgres (${PG_IMAGE})"
docker network create "$NETWORK" >/dev/null
docker run -d --name "$CONTAINER" --network "$NETWORK" \
    -e POSTGRES_PASSWORD="$TEST_PASSWORD" -e POSTGRES_USER=verify -e POSTGRES_DB=verify \
    --tmpfs /var/lib/postgresql/data:rw,size=4g \
    "$PG_IMAGE" >/dev/null
for _ in $(seq 1 60); do
    docker exec "$CONTAINER" pg_isready -U verify -d verify >/dev/null 2>&1 && break
    sleep 1
done
docker exec "$CONTAINER" pg_isready -U verify -d verify >/dev/null 2>&1 \
    || die "tymczasowy Postgres nie wystartował"

# --- 3. pg_restore ---------------------------------------------------------------------------
log "3/4 pg_restore"
# `--exit-on-error` świadomie: częściowo odtworzona baza wygląda na kompletną i właśnie dlatego
# jest najgorszym możliwym wynikiem tego testu.
docker exec -i "$CONTAINER" pg_restore -U verify -d verify --no-owner --exit-on-error < "$DUMP_PLAIN"

# --- 4. Liczenie -----------------------------------------------------------------------------
log "4/4 SELECT count(*) na tabelach kluczowych"
FAILED=""
SUMMARY=""
for table in $TABLES; do
    count="$(docker exec "$CONTAINER" psql -U verify -d verify -Atc "SELECT count(*) FROM ${table}" 2>/dev/null || echo "")"
    if [ -z "$count" ]; then
        printf '    %-28s BRAK TABELI\n' "$table"
        FAILED="${FAILED} ${table}(brak)"
        continue
    fi
    printf '    %-28s %s\n' "$table" "$count"
    SUMMARY="${SUMMARY}${table}=${count} "
done
# Jedyny próg ilościowy jest na kontach. Pozostałe tabele bywają puste zgodnie z prawdą – przed
# pierwszym etapem nie ma ani jednego rozwiązania i to nie jest awaria kopii.
USERS="$(docker exec "$CONTAINER" psql -U verify -d verify -Atc "SELECT count(*) FROM accounts_user" 2>/dev/null || echo 0)"
[ "${USERS:-0}" -ge "$MIN_USERS" ] || FAILED="${FAILED} accounts_user(${USERS}<${MIN_USERS})"

if [ -n "$FAILED" ]; then
    printf 'TEST ODTWARZANIA NIEUDANY:%s\n' "$FAILED" >&2
    docker compose exec -T web python manage.py record_backup_status --failed \
        --note "test odtwarzania:${FAILED}" </dev/null || true
    exit 1
fi

# Znacznik czyta /status.json i watchdog alertów (apps.core.alerts). Dopiero ten zapis odróżnia
# „kopia powstała” od „kopia daje się odtworzyć”.
docker compose exec -T web python manage.py record_backup_status --verified \
    --note "$(basename "$DUMP_PATH"): ${SUMMARY}" </dev/null

log "Test odtwarzania zakończony powodzeniem."
