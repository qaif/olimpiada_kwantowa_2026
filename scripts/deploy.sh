#!/usr/bin/env bash
# Wdrożenie produkcyjne przez SSH (klucz, bez hasła): kod z `git archive HEAD`, Docker, .env, compose, seedy.
#
# Użycie (z katalogu repo, Git Bash/Linux):
#   scripts/deploy.sh root@olimpiadakwantowa.pl            # pełne wdrożenie / aktualizacja
#   SSH_KEY=~/.ssh/olimpiada_deploy scripts/deploy.sh root@169.58.242.197
#
# Wymagane zmienne przy PIERWSZYM wdrożeniu (tworzą .env na serwerze; potem .env nie jest nadpisywany):
#   SITE_DOMAIN            np. olimpiadakwantowa.pl
#   ACME_EMAIL             e-mail do Let's Encrypt
#   COORDINATOR_EMAIL      pierwsze konto koordynatora (superuser + grupa coordinator)
#   COORDINATOR_PASSWORD   hasło tego konta (przekazane przez SSH env, nie zapisywane w repo)
# Opcjonalne: S3_PUBLIC_ADDRESS (domyślnie <domena>:9000; po dodaniu rekordu DNS: s3.<domena>),
#             MAKE_EDITION_CURRENT=1 (edycja „I edycja 2026/2027” jako bieżąca), APP_VERSION,
#             DMARC_RUA (adres raportów DMARC, domyślnie contact@qaif.org),
#             MAIL_PUBLIC_IP (adres do rekordu SPF, gdy serwer wychodzi przez inny IP niż własny),
#             BACKUP_DIR (katalog kopii przed migracjami, domyślnie /opt/olimpiada-backups).
#
# Gotowy obraz zamiast budowania na serwerze (opcjonalnie, docs/UNIWERSALNY-ETAP-2.md § 1.7.2):
#   WEB_IMAGE=ghcr.io/qaif/olimpiada-web:v0.24.0 scripts/deploy.sh root@<host>
# Krok 4/8 pobiera wtedy obraz z rejestru zamiast go budować, a wartość trafia do <REMOTE_DIR>/.env,
# żeby każde kolejne `docker compose` na serwerze (start, restart, `up -d proxy`) widziało ten sam
# obraz. BEZ tej zmiennej wdrożenie robi dokładnie to, co robiło: `docker compose build --pull web`
# na serwerze – i to jest droga domyślna dla Olimpiady Kwantowej. Powrót do budowania = kolejne
# wdrożenie bez WEB_IMAGE (skrypt usuwa wtedy wpis z .env).
#
# Nowy konkurs (platforma wielokonkursowa, docs/UNIWERSALNY-ETAP-1.md § 4.5) – krok 6a wykonuje się
# WYŁĄCZNIE wtedy, gdy ustawiono NEW_COMPETITION_SLUG. Bez tej zmiennej wdrożenie nie woła komendy
# `create_competition` ani razu, więc przebieg dla Olimpiady Kwantowej jest taki, jak był:
#   NEW_COMPETITION_SLUG=fizyczna NEW_COMPETITION_NAME="Olimpiada Fizyczna" \
#   NEW_COMPETITION_DOMAIN=olimpiadafizyczna.pl NEW_COMPETITION_TEMPLATE=przedmiotowa \
#   [NEW_COMPETITION_ORGANIZER="Polskie Towarzystwo Fizyczne"] \
#   [NEW_COMPETITION_EDITION_LABEL="I edycja 2026/2027"] \
#   [NEW_COMPETITION_COORDINATOR_EMAIL=koordynator@example.org] scripts/deploy.sh root@<host>
# Komenda zakłada też pierwszą edycję (bieżącą) i etapy z szablonu – ich terminy są wartością
# początkową odłożoną od pierwszego dnia następnego miesiąca i poprawia je koordynator w panelu.
# Domena konkursu musi jeszcze trafić do EXTRA_DOMAINS, DJANGO_ALLOWED_HOSTS
# i DJANGO_CSRF_TRUSTED_ORIGINS w <REMOTE_DIR>/.env – komenda wypisuje gotowe linijki, a rozjazd
# wykrywa `manage.py check_domains`, wołane na końcu wdrożenia.
#
# Konkursy w subdomenach platformy (docs/OPERACJE.md § 6.5) – `PLATFORM_SUBDOMAINS=1`
# w <REMOTE_DIR>/.env. Wtedy krok 4/8 generuje konfigurację proxy z blokiem `*.<domena>`
# i on-demand TLS za zgodą aplikacji, a koordynator zakłada kolejne konkursy z panelu, bez
# wdrożenia. BEZ tej zmiennej (domyślnie) konfiguracja proxy jest co do bajtu ta, co dotąd.
#
# Krok 7 wypisuje i zapisuje do <REMOTE_DIR>/mail-dns.txt rekordy SPF/DKIM/DMARC/PTR dla usługi
# `mail` (własny Postfix). Dopóki ich nie dodasz w DNS-ie, poczta idzie do spamu albo jest odrzucana.
set -euo pipefail

