#!/usr/bin/env bash
# Kontrola dymna serwisu po wdrożeniu (docs/tasks/OPS-04.md § 1, docs/OPERACJE.md § 48).
#
# WYŁĄCZNIE ODCZYT: same żądania GET, bez logowania, bez formularzy, bez zapisu czegokolwiek
# w serwisie. Można ją więc puścić w dowolnej chwili – także w środku etapu.
#
# Użycie:
#   scripts/smoke.sh https://olimpiadakwantowa.pl https://iqo.example        # z dowolnej maszyny
#   bash scripts/smoke.sh --server /opt/olimpiada                           # na serwerze (woła deploy.sh)
#
# Opcje:
#   --server KATALOG     hosty z KATALOG/.env (SITE_DOMAIN) i z `manage.py check_domains --hosts`
#                        (aktywne konkursy z własnym hostem), żądania przez proxy TEGO serwera
#                        (--resolve 127.0.0.1), przepustka strony prac technicznych, LiveKit przy
#                        LIVEKIT_PROXY=1, djcms przy DJCMS_ENABLED=1
#   --resolve IP         każde żądanie do IP (curl --resolve <host>:<port>:IP) – bez DNS
#   --expect-version V   wersja w /status.json (inna = ostrzeżenie: odpowiedź jest buforowana 30 s)
#   --insecure           bez weryfikacji certyfikatu (lokalny dev z certyfikatem Caddy'ego)
#   --livekit URL        sprawdź też sygnalizację LiveKit (odpowiedź < 500)
#   --djcms              sprawdź też djcms pod pierwszym hostem (/djcms/healthz/, /djcms/preview/)
#   --report PLIK        kopia wydruku do pliku (treść listu alarmowego scripts/rollback.sh)
#
# Zmienne: SMOKE_PAGES (domyślnie "/status/ /password-reset/"), SMOKE_LOGIN_PATH (/login/),
# SMOKE_API_PATH (/api/competitions/editions/current/), SMOKE_TIMEOUT (15 s na żądanie),
# SMOKE_RETRIES (3 próby każdego sprawdzenia), SMOKE_RETRY_DELAY (5 s między próbami).
#
# Kod wyjścia: 0 – wszystko przeszło (ostrzeżenia dozwolone), 1 – co najmniej jeden błąd,
# 2 – złe wywołanie. Na końcu podsumowanie z listą błędów.
#
# Plik statyczny z hashem manifestu i arkusze motywu bierzemy ze strony LOGOWANIA, a nie głównej:
# strona główna bywa w buforze całych stron (OPERACJE § 13, do 120 s) i przez tę chwilę po wdrożeniu
# odsyła do plików poprzedniej wersji, które `collectstatic --clear` już skasował. Logowanie nie jest
# buforowane (ustawia ciasteczko CSRF), więc pokazuje to, co serwuje nowa wersja.
set -uo pipefail

SMOKE_PAGES="${SMOKE_PAGES-/status/ /password-reset/}"
SMOKE_LOGIN_PATH="${SMOKE_LOGIN_PATH:-/login/}"
SMOKE_API_PATH="${SMOKE_API_PATH:-/api/competitions/editions/current/}"
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-15}"
SMOKE_RETRIES="${SMOKE_RETRIES:-3}"
SMOKE_RETRY_DELAY="${SMOKE_RETRY_DELAY:-5}"

RESOLVE_IP=""
INSECURE=0
EXPECT_VERSION=""
REPORT=""
LIVEKIT_URL=""
DJCMS=0
SERVER_DIR=""
BYPASS=""
STRICT_TLS=" "      # hosty, dla których błąd TLS jest błędem (a nie ostrzeżeniem)
TARGETS=()

CHECKS=0
FAILS=0
WARNS=0
FAIL_LINES=()
CURL_TIME_TOTAL=0

# --- Wydruk ----------------------------------------------------------------------------------------

say() {  # say <tekst…> – na ekran i do raportu
  printf '%s\n' "$*"
  if [ -n "$REPORT" ]; then printf '%s\n' "$*" >>"$REPORT"; fi
}

# --- Adresy ----------------------------------------------------------------------------------------

