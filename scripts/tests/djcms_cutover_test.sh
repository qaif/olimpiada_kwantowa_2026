#!/usr/bin/env bash
# Test skryptu przełączenia (`scripts/djcms_cutover.sh`, docs/tasks/DJ-02.md § 10.1, § 10.4).
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu; bez Dockera):
#   scripts/tests/djcms_cutover_test.sh
#
# Skrypt przełączenia biegnie naprawdę w piaskownicy udającej /opt/olimpiada. Atrapy modelują to,
# co ma znaczenie dla kolejności i semantyki przerwania:
#   - `docker` – stan w $STATE: zamrożenie Wagtaila (plik `frozen`, `cms_freeze on|off|status` z kodami
#     jak prawdziwa komenda: status 0 = zamrożone, 1 = otwarte), import (`imported`), odpowiedzi
#     kontroli (kontrakt tras, aliasy, rejestr, import próbny) i tabela `verify_cutover`; każde
#     wywołanie trafia do dziennika, więc test sprawdza, CZEGO skrypt nie zrobił,
#   - `scripts/djcms_switch.sh`, `scripts/backup.sh`, `scripts/backup_verify.sh` – atrapy w piaskownicy
#     (przełącznik ustawia DJCMS_PRIMARY w .env jak prawdziwy, a przy porażce zostawia 0 – jak jego
#     automatyczny powrót). Sam przełącznik sprawdza scripts/tests/djcms_switch_test.sh.
# Porażkę każdego kroku wymusza zmienna STUB_*.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/djcms-cutover-test.XXXXXX")"
trap '[ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"' EXIT
SRV="$WORK/srv" STATE="$WORK/state" BIN="$WORK/bin" LOGS="$WORK/logs"
mkdir -p "$BIN"

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}

bash -n "$ROOT/scripts/djcms_cutover.sh"
check "scripts/djcms_cutover.sh przechodzi bash -n" $?

