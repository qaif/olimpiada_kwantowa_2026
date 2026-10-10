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
# Użycie:
#   scripts/backup.sh                  nocna kopia (cron): zrzut, szyfrowanie, wysyłka, retencja
#   scripts/backup.sh --offsite-test   tylko sprawdzenie miejsca poza serwerem: plik próbny
#                                      zapisany, wylistowany, odczytany i skasowany (bez zrzutu)
#   scripts/backup.sh --drive-token    wklejenie tokenu Dysku Google z `rclone authorize`
#                                      (docs/OPERACJE.md § 1.6) – zapis do secrets/rclone/
#
# Wymagane w .env (poza tym, co już tam jest na potrzeby compose):
#   BACKUP_PASSPHRASE    hasło do symetrycznego szyfrowania paczek (gpg AES-256)
# Kopia poza serwerem – jedno z dwóch miejsc (BACKUP_REMOTE_TYPE=s3|drive|none; bez tej zmiennej
# wybór automatyczny, szczegóły w scripts/lib/backup_offsite.sh). Bez żadnego skrypt robi wyłącznie
# kopię lokalną i mówi o tym na stdout.
#   S3:  BACKUP_REMOTE_URL (endpoint, np. https://s3.eu-central-003.backblazeb2.com),
#        BACKUP_ACCESS_KEY / BACKUP_SECRET_KEY, BACKUP_BUCKET, opcjonalnie BACKUP_REMOTE_REGION
#   Dysk Google: token z `scripts/backup.sh --drive-token` (albo BACKUP_DRIVE_TOKEN w .env),
#        opcjonalnie BACKUP_DRIVE_FOLDER, BACKUP_DRIVE_CLIENT_ID / _CLIENT_SECRET,
#        BACKUP_DRIVE_TEAM_DRIVE, BACKUP_DRIVE_USE_TRASH
#
# Retencja: 30 dni kopii dziennych i 365 dni miesięcznych po stronie zdalnej (REMOTE_DAILY_KEEP_DAYS,
# REMOTE_MONTHLY_KEEP_DAYS), 7 dni lokalnie (LOCAL_KEEP_DAYS).
#
# Wersja porównawcza django CMS (dj.<domena>, docs/OPERACJE.md § 22): przy DJCMS_ENABLED=1 w .env
# i istniejącej bazie `olimpiada_djcms` dochodzą dwie paczki – `djcms-db-<stamp>.dump.gpg` (podkrok
# 1b) i `djcms-files-<stamp>.tar.gpg` z wolumenu `djcms_media` (podkrok 2b) – wysyłane i sprzątane
# razem z pozostałymi. Bez przełącznika skrypt nie wykonuje żadnego polecenia djcms
# (scripts/tests/backup_offsite_test.sh, przypadek 12).
#
# Nieudana wysyłka poza serwer to nieudana kopia: kod wyjścia 1 i meldunek `--failed` (watchdog
# alarmuje brakiem świeżej kopii). Kopia lokalna z tej nocy zostaje – jest jedyną, jaka powstała.
set -euo pipefail

MODE=backup
case "${1:-}" in
    "") ;;
    --offsite-test) MODE=offsite-test ;;
    --drive-token) MODE=drive-token ;;
    -h|--help) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d'; exit 0 ;;
    *) printf 'BŁĄD: nieznany argument: %s (scripts/backup.sh --help)\n' "$1" >&2; exit 2 ;;
esac

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
# .env czytany jako tekst (scripts/lib/env.sh), a nie wykonywany `. ./.env` (do 10.10.2026): wartość
# ze spacją bez cudzysłowu – a tak wpisuje ją .env.example – kończyła kopię kodem 127, a `$(…)`
# w wartości było poleceniem uruchamianym przez roota z crona (audyt bezpieczeństwa, S17).
# shellcheck source=lib/env.sh
. "${SCRIPT_DIR}/lib/env.sh"
env_load .env

