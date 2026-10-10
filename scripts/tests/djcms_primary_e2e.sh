#!/usr/bin/env bash
# E2E przełączenia serwisu publicznego Wagtail ⇄ django CMS na PRAWDZIWYM stosie (docs/tasks/DJ-02.md
# § 11, podkrok DJ-02j) – obowiązkowy przed DoD DJ-02, lokalnie (Docker Desktop / Linux), bez sieci.
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu):
#   scripts/tests/djcms_primary_e2e.sh              # pełny przebieg od czystych wolumenów, na końcu down -v
#   scripts/tests/djcms_primary_e2e.sh --keep       # jw., stos zostaje (debug: katalog roboczy niżej)
#   scripts/tests/djcms_primary_e2e.sh --reuse      # stos i dane z poprzedniego --keep (bez budowania i seedów)
#   scripts/tests/djcms_primary_e2e.sh --no-bench   # bez pomiaru (scripts/djcms_bench.py)
#   scripts/tests/djcms_primary_e2e.sh --up-only    # tylko stos i świat (bez scenariusza), stos zostaje
#   scripts/tests/djcms_primary_e2e.sh --down       # tylko sprzątnięcie stosu E2E
# Zmienne: DJCMS_E2E_DIR (katalog roboczy, domyślnie runs/djcms-e2e), DJCMS_E2E_BENCH_REQUESTS (300),
# DJCMS_E2E_BENCH_CONCURRENCY (8).
#
# Co stawia – OSOBNY projekt compose `olimpiada-e2e-djcms` (środowisko dev `olimpiadaclade` zostaje
# nietknięte: własne wolumeny, własne podsieci 172.31.x, obrazy `…:e2e-djcms`, tylko 127.0.0.1:443
# na hoście). Katalog roboczy udaje katalog instalacji z serwera (/opt/olimpiada): kopie scripts/,
# deploy/, backend/djcms_contract/, plików compose i własny .env (SITE_DOMAIN=olimpiada.test,
# PLATFORM_SUBDOMAINS=1, DJCMS_ENABLED=1, CADDY_CONFIG_DIR=./caddy – docs/OPERACJE.md § 23). Dzięki temu
# scripts/djcms_switch.sh i scripts/djcms_cutover.sh idą BEZ ZMIAN – jak na serwerze. Jedyna zmiana
# pliku Caddy'ego: `local_certs` + `skip_install_trust` w opcjach globalnych (jak djcms_routing_test.sh).
# Usługi: db, redis, minio(+init), web (gunicorn, DEBUG=0, bufor stron), djcms (gunicorn 3×4), proxy
# (Caddy z wygenerowanym plikiem). Bez worker/beat/clamav/mail – przebieg nie wysyła poczty i nie
# przyjmuje plików. Klient testów (Playwright + requests) to kontener w sieci `edge` z hostami
# *.olimpiada.test rozwiązywanymi na adres `proxy` (bez pliku hosts).
#
# Świat: konkurs domyślny (seed_demo, seed_cms, seed_regulamin, seed_legacy_content, seed_partners,
# seed_edition_kwantowa) pod olimpiada.test, `fizyczna` w subdomenie platformy (fizyczna.olimpiada.test),
# `e2e-druga` pod prefiksem ścieżki (olimpiada.test/druga/) – create_competition jak scripts/e2e.sh;
# djcms: setup_djcms_groups + sync_competitions --import-missing (jak scripts/deploy.sh).
#
# Scenariusz (każdy krok = wiersze ok/FAIL; kod wyjścia ≠ 0 przy którejkolwiek porażce):
#   A. PRIMARY=0 – e2e/check_djcms_primary.py --phase preview (trzy konkursy: Wagtail bez nagłówka,
#      podgląd przez /djcms/preview/ → djcms `preview` + noindex, dj. → 302, logowanie, panele, /cms/,
#      /api/, /static/, /documents/, /internal/* 404) + djcms_switch.sh check + djcms_cutover.sh
#      --check i --dry-run;
#   B. djcms_cutover.sh --yes (kopia + backup_verify, cms_freeze on, sync, import, verify_cutover,
#      djcms_switch.sh on) POD RUCHEM (pętla GET / i /login/ trzech konkursów) – zero 5xx i zerwań;
#      gdy verify_cutover zatrzyma przełączenie na konkursach X – FAIL i ponowienie z `--skip X`
#      (droga z runbooka), żeby reszta scenariusza sprawdziła tryb primary;
#   C. PRIMARY=1 – --phase primary (djcms `primary`, bez noindex, canonical, robots/sitemap, ciasteczko
#      djcms_view=wagtail → Wagtail, logowanie i panele z web, /cms/ z banerem zamrożenia) + nowy konkurs
#      `ekologiczna` (pierwsze wejście = drzewo startowe djcms, wyłączenie → 404);
#   D. pomiar p50/p95 (scripts/djcms_bench.py): djcms vs Wagtail z buforem stron i bez;
#   E. djcms_cutover.sh --rollback --unfreeze POD RUCHEM → --phase wagtail (Wagtail, bez banera).
# Raport: $DJCMS_E2E_DIR/artifacts/ (wyjścia kroków, bench.json, zrzuty ekranu Playwrighta).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${DJCMS_E2E_DIR:-$ROOT/runs/djcms-e2e}"
STAGE="$WORK/install"
ART="$WORK/artifacts"
PROJECT=olimpiada-e2e-djcms
DOMAIN=olimpiada.test
RUNNER="$PROJECT-runner"
PW_IMAGE="$(sed -n 's/^    image: \(mcr\.microsoft\.com\/playwright\/python:.*\)$/\1/p' "$ROOT/docker-compose.dev.yml" | head -1)"
BENCH_REQUESTS="${DJCMS_E2E_BENCH_REQUESTS:-300}"
BENCH_CONCURRENCY="${DJCMS_E2E_BENCH_CONCURRENCY:-8}"
export MSYS_NO_PATHCONV=1

