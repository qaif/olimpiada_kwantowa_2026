#!/usr/bin/env bash
# Test przełącznika serwisu publicznego (`scripts/djcms_switch.sh`, docs/tasks/DJ-02.md § 10.2).
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu; bez Dockera):
#   scripts/tests/djcms_switch_test.sh
#
# Przełącznik biegnie naprawdę (z prawdziwym scripts/render_caddyfile.sh i kontraktem tras) w
# piaskownicy udającej /opt/olimpiada, a `docker` i `curl` są atrapami, które modelują to, co ma
# znaczenie: kontener `proxy` widzi caddy/Caddyfile przez montaż katalogu (żywy) albo inną treść
# (montaż sprzed CADDY_CONFIG_DIR – docs/OPERACJE.md § 23),
# `caddy reload` ładuje to, co kontener widzi, a odpowiedzi przez proxy (curl) zależą od
# załadowanej konfiguracji – strony publiczne z `X-Djcms-Mode: primary` tylko wtedy, gdy Caddy
# naprawdę ma trasy trybu primary. Trasy same w sobie sprawdza scripts/tests/djcms_routing_test.sh.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/djcms-switch-test.XXXXXX")"
trap '[ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"' EXIT
SRV="$WORK/srv" BOX="$WORK/box" BIN="$WORK/bin"
mkdir -p "$BIN"

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}

bash -n "$ROOT/scripts/djcms_switch.sh"
check "scripts/djcms_switch.sh przechodzi bash -n" $?

# --- Atrapy ------------------------------------------------------------------------------------
# docker: stan „kontenera proxy” w $BOX: `mount` = live (widzi plik hosta) albo stale (stara kopia),
# `loaded` = konfiguracja załadowana przez Caddy'ego (reload kopiuje to, co kontener widzi).
cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
seen() { if [ "$(cat "$BOX/mount")" = live ]; then cat caddy/Caddyfile; else cat "$BOX/stale"; fi; }
case "$*" in
  "compose ps --format {{.Service}}={{.Health}}")
    printf 'web=healthy\nproxy=healthy\ndjcms=%s\n' "${STUB_DJCMS_HEALTH:-healthy}" ;;
  "compose exec -T proxy sha256sum /etc/caddy/Caddyfile")
    printf '%s  /etc/caddy/Caddyfile\n' "$(seen | sha256sum | cut -d' ' -f1)" ;;
  "compose exec -T proxy sh -c "*"caddy validate"*)
    cat >"$BOX/validated"; exit "${STUB_VALIDATE_RC:-0}" ;;
  "compose exec -T proxy caddy reload"*)
    seen >"$BOX/loaded" ;;
  "compose exec -T proxy wget"*) ;;
  "compose up -d --force-recreate --no-deps proxy")
    echo live >"$BOX/mount"; seen >"$BOX/loaded" ;;
  "compose exec -T djcms python manage.py sync_competitions --list-hosts")
    [ "${STUB_DJCMS_HEALTH:-healthy}" = healthy ] || exit 1
    printf 'nowe: –\nbez zmian: kwantowa, fizyczna, e2e-druga\nwygaszone: –\n'
    # e2e-druga.localhost – własna domena konkursu pod prefiksem: rejestr ją zna, proxy jej nie obsługuje.
    printf 'olimpiada.example kwantowa\nfizyczna.example fizyczna\ne2e-druga.localhost e2e-druga\nolimpiada.example/druga/ e2e-druga\n'
    printf 'sync_competitions: gotowe.\n' ;;
  "compose exec -T web python manage.py cms_freeze status")
    if [ "${STUB_FREEZE_RC:-0}" = 0 ]; then echo "Edycja stron ZAMROŻONA od 2026-09-26"; else echo "Edycja stron otwarta."; fi
    exit "${STUB_FREEZE_RC:-0}" ;;
  *) echo "docker (atrapa): nieoczekiwane polecenie: $*" >&2; exit 97 ;;
