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
# djcms_db.sh, upgrade_postgres18.sh, maintenance.sh) są prawdziwe.
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
  *"compose ps"*) printf 'db=healthy\nweb=healthy\nproxy=healthy\ndjcms=healthy\n' ;;
  *"exec -T db psql"*"datname = 'olimpiada_djcms'"*) echo 1 ;;
  *"exec -T db psql"*"ON_ERROR_STOP"*) cat >>"$DOCKER_LOG.sql" ;;   # scripts/djcms_db.sh (SQL na stdin)
  *"exec -T db psql"*) echo 0 ;;
  *"exec -T db pg_dump"*) echo "PGDMP-atrapa" ;;
  *"import_cms_bundle"*) exit "${STUB_IMPORT_RC:-0}" ;;
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
chmod +x "$BIN/ssh" "$BIN/docker" "$BIN/git"

# Drzewo „z repozytorium”, które deploy rozpakowuje na serwerze: stan roboczy (także niezacommitowany).
tar -C "$ROOT" -cf "$WORK/tree.tar" --exclude='deploy/Caddyfile.generated' \
  scripts deploy docker-compose.yml docker-compose.djcms.yml

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
S3_PUBLIC_ENDPOINT_URL=https://olimpiada.example:9000

# Przepustka operatora
MAINTENANCE_BYPASS_TOKEN=0123456789abcdefghijklmnopqrstuvwxyzABCD

EXTRA_DOMAINS=

# PLATFORM_SUBDOMAINS=1
CADDYFILE_PATH=./deploy/Caddyfile.generated

BACKUP_PASSPHRASE=backuppassphrase0123456789abcdefghijklmnopqrstuv
ENV

reset_server() {  # czysty serwer z .env z fixture'a (albo z pliku podanego w $1)
  rm -rf "${SRV:?}" "${BAK:?}" "${WORK:?}/home"
  mkdir -p "$SRV" "$BAK" "$WORK/home"
  cp "${1:-$WORK/env.fixture}" "$SRV/.env"
}

run_deploy() {
  # run_deploy <plik deploy.sh> <etykieta> [ZMIENNA=wartość…] – jeden pełny przebieg; kod wyjścia.
  local script="$1" label="$2"; shift 2
  DOCKER_LOG="$WORK/$label.docker"; SSH_LOG="$WORK/$label.ssh"; OUT="$WORK/$label.out"
  : >"$DOCKER_LOG"; : >"$SSH_LOG"; rm -f "$DOCKER_LOG.sql"
  ( env -u DJCMS_ENABLE -u DJCMS_IMAGE -u DJCMS_ADMIN_EMAIL -u DJCMS_ADMIN_PASSWORD -u WEB_IMAGE \
      -u NEW_COMPETITION_SLUG -u COORDINATOR_EMAIL -u COORDINATOR_PASSWORD \
      PATH="$BIN:$PATH" DOCKER_LOG="$DOCKER_LOG" SSH_LOG="$SSH_LOG" SRC_TAR="${SRC_TAR:-$WORK/tree.tar}" \
      FAKE_HOME="$WORK/home" REMOTE_DIR="$SRV" BACKUP_DIR="$BAK" SSH_KEY=/dev/null \
      APP_VERSION=vtest MAIL_PUBLIC_IP=203.0.113.7 "$@" \
      bash "$script" root@test.invalid ) </dev/null >"$OUT" 2>&1
}

show_on_fail() {  # show_on_fail <kod> <plik> – przy porażce pokaż plik (wcięty)
  [ "$1" -eq 0 ] || sed 's/^/     /' "$2"
}

# Polecenia dockera pełnego wdrożenia bez dj. (bez WEB_IMAGE, bez --maintenance, bez nowego
# konkursu, bez seedów treści) – kroki 2–7/8 i porządki, w kolejności. Stan sprzed DJ-01.
DZISIAJ='compose config
volume inspect olimpiada_pg_data
compose build --pull web
compose up -d db
compose ps --format {{.Service}}={{.Health}}
compose ps --format {{.Service}}={{.Health}}
compose exec -T db pg_dump -U olimpiada -d olimpiada -Fc
compose up -d --remove-orphans db redis minio minio-init clamav mail web worker beat proxy
compose ps --format {{.Service}}={{.Health}}
compose exec -T web python manage.py seed_edition_kwantowa
compose exec -T web python manage.py seed_schools
compose ps --format table {{.Service}}\t{{.State}}\t{{.Health}}
compose exec -T mail cat /etc/opendkim/keys/olimpiada.example.txt
images --filter=reference=olimpiada/web --format {{.CreatedAt}}|{{.Repository}}:{{.Tag}}
image prune -f
compose exec -T web python manage.py check_domains --all'

# ================================================================================================
# 1. dj. wyłączone (brak wpisu w .env): polecenia dockera dzisiejsze, .env nietknięty.
# ================================================================================================
reset_server
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
cmp -s "$ROOT/deploy/Caddyfile" "$SRV/deploy/Caddyfile.generated"
check "konfiguracja proxy = deploy/Caddyfile bajt w bajt" $?
! grep -qE '^==> dj\.' "$WORK/off.out"
check "log wdrożenia bez kroku dj." $?
[ ! -e "$WORK/off.docker.sql" ]
check "scripts/djcms_db.sh nie jest wołany" $?

# Wartość wyłączona wprost (DJCMS_ENABLED=0) – to samo.
reset_server
{ cat "$WORK/env.fixture"; echo "DJCMS_ENABLED=0"; } >"$SRV/.env"
cp "$SRV/.env" "$WORK/env.zero"
run_deploy "$DEPLOY" zero
[ "$(cat "$WORK/zero.docker")" = "$DZISIAJ" ] && cmp -s "$WORK/env.zero" "$SRV/.env"
check "DJCMS_ENABLED=0: polecenia dzisiejsze, .env nietknięty" $?