TARGET="${1:?użycie: scripts/deploy.sh user@host}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/olimpiada_deploy}"
REMOTE_DIR="${REMOTE_DIR:-/opt/olimpiada}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$TARGET")
APP_VERSION="${APP_VERSION:-$(git describe --tags --always)}"

log() { printf '\n==> %s\n' "$*"; }

log "1/8 Docker na serwerze"
"${SSH[@]}" bash -s <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  # Pakiety Ubuntu (docker.io + docker-compose-v2): dostępne od razu dla każdego wydania, bez zależności od
  # tego, czy repozytorium Dockera zna już nazwę kodową systemu (26.04).
  apt-get install -y -qq ca-certificates curl ufw docker.io docker-compose-v2 >/dev/null
  systemctl enable --now docker
fi
docker --version && docker compose version
# Zapora: SSH, HTTP/HTTPS, port S3 dla presigned URL. Reszta (db, redis, clamav) nie jest publikowana.
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 9000/tcp >/dev/null
  ufw --force enable >/dev/null
  ufw status | head -8
fi
REMOTE

log "2/8 Kod: git archive HEAD -> $REMOTE_DIR"
"${SSH[@]}" "mkdir -p '$REMOTE_DIR' && find '$REMOTE_DIR' -mindepth 1 -maxdepth 1 ! -name .env ! -name 'e2e' -exec rm -rf {} +"
git archive --format=tar HEAD | "${SSH[@]}" "tar -x -C '$REMOTE_DIR'"

log "3/8 .env (tworzony tylko przy pierwszym wdrożeniu)"
"${SSH[@]}" env SITE_DOMAIN="${SITE_DOMAIN:-}" ACME_EMAIL="${ACME_EMAIL:-}" S3_PUBLIC_ADDRESS="${S3_PUBLIC_ADDRESS:-}" APP_VERSION="$APP_VERSION" REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
gen() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }
if [ ! -f .env ]; then
  : "${SITE_DOMAIN:?SITE_DOMAIN wymagane przy pierwszym wdrożeniu}"
  : "${ACME_EMAIL:?ACME_EMAIL wymagane przy pierwszym wdrożeniu}"
  S3_ADDR="${S3_PUBLIC_ADDRESS:-$SITE_DOMAIN:9000}"
  cat > .env <<EOF
APP_VERSION=$APP_VERSION
SITE_DOMAIN=$SITE_DOMAIN
ACME_EMAIL=$ACME_EMAIL
S3_PUBLIC_ADDRESS=$S3_ADDR
MAX_UPLOAD_MB=25

DJANGO_SECRET_KEY=$(gen 64)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$SITE_DOMAIN,www.$SITE_DOMAIN,web,127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://$SITE_DOMAIN,https://www.$SITE_DOMAIN
WEB_WORKERS=3
CELERY_CONCURRENCY=2

POSTGRES_DB=olimpiada
POSTGRES_USER=olimpiada
POSTGRES_PASSWORD=$(gen 32)

MINIO_ROOT_USER=minio-root
MINIO_ROOT_PASSWORD=$(gen 32)
S3_PRESIGNED_TTL_SECONDS=600
S3_PUBLIC_ENDPOINT_URL=https://$S3_ADDR
S3_PUBLIC_ACCESS_KEY=wagtail-media
S3_PUBLIC_SECRET_KEY=$(gen 32)
S3_PRIVATE_ACCESS_KEY=app-private
S3_PRIVATE_SECRET_KEY=$(gen 32)

TRUSTED_PROXY_IPS=172.30.1.0/24,172.30.2.0/24

# Poczta wychodząca: własny Postfix z usługi \`mail\` (send-only relay + DKIM), bez zewnętrznego
# dostawcy. Port 587 nie jest publikowany – relay widzi tylko sieć compose. Aby użyć dostawcy
# zewnętrznego, podmień na EMAIL_URL=smtp+tls://uzytkownik:haslo@host:587 (README § 4.1, wariant B).
EMAIL_URL=smtp://mail:587
DEFAULT_FROM_EMAIL=noreply@$SITE_DOMAIN
EMAIL_TIMEOUT=10
EOF
  chmod 600 .env
  # Znacznik dla kroku 6/7: seedy treści uruchamiają się same wyłącznie przy pierwszym wdrożeniu.
  touch .first-deploy
  echo ".env utworzony"
