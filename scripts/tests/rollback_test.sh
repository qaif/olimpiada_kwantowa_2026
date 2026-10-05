#!/usr/bin/env bash
# Test migawki i wycofania wdrożenia (`scripts/rollback.sh`, docs/tasks/OPS-04.md § 2).
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu):
#   scripts/tests/rollback_test.sh
#
# Bez Dockera: atrapa `docker` modeluje to, czego skrypt potrzebuje – działające kontenery
# ($BOX/<usługa>.cid → ID obrazu), tagi obrazów ($BOX/tags/), zastosowane migracje w bazach
# ($BOX/mig.<baza>; $BOX/db.down = baza nie odpowiada), stan healthy po `up` i list alarmowy
# (temat i treść z $DEPLOY_ALERT_* do $BOX/alert.*). Kontrolę po wycofaniu zastępuje atrapa
# scripts/smoke.sh w piaskownicy (kod z STUB_SMOKE_RC, argumenty do $BOX/smoke.args).
#
# Najważniejsze: decyzja (bez migracji → wolno; nowa migracja bazy głównej albo djcms, nieznany
# stan, brak migawki → nie wolno), to że wycofanie zmienia w .env wyłącznie APP_VERSION/WEB_IMAGE/
# DJCMS_IMAGE i nie wydaje żadnego polecenia, które dotyka bazy, wolumenów albo usług danych.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/rollback-test.XXXXXX")"
trap '[ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"' EXIT
BIN="$WORK/bin"
SRV="$WORK/srv"
BOX="$WORK/box"
mkdir -p "$BIN"

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}
show_on_fail() { [ "$1" -eq 0 ] || sed 's/^/     /' "$2"; }

bash -n "$ROOT/scripts/rollback.sh"
check "scripts/rollback.sh przechodzi bash -n" $?

cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
a="$*"
tagfile() { printf '%s/tags/%s' "$BOX" "$(printf '%s' "$1" | tr '/:' '__')"; }
case "$*" in
  "compose ps -q web") cat "$BOX/web.cid" 2>/dev/null ;;
  "compose ps -q djcms") cat "$BOX/djcms.cid" 2>/dev/null ;;
  "inspect --format {{.Image}} "*) cat "$BOX/cid.${a##* }" 2>/dev/null || exit 1 ;;
  "image inspect --format {{.Id}} "*) cat "$(tagfile "${a##* }")" 2>/dev/null || exit 1 ;;
  "tag "*)
    set -- $*
    case "$2" in sha256:*) id="$2" ;; *) id="$(cat "$(tagfile "$2")" 2>/dev/null)" || exit 1 ;; esac
    echo "$id" >"$(tagfile "$3")" ;;
  *psql*pg_database*) [ -f "$BOX/db.down" ] && exit 2; [ -f "$BOX/mig.olimpiada_djcms" ] && echo 1 ;;
  *psql*django_migrations*)
    [ -f "$BOX/db.down" ] && exit 2
    db="$(printf '%s' "$*" | sed -n 's/.* -d \([^ ]*\) .*/\1/p')"
    cat "$BOX/mig.$db" 2>/dev/null || { echo 'ERROR: relation "django_migrations" does not exist' >&2; exit 1; } ;;
  "compose up -d --no-deps --no-build "*) printf '%s\n' ${STUB_HEALTH:-web=healthy djcms=healthy} >"$BOX/health" ;;
  "compose ps --format {{.Service}}={{.Health}}") cat "$BOX/health" 2>/dev/null ;;
  "compose exec -T -e DEPLOY_ALERT_SUBJECT -e DEPLOY_ALERT_BODY web "*)
    printf '%s\n---\n%s\n' "$DEPLOY_ALERT_SUBJECT" "$DEPLOY_ALERT_BODY" >"$BOX/alert.exec"; exit "${STUB_ALERT_RC:-0}" ;;
  "compose run --rm --no-deps -T -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 "*)
    printf '%s\n---\n%s\n' "$DEPLOY_ALERT_SUBJECT" "$DEPLOY_ALERT_BODY" >"$BOX/alert.run" ;;
esac
exit 0
STUB
chmod +x "$BIN/docker"

world() {  # world [djcms] – serwer po wdrożeniu v1 (działa web c1 = sha256:old), migracje A, B
  rm -rf "$SRV" "$BOX"
  mkdir -p "$SRV/scripts" "$BOX/tags"
  cp "$ROOT/scripts/rollback.sh" "$SRV/scripts/"
  cat >"$SRV/scripts/smoke.sh" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$*" >"$BOX/smoke.args"
for a in "$@"; do case "$prev" in --report) echo "kontrola po wycofaniu" >"$a" ;; esac; prev="$a"; done
exit "${STUB_SMOKE_RC:-0}"
FAKE
  cat >"$SRV/.env" <<'ENV'
