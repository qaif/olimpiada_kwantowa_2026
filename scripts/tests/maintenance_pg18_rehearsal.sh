#!/usr/bin/env bash
# Próba kolejności przejścia 16 -> 18 ze stroną „Prace techniczne” (wymóg organizatora z 25.09.2026,
# docs/OPERACJE.md § 19.4 i § 20). Dowodzi na prawdziwym stosie compose, że:
#   1. zapis zrobiony tuż PRZED włączeniem strony jest w bazie 18 (i każdy inny potwierdzony zapis),
#   2. zrzut odtworzony na 18 zaczął się PO włączeniu strony i po zatrzymaniu aplikacji,
#   3. odtworzony plik to dokładnie ten zrzut (SHA-256 z dumps.sha256 = SHA-256 pliku),
#   4. przez całą przerwę proxy odpowiadało stroną prac technicznych (503 + {"status":"maintenance"}),
#      nigdy gołym 502/504, a po wyłączeniu strony – znów 200,
#   5. strona jest na końcu wyłączona.
#
# Uruchomienie (Git Bash / Linux, z działającym Dockerem; kilka minut):
#   scripts/tests/maintenance_pg18_rehearsal.sh            # stos sprzątany na końcu
#   scripts/tests/maintenance_pg18_rehearsal.sh --keep     # stos zostaje (ręczne sprawdzenia)
#
# Stos jest osobny: własna nazwa projektu compose, własne podsieci i porty proxy tylko na 127.0.0.1
# (REHEARSAL_PROJECT, REHEARSAL_HTTPS_PORT, REHEARSAL_SUBNET_EDGE/INTERNAL) – współdzielony stos
# deweloperski zostaje nietknięty. Obraz aplikacji: REHEARSAL_WEB_IMAGE (brak = budowany z backend/).
# Kod (docker-compose.yml, deploy/, scripts/) jest kopiowany do katalogu tymczasowego, bo skrypt
# przejścia pracuje na `.env` i wolumenach projektu – a tego nie wolno robić w katalogu repozytorium.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

PROJECT="${REHEARSAL_PROJECT:-olimpmaint}"
HTTP_PORT="${REHEARSAL_HTTP_PORT:-18080}"
HTTPS_PORT="${REHEARSAL_HTTPS_PORT:-18443}"
SUBNET_EDGE="${REHEARSAL_SUBNET_EDGE:-172.30.81.0/24}"
SUBNET_INTERNAL="${REHEARSAL_SUBNET_INTERNAL:-172.30.82.0/24}"
WEB_IMAGE="${REHEARSAL_WEB_IMAGE:-olimpiada/web:maint-rehearsal}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/maint-pg18-rehearsal.XXXXXX")"
APP="$WORK/app"
BASE_URL="https://localhost:${HTTPS_PORT}"

export COMPOSE_PROJECT_NAME="$PROJECT"
export COMPOSE_PATH_SEPARATOR=","
export COMPOSE_FILE="docker-compose.yml,docker-compose.rehearsal.yml"

failures=0
check() { if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi; }
say() { printf '\n--- %s\n' "$*"; }
rnd() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }
dc() { (cd "$APP" && docker compose "$@"); }
# Ścieżki kontenera w argumentach: bez zamiany na ścieżki Windows (Git Bash).
dcx() { (cd "$APP" && MSYS_NO_PATHCONV=1 docker compose "$@"); }
sql() { dcx exec -T db psql -X -q -U olimpiada -d olimpiada -v ON_ERROR_STOP=1 -Atc "$1" </dev/null | tr -d '\r'; }

