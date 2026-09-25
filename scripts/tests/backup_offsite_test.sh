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
trap 'rm -rf "$WORK"' EXIT

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
for f in scripts/backup.sh scripts/restore.sh scripts/lib/backup_offsite.sh scripts/deploy.sh; do
  bash -n "$ROOT/$f"
  check "$f przechodzi bash -n" $?
done
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x -S warning "$ROOT/scripts/backup.sh" "$ROOT/scripts/restore.sh" "$ROOT/scripts/lib/backup_offsite.sh"
  check "shellcheck (warning) dla backup.sh, restore.sh i biblioteki" $?
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
out="" prev=""
for a in "$@"; do [ "$prev" = "--output" ] && out="$a"; prev="$a"; done
cp "${!#}" "$out"
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
  mkdir -p "$CASE/repo" "$CASE/backups" "$CASE/remote"
  {
    echo "POSTGRES_USER=olimpiada"
    echo "POSTGRES_DB=olimpiada"
    echo "MINIO_ROOT_USER=minio"
    echo "MINIO_ROOT_PASSWORD=minio-secret"
    echo "BACKUP_PASSPHRASE=test-passphrase"
    for line in "$@"; do printf '%s\n' "$line"; done
  } >"$CASE/repo/.env"
  export DOCKER_LOG="$CASE/docker.log" REMOTE="$CASE/remote"
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

echo
if [ "$failures" -eq 0 ]; then
  echo "Wszystkie sprawdzenia przeszły."
else
  echo "Nieudanych sprawdzeń: $failures"
  exit 1
fi
