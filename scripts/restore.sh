#!/usr/bin/env bash
# Odtworzenie kopii zapasowej: zrzut bazy do NOWEJ bazy, pliki do NOWEGO kubełka.
#
# Użycie (z katalogu /opt/olimpiada):
#   scripts/restore.sh --list                         # co jest do odtworzenia (lokalnie i zdalnie)
#   scripts/restore.sh --dry-run                      # co by się stało z najnowszą kopią
#   scripts/restore.sh --dump db-20260117T030000Z.dump.gpg --files files-20260117T030000Z.tar.gpg
#   scripts/restore.sh --dump ... --db olimpiada_restore --bucket submissions-restore
#
# Dlaczego domyślnie do NOWEJ bazy i NOWEGO kubełka, a nie „na miejsce”: odtwarzanie robi się
# w sytuacji, w której nikt do końca nie wie, co się stało. Nadpisanie działającej bazy zrzutem
# sprzed doby zamienia wtedy jeden problem („zginęła edycja 2025”) w drugi, nieodwracalny
# („zginął dzisiejszy dzień zawodów”). Przełączenie serwisu na odtworzoną bazę jest osobną,
# świadomą czynnością opisaną w docs/OPERACJE.md § „Odtwarzanie” – i wymaga zatrzymania usług.
#
# --dry-run wypisuje każdy krok razem z rozmiarem paczki i liczbą wierszy, których się spodziewa,
# ale nie tworzy ani bazy, ani kubełka.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

BACKUP_DIR="${BACKUP_DIR:-/opt/olimpiada-backups}"
MC_IMAGE="${MC_IMAGE:-minio/mc:RELEASE.2025-04-16T18-13-26Z}"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.69}"

DRY_RUN=0
LIST_ONLY=0
DUMP_NAME=""
FILES_NAME=""
TARGET_DB=""
TARGET_BUCKET=""

log() { printf '==> %s\n' "$*"; }
die() { printf 'BŁĄD: %s\n' "$*" >&2; exit 1; }
# W trybie próbnym każde polecenie zmieniające stan przechodzi przez `run` – dzięki temu „co się
# stanie” i „co się dzieje” są tym samym ciągiem poleceń, a nie dwiema listami, które mogą się
# rozjechać przy pierwszej zmianie skryptu.
run() {
    if [ "$DRY_RUN" = "1" ]; then
        printf '    [próba] %s\n' "$*"
    else
        "$@"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --list) LIST_ONLY=1 ;;
        --dump) DUMP_NAME="${2:?--dump wymaga nazwy pliku}"; shift ;;
        --files) FILES_NAME="${2:?--files wymaga nazwy pliku}"; shift ;;
        --db) TARGET_DB="${2:?--db wymaga nazwy bazy}"; shift ;;
        --bucket) TARGET_BUCKET="${2:?--bucket wymaga nazwy kubełka}"; shift ;;
        -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "nieznany argument: $1" ;;
    esac
    shift
done

[ -f .env ] || die "brak pliku .env w $REPO_DIR"
set -a
# shellcheck disable=SC1091
. ./.env
set +a

: "${POSTGRES_USER:?POSTGRES_USER musi być w .env}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE musi być w .env – bez hasła paczek nie da się otworzyć}"

rclone_offsite() {
    docker run --rm \
        -v "${BACKUP_DIR}:/data" \
        -e RCLONE_CONFIG_OFFSITE_TYPE=s3 \
        -e RCLONE_CONFIG_OFFSITE_PROVIDER="${BACKUP_REMOTE_PROVIDER:-Other}" \
        -e RCLONE_CONFIG_OFFSITE_ENDPOINT="${BACKUP_REMOTE_URL:-}" \
        -e RCLONE_CONFIG_OFFSITE_REGION="${BACKUP_REMOTE_REGION:-}" \
        -e RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID="${BACKUP_ACCESS_KEY:-}" \
        -e RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY="${BACKUP_SECRET_KEY:-}" \
        "$RCLONE_IMAGE" "$@"
}

