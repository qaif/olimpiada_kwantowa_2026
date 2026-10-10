#!/usr/bin/env bash
# Własne Jitsi Meet (meet.<domena>) obok portalu – osobny projekt compose na tym samym serwerze.
#
# Użycie (po scripts/deploy.sh, z katalogu repo):
#   scripts/deploy_jitsi.sh root@169.58.242.197
#   SSH_KEY=~/.ssh/olimpiada_deploy scripts/deploy_jitsi.sh root@olimpiadakwantowa.pl
#
# Co robi: kopiuje deploy/jitsi/ na serwer, tworzy jednorazowo jitsi/.env z sekretami (nie nadpisuje),
# przekazuje portalowi sekret przepustek (niżej), otwiera UDP 10000 w ufw, startuje kontenery
# jitsi/{web,prosody,jicofo,jvb} i restartuje Caddy, żeby wczytał blok `meet.<domena>` z deploy/Caddyfile.
# Certyfikat Let's Encrypt wystawi się sam, gdy tylko istnieje rekord DNS A `meet.<domena>` -> adres
# serwera (do dodania w panelu domeny).
#
# Przepustki platformy (v0.39.0, docs/OPERACJE.md § 25) – Jitsi wpuszcza wyłącznie z tokenem JWT
# podpisanym sekretem, który zna portal. Sekret ma JEDNO źródło: <REMOTE_DIR>/.env portalu
# (`JITSI_JWT_APP_SECRET`, 64 znaki [A-Za-z0-9], tworzony raz – kolejne przebiegi go nie ruszają);
# jitsi/.env dostaje jego kopię (`JWT_APP_SECRET`) przy KAŻDYM przebiegu, więc rozjazd obu plików
# naprawia się sam. Wartość nigdy nie jest wypisywana – porównania idą po SHA-256.
#
# Kolejność, która nikogo nie zamyka w połowie zawodów:
#   1. portal z sekretem: jeśli web/worker/beat nie widzą jeszcze tego sekretu (świeżo wygenerowany
#      albo .env zmieniony ręcznie), skrypt odtwarza te trzy usługi (`up -d --no-deps`) – kilka
#      sekund przerwy zasłania strona zastępcza proxy – i sprawdza, że `web` widzi ten sam skrót.
#      Portal od tej chwili wystawia przepustki, które OTWARTE jeszcze Jitsi po prostu ignoruje,
#   2. dopiero potem Jitsi przechodzi na `ENABLE_AUTH=1`. Gdy krok 1 się nie uda, skrypt kończy się
#      błędem PRZED dotknięciem Jitsi – pokoje zostają otwarte, nikt nie traci wejścia.
# Wycofanie: `ENABLE_AUTH=0` i `ENABLE_AUTO_OWNER=1` w jitsi/.env, potem ten skrypt (albo
# `docker compose … up -d` w katalogu jitsi/) – opis w docs/OPERACJE.md § 25.7.
set -euo pipefail

TARGET="${1:?użycie: scripts/deploy_jitsi.sh user@host}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/olimpiada_deploy}"
REMOTE_DIR="${REMOTE_DIR:-/opt/olimpiada}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$TARGET")

log() { printf '\n==> %s\n' "$*"; }

log "1/5 Pliki Jitsi -> $REMOTE_DIR/jitsi (oraz aktualny deploy/Caddyfile z blokiem meet.<domena>)"
"${SSH[@]}" "mkdir -p '$REMOTE_DIR/jitsi' '$REMOTE_DIR/deploy'"
tar -C deploy/jitsi -cf - . | "${SSH[@]}" "tar -x -C '$REMOTE_DIR/jitsi'"
tar -C deploy -cf - Caddyfile | "${SSH[@]}" "tar -x -C '$REMOTE_DIR/deploy'"

log "2/5 jitsi/.env (tworzony tylko raz), sekret przepustek w obu .env i zapora"
"${SSH[@]}" env REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
gen() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }
# Odczyt jak w scripts/render_caddyfile.sh: ostatnie wystąpienie, bez cudzysłowów i CR (compose czyta
# .env tak samo).
envv() { sed -n "s/^$1=//p" "$2" | tail -n 1 | tr -d '\r\042\047'; }
SITE_DOMAIN=$(envv SITE_DOMAIN .env)
: "${SITE_DOMAIN:?brak SITE_DOMAIN w $REMOTE_DIR/.env}"