# --- Atrapy ------------------------------------------------------------------------------------
cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
case "$*" in
  "compose ps --format {{.Service}}={{.Health}}")
    printf 'web=healthy\nproxy=healthy\ndjcms=%s\n' "${STUB_DJCMS_HEALTH:-healthy}" ;;
  "compose exec -T proxy sha256sum /etc/caddy/Caddyfile")
    if [ "${STUB_PROXY_STALE:-0}" = 1 ]; then echo "0000  /etc/caddy/Caddyfile"
    else printf '%s  /etc/caddy/Caddyfile\n' "$(sha256sum caddy/Caddyfile | cut -d' ' -f1)"; fi ;;
  "compose exec -T web python manage.py cms_freeze status")
    [ -n "${STUB_FREEZE_STATUS_RC:-}" ] && { echo "Traceback (atrapa)"; exit "$STUB_FREEZE_STATUS_RC"; }
    if [ -f "$STATE/frozen" ]; then echo "Zamrożenie edycji stron: WŁĄCZONE – $(cat "$STATE/frozen")"; exit 0; fi
    echo "Zamrożenie edycji stron: WYŁĄCZONE (strony w /cms/ edytowalne)."; exit 1 ;;
  "compose exec -T web python manage.py cms_freeze on "*)
    [ "${STUB_FREEZE_ON_RC:-0}" = 0 ] || exit "$STUB_FREEZE_ON_RC"
    printf '%s\n' "$*" >"$STATE/frozen"; echo "Zamrożenie edycji stron: WŁĄCZONE" ;;
  "compose exec -T web python manage.py cms_freeze off "*)
    rm -f "$STATE/frozen"; echo "Zamrożenie edycji stron: WYŁĄCZONE" ;;
  "compose exec -T web python manage.py djcms_routes --check")
    [ "${STUB_ROUTES_RC:-0}" = 0 ] && echo "Kontrakt tras aktualny." || { echo "--- app_routes.env"; exit 1; } ;;
  "compose exec -T web python manage.py djcms_routes --format env")
    if [ "${STUB_ROUTES_ENV_DIFF:-0}" = 1 ]; then echo "APP_RE='^/inny$'"; else cat backend/djcms_contract/app_routes.env; fi ;;
  "compose exec -T web python manage.py shell -c "*)
    printf 'DJCMS_CUTOVER_ALIASES=%s\r\n' "${STUB_ALIASES:-}" ;;
  "compose exec -T djcms python manage.py sync_competitions --dry-run")
    printf '[dry-run] nowe: –\n[dry-run] bez zmian: fizyczna, kwantowa\n'; exit "${STUB_SYNC_DRY_RC:-0}" ;;
  "compose exec -T djcms python manage.py sync_competitions")
    echo "bez zmian: fizyczna, kwantowa"; exit "${STUB_SYNC_RC:-0}" ;;
  "compose exec -T djcms python manage.py import_cms_bundle --from-api --all --replace"*"--dry-run")
    echo "== Konkurs „kwantowa” =="; exit "${STUB_IMPORT_DRY_RC:-0}" ;;
  "compose exec -T djcms python manage.py import_cms_bundle --from-api --all --replace"*)
    [ "${STUB_IMPORT_RC:-0}" = 0 ] || { echo "Import nie powiódł się dla: kwantowa" >&2; exit "$STUB_IMPORT_RC"; }
    printf '%s\n' "$*" >"$STATE/imported"; echo "import_cms_bundle: gotowe." ;;
  "compose exec -T djcms python manage.py verify_cutover")
    row() { printf '%-20s %11s %11s %10s  %s\n' "$@"; }
    case "${STUB_VERIFY:-ok}" in
      crash) echo "CommandError: Brak aktywnych konkursów w rejestrze" >&2; exit 1 ;;
    esac
    row konkurs strony 'adresy 200' przekier. wynik
    echo "--------------------------------------------------------------"
    case "${STUB_VERIFY:-ok}" in
      ok) row fizyczna 5/5 7/7 0/0 OK; row kwantowa 14/14 18/18 3/3 OK; echo "verify_cutover: 2 witryn gotowych do przełączenia."; exit 0 ;;
      fizyczna) row fizyczna 6/5 7/7 0/0 BŁĄD; row kwantowa 14/14 18/18 3/3 OK
        echo "  ✗ fizyczna: stron opublikowanych 6, w paczce 5"; echo "verify_cutover: 1 z 2 witryn nie przeszło kontroli." >&2; exit 1 ;;
      kwantowa) row fizyczna 6/5 7/7 0/0 BŁĄD; row kwantowa 13/14 17/18 3/3 BŁĄD
        echo "  ✗ kwantowa: https://olimpiada.example/faq/ → 404"; echo "verify_cutover: 2 z 2 witryn nie przeszło kontroli." >&2; exit 1 ;;
    esac ;;
  *) echo "docker (atrapa): nieoczekiwane polecenie: $*" >&2; exit 97 ;;
esac
STUB
chmod +x "$BIN/docker"

# Atrapy skryptów w piaskownicy (prawdziwe mają własne testy).
make_script_stubs() {
  cat >"$SRV/scripts/djcms_switch.sh" <<'STUB'
#!/usr/bin/env bash
printf 'switch %s\n' "$*" >>"$DOCKER_LOG"
set_primary() { if grep -q '^DJCMS_PRIMARY=' .env; then sed -i "s/^DJCMS_PRIMARY=.*/DJCMS_PRIMARY=$1/" .env; else echo "DJCMS_PRIMARY=$1" >>.env; fi; }
case "$1" in
  on)  if [ "${STUB_SWITCH_RC:-0}" != 0 ]; then echo "!!! Przełączenie nie powiodło się – wracam do DJCMS_PRIMARY=0 (Wagtail)"; set_primary 0; exit 1; fi
       set_primary 1; echo "Gotowe: strony publiczne z django CMS (djcms)." ;;
  off) [ "${STUB_SWITCH_OFF_RC:-0}" = 0 ] || exit 1
       set_primary 0; echo "Gotowe: strony publiczne z Wagtaila (web)." ;;
esac
STUB
  cat >"$SRV/scripts/backup.sh" <<'STUB'
#!/usr/bin/env bash
printf 'backup.sh %s\n' "$*" >>"$DOCKER_LOG"; echo "==> Gotowe: kopia (atrapa)"; exit "${STUB_BACKUP_RC:-0}"
STUB
  cat >"$SRV/scripts/backup_verify.sh" <<'STUB'
#!/usr/bin/env bash
printf 'backup_verify.sh %s\n' "$*" >>"$DOCKER_LOG"; echo "==> Test odtwarzania (atrapa)"; exit "${STUB_BVERIFY_RC:-0}"
STUB
}