# shellcheck source=lib/backup_offsite.sh
. "${SCRIPT_DIR}/lib/backup_offsite.sh"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# --- Tryb: wklejenie tokenu Dysku Google -----------------------------------------------------
# Token przychodzi przez stdin (wklejenie w terminalu albo potok), a nie w argumencie: argumenty
# procesu widzi każdy `ps` na maszynie i zapisuje historia powłoki.
if [ "$MODE" = "drive-token" ]; then
    if [ -t 0 ]; then
        printf 'Wklej token z „rclone authorize” (jedna linia zaczynająca się od {) i naciśnij Enter:\n'
    fi
    IFS= read -r TOKEN || true
    # Końcowe spacje i CR (wklejenie z Windowsa) nie są częścią tokenu.
    TOKEN="$(printf '%s' "$TOKEN" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    drive_token_check "$TOKEN" || exit 1
    # Token w .env z INNYM refresh_token nadpisałby wklejony przy najbliższym przebiegu (patrz
    # drive_conf_prepare). Lepiej powiedzieć to teraz niż odkryć jutro rano.
    if [ -n "${BACKUP_DRIVE_TOKEN:-}" ] \
        && [ "$(drive_refresh_token_of "$BACKUP_DRIVE_TOKEN")" != "$(drive_refresh_token_of "$TOKEN")" ]; then
        die "w ${REPO_DIR}/.env jest inny BACKUP_DRIVE_TOKEN – usuń tę linię z .env i wklej token ponownie"
    fi
    drive_conf_write "$TOKEN" || exit 1
    log "Zapisano token Dysku Google: ${DRIVE_CONF} (tylko root, uprawnienia 600)."
    if [ -n "${BACKUP_REMOTE_URL:-}" ] && [ "${BACKUP_REMOTE_TYPE:-}" != "drive" ]; then
        log "UWAGA: w .env jest też BACKUP_REMOTE_URL (S3) – żeby kopie szły na Dysk, dopisz do .env BACKUP_REMOTE_TYPE=drive"
    fi
    log "Teraz sprawdź połączenie: scripts/backup.sh --offsite-test"
    exit 0
fi

# --- Tryb: test miejsca poza serwerem --------------------------------------------------------
if [ "$MODE" = "offsite-test" ]; then
    command -v docker >/dev/null 2>&1 || die "brak dockera na hoście"
    offsite_configure || exit 1
    [ "$OFFSITE_TYPE" != "none" ] \
        || die "kopia poza serwerem nie jest skonfigurowana (brak BACKUP_REMOTE_URL i tokenu Dysku) – docs/OPERACJE.md § 1.3 / § 1.6"
    log "Test kopii poza serwerem: $(offsite_describe)"
    offsite_selftest "$STAMP" || exit 1
    # Stan bez zmian – tylko informacja, ile kopii już tam leży (przy pierwszym teście: zero).
    for prefix in daily monthly; do
        count="$(offsite_rclone lsf "${OFFSITE_ROOT}/${prefix}/" 2>/dev/null | grep -c . || true)"
        log "    ${prefix}/: ${count:-0} plików"
    done
    log "Test udany: zapis, lista, odczyt i kasowanie działają."
    exit 0
fi

: "${POSTGRES_USER:?POSTGRES_USER musi być w .env}"
: "${POSTGRES_DB:?POSTGRES_DB musi być w .env}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE musi być w .env – bez hasła nie szyfrujemy, a bez szyfrowania nie wysyłamy}"

command -v gpg >/dev/null 2>&1 || die "brak gpg na hoście (apt-get install -y gnupg)"
command -v docker >/dev/null 2>&1 || die "brak dockera na hoście"

DAY_OF_MONTH="$(date -u +%d)"

# Konfiguracja miejsca poza serwerem sprawdzana PRZED zrzutem, ale jej błąd zrzutu nie zatrzymuje:
# kopia lokalna ma powstać zawsze, a zła konfiguracja wysyłki wychodzi w kroku 3 jako awaria.
OFFSITE_STATUS=pending
if ! offsite_configure; then
    OFFSITE_STATUS=failed
fi
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