if [ "$LIST_ONLY" = "1" ]; then
    log "Kopie lokalne (${BACKUP_DIR}):"
    ls -1sh "$BACKUP_DIR"/*.gpg 2>/dev/null || echo "    (brak)"
    if [ -n "${BACKUP_REMOTE_URL:-}" ]; then
        log "Kopie zdalne (offsite:${BACKUP_BUCKET}):"
        rclone_offsite lsl "offsite:${BACKUP_BUCKET}/daily/" || true
        rclone_offsite lsl "offsite:${BACKUP_BUCKET}/monthly/" || true
    else
        log "BACKUP_REMOTE_URL nie jest ustawione – kopii zdalnych nie ma."
    fi
    exit 0
fi

# Domyślnie najnowsza para paczek z katalogu lokalnego. `ls -t` po nazwie wzorca, a nie `find |
# sort`: nazwy niosą znacznik czasu w UTC w formacie sortowalnym leksykograficznie, więc obie drogi
# dają to samo, a ta jest czytelna w logu awaryjnym o trzeciej w nocy.
latest() { ls -1t "$BACKUP_DIR"/$1 2>/dev/null | head -1; }
[ -n "$DUMP_NAME" ] || DUMP_NAME="$(basename "$(latest 'db-*.dump.gpg')" 2>/dev/null || true)"
[ -n "$FILES_NAME" ] || FILES_NAME="$(basename "$(latest 'files-*.tar.gpg')" 2>/dev/null || true)"
[ -n "$DUMP_NAME" ] || die "nie znalazłem żadnej kopii bazy w ${BACKUP_DIR} (pobierz ją: scripts/restore.sh --list)"

DUMP_PATH="${BACKUP_DIR}/${DUMP_NAME}"
[ -f "$DUMP_PATH" ] || die "brak pliku ${DUMP_PATH}"

# Nazwa bazy i kubełka niosą znacznik kopii. Dwa odtworzenia tej samej doby nie mogą wpaść na
# siebie, a po tygodniu musi być widać, z czego powstała baza `olimpiada_restore_…`.
STAMP="$(printf '%s' "$DUMP_NAME" | sed -E 's/^db-([0-9TZ]+)\.dump\.gpg$/\1/')"
# Znacznik w postaci przyjaznej dla identyfikatorów: bez „Z” na końcu (dałoby podkreślenie-sierotę
# w nazwie bazy) i małymi literami (nazwy kubełków S3 nie dopuszczają wielkich).
SLUG="$(printf '%s' "${STAMP%Z}" | tr 'T-' '__' | tr '[:upper:]' '[:lower:]')"
TARGET_DB="${TARGET_DB:-restore_${SLUG}}"
TARGET_BUCKET="${TARGET_BUCKET:-submissions-restore-$(printf '%s' "$SLUG" | tr '_' '-')}"

log "Kopia bazy:   $DUMP_PATH"
log "Kopia plików: ${FILES_NAME:-(brak – odtwarzam samą bazę)}"
log "Baza docelowa:   $TARGET_DB   (NOWA, istniejąca nie jest ruszana)"
log "Kubełek docelowy: $TARGET_BUCKET (NOWY)"
[ "$DRY_RUN" = "1" ] && log "TRYB PRÓBNY – nic nie zostanie utworzone ani zapisane."

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/olimpiada-restore-XXXXXX")"
chmod 700 "$WORK_DIR"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

decrypt() {
    printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --yes --quiet \
        --pinentry-mode loopback --passphrase-fd 0 --decrypt --output "$2" "$1"
}

# --- 1. Rozszyfrowanie -----------------------------------------------------------------------
# Robimy je także w trybie próbnym i to jest sedno tego trybu: najczęstsza przyczyna nieudanego
# odtwarzania to nie uszkodzona paczka, tylko złe hasło w .env. Lepiej, żeby wyszło przy próbie.
log "1/4 Rozszyfrowanie paczki bazy"
DUMP_PLAIN="${WORK_DIR}/db.dump"
decrypt "$DUMP_PATH" "$DUMP_PLAIN"
log "    rozmiar po rozszyfrowaniu: $(du -h "$DUMP_PLAIN" | cut -f1)"

# --- 2. Nowa baza ----------------------------------------------------------------------------
log "2/4 Tworzenie bazy ${TARGET_DB}"
if [ "$DRY_RUN" = "0" ]; then
    docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres \
        -Atc "SELECT 1 FROM pg_database WHERE datname='${TARGET_DB}'" </dev/null | grep -q 1 \
        && die "baza ${TARGET_DB} już istnieje – podaj inną przez --db"
fi
run docker compose exec -T db createdb -U "$POSTGRES_USER" "$TARGET_DB"

log "3/4 pg_restore -> ${TARGET_DB}"
if [ "$DRY_RUN" = "1" ]; then
    printf '    [próba] docker compose exec -T db pg_restore -U %s -d %s --no-owner\n' "$POSTGRES_USER" "$TARGET_DB"
    # Sprawdzenie nagłówka zamiast pełnego spisu treści (`pg_restore -l`): ten drugi wymaga pliku,
    # po którym da się skakać, a przez `docker compose exec` wchodzi potok. Pięć bajtów „PGDMP”
    # na początku odpowiada za to, na czym w trybie próbnym naprawdę nam zależy: czy po
    # rozszyfrowaniu wyszedł zrzut Postgresa, czy coś zupełnie innego (np. paczka z innym hasłem).
    if [ "$(head -c 5 "$DUMP_PLAIN")" = "PGDMP" ]; then
        printf '    [próba] nagłówek paczki: PGDMP (zrzut w formacie custom) – wygląda poprawnie\n'
    else
        die "po rozszyfrowaniu to nie jest zrzut Postgresa (brak nagłówka PGDMP)"
    fi
else
    # `--no-owner`: zrzut niesie właściciela z serwera źródłowego, a baza odtwarzana bywa stawiana
    # pod innym kontem (np. w kontenerze testowym z backup_verify.sh). Bez tego pg_restore sypie
    # ostrzeżeniami o nieistniejącej roli przy każdym obiekcie.
    # `--exit-on-error`: cichy częściowy restore jest gorszy od braku restore'u – po nim baza
    # wygląda na kompletną i nikt nie sprawdza, czego brakuje.
    docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$TARGET_DB" --no-owner --exit-on-error < "$DUMP_PLAIN"
    docker compose exec -T db psql -U "$POSTGRES_USER" -d "$TARGET_DB" -Atc \
        "SELECT 'accounts_user=' || count(*) FROM accounts_user" </dev/null
fi

# --- 4. Pliki --------------------------------------------------------------------------------
if [ -z "$FILES_NAME" ] || [ ! -f "${BACKUP_DIR}/${FILES_NAME}" ]; then
    log "4/4 Brak paczki plików – kubełek nie jest odtwarzany."
else
    log "4/4 Rozszyfrowanie i wgranie plików do kubełka ${TARGET_BUCKET}"
    FILES_PLAIN="${WORK_DIR}/files.tar"
    decrypt "${BACKUP_DIR}/${FILES_NAME}" "$FILES_PLAIN"
    mkdir -p "${WORK_DIR}/buckets"
    tar -xf "$FILES_PLAIN" -C "${WORK_DIR}/buckets"
    log "    w paczce: $(find "${WORK_DIR}/buckets" -type f | wc -l) plików"
    NETWORK="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' \
        "$(docker compose ps -q minio)" | grep -E '_internal$' | head -1)"
    [ -n "$NETWORK" ] || die "usługa minio nie działa – nie ma dokąd wgrać plików"
    # Osobny kubełek, a nie `submissions`: dokładnie ten sam powód, co przy osobnej bazie.
    # Podmiana kubełka na produkcyjny jest krokiem ręcznym opisanym w runbooku.
    #
    # Polecenia **nie** puszczamy przez `run`, mimo że reszta skryptu tak robi: `MC_HOST_dst`
    # niesie hasło administratora MinIO, a `run` w trybie próbnym wypisuje swoje argumenty.
    # Wydruk trybu próbnego bywa wklejany do zgłoszenia albo zostaje w logu terminala.
    if [ "$DRY_RUN" = "1" ]; then
        printf '    [próba] mc mirror -> dst/%s (kubełek prywatny, %s plików)\n' \
            "$TARGET_BUCKET" "$(find "${WORK_DIR}/buckets/submissions" -type f 2>/dev/null | wc -l)"
    else
        docker run --rm --network "$NETWORK" \
            -v "${WORK_DIR}/buckets:/backup:ro" \
            -e MC_HOST_dst="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
            -e MC_QUIET=on -e MC_NO_COLOR=on \
            --entrypoint sh "$MC_IMAGE" -c "
                set -e
                mc mb --ignore-existing 'dst/${TARGET_BUCKET}'
                # Kubełek odtworzeniowy jest prywatny od pierwszej sekundy. Prace uczestników nie
                # mogą przez ani chwilę wisieć pod anonimowym odczytem, a domyślna polityka nowego
                # kubełka zależy od konfiguracji serwera – ustawiamy ją jawnie, zanim coś wgramy.
                mc anonymous set none 'dst/${TARGET_BUCKET}'
                mc mirror --overwrite /backup/submissions 'dst/${TARGET_BUCKET}' >/dev/null
            "
    fi
fi

if [ "$DRY_RUN" = "1" ]; then
    log "Próba zakończona – hasło pasuje, paczki dają się otworzyć. Nic nie zostało utworzone."
else
    log "Gotowe. Baza: ${TARGET_DB}, kubełek: ${TARGET_BUCKET}."
    log "Przełączenie serwisu na te dane jest osobną czynnością – docs/OPERACJE.md § Odtwarzanie."
fi