APP_VERSION=v1
SITE_DOMAIN=olimpiada.example
POSTGRES_DB=olimpiada
POSTGRES_USER=olimpiada
ALERT_EMAILS=dyzurny@example.org
ENV
  echo c1 >"$BOX/web.cid"; echo sha256:old >"$BOX/cid.c1"
  echo sha256:old >"$BOX/tags/olimpiada_web_v1"
  printf 'accounts.0001_initial\ncore.0001_initial\n' >"$BOX/mig.olimpiada"
  if [ "${1:-}" = djcms ]; then
    echo "DJCMS_ENABLED=1" >>"$SRV/.env"
    echo d1 >"$BOX/djcms.cid"; echo sha256:djold >"$BOX/cid.d1"
    echo sha256:djold >"$BOX/tags/olimpiada_djcms_v1"
    printf 'pages.0001_initial\n' >"$BOX/mig.olimpiada_djcms"
  fi
}
deploy_v2() {  # „wdrożenie” v2: nowy obraz pod nowym tagiem, działa web c2 (i djcms d2)
  sed -i 's/^APP_VERSION=.*/APP_VERSION=v2/' "$SRV/.env"
  echo sha256:new >"$BOX/tags/olimpiada_web_v2"
  echo c2 >"$BOX/web.cid"; echo sha256:new >"$BOX/cid.c2"
  if [ -f "$BOX/djcms.cid" ]; then
    echo sha256:djnew >"$BOX/tags/olimpiada_djcms_v2"; echo d2 >"$BOX/djcms.cid"; echo sha256:djnew >"$BOX/cid.d2"
  fi
}
rb() {  # rb <etykieta> <polecenie…> – kod wyjścia; wydruk w $WORK/<etykieta>.out, docker w .docker
  local label="$1"; shift
  DOCKER_LOG="$WORK/$label.docker"; : >"$DOCKER_LOG"
  ( PATH="$BIN:$PATH" DOCKER_LOG="$DOCKER_LOG" BOX="$BOX" ROLLBACK_WAIT_SECONDS=0 OLIMPIADA_PROXY_LOCK=held \
      bash "$SRV/scripts/rollback.sh" "$@" ) </dev/null >"$WORK/$label.out" 2>&1
}
st() { sed -n "s/^$2=//p" "$SRV/deploy-state/$1" | tail -n 1; }
# Polecenia, których wycofanie nie może wydać NIGDY: baza, wolumeny, usługi danych, proxy, down.
no_data_commands() {
  ! grep -E '(^| )(down|volume|rm|rmi|prune)( |$)| -v( |$)|compose up.* (db|redis|minio|proxy|clamav|mail)( |$)|psql.*(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE)|pg_restore' "$1" \
    | grep -vE '^compose run --rm --no-deps -T -e RUN_MIGRATIONS=0'
}

# ================================================================================================
# 1. Migawka.
# ================================================================================================
world
rb snap snapshot
rc=$?
check "snapshot: kod 0" $rc
show_on_fail $rc "$WORK/snap.out"
[ "$(cat "$BOX/tags/olimpiada_web_previous" 2>/dev/null)" = sha256:old ]
check "snapshot: obraz DZIAŁAJĄCEGO web otagowany olimpiada/web:previous (po ID)" $?
[ "$(st previous.env PREV_APP_VERSION)" = v1 ] && [ "$(st previous.env WEB_IMAGE_ID)" = sha256:old ] &&
  [ "$(st previous.env MIGRATIONS_KNOWN)" = 1 ] && [ -z "$(st previous.env DJCMS_IMAGE_ID)" ]
check "snapshot: previous.env – wersja, ID obrazu, stan migracji znany, bez djcms" $?
cmp -s "$BOX/mig.olimpiada" "$SRV/deploy-state/migrations-before.txt" && [ ! -s "$SRV/deploy-state/djcms-migrations-before.txt" ]
check "snapshot: migracje zapisane (baza główna; djcms – brak bazy = pusta lista)" $?
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) echo "–    snapshot: prawa katalogu stanu – pominięte (NTFS nie ma praw POSIX)" ;;
  *) [ "$(stat -c %a "$SRV/deploy-state")" = 700 ]; check "snapshot: katalog stanu 700" $? ;;
esac
no_data_commands "$WORK/snap.docker"
check "snapshot: żadnego polecenia zmieniającego dane" $?

