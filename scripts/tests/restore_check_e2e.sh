#!/usr/bin/env bash
# Conocny test odtwarzania (OPS-01) od początku do końca na lokalnym Dockerze – bez atrap.
#
# Uruchomienie (Git Bash / Linux, Docker musi działać; nic nie jest pobierane – potrzebne obrazy:
# postgres:18-alpine, redis:7-alpine i obraz aplikacji, domyślnie olimpiada/web:dev):
#   scripts/tests/restore_check_e2e.sh
#
# Co jest prawdziwe: `scripts/backup_verify.sh` w całości, gpg, Postgres (żywy i tymczasowy),
# pg_dump/pg_restore, obraz aplikacji z kodem z TEGO drzewa (montowany), komendy
# `restore_check live-counts|verify|record|show`, wysyłka listu alarmowego (backend konsolowy).
# Co jest udawane: „produkcja” to trzy jednorazowe kontenery na własnej sieci (Postgres z danymi
# testowymi, Redis, `web` uśpiony `sleep`), a nie stos compose – stos compose ma stałe podsieci
# i drugi egzemplarz obok stosu deweloperskiego by nie wstał. Paczka plików to tar z plikami prac
# zapisanymi w bazie (bez MinIO – lustro kubełków robi backup.sh, a nie test odtwarzania).
#
# Przypadki:
#   1. świeży zrzut + paczka plików           -> kod 0, wynik ok, poziom `ok`, historia w jsonl,
#   2. zrzut obcięty w połowie (uszkodzony)   -> kod 1, krok pg_restore, poziom `failed`, list ALARM,
#   3. zrzut sprzed 30 h                      -> kod 1, sprawdzenie backup_age,
#   4. brak paczki plików z tej nocy          -> kod 1, files_archive i media_sample,
#   5. `restore_check verify` na bazie żywej  -> odmowa bramki, baza żywa nietknięta.
# Na końcu wypisuje czasy z przypadku 1 (RTO lokalne, docs/OPERACJE.md § 43.4).
set -uo pipefail
export MSYS_NO_PATHCONV=1 LC_NUMERIC=C

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WEB_IMAGE="${WEB_TEST_IMAGE:-olimpiada/web:dev}"
PG_IMAGE="${PG_IMAGE:-postgres:18-alpine}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"

if ! docker info >/dev/null 2>&1; then echo "skip: Docker nie działa – test e2e pominięty"; exit 0; fi
for image in "$WEB_IMAGE" "$PG_IMAGE" "$REDIS_IMAGE"; do
  docker image inspect "$image" >/dev/null 2>&1 || { echo "skip: brak obrazu $image (test niczego nie pobiera)"; exit 0; }
done
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
BACKEND="$(host_path "$ROOT/backend")"
# Docker Desktop pod Git Bashem nie rozumie ścieżek /tmp/… (konwersja MSYS jest wyłączona wyżej,
# bo psułaby ścieżki wewnątrz kontenerów) – katalogi robocze testu i skryptu w zapisie C:/….
TMPDIR="$(host_path "${TMPDIR:-/tmp}")"
export TMPDIR

ID="rce2e-$$"
NET="${ID}-net" DB="${ID}-db" REDIS="${ID}-redis" WEB="${ID}-web"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/restore-check-e2e.XXXXXX")"
cleanup() {
  docker rm -f -v "$WEB" "$DB" "$REDIS" >/dev/null 2>&1
  # Kontenery testu odtwarzania sprząta sam skrypt; gdyby przerwany – po nazwie.
  docker ps -aq --filter name=olimpiada-restore-check- | xargs -r docker rm -f -v >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
  [ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"
}
trap cleanup EXIT

failures=0
RUNS=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}
rand() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# --- „produkcja”: Postgres, Redis, web ----------------------------------------------------------
PG_PASSWORD="$(rand)"
PASSPHRASE="$(rand)"
docker network create --internal "$NET" >/dev/null
docker run -d --name "$DB" --network "$NET" -e POSTGRES_USER=olimpiada -e POSTGRES_PASSWORD="$PG_PASSWORD" \
  -e POSTGRES_DB=olimpiada --tmpfs /var/lib/postgresql:rw,size=3g "$PG_IMAGE" >/dev/null
