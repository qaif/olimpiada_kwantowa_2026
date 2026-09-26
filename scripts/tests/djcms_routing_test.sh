#!/usr/bin/env bash
# Test tras Caddy'ego dla serwisu publicznego na django CMS (docs/tasks/DJ-02.md § 3) – na ŻYWYM
# Caddym, z plikiem z `scripts/render_caddyfile.sh`.
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu; wymaga Dockera):
#   scripts/tests/djcms_routing_test.sh
# Wołany też na końcu scripts/tests/render_caddyfile_test.sh (gdy Docker jest dostępny).
#
# Jak: jeden kontener `caddy:2.8` (obraz usługi `proxy` z docker-compose.yml), w nim
# - Caddy z wygenerowanym plikiem (DJCMS_ENABLED=1, PLATFORM_SUBDOMAINS=1, EXTRA_DOMAINS
#   „fizyczna.test www.fizyczna.test”, SITE_DOMAIN=olimpiada.test) + opcje globalne `local_certs`
#   i `skip_install_trust` (certyfikaty z własnego CA Caddy'ego, bez sieci) – to jedyna zmiana
#   pliku na potrzeby testu;
# - dwie atrapy upstreamów – osobne procesy Caddy'ego nasłuchujące na 127.0.0.2:8000 (`web`)
#   i 127.0.0.3:8000 (`djcms`), nazwy rozwiązywane przez `--add-host`. Odpowiadają swoją nazwą,
#   nagłówkiem `X-Djcms-Mode`, który do nich DOSZEDŁ, i adresem: `upstream=web mode=[] uri=/login/`.
#   Atrapa `web` odpowiada na pytanie o zgodę na certyfikat (`ask`, /internal/tls-allowed) jak
#   aplikacja: 200 WYŁĄCZNIE dla `ekologiczna.olimpiada.test` (konkurs w subdomenie), 404 dla każdej innej
#   nazwy – także `dj.`, `www.` itd. Nazwy stałe muszą więc mieć zwykły certyfikat, a nie on-demand
#   z bloku `*.` (render_caddyfile.sh przypina im politykę TLS); nieznana subdomena – żadnego.
# Tablica § 3 wiersz po wierszu, w obu trybach (DJCMS_PRIMARY=0 i 1): host (domena główna,
# EXTRA_DOMAINS, subdomena platformy, `dj.`, `www.`) × ścieżka (strony publiczne, adresy aplikacji,
# konkurs pod prefiksem, `/djcms/*`, pliki redaktorów, statyki, `/internal/*`) × ciasteczko
# `djcms_view` × podrobiony `X-Djcms-Mode` od klienta. Na końcu: djcms leży (502 → strona prac
# technicznych 503) i przerwa planowa (flaga `on`).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RENDER="$ROOT/scripts/render_caddyfile.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/djcms-routing-test.XXXXXX")"
trap '[ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"' EXIT
CADDY_IMAGE="${CADDY_IMAGE:-$(sed -n 's/^    image: \(caddy:.*\)$/\1/p' "$ROOT/docker-compose.yml" | head -1)}"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  printf 'NIE WYKONANO: brak działającego Dockera – ten test niczego nie sprawdził.\n' >&2
  exit 2
fi

failures=0
check() {
  if [ "$2" -eq 0 ]; then
    printf 'ok   %s\n' "$1"
  else
    printf 'FAIL %s\n' "$1"
    failures=$((failures + 1))
  fi
}

