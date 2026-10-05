#!/usr/bin/env bash
# Test wdrożenia wersji porównawczej django CMS (`scripts/deploy.sh`, docs/tasks/DJ-01.md § 8.10).
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu):
#   scripts/tests/deploy_djcms_test.sh
#   DEPLOY_BASELINE_REF=main scripts/tests/deploy_djcms_test.sh   # + porównanie z deploy.sh z gałęzi
#
# Najważniejszy przypadek jest pierwszy: **bez `DJCMS_ENABLED` wdrożenie wykonuje dokładnie te
# polecenia dockera, co przed DJ-01, i nie zmienia w .env ani bajtu** (DJ-01 § 1.2 p. 9, kryterium
# 11). Lista poleceń jest wpisana niżej wprost – test mówi, co uznaje za „dzisiaj”. Z
# `DEPLOY_BASELINE_REF` test uruchamia dodatkowo `scripts/deploy.sh` z podanej rewizji w tej samej
# piaskownicy i porównuje z nim cały przebieg: polecenia dockera, polecenia ssh, .env i wyjście
# (z zamaskowanymi znacznikami czasu). Jedyną dopuszczalną różnicą jest jedno odczytanie
# przełącznika z .env (ssh + sed, bez dockera) po kroku 4/8.
#
# Jak to jest uruchamiane bez serwera: CAŁY deploy.sh biegnie lokalnie na atrapach. `ssh` wykonuje
# polecenie zdalne w piaskownicy (`bash -c`, stdin przekazany dalej), `docker` zapisuje wywołania,
# `git archive` pakuje bieżące drzewo (scripts/, deploy/, pliki compose'a). Kroki 1/8 (apt, ufw)
# i 8/8 (cron i logrotate w /etc) atrapa ssh pomija – nie dotyczą djcms, a na maszynie
# deweloperskiej pisałyby poza piaskownicą. Skrypty po stronie serwera (render_caddyfile.sh,
# proxy_config.sh, djcms_db.sh, upgrade_postgres18.sh, maintenance.sh) są prawdziwe.
#
# Część 10 (konfiguracja proxy przy KAŻDYM wdrożeniu, docs/OPERACJE.md § 23) korzysta z tej samej
# piaskownicy: atrapa dockera modeluje kontener proxy (widzi katalog caddy/, stary montaż albo nie
# działa) i to, co Caddy ma załadowane – test sprawdza, że zmiana deploy/Caddyfile dochodzi do
# działającego proxy przez `caddy reload`, bez odtwarzania kontenera.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY="$ROOT/scripts/deploy.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/deploy-djcms-test.XXXXXX")"
trap '[ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"' EXIT

SRV="$WORK/srv"            # udawany REMOTE_DIR na serwerze
BAK="$WORK/backups"        # BACKUP_DIR
BIN="$WORK/bin"
mkdir -p "$BIN"

failures=0
check() {
  if [ "$2" -eq 0 ]; then
    printf 'ok   %s\n' "$1"
  else
    printf 'FAIL %s\n' "$1"
    failures=$((failures + 1))
  fi
}

# 0. Składnia.
bash -n "$DEPLOY"
check "scripts/deploy.sh przechodzi bash -n" $?

# --- Atrapy ------------------------------------------------------------------------------------

# ssh: `ssh -i KLUCZ -o … -o … CEL polecenie…` – argumenty po celu sklejone spacją (tak robi ssh),
# wykonane w piaskownicy. Każde polecenie trafia do SSH_LOG; stdin (skrypt kroku) do pliku obok.
cat >"$BIN/ssh" <<'STUB'
#!/usr/bin/env bash
while [ $# -gt 0 ]; do
  case "$1" in
    -i|-o) shift 2 ;;
    *) shift; break ;;   # cel (user@host)
  esac
done
cmd="$*"
n=$(( $(wc -l <"$SSH_LOG") + 1 ))
printf '%s\n' "$cmd" >>"$SSH_LOG"
# Sesja blokady (krok 2/8, w tle do końca wdrożenia): wejście przechodzi wprost – czeka na jego koniec.
case "$cmd" in *"/caddy/.lock'"*) cd "$FAKE_HOME" && exec bash -c "$cmd" ;; esac
cat >"$SSH_LOG.stdin.$n"
# Krok 1/8 (apt, ufw) i 8/8 (/etc/cron.d, /etc/logrotate.d) – pomijane, patrz nagłówek testu.
# Rozpoznanie po treści skryptu kroku – ale tylko dla `bash -s` (paczka kodu z kroku 2/8 zawiera
# deploy.sh, więc te same napisy są też w niej).
if [ "${cmd%bash -s}" != "$cmd" ] &&
   grep -qE 'apt-get install -y -qq ca-certificates|/etc/cron\.d/olimpiada-backup' "$SSH_LOG.stdin.$n"; then
  exit 0
fi
cd "$FAKE_HOME" && bash -c "$cmd" <"$SSH_LOG.stdin.$n"
STUB

# docker: zapis wywołania i odpowiedzi, których potrzebują kroki (zdrowe usługi, liczba klientów
# bazy, niepusty zrzut, istniejąca baza djcms, wynik importu z STUB_IMPORT_RC).
cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
case "$*" in
  "compose config") echo "name: olimpiada" ;;
  "volume inspect "*) exit 1 ;;
  *"compose ps -q --status running proxy"*) [ "$(cat "$STUB_BOX/state")" = down ] || echo 0123abcd ;;
  # Migawka do wycofania (krok 2a/8) i decyzja (scripts/rollback.sh, OPS-04): działający web
  # (c0ffee → sha256:web), djcms – gdy włączony w .env; zastosowane migracje w $STUB_BOX/migrations
  # (STUB_NEW_MIGRATION=1: start usług w 4b dopisuje migrację – jak entrypoint web nowej wersji).
  "compose ps -q web") echo c0ffee ;;
  "compose ps -q djcms") grep -q '^DJCMS_ENABLED=1' .env 2>/dev/null && echo d0ffee ;;
  "inspect --format {{.Image}} c0ffee") echo sha256:web ;;
  "inspect --format {{.Image}} d0ffee") echo sha256:dj ;;
  "image inspect --format {{.Id}} olimpiada/web:previous") echo sha256:web ;;
  "image inspect --format {{.Id}} olimpiada/djcms:previous") echo sha256:dj ;;
  *"exec -T db psql"*"django_migrations"*) cat "$STUB_BOX/migrations" ;;
  *"check_domains --hosts"*) echo olimpiada.example ;;
  *"manage.py page_cache_clear"*) exit "${STUB_PCC_RC:-0}" ;;   # STUB_PCC_RC≠0 – web nie odpowiada
  *"compose ps"*) printf 'db=healthy\nweb=healthy\nproxy=healthy\ndjcms=healthy\n' ;;
  *"exec -T db psql"*"datname = 'olimpiada_djcms'"*) echo 1 ;;
  *"exec -T db psql"*"ON_ERROR_STOP"*) cat >>"$DOCKER_LOG.sql" ;;   # scripts/djcms_db.sh (SQL na stdin)
  *"exec -T db psql"*) echo 0 ;;
  *"exec -T db pg_dump"*) echo "PGDMP-atrapa" ;;
  *"import_cms_bundle"*) exit "${STUB_IMPORT_RC:-0}" ;;
  # Kontener proxy – stan w $STUB_BOX/state: `live` (montaż katalogu caddy/: widzi bieżący plik),
  # `stale` (montaż sprzed CADDY_CONFIG_DIR: widzi inną treść), `down` (nie działa). Odtworzenie
  # daje `live`; `caddy reload` kopiuje to, co kontener widzi, do $STUB_BOX/loaded (konfiguracja
  # załadowana), `caddy validate` zapisuje sprawdzaną treść w $STUB_BOX/validated.
  *"exec -T proxy sha256sum /etc/caddy/Caddyfile"*)
    case "$(cat "$STUB_BOX/state")" in
      live) sha256sum caddy/Caddyfile | sed 's|caddy/Caddyfile|/etc/caddy/Caddyfile|' ;;
      stale) echo "0000000000000000000000000000000000000000000000000000000000000000  /etc/caddy/Caddyfile" ;;
      *) exit 1 ;;
    esac ;;
  # Treść /etc/caddy/Caddyfile, którą widzi kontener (kopia przy pierwszym renderze na serwerze).
  *"exec -T proxy cat /etc/caddy/Caddyfile"*)
    case "$(cat "$STUB_BOX/state")" in
      live) cat caddy/Caddyfile 2>/dev/null || exit 1 ;;
      stale) printf '# stara treść (montaż sprzed CADDY_CONFIG_DIR)\n' ;;
      *) exit 1 ;;
    esac ;;
  *"exec -T proxy sh -c "*"caddy validate"*) cat >"$STUB_BOX/validated"; exit "${STUB_VALIDATE_RC:-0}" ;;
  *"exec -T proxy caddy reload"*)
    [ "${STUB_RELOAD_RC:-0}" = 0 ] || exit "$STUB_RELOAD_RC"
    [ "$(cat "$STUB_BOX/state")" = live ] && cp caddy/Caddyfile "$STUB_BOX/loaded" ;;
  *"compose up -d --force-recreate --no-deps proxy"*) echo live >"$STUB_BOX/state"; cp caddy/Caddyfile "$STUB_BOX/loaded" ;;
  # Strona prac technicznych: proxy widzi katalog maintenance/ na żywo (montaż katalogu stanu).
  *"exec -T proxy test -f /srv/maintenance/"*) a="$*"; [ -e "maintenance/${a##*/srv/maintenance/}" ]; exit $? ;;
  # `up -d` uruchamia proxy, które nie działa (z plikiem z tej chwili); działającego nie odtwarza,
  # bo konfiguracja compose'a proxy się nie zmienia – dokładnie ten przypadek był błędem sprzed
  # montażu katalogu (stan `stale` zostaje `stale`).
  *"compose up -d --remove-orphans "*" proxy"*)
    [ "${STUB_NEW_MIGRATION:-0}" = 1 ] && echo "results.0099_nowa_kolumna" >>"$STUB_BOX/migrations"
    # STUB_UP_RC≠0: nowy web nie staje się healthy – compose kończy `up` błędem (proxy zależy od web).
    [ "${STUB_UP_RC:-0}" = 0 ] || { echo "dependency failed to start: container web is unhealthy" >&2; exit "$STUB_UP_RC"; }
    [ "$(cat "$STUB_BOX/state")" = down ] && { echo live >"$STUB_BOX/state"; cp caddy/Caddyfile "$STUB_BOX/loaded"; } ;;
  # Kontrakt tras z obrazu web (krok dj. przy DJCMS_PRIMARY=1); STUB_ROUTES_DIFF=1 – obraz w innej wersji.
  *"djcms_routes --format env"*)
    if [ "${STUB_ROUTES_DIFF:-0}" = 1 ]; then echo "APP_RE='^/inny$'"; else cat backend/djcms_contract/app_routes.env; fi ;;
  *"sync_competitions --list-hosts"*) printf 'bez zmian: kwantowa\nolimpiada.example kwantowa\n' ;;
  # Pomoc komendy: obraz djcms z DJ-02e zna `--import-missing` (domyślnie); STUB_SYNC_OLD=1 – obraz
  # sprzed DJ-02e (flagi nie ma – wdrożenie woła wtedy samo `sync_competitions`).
  *"sync_competitions --help"*)
    echo "usage: manage.py sync_competitions [-h] [--dry-run] [--list-hosts]"
    [ "${STUB_SYNC_OLD:-0}" = 1 ] || echo "  --import-missing  Zaimportuj treść z API do witryn bez stron." ;;
  # STUB_SYNC_FAILS=N: pierwsze N wywołań kończy się błędem API (jak tuż po restarcie web, OPS-04 § 4).
  *"sync_competitions"*)
    n="$(cat "$STUB_BOX/sync_fails" 2>/dev/null || echo "${STUB_SYNC_FAILS:-0}")"
    if [ "$n" -gt 0 ]; then
      echo $((n - 1)) >"$STUB_BOX/sync_fails"
      echo "CommandError: Lista konkursów z API niedostępna: timeout" >&2
      exit 1
    fi
    exit "${STUB_SYNC_RC:-0}" ;;
