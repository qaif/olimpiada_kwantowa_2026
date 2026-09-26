#!/usr/bin/env bash
# Przełącznik serwisu publicznego: Wagtail (`web`) ⇄ django CMS (`djcms`) – docs/tasks/DJ-02.md § 10.2.
#
# Uruchamiany NA SERWERZE, w katalogu instalacji (/opt/olimpiada):
#   bash scripts/djcms_switch.sh on       # strony publiczne wszystkich konkursów z djcms (DJCMS_PRIMARY=1)
#   bash scripts/djcms_switch.sh off      # z powrotem Wagtail (DJCMS_PRIMARY=0) – droga ratunkowa, ~2 s
#   bash scripts/djcms_switch.sh status   # .env, wygenerowany plik, konfiguracja w kontenerze, zamrożenie
#   bash scripts/djcms_switch.sh check    # sama kontrola dymna dla trybu z .env (woła ją deploy.sh)
#
# Co robi `on`/`off` (nic poza tym – bez restartu `web` i `djcms`, bez DNS, bez certyfikatów):
#   1. warunki: DJCMS_ENABLED=1, proxy montuje katalog caddy/ (CADDY_CONFIG_DIR=./caddy), przy `on`
#      także `djcms` healthy;
#   2. plik kandydujący (scripts/render_caddyfile.sh z DJCMS_PRIMARY podanym jawnie) i `caddy validate`
#      w kontenerze `proxy` – zła konfiguracja zatrzymuje przełącznik, ZANIM cokolwiek się zmieni;
#   3. DJCMS_PRIMARY w .env (jedna linijka; dopisana, gdy jej nie ma), render caddy/Caddyfile
#      (zapis w miejscu) i sprawdzenie, że kontener widzi nową treść;
#   4. `caddy reload` – nowa konfiguracja bez zrywania połączeń;
#   5. kontrola dymna przez proxy na tym serwerze (curl --resolve <host>:443:127.0.0.1) dla hostów
#      konkursów (`sync_competitions --list-hosts` w djcms; bez djcms – SITE_DOMAIN i EXTRA_DOMAINS):
#      `/` (i `/<prefiks>/`) z djcms (`X-Djcms-Mode: primary`) przy `on`, z web (bez nagłówka) przy
#      `off`; `/login/` zawsze z web; `/static/css/app.css` 200; `/internal/tls-allowed` 404;
#      `/robots.txt` 200 przy `on`.
#   Błąd w krokach 3–5 przy `on` = automatyczny powrót do DJCMS_PRIMARY=0 (render + reload) i kod 1.
#   `off` niczego nie cofa (jest drogą powrotu) – błąd kontroli dymnej to kod 1 i komunikat.
#
# Treść: `on` pokazuje to, co jest w djcms; `off` – Wagtail w stanie z chwili zamrożenia (zmiany
# zrobione później w djcms do Wagtaila NIE wracają). Zamrożenie edycji Wagtaila przełącza osobno
# `manage.py cms_freeze on|off` (robi to scripts/djcms_cutover.sh; `cms_freeze off` dopiero po decyzji).
# Produkcja: przełączenie wyłącznie po zgodzie organizatora (DJ-02 § 12 p. 7).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACTION="${1:-}"
case "$ACTION" in
  on|off|status|check) ;;
  *) echo "użycie: bash scripts/djcms_switch.sh on|off|status|check" >&2; exit 2 ;;
esac

GENERATED="caddy/Caddyfile"
CANDIDATE="deploy/Caddyfile.candidate"
T0="$(date +%s)"

log() { printf '==> %s\n' "$*"; }
die() { printf 'djcms_switch: BŁĄD: %s\n' "$*" >&2; exit 1; }

[ -f .env ] || die "brak .env w $ROOT – uruchom w katalogu instalacji (/opt/olimpiada)"

