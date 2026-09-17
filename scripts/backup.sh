#!/usr/bin/env bash
# Kopia zapasowa platformy: zrzut bazy + lustro kubełków MinIO, zaszyfrowane i wysłane poza serwer.
#
# Uruchamiany przez crona hosta (wpis /etc/cron.d/olimpiada-backup zakłada scripts/deploy.sh),
# ręcznie: `cd /opt/olimpiada && scripts/backup.sh`.
#
# Dlaczego kopia w ogóle musi wyjechać poza serwer: wszystko, co trzyma ta platforma – prace
# uczestników, protokoły recenzji, decyzje komisji odwoławczej – istnieje w jednym egzemplarzu na
# jednej maszynie. Wolumen `pg_data` i wolumen `minio_data` giną razem z tą maszyną, więc kopia
# leżąca obok nich chroni wyłącznie przed „skasowałem nie tę tabelę”, a nie przed utratą serwera.
# Stąd trzy kroki, z których żaden nie jest opcjonalny: zrzut, szyfrowanie, wysyłka gdzie indziej.
#
# Dlaczego szyfrujemy PRZED wysyłką, a nie ufamy szyfrowaniu u dostawcy: w tej paczce są dane
# osobowe małoletnich (art. 9 RODO ich nie obejmuje, ale art. 32 owszem). Powierzenie ich obcemu
# operatorowi obiektowemu bez szyfrowania po naszej stronie znaczyłoby, że o poufności decyduje
# jego regulamin i jego administrator. Hasło (BACKUP_PASSPHRASE) nie wyjeżdża razem z paczką –
# i dlatego jego utrata jest równoznaczna z utratą kopii. Patrz docs/OPERACJE.md § „Kopie zapasowe”.
#
# Wymagane w .env (poza tym, co już tam jest na potrzeby compose):
#   BACKUP_PASSPHRASE    hasło do symetrycznego szyfrowania paczek (gpg AES-256)
# Opcjonalne – bez nich skrypt robi wyłącznie kopię lokalną i mówi o tym na stdout:
#   BACKUP_REMOTE_URL    endpoint S3-kompatybilny, np. https://s3.eu-central-003.backblazeb2.com
#   BACKUP_ACCESS_KEY / BACKUP_SECRET_KEY    poświadczenia do tego endpointu
#   BACKUP_BUCKET        nazwa kubełka po tamtej stronie
#   BACKUP_REMOTE_REGION region (część dostawców jej wymaga; domyślnie pusta)
#
# Retencja: 30 kopii dziennych i 12 miesięcznych po stronie zdalnej, 7 dni lokalnie.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

# Katalog kopii lokalnych. POZA katalogiem repozytorium, bo krok 2/7 wdrożenia czyści /opt/olimpiada
# (`find ... -exec rm -rf`) – kopia trzymana w repozytorium znikałaby przy każdym wdrożeniu, czyli
# dokładnie wtedy, gdy jest najbardziej potrzebna.
BACKUP_DIR="${BACKUP_DIR:-/opt/olimpiada-backups}"
LOCAL_KEEP_DAYS="${LOCAL_KEEP_DAYS:-7}"
REMOTE_DAILY_KEEP_DAYS="${REMOTE_DAILY_KEEP_DAYS:-30}"
REMOTE_MONTHLY_KEEP_DAYS="${REMOTE_MONTHLY_KEEP_DAYS:-365}"

# Obrazy narzędzi. Przypięte do wydania (nie `latest`), bo kopia zapasowa jest jedyną rzeczą,
# której nie wolno zepsuć cichą zmianą wersji narzędzia – błąd wyjdzie dopiero przy odtwarzaniu.
MC_IMAGE="${MC_IMAGE:-minio/mc:RELEASE.2025-04-16T18-13-26Z}"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.69}"

log() { printf '==> %s\n' "$*"; }
die() { printf 'BŁĄD: %s\n' "$*" >&2; exit 1; }

[ -f .env ] || die "brak pliku .env w $REPO_DIR"
# `set -a` zamiast czytania pliku linia po linii: .env jest w formacie klucz=wartość bez cudzysłowów
# (tworzy go scripts/deploy.sh), a docker compose czyta go tak samo.
set -a
# shellcheck disable=SC1091
. ./.env
set +a

: "${POSTGRES_USER:?POSTGRES_USER musi być w .env}"
: "${POSTGRES_DB:?POSTGRES_DB musi być w .env}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE musi być w .env – bez hasła nie szyfrujemy, a bez szyfrowania nie wysyłamy}"

command -v gpg >/dev/null 2>&1 || die "brak gpg na hoście (apt-get install -y gnupg)"
command -v docker >/dev/null 2>&1 || die "brak dockera na hoście"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DAY_OF_MONTH="$(date -u +%d)"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Katalog roboczy na lustro kubełków. Sprzątany zawsze, także po błędzie – inaczej nieudany przebieg
# zostawiałby na dysku rozszyfrowane prace uczestników.
WORK_DIR="$(mktemp -d "${BACKUP_DIR}/.work-XXXXXX")"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

