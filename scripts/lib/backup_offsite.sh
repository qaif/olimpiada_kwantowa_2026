# shellcheck shell=bash
# Kopia poza serwerem: wybór miejsca (S3 albo Dysk Google), wywołanie rclone i token Dysku.
#
# Biblioteka – nie uruchamia się jej wprost. Wczytują ją `scripts/backup.sh` (wysyłka, retencja,
# `--offsite-test`, `--drive-token`) i `scripts/restore.sh` (`--list`, `--fetch`), dzięki czemu
# „dokąd wysyłamy” i „skąd ściągamy” są jednym kodem, a nie dwiema kopiami, które rozjadą się przy
# pierwszej zmianie. Oczekuje wczytanego `.env`, ustawionych REPO_DIR i BACKUP_DIR oraz funkcji
# `log`. Żadna funkcja tu nie woła `exit`: błąd wraca kodem i opisem w OFFSITE_ERROR, bo nocna
# kopia po nieudanej wysyłce ma jeszcze coś do zrobienia (retencja lokalna, meldunek o awarii).
#
# Dwa rodzaje miejsca docelowego (BACKUP_REMOTE_TYPE):
#   s3     kubełek zgodny z S3 u innego dostawcy (BACKUP_REMOTE_URL, BACKUP_BUCKET, klucze),
#   drive  Dysk Google konta Fundacji (token OAuth z `rclone authorize`), docs/OPERACJE.md § 1.6,
#   none   brak kopii poza serwerem (jawne wyłączenie).
# Bez BACKUP_REMOTE_TYPE wybór jest automatyczny: s3, gdy jest BACKUP_REMOTE_URL; drive, gdy jest
# BACKUP_DRIVE_TOKEN albo zapisany wcześniej token w katalogu sekretów; w pozostałych przypadkach none.

RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.69}"
# Retencja zdalna w dniach. Domyślne wartości powtórzone tutaj (a nie tylko w backup.sh), bo pusta
# linia `REMOTE_DAILY_KEEP_DAYS=` w .env nadpisałaby je po wczytaniu pliku.
REMOTE_DAILY_KEEP_DAYS="${REMOTE_DAILY_KEEP_DAYS:-30}"
REMOTE_MONTHLY_KEEP_DAYS="${REMOTE_MONTHLY_KEEP_DAYS:-365}"

# Katalog sekretów trzymanych POZA .env. Jedyny mieszkaniec to plik konfiguracji rclone dla Dysku
# (niżej: dlaczego to wyjątek od zasady „sekrety wyłącznie w .env”). W katalogu repozytorium, ale
# krok 2/8 wdrożenia go omija (`! -name secrets`) – tak jak omija `.env`.
BACKUP_SECRETS_DIR="${BACKUP_SECRETS_DIR:-${REPO_DIR}/secrets}"
DRIVE_CONF_DIR="${BACKUP_SECRETS_DIR}/rclone"
DRIVE_CONF="${DRIVE_CONF_DIR}/rclone.conf"

OFFSITE_TYPE=""
OFFSITE_ROOT=""
OFFSITE_ERROR=""
# ro: nocna kopia tylko czyta katalog kopii; rw: `restore.sh --fetch` ściąga do niego paczkę.
OFFSITE_DATA_MODE="${OFFSITE_DATA_MODE:-ro}"

offsite_fail() { OFFSITE_ERROR="$*"; printf 'BŁĄD (kopia poza serwerem): %s\n' "$*" >&2; return 1; }