esac
exit 0
STUB

# git: tylko `archive` (paczka drzewa z SRC_TAR). APP_VERSION jest podane, więc `describe` nie pada.
cat >"$BIN/git" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  archive) cat "$SRC_TAR" ;;
  *) echo "git (atrapa): $*" >&2; exit 1 ;;
esac
STUB
# curl (kontrola dymna djcms_switch.sh check przy DJCMS_PRIMARY=1): strony publiczne z nagłówkiem
# trybu z STUB_CURL_MODE (pusty = jak web), /login/ i /static/ – 200, /internal/ – 404.
cat >"$BIN/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG.curl"
# Konfiguracja z `-K -` (przepustka prac technicznych) – do dziennika jako „config: …”.
case " $* " in *" -K - "*) sed 's/^/config: /' >>"$DOCKER_LOG.curl" ;; esac
url="${!#}"; path="/${url#https://*/}"
# Kontrola dymna po wdrożeniu (scripts/smoke.sh, krok 5b/8, OPS-04): `-D plik -o plik -w '%{http_code}
# %{time_total}'` – zdrowy serwis; STUB_SMOKE_FAIL=<wersja>|* – /healthz/ 503, gdy APP_VERSION w .env
# (katalog bieżący = REMOTE_DIR) to ta wersja (np. tylko nowa, a po wycofaniu już nie).
case " $* " in *" %{http_code} %{time_total} "*)
  hdr=/dev/null body=/dev/null prev=""
  for a in "$@"; do case "$prev" in -D) hdr="$a" ;; -o) body="$a" ;; esac; prev="$a"; done
  ver="$(sed -n 's/^APP_VERSION=//p' .env 2>/dev/null | tail -n 1)"
  code=200 h="content-type: text/html" b="ok"
  case "$path" in
    /healthz/) b='{"status": "ok"}'
      case "${STUB_SMOKE_FAIL:-}" in '') ;; '*'|"$ver") code=503; b='{"status": "degraded"}' ;; esac ;;
    /status.json) b="{\"status\": \"ok\", \"version\": \"$ver\"}" ;;
    /login/) h="content-security-policy: default-src 'self'