docker run -d --name "$REDIS" --network "$NET" "$REDIS_IMAGE" >/dev/null
cat >"$WORK/web.env" <<ENV
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_DEBUG=1
DJANGO_SECRET_KEY=$(rand)$(rand)
DATABASE_URL=postgres://olimpiada:${PG_PASSWORD}@${DB}:5432/olimpiada
POSTGRES_DB=olimpiada
REDIS_URL=redis://${REDIS}:6379/0
CELERY_BROKER_URL=redis://${REDIS}:6379/1
MINIO_ROOT_USER=e2e
MINIO_ROOT_PASSWORD=$(rand)
S3_ENDPOINT_URL=http://127.0.0.1:1
EMAIL_URL=consolemail://
ALERT_EMAILS=dyzurny@example.test
SITE_DOMAIN=localhost
ENV
docker run -d --name "$WEB" --network "$NET" --env-file "$WORK/web.env" -v "$BACKEND:/app" \
  --entrypoint sleep "$WEB_IMAGE" infinity >/dev/null
for _ in $(seq 1 60); do docker exec "$DB" pg_isready -h 127.0.0.1 -U olimpiada >/dev/null 2>&1 && break; sleep 1; done
manage() { docker exec -i "$WEB" python manage.py "$@"; }

echo "==> migracje bazy „żywej” (kilka minut)"
manage migrate --noinput >"$WORK/migrate.log" 2>&1
check "migracje bazy żywej" $?
[ "$failures" -eq 0 ] || { tail -20 "$WORK/migrate.log"; exit 1; }

# Dane: konto, delegacja z zaszyfrowanym numerem paszportu (Fernet), trzy pliki prac (clean).
manage shell >"$WORK/seed.out" 2>"$WORK/seed.err" <<'PY'
from apps.accounts.tests.factories import CoordinatorFactory, UserFactory
from apps.accounts.tests.test_delegations import leader_for_country, make_delegations_competition
from apps.delegation_logistics.models import DelegationMember, MemberKind
from apps.submissions.models import AvStatus
from apps.submissions.tests.factories import SubmissionFileFactory
from apps.tenancy.context import competition_context
from apps.tenancy.models import Competition

competition = Competition.objects.order_by("pk").first()
with competition_context(competition):
    UserFactory.create_batch(5)
    make_delegations_competition(competition)
    leader = leader_for_country(competition, CoordinatorFactory(), "lead-e2e@example.test")
    DelegationMember.objects.create(
        delegation=leader.delegation, kind=MemberKind.LEADER, user=leader.user, passport_number="E2E000001"
    )
    for item in SubmissionFileFactory.create_batch(3, av_status=AvStatus.CLEAN, competition=competition):
        print("KEY", item.object_key)
PY
check "dane testowe (konta, delegacja, pliki prac)" $?
grep -q '^KEY ' "$WORK/seed.out" || { tail -20 "$WORK/seed.err"; exit 1; }
# Opcjonalny balast do pomiaru RTO na bazie rzędu produkcyjnej: RESTORE_CHECK_E2E_AUDIT_ROWS=1000000
# dokłada tyle wierszy audytu (~300 B każdy) – zrzut rośnie do kilkudziesięciu MB, baza do setek.
if [ "${RESTORE_CHECK_E2E_AUDIT_ROWS:-0}" -gt 0 ]; then
  docker exec "$DB" psql -q -U olimpiada -d olimpiada -c "INSERT INTO core_auditlog (action, target_type, target_id, diff, at)
    SELECT 'e2e.load', 'core.e2e', g::text, jsonb_build_object('n', g, 'pad', repeat('x', 200)), now()
    FROM generate_series(1, ${RESTORE_CHECK_E2E_AUDIT_ROWS}) g" >/dev/null
  check "balast: ${RESTORE_CHECK_E2E_AUDIT_ROWS} wierszy audytu" $?
  echo "    baza żywa: $(docker exec "$DB" psql -U olimpiada -d olimpiada -Atc "SELECT pg_size_pretty(pg_database_size('olimpiada'))")"