cleanup() {
  [ -n "${WRITER_PID:-}" ] && kill "$WRITER_PID" 2>/dev/null
  [ -n "${PROBE_PID:-}" ] && kill "$PROBE_PID" 2>/dev/null
  if [ "$KEEP" = "1" ]; then
    printf '\nStos zostaje (--keep): cd %s && COMPOSE_PROJECT_NAME=%s COMPOSE_PATH_SEPARATOR=, COMPOSE_FILE=%s docker compose ps\n' "$APP" "$PROJECT" "$COMPOSE_FILE"
    printf 'Sprzątanie: (w tym katalogu) docker compose down -v\n'
  else
    dc down -v --remove-orphans >/dev/null 2>&1
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

say "Przygotowanie projektu $PROJECT w $APP"
mkdir -p "$APP"
cp -r "$ROOT/docker-compose.yml" "$ROOT/deploy" "$ROOT/scripts" "$APP/"
cat > "$APP/docker-compose.rehearsal.yml" <<EOF
# Nakładka próby: porty proxy tylko lokalnie i bez 9000, własne podsieci (nie kolidują z devem).
services:
  proxy:
    ports: !override
      - "127.0.0.1:${HTTP_PORT}:80"
      - "127.0.0.1:${HTTPS_PORT}:443"
networks:
  edge:
    ipam:
      config: !override
        - subnet: ${SUBNET_EDGE}
  internal:
    ipam:
      config: !override
        - subnet: ${SUBNET_INTERNAL}
EOF
TOKEN="$(rnd 40)"
cat > "$APP/.env" <<EOF
APP_VERSION=rehearsal
WEB_IMAGE=$WEB_IMAGE
SITE_DOMAIN=localhost
ACME_EMAIL=ops@example.org
S3_PUBLIC_ADDRESS=s3.localhost
MAX_UPLOAD_MB=25
DJANGO_SECRET_KEY=$(rnd 64)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=localhost,web,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=$BASE_URL
WEB_WORKERS=2
WEB_THREADS=2
CELERY_CONCURRENCY=1
POSTGRES_DB=olimpiada
POSTGRES_USER=olimpiada
POSTGRES_PASSWORD=$(rnd 32)
MINIO_ROOT_USER=minio-root
MINIO_ROOT_PASSWORD=$(rnd 32)
S3_PRESIGNED_TTL_SECONDS=600
S3_PUBLIC_ENDPOINT_URL=https://s3.localhost
S3_PUBLIC_ACCESS_KEY=wagtail-media
S3_PUBLIC_SECRET_KEY=$(rnd 32)
S3_PRIVATE_ACCESS_KEY=app-private
S3_PRIVATE_SECRET_KEY=$(rnd 32)
TRUSTED_PROXY_IPS=${SUBNET_EDGE},${SUBNET_INTERNAL}
EMAIL_URL=smtp://mail:587
DEFAULT_FROM_EMAIL=noreply@localhost
MAINTENANCE_BYPASS_TOKEN=$TOKEN

# PostgreSQL: przypięcie do 16 do czasu przejścia na 18 (scripts/upgrade_postgres18.sh, docs/OPERACJE.md § 19).
# Usuwa je sam skrypt po udanym przejściu; wpisuje je z powrotem --rollback.
POSTGRES_IMAGE=postgres:16-alpine
POSTGRES_VOLUME=pg_data:/var/lib/postgresql/data
EOF

if ! docker image inspect "$WEB_IMAGE" >/dev/null 2>&1; then
  say "Budowanie obrazu $WEB_IMAGE z backend/"
  docker build -q --target runtime -t "$WEB_IMAGE" "$ROOT/backend" || { echo "build nie wyszedł"; exit 1; }
fi

say "Start stosu na PostgreSQL 16 (db redis minio minio-init web worker beat proxy)"
(cd "$APP" && bash scripts/maintenance.sh sync) >/dev/null
# Po kolei: na pustej bazie entrypointy web, worker i beat migrowałyby równocześnie (wyścig przy
# pierwszym `migrate`), a `up` całości kończy się błędem, gdy web chwilę dłużej nie jest healthy.
healthy() { dc ps --format '{{.Service}}={{.Health}}' 2>/dev/null | grep -qx "$1=healthy"; }
wait_healthy() {  # wait_healthy <usługa> <sekundy>
  local i
  for i in $(seq 1 $(( $2 / 3 ))); do healthy "$1" && return 0; sleep 3; done
  return 1
}
dc up -d db redis minio minio-init >/dev/null 2>&1 || { dc ps; echo "up (dane) nie wyszedł"; exit 1; }
# Baza MUSI być healthy (i przyjmować połączenia po TCP), zanim wystartuje web – entrypoint web
# ma ograniczoną liczbę prób, a na obciążonym Docker Desktop pierwszy start bazy bywa wolny.
wait_healthy db 120 || { dc ps; echo "db nie jest healthy"; exit 1; }
for _ in $(seq 1 40); do
  sql 'SELECT 1' >/dev/null 2>&1 && break
  sleep 3
done
# web: do dwóch podejść – drugie z odtworzonym kontenerem (świeże połączenia i DNS sieci compose).
web_ok=0
for attempt in 1 2; do
  if [ "$attempt" = 1 ]; then
    dc up -d --no-deps web >/dev/null 2>&1
  else
    echo "web nie wstał w 1. podejściu – odtwarzam kontener (ostatnie linie logu niżej)"
    dc logs --tail 5 web 2>&1 | sed 's/^/  | /'
    dc up -d --no-deps --force-recreate web >/dev/null 2>&1
  fi
  wait_healthy web 240 && { web_ok=1; break; }
done
[ "$web_ok" = 1 ] || { dc ps; dc logs --tail 30 web; echo "web nie wstał w dwóch podejściach"; exit 1; }
dc up -d worker beat proxy >/dev/null 2>&1 || { dc ps; dc logs --tail 30 web; echo "up worker/beat/proxy nie wyszedł"; exit 1; }
for _ in $(seq 1 90); do
  dc ps --format '{{.Service}}={{.Health}}' | grep -qx 'web=healthy' \
    && dc ps --format '{{.Service}}={{.Health}}' | grep -qx 'proxy=healthy' && break
  sleep 4
done
dc ps --format '{{.Service}}={{.Health}}' | grep -qx 'web=healthy'
check "stos wstał (web healthy)" $?
[ "$(sql 'SHOW server_version_num' | cut -c1-2)" = "16" ]
check "baza startowa to PostgreSQL 16" $?

# Certyfikat Caddy'ego dla localhost jest z jego lokalnego CA – curl (także w skrypcie przejścia)
# ufa mu przez CURL_CA_BUNDLE, bez wyłączania weryfikacji.
for _ in $(seq 1 30); do
  dcx exec -T proxy cat /data/caddy/pki/authorities/local/root.crt > "$WORK/caddy-root.crt" 2>/dev/null </dev/null && [ -s "$WORK/caddy-root.crt" ] && break
  sleep 2
done
export CURL_CA_BUNDLE="$WORK/caddy-root.crt"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    # curl z Git Basha to Schannel: nie buduje łańcucha do CA spoza magazynu Windows
    # (CERT_TRUST_IS_PARTIAL_CHAIN), a dopisywanie CA próby do magazynu systemu byłoby zmianą
    # ustawień maszyny. Tylko tu i tylko w próbie: nakładka na curl bez weryfikacji certyfikatu
    # (ruch idzie wyłącznie do 127.0.0.1). Skrypty produkcyjne nie mają żadnego -k.
    mkdir -p "$WORK/bin"
    printf '#!/usr/bin/env bash\nexec %q --insecure "$@"\n' "$(command -v curl)" > "$WORK/bin/curl"
    chmod +x "$WORK/bin/curl"
    export PATH="$WORK/bin:$PATH"
    ;;
esac
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/healthz/")"
[ "$code" = "200" ]
check "przed przerwą: $BASE_URL/healthz/ -> 200 (jest $code)" $?

say "Tabela znaczników i pisarz (zapisy co ~0,2 s aż do pojawienia się flagi prac technicznych)"
sql "CREATE TABLE rehearsal_marker (id bigserial PRIMARY KEY, host_ts numeric NOT NULL, db_ts timestamptz NOT NULL DEFAULT clock_timestamp())" >/dev/null
WRITER_LOG="$WORK/writer.log"
: > "$WRITER_LOG"
(
  while [ ! -f "$APP/maintenance/on" ]; do
    before="$(date +%s.%N)"
    id="$(sql "INSERT INTO rehearsal_marker (host_ts) VALUES ($before) RETURNING id" 2>/dev/null | head -1)"
    ack="$(date +%s.%N)"
    # Tylko zapis potwierdzony przez bazę (zwrócone id) jest „zapisem, który musi przetrwać”.
    [ -n "$id" ] && echo "$id $before $ack" >> "$WRITER_LOG"
    sleep 0.2
  done
) &
WRITER_PID=$!
PROBE_LOG="$WORK/probe.log"
: > "$PROBE_LOG"
(
  # Co ~0,5 s: kod i to, czy odpowiedź jest stroną prac technicznych. Dwa adresy – JSON i HTML.
  while :; do
    ts="$(date +%s.%N)"
    j="$(curl -s -w ' %{http_code}' --max-time 5 "$BASE_URL/healthz/" 2>/dev/null | tr -d '\n')"
    h="$(curl -s -o "$WORK/probe.html" -w '%{http_code}' --max-time 5 "$BASE_URL/" 2>/dev/null)"
    jm=0; hm=0
    case "$j" in *'"maintenance"'*) jm=1 ;; esac
    grep -q 'Prace techniczne' "$WORK/probe.html" 2>/dev/null && hm=1
    echo "$ts json=${j##* } json_maint=$jm html=$h html_maint=$hm" >> "$PROBE_LOG"
    sleep 0.5
  done
) &
PROBE_PID=$!
sleep 6   # kilka zapisów i próbek przed startem przejścia