# --- Token Dysku Google ----------------------------------------------------------------------
# Dlaczego token leży w pliku, a nie tylko w zmiennej środowiskowej (wyjątek od reguły „rclone
# wyłącznie ze zmiennych”, którą trzyma wariant S3):
#
# Token z `rclone authorize` to para: krótki `access_token` (godzina) i długi `refresh_token`.
# Przy każdym przebiegu rclone wymienia refresh_token na nowy access_token i ZAPISUJE wynik do
# pliku konfiguracji. Zmiennej środowiskowej zapisać nie umie – z samym .env każda noc zaczynałaby
# od tokenu przeterminowanego, a gdyby Google kiedyś zwrócił nowy refresh_token (wolno mu), stary
# z .env przestałby działać po cichu i kopia poza serwer skończyłaby się bez śladu w .env.
# Co gorsza, zmienna RCLONE_CONFIG_OFFSITE_TOKEN ma w rclone PIERWSZEŃSTWO przed plikiem, więc
# token z .env przykrywałby odświeżony – dlatego do kontenera idzie wyłącznie plik.
#
# Plik: ${BACKUP_SECRETS_DIR}/rclone/rclone.conf, katalog 700, plik 600, właściciel root. Montowany
# jest KATALOG, nie plik: rclone zapisuje konfigurację przez plik tymczasowy i `rename`, a podmiana
# pliku zamontowanego pojedynczo kończy się „device or resource busy”.
#
# Skąd plik się bierze: `scripts/backup.sh --drive-token` (wklejenie tokenu – droga dla organizatora,
# bez edycji .env i bez pułapek z cudzysłowami) albo BACKUP_DRIVE_TOKEN w .env (droga dla
# administratora). Z .env plik jest zakładany przy pierwszym przebiegu i nadpisywany tylko wtedy,
# gdy w .env pojawi się token z INNYM refresh_token (ktoś autoryzował dostęp od nowa) – inaczej
# odświeżony przez rclone token w pliku zostaje nietknięty.

# refresh_token z tokenu JSON (bez jq – nie ma go w minimalnym Ubuntu). Pusty wynik = brak.
drive_refresh_token_of() {
    printf '%s' "$1" | grep -o '"refresh_token"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 \
        | sed 's/^.*:[[:space:]]*"\(.*\)"$/\1/' || true
}

# Czy napis wygląda na token z `rclone authorize`. Najczęstszy błąd ręcznego wpisu w .env to
# brak apostrofów: powłoka zjada wtedy cudzysłowy JSON-a i zostaje `{access_token:ya29...}`.
drive_token_check() {
    local token="$1"
    case "$token" in
        \{*\}) ;;
        *) offsite_fail "token Dysku nie jest obiektem JSON {…} – skopiuj całą linię między ---> a <--- z wyniku rclone authorize"; return 1 ;;
    esac
    if [ -z "$(drive_refresh_token_of "$token")" ]; then
        case "$token" in
            *refresh_token:*) offsite_fail "token Dysku stracił cudzysłowy – w .env wpisz go w apostrofach: BACKUP_DRIVE_TOKEN='{\"access_token\":…}'" ;;
            *) offsite_fail "token Dysku nie zawiera refresh_token – to nie jest wynik rclone authorize" ;;
        esac
        return 1
    fi
}

# Zapis pliku konfiguracji z tokenem. Atomowo (plik tymczasowy + mv) i z uprawnieniami ustawionymi
# PRZED wpisaniem treści – przez żadną chwilę token nie leży w pliku czytelnym dla innych.
drive_conf_write() {
    local token="$1" tmp
    ( umask 077 && mkdir -p "$DRIVE_CONF_DIR" ) || { offsite_fail "nie mogę utworzyć ${DRIVE_CONF_DIR}"; return 1; }
    chmod 700 "$BACKUP_SECRETS_DIR" "$DRIVE_CONF_DIR" 2>/dev/null || true
    tmp="$(umask 077 && mktemp "${DRIVE_CONF_DIR}/.rclone.conf.XXXXXX")" || { offsite_fail "nie mogę zapisać w ${DRIVE_CONF_DIR}"; return 1; }
    # `type` i `scope` w pliku wyłącznie dla czytelności i ręcznego użycia rclone z tym katalogiem;
    # w przebiegu skryptu i tak rozstrzygają zmienne środowiskowe (mają pierwszeństwo przed plikiem).
    printf '[offsite]\ntype = drive\nscope = %s\ntoken = %s\n' "${BACKUP_DRIVE_SCOPE:-drive.file}" "$token" > "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$DRIVE_CONF"
}

drive_conf_token() { sed -n 's/^token[[:space:]]*=[[:space:]]*//p' "$DRIVE_CONF" 2>/dev/null | head -1 || true; }

