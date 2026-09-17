#!/usr/bin/env bash
# Własne Jitsi Meet (meet.<domena>) obok portalu – osobny projekt compose na tym samym serwerze.
#
# Użycie (po scripts/deploy.sh, z katalogu repo):
#   scripts/deploy_jitsi.sh root@169.58.242.197
#   SSH_KEY=~/.ssh/olimpiada_deploy scripts/deploy_jitsi.sh root@olimpiadakwantowa.pl
#
# Co robi: kopiuje deploy/jitsi/ na serwer, tworzy jednorazowo jitsi/.env z sekretami (nie nadpisuje),
# otwiera UDP 10000 w ufw, startuje kontenery jitsi/{web,prosody,jicofo,jvb} i restartuje Caddy,
# żeby wczytał blok `meet.<domena>` z deploy/Caddyfile. Certyfikat Let's Encrypt wystawi się sam,
# gdy tylko istnieje rekord DNS A `meet.<domena>` -> adres serwera (do dodania w panelu domeny).
set -euo pipefail

TARGET="${1:?użycie: scripts/deploy_jitsi.sh user@host}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/olimpiada_deploy}"
REMOTE_DIR="${REMOTE_DIR:-/opt/olimpiada}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$TARGET")

log() { printf '\n==> %s\n' "$*"; }

log "1/4 Pliki Jitsi -> $REMOTE_DIR/jitsi (oraz aktualny deploy/Caddyfile z blokiem meet.<domena>)"
"${SSH[@]}" "mkdir -p '$REMOTE_DIR/jitsi' '$REMOTE_DIR/deploy'"
tar -C deploy/jitsi -cf - . | "${SSH[@]}" "tar -x -C '$REMOTE_DIR/jitsi'"
tar -C deploy -cf - Caddyfile | "${SSH[@]}" "tar -x -C '$REMOTE_DIR/deploy'"

log "2/4 jitsi/.env (tworzony tylko raz) i zapora"
"${SSH[@]}" env REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR/jitsi"
gen() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }
SITE_DOMAIN=$(grep -E '^SITE_DOMAIN=' ../.env | cut -d= -f2-)
: "${SITE_DOMAIN:?brak SITE_DOMAIN w $REMOTE_DIR/.env}"
if [ ! -f .env ]; then
  PUBLIC_IP=$(curl -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
  EDGE=$(docker network ls --format '{{.Name}}' | grep -E '_edge$' | head -1)
  cat > .env <<EOF
PUBLIC_URL=https://meet.$SITE_DOMAIN
JVB_ADVERTISE_IPS=$PUBLIC_IP
JVB_PORT=10000
TZ=Europe/Warsaw
XMPP_DOMAIN=meet.jitsi
JICOFO_AUTH_PASSWORD=$(gen 32)
JVB_AUTH_PASSWORD=$(gen 32)
EDGE_NETWORK=${EDGE:-olimpiada_edge}
JITSI_IMAGE_VERSION=stable
EOF
  chmod 600 .env
  echo "utworzono jitsi/.env (PUBLIC_URL=https://meet.$SITE_DOMAIN, JVB_ADVERTISE_IPS=$PUBLIC_IP)"
else
  echo "jitsi/.env istnieje – zachowany"
fi
if command -v ufw >/dev/null 2>&1; then
  ufw allow 10000/udp >/dev/null && echo "ufw: 10000/udp otwarty"
fi
REMOTE

log "3/4 Start kontenerów Jitsi i przeładowanie Caddy"
"${SSH[@]}" "cd '$REMOTE_DIR/jitsi' && docker compose -p olimpiada-jitsi --env-file .env -f docker-compose.jitsi.yml pull -q && docker compose -p olimpiada-jitsi --env-file .env -f docker-compose.jitsi.yml up -d --remove-orphans && cd '$REMOTE_DIR' && docker compose restart proxy >/dev/null && docker compose -p olimpiada-jitsi ps --format '{{.Service}} {{.Status}}'"

log "4/4 DNS"
SITE_DOMAIN=$("${SSH[@]}" "grep -E '^SITE_DOMAIN=' '$REMOTE_DIR/.env' | cut -d= -f2-")
IP=$("${SSH[@]}" "grep -E '^JVB_ADVERTISE_IPS=' '$REMOTE_DIR/jitsi/.env' | cut -d= -f2-")
cat <<EOF
Rekord DNS do dodania u operatora strefy (jeśli jeszcze go nie ma):
  A   meet.$SITE_DOMAIN   ->   $IP
Po propagacji Caddy sam pobierze certyfikat; sprawdź: https://meet.$SITE_DOMAIN/
W panelu koordynatora ustaw w etapie z rozmowami dostawcę wideo „własny serwer” z adresem https://meet.$SITE_DOMAIN/.
EOF