encrypt() {
    # $1 = plik jawny, $2 = plik wyjściowy. Hasło idzie przez potok na deskryptor, a nie w argumencie:
    # argumenty procesu widzi każdy `ps` na maszynie, w tym konto bez uprawnień.
    printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --yes --quiet \
        --pinentry-mode loopback --passphrase-fd 0 \
        --symmetric --cipher-algo AES256 --compress-algo none \
        --output "$2" "$1"
    chmod 600 "$2"
}

# --- 1. Zrzut bazy ---------------------------------------------------------------------------
# Format „custom” (-Fc), a nie zwykły SQL: pozwala odtworzyć wybrane tabele (`pg_restore -t`),
# jest skompresowany i nie wymaga, żeby baza docelowa nazywała się tak samo. Odtwarzanie wybiórcze
# jest tu realnym scenariuszem – najczęstsza awaria to „koordynator skasował nie tę edycję”.
DUMP_PLAIN="${WORK_DIR}/db-${STAMP}.dump"
DUMP_FILE="${BACKUP_DIR}/db-${STAMP}.dump.gpg"
log "1/5 Zrzut bazy ${POSTGRES_DB}"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc </dev/null > "$DUMP_PLAIN"
[ -s "$DUMP_PLAIN" ] || die "pg_dump zwrócił pusty plik"
encrypt "$DUMP_PLAIN" "$DUMP_FILE"
log "    $(du -h "$DUMP_FILE" | cut -f1) -> $DUMP_FILE"

# --- 2. Lustro kubełków MinIO ----------------------------------------------------------------
# Prace uczestników NIE są w bazie – w bazie są tylko metryki plików. Kopia samej bazy dałaby więc
# po odtworzeniu komplet wpisów „uczestnik oddał rozwiązanie zadania 2” bez ani jednego pliku.
#
# `mc` uruchamiamy w osobnym kontenerze dołączonym do tej samej sieci compose, co MinIO. Wnętrze
# kontenera `minio` też ma `mc`, ale zapis lustra musiałby wtedy wylądować na wolumenie danych
# MinIO – czyli kopia leżałaby w tym samym miejscu, którego kopią miała być.
compose_network() {
    local cid
    cid="$(docker compose ps -q minio)"
    [ -n "$cid" ] || die "usługa minio nie działa – nie ma czego kopiować"
    docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' "$cid" \
        | grep -E '_internal$' | head -1
}
NETWORK="$(compose_network)"
[ -n "$NETWORK" ] || die "nie znalazłem sieci compose 'internal' kontenera minio"

log "2/5 Lustro kubełków MinIO (sieć ${NETWORK})"
mkdir -p "${WORK_DIR}/buckets"
docker run --rm --network "$NETWORK" \
    -v "${WORK_DIR}/buckets:/backup" \
    -e MC_HOST_src="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
    -e MC_QUIET=on -e MC_NO_COLOR=on \
    --entrypoint sh "$MC_IMAGE" -c '
        set -e
        for bucket in submissions public-media; do
            # Zwykłe wyjście mc to jedna linia na plik, czyli w logu crona kilkadziesiąt tysięcy
            # linii co noc. Wyrzucamy je, ale WYŁĄCZNIE ze standardowego wyjścia – błędy idą na
            # stderr i zostają, a `set -e` nadal przerwie przebieg na niezerowym kodzie.
            mc mirror --overwrite --remove "src/$bucket" "/backup/$bucket" >/dev/null
        done
    '
# tar przed szyfrowaniem: gpg szyfruje jeden strumień, a kubełki to tysiące małych plików.
# `--sort=name` daje powtarzalną kolejność, dzięki czemu dwie kopie tej samej treści są identyczne
# bajt w bajt i deduplikacja u dostawcy w ogóle ma co robić.
FILES_PLAIN="${WORK_DIR}/files-${STAMP}.tar"
FILES_FILE="${BACKUP_DIR}/files-${STAMP}.tar.gpg"
tar --sort=name -cf "$FILES_PLAIN" -C "${WORK_DIR}/buckets" .
encrypt "$FILES_PLAIN" "$FILES_FILE"
log "    $(du -h "$FILES_FILE" | cut -f1) -> $FILES_FILE"

# --- 3. Wysyłka poza serwer ------------------------------------------------------------------
REMOTE_OK=0
if [ -z "${BACKUP_REMOTE_URL:-}" ]; then
    log "3/5 BACKUP_REMOTE_URL nie jest ustawione – kopia zostaje WYŁĄCZNIE lokalnie (${BACKUP_DIR})."
    log "    To nie jest kopia zapasowa w sensie, w jakim potrzebuje jej olimpiada: ginie razem"
    log "    z serwerem. Konfiguracja zdalna: docs/OPERACJE.md § Kopie zapasowe."