REUSE=0; KEEP=0; BENCH=1; DOWN_ONLY=0; UP_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --reuse) REUSE=1; KEEP=1 ;;
    --keep) KEEP=1 ;;
    --no-bench) BENCH=0 ;;
    --down) DOWN_ONLY=1 ;;
    --up-only) UP_ONLY=1; KEEP=1 ;;
    -h|--help) sed -n '2,/^set -uo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *) echo "djcms_primary_e2e: nieznany argument: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  printf 'NIE WYKONANO: brak działającego Dockera – ten test niczego nie sprawdził.\n' >&2
  exit 2
fi

# Ścieżka hosta dla Dockera (Git Bash: C:/…; Linux: bez zmian).
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}
log() { printf '\n==> %s\n' "$*"; }
die() { printf '\ndjcms_primary_e2e: BŁĄD: %s\n' "$*" >&2; finish 1; }

dc() { (cd "$STAGE" && docker compose "$@"); }
dcx() { (cd "$STAGE" && docker compose exec -T "$@" </dev/null); }
in_stage() { (cd "$STAGE" && "$@"); }

down_all() {
  docker rm -f "$RUNNER" >/dev/null 2>&1 || true
  if [ -f "$STAGE/.env" ]; then
    dc down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}

finish() {
  local rc="${1:-0}"
  if [ "$KEEP" = 1 ]; then
    printf '\nStos E2E zostaje (--keep): cd "%s" && docker compose ps   ·   sprzątnięcie: %s --down\n' "$STAGE" "$0"
  else
    log "Sprzątanie stosu E2E (docker compose down -v)"
    down_all
  fi
  if [ "$failures" -ne 0 ] || [ "$rc" -ne 0 ]; then
    printf '\n%d kontrol(i) nie przeszło%s. Artefakty: %s\n' "$failures" "$([ "$rc" -ne 0 ] && echo ', przebieg przerwany')" "$ART"
    exit 1
  fi
  printf '\nE2E przełączenia djcms: wszystkie kontrole przeszły. Artefakty: %s\n' "$ART"
  exit 0
}

if [ "$DOWN_ONLY" = 1 ]; then
  down_all
  echo "Stos E2E sprzątnięty."
  exit 0
fi

# --- 0. Katalog instalacji ----------------------------------------------------------------------
rand() { openssl rand -hex "$1"; }  # [0-9a-f] – bezpieczne w DATABASE_URL i .env