else
  sed -i "s/^APP_VERSION=.*/APP_VERSION=$APP_VERSION/" .env
  echo ".env istnieje – zachowany (zaktualizowano APP_VERSION)"
fi
REMOTE

if [ -n "${WEB_IMAGE:-}" ]; then
    log "4/8 Konfiguracja proxy (EXTRA_DOMAINS), obraz z rejestru ($WEB_IMAGE) i start samej bazy"
else
    log "4/8 Konfiguracja proxy (EXTRA_DOMAINS), build obrazu i start samej bazy"
fi
# Rozdzielenie dawnego kroku „build i start usług” na 4 / 4a / 4b bierze się z jednego faktu:
# migracje uruchamia entrypoint kontenera `web` (backend/entrypoint.sh), więc jedyne miejsce, w
# którym da się zrobić kopię bazy **sprzed** migracji, jest między startem `db` a startem `web`.
# Polecenie startujące komplet usług (krok 4b) zostaje co do znaku takie, jakie było.
"${SSH[@]}" env REMOTE_DIR="$REMOTE_DIR" WEB_IMAGE="${WEB_IMAGE:-}" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
# Dwie zmienne wielokonkursowości dokładane do .env **tylko wtedy, gdy ich nie ma**. Istniejących
# wartości ten skrypt nie rusza (tak samo jak krok 3/8 nie rusza całego pliku), a serwer, na
# którym .env powstał przed wielokonkursowością, dostaje je przy pierwszym wdrożeniu po zmianie –
# bez ręcznej edycji pliku, której nikt by nie pamiętał.
if ! grep -qE '^EXTRA_DOMAINS=' .env; then
  {
    echo
    echo "# Domeny kolejnych konkursów, rozdzielone spacjami (docs/UNIWERSALNY-ETAP-1.md § 2.5)."
    echo "# Ta sama lista wchodzi do bloków Caddy'ego (scripts/render_caddyfile.sh), do"
    echo "# ALLOWED_HOSTS i do CSRF_TRUSTED_ORIGINS – wpisana w dwóch miejscach z trzech daje"
    echo "# albo 400 na każde żądanie, albo odmowę CSRF na każdym formularzu."
    echo "EXTRA_DOMAINS="
  } >> .env
  chmod 600 .env
fi
# Subdomeny platformy: przełącznik dokładany **zakomentowany**, więc domyślną odpowiedzią jest
# „wyłączone” i konfiguracja proxy zostaje taka, jaka była. Wpis istnieje po to, żeby operator
# znalazł przełącznik w swoim `.env`, a nie w dokumentacji (docs/OPERACJE.md § 6.5). Wzorzec
# `^#? *PLATFORM_SUBDOMAINS=` dopasowuje także wersję zakomentowaną – inaczej każde wdrożenie
# dopisywałoby ten sam akapit jeszcze raz.
if ! grep -qE '^#? *PLATFORM_SUBDOMAINS=' .env; then
  {
    echo
    echo "# Konkursy w subdomenach platformy: <slug>.<SITE_DOMAIN> zakładane z panelu koordynatora,"
    echo "# bez wdrożenia i bez wpisu w tym pliku (docs/OPERACJE.md § 6.5). Wymaga rekordu DNS"
    echo "# *.<SITE_DOMAIN> wskazującego ten serwer; certyfikat powstaje przy pierwszym wejściu,"
    echo "# a zgody na jego wystawienie udziela aplikacja (/internal/tls-allowed), więc limit"
    echo "# Let's Encrypt (50 certyfikatów na domenę tygodniowo) zużywają tylko istniejące konkursy."
    echo "# PLATFORM_SUBDOMAINS=1"
  } >> .env
  chmod 600 .env
fi
if ! grep -qE '^CADDYFILE_PATH=' .env; then
  {
    echo "# Konfiguracja proxy montowana do kontenera: plik składany z deploy/Caddyfile"
    echo "# i EXTRA_DOMAINS przez scripts/render_caddyfile.sh (patrz docker-compose.yml)."
    echo "CADDYFILE_PATH=./deploy/Caddyfile.generated"
  } >> .env
  chmod 600 .env