cat >"$WORK/env.fixture" <<'ENV'
SITE_DOMAIN=olimpiada.example
EXTRA_DOMAINS=fizyczna.example
CADDY_CONFIG_DIR=./caddy
DJCMS_ENABLED=1
COMPOSE_FILE=docker-compose.yml:docker-compose.djcms.yml
COMPOSE_PROFILES=djcms
DJCMS_PRIMARY=0
ENV

reset_server() {  # reset_server [plik .env] – świeża instalacja: DJCMS_PRIMARY=0, Wagtail otwarty
  rm -rf "${SRV:?}" "${STATE:?}" "${LOGS:?}"
  mkdir -p "$SRV/scripts" "$SRV/caddy" "$SRV/backend/djcms_contract" "$STATE" "$LOGS" "$WORK/bak"
  cp "$ROOT/scripts/djcms_cutover.sh" "$SRV/scripts/"
  cp "$ROOT/backend/djcms_contract/app_routes.env" "$SRV/backend/djcms_contract/"
  printf '{\n  admin off\n}\n# header_up X-Djcms-Mode preview\n' >"$SRV/caddy/Caddyfile"
  cp "${1:-$WORK/env.fixture}" "$SRV/.env"
  make_script_stubs
}

run_cut() {  # run_cut <etykieta> [argumenty skryptu…] [-- ZMIENNA=wartość…] – kod wyjścia
  local label="$1"; shift
  local args=() envs=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do args+=("$1"); shift; done
  [ $# -gt 0 ] && shift
  envs=("$@")
  DOCKER_LOG="$WORK/$label.docker"; OUT="$WORK/$label.out"
  : >"$DOCKER_LOG"
  ( cd "$SRV" && env -u DJCMS_CUTOVER_CHILD -u DJCMS_CUTOVER_LOG \
      PATH="$BIN:$PATH" DOCKER_LOG="$DOCKER_LOG" STATE="$STATE" DJCMS_CUTOVER_LOG_DIR="$LOGS" \
      BACKUP_DIR="$WORK/bak" DJCMS_CUTOVER_MIN_FREE_MB=1 "${envs[@]+"${envs[@]}"}" \
      bash scripts/djcms_cutover.sh "${args[@]+"${args[@]}"}" ) </dev/null >"$OUT" 2>&1
}
show_on_fail() { [ "$1" -eq 0 ] || sed 's/^/     /' "$2"; }
env_line() { sed -n "s/^$1=//p" "$SRV/.env" | tail -n 1; }
# Polecenia zmieniające stan (kopia, zamrożenie, rejestr, import, przełącznik) – żadne w --check.
MUTATING='backup\.sh|cms_freeze (on|off)|sync_competitions$|import_cms_bundle .*--replace( --skip [a-z0-9-]+)*$|verify_cutover|^switch '
mutations() { grep -E "$MUTATING" "$WORK/$1.docker" | grep -v '^backup_verify' ; }
# Pozycja pierwszego wiersza dziennika pasującego do wzorca (0 = brak).
pos() { local n; n="$(grep -nE "$2" "$WORK/$1.docker" | head -n 1 | cut -d: -f1)"; echo "${n:-0}"; }

# ================================================================================================
# 1. Argumenty – kod 2, zanim cokolwiek się stanie.
# ================================================================================================
reset_server
for bad in "--nie-wiem" "--unfreeze" "--skip" "--skip Zly_Slug" "--rollback --skip kwantowa" "--no-backup-verify"; do
  # shellcheck disable=SC2086
  run_cut arg $bad
  rc=$?
  [ $rc -eq 2 ] && [ ! -s "$WORK/arg.docker" ] && [ -z "$(ls -A "$LOGS")" ]
  check "argumenty „$bad” – kod 2, bez dockera i bez dziennika" $?
done
run_cut help --help
[ $? -eq 0 ] && grep -q -- '--rollback \[--unfreeze\]' "$WORK/help.out" && grep -q -- '--force-reimport' "$WORK/help.out"
check "--help wypisuje nagłówek z trybami i opcjami" $?

# ================================================================================================
# 2. --check: wszystkie kontrole, nic zmieniającego stanu; dziennik.
# ================================================================================================
reset_server
cp "$SRV/.env" "$WORK/env.before"
run_cut chk --check
rc=$?
check "--check na gotowym serwerze – kod 0" $rc
show_on_fail $rc "$WORK/chk.out"
[ -z "$(mutations chk)" ] && cmp -s "$WORK/env.before" "$SRV/.env" && [ ! -e "$STATE/frozen" ]
check "--check: żadnego polecenia zmieniającego stan, .env nietknięty, Wagtail otwarty" $?
for want in 'djcms_routes --check' 'djcms_routes --format env' 'manage.py shell -c' 'sync_competitions --dry-run' \
            'import_cms_bundle --from-api --all --replace --dry-run' 'cms_freeze status' 'proxy sha256sum' '^backup_verify.sh'; do
  grep -qE -- "$want" "$WORK/chk.docker"
  check "--check sprawdza: $want" $?
done
! grep -q '^   FAIL' "$WORK/chk.out" && grep -qF 'nic nie zostało zmienione' "$WORK/chk.out"
check "--check: bez porażek, komunikat „nic nie zostało zmienione”" $?
logf="$(ls "$LOGS"/olimpiada-djcms-cutover-*.log 2>/dev/null | head -n 1)"
[ -n "$logf" ] && cmp -s "$logf" "$WORK/chk.out"
check "dziennik w DJCMS_CUTOVER_LOG_DIR = pełne wyjście przebiegu" $?

# Każda kontrola osobno zatrzymuje (kod 1) i nic nie zmienia; wszystkie porażki zebrane w jednym przebiegu.
for case_ in "STUB_ALIASES=fizyczna|aliasy językowe" "STUB_ROUTES_RC=1|djcms_routes --check" \
             "STUB_ROUTES_ENV_DIFF=1|różni się od kontraktu" "STUB_PROXY_STALE=1|proxy nie działa albo widzi inną" \
             "STUB_DJCMS_HEALTH=unhealthy|djcms nie jest healthy" "STUB_SYNC_DRY_RC=1|sync_competitions --dry-run" \
             "STUB_IMPORT_DRY_RC=1|import_cms_bundle --all --replace --dry-run (kod" "STUB_FREEZE_STATUS_RC=3|cms_freeze status nie odpowiada" \
             "STUB_BVERIFY_RC=1|backup_verify ostatniej kopii nie przeszedł" "DJCMS_CUTOVER_MIN_FREE_MB=999999999|wolne miejsce"; do
  var="${case_%%|*}" msg="${case_#*|}"
  reset_server
  run_cut chkbad --check -- "$var"
  rc=$?
  [ $rc -eq 1 ] && grep -F '   FAIL' "$WORK/chkbad.out" | grep -qF -- "$msg" && [ -z "$(mutations chkbad)" ]
  check "--check z $var – kod 1, FAIL „$msg”, nic nie zmienione" $?
done
# .env sprzed konfiguracji proxy w caddy/ (CADDYFILE_PATH, bez CADDY_CONFIG_DIR) – FAIL z podpowiedzią wdrożenia.
reset_server
sed -i 's|^CADDY_CONFIG_DIR=.*|CADDYFILE_PATH=./deploy/Caddyfile.generated|' "$SRV/.env"
run_cut chkdir --check
[ $? -eq 1 ] && grep -F '   FAIL' "$WORK/chkdir.out" | grep -qF 'CADDY_CONFIG_DIR' && [ -z "$(mutations chkdir)" ]
check "--check przy .env bez CADDY_CONFIG_DIR=./caddy – kod 1, FAIL z podpowiedzią wdrożenia" $?
reset_server
run_cut chk2 --check -- STUB_ALIASES=fizyczna STUB_ROUTES_RC=1
[ $? -eq 1 ] && [ "$(grep -c '^   FAIL' "$WORK/chk2.out")" -ge 2 ] && grep -q '^backup_verify' "$WORK/chk2.docker"
check "--check zbiera wszystkie porażki (nie staje na pierwszej)" $?
reset_server
sed -i '/^DJCMS_ENABLED=/d' "$SRV/.env"
run_cut chkoff --check
[ $? -eq 1 ] && grep -qF 'DJCMS_ENABLED w .env nie jest 1' "$WORK/chkoff.out" && [ ! -s "$WORK/chkoff.docker" ]
check "--check bez DJCMS_ENABLED=1 – kod 1 bez żadnego polecenia dockera" $?

# ================================================================================================
# 3. --dry-run: kontrole + plan z dokładnymi poleceniami (także --skip), bez wykonania.
# ================================================================================================
reset_server
run_cut dry --dry-run --skip fizyczna --no-backup-verify
rc=$?
check "--dry-run --skip fizyczna – kod 0" $rc
show_on_fail $rc "$WORK/dry.out"
[ -z "$(mutations dry)" ] && ! grep -q 'backup_verify' "$WORK/dry.docker" && [ ! -e "$STATE/frozen" ]
check "--dry-run: nic nie wykonane, --no-backup-verify pomija test odtwarzania" $?
grep -qF 'import_cms_bundle --from-api --all --replace --skip fizyczna --dry-run' "$WORK/dry.docker"
check "--dry-run: import próbny dostaje --skip" $?
for want in '1. bash scripts/backup.sh && bash scripts/backup_verify.sh' 'cms_freeze on --message "Edycja treści przeniesiona do django CMS"' \
            '3. docker compose exec -T djcms python manage.py sync_competitions' \
            '4. docker compose exec -T djcms python manage.py import_cms_bundle --from-api --all --replace --skip fizyczna' \
            '6. bash scripts/djcms_switch.sh on' 'Wykonanie: bash scripts/djcms_cutover.sh --skip fizyczna'; do
  grep -qF -- "$want" "$WORK/dry.out"
  check "--dry-run: plan zawiera „$want”" $?
done

# ================================================================================================
# 4. Pełny przebieg: potwierdzenie, kolejność kroków, stan końcowy.
# ================================================================================================
reset_server
cp "$SRV/.env" "$WORK/env.before"
run_cut noyes
[ $? -eq 2 ] && grep -qF -- '--yes' "$WORK/noyes.out" && [ -z "$(mutations noyes)" ] && cmp -s "$WORK/env.before" "$SRV/.env" &&
  grep -qF 'Nic nie zostało zmienione' "$WORK/noyes.out"
check "bez terminala i bez --yes – kod 2 po kontrolach, nic nie zmienione" $?

reset_server
run_cut full --yes
rc=$?
check "pełny przebieg – kod 0" $rc
show_on_fail $rc "$WORK/full.out"
order="$(pos full '^backup\.sh') $(pos full '^backup_verify') $(pos full 'cms_freeze on') $(pos full 'sync_competitions$') \
$(pos full 'import_cms_bundle .*--replace$') $(pos full 'verify_cutover') $(pos full '^switch on')"
sorted="$(printf '%s\n' $order | sort -n | tr '\n' ' ' | sed 's/ $//')"
[ "$sorted" = "$(echo $order)" ] && ! printf '%s\n' $order | grep -qx 0
check "kolejność: kopia → test odtwarzania → cms_freeze on → sync → import --replace → verify → switch on [$(echo $order)]" $?
[ "$(grep -c '^backup_verify' "$WORK/full.docker")" = 1 ] && [ "$(pos full '^backup_verify')" -gt "$(pos full '^backup\.sh')" ]
check "test odtwarzania raz – na świeżej kopii, nie na poprzedniej" $?
[ "$(pos full 'sync_competitions --dry-run')" -lt "$(pos full '^backup\.sh')" ] &&
  [ "$(pos full 'import_cms_bundle .*--dry-run')" -lt "$(pos full '^backup\.sh')" ]
check "kontrole (rejestr, import próbny) przed kopią" $?
[ "$(env_line DJCMS_PRIMARY)" = 1 ] && env_line DJCMS_CUTOVER_DONE | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' && [ -f "$STATE/frozen" ]
check "stan końcowy: DJCMS_PRIMARY=1, znacznik DJCMS_CUTOVER_DONE, Wagtail zamrożony" $?
grep -qF -- '--message Edycja treści przeniesiona do django CMS --by scripts/djcms_cutover.sh' "$STATE/frozen"
check "cms_freeze on z komunikatem banera i autorem" $?
grep -qE '^ +kwantowa +14/14 +18/18 +3/3 +OK' "$WORK/full.out" && grep -qF 'djcms_cutover.sh --rollback' "$WORK/full.out" &&
  grep -qF 'NIE wracają' "$WORK/full.out"
check "podsumowanie: tabela stron i przekierowań, polecenie wycofania, ostrzeżenie o stratności" $?

# Idempotentny ponowny przebieg: po udanym przełączeniu – nic (bez ponownego importu).
cp "$SRV/.env" "$WORK/env.after"
run_cut again --yes
rc=$?
[ $rc -eq 0 ] && [ ! -s "$WORK/again.docker" ] && cmp -s "$WORK/env.after" "$SRV/.env" && grep -qF 'nic do zrobienia' "$WORK/again.out"
check "ponowny przebieg po przełączeniu – kod 0, żadnego polecenia (import skasowałby redakcję djcms)" $?
run_cut again2 --check
[ $? -eq 0 ] && [ ! -s "$WORK/again2.docker" ]
check "--check po przełączeniu – kod 0, nic do sprawdzania" $?

# ================================================================================================
# 5. Porażka każdego kroku: kod ≠ 0, nic po nim, stan opisany w komunikacie.
# ================================================================================================
# <zmienna> | <czego NIE ma w dzienniku> | <fragment komunikatu> | <Wagtail zamrożony po przerwaniu: 0/1>
while IFS='|' read -r var absent msg frozen; do
  [ -n "$var" ] || continue
  reset_server
  run_cut fail --yes -- "$var"
  rc=$?
  [ $rc -ne 0 ] && ! grep -qE -- "$absent" "$WORK/fail.docker" && grep -qF -- "$msg" "$WORK/fail.out" &&
    [ "$(env_line DJCMS_PRIMARY)" = 0 ] && [ -z "$(env_line DJCMS_CUTOVER_DONE)" ] &&
    { [ "$frozen" = 1 ] && [ -f "$STATE/frozen" ] || { [ "$frozen" = 0 ] && [ ! -f "$STATE/frozen" ]; }; }
  rc=$?
  check "porażka $var: kod ≠ 0, bez „$absent”, komunikat „$msg”, zamrożone=$frozen, DJCMS_PRIMARY=0" $rc
  show_on_fail $rc "$WORK/fail.out"
done <<'CASES'
STUB_ALIASES=kwantowa|^backup|Nic nie zostało zmienione|0
STUB_BACKUP_RC=1|cms_freeze on|Nic w serwisie nie zostało zmienione|0
STUB_BVERIFY_RC=1|cms_freeze on|Nic w serwisie nie zostało zmienione|0
STUB_FREEZE_ON_RC=1|sync_competitions$|stan niepewny|0
STUB_SYNC_RC=1|--replace$|--rollback --unfreeze|1
STUB_IMPORT_RC=1|verify_cutover|--rollback --unfreeze|1
STUB_VERIFY=kwantowa|^switch|nie przeszły witryny: fizyczna kwantowa|1
STUB_VERIFY=crash|^switch|verify_cutover nie powiódł się|1
STUB_SWITCH_RC=1|^switch off|przełącznik sam wrócił do DJCMS_PRIMARY=0|1
CASES
# Po przerwaniu w środku skrypt NIE odmraża Wagtaila sam (§ 10.1 p. 5) – decyzja operatora.
reset_server
run_cut failimp --yes -- STUB_IMPORT_RC=1
! grep -q 'cms_freeze off' "$WORK/failimp.docker" && grep -qF 'skrypt jej nie odmraża' "$WORK/failimp.out"
check "przerwanie po zamrożeniu: bez automatycznego cms_freeze off, komunikat o tym" $?

# Ponowienie po przerwaniu (poprawiona przyczyna) – przechodzi; zamrożenie i import powtórzone bezpiecznie.
reset_server
run_cut retry1 --yes -- STUB_VERIFY=kwantowa
run_cut retry2 --yes
rc=$?
[ $rc -eq 0 ] && grep -qF 'było już zamrożone' "$WORK/retry2.out" && grep -q 'import_cms_bundle .*--replace$' "$WORK/retry2.docker" &&
  [ "$(env_line DJCMS_PRIMARY)" = 1 ]
check "ponowienie po nieudanej weryfikacji – kod 0 (zamrożenie już było, import powtórzony, przełączone)" $rc
show_on_fail $rc "$WORK/retry2.out"

# ================================================================================================
# 6. --skip: import bez tego konkursu; jego porażka w weryfikacji to ostrzeżenie, innych – blokada.
# ================================================================================================
reset_server
run_cut skip --yes --skip fizyczna -- STUB_VERIFY=fizyczna
rc=$?
check "--skip fizyczna, weryfikacja nie przeszła tylko dla fizycznej – kod 0" $rc
show_on_fail $rc "$WORK/skip.out"
grep -qF -- '--replace --skip fizyczna' "$STATE/imported" && grep -qF 'UWAGA: weryfikacja nie przeszła dla konkursów z --skip: fizyczna' "$WORK/skip.out" &&
  [ "$(env_line DJCMS_PRIMARY)" = 1 ] && grep -qF 'Bez importu (treść djcms zachowana): fizyczna' "$WORK/skip.out"
check "--skip: przekazany do importu, ostrzeżenie, przełączone, podsumowanie wymienia pominięty konkurs" $?
reset_server
run_cut skip2 --yes --skip fizyczna -- STUB_VERIFY=kwantowa
[ $? -ne 0 ] && ! grep -q '^switch' "$WORK/skip2.docker" && grep -qF 'nie przeszły witryny: kwantowa' "$WORK/skip2.out"
check "--skip fizyczna, a nie przeszła kwantowa – bez przełączenia" $?

# ================================================================================================
# 7. Wycofanie i ponowne przełączenie.
# ================================================================================================
reset_server
run_cut full2 --yes
run_cut rb --rollback
rc=$?
[ $rc -eq 0 ] && grep -qx 'switch off' "$WORK/rb.docker" && [ "$(env_line DJCMS_PRIMARY)" = 0 ] && [ -f "$STATE/frozen" ] &&
  ! grep -q 'cms_freeze off' "$WORK/rb.docker" && grep -qF -- '--rollback --unfreeze' "$WORK/rb.out" &&
  grep -qF 'bash scripts/djcms_switch.sh on' "$WORK/rb.out" && [ -n "$(env_line DJCMS_CUTOVER_DONE)" ]
check "--rollback: switch off, Wagtail nadal zamrożony (z podpowiedzią), znacznik przełączenia zostaje" $?
show_on_fail $rc "$WORK/rb.out"
run_cut rb2 --rollback --unfreeze
rc=$?
[ $rc -eq 0 ] && ! grep -q '^switch' "$WORK/rb2.docker" && grep -q 'cms_freeze off --by' "$WORK/rb2.docker" && [ ! -f "$STATE/frozen" ]
check "--rollback --unfreeze przy DJCMS_PRIMARY=0: bez przełącznika (już Wagtail), cms_freeze off" $?

# Po wycofaniu: pełny przebieg odmawia bez --force-reimport (import skasowałby zmiany z djcms).
cp "$SRV/.env" "$WORK/env.rb"
run_cut reimp --yes
[ $? -eq 1 ] && [ -z "$(mutations reimp)" ] && ! grep -q 'import_cms_bundle' "$WORK/reimp.docker" &&
  grep -qF 'bash scripts/djcms_switch.sh on' "$WORK/reimp.out" && cmp -s "$WORK/env.rb" "$SRV/.env"
check "po wycofaniu bez --force-reimport – kod 1, nic nie zmienione, podpowiedź djcms_switch.sh on" $?
run_cut reimpchk --check
[ $? -eq 0 ] && grep -qF -- '--force-reimport' "$WORK/reimpchk.out"
check "po wycofaniu --check przechodzi z ostrzeżeniem o --force-reimport" $?
run_cut reimp2 --yes --force-reimport
rc=$?
[ $rc -eq 0 ] && [ "$(env_line DJCMS_PRIMARY)" = 1 ] && grep -q 'import_cms_bundle .*--replace$' "$WORK/reimp2.docker"
check "po wycofaniu z --force-reimport – ponowne przełączenie" $rc
show_on_fail $rc "$WORK/reimp2.out"

# Nieudany switch off w --rollback – kod 1.
run_cut rbbad --rollback -- STUB_SWITCH_OFF_RC=1
[ $? -eq 1 ] && grep -qF 'djcms_switch.sh off nie powiódł się' "$WORK/rbbad.out"
check "--rollback: nieudany djcms_switch.sh off – kod 1 z komunikatem" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy djcms_cutover.sh przeszły.\n'
