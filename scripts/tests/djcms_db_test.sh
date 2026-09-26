#!/usr/bin/env bash
# Test scripts/djcms_db.sh na działającym compose (dev albo serwer): idempotencja i rozdział baz.
#
# Uruchomienie (z działającą usługą `db`, np. po `docker compose ... up -d db`):
#   scripts/tests/djcms_db_test.sh
#
# Sprawdza (docs/tasks/DJ-01.md § 8.9 i reguła 11 z § 7):
#   1. dwa uruchomienia z rzędu → kod 0 za każdym razem,
#   2. dokładnie jedna rola `olimpiada_djcms` i jedna baza `olimpiada_djcms`, właścicielem bazy jest rola,
#   3. rola djcms **nie** może połączyć się z bazą aplikacji głównej, a ze swoją – może,
#   4. rola nie jest superuserem i nie tworzy ról.
# Skrypt zmienia stan bazy dokładnie tak, jak zmieniłoby go zwykłe wdrożenie (to jest ta sama
# komenda), więc nie sprząta po sobie. `--allow-createdb` zostaje, jeśli podano go w
# DJCMS_DB_TEST_ARGS (dev); domyślnie test woła skrypt bez flag, jak produkcja.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
COMPOSE="${COMPOSE:-docker compose}"

# shellcheck disable=SC2086
if ! $COMPOSE exec -T db true >/dev/null 2>&1; then
  printf 'NIE WYKONANO: usługa `db` nie działa – ten test niczego nie sprawdził.\n' >&2
  exit 2
fi

env_value() {
  local name="$1"
  if [ -n "${!name+x}" ]; then printf '%s' "${!name}"
  elif [ -f "$ROOT/.env" ]; then sed -n "s/^${name}=//p" "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047'
  fi
}
PGUSER_MAIN="$(env_value POSTGRES_USER)"
PGDB_MAIN="$(env_value POSTGRES_DB)"

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}
sql() {
  # shellcheck disable=SC2086
  $COMPOSE exec -T db psql -X -At -U "$PGUSER_MAIN" -d "$PGDB_MAIN" -c "$1" </dev/null
}

# shellcheck disable=SC2086
bash scripts/djcms_db.sh ${DJCMS_DB_TEST_ARGS:-} >/dev/null
check "pierwsze uruchomienie djcms_db.sh – kod 0" $?
# shellcheck disable=SC2086
bash scripts/djcms_db.sh ${DJCMS_DB_TEST_ARGS:-} >/dev/null
check "drugie uruchomienie z rzędu – kod 0 (idempotencja)" $?

[ "$(sql "SELECT count(*) FROM pg_roles WHERE rolname = 'olimpiada_djcms'")" = "1" ]
check "dokładnie jedna rola olimpiada_djcms" $?
[ "$(sql "SELECT count(*) FROM pg_database WHERE datname = 'olimpiada_djcms' AND pg_get_userbyid(datdba) = 'olimpiada_djcms'")" = "1" ]
check "dokładnie jedna baza olimpiada_djcms, właścicielem jest rola djcms" $?
[ "$(sql "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = 'olimpiada_djcms'")" = "f" ]
check "rola djcms bez SUPERUSER i CREATEROLE" $?

# Połączenie przez gniazdo lokalne kontenera (uwierzytelnienie trust) – sprawdzamy wyłącznie
# uprawnienie CONNECT, które Postgres egzekwuje niezależnie od metody uwierzytelnienia.
# shellcheck disable=SC2086
out="$($COMPOSE exec -T db psql -X -At -U olimpiada_djcms -d "$PGDB_MAIN" -c 'SELECT 1' </dev/null 2>&1)"
printf '%s' "$out" | grep -q 'permission denied'
check "rola djcms NIE łączy się z bazą aplikacji głównej ($PGDB_MAIN)" $?
# shellcheck disable=SC2086
[ "$($COMPOSE exec -T db psql -X -At -U olimpiada_djcms -d olimpiada_djcms -c 'SELECT 1' </dev/null 2>&1)" = "1" ]
check "rola djcms łączy się ze swoją bazą" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy djcms_db.sh przeszły.\n'