make_stage() {
  mkdir -p "$STAGE" "$ART" "$STAGE/backend" "$STAGE/maintenance" "$STAGE/backups" "$STAGE/logs"
  # Kod skryptów i kontrakt tras – zawsze świeże (jak krok 2/8 wdrożenia).
  rm -rf "$STAGE/scripts" "$STAGE/backend/djcms_contract"
  cp -R "$ROOT/scripts" "$STAGE/scripts"
  cp -R "$ROOT/backend/djcms_contract" "$STAGE/backend/djcms_contract"
  cp "$ROOT/docker-compose.yml" "$ROOT/docker-compose.djcms.yml" "$ROOT/docker-compose.e2e-djcms.yml" "$STAGE/"
  # deploy/ zawsze od nowa (jak krok 2/8): proxy montuje katalog stanu caddy/ (CADDY_CONFIG_DIR),
  # nie plik z deploy/, więc podmiana deploy/ pod działającym proxy (--reuse) niczego nie rozjeżdża.
  rm -rf "$STAGE/deploy"
  cp -R "$ROOT/deploy" "$STAGE/deploy"
  # Jedyna zmiana konfiguracji proxy na potrzeby testu: certyfikaty z lokalnego CA Caddy'ego (bez ACME).
  awk '{ print } $0 == "    email {$ACME_EMAIL}" { print "    local_certs"; print "    skip_install_trust" }' \
    "$ROOT/deploy/Caddyfile" >"$STAGE/deploy/Caddyfile"
  grep -q '^    local_certs$' "$STAGE/deploy/Caddyfile" || die "nie znalazłem kotwicy „email {\$ACME_EMAIL}” w deploy/Caddyfile"

  if [ "$REUSE" = 1 ] && [ -f "$STAGE/.env" ]; then return; fi
  local extra_ca
  extra_ca="$(sed -n 's/^EXTRA_CA_FILE=//p' "$ROOT/.env" 2>/dev/null | tail -n 1 | tr -d '\r\042\047')"
  umask 077
  cat >"$STAGE/.env" <<EOF
# Wygenerowane przez scripts/tests/djcms_primary_e2e.sh – środowisko E2E, sekrety jednorazowe.
COMPOSE_PROJECT_NAME=$PROJECT
COMPOSE_FILE=docker-compose.yml:docker-compose.djcms.yml:docker-compose.e2e-djcms.yml
COMPOSE_PATH_SEPARATOR=:
COMPOSE_PROFILES=djcms
E2E_REPO_DIR="$(host_path "$ROOT")"
${extra_ca:+EXTRA_CA_FILE=$extra_ca}
APP_VERSION=e2e-djcms
WEB_IMAGE=olimpiada/web:e2e-djcms
DJCMS_IMAGE=olimpiada/djcms:e2e-djcms
SITE_DOMAIN=$DOMAIN
PLATFORM_SUBDOMAINS=1
EXTRA_DOMAINS=
CADDY_CONFIG_DIR=./caddy
ACME_EMAIL=e2e@example.org
MAX_UPLOAD_MB=25
DJANGO_SECRET_KEY=$(rand 40)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,web,$DOMAIN
PAGE_CACHE_ENABLED=1
POSTGRES_DB=olimpiada
POSTGRES_USER=olimpiada
POSTGRES_PASSWORD=$(rand 16)
# Redis z hasłem (jak na produkcji po 1.10.2026) – E2E przechodzi drogę REDIS_URL z `:hasło@`.
REDIS_PASSWORD=$(rand 16)
MINIO_ROOT_USER=e2eroot$(rand 4)
MINIO_ROOT_PASSWORD=$(rand 16)
S3_PUBLIC_ACCESS_KEY=e2epub$(rand 4)
S3_PUBLIC_SECRET_KEY=$(rand 16)
S3_PRIVATE_ACCESS_KEY=e2epriv$(rand 4)
S3_PRIVATE_SECRET_KEY=$(rand 16)
S3_PUBLIC_ENDPOINT_URL=https://s3.$DOMAIN
EMAIL_URL=consolemail://
DEFAULT_FROM_EMAIL=olimpiada@$DOMAIN
TRUSTED_PROXY_IPS=172.31.1.250/32,172.31.2.250/32
PROXY_EDGE_IP=172.31.1.250
DJCMS_PROXY_IP=172.31.2.250
MAIL_SUBNET=172.31.5.0/24
DJCMS_SECRET_KEY=$(rand 32)
DJCMS_DB_PASSWORD=$(rand 16)
DJCMS_INTERNAL_TOKEN=$(rand 24)
DJCMS_SSO_KEY=$(rand 32)
DJCMS_E2E_ADMIN_PASSWORD=$(rand 16)
BACKUP_PASSPHRASE=$(rand 16)
BACKUP_REMOTE_TYPE=none
DJCMS_ENABLED=1
DJCMS_PRIMARY=0
EOF
  umask 022
}

wait_healthy() {  # wait_healthy <usługa> <próby> <przerwa>
  local service="$1" attempts="$2" pause="$3" state=unknown id
  for _ in $(seq 1 "$attempts"); do
    id="$(dc ps -q "$service" 2>/dev/null || true)"
    if [ -n "$id" ]; then
      state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo unknown)"
      [ "$state" = healthy ] && return 0
    fi
    sleep "$pause"
  done
  echo "    $service: $state – nie osiągnął stanu healthy" >&2
  return 1
}