set-cookie: csrftoken=x; Secure"
      b='<input name="csrfmiddlewaretoken"><link rel="stylesheet" href="/static/css/app.0123456789ab.css">' ;;
    /api/*) code=404; h="content-type: application/json"; b='{}' ;;
    /) h="content-security-policy: default-src 'self'" ;;
  esac
  printf 'HTTP/2 %s\r\n%s\r\n\r\n' "$code" "$h" >"$hdr"
  printf '%s' "$b" >"$body"
  printf '%s 0.001' "$code"
  exit 0 ;;
esac
# Kontrola z przepustką przy --maintenance (krok 5a): `-w '%{http_code}' -o /dev/null`.
case " $* " in *" %{http_code} "*) echo "${STUB_HEALTHZ_CODE:-200}"; exit 0 ;; esac
case "$path" in
  /internal/*) printf 'HTTP/2 404\r\n\r\n' ;;
  */login/|/static/*) printf 'HTTP/2 200\r\n\r\n' ;;
  *) printf 'HTTP/2 200\r\n%s\r\n' "${STUB_CURL_MODE:+x-djcms-mode: $STUB_CURL_MODE}" ;;
esac
STUB
chmod +x "$BIN/ssh" "$BIN/docker" "$BIN/git" "$BIN/curl"

# Drzewo „z repozytorium”, które deploy rozpakowuje na serwerze: stan roboczy (także niezacommitowany).
tar -C "$ROOT" -cf "$WORK/tree.tar" --exclude='deploy/Caddyfile.generated' \
  scripts deploy docker-compose.yml docker-compose.djcms.yml backend/djcms_contract

# .env serwera po wcześniejszych wdrożeniach (bez dj.): wszystkie wpisy, które krok 4/8 dokłada
# „jeśli brak”, już są – kolejne wdrożenie bez dj. ma go zostawić co do bajtu.
cat >"$WORK/env.fixture" <<'ENV'
APP_VERSION=vtest
SITE_DOMAIN=olimpiada.example
ACME_EMAIL=ops@olimpiada.example
S3_PUBLIC_ADDRESS=olimpiada.example:9000
MAX_UPLOAD_MB=25

DJANGO_SECRET_KEY=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab
POSTGRES_DB=olimpiada
POSTGRES_USER=olimpiada
POSTGRES_PASSWORD=pgpassword0123456789abcdefghijkl
REDIS_PASSWORD=redispassword0123456789abcdefghi
S3_PUBLIC_ENDPOINT_URL=https://olimpiada.example:9000

# Przepustka operatora
MAINTENANCE_BYPASS_TOKEN=0123456789abcdefghijklmnopqrstuvwxyzABCD

EXTRA_DOMAINS=

# PLATFORM_SUBDOMAINS=1
CADDY_CONFIG_DIR=./caddy

BACKUP_PASSPHRASE=backuppassphrase0123456789abcdefghijklmnopqrstuv
ENV

BOX="$WORK/box"            # stan atrapy kontenera proxy (state, loaded, validated)
reset_server() {  # czysty serwer z .env z fixture'a (albo z pliku podanego w $1); proxy nie działa
  rm -rf "${SRV:?}" "${BAK:?}" "${WORK:?}/home" "${BOX:?}"
  mkdir -p "$SRV" "$BAK" "$WORK/home" "$BOX"
  cp "${1:-$WORK/env.fixture}" "$SRV/.env"
  echo down >"$BOX/state"
  echo core.0001_initial >"$BOX/migrations"   # zastosowane migracje (atrapa psql, OPS-04)
}

installed_proxy_cfg() {  # serwer po wcześniejszym wdrożeniu z CADDY_CONFIG_DIR: caddy/Caddyfile jest, proxy go widzi
  mkdir -p "$SRV/caddy"
  cp "$ROOT/deploy/Caddyfile" "$SRV/caddy/Caddyfile"
  echo live >"$BOX/state"
}

run_deploy() {
  # run_deploy <plik deploy.sh> <etykieta> [ZMIENNA=wartość…] – jeden pełny przebieg; kod wyjścia.
  local script="$1" label="$2"; shift 2
  DOCKER_LOG="$WORK/$label.docker"; SSH_LOG="$WORK/$label.ssh"; OUT="$WORK/$label.out"
  : >"$DOCKER_LOG"; : >"$SSH_LOG"; rm -f "$DOCKER_LOG.sql" "$DOCKER_LOG.curl" "$BOX/sync_fails"
  # Kontrola dymna i wycofanie (OPS-04) bez czekania: jedna próba, bez przerw; ponawianie
  # sync_competitions bez przerw. Atrapa ssh przekazuje środowisko dalej, więc dochodzą do „serwera”.
  ( env -u DJCMS_ENABLE -u DJCMS_IMAGE -u DJCMS_ADMIN_EMAIL -u DJCMS_ADMIN_PASSWORD -u WEB_IMAGE \
      -u STUB_SMOKE_FAIL -u STUB_NEW_MIGRATION -u STUB_SYNC_FAILS -u DEPLOY_SMOKE -u STUB_PCC_RC -u STUB_UP_RC \
      -u DJCMS_PRIMARY -u STUB_CURL_MODE -u STUB_SYNC_RC -u STUB_SYNC_OLD -u STUB_VALIDATE_RC -u STUB_RELOAD_RC       -u STUB_HEALTHZ_CODE -u STUB_ROUTES_DIFF -u OLIMPIADA_PROXY_LOCK -u NEW_COMPETITION_SLUG -u COORDINATOR_EMAIL -u COORDINATOR_PASSWORD       -u MAINTENANCE_MESSAGE -u MAINTENANCE_MINUTES       PATH="$BIN:$PATH" DOCKER_LOG="$DOCKER_LOG" SSH_LOG="$SSH_LOG" SRC_TAR="${SRC_TAR:-$WORK/tree.tar}" \
      STUB_BOX="$BOX" \
      SMOKE_RETRIES=1 SMOKE_RETRY_DELAY=0 ROLLBACK_WAIT_SECONDS=0 DJCMS_SYNC_RETRY_DELAYS="0 0 0" \
      FAKE_HOME="$WORK/home" REMOTE_DIR="$SRV" BACKUP_DIR="$BAK" SSH_KEY=/dev/null \
      APP_VERSION=vtest MAIL_PUBLIC_IP=203.0.113.7 "$@" \
      bash "$script" ${DEPLOY_FLAGS:-} root@test.invalid ) </dev/null >"$OUT" 2>&1
}

show_on_fail() {  # show_on_fail <kod> <plik> – przy porażce pokaż plik (wcięty)
  [ "$1" -eq 0 ] || sed 's/^/     /' "$2"
}

# Polecenia dockera pełnego wdrożenia bez dj. (bez WEB_IMAGE, bez --maintenance, bez nowego
# konkursu, bez seedów treści) – kroki 2–7/8 i porządki, w kolejności, przy działającym proxy,
# które widzi katalog caddy/. Stan sprzed DJ-01 plus konfiguracja proxy (OPERACJE § 23): walidacja
# nowego pliku w kroku 4/8, przed budowaniem, i `caddy reload` w kroku 4c/8, po starcie usług; na końcu
# dwa odczyty profilu `monitoring` (OPS-02 – ostrzeżenie o GlitchTipie bez kont, odświeżenie `uptime`).
# Od OPS-04: migawka do wycofania na początku (krok 2a/8: obraz działającego web → :previous,
# odczyt django_migrations) oraz kontrola dymna po kroku 5/8 (hosty z check_domains --hosts, reszta
# przez curl) i zapis udanego wdrożenia (obraz działającego web) – bez djcms przy wyłączonym dj.
DZISIAJ='compose ps -q web
inspect --format {{.Image}} c0ffee
tag sha256:web olimpiada/web:previous
compose exec -T db psql -X -U olimpiada -d olimpiada -Atc SELECT app || '"'.'"' || name FROM django_migrations ORDER BY 1
compose config
volume inspect olimpiada_pg_data
compose ps -q --status running proxy
compose config
compose ps --status running --format {{.Image}} proxy
compose exec -T proxy sh -c cat > /tmp/Caddyfile.next && caddy validate --config /tmp/Caddyfile.next --adapter caddyfile
compose build --pull web
compose pull --ignore-buildable --quiet
compose up -d db
compose ps --format {{.Service}}={{.Health}}
compose ps --format {{.Service}}={{.Health}}
compose exec -T db pg_dump -U olimpiada -d olimpiada -Fc
compose up -d --remove-orphans db redis minio minio-init clamav mail web worker beat proxy
compose exec -T proxy sha256sum /etc/caddy/Caddyfile
compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
compose ps --format {{.Service}}={{.Health}}
compose exec -T web python manage.py page_cache_clear
compose exec -T web python manage.py check_domains --hosts
compose ps -q web
inspect --format {{.Image}} c0ffee
compose exec -T web python manage.py seed_edition_kwantowa
compose exec -T web python manage.py seed_schools
compose ps --format table {{.Service}}\t{{.State}}\t{{.Health}}
compose exec -T mail cat /etc/opendkim/keys/olimpiada.example.txt
images --filter=reference=olimpiada/web --format {{.CreatedAt}}|{{.Repository}}:{{.Tag}}
image prune -f
compose exec -T web python manage.py check_domains --all
compose --profile monitoring ps -q --status running glitchtip
compose --profile monitoring ps -q uptime'

# ================================================================================================
# 1. dj. wyłączone (brak wpisu w .env): polecenia dockera dzisiejsze, .env nietknięty.
# ================================================================================================
reset_server
installed_proxy_cfg
run_deploy "$DEPLOY" off
rc=$?
check "wdrożenie bez DJCMS_ENABLED kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/off.out"
[ "$(cat "$WORK/off.docker")" = "$DZISIAJ" ]
rc=$?
check "bez DJCMS_ENABLED polecenia docker są dokładnie dzisiejsze" $rc
[ $rc -eq 0 ] || { printf -- '     --- wykonane:\n'; sed 's/^/     /' "$WORK/off.docker"; }
cmp -s "$WORK/env.fixture" "$SRV/.env"
check "bez DJCMS_ENABLED .env zostaje co do bajtu" $?
! grep -qi 'djcms' "$WORK/off.docker"
check "ani jedno polecenie docker nie wspomina djcms" $?
[ "$(grep -c 'DJCMS' "$WORK/off.ssh")" = "1" ] && grep -qFx "sed -n 's/^DJCMS_ENABLED=//p' '$SRV/.env' | tail -n 1" "$WORK/off.ssh"
check "jedyne polecenie ssh z DJCMS to odczyt przełącznika z .env (bez dockera)" $?
cmp -s "$ROOT/deploy/Caddyfile" "$SRV/caddy/Caddyfile" && cmp -s "$SRV/caddy/Caddyfile" "$BOX/loaded"
check "konfiguracja proxy = deploy/Caddyfile bajt w bajt, załadowana w proxy" $?
! grep -qE '^==> dj\.' "$WORK/off.out"
check "log wdrożenia bez kroku dj." $?
[ ! -e "$WORK/off.docker.sql" ]
check "scripts/djcms_db.sh nie jest wołany" $?

# Wartość wyłączona wprost (DJCMS_ENABLED=0) – to samo.
reset_server
installed_proxy_cfg
{ cat "$WORK/env.fixture"; echo "DJCMS_ENABLED=0"; } >"$SRV/.env"
cp "$SRV/.env" "$WORK/env.zero"
run_deploy "$DEPLOY" zero
[ "$(cat "$WORK/zero.docker")" = "$DZISIAJ" ] && cmp -s "$WORK/env.zero" "$SRV/.env"
check "DJCMS_ENABLED=0: polecenia dzisiejsze, .env nietknięty" $?

# 1a. Porównanie z deploy.sh z innej rewizji (opcjonalne, DEPLOY_BASELINE_REF).
mask() {  # znaczniki czasu, rozmiary plików i wiersz z ls -lh zmieniają się między przebiegami
  sed -E 's/[0-9]{8}-[0-9]{6}/STAMP/g; s/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:+-]+/DATE/g; /^-rw/d' "$1" | no_proxy_cfg out
}
# Konfiguracja proxy (OPERACJE § 23) zmienia przebieg KAŻDEGO wdrożenia celowo – rewizja sprzed niej
# różni się tylko tym: poleceniami proxy w dockerze, krokiem 4c/8 i katalogiem caddy w ssh, linijkami
# CADDYFILE_PATH / CADDY_CONFIG_DIR w .env i komunikatami proxy_config.sh. Porównanie je pomija.
no_proxy_cfg() {  # no_proxy_cfg docker|ssh|env|out  (stdin → stdout)
  # Pomija też to, co celowo dokłada OPS-04 (migawka 2a/8, kontrola dymna 5b/8, zapis udanego wdrożenia).
  case "$1" in
    docker) grep -vE '^compose (ps -q --status running proxy|exec -T proxy (sh -c .*caddy validate|sha256sum /etc/caddy/Caddyfile|cat /etc/caddy/Caddyfile|caddy reload |wget )|up -d --force-recreate --no-deps proxy)' \
      | grep -vE '^(compose ps -q (web|djcms)|inspect --format \{\{\.Image\}\} |tag sha256:|compose exec -T db psql .*django_migrations|compose exec -T web python manage.py (check_domains --hosts|page_cache_clear))' ;;
    ssh) grep -vF 'bash scripts/proxy_config.sh apply' | grep -vF "/caddy/.lock'" | grep -vE 'scripts/(rollback|smoke)\.sh' \
      | sed 's/ ! -name deploy-state -exec/ -exec/; s/ ! -name caddy -exec/ -exec/; s/ OLIMPIADA_PROXY_LOCK=held / /' ;;
    env) grep -vE '^(CADDYFILE_PATH=|CADDY_CONFIG_DIR=|# Konfiguracja proxy|# i EXTRA_DOMAINS przez scripts/render_caddyfile|# przez scripts/proxy_config\.sh)' ;;
    out) grep -vE '^(proxy: |==> 4c/8 |==> 2a/8 |==> 5b/8 |migawka: |smoke: |  (ok|–) |== https://|zapisano: wersja |blokada zmian serwisu |UWAGA: brak flock na serwerze|$)' | sed -E 's/^render_caddyfile: [^ ]+ /render_caddyfile: OUT /' ;;
  esac
}
if [ -n "${DEPLOY_BASELINE_REF:-}" ]; then
  git -C "$ROOT" show "$DEPLOY_BASELINE_REF:scripts/deploy.sh" >"$WORK/deploy-base.sh" 2>/dev/null
  check "deploy.sh z rewizji $DEPLOY_BASELINE_REF odczytany" $?
  reset_server; echo live >"$BOX/state"
  run_deploy "$WORK/deploy-base.sh" base
  cp "$SRV/.env" "$WORK/base.env"
  reset_server; echo live >"$BOX/state"
  run_deploy "$DEPLOY" new
  [ "$(no_proxy_cfg docker <"$WORK/base.docker")" = "$(no_proxy_cfg docker <"$WORK/new.docker")" ]
  check "[$DEPLOY_BASELINE_REF] polecenia docker identyczne (poza konfiguracją proxy)" $?
  [ "$(no_proxy_cfg env <"$WORK/base.env")" = "$(no_proxy_cfg env <"$SRV/.env")" ]
  check "[$DEPLOY_BASELINE_REF] .env po wdrożeniu identyczny (poza CADDYFILE_PATH / CADDY_CONFIG_DIR)" $?
  diff <(no_proxy_cfg ssh <"$WORK/base.ssh") <(no_proxy_cfg ssh <"$WORK/new.ssh") | grep -E '^[<>]' >"$WORK/ssh.diff"
  [ "$(cat "$WORK/ssh.diff")" = "> sed -n 's/^DJCMS_ENABLED=//p' '$SRV/.env' | tail -n 1" ]
  rc=$?
  check "[$DEPLOY_BASELINE_REF] polecenia ssh identyczne poza jednym odczytem przełącznika z .env" $rc
  show_on_fail $rc "$WORK/ssh.diff"
  diff <(mask "$WORK/base.out") <(mask "$WORK/new.out") >"$WORK/out.diff"
  rc=$?
  check "[$DEPLOY_BASELINE_REF] wyjście wdrożenia identyczne (po zamaskowaniu czasu)" $rc
  show_on_fail $rc "$WORK/out.diff"
fi

# ================================================================================================
# 2. Włączenie z wdrożenia: DJCMS_ENABLE=1 + konto administratora.
# ================================================================================================
ADMIN_PW="Hasło z spacją i 'apostrofem' \$HOME"
reset_server
run_deploy "$DEPLOY" on DJCMS_ENABLE=1 DJCMS_ADMIN_EMAIL=redakcja@olimpiada.example DJCMS_ADMIN_PASSWORD="$ADMIN_PW"
rc=$?
check "wdrożenie z DJCMS_ENABLE=1 kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/on.out"
env_line() { sed -n "s/^$1=//p" "$SRV/.env" | tail -n 1; }
[ "$(env_line DJCMS_ENABLED)" = "1" ] && [ "$(grep -c '^DJCMS_ENABLED=' "$SRV/.env")" = "1" ]
check ".env: DJCMS_ENABLED=1 dopisane raz" $?
env_line DJCMS_SECRET_KEY | grep -qE '^[A-Za-z0-9]{64}$' &&
  env_line DJCMS_DB_PASSWORD | grep -qE '^[A-Za-z0-9]{32}$' &&
  env_line DJCMS_INTERNAL_TOKEN | grep -qE '^[A-Za-z0-9]{48}$' &&
  env_line DJCMS_SSO_KEY | grep -qE '^[A-Za-z0-9]{64}$' &&
  [ "$(env_line DJCMS_SSO_KEY)" != "$(env_line DJCMS_SECRET_KEY)" ]
check ".env: sekrety djcms (64/32/48/64 znaków [A-Za-z0-9], klucz SSO osobny)" $?
env_line DJCMS_DB_PASSWORD | grep -qE '^[A-Za-z0-9_-]{16,}$'
check ".env: DJCMS_DB_PASSWORD spełnia warunek scripts/djcms_db.sh" $?
[ "$(env_line COMPOSE_FILE)" = "docker-compose.yml:docker-compose.djcms.yml" ] && [ "$(env_line COMPOSE_PROFILES)" = "djcms" ]
check ".env: COMPOSE_FILE z nakładką djcms i COMPOSE_PROFILES=djcms" $?
[ "$(env_line DJCMS_INITIAL_IMPORT)" = "done" ]
check ".env: pierwszy import oznaczony jako wykonany" $?
# Plik wynikowy = fixture + dopiski (nic z istniejących linijek nie zmienione ani nie usunięte).
head -c "$(wc -c <"$WORK/env.fixture")" "$SRV/.env" | cmp -s - "$WORK/env.fixture"
check ".env: istniejące linijki nietknięte (djcms tylko dopisuje na końcu)" $?
grep -q '^dj\.{\$SITE_DOMAIN} {$' "$SRV/caddy/Caddyfile" && cmp -s "$SRV/caddy/Caddyfile" "$BOX/loaded"
check "konfiguracja proxy z blokiem dj., załadowana w proxy" $?

WLACZONE='compose ps -q web
inspect --format {{.Image}} c0ffee
tag sha256:web olimpiada/web:previous
compose exec -T db psql -X -U olimpiada -d olimpiada -Atc SELECT app || '"'.'"' || name FROM django_migrations ORDER BY 1
compose config
volume inspect olimpiada_pg_data
compose ps -q --status running proxy
compose build --pull web
compose --profile djcms build --pull djcms
compose pull --ignore-buildable --quiet
compose up -d db
compose ps --format {{.Service}}={{.Health}}
compose ps --format {{.Service}}={{.Health}}
compose exec -T db psql -X -q -v ON_ERROR_STOP=1 -U olimpiada -d olimpiada
compose exec -T db pg_dump -U olimpiada -d olimpiada -Fc
compose exec -T db psql -X -U olimpiada -d olimpiada -Atc SELECT 1 FROM pg_database WHERE datname = '"'olimpiada_djcms'"'
compose exec -T db pg_dump -U olimpiada -d olimpiada_djcms -Fc
compose up -d --remove-orphans db redis minio minio-init clamav mail web worker beat proxy djcms
compose exec -T proxy sha256sum /etc/caddy/Caddyfile
compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
compose ps --format {{.Service}}={{.Health}}
compose exec -T web python manage.py page_cache_clear
compose exec -T web python manage.py check_domains --hosts
compose ps -q web
inspect --format {{.Image}} c0ffee
compose ps -q djcms
inspect --format {{.Image}} d0ffee
compose exec -T web python manage.py seed_edition_kwantowa
compose exec -T web python manage.py seed_schools
compose ps --format table {{.Service}}\t{{.State}}\t{{.Health}}
compose exec -T mail cat /etc/opendkim/keys/olimpiada.example.txt
images --filter=reference=olimpiada/web --format {{.CreatedAt}}|{{.Repository}}:{{.Tag}}
image prune -f
compose exec -T web python manage.py check_domains --all
compose ps --format {{.Service}}={{.Health}}
compose exec -T djcms python manage.py setup_djcms_groups
compose exec -T -e DJCMS_ADMIN_EMAIL -e DJCMS_ADMIN_PASSWORD djcms python manage.py bootstrap_djcms_admin
compose exec -T djcms python manage.py sync_competitions --help
compose exec -T djcms python manage.py sync_competitions --import-missing
compose exec -T djcms python manage.py import_cms_bundle --from-api --if-empty
compose --profile monitoring ps -q --status running glitchtip
compose --profile monitoring ps -q uptime'
[ "$(cat "$WORK/on.docker")" = "$WLACZONE" ]
rc=$?
check "z DJCMS_ENABLE=1: build djcms, baza, kopia, start (proxy uruchomione z bieżącym plikiem), grupy, administrator, rejestr (--import-missing), import – w tej kolejności" $rc
[ $rc -eq 0 ] || diff <(printf '%s\n' "$WLACZONE") "$WORK/on.docker" | sed 's/^/     /'
grep -q 'CREATE ROLE olimpiada_djcms' "$WORK/on.docker.sql" 2>/dev/null
check "scripts/djcms_db.sh dostał SQL roli i bazy" $?
ls "$BAK"/djcms-db-pre-*-vtest.dump >/dev/null 2>&1 && ls "$BAK"/pre-deploy-*-vtest.dump >/dev/null 2>&1
check "kopie przed migracjami: baza główna i baza djcms" $?
! grep -qF "$ADMIN_PW" "$WORK/on.ssh" "$WORK/on.docker" &&
  ! grep -qF "redakcja@olimpiada.example" "$WORK/on.ssh" "$WORK/on.docker"
check "dane administratora nie pojawiają się w argumentach ssh ani docker (tylko stdin i środowisko)" $?
grep -qE '^==> dj\. ' "$WORK/on.out" && grep -qF 'https://dj.olimpiada.example/' "$WORK/on.out" &&
  grep -qF 'Serwis publiczny: Wagtail (DJCMS_PRIMARY=0)' "$WORK/on.out"
check "log wdrożenia ma krok dj. z adresem i trybem serwisu (Wagtail)" $?
grep -q 'header_up X-Djcms-Mode preview' "$SRV/caddy/Caddyfile" &&
  [ "$(grep -c '^    # >>> django CMS ' "$SRV/caddy/Caddyfile")" -ge 1 ]
check "konfiguracja proxy z sekcją tras djcms w trybie preview (kontrakt tras z paczki kodu)" $?
! grep -q -- "-o /dev/null -D -" "$WORK/on.docker.curl"
check "przy DJCMS_PRIMARY=0 wdrożenie nie robi kontroli dymnej djcms" $?

# Hasło doszło do skryptu zdalnego nienaruszone (printf %q w pierwszych linijkach stdin).
last_stdin="$(grep -l '^DJCMS_ADMIN_PASSWORD=' "$WORK"/on.ssh.stdin.* | head -n 1)"
( eval "$(grep -E '^DJCMS_ADMIN_PASSWORD=' "$last_stdin")"; [ "$DJCMS_ADMIN_PASSWORD" = "$ADMIN_PW" ] )
check "hasło ze spacją, apostrofem i \$ przechodzi przez printf %q bez zmian" $?

# ================================================================================================
# 3. Kolejne wdrożenie (bez DJCMS_ENABLE i bez danych konta): nic się nie dubluje ani nie zmienia.
# ================================================================================================
cp "$SRV/.env" "$WORK/env.after-on"
run_deploy "$DEPLOY" again
rc=$?
check "kolejne wdrożenie z włączonym dj. kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/again.out"
cmp -s "$WORK/env.after-on" "$SRV/.env"
check "kolejne wdrożenie nie zmienia .env (sekrety, znaczniki, COMPOSE_*)" $?
grep -q 'compose up -d --remove-orphans .* proxy djcms$' "$WORK/again.docker" &&
  grep -q 'setup_djcms_groups' "$WORK/again.docker" && grep -qx 'compose exec -T djcms python manage.py sync_competitions --import-missing' "$WORK/again.docker"
check "kolejne wdrożenie startuje djcms, odświeża grupę redaktorów i rejestr konkursów" $?
! grep -qE 'bootstrap_djcms_admin|import_cms_bundle' "$WORK/again.docker"
check "bez DJCMS_ADMIN_* nie ma zakładania konta, po imporcie – nie ma drugiego importu" $?

# ================================================================================================
# 4. Nieudany pierwszy import: wdrożenie kończy się błędem PO krokach głównego serwisu, znacznik
#    zostaje `pending`, a następne wdrożenie próbuje ponownie.
# ================================================================================================
reset_server
run_deploy "$DEPLOY" failimp DJCMS_ENABLE=1 STUB_IMPORT_RC=1
rc=$?
[ $rc -ne 0 ]
check "nieudany import = wdrożenie z kodem ≠ 0" $?
[ "$(env_line DJCMS_INITIAL_IMPORT)" = "pending" ]
check "po nieudanym imporcie DJCMS_INITIAL_IMPORT zostaje pending" $?
[ -f "$SRV/mail-dns.txt" ] && grep -q 'check_domains' "$WORK/failimp.docker"
check "błąd dj. nie zatrzymał kroków głównego serwisu (7/8, kontrola domen)" $?
grep -q 'DJCMS_ADMIN_EMAIL' "$WORK/failimp.out"
check "komunikat podpowiada DJCMS_ADMIN_EMAIL/PASSWORD" $?
run_deploy "$DEPLOY" retry
[ "$(env_line DJCMS_INITIAL_IMPORT)" = "done" ] && grep -q 'import_cms_bundle' "$WORK/retry.docker"
check "kolejne wdrożenie ponawia import i oznacza go jako wykonany" $?

# ================================================================================================
# 5. Wartości, których wdrożenie nie rozumie – odmowa, zanim cokolwiek dotknie serwera.
# ================================================================================================
for bad in 0 tak off 2; do
  reset_server
  run_deploy "$DEPLOY" bad DJCMS_ENABLE="$bad"
  rc=$?
  [ $rc -ne 0 ] && [ ! -s "$WORK/bad.ssh" ]
  check "DJCMS_ENABLE=„$bad” – odmowa bez żadnego ssh" $?
done
# Zła wartość w .env zatrzymuje generator Caddy'ego w kroku 4/8 – przed budowaniem czegokolwiek.
reset_server
{ cat "$WORK/env.fixture"; echo "DJCMS_ENABLED=tak"; } >"$SRV/.env"
run_deploy "$DEPLOY" badenv
rc=$?
[ $rc -ne 0 ] && ! grep -q 'build' "$WORK/badenv.docker"
check "DJCMS_ENABLED=tak w .env – wdrożenie staje w kroku 4/8, nic nie jest budowane" $?

# Ręcznie wpisane sekrety, które nie działałyby: krótki token, hasło ze znakiem spoza listy.
for bad_line in "DJCMS_INTERNAL_TOKEN=krotki" "DJCMS_DB_PASSWORD=abc@def:0123456789xyz"; do
  reset_server
  { cat "$WORK/env.fixture"; echo "DJCMS_ENABLED=1"; echo "$bad_line"; } >"$SRV/.env"
  run_deploy "$DEPLOY" badsecret
  rc=$?
  [ $rc -ne 0 ] && ! grep -q 'build' "$WORK/badsecret.docker" && grep -qx "$bad_line" "$SRV/.env"
  check "„${bad_line%%=*}” nie do użytku – odmowa przed budowaniem, wartość nietknięta" $?
done

# Pusta linijka z .env.example jest zastępowana wygenerowaną wartością (a nie zostawiana obok).
reset_server
{ cat "$WORK/env.fixture"; printf 'DJCMS_ENABLED=1\nDJCMS_SECRET_KEY=\nDJCMS_DB_PASSWORD=\n'; } >"$SRV/.env"
run_deploy "$DEPLOY" emptyline
[ "$(grep -c '^DJCMS_SECRET_KEY=' "$SRV/.env")" = "1" ] && env_line DJCMS_SECRET_KEY | grep -qE '^[A-Za-z0-9]{64}$'
check "pusta linijka DJCMS_SECRET_KEY= zastąpiona wygenerowanym kluczem" $?

# Istniejące COMPOSE_* operatora: dopisany profil jest akceptowany, obcy – zatrzymuje z instrukcją.
reset_server
{ cat "$WORK/env.fixture"; printf 'DJCMS_ENABLED=1\nCOMPOSE_PROFILES=monitoring\n'; } >"$SRV/.env"
run_deploy "$DEPLOY" profiles
rc=$?
[ $rc -ne 0 ] && grep -q 'COMPOSE_PROFILES' "$WORK/profiles.out" && ! grep -q 'build' "$WORK/profiles.docker"
check "COMPOSE_PROFILES bez djcms – odmowa z instrukcją, przed budowaniem" $?
reset_server
{ cat "$WORK/env.fixture"; printf 'DJCMS_ENABLED=1\nCOMPOSE_PROFILES=monitoring,djcms\n'; } >"$SRV/.env"
run_deploy "$DEPLOY" profiles2
check "COMPOSE_PROFILES=monitoring,djcms – akceptowane" $?
[ "$(grep -c '^COMPOSE_PROFILES=' "$SRV/.env")" = "1" ]
check "…i nie dublowane" $?

# ================================================================================================
# 6. DJCMS_IMAGE: obraz djcms z rejestru zamiast budowania, wpis w .env; powrót sprząta wpis.
# ================================================================================================
reset_server
run_deploy "$DEPLOY" img DJCMS_ENABLE=1 DJCMS_IMAGE=ghcr.io/qaif/olimpiada-djcms:v9.9.9
grep -qx 'compose --profile djcms pull djcms' "$WORK/img.docker" && ! grep -q 'build --pull djcms' "$WORK/img.docker" &&
  [ "$(env_line DJCMS_IMAGE)" = "ghcr.io/qaif/olimpiada-djcms:v9.9.9" ]
check "DJCMS_IMAGE: pobranie z rejestru i wpis w .env" $?
run_deploy "$DEPLOY" img2
grep -qx 'compose --profile djcms build --pull djcms' "$WORK/img2.docker" && ! grep -q '^DJCMS_IMAGE=' "$SRV/.env"
check "wdrożenie bez DJCMS_IMAGE wraca do budowania i kasuje wpis" $?

# ================================================================================================
# 7. --maintenance: djcms zatrzymany razem z aplikacją, przed startem bazy i kopią z kroku 4a.
#    (Sam krok 4/8 wycięty z deploy.sh, jak w deploy_image_source_test.sh – pełny przebieg
#    z --maintenance wymagałby atrapy proxy z montażem strony prac technicznych.)
# ================================================================================================
awk '/log "4\/8 Konfiguracja proxy/ { seen = 1 }
     seen && /bash -s"? <<.REMOTE.$/ { inside = 1; next }
     inside && /^REMOTE$/ { exit }
     inside { print }' "$DEPLOY" >"$WORK/krok4.sh"
krok4() {  # krok4 <etykieta> [ZMIENNA=wartość…]
  local label="$1"; shift
  DOCKER_LOG="$WORK/$label.docker"; : >"$DOCKER_LOG"
  ( cd "$SRV" && env PATH="$BIN:$PATH" DOCKER_LOG="$DOCKER_LOG" STUB_BOX="$BOX" REMOTE_DIR="$SRV" WEB_IMAGE= MAINTENANCE=1 \
      MAINTENANCE_MESSAGE="Test." MAINTENANCE_MINUTES=10 "$@" bash "$WORK/krok4.sh" ) </dev/null >"$WORK/$label.out" 2>&1
}
reset_server
tar -C "$SRV" -xf "$WORK/tree.tar"
krok4 m-off
grep -qx 'compose stop web worker beat' "$WORK/m-off.docker" && ! grep -q djcms "$WORK/m-off.docker"
check "--maintenance bez dj.: zatrzymanie aplikacji jak dotąd, bez djcms" $?
rm -f "$SRV/maintenance/on" "$SRV/maintenance/.deploy-maintenance-on"
krok4 m-on DJCMS_ENABLE=1
stop_app="$(grep -nx 'compose stop web worker beat' "$WORK/m-on.docker" | cut -d: -f1)"
stop_dj="$(grep -nx 'compose stop djcms' "$WORK/m-on.docker" | cut -d: -f1)"
up_db="$(grep -nx 'compose up -d db' "$WORK/m-on.docker" | cut -d: -f1)"
build_dj="$(grep -nx 'compose --profile djcms build --pull djcms' "$WORK/m-on.docker" | cut -d: -f1)"
[ -n "$stop_dj" ] && [ -n "$build_dj" ] && [ "$build_dj" -lt "$stop_app" ] && [ "$stop_app" -lt "$stop_dj" ] && [ "$stop_dj" -lt "$up_db" ]
check "--maintenance z dj.: build djcms -> stop web/worker/beat -> stop djcms -> up -d db" $?

# ================================================================================================
# 8. Nakładka compose'a wymieniona w .env istnieje w drzewie, które wdrożenie rozpakowuje.
# ================================================================================================
tar -tf "$WORK/tree.tar" | grep -qx 'docker-compose.djcms.yml' && git -C "$ROOT" ls-files --error-unmatch docker-compose.djcms.yml >/dev/null 2>&1
rc=$?
check "docker-compose.djcms.yml jest w repozytorium (git archive HEAD go zabierze)" $rc
[ $rc -eq 0 ] || printf '     (plik jeszcze nie dodany do gita – `git add docker-compose.djcms.yml`)\n'

# ================================================================================================
# 9. DJCMS_PRIMARY (serwis publiczny na djcms, DJ-02 § 3, § 10.2): generator czyta go z .env w kroku
#    4/8, wdrożenie trybu nie zmienia, przy PRIMARY=1 kończy kontrolą dymną djcms_switch.sh check.
# ================================================================================================
# 9a. PRIMARY=1 bez DJCMS_ENABLED – sprzeczność, odmowa w kroku 4/8 przed budowaniem czegokolwiek.
reset_server
{ cat "$WORK/env.fixture"; echo "DJCMS_PRIMARY=1"; } >"$SRV/.env"
run_deploy "$DEPLOY" prim-noenable
rc=$?
[ $rc -ne 0 ] && ! grep -q 'build' "$WORK/prim-noenable.docker" && grep -qF 'DJCMS_PRIMARY=1 wymaga DJCMS_ENABLED=1' "$WORK/prim-noenable.out"
check "DJCMS_PRIMARY=1 bez DJCMS_ENABLED=1 – wdrożenie staje w kroku 4/8, nic nie jest budowane" $?
# 9b. PRIMARY=0 wprost przy wyłączonym dj. – polecenia dzisiejsze, .env i Caddyfile nietknięte.
reset_server
installed_proxy_cfg
{ cat "$WORK/env.fixture"; echo "DJCMS_PRIMARY=0"; } >"$SRV/.env"
cp "$SRV/.env" "$WORK/env.p0"
run_deploy "$DEPLOY" prim-zero
[ "$(cat "$WORK/prim-zero.docker")" = "$DZISIAJ" ] && cmp -s "$WORK/env.p0" "$SRV/.env" &&
  cmp -s "$ROOT/deploy/Caddyfile" "$SRV/caddy/Caddyfile"
check "DJCMS_PRIMARY=0 przy wyłączonym dj.: polecenia dzisiejsze, .env i Caddyfile bez zmian" $?
# 9c. Włączone dj. i PRIMARY=1: tryb primary w konfiguracji proxy, kontrola dymna przez proxy.
reset_server
run_deploy "$DEPLOY" prim-enable DJCMS_ENABLE=1
# Przełączenie jak `djcms_switch.sh on` (jego testy: djcms_switch_test.sh): .env i plik w działającym
# proxy w trybie primary. Samo dopisanie DJCMS_PRIMARY=1 do .env wdrożenie odrzuca (9f).
{ echo; echo "DJCMS_PRIMARY=1"; } >>"$SRV/.env"
( cd "$SRV" && env -u EXTRA_DOMAINS -u PLATFORM_SUBDOMAINS -u DJCMS_ENABLED -u DJCMS_PRIMARY -u DJCMS_ROUTES_ENV \
    bash scripts/render_caddyfile.sh >/dev/null ) && cp "$SRV/caddy/Caddyfile" "$BOX/loaded"
cp "$SRV/.env" "$WORK/env.prim"
run_deploy "$DEPLOY" prim STUB_CURL_MODE=primary
rc=$?
check "wdrożenie przy DJCMS_PRIMARY=1 i działającym djcms kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/prim.out"
grep -q 'header_up X-Djcms-Mode primary' "$SRV/caddy/Caddyfile" && cmp -s "$WORK/env.prim" "$SRV/.env"
check "PRIMARY=1: konfiguracja proxy w trybie primary, .env nietknięty (wdrożenie trybu nie zmienia)" $?
grep -qE ' https://olimpiada\.example/$' "$WORK/prim.docker.curl" && grep -qE ' https://olimpiada\.example/login/$' "$WORK/prim.docker.curl" &&
  grep -qF 'Serwis publiczny: django CMS (DJCMS_PRIMARY=1)' "$WORK/prim.out"
check "PRIMARY=1: kontrola dymna (/ z djcms, /login/ z web) i komunikat o trybie" $?
! grep -q 'force-recreate' "$WORK/prim.docker" && grep -q 'caddy reload' "$WORK/prim.docker" &&
  grep -q 'header_up X-Djcms-Mode primary' "$BOX/loaded"
check "tryb primary załadowany przez caddy reload – bez odtwarzania kontenera" $?
run_deploy "$DEPLOY" prim-bad
rc=$?
[ $rc -ne 0 ] && grep -qF 'djcms_switch.sh off' "$WORK/prim-bad.out" && [ "$(env_line DJCMS_PRIMARY)" = 1 ] &&
  grep -q 'check_domains' "$WORK/prim-bad.docker"
check "PRIMARY=1, a strony publiczne nie z djcms: kod ≠ 0 z podpowiedzią off, tryb bez zmian, kroki główne wykonane" $?
# 9d. Nieudane uzgodnienie rejestru konkursów – kod ≠ 0 po krokach głównego serwisu, bez importu.
run_deploy "$DEPLOY" sync-bad STUB_SYNC_RC=1 STUB_CURL_MODE=primary
rc=$?
[ $rc -ne 0 ] && grep -q 'check_domains' "$WORK/sync-bad.docker" && ! grep -q 'import_cms_bundle' "$WORK/sync-bad.docker"
check "nieudane sync_competitions: kod ≠ 0, po krokach głównego serwisu, bez importu" $?
# 9e. Obraz djcms sprzed DJ-02e (bez `--import-missing`, np. DJCMS_IMAGE z rejestru w starszej wersji):
#     wdrożenie woła samo `sync_competitions` – nieznany argument nie może go zatrzymać.
run_deploy "$DEPLOY" sync-old STUB_SYNC_OLD=1 STUB_CURL_MODE=primary
rc=$?
[ $rc -eq 0 ] && grep -qx 'compose exec -T djcms python manage.py sync_competitions' "$WORK/sync-old.docker" &&
  ! grep -q -- 'sync_competitions --import-missing' "$WORK/sync-old.docker"
rc=$?
check "obraz djcms bez --import-missing: samo sync_competitions, wdrożenie przechodzi" $rc
show_on_fail $rc "$WORK/sync-old.out"
grep -qx 'compose exec -T djcms python manage.py sync_competitions --help' "$WORK/prim.docker" &&
  grep -qx 'compose exec -T djcms python manage.py sync_competitions --import-missing' "$WORK/prim.docker"
check "obraz djcms z --import-missing: flaga sprawdzona (--help) i użyta" $?
grep -qx 'compose exec -T web python manage.py djcms_routes --format env' "$WORK/prim.docker"
check "PRIMARY=1: krok dj. porównuje kontrakt tras hosta z obrazem web" $?
# 9f. Kontrakt tras obrazu web inny niż kod na serwerze (np. WEB_IMAGE z rejestru w innej wersji) –
#     kod ≠ 0 po krokach głównego serwisu, przed kontrolą dymną, z podpowiedzią off.
run_deploy "$DEPLOY" routes-diff STUB_ROUTES_DIFF=1 STUB_CURL_MODE=primary
rc=$?
[ $rc -ne 0 ] && grep -qF 'różni się od kontraktu tras obrazu web' "$WORK/routes-diff.out" &&
  grep -q 'check_domains' "$WORK/routes-diff.docker" && ! grep -q -- "-o /dev/null -D -" "$WORK/routes-diff.docker.curl"
check "PRIMARY=1, kontrakt tras obrazu web ≠ kod: kod ≠ 0 z komunikatem, bez kontroli dymnej" $?
# 9g. .env w innym trybie niż działające proxy (przerwane `djcms_switch.sh on|off` albo ręczna zmiana
#     DJCMS_PRIMARY): wdrożenie staje w kroku 4/8, zanim cokolwiek zbuduje – dokończyłoby przełączenie
#     bez kontroli dymnej.
sed -i 's/^DJCMS_PRIMARY=1$/DJCMS_PRIMARY=0/' "$SRV/.env"
cp "$SRV/caddy/Caddyfile" "$WORK/caddy.mode"
run_deploy "$DEPLOY" mode-diff
rc=$?
[ $rc -ne 0 ] && ! grep -q 'build' "$WORK/mode-diff.docker" && grep -qF 'djcms_switch.sh off' "$WORK/mode-diff.out" &&
  cmp -s "$WORK/caddy.mode" "$SRV/caddy/Caddyfile"
check "DJCMS_PRIMARY w .env ≠ tryb działającego proxy: odmowa w kroku 4/8 (djcms_switch.sh off), nic nie zbudowane" $?
sed -i 's/^DJCMS_PRIMARY=0$/DJCMS_PRIMARY=1/' "$SRV/.env"

# 9h. Blokada zmian serwisu: sesja w tle od kroku 2/8 (przed kasowaniem katalogu), kroki proxy z
#     OLIMPIADA_PROXY_LOCK=held (inaczej czekałyby na blokadę wdrożenia).
l="$(grep -nF "/caddy/.lock'" "$WORK/prim.ssh" | head -n 1 | cut -d: -f1)"
f="$(grep -nF -- "-exec rm -rf" "$WORK/prim.ssh" | head -n 1 | cut -d: -f1)"
[ -n "$l" ] && [ -n "$f" ] && [ "$l" -lt "$f" ] && grep -q 'OLIMPIADA_PROXY_LOCK=held.* bash -s$' "$WORK/prim.ssh" &&
  grep -qF 'OLIMPIADA_PROXY_LOCK=held bash scripts/proxy_config.sh apply' "$WORK/prim.ssh" &&
  grep -qE '^(blokada zmian serwisu .* wzięta|UWAGA: brak flock na serwerze)' "$WORK/prim.out"
check "blokada zmian serwisu: sesja przed krokiem 2/8, kroki 4/8 i 4c/8 z OLIMPIADA_PROXY_LOCK=held" $?

# ================================================================================================
# 10. Konfiguracja proxy przy KAŻDYM wdrożeniu (docs/OPERACJE.md § 23), bez dj.: zmiana
#     deploy/Caddyfile dochodzi do działającego proxy przez `caddy reload`, bez odtwarzania
#     kontenera. Błąd sprzed tej zmiany: krok 2/8 kasował deploy/, proxy trzymało stary i-węzeł
#     montowanego pliku, a `up -d` go nie odtwarzało – nowa treść nie docierała nigdy.
# ================================================================================================
line_no() { grep -nxF -- "$2" "$1" | head -n 1 | cut -d: -f1; }   # line_no <log> <polecenie>
VALIDATE_CMD='compose exec -T proxy sh -c cat > /tmp/Caddyfile.next && caddy validate --config /tmp/Caddyfile.next --adapter caddyfile'
RELOAD_CMD='compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile'
UP_CMD='compose up -d --remove-orphans db redis minio minio-init clamav mail web worker beat proxy'

# Drugie wydanie: deploy/Caddyfile z nowym nagłówkiem (fragment niczego nie zmienia w routingu –
# chodzi o inną treść pliku, jak przy każdej zmianie nagłówków, tras albo domen).
mkdir -p "$WORK/tree2"
tar -C "$WORK/tree2" -xf "$WORK/tree.tar"
printf '\n(release_marker) {\n\theader X-Release-Marker 2\n}\n' >>"$WORK/tree2/deploy/Caddyfile"
tar -C "$WORK/tree2" -cf "$WORK/tree2.tar" scripts deploy docker-compose.yml docker-compose.djcms.yml backend

# 10a. Pierwsze wydanie, potem drugie – proxy cały czas działa (montaż katalogu caddy/).
reset_server
echo live >"$BOX/state"
run_deploy "$DEPLOY" rel1
check "wydanie 1: wdrożenie kończy się powodzeniem" $?
touch "$SRV/caddy/.znacznik" "$SRV/deploy/.znacznik"
cp "$SRV/caddy/Caddyfile" "$WORK/caddy.rel1"
SRC_TAR="$WORK/tree2.tar" run_deploy "$DEPLOY" rel2
rc=$?
check "wydanie 2 (zmieniony deploy/Caddyfile): wdrożenie kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/rel2.out"
grep -q 'X-Release-Marker' "$SRV/caddy/Caddyfile" && cmp -s "$SRV/caddy/Caddyfile" "$WORK/tree2/deploy/Caddyfile"
check "caddy/Caddyfile = nowy deploy/Caddyfile" $?
cmp -s "$SRV/caddy/Caddyfile" "$BOX/loaded"
check "działające proxy ma załadowaną NOWĄ konfigurację" $?
grep -qxF "$RELOAD_CMD" "$WORK/rel2.docker" && ! grep -q 'force-recreate' "$WORK/rel2.docker"
check "…przez caddy reload, bez odtwarzania kontenera proxy" $?
[ -e "$SRV/caddy/.znacznik" ] && [ ! -e "$SRV/deploy/.znacznik" ]
check "krok 2/8 omija katalog caddy/ (a deploy/ rozpakowuje od nowa)" $?
v="$(line_no "$WORK/rel2.docker" "$VALIDATE_CMD")"; b="$(line_no "$WORK/rel2.docker" 'compose build --pull web')"
u="$(line_no "$WORK/rel2.docker" "$UP_CMD")"; r="$(line_no "$WORK/rel2.docker" "$RELOAD_CMD")"
[ -n "$v" ] && [ -n "$r" ] && [ "$v" -lt "$b" ] && [ "$u" -lt "$r" ] && cmp -s "$BOX/validated" "$SRV/caddy/Caddyfile"
check "kolejność: caddy validate nowej treści (przed buildem) -> start usług -> caddy reload" $?
[ ! -e "$SRV/caddy/Caddyfile.next" ]
check "po wdrożeniu nie zostaje caddy/Caddyfile.next" $?

# 10b. Proxy, które widzi inną treść (montaż sprzed CADDY_CONFIG_DIR) – krok 4c/8 odtwarza kontener.
echo stale >"$BOX/state"
SRC_TAR="$WORK/tree2.tar" run_deploy "$DEPLOY" stale
rc=$?
check "proxy ze starym montażem: wdrożenie kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/stale.out"
grep -qx 'compose up -d --force-recreate --no-deps proxy' "$WORK/stale.docker" && ! grep -qxF "$RELOAD_CMD" "$WORK/stale.docker" &&
  [ "$(cat "$BOX/state")" = live ] && cmp -s "$SRV/caddy/Caddyfile" "$BOX/loaded"
check "proxy ze starym montażem: odtworzone w 4c/8 i widzi bieżący plik" $?
grep -qF 'odtwarzam proxy' "$WORK/stale.out"
check "…i log mówi o odtworzeniu (kilka sekund bez HTTPS)" $?

# 10c. .env sprzed tej zmiany (CADDYFILE_PATH z komentarzem, który wpisywało wdrożenie): wpis znika,
#      CADDY_CONFIG_DIR=./caddy dopisany raz, reszta pliku nietknięta; kolejne wdrożenie .env nie zmienia.
awk '/^CADDY_CONFIG_DIR=/ {
       print "# Konfiguracja proxy montowana do kontenera: plik składany z deploy/Caddyfile"
       print "# i EXTRA_DOMAINS przez scripts/render_caddyfile.sh (patrz docker-compose.yml)."
       print "CADDYFILE_PATH=./deploy/Caddyfile.generated"; next } { print }' "$WORK/env.fixture" >"$WORK/env.legacy"
reset_server "$WORK/env.legacy"
echo stale >"$BOX/state"
mkdir -p "$SRV/deploy"; cp "$ROOT/deploy/Caddyfile" "$SRV/deploy/Caddyfile.generated"
run_deploy "$DEPLOY" legacy
rc=$?
check "migracja z CADDYFILE_PATH: wdrożenie kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/legacy.out"
! grep -qE '^CADDYFILE_PATH=|^# Konfiguracja proxy montowana|^# i EXTRA_DOMAINS przez scripts/render' "$SRV/.env" &&
  [ "$(grep -c '^CADDY_CONFIG_DIR=' "$SRV/.env")" = 1 ] && [ "$(env_line CADDY_CONFIG_DIR)" = ./caddy ]
check "migracja: CADDYFILE_PATH z komentarzem usunięty, CADDY_CONFIG_DIR=./caddy dopisany raz" $?
diff <(grep -vE '^(CADDYFILE_PATH=|CADDY_CONFIG_DIR=|# Konfiguracja proxy|# i EXTRA_DOMAINS przez|# przez scripts/proxy_config)' "$WORK/env.legacy") \
  <(grep -vE '^(CADDY_CONFIG_DIR=|# Konfiguracja proxy|# przez scripts/proxy_config)' "$SRV/.env") >/dev/null
check "migracja: pozostałe linijki .env nietknięte" $?
[ "$(cat "$BOX/state")" = live ] && cmp -s "$SRV/caddy/Caddyfile" "$BOX/loaded" && [ ! -e "$SRV/deploy/Caddyfile.generated" ]
check "migracja: proxy widzi i ma załadowany caddy/Caddyfile, stary deploy/Caddyfile.generated zniknął" $?
grep -qx '# stara treść (montaż sprzed CADDY_CONFIG_DIR)' "$SRV/caddy/Caddyfile.prev" &&
  [ "$(line_no "$WORK/legacy.docker" 'compose exec -T proxy cat /etc/caddy/Caddyfile')" -lt "$(line_no "$WORK/legacy.docker" "$VALIDATE_CMD")" ]
check "migracja: najpierw kopia konfiguracji działającego proxy (punkt powrotu w caddy/Caddyfile.prev), potem validate" $?
cp "$SRV/.env" "$WORK/env.migrated"
run_deploy "$DEPLOY" legacy2
cmp -s "$WORK/env.migrated" "$SRV/.env" && grep -qxF "$RELOAD_CMD" "$WORK/legacy2.docker" && ! grep -q 'force-recreate' "$WORK/legacy2.docker"
check "po migracji: kolejne wdrożenie nie zmienia .env i przeładowuje proxy bez odtwarzania" $?

# 10d. CADDYFILE_PATH wpisany ręcznie (własny plik proxy) – odmowa przed budowaniem, .env nietknięty.
sed 's|^CADDYFILE_PATH=.*|CADDYFILE_PATH=./moj/Caddyfile|' "$WORK/env.legacy" >"$WORK/env.custom"
reset_server "$WORK/env.custom"
echo live >"$BOX/state"
run_deploy "$DEPLOY" custom
rc=$?
[ $rc -ne 0 ] && cmp -s "$WORK/env.custom" "$SRV/.env" && ! grep -q 'build' "$WORK/custom.docker" &&
  grep -qF 'CADDYFILE_PATH' "$WORK/custom.out"
check "własny CADDYFILE_PATH: odmowa z instrukcją, przed budowaniem, .env nietknięty" $?

# 10e. caddy validate odrzuca nowy plik – wdrożenie staje w kroku 4/8: nic nie zbudowane ani
#      zatrzymane, zainstalowany plik i konfiguracja w proxy bez zmian; przy --maintenance strona
#      nie zostaje włączona.
for flags in "" "--maintenance"; do
  reset_server
  echo live >"$BOX/state"
  run_deploy "$DEPLOY" val0
  cp "$SRV/caddy/Caddyfile" "$WORK/caddy.before"; cp "$BOX/loaded" "$WORK/loaded.before"
  SRC_TAR="$WORK/tree2.tar" DEPLOY_FLAGS="$flags" run_deploy "$DEPLOY" val STUB_VALIDATE_RC=1
  rc=$?
  [ $rc -ne 0 ] && ! grep -qE 'build|compose stop|compose up' "$WORK/val.docker" &&
    cmp -s "$WORK/caddy.before" "$SRV/caddy/Caddyfile" && cmp -s "$WORK/loaded.before" "$BOX/loaded" &&
    [ ! -e "$SRV/caddy/Caddyfile.next" ] && [ ! -e "$SRV/maintenance/on" ] && grep -qF 'caddy validate odrzucił' "$WORK/val.out"
  check "odrzucony caddy validate${flags:+ ($flags)}: kod ≠ 0 w kroku 4/8, nic nie zbudowane, plik i proxy bez zmian${flags:+, strona nie włączona}" $?
done

# 10f. --maintenance z nowym plikiem: strona włączona przed zatrzymaniem aplikacji, reload po starcie
#      usług i PRZED kontrolą z przepustką (5a), strona wyłączona na końcu.
reset_server
echo live >"$BOX/state"
run_deploy "$DEPLOY" m0
SRC_TAR="$WORK/tree2.tar" DEPLOY_FLAGS=--maintenance run_deploy "$DEPLOY" mnt
rc=$?
check "--maintenance ze zmienionym deploy/Caddyfile: wdrożenie kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/mnt.out"
v="$(line_no "$WORK/mnt.docker" "$VALIDATE_CMD")"; s="$(line_no "$WORK/mnt.docker" 'compose stop web worker beat')"
u="$(line_no "$WORK/mnt.docker" "$UP_CMD")"; r="$(line_no "$WORK/mnt.docker" "$RELOAD_CMD")"
o4c="$(grep -n '^==> 4c/8' "$WORK/mnt.out" | cut -d: -f1)"; o5a="$(grep -n '^==> 5a/8' "$WORK/mnt.out" | cut -d: -f1)"
[ -n "$v" ] && [ -n "$s" ] && [ -n "$r" ] && [ "$v" -lt "$s" ] && [ "$s" -lt "$u" ] && [ "$u" -lt "$r" ] &&
  [ -n "$o4c" ] && [ -n "$o5a" ] && [ "$o4c" -lt "$o5a" ]
check "--maintenance: validate -> stop aplikacji -> start usług -> caddy reload -> kontrola z przepustką (5a)" $?
[ ! -e "$SRV/maintenance/on" ] && cmp -s "$SRV/caddy/Caddyfile" "$BOX/loaded" && grep -q 'X-Maintenance-Bypass' "$WORK/mnt.docker.curl"
check "--maintenance: nowa konfiguracja załadowana, strona prac technicznych wyłączona po kontroli" $?

# 10g. caddy reload odrzuca plik (np. inna wersja Caddy'ego niż ta, która walidowała) – kod ≠ 0 zaraz
#      po starcie usług; przy --maintenance strona zostaje włączona i log mówi to głośno.
reset_server
echo live >"$BOX/state"
run_deploy "$DEPLOY" rl0
SRC_TAR="$WORK/tree2.tar" DEPLOY_FLAGS=--maintenance run_deploy "$DEPLOY" rl STUB_RELOAD_RC=1
rc=$?
[ $rc -ne 0 ] && grep -qF 'caddy reload odrzucił' "$WORK/rl.out" && ! grep -q 'seed_edition_kwantowa' "$WORK/rl.docker" &&
  [ -e "$SRV/maintenance/on" ] && grep -qF 'PRACE TECHNICZNE' "$WORK/rl.out" &&
  ! grep -q 'X-Release-Marker' "$SRV/caddy/Caddyfile" && ! grep -q 'X-Release-Marker' "$BOX/loaded"
check "odrzucony caddy reload przy --maintenance: kod ≠ 0 przed kontrolą 5a, strona zostaje włączona z komunikatem, caddy/Caddyfile wraca do poprzedniej treści" $?

# ================================================================================================
# 11. Kontrola dymna i wycofanie (OPS-04, docs/OPERACJE.md § 48): porażka kontroli po wdrożeniu
#     nowej wersji – bez nowych migracji powrót do obrazów :previous, z nowymi – bez wycofania;
#     w obu przypadkach kod 1 i żadnych seedów. Furtka DEPLOY_SMOKE=warn|0.
# ================================================================================================
ROLLBACK_UP='compose up -d --no-deps --no-build web worker beat'
reset_server
installed_proxy_cfg
run_deploy "$DEPLOY" sm-ok APP_VERSION=v2
rc=$?
check "OPS-04: udane wdrożenie v2 – kod 0, kontrola dymna przeszła" $rc
show_on_fail $rc "$WORK/sm-ok.out"
grep -q '^==> 2a/8 ' "$WORK/sm-ok.out" && grep -q '^==> 5b/8 ' "$WORK/sm-ok.out" &&
  grep -qE '^smoke: [0-9]+ sprawdzeń, błędów: 0, ostrzeżeń: 0' "$WORK/sm-ok.out"
check "OPS-04: log ma migawkę (2a/8) i kontrolę dymną (5b/8) bez błędów i ostrzeżeń (wersja v2 w /status.json)" $?
[ "$(sed -n 's/^PREV_APP_VERSION=//p' "$SRV/deploy-state/previous.env")" = vtest ] &&
  [ "$(sed -n 's/^APP_VERSION=//p' "$SRV/deploy-state/deployed.env")" = v2 ] && [ -s "$SRV/deploy-state/last-smoke.txt" ]
check "OPS-04: deploy-state – migawka sprzed wdrożenia (vtest), zapis udanego (v2), wydruk kontroli" $?
grep -q -- "--resolve olimpiada.example:443:127.0.0.1" "$WORK/sm-ok.docker.curl" && ! grep -qF "$ROLLBACK_UP" "$WORK/sm-ok.docker"
check "OPS-04: kontrola przez proxy serwera (--resolve 127.0.0.1), bez wycofania" $?
l2a="$(grep -n 'scripts/rollback.sh snapshot' "$WORK/sm-ok.ssh" | cut -d: -f1)"
l3="$(grep -n 'SITE_DOMAIN=' "$WORK/sm-ok.ssh" | head -n 1 | cut -d: -f1)"
lrm="$(grep -nF -- '! -name deploy-state -exec rm -rf' "$WORK/sm-ok.ssh" | cut -d: -f1)"
[ -n "$l2a" ] && [ -n "$l3" ] && [ -n "$lrm" ] && [ "$lrm" -lt "$l2a" ] && [ "$l2a" -lt "$l3" ]
check "OPS-04: krok 2/8 omija deploy-state, migawka po rozpakowaniu kodu i PRZED krokiem 3/8 (APP_VERSION)" $?

pcc="$(line_no "$WORK/sm-ok.docker" 'compose exec -T web python manage.py page_cache_clear')"
upn="$(line_no "$WORK/sm-ok.docker" "$UP_CMD")"
smk="$(line_no "$WORK/sm-ok.docker" 'compose exec -T web python manage.py check_domains --hosts')"
[ -n "$pcc" ] && [ -n "$upn" ] && [ -n "$smk" ] && [ "$upn" -lt "$pcc" ] && [ "$pcc" -lt "$smk" ]
check "OPS-04: bufor stron gościa czyszczony po starcie nowej wersji (collectstatic), przed kontrolą dymną" $?
reset_server
installed_proxy_cfg
run_deploy "$DEPLOY" sm-pcc APP_VERSION=v2 STUB_PCC_RC=1
[ $? = 0 ] && grep -qF 'page_cache_clear nieudane' "$WORK/sm-pcc.out" && grep -q 'seed_edition_kwantowa' "$WORK/sm-pcc.docker"
check "OPS-04: nieudane page_cache_clear – ostrzeżenie, wdrożenie idzie dalej (klucz bufora zawiera wydanie)" $?

# 11a. Kontrola nie przechodzi na v2, bez nowych migracji → automatyczne wycofanie do vtest.
reset_server
installed_proxy_cfg
run_deploy "$DEPLOY" sm-rb APP_VERSION=v2 STUB_SMOKE_FAIL=v2
rc=$?
[ $rc = 1 ] && grep -qxF "$ROLLBACK_UP" "$WORK/sm-rb.docker" && grep -qx 'tag olimpiada/web:previous olimpiada/web:vtest' "$WORK/sm-rb.docker" &&
  [ "$(env_line APP_VERSION)" = vtest ]
rc=$?
check "OPS-04: kontrola nie przeszła, bez migracji → wycofanie web/worker/beat do vtest, APP_VERSION=vtest, kod 1" $rc
show_on_fail $rc "$WORK/sm-rb.out"
grep -qF 'NIEUDANE i WYCOFANE' "$WORK/sm-rb.out" && ! grep -q 'seed_edition_kwantowa' "$WORK/sm-rb.docker" &&
  grep -q 'shell -c' "$WORK/sm-rb.docker"
check "OPS-04: ramka w logu, list alarmowy, kroki 6–8 nie wykonane" $?
awk -v s="$ROLLBACK_UP" 'f { print } $0 == s { f = 1 }' "$WORK/sm-rb.docker" >"$WORK/sm-rb.after"
! grep -qE '(^| )(down|volume|rm|rmi|prune)( |$)| -v( |$)|pg_restore|compose up' "$WORK/sm-rb.after"
check "OPS-04: wycofanie bez down/-v/rm/pg_restore i bez drugiego up (baza i wolumeny nietknięte)" $?

# 11a'. Przegląd PR #80 (M1): nowy web nie wstaje → `docker compose up -d` w 4b kończy się błędem.
#       Wdrożenie nie urywa się pod `set -e`, tylko idzie do 5b: kontrola i rollback.sh auto – także
#       przy DEPLOY_SMOKE=0 i nawet gdy kontrola przypadkiem przechodzi (stary proxy/web odpowiada).
reset_server
installed_proxy_cfg
run_deploy "$DEPLOY" up-fail APP_VERSION=v2 STUB_UP_RC=1 DEPLOY_SMOKE=0
rc=$?
[ $rc = 1 ] && grep -qxF "$ROLLBACK_UP" "$WORK/up-fail.docker" && [ "$(env_line APP_VERSION)" = vtest ] &&
  ! grep -qxF "$RELOAD_CMD" "$WORK/up-fail.docker" && ! grep -q '^==> 5/8 ' "$WORK/up-fail.out" &&
  grep -q 'docker compose up -d zakończył się kodem 1' "$WORK/up-fail.out" &&
  grep -q 'powód porażki wdrożenia: docker compose up -d (krok 4b/8)' "$WORK/up-fail.out" &&
  ! grep -q 'seed_edition_kwantowa' "$WORK/up-fail.docker" && grep -qF 'NIEUDANE i WYCOFANE' "$WORK/up-fail.out"
rc=$?
check "M1: nieudane up -d (web nie healthy) → kroki 4c–5a pominięte, kontrola i automatyczne wycofanie do vtest, kod 1" $rc
show_on_fail $rc "$WORK/up-fail.out"
reset_server
installed_proxy_cfg
run_deploy "$DEPLOY" up-fail-mig APP_VERSION=v2 STUB_UP_RC=1 STUB_NEW_MIGRATION=1
rc=$?
[ $rc = 1 ] && ! grep -qxF "$ROLLBACK_UP" "$WORK/up-fail-mig.docker" && [ "$(env_line APP_VERSION)" = v2 ] &&
  grep -qF 'NIE wycofane' "$WORK/up-fail-mig.out"
rc=$?
check "M1: nieudane up -d po migracji → bez wycofania, kod 1, decyzja ręczna" $rc
show_on_fail $rc "$WORK/up-fail-mig.out"
reset_server
installed_proxy_cfg
DEPLOY_FLAGS=--maintenance run_deploy "$DEPLOY" up-fail-mnt APP_VERSION=v2 STUB_UP_RC=1
rc=$?
[ $rc = 1 ] && grep -qxF "$ROLLBACK_UP" "$WORK/up-fail-mnt.docker" && [ -e "$SRV/maintenance/on" ] &&
  grep -qF 'PRACE TECHNICZNE' "$WORK/up-fail-mnt.out" && ! grep -q '^==> 5a/8 ' "$WORK/up-fail-mnt.out"
rc=$?
check "M1 z --maintenance: wycofanie za stroną prac technicznych, strona zostaje włączona z komunikatem" $rc
show_on_fail $rc "$WORK/up-fail-mnt.out"

# 11b. Kontrola nie przechodzi, a wdrożenie zastosowało migrację → BEZ wycofania.
reset_server
installed_proxy_cfg
run_deploy "$DEPLOY" sm-mig APP_VERSION=v2 STUB_SMOKE_FAIL=v2 STUB_NEW_MIGRATION=1
rc=$?
[ $rc = 1 ] && ! grep -qF "$ROLLBACK_UP" "$WORK/sm-mig.docker" && [ "$(env_line APP_VERSION)" = v2 ] &&
  grep -qF 'results.0099_nowa_kolumna' "$WORK/sm-mig.out" && grep -qF 'NIE wycofane' "$WORK/sm-mig.out" &&
  ! grep -q 'seed_edition_kwantowa' "$WORK/sm-mig.docker" && [ ! -e "$SRV/maintenance/on" ]
rc=$?
check "OPS-04: kontrola nie przeszła po migracji → bez wycofania, kod 1, powód w logu, strona prac technicznych wyłączona" $rc
show_on_fail $rc "$WORK/sm-mig.out"

# 11c. DEPLOY_SMOKE=warn – porażka kontroli to ostrzeżenie, wdrożenie idzie dalej; DEPLOY_SMOKE=0 – bez kontroli.
reset_server
installed_proxy_cfg
run_deploy "$DEPLOY" sm-warn APP_VERSION=v2 STUB_SMOKE_FAIL=v2 DEPLOY_SMOKE=warn
rc=$?
[ $rc = 0 ] && ! grep -qF "$ROLLBACK_UP" "$WORK/sm-warn.docker" && grep -q 'seed_edition_kwantowa' "$WORK/sm-warn.docker" &&
  grep -qF 'DEPLOY_SMOKE=warn' "$WORK/sm-warn.out"
check "OPS-04: DEPLOY_SMOKE=warn – ostrzeżenie, bez wycofania, wdrożenie dokończone" $?
run_deploy "$DEPLOY" sm-off APP_VERSION=v3 DEPLOY_SMOKE=0
[ $? = 0 ] && [ ! -s "$WORK/sm-off.docker.curl" ] && grep -q 'POMINIĘTA (DEPLOY_SMOKE=0)' "$WORK/sm-off.out"
check "OPS-04: DEPLOY_SMOKE=0 – bez kontroli dymnej" $?
reset_server
run_deploy "$DEPLOY" sm-bad DEPLOY_SMOKE=tak
[ $? = 2 ] && [ ! -s "$WORK/sm-bad.ssh" ]
check "OPS-04: DEPLOY_SMOKE=„tak” – odmowa bez żadnego ssh" $?

# 11d. Ponawianie sync_competitions (dj.) przy „Lista konkursów z API niedostępna” – OPS-04 § 4.
reset_server
run_deploy "$DEPLOY" sync-on DJCMS_ENABLE=1
run_deploy "$DEPLOY" sync-retry STUB_SYNC_FAILS=2
rc=$?
[ $rc = 0 ] && [ "$(grep -cx 'compose exec -T djcms python manage.py sync_competitions --import-missing' "$WORK/sync-retry.docker")" = 3 ] &&
  grep -q 'lista konkursów z API niedostępna (próba 2) – ponawiam' "$WORK/sync-retry.out"
rc=$?
check "dj.: sync_competitions – dwa błędy API, trzecia próba przechodzi, wdrożenie z kodem 0" $rc
show_on_fail $rc "$WORK/sync-retry.out"
run_deploy "$DEPLOY" sync-giveup STUB_SYNC_FAILS=9
rc=$?
[ $rc -ne 0 ] && [ "$(grep -cx 'compose exec -T djcms python manage.py sync_competitions --import-missing' "$WORK/sync-giveup.docker")" = 4 ] &&
  grep -q 'niedostępna po 4 próbach' "$WORK/sync-giveup.out"
check "dj.: sync_competitions – po 4 próbach błąd z instrukcją" $?
run_deploy "$DEPLOY" sync-other STUB_SYNC_RC=1
rc=$?
[ $rc -ne 0 ] && [ "$(grep -cx 'compose exec -T djcms python manage.py sync_competitions --import-missing' "$WORK/sync-other.docker")" = 1 ]
check "dj.: inny błąd sync_competitions (np. importu) – bez ponawiania" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy wdrożenia dj. przeszły.\n'