# Przygotowanie pliku przed pierwszym wywołaniem rclone w tym przebiegu.
drive_conf_prepare() {
    if [ -n "${BACKUP_DRIVE_TOKEN:-}" ]; then
        drive_token_check "$BACKUP_DRIVE_TOKEN" || return 1
        local wanted current
        wanted="$(drive_refresh_token_of "$BACKUP_DRIVE_TOKEN")"
        current="$(drive_refresh_token_of "$(drive_conf_token)")"
        if [ ! -f "$DRIVE_CONF" ]; then
            log "    Zapisuję token Dysku z .env do ${DRIVE_CONF}"
            drive_conf_write "$BACKUP_DRIVE_TOKEN" || return 1
        elif [ "$wanted" != "$current" ]; then
            log "    W .env jest nowy token Dysku (inny refresh_token) – zastępuję zapisany"
            drive_conf_write "$BACKUP_DRIVE_TOKEN" || return 1
        fi
    fi
    [ -f "$DRIVE_CONF" ] || { offsite_fail "brak tokenu Dysku Google: uruchom scripts/backup.sh --drive-token (docs/OPERACJE.md § 1.6)"; return 1; }
    [ -n "$(drive_refresh_token_of "$(drive_conf_token)")" ] \
        || { offsite_fail "plik ${DRIVE_CONF} nie zawiera tokenu z refresh_token – wklej token ponownie: scripts/backup.sh --drive-token"; return 1; }
    # Uprawnienia pilnowane przy każdym przebiegu, nie tylko przy zakładaniu: `cp -r` albo ręczna
    # edycja potrafią je poluzować, a w pliku jest klucz do kopii zapasowych.
    chmod 700 "$BACKUP_SECRETS_DIR" "$DRIVE_CONF_DIR" 2>/dev/null || true
    chmod 600 "$DRIVE_CONF" 2>/dev/null || true
}

# --- Wybór miejsca docelowego ----------------------------------------------------------------
is_positive_int() { case "$1" in ''|*[!0-9]*) return 1 ;; *) [ "$1" -ge 1 ] ;; esac; }

offsite_configure() {
    OFFSITE_ERROR=""
    local type="${BACKUP_REMOTE_TYPE:-}"
    if [ -z "$type" ]; then
        if [ -n "${BACKUP_REMOTE_URL:-}" ]; then
            type=s3
            if [ -n "${BACKUP_DRIVE_TOKEN:-}" ] || [ -f "$DRIVE_CONF" ]; then
                log "    UWAGA: jest i BACKUP_REMOTE_URL, i token Dysku – wybieram s3. Rozstrzygnij: BACKUP_REMOTE_TYPE=s3|drive"
            fi
        elif [ -n "${BACKUP_DRIVE_TOKEN:-}" ] || [ -f "$DRIVE_CONF" ]; then
            type=drive
        else
            type=none
        fi
    fi

    # Retencja sprawdzana przed czymkolwiek, co kasuje: `--min-age 0d` albo pusta wartość
    # skasowałyby po stronie zdalnej wszystko, łącznie z kopią z tej nocy.
    is_positive_int "${REMOTE_DAILY_KEEP_DAYS:-}" \
        || { offsite_fail "REMOTE_DAILY_KEEP_DAYS musi być liczbą dni >= 1 (jest: '${REMOTE_DAILY_KEEP_DAYS:-}')"; return 1; }
    is_positive_int "${REMOTE_MONTHLY_KEEP_DAYS:-}" \
        || { offsite_fail "REMOTE_MONTHLY_KEEP_DAYS musi być liczbą dni >= 1 (jest: '${REMOTE_MONTHLY_KEEP_DAYS:-}')"; return 1; }

    case "$type" in
        none)
            OFFSITE_TYPE=none; OFFSITE_ROOT="" ;;
        s3)
            [ -n "${BACKUP_REMOTE_URL:-}" ] || { offsite_fail "BACKUP_REMOTE_TYPE=s3 wymaga BACKUP_REMOTE_URL"; return 1; }
            [ -n "${BACKUP_ACCESS_KEY:-}" ] || { offsite_fail "BACKUP_ACCESS_KEY wymagane razem z BACKUP_REMOTE_URL"; return 1; }
            [ -n "${BACKUP_SECRET_KEY:-}" ] || { offsite_fail "BACKUP_SECRET_KEY wymagane razem z BACKUP_REMOTE_URL"; return 1; }
            [ -n "${BACKUP_BUCKET:-}" ] || { offsite_fail "BACKUP_BUCKET wymagane razem z BACKUP_REMOTE_URL"; return 1; }
            OFFSITE_TYPE=s3; OFFSITE_ROOT="offsite:${BACKUP_BUCKET}" ;;
        drive)
            # Folder na Dysku, liczony od „Mój dysk” (albo od dysku współdzielonego). Zakłada go
            # rclone przy pierwszej wysyłce – przy zakresie drive.file MUSI go założyć rclone, bo
            # folderu utworzonego ręcznie w przeglądarce rclone po prostu nie widzi (docs § 1.6).
            local folder="${BACKUP_DRIVE_FOLDER:-Olimpiada-kopie-zapasowe}"
            folder="${folder#/}"; folder="${folder%/}"
            [ -n "$folder" ] || { offsite_fail "BACKUP_DRIVE_FOLDER nie może być pusty – kopie leżałyby luzem w katalogu głównym Dysku"; return 1; }
            drive_conf_prepare || return 1
            OFFSITE_TYPE=drive; OFFSITE_ROOT="offsite:${folder}" ;;
        *)
            offsite_fail "BACKUP_REMOTE_TYPE='${type}' – dozwolone: s3, drive, none"; return 1 ;;
    esac
}

