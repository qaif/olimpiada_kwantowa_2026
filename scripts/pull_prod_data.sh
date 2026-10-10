#!/usr/bin/env bash
# Pobiera DANE z produkcji (baza Postgres + obiekty MinIO: public-media, submissions) i ładuje je do lokalnego
# środowiska dev (docker compose z nakładką docker-compose.dev.yml). Kod nie jest ruszany – ten musi być
# w tej samej wersji co na serwerze (deploy robi `git archive HEAD`), inaczej migracje przy starcie `web`
# mogą się nie zgadzać.
#
# !!! RODO / DANE OSOBOWE MAŁOLETNICH !!!
# Ten skrypt kopiuje na laptopa DANE OSOBOWE uczestników (w większości niepełnoletnich): imiona,
# nazwiska, adresy e-mail, szkoły, zgody opiekunów, wyniki, a z `--with-submissions` także ich PRACE.
# Laptop z DEBUG=1, kontami demo i bez szyfrowania dysku to inne środowisko niż serwer. Uruchamiaj
# wyłącznie, gdy jest do tego podstawa (zgłoszony błąd, którego nie da się odtworzyć na danych
# testowych), na zaszyfrowanym dysku, i usuń dane po zakończeniu pracy (`docker compose down -v`
# na projekcie deweloperskim). Kopia jest przetwarzaniem danych – administrator (organizator) musi
# o niej wiedzieć. Audyt bezpieczeństwa 10.10.2026: tokeny API i sesje NIE opuszczają serwera
# (`--exclude-table-data` w zrzucie), a prace uczestników – tylko na wyraźne żądanie.
#
# Użycie (Git Bash, z katalogu repo):
#   scripts/pull_prod_data.sh root@169.58.242.197
#   scripts/pull_prod_data.sh --with-submissions root@169.58.242.197   # także bucket `submissions`
#   SSH_KEY=~/.ssh/olimpiada_deploy KEEP_DUMP=1 scripts/pull_prod_data.sh root@olimpiadakwantowa.pl
#
# Co robi:
#   1. pg_dump w kontenerze `db` na serwerze (BEZ danych tabel `authtoken_token` i `django_session`
#      – jawne tokeny API i aktywne sesje produkcji) -> plik tymczasowy lokalnie
#   2. mc mirror bucketów w kontenerze `minio` na serwerze -> tar -> plik tymczasowy lokalnie;
#      domyślnie WYŁĄCZNIE `public-media` (media redakcyjne), `submissions` (prace uczestników
#      i treści zadań) tylko z `--with-submissions`
#   3. lokalnie: stop web/worker/beat, DROP + CREATE bazy, pg_restore (bez właścicieli/uprawnień),
#      a po nim jeszcze `TRUNCATE authtoken_token, django_session` (gdyby zrzut powstał inaczej)
#   4. lokalnie: domyślny Site Wagtaila -> localhost:8000 (adresy stron i podglądy w dev)
#   5. lokalnie: mc mirror --remove do lokalnych bucketów (dokładna kopia pobranych bucketów)
#   6. start web/worker/beat
# UWAGA: lokalna baza i buckety są ZASTĘPOWANE. Dump zawiera dane osobowe – plik jest kasowany po imporcie
# (KEEP_DUMP=1 zostawia go w katalogu podanym na końcu). Sesje/logowania wygasną (inny SECRET_KEY) – hasła
# użytkowników z produkcji działają lokalnie (hash w bazie).
set -euo pipefail
export MSYS_NO_PATHCONV=1

WITH_SUBMISSIONS=0
if [ "${1:-}" = "--with-submissions" ]; then WITH_SUBMISSIONS=1; shift; fi
TARGET="${1:?użycie: scripts/pull_prod_data.sh [--with-submissions] user@host}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/olimpiada_deploy}"
REMOTE_DIR="${REMOTE_DIR:-/opt/olimpiada}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$TARGET")
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)
# Prace uczestników (bucket `submissions`) tylko na wyraźne żądanie: do odtworzenia błędu strony
# czy CMS-u wystarczają media redakcyjne, a prace to największa i najwrażliwsza część danych.
BUCKETS="public-media"
[ "$WITH_SUBMISSIONS" = 1 ] && BUCKETS="public-media submissions"
printf 'UWAGA: kopiujesz dane osobowe uczestników z produkcji na ten komputer (buckety: %s).\n' "$BUCKETS" >&2
WORK="$(mktemp -d)"
trap '[ "${KEEP_DUMP:-0}" = 1 ] && echo "Pliki zostawione w: $WORK" || rm -rf "$WORK"' EXIT

log() { printf '\n==> %s\n' "$*"; }

log "1/6 Dump bazy z produkcji ($TARGET:$REMOTE_DIR)"
# `--exclude-table-data`: struktura tabel zostaje (migracje się zgadzają), ale jawne tokeny API
# (`authtoken_token` – klucz w bazie wprost) i sesje produkcji nie wyjeżdżają z serwera wcale.
"${SSH[@]}" "cd '$REMOTE_DIR' && docker compose exec -T db sh -c 'pg_dump -Fc --exclude-table-data=authtoken_token --exclude-table-data=django_session -U \"\$POSTGRES_USER\" \"\$POSTGRES_DB\"'" > "$WORK/prod.dump"
ls -la "$WORK/prod.dump"