# --- 1b. Baza wersji porównawczej django CMS (tylko przy DJCMS_ENABLED=1) ----------------------
# Przełącznik czytany tak samo jak w scripts/deploy.sh (1/true/yes/on, bez względu na wielkość
# liter). Wyłączony = ani jednego polecenia więcej niż przed DJ-01 – także zapytania „czy baza
# istnieje”: przebieg kopii instalacji bez dj. ma być co do polecenia dzisiejszy.
#
# Świadomie przełącznik, a nie samo istnienie bazy (DJ-01 § 8.11 mówił o samym istnieniu): po
# wyłączeniu dj. (§ 22.6) baza i wolumen zostają „na przechowanie” do decyzji o usunięciu, a ich
# ostatnia kopia to ta sprzed wyłączenia (30 dni w daily/, 365 w monthly/). Kopiowanie co noc danych
# serwisu, którego nikt już nie redaguje, dawałoby wyłącznie identyczne paczki.
#
# Błąd któregokolwiek kroku djcms NIE przerywa kopii głównej: jej wysyłka poza serwer jest ważniejsza
# od wersji porównawczej. Zapisujemy go w DJCMS_ERROR, a na końcu przebieg melduje `--failed`
# i kończy się kodem 1 – kopia, która miała objąć dj., a nie objęła, nie jest kopią „ok”.
DJCMS_ON=0
case "$(printf '%s' "${DJCMS_ENABLED:-}" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) DJCMS_ON=1 ;; esac
DJCMS_DB=olimpiada_djcms
DJCMS_ERROR=""
DJCMS_DUMP_FILE=""
DJCMS_FILES_FILE=""
if [ "$DJCMS_ON" = "1" ]; then
    # Zapytanie w warunku `if`: jego błąd (baza przed chwilą oddała zrzut, więc to rzadkość) nie
    # przerywa skryptu przez `set -e`, tylko zamienia się niżej w DJCMS_ERROR.
    if ! djcms_exists="$(docker compose exec -T db psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
        "SELECT 1 FROM pg_database WHERE datname = '${DJCMS_DB}'" </dev/null)"; then
        DJCMS_ERROR="nie udało się sprawdzić, czy baza ${DJCMS_DB} istnieje"
        printf 'UWAGA: %s – kopia główna idzie dalej.\n' "$DJCMS_ERROR" >&2
    elif [ "$(printf '%s' "$djcms_exists" | tr -d '\r')" = "1" ]; then
        log "1b/5 Zrzut bazy ${DJCMS_DB} (wersja porównawcza dj.)"
        DJCMS_DUMP_PLAIN="${WORK_DIR}/djcms-db-${STAMP}.dump"
        DJCMS_DUMP_FILE="${BACKUP_DIR}/djcms-db-${STAMP}.dump.gpg"
        # Konto aplikacji (superuser klastra), a nie rola `olimpiada_djcms`: jej hasło nie musi być
        # znane temu skryptowi, a superuser czyta każdą bazę. Ten sam format -Fc co baza główna.
        if docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$DJCMS_DB" -Fc </dev/null > "$DJCMS_DUMP_PLAIN" \
            && [ -s "$DJCMS_DUMP_PLAIN" ]; then
            encrypt "$DJCMS_DUMP_PLAIN" "$DJCMS_DUMP_FILE"
            log "    $(du -h "$DJCMS_DUMP_FILE" | cut -f1) -> $DJCMS_DUMP_FILE"
        else
            DJCMS_ERROR="zrzut bazy ${DJCMS_DB} nie powiódł się"
            DJCMS_DUMP_FILE=""
            printf 'UWAGA: %s – kopia główna idzie dalej.\n' "$DJCMS_ERROR" >&2
        fi
    else
        # DJCMS_ENABLED=1, a bazy nie ma: przełącznik wpisany ręcznie przed pierwszym wdrożeniem
        # z dj. (bazę zakłada scripts/djcms_db.sh w kroku 4/8). Nie ma czego kopiować – to nie błąd.
        log "1b/5 DJCMS_ENABLED=1, ale baza ${DJCMS_DB} nie istnieje – kopia dj. pominięta"
    fi
