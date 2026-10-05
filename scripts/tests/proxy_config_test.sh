#!/usr/bin/env bash
# Test konfiguracji proxy na serwerze (`scripts/proxy_config.sh`, docs/OPERACJE.md § 23).
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu; bez Dockera):
#   scripts/tests/proxy_config_test.sh
#
# Skrypt biegnie naprawdę (z prawdziwym scripts/render_caddyfile.sh) w piaskownicy udającej
# /opt/olimpiada, a `docker` jest atrapą, która modeluje to, co ma znaczenie: kontener `proxy`
# widzi caddy/Caddyfile przez montaż katalogu (`live`), inną treść (`stale` – montaż sprzed
# CADDY_CONFIG_DIR) albo nie działa (`down`); `caddy reload` ładuje to, co kontener widzi,
# a odtworzenie kontenera daje `live`. Pełne wdrożenie z tym skryptem sprawdza
# scripts/tests/deploy_djcms_test.sh (część 10).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/proxy-config-test.XXXXXX")"
trap '[ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"' EXIT
SRV="$WORK/srv" BOX="$WORK/box" BIN="$WORK/bin"
mkdir -p "$BIN"

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}

bash -n "$ROOT/scripts/proxy_config.sh"
check "scripts/proxy_config.sh przechodzi bash -n" $?

# --- Atrapy ------------------------------------------------------------------------------------
# docker: stan kontenera proxy w $BOX/state (live|stale|down), załadowana konfiguracja w $BOX/loaded,
# treść sprawdzana przez `caddy validate` w $BOX/validated. STUB_RECREATE_STALE=1 – odtworzony
# kontener dalej widzi inną treść (np. CADDY_CONFIG_DIR, którego compose nie czyta).
cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
state="$(cat "$BOX/state")"
seen() { if [ "$state" = live ]; then cat caddy/Caddyfile; else printf '# stara treść\n'; fi; }
case "$*" in
  "compose ps -q --status running proxy") [ "$state" = down ] || echo 0123abcd ;;
  # Obraz z compose'a i obraz działającego proxy (proxy_image_changed): domyślnie te same.
  "compose config") printf 'name: olimpiada\nservices:\n  proxy:\n    image: %s\n' "${STUB_WANT_IMAGE:-caddy:2.10}" ;;
  "compose ps --status running --format {{.Image}} proxy") [ "$state" = down ] || echo "${STUB_HAVE_IMAGE:-caddy:2.10}" ;;
  "compose run --rm --no-deps -T --entrypoint sh proxy -c "*"caddy validate"*)
    cat >"$BOX/validated"; echo run >"$BOX/validated_by"; exit "${STUB_VALIDATE_RC:-0}" ;;
  "compose exec -T proxy sha256sum /etc/caddy/Caddyfile")
    [ "$state" = down ] && { echo "service \"proxy\" is not running" >&2; exit 1; }
    printf '%s  /etc/caddy/Caddyfile\n' "$(seen | sha256sum | cut -d' ' -f1)" ;;
  "compose exec -T proxy sh -c "*"caddy validate"*)
    [ "$state" = down ] && exit 1
    cat >"$BOX/validated"; exit "${STUB_VALIDATE_RC:-0}" ;;
  "compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile")
    [ "${STUB_RELOAD_RC:-0}" = 0 ] || { echo '{"level":"error","msg":"adapting config"}' >&2; exit "$STUB_RELOAD_RC"; }
    seen >"$BOX/loaded" ;;
  "compose up -d --force-recreate --no-deps proxy")
    if [ "${STUB_RECREATE_STALE:-0}" = 1 ]; then echo stale >"$BOX/state"; else echo live >"$BOX/state"; fi
    state="$(cat "$BOX/state")"; seen >"$BOX/loaded" ;;
  # Caddy wstaje (API administracyjne odpowiada), chyba że załadowana treść zawiera STUB_BROKEN_MARK.
  "compose exec -T proxy wget"*)
    [ -n "${STUB_BROKEN_MARK:-}" ] && grep -qF "$STUB_BROKEN_MARK" "$BOX/loaded" && exit 1
    [ "$state" != down ] ;;
  "compose exec -T proxy cat /etc/caddy/Caddyfile")
    [ "$state" = down ] && exit 1
    if [ "$state" = live ]; then cat caddy/Caddyfile 2>/dev/null || exit 1; else printf '# stara treść
