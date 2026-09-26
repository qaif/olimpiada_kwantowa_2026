#!/usr/bin/env bash
# Odtworzenie kopii zapasowej: zrzut bazy do NOWEJ bazy, pliki do NOWEGO kubełka.
#
# Użycie (z katalogu /opt/olimpiada):
#   scripts/restore.sh --list                         # co jest do odtworzenia (lokalnie i zdalnie)
#   scripts/restore.sh --fetch db-20260117T030000Z.dump.gpg   # ściągnięcie paczki spoza serwera
#                                                     # (S3 albo Dysk Google) do katalogu kopii
#   scripts/restore.sh --dry-run                      # co by się stało z najnowszą kopią
#   scripts/restore.sh --dump db-20260117T030000Z.dump.gpg --files files-20260117T030000Z.tar.gpg
#   scripts/restore.sh --dump ... --db olimpiada_restore --bucket submissions-restore
#   scripts/restore.sh --djcms-dump djcms-db-20260117T030000Z.dump.gpg \
#                      --djcms-files djcms-files-20260117T030000Z.tar.gpg   # wersja porównawcza dj.
#
# Wersja porównawcza django CMS (dj., docs/OPERACJE.md § 22 i § 2.4) odtwarza się OSOBNYM
# przebiegiem (`--djcms-dump` nie łączy się z `--dump`/`--files`/`--bucket`): baza do NOWEJ bazy
# `olimpiada_djcms_restore_<stamp>` (albo `--db`), pliki redaktorów rozpakowane do NOWEGO katalogu
# `${BACKUP_DIR}/djcms-media-restore-<stamp>/` – ani bazy `olimpiada_djcms`, ani wolumenu
# `djcms_media` skrypt nie dotyka. Na końcu wypisuje polecenia podmiany. `--dry-run` jak niżej.
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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

BACKUP_DIR="${BACKUP_DIR:-/opt/olimpiada-backups}"
MC_IMAGE="${MC_IMAGE:-minio/mc:RELEASE.2025-04-16T18-13-26Z}"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.69}"

DRY_RUN=0
LIST_ONLY=0
FETCH_NAMES=()
DUMP_NAME=""
FILES_NAME=""
TARGET_DB=""
TARGET_BUCKET=""
DJCMS_DUMP_NAME=""
DJCMS_FILES_NAME=""

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
        --fetch) FETCH_NAMES+=("${2:?--fetch wymaga nazwy pliku}"); shift ;;
        --dump) DUMP_NAME="${2:?--dump wymaga nazwy pliku}"; shift ;;
        --files) FILES_NAME="${2:?--files wymaga nazwy pliku}"; shift ;;
        --db) TARGET_DB="${2:?--db wymaga nazwy bazy}"; shift ;;
        --bucket) TARGET_BUCKET="${2:?--bucket wymaga nazwy kubełka}"; shift ;;
        --djcms-dump) DJCMS_DUMP_NAME="${2:?--djcms-dump wymaga nazwy pliku}"; shift ;;
        --djcms-files) DJCMS_FILES_NAME="${2:?--djcms-files wymaga nazwy pliku}"; shift ;;
        -h|--help) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d'; exit 0 ;;
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

# Kopia poza serwerem (S3 albo Dysk Google) – ten sam kod, którym wysyła scripts/backup.sh.
# shellcheck source=lib/backup_offsite.sh
. "${SCRIPT_DIR}/lib/backup_offsite.sh"