# --- Tablica: id | adres | ciasteczko | X-Djcms-Mode od klienta | oczekiwane PRIMARY=0 | PRIMARY=1
#
# Oczekiwane:
#   web / djcms:<tryb>    – odpowiedział ten upstream; `mode=[…]` to nagłówek, który do niego doszedł
#                           (web: zawsze pusty – nagłówek od klienta zdjęty; djcms: nadany przez proxy)
#   …+V                   – odpowiedź ma `Vary: Cookie` (ścieżki publiczne)
#   static                – plik z /srv/static (web)
#   media-csp / media-pdf – plik redaktora z /srv/djcms-media z CSP `sandbox` / bez (PDF)
#   404, 503              – kod odpowiedzi
#   302=<adres>, 301=<adres> – przekierowanie i pierwsze `Location`
#   tls-refused           – brak odpowiedzi HTTP: certyfikatu nie ma (ask odmówił), uścisk TLS pada
# Domena główna spoza nazw „wewnętrznych” Caddy'ego (localhost, *.local…): tylko wtedy adapter
# układa polityki TLS jak dla domeny publicznej – na `localhost` błąd z nazwami stałymi pod `*.`
# (on-demand zamiast zwykłego certyfikatu) nie wychodził.
DOM=olimpiada.test
L=https://$DOM F=https://fizyczna.test E=https://ekologiczna.$DOM
MEDIA=/djcms/media/filer_public/ab/cd/0f1e
ROWS=()
for H in L F E; do
  u="${!H}"
  ROWS+=(
    "$H-root|$u/|-|-|web+V|djcms:primary+V"
    "$H-root-dj|$u/|djcms_view=dj|-|djcms:preview+V|djcms:primary+V"
    "$H-root-wagtail|$u/|djcms_view=wagtail|-|web+V|web+V"
    "$H-root-cookies|$u/|a=1; djcms_view=dj; b=2|-|djcms:preview+V|djcms:primary+V"
    "$H-root-otherval|$u/|djcms_view=djx|-|web+V|djcms:primary+V"
    "$H-page-forged|$u/zadania/|-|primary|web+V|djcms:primary+V"
    "$H-page-forged-dj|$u/zadania/?edycja=2|djcms_view=dj|primary|djcms:preview+V|djcms:primary+V"
    "$H-page-forged-wagtail|$u/wyniki/|djcms_view=wagtail|preview|web+V|web+V"
    "$H-robots|$u/robots.txt|-|-|web+V|djcms:primary+V"
    "$H-sitemap|$u/sitemap.xml|djcms_view=dj|-|djcms:preview+V|djcms:primary+V"
    "$H-favicon|$u/favicon.ico|-|-|web+V|djcms:primary+V"
    "$H-workshops|$u/warsztaty/|-|-|web+V|djcms:primary+V"
    "$H-login|$u/login/|djcms_view=dj|primary|web|web"
    "$H-login-nocookie|$u/login/|-|-|web|web"
    "$H-me|$u/me/zgloszenia/|djcms_view=dj|-|web|web"
    "$H-api|$u/api/v1/competitions/|djcms_view=dj|-|web|web"
    "$H-cms|$u/cms/pages/|djcms_view=dj|-|web|web"
    "$H-documents|$u/documents/5/regulamin.pdf|djcms_view=dj|-|web|web"
    "$H-admin|$u/admin/|djcms_view=dj|-|web|web"
    "$H-healthz|$u/healthz/|-|-|web|web"
    "$H-statusjson|$u/status.json|-|-|web|web"
    "$H-gverify|$u/google0123abcd.html|-|-|web|web"
    "$H-materials|$u/warsztaty/materialy/12/|djcms_view=dj|-|web|web"
    "$H-static|$u/static/css/app.css|djcms_view=dj|-|static|static"
    "$H-djcms-admin|$u/djcms/admin/|-|primary|djcms:preview|djcms:primary"
    "$H-djcms-preview|$u/djcms/preview/?next=/zadania/|djcms_view=wagtail|-|djcms:preview|djcms:primary"
    "$H-djcms-static|$u/djcms/static/dj/css/dj.css|-|-|djcms:preview|djcms:primary"
    "$H-djcms-bare|$u/djcms|-|-|djcms:preview|djcms:primary"
    "$H-media-svg|$u$MEDIA/logo.svg|-|-|media-csp|media-csp"
    "$H-media-html|$u$MEDIA/Strona.HTML|-|-|media-csp|media-csp"
    "$H-media-pdf|$u$MEDIA/regulamin.PDF|-|-|media-pdf|media-pdf"
    "$H-internal-tls|$u/internal/tls-allowed?domain=x.$DOM|djcms_view=dj|-|404|404"
    "$H-internal-api|$u/internal/djcms/v2/competitions|djcms_view=dj|-|404|404"
    "$H-prefix-page|$u/druga/zadania/|-|-|web+V|djcms:primary+V"
    "$H-prefix-page-dj|$u/druga/|djcms_view=dj|-|djcms:preview+V|djcms:primary+V"
  )
