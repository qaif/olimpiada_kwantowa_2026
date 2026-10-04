#!/usr/bin/env bash
# Test wyboru miejsca kopii poza serwerem i poleceń rclone w `scripts/backup.sh` / `restore.sh`.
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu):
#   scripts/tests/backup_offsite_test.sh
#
# Bez serwera, bez Dockera i bez Google: PRAWDZIWY `scripts/backup.sh` (i biblioteka
# `scripts/lib/backup_offsite.sh`) chodzi na atrapach `docker`, `gpg` i `date`. Atrapa `docker`
# zapisuje każde wywołanie (jedno na linię) i udaje rclone na katalogu
# `remote/` – tyle, żeby wysyłka, weryfikacja, retencja i test połączenia miały po czym chodzić.
# Sprawdzamy to, co pojedzie na produkcję: jakie zmienne i montowania dostaje kontener rclone,
# gdzie ląduje token Dysku (i gdzie NIE ląduje), co melduje aplikacji nieudana wysyłka.
#
# Drugi test, `backup_offsite_e2e_test.sh`, puszcza te same ścieżki przez prawdziwy obraz rclone.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/backup-offsite-test.XXXXXX")"
trap '[ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"' EXIT

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
for f in scripts/backup.sh scripts/restore.sh scripts/backup_verify.sh scripts/lib/backup_offsite.sh scripts/deploy.sh; do
  bash -n "$ROOT/$f"
  check "$f przechodzi bash -n" $?
done
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x -S warning "$ROOT/scripts/backup.sh" "$ROOT/scripts/restore.sh" "$ROOT/scripts/backup_verify.sh" "$ROOT/scripts/lib/backup_offsite.sh"
  check "shellcheck (warning) dla backup.sh, restore.sh, backup_verify.sh i biblioteki" $?
else
  printf 'skip shellcheck – brak w PATH\n'
fi

# Krok 2/8 wdrożenia nie może kasować katalogu sekretów (token odświeżany przez rclone).
grep -q "! -name secrets" "$ROOT/scripts/deploy.sh"
check "deploy.sh (krok 2/8) omija katalog secrets/" $?

# --- atrapy ----------------------------------------------------------------------------------
BIN="$WORK/bin"
mkdir -p "$BIN"
REAL_DATE="$(command -v date)"

cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  # --- wersja porównawcza dj. (przypadki 12–15); przed ogólnym `pg_dump`, bo ten łapie oba ---
  "compose exec -T db psql -X -U olimpiada -d olimpiada -Atc SELECT 1 FROM pg_database WHERE datname = 'olimpiada_djcms'")
    [ -z "${MOCK_DJCMS_DB_FAIL:-}" ] || exit 1
    echo "${MOCK_DJCMS_DB:-}" ; exit 0 ;;
  "compose exec -T db pg_dump -U olimpiada -d olimpiada_djcms -Fc")
    [ -z "${MOCK_DJCMS_DUMP_FAIL:-}" ] || { echo "pg_dump: udawany błąd" >&2; exit 1; }
    printf 'PGDMP-DJCMS' ; exit 0 ;;
  "compose --profile djcms ps -q --status running djcms") echo "${MOCK_DJCMS_RUNNING-djcid}" ; exit 0 ;;
  "compose --profile djcms exec -T djcms tar --sort=name -C /app/media -cf - .")
    [ -z "${MOCK_DJCMS_TAR_FAIL:-}" ] || exit 1
    tar -cf - -C "$FAKE_MEDIA" . ; exit $? ;;
  "compose exec -T db psql -X -U olimpiada -d postgres -Atc SELECT 1 FROM pg_roles WHERE rolname = 'olimpiada_djcms'")
    echo "${MOCK_DJCMS_ROLE-1}" ; exit 0 ;;
  # --- backup_verify.sh (OPS-01): żywy web, tymczasowy Postgres, kontener sprawdzeń ---
  "compose ps -q web") echo "${MOCK_WEB-webcid}" ; exit 0 ;;
  "compose ps -q db") echo dbcid ; exit 0 ;;
  "exec -i webcid python manage.py restore_check live-counts")
    [ -z "${MOCK_LIVE_FAIL:-}" ] || exit 1
    echo '{"accounts.User": 3, "core.AuditLog": 10}' ; exit 0 ;;
  "exec -i webcid python manage.py restore_check record")
    cat >"$CASE/record-stdin.json" ; exit 0 ;;
  "exec -i webcid python manage.py restore_check record --failure"*) exit 0 ;;
  "inspect -f {{.Image}} webcid") echo sha256:webimage ; exit 0 ;;
  "inspect -f {{.Created}} webcid") echo 2026-01-01T00:00:00.123456789Z ; exit 0 ;;
  "inspect -f {{range .Config.Env}}{{println .}}{{end}} webcid")
    printf 'DJANGO_SECRET_KEY=klucz-aplikacji\nDATABASE_URL=postgres://olimpiada:zywe@db:5432/olimpiada\nREDIS_URL=redis://redis:6379/0\nPOSTGRES_DB=olimpiada\n'
    exit 0 ;;
  "inspect -f {{.Id}} olimpiada-restore-check-"*)
    if [ -n "${MOCK_TARGET_IS_DB:-}" ]; then echo dbcid; else echo targetcid; fi ; exit 0 ;;
  "run -d --name olimpiada-restore-check-"*) exit 0 ;;
  "exec olimpiada-restore-check-"*" psql -U restorecheck -d restorecheck_djcms -Atc"*) echo "${MOCK_CMS_PAGES-16}" ; exit 0 ;;
  "exec -i olimpiada-restore-check-"*" pg_restore -U restorecheck -d restorecheck_djcms"*)
    cat >/dev/null; [ -z "${MOCK_DJCMS_RESTORE_FAIL:-}" ] || exit 1; exit 0 ;;
  "exec -i olimpiada-restore-check-"*" pg_restore -U restorecheck -d restorecheck_main"*)
    cat >"$CASE/restored.dump"; [ -z "${MOCK_RESTORE_FAIL:-}" ] || exit 1; exit 0 ;;
  "run --rm -i --network olimpiada-restore-check-net-"*)
    # Kontener sprawdzeń: wejście (nagłówek + lista plików) i plik środowiska do wglądu testu.
    prev=""
    for a in "$@"; do [ "$prev" = "--env-file" ] && cp "$a" "$CASE/app.env.copy"; prev="$a"; done
    cat >"$CASE/verify-input.txt"
    [ -z "${MOCK_VERIFY_CRASH:-}" ] || exit 2
    if head -1 "$CASE/verify-input.txt" | grep -q '"status":"fail"'; then
      echo '{"version": 1, "status": "failed", "failed": ["djcms"]}' ; exit 1
    fi
    echo '{"version": 1, "status": "ok", "failed": []}' ; exit 0 ;;
  "compose exec -T db pg_dump"*) printf 'PGDUMP-DATA' ; exit 0 ;;
  "compose ps -q minio") echo cid123 ; exit 0 ;;
  "inspect -f"*) echo proj_internal ; exit 0 ;;
  "compose exec -T web python manage.py record_backup_status"*) exit 0 ;;
  "run --rm --network"*) exit 0 ;;   # mc mirror – kubełki puste
esac
[ "${1:-}" = "run" ] || exit 0

# --- udawany rclone ---
data="" conf=""
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
  if [ "${args[$i]}" = "-v" ]; then
    spec="${args[$((i+1))]}"
    case "$spec" in
      *:/data:*) data="${spec%%:/data:*}" ;;
      *:/config/rclone) conf="${spec%:/config/rclone}" ;;
    esac
  fi
  if [[ "${args[$i]}" == rclone/rclone* ]]; then break; fi
  i=$((i+1))
done
cmd=("${args[@]:$((i+1))}")
for pattern in ${MOCK_FAIL:-}; do
  case "${cmd[*]}" in *"$pattern"*) echo "rclone: udawany błąd ($pattern)" >&2; exit 1 ;; esac
done
# Symulacja odświeżenia tokenu przez rclone: nowy access_token zapisany do pliku konfiguracji.
if [ -n "$conf" ] && [ -n "${MOCK_REFRESH:-}" ]; then
  sed -i 's/"access_token":"[^"]*"/"access_token":"ODSWIEZONY"/' "$conf/rclone.conf"