if [ "$LIST_ONLY" = "1" ]; then
    log "Kopie lokalne (${BACKUP_DIR}):"
    ls -1sh "$BACKUP_DIR"/*.gpg 2>/dev/null || echo "    (brak)"
    if ! offsite_configure; then
        log "Kopii spoza serwera nie da się wylistować: ${OFFSITE_ERROR}"
    elif [ "$OFFSITE_TYPE" = "none" ]; then
        log "Kopia poza serwerem nie jest skonfigurowana – kopii zdalnych nie ma."
    else
        log "Kopie poza serwerem ($(offsite_describe)):"
        for prefix in daily monthly; do
            log "  ${prefix}/"
            offsite_rclone lsl "${OFFSITE_ROOT}/${prefix}/" || true
        done
    fi
    exit 0
fi

# Ściągnięcie paczek spoza serwera do katalogu kopii. Najpierw daily/, potem monthly/ – ta sama
# nazwa bywa w obu (kopia z pierwszego dnia miesiąca) i jest wtedy tym samym plikiem. Po
# ściągnięciu `rclone check` tej paczki (suma kontrolna): obcięty plik wychodzi inaczej dopiero
# błędem gpg w połowie odtwarzania, czyli w najgorszym możliwym momencie.
if [ "${#FETCH_NAMES[@]}" -gt 0 ]; then
    offsite_configure || die "konfiguracja kopii poza serwerem: ${OFFSITE_ERROR}"
    [ "$OFFSITE_TYPE" != "none" ] || die "kopia poza serwerem nie jest skonfigurowana – nie ma skąd ściągać"
    mkdir -p "$BACKUP_DIR"
    OFFSITE_DATA_MODE=rw
    for name in "${FETCH_NAMES[@]}"; do
        case "$name" in */*) die "--fetch przyjmuje samą nazwę pliku (bez katalogu): $name" ;; esac
        fetched=0
        for prefix in daily monthly; do
            listing="$(offsite_rclone lsf "${OFFSITE_ROOT}/${prefix}/" --include "/${name}" 2>/dev/null || true)"
            printf '%s\n' "$listing" | grep -qxF "$name" || continue
            log "Ściągam ${prefix}/${name} -> ${BACKUP_DIR}/"
            offsite_rclone copy "${OFFSITE_ROOT}/${prefix}/${name}" /data/ || die "ściąganie ${name} nie powiodło się"
            offsite_rclone check "${OFFSITE_ROOT}/${prefix}/" /data --one-way --include "/${name}" \
                || die "ściągnięty ${name} ma inną sumę kontrolną niż oryginał"
            chmod 600 "${BACKUP_DIR}/${name}" 2>/dev/null || true
            fetched=1
            break
        done
        [ "$fetched" = "1" ] || die "nie ma ${name} ani w daily/, ani w monthly/ (scripts/restore.sh --list)"
    done
    log "Gotowe. Dalej: scripts/restore.sh --dry-run --dump <plik db-…> [--files <plik files-…>]"
    exit 0
fi

decrypt() {
    printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --yes --quiet \
        --pinentry-mode loopback --passphrase-fd 0 --decrypt --output "$2" "$1"
}