esac
STUB
# curl: odpowiedź „przez proxy” wg konfiguracji załadowanej w $BOX/loaded. Ostatni argument = adres.
cat >"$BIN/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CURL_LOG"
url="${!#}"; path="/${url#https://*/}"
[ "${STUB_CURL_BROKEN:-0}" = 1 ] && { printf 'HTTP/2 502\r\n\r\n'; exit 0; }
primary=0; grep -q 'header_up X-Djcms-Mode primary' "$BOX/loaded" 2>/dev/null && primary=1
case "$path" in
  /internal/*) printf 'HTTP/2 404\r\n\r\n' ;;
  */login/|/static/*) printf 'HTTP/2 200\r\ncontent-type: text/html\r\n\r\n' ;;
  *) if [ "$primary" = 1 ]; then printf 'HTTP/2 200\r\nx-djcms-mode: primary\r\nvary: Cookie\r\n\r\n'
     else printf 'HTTP/2 200\r\nvary: Cookie\r\n\r\n'; fi ;;
esac
STUB
chmod +x "$BIN/docker" "$BIN/curl"

cat >"$WORK/env.fixture" <<'ENV'
SITE_DOMAIN=olimpiada.example
ACME_EMAIL=ops@olimpiada.example
EXTRA_DOMAINS=fizyczna.example www.fizyczna.example
CADDY_CONFIG_DIR=./caddy
MAINTENANCE_BYPASS_TOKEN=0123456789abcdefghijklmnopqrstuvwxyzABCD

# dj.
DJCMS_ENABLED=1
COMPOSE_FILE=docker-compose.yml:docker-compose.djcms.yml
COMPOSE_PROFILES=djcms
ENV

reset_server() {  # reset_server [plik .env] – świeża instalacja: kod, .env, plik wygenerowany, proxy „żywe”
  rm -rf "${SRV:?}" "${BOX:?}"
  mkdir -p "$SRV/scripts" "$SRV/deploy" "$SRV/backend/djcms_contract" "$BOX"
  cp "$ROOT/scripts/render_caddyfile.sh" "$ROOT/scripts/djcms_switch.sh" "$SRV/scripts/"
  cp "$ROOT/deploy/Caddyfile" "$SRV/deploy/"
  cp "$ROOT/backend/djcms_contract/app_routes.env" "$SRV/backend/djcms_contract/"
  cp "${1:-$WORK/env.fixture}" "$SRV/.env"
  ( cd "$SRV" && env -u EXTRA_DOMAINS -u PLATFORM_SUBDOMAINS -u DJCMS_ENABLED -u DJCMS_PRIMARY \
      bash scripts/render_caddyfile.sh >/dev/null 2>&1 )
  echo live >"$BOX/mount"
  cp "$SRV/caddy/Caddyfile" "$BOX/loaded"
  cp "$SRV/caddy/Caddyfile" "$BOX/stale"
}

run_switch() {  # run_switch <etykieta> <akcja> [ZMIENNA=wartość…] – kod wyjścia
  local label="$1" action="$2"; shift 2
  DOCKER_LOG="$WORK/$label.docker"; CURL_LOG="$WORK/$label.curl"; OUT="$WORK/$label.out"
  : >"$DOCKER_LOG"; : >"$CURL_LOG"
  ( cd "$SRV" && env -u DJCMS_PRIMARY -u DJCMS_ENABLED -u EXTRA_DOMAINS -u PLATFORM_SUBDOMAINS \
      PATH="$BIN:$PATH" DOCKER_LOG="$DOCKER_LOG" CURL_LOG="$CURL_LOG" BOX="$BOX" "$@" \
      bash scripts/djcms_switch.sh "$action" ) </dev/null >"$OUT" 2>&1
}
show_on_fail() { [ "$1" -eq 0 ] || sed 's/^/     /' "$2"; }
env_line() { sed -n "s/^$1=//p" "$SRV/.env" | tail -n 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }

# 1. Zły argument.
reset_server
run_switch arg nie-wiem
[ $? -eq 2 ]
check "nieznana akcja – kod 2" $?

# 2. status na świeżej instalacji (brak DJCMS_PRIMARY = Wagtail). `cms_freeze status` z kodem 1
#    (edycja otwarta) nie może przerwać skryptu (set -e).
reset_server
run_switch st0 status STUB_FREEZE_RC=1
rc=$?
check "status kończy się kodem 0 także przy otwartej edycji Wagtaila (cms_freeze status = 1)" $rc
show_on_fail $rc "$WORK/st0.out"
grep -qF 'strony publiczne: Wagtail (web)' "$WORK/st0.out" && grep -qF 'zgodny z .env' "$WORK/st0.out" &&
  grep -qF 'tryb w pliku: preview' "$WORK/st0.out" && grep -qF 'edycja stron otwarta' "$WORK/st0.out" &&
  grep -qF 'proxy: widzi ten sam plik' "$WORK/st0.out"
check "status: Wagtail, plik zgodny, tryb preview, edycja otwarta, proxy widzi plik" $?

# 3. on: .env, plik, reload, kontrola dymna (hosty z rejestru djcms, prefiks, /login/, statyki, /internal).
cp "$SRV/.env" "$WORK/env.before-on"
run_switch on on
rc=$?
check "on kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/on.out"
[ "$(env_line DJCMS_PRIMARY)" = 1 ] && [ "$(grep -c '^DJCMS_PRIMARY=' "$SRV/.env")" = 1 ]
check ".env: DJCMS_PRIMARY=1 dopisane raz" $?
head -c "$(wc -c <"$WORK/env.before-on")" "$SRV/.env" | cmp -s - "$WORK/env.before-on"
check ".env: istniejące linijki nietknięte (przełącznik tylko dopisuje)" $?
grep -q 'header_up X-Djcms-Mode primary' "$SRV/caddy/Caddyfile" && grep -q 'header_up X-Djcms-Mode primary' "$BOX/loaded"
check "plik wygenerowany i konfiguracja załadowana w proxy: tryb primary" $?
grep -q 'header_up X-Djcms-Mode primary' "$BOX/validated"
check "caddy validate dostał plik kandydujący trybu primary" $?
order="$(grep -nE 'caddy validate|caddy reload' "$WORK/on.docker" | cut -d: -f1 | tr '\n' ' ')"
[ "$(grep -c 'caddy validate' "$WORK/on.docker")" = 1 ] && [ "$(grep -c 'caddy reload' "$WORK/on.docker")" = 1 ] &&
  [ "${order%% *}" -lt "$(echo "$order" | awk '{print $2}')" ]
check "validate przed reload, każde raz [$order]" $?
! grep -qE 'restart|up -d|stop' "$WORK/on.docker"
check "on nie restartuje ani nie odtwarza żadnej usługi (tylko caddy reload)" $?
for u in https://olimpiada.example/ https://olimpiada.example/login/ https://fizyczna.example/ https://fizyczna.example/login/ \
         https://olimpiada.example/druga/ https://olimpiada.example/druga/login/ https://olimpiada.example/static/css/app.css \
         https://olimpiada.example/internal/tls-allowed https://olimpiada.example/robots.txt https://fizyczna.example/robots.txt; do
  grep -qE " ${u//./\\.}\$" "$WORK/on.curl"
  check "kontrola dymna sprawdza $u" $?
done
grep -qF -- '--resolve olimpiada.example:443:127.0.0.1' "$WORK/on.curl" && grep -qF 'X-Maintenance-Bypass: ' "$WORK/on.curl"
check "kontrola dymna idzie przez lokalne proxy (--resolve) z przepustką prac technicznych" $?
! grep -q 'www.fizyczna.example' "$WORK/on.curl"
check "kontrola dymna pomija przekierowania www." $?
! grep -q '^   FAIL' "$WORK/on.out"
check "kontrola dymna bez porażek" $?
! grep -q 'e2e-druga.localhost' "$WORK/on.curl" && grep -qF 'https://e2e-druga.localhost/ – pominięte' "$WORK/on.out"
check "kontrola dymna pomija host z rejestru, którego proxy nie obsługuje (domena konkursu pod prefiksem)" $?

# 4. status po on.
run_switch st1 status
grep -qF 'strony publiczne: django CMS (djcms)' "$WORK/st1.out" && grep -qF 'tryb w pliku: primary' "$WORK/st1.out" &&
  grep -qF 'ZAMROŻONA' "$WORK/st1.out"
check "status po on: django CMS, tryb primary, Wagtail zamrożony" $?

# 5. check (kontrola dymna trybu z .env – woła ją deploy.sh przy DJCMS_PRIMARY=1).
run_switch chk check
check "check w trybie primary kończy się powodzeniem" $?
! grep -qE 'caddy (reload|validate)' "$WORK/chk.docker"
check "check niczego nie przeładowuje" $?
run_switch chkbad check STUB_CURL_BROKEN=1
[ $? -ne 0 ]
check "check przy 502 przez proxy – kod ≠ 0" $?

# 6. off: z powrotem Wagtail, bez wymogu zdrowego djcms (hosty wtedy z .env).
run_switch off off STUB_DJCMS_HEALTH=unhealthy
rc=$?
check "off kończy się powodzeniem także przy niezdrowym djcms" $rc
show_on_fail $rc "$WORK/off.out"
[ "$(env_line DJCMS_PRIMARY)" = 0 ] && [ "$(grep -c '^DJCMS_PRIMARY=' "$SRV/.env")" = 1 ] &&
  grep -q 'header_up X-Djcms-Mode preview' "$BOX/loaded"
check "off: DJCMS_PRIMARY=0 (ta sama linijka), proxy w trybie preview" $?
grep -qE ' https://fizyczna\.example/$' "$WORK/off.curl" && ! grep -q 'druga' "$WORK/off.curl"
check "off bez djcms: hosty z SITE_DOMAIN i EXTRA_DOMAINS" $?
grep -qF 'NIE wracają do Wagtaila' "$WORK/off.out" && grep -qF 'cms_freeze off' "$WORK/off.out"
check "off przypomina: zmiany z djcms nie wracają, cms_freeze off osobno" $?

# 7. on przy odrzuconej walidacji – nic się nie zmienia.
reset_server
cp "$SRV/.env" "$WORK/env.v"; before="$(sha "$SRV/caddy/Caddyfile")"
run_switch val on STUB_VALIDATE_RC=1
[ $? -ne 0 ] && cmp -s "$WORK/env.v" "$SRV/.env" && [ "$before" = "$(sha "$SRV/caddy/Caddyfile")" ] &&
  ! grep -q 'caddy reload' "$WORK/val.docker" && [ ! -e "$SRV/deploy/Caddyfile.candidate" ]
check "odrzucony caddy validate: kod ≠ 0, .env i plik nietknięte, bez reload, bez pliku kandydującego" $?

# 8. on, a kontrola dymna nie przechodzi – automatyczny powrót do Wagtaila.
reset_server
run_switch rb on STUB_CURL_BROKEN=1
rc=$?
[ $rc -ne 0 ] && [ "$(env_line DJCMS_PRIMARY)" = 0 ] && grep -q 'header_up X-Djcms-Mode preview' "$BOX/loaded" &&
  grep -q 'header_up X-Djcms-Mode preview' "$SRV/caddy/Caddyfile" &&
  [ "$(grep -c 'caddy reload' "$WORK/rb.docker")" = 2 ] && grep -qF 'Wrócono do Wagtaila' "$WORK/rb.out"
check "porażka kontroli dymnej po on: kod ≠ 0, powrót do DJCMS_PRIMARY=0 (render + drugi reload)" $?

# 9. on przy niezdrowym djcms / wyłączonym DJCMS_ENABLED / proxy bez pliku wygenerowanego – odmowa.
reset_server
cp "$SRV/.env" "$WORK/env.u"
run_switch unh on STUB_DJCMS_HEALTH=starting
[ $? -ne 0 ] && cmp -s "$WORK/env.u" "$SRV/.env" && ! grep -qE 'caddy (validate|reload)' "$WORK/unh.docker"
check "on przy djcms≠healthy: odmowa bez zmian" $?
sed 's/^DJCMS_ENABLED=1$/DJCMS_ENABLED=0/' "$WORK/env.fixture" >"$WORK/env.off"
reset_server "$WORK/env.off"
run_switch dis on
[ $? -ne 0 ] && grep -qF 'DJCMS_ENABLED' "$WORK/dis.out" && [ ! -s "$WORK/dis.docker" ]
check "on przy DJCMS_ENABLED=0: odmowa bez dockera" $?
sed 's|^CADDY_CONFIG_DIR=.*|CADDY_CONFIG_DIR=./deploy|' "$WORK/env.fixture" >"$WORK/env.src"
reset_server "$WORK/env.src"
run_switch src on
[ $? -ne 0 ] && grep -qF 'CADDY_CONFIG_DIR' "$WORK/src.out" && ! grep -qE 'caddy (validate|reload)' "$WORK/src.docker"
check "on, gdy proxy montuje deploy/ (plik źródłowy, nie caddy/): odmowa" $?

# 10. Kontener widzi inną treść (montaż sprzed CADDY_CONFIG_DIR): on odmawia, off odtwarza proxy.
reset_server
echo stale >"$BOX/mount"; echo "# stara treść" >>"$BOX/stale"
cp "$SRV/.env" "$WORK/env.s"
run_switch stale-on on
[ $? -ne 0 ] && cmp -s "$WORK/env.s" "$SRV/.env" && grep -qF 'force-recreate' "$WORK/stale-on.out" &&
  ! grep -qE 'caddy reload|force-recreate' "$WORK/stale-on.docker"
check "on przy starym montażu w proxy: odmowa z instrukcją, nic nie zmienione" $?
run_switch stale-off off
rc=$?
[ $rc -eq 0 ] && grep -qx 'compose up -d --force-recreate --no-deps proxy' "$WORK/stale-off.docker" &&
  [ "$(cat "$BOX/mount")" = live ] && grep -q 'header_up X-Djcms-Mode preview' "$BOX/loaded"
check "off przy starym montażu: odtwarza proxy i kończy w trybie preview" $?
show_on_fail $rc "$WORK/stale-off.out"

# 11. on przy otwartej edycji Wagtaila – ostrzeżenie, ale przełączenie (decyzja operatora).
reset_server
run_switch warn on STUB_FREEZE_RC=1
[ $? -eq 0 ] && grep -qF 'nie jest zamrożona' "$WORK/warn.out" && [ "$(env_line DJCMS_PRIMARY)" = 1 ]
check "on przy otwartej edycji Wagtaila: ostrzeżenie i przełączenie" $?

# 12. Istniejąca linijka DJCMS_PRIMARY (np. z .env.example) jest zmieniana, nie dublowana; wartość
#     ze zmiennej środowiskowej operatora nie wygrywa z .env.
{ cat "$WORK/env.fixture"; echo "DJCMS_PRIMARY=0"; } >"$WORK/env.p0"
reset_server "$WORK/env.p0"
run_switch envline on DJCMS_PRIMARY=0
[ $? -eq 0 ] && [ "$(grep -c '^DJCMS_PRIMARY=' "$SRV/.env")" = 1 ] && [ "$(env_line DJCMS_PRIMARY)" = 1 ] &&
  grep -q 'header_up X-Djcms-Mode primary' "$BOX/loaded"
check "DJCMS_PRIMARY=0 w .env → on zmienia tę linijkę (bez duplikatu), zmienna powłoki nie wygrywa" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło (KEEP_WORK=1 zostawia %s).\n' "$failures" "$WORK"
  exit 1
fi
printf '\nWszystkie testy przełącznika djcms przeszły.\n'