say "Przejście: scripts/upgrade_postgres18.sh (BACKUP_DIR w katalogu próby)"
(
  cd "$APP" && BACKUP_DIR="$WORK/backups" MAINTENANCE_CHECK_URL="$BASE_URL" \
    bash scripts/upgrade_postgres18.sh --allow-stale-backup
) > "$WORK/upgrade.out" 2>&1
rc=$?
sed 's/^/  | /' "$WORK/upgrade.out" | grep -E '^\s*\| (==>|    (sha256|rozmiar|zrzut do odtworzenia|brak klientów|SHA-256|przerwa|stan 16)|UWAGA|BŁĄD|!!!)' || true
check "upgrade_postgres18.sh zakończony kodem 0 (jest $rc)" "$rc"
wait "$WRITER_PID" 2>/dev/null; WRITER_PID=""
sleep 3
kill "$PROBE_PID" 2>/dev/null; wait "$PROBE_PID" 2>/dev/null; PROBE_PID=""

RUN_DIR="$(ls -1dt "$WORK"/backups/pg18-upgrade-* 2>/dev/null | head -1)"
TL="$RUN_DIR/timeline.txt"
[ -f "$TL" ]
check "timeline.txt przebiegu istnieje ($TL)" $?
t() { awk -v e="$1" '$1 == e { print $2; exit }' "$TL" 2>/dev/null; }
after() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a != "" && b != "" && a + 0 > b + 0) }'; }