env_value() {  # env_value <NAZWA> – jak scripts/render_caddyfile.sh: ostatnie wystąpienie, bez cudzysłowów i CR
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r\042\047'
}
flag_on() {  # flag_on <wartość> – 0 dla 1/true/yes/on
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}
mode_of() { if [ "$1" = "1" ]; then echo primary; else echo preview; fi; }

# Generator czyta WYŁĄCZNIE .env (zmienne z powłoki operatora nie mogą po cichu wygrać z plikiem),
# poza DJCMS_PRIMARY, gdy podany jawnie drugim argumentem (plik kandydujący).
render() {  # render <plik wyjściowy> [<DJCMS_PRIMARY>]
  if [ $# -ge 2 ]; then
    env -u EXTRA_DOMAINS -u PLATFORM_SUBDOMAINS -u DJCMS_ENABLED -u DJCMS_ROUTES_ENV -u CADDYFILE_SRC \
      DJCMS_PRIMARY="$2" CADDYFILE_OUT="$1" bash scripts/render_caddyfile.sh
  else
    env -u EXTRA_DOMAINS -u PLATFORM_SUBDOMAINS -u DJCMS_ENABLED -u DJCMS_PRIMARY -u DJCMS_ROUTES_ENV \
      -u CADDYFILE_SRC CADDYFILE_OUT="$1" bash scripts/render_caddyfile.sh
  fi
}

set_primary() {  # set_primary <0|1> – jedna linijka w .env (wszystkie wystąpienia na tę samą wartość)
  if grep -qE '^DJCMS_PRIMARY=' .env; then
    sed -i "s/^DJCMS_PRIMARY=.*/DJCMS_PRIMARY=$1/" .env
  else
    {
      echo
      echo "# Serwis publiczny: 0 = Wagtail (web), 1 = django CMS (djcms) – przełącza"
      echo "# scripts/djcms_switch.sh on|off (docs/OPERACJE.md § 22.8). Nie zmieniaj ręcznie bez przełącznika."
      echo "DJCMS_PRIMARY=$1"
    } >> .env
  fi
  chmod 600 .env
}

caddy_reload() {
  docker compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null
}

# Czy `proxy` widzi TĘ treść pliku, którą właśnie wygenerowaliśmy (kontener sprzed montażu katalogu
# caddy/ widziałby inny plik, a reload przeładowałby właśnie jego – docs/OPERACJE.md § 23).
container_sees_generated() {
  local host_sum box_sum
  host_sum="$(sha256sum "$GENERATED" | cut -d' ' -f1)"
  box_sum="$(docker compose exec -T proxy sha256sum /etc/caddy/Caddyfile </dev/null | tr -d '\r' | cut -d' ' -f1)"
  [ -n "$box_sum" ] && [ "$host_sum" = "$box_sum" ]
}

freeze_state() {  # stdout: opis; kod: 0 zamrożone, 1 otwarte, 2 nie wiadomo (web nie odpowiada)
  local out rc=0
  # `cms_freeze status` kończy się kodem 1, gdy edycja jest OTWARTA – to nie błąd (set -e).
  out="$(docker compose exec -T web python manage.py cms_freeze status </dev/null 2>&1)" || rc=$?
  printf '%s\n' "$out" | tr -d '\r' | sed '/^$/d'
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) return 2 ;; esac
}

# --- Kontrola dymna -------------------------------------------------------------------------------

SMOKE_FAIL=0
smoke_hosts() {
  # Hosty i prefiksy aktywnych konkursów z rejestru djcms (`<host> <slug>`, `<host>/<prefiks>/ <slug>`);
  # `sync_competitions` uzgadnia przy okazji rejestr (idempotentnie). Bez djcms (np. `off` przy
  # awarii) – hosty z .env: SITE_DOMAIN i EXTRA_DOMAINS bez przekierowań `www.`.
  local list="" domain extra h
  list="$(docker compose exec -T djcms python manage.py sync_competitions --list-hosts </dev/null 2>/dev/null \
    | tr -d '\r' | grep -E '^[a-z0-9.-]+(/[^ /]+/)? [a-z0-9_-]+$' | cut -d' ' -f1 || true)"
  domain="$(env_value SITE_DOMAIN)"
  extra="$(env_value EXTRA_DOMAINS)"
  {
    [ -n "$domain" ] && echo "$domain"
    printf '%s\n' "$list"
    if [ -z "$list" ]; then
      for h in $extra; do
        case " $extra " in *" ${h#www.} "*) [ "${h#www.}" != "$h" ] && continue ;; esac
        echo "$h"
      done
    fi
  } | sed '/^$/d' | awk '!seen[$0]++'
}

