#!/usr/bin/env bash
# Strona „Prace techniczne” – włączanie, wyłączanie i stan (docs/OPERACJE.md § 20).
#
# Użycie (na serwerze, w /opt/olimpiada):
#   scripts/maintenance.sh on  [--message "Aktualizacja bazy danych"] [--until "14:30" | --in 15]
#   scripts/maintenance.sh off
#   scripts/maintenance.sh status
#   scripts/maintenance.sh sync      # tylko kopia deploy/maintenance/ -> maintenance/page/ (deploy.sh, krok 4/8)
#
# Jak to działa: Caddy (fragment `(maintenance)` w deploy/Caddyfile) przy KAŻDYM żądaniu sprawdza,
# czy istnieje plik /srv/maintenance/on (= <REMOTE_DIR>/maintenance/on na hoście). Jest – odpowiada
# 503 ze stroną prac technicznych (HTML albo JSON dla /status.json, /healthz/, /api/*). Przełączenie
# to więc utworzenie albo skasowanie pliku: bez przeładowania Caddy'ego i bez restartu czegokolwiek.
# Ten skrypt po każdej zmianie sprawdza **z wnętrza kontenera proxy**, że Caddy widzi to samo co
# host – montaż katalogu, który ktoś skasował i utworzył od nowa, wyglądałby z hosta dobrze, a proxy
# widziałoby stary, pusty katalog (wtedy: docker compose up -d --force-recreate proxy).
#
# W czasie przerwy operator wchodzi na stronę z przepustką (MAINTENANCE_BYPASS_TOKEN z .env):
#   curl -H "X-Maintenance-Bypass: <token>" https://<domena>/status.json
#   przeglądarka: https://<domena>/__maintenance/bypass?token=<token>   (ciasteczko na 12 h)
#
# --until "HH:MM"  – planowany koniec, czas polski (pokazywany na stronie po polsku i angielsku).
# --in MINUTY      – to samo, liczone od teraz w strefie Europe/Warsaw (bez bazy stref czasowych,
#                    np. w Git Bashu, godzina jest pomijana, a nie zgadywana w UTC).
# --message "…"    – jedno zdanie dla uczestników (do 300 znaków; znaki HTML i klamry są zamieniane
#                    na encje, więc treść nie może niczego wstrzyknąć ani do strony, ani do szablonu).
# Zmienne: MAINTENANCE_DIR (domyślnie z .env albo ./maintenance), MAINTENANCE_CHECK_URL (adres do
# kontroli HTTP, domyślnie https://<SITE_DOMAIN>; przy SITE_DOMAIN=localhost kontrola jest pomijana).
set -euo pipefail
# Git Bash (lokalne próby): bez tego ścieżki kontenera w argumentach `docker compose exec` zamieniają
# się w ścieżki Windows. Na Linuksie zmienna nic nie robi.
export MSYS_NO_PATHCONV=1

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

die() { printf 'BŁĄD: %s\n' "$*" >&2; exit 1; }
warn() { printf 'UWAGA: %s\n' "$*" >&2; }
info() { printf '    %s\n' "$*"; }
# .env czytany sed-em, a nie `source` – ten sam powód co w scripts/render_caddyfile.sh.
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r\042\047' || true; }