fi
# Generator chodzi przy każdym wdrożeniu, także gdy EXTRA_DOMAINS jest puste: krok 2/8 czyści
# katalog z wszystkiego poza .env, a plik wynikowy nie jest w repozytorium. Przy pustej liście
# i wyłączonym PLATFORM_SUBDOMAINS wynik jest kopią deploy/Caddyfile co do bajtu
# (scripts/tests/render_caddyfile_test.sh).
#
# Obie zmienne generator czyta **sam**, z <REMOTE_DIR>/.env: skrypt wywołuje go z katalogu
# wdrożenia, więc jego `ROOT` to ten katalog. Dlatego PLATFORM_SUBDOMAINS trafia do konfiguracji
# proxy tą samą drogą co EXTRA_DOMAINS i wdrożenie nie musi nic przekazywać przez środowisko
# (ani nie da się przez pomyłkę nadpisać wartości z serwera zmienną z własnej powłoki).
chmod +x scripts/render_caddyfile.sh
./scripts/render_caddyfile.sh
# Źródło obrazu aplikacji: rejestr albo build na miejscu. Rozgałęzienie, a nie podmiana – bez
# WEB_IMAGE wykonuje się dokładnie to polecenie, które wykonywało się dotąd
# (docs/UNIWERSALNY-ETAP-2.md § 0.2 pkt 11 i § 1.7.2).
if [ -n "${WEB_IMAGE:-}" ]; then
  # Wpis w .env, bo compose czyta zmienne stamtąd: krok 4b i każde późniejsze `docker compose`
  # na serwerze (restart usługi, `up -d proxy`, praca ręczna w awarii) muszą widzieć ten sam
  # obraz. Bez wpisu compose wróciłby do wartości domyślnej, czyli do **budowania** obrazu
  # w pierwszym poleceniu `up`, o którym nikt by się nie dowiedział.
  if grep -qE '^WEB_IMAGE=' .env; then
    sed -i "s|^WEB_IMAGE=.*|WEB_IMAGE=$WEB_IMAGE|" .env
  else
    {
      echo
      echo "# Obraz aplikacji z rejestru zamiast budowanego na serwerze – wpisuje scripts/deploy.sh,"
      echo "# gdy wdrożenie dostanie WEB_IMAGE=… . Brak linijki WEB_IMAGE niżej znaczy: obraz budowany"
      echo "# na serwerze (skrypt kasuje ją sam przy wdrożeniu bez tej zmiennej)."
      echo "WEB_IMAGE=$WEB_IMAGE"
    } >> .env
  fi
  chmod 600 .env
  docker compose pull web
else
  # Powrót do budowania na serwerze musi też sprzątnąć po sobie: wpis z poprzedniego wdrożenia
  # z rejestru trzymałby stary obraz przy życiu mimo świeżego kodu w katalogu.
  if grep -qE '^WEB_IMAGE=' .env; then
    sed -i '/^WEB_IMAGE=/d' .env
  fi
  docker compose build --pull web
fi
docker compose up -d db
for _ in $(seq 1 30); do
  docker compose ps --format '{{.Service}}={{.Health}}' | grep -q 'db=healthy' && break
  sleep 2
done
# Twardo: bez działającej bazy nie ma kopii z kroku 4a, a bez kopii nie wolno migrować.
docker compose ps --format '{{.Service}}={{.Health}}' | grep -q 'db=healthy'
REMOTE

log "4a/8 Kopia bazy przed migracjami (pg_dump -Fc)"
# Format `custom` (-Fc), a nie zwykły SQL: pozwala odtworzyć wybraną tabelę zamiast całej bazy,
# co przy pomyłce w migracji jest różnicą między dziesięcioma minutami a wieczorem. Kopia jest
# **warunkiem** wdrożenia (`set -e` + sprawdzenie rozmiaru): niepowodzenie zatrzymuje skrypt,
# zanim entrypoint `web` wykona `migrate` (docs/UNIWERSALNY-ETAP-1.md § 0.2).
"${SSH[@]}" env REMOTE_DIR="$REMOTE_DIR" APP_VERSION="$APP_VERSION" BACKUP_DIR="${BACKUP_DIR:-/opt/olimpiada-backups}" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
PG_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)"
PG_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
# Znacznik: czas i wydanie. Czas, bo w jednym dniu bywa kilka wdrożeń tego samego tagu; wydanie,
# bo przy odtwarzaniu pytanie brzmi „sprzed której wersji”, a nie „z której godziny”.
STAMP="$(date +%Y%m%d-%H%M%S)-$(printf '%s' "$APP_VERSION" | tr -cs 'A-Za-z0-9._-' '-')"
DUMP="$BACKUP_DIR/pre-deploy-$STAMP.dump"
# </dev/null: `exec` nie może czytać stdin, bo to strumień tego skryptu.
docker compose exec -T db pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$DUMP" </dev/null
chmod 600 "$DUMP"
[ -s "$DUMP" ] || { echo "Kopia przed migracjami jest pusta – przerywam wdrożenie."; exit 1; }
ls -lh "$DUMP"
# Zostaje dziesięć ostatnich kopii przedwdrożeniowych. Bez tego katalog rośnie o pełny zrzut bazy
# przy każdym wdrożeniu i po pół roku to on wywoła awarię, przed którą miał chronić. Kopie
# nocne (scripts/backup.sh) są osobnym zestawem plików i ten limit ich nie dotyczy.
ls -1t "$BACKUP_DIR"/pre-deploy-*.dump 2>/dev/null | tail -n +11 | xargs -r rm -f
echo "Odtworzenie: docker compose exec -T db pg_restore -U $PG_USER -d $PG_DB --clean --if-exists < $DUMP"
REMOTE