# --- Wersja porównawcza dj. (osobny przebieg, docs/OPERACJE.md § 2.4) --------------------------
# Wszystko, co niżej robi przebieg bazy głównej, ma tu odpowiednik – z trzema różnicami:
#   1. właścicielem nowej bazy i jej obiektów jest rola `olimpiada_djcms` (`createdb -O`,
#      `pg_restore --role`), bo po podmianie łączy się nią djcms; przy samym `--no-owner` tabele
#      należałyby do superusera i djcms po podmianie nie przeczytałby żadnej,
#   2. pliki nie idą do MinIO, tylko do katalogu obok kopii – wolumen `djcms_media` podmienia
#      operator (polecenia na końcu), bo w działającym wolumenie siedzą pliki bieżące,
#   3. licznik po odtworzeniu to `cms_page` (strony), a nie konta.
# Przebieg bez `--djcms-*` omija ten blok w całości – co do polecenia ten sam, co przed DJ-01.
if [ -n "$DJCMS_DUMP_NAME" ] || [ -n "$DJCMS_FILES_NAME" ]; then
    [ -n "$DJCMS_DUMP_NAME" ] || die "--djcms-files wymaga --djcms-dump (pliki bez bazy, która je opisuje, są bezużyteczne)"
    [ -z "${DUMP_NAME}${FILES_NAME}${TARGET_BUCKET}" ] \
        || die "--djcms-dump odtwarza się osobnym przebiegiem – bez --dump/--files/--bucket"
    DJ_DB_ROLE=olimpiada_djcms
    DJ_DUMP_PATH="${BACKUP_DIR}/${DJCMS_DUMP_NAME}"
    [ -f "$DJ_DUMP_PATH" ] || die "brak pliku ${DJ_DUMP_PATH} (spoza serwera: scripts/restore.sh --fetch ${DJCMS_DUMP_NAME})"
    DJ_FILES_PATH=""
    if [ -n "$DJCMS_FILES_NAME" ]; then
        DJ_FILES_PATH="${BACKUP_DIR}/${DJCMS_FILES_NAME}"
        [ -f "$DJ_FILES_PATH" ] || die "brak pliku ${DJ_FILES_PATH} (spoza serwera: scripts/restore.sh --fetch ${DJCMS_FILES_NAME})"
    fi
    DJ_STAMP="$(printf '%s' "$DJCMS_DUMP_NAME" | sed -nE 's/^djcms-db-([0-9]{8}T[0-9]{6}Z)\.dump\.gpg$/\1/p')"
    [ -n "$DJ_STAMP" ] \
        || die "--djcms-dump: oczekiwana kopia nocna djcms-db-<stamp>.dump.gpg (scripts/backup.sh), jest: ${DJCMS_DUMP_NAME}"
    # Znacznik jak przy bazie głównej: bez „Z”, podkreślenia, małe litery.
    DJ_SLUG="$(printf '%s' "${DJ_STAMP%Z}" | tr 'T-' '__' | tr '[:upper:]' '[:lower:]')"
    TARGET_DB="${TARGET_DB:-olimpiada_djcms_restore_${DJ_SLUG}}"
    DJ_MEDIA_DIR="${BACKUP_DIR}/djcms-media-restore-${DJ_SLUG}"
    # Sprawdzone tutaj, zanim powstanie baza: odmowa po createdb zostawiałaby pół odtworzenia.
    if [ "$DRY_RUN" = "0" ] && [ -n "$DJ_FILES_PATH" ] && [ -e "$DJ_MEDIA_DIR" ]; then
        die "katalog ${DJ_MEDIA_DIR} już istnieje – przenieś go albo usuń (drugie odtworzenie tej samej nocy)"
    fi

    log "Kopia bazy dj.:   $DJ_DUMP_PATH"
    log "Kopia plików dj.: ${DJ_FILES_PATH:-(brak – odtwarzam samą bazę)}"
    log "Baza docelowa:    $TARGET_DB   (NOWA, właściciel ${DJ_DB_ROLE}; olimpiada_djcms nie jest ruszana)"
    [ -z "$DJ_FILES_PATH" ] || log "Katalog plików:   $DJ_MEDIA_DIR   (NOWY; wolumen djcms_media nie jest ruszany)"
    [ "$DRY_RUN" = "1" ] && log "TRYB PRÓBNY – nic nie zostanie utworzone ani zapisane."

    WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/olimpiada-restore-XXXXXX")"
    chmod 700 "$WORK_DIR"
    cleanup() { rm -rf "$WORK_DIR"; }
    trap cleanup EXIT

    # Rozszyfrowanie i nagłówek także w trybie próbnym – z tego samego powodu co niżej (złe hasło).
    log "1/4 Rozszyfrowanie paczki bazy dj."
    DJ_PLAIN="${WORK_DIR}/djcms-db.dump"
    decrypt "$DJ_DUMP_PATH" "$DJ_PLAIN"
    [ "$(head -c 5 "$DJ_PLAIN")" = "PGDMP" ] || die "po rozszyfrowaniu to nie jest zrzut Postgresa (brak nagłówka PGDMP)"
    log "    rozmiar po rozszyfrowaniu: $(du -h "$DJ_PLAIN" | cut -f1), nagłówek PGDMP"

    log "2/4 Tworzenie bazy ${TARGET_DB}"
    # Rola musi istnieć, zanim powstanie baza, której ma być właścicielem. Na nowym serwerze zakłada
    # ją wdrożenie z DJCMS_ENABLE=1 (scripts/djcms_db.sh) – odtwarzanie dj. idzie po nim. Sprawdzane
    # także w trybie próbnym: tryb próbny odpowiada na pytanie „czy to się w ogóle uda”.
    role="$(docker compose exec -T db psql -X -U "$POSTGRES_USER" -d postgres \
        -Atc "SELECT 1 FROM pg_roles WHERE rolname = '${DJ_DB_ROLE}'" </dev/null | tr -d '\r')" \
        || die "nie udało się zapytać klastra o rolę ${DJ_DB_ROLE} (czy usługa db działa?)"
    [ "$role" = "1" ] \
        || die "w klastrze nie ma roli ${DJ_DB_ROLE} – najpierw wdrożenie z DJCMS_ENABLE=1 albo scripts/djcms_db.sh (docs/OPERACJE.md § 22.2)"
    if [ "$DRY_RUN" = "0" ]; then
        docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres \
            -Atc "SELECT 1 FROM pg_database WHERE datname='${TARGET_DB}'" </dev/null | grep -q 1 \
            && die "baza ${TARGET_DB} już istnieje – podaj inną przez --db"
    fi
    run docker compose exec -T db createdb -U "$POSTGRES_USER" -O "$DJ_DB_ROLE" "$TARGET_DB"
    # Jak przy bazie głównej: CONNECT dla PUBLIC odbierany zaraz po createdb, przed pg_restore.
    # Właściciel (`olimpiada_djcms`) łączy się dalej – ma CONNECT jako właściciel, nie przez PUBLIC.
    run docker compose exec -T db psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
        -c "REVOKE CONNECT ON DATABASE \"${TARGET_DB//\"/\"\"}\" FROM PUBLIC" </dev/null

    log "3/4 pg_restore -> ${TARGET_DB}"
    if [ "$DRY_RUN" = "1" ]; then
        printf '    [próba] docker compose exec -T db pg_restore -U %s -d %s --no-owner --role=%s --exit-on-error\n' \
            "$POSTGRES_USER" "$TARGET_DB" "$DJ_DB_ROLE"
    else
        # `--role`: po połączeniu superuserem `SET ROLE olimpiada_djcms`, więc każdy odtworzony
        # obiekt należy do roli djcms. `--no-owner` pomija `ALTER … OWNER TO` ze zrzutu – kopia
        # z innego serwera mogłaby nieść inną nazwę właściciela.
        docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$TARGET_DB" --no-owner \
            --role="$DJ_DB_ROLE" --exit-on-error < "$DJ_PLAIN"
        docker compose exec -T db psql -U "$POSTGRES_USER" -d "$TARGET_DB" -Atc \
            "SELECT 'cms_page=' || count(*) FROM cms_page" </dev/null
    fi

    if [ -z "$DJ_FILES_PATH" ]; then
        log "4/4 Brak paczki plików dj. – katalog plików nie jest odtwarzany."
    else
        log "4/4 Rozszyfrowanie i rozpakowanie plików dj."
        DJ_FILES_PLAIN="${WORK_DIR}/djcms-files.tar"
        decrypt "$DJ_FILES_PATH" "$DJ_FILES_PLAIN"
        log "    w paczce: $(tar -tf "$DJ_FILES_PLAIN" | grep -vc '/$' || true) plików"
        # Katalog tylko dla roota (umask 077, a nie `mkdir -m`/`chmod` – ten sam wynik bez osobnej
        # zmiany uprawnień): rozpakowane pliki to także treść jeszcze nieopublikowana (szkice
        # redaktorów). `--no-overwrite-dir` zostawia te uprawnienia na katalogu głównym mimo wpisu
        # „./” w paczce; `--numeric-owner` zachowuje uid 1000 (`app` obrazu djcms) niezależnie od
        # tego, jak ten numer nazywa się na hoście – po skopiowaniu do wolumenu djcms je nadpisze.
        if [ "$DRY_RUN" = "1" ]; then
            printf '    [próba] (umask 077 && mkdir %s)\n' "$DJ_MEDIA_DIR"
        else
            (umask 077 && mkdir "$DJ_MEDIA_DIR")
        fi
        run tar --numeric-owner --no-overwrite-dir -xf "$DJ_FILES_PLAIN" -C "$DJ_MEDIA_DIR"
    fi

    if [ "$DRY_RUN" = "1" ]; then
        log "Próba zakończona – hasło pasuje, paczki dają się otworzyć. Nic nie zostało utworzone."
        exit 0
    fi
    log "Gotowe. Baza: ${TARGET_DB}${DJ_FILES_PATH:+, pliki: ${DJ_MEDIA_DIR}}. dj. działa dalej na danych bieżących."
    log "Podmiana – dopiero po sprawdzeniu odtworzonych danych (docs/OPERACJE.md § 2.4):"
    printf '    docker compose stop djcms\n'
    printf "    docker compose exec -T db psql -U %s -d postgres -c 'ALTER DATABASE olimpiada_djcms RENAME TO olimpiada_djcms_przed_awaria' -c 'ALTER DATABASE \"%s\" RENAME TO olimpiada_djcms'\n" \
        "$POSTGRES_USER" "$TARGET_DB"
    if [ -n "$DJ_FILES_PATH" ]; then
        # Pełna nazwa wolumenu (z prefiksem projektu) z `docker compose config` – ten sam compose,
        # te same COMPOSE_FILE/COMPOSE_PROFILES z .env. Gdy się nie uda (np. profil djcms już
        # wyłączony), nazwa po regule compose'a: nazwa projektu małymi literami, bez znaków spoza
        # [a-z0-9_-] (katalog „olimpiada clade” daje „olimpiadaclade”).
        DJ_VOLUME="$(docker compose config 2>/dev/null </dev/null | sed -n '/^volumes:/,$p' \
            | sed -n '/^  djcms_media:$/{n;s/^ *name: *//p;}' | head -1 || true)"
        [ -n "$DJ_VOLUME" ] || DJ_VOLUME="$(printf '%s' "${COMPOSE_PROJECT_NAME:-$(basename "$REPO_DIR")}" \
            | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')_djcms_media"
        # Obraz Postgresa, bo na serwerze na pewno jest (usługa `db`) i ma `sh`, `cp`, `find`, `chown`.
        # Bieżąca zawartość wolumenu idzie najpierw do katalogu `…-przed` obok kopii, nie do kosza;
        # `chown` na końcu, bo `cp -a` przenosi na wolumen także właściciela katalogu źródłowego.
        printf "    docker run --rm -v %s:/m -v %s:/src:ro -v %s-przed:/old %s sh -c 'cp -a /m/. /old/ && find /m -mindepth 1 -delete && cp -a /src/. /m/ && chown -R 1000:1000 /m'\n" \
            "$DJ_VOLUME" "$DJ_MEDIA_DIR" "$DJ_MEDIA_DIR" "${POSTGRES_IMAGE:-postgres:18-alpine}"
    fi
    printf '    docker compose up -d djcms\n'
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
# Nowa baza ma domyślne uprawnienia, czyli CONNECT dla PUBLIC – każda rola z LOGIN w tym klastrze
# (także `olimpiada_djcms` wersji porównawczej, docs/tasks/DJ-01.md § 8.9) mogłaby się połączyć
# z pełną kopią danych uczestników. Odbieramy go od razu, **przed** pg_restore. Konto aplikacji
# (`POSTGRES_USER`) jest superuserem i właścicielem tej bazy, więc je to nie dotyczy.
# Identyfikator w cudzysłowie z podwojonym `"` – nazwa z `--db` bywa dowolna.
run docker compose exec -T db psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
    -c "REVOKE CONNECT ON DATABASE \"${TARGET_DB//\"/\"\"}\" FROM PUBLIC" </dev/null

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