'; fi ;;
  *) echo "docker (atrapa): nieoczekiwane polecenie: $*" >&2; exit 97 ;;
esac
STUB
chmod +x "$BIN/docker"

cat >"$WORK/env.fixture" <<'ENV'
SITE_DOMAIN=olimpiada.example
ACME_EMAIL=ops@olimpiada.example
EXTRA_DOMAINS=
CADDY_CONFIG_DIR=./caddy
ENV

reset_server() {  # reset_server [plik .env] [stan proxy] – kod i .env, bez caddy/
  rm -rf "${SRV:?}" "${BOX:?}"
  mkdir -p "$SRV/scripts" "$SRV/deploy" "$SRV/backend/djcms_contract" "$BOX"
  cp "$ROOT/scripts/render_caddyfile.sh" "$ROOT/scripts/proxy_config.sh" "$SRV/scripts/"
  cp "$ROOT/deploy/Caddyfile" "$SRV/deploy/"
  cp "$ROOT/backend/djcms_contract/app_routes.env" "$SRV/backend/djcms_contract/"
  cp "${1:-$WORK/env.fixture}" "$SRV/.env"
  echo "${2:-live}" >"$BOX/state"
  : >"$BOX/loaded"
}

run_pc() {  # run_pc <etykieta> <akcja> [ZMIENNA=wartość…] – kod wyjścia
  local label="$1" action="$2"; shift 2
  DOCKER_LOG="$WORK/$label.docker"; OUT="$WORK/$label.out"
  : >"$DOCKER_LOG"
  ( cd "$SRV" && env -u EXTRA_DOMAINS -u PLATFORM_SUBDOMAINS -u DJCMS_ENABLED -u DJCMS_PRIMARY \
      PATH="$BIN:$PATH" DOCKER_LOG="$DOCKER_LOG" BOX="$BOX" "$@" \
      bash scripts/proxy_config.sh "$action" ) </dev/null >"$OUT" 2>&1
}
show_on_fail() { [ "$1" -eq 0 ] || sed 's/^/     /' "$2"; }
RELOAD='compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile'
RECREATE='compose up -d --force-recreate --no-deps proxy'

# 1. Zły argument, brak .env.
reset_server
run_pc arg nie-wiem
[ $? -eq 2 ]
check "nieznana akcja – kod 2" $?
rm -f "$SRV/.env"
run_pc noenv render
[ $? -ne 0 ] && grep -qF 'brak .env' "$WORK/noenv.out" && [ ! -s "$WORK/noenv.docker" ]
check "brak .env – odmowa bez dockera" $?

# 2. render bez CADDY_CONFIG_DIR (albo z innym katalogiem) – odmowa: plik nie miałby skutku.
for bad in "" "CADDY_CONFIG_DIR=./deploy"; do
  grep -v '^CADDY_CONFIG_DIR=' "$WORK/env.fixture" >"$WORK/env.bad"
  [ -n "$bad" ] && echo "$bad" >>"$WORK/env.bad"
  reset_server "$WORK/env.bad"
  run_pc baddir render
  [ $? -ne 0 ] && grep -qF 'CADDY_CONFIG_DIR' "$WORK/baddir.out" && [ ! -e "$SRV/caddy/Caddyfile" ] && [ ! -s "$WORK/baddir.docker" ]
  check "render przy „${bad:-brak CADDY_CONFIG_DIR}” – odmowa, nic nie zapisane, bez dockera" $?
done

# 3. render przy działającym proxy: walidacja nowej treści, potem instalacja; bez pliku .next.
reset_server
run_pc r1 render
rc=$?
check "render (proxy działa) kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/r1.out"
cmp -s "$ROOT/deploy/Caddyfile" "$SRV/caddy/Caddyfile" && cmp -s "$BOX/validated" "$SRV/caddy/Caddyfile" &&
  [ ! -e "$SRV/caddy/Caddyfile.next" ]