# Czy wygenerowany Caddyfile ma blok aplikacji dla tej nazwy: SITE_DOMAIN, domena z EXTRA_DOMAINS
# albo (PLATFORM_SUBDOMAINS=1) jednoczłonowa subdomena SITE_DOMAIN – ta sama reguła co generator.
# Rejestr djcms zna też hosty, których proxy nie obsługuje – np. własną domenę konkursu pod
# prefiksem ścieżki (`e2e-druga.localhost` przy `olimpiada.test/druga/`; API v2 podaje ją celowo,
# DJ-02 § 4.2). Pytanie o nią przez proxy kończy się błędem TLS, a nie odpowiedzią którejkolwiek
# usługi – kontrola dymna ją pomija (inaczej `on` wracałby do Wagtaila na każdej takiej instalacji).
proxy_serves() {  # proxy_serves <host>
  local host="$1" domain extra
  domain="$(env_value SITE_DOMAIN)"
  extra="$(env_value EXTRA_DOMAINS)"
  [ -n "$domain" ] && [ "$host" = "$domain" ] && return 0
  case " $extra " in *" $host "*) return 0 ;; esac
  if flag_on "$(env_value PLATFORM_SUBDOMAINS)" && [ -n "$domain" ]; then
    case "$host" in
      *."$domain") case "${host%."$domain"}" in *.*|dj|www|meet|monitor|s3|mail|'') ;; *) return 0 ;; esac ;;
    esac
  fi
  return 1
}

probe() {  # probe <host> <ścieżka> – nagłówki odpowiedzi (curl -D), pierwsza linijka = status
  local args=(-sk --max-time 15 -o /dev/null -D - --resolve "$1:443:127.0.0.1")
  [ "${#BYPASS}" -ge 32 ] && args+=(-H "X-Maintenance-Bypass: $BYPASS")
  curl "${args[@]}" "https://$1$2" 2>/dev/null | tr -d '\r' || true
}
status_of() { printf '%s\n' "$1" | awk 'NR == 1 && /^HTTP\// { print $2; exit }'; }
mode_header() { printf '%s\n' "$1" | awk -F': *' 'tolower($1) == "x-djcms-mode" { print $2; exit }'; }

expect() {  # expect <opis> <warunek-spełniony 0/1>
  if [ "$2" = "0" ]; then printf '   ok   %s\n' "$1"; else printf '   FAIL %s\n' "$1"; SMOKE_FAIL=1; fi
}