fi

# --- kopia: pg_dump + tar plików, zaszyfrowane jak w backup.sh ---------------------------------
mkdir -p "$WORK/repo" "$WORK/backups"
printf 'BACKUP_PASSPHRASE=%s\nPOSTGRES_IMAGE=%s\n' "$PASSPHRASE" "$PG_IMAGE" >"$WORK/repo/.env"
encrypt() { printf '%s' "$PASSPHRASE" | gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-fd 0 \
  --symmetric --cipher-algo AES256 --compress-algo none --output "$2" "$1"; }
make_backup() {  # $1 = znacznik UTC
  docker exec "$DB" pg_dump -U olimpiada -d olimpiada -Fc >"$WORK/db.dump"
  rm -rf "$WORK/buckets"; mkdir -p "$WORK/buckets/submissions" "$WORK/buckets/public-media"
  sed -n 's/^KEY //p' "$WORK/seed.out" | tr -d '\r' | while read -r key; do
    mkdir -p "$WORK/buckets/submissions/$(dirname "$key")"; printf '%%PDF-1.4' >"$WORK/buckets/submissions/$key"
  done
  tar --force-local -cf "$WORK/files.tar" -C "$WORK/buckets" .
  encrypt "$WORK/db.dump" "$WORK/backups/db-$1.dump.gpg"
  encrypt "$WORK/files.tar" "$WORK/backups/files-$1.tar.gpg"
}
verify() {
  REPO_DIR="$WORK/repo" BACKUP_DIR="$WORK/backups" LIVE_WEB_CID="$WEB" \
    RESTORE_CHECK_APP_VOLUME="$BACKEND:/app:ro" bash "$ROOT/scripts/backup_verify.sh" "$@" >"$WORK/out.txt" 2>&1
  local rc=$?; cp "$WORK/out.txt" "$WORK/out-$((++RUNS)).txt"; return $rc
}
level() { manage restore_check show </dev/null 2>/dev/null | sed -n 's/^poziom: \([a-z]*\).*/\1/p'; }

# 1. Świeża kopia – przechodzi.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
make_backup "$STAMP"
verify; rc=$?
check "1. świeża kopia: kod 0" $rc
[ $rc -eq 0 ] || tail -40 "$WORK/out.txt"
[ "$(level)" = "ok" ]
check "1. poziom w aplikacji: ok" $?
grep -q '"status": "ok"' "$WORK/backups/restore-checks.jsonl"
check "1. wiersz historii restore-checks.jsonl" $?
grep -qE 'ok +fernet +1 szyfrogram' "$WORK/out.txt"
check "1. Fernet: szyfrogram z kopii odszyfrowany bieżącym kluczem" $?
grep -qE 'ok +media_sample +3 plików' "$WORK/out.txt"
check "1. próbka plików prac obecna w paczce" $?
! grep -q 'E2E000001' "$WORK/out.txt" "$WORK/backups/restore-checks.jsonl"
check "1. odszyfrowana wartość nie trafia do logu ani historii" $?
[ -z "$(docker ps -aq --filter name=olimpiada-restore-check-)" ] && [ -z "$(docker network ls -q --filter name=olimpiada-restore-check-net-)" ]
check "1. kontenery i sieć testu sprzątnięte" $?
GOOD_RESULT="$(tail -1 "$WORK/backups/restore-checks.jsonl")"