MAINT_DIR="${MAINTENANCE_DIR:-$(env_get MAINTENANCE_DIR)}"
MAINT_DIR="${MAINT_DIR:-./maintenance}"
case "$MAINT_DIR" in /*) ;; *) MAINT_DIR="$REPO_DIR/${MAINT_DIR#./}" ;; esac
PAGE_SRC="$REPO_DIR/deploy/maintenance"
FLAG="$MAINT_DIR/on"
INFO="$MAINT_DIR/info.html"
SITE_DOMAIN="$(env_get SITE_DOMAIN)"
TOKEN="$(env_get MAINTENANCE_BYPASS_TOKEN)"
CHECK_URL="${MAINTENANCE_CHECK_URL:-}"
if [ -z "$CHECK_URL" ] && [ -n "$SITE_DOMAIN" ] && [ "$SITE_DOMAIN" != "localhost" ]; then
  CHECK_URL="https://$SITE_DOMAIN"
fi

usage() { awk 'NR > 1 && /^set -euo/ { exit } NR > 1 { print }' "${BASH_SOURCE[0]}"; }

CMD="${1:-}"
[ -n "$CMD" ] || { usage; exit 2; }
shift
MESSAGE=""
UNTIL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --message) [ $# -ge 2 ] || die "--message wymaga treści"; MESSAGE="$2"; shift ;;
    --until) [ $# -ge 2 ] || die "--until wymaga godziny HH:MM"; UNTIL="$2"; shift ;;
    --in)
      [ $# -ge 2 ] && [[ $2 =~ ^[0-9]{1,4}$ ]] || die "--in wymaga liczby minut"
      # Strefa znana systemowi daje CET/CEST; nieznana – „GMT”/„Europe”, czyli po cichu UTC.
      case "$(TZ=Europe/Warsaw date +%Z 2>/dev/null)" in
        CET|CEST) UNTIL="$(TZ=Europe/Warsaw date -d "+$2 min" +%H:%M)" ;;
        *) warn "brak strefy Europe/Warsaw w systemie – planowana godzina końca pominięta" ;;
      esac
      shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "nieznany argument: $1 (pomoc: --help)" ;;
  esac
  shift
done

html_escape() {
  # Encje dla znaków HTML i klamer: info.html przechodzi przez `templates` Caddy'ego (`include`
  # wykonuje dołączany plik jako szablon), więc „{{” w komunikacie byłoby wywołaniem szablonu.
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g' \
      -e "s/'/\&#39;/g" -e 's/{/\&#123;/g' -e 's/}/\&#125;/g'
}

sync_page() {
  [ -f "$PAGE_SRC/index.html" ] || die "brak $PAGE_SRC/index.html"
  mkdir -p "$MAINT_DIR/page"
  # `cat >`, a nie `cp`/`mv`: zapis do istniejącego pliku zostawia ten sam i-węzeł i ten sam
  # katalog, więc działający kontener proxy widzi nową treść bez restartu.
  local f
  for f in "$PAGE_SRC"/*; do
    [ -f "$f" ] || continue
    cat "$f" > "$MAINT_DIR/page/$(basename "$f")"
  done
  chmod -R a+rX "$MAINT_DIR"
}

proxy_running() { [ -n "$(docker compose ps -q --status running proxy 2>/dev/null)" ]; }

proxy_sees_flag() {  # kod 0 = proxy widzi /srv/maintenance/on
  docker compose exec -T proxy test -f /srv/maintenance/on </dev/null
}

check_proxy() {  # check_proxy on|off – czy Caddy widzi ten sam stan co host
  local expect="$1" seen
  if ! command -v docker >/dev/null 2>&1 || ! proxy_running; then
    warn "kontener proxy nie działa – nie sprawdzę, co widzi Caddy (flaga na hoście: $expect)"
    return 0
  fi
  if proxy_sees_flag; then seen=on; else seen=off; fi
  if [ "$seen" != "$expect" ]; then
    printf '\n!!! Caddy NIE widzi zmiany: na hoście „%s”, w kontenerze proxy „%s”.\n' "$expect" "$seen" >&2
    printf '!!! Najpewniej katalog %s został skasowany i utworzony od nowa po starcie proxy.\n' "$MAINT_DIR" >&2
    printf '!!! Naprawa (kilka sekund bez HTTPS): docker compose up -d --force-recreate proxy\n\n' >&2
    return 1
  fi
  docker compose exec -T proxy test -f /srv/maintenance/page/index.html </dev/null \
    || warn "proxy nie widzi /srv/maintenance/page/index.html – strona zastępcza będzie zwykłym tekstem (scripts/maintenance.sh sync)"
  info "proxy: flaga ${seen} (Caddy sprawdza ją przy każdym żądaniu – przeładowanie niepotrzebne)"
}

http_probe() {
  [ -n "$CHECK_URL" ] || { info "kontrola HTTP pominięta (SITE_DOMAIN=${SITE_DOMAIN:-brak}; MAINTENANCE_CHECK_URL ustawia adres)"; return 0; }
  command -v curl >/dev/null 2>&1 || { info "kontrola HTTP pominięta (brak curl)"; return 0; }
  local code_html code_json code_bypass
  code_html="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$CHECK_URL/" || true)"
  code_json="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$CHECK_URL/status.json" || true)"
  info "HTTP $CHECK_URL/ -> $code_html, /status.json -> $code_json"
  if [ ${#TOKEN} -ge 32 ]; then
    code_bypass="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "X-Maintenance-Bypass: $TOKEN" "$CHECK_URL/status.json" || true)"
    info "HTTP z przepustką operatora: /status.json -> $code_bypass"
  fi
}

write_flag() {
  local since_epoch="$1" tmp
  tmp="$(mktemp "$MAINT_DIR/.on.XXXXXX")"
  {
    echo "since_epoch=$since_epoch"
    echo "since=$(date -d "@${since_epoch%.*}" -Iseconds 2>/dev/null || date -Iseconds)"
    echo "by=$(id -un 2>/dev/null || echo '?')@$(hostname 2>/dev/null || echo '?')"
    echo "until=$UNTIL"
    echo "message=$(printf '%s' "$MESSAGE" | tr -d '\r\n')"
  } > "$tmp"
  chmod 644 "$tmp"
  # rename w obrębie katalogu jest atomowe: Caddy widzi flagę całą albo wcale.
  mv -f "$tmp" "$FLAG"
}

write_info() {
  if [ -z "$MESSAGE" ] && [ -z "$UNTIL" ]; then
    rm -f "$INFO"
    return 0
  fi
  local tmp msg
  tmp="$(mktemp "$MAINT_DIR/.info.XXXXXX")"
  {
    echo '<div class="info">'
    if [ -n "$UNTIL" ]; then
      echo "<p><strong>Planowany koniec: ok. $UNTIL</strong> (czas polski).<br><span lang=\"en\">Expected back at about $UNTIL (Polish time).</span></p>"
    fi
    if [ -n "$MESSAGE" ]; then
      msg="$(printf '%s' "$MESSAGE" | tr -d '\r' | tr '\n' ' ' | html_escape)"
      echo "<p>$msg</p>"
    fi
    echo '</div>'
  } > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$INFO"
}

flag_field() { sed -n "s/^$1=//p" "$FLAG" 2>/dev/null | head -1; }

case "$CMD" in
  on)
    if [ -n "$UNTIL" ] && ! [[ $UNTIL =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
      die "--until: oczekuję godziny HH:MM (czas polski), dostałem „$UNTIL”"
    fi
    [ ${#MESSAGE} -le 300 ] || die "--message: najwyżej 300 znaków (jest ${#MESSAGE})"
    sync_page
    since="$(date +%s.%N)"
    if [ -f "$FLAG" ]; then
      old="$(flag_field since_epoch)"
      since="${old:-$since}"
      echo "==> Prace techniczne były już włączone (od $(flag_field since)) – aktualizuję komunikat"
    fi
    write_info
    write_flag "$since"
    echo "==> Prace techniczne: WŁĄCZONE ($(date -Iseconds))"
    [ -n "$UNTIL" ] && info "planowany koniec: $UNTIL"
    [ -n "$MESSAGE" ] && info "komunikat: $MESSAGE"
    [ ${#TOKEN} -ge 32 ] || warn "MAINTENANCE_BYPASS_TOKEN w .env pusty albo krótszy niż 32 znaki – przepustka operatora nie zadziała"
    if ! check_proxy on; then
      # Flaga, której proxy nie widzi, nie zasłania niczego teraz, a zasłoniłaby serwis znienacka
      # po najbliższym odtworzeniu proxy – cofamy ją, stan zostaje spójny: wyłączone.
      rm -f "$FLAG" "$INFO"
      printf '!!! Strona prac technicznych NIE została włączona (flaga cofnięta).\n' >&2
      exit 2
    fi
    http_probe
    info "wyłączenie: scripts/maintenance.sh off"
    ;;
  off)
    was_on=0
    [ -f "$FLAG" ] && was_on=1
    rm -f "$FLAG" "$INFO"
    if [ "$was_on" = "1" ]; then
      echo "==> Prace techniczne: WYŁĄCZONE ($(date -Iseconds))"
    else
      echo "==> Prace techniczne były wyłączone – bez zmian"
    fi
    check_proxy off || exit 2
    http_probe
    ;;
  status)
    if [ -f "$FLAG" ]; then
      since_epoch="$(flag_field since_epoch)"
      mins=""
      [ -n "$since_epoch" ] && mins=" – $(( ( $(date +%s) - ${since_epoch%.*} ) / 60 )) min"
      echo "==> Prace techniczne: WŁĄCZONE od $(flag_field since)${mins}"
      info "kto: $(flag_field by)"
      [ -n "$(flag_field until)" ] && info "planowany koniec: $(flag_field until)"
      [ -n "$(flag_field message)" ] && info "komunikat: $(flag_field message)"
      expect=on
    else
      echo "==> Prace techniczne: wyłączone (strona zastępcza działa tylko przy awarii web – 502/503/504)"
      expect=off
    fi
    info "katalog: $MAINT_DIR (strona: $([ -f "$MAINT_DIR/page/index.html" ] && echo jest || echo 'BRAK – scripts/maintenance.sh sync'))"
    if [ ${#TOKEN} -ge 32 ]; then
      info "przepustka: MAINTENANCE_BYPASS_TOKEN ustawiony (${#TOKEN} znaków) – nagłówek X-Maintenance-Bypass"
      [ -n "$CHECK_URL" ] && info "przeglądarka: $CHECK_URL/__maintenance/bypass?token=<token z .env>"
    else
      warn "MAINTENANCE_BYPASS_TOKEN w .env pusty albo krótszy niż 32 znaki – przepustka nie działa"
    fi
    check_proxy "$expect" || exit 2
    http_probe
    ;;
  sync)
    sync_page
    echo "==> Strona prac technicznych skopiowana: $PAGE_SRC -> $MAINT_DIR/page"
    ;;
  *)
    usage
    exit 2
    ;;
esac