fi

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
# Adres z hasłem roota MinIO przez plik `--env-file` (600, w katalogu roboczym 700 sprzątanym przez
# `trap`), a nie `-e MC_HOST_src=http://root:hasło@…` w argumentach: argumenty `docker run` widzi
# `ps` każdego konta na serwerze przez cały czas lustra (audyt 10.10.2026). W `docker inspect`
# kontenera zmienna i tak jest – ale tam widzi ją wyłącznie root, a kontener żyje minuty.
( umask 077 && printf 'MC_HOST_src=http://%s:%s@minio:9000\n' "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" \
    > "${WORK_DIR}/mc.env" )
docker run --rm --network "$NETWORK" --env-file "${WORK_DIR}/mc.env" \
    -v "${WORK_DIR}/buckets:/backup" \
    -e MC_QUIET=1 -e MC_NO_COLOR=1 \
    --entrypoint sh "$MC_IMAGE" -c '
        set -e
        for bucket in submissions public-media; do
            # Zwykłe wyjście mc to jedna linia na plik, czyli w logu crona kilkadziesiąt tysięcy
            # linii co noc. Wyrzucamy je, ale WYŁĄCZNIE ze standardowego wyjścia – błędy idą na
            # stderr i zostają, a `set -e` nadal przerwie przebieg na niezerowym kodzie.
            #
            # `workshop-materials/` (nagrania i pliki z warsztatów, apps/workshop_materials) jest
            # WYŁĄCZONE z kopii nocnej: to materiały organizatora, których oryginały ma organizator
            # (platforma wideo, dysk prowadzącego), a pojedyncze nagranie waży gigabajty. Kopia jest
            # co noc pełna (lustro do katalogu tymczasowego → tar → gpg → 7 dni lokalnie + 30 dni
            # poza serwerem), więc 20 GB filmów to ~60 GB chwilowo na dysku i 600 GB u dostawcy.
            # Po odtworzeniu z kopii materiały trzeba wgrać ponownie – docs/OPERACJE.md § 16.
            mc mirror --overwrite --remove --exclude "workshop-materials/*" "src/$bucket" "/backup/$bucket" >/dev/null
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

# --- 2b. Pliki redaktorów dj. (wolumen djcms_media) ------------------------------------------
# Obrazy wgrane w dj. (filer) nie są w MinIO, tylko na wolumenie `djcms_media` – baza djcms bez
# nich odtworzyłaby strony z pustymi ramkami w miejscu zdjęć. Tylko wtedy, gdy powstał zrzut bazy
# z 1b: pliki bez bazy, która je opisuje, są bezużyteczne (filer nie ma ich w indeksie).
#
# `tar` wewnątrz działającego kontenera `djcms` (obraz ma GNU tar), strumieniem na stdout – ten sam
# wzorzec co zrzut bazy przez `exec -T db pg_dump`. Kontener ma system plików tylko do odczytu
# i 64 MB /tmp, więc paczka nie może powstać w środku; wolumenu nie montujemy też do osobnego
# kontenera, bo nazwa wolumenu zależy od nazwy projektu compose'a, a to jedno miejsce więcej, które
# może się rozjechać. `--profile djcms` jawnie, choć COMPOSE_PROFILES w .env już go włącza (jak
# w scripts/deploy.sh) – polecenie ma działać także przy ręcznie poprawionym COMPOSE_PROFILES.
#
# djcms nie działa, a baza jest → ostrzeżenie i przebieg nieudany (DJCMS_ERROR). Kopia plików
# ze starego stanu byłaby gorsza niż żadna, a „jakoś się uda” o trzeciej w nocy nikt nie sprawdzi.
if [ -n "$DJCMS_DUMP_FILE" ]; then
    log "2b/5 Pliki dj. (wolumen djcms_media)"
    DJCMS_FILES_PLAIN="${WORK_DIR}/djcms-files-${STAMP}.tar"
    if [ -z "$(docker compose --profile djcms ps -q --status running djcms </dev/null 2>/dev/null || true)" ]; then
        DJCMS_ERROR="kontener djcms nie działa – brak kopii plików dj. (baza ${DJCMS_DB} skopiowana)"
        printf 'UWAGA: %s.\n' "$DJCMS_ERROR" >&2
    elif docker compose --profile djcms exec -T djcms tar --sort=name -C /app/media -cf - . </dev/null > "$DJCMS_FILES_PLAIN" \
        && [ -s "$DJCMS_FILES_PLAIN" ]; then
        DJCMS_FILES_FILE="${BACKUP_DIR}/djcms-files-${STAMP}.tar.gpg"
        encrypt "$DJCMS_FILES_PLAIN" "$DJCMS_FILES_FILE"
        log "    $(du -h "$DJCMS_FILES_FILE" | cut -f1) -> $DJCMS_FILES_FILE"
    else
        DJCMS_ERROR="tar wolumenu djcms_media nie powiódł się"
        printf 'UWAGA: %s – kopia główna idzie dalej.\n' "$DJCMS_ERROR" >&2
    fi