done
# Konkurs pod prefiksem ścieżki – wyłącznie na domenie głównej (DJ-02 D3): `/<prefiks>/<adres
# aplikacji>` do web, `/<prefiks>/djcms/…` do djcms. Na innych hostach te same ścieżki są zwykłymi
# ścieżkami publicznymi (tam prefiksów nie ma – aplikacja by ich nie rozstrzygnęła).
ROWS+=(
  "L-prefix-login|$L/druga/login/|djcms_view=dj|primary|web|web"
  "L-prefix-me|$L/druga/me/|-|-|web|web"
  "L-prefix-status|$L/druga/status.json|djcms_view=dj|-|web|web"
  # `/<prefiks>/internal/…` – odmowa w proxy (adres aplikacji z prefiksem nie może prowadzić do /internal/).
  "L-prefix-internal|$L/druga/internal/djcms/v2/competitions|-|-|404|404"
  "L-prefix-internal-tls|$L/druga/internal/tls-allowed?domain=ekologiczna.$DOM|djcms_view=dj|-|404|404"
  "L-prefix-djcms|$L/druga/djcms/admin/|-|primary|djcms:preview|djcms:primary"
  "F-prefix-login|$F/druga/login/|-|-|web+V|djcms:primary+V"
  "F-prefix-djcms|$F/druga/djcms/admin/|-|-|web+V|djcms:primary+V"
  "E-prefix-login|$E/druga/login/|djcms_view=dj|-|djcms:preview+V|djcms:primary+V"
  # dj.: wyłącznie przekierowanie (D1), odmowa /internal/* zostaje (S4).
  "D-page|https://dj.$DOM/zadania/?a=1|-|-|302=https://$DOM/djcms/preview/?next=/zadania/?a=1|302=https://$DOM/zadania/?a=1"
  "D-root|https://dj.$DOM/|djcms_view=dj|-|302=https://$DOM/djcms/preview/?next=/|302=https://$DOM/"
  "D-internal|https://dj.$DOM/internal/djcms/v2/competitions|-|-|404|404"
  # www. domeny głównej – nazwa stała pod `*.`: zwykły certyfikat (ask by jej odmówił), 301.
  "T-www|https://www.$DOM/zadania/|-|-|301=https://$DOM/zadania/|301=https://$DOM/zadania/"
  # Subdomena bez konkursu: ask odmawia – certyfikatu nie ma, uścisk TLS pada (żadnej odpowiedzi).
  "T-unknown|https://nieznany.$DOM/|-|-|tls-refused|tls-refused"
  # www. z EXTRA_DOMAINS – bez zmian (301 na domenę konkursu).
  "W-redirect|https://www.fizyczna.test/zadania/|-|-|301=https://fizyczna.test/zadania/|301=https://fizyczna.test/zadania/"
  # djcms leży: 502 z reverse_proxy → strona prac technicznych (503) – także dla stron publicznych
  # w trybie primary; adresy aplikacji działają dalej.
  "!kill-djcms"
  "X-down-public|$L/|djcms_view=dj|-|503|503"
  "X-down-djcms|$L/djcms/admin/|-|-|503|503"
  "X-down-login|$L/login/|-|-|web|web"
  # Przerwa planowa (flaga /srv/maintenance/on) zasłania cały blok, także ścieżki djcms.
  "!maintenance-on"
  "M-djcms|$L/djcms/admin/|-|-|503|503"
  "M-public|$F/|djcms_view=dj|-|503|503"
  "M-login|$E/login/|-|-|503|503"
)