log "4b/8 Start usług (migracje wykonuje entrypoint kontenera web)"
"${SSH[@]}" "cd '$REMOTE_DIR' && docker compose up -d --remove-orphans db redis minio minio-init clamav mail web worker beat proxy"

log "5/8 Oczekiwanie na healthy"
"${SSH[@]}" bash -s <<REMOTE
set -euo pipefail
cd '$REMOTE_DIR'
for i in \$(seq 1 60); do
  s=\$(docker compose ps --format '{{.Service}}={{.Health}}' | tr '\n' ' ')
  echo "\$s" | grep -q 'web=healthy' && echo "\$s" | grep -q 'proxy=healthy' && { echo "\$s"; break; }
  sleep 5
done
REMOTE

log "6/8 Seedy treści i konto koordynatora"
# Seedy treści (seed_cms, seed_regulamin, seed_legacy_content, seed_partners) są narzędziami
# importującymi: każdy przebieg nadpisuje strony CMS treścią z plików repozytorium. Po pierwszym
# wdrożeniu treść należy do redakcji (/cms/), a terminy etapów do koordynatora (panel), więc
# kolejne wdrożenia ich nie uruchamiają – chyba że jawnie: RUN_CONTENT_SEEDS=1.
# ``seed_edition_kwantowa`` bez ``--sync-dates`` tworzy wyłącznie brakujące etapy i nie rusza
# istniejących terminów, dlatego zostaje w każdym wdrożeniu.
"${SSH[@]}" env COORDINATOR_EMAIL="${COORDINATOR_EMAIL:-}" COORDINATOR_PASSWORD="${COORDINATOR_PASSWORD:-}" MAKE_EDITION_CURRENT="${MAKE_EDITION_CURRENT:-0}" SYNC_STAGE_DATES="${SYNC_STAGE_DATES:-0}" RUN_CONTENT_SEEDS="${RUN_CONTENT_SEEDS:-auto}" REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
# </dev/null: exec nie może czytać stdin, bo to strumień tego skryptu (inaczej połknąłby dalsze polecenia).
dc() { docker compose exec -T web "$@" </dev/null; }
# ``auto``: tylko przy pierwszym wdrożeniu (znacznik zostawia krok 3/7 przy tworzeniu .env).
if [ "$RUN_CONTENT_SEEDS" = "1" ] || { [ "$RUN_CONTENT_SEEDS" = "auto" ] && [ -f .first-deploy ]; }; then
  dc python manage.py seed_cms
  dc python manage.py seed_regulamin
  dc python manage.py seed_legacy_content
  dc python manage.py seed_partners          # po seed_legacy_content: dopisuje logotypy do /partnerzy/
  rm -f .first-deploy
else
  echo "seedy treści pominięte – strony CMS zostają takie, jak zredagowano na serwerze (RUN_CONTENT_SEEDS=1 wymusza)"
fi
EDITION_ARGS=""
[ "$MAKE_EDITION_CURRENT" = "1" ] && EDITION_ARGS="$EDITION_ARGS --make-current"
[ "$SYNC_STAGE_DATES" = "1" ] && EDITION_ARGS="$EDITION_ARGS --sync-dates"   # przestawia terminy istniejących etapów
dc python manage.py seed_edition_kwantowa $EDITION_ARGS
# Słownik szkół (SIO/RSPO) – dane referencyjne, nie treść redakcyjna, więc **poza** bramką
# .first-deploy: nikt go nie edytuje w panelu, a przebieg jest idempotentny (upsert po RSPO,
# szkoły spoza wykazu tylko wygasza). Bez tego wyszukiwarka w rejestracji nie ma czego pokazać.
dc python manage.py seed_schools
if [ -n "$COORDINATOR_EMAIL" ] && [ -n "$COORDINATOR_PASSWORD" ]; then
  docker compose exec -T -e COORDINATOR_EMAIL="$COORDINATOR_EMAIL" -e COORDINATOR_PASSWORD="$COORDINATOR_PASSWORD" web \
    python manage.py bootstrap_coordinator </dev/null
