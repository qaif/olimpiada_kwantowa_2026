#!/usr/bin/env bash
# Test kontroli dymnej po wdrożeniu (`scripts/smoke.sh`, docs/tasks/OPS-04.md § 1).
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu):
#   scripts/tests/smoke_test.sh
#
# Bez sieci: `curl` jest atrapą, która odpowiada z katalogu odpowiedzi ($FIXT, klucz = cksum adresu)
# i zapisuje każde wywołanie (argumenty, a konfigurację z `-K -` jako „config: …”) w $CURL_LOG.
# `docker` (tryb --server: `check_domains --hosts`) – atrapa z listą hostów z $STUB_HOSTS.
# Sprawdzamy to, co decyduje o wycofaniu wdrożenia: co jest błędem, co ostrzeżeniem, kod wyjścia
# i podsumowanie – oraz parsowanie HTML-a, nagłówków i JSON-a bez jq.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE="$ROOT/scripts/smoke.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/smoke-test.XXXXXX")"
trap '[ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"' EXIT
BIN="$WORK/bin"
FIXT="$WORK/fixt"
mkdir -p "$BIN" "$FIXT"

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}
show_on_fail() { [ "$1" -eq 0 ] || sed 's/^/     /' "$2"; }

bash -n "$SMOKE"
check "scripts/smoke.sh przechodzi bash -n" $?

# --- Atrapy ------------------------------------------------------------------------------------

cat >"$BIN/curl" <<'STUB'
#!/usr/bin/env bash
hdr=/dev/null body=/dev/null url="" cfg=0
printf '%s\n' "$*" >>"$CURL_LOG"
while [ $# -gt 0 ]; do
  case "$1" in
    -D) hdr="$2"; shift 2 ;;
    -o) body="$2"; shift 2 ;;
    -w|--max-time|--max-redirs|--resolve) shift 2 ;;
    -K) cfg=1; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
[ "$cfg" = 1 ] && sed 's/^/config: /' >>"$CURL_LOG"
d="$FIXT/$(printf '%s' "$url" | cksum | cut -d' ' -f1)"
: >"$hdr"; : >"$body"
if [ -f "$d.rc" ]; then echo "curl: ($(cat "$d.rc")) SSL certificate problem" >&2; printf '000 0.000'; exit "$(cat "$d.rc")"; fi
if [ -f "$d.fail" ] && [ "$(cat "$d.fail")" -gt 0 ]; then
  echo $(( $(cat "$d.fail") - 1 )) >"$d.fail"
  printf 'HTTP/2 503\r\n\r\n' >"$hdr"; printf 'chwilowo' >"$body"; printf '503 0.010'; exit 0
fi
[ -f "$d.code" ] || { echo "curl: (7) Failed to connect" >&2; printf '000 0.000'; exit 7; }
# Przekierowanie (przy -L): dwa bloki nagłówków, liczy się ostatni.
[ -f "$d.redirect" ] && printf 'HTTP/2 302\r\nlocation: /\r\ncontent-security-policy: stara\r\n\r\n' >>"$hdr"
{ printf 'HTTP/2 %s\r\n' "$(cat "$d.code")"; [ -f "$d.headers" ] && sed 's/$/\r/' "$d.headers"; printf '\r\n'; } >>"$hdr"
cat "$d.body" >"$body"
printf '%s 0.012' "$(cat "$d.code")"
STUB
cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
case "$*" in
  *"check_domains --hosts"*) [ "${STUB_HOSTS_RC:-0}" = 0 ] || exit "$STUB_HOSTS_RC"; printf '%s\n' $STUB_HOSTS | sed 's/$/\r/' ;;
esac
exit 0
STUB
chmod +x "$BIN/curl" "$BIN/docker"