# --- Przebieg kontenera --------------------------------------------------------------------------
cat >"$WORK/inside.sh" <<'INSIDE'
set -u
d=/srv/djcms-media/filer_public/ab/cd/0f1e
mkdir -p /srv/static/css /srv/maintenance "$d"
echo "static-app-css" > /srv/static/css/app.css
echo '<svg xmlns="http://www.w3.org/2000/svg"/>' > "$d/logo.svg"
echo "<p>x</p>" > "$d/Strona.HTML"
echo "%PDF-1.4" > "$d/regulamin.PDF"
stub() {  # stub <nazwa> <adres>
  cat > "/tmp/stub-$1" <<EOF
{
	admin off
	auto_https off
	persist_config off
}
:8000 {
	bind $2
	# Zgoda na certyfikat (on_demand_tls ask) jak w aplikacji: tylko konkurs w subdomenie platformy.
	handle /internal/tls-allowed {
		@tls_ok query domain=ekologiczna.olimpiada.test
		respond @tls_ok 200
		respond 404
	}
	handle {
		# Upstream z własnym `Vary` (jak Django) – `Vary: Cookie` od proxy ma zostać dopisane, nie zastąpione.
		header Vary Accept-Language
		respond "upstream=$1 mode=[{http.request.header.X-Djcms-Mode}] uri={uri}" 200
	}
}
EOF
  caddy run --config "/tmp/stub-$1" --adapter caddyfile >"/tmp/stub-$1.log" 2>&1 &
  echo $! > "/tmp/stub-$1.pid"
}
stub web 127.0.0.2
stub djcms 127.0.0.3
caddy run --config /tmp/Caddyfile --adapter caddyfile >/tmp/caddy.log 2>&1 &
for _ in $(seq 1 40); do wget -qO- http://127.0.0.1:2019/config/ >/dev/null 2>&1 && break; sleep 0.25; done
sleep 1
while IFS='|' read -r id url cookie xmode; do
  case "$id" in
    '!kill-djcms') kill "$(cat /tmp/stub-djcms.pid)"; sleep 0.5; continue ;;
    '!maintenance-on') touch /srv/maintenance/on; continue ;;
  esac
  set -- -S -O - -T 15 --no-check-certificate
  [ "$cookie" != "-" ] && set -- "$@" --header "Cookie: $cookie"
  [ "$xmode" != "-" ] && set -- "$@" --header "X-Djcms-Mode: $xmode"
  echo "== $id"
  wget "$@" "$url" 2>&1
  echo
done < /tmp/requests
echo "== caddy.log"
grep -iE '"level":"(error|warn)"' /tmp/caddy.log | grep -v 'Unnecessary header_up' | tail -n 20
INSIDE

run_primary() {  # run_primary <0|1> – render, kontener, surowe wyjście w $WORK/p<P>.out
  local p="$1"
  EXTRA_DOMAINS="fizyczna.test www.fizyczna.test" PLATFORM_SUBDOMAINS=1 DJCMS_ENABLED=1 DJCMS_PRIMARY="$p" \
    CADDYFILE_OUT="$WORK/p$p.caddy" bash "$RENDER" >/dev/null 2>"$WORK/p$p.render.err"
  check "render_caddyfile.sh przy DJCMS_PRIMARY=$p" $?
  # Jedyna zmiana pliku na potrzeby testu: certyfikaty z lokalnego CA (bez ACME i sieci).
  awk '{ print } $0 == "    email {$ACME_EMAIL}" { print "    local_certs"; print "    skip_install_trust" }' \
    "$WORK/p$p.caddy" >"$WORK/p$p.test.caddy"
  {
    printf "cat > /tmp/Caddyfile <<'__CADDYFILE__'\n"; cat "$WORK/p$p.test.caddy"; printf '__CADDYFILE__\n'
    printf "cat > /tmp/requests <<'__REQUESTS__'\n"
    for row in "${ROWS[@]}"; do
      IFS='|' read -r id url cookie xmode _ <<<"$row"
      printf '%s|%s|%s|%s\n' "$id" "${url:-}" "${cookie:-}" "${xmode:-}"
    done
    printf '__REQUESTS__\n'
    cat "$WORK/inside.sh"
  } | MSYS_NO_PATHCONV=1 docker run --rm -i \
      --add-host $DOM:127.0.0.1 --add-host dj.$DOM:127.0.0.1 --add-host ekologiczna.$DOM:127.0.0.1 \
      --add-host www.$DOM:127.0.0.1 --add-host nieznany.$DOM:127.0.0.1 \
      --add-host fizyczna.test:127.0.0.1 --add-host www.fizyczna.test:127.0.0.1 \
      --add-host web:127.0.0.2 --add-host djcms:127.0.0.3 \
      -e SITE_DOMAIN=$DOM -e S3_PUBLIC_ADDRESS=$DOM:9000 -e ACME_EMAIL=ops@example.org \
      -e MAX_UPLOAD_MB=25 "$CADDY_IMAGE" sh >"$WORK/p$p.out" 2>&1
}