check "render: caddy/Caddyfile = deploy/Caddyfile (puste EXTRA_DOMAINS), ta treść przeszła caddy validate" $?
[ "$(grep -vxE 'compose exec -T proxy cat /etc/caddy/Caddyfile|compose config|compose ps --status running --format \{\{\.Image\}\} proxy' "$WORK/r1.docker" | head -n 2)" = "compose ps -q --status running proxy
compose exec -T proxy sh -c cat > /tmp/Caddyfile.next && caddy validate --config /tmp/Caddyfile.next --adapter caddyfile" ] &&
  ! grep -qE 'reload|force-recreate' "$WORK/r1.docker"
check "render: tylko walidacja w kontenerze – bez reload i bez odtwarzania" $?
run_pc r2 render
grep -qF 'bez zmian' "$WORK/r2.out"
check "render tej samej treści: „bez zmian”" $?

# 4. Zapis w miejscu: nowa treść w tym samym i-węźle (ważne dla kontenera sprzed montażu katalogu).
ino_before="$(stat -c %i "$SRV/caddy/Caddyfile" 2>/dev/null)"
sed -i 's/^EXTRA_DOMAINS=$/EXTRA_DOMAINS=fizyczna.example/' "$SRV/.env"
run_pc r3 render
grep -q '^fizyczna\.example {' "$SRV/caddy/Caddyfile" && [ "$(stat -c %i "$SRV/caddy/Caddyfile" 2>/dev/null)" = "$ino_before" ]
check "render nowej treści: zapis w miejscu (ten sam i-węzeł)" $?

# 5. Generator czyta wyłącznie .env – zmienna z powłoki operatora nie wygrywa.
reset_server
run_pc envsh render EXTRA_DOMAINS=obca.example
! grep -q 'obca\.example' "$SRV/caddy/Caddyfile"
check "EXTRA_DOMAINS z powłoki nie trafia do konfiguracji (liczy się .env)" $?

# 6. caddy validate odrzuca – kod 1, zainstalowany plik bez zmian, bez pliku .next.
reset_server
run_pc v0 render
cp "$SRV/caddy/Caddyfile" "$WORK/caddy.before"
sed -i 's/^EXTRA_DOMAINS=$/EXTRA_DOMAINS=fizyczna.example/' "$SRV/.env"
run_pc vbad render STUB_VALIDATE_RC=1
[ $? -ne 0 ] && cmp -s "$WORK/caddy.before" "$SRV/caddy/Caddyfile" && [ ! -e "$SRV/caddy/Caddyfile.next" ] &&
  grep -qF 'caddy validate odrzucił' "$WORK/vbad.out"
check "odrzucony caddy validate: kod ≠ 0, zainstalowany plik bez zmian, bez .next" $?

# 6a. Wdrożenie zmienia obraz proxy (DEP-02: caddy 2.8 → 2.10): walidacja w jednorazowym kontenerze
# NOWEGO obrazu (`compose run`), nie w działającym starym – stary odrzuciłby `tls force_automate`.
reset_server
run_pc img render STUB_HAVE_IMAGE=caddy:2.8 STUB_WANT_IMAGE=caddy:2.10
rc=$?
[ $rc -eq 0 ] && [ "$(cat "$BOX/validated_by" 2>/dev/null)" = run ] && cmp -s "$BOX/validated" "$SRV/caddy/Caddyfile" \
  && ! grep -qF 'compose exec -T proxy sh -c' "$WORK/img.docker" && grep -qF 'nowym obrazie caddy:2.10 (działa caddy:2.8)' "$WORK/img.out"
check "render przy zmianie obrazu proxy: caddy validate w nowym obrazie (compose run), nie w działającym" $?
show_on_fail $rc "$WORK/img.out"
run_pc imgbad render STUB_HAVE_IMAGE=caddy:2.8 STUB_VALIDATE_RC=1
[ $? -ne 0 ] && grep -qF 'caddy validate odrzucił nową konfigurację (nowym obrazie caddy:2.10' "$WORK/imgbad.out"
check "odrzucenie w nowym obrazie: kod ≠ 0 z nazwą obrazu" $?

# 7. render przy niedziałającym proxy – bez walidacji, plik zainstalowany (start proxy go wczyta).
reset_server "" down
run_pc rdown render
[ $? -eq 0 ] && [ -s "$SRV/caddy/Caddyfile" ] && ! grep -q 'caddy validate' "$WORK/rdown.docker" &&
  grep -qF 'walidacja pominięta' "$WORK/rdown.out"
check "render przy niedziałającym proxy: plik zainstalowany, walidacja pominięta z komunikatem" $?

# 8. apply przy działającym proxy (montaż katalogu): caddy reload, bez odtwarzania.
reset_server
run_pc a0 render
run_pc a1 apply
rc=$?
check "apply (proxy widzi caddy/) kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/a1.out"
grep -qxF "$RELOAD" "$WORK/a1.docker" && ! grep -qxF "$RECREATE" "$WORK/a1.docker" && cmp -s "$BOX/loaded" "$SRV/caddy/Caddyfile"
check "apply: caddy reload załadował caddy/Caddyfile, kontener nie odtwarzany" $?

# 9. update po zmianie EXTRA_DOMAINS – nowa domena w konfiguracji załadowanej, bez odtwarzania.
sed -i 's/^EXTRA_DOMAINS=$/EXTRA_DOMAINS=fizyczna.example www.fizyczna.example/' "$SRV/.env"
run_pc upd update
rc=$?
check "update kończy się powodzeniem" $rc
show_on_fail $rc "$WORK/upd.out"
grep -q '^fizyczna\.example {' "$BOX/loaded" && ! grep -qxF "$RECREATE" "$WORK/upd.docker"
check "update: nowa domena załadowana w proxy przez caddy reload, bez restartu" $?

# 10. apply, gdy kontener widzi inną treść (montaż sprzed CADDY_CONFIG_DIR) – odtworzenie.
reset_server "" stale
run_pc s0 render
run_pc s1 apply
rc=$?
[ $rc -eq 0 ] && grep -qxF "$RECREATE" "$WORK/s1.docker" && ! grep -qxF "$RELOAD" "$WORK/s1.docker" &&
  [ "$(cat "$BOX/state")" = live ] && cmp -s "$BOX/loaded" "$SRV/caddy/Caddyfile" && grep -qF 'odtwarzam proxy' "$WORK/s1.out"
check "apply przy starym montażu: kontener odtworzony, widzi i ma załadowany caddy/Caddyfile" $?
show_on_fail $rc "$WORK/s1.out"

# 11. apply przy niedziałającym proxy – uruchomienie od nowa.
reset_server "" down
run_pc d0 render
run_pc d1 apply
[ $? -eq 0 ] && grep -qxF "$RECREATE" "$WORK/d1.docker" && [ "$(cat "$BOX/state")" = live ] && grep -qF 'nie działa' "$WORK/d1.out"
check "apply przy niedziałającym proxy: kontener uruchomiony z caddy/Caddyfile" $?

# 12. Odtworzony kontener dalej widzi coś innego (compose montuje inny katalog) – kod ≠ 0 z podpowiedzią.
reset_server "" stale
run_pc w0 render
run_pc w1 apply STUB_RECREATE_STALE=1
[ $? -ne 0 ] && grep -qF 'CADDY_CONFIG_DIR' "$WORK/w1.out"
check "apply: po odtworzeniu proxy wciąż nie widzi pliku – kod ≠ 0, podpowiedź CADDY_CONFIG_DIR" $?

# 13. caddy reload odrzuca – kod ≠ 0, proxy zostaje przy poprzedniej konfiguracji.
reset_server
run_pc rl0 render
run_pc rl1 apply
cp "$BOX/loaded" "$WORK/loaded.before"
sed -i 's/^EXTRA_DOMAINS=$/EXTRA_DOMAINS=fizyczna.example/' "$SRV/.env"
run_pc rl2 render
run_pc rl3 apply STUB_RELOAD_RC=1
[ $? -ne 0 ] && cmp -s "$WORK/loaded.before" "$BOX/loaded" && grep -qF 'POPRZEDNIEJ' "$WORK/rl3.out" &&
  ! grep -qxF "$RECREATE" "$WORK/rl3.docker"
check "odrzucony caddy reload: kod ≠ 0, poprzednia konfiguracja zostaje, bez odtwarzania" $?

# 13a. …i plik na dysku wraca do poprzedniej treści (tej, na której proxy działa): restart
#      kontenera nie może wczytać konfiguracji, której Caddy właśnie odmówił.
cmp -s "$WORK/loaded.before" "$SRV/caddy/Caddyfile" && ! grep -q fizyczna "$SRV/caddy/Caddyfile" && grep -qF 'przywrócony' "$WORK/rl3.out"
check "odrzucony caddy reload: caddy/Caddyfile przywrócony z Caddyfile.prev" $?

# 13b. Odtworzenie (stary montaż), a Caddy z nową treścią nie wstaje – powrót do poprzedniej, drugi
#      start, kod ≠ 0. Proxy działa na poprzedniej konfiguracji, plik na dysku – ta sama treść.
reset_server "" stale
mkdir -p "$SRV/caddy"; cp "$ROOT/deploy/Caddyfile" "$SRV/caddy/Caddyfile"
sed -i 's/^EXTRA_DOMAINS=$/EXTRA_DOMAINS=fizyczna.example/' "$SRV/.env"
run_pc bs0 render
run_pc bs1 apply STUB_BROKEN_MARK=fizyczna.example
rc=$?
[ $rc -ne 0 ] && [ "$(grep -cxF "$RECREATE" "$WORK/bs1.docker")" = 2 ] && cmp -s "$ROOT/deploy/Caddyfile" "$SRV/caddy/Caddyfile" &&
  cmp -s "$BOX/loaded" "$SRV/caddy/Caddyfile" && grep -qF 'POPRZEDNIEJ' "$WORK/bs1.out"
rc=$?
check "apply: Caddy nie wstaje z nową treścią – powrót do Caddyfile.prev, proxy odtworzone z niej, kod ≠ 0" $rc
show_on_fail $rc "$WORK/bs1.out"

# 13c. Pierwszy render na serwerze (brak caddy/Caddyfile), proxy działa ze starym montażem, a nowa
#      treść odpada w caddy validate: w caddy/ zostaje kopia tego, co proxy widzi (każde odtworzenie
#      proxy po zmianie .env montuje ten katalog – nie może być pusty).
reset_server "" stale
run_pc seed render STUB_VALIDATE_RC=1
[ $? -ne 0 ] && [ "$(cat "$SRV/caddy/Caddyfile")" = "# stara treść" ] && grep -qF 'kopia konfiguracji' "$WORK/seed.out" &&
  [ ! -e "$SRV/caddy/Caddyfile.seed" ]
check "pierwszy render, odrzucony validate: caddy/Caddyfile = kopia konfiguracji działającego proxy" $?
reset_server "" stale
run_pc seed2 render
cmp -s "$ROOT/deploy/Caddyfile" "$SRV/caddy/Caddyfile" && [ "$(cat "$SRV/caddy/Caddyfile.prev")" = "# stara treść" ]
check "pierwszy render, udany: nowa treść zainstalowana, poprzednia (z proxy) w Caddyfile.prev" $?
reset_server "" down
run_pc seed3 render
cmp -s "$ROOT/deploy/Caddyfile" "$SRV/caddy/Caddyfile" && ! grep -q 'proxy cat' "$WORK/seed3.docker" && [ ! -e "$SRV/caddy/Caddyfile.prev" ]
check "pierwszy render przy niedziałającym proxy: bez kopii, nowa treść zainstalowana" $?

# 13d. Tryb djcms: .env w innym trybie niż konfiguracja w działającym proxy (przerwane `djcms_switch.sh
#      on` albo ręczna zmiana DJCMS_PRIMARY) – render odmawia, zanim cokolwiek zapisze; zgodny – przechodzi.
{ cat "$WORK/env.fixture"; echo "DJCMS_ENABLED=1"; } >"$WORK/env.dj"
reset_server "$WORK/env.dj"
run_pc md0 render
grep -q 'header_up X-Djcms-Mode preview' "$SRV/caddy/Caddyfile"
check "render przy DJCMS_ENABLED=1: tryb preview w caddy/Caddyfile" $?
cp "$SRV/caddy/Caddyfile" "$WORK/caddy.md0"
echo "DJCMS_PRIMARY=1" >>"$SRV/.env"
run_pc md1 render
[ $? -ne 0 ] && cmp -s "$WORK/caddy.md0" "$SRV/caddy/Caddyfile" && grep -qF 'djcms_switch.sh on' "$WORK/md1.out" &&
  grep -qF 'tryb primary, a działające proxy ma tryb preview' "$WORK/md1.out" && ! grep -q 'caddy validate' "$WORK/md1.docker"
check "DJCMS_PRIMARY=1 w .env przy proxy w trybie preview – odmowa (djcms_switch.sh on), plik bez zmian" $?
sed -i 's/^DJCMS_PRIMARY=1$/DJCMS_PRIMARY=0/' "$SRV/.env"
run_pc md2 render
check "tryb w .env zgodny z działającym proxy – render przechodzi" $?
reset_server "$WORK/env.dj" down
echo "DJCMS_PRIMARY=1" >>"$SRV/.env"
run_pc md3 render
[ $? -eq 0 ] && grep -q 'header_up X-Djcms-Mode primary' "$SRV/caddy/Caddyfile"
check "proxy nie działa – nie ma z czym porównać trybu, render przechodzi" $?

# 13e. Blokada trzymana przez wdrożenie (OLIMPIADA_PROXY_LOCK=held) – update bez własnego flock.
reset_server
run_pc held update OLIMPIADA_PROXY_LOCK=held
check "update przy OLIMPIADA_PROXY_LOCK=held (blokada wdrożenia) przechodzi" $?
if command -v flock >/dev/null 2>&1; then
  reset_server
  mkdir -p "$SRV/caddy"
  ( exec 8>"$SRV/caddy/.lock"; flock 8; sleep 3 ) &
  holder=$!
  sleep 1
  run_pc wait render
  rc=$?
  wait "$holder"
  [ $rc -eq 0 ]
  check "render czeka na blokadę trzymaną przez inny proces (flock -w) i przechodzi po jej zwolnieniu" $?
fi

# 14. apply bez pliku – odmowa bez dockera.
reset_server
run_pc nofile apply
[ $? -ne 0 ] && grep -qF 'brak caddy/Caddyfile' "$WORK/nofile.out" && [ ! -s "$WORK/nofile.docker" ]
check "apply bez caddy/Caddyfile: odmowa bez dockera" $?

# 15. status.
reset_server
run_pc st0 render
run_pc st1 status
[ $? -eq 0 ] && grep -qF 'zgodny z deploy/Caddyfile i .env' "$WORK/st1.out" && grep -qF 'kontener proxy: widzi caddy/Caddyfile' "$WORK/st1.out"
check "status: plik zgodny z .env, kontener widzi caddy/" $?
sed -i 's/^EXTRA_DOMAINS=$/EXTRA_DOMAINS=fizyczna.example/' "$SRV/.env"
echo stale >"$BOX/state"
run_pc st2 status
grep -qF 'NIEZGODNY' "$WORK/st2.out" && grep -qF 'widzi INNĄ treść' "$WORK/st2.out"
check "status: plik niezgodny z .env i kontener ze starym montażem – obie rzeczy nazwane" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło (KEEP_WORK=1 zostawia %s).\n' "$failures" "$WORK"
  exit 1
fi
printf '\nWszystkie testy konfiguracji proxy przeszły.\n'