offsite_describe() {
    case "$OFFSITE_TYPE" in
        s3) printf 'kubełek S3 %s (%s)' "${BACKUP_BUCKET}" "${BACKUP_REMOTE_URL}" ;;
        drive)
            if [ -n "${BACKUP_DRIVE_TEAM_DRIVE:-}" ]; then
                printf 'Dysk Google, dysk współdzielony %s, folder „%s”' "$BACKUP_DRIVE_TEAM_DRIVE" "${OFFSITE_ROOT#offsite:}"
            else
                printf 'Dysk Google, Mój dysk, folder „%s”' "${OFFSITE_ROOT#offsite:}"
            fi ;;
        *) printf 'brak' ;;
    esac
}

# --- rclone w kontenerze ---------------------------------------------------------------------
# `offsite_rclone [-i] <polecenie rclone> …` – `-i` podłącza stdin (dla `rcat`).
# Wariant S3 jak dotąd: konfiguracja wyłącznie zmiennymi (RCLONE_CONFIG_OFFSITE_*), bez pliku.
# Wariant Dysku: zmienne dla wszystkiego poza tokenem, token z zamontowanego katalogu sekretów.
offsite_rclone() {
    local stdin_args=()
    if [ "${1:-}" = "-i" ]; then stdin_args=(-i); shift; fi
    local args=(run --rm "${stdin_args[@]}" -v "${BACKUP_DIR}:/data:${OFFSITE_DATA_MODE}")
    case "$OFFSITE_TYPE" in
        s3)
            args+=(
                -e RCLONE_CONFIG_OFFSITE_TYPE=s3
                -e RCLONE_CONFIG_OFFSITE_PROVIDER="${BACKUP_REMOTE_PROVIDER:-Other}"
                -e RCLONE_CONFIG_OFFSITE_ENDPOINT="$BACKUP_REMOTE_URL"
                -e RCLONE_CONFIG_OFFSITE_REGION="${BACKUP_REMOTE_REGION:-}"
                -e RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID="$BACKUP_ACCESS_KEY"
                -e RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY="$BACKUP_SECRET_KEY"
                -e RCLONE_CONFIG_OFFSITE_ACL=private
                -e RCLONE_CONFIG_OFFSITE_NO_CHECK_BUCKET=true
            ) ;;
        drive)
            args+=(
                -v "${DRIVE_CONF_DIR}:/config/rclone"
                -e RCLONE_CONFIG=/config/rclone/rclone.conf
                -e RCLONE_CONFIG_OFFSITE_TYPE=drive
                # drive.file: rclone widzi i może kasować WYŁĄCZNIE pliki, które sam założył.
                # Token wycieknięty z tego serwera nie otwiera reszty Dysku Fundacji.
                -e RCLONE_CONFIG_OFFSITE_SCOPE="${BACKUP_DRIVE_SCOPE:-drive.file}"
                # Retencja kasuje NA STAŁE, z pominięciem kosza (uzasadnienie: docs/OPERACJE.md
                # § 1.6 „Retencja i kosz”). BACKUP_DRIVE_USE_TRASH=true przywraca kosz.
                -e RCLONE_CONFIG_OFFSITE_USE_TRASH="${BACKUP_DRIVE_USE_TRASH:-false}"
            )
            # Własny klient OAuth (opcjonalny). Przekazywany tylko, gdy jest – pusta zmienna też
            # jest dla rclone „ustawiona”, a token jest związany z klientem, którym go wydano.
            [ -n "${BACKUP_DRIVE_CLIENT_ID:-}" ] && args+=(-e RCLONE_CONFIG_OFFSITE_CLIENT_ID="$BACKUP_DRIVE_CLIENT_ID")
            [ -n "${BACKUP_DRIVE_CLIENT_SECRET:-}" ] && args+=(-e RCLONE_CONFIG_OFFSITE_CLIENT_SECRET="$BACKUP_DRIVE_CLIENT_SECRET")
            [ -n "${BACKUP_DRIVE_TEAM_DRIVE:-}" ] && args+=(-e RCLONE_CONFIG_OFFSITE_TEAM_DRIVE="$BACKUP_DRIVE_TEAM_DRIVE")
            ;;
        *) offsite_fail "offsite_rclone bez skonfigurowanego miejsca docelowego"; return 1 ;;
    esac
    docker "${args[@]}" "$RCLONE_IMAGE" "$@"
}