smoke() {  # smoke <primary|preview>
  local want="$1" entry host prefix out code mode seen_hosts=" "
  SMOKE_FAIL=0
  BYPASS="$(env_value MAINTENANCE_BYPASS_TOKEN)"
  log "Kontrola dymna (tryb $want) przez proxy na 127.0.0.1:443"
  while IFS= read -r entry; do
    host="${entry%%/*}"
    prefix="/"
    [ "$entry" != "$host" ] && prefix="/${entry#*/}"
    if ! proxy_serves "$host"; then
      printf '   –    https://%s%s – pominięte: proxy nie ma bloku dla tej nazwy (SITE_DOMAIN, *.SITE_DOMAIN, EXTRA_DOMAINS)\n' "$host" "$prefix"
      continue
    fi
    # Strona publiczna konkursu: primary → djcms z nagłówkiem trybu; preview → web (bez nagłówka).
    out="$(probe "$host" "$prefix")"; code="$(status_of "$out")"; mode="$(mode_header "$out")"
    if [ "$want" = primary ]; then
      [ "$mode" = primary ] && [ -n "$code" ] && [ "$code" -lt 500 ]
      expect "https://$host$prefix → djcms (X-Djcms-Mode: primary) [${code:-brak}, ${mode:-bez nagłówka}]" $?
    else
      [ -z "$mode" ] && [ -n "$code" ] && [ "$code" -lt 500 ]
      expect "https://$host$prefix → web (bez X-Djcms-Mode) [${code:-brak}${mode:+, $mode}]" $?
    fi
    out="$(probe "$host" "${prefix}login/")"; code="$(status_of "$out")"; mode="$(mode_header "$out")"
    [ -z "$mode" ] && [ -n "$code" ] && [ "$code" -lt 400 ]
    expect "https://$host${prefix}login/ → web [${code:-brak}${mode:+, $mode}]" $?
    # Adresy bez prefiksu – raz na host.
    case "$seen_hosts" in *" $host "*) continue ;; esac
    seen_hosts="$seen_hosts$host "
    code="$(status_of "$(probe "$host" /static/css/app.css)")"
    [ "$code" = 200 ]
    expect "https://$host/static/css/app.css → 200 [${code:-brak}]" $?
    code="$(status_of "$(probe "$host" /internal/tls-allowed)")"
    [ "$code" = 404 ]
    expect "https://$host/internal/tls-allowed → 404 [${code:-brak}]" $?
    if [ "$want" = primary ]; then
      out="$(probe "$host" /robots.txt)"; code="$(status_of "$out")"; mode="$(mode_header "$out")"
      [ "$code" = 200 ] && [ "$mode" = primary ]
      expect "https://$host/robots.txt → 200 z djcms [${code:-brak}, ${mode:-bez nagłówka}]" $?
    fi
  done < <(smoke_hosts)
  [ "$seen_hosts" != " " ] || { printf '   FAIL brak hostów do sprawdzenia (SITE_DOMAIN w .env?)\n'; SMOKE_FAIL=1; }
  return "$SMOKE_FAIL"
}

# --- Polecenia ------------------------------------------------------------------------------------

ENABLED=0; flag_on "$(env_value DJCMS_ENABLED)" && ENABLED=1
PRIMARY_NOW=0; flag_on "$(env_value DJCMS_PRIMARY)" && PRIMARY_NOW=1

if [ "$ACTION" = status ]; then
  echo "DJCMS_ENABLED=$ENABLED, DJCMS_PRIMARY=$PRIMARY_NOW w .env – strony publiczne: $(
    [ "$ENABLED$PRIMARY_NOW" = 11 ] && echo 'django CMS (djcms)' || echo 'Wagtail (web)')"
  if [ -f "$GENERATED" ]; then
    tmp="$(mktemp)"
    if render "$tmp" >/dev/null 2>&1 && cmp -s "$tmp" "$GENERATED"; then
      echo "$GENERATED: zgodny z .env"
    else
      echo "$GENERATED: NIEZGODNY z .env – bash scripts/djcms_switch.sh $([ "$PRIMARY_NOW" = 1 ] && echo on || echo off)"
    fi
    rm -f "$tmp"
    case "$(grep -m1 -oE 'header_up X-Djcms-Mode (preview|primary)' "$GENERATED" || true)" in
      *primary) echo "tryb w pliku: primary" ;;
      *preview) echo "tryb w pliku: preview" ;;
      *) echo "tryb w pliku: brak tras djcms (DJCMS_ENABLED=0)" ;;
    esac
    if container_sees_generated 2>/dev/null; then
      echo "proxy: widzi ten sam plik (po zmianie ręcznej: caddy reload – bash scripts/djcms_switch.sh on|off)"
    else
      echo "proxy: nie działa albo widzi inny plik (CADDY_CONFIG_DIR=$(env_value CADDY_CONFIG_DIR); bash scripts/proxy_config.sh apply)"
    fi
  else
    echo "$GENERATED: brak (wdrożenie go tworzy)"
  fi
  rc=0; desc="$(freeze_state)" || rc=$?
  case "$rc" in
    0) echo "Wagtail: edycja stron ZAMROŻONA – $desc" ;;
    1) echo "Wagtail: edycja stron otwarta – $desc" ;;
    *) echo "Wagtail: stan zamrożenia nieznany (web nie odpowiada)" ;;
  esac
  exit 0