wait_proxy() {  # Caddy przyjął konfigurację (API administracyjne odpowiada)
  for _ in $(seq 1 60); do
    dcx proxy wget -q -O /dev/null http://127.0.0.1:2019/config/ >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

# Kontener klienta testów: Playwright + requests w sieci `edge` projektu E2E. Zależności instalowane
# raz na życie kontenera (pip ~20 s), wyjścia kroków w e2e/artifacts/djcms/ (montaż e2e/).
start_runner() {
  local ca_args=() ca
  # Dodatkowe CA (antywirus przechwytujący HTTPS – ten sam EXTRA_CA_FILE co przy budowaniu djcms):
  # pip w kontenerze klienta dostaje pakiet CA systemu + ten plik.
  ca="$(sed -n 's/^EXTRA_CA_FILE=//p' "$STAGE/.env" | tail -n 1 | tr -d '\r\042\047')"
  [ -n "$ca" ] && [ -s "$ca" ] && ca_args=(-v "$(host_path "$ca"):/extra-ca.pem:ro")
  docker rm -f "$RUNNER" >/dev/null 2>&1 || true
  docker run -d --name "$RUNNER" --network "${PROJECT}_edge" "${ca_args[@]}" \
    -v "$(host_path "$ROOT")/e2e:/e2e" -v "$(host_path "$ROOT")/scripts:/scripts:ro" -w /e2e \
    -e E2E_SITE_DOMAIN="$DOMAIN" -e E2E_PROXY_HOST=proxy -e PYTHONUNBUFFERED=1 \
    "$PW_IMAGE" sleep infinity >/dev/null || return 1
  docker exec "$RUNNER" sh -c '
    if [ -s /extra-ca.pem ]; then
      cat /etc/ssl/certs/ca-certificates.crt /extra-ca.pem >/tmp/ca.pem
      export PIP_CERT=/tmp/ca.pem
    fi
    pip install -q -r requirements.txt' >"$ART/runner-pip.out" 2>&1 || { tail -n 5 "$ART/runner-pip.out"; return 1; }
}
run_check() {  # run_check <faza> [argumenty…] – e2e/check_djcms_primary.py w kontenerze klienta
  docker exec "$RUNNER" python check_djcms_primary.py --phase "$@"
}

# Ruch w tle podczas przełączenia: pętla GET / i /login/ trzech konkursów do chwili pliku-stopu.
LOAD_STOP="$ROOT/e2e/artifacts/djcms/load.stop"
start_load() {  # start_load <nazwa>
  mkdir -p "$ROOT/e2e/artifacts/djcms"
  rm -f "$LOAD_STOP"
  docker exec "$RUNNER" python check_djcms_primary.py --phase load --stop-file artifacts/djcms/load.stop \
    >"$ART/load-$1.out" 2>&1 &
  LOAD_PID=$!
  sleep 3
}
stop_load() {  # stop_load <nazwa> – kod pętli (0 = zero 5xx i zerwań, co najmniej 200 żądań)
  touch "$LOAD_STOP"
  wait "$LOAD_PID"
  local rc=$?
  rm -f "$LOAD_STOP"
  tail -n 5 "$ART/load-$1.out" | sed 's/^/     /'
  return $rc
}

# --- 1. Stos ----------------------------------------------------------------------------------------
T0="$(date +%s)"
if [ "$REUSE" = 0 ]; then
  log "Sprzątanie poprzedniego stosu E2E (jeśli jest)"
  down_all
fi
make_stage
rm -rf "$ART"; mkdir -p "$ART" "$ROOT/e2e/artifacts/djcms"

if [ "$REUSE" = 0 ]; then
  log "Budowanie obrazów web i djcms z drzewa roboczego (tagi …:e2e-djcms)"
  dc build djcms >"$ART/build-djcms.out" 2>&1 || { tail -n 40 "$ART/build-djcms.out"; die "budowanie obrazu djcms nie powiodło się"; }
  if ! dc build web >"$ART/build-web.out" 2>&1; then
    # backend/Dockerfile nie ma sekretu extra_ca (djcms ma): za antywirusem przechwytującym HTTPS
    # `uv pip install` pada na UnknownIssuer. Wtedy obraz = zależności z obrazu bazowego dev
    # (DJCMS_E2E_WEB_BASE, domyślnie olimpiada/web:dev – ten, na którym stoi środowisko dev) + KOD
    # z drzewa roboczego i skompilowane tłumaczenia – te same kroki co etap `runtime` Dockerfile'a.
    base="${DJCMS_E2E_WEB_BASE:-olimpiada/web:dev}"
    printf '    docker compose build web nie przeszedł (%s) – obraz z zależności %s + kod z drzewa\n' \
      "$(grep -m1 -oE 'UnknownIssuer|failed to solve[^:]*' "$ART/build-web.out" || echo 'patrz build-web.out')" "$base"
    docker image inspect "$base" >/dev/null 2>&1 || die "brak obrazu $base (DJCMS_E2E_WEB_BASE) – zbuduj web raz z siecią"
    # Kontekst bez śmieci drzewa roboczego (.venv z Windowsa, staticfiles, bufory narzędzi).
    rm -rf "$WORK/web-context"; mkdir -p "$WORK/web-context"
    tar -C "$ROOT/backend" -cf - --exclude=./.venv --exclude=./staticfiles --exclude=./media \
      --exclude=./.pytest_cache --exclude=./.ruff_cache --exclude=./.mypy_cache --exclude=./.coverage \
      --exclude='./C:' --exclude=./nul --exclude=__pycache__ . | tar -C "$WORK/web-context" -xf - \
      || die "kopia backend/ do kontekstu budowania"
    cat >"$WORK/web-context/Dockerfile.e2e" <<EOF
FROM $base
USER root
RUN find /app -mindepth 1 -maxdepth 1 ! -name staticfiles -exec rm -rf {} +
COPY --chown=app:app . /app
RUN chmod +x /app/entrypoint.sh && mkdir -p /app/staticfiles && chown app:app /app/staticfiles \\
 && for po in /app/locale/*/LC_MESSAGES/django.po; do [ -e "\$po" ] || continue; msgfmt -o "\${po%.po}.mo" "\$po"; done
USER app
EOF
    docker build -t olimpiada/web:e2e-djcms -f "$(host_path "$WORK/web-context/Dockerfile.e2e")" \
      "$(host_path "$WORK/web-context")" >>"$ART/build-web.out" 2>&1 \
      || { tail -n 30 "$ART/build-web.out"; die "budowanie obrazu web (z $base) nie powiodło się"; }
  fi

  log "Baza, Redis, MinIO"
  dc up -d db redis minio >"$ART/up.out" 2>&1 || die "docker compose up db redis minio"
  wait_healthy db 60 2 || die "db nie wstała"
  wait_healthy minio 60 2 || die "minio nie wstało"
  dc up minio-init >>"$ART/up.out" 2>&1 || die "minio-init"
  in_stage bash scripts/djcms_db.sh </dev/null >"$ART/djcms_db.out" 2>&1 || { cat "$ART/djcms_db.out"; die "scripts/djcms_db.sh"; }

  log "web (migracje, collectstatic) i dane"
  dc up -d web >>"$ART/up.out" 2>&1 || die "docker compose up web"
  wait_healthy web 90 4 || { dc logs --tail 60 web; die "web nie wstał"; }
  # seed_demo przy DEBUG=0 wymaga --force (konta demo z jawnym hasłem – tu świadomie, stos jednorazowy).
  for cmd in "seed_demo --force" seed_cms seed_regulamin seed_legacy_content seed_partners seed_edition_kwantowa; do
    # shellcheck disable=SC2086 # komenda z argumentem
    dcx web python manage.py $cmd >"$ART/seed-${cmd%% *}.out" 2>&1 || { tail -n 30 "$ART/seed-${cmd%% *}.out"; die "manage.py $cmd"; }
  done
  dcx web python manage.py create_competition --slug fizyczna --name "Olimpiada Fizyczna" \
    --domain "fizyczna.$DOMAIN" --from-template przedmiotowa --coordinator-email koordynator@example.com \
    --skip-existing >"$ART/create-fizyczna.out" 2>&1 || { cat "$ART/create-fizyczna.out"; die "create_competition fizyczna"; }
  dcx web python manage.py create_competition --slug e2e-druga --name "Olimpiada Druga" \
    --domain e2e-druga.localhost --from-template przedmiotowa --path-prefix druga \
    --coordinator-email koordynator@example.com --skip-existing >"$ART/create-druga.out" 2>&1 \
    || { cat "$ART/create-druga.out"; die "create_competition e2e-druga"; }

  log "djcms (migracje, grupy, rejestr konkursów i import brakujących witryn – jak scripts/deploy.sh)"
  dc up -d djcms >>"$ART/up.out" 2>&1 || die "docker compose up djcms"
  wait_healthy djcms 60 3 || { dc logs --tail 60 djcms; die "djcms nie wstał"; }
  dcx djcms python manage.py setup_djcms_groups >"$ART/djcms-setup.out" 2>&1 || { cat "$ART/djcms-setup.out"; die "setup_djcms_groups"; }
  # Konto techniczne djcms (autor wersji importu) – jak deploy.sh z DJCMS_ADMIN_EMAIL/PASSWORD;
  # hasło jednorazowe z .env stosu E2E.
  dcx -e DJCMS_ADMIN_EMAIL="admin@$DOMAIN" -e DJCMS_ADMIN_PASSWORD="$(sed -n 's/^DJCMS_E2E_ADMIN_PASSWORD=//p' "$STAGE/.env")" \
    djcms python manage.py bootstrap_djcms_admin >>"$ART/djcms-setup.out" 2>&1 || { cat "$ART/djcms-setup.out"; die "bootstrap_djcms_admin"; }
  dcx djcms python manage.py sync_competitions --import-missing >"$ART/djcms-sync.out" 2>&1 \
    || { tail -n 30 "$ART/djcms-sync.out"; die "sync_competitions --import-missing"; }
  sed 's/^/     /' "$ART/djcms-sync.out" | tail -n 12

  log "proxy (Caddyfile z scripts/render_caddyfile.sh, DJCMS_PRIMARY=0)"
  in_stage bash scripts/render_caddyfile.sh >"$ART/render.out" 2>&1 || { cat "$ART/render.out"; die "render_caddyfile.sh"; }
  dc up -d proxy >>"$ART/up.out" 2>&1 || die "docker compose up proxy"
  wait_proxy || { dc logs --tail 40 proxy; die "proxy nie wstało"; }
fi
start_runner || die "kontener klienta testów ($PW_IMAGE) nie wstał"
printf '    stos gotowy w %s s\n' "$(( $(date +%s) - T0 ))"
[ "$UP_ONLY" = 0 ] || finish 0

# --- A. PRIMARY=0 -----------------------------------------------------------------------------------
log "A. DJCMS_PRIMARY=0 – Wagtail publicznie, djcms w podglądzie"
in_stage bash scripts/djcms_switch.sh status </dev/null >"$ART/switch-status-0.out" 2>&1
grep -q 'DJCMS_PRIMARY=0' "$ART/switch-status-0.out" && grep -q 'tryb w pliku: preview' "$ART/switch-status-0.out" \
  && grep -q 'proxy: widzi ten sam plik' "$ART/switch-status-0.out"
check "djcms_switch.sh status: PRIMARY=0, plik w trybie preview, proxy widzi ten sam plik" $?
in_stage bash scripts/djcms_switch.sh check </dev/null >"$ART/switch-check-0.out" 2>&1
rc=$?; check "djcms_switch.sh check (kontrola dymna przez proxy na 127.0.0.1:443, tryb preview)" $rc
[ $rc -eq 0 ] || sed 's/^/     /' "$ART/switch-check-0.out"
run_check preview 2>&1 | tee "$ART/check-preview.out"
check "check_djcms_primary.py --phase preview" "${PIPESTATUS[0]}"

in_stage env DJCMS_CUTOVER_LOG_DIR="$STAGE/logs" BACKUP_DIR="$STAGE/backups" \
  bash scripts/djcms_cutover.sh --check --no-backup-verify </dev/null >"$ART/cutover-check.out" 2>&1
rc=$?; check "djcms_cutover.sh --check --no-backup-verify (nic nie zmienia)" $rc
[ $rc -eq 0 ] || grep -E 'FAIL|BŁĄD' "$ART/cutover-check.out" | sed 's/^/     /'
in_stage env DJCMS_CUTOVER_LOG_DIR="$STAGE/logs" BACKUP_DIR="$STAGE/backups" \
  bash scripts/djcms_cutover.sh --dry-run --no-backup-verify </dev/null >"$ART/cutover-dry-run.out" 2>&1
rc=$?
[ $rc -eq 0 ] && grep -q 'djcms_switch.sh on' "$ART/cutover-dry-run.out" && grep -q '^DJCMS_PRIMARY=0$' "$STAGE/.env"
check "djcms_cutover.sh --dry-run: plan wypisany, DJCMS_PRIMARY dalej 0" $?

# --- B. Przełączenie pod ruchem -----------------------------------------------------------------------
log "B. djcms_cutover.sh --yes (kopia, zamrożenie, import, weryfikacja, djcms_switch.sh on) pod ruchem"
start_load cutover
T_SW="$(date +%s)"
in_stage env DJCMS_CUTOVER_LOG_DIR="$STAGE/logs" BACKUP_DIR="$STAGE/backups" \
  bash scripts/djcms_cutover.sh --yes </dev/null >"$ART/cutover-run.out" 2>&1
rc=$?
T_SW="$(( $(date +%s) - T_SW ))"
stop_load cutover
check "ruch w trakcie przełączenia: zero 5xx i zerwanych połączeń" $?
check "djcms_cutover.sh --yes zakończony powodzeniem (${T_SW} s)" $rc
if [ $rc -ne 0 ]; then
  grep -E '✗|BŁĄD' "$ART/cutover-run.out" | sed 's/^/     /'
  # Droga z runbooka dla konkursu, którego weryfikacja nie przechodzi z powodu, którego przełączenie
  # nie zmienia (np. S16 – odnośnik aplikacji do strony, której nie ma ani w Wagtailu, ani w djcms):
  # ponowienie z --skip. Dzięki temu reszta scenariusza sprawdza tryb primary mimo usterki wyżej.
  failed_sites="$(sed -n 's/.*verify_cutover: nie przeszły witryny:\(.*\) – nie przełączam.*/\1/p' "$ART/cutover-run.out" | tail -n 1)"
  if [ -n "$failed_sites" ] && grep -q '^DJCMS_PRIMARY=0$' "$STAGE/.env"; then
    skip_args=(); for s in $failed_sites; do skip_args+=(--skip "$s"); done
    log "B2. Ponowienie przełączenia z ${skip_args[*]} (pod ruchem)"
    start_load cutover-skip
    T_SW="$(date +%s)"
    in_stage env DJCMS_CUTOVER_LOG_DIR="$STAGE/logs" BACKUP_DIR="$STAGE/backups" \
      bash scripts/djcms_cutover.sh --yes "${skip_args[@]}" </dev/null >"$ART/cutover-run-skip.out" 2>&1
    rc=$?
    T_SW="$(( $(date +%s) - T_SW ))"
    stop_load cutover-skip
    check "ruch w trakcie ponowionego przełączenia: zero 5xx i zerwanych połączeń" $?
    check "djcms_cutover.sh --yes ${skip_args[*]} zakończony powodzeniem (${T_SW} s)" $rc
    [ $rc -eq 0 ] || tail -n 40 "$ART/cutover-run-skip.out" | sed 's/^/     /'
  else
    tail -n 40 "$ART/cutover-run.out" | sed 's/^/     /'
  fi
fi
grep -q '^DJCMS_PRIMARY=1$' "$STAGE/.env" && grep -q '^DJCMS_CUTOVER_DONE=' "$STAGE/.env" \
  && grep -q 'header_up X-Djcms-Mode primary' "$STAGE/caddy/Caddyfile"
check "po przełączeniu: DJCMS_PRIMARY=1 i DJCMS_CUTOVER_DONE w .env, Caddyfile w trybie primary" $?
dcx web python manage.py cms_freeze status >"$ART/freeze-on.out" 2>&1
check "cms_freeze status: edycja stron Wagtaila zamrożona" $?

# --- C. PRIMARY=1 -----------------------------------------------------------------------------------
log "C. DJCMS_PRIMARY=1 – djcms publicznie"
run_check primary 2>&1 | tee "$ART/check-primary.out"
check "check_djcms_primary.py --phase primary" "${PIPESTATUS[0]}"
in_stage bash scripts/djcms_switch.sh check </dev/null >"$ART/switch-check-1.out" 2>&1
rc=$?; check "djcms_switch.sh check (tryb primary)" $rc
[ $rc -eq 0 ] || sed 's/^/     /' "$ART/switch-check-1.out"

log "C2. Nowy konkurs po przełączeniu (ekologiczna.$DOMAIN) i jego wyłączenie"
dcx web python manage.py create_competition --slug ekologiczna --name "Olimpiada Ekologiczna" \
  --domain "ekologiczna.$DOMAIN" --from-template przedmiotowa --skip-existing >"$ART/create-ekologiczna.out" 2>&1
check "create_competition ekologiczna (jak ekran „Nowy konkurs”)" $?
run_check new-competition --slug ekologiczna 2>&1 | tee "$ART/check-new-competition.out"
check "nowy konkurs: pierwsze wejście = strona djcms (drzewo startowe)" "${PIPESTATUS[0]}"
dcx web python manage.py shell -c "from apps.tenancy.models import Competition; Competition.objects.filter(slug='ekologiczna').update(is_active=False)" >/dev/null 2>&1
check "wyłączenie konkursu ekologiczna (is_active=False)" $?
run_check disabled-competition --slug ekologiczna 2>&1 | tee "$ART/check-disabled-competition.out"
check "wyłączony konkurs: po odświeżeniu rejestru djcms 404" "${PIPESTATUS[0]}"

# --- D. Pomiar -----------------------------------------------------------------------------------------
# Strony `/`, `/zadania/`, `/wyniki/` (i `/druga/…`) przez prawdziwego Caddy'ego z kontenera w sieci
# `edge`: djcms (primary) kontra Wagtail (ciasteczko djcms_view=wagtail) z buforem stron web i bez.
# Współbieżność 1 = koszt jednego żądania bez kolejki (≈ czas CPU renderu), BENCH_CONCURRENCY (8)
# = lekki ruch, 16 = więcej niż wątków djcms (3×4) – widać kolejkowanie.
if [ "$BENCH" = 1 ]; then
  log "D. Pomiar: $BENCH_REQUESTS żądań, współbieżność 1/$BENCH_CONCURRENCY/16, przez proxy (scripts/djcms_bench.py)"
  bench() {  # bench <etykieta> <współbieżność> [argumenty djcms_bench.py…]
    local label="$1" conc="$2"; shift 2
    docker exec "$RUNNER" python /scripts/djcms_bench.py --connect proxy --label "$label c$conc" \
      --requests "$BENCH_REQUESTS" --concurrency "$conc" --json "artifacts/djcms/bench-$label-c$conc.json" "$@" \
      2>&1 | tee -a "$ART/bench.out"
  }
  pages() { printf -- '--url https://%s/ --url https://%s/zadania/ --url https://%s/wyniki/' "$1" "$1" "$1"; }
  rm -f "$ROOT"/e2e/artifacts/djcms/bench-*.json
  : >"$ART/bench.out"
  # shellcheck disable=SC2046 # pages: celowo rozbite na argumenty
  for conc in 1 "$BENCH_CONCURRENCY" 16; do
    bench "djcms@$DOMAIN" "$conc" $(pages "$DOMAIN")
    bench "wagtail-cache@$DOMAIN" "$conc" --cookie djcms_view=wagtail $(pages "$DOMAIN")
  done
  bench "djcms@fizyczna.$DOMAIN" "$BENCH_CONCURRENCY" $(pages "fizyczna.$DOMAIN")
  bench "djcms@$DOMAIN-druga" "$BENCH_CONCURRENCY" --url "https://$DOMAIN/druga/" --url "https://$DOMAIN/druga/zadania/"
  # Wagtail bez bufora stron: web odtworzony z PAGE_CACHE_ENABLED=0 (compose widzi zmianę env_file).
  sed -i 's/^PAGE_CACHE_ENABLED=.*/PAGE_CACHE_ENABLED=0/' "$STAGE/.env"
  dc up -d web >>"$ART/up.out" 2>&1 && wait_healthy web 60 3
  check "web odtworzony z PAGE_CACHE_ENABLED=0" $?
  for conc in 1 "$BENCH_CONCURRENCY" 16; do
    bench "wagtail-nocache@$DOMAIN" "$conc" --cookie djcms_view=wagtail $(pages "$DOMAIN")
  done
  sed -i 's/^PAGE_CACHE_ENABLED=.*/PAGE_CACHE_ENABLED=1/' "$STAGE/.env"
  dc up -d web >>"$ART/up.out" 2>&1 && wait_healthy web 60 3
  check "web z powrotem z buforem stron" $?
  cp "$ROOT"/e2e/artifacts/djcms/bench-*.json "$ART/" 2>/dev/null || true
  docker exec "$RUNNER" python /scripts/djcms_bench.py --summary artifacts/djcms 2>&1 | tee "$ART/bench-summary.out"
fi

# --- E. Wycofanie pod ruchem ---------------------------------------------------------------------------
log "E. djcms_cutover.sh --rollback --unfreeze (djcms_switch.sh off) pod ruchem"
start_load rollback
T_SW="$(date +%s)"
in_stage env DJCMS_CUTOVER_LOG_DIR="$STAGE/logs" BACKUP_DIR="$STAGE/backups" \
  bash scripts/djcms_cutover.sh --rollback --unfreeze </dev/null >"$ART/cutover-rollback.out" 2>&1
rc=$?
T_SW="$(( $(date +%s) - T_SW ))"
stop_load rollback
check "ruch w trakcie wycofania: zero 5xx i zerwanych połączeń" $?
check "djcms_cutover.sh --rollback --unfreeze zakończony powodzeniem (${T_SW} s)" $rc
[ $rc -eq 0 ] || tail -n 30 "$ART/cutover-rollback.out" | sed 's/^/     /'
grep -q '^DJCMS_PRIMARY=0$' "$STAGE/.env" && grep -q 'header_up X-Djcms-Mode preview' "$STAGE/caddy/Caddyfile"
check "po wycofaniu: DJCMS_PRIMARY=0, Caddyfile w trybie preview" $?
dcx web python manage.py cms_freeze status >"$ART/freeze-off.out" 2>&1
[ $? -eq 1 ]
check "cms_freeze status: edycja stron Wagtaila otwarta" $?
run_check wagtail 2>&1 | tee "$ART/check-wagtail.out"
check "check_djcms_primary.py --phase wagtail" "${PIPESTATUS[0]}"

cp -R "$ROOT/e2e/artifacts/djcms/." "$ART/" 2>/dev/null || true
printf '\n    czas całego przebiegu: %s s\n' "$(( $(date +%s) - T0 ))"
finish 0
