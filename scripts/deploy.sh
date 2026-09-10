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
#             MAIL_PUBLIC_IP (adres do rekordu SPF, gdy serwer wychodzi przez inny IP niż własny).
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

log "1/7 Docker na serwerze"
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

log "2/7 Kod: git archive HEAD -> $REMOTE_DIR"
"${SSH[@]}" "mkdir -p '$REMOTE_DIR' && find '$REMOTE_DIR' -mindepth 1 -maxdepth 1 ! -name .env ! -name 'e2e' -exec rm -rf {} +"
git archive --format=tar HEAD | "${SSH[@]}" "tar -x -C '$REMOTE_DIR'"

log "3/7 .env (tworzony tylko przy pierwszym wdrożeniu)"
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

log "4/7 Build i start usług"
"${SSH[@]}" "cd '$REMOTE_DIR' && docker compose build --pull web && docker compose up -d --remove-orphans db redis minio minio-init clamav mail web worker beat proxy"

log "5/7 Oczekiwanie na healthy"
"${SSH[@]}" bash -s <<REMOTE
set -euo pipefail
cd '$REMOTE_DIR'
for i in \$(seq 1 60); do
  s=\$(docker compose ps --format '{{.Service}}={{.Health}}' | tr '\n' ' ')
  echo "\$s" | grep -q 'web=healthy' && echo "\$s" | grep -q 'proxy=healthy' && { echo "\$s"; break; }
  sleep 5
done
REMOTE

log "6/7 Seedy treści i konto koordynatora"
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

log "7/7 DNS dla poczty (SPF / DKIM / DMARC / PTR)"
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

log "Gotowe: https://${SITE_DOMAIN:-<domena z .env>}/  (panel: /coordinator/, CMS: /cms/, admin: /admin/)"