# Sekret przepustek w .env PORTALU – wzorzec MAINTENANCE_BYPASS_TOKEN z scripts/deploy.sh: tworzony
# raz, istniejącej wartości skrypt nie rusza, pusta linijka (np. z kopii .env.example) jest zastępowana.
if ! grep -qE '^JITSI_JWT_APP_SECRET=.' .env; then
  sed -i '/^JITSI_JWT_APP_SECRET=$/d' .env
  {
    echo
    echo "# Sekret przepustek do własnego Jitsi (JWT HS256, v0.39.0). Ten sam co JWT_APP_SECRET w jitsi/.env –"
    echo "# przepisuje go scripts/deploy_jitsi.sh. Zmiana = rotacja: docs/OPERACJE.md § 25.5."
    echo "JITSI_JWT_APP_SECRET=$(gen 64)"
  } >> .env
  chmod 600 .env
  echo "portal: wygenerowano JITSI_JWT_APP_SECRET (wartość nie jest wypisywana)"
fi
SECRET=$(envv JITSI_JWT_APP_SECRET .env)
# Wartość wpisaną ręcznie sprawdzamy od razu: krótsza niż 32 znaki wyłącza przepustki po stronie
# portalu (competitions.W001), a znak spoza [A-Za-z0-9] rozbiłby linię .env Jitsi.
printf '%s' "$SECRET" | grep -Eq '^[A-Za-z0-9]{48,}$' \
  || { echo "BŁĄD: JITSI_JWT_APP_SECRET w $REMOTE_DIR/.env – wyłącznie [A-Za-z0-9], co najmniej 48 znaków"; exit 1; }
APP_ID=$(envv JITSI_JWT_APP_ID .env)
APP_ID=${APP_ID:-olimpiada}

cd jitsi
if [ ! -f .env ]; then
  PUBLIC_IP=$(curl -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
  cat > .env <<EOF
PUBLIC_URL=https://meet.$SITE_DOMAIN
JVB_ADVERTISE_IPS=$PUBLIC_IP
JVB_PORT=10000
TZ=Europe/Warsaw
XMPP_DOMAIN=meet.jitsi
JICOFO_AUTH_PASSWORD=$(gen 32)
JVB_AUTH_PASSWORD=$(gen 32)
EOF
  chmod 600 .env
  echo "utworzono jitsi/.env (PUBLIC_URL=https://meet.$SITE_DOMAIN, JVB_ADVERTISE_IPS=$PUBLIC_IP)"
else
  echo "jitsi/.env istnieje – zachowany"
fi

# set_kv KLUCZ WARTOŚĆ – ustawia wpis (zastępuje albo dopisuje). Wartość przez plik tymczasowy
# i awk, a nie `sed -i s/…/$WARTOŚĆ/`: sekret nie trafia do wiersza poleceń (lista procesów), a znak
# specjalny sed-a niczego by nie rozbił.
set_kv() {
  local tmp
  tmp=$(mktemp)
  printf '%s' "$2" > "$tmp.val"
  awk -v key="$1" -v valfile="$tmp.val" '
    BEGIN { getline val < valfile; done = 0 }
    index($0, key "=") == 1 { if (!done) { print key "=" val; done = 1 }; next }
    { print }
    END { if (!done) print key "=" val }
  ' .env > "$tmp"
  cat "$tmp" > .env
  rm -f "$tmp" "$tmp.val"
  chmod 600 .env
}
# add_kv KLUCZ WARTOŚĆ – dopisuje wyłącznie, gdy wpisu nie ma (decyzje operatora zostają nietknięte).
add_kv() { grep -qE "^$1=" .env || { printf '%s=%s\n' "$1" "$2" >> .env; chmod 600 .env; }; }

# Kopia sekretu portalu – przy każdym przebiegu, po skrótach, bez wypisywania wartości.
if [ "$(printf '%s' "$(envv JWT_APP_SECRET .env)" | sha256sum)" != "$(printf '%s' "$SECRET" | sha256sum)" ]; then
  set_kv JWT_APP_SECRET "$SECRET"
  echo "jitsi/.env: JWT_APP_SECRET zsynchronizowany z portalem"
fi
set_kv JWT_APP_ID "$APP_ID"
add_kv JWT_ACCEPTED_AUDIENCES jitsi
add_kv ENABLE_AUTO_OWNER 0
# Obrazy przypięte do sprawdzonego wydania (docs/OPERACJE.md § 25.2). Pływający `stable` z .env
# sprzed v0.39.0 podmieniamy; wersję wpisaną świadomie przez operatora zostawiamy.
case "$(envv JITSI_IMAGE_VERSION .env)" in
  ''|stable) set_kv JITSI_IMAGE_VERSION stable-11031; echo "jitsi/.env: JITSI_IMAGE_VERSION=stable-11031" ;;