say "Kolejność etapów (timeline.txt)"
sed 's/^/  | /' "$TL"
prev=""
for ev in maintenance_on app_stopped no_clients dump16_start dump18_start dump18_end restore_start restore_end compare_ok switched app_healthy checks_ok maintenance_off; do
  cur="$(t "$ev")"
  if [ -n "$prev" ]; then
    after "$cur" "$(t "$prev")"
    check "$ev po $prev" $?
  fi
  prev="$ev"
done
after "$(t dump18_start)" "$(t maintenance_on)"
check "zrzut odtwarzany (klient 18) zaczęty PO włączeniu strony" $?
after "$(t dump16_start)" "$(t maintenance_on)"
check "zrzut drogi powrotu (klient 16) zaczęty PO włączeniu strony" $?

say "Zrzut odtworzony = zrzut zapisany (SHA-256)"
DUMP18="$(ls -1 "$RUN_DIR"/db-for-pg18-*.dump 2>/dev/null | head -1)"
DUMP18_NAME="$(basename "$DUMP18")"
# sha256sum pisze „<suma> *<plik>” w trybie binarnym (Git Bash) albo „<suma>  <plik>” – gwiazdka zdejmowana.
recorded="$(awk -v f="$DUMP18_NAME" '{ n = $2; sub(/^\*/, "", n) } n == f { print $1 }' "$RUN_DIR/dumps.sha256")"
actual="$(sha256sum "$DUMP18" | cut -d' ' -f1)"
[ -n "$recorded" ] && [ "$recorded" = "$actual" ]
rc=$?   # osobno: podstawienie $(…) w opisie nadpisałoby kod wyniku
check "SHA-256 $DUMP18_NAME: zapisany ${recorded:0:16}… = plik ${actual:0:16}…" "$rc"
grep -q 'SHA-256 zgodne' "$WORK/upgrade.out"
check "skrypt sprawdził SHA-256 tuż przed pg_restore" $?

