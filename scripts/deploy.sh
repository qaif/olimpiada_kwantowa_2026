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
#             MAKE_EDITION_CURRENT=1 (edycja „I edycja 2026/2027” jako bieżąca), APP_VERSION.
set -euo pipefail

TARGET="${1:?użycie: scripts/deploy.sh user@host}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/olimpiada_deploy}"
REMOTE_DIR="${REMOTE_DIR:-/opt/olimpiada}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$TARGET")
APP_VERSION="${APP_VERSION:-$(git describe --tags --always)}"

log() { printf '\n==> %s\n' "$*"; }

log "1/6 Docker na serwerze"
"${SSH[@]}" bash -s <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg ufw >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
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

log "2/6 Kod: git archive HEAD -> $REMOTE_DIR"
"${SSH[@]}" "mkdir -p '$REMOTE_DIR' && find '$REMOTE_DIR' -mindepth 1 -maxdepth 1 ! -name .env ! -name 'e2e' -exec rm -rf {} +"
git archive --format=tar HEAD | "${SSH[@]}" "tar -x -C '$REMOTE_DIR'"

log "3/6 .env (tworzony tylko przy pierwszym wdrożeniu)"
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
DJANGO_ALLOWED_HOSTS=$SITE_DOMAIN,www.$SITE_DOMAIN,web
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
EMAIL_URL=smtp://localhost:25
DEFAULT_FROM_EMAIL=noreply@$SITE_DOMAIN
EOF
  chmod 600 .env
  echo ".env utworzony"
else
  sed -i "s/^APP_VERSION=.*/APP_VERSION=$APP_VERSION/" .env
  echo ".env istnieje – zachowany (zaktualizowano APP_VERSION)"
fi
REMOTE

log "4/6 Build i start usług"
"${SSH[@]}" "cd '$REMOTE_DIR' && docker compose build --pull web && docker compose up -d --remove-orphans db redis minio minio-init clamav web worker beat proxy"

log "5/6 Oczekiwanie na healthy"
"${SSH[@]}" bash -s <<REMOTE
set -euo pipefail
cd '$REMOTE_DIR'
for i in \$(seq 1 60); do
  s=\$(docker compose ps --format '{{.Service}}={{.Health}}' | tr '\n' ' ')
  echo "\$s" | grep -q 'web=healthy' && echo "\$s" | grep -q 'proxy=healthy' && { echo "\$s"; break; }
  sleep 5
done
REMOTE

log "6/6 Seedy treści i konto koordynatora"
"${SSH[@]}" env COORDINATOR_EMAIL="${COORDINATOR_EMAIL:-}" COORDINATOR_PASSWORD="${COORDINATOR_PASSWORD:-}" MAKE_EDITION_CURRENT="${MAKE_EDITION_CURRENT:-0}" REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
dc() { docker compose exec -T web "$@"; }
dc python manage.py seed_cms
dc python manage.py seed_regulamin
dc python manage.py seed_legacy_content
if [ "$MAKE_EDITION_CURRENT" = "1" ]; then
  dc python manage.py seed_edition_kwantowa --make-current
else
  dc python manage.py seed_edition_kwantowa
fi
if [ -n "$COORDINATOR_EMAIL" ] && [ -n "$COORDINATOR_PASSWORD" ]; then
  docker compose exec -T -e COORDINATOR_EMAIL="$COORDINATOR_EMAIL" -e COORDINATOR_PASSWORD="$COORDINATOR_PASSWORD" web \
    python manage.py bootstrap_coordinator
fi
docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
REMOTE

log "Gotowe: https://${SITE_DOMAIN:-<domena z .env>}/  (panel: /coordinator/, CMS: /cms/, admin: /admin/)"
