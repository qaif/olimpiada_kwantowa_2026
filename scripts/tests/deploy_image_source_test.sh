#!/usr/bin/env bash
# Test rozgałęzienia „skąd bierzemy obraz” w kroku 4/8 wdrożenia (`scripts/deploy.sh`).
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu):
#   scripts/tests/deploy_image_source_test.sh
#
# Najważniejszy przypadek jest pierwszy i jest nim **brak** zmiennej `WEB_IMAGE`: wdrożenie
# Olimpiady Kwantowej ma wykonać dokładnie te polecenia, co przed etapem 2 — ani jednego więcej
# (docs/UNIWERSALNY-ETAP-2.md § 0.2 pkt 11, § 1.7.2). Dlatego test nie sprawdza „czy jest gałąź”,
# tylko porównuje **całą listę** wywołań `docker` z listą wpisaną tutaj wprost.
#
# Jak to jest uruchamiane bez serwera: krok 4/8 to skrypt wysyłany przez SSH here-documentem,
# więc test wycina jego treść z `scripts/deploy.sh` (nie kopiuje jej!) i uruchamia w katalogu
# tymczasowym na atrapach `docker` i generatora Caddy'ego. Sprawdzamy ten sam tekst, który
# pojedzie na produkcję – kopia w teście zestarzałaby się przy pierwszej poprawce skryptu.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY="$ROOT/scripts/deploy.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/deploy-image-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

SRV="$WORK/srv"            # udawany $REMOTE_DIR na serwerze
LOG="$WORK/docker.log"     # wywołania `docker` w kolejności

failures=0
check() {
  if [ "$2" -eq 0 ]; then
    printf 'ok   %s\n' "$1"
  else
    printf 'FAIL %s\n' "$1"
    failures=$((failures + 1))
  fi
}

# 0. Składnia całego skryptu wdrożeniowego.
bash -n "$DEPLOY"
check "scripts/deploy.sh przechodzi bash -n" $?

# Ciało zdalnego skryptu kroku 4/8: od linii `bash -s <<'REMOTE'` po zamykające `REMOTE`.
awk '/log "4\/8 Konfiguracja proxy/ { seen = 1 }
     seen && /bash -s <<.REMOTE.$/ { inside = 1; next }
     inside && /^REMOTE$/ { exit }
     inside { print }' "$DEPLOY" >"$WORK/krok4.sh"
[ -s "$WORK/krok4.sh" ] && grep -q 'docker compose up -d db' "$WORK/krok4.sh"
check "udało się wyciąć zdalny skrypt kroku 4/8 z deploy.sh" $?

mkdir -p "$SRV/scripts" "$SRV/deploy" "$WORK/bin"

# Atrapa `docker`: zapisuje wywołanie i udaje zdrową bazę (krok czeka na `db=healthy`).
# `compose config` podaje nazwę projektu, a `volume inspect` odpowiada według DOCKER_VOLUMES –
# tyle potrzebuje `scripts/upgrade_postgres18.sh --pin-if-needed` (PostgreSQL 16 -> 18).
cat >"$WORK/bin/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  *"compose ps"*) echo "db=healthy" ;;
  *"exec -T db psql"*) echo 0 ;;   # liczba klientów bazy przy --maintenance
  "compose config") echo "name: olimpiada" ;;
  "volume inspect "*)
    case " ${DOCKER_VOLUMES:-} " in *" $3 "*) exit 0 ;; *) exit 1 ;; esac ;;
esac
exit 0
STUB
chmod +x "$WORK/bin/docker"

# Przypięcie PostgreSQL-a 16 woła PRAWDZIWY skrypt (to jego zachowanie jest tu sprawdzane),
# z prawdziwym docker-compose.yml – skrypt odmawia pracy z plikiem, który nie zna POSTGRES_VOLUME.
cp "$ROOT/scripts/upgrade_postgres18.sh" "$SRV/scripts/"
cp "$ROOT/docker-compose.yml" "$SRV/"
# Strona „Prace techniczne”: krok 4/8 kopiuje ją do katalogu stanu (prawdziwy skrypt, bez dockera).
cp "$ROOT/scripts/maintenance.sh" "$SRV/scripts/"
mkdir -p "$SRV/deploy/maintenance"
cp "$ROOT/deploy/maintenance/index.html" "$SRV/deploy/maintenance/"

# Atrapa generatora Caddy'ego: jego własny test jest osobno (render_caddyfile_test.sh).
cat >"$SRV/scripts/render_caddyfile.sh" <<'STUB'
#!/usr/bin/env bash
: > deploy/Caddyfile.generated
STUB