fi
rpath() { local p="${1#offsite:}"; printf '%s/%s' "$REMOTE" "${p%/}"; }
case "${cmd[0]}" in
  copy)
    src="${cmd[1]}" dst="${cmd[2]}"
    if [[ "$src" == /data/* ]]; then mkdir -p "$(rpath "$dst")"; cp "$data/${src#/data/}" "$(rpath "$dst")/"
    else cp "$(rpath "$src")" "$data/"; fi ;;
  rcat) mkdir -p "$(dirname "$(rpath "${cmd[1]}")")"; cat > "$(rpath "${cmd[1]}")" ;;
  lsf|lsl) ls -1 "$(rpath "${cmd[1]}")" 2>/dev/null ;;
  cat) cat "$(rpath "${cmd[1]}")" ;;
  deletefile) rm -f "$(rpath "${cmd[1]}")" ;;
  rmdir) rmdir "$(rpath "${cmd[1]}")" 2>/dev/null ;;
  check|delete) : ;;
esac
exit 0
STUB

cat >"$BIN/gpg" <<'STUB'
#!/usr/bin/env bash
cat >/dev/null            # hasło z potoku
[ -z "${MOCK_GPG_FAIL:-}" ] || { echo "gpg: decryption failed: Bad session key" >&2; exit 2; }
out="" prev=""
for a in "$@"; do [ "$prev" = "--output" ] && out="$a"; prev="$a"; done
# Bez --output (backup_verify.sh: rozszyfrowanie strumieniem do pg_restore / tar) – na stdout.
if [ -z "$out" ]; then cat "${!#}"; else cp "${!#}" "$out"; fi
STUB

cat >"$BIN/date" <<STUB
#!/usr/bin/env bash
if [ "\${1:-}" = "-u" ] && [ "\${2:-}" = "+%d" ] && [ -n "\${FAKE_DAY:-}" ]; then echo "\$FAKE_DAY"; exit 0; fi
exec "$REAL_DATE" "\$@"
STUB
chmod +x "$BIN/docker" "$BIN/gpg" "$BIN/date"

TOKEN_A='{"access_token":"ya29.A","token_type":"Bearer","refresh_token":"1//REFRESH-A","expiry":"2026-09-25T20:00:00Z"}'
TOKEN_B='{"access_token":"ya29.B","token_type":"Bearer","refresh_token":"1//REFRESH-B","expiry":"2026-09-25T21:00:00Z"}'

# Świeża „instalacja” na każdy przypadek: .env, katalog kopii, zdalny katalog, log dockera.
# Argumenty: dodatkowe linie .env.
setup_case() {
  CASE="$WORK/case-$1"; shift
  rm -rf "$CASE"
  mkdir -p "$CASE/repo" "$CASE/backups" "$CASE/remote" "$CASE/tmp"
  {
    echo "POSTGRES_USER=olimpiada"
    echo "POSTGRES_DB=olimpiada"
    echo "MINIO_ROOT_USER=minio"
    echo "MINIO_ROOT_PASSWORD=minio-secret"
    echo "BACKUP_PASSPHRASE=test-passphrase"
    for line in "$@"; do printf '%s\n' "$line"; done
  } >"$CASE/repo/.env"
  export CASE DOCKER_LOG="$CASE/docker.log" REMOTE="$CASE/remote" FAKE_MEDIA="$CASE/djcms-media"
  # Wolumen djcms_media „w kontenerze” (przypadki 12–15): dwa pliki filera w podkatalogach.
  mkdir -p "$FAKE_MEDIA/filer_public/ab/cd"
  printf 'PNG-ATRAPA' >"$FAKE_MEDIA/filer_public/ab/cd/logo.png"
  printf 'SVG-ATRAPA' >"$FAKE_MEDIA/filer_public/ab/cd/ikona.svg"
  : >"$DOCKER_LOG"
}

run_backup() {
  REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" \
    bash "$ROOT/scripts/backup.sh" "$@" >"$CASE/out.txt" 2>&1
}
rclone_calls() { grep -E 'rclone/rclone' "$DOCKER_LOG"; }
record_calls() { grep -E 'record_backup_status' "$DOCKER_LOG"; }

# --- 1. brak konfiguracji: kopia wyłącznie lokalna, jak dotąd ---------------------------------
setup_case none
run_backup; rc=$?
check "bez konfiguracji: kod 0" $rc
[ -z "$(rclone_calls)" ]
check "bez konfiguracji: ani jednego wywołania rclone" $?
grep -q "WYŁĄCZNIE lokalnie" "$CASE/out.txt"
check "bez konfiguracji: komunikat o kopii wyłącznie lokalnej" $?
record_calls | grep -q -- '--ok' && ! record_calls | grep -q -- '--offsite'
check "bez konfiguracji: meldunek --ok bez --offsite" $?
ls "$CASE/backups"/db-*.dump.gpg "$CASE/backups"/files-*.tar.gpg >/dev/null 2>&1
check "bez konfiguracji: obie paczki lokalnie" $?

# --- 2. S3: te same zmienne co dotąd + weryfikacja sumą ----------------------------------------
setup_case s3 "BACKUP_REMOTE_URL=https://s3.example.test" "BACKUP_ACCESS_KEY=AK" "BACKUP_SECRET_KEY=SK" "BACKUP_BUCKET=kubel"
run_backup; rc=$?
check "S3: kod 0" $rc
rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_TYPE=s3' && rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY=SK'
check "S3: konfiguracja rclone ze zmiennych (TYPE=s3, klucze)" $?
[ "$(rclone_calls | grep -c ' copy /data/.* offsite:kubel/daily/')" -eq 2 ]
check "S3: dwie paczki do offsite:kubel/daily/" $?
rclone_calls | grep ' check /data offsite:kubel/daily/ --one-way' | grep -q -- '--include /db-' \
  && rclone_calls | grep ' check ' | grep -q -- '--include /files-'
check "S3: rclone check --one-way obu paczek po wysyłce" $?
rclone_calls | grep -q 'delete offsite:kubel/daily/ --min-age 30d' && rclone_calls | grep -q 'delete offsite:kubel/monthly/ --min-age 365d'
check "S3: retencja 30d / 365d" $?
! rclone_calls | grep -q 'USE_TRASH\|/config/rclone'
check "S3: bez opcji Dysku i bez pliku konfiguracji" $?
! rclone_calls | grep -q 'monthly/ *$' && ! rclone_calls | grep -q 'copy .*monthly'
check "S3: nie pierwszy dzień miesiąca – nic do monthly/" $?
record_calls | grep -q -- '--ok --offsite'
check "S3: meldunek --ok --offsite" $?

# --- 3. Dysk Google z tokenem w .env ------------------------------------------------------------
setup_case drive "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
run_backup; rc=$?
check "Dysk (token w .env): kod 0" $rc
CONF="$CASE/repo/secrets/rclone/rclone.conf"
[ -f "$CONF" ] && grep -qF "token = $TOKEN_A" "$CONF"
check "Dysk: token zapisany do secrets/rclone/rclone.conf" $?
if [ "$(uname -s)" = "Linux" ]; then
  [ "$(stat -c %a "$CONF")" = "600" ] && [ "$(stat -c %a "$(dirname "$CONF")")" = "700" ]
  check "Dysk: uprawnienia 600 (plik) i 700 (katalog)" $?
fi
rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_TYPE=drive' \
  && rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_SCOPE=drive.file' \
  && rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_USE_TRASH=false'
check "Dysk: TYPE=drive, SCOPE=drive.file, USE_TRASH=false" $?
rclone_calls | grep -q -- "-v $CASE/repo/secrets/rclone:/config/rclone " && rclone_calls | grep -q 'RCLONE_CONFIG=/config/rclone/rclone.conf'
check "Dysk: montowany KATALOG sekretów (nie pojedynczy plik) jako /config/rclone" $?
! grep -q 'REFRESH-A' "$DOCKER_LOG"
check "Dysk: token NIE idzie w argumentach/zmiennych kontenera" $?
! rclone_calls | grep -q 'CLIENT_ID\|TEAM_DRIVE'
check "Dysk: bez własnego klienta i dysku współdzielonego – żadnych pustych zmiennych" $?
[ "$(rclone_calls | grep -c ' copy /data/.* offsite:Olimpiada-kopie-zapasowe/daily/')" -eq 2 ]
check "Dysk: dwie paczki do folderu Olimpiada-kopie-zapasowe/daily/" $?
rclone_calls | grep -q 'check /data offsite:Olimpiada-kopie-zapasowe/daily/ --one-way'
check "Dysk: weryfikacja rclone check (MD5 Dysku)" $?
rclone_calls | grep -q 'delete offsite:Olimpiada-kopie-zapasowe/daily/ --min-age 30d'
check "Dysk: retencja daily 30d" $?
record_calls | grep -q -- '--ok --offsite'
check "Dysk: meldunek --ok --offsite" $?
ls "$CASE/remote/Olimpiada-kopie-zapasowe/daily"/db-*.dump.gpg >/dev/null 2>&1
check "Dysk: paczka bazy po „tamtej stronie”" $?

# 3a. rclone odświeża token w pliku – kolejny przebieg z tym samym .env go NIE nadpisuje.
MOCK_REFRESH=1 run_backup
grep -q '"access_token":"ODSWIEZONY"' "$CONF"
check "Dysk: token odświeżony przez rclone zapisał się w pliku" $?
run_backup; rc=$?
grep -q '"access_token":"ODSWIEZONY"' "$CONF" && [ $rc -eq 0 ]
check "Dysk: ten sam refresh_token w .env nie nadpisuje odświeżonego tokenu" $?

# 3b. Nowa autoryzacja (inny refresh_token w .env) zastępuje plik.
sed -i "s|^BACKUP_DRIVE_TOKEN=.*|BACKUP_DRIVE_TOKEN='$TOKEN_B'|" "$CASE/repo/.env"
run_backup
grep -q 'REFRESH-B' "$CONF" && ! grep -q 'REFRESH-A' "$CONF"
check "Dysk: nowy refresh_token w .env zastępuje zapisany token" $?

# --- 4. Dysk: token wklejony (--drive-token), bez .env ------------------------------------------
setup_case paste
printf '%s\r\n' "$TOKEN_A" | run_backup --drive-token; rc=$?
CONF="$CASE/repo/secrets/rclone/rclone.conf"
[ $rc -eq 0 ] && grep -qF "token = $TOKEN_A" "$CONF"
check "--drive-token: wklejony token (z CR z Windowsa) zapisany do pliku" $?
[ -z "$(rclone_calls)" ] && ! grep -q 'pg_dump' "$DOCKER_LOG"
check "--drive-token: bez zrzutu i bez rclone" $?
run_backup; rc=$?
[ $rc -eq 0 ] && rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_TYPE=drive'
check "--drive-token: następny przebieg sam wybiera Dysk (plik tokenu istnieje)" $?

printf '%s\n' '{access_token:ya29.X,refresh_token:1//Y}' | run_backup --drive-token; rc=$?
[ $rc -ne 0 ] && grep -q 'apostrofach' "$CASE/out.txt"
check "--drive-token: token bez cudzysłowów odrzucony z podpowiedzią" $?
printf '%s\n' 'to nie jest token' | run_backup --drive-token; rc=$?
[ $rc -ne 0 ] && grep -q 'JSON' "$CASE/out.txt" && grep -qF "$TOKEN_A" "$CONF"
check "--drive-token: śmieci odrzucone, zapisany token nietknięty" $?

# --drive-token przy innym tokenie w .env: odmowa (nadpisałby wklejony w nocy).
setup_case paste-conflict "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
printf '%s\n' "$TOKEN_B" | run_backup --drive-token; rc=$?
[ $rc -ne 0 ] && grep -q 'usuń tę linię' "$CASE/out.txt"
check "--drive-token: odmowa, gdy w .env jest inny token" $?

# --- 5. opcje Dysku: własny klient, dysk współdzielony, folder, kosz ---------------------------
setup_case drive-opts "BACKUP_DRIVE_TOKEN='$TOKEN_A'" "BACKUP_DRIVE_CLIENT_ID=123.apps.googleusercontent.com" \
  "BACKUP_DRIVE_CLIENT_SECRET=GOCSPX-x" "BACKUP_DRIVE_TEAM_DRIVE=0ABCteam" "BACKUP_DRIVE_FOLDER=/Kopie/Olimpiada/" \
  "BACKUP_DRIVE_USE_TRASH=true" "REMOTE_DAILY_KEEP_DAYS=14"
run_backup; rc=$?
[ $rc -eq 0 ] \
  && rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_CLIENT_ID=123.apps.googleusercontent.com' \
  && rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_CLIENT_SECRET=GOCSPX-x' \
  && rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_TEAM_DRIVE=0ABCteam' \
  && rclone_calls | grep -q 'RCLONE_CONFIG_OFFSITE_USE_TRASH=true'
check "Dysk: własny klient OAuth, dysk współdzielony i kosz przekazane do rclone" $?
rclone_calls | grep -q 'copy /data/db-.* offsite:Kopie/Olimpiada/daily/' \
  && rclone_calls | grep -q 'delete offsite:Kopie/Olimpiada/daily/ --min-age 14d'
check "Dysk: folder bez skrajnych ukośników, retencja z .env (14d)" $?

# --- 6. pierwszy dzień miesiąca: także monthly/ z weryfikacją ------------------------------------
setup_case monthly "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
FAKE_DAY=01 run_backup; rc=$?
[ $rc -eq 0 ] && [ "$(rclone_calls | grep -c 'copy /data/.* offsite:Olimpiada-kopie-zapasowe/monthly/')" -eq 2 ] \
  && rclone_calls | grep -q 'check /data offsite:Olimpiada-kopie-zapasowe/monthly/ --one-way'
check "1. dzień miesiąca: dwie paczki do monthly/ i ich weryfikacja" $?

# --- 7. awaria wysyłki = awaria kopii, kopia lokalna zostaje ------------------------------------
setup_case upload-fail "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
MOCK_FAIL="copy" run_backup; rc=$?
[ $rc -eq 1 ]
check "wysyłka nieudana: kod wyjścia 1" $?
record_calls | grep -q -- '--failed' && ! record_calls | grep -q -- '--ok'
check "wysyłka nieudana: meldunek --failed, żadnego --ok" $?
record_calls | grep -q 'NIE dotarła'
check "wysyłka nieudana: notatka mówi, że kopia nie dotarła" $?
ls "$CASE/backups"/db-*.dump.gpg >/dev/null 2>&1
check "wysyłka nieudana: kopia lokalna jest" $?
! rclone_calls | grep -q ' delete '
check "wysyłka nieudana: retencja zdalna NIE rusza (stare kopie są jedynymi poza serwerem)" $?

setup_case check-fail "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
MOCK_FAIL="check" run_backup; rc=$?
[ $rc -eq 1 ] && record_calls | grep -q -- '--failed' && grep -q 'suma kontrolna' "$CASE/out.txt"
check "weryfikacja nieudana (suma się nie zgadza): awaria kopii" $?

setup_case retention-fail "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
MOCK_FAIL="delete" run_backup; rc=$?
[ $rc -eq 1 ] && record_calls | grep -q -- '--failed'
check "retencja zdalna nieudana: awaria przebiegu" $?

# --- 8. zła konfiguracja: kopia lokalna mimo to, przebieg nieudany ------------------------------
setup_case no-token "BACKUP_REMOTE_TYPE=drive"
run_backup; rc=$?
[ $rc -eq 1 ] && grep -q 'drive-token' "$CASE/out.txt" && ls "$CASE/backups"/db-*.dump.gpg >/dev/null 2>&1 \
  && record_calls | grep -q -- '--failed' && [ -z "$(rclone_calls)" ]
check "BACKUP_REMOTE_TYPE=drive bez tokenu: kopia lokalna, --failed, podpowiedź --drive-token" $?

setup_case bad-token "BACKUP_DRIVE_TOKEN={access_token:ya29,refresh_token:1//X}"
run_backup; rc=$?
[ $rc -eq 1 ] && grep -q 'apostrofach' "$CASE/out.txt"
check "token w .env bez apostrofów: czytelny błąd" $?

setup_case bad-type "BACKUP_REMOTE_TYPE=ftp"
run_backup; rc=$?
[ $rc -eq 1 ] && grep -q "dozwolone: s3, drive, none" "$CASE/out.txt"
check "nieznany BACKUP_REMOTE_TYPE: błąd" $?

setup_case bad-retention "BACKUP_DRIVE_TOKEN='$TOKEN_A'" "REMOTE_DAILY_KEEP_DAYS=0"
run_backup; rc=$?
[ $rc -eq 1 ] && ! rclone_calls | grep -q ' delete ' && grep -q 'REMOTE_DAILY_KEEP_DAYS' "$CASE/out.txt"
check "REMOTE_DAILY_KEEP_DAYS=0: odmowa, nic nie jest kasowane" $?

setup_case s3-incomplete "BACKUP_REMOTE_URL=https://s3.example.test" "BACKUP_BUCKET=kubel"
run_backup; rc=$?
[ $rc -eq 1 ] && grep -q 'BACKUP_ACCESS_KEY' "$CASE/out.txt" && ls "$CASE/backups"/db-*.dump.gpg >/dev/null 2>&1
check "S3 bez kluczy: kopia lokalna jest, przebieg nieudany (wcześniej: przerwanie przed meldunkiem)" $?

# Oba warianty naraz: s3 wygrywa (kolejność z opisu), z ostrzeżeniem; jawny typ rozstrzyga.
setup_case both "BACKUP_REMOTE_URL=https://s3.example.test" "BACKUP_ACCESS_KEY=AK" "BACKUP_SECRET_KEY=SK" \
  "BACKUP_BUCKET=kubel" "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
run_backup; rc=$?
[ $rc -eq 0 ] && rclone_calls | grep -q 'TYPE=s3' && grep -q 'Rozstrzygnij' "$CASE/out.txt"
check "S3 i Dysk naraz, bez BACKUP_REMOTE_TYPE: S3 z ostrzeżeniem" $?
echo "BACKUP_REMOTE_TYPE=drive" >>"$CASE/repo/.env"
: >"$DOCKER_LOG"
run_backup; rc=$?
[ $rc -eq 0 ] && rclone_calls | grep -q 'TYPE=drive' && ! rclone_calls | grep -q 'TYPE=s3'
check "BACKUP_REMOTE_TYPE=drive rozstrzyga na Dysk" $?

setup_case explicit-none "BACKUP_REMOTE_TYPE=none" "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
run_backup; rc=$?
[ $rc -eq 0 ] && [ -z "$(rclone_calls)" ]
check "BACKUP_REMOTE_TYPE=none wyłącza wysyłkę mimo tokenu" $?

# --- 9. --offsite-test --------------------------------------------------------------------------
setup_case selftest "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
run_backup --offsite-test; rc=$?
[ $rc -eq 0 ] && grep -q 'Test udany' "$CASE/out.txt"
check "--offsite-test: udany" $?
for op in "rcat offsite:Olimpiada-kopie-zapasowe/test/offsite-test-" "lsf offsite:Olimpiada-kopie-zapasowe/test/" \
          "cat offsite:Olimpiada-kopie-zapasowe/test/offsite-test-" "deletefile offsite:Olimpiada-kopie-zapasowe/test/offsite-test-"; do
  rclone_calls | grep -q -- "$op" || { rc=1; printf '     brak: %s\n' "$op"; }
done
check "--offsite-test: zapis, lista, odczyt, kasowanie" $rc
rclone_calls | grep -q -- 'run --rm -i ' && ! grep -q 'pg_dump\|record_backup_status' "$DOCKER_LOG"
check "--offsite-test: rcat ze stdin, bez zrzutu i bez meldunku" $?
[ -z "$(ls -A "$CASE/remote/Olimpiada-kopie-zapasowe/test" 2>/dev/null)" ]
check "--offsite-test: po teście nie zostaje plik próbny" $?

setup_case selftest-fail "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
MOCK_FAIL="rcat" run_backup --offsite-test; rc=$?
[ $rc -ne 0 ] && grep -q 'zapis pliku próbnego' "$CASE/out.txt"
check "--offsite-test: nieudany zapis = kod różny od zera i opis" $?

setup_case selftest-none
run_backup --offsite-test; rc=$?
[ $rc -ne 0 ] && grep -q 'nie jest skonfigurowana' "$CASE/out.txt"
check "--offsite-test bez konfiguracji: błąd" $?

run_backup --bogus; rc=$?
[ $rc -eq 2 ]
check "nieznany argument: kod 2" $?

# --- 10. restore.sh: --list i --fetch przez tę samą bibliotekę ---------------------------------
setup_case restore "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
run_backup
NAME="$(basename "$(ls "$CASE/backups"/db-*.dump.gpg)")"
rm -f "$CASE/backups/$NAME"
: >"$DOCKER_LOG"
REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" bash "$ROOT/scripts/restore.sh" --list >"$CASE/out.txt" 2>&1
grep -q 'Dysk Google' "$CASE/out.txt" && rclone_calls | grep -q 'lsl offsite:Olimpiada-kopie-zapasowe/daily/'
check "restore.sh --list: kopie z Dysku" $?
REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" bash "$ROOT/scripts/restore.sh" --fetch "$NAME" >"$CASE/out.txt" 2>&1; rc=$?
[ $rc -eq 0 ] && [ -f "$CASE/backups/$NAME" ] && rclone_calls | grep -q -- ":/data:rw " \
  && rclone_calls | grep -q "check offsite:Olimpiada-kopie-zapasowe/daily/ /data --one-way --include /$NAME"
check "restore.sh --fetch: paczka ściągnięta (montowanie rw) i sprawdzona sumą" $?
REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" bash "$ROOT/scripts/restore.sh" --fetch db-nie-ma.dump.gpg >"$CASE/out.txt" 2>&1; rc=$?
[ $rc -ne 0 ] && grep -q 'ani w daily/, ani w monthly/' "$CASE/out.txt"
check "restore.sh --fetch: brak pliku = czytelny błąd" $?

# --- 11. restore.sh: nowa baza bez CONNECT dla PUBLIC (docs/tasks/DJ-01.md § 8.9) ---------------
# Baza odtworzona z kopii to pełne dane uczestników; w tym samym klastrze stoi rola z LOGIN wersji
# porównawczej (`olimpiada_djcms`). CONNECT dla PUBLIC ma zniknąć zaraz po `createdb`, przed
# `pg_restore` – a nazwa z `--db` ma trafić do SQL-a jako identyfikator w cudzysłowie.
setup_case restore-acl
printf 'PGDMP-atrapa' >"$CASE/backups/db-20260101T030000Z.dump.gpg"
restore() {
  REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" bash "$ROOT/scripts/restore.sh" \
    --dump db-20260101T030000Z.dump.gpg --files brak.tar.gpg "$@" >"$CASE/out.txt" 2>&1
}
restore; rc=$?
create_line="$(grep -n 'compose exec -T db createdb -U olimpiada restore_20260101_030000$' "$DOCKER_LOG" | cut -d: -f1)"
revoke_line="$(grep -nF 'REVOKE CONNECT ON DATABASE "restore_20260101_030000" FROM PUBLIC' "$DOCKER_LOG" | cut -d: -f1)"
restore_line="$(grep -n 'compose exec -T db pg_restore' "$DOCKER_LOG" | cut -d: -f1)"
[ $rc -eq 0 ] && [ -n "$create_line" ] && [ -n "$revoke_line" ] && [ -n "$restore_line" ] \
  && [ "$revoke_line" -eq $((create_line + 1)) ] && [ "$revoke_line" -lt "$restore_line" ]
check "restore.sh: REVOKE CONNECT FROM PUBLIC zaraz po createdb, przed pg_restore" $?
grep -F 'REVOKE CONNECT' "$DOCKER_LOG" | grep -q -- '-U olimpiada -d postgres'
check "restore.sh: REVOKE jako konto aplikacji (superuser, właściciel bazy), z bazy postgres" $?
: >"$DOCKER_LOG"
restore --db 'odtw"orzona'; rc=$?
[ $rc -eq 0 ] && grep -qF 'REVOKE CONNECT ON DATABASE "odtw""orzona" FROM PUBLIC' "$DOCKER_LOG"
check "restore.sh --db: nazwa bazy jako identyfikator z podwojonym cudzysłowem" $?
: >"$DOCKER_LOG"
restore --dry-run; rc=$?
[ $rc -eq 0 ] && grep -q '\[próba\] docker compose exec -T db psql .*REVOKE CONNECT ON DATABASE "restore_20260101_030000" FROM PUBLIC' "$CASE/out.txt" \
  && ! grep -q 'REVOKE\|createdb' "$DOCKER_LOG"
check "restore.sh --dry-run: REVOKE tylko wypisany, nic nie wykonane" $?

# === Wersja porównawcza django CMS (dj., docs/tasks/DJ-01.md § 8.11, podkrok DJ-01i) =============
# Maskowanie zmiennych części przebiegu: znacznik czasu, katalogi robocze, numer procesu w nazwie
# kontenera testowego i jego jednorazowe hasło. Po zamaskowaniu dwa przebiegi w tej samej
# piaskownicy dają ten sam tekst.
mask() {
  sed -E -e 's/[0-9]{8}T[0-9]{6}Z/STAMP/g' -e 's/\.work-[A-Za-z0-9]{6}/.work-X/g' \
    -e 's/olimpiada-(verify|restore)-[A-Za-z0-9]{6}/olimpiada-\1-X/g' \
    -e 's/olimpiada-restore-test-(net-)?[0-9]+/olimpiada-restore-test-\1PID/g' \
    -e 's/POSTGRES_PASSWORD=[0-9a-f]+/POSTGRES_PASSWORD=X/g' "$@"
}
verify() {
  REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" TMPDIR="$CASE/tmp" \
    bash "$ROOT/scripts/backup_verify.sh" "$@" >"$CASE/out.txt" 2>&1
}
djrestore() {
  REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" \
    bash "$ROOT/scripts/restore.sh" "$@" >"$CASE/out.txt" 2>&1
}
line_of() { grep -n -- "$1" "$DOCKER_LOG" | head -1 | cut -d: -f1; }
# Pierwsze linie wywołań dockera (skrypt `sh -c` dla mc jest wielolinijkowy – jego treść, wcięta,
# odpada), po zamaskowaniu i z katalogiem przypadku zamienionym na CASE.
docker_calls() { mask "$DOCKER_LOG" | sed -e "s|$CASE|CASE|g" -e "s/ *$//" | grep -Ev "^( |$)"; }

# --- 12. dj. wyłączone: przebieg co do polecenia ten sam, co przed DJ-01 ------------------------
# Lista poleceń dockera wpisana wprost – test mówi, co uznaje za „dzisiaj”. Warianty: bez zmiennej,
# DJCMS_ENABLED=0 i DJCMS_ENABLED=off – za każdym razem baza djcms „istnieje” (atrapa odpowiada 1),
# bo o kopii decyduje przełącznik, a nie sama baza.
EXPECTED_OFF='compose exec -T db pg_dump -U olimpiada -d olimpiada -Fc
compose ps -q minio
inspect -f {{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}} cid123
run --rm --network proj_internal -v CASE/backups/.work-X/buckets:/backup -e MC_HOST_src=http://minio:minio-secret@minio:9000 -e MC_QUIET=1 -e MC_NO_COLOR=1 --entrypoint sh minio/mc:RELEASE.2025-04-16T18-13-26Z -c
compose exec -T web python manage.py record_backup_status --ok'
for variant in "" "DJCMS_ENABLED=0" "DJCMS_ENABLED=off"; do
  setup_case "dj-off-${variant#DJCMS_ENABLED=}" ${variant:+"$variant"}
  MOCK_DJCMS_DB=1 run_backup; rc=$?
  got="$(docker_calls)"
  [ $rc -eq 0 ] && [ "$got" = "$EXPECTED_OFF" ]
  check "dj. wyłączone (${variant:-bez zmiennej}): polecenia dockera znak w znak dzisiejsze" $?
  [ "$got" = "$EXPECTED_OFF" ] || diff <(printf '%s\n' "$EXPECTED_OFF") <(printf '%s\n' "$got") | sed 's/^/     /'
  ! grep -q 'djcms\|1b/5\|2b/5' "$DOCKER_LOG" "$CASE/out.txt" && [ "$(ls "$CASE/backups" | grep -c .)" -eq 2 ]
  check "dj. wyłączone (${variant:-bez zmiennej}): ani słowa o djcms, dwie paczki" $?
done

# 12a. Porównanie z backup.sh / backup_verify.sh / restore.sh z innej rewizji (opcjonalne): cały
# przebieg bez dj. (polecenia dockera, wyjście, kod, pliki w katalogu kopii) po zamaskowaniu musi
# być identyczny. Rewizja sprzed DJ-01i:
#   BACKUP_BASELINE_REF=f8768c6 scripts/tests/backup_offsite_test.sh
if [ -n "${BACKUP_BASELINE_REF:-}" ]; then
  BASE="$WORK/base/scripts"
  mkdir -p "$BASE/lib"
  rc=0
  for f in backup.sh restore.sh backup_verify.sh lib/backup_offsite.sh; do
    git -C "$ROOT" show "$BACKUP_BASELINE_REF:scripts/$f" >"$BASE/$f" 2>/dev/null || rc=1
  done
  check "skrypty kopii z rewizji $BACKUP_BASELINE_REF odczytane" $rc
  compare_runs() {  # compare_runs <opis> <skrypt> [argumenty…]
    local label="$1" script="$2" side scripts
    shift 2
    for side in base new; do
      if [ "$side" = base ]; then scripts="$BASE"; else scripts="$ROOT/scripts"; fi
      setup_case "baseline-$side" "BACKUP_DRIVE_TOKEN='$TOKEN_A'"
      printf 'PGDMP-atrapa' >"$CASE/backups/db-20260101T030000Z.dump.gpg"
      printf 'tar-atrapa' >"$CASE/backups/files-20260101T030000Z.tar.gpg"
      MOCK_DJCMS_DB=1 REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" \
        bash "$scripts/$script" "$@" >"$CASE/run.txt" 2>&1
      echo "rc=$?" >>"$CASE/run.txt"
      { docker_calls; echo ---; mask "$CASE/run.txt"; echo ---; ls "$CASE/backups" | mask; } \
        | sed -e "s|$CASE|CASE|g" -e "s|$scripts|SCRIPTS|g" >"$WORK/baseline-$side.txt"
    done
    diff "$WORK/baseline-base.txt" "$WORK/baseline-new.txt" >"$WORK/baseline.diff"
    check "[$BACKUP_BASELINE_REF] $label: przebieg identyczny" $?
    [ -s "$WORK/baseline.diff" ] && sed 's/^/     /' "$WORK/baseline.diff" | head -20
  }
  compare_runs "backup.sh (Dysk, bez dj.)" backup.sh
  # backup_verify.sh nie jest porównywany: OPS-01 przebudował go celowo (conocny test z kontenerem
  # sprawdzeń aplikacji) – jego polecenia sprawdza przypadek 14 i 16 niżej.
  compare_runs "restore.sh --dump --files" restore.sh --dump db-20260101T030000Z.dump.gpg --files files-20260101T030000Z.tar.gpg
  compare_runs "restore.sh --dry-run" restore.sh --dry-run
  compare_runs "restore.sh --list" restore.sh --list
fi

# --- 13. dj. włączone: baza i pliki djcms w kopii, wysyłce i meldunku ---------------------------
setup_case dj-on "BACKUP_DRIVE_TOKEN='$TOKEN_A'" "DJCMS_ENABLED=1"
MOCK_DJCMS_DB=1 run_backup; rc=$?
check "dj. włączone: kod 0" $rc
ls "$CASE/backups"/djcms-db-*.dump.gpg "$CASE/backups"/djcms-files-*.tar.gpg >/dev/null 2>&1 \
  && [ "$(ls "$CASE/backups" | grep -c .)" -eq 4 ]
check "dj. włączone: cztery paczki (db, files, djcms-db, djcms-files)" $?
STAMP_MAIN="$(ls "$CASE/backups" | sed -nE 's/^db-(.*)\.dump\.gpg$/\1/p')"
[ -f "$CASE/backups/djcms-db-$STAMP_MAIN.dump.gpg" ] && [ -f "$CASE/backups/djcms-files-$STAMP_MAIN.tar.gpg" ]
check "dj. włączone: paczki djcms z tym samym znacznikiem co kopia główna" $?
[ "$(cat "$CASE/backups/djcms-db-$STAMP_MAIN.dump.gpg")" = "PGDMP-DJCMS" ]
check "dj. włączone: zrzut bazy olimpiada_djcms (-Fc, kontem aplikacji)" $?
tar -tf "$CASE/backups/djcms-files-$STAMP_MAIN.tar.gpg" | grep -q 'filer_public/ab/cd/logo.png'
check "dj. włączone: paczka plików to tar wolumenu (/app/media) z plikami filera" $?
[ "$(line_of 'pg_dump -U olimpiada -d olimpiada -Fc')" -lt "$(line_of 'datname = .olimpiada_djcms.')" ] \
  && [ "$(line_of 'datname = .olimpiada_djcms.')" -lt "$(line_of 'pg_dump -U olimpiada -d olimpiada_djcms')" ] \
  && [ "$(line_of 'pg_dump -U olimpiada -d olimpiada_djcms')" -lt "$(line_of 'compose ps -q minio')" ] \
  && [ "$(line_of 'run --rm --network')" -lt "$(line_of 'ps -q --status running djcms')" ] \
  && [ "$(line_of 'ps -q --status running djcms')" -lt "$(line_of 'exec -T djcms tar')" ] \
  && [ "$(line_of 'exec -T djcms tar')" -lt "$(line_of 'rclone/rclone')" ]
check "dj. włączone: kolejność 1 → 1b (czy baza jest, zrzut) → 2 → 2b (czy djcms działa, tar) → wysyłka" $?
grep -q '^==> 1b/5 ' "$CASE/out.txt" && grep -q '^==> 2b/5 ' "$CASE/out.txt" && grep -q '^==> 5/5 ' "$CASE/out.txt"
check "dj. włączone: podkroki 1b/5 i 2b/5, numeracja /5 zostaje" $?
[ "$(rclone_calls | grep -c ' copy /data/.* offsite:Olimpiada-kopie-zapasowe/daily/')" -eq 4 ] \
  && rclone_calls | grep ' check /data offsite:Olimpiada-kopie-zapasowe/daily/ --one-way' \
     | grep -- "--include /djcms-db-$STAMP_MAIN.dump.gpg" | grep -q -- "--include /djcms-files-$STAMP_MAIN.tar.gpg"
check "dj. włączone: cztery paczki do daily/, rclone check obejmuje obie paczki djcms" $?
[ -f "$CASE/remote/Olimpiada-kopie-zapasowe/daily/djcms-files-$STAMP_MAIN.tar.gpg" ]
check "dj. włączone: paczka plików djcms po „tamtej stronie”" $?
record_calls | grep -q -- '--ok --offsite'
check "dj. włączone: meldunek --ok --offsite" $?

setup_case dj-on-monthly "BACKUP_DRIVE_TOKEN='$TOKEN_A'" "DJCMS_ENABLED=True"
MOCK_DJCMS_DB=1 FAKE_DAY=01 run_backup; rc=$?
[ $rc -eq 0 ] && [ "$(rclone_calls | grep -c 'copy /data/.* offsite:Olimpiada-kopie-zapasowe/monthly/')" -eq 4 ]
check "dj. włączone (DJCMS_ENABLED=True): 1. dzień miesiąca – cztery paczki do monthly/" $?

# Retencja lokalna obejmuje paczki djcms (wzorzec *.gpg), a kopii przedwdrożeniowych (*.dump) nie.
setup_case dj-retention "DJCMS_ENABLED=1"
for f in djcms-db-20200101T031500Z.dump.gpg djcms-files-20200101T031500Z.tar.gpg djcms-db-pre-20200101-000000-v1.dump; do
  printf 'stara' >"$CASE/backups/$f"; touch -d '30 days ago' "$CASE/backups/$f"
done
MOCK_DJCMS_DB=1 run_backup; rc=$?
[ $rc -eq 0 ] && [ ! -e "$CASE/backups/djcms-db-20200101T031500Z.dump.gpg" ] \
  && [ ! -e "$CASE/backups/djcms-files-20200101T031500Z.tar.gpg" ] && [ -e "$CASE/backups/djcms-db-pre-20200101-000000-v1.dump" ]
check "dj.: retencja lokalna kasuje stare paczki djcms, kopii przedwdrożeniowej nie rusza" $?

# --- 13a. dj. włączone, a bazy jeszcze nie ma (przed pierwszym wdrożeniem z dj.) -----------------
setup_case dj-nodb "DJCMS_ENABLED=1"
MOCK_DJCMS_DB="" run_backup; rc=$?
[ $rc -eq 0 ] && grep -q 'nie istnieje – kopia dj. pominięta' "$CASE/out.txt" \
  && ! grep -q 'olimpiada_djcms -Fc\|djcms tar\|running djcms' "$DOCKER_LOG" && record_calls | grep -q -- '--ok'
check "dj. włączone bez bazy: bez zrzutu i tar, kopia główna ok" $?

# --- 13b. awarie po stronie dj.: kopia główna idzie dalej, przebieg nieudany --------------------
setup_case dj-stopped "BACKUP_DRIVE_TOKEN='$TOKEN_A'" "DJCMS_ENABLED=1"
MOCK_DJCMS_DB=1 MOCK_DJCMS_RUNNING="" run_backup; rc=$?
[ $rc -eq 1 ] && ls "$CASE/backups"/djcms-db-*.dump.gpg >/dev/null 2>&1 && ! ls "$CASE/backups"/djcms-files-* >/dev/null 2>&1 \
  && ! grep -q 'exec -T djcms' "$DOCKER_LOG"
check "djcms nie działa: baza djcms skopiowana, bez tar, kod 1" $?
[ "$(rclone_calls | grep -c ' copy /data/')" -eq 3 ] && rclone_calls | grep -q ' delete '
check "djcms nie działa: trzy paczki wysłane i sprawdzone, retencja zdalna rusza (kopia główna jest)" $?
record_calls | grep -q -- '--failed' && ! record_calls | grep -q -- '--ok' \
  && record_calls | grep -q 'kopia główna .* jest (lokalnie i poza serwerem), kopia dj. NIE: kontener djcms nie działa'
check "djcms nie działa: meldunek --failed z notatką (kopia główna jest, dj. nie)" $?
grep -q 'UWAGA: kontener djcms nie działa' "$CASE/out.txt"
check "djcms nie działa: ostrzeżenie w logu" $?

setup_case dj-dump-fail "DJCMS_ENABLED=1"
MOCK_DJCMS_DB=1 MOCK_DJCMS_DUMP_FAIL=1 run_backup; rc=$?
[ $rc -eq 1 ] && ls "$CASE/backups"/db-*.dump.gpg "$CASE/backups"/files-*.tar.gpg >/dev/null 2>&1 \
  && ! ls "$CASE/backups"/djcms-* >/dev/null 2>&1 && ! grep -q 'djcms tar' "$DOCKER_LOG" \
  && record_calls | grep -q -- '--failed .*wyłącznie lokalnie), kopia dj. NIE: zrzut bazy olimpiada_djcms'
check "zrzut bazy djcms nieudany: kopia główna jest, bez paczek djcms, --failed" $?

setup_case dj-tar-fail "DJCMS_ENABLED=1"
MOCK_DJCMS_DB=1 MOCK_DJCMS_TAR_FAIL=1 run_backup; rc=$?
[ $rc -eq 1 ] && ls "$CASE/backups"/djcms-db-*.dump.gpg >/dev/null 2>&1 && ! ls "$CASE/backups"/djcms-files-* >/dev/null 2>&1 \
  && record_calls | grep -q -- '--failed .*tar wolumenu djcms_media'
check "tar wolumenu nieudany: baza djcms jest, plików nie, --failed" $?

setup_case dj-query-fail "DJCMS_ENABLED=1"
MOCK_DJCMS_DB_FAIL=1 run_backup; rc=$?
[ $rc -eq 1 ] && ls "$CASE/backups"/db-*.dump.gpg >/dev/null 2>&1 && record_calls | grep -q -- '--failed .*nie udało się sprawdzić'
check "zapytanie o bazę djcms nieudane: kopia główna jest, --failed" $?

setup_case dj-upload-fail "BACKUP_DRIVE_TOKEN='$TOKEN_A'" "DJCMS_ENABLED=1"
MOCK_DJCMS_DB=1 MOCK_DJCMS_RUNNING="" MOCK_FAIL="copy" run_backup; rc=$?
[ $rc -eq 1 ] && record_calls | grep -q 'poza serwer NIE dotarła: .*; dj.: kontener djcms nie działa'
check "wysyłka i dj. nieudane naraz: jedna notatka z oboma powodami" $?

# --- 14. backup_verify.sh: baza i pliki djcms z tej samej nocy ----------------------------------
# Od OPS-01 wyniki dj. idą do aplikacji jako dodatkowe sprawdzenia w nagłówku kontenera sprawdzeń
# (`extra_checks`), a meldunek to jeden `restore_check record` na noc.
header() { head -1 "$CASE/verify-input.txt"; }
check_calls() { grep -E 'restore_check record' "$DOCKER_LOG"; }
setup_case verify-dj "DJCMS_ENABLED=1"
MOCK_DJCMS_DB=1 run_backup
: >"$DOCKER_LOG"
verify; rc=$?
[ $rc -eq 0 ] && grep -q 'createdb -U restorecheck restorecheck_djcms' "$DOCKER_LOG" \
  && grep -q 'pg_restore -U restorecheck -d restorecheck_djcms --no-owner --exit-on-error' "$DOCKER_LOG" \
  && grep -q 'psql -U restorecheck -d restorecheck_djcms -Atc SELECT count(\*) FROM cms_page' "$DOCKER_LOG"
check "backup_verify.sh: baza djcms w tym samym tymczasowym Postgresie (osobna baza), cms_page" $?
header | grep -q '"name":"djcms_db","status":"ok","detail":"cms_page=16"' \
  && header | grep -q '"name":"djcms_files","status":"ok","detail":"wpisów tar: [0-9]'
check "backup_verify.sh: djcms ok w nagłówku sprawdzeń (strony i wpisy tar)" $?
grep -q '^==> 4b/6 ' "$CASE/out.txt"
check "backup_verify.sh: podkrok 4b/6" $?

: >"$DOCKER_LOG"
MOCK_CMS_PAGES=0 verify; rc=$?
[ $rc -eq 1 ] && header | grep -q '"djcms_db","status":"fail","detail":"cms_page=0"' && check_calls | grep -q 'record$'
check "backup_verify.sh: zero stron djcms = test nieudany (z meldunkiem)" $?
: >"$DOCKER_LOG"
MOCK_CMS_PAGES="" verify; rc=$?
[ $rc -eq 1 ] && header | grep -q '"detail":"brak tabeli cms_page"'
check "backup_verify.sh: brak tabeli cms_page = test nieudany" $?
: >"$DOCKER_LOG"
MOCK_DJCMS_RESTORE_FAIL=1 verify; rc=$?
[ $rc -eq 1 ] && header | grep -q 'pg_restore bazy djcms nie powiódł się'
check "backup_verify.sh: nieudany pg_restore djcms = test nieudany (z meldunkiem, bez przerwania)" $?

VSTAMP="$(ls "$CASE/backups" | sed -nE 's/^db-(.*)\.dump\.gpg$/\1/p')"
rm -f "$CASE/backups/djcms-files-$VSTAMP.tar.gpg"
: >"$DOCKER_LOG"
verify; rc=$?
[ $rc -eq 1 ] && header | grep -q '"djcms_files","status":"fail","detail":"brak paczki'
check "backup_verify.sh: baza djcms bez paczki plików = test nieudany" $?
printf 'to nie jest tar' >"$CASE/backups/djcms-files-$VSTAMP.tar.gpg"
: >"$DOCKER_LOG"
verify; rc=$?
[ $rc -eq 1 ] && header | grep -q 'paczka plików djcms nieczytelna'
check "backup_verify.sh: nieczytelna paczka plików djcms = test nieudany" $?

# Paczki djcms z innej nocy i kopie przedwdrożeniowe nie są brane pod uwagę.
setup_case verify-other
run_backup
printf 'PGDMP' >"$CASE/backups/djcms-db-20200101T031500Z.dump.gpg"
printf 'PGDMP' >"$CASE/backups/djcms-db-pre-20260101-000000-v1.dump"
: >"$DOCKER_LOG"
verify; rc=$?
[ $rc -eq 0 ] && ! grep -q djcms "$DOCKER_LOG" && ! grep -q '4b/6' "$CASE/out.txt" && header | grep -q '"extra_checks":\[\]'
check "backup_verify.sh: bez paczek djcms tej nocy – żadnego polecenia djcms" $?

# --- 16. backup_verify.sh (OPS-01): izolacja, limity, sekrety, meldunki z nazwą kroku -------------
setup_case verify-ops
run_backup
: >"$DOCKER_LOG"
verify; rc=$?
check "OPS-01: kod 0 przy wyniku ok" $rc
[ "$(line_of 'restore_check live-counts')" -lt "$(line_of 'run -d --name olimpiada-restore-check-')" ] \
  && grep -q '^network create --internal olimpiada-restore-check-net-[0-9]*$' "$DOCKER_LOG"
check "OPS-01: liczności bazy żywej przed odtworzeniem; sieć tymczasowa --internal" $?
grep 'run -d --name olimpiada-restore-check-' "$DOCKER_LOG" \
  | grep -q -- '--memory 3g --memory-swap 3g --cpus 1 --cpu-shares 256 .*-e POSTGRES_USER=restorecheck -e POSTGRES_DB=restorecheck_main --tmpfs /var/lib/postgresql/data:rw,size=3g postgres:18-alpine'
check "OPS-01: tymczasowy Postgres z limitami, bazą restorecheck_main i danymi na tmpfs" $?
VRUN="$(grep '^run --rm -i --network olimpiada-restore-check-net-' "$DOCKER_LOG")"
printf '%s' "$VRUN" | grep -q -- '--read-only --tmpfs /tmp:size=64m --cap-drop ALL --security-opt no-new-privileges:true --entrypoint python sha256:webimage manage.py restore_check verify' \
  && printf '%s' "$VRUN" | grep -q -- '--memory 1g'
check "OPS-01: sprawdzenia w obrazie działającego web (po identyfikatorze), tylko do odczytu, bez uprawnień" $?
grep -q '^DATABASE_URL=postgres://restorecheck:[0-9a-f]*@olimpiada-restore-check-[0-9]*:5432/restorecheck_main$' "$CASE/app.env.copy" \
  && grep -q '^RESTORE_CHECK_ISOLATED=1$' "$CASE/app.env.copy" && grep -q '^DJANGO_SECRET_KEY=klucz-aplikacji$' "$CASE/app.env.copy" \
  && ! grep -q 'zywe@db\|redis://redis' "$CASE/app.env.copy" && [ "$(grep -c '^DATABASE_URL=' "$CASE/app.env.copy")" -eq 1 ]
check "OPS-01: środowisko web z podmienioną bazą (tymczasowa) i Redisem (martwy), ten sam SECRET_KEY" $?
[ "$(cat "$CASE/restored.dump")" = "PGDUMP-DATA" ]
check "OPS-01: zrzut rozszyfrowany strumieniem prosto do pg_restore" $?
header | grep -q '"live_counts":{"accounts.User": 3, "core.AuditLog": 10}' \
  && header | grep -q '"files_status":"ok"' \
  && header | grep -q '"web_created_at":"2026-01-01T00:00:00.123456789Z"'
check "OPS-01: nagłówek sprawdzeń: liczności żywej bazy, paczka plików, chwila wdrożenia" $?
grep -q '"status": "ok"' "$CASE/record-stdin.json" && grep -q '"status": "ok"' "$CASE/backups/restore-checks.jsonl"
check "OPS-01: wynik do restore_check record i do historii restore-checks.jsonl" $?
! grep -q 'test-passphrase' "$DOCKER_LOG" "$CASE/out.txt" "$CASE/backups/restore-checks.jsonl"
check "OPS-01: hasło kopii ani razu w poleceniu, logu ani historii" $?
grep -q '^rm -f -v olimpiada-restore-check-' "$DOCKER_LOG" && grep -q '^network rm olimpiada-restore-check-net-' "$DOCKER_LOG" \
  && [ -z "$(ls -d "$CASE"/tmp/olimpiada-verify-* 2>/dev/null)" ]
check "OPS-01: sprzątanie kontenera, sieci i katalogu roboczego" $?

failure_case() {  # $1 = opis, $2 = oczekiwany krok; reszta: zmienne atrapy (NAZWA=wartość)
  local label="$1" step="$2"
  shift 2
  : >"$DOCKER_LOG"
  env "$@" REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" TMPDIR="$CASE/tmp" \
    bash "$ROOT/scripts/backup_verify.sh" >"$CASE/out.txt" 2>&1
  local rc=$?
  [ $rc -eq 1 ] && grep -q "restore_check record --failure ${step} " "$DOCKER_LOG" \
    && tail -1 "$CASE/backups/restore-checks.jsonl" | grep -qF "\"failed\":[\"${step}\"]"
  check "OPS-01: ${label} = meldunek --failure ${step}, kod 1" $?
}
failure_case "złe hasło / uszkodzona paczka" decrypt MOCK_GPG_FAIL=1
! grep -q 'run --rm -i' "$DOCKER_LOG"
check "OPS-01: po nieudanym rozszyfrowaniu nie ma kontenera sprawdzeń" $?
failure_case "nieudany pg_restore" pg_restore MOCK_RESTORE_FAIL=1
failure_case "cel odtworzenia = kontener db" guard MOCK_TARGET_IS_DB=1
! grep -q 'pg_restore' "$DOCKER_LOG"
check "OPS-01: bramka zatrzymuje przebieg przed pg_restore" $?
failure_case "kontener sprawdzeń bez wyniku" checks MOCK_VERIFY_CRASH=1
failure_case "brak liczności bazy żywej" live-counts MOCK_LIVE_FAIL=1
mkdir -p "$CASE/stare" && mv "$CASE/backups"/db-*.dump.gpg "$CASE/stare/"
failure_case "brak kopii w katalogu" no-backup MOCK_NONE=1
mv "$CASE/stare"/* "$CASE/backups/"
: >"$DOCKER_LOG"
MOCK_WEB="" verify; rc=$?
[ $rc -eq 1 ] && grep -q 'kontener web nie działa' "$CASE/out.txt" && ! grep -q '^run ' "$DOCKER_LOG"
check "OPS-01: web nie działa = odmowa bez tymczasowego Postgresa" $?
rm -f "$CASE/backups"/files-*.tar.gpg
: >"$DOCKER_LOG"
verify; rc=$?
header | grep -q '"files_status":"missing"'
check "OPS-01: brak paczki plików idzie do sprawdzeń jako files_status=missing" $?

# --- 15. restore.sh --djcms-dump / --djcms-files ------------------------------------------------
setup_case restore-dj
printf 'PGDMP-djcms' >"$CASE/backups/djcms-db-20260101T030000Z.dump.gpg"
tar -cf "$CASE/backups/djcms-files-20260101T030000Z.tar.gpg" -C "$FAKE_MEDIA" .
djrestore --djcms-dump djcms-db-20260101T030000Z.dump.gpg --djcms-files djcms-files-20260101T030000Z.tar.gpg; rc=$?
TDB=olimpiada_djcms_restore_20260101_030000
create_line="$(grep -n "compose exec -T db createdb -U olimpiada -O olimpiada_djcms $TDB\$" "$DOCKER_LOG" | cut -d: -f1)"
revoke_line="$(grep -nF "REVOKE CONNECT ON DATABASE \"$TDB\" FROM PUBLIC" "$DOCKER_LOG" | cut -d: -f1)"
restore_line="$(grep -n "compose exec -T db pg_restore -U olimpiada -d $TDB --no-owner --role=olimpiada_djcms --exit-on-error" "$DOCKER_LOG" | cut -d: -f1)"
[ $rc -eq 0 ] && [ -n "$create_line" ] && [ -n "$revoke_line" ] && [ -n "$restore_line" ] \
  && [ "$revoke_line" -eq $((create_line + 1)) ] && [ "$revoke_line" -lt "$restore_line" ]
check "restore.sh --djcms-dump: nowa baza (właściciel olimpiada_djcms), REVOKE PUBLIC, pg_restore --role" $?
grep -q "psql -U olimpiada -d $TDB -Atc SELECT 'cms_page=' || count(\*) FROM cms_page" "$DOCKER_LOG"
check "restore.sh --djcms-dump: licznik stron po odtworzeniu" $?
MEDIA_OUT="$CASE/backups/djcms-media-restore-20260101_030000"
[ "$(cat "$MEDIA_OUT/filer_public/ab/cd/logo.png" 2>/dev/null)" = "PNG-ATRAPA" ]
check "restore.sh --djcms-files: pliki rozpakowane do djcms-media-restore-<stamp>/" $?
if [ "$(uname -s)" = "Linux" ]; then
  [ "$(stat -c %a "$MEDIA_OUT")" = "700" ]
  check "restore.sh --djcms-files: katalog odtworzenia 700 mimo wpisu ./ w paczce" $?
fi
! grep -q 'minio\|olimpiada_djcms -Fc\|ALTER DATABASE\|^run \|compose stop\|compose up' "$DOCKER_LOG"
check "restore.sh --djcms-dump: ani MinIO, ani podmiany – nic poza nową bazą" $?
grep -qF "ALTER DATABASE olimpiada_djcms RENAME TO olimpiada_djcms_przed_awaria' -c 'ALTER DATABASE \"$TDB\" RENAME TO olimpiada_djcms'" "$CASE/out.txt" \
  && grep -qF "docker run --rm -v repo_djcms_media:/m -v $MEDIA_OUT:/src:ro" "$CASE/out.txt" \
  && grep -q 'docker compose stop djcms' "$CASE/out.txt" && grep -q 'docker compose up -d djcms' "$CASE/out.txt"
check "restore.sh --djcms-dump: wypisane polecenia podmiany bazy i wolumenu" $?

: >"$DOCKER_LOG"
djrestore --djcms-dump djcms-db-20260101T030000Z.dump.gpg --djcms-files djcms-files-20260101T030000Z.tar.gpg --db inna; rc=$?
[ $rc -ne 0 ] && grep -q 'djcms-media-restore-20260101_030000 już istnieje' "$CASE/out.txt" && ! grep -q createdb "$DOCKER_LOG"
check "restore.sh --djcms-files: istniejący katalog odtworzenia = odmowa, zanim powstanie baza" $?

setup_case restore-dj-dry
printf 'PGDMP-djcms' >"$CASE/backups/djcms-db-20260101T030000Z.dump.gpg"
tar -cf "$CASE/backups/djcms-files-20260101T030000Z.tar.gpg" -C "$FAKE_MEDIA" .
djrestore --dry-run --djcms-dump djcms-db-20260101T030000Z.dump.gpg --djcms-files djcms-files-20260101T030000Z.tar.gpg; rc=$?
[ $rc -eq 0 ] && ! grep -q 'createdb\|REVOKE\|pg_restore' "$DOCKER_LOG" \
  && grep -q '\[próba\] docker compose exec -T db createdb -U olimpiada -O olimpiada_djcms' "$CASE/out.txt" \
  && grep -q '\[próba\] tar --numeric-owner' "$CASE/out.txt" && grep -q 'w paczce: 2 plików' "$CASE/out.txt" \
  && [ ! -e "$CASE/backups/djcms-media-restore-20260101_030000" ]
check "restore.sh --djcms-dump --dry-run: rozszyfrowanie i liczenie, nic nie utworzone" $?

MOCK_DJCMS_ROLE="" djrestore --djcms-dump djcms-db-20260101T030000Z.dump.gpg; rc=$?
[ $rc -ne 0 ] && grep -q 'nie ma roli olimpiada_djcms' "$CASE/out.txt" && ! grep -q createdb "$DOCKER_LOG"
check "restore.sh --djcms-dump: brak roli olimpiada_djcms = odmowa przed createdb" $?
djrestore --djcms-files djcms-files-20260101T030000Z.tar.gpg; rc=$?
[ $rc -ne 0 ] && grep -q 'wymaga --djcms-dump' "$CASE/out.txt"
check "restore.sh --djcms-files bez --djcms-dump: odmowa" $?
djrestore --djcms-dump djcms-db-20260101T030000Z.dump.gpg --dump db-x.dump.gpg; rc=$?
[ $rc -ne 0 ] && grep -q 'osobnym przebiegiem' "$CASE/out.txt"
check "restore.sh --djcms-dump z --dump: odmowa (osobne przebiegi)" $?
printf 'PGDMP' >"$CASE/backups/djcms-db-pre-20260101-000000-v1.dump"
djrestore --djcms-dump djcms-db-pre-20260101-000000-v1.dump; rc=$?
[ $rc -ne 0 ] && grep -q 'oczekiwana kopia nocna' "$CASE/out.txt"
check "restore.sh --djcms-dump: kopia przedwdrożeniowa (jawna) odrzucona z podpowiedzią" $?
printf 'smieci' >"$CASE/backups/djcms-db-20260102T030000Z.dump.gpg"
djrestore --dry-run --djcms-dump djcms-db-20260102T030000Z.dump.gpg; rc=$?
[ $rc -ne 0 ] && grep -q 'brak nagłówka PGDMP' "$CASE/out.txt"
check "restore.sh --djcms-dump: paczka bez nagłówka PGDMP odrzucona (także w trybie próbnym)" $?

echo
if [ "$failures" -eq 0 ]; then
  echo "Wszystkie sprawdzenia przeszły."
else
  echo "Nieudanych sprawdzeń: $failures"
  exit 1
fi