# Stara migawka nie może przeżyć nieudanej nowej (wycofanie do wersji sprzed dwóch wydań).
rm -f "$BOX/web.cid" "$BOX/tags/olimpiada_web_v1"
rb snap-none snapshot
[ $? = 0 ] && [ ! -f "$SRV/deploy-state/previous.env" ] && grep -q 'nie ma obrazu web sprzed wdrożenia' "$WORK/snap-none.out"
check "snapshot bez obrazu web: kod 0 (wdrożenie idzie dalej), stara migawka skasowana" $?

world
touch "$BOX/db.down"
rb snap-dbdown snapshot
[ $? = 0 ] && [ "$(st previous.env MIGRATIONS_KNOWN)" = 0 ] && [ ! -f "$SRV/deploy-state/migrations-before.txt" ]
check "snapshot przy niedziałającej bazie: MIGRATIONS_KNOWN=0" $?

world
rm "$SRV/.env"
rb snap-first snapshot
[ $? = 0 ] && grep -q 'pierwsze wdrożenie' "$WORK/snap-first.out"
check "snapshot bez .env (pierwsze wdrożenie): kod 0 z komunikatem" $?

# Web nie działa, ale obraz z .env jest – migawka z niego.
world
rm -f "$BOX/web.cid"
rb snap-stopped snapshot
[ "$(st previous.env WEB_IMAGE_ID)" = sha256:old ]
check "snapshot przy zatrzymanym web: obraz, który compose by wziął (olimpiada/web:\$APP_VERSION)" $?

# ================================================================================================
# 2. Decyzja.
# ================================================================================================
world; rb s snapshot; deploy_v2
rb dec0 decide
[ $? = 0 ] && grep -q '^auto: bez nowych migracji' "$WORK/dec0.out"
check "decide: bez nowych migracji → auto (kod 0)" $?

printf 'results.0042_nowa_kolumna\n' >>"$BOX/mig.olimpiada"
rb dec1 decide
[ $? = 3 ] && grep -q '^manual: wdrożenie zastosowało migracje (1): results.0042_nowa_kolumna' "$WORK/dec1.out"
check "decide: nowa migracja bazy głównej → manual (kod 3) z nazwą migracji" $?

world djcms; rb s snapshot; deploy_v2
printf 'pages.0002_x\n' >>"$BOX/mig.olimpiada_djcms"
rb dec2 decide
[ $? = 3 ] && grep -q 'djcms: pages.0002_x' "$WORK/dec2.out"
check "decide: nowa migracja djcms → manual (kod 3)" $?

world; rb s snapshot; deploy_v2
touch "$BOX/db.down"
rb dec3 decide
[ $? = 3 ] && grep -q 'stan nieznany' "$WORK/dec3.out"
check "decide: baza nie odpowiada → manual (kod 3, nie zgadujemy)" $?

world; touch "$BOX/db.down"; rb s snapshot; rm "$BOX/db.down"; deploy_v2
rb dec4 decide
[ $? = 3 ] && grep -q 'stan migracji sprzed wdrożenia nieznany' "$WORK/dec4.out"
check "decide: migawka bez stanu migracji → manual (kod 3)" $?

world; deploy_v2
rb dec5 decide
[ $? = 4 ] && grep -q '^impossible: brak migawki' "$WORK/dec5.out"
check "decide: brak migawki → impossible (kod 4)" $?

world; rb s snapshot; deploy_v2
echo sha256:inny >"$BOX/tags/olimpiada_web_previous"
rb dec6 decide
[ $? = 4 ]
check "decide: tag :previous wskazuje inny obraz niż migawka → impossible (kod 4)" $?

# ================================================================================================
# 3. Wycofanie ręczne (run).
# ================================================================================================
world; rb s snapshot; deploy_v2
cp "$SRV/.env" "$WORK/env.v2"
rb run0 run
[ $? = 2 ] && cmp -s "$WORK/env.v2" "$SRV/.env" && ! grep -q 'compose up' "$WORK/run0.docker"
check "run bez terminala i bez --yes: kod 2, nic nie zmienione" $?