# 1a. Porównanie z deploy.sh z innej rewizji (opcjonalne, DEPLOY_BASELINE_REF).
mask() {  # znaczniki czasu, rozmiary plików i wiersz z ls -lh zmieniają się między przebiegami
  sed -E 's/[0-9]{8}-[0-9]{6}/STAMP/g; s/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:+-]+/DATE/g; /^-rw/d' "$1"
}
if [ -n "${DEPLOY_BASELINE_REF:-}" ]; then
  git -C "$ROOT" show "$DEPLOY_BASELINE_REF:scripts/deploy.sh" >"$WORK/deploy-base.sh" 2>/dev/null
  check "deploy.sh z rewizji $DEPLOY_BASELINE_REF odczytany" $?
  reset_server
  run_deploy "$WORK/deploy-base.sh" base
  cp "$SRV/.env" "$WORK/base.env"
  reset_server
  run_deploy "$DEPLOY" new
  [ "$(cat "$WORK/base.docker")" = "$(cat "$WORK/new.docker")" ]
  check "[$DEPLOY_BASELINE_REF] polecenia docker identyczne" $?
  cmp -s "$WORK/base.env" "$SRV/.env"
  check "[$DEPLOY_BASELINE_REF] .env po wdrożeniu identyczny" $?
  diff "$WORK/base.ssh" "$WORK/new.ssh" | grep -E '^[<>]' >"$WORK/ssh.diff"
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
  env_line DJCMS_INTERNAL_TOKEN | grep -qE '^[A-Za-z0-9]{48}$'
check ".env: sekrety djcms (64/32/48 znaków [A-Za-z0-9])" $?
env_line DJCMS_DB_PASSWORD | grep -qE '^[A-Za-z0-9_-]{16,}$'
check ".env: DJCMS_DB_PASSWORD spełnia warunek scripts/djcms_db.sh" $?
[ "$(env_line COMPOSE_FILE)" = "docker-compose.yml:docker-compose.djcms.yml" ] && [ "$(env_line COMPOSE_PROFILES)" = "djcms" ]
check ".env: COMPOSE_FILE z nakładką djcms i COMPOSE_PROFILES=djcms" $?
[ "$(env_line DJCMS_INITIAL_IMPORT)" = "done" ]
check ".env: pierwszy import oznaczony jako wykonany" $?
# Plik wynikowy = fixture + dopiski (nic z istniejących linijek nie zmienione ani nie usunięte).
head -c "$(wc -c <"$WORK/env.fixture")" "$SRV/.env" | cmp -s - "$WORK/env.fixture"
check ".env: istniejące linijki nietknięte (djcms tylko dopisuje na końcu)" $?
grep -q '^dj\.{\$SITE_DOMAIN} {$' "$SRV/deploy/Caddyfile.generated"
check "konfiguracja proxy z blokiem dj." $?

WLACZONE='compose config
volume inspect olimpiada_pg_data
compose build --pull web
compose --profile djcms build --pull djcms
compose up -d db
compose ps --format {{.Service}}={{.Health}}
compose ps --format {{.Service}}={{.Health}}
compose exec -T db psql -X -q -v ON_ERROR_STOP=1 -U olimpiada -d olimpiada
compose exec -T db pg_dump -U olimpiada -d olimpiada -Fc
compose exec -T db psql -X -U olimpiada -d olimpiada -Atc SELECT 1 FROM pg_database WHERE datname = '"'olimpiada_djcms'"'
compose exec -T db pg_dump -U olimpiada -d olimpiada_djcms -Fc
compose up -d --remove-orphans db redis minio minio-init clamav mail web worker beat proxy djcms
compose ps --format {{.Service}}={{.Health}}
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
compose exec -T djcms python manage.py import_cms_bundle --from-api --if-empty'
[ "$(cat "$WORK/on.docker")" = "$WLACZONE" ]
rc=$?
check "z DJCMS_ENABLE=1: build djcms, baza, kopia, start, grupy, administrator, import – w tej kolejności" $rc
[ $rc -eq 0 ] || diff <(printf '%s\n' "$WLACZONE") "$WORK/on.docker" | sed 's/^/     /'
grep -q 'CREATE ROLE olimpiada_djcms' "$WORK/on.docker.sql" 2>/dev/null
check "scripts/djcms_db.sh dostał SQL roli i bazy" $?
ls "$BAK"/djcms-db-pre-*-vtest.dump >/dev/null 2>&1 && ls "$BAK"/pre-deploy-*-vtest.dump >/dev/null 2>&1
check "kopie przed migracjami: baza główna i baza djcms" $?
! grep -qF "$ADMIN_PW" "$WORK/on.ssh" "$WORK/on.docker" &&
  ! grep -qF "redakcja@olimpiada.example" "$WORK/on.ssh" "$WORK/on.docker"
check "dane administratora nie pojawiają się w argumentach ssh ani docker (tylko stdin i środowisko)" $?
grep -qE '^==> dj\. ' "$WORK/on.out" && grep -qF 'https://dj.olimpiada.example/' "$WORK/on.out"
check "log wdrożenia ma krok dj. z adresem" $?

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
  grep -q 'setup_djcms_groups' "$WORK/again.docker"
check "kolejne wdrożenie startuje djcms i odświeża grupę redaktorów" $?
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
  ( cd "$SRV" && env PATH="$BIN:$PATH" DOCKER_LOG="$DOCKER_LOG" REMOTE_DIR="$SRV" WEB_IMAGE= MAINTENANCE=1 \
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

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy wdrożenia dj. przeszły.\n'