url_scheme() { printf '%s' "${1%%://*}"; }
url_hostport() { local rest="${1#*://}"; printf '%s' "${rest%%/*}"; }
url_host() { local hp; hp="$(url_hostport "$1")"; printf '%s' "${hp%%:*}"; }
url_port() {
  local hp; hp="$(url_hostport "$1")"
  if [ "${hp#*:}" != "$hp" ]; then printf '%s' "${hp#*:}"
  elif [ "$(url_scheme "$1")" = http ]; then printf 80
  else printf 443; fi
}
url_origin() { printf '%s://%s' "$(url_scheme "$1")" "$(url_hostport "$1")"; }
absolute_url() {  # absolute_url <origin> <href> – href względny do pochodzenia strony
  case "$2" in
    http://*|https://*) printf '%s' "$2" ;;
    //*) printf '%s:%s' "$(url_scheme "$1")" "$2" ;;
    /*) printf '%s%s' "$1" "$2" ;;
    *) printf '%s/%s' "$1" "$2" ;;
  esac
}

# --- Żądanie ---------------------------------------------------------------------------------------

# Katalog roboczy zakłada `init_work` (main albo test, który źródłuje ten plik) – samo `source`
# nie zostawia po sobie ani katalogu, ani pułapki EXIT w powłoce wołającej.
WORK=""
HDR=""
BODY=""
init_work() {
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/smoke.XXXXXX")"
  HDR="$WORK/headers"
  BODY="$WORK/body"
}
F_CODE="000"
F_TIME="0"
F_RC=0
CHECK_TIME=0

F_OFFHOST=""
# Przekierowania śledzimy sami (bez `curl -L`) i WYŁĄCZNIE w obrębie tego samego pochodzenia:
# `-L` wysłałby nagłówek przepustki prac technicznych (`-K -`) także pod adres z `Location` – na
# inny host, którego nie kontrolujemy. Przekierowanie na inny host = koniec, adres w F_OFFHOST.
fetch() {  # fetch <url> [follow] – nagłówki do $HDR, treść do $BODY; F_CODE, F_TIME, F_RC, F_OFFHOST
  local url="$1" follow="${2:-0}" hops=0 loc next
  F_OFFHOST=""
  while :; do
    fetch_once "$url"
    [ "$follow" = 1 ] || return 0
    case "$F_CODE" in 301|302|303|307|308) ;; *) return 0 ;; esac
    loc="$(header_value location)"
    [ -n "$loc" ] || return 0
    next="$(absolute_url "$(url_origin "$url")" "$loc")"
    if [ "$(url_origin "$next")" != "$(url_origin "$url")" ]; then F_OFFHOST="$next"; return 0; fi
    hops=$((hops + 1))
    [ "$hops" -le 5 ] || return 0
    url="$next"
  done
}
fetch_once() {  # fetch_once <url> – jedno żądanie, bez przekierowań
  local url="$1" out
  local args=(-sS --max-time "$SMOKE_TIMEOUT" -D "$HDR" -o "$BODY" -w '%{http_code} %{time_total}')
  [ "$INSECURE" = 1 ] && args+=(-k)
  [ -n "$RESOLVE_IP" ] && args+=(--resolve "$(url_host "$url"):$(url_port "$url"):$RESOLVE_IP")
  : >"$HDR"; : >"$BODY"
  F_RC=0
  if [ -n "$BYPASS" ]; then
    # Przepustka konfiguracją curla na stdin (`-K -`), nie w argumentach – te widzi `ps` każdego
    # konta na serwerze (ten sam wzorzec co krok 5a/8 deploy.sh i scripts/djcms_switch.sh).
    out="$(printf 'header = "X-Maintenance-Bypass: %s"\n' "$BYPASS" | curl "${args[@]}" -K - "$url" 2>"$WORK/err")" || F_RC=$?
  else
    out="$(curl "${args[@]}" "$url" </dev/null 2>"$WORK/err")" || F_RC=$?
  fi
  F_CODE="${out%% *}"
  F_TIME="${out#* }"
  case "$F_CODE" in [0-9][0-9][0-9]) ;; *) F_CODE="000" ;; esac
  case "$F_TIME" in [0-9]*) ;; *) F_TIME=0 ;; esac
  CHECK_TIME="$(awk -v a="$CHECK_TIME" -v b="$F_TIME" 'BEGIN { printf "%.2f", a + b }')"
}

# Nagłówki OSTATNIEJ odpowiedzi w pliku (gdyby kiedyś było ich kilka bloków – liczy się ostatni).
last_headers() { tr -d '\r' <"$HDR" | awk '/^HTTP\// { buf = "" } { buf = buf $0 "\n" } END { printf "%s", buf }'; }
header_value() {  # header_value <nazwa> – wartość nagłówka ostatniej odpowiedzi (bez względu na wielkość liter)
  last_headers | awk -v n="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" '
    { i = index($0, ":"); if (i == 0) next
      if (tolower(substr($0, 1, i - 1)) == n) { v = substr($0, i + 1); sub(/^[ \t]+/, "", v); print v; exit } }'
}
has_cookie() {  # has_cookie <nazwa> – Set-Cookie z tą nazwą w ostatniej odpowiedzi
  last_headers | grep -qiE "^set-cookie:[[:space:]]*$1="
}
json_str() {  # json_str <pole> [plik] – pierwsze wystąpienie "pole": "wartość" (bez jq na serwerze)
  tr -d '\n' <"${2:-$BODY}" | grep -oE "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -n 1 \
    | sed -E 's/^.*:[[:space:]]*"//; s/"$//'
}
static_path() {  # static_path <plik HTML> – pierwszy plik statyczny z hashem manifestu (12 znaków hex)
  grep -oE '/static/[A-Za-z0-9_./-]+\.[0-9a-f]{12}\.(css|js)' "$1" | head -n 1
}
theme_css() {  # theme_css <plik HTML> – arkusze motywu (<link rel="stylesheet">, /themes/ albo /_theme/)
  grep -oiE '<link[^>]+>' "$1" | grep -iE 'rel="?stylesheet' | grep -oiE 'href="[^"]+"' \
    | sed -E 's/^[Hh][Rr][Ee][Ff]="//; s/"$//; s/&amp;/\&/g' | grep -E '/themes/|/_theme/' | awk '!seen[$0]++'
}
tls_error() { case "$F_RC" in 35|51|58|59|60|77|80|82|83|90|91) return 0 ;; esac; return 1; }
curl_note() {
  if [ "$F_RC" != 0 ]; then printf 'curl %s: %s' "$F_RC" "$(head -c 160 "$WORK/err" | tr -d '\r\n')"; fi
  if [ -n "$F_OFFHOST" ]; then printf 'przekierowanie na inny host (%s) – nie podążam' "$F_OFFHOST"; fi
}

# --- Sprawdzenia -----------------------------------------------------------------------------------
# Każde: 0 – ok, 1 – błąd (ponawiany), 2 – ostrzeżenie (bez ponawiania), 3 – informacja.
# Wynik słowny w MSG.

MSG=""
HOST_TLS_SKIP=0

c_home() {  # c_home <url> <host> – strona główna: 200 i nagłówek CSP
  fetch "$1" 1
  if tls_error; then
    case "$STRICT_TLS" in
      *" $2 "*) MSG="błąd TLS ($(curl_note))"; return 1 ;;
      *) MSG="błąd TLS ($(curl_note)) – certyfikat tej nazwy to nie sprawa wydania, host pominięty"
         HOST_TLS_SKIP=1; return 2 ;;
    esac
  fi
  [ "$F_CODE" = 200 ] || { MSG="$F_CODE (oczekiwano 200) $(curl_note)"; return 1; }
  [ -n "$(header_value content-security-policy)" ] || { MSG="200, ale bez nagłówka Content-Security-Policy"; return 1; }
  MSG="200, CSP"
}
c_page() {  # c_page <url> – 200 (po przekierowaniach)
  fetch "$1" 1
  [ "$F_CODE" = 200 ] || { MSG="$F_CODE (oczekiwano 200) $(curl_note)"; return 1; }
  MSG="200"
}
c_healthz() {
  fetch "$1"
  local st; st="$(json_str status)"
  [ "$F_CODE" = 200 ] && [ "$st" = ok ] || { MSG="$F_CODE, status=${st:-brak} (oczekiwano 200 i \"status\": \"ok\") $(curl_note)"; return 1; }
  MSG="200, status=ok"
}
c_status_json() {  # c_status_json <url> <plik na kopię treści>
  fetch "$1"
  local st; st="$(json_str status)"
  cp "$BODY" "$2"
  [ "$F_CODE" = 200 ] && [ "$st" = ok ] || { MSG="$F_CODE, status=${st:-brak} (oczekiwano 200 i \"status\": \"ok\") $(curl_note)"; return 1; }
  MSG="200, status=ok, wersja $(json_str version "$2")"
}
c_version() {  # c_version <plik status.json> – wersja = --expect-version (inna: ostrzeżenie)
  local v; v="$(json_str version "$1")"
  [ "$v" = "$EXPECT_VERSION" ] && { MSG="$v"; return 0; }
  MSG="/status.json mówi „${v:-brak}”, oczekiwano „$EXPECT_VERSION” (odpowiedź buforowana 30 s – sprawdź za minutę)"
  return 2
}
c_login() {  # c_login <url> <plik na kopię HTML> – 200, pole CSRF, ciasteczko csrftoken, CSP
  fetch "$1"
  [ "$F_CODE" = 200 ] || { MSG="$F_CODE (oczekiwano 200) $(curl_note)"; return 1; }
  cp "$BODY" "$2"
  grep -q 'csrfmiddlewaretoken' "$BODY" || { MSG="200, ale bez pola csrfmiddlewaretoken"; return 1; }
  has_cookie csrftoken || { MSG="200, ale bez ciasteczka csrftoken (Set-Cookie)"; return 1; }
  [ -n "$(header_value content-security-policy)" ] || { MSG="200, ale bez nagłówka Content-Security-Policy"; return 1; }
  MSG="200, CSRF (pole + ciasteczko), CSP"
}
c_static() {  # c_static <origin> <plik HTML logowania>
  [ -s "$2" ] || { MSG="brak HTML-a strony logowania (sprawdzenie wyżej nie przeszło)"; return 1; }
  local path; path="$(static_path "$2")"
  [ -n "$path" ] || { MSG="w HTML-u logowania brak pliku /static/….<hash>.css|js (manifest plików statycznych?)"; return 1; }
  fetch "$1$path"
  [ "$F_CODE" = 200 ] && [ -s "$BODY" ] || { MSG="$path → $F_CODE (oczekiwano 200, niepusty) $(curl_note)"; return 1; }
  MSG="$path → 200"
}
c_asset() {  # c_asset <url> – 200 i niepusta treść
  fetch "$1"
  [ "$F_CODE" = 200 ] && [ -s "$BODY" ] || { MSG="$F_CODE (oczekiwano 200, niepusty) $(curl_note)"; return 1; }
  MSG="200"
}
c_api() {  # c_api <url> – 200 albo 404 (brak bieżącej edycji), zawsze JSON
  fetch "$1"
  local ct; ct="$(header_value content-type)"
  case "$F_CODE" in 200|404) ;; *) MSG="$F_CODE (oczekiwano 200 albo 404) $(curl_note)"; return 1 ;; esac
  case "$ct" in application/json*) ;; *) MSG="$F_CODE, ale Content-Type „${ct:-brak}” (oczekiwano application/json)"; return 1 ;; esac
  MSG="$F_CODE, JSON"
}
c_lt500() {  # c_lt500 <url> – jakakolwiek odpowiedź usługi (< 500)
  fetch "$1"
  [ "$F_CODE" != 000 ] && [ "$F_CODE" -lt 500 ] || { MSG="$F_CODE (oczekiwano odpowiedzi < 500) $(curl_note)"; return 1; }
  MSG="$F_CODE"
}
c_200() { c_page "$1"; }

run_check() {  # run_check <opis> <funkcja> [argumenty…] – próby, czas, wydruk, liczniki
  local label="$1" attempt=1 rc mark; shift
  while :; do
    MSG=""; CHECK_TIME=0
    "$@"; rc=$?
    case "$rc" in 0|2|3) break ;; esac
    [ "$attempt" -ge "$SMOKE_RETRIES" ] && break
    attempt=$((attempt + 1))
    sleep "$SMOKE_RETRY_DELAY"
  done
  CURL_TIME_TOTAL="$(awk -v a="$CURL_TIME_TOTAL" -v b="$CHECK_TIME" 'BEGIN { printf "%.2f", a + b }')"
  case "$rc" in
    0) mark="ok"; CHECKS=$((CHECKS + 1)) ;;
    2) mark="UWAGA"; CHECKS=$((CHECKS + 1)); WARNS=$((WARNS + 1)) ;;
    3) mark="–" ;;
    *) mark="FAIL"; CHECKS=$((CHECKS + 1)); FAILS=$((FAILS + 1)); FAIL_LINES+=("$label: $MSG") ;;
  esac
  local tries=""
  [ "$attempt" -gt 1 ] && tries=" (próba $attempt/$SMOKE_RETRIES)"
  say "$(printf '  %-5s %6ss  %s – %s%s' "$mark" "$CHECK_TIME" "$label" "$MSG" "$tries")"
  return 0
}

check_host() {  # check_host <adres bazowy> – komplet sprawdzeń jednego hosta
  local base="${1%/}" origin host page login_html status_body css n=0
  origin="$(url_origin "$base")"
  host="$(url_host "$base")"
  login_html="$WORK/login-$host.html"
  status_body="$WORK/status-$host.json"
  rm -f "$login_html" "$status_body"
  say ""
  say "== $base${RESOLVE_IP:+ (przez $RESOLVE_IP)}"
  HOST_TLS_SKIP=0
  run_check "GET $base/" c_home "$base/" "$host"
  [ "$HOST_TLS_SKIP" = 1 ] && return 0
  for page in $SMOKE_PAGES; do
    run_check "GET $base$page" c_page "$base$page"
  done
  run_check "GET $origin/healthz/" c_healthz "$origin/healthz/"
  run_check "GET $origin/status.json" c_status_json "$origin/status.json" "$status_body"
  if [ -n "$EXPECT_VERSION" ] && [ -s "$status_body" ]; then
    run_check "wersja w /status.json" c_version "$status_body"
  fi
  run_check "GET $base$SMOKE_LOGIN_PATH" c_login "$base$SMOKE_LOGIN_PATH" "$login_html"
  run_check "plik statyczny z hashem manifestu" c_static "$origin" "$login_html"
  if [ -s "$login_html" ]; then
    while IFS= read -r css; do
      [ -n "$css" ] || continue
      n=$((n + 1))
      run_check "arkusz motywu $(absolute_url "$origin" "$css")" c_asset "$(absolute_url "$origin" "$css")"
    done < <(theme_css "$login_html")
    [ "$n" -gt 0 ] || say "  –          motyw: brak arkuszy motywu na stronie logowania (konkurs bez motywu)"
  fi
  run_check "GET $origin$SMOKE_API_PATH" c_api "$origin$SMOKE_API_PATH"
}

# --- Tryb serwera (--server) -----------------------------------------------------------------------

env_value() {  # env_value <NAZWA> – jak scripts/render_caddyfile.sh: ostatnie wystąpienie, bez cudzysłowów i CR
  sed -n "s/^$1=//p" "$SERVER_DIR/.env" 2>/dev/null | tail -n 1 | tr -d '\r\042\047'
}
flag_on() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in 1|true|yes|on) return 0 ;; esac
  return 1
}
# Czy proxy ma blok dla tej nazwy – ta sama reguła co scripts/djcms_switch.sh (proxy_serves):
# SITE_DOMAIN, EXTRA_DOMAINS albo (PLATFORM_SUBDOMAINS=1) jednoczłonowa subdomena SITE_DOMAIN.
proxy_serves() {
  local host="$1" domain extra
  domain="$(env_value SITE_DOMAIN)"
  extra="$(env_value EXTRA_DOMAINS)"
  [ -n "$domain" ] && [ "$host" = "$domain" ] && return 0
  case " $extra " in *" $host "*) return 0 ;; esac
  if flag_on "$(env_value PLATFORM_SUBDOMAINS)" && [ -n "$domain" ]; then
    case "$host" in
      *."$domain") case "${host%."$domain"}" in *.*|dj|www|meet|monitor|s3|mail|live|'') ;; *) return 0 ;; esac ;;
    esac
  fi
  return 1
}
server_hosts() {  # hosty do sprawdzenia: SITE_DOMAIN, potem aktywne konkursy z bazy (albo EXTRA_DOMAINS)
  local domain extra list h
  domain="$(env_value SITE_DOMAIN)"
  extra="$(env_value EXTRA_DOMAINS)"
  # `check_domains --hosts` – obraz sprzed OPS-04 nie zna opcji, niedziałający `web` nie odpowie:
  # wtedy lista z .env. </dev/null: exec nie może czytać stdin wywołującego.
  list="$(cd "$SERVER_DIR" && docker compose exec -T web python manage.py check_domains --hosts </dev/null 2>/dev/null \
    | tr -d '\r' | grep -E '^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$' || true)"
  {
    [ -n "$domain" ] && echo "$domain"
    if [ -n "$list" ]; then
      printf '%s\n' "$list"
    else
      for h in $extra; do
        case " $extra " in *" ${h#www.} "*) [ "${h#www.}" != "$h" ] && continue ;; esac
        echo "$h"
      done
    fi
  } | sed '/^$/d' | awk '!seen[$0]++'
}

# --- Główna ----------------------------------------------------------------------------------------

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --server) SERVER_DIR="${2:?--server wymaga katalogu}"; shift 2 ;;
      --resolve) RESOLVE_IP="${2:?--resolve wymaga adresu}"; shift 2 ;;
      --expect-version) EXPECT_VERSION="${2-}"; shift 2 ;;
      --insecure|-k) INSECURE=1; shift ;;
      --livekit) LIVEKIT_URL="${2:?--livekit wymaga adresu}"; shift 2 ;;
      --djcms) DJCMS=1; shift ;;
      --report) REPORT="${2:?--report wymaga pliku}"; shift 2 ;;
      -h|--help) usage ;;
      -*) echo "smoke: nieznana opcja $1" >&2; exit 2 ;;
      *) TARGETS+=("$1"); shift ;;
    esac
  done
  if [ -n "$REPORT" ]; then : >"$REPORT" || { echo "smoke: nie mogę pisać do $REPORT" >&2; exit 2; }; fi
  init_work
  trap 'rm -rf "$WORK"' EXIT

  local djcms_base="" t host started
  started="$SECONDS"
  if [ -n "$SERVER_DIR" ]; then
    [ -f "$SERVER_DIR/.env" ] || { echo "smoke: brak $SERVER_DIR/.env" >&2; exit 2; }
    local domain; domain="$(env_value SITE_DOMAIN)"
    [ -n "$domain" ] || { echo "smoke: brak SITE_DOMAIN w $SERVER_DIR/.env" >&2; exit 2; }
    [ -n "$RESOLVE_IP" ] || RESOLVE_IP=127.0.0.1
    STRICT_TLS=" $domain "
    BYPASS="$(env_value MAINTENANCE_BYPASS_TOKEN)"
    [ "${#BYPASS}" -ge 32 ] || BYPASS=""
    while IFS= read -r host; do
      if proxy_serves "$host"; then
        TARGETS+=("https://$host")
      else
        say "  –          https://$host – pominięte: proxy nie ma bloku dla tej nazwy (SITE_DOMAIN, *.SITE_DOMAIN, EXTRA_DOMAINS)"
      fi
    done < <(server_hosts)
    flag_on "$(env_value LIVEKIT_PROXY)" && [ -z "$LIVEKIT_URL" ] && LIVEKIT_URL="https://live.$domain/"
    flag_on "$(env_value DJCMS_ENABLED)" && DJCMS=1
    djcms_base="https://$domain"
  else
    for t in "${TARGETS[@]+"${TARGETS[@]}"}"; do STRICT_TLS="$STRICT_TLS$(url_host "$t") "; done
  fi
  [ "${#TARGETS[@]}" -gt 0 ] || { echo "smoke: podaj adresy (https://…) albo --server KATALOG" >&2; exit 2; }
  for t in "${TARGETS[@]}"; do
    case "$t" in http://*|https://*) ;; *) echo "smoke: „$t” nie jest adresem http(s)://" >&2; exit 2 ;; esac
  done

  say "smoke: kontrola dymna – ${#TARGETS[@]} host(ów)${EXPECT_VERSION:+, oczekiwana wersja $EXPECT_VERSION}"
  for t in "${TARGETS[@]}"; do check_host "$t"; done

  if [ -n "$LIVEKIT_URL" ] || [ "$DJCMS" = 1 ]; then
    say ""
    say "== usługi dodatkowe"
  fi
  [ -n "$LIVEKIT_URL" ] && run_check "LiveKit $LIVEKIT_URL" c_lt500 "$LIVEKIT_URL"
  if [ "$DJCMS" = 1 ]; then
    [ -n "$djcms_base" ] || djcms_base="$(url_origin "${TARGETS[0]}")"
    run_check "djcms GET $djcms_base/djcms/healthz/" c_200 "$djcms_base/djcms/healthz/"
    run_check "djcms GET $djcms_base/djcms/preview/" c_lt500 "$djcms_base/djcms/preview/"
  fi

  say ""
  local verdict="PRZESZŁO"
  [ "$FAILS" -eq 0 ] || verdict="NIE PRZESZŁO"
  say "smoke: $CHECKS sprawdzeń, błędów: $FAILS, ostrzeżeń: $WARNS, czas: $((SECONDS - started)) s (żądania ${CURL_TIME_TOTAL} s) – $verdict"
  local line
  for line in "${FAIL_LINES[@]+"${FAIL_LINES[@]}"}"; do say "  FAIL $line"; done
  [ "$FAILS" -eq 0 ]
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
