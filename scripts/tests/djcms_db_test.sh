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
#   4. rola nie jest superuserem i nie tworzy ról,
#   5. PUBLIC nie ma CONNECT do **żadnej** bazy poza szablonami i bazą djcms – także do `postgres`
#      i do bazy, której nazwy skrypt nie zna (test zakłada ją przed uruchomieniem i sprząta po sobie),
#      a rola djcms łączy się z `postgres` wyłącznie przy `--allow-createdb` (dev, pytest-django),
#   6. hasło spoza [A-Za-z0-9_-] albo krótsze niż 16 znaków jest odrzucane z czytelnym komunikatem,
#      **zanim** skrypt dotknie bazy (sprawdzane na atrapie compose'a, bez Postgresa).
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

# Baza, o której skrypt nic nie wie (jak baza odtworzona z kopii albo testowa w devie). Zakładana
# z domyślnymi uprawnieniami, czyli z CONNECT dla PUBLIC – skrypt ma go odebrać.
EXTRA_DB=djcms_db_test_extra
cleanup() {
  # shellcheck disable=SC2086
  $COMPOSE exec -T db dropdb -U "$PGUSER_MAIN" --if-exists "$EXTRA_DB" </dev/null >/dev/null 2>&1 || true
  rm -rf "${STUB_DIR:-}"
}
trap cleanup EXIT

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}
sql() {
  # shellcheck disable=SC2086
  $COMPOSE exec -T db psql -X -At -U "$PGUSER_MAIN" -d "$PGDB_MAIN" -c "$1" </dev/null
}

# --- 6. walidacja hasła (bez bazy) ---------------------------------------------------------------
# Atrapa compose'a: zapisuje argumenty i standardowe wejście. Odrzucenie ma nastąpić przed nią.
STUB_DIR="$(mktemp -d "${TMPDIR:-/tmp}/djcms-db-test.XXXXXX")"
cat >"$STUB_DIR/compose" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >"$(dirname "$0")/argv"
cat >"$(dirname "$0")/stdin"
STUB
chmod +x "$STUB_DIR/compose"
bad_password() {
  rm -f "$STUB_DIR/argv"
  out="$(DJCMS_DB_PASSWORD="$1" COMPOSE="$STUB_DIR/compose" bash scripts/djcms_db.sh 2>&1)"
  rc=$?
  [ "$rc" -eq 1 ] && [ ! -e "$STUB_DIR/argv" ] && printf '%s' "$out" | grep -q "$2"
}
bad_password 'krotkie1234' 'co najmniej 16 znaków'
check "hasło krótsze niż 16 znaków – odmowa przed compose'em" $?
for pw in 'haslo@db:5432/inne-x' 'haslo#fragment-12345' 'haslo?q=1&x=1234567' 'haslo%41procent12345' 'haslo ze spacja 12345' 'haslo.z.kropkami.1234'; do
  bad_password "$pw" 'wyłącznie litery'
  check "hasło „$pw” (znak niebezpieczny w DATABASE_URL) – odmowa z komunikatem" $?
done
DJCMS_DB_PASSWORD='Abc_def-0123456789xyz' COMPOSE="$STUB_DIR/compose" bash scripts/djcms_db.sh >/dev/null 2>&1
[ $? -eq 0 ] && grep -q "exec -T db psql" "$STUB_DIR/argv" && ! grep -q 'Abc_def-0123456789xyz' "$STUB_DIR/argv" \
  && grep -q "Abc_def-0123456789xyz" "$STUB_DIR/stdin"
check "hasło [A-Za-z0-9_-] przechodzi – przez stdin psql, nie w argumentach" $?
grep -q "FROM pg_database WHERE NOT datistemplate AND datname <> 'olimpiada_djcms'" "$STUB_DIR/stdin" \
  && grep -q "REVOKE CONNECT ON DATABASE postgres FROM olimpiada_djcms;" "$STUB_DIR/stdin"
check "SQL: REVOKE CONNECT FROM PUBLIC w pętli po bazach; bez flagi – bez CONNECT do postgres" $?

# --- 1–5. prawdziwa baza --------------------------------------------------------------------------
# shellcheck disable=SC2086
$COMPOSE exec -T db dropdb -U "$PGUSER_MAIN" --if-exists "$EXTRA_DB" </dev/null >/dev/null 2>&1
# shellcheck disable=SC2086
$COMPOSE exec -T db createdb -U "$PGUSER_MAIN" "$EXTRA_DB" </dev/null
check "przygotowanie: baza $EXTRA_DB (domyślne uprawnienia, CONNECT dla PUBLIC)" $?

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

# Wszystkie wpisy ACL każdej bazy (brak ACL = uprawnienia domyślne, czyli CONNECT/TEMP dla PUBLIC).
[ "$(sql "SELECT count(*) FROM pg_database d WHERE NOT d.datistemplate AND d.datname <> 'olimpiada_djcms'
          AND EXISTS (SELECT 1 FROM aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a
                      WHERE a.grantee = 0 AND a.privilege_type = 'CONNECT')")" = "0" ]
check "PUBLIC bez CONNECT do każdej bazy poza szablonami i olimpiada_djcms (także postgres)" $?
# shellcheck disable=SC2086
out="$($COMPOSE exec -T db psql -X -At -U olimpiada_djcms -d "$EXTRA_DB" -c 'SELECT 1' </dev/null 2>&1)"
printf '%s' "$out" | grep -q 'permission denied'
check "rola djcms NIE łączy się z bazą nieznaną skryptowi ($EXTRA_DB)" $?
# shellcheck disable=SC2086
out="$($COMPOSE exec -T db psql -X -At -U olimpiada_djcms -d postgres -c 'SELECT 1' </dev/null 2>&1)"
case " ${DJCMS_DB_TEST_ARGS:-} " in
  *" --allow-createdb "*)
    [ "$out" = "1" ]
    check "--allow-createdb: rola djcms łączy się z postgres (pytest-django zakłada bazę testową)" $? ;;
  *)
    printf '%s' "$out" | grep -q 'permission denied'
    check "rola djcms NIE łączy się z postgres" $? ;;
esac
[ "$(sql "SELECT has_database_privilege('$PGUSER_MAIN', '$PGDB_MAIN', 'CONNECT')")" = "t" ]
check "konto aplikacji głównej ($PGUSER_MAIN) nadal łączy się ze swoją bazą" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy djcms_db.sh przeszły.\n'