log "2/6 Obiekty MinIO z produkcji ($BUCKETS)"
# Obraz MinIO nie ma ``tar`` – ``mc mirror`` zrzuca obiekty do katalogu w kontenerze, ``docker compose cp``
# wyciąga go na hosta, a pakuje dopiero ``tar`` hosta. Wyjście ``cp`` idzie do /dev/null, bo stdout
# tego polecenia jest strumieniem archiwum.
"${SSH[@]}" "cd '$REMOTE_DIR' && docker compose exec -T minio sh -c '
  set -e
  mc alias set local http://127.0.0.1:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" >/dev/null
  rm -rf /tmp/pull && mkdir -p /tmp/pull
  for b in $BUCKETS; do mkdir -p /tmp/pull/\$b; mc mirror --quiet local/\$b /tmp/pull/\$b >/dev/null 2>&1 || true; done' \
  && rm -rf /tmp/olimpiada-pull \
  && docker compose cp minio:/tmp/pull /tmp/olimpiada-pull >/dev/null 2>&1 \
  && docker compose exec -T minio rm -rf /tmp/pull \
  && tar -c -C /tmp/olimpiada-pull . \
  && rm -rf /tmp/olimpiada-pull" > "$WORK/objects.tar"
ls -la "$WORK/objects.tar"
mkdir -p "$WORK/objects" && tar -x -C "$WORK/objects" -f "$WORK/objects.tar"

log "3/6 Lokalnie: zatrzymanie aplikacji i przywrócenie bazy"
"${COMPOSE[@]}" stop web worker beat >/dev/null
"${COMPOSE[@]}" exec -T db sh -c '
  set -e
  dropdb --force --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"
  createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"'
# pg_restore zwraca kod 1 także przy nieszkodliwych ostrzeżeniach (np. COMMENT ON EXTENSION) – pokazujemy je i idziemy dalej.
"${COMPOSE[@]}" exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges' < "$WORK/prod.dump" \
  || echo "pg_restore zakończył się ostrzeżeniami (patrz wyżej)"
# Druga zapora po `--exclude-table-data` w kroku 1 (zrzut zrobiony ręcznie albo starszą wersją
# skryptu): żadnego działającego tokenu API ani sesji z produkcji w bazie deweloperskiej.
"${COMPOSE[@]}" exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "TRUNCATE authtoken_token, django_session;"'

log "4/6 Lokalnie: domyślny Site Wagtaila -> localhost:8000"
# Trzy osobne wywołania psql, a nie jedno wieloinstrukcyjne: psql wysyła cały ciąg -c jako JEDNĄ
# transakcję, więc błąd któregokolwiek SELECT-a (literówka w nazwie tabeli, inny schemat po
# zmianie modelu) cofałby także UPDATE – i skrypt kończyłby się „sukcesem” z niepodmienionym
# hostname. Model użytkownika to ``accounts.User``, a więc tabela ``accounts_user``: ``auth_user``
# w tej bazie nie istnieje.
SITE_SQL="UPDATE wagtailcore_site SET hostname='localhost', port=8000 WHERE is_default_site;"
"${COMPOSE[@]}" exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' _ "$SITE_SQL"
# Licznik wierszy jednej tabeli. Nazwa tabeli wchodzi do SQL po stronie bash-a, sam SQL leci do
# `sh -c` jako argument pozycyjny – dzięki temu apostrofy nie muszą przechodzić przez dwa poziomy
# cudzysłowów. Każdy licznik to osobne wywołanie: nieudany SELECT ma zwrócić pustą liczbę, a nie
# cofnąć UPDATE powyżej.
psql_count() {
  local sql="SELECT count(*) FROM $1;"
  "${COMPOSE[@]}" exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"' _ "$sql" | tr -d '\r'
}
echo "users=$(psql_count accounts_user)"
echo "pages=$(psql_count wagtailcore_page)"

log "5/6 Lokalnie: obiekty MinIO (mirror --remove)"
# Ta sama droga w drugą stronę: rozpakowane na hoście, wkopiowane ``docker compose cp`` (bez ``tar``
# w kontenerze). Ścieżka hosta przez ``cygpath -w``: docker.exe pod Windows nie zna /tmp Git Basha.
HOST_OBJECTS="$(cygpath -w "$WORK/objects" 2>/dev/null || echo "$WORK/objects")"
"${COMPOSE[@]}" exec -T minio rm -rf /tmp/pull
"${COMPOSE[@]}" cp "$HOST_OBJECTS" minio:/tmp/pull >/dev/null
"${COMPOSE[@]}" exec -T minio sh -c '
  set -e
  mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
  for b in '"$BUCKETS"'; do
    mkdir -p /tmp/pull/$b
    mc mirror --overwrite --remove --quiet /tmp/pull/$b local/$b >/dev/null 2>&1 || true
    mc du local/$b
  done
  rm -rf /tmp/pull'

log "6/6 Lokalnie: start aplikacji"
"${COMPOSE[@]}" start web worker beat >/dev/null
"${COMPOSE[@]}" ps --format 'table {{.Service}}\t{{.Status}}'
echo
echo "Gotowe. Dane z produkcji są w lokalnym dev: http://localhost:8000/"