key() { printf '%s' "$1" | cksum | cut -d' ' -f1; }
respond() {  # respond <url> <kod> [nagłówek…] – treść ze stdin
  local d; d="$FIXT/$(key "$1")"; shift
  rm -f "$d".*
  echo "$1" >"$d.code"; shift
  : >"$d.headers"
  for h in "$@"; do printf '%s\n' "$h" >>"$d.headers"; done
  cat >"$d.body"
}
fixt() { printf '%s/%s.%s' "$FIXT" "$(key "$1")" "$2"; }   # fixt <url> <rozszerzenie>

CSP='content-security-policy: default-src '\''self'\'''
LOGIN_HTML='<html><head>
  <link rel="stylesheet" href="/static/css/app.0123456789ab.css">
  <link rel="stylesheet" href="https://s3.example:9000/public-media/themes/iqo/1.1.1-abcdef12/tokens.css">
  <link rel="stylesheet" href="https://s3.example:9000/public-media/themes/iqo/1.1.1-abcdef12/theme.css">
  <link rel="stylesheet" href="/_theme/custom.css?s=abc&amp;v=2">
  <script src="/static/js/app.fedcba987654.js" nonce="x"></script>
</head><body><form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="tok"></form></body></html>'

site() {  # site <origin> – zdrowy serwis pod tym adresem (motyw IQO na stronie logowania)
  local o="$1"
  echo '<html>strona główna</html>' | respond "$o/" 200 "$CSP" "content-type: text/html"
  echo 'status' | respond "$o/status/" 200
  echo 'reset' | respond "$o/password-reset/" 200
  echo '{"status": "ok", "db": true, "redis": true}' | respond "$o/healthz/" 200 "content-type: application/json"
  echo '{"status": "ok", "time": "2026-10-05T12:00:00+02:00", "version": "v1.2.3", "services": {"database": true}}' \
    | respond "$o/status.json" 200 "content-type: application/json"
  printf '%s\n' "$LOGIN_HTML" | respond "$o/login/" 200 "$CSP" "Set-Cookie: csrftoken=abc; Path=/; Secure" "content-type: text/html"
  echo 'body{}' | respond "$o/static/css/app.0123456789ab.css" 200
  echo ':root{}' | respond "https://s3.example:9000/public-media/themes/iqo/1.1.1-abcdef12/tokens.css" 200
  echo ':root{}' | respond "https://s3.example:9000/public-media/themes/iqo/1.1.1-abcdef12/theme.css" 200
  echo ':root{}' | respond "$o/_theme/custom.css?s=abc&v=2" 200
  echo '{"detail": "Nie ustawiono bieżącej edycji.", "code": "NO_CURRENT_EDITION"}' \
    | respond "$o/api/competitions/editions/current/" 404 "Content-Type: application/json"
}

run_smoke() {  # run_smoke <etykieta> [argumenty…] – kod wyjścia; wydruk w $WORK/<etykieta>.out
  local label="$1"; shift
  CURL_LOG="$WORK/$label.curl"; DOCKER_LOG="$WORK/$label.docker"; OUT="$WORK/$label.out"
  : >"$CURL_LOG"; : >"$DOCKER_LOG"
  ( PATH="$BIN:$PATH" CURL_LOG="$CURL_LOG" DOCKER_LOG="$DOCKER_LOG" FIXT="$FIXT" \
      SMOKE_RETRIES="${SMOKE_RETRIES:-2}" SMOKE_RETRY_DELAY=0 bash "$SMOKE" "$@" ) </dev/null >"$OUT" 2>&1
}
calls() { grep -cF -- "$2" "$WORK/$1.curl"; }   # calls <etykieta> <fragment adresu>

O=https://olimpiada.example