else
    : "${BACKUP_ACCESS_KEY:?BACKUP_ACCESS_KEY wymagane razem z BACKUP_REMOTE_URL}"
    : "${BACKUP_SECRET_KEY:?BACKUP_SECRET_KEY wymagane razem z BACKUP_REMOTE_URL}"
    : "${BACKUP_BUCKET:?BACKUP_BUCKET wymagane razem z BACKUP_REMOTE_URL}"

    # rclone konfigurujemy zmiennymi środowiskowymi (RCLONE_CONFIG_<REMOTE>_<OPCJA>), a nie plikiem
    # rclone.conf: plik byłby kolejnym miejscem, w którym leżą poświadczenia, i trzeba by pilnować
    # jego uprawnień. Zmienne żyją tyle, co proces kontenera.
    rclone() {
        docker run --rm \
            -v "${BACKUP_DIR}:/data:ro" \
            -e RCLONE_CONFIG_OFFSITE_TYPE=s3 \
            -e RCLONE_CONFIG_OFFSITE_PROVIDER="${BACKUP_REMOTE_PROVIDER:-Other}" \
            -e RCLONE_CONFIG_OFFSITE_ENDPOINT="$BACKUP_REMOTE_URL" \
            -e RCLONE_CONFIG_OFFSITE_REGION="${BACKUP_REMOTE_REGION:-}" \
            -e RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID="$BACKUP_ACCESS_KEY" \
            -e RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY="$BACKUP_SECRET_KEY" \
            -e RCLONE_CONFIG_OFFSITE_ACL=private \
            -e RCLONE_CONFIG_OFFSITE_NO_CHECK_BUCKET=true \
            "$RCLONE_IMAGE" "$@"
    }
    log "3/5 Wysyłka do offsite:${BACKUP_BUCKET}/daily/"
    rclone copy "/data/$(basename "$DUMP_FILE")" "offsite:${BACKUP_BUCKET}/daily/"
    rclone copy "/data/$(basename "$FILES_FILE")" "offsite:${BACKUP_BUCKET}/daily/"
    REMOTE_OK=1

    # Kopia miesięczna pierwszego dnia miesiąca. Osobny prefiks, a nie dłuższa retencja dzienna:
    # awarie, które wychodzą po kwartale (cicha korupcja danych, skasowana edycja sprzed roku),
    # wymagają punktu odniesienia starszego niż 30 dni, a trzymanie 365 kopii dziennych jest
    # trzydziestokrotnie droższe od trzymania dwunastu miesięcznych.
    if [ "$DAY_OF_MONTH" = "01" ]; then
        log "    Pierwszy dzień miesiąca – kopia także do monthly/"
        rclone copy "/data/$(basename "$DUMP_FILE")" "offsite:${BACKUP_BUCKET}/monthly/"
        rclone copy "/data/$(basename "$FILES_FILE")" "offsite:${BACKUP_BUCKET}/monthly/"
    fi

    # --- 4. Retencja zdalna -------------------------------------------------------------------
    # `delete --min-age` po stronie rclone, a nie reguła lifecycle u dostawcy: reguła lifecycle
    # jest niewidoczna z tego repozytorium i po zmianie dostawcy trzeba ją założyć od nowa (i nikt
    # o tym nie pamięta). Jeśli dostawca ma lifecycle i ktoś je włączy, te dwa polecenia po prostu
    # nie znajdą nic do skasowania – nie kolidują.
    log "4/5 Retencja zdalna: daily > ${REMOTE_DAILY_KEEP_DAYS}d, monthly > ${REMOTE_MONTHLY_KEEP_DAYS}d"
    rclone delete "offsite:${BACKUP_BUCKET}/daily/" --min-age "${REMOTE_DAILY_KEEP_DAYS}d"
    rclone delete "offsite:${BACKUP_BUCKET}/monthly/" --min-age "${REMOTE_MONTHLY_KEEP_DAYS}d"
fi

# --- 5. Retencja lokalna i ślad w aplikacji --------------------------------------------------
log "5/5 Retencja lokalna: > ${LOCAL_KEEP_DAYS} dni"
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.gpg' -mtime "+${LOCAL_KEEP_DAYS}" -print -delete

# Znacznik dla /status.json i dla watchdoga alertów. Zapisujemy go dopiero tutaj, po wszystkich
# krokach: „ostatnia udana kopia” ma znaczyć kopię kompletną, a nie moment rozpoczęcia przebiegu.
# Błąd tego kroku nie unieważnia samej kopii, więc nie przerywa skryptu – ale idzie na stderr.
if docker compose exec -T web python manage.py record_backup_status --ok </dev/null; then
    :
else
    printf 'UWAGA: nie udało się zapisać znacznika kopii w aplikacji (kopia sama jest zrobiona).\n' >&2
fi

if [ "$REMOTE_OK" = "1" ]; then
    log "Gotowe: kopia lokalna + zdalna (${STAMP})."
else
    log "Gotowe: kopia WYŁĄCZNIE lokalna (${STAMP})."
fi