fi

# --- 3. Wysyłka poza serwer ------------------------------------------------------------------
# Dokąd i jak – scripts/lib/backup_offsite.sh (S3 albo Dysk Google, konfiguracja wybrana wyżej).
DUMP_NAME="$(basename "$DUMP_FILE")"
FILES_NAME="$(basename "$FILES_FILE")"
# Lista paczek tej nocy: zawsze baza i kubełki, przy dj. także jego baza i pliki (1b/2b) – te,
# które powstały. Bez dj. lista jest ta sama co przed DJ-01, więc i polecenia rclone są te same.
UPLOAD_NAMES=("$DUMP_NAME" "$FILES_NAME")
[ -z "$DJCMS_DUMP_FILE" ] || UPLOAD_NAMES+=("$(basename "$DJCMS_DUMP_FILE")")
[ -z "$DJCMS_FILES_FILE" ] || UPLOAD_NAMES+=("$(basename "$DJCMS_FILES_FILE")")
if [ "$OFFSITE_STATUS" = "failed" ]; then
    log "3/5 Wysyłka poza serwer POMINIĘTA – błąd konfiguracji: ${OFFSITE_ERROR}"
elif [ "$OFFSITE_TYPE" = "none" ]; then
    OFFSITE_STATUS=none
    log "3/5 Kopia poza serwerem nie jest skonfigurowana – kopia zostaje WYŁĄCZNIE lokalnie (${BACKUP_DIR})."
    log "    To nie jest kopia zapasowa w sensie, w jakim potrzebuje jej olimpiada: ginie razem"
    log "    z serwerem. Konfiguracja: docs/OPERACJE.md § 1.3 (S3) albo § 1.6 (Dysk Google)."
else
    log "3/5 Wysyłka: $(offsite_describe) -> daily/"
    OFFSITE_STATUS=ok
    offsite_upload daily "${UPLOAD_NAMES[@]}" || OFFSITE_STATUS=failed

    # Kopia miesięczna pierwszego dnia miesiąca. Osobny prefiks, a nie dłuższa retencja dzienna:
    # awarie, które wychodzą po kwartale (cicha korupcja danych, skasowana edycja sprzed roku),
    # wymagają punktu odniesienia starszego niż 30 dni, a trzymanie 365 kopii dziennych jest
    # trzydziestokrotnie droższe od trzymania dwunastu miesięcznych.
    if [ "$OFFSITE_STATUS" = "ok" ] && [ "$DAY_OF_MONTH" = "01" ]; then
        log "    Pierwszy dzień miesiąca – kopia także do monthly/"
        offsite_upload monthly "${UPLOAD_NAMES[@]}" || OFFSITE_STATUS=failed
    fi

    # --- 4. Retencja zdalna -------------------------------------------------------------------
    # `delete --min-age` po stronie rclone, a nie reguła lifecycle u dostawcy: reguła lifecycle
    # jest niewidoczna z tego repozytorium i po zmianie dostawcy trzeba ją założyć od nowa (i nikt
    # o tym nie pamięta). Jeśli dostawca ma lifecycle i ktoś je włączy, te dwa polecenia po prostu
    # nie znajdą nic do skasowania – nie kolidują.
    #
    # Retencja rusza WYŁĄCZNIE po udanej i zweryfikowanej wysyłce: skoro dzisiejsza kopia leży po
    # tamtej stronie, kasowanie najstarszych nie zostawi pustego miejsca. Po nieudanej wysyłce stare
    # kopie są jedynymi, jakie są poza serwerem – nie kasujemy ich.
    if [ "$OFFSITE_STATUS" = "ok" ]; then
        log "4/5 Retencja zdalna: daily > ${REMOTE_DAILY_KEEP_DAYS}d, monthly > ${REMOTE_MONTHLY_KEEP_DAYS}d"
        offsite_retention || OFFSITE_STATUS=failed
    else
        log "4/5 Retencja zdalna POMINIĘTA – wysyłka się nie powiodła"
    fi