rb run1 run --yes
rc=$?
check "run --yes: kod 0" $rc
show_on_fail $rc "$WORK/run1.out"
grep -qx 'tag olimpiada/web:previous olimpiada/web:v1' "$WORK/run1.docker" && [ "$(cat "$BOX/tags/olimpiada_web_v1")" = sha256:old ]
check "run: tag poprzedniej wersji wskazuje obraz z migawki" $?
[ "$(grep '^compose up' "$WORK/run1.docker")" = "compose up -d --no-deps --no-build web worker beat" ]
check "run: jedno polecenie up – wyłącznie web worker beat, --no-deps --no-build" $?
no_data_commands "$WORK/run1.docker"
check "run: żadnego polecenia dotykającego bazy, wolumenów, usług danych ani proxy" $?
diff "$WORK/env.v2" "$SRV/.env" | grep -E '^[<>]' >"$WORK/run1.envdiff"
[ "$(cat "$WORK/run1.envdiff")" = "< APP_VERSION=v2
> APP_VERSION=v1" ]
check "run: w .env zmieniona wyłącznie linijka APP_VERSION (v2 → v1)" $?
cmp -s "$WORK/env.v2" "$SRV/deploy-state/env.before-rollback"
check "run: kopia .env sprzed wycofania w deploy-state/env.before-rollback" $?
[ "$(st last-rollback.env FROM_APP_VERSION)" = v2 ] && [ "$(st last-rollback.env TO_APP_VERSION)" = v1 ] &&
  [ "$(st last-rollback.env HEALTHY)" = 1 ]
check "run: last-rollback.env (z v2 do v1, healthy)" $?

# Obraz z rejestru przed wdrożeniem (WEB_IMAGE) – wraca ten wpis; wdrożenie z WEB_IMAGE, wcześniej
# build – wpis znika.
world; echo "WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v1" >>"$SRV/.env"; rb s snapshot; deploy_v2
sed -i 's|^WEB_IMAGE=.*|WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v2|' "$SRV/.env"
rb run2 run --yes
grep -qx 'WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v1' "$SRV/.env" && [ "$(cat "$BOX/tags/ghcr.io_qaif_olimpiada-web_v1")" = sha256:old ]
check "run: poprzedni WEB_IMAGE (rejestr) wraca do .env i wskazuje obraz z migawki" $?
world; rb s snapshot; deploy_v2; echo "WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v2" >>"$SRV/.env"
rb run3 run --yes
! grep -q '^WEB_IMAGE=' "$SRV/.env"
check "run: WEB_IMAGE z nieudanego wdrożenia usunięty (wcześniej build na serwerze)" $?

# djcms działał przed wdrożeniem – wraca razem z web.
world djcms; rb s snapshot; deploy_v2
rb run4 run --yes
[ "$(grep '^compose up' "$WORK/run4.docker")" = "compose up -d --no-deps --no-build web worker beat djcms" ] &&
  grep -qx 'tag olimpiada/djcms:previous olimpiada/djcms:v1' "$WORK/run4.docker" &&
  [ "$(cat "$BOX/tags/olimpiada_djcms_v1")" = sha256:djold ]
check "run (djcms): web, worker, beat i djcms do obrazów z migawki" $?

# Po migracji: odmowa z instrukcją; --allow-migrations – świadomie.
world; rb s snapshot; deploy_v2
printf 'results.0042_nowa_kolumna\n' >>"$BOX/mig.olimpiada"
cp "$SRV/.env" "$WORK/env.v2m"
rb run5 run --yes
[ $? = 3 ] && cmp -s "$WORK/env.v2m" "$SRV/.env" && ! grep -q 'compose up' "$WORK/run5.docker" &&
  grep -q 'Procedura ręczna' "$WORK/run5.out" && grep -q 'rollback.sh run --allow-migrations' "$WORK/run5.out"
check "run po nowej migracji: kod 3, nic nie zmienione, procedura ręczna" $?
rb run6 run --yes --allow-migrations
[ $? = 0 ] && grep -q 'compose up -d --no-deps --no-build web worker beat' "$WORK/run6.docker" &&
  grep -q 'NOWSZYM schemacie' "$WORK/run6.out" && no_data_commands "$WORK/run6.docker"
check "run --allow-migrations: wycofuje z ostrzeżeniem, nadal bez dotykania bazy" $?

world; deploy_v2
rb run7 run --yes --allow-migrations
[ $? = 4 ] && ! grep -q 'compose up' "$WORK/run7.docker"
check "run bez migawki: kod 4 także z --allow-migrations" $?

# Usługi nie wstają po wycofaniu – kod 1, HEALTHY=0.
world; rb s snapshot; deploy_v2
STUB_HEALTH="web=unhealthy" rb run8 run --yes
[ $? = 1 ] && [ "$(st last-rollback.env HEALTHY)" = 0 ]
check "run: web nie healthy po wycofaniu → kod 1, HEALTHY=0" $?

# ================================================================================================
# 4. auto (wdrożenie po nieudanej kontroli dymnej).
# ================================================================================================
world; rb s snapshot; deploy_v2
echo "FAIL GET https://olimpiada.example/healthz/: 503" >"$SRV/deploy-state/last-smoke.txt"
rb auto0 auto --failed-version v2
[ $? = 10 ]
check "auto bez migracji: wycofano, kontrola po wycofaniu przeszła → kod 10" $?
grep -q -- '--server '"$SRV"' --expect-version v1 --report '"$SRV"'/deploy-state/rollback-smoke.txt' "$BOX/smoke.args"
check "auto: kontrola po wycofaniu z oczekiwaną poprzednią wersją" $?
head -n 1 "$BOX/alert.exec" | grep -q 'wdrożenie v2 WYCOFANE do v1' && grep -q 'healthz/: 503' "$BOX/alert.exec" &&
  ! grep -qi 'password\|secret\|token' "$BOX/alert.exec"
check "auto: list do ALERT_EMAILS (temat: wycofane; treść: wydruk kontroli, bez sekretów)" $?
grep -q 'compose exec -T -e DEPLOY_ALERT_SUBJECT -e DEPLOY_ALERT_BODY web python manage.py shell -c' "$WORK/auto0.docker" &&
  ! grep -q 'dyzurny@example.org' "$WORK/auto0.docker"
check "auto: treść listu przez środowisko (-e NAZWA), nie w argumentach" $?

world; rb s snapshot; deploy_v2
STUB_SMOKE_RC=1 rb auto1 auto --failed-version v2
[ $? = 12 ] && head -n 1 "$BOX/alert.exec" | grep -q 'NADAL nie działa'
check "auto: wycofano, ale kontrola dalej nie przechodzi → kod 12 i list" $?

world; rb s snapshot; deploy_v2
printf 'results.0042_nowa_kolumna\n' >>"$BOX/mig.olimpiada"
rb auto2 auto --failed-version v2
[ $? = 11 ] && ! grep -q 'compose up' "$WORK/auto2.docker" && grep -q '^APP_VERSION=v2' "$SRV/.env"
check "auto po migracji: NIE wycofano (kod 11), .env i kontenery bez zmian" $?
head -n 1 "$BOX/alert.exec" | grep -q 'NIE PRZESZŁO kontroli' && grep -q 'results.0042_nowa_kolumna' "$BOX/alert.exec" &&
  grep -q 'Procedura ręczna' "$BOX/alert.exec" && grep -q 'nie została włączona' "$BOX/alert.exec"
check "auto po migracji: list z powodem, procedurą ręczną i stanem strony prac technicznych" $?
! grep -q 'maintenance' "$WORK/auto2.docker"
check "auto po migracji: strona prac technicznych nietknięta" $?

# web nie działa – list przez jednorazowy kontener (bez migracji i collectstatic).
world; rb s snapshot; deploy_v2
printf 'x.0001\n' >>"$BOX/mig.olimpiada"
STUB_ALERT_RC=1 rb auto3 auto --failed-version v2
[ -f "$BOX/alert.run" ] && grep -q 'compose run --rm --no-deps -T -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e DEPLOY_ALERT_SUBJECT -e DEPLOY_ALERT_BODY web' "$WORK/auto3.docker"
check "auto: web nie działa → list przez docker compose run (RUN_MIGRATIONS=0, RUN_COLLECTSTATIC=0)" $?

world; deploy_v2
rb auto4 auto --failed-version v2
[ $? = 11 ] && grep -q 'brak migawki' "$BOX/alert.exec"
check "auto bez migawki: kod 11 i list" $?

# ================================================================================================
# 5. record-success i commit poprzedniej wersji w kolejnej migawce.
# ================================================================================================
world
rb rec record-success --git-commit abc1234
[ $? = 0 ] && [ "$(st deployed.env APP_VERSION)" = v1 ] && [ "$(st deployed.env GIT_COMMIT)" = abc1234 ] &&
  [ "$(st deployed.env WEB_IMAGE_ID)" = sha256:old ]
check "record-success: deployed.env (wersja, commit, obraz)" $?
rb s2 snapshot
[ "$(st previous.env PREV_GIT_COMMIT)" = abc1234 ]
check "snapshot: PREV_GIT_COMMIT z deployed.env tej samej wersji" $?
rb stat status
[ $? = 0 ] && grep -q 'GIT_COMMIT=abc1234' "$WORK/stat.out" && grep -q '^auto:' "$WORK/stat.out"
check "status: stan i decyzja" $?

echo
if [ "$failures" -eq 0 ]; then echo "rollback_test: wszystko ok"; else echo "rollback_test: $failures błędów"; fi
[ "$failures" -eq 0 ]