krok4() {
  # krok4 "<WEB_IMAGE>" ["<istniejące wolumeny>"] – jeden przebieg kroku 4/8 w piaskownicy;
  # zwraca kod wyjścia skryptu.
  : >"$LOG"
  ( cd "$SRV" && PATH="$WORK/bin:$PATH" DOCKER_LOG="$LOG" REMOTE_DIR="$SRV" WEB_IMAGE="$1" \
      DOCKER_VOLUMES="${2:-}" MAINTENANCE="${MAINTENANCE:-0}" MAINTENANCE_MESSAGE="Test." \
      MAINTENANCE_MINUTES=10 bash "$WORK/krok4.sh" ) >"$WORK/stdout" 2>&1
}

# Dzisiejszy przebieg kroku 4/8, co do wywołania: build na serwerze, sprawdzenie, czy bazę trzeba
# przypiąć do PostgreSQL-a 16 (nazwa projektu + wolumen `pg_data` – na czystym serwerze go nie ma),
# start bazy, dwa odpytania o stan (pętla oczekiwania + twarde sprawdzenie przed kopią z kroku 4a).
DZISIAJ='compose build --pull web
compose config
volume inspect olimpiada_pg_data
compose up -d db
compose ps --format {{.Service}}={{.Health}}
compose ps --format {{.Service}}={{.Health}}'
Z_REJESTRU='compose pull web
compose config
volume inspect olimpiada_pg_data
compose up -d db
compose ps --format {{.Service}}={{.Health}}
compose ps --format {{.Service}}={{.Health}}'

printf 'APP_VERSION=test\n' >"$SRV/.env"

# 1. Bez WEB_IMAGE – przebieg Konkursu #1, znak w znak taki, jak był.
krok4 ""
check "krok 4/8 bez WEB_IMAGE kończy się powodzeniem" $?
[ "$(cat "$LOG")" = "$DZISIAJ" ]
check "bez WEB_IMAGE polecenia docker są dokładnie dzisiejsze" $?
[ "$(cat "$LOG")" = "$DZISIAJ" ] || { printf -- '--- wykonane:\n'; sed 's/^/     /' "$LOG"; }
! grep -qE '^WEB_IMAGE=' "$SRV/.env"
check "bez WEB_IMAGE w .env nie pojawia się wpis o obrazie" $?
grep -qE '^EXTRA_DOMAINS=' "$SRV/.env" && grep -qE '^CADDYFILE_PATH=' "$SRV/.env"
check "krok nadal dokłada do .env EXTRA_DOMAINS i CADDYFILE_PATH" $?
grep -qE '^MAINTENANCE_BYPASS_TOKEN=[A-Za-z0-9]{40}$' "$SRV/.env"
check "krok dopisuje do .env przepustkę MAINTENANCE_BYPASS_TOKEN (40 znaków)" $?
cmp -s "$ROOT/deploy/maintenance/index.html" "$SRV/maintenance/page/index.html" && [ ! -e "$SRV/maintenance/on" ]
check "krok kopiuje stronę prac technicznych do maintenance/page i jej NIE włącza" $?
token_before="$(grep '^MAINTENANCE_BYPASS_TOKEN=' "$SRV/.env")"

# 2. Z WEB_IMAGE – pobranie zamiast budowania i wpis do .env dla kolejnych wywołań compose'a.
krok4 "ghcr.io/qaif/olimpiada-web:v9.9.9"
check "krok 4/8 z WEB_IMAGE kończy się powodzeniem" $?
[ "$(cat "$LOG")" = "$Z_REJESTRU" ]
check "z WEB_IMAGE obraz jest pobierany, a nie budowany" $?
[ "$(grep -c '^WEB_IMAGE=' "$SRV/.env")" = "1" ] &&
  grep -qx 'WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v9.9.9' "$SRV/.env"
check "wartość trafia do .env dokładnie raz" $?

# 3. Kolejne wdrożenie z innym tagiem podmienia wpis, a nie dokłada drugiego.
krok4 "ghcr.io/qaif/olimpiada-web:v9.9.10"
[ "$(grep -c '^WEB_IMAGE=' "$SRV/.env")" = "1" ] &&
  grep -qx 'WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v9.9.10' "$SRV/.env"
check "drugie wdrożenie z rejestru podmienia wpis w .env" $?