fi
docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
REMOTE

log "6a/8 Nowy konkurs (tylko przy NEW_COMPETITION_SLUG)"
# Krok opcjonalny i domyślnie pusty. Bez NEW_COMPETITION_SLUG nie wykonuje ani jednego polecenia
# w kontenerze – wdrożenie Olimpiady Kwantowej wygląda dokładnie tak, jak wyglądało.
"${SSH[@]}" env REMOTE_DIR="$REMOTE_DIR" \
  NEW_COMPETITION_SLUG="${NEW_COMPETITION_SLUG:-}" \
  NEW_COMPETITION_NAME="${NEW_COMPETITION_NAME:-}" \
  NEW_COMPETITION_DOMAIN="${NEW_COMPETITION_DOMAIN:-}" \
  NEW_COMPETITION_TEMPLATE="${NEW_COMPETITION_TEMPLATE:-pusty}" \
  NEW_COMPETITION_ORGANIZER="${NEW_COMPETITION_ORGANIZER:-}" \
  NEW_COMPETITION_EDITION_LABEL="${NEW_COMPETITION_EDITION_LABEL:-}" \
  NEW_COMPETITION_COORDINATOR_EMAIL="${NEW_COMPETITION_COORDINATOR_EMAIL:-}" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
if [ -z "$NEW_COMPETITION_SLUG" ]; then
  echo "NEW_COMPETITION_SLUG nieustawione – żaden konkurs nie jest zakładany."
  exit 0
fi
: "${NEW_COMPETITION_NAME:?NEW_COMPETITION_NAME wymagane razem z NEW_COMPETITION_SLUG}"
: "${NEW_COMPETITION_DOMAIN:?NEW_COMPETITION_DOMAIN wymagane razem z NEW_COMPETITION_SLUG}"
# --skip-existing: wdrożenie musi dać się powtórzyć. Bez tej flagi drugi przebieg z tymi samymi
# zmiennymi kończyłby się błędem „konkurs już istnieje”, czyli czerwonym wdrożeniem za to, że
# poprzednie się udało. Uruchomiona bez flagi (ręcznie) komenda nadal odmawia duplikatu.
ARGS=(--slug "$NEW_COMPETITION_SLUG" --name "$NEW_COMPETITION_NAME"
      --domain "$NEW_COMPETITION_DOMAIN" --from-template "$NEW_COMPETITION_TEMPLATE"
      --skip-existing)
[ -n "$NEW_COMPETITION_ORGANIZER" ] && ARGS+=(--organizer "$NEW_COMPETITION_ORGANIZER")
# Oznaczenie edycji puste = bieżący rocznik szkolny liczony przez komendę; adres koordynatora
# pusty = konkurs bez roli nadanej z wdrożenia. Konto musi już istnieć – komenda kont nie zakłada.
[ -n "$NEW_COMPETITION_EDITION_LABEL" ] && ARGS+=(--edition-label "$NEW_COMPETITION_EDITION_LABEL")
[ -n "$NEW_COMPETITION_COORDINATOR_EMAIL" ] && ARGS+=(--coordinator-email "$NEW_COMPETITION_COORDINATOR_EMAIL")
docker compose exec -T web python manage.py create_competition "${ARGS[@]}" </dev/null
REMOTE

log "7/8 DNS dla poczty (SPF / DKIM / DMARC / PTR)"
# Klucz DKIM powstaje przy pierwszym starcie usługi `mail` i leży na wolumenie `mail_dkim`,
# więc te rekordy są stałe – dopóki wolumen istnieje, kolejne wdrożenia ich nie zmieniają.
"${SSH[@]}" env REMOTE_DIR="$REMOTE_DIR" DMARC_RUA="${DMARC_RUA:-contact@qaif.org}" MAIL_PUBLIC_IP="${MAIL_PUBLIC_IP:-}" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
DOMAIN="$(grep -E '^SITE_DOMAIN=' .env | cut -d= -f2-)"
SELECTOR="$(grep -E '^DKIM_SELECTOR=' .env | cut -d= -f2- || true)"; SELECTOR="${SELECTOR:-olimpiada}"
# Adres wyjściowy: pierwszy globalny IPv4 hosta (Docker NAT-uje ruch z kontenera właśnie na niego).
IP="${MAIL_PUBLIC_IP:-$(hostname -I | tr ' ' '\n' | grep -E '^[0-9]+\.' | grep -vE '^(127|10|172\.(1[6-9]|2[0-9]|3[01])|192\.168)\.' | head -1)}"