section() {  # section <plik> <id> – wyjście wget jednego żądania
  awk -v h="== $2" '$0 == h {on=1; next} /^== / {on=0} on' "$1"
}

verify() {  # verify <plik> <id> <oczekiwane> – kod 0, gdy się zgadza; opis różnicy na stdout
  local out exp="$3" vary=0 status loc
  out="$(section "$1" "$2")"
  status="$(printf '%s\n' "$out" | grep -m1 -E '^  HTTP/' | awk '{print $2}')"
  case "$exp" in *+V) vary=1; exp="${exp%+V}" ;; esac
  case "$exp" in
    web|djcms:*)
      local up="${exp%%:*}" mode=""
      [ "$up" = djcms ] && mode="${exp#djcms:}"
      printf '%s\n' "$out" | grep -qF "upstream=$up mode=[$mode] " || { echo "oczekiwano upstream=$up mode=[$mode], jest: $(printf '%s\n' "$out" | grep -m1 'upstream=' || echo "status $status")"; return 1; }
      ;;
    static)
      printf '%s\n' "$out" | grep -qx 'static-app-css' || { echo "brak pliku statycznego (status $status)"; return 1; }
      ;;
    media-csp|media-pdf)
      [ "$status" = 200 ] || { echo "status $status"; return 1; }
      printf '%s\n' "$out" | grep -qF 'X-Content-Type-Options: nosniff' || { echo "brak nosniff"; return 1; }
      printf '%s\n' "$out" | grep -qF 'Cache-Control: public, max-age=86400' || { echo "brak Cache-Control"; return 1; }
      if [ "$exp" = media-csp ]; then
        printf '%s\n' "$out" | grep -qF "Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox" || { echo "brak CSP sandbox"; return 1; }
      else
        ! printf '%s\n' "$out" | grep -q 'Content-Security-Policy' || { echo "PDF z CSP"; return 1; }
      fi
      ;;
    tls-refused)
      [ -z "$status" ] || { echo "status $status – certyfikat wystawiony mimo odmowy ask"; return 1; }
      printf '%s\n' "$out" | grep -qiE 'ssl|tls|handshake|certificate|unable to establish' || { echo "brak błędu TLS w wyjściu wget"; return 1; }
      ;;
    30[12]=*)
      loc="$(printf '%s\n' "$out" | grep -m1 -E '^  Location: ' | sed 's/^  Location: //' | tr -d '\r')"
      [ "$status" = "${exp%%=*}" ] && [ "$loc" = "${exp#*=}" ] || { echo "status $status, Location „$loc”"; return 1; }
      ;;
    *)
      [ "$status" = "$exp" ] || { echo "status $status"; return 1; }
      ;;
  esac
  if [ "$vary" = 1 ]; then
    printf '%s\n' "$out" | grep -qiE '^  Vary: .*Cookie' || { echo "brak Vary: Cookie"; return 1; }
  fi
  return 0
}

for p in 0 1; do
  run_primary "$p"
  grep -q '^== caddy.log$' "$WORK/p$p.out"
  check "DJCMS_PRIMARY=$p: kontener z Caddym przeszedł całą tablicę" $?
  bad=0
  for row in "${ROWS[@]}"; do
    case "$row" in '!'*) continue ;; esac
    IFS='|' read -r id url cookie xmode e0 e1 <<<"$row"
    exp="$e0"; [ "$p" = 1 ] && exp="$e1"
    why="$(verify "$WORK/p$p.out" "$id" "$exp")"
    rc=$?
    check "PRIMARY=$p $id: ${url#https://} [${cookie}] [X-Djcms-Mode: ${xmode}] → $exp${why:+ – $why}" $rc
    [ $rc -eq 0 ] || bad=1
  done
  if [ "$bad" = 1 ]; then
    printf '     --- ostrzeżenia i błędy Caddy'"'"'ego (PRIMARY=%s):\n' "$p"
    section "$WORK/p$p.out" caddy.log | sed 's/^/     /'
  fi
done

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło (KEEP_WORK=1 zostawia %s).\n' "$failures" "$WORK"
  exit 1
fi
printf '\nWszystkie testy tras djcms na żywym Caddym przeszły.\n'
