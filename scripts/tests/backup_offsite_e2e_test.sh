#!/usr/bin/env bash
# Kopia poza serwerem od początku do końca na PRAWDZIWYM rclone (obraz przypięty w skrypcie),
# z lokalnym katalogiem w miejscu Dysku Google.
#
# Uruchomienie (Git Bash / Linux, Docker musi działać; obraz rclone/rclone:1.69 zostanie pobrany):
#   scripts/tests/backup_offsite_e2e_test.sh
#
# Co udaje, a co jest prawdziwe:
#   - prawdziwe: `scripts/backup.sh` i `restore.sh`, kontener rclone, wszystkie jego polecenia
#     i flagi (copy, check --one-way --include, delete --min-age, rcat, lsf, cat, deletefile,
#     rmdir), montowanie katalogu kopii i katalogu sekretów, zapis tokenu do secrets/;
#   - podmienione: typ zdalnego miejsca. Atrapa `docker` przepisuje w wywołaniu rclone
#     RCLONE_CONFIG_OFFSITE_TYPE=drive na `alias` wskazujący zamontowany katalog `remote/`.
#     Opcje Dysku (SCOPE, USE_TRASH, token w pliku) dalej są w wywołaniu – rclone je czyta, ale
#     backend `alias` ich nie używa. Tego, czy Google przyjmie token, ten test nie sprawdzi:
#     od tego jest `scripts/backup.sh --offsite-test` na serwerze.
#   - atrapy jak w backup_offsite_test.sh: pg_dump, mc mirror, gpg, meldunek do aplikacji.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.69}"

REAL_DOCKER="$(command -v docker || true)"
if [ -z "$REAL_DOCKER" ] || ! "$REAL_DOCKER" info >/dev/null 2>&1; then
  echo "skip: Docker nie działa – test e2e pominięty"
  exit 0
fi
"$REAL_DOCKER" image inspect "$RCLONE_IMAGE" >/dev/null 2>&1 || "$REAL_DOCKER" pull -q "$RCLONE_IMAGE" >/dev/null

WORK="$(mktemp -d "${TMPDIR:-/tmp}/backup-offsite-e2e.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}

BIN="$WORK/bin"
mkdir -p "$BIN"
cat >"$BIN/docker" <<STUB
#!/usr/bin/env bash
REAL_DOCKER="$REAL_DOCKER"
STUB
cat >>"$BIN/docker" <<'STUB'
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  "compose exec -T db pg_dump"*) head -c 200000 /dev/urandom ; exit 0 ;;
  "compose ps -q minio") echo cid123 ; exit 0 ;;
  "inspect -f"*) echo proj_internal ; exit 0 ;;
  "compose exec -T web python manage.py record_backup_status"*) exit 0 ;;
  "run --rm --network"*) exit 0 ;;
esac
# Ścieżka hosta dla Dockera Desktop pod Git Bashem (C:/…); na Linuksie bez zmian.
host() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
out=() prev=""
for a in "$@"; do
  if [ "$prev" = "-v" ]; then a="$(host "${a%%:*}"):${a#*:}"; fi
  case "$a" in
    RCLONE_CONFIG_OFFSITE_TYPE=*) a="RCLONE_CONFIG_OFFSITE_TYPE=alias" ;;
    rclone/rclone*) out+=(-e RCLONE_CONFIG_OFFSITE_REMOTE=/remote -v "$(host "$REMOTE"):/remote") ;;
  esac
  out+=("$a"); prev="$a"
done
MSYS_NO_PATHCONV=1 exec "$REAL_DOCKER" "${out[@]}"
STUB
cat >"$BIN/gpg" <<'STUB'
#!/usr/bin/env bash
cat >/dev/null
out="" prev=""
for a in "$@"; do [ "$prev" = "--output" ] && out="$a"; prev="$a"; done
cp "${!#}" "$out"
STUB
chmod +x "$BIN/docker" "$BIN/gpg"

CASE="$WORK/case"
mkdir -p "$CASE/repo" "$CASE/backups" "$CASE/remote"
TOKEN='{"access_token":"ya29.E2E","token_type":"Bearer","refresh_token":"1//E2E","expiry":"2026-09-25T20:00:00Z"}'
cat >"$CASE/repo/.env" <<ENV
POSTGRES_USER=olimpiada
POSTGRES_DB=olimpiada
MINIO_ROOT_USER=minio
MINIO_ROOT_PASSWORD=minio-secret
BACKUP_PASSPHRASE=test-passphrase
BACKUP_REMOTE_TYPE=drive
ENV
export DOCKER_LOG="$CASE/docker.log" REMOTE="$CASE/remote"
: >"$DOCKER_LOG"
run() { REPO_DIR="$CASE/repo" BACKUP_DIR="$CASE/backups" PATH="$BIN:$PATH" bash "$ROOT/scripts/$1" "${@:2}" >"$CASE/out.txt" 2>&1; }