say "Zapisy sprzed włączenia strony są w PostgreSQL 18"
[ "$(sql 'SHOW server_version_num' | cut -c1-2)" = "18" ]
check "baza po przejściu to PostgreSQL 18" $?
sql "SELECT id FROM rehearsal_marker ORDER BY id" > "$WORK/pg18_ids.txt"
awk '{print $1}' "$WRITER_LOG" | sort -n > "$WORK/acked_ids.txt"
acked="$(wc -l < "$WORK/acked_ids.txt")"
missing="$(comm -23 <(sort "$WORK/acked_ids.txt") <(sort "$WORK/pg18_ids.txt") | wc -l)"
[ "$acked" -gt 0 ] && [ "$missing" -eq 0 ]
check "każdy potwierdzony zapis pisarza jest w 18 (zapisów: $acked, brakuje: $missing)" $?
MON="$(t maintenance_on)"
last_before="$(awk -v m="$MON" '$3 + 0 < m + 0 { id = $1; ack = $3 } END { print id, ack }' "$WRITER_LOG")"
last_id="${last_before%% *}"; last_ack="${last_before##* }"
gap="$(awk -v m="$MON" -v a="$last_ack" 'BEGIN { printf "%.2f", m - a }')"
[ -n "$last_id" ] && grep -qx "$last_id" "$WORK/pg18_ids.txt" && awk -v g="$gap" 'BEGIN { exit !(g >= 0 && g < 5) }'
check "ostatni zapis przed włączeniem strony (id $last_id, $gap s przed nią) jest w 18" $?
after "$(t dump18_start)" "$last_ack"
check "zrzut odtwarzany zaczął się po ostatnim zapisie sprzed włączenia strony" $?