esac
# Sieć proxy ⇄ jitsi-web (audyt bezpieczeństwa 10.10.2026, S20): `jitsi-web` stoi w sieci `<projekt>_meet`
# portalu (tylko z proxy), a nie w `<projekt>_edge` (obok web, monitora i poczty). Sieć zakłada compose
# portalu razem z `proxy` – brak = portal sprzed tej zmiany, najpierw scripts/deploy.sh. Dawny wpis
# EDGE_NETWORK w jitsi/.env jest martwy (deploy/jitsi/docker-compose.jitsi.yml go nie czyta).
MEET=$(docker network ls --format '{{.Name}}' | grep -E '_meet$' | head -1 || true)
[ -n "$MEET" ] || { echo "BŁĄD: brak sieci *_meet portalu – najpierw wdróż portal (scripts/deploy.sh), potem Jitsi"; exit 1; }
set_kv PROXY_MEET_NETWORK "$MEET"

if command -v ufw >/dev/null 2>&1; then
  ufw allow 10000/udp >/dev/null && echo "ufw: 10000/udp otwarty"
fi
REMOTE

log "3/5 Portal widzi sekret przepustek (web/worker/beat) – przed zamknięciem Jitsi"
"${SSH[@]}" env REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
envv() { sed -n "s/^$1=//p" "$2" | tail -n 1 | tr -d '\r\042\047'; }
# `printf '%s'`, a nie potok z `sed`: bez końcowego znaku nowej linii – tak jak widzi wartość Python.
want=$(printf '%s' "$(envv JITSI_JWT_APP_SECRET .env)" | sha256sum | cut -d' ' -f1)
# Skrót tego, co widzi DZIAŁAJĄCY web. Sam skrót opuszcza kontener – nigdy wartość.
seen() {
  docker compose exec -T web python -c \
    'import hashlib,os;print(hashlib.sha256(os.environ.get("JITSI_JWT_APP_SECRET","").encode()).hexdigest())' \
    </dev/null 2>/dev/null | tr -d '\r' || true
}
if [ "$(seen)" = "$want" ]; then
  echo "portal: web widzi aktualny sekret – bez restartu"
else
  echo "portal: web nie widzi jeszcze sekretu – odtwarzam web, worker, beat (kilka sekund przerwy)"
  docker compose up -d --no-deps web worker beat </dev/null
  for _ in $(seq 1 60); do
    [ "$(seen)" = "$want" ] && break
    sleep 2
  done
  [ "$(seen)" = "$want" ] || {
    echo "BŁĄD: web po odtworzeniu nadal nie widzi JITSI_JWT_APP_SECRET – Jitsi zostaje bez zmian (otwarte)."
    echo "      Sprawdź: docker compose ps web; docker compose logs --tail 50 web"
    exit 1
  }
  echo "portal: web widzi aktualny sekret"
fi
# Dopiero teraz wolno zamknąć Jitsi. Wpis dopisujemy tylko, gdy go nie ma: `ENABLE_AUTH=0` wpisane
# przez operatora (wycofanie) zostaje.
cd jitsi
grep -qE '^ENABLE_AUTH=' .env || { echo "ENABLE_AUTH=1" >> .env; chmod 600 .env; echo "jitsi/.env: ENABLE_AUTH=1"; }
echo "jitsi: ENABLE_AUTH=$(sed -n 's/^ENABLE_AUTH=//p' .env | tail -n 1)"
REMOTE

log "4/5 Start kontenerów Jitsi i przeładowanie Caddy"
"${SSH[@]}" "cd '$REMOTE_DIR/jitsi' && docker compose -p olimpiada-jitsi --env-file .env -f docker-compose.jitsi.yml pull -q && docker compose -p olimpiada-jitsi --env-file .env -f docker-compose.jitsi.yml up -d --remove-orphans && cd '$REMOTE_DIR' && docker compose restart proxy >/dev/null && docker compose -p olimpiada-jitsi ps --format '{{.Service}} {{.Status}}'"

log "5/5 DNS i sprawdzenie"
SITE_DOMAIN=$("${SSH[@]}" "grep -E '^SITE_DOMAIN=' '$REMOTE_DIR/.env' | cut -d= -f2-")
IP=$("${SSH[@]}" "grep -E '^JVB_ADVERTISE_IPS=' '$REMOTE_DIR/jitsi/.env' | cut -d= -f2-")
cat <<EOF
Rekord DNS do dodania u operatora strefy (jeśli jeszcze go nie ma):
  A   meet.$SITE_DOMAIN   ->   $IP
Po propagacji Caddy sam pobierze certyfikat; sprawdź: https://meet.$SITE_DOMAIN/
W panelu koordynatora ustaw w etapie z rozmowami dostawcę wideo „własna instancja” z adresem https://meet.$SITE_DOMAIN/.
Sprawdzenie przepustek (tylko odczyt): docs/OPERACJE.md § 25.4 – konfiguracja Prosody i wejście bez tokenu.
EOF