fi

[ "$ENABLED" = 1 ] || die "DJCMS_ENABLED w .env nie jest 1 – bez djcms nie ma czego przełączać (docs/OPERACJE.md § 22)"

if [ "$ACTION" = check ]; then
  smoke "$(mode_of "$PRIMARY_NOW")" || die "kontrola dymna nie przeszła (tryb $(mode_of "$PRIMARY_NOW")) – bash scripts/djcms_switch.sh off wraca do Wagtaila"
  log "Kontrola dymna: wszystko w porządku"
  exit 0
fi

# on / off
WANT=0; [ "$ACTION" = on ] && WANT=1
case "$(env_value CADDY_CONFIG_DIR)" in
  ./caddy|caddy|./caddy/|caddy/) ;;
  *) die "proxy nie montuje katalogu z $GENERATED (CADDY_CONFIG_DIR=$(env_value CADDY_CONFIG_DIR)) – przełącznik nie miałby skutku; wdróż (scripts/deploy.sh dopisuje CADDY_CONFIG_DIR=./caddy)" ;;
esac

# Jedna zmiana konfiguracji proxy naraz (dwa równoległe `on`/`off` zostawiłyby .env i plik w różnych
# stanach) – ta sama blokada co w scripts/proxy_config.sh, które pisze ten sam plik przy wdrożeniu.
if command -v flock >/dev/null 2>&1; then
  mkdir -p "$(dirname "$GENERATED")"
  exec 9>"$(dirname "$GENERATED")/.lock"
  flock -n 9 || die "konfigurację proxy zmienia właśnie inny proces (djcms_switch.sh albo wdrożenie – scripts/proxy_config.sh)"
fi

if [ "$WANT" = 1 ]; then
  docker compose ps --format '{{.Service}}={{.Health}}' </dev/null | tr -d '\r' | grep -qx 'djcms=healthy' \
    || die "djcms nie jest healthy (docker compose ps djcms; docker compose logs djcms) – nie przełączam"
  rc=0; desc="$(freeze_state)" || rc=$?
  if [ "$rc" != 0 ]; then
    echo "UWAGA: edycja stron w Wagtailu nie jest zamrożona – zmiany w /cms/ nie będą widoczne publicznie."
    echo "       Zwykła droga: scripts/djcms_cutover.sh (zamrożenie → import → weryfikacja → on)."
  fi
fi

# `proxy` montuje katalog caddy/ i widzi każdą wersję pliku; kontener, który widzi inną treść, to
# montaż sprzed tej zmiany albo proxy, które nie działa (docs/OPERACJE.md § 23; wdrożenie naprawia
# to samo w kroku 4c/8). `on` odmawia (nic nie zmienia); `off` jest drogą ratunkową, więc odtwarza
# proxy sam (kilka sekund bez HTTPS na wszystkich domenach – to i tak mniej niż awaria, z powodu
# której ktoś robi `off`).
[ -f "$GENERATED" ] || die "brak $GENERATED – wdróż (scripts/deploy.sh) albo bash scripts/proxy_config.sh update"
if ! container_sees_generated; then
  if [ "$WANT" = 1 ]; then
    die "proxy nie działa albo widzi inną treść /etc/caddy/Caddyfile niż $GENERATED – bash scripts/proxy_config.sh apply (odtworzy proxy: kilka sekund przerwy, docker compose up -d --force-recreate --no-deps proxy) i ponów"
  fi
  echo "UWAGA: proxy widzi inną treść /etc/caddy/Caddyfile niż $GENERATED – odtwarzam kontener proxy"
  docker compose up -d --force-recreate --no-deps proxy </dev/null
  for _ in $(seq 1 30); do
    docker compose exec -T proxy wget -q -O /dev/null http://127.0.0.1:2019/config/ </dev/null >/dev/null 2>&1 && break
    sleep 1
  done