# 1. Token wklejony jak przez organizatora.
printf '%s\n' "$TOKEN" | run backup.sh --drive-token
check "--drive-token zapisuje token" $?

# 2. Test połączenia na prawdziwym rclone (rcat przez stdin kontenera).
run backup.sh --offsite-test; rc=$?
[ $rc -eq 0 ] && grep -q 'Test udany' "$CASE/out.txt"
check "--offsite-test przechodzi na prawdziwym rclone" $?
[ -z "$(ls -A "$CASE/remote/Olimpiada-kopie-zapasowe/test" 2>/dev/null)" ]
check "--offsite-test sprząta po sobie" $?
[ $rc -eq 0 ] || cat "$CASE/out.txt"

# 3. Stare kopie „po tamtej stronie” – do sprawdzenia retencji.
D="$CASE/remote/Olimpiada-kopie-zapasowe"
mkdir -p "$D/daily" "$D/monthly"
echo stara >"$D/daily/db-20260801T031500Z.dump.gpg";   touch -d '40 days ago' "$D/daily/db-20260801T031500Z.dump.gpg"
echo swieza >"$D/daily/db-20260915T031500Z.dump.gpg";  touch -d '10 days ago' "$D/daily/db-20260915T031500Z.dump.gpg"
echo stara >"$D/monthly/db-20250801T031500Z.dump.gpg"; touch -d '400 days ago' "$D/monthly/db-20250801T031500Z.dump.gpg"
echo mies >"$D/monthly/db-20260601T031500Z.dump.gpg";  touch -d '100 days ago' "$D/monthly/db-20260601T031500Z.dump.gpg"

# 4. Nocny przebieg.
run backup.sh; rc=$?
check "backup.sh: kod 0" $rc
[ $rc -eq 0 ] || cat "$CASE/out.txt"
DUMP="$(basename "$(ls "$CASE/backups"/db-*.dump.gpg | head -1)")"
FILES="$(basename "$(ls "$CASE/backups"/files-*.tar.gpg | head -1)")"
cmp -s "$CASE/backups/$DUMP" "$D/daily/$DUMP" && cmp -s "$CASE/backups/$FILES" "$D/daily/$FILES"
check "obie paczki w daily/ – bajt w bajt" $?
[ ! -e "$D/daily/db-20260801T031500Z.dump.gpg" ] && [ -e "$D/daily/db-20260915T031500Z.dump.gpg" ]
check "retencja daily: 40 dni skasowane, 10 dni zostaje" $?
[ ! -e "$D/monthly/db-20250801T031500Z.dump.gpg" ] && [ -e "$D/monthly/db-20260601T031500Z.dump.gpg" ]
check "retencja monthly: 400 dni skasowane, 100 dni zostaje" $?
grep -q 'zweryfikowana sumą kontrolną' "$CASE/out.txt" && grep -q -- '--ok --offsite' "$DOCKER_LOG"
check "meldunek --ok --offsite po weryfikacji" $?
grep -qF "token = $TOKEN" "$CASE/repo/secrets/rclone/rclone.conf"
check "token nadal w secrets/rclone/rclone.conf" $?

# 5. Odtwarzanie: paczka ściągnięta z „Dysku” i sprawdzona sumą.
cp "$CASE/backups/$DUMP" "$WORK/oryginal"
rm -f "$CASE/backups/$DUMP"
run restore.sh --fetch "$DUMP"; rc=$?
[ $rc -eq 0 ] && cmp -s "$WORK/oryginal" "$CASE/backups/$DUMP"
check "restore.sh --fetch ściąga paczkę bajt w bajt" $?
[ $rc -eq 0 ] || cat "$CASE/out.txt"
run restore.sh --list; rc=$?
[ $rc -eq 0 ] && grep -q "$DUMP" "$CASE/out.txt"
check "restore.sh --list pokazuje kopię z „Dysku”" $?

echo
if [ "$failures" -eq 0 ]; then echo "Wszystkie sprawdzenia przeszły."; else echo "Nieudanych sprawdzeń: $failures"; exit 1; fi