# --- Wysyłka z weryfikacją -------------------------------------------------------------------
# `offsite_upload <prefiks> <plik> …` – pliki z BACKUP_DIR (same nazwy) do <root>/<prefiks>/.
# Po wysyłce `rclone check --one-way`: porównuje rozmiar i sumę kontrolną (Dysk: MD5 liczone przez
# Google po stronie serwera; S3: ETag = MD5 dla wysyłki jednoczęściowej). „rclone copy zwrócił 0”
# to deklaracja nadawcy; zgodna suma po drugiej stronie to potwierdzenie odbiorcy.
offsite_upload() {
    local prefix="$1"; shift
    local name includes=()
    for name in "$@"; do
        offsite_rclone copy "/data/${name}" "${OFFSITE_ROOT}/${prefix}/" \
            || { offsite_fail "wysyłka ${name} do ${prefix}/ nie powiodła się"; return 1; }
        includes+=(--include "/${name}")
    done
    offsite_rclone check /data "${OFFSITE_ROOT}/${prefix}/" --one-way "${includes[@]}" \
        || { offsite_fail "weryfikacja ${prefix}/ po wysyłce: rozmiar lub suma kontrolna się nie zgadza"; return 1; }
}

offsite_retention() {
    offsite_rclone delete "${OFFSITE_ROOT}/daily/" --min-age "${REMOTE_DAILY_KEEP_DAYS}d" \
        || { offsite_fail "retencja daily/ nie powiodła się"; return 1; }
    offsite_rclone delete "${OFFSITE_ROOT}/monthly/" --min-age "${REMOTE_MONTHLY_KEEP_DAYS}d" \
        || { offsite_fail "retencja monthly/ nie powiodła się"; return 1; }
}

# --- Test połączenia bez czekania na noc -----------------------------------------------------
# Plik próbny: zapis (rcat), lista, odczyt z porównaniem treści, skasowanie. Cztery operacje, bo
# każda z nich może się nie udać osobno (token bez prawa zapisu, zły folder, zły klient OAuth).
offsite_selftest() {
    local stamp="$1" name content got listing
    name="offsite-test-${stamp}.txt"
    content="Olimpiada: test kopii poza serwerem ${stamp}"
    log "    zapis   ${OFFSITE_ROOT}/test/${name}"
    printf '%s\n' "$content" | offsite_rclone -i rcat "${OFFSITE_ROOT}/test/${name}" \
        || { offsite_fail "zapis pliku próbnego nie powiódł się (token? uprawnienia? folder?)"; return 1; }
    log "    lista"
    listing="$(offsite_rclone lsf "${OFFSITE_ROOT}/test/")" \
        || { offsite_fail "listowanie folderu test/ nie powiodło się"; return 1; }
    printf '%s\n' "$listing" | grep -qxF "$name" \
        || { offsite_fail "pliku próbnego nie widać na liście po zapisie"; return 1; }
    log "    odczyt"
    got="$(offsite_rclone cat "${OFFSITE_ROOT}/test/${name}")" \
        || { offsite_fail "odczyt pliku próbnego nie powiódł się"; return 1; }
    [ "$got" = "$content" ] || { offsite_fail "odczytana treść pliku próbnego różni się od zapisanej"; return 1; }
    log "    kasowanie"
    offsite_rclone deletefile "${OFFSITE_ROOT}/test/${name}" \
        || { offsite_fail "skasowanie pliku próbnego nie powiodło się"; return 1; }
    # Pusty folder test/ sprzątamy, ale jego pozostawienie niczego nie psuje.
    offsite_rclone rmdir "${OFFSITE_ROOT}/test" >/dev/null 2>&1 || true
}
