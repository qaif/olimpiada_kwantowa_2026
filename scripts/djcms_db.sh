#!/usr/bin/env bash
# Baza i rola Postgresa dla wersji porównawczej na django CMS (docs/tasks/DJ-01.md § 8.9).
#
# Zakłada (albo doprowadza do stanu docelowego) w **tym samym** kontenerze `db`:
#   - rolę `olimpiada_djcms` z hasłem `DJCMS_DB_PASSWORD`, bez superusera i bez tworzenia ról,
#   - bazę `olimpiada_djcms` należącą do tej roli (UTF8, polskie sortowanie jak baza główna),
#   - odebrane `CONNECT` dla PUBLIC na **obu** bazach: rola djcms nie może się połączyć z bazą
#     aplikacji głównej (reguła 11 – rozdział danych), a nikt poza nią i superuserem – z bazą djcms.
#
# Idempotentny: każdy krok jest „utwórz, jeśli brak” albo „ustaw na wartość docelową”, więc drugie
# uruchomienie z rzędu kończy się kodem 0 i nie zmienia niczego (test:
# scripts/tests/djcms_db_test.sh). Hasło jest ustawiane przy każdym uruchomieniu – zmiana
# `DJCMS_DB_PASSWORD` w .env i ponowne uruchomienie to cała procedura rotacji hasła.
#
# Dlaczego skrypt, a nie init-script obrazu Postgresa: te działają wyłącznie na pustym wolumenie,
# czyli nigdy na istniejącej instalacji.
#
# Hasło nie pojawia się w argumentach żadnego procesu (widać je w `ps` każdego konta na maszynie):
# trafia do psql zmienną ustawioną meta-poleceniem `\set` na **standardowym wejściu**, a w SQL-u
# jest cytowane przez psql (`:'djpass'`) – bez sklejania tekstu zapytania w powłoce.
#
# Użycie (z katalogu instalacji albo z dowolnego – skrypt przechodzi do korzenia repozytorium):
#   scripts/djcms_db.sh                    # produkcja: rola z NOCREATEDB
#   scripts/djcms_db.sh --allow-createdb   # dev: pytest-django zakłada test_olimpiada_djcms
# Zmienne: POSTGRES_USER, POSTGRES_DB, DJCMS_DB_PASSWORD – ze środowiska, a gdy nieustawione, z .env.
# `COMPOSE` (opcjonalnie) – polecenie compose'a, domyślnie `docker compose`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ALLOW_CREATEDB=0
for arg in "$@"; do
  case "$arg" in
    --allow-createdb) ALLOW_CREATEDB=1 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "djcms_db: nieznany argument „$arg”" >&2; exit 2 ;;
  esac
done

# Odczyt jak w scripts/render_caddyfile.sh: zmienna nieustawiona = bierzemy z .env (ostatnie
# wystąpienie wygrywa, jak w docker compose), bez `source` – plik należy do administratora serwera.
env_value() {
  local name="$1"
  if [ -n "${!name+x}" ]; then
    printf '%s' "${!name}"
  elif [ -f "$ROOT/.env" ]; then
    sed -n "s/^${name}=//p" "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047'
  fi
}

POSTGRES_USER="$(env_value POSTGRES_USER)"
POSTGRES_DB="$(env_value POSTGRES_DB)"
DJCMS_DB_PASSWORD="$(env_value DJCMS_DB_PASSWORD)"

[ -n "$POSTGRES_USER" ] || { echo "djcms_db: brak POSTGRES_USER (środowisko albo .env)" >&2; exit 1; }
[ -n "$POSTGRES_DB" ] || { echo "djcms_db: brak POSTGRES_DB (środowisko albo .env)" >&2; exit 1; }
# Minimum 16 znaków: rola ma LOGIN w kontenerze, do którego sieć compose'a ma dostęp z każdej
# usługi. deploy.sh generuje 32 znaki; krótsze hasło to prawie na pewno pomyłka w .env.
if [ "${#DJCMS_DB_PASSWORD}" -lt 16 ]; then
  echo "djcms_db: DJCMS_DB_PASSWORD musi mieć co najmniej 16 znaków (środowisko albo .env)" >&2
  exit 1
fi
if [ "$POSTGRES_DB" = "olimpiada_djcms" ]; then
  echo "djcms_db: POSTGRES_DB nie może nazywać się olimpiada_djcms" >&2
  exit 1
fi

if [ "$ALLOW_CREATEDB" = "1" ]; then CREATEDB=CREATEDB; else CREATEDB=NOCREATEDB; fi

# Wartość do meta-polecenia `\set nazwa '…'`: w cudzysłowie psql traktuje `\` jako początek
# sekwencji ucieczki, a `'` kończy napis – oba podwajamy/ucieczkujemy.
psql_quote() {
  local value="${1//\\/\\\\}"
  printf "'%s'" "${value//\'/\'\'}"
}

COMPOSE="${COMPOSE:-docker compose}"

# shellcheck disable=SC2086 # COMPOSE to celowo kilka słów ("docker compose")
{
  printf '\\set djpass %s\n' "$(psql_quote "$DJCMS_DB_PASSWORD")"
  printf '\\set maindb %s\n' "$(psql_quote "$POSTGRES_DB")"
  cat <<SQL
SELECT 'CREATE ROLE olimpiada_djcms LOGIN'
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'olimpiada_djcms')\gexec
ALTER ROLE olimpiada_djcms WITH LOGIN PASSWORD :'djpass' NOSUPERUSER NOCREATEROLE ${CREATEDB};
SELECT 'CREATE DATABASE olimpiada_djcms OWNER olimpiada_djcms TEMPLATE template0 ENCODING ''UTF8'' LC_COLLATE ''pl_PL.utf8'' LC_CTYPE ''pl_PL.utf8'''
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'olimpiada_djcms')\gexec
REVOKE ALL ON DATABASE olimpiada_djcms FROM PUBLIC;
GRANT CONNECT ON DATABASE olimpiada_djcms TO olimpiada_djcms;
REVOKE CONNECT ON DATABASE :"maindb" FROM PUBLIC;
SQL
} | $COMPOSE exec -T db psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"

echo "djcms_db: rola i baza olimpiada_djcms gotowe (${CREATEDB})."