# ================================================================================================
# 1. Funkcje parsujące (źródłowane, bez main).
# ================================================================================================
T="$WORK"   # smoke.sh po `source` ma własne WORK (katalog roboczy) – test pisze do T
(
  # shellcheck source=../smoke.sh
  source "$SMOKE"
  printf '%s\n' "$LOGIN_HTML" >"$T/login.html"
  [ "$(static_path "$T/login.html")" = "/static/css/app.0123456789ab.css" ] || exit 1
  [ "$(theme_css "$T/login.html" | wc -l | tr -d ' ')" = 3 ] || exit 2
  theme_css "$T/login.html" | grep -qxF '/_theme/custom.css?s=abc&v=2' || exit 3
  printf '<link href="/static/css/app.css" rel="stylesheet">\n' >"$T/plain.html"
  [ -z "$(static_path "$T/plain.html")" ] || exit 4   # bez hasha manifestu – nie liczy się
  [ -z "$(theme_css "$T/plain.html")" ] || exit 5
  printf '{"status":"degraded","version":"v9"}' >"$T/s.json"
  [ "$(json_str status "$T/s.json")" = degraded ] && [ "$(json_str version "$T/s.json")" = v9 ] || exit 6
  init_work
  trap 'rm -rf "$WORK"' EXIT
  printf 'HTTP/2 302\r\nContent-Security-Policy: stara\r\n\r\nHTTP/2 200\r\nX-Inne: 1\r\n\r\n' >"$HDR"
  [ -z "$(header_value content-security-policy)" ] || exit 7     # liczy się OSTATNI blok nagłówków
  [ "$(header_value x-inne)" = 1 ] || exit 8
  [ "$(absolute_url https://a.example /x)" = https://a.example/x ] &&
    [ "$(absolute_url https://a.example //b.example/y)" = https://b.example/y ] &&
    [ "$(absolute_url https://a.example https://c.example/z)" = https://c.example/z ] || exit 9
  [ "$(url_port https://a.example:9000/x)" = 9000 ] && [ "$(url_port http://a.example/)" = 80 ] &&
    [ "$(url_port https://a.example)" = 443 ] || exit 10
)
rc=$?
check "parsowanie: plik statyczny z hashem, arkusze motywu (&amp;), JSON, ostatni blok nagłówków, adresy (kod $rc)" $rc

# ================================================================================================
# 2. Zdrowy serwis: kod 0, komplet sprawdzeń, motyw, czasy.
# ================================================================================================
site "$O"
run_smoke ok "$O"
rc=$?
check "zdrowy serwis: kod 0" $rc
show_on_fail $rc "$WORK/ok.out"
grep -qE '^smoke: [0-9]+ sprawdzeń, błędów: 0, ostrzeżeń: 0, czas: [0-9]+ s .* – PRZESZŁO$' "$WORK/ok.out"
check "podsumowanie: liczba sprawdzeń, 0 błędów, czas, PRZESZŁO" $?
[ "$(grep -c '^  ok ' "$WORK/ok.out")" = 11 ]
check "11 sprawdzeń ok (strona, 2 strony, healthz, status.json, logowanie, statyczny, 3 arkusze, API)" $?
grep -qE '^  ok +0\.01s  GET https://olimpiada\.example/healthz/' "$WORK/ok.out"
check "każde sprawdzenie z czasem (z curl time_total)" $?
[ "$(calls ok '/themes/iqo/1.1.1-abcdef12/theme.css')" = 1 ] && [ "$(calls ok '/_theme/custom.css?s=abc&v=2')" = 1 ]
check "arkusze motywu IQO pobrane (bucket i /_theme/custom.css z odkodowanym &amp;)" $?
grep -q 'GET https://olimpiada.example/api/competitions/editions/current/ – 404, JSON' "$WORK/ok.out"
check "API: 404 z JSON-em (brak bieżącej edycji) to odpowiedź zdrowa" $?
! grep -q -- '--resolve' "$WORK/ok.curl" && ! grep -q -- '-K' "$WORK/ok.curl"
check "tryb z argumentami: bez --resolve i bez konfiguracji -K (brak przepustki)" $?
[ "$(calls ok 'https://olimpiada.example/status.json')" = 1 ]
check "bez ponowień, gdy wszystko przechodzi" $?

# ================================================================================================
# 3. Błędy, które mają zatrzymać wdrożenie.
# ================================================================================================
expect_fail() {  # expect_fail <opis> <fragment wiersza FAIL> – po zepsuciu fikstury
  local label="f$((++NFAIL))"
  run_smoke "$label" "$O"
  local rc=$?
  [ "$rc" = 1 ] && grep -qF -- "$2" "$WORK/$label.out" && grep -q 'NIE PRZESZŁO' "$WORK/$label.out" &&
    grep -qE '^  FAIL ' "$WORK/$label.out"
  local ok=$?
  check "$1" $ok
  show_on_fail $ok "$WORK/$label.out"
  site "$O"   # przywrócenie zdrowego serwisu
}
NFAIL=0

echo '{"status": "degraded", "db": false, "redis": true}' | respond "$O/healthz/" 503
expect_fail "/healthz/ 503 → kod 1, błąd w podsumowaniu" "healthz/: 503, status=degraded"
echo '{"status": "degraded"}' | respond "$O/healthz/" 503
SMOKE_RETRIES=3 run_smoke retries "$O"
[ "$(calls retries "$O/healthz/")" = 3 ]
check "błąd ponawiany SMOKE_RETRIES razy (3 żądania /healthz/)" $?
site "$O"

echo 1 >"$(fixt "$O/status.json" fail)"
run_smoke transient "$O"
rc=$?
[ "$rc" = 0 ] && grep -q 'status.json – 200, status=ok, wersja v1.2.3 (próba 2/2)' "$WORK/transient.out"
check "chwilowy błąd (503 raz) przechodzi przy drugiej próbie i mówi o tym" $?
site "$O"

echo '{"status": "degraded", "version": "v1.2.3"}' | respond "$O/status.json" 200
expect_fail "/status.json degraded → błąd" "status=degraded"
echo '<html>bez CSP</html>' | respond "$O/" 200 "content-type: text/html"
expect_fail "strona główna bez Content-Security-Policy → błąd" "bez nagłówka Content-Security-Policy"
echo '<html>x</html>' | respond "$O/" 500
expect_fail "strona główna 500 → błąd" "GET https://olimpiada.example/ – 500"
echo '<html>x</html>' | respond "$O/" 200 "content-type: text/html"
touch "$(fixt "$O/" redirect)"
expect_fail "CSP tylko w odpowiedzi-przekierowaniu (nie w ostatniej) → błąd" "bez nagłówka Content-Security-Policy"
printf '<html><link rel="stylesheet" href="/static/css/app.0123456789ab.css"></html>\n' \
  | respond "$O/login/" 200 "$CSP" "Set-Cookie: csrftoken=abc"
expect_fail "logowanie bez pola csrfmiddlewaretoken → błąd" "bez pola csrfmiddlewaretoken"
printf '%s\n' "$LOGIN_HTML" | respond "$O/login/" 200 "$CSP"
expect_fail "logowanie bez ciasteczka csrftoken → błąd" "bez ciasteczka csrftoken"
printf '%s\n' "$LOGIN_HTML" | respond "$O/login/" 200 "Set-Cookie: csrftoken=abc"
expect_fail "logowanie bez CSP → błąd" "GET https://olimpiada.example/login/ – 200, ale bez nagłówka Content-Security-Policy"
printf '<form><input name="csrfmiddlewaretoken"></form><link rel="stylesheet" href="/static/css/app.css">\n' \
  | respond "$O/login/" 200 "$CSP" "Set-Cookie: csrftoken=abc"
expect_fail "brak pliku statycznego z hashem manifestu → błąd" "brak pliku /static/"
echo 'nie ma' | respond "$O/static/css/app.0123456789ab.css" 404
expect_fail "plik statyczny z manifestu 404 → błąd" "/static/css/app.0123456789ab.css → 404"
echo 'nie ma' | respond "https://s3.example:9000/public-media/themes/iqo/1.1.1-abcdef12/theme.css" 404
expect_fail "arkusz motywu 404 → błąd" "theme.css – 404"
echo '<h1>Server Error</h1>' | respond "$O/api/competitions/editions/current/" 500 "Content-Type: text/html"
expect_fail "API 500 → błąd" "editions/current/ – 500"
echo '<html>200, ale HTML</html>' | respond "$O/api/competitions/editions/current/" 200 "Content-Type: text/html"
expect_fail "API bez JSON-a → błąd" "Content-Type „text/html”"
rm -f "$(fixt "$O/password-reset/" code)"
expect_fail "kluczowa strona nieosiągalna (curl 7) → błąd z kodem curla" "password-reset/ – 000 (oczekiwano 200) curl 7"

# Motyw nieobecny = informacja, nie błąd.
printf '<form><input name="csrfmiddlewaretoken"></form><link rel="stylesheet" href="/static/css/app.0123456789ab.css">\n' \
  | respond "$O/login/" 200 "$CSP" "Set-Cookie: csrftoken=abc"
run_smoke notheme "$O"
rc=$?
[ "$rc" = 0 ] && grep -q 'motyw: brak arkuszy motywu' "$WORK/notheme.out"
check "konkurs bez motywu: informacja, kod 0" $?
site "$O"

# ================================================================================================
# 4. Ostrzeżenia (bez zatrzymania): wersja w /status.json.
# ================================================================================================
run_smoke ver "$O" --expect-version v9.9.9
rc=$?
[ "$rc" = 0 ] && grep -q '^  UWAGA .*wersja w /status.json – /status.json mówi „v1.2.3”, oczekiwano „v9.9.9”' "$WORK/ver.out" &&
  grep -q 'błędów: 0, ostrzeżeń: 1' "$WORK/ver.out"
check "inna wersja w /status.json: ostrzeżenie (bufor 30 s), kod 0" $?
run_smoke ver2 "$O" --expect-version v1.2.3
[ $? = 0 ] && grep -q '^  ok .*wersja w /status.json – v1.2.3' "$WORK/ver2.out"
check "zgodna wersja: ok" $?

# ================================================================================================
# 5. TLS: z argumentu – błąd; drugi host w trybie --server – ostrzeżenie.
# ================================================================================================
echo 60 >"$(fixt "https://zly-cert.example/" rc)"
run_smoke tlsarg https://zly-cert.example
[ $? = 1 ] && grep -q 'błąd TLS (curl 60' "$WORK/tlsarg.out"
check "błąd TLS hosta podanego wprost → błąd" $?

# ================================================================================================
# 6. Tryb --server: hosty z bazy, --resolve, przepustka poza argumentami, LiveKit, djcms.
# ================================================================================================
SRV="$WORK/srv"; mkdir -p "$SRV"
TOKEN=0123456789abcdefghijklmnopqrstuvwxyzABCD
cat >"$SRV/.env" <<ENV
SITE_DOMAIN=olimpiada.example
EXTRA_DOMAINS=iqo.example www.iqo.example nowy.example
MAINTENANCE_BYPASS_TOKEN=$TOKEN
LIVEKIT_PROXY=1
DJCMS_ENABLED=1
ENV
site "$O"; site https://iqo.example
echo 60 >"$(fixt "https://nowy.example/" rc)"
echo ok | respond "https://live.olimpiada.example/" 200
echo ok | respond "$O/djcms/healthz/" 200
echo '<html>podgląd</html>' | respond "$O/djcms/preview/" 200
export STUB_HOSTS="olimpiada.example iqo.example obcy.example nowy.example"
run_smoke server --server "$SRV" --report "$WORK/report.txt"
rc=$?
check "--server: kod 0 (błąd TLS hosta innego niż SITE_DOMAIN = ostrzeżenie)" $rc
show_on_fail $rc "$WORK/server.out"
grep -q 'check_domains --hosts' "$WORK/server.docker"
check "--server: hosty z manage.py check_domains --hosts" $?
grep -q -- '--resolve olimpiada.example:443:127.0.0.1' "$WORK/server.curl" &&
  grep -q -- '--resolve iqo.example:443:127.0.0.1' "$WORK/server.curl" &&
  grep -q -- '--resolve s3.example:9000:127.0.0.1' "$WORK/server.curl"
check "--server: każde żądanie przez proxy tego serwera (--resolve …:127.0.0.1, także port bucketu)" $?
grep -q 'obcy.example – pominięte: proxy nie ma bloku' "$WORK/server.out" && ! grep -q 'https://obcy.example/healthz' "$WORK/server.curl"
check "--server: host spoza EXTRA_DOMAINS/SITE_DOMAIN pominięty z komunikatem" $?
grep -q '^  UWAGA .*nowy.example.*błąd TLS' "$WORK/server.out" && grep -q 'ostrzeżeń: 1' "$WORK/server.out"
check "--server: błąd TLS hosta konkursu (certyfikat) = ostrzeżenie, host pominięty" $?
! grep -v '^config: ' "$WORK/server.curl" | grep -qF "$TOKEN" && grep -qF "config: header = \"X-Maintenance-Bypass: $TOKEN\"" "$WORK/server.curl"
check "--server: przepustka prac technicznych wyłącznie w konfiguracji -K - (nie w argumentach curla)" $?
grep -q 'LiveKit https://live.olimpiada.example/ – 200' "$WORK/server.out" &&
  grep -q 'djcms GET https://olimpiada.example/djcms/healthz/ – 200' "$WORK/server.out" &&
  grep -q 'djcms GET https://olimpiada.example/djcms/preview/ – 200' "$WORK/server.out"
check "--server: LiveKit (LIVEKIT_PROXY=1) i djcms (DJCMS_ENABLED=1) sprawdzone" $?
cmp -s "$WORK/server.out" "$WORK/report.txt"
check "--report: kopia wydruku w pliku (treść listu alarmowego)" $?

# SITE_DOMAIN z błędem TLS – to już błąd.
echo 60 >"$(fixt "$O/" rc)"
run_smoke server-tls --server "$SRV"
[ $? = 1 ]
check "--server: błąd TLS samego SITE_DOMAIN = błąd" $?
site "$O"

# web nie odpowiada (obraz bez --hosts albo kontener padł) – hosty z EXTRA_DOMAINS, bez www. duplikatu.
STUB_HOSTS_RC=1 run_smoke fallback --server "$SRV"
grep -q '^== https://iqo.example' "$WORK/fallback.out" && ! grep -q '^== https://www.iqo.example' "$WORK/fallback.out" &&
  grep -q '^== https://olimpiada.example' "$WORK/fallback.out"
check "--server bez odpowiedzi check_domains: SITE_DOMAIN + EXTRA_DOMAINS (bez www. duplikatu)" $?

# djcms padł – błąd.
echo 'Bad Gateway' | respond "$O/djcms/healthz/" 502
run_smoke djdown --server "$SRV"
[ $? = 1 ] && grep -q 'djcms GET https://olimpiada.example/djcms/healthz/ – 502' "$WORK/djdown.out"
check "--server: djcms 502 → błąd" $?
unset STUB_HOSTS

# ================================================================================================
# 7. Złe wywołania – kod 2.
# ================================================================================================
run_smoke usage1; [ $? = 2 ]; check "bez adresów – kod 2" $?
run_smoke usage2 olimpiada.example; [ $? = 2 ]; check "adres bez http(s):// – kod 2" $?
run_smoke usage3 --server "$WORK/nie-ma"; [ $? = 2 ]; check "--server bez .env – kod 2" $?
run_smoke usage4 --nieznana "$O"; [ $? = 2 ]; check "nieznana opcja – kod 2" $?

echo
if [ "$failures" -eq 0 ]; then echo "smoke_test: wszystko ok"; else echo "smoke_test: $failures błędów"; fi
[ "$failures" -eq 0 ]