fi

log "1/4 Plik kandydujący (DJCMS_PRIMARY=$WANT) i caddy validate w kontenerze proxy"
VALIDATE_OUT="$(mktemp)"
trap 'rm -f "$CANDIDATE" "$VALIDATE_OUT"' EXIT
render "$CANDIDATE" "$WANT" >/dev/null
docker compose exec -T proxy sh -c 'cat > /tmp/Caddyfile.candidate && caddy validate --config /tmp/Caddyfile.candidate --adapter caddyfile' \
  <"$CANDIDATE" >"$VALIDATE_OUT" 2>&1 || {
  grep -vE '"level":"(info|warn)"' "$VALIDATE_OUT" | tail -n 20 >&2 || true
  die "caddy validate odrzucił nową konfigurację – nic nie zostało zmienione"
}

apply() {  # apply <0|1> – .env, render w miejscu, kontener widzi plik, reload
  set_primary "$1"
  render "$GENERATED" >/dev/null
  cmp -s "$GENERATED" "$CANDIDATE" || [ "$1" != "$WANT" ] \
    || { echo "wygenerowany plik różni się od kandydata (zmiana .env w trakcie?)" >&2; return 1; }
  container_sees_generated || { echo "proxy widzi inną treść /etc/caddy/Caddyfile niż $GENERATED (montaż – CADDY_CONFIG_DIR)" >&2; return 1; }
  caddy_reload
}

rollback() {
  echo "!!! Przełączenie nie powiodło się – wracam do DJCMS_PRIMARY=0 (Wagtail)" >&2
  if apply 0; then
    echo "!!! Wrócono do Wagtaila (DJCMS_PRIMARY=0). Sprawdź: bash scripts/djcms_switch.sh status" >&2
  else
    echo "!!! POWRÓT TEŻ SIĘ NIE UDAŁ – ręcznie: sed -i 's/^DJCMS_PRIMARY=.*/DJCMS_PRIMARY=0/' .env &&" >&2
    echo "!!!   bash scripts/proxy_config.sh update" >&2
  fi
  exit 1
}

log "2/4 DJCMS_PRIMARY=$WANT w .env i $GENERATED (zapis w miejscu)"
log "3/4 caddy reload"
if ! apply "$WANT"; then
  [ "$WANT" = 1 ] && rollback
  die "nie udało się zastosować DJCMS_PRIMARY=0 – sprawdź docker compose ps proxy i $GENERATED"
fi

log "4/4 Kontrola dymna"
if ! smoke "$(mode_of "$WANT")"; then
  [ "$WANT" = 1 ] && rollback
  die "po przełączeniu na Wagtail kontrola dymna nie przeszła – sprawdź serwis (docker compose ps; logs proxy web)"
fi

log "Gotowe w $(( $(date +%s) - T0 )) s: strony publiczne z $([ "$WANT" = 1 ] && echo 'django CMS (djcms)' || echo 'Wagtaila (web)')."
if [ "$WANT" = 1 ]; then
  echo "    Wycofanie: bash scripts/djcms_switch.sh off (~2 s). Wagtail pokaże stan z chwili zamrożenia."
  echo "    Porównanie w przeglądarce: /djcms/preview/ na hoście konkursu (ciasteczko djcms_view=wagtail)."
else
  echo "    Zmiany zrobione w djcms po przełączeniu NIE wracają do Wagtaila (DJ-02 D8)."
  echo "    Edycję w Wagtailu odblokowuje dopiero: docker compose exec -T web python manage.py cms_freeze off"
fi