# 4. Powrót do budowania na serwerze sprząta po sobie – inaczej compose trzymałby stary obraz.
krok4 ""
[ "$(cat "$LOG")" = "$DZISIAJ" ] && ! grep -qE '^WEB_IMAGE=' "$SRV/.env"
check "wdrożenie bez WEB_IMAGE wraca do budowania i kasuje wpis z .env" $?
[ "$(grep -c '^MAINTENANCE_BYPASS_TOKEN=' "$SRV/.env")" = "1" ] && [ "$(grep '^MAINTENANCE_BYPASS_TOKEN=' "$SRV/.env")" = "$token_before" ]
check "kolejne wdrożenia nie zmieniają ani nie dublują przepustki" $?

# 4a. --maintenance: strona włączona i aplikacja zatrzymana PRZED startem bazy i kopią z kroku 4a.
MAINTENANCE=1 krok4 ""
check "krok 4/8 z --maintenance kończy się powodzeniem" $?
[ -f "$SRV/maintenance/on" ] && [ -f "$SRV/maintenance/.deploy-maintenance-on" ]
check "--maintenance: flaga prac technicznych i znacznik czasu włączenia są na miejscu" $?
stop_line="$(grep -n '^compose stop web worker beat$' "$LOG" | cut -d: -f1)"
up_line="$(grep -n '^compose up -d db$' "$LOG" | cut -d: -f1)"
build_line="$(grep -n '^compose build --pull web$' "$LOG" | cut -d: -f1)"
[ -n "$stop_line" ] && [ -n "$up_line" ] && [ "$build_line" -lt "$stop_line" ] && [ "$stop_line" -lt "$up_line" ]
check "--maintenance: build -> stop web/worker/beat -> up -d db (kolejność w logu docker)" $?
grep -q 'exec -T db psql' "$LOG" && grep -q 'zero klientów' "$WORK/stdout"
check "--maintenance: kontrola klientów bazy przed kopią" $?
rm -f "$SRV/maintenance/on" "$SRV/maintenance/.deploy-maintenance-on" "$SRV/maintenance/info.html"

# 5. Nagłówek skryptu opisuje zmienną – wdrożenie bywa czytane wtedy, gdy nie ma czasu na docs/.
grep -q 'WEB_IMAGE=ghcr.io/' "$DEPLOY"
check "deploy.sh ma w nagłówku przykład użycia WEB_IMAGE" $?

# 6. PostgreSQL 16 -> 18: serwer z danymi na 16 (`pg_data`, bez `pg18_data`) dostaje przypięcie
#    do 16 PRZED `up -d db` – inaczej wdrożenie postawiłoby pustą bazę 18 (docs/OPERACJE.md § 19).
krok4 "" "olimpiada_pg_data"
check "krok 4/8 na serwerze z bazą 16 kończy się powodzeniem" $?
grep -qx 'POSTGRES_IMAGE=postgres:16-alpine' "$SRV/.env" &&
  grep -qx 'POSTGRES_VOLUME=pg_data:/var/lib/postgresql/data' "$SRV/.env"
check "serwer z pg_data i bez pg18_data dostaje w .env przypięcie do 16" $?
[ "$(sed -n '/compose up -d db/=' "$LOG")" -gt "$(sed -n '/volume inspect olimpiada_pg18_data/=' "$LOG")" ]
check "przypięcie zapada przed startem bazy" $?

# 7. Kolejne wdrożenie: przypięcie już jest – bez drugiego wpisu i bez pytania o wolumeny.
krok4 "" "olimpiada_pg_data"
[ "$(grep -c '^POSTGRES_VOLUME=' "$SRV/.env")" = "1" ] && ! grep -q 'volume inspect' "$LOG"
check "przypięcie nie jest dopisywane drugi raz" $?

# 8. Po przejściu (jest pg18_data, przypięcia brak) i na nowej instalacji – .env bez przypięcia.
sed -i '/^POSTGRES_IMAGE=/d; /^POSTGRES_VOLUME=/d' "$SRV/.env"
krok4 "" "olimpiada_pg_data olimpiada_pg18_data"
! grep -qE '^POSTGRES_(IMAGE|VOLUME)=' "$SRV/.env"
check "po przejściu na 18 wdrożenie nie przypina 16" $?
krok4 ""
! grep -qE '^POSTGRES_(IMAGE|VOLUME)=' "$SRV/.env"
check "nowa instalacja (bez wolumenów) nie dostaje przypięcia" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy źródła obrazu w kroku 4/8 przeszły.\n'