say "Co widział użytkownik (próbki proxy co ~0,5 s)"
awk '{ split($2, j, "="); split($4, h, "="); c[j[2]]++; c[h[2]]++ } END { for (k in c) printf "  kod %s: %d\n", k, c[k] }' "$PROBE_LOG"
! grep -qE 'json=(502|504) |html=(502|504) ' "$PROBE_LOG"
check "ani jednej odpowiedzi 502/504 w trakcie próby" $?
grep -q 'json=503 json_maint=1 html=503 html_maint=1' "$PROBE_LOG"
check "w trakcie przerwy: 503 + JSON maintenance i 503 + strona HTML" $?
MOFF="$(t maintenance_off)"
awk -v a="$MON" -v b="$MOFF" '$3 == "json_maint=1" || $5 == "html_maint=1" { if ($1 + 0 < a - 1 || $1 + 0 > b + 2) bad++ } END { exit bad > 0 }' "$PROBE_LOG"
check "strona prac technicznych tylko między włączeniem a wyłączeniem" $?
tail -n 1 "$PROBE_LOG" | grep -q 'json=200 json_maint=0 html=200 html_maint=0'
check "po przejściu: /healthz/ i / -> 200 bez strony prac technicznych" $?
[ ! -f "$APP/maintenance/on" ]
check "strona prac technicznych wyłączona na końcu" $?

say "Wycofanie: scripts/upgrade_postgres18.sh --rollback --yes (ta sama kolejność ze stroną)"
PROBE_LOG="$WORK/probe-rollback.log"
: > "$PROBE_LOG"
(
  while :; do
    ts="$(date +%s.%N)"
    j="$(curl -s -w ' %{http_code}' --max-time 5 "$BASE_URL/healthz/" 2>/dev/null | tr -d '\n')"
    jm=0; case "$j" in *'"maintenance"'*) jm=1 ;; esac
    echo "$ts json=${j##* } json_maint=$jm" >> "$PROBE_LOG"
    sleep 0.5
  done
) &
PROBE_PID=$!
(
  cd "$APP" && BACKUP_DIR="$WORK/backups" MAINTENANCE_CHECK_URL="$BASE_URL" \
    bash scripts/upgrade_postgres18.sh --rollback --yes
) > "$WORK/rollback.out" 2>&1
rc=$?
sed 's/^/  | /' "$WORK/rollback.out" | grep -E '^  \| (==>|    sha256|UWAGA|BŁĄD|!!!)' || true
kill "$PROBE_PID" 2>/dev/null; wait "$PROBE_PID" 2>/dev/null; PROBE_PID=""
check "--rollback --yes zakończony kodem 0 (jest $rc)" "$rc"
RB_DIR="$(ls -1dt "$WORK"/backups/pg18-rollback-* 2>/dev/null | head -1)"
TL="$RB_DIR/timeline.txt"
prev=""
for ev in maintenance_on app_stopped dump_pg18_rollback_start dump_pg18_rollback_end app_healthy checks_ok maintenance_off; do
  cur="$(t "$ev")"
  if [ -n "$prev" ]; then
    after "$cur" "$(t "$prev")"
    check "wycofanie: $ev po $prev" $?
  fi
  prev="$ev"
done
[ "$(sql 'SHOW server_version_num' | cut -c1-2)" = "16" ] && grep -q '^POSTGRES_VOLUME=pg_data' "$APP/.env"
check "po wycofaniu: PostgreSQL 16 i przypięcie w .env" $?
missing="$(comm -23 <(sort "$WORK/acked_ids.txt") <(sql "SELECT id FROM rehearsal_marker ORDER BY id" | sort) | wc -l)"
[ "$missing" -eq 0 ]
check "po wycofaniu: wszystkie zapisy sprzed przejścia są w 16 (brakuje: $missing)" $?
! grep -qE 'json=(502|504) ' "$PROBE_LOG" && grep -q 'json=503 json_maint=1' "$PROBE_LOG"
check "wycofanie: 503 maintenance w przerwie, ani jednego 502/504" $?
tail -n 1 "$PROBE_LOG" | grep -q 'json=200 json_maint=0' && [ ! -f "$APP/maintenance/on" ]
check "po wycofaniu: /healthz/ 200, strona wyłączona" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło. Wyjście skryptu: %s\n' "$failures" "$WORK/upgrade.out"
  KEEP=1
  exit 1
fi
printf '\nPróba kolejności przejścia ze stroną prac technicznych przeszła.\n'