# 2. Zrzut uszkodzony (obcięty w połowie) – alarm.
SIZE="$(stat -c %s "$WORK/db.dump")"
head -c $((SIZE / 2)) "$WORK/db.dump" >"$WORK/db-cut.dump"
STAMP2="$(date -u +%Y%m%dT%H%M%SZ)"
sleep 1
encrypt "$WORK/db-cut.dump" "$WORK/backups/db-$STAMP2.dump.gpg"
cp "$WORK/backups/files-$STAMP.tar.gpg" "$WORK/backups/files-$STAMP2.tar.gpg"
verify; rc=$?
[ $rc -eq 1 ] && grep -q 'NIEUDANY: pg_restore' "$WORK/out.txt"
check "2. uszkodzony zrzut: kod 1, krok pg_restore" $?
[ "$(level)" = "failed" ]
check "2. poziom w aplikacji: failed" $?
grep -q 'ALARM: test odtwarzania kopii zapasowej NIEUDANY' "$WORK/out.txt" && grep -q 'dyzurny@example.test' "$WORK/out.txt"
check "2. list alarmowy do ALERT_EMAILS" $?
rm -f "$WORK/backups/db-$STAMP2.dump.gpg" "$WORK/backups/files-$STAMP2.tar.gpg"

# 3. Kopia sprzed 30 h.
OLD="$(date -u -d '30 hours ago' +%Y%m%dT%H%M%SZ)"
cp "$WORK/backups/db-$STAMP.dump.gpg" "$WORK/backups/db-$OLD.dump.gpg"
cp "$WORK/backups/files-$STAMP.tar.gpg" "$WORK/backups/files-$OLD.tar.gpg"
verify "$WORK/backups/db-$OLD.dump.gpg"; rc=$?
[ $rc -eq 1 ] && grep -qE 'fail +backup_age +kopia za stara' "$WORK/out.txt"
check "3. kopia sprzed 30 h: kod 1, backup_age" $?

# 4. Brak paczki plików z tej nocy.
rm -f "$WORK/backups/files-$OLD.tar.gpg"
STAMP4="$(date -u +%Y%m%dT%H%M%SZ)"
cp "$WORK/backups/db-$STAMP.dump.gpg" "$WORK/backups/db-$STAMP4.dump.gpg"
verify "$WORK/backups/db-$STAMP4.dump.gpg"; rc=$?
[ $rc -eq 1 ] && grep -qE 'fail +files_archive' "$WORK/out.txt" && grep -qE 'fail +media_sample' "$WORK/out.txt"
check "4. brak paczki plików: kod 1, files_archive + media_sample" $?

# 5. Bramka: `verify` w kontenerze web (baza żywa) odmawia.
before="$(docker exec "$DB" psql -U olimpiada -d olimpiada -Atc 'SELECT count(*) FROM accounts_user')"
printf '{}\n' | manage restore_check verify >"$WORK/guard.txt" 2>&1; rc=$?
after="$(docker exec "$DB" psql -U olimpiada -d olimpiada -Atc 'SELECT count(*) FROM accounts_user')"
[ $rc -ne 0 ] && grep -q 'odmowa' "$WORK/guard.txt" && [ "$before" = "$after" ]
check "5. verify na bazie żywej: odmowa, baza nietknięta" $?

echo
echo "Czasy przypadku 1 (RTO lokalne):"
printf '%s\n' "$GOOD_RESULT" | python -c 'import json,sys; r=json.load(sys.stdin); b=r["backup"]; print("  kopia:", b.get("size_bytes"), "B, pliki:", b.get("files_size_bytes"), "B"); print("  czasy [s]:", r["timings"])' 2>/dev/null \
  || printf '%s\n' "$GOOD_RESULT" | grep -o '"timings": {[^}]*}'
echo
if [ "$failures" -eq 0 ]; then echo "Wszystkie sprawdzenia przeszły."; else echo "Nieudanych sprawdzeń: $failures"; exit 1; fi