# opendkim-genkey zapisuje rekord w formacie strefy BIND: wartość rozbita na kilka fragmentów
# w cudzysłowach. Panele DNS chcą jednego ciągu – sklejamy fragmenty.
DKIM_RAW="$(docker compose exec -T mail cat "/etc/opendkim/keys/${DOMAIN}.txt" </dev/null 2>/dev/null || true)"
DKIM_VALUE="$(printf '%s' "$DKIM_RAW" | tr -d '\r\n\t' | grep -oE '"[^"]*"' | tr -d '"' | tr -d '\n' || true)"

{
  echo "# Rekordy DNS dla poczty wychodzącej – ${DOMAIN}"
  echo "# Wygenerowane przez scripts/deploy.sh, $(date -Iseconds). Dodaj je u operatora strefy."
  echo
  echo "1) SPF   – TXT, nazwa: ${DOMAIN} (rekord w korzeniu strefy, znak @)"
  echo "   v=spf1 ip4:${IP} -all"
  echo
  echo "2) DKIM  – TXT, nazwa: ${SELECTOR}._domainkey.${DOMAIN}"
  if [ -n "$DKIM_VALUE" ]; then
    echo "   ${DKIM_VALUE}"
  else
    echo "   (BRAK – usługa 'mail' nie działa albo klucz nie został jeszcze wygenerowany;"
    echo "    odczyt: docker compose exec mail cat /etc/opendkim/keys/${DOMAIN}.txt)"
  fi
  echo
  echo "3) DMARC – TXT, nazwa: _dmarc.${DOMAIN}"
  echo "   v=DMARC1; p=quarantine; rua=mailto:${DMARC_RUA}; adkim=r; aspf=r; fo=1"
  echo
  echo "4) A     – nazwa: mail.${DOMAIN}  ->  ${IP}"
  echo "   (nazwa z HELO/EHLO musi się rozwiązywać; wymagana przez część odbiorców)"
  echo
  echo "5) PTR (rDNS) – NIE w strefie domeny: ustawia się w panelu dostawcy serwera"
  echo "   ${IP}  ->  mail.${DOMAIN}"
  echo "   Bez tego Gmail i Outlook odrzucają pocztę niezależnie od SPF i DKIM."
} > mail-dns.txt
chmod 600 mail-dns.txt
cat mail-dns.txt
echo
echo "Zapisano: ${REMOTE_DIR}/mail-dns.txt"
REMOTE

log "8/8 Kopie zapasowe: hasło, katalog i cron"
# Krok jest idempotentny w całości: hasło powstaje tylko raz (potem .env nie jest ruszany), a wpis
# crona jest za każdym razem **nadpisywany** tą samą treścią. Nadpisanie, a nie dopisanie: wpis
# dopisywany co wdrożenie dałby po pół roku kilkadziesiąt kopii tej samej kopii zapasowej naraz.
#
# Dlaczego cron hosta, a nie zadanie w Celery beat: kopia ma powstać także wtedy, gdy aplikacja
# nie działa – a to jest najczęstszy dzień, w którym się jej szuka. Poza tym skrypt woła
# `docker compose exec` i `docker run`, czyli potrzebuje dostępu do demona Dockera, którego
# kontener aplikacji świadomie nie ma.
"${SSH[@]}" env REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
export DEBIAN_FRONTEND=noninteractive

# gnupg (szyfrowanie paczek) i cron. Obraz Ubuntu na serwerze wirtualnym bywa bez obu.
for pkg in gnupg cron; do
  dpkg -s "$pkg" >/dev/null 2>&1 || apt-get install -y -qq "$pkg" >/dev/null
done
systemctl enable --now cron >/dev/null 2>&1 || true

# Hasło do szyfrowania kopii. Tworzone raz; kolejne wdrożenia go NIE zmieniają – zmiana
# unieważniłaby wszystkie dotychczasowe paczki, bo są zaszyfrowane starym.
if ! grep -qE '^BACKUP_PASSPHRASE=' .env; then
  {
    echo
    echo "# Hasło do szyfrowania kopii zapasowych (gpg AES-256). Wygenerowane przez scripts/deploy.sh."
    echo "# JEGO UTRATA = UTRATA WSZYSTKICH KOPII. Zapisz je w menedżerze haseł organizatora."
    echo "BACKUP_PASSPHRASE=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 48)"
    echo "# Kopia poza serwerem – bez tych czterech wartości scripts/backup.sh robi tylko kopię"
    echo "# lokalną, która ginie razem z maszyną (docs/OPERACJE.md § Kopie zapasowe)."
    echo "# BACKUP_REMOTE_URL="
    echo "# BACKUP_ACCESS_KEY="
    echo "# BACKUP_SECRET_KEY="
    echo "# BACKUP_BUCKET="
  } >> .env
  chmod 600 .env
  NEW_PASSPHRASE=1
else
  NEW_PASSPHRASE=0
fi

mkdir -p /opt/olimpiada-backups
chmod 700 /opt/olimpiada-backups
chmod +x scripts/backup.sh scripts/restore.sh scripts/backup_verify.sh 2>/dev/null || true

# Godziny: kopia o 3:15 (najniższy ruch, po nocnych zadaniach beatu), test odtwarzania w niedzielę
# o 4:40 – po kopii, żeby sprawdzał paczkę z tej samej nocy, i nie w tej samej minucie, bo oba
# przebiegi zajmują dysk i pamięć.
cat > /etc/cron.d/olimpiada-backup <<CRON
# Kopie zapasowe platformy Olimpiady. Plik zakłada scripts/deploy.sh (krok 8/8) – zmiany
# wprowadzaj tam, bo kolejne wdrożenie nadpisze ten plik.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=""
15 3 * * *   root  cd ${REMOTE_DIR} && ./scripts/backup.sh        >> /var/log/olimpiada-backup.log 2>&1
40 4 * * 0   root  cd ${REMOTE_DIR} && ./scripts/backup_verify.sh >> /var/log/olimpiada-backup.log 2>&1
CRON
chmod 644 /etc/cron.d/olimpiada-backup

# Rotacja logu: bez niej /var/log/olimpiada-backup.log rośnie w nieskończoność i po roku jest
# jedynym plikiem, który zapełnił dysk – czyli sam wywołał awarię, o której miał donosić.
cat > /etc/logrotate.d/olimpiada-backup <<'ROTATE'
/var/log/olimpiada-backup.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
ROTATE

echo "cron: $(grep -c '^[0-9]' /etc/cron.d/olimpiada-backup) zadania, katalog kopii: /opt/olimpiada-backups"
if [ "$NEW_PASSPHRASE" = "1" ]; then
  echo
  echo "!!! WYGENEROWANO NOWE BACKUP_PASSPHRASE !!!"
  echo "    Odczytaj je na serwerze i zapisz w menedżerze haseł organizatora:"
  echo "      grep BACKUP_PASSPHRASE ${REMOTE_DIR}/.env"
  echo "    (świadomie NIE wypisujemy go tutaj – ten log bywa logiem GitHub Actions)"
  echo "    Bez tego hasła żadnej kopii nie da się otworzyć. Nikt go nie odzyska."
fi
REMOTE

log "Kontrola domen konkursów"
# Ostrzeżenie, a nie bramka: rozjazd między domenami konkursów a ALLOWED_HOSTS / CSRF /
# EXTRA_DOMAINS nie psuje konkursów, które już działają, więc nie ma powodu zatrzymywać wdrożenia
# — ale jest jedyny moment, w którym ktoś na to patrzy, i jest nim ten log. `|| true`, bo
# niedostępny kontener `web` (np. tuż po restarcie) nie może być powodem czerwonego wdrożenia.
"${SSH[@]}" "cd '$REMOTE_DIR' && docker compose exec -T web python manage.py check_domains --all </dev/null" || true

# Podpowiedź wyłącznie przy włączonym przełączniku – przy wyłączonym ten krok nie wypisuje ani
# jednej linijki, więc log wdrożenia Olimpiady Kwantowej wygląda tak, jak wyglądał. Jedyna rzecz,
# której subdomeny wymagają poza `.env`, jest w DNS-ie, a DNS jest jedynym elementem wdrożenia,
# do którego skrypt nie ma dostępu i którego nikt nie zauważy, dopóki nie zabraknie certyfikatu.
"${SSH[@]}" env REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
FLAG="$(sed -n 's/^PLATFORM_SUBDOMAINS=//p' .env | tail -n 1 | tr -d '\r\042\047' | tr '[:upper:]' '[:lower:]')"
case "$FLAG" in
  1|true|yes|on)
    # `sed`, a nie `grep | cut`: brak linijki SITE_DOMAIN daje pusty wynik, a nie kod 1 – a kod 1
    # pod `set -e` zamieniłby podpowiedź w czerwone wdrożenie.
    DOMAIN="$(sed -n 's/^SITE_DOMAIN=//p' .env | tail -n 1 | tr -d '\r\042\047')"
    echo
    echo "==> Subdomeny platformy: włączone — rekord DNS *.${DOMAIN:-<domena>} musi wskazywać na ten serwer"
    ;;
esac
REMOTE

log "Gotowe: https://${SITE_DOMAIN:-<domena z .env>}/  (panel: /coordinator/, CMS: /cms/, admin: /admin/)"