fi

# --- 5. Retencja lokalna i ślad w aplikacji --------------------------------------------------
# Lokalna retencja idzie także po nieudanej wysyłce: kasuje paczki starsze niż LOCAL_KEEP_DAYS,
# a dzisiejsza (świeża) zostaje. Bez tego seria nieudanych nocy zapełniłaby dysk.
log "5/5 Retencja lokalna: > ${LOCAL_KEEP_DAYS} dni"
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.gpg' -mtime "+${LOCAL_KEEP_DAYS}" -print -delete

# Znacznik dla /status.json i dla watchdoga alertów. Zapisujemy go dopiero tutaj, po wszystkich
# krokach: „ostatnia udana kopia” ma znaczyć kopię kompletną, a nie moment rozpoczęcia przebiegu.
# Błąd samego zapisu znacznika nie unieważnia kopii, więc nie przerywa skryptu – ale idzie na stderr.
#
# Skonfigurowana, a nieudana wysyłka poza serwer to NIEUDANA kopia: `--failed` nie przesuwa
# znacznika, więc po progu (36 h) watchdog alarmuje, a notatka mówi, co się stało. Kopia lokalna
# istnieje, ale kopia, która ginie razem z serwerem, nie jest tą, którą obiecuje konfiguracja.
record_status() {
    if ! docker compose exec -T web python manage.py record_backup_status "$@" </dev/null; then
        printf 'UWAGA: nie udało się zapisać znacznika kopii w aplikacji.\n' >&2
    fi
}

# Kopia dj., która się nie udała (DJCMS_ERROR z 1b/2b), przy udanej kopii głównej to także przebieg
# NIEUDANY: znaczniki stoją, notatka mówi, że kopia główna jest, a czego brakuje. Przebieg „ok”
# z brakującą bazą albo plikami dj. znaczyłby, że po awarii serwera wersji porównawczej nie ma
# z czego odtworzyć, a nikt się o tym nie dowiedział (DJ-01 § 8.11: „status nie ok”).
if [ -n "$DJCMS_ERROR" ] && { [ "$OFFSITE_STATUS" = "ok" ] || [ "$OFFSITE_STATUS" = "none" ]; }; then
    if [ "$OFFSITE_STATUS" = "ok" ]; then where="lokalnie i poza serwerem"; else where="wyłącznie lokalnie"; fi
    record_status --failed --note "kopia główna ${STAMP} jest (${where}), kopia dj. NIE: ${DJCMS_ERROR}"
    printf 'BŁĄD: kopia wersji porównawczej dj. niepełna (%s). Kopia główna: %s, %s (%s).\n' \
        "$DJCMS_ERROR" "$DUMP_FILE" "$FILES_FILE" "$where" >&2
    exit 1
fi
case "$OFFSITE_STATUS" in
    ok)
        record_status --ok --offsite --note "poza serwerem: $(offsite_describe) (${STAMP})"
        log "Gotowe: kopia lokalna + poza serwerem, zweryfikowana sumą kontrolną (${STAMP})."
        ;;
    none)
        record_status --ok
        log "Gotowe: kopia WYŁĄCZNIE lokalna (${STAMP})."
        ;;
    *)
        record_status --failed --note "kopia lokalna ${STAMP} jest, poza serwer NIE dotarła: ${OFFSITE_ERROR:-nieznany błąd}${DJCMS_ERROR:+; dj.: ${DJCMS_ERROR}}"
        printf 'BŁĄD: kopia poza serwerem nie powiodła się (%s). Kopia lokalna: %s, %s\n' \
            "${OFFSITE_ERROR:-nieznany błąd}" "$DUMP_FILE" "$FILES_FILE" >&2
        exit 1
        ;;
esac
