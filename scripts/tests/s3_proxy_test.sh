#!/usr/bin/env bash
# Test bloku S3 Caddy'ego (`{$S3_PUBLIC_ADDRESS}` w deploy/Caddyfile) na ŻYWYM Caddym.
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu; wymaga Dockera):
#   scripts/tests/s3_proxy_test.sh
# Wołany też na końcu scripts/tests/render_caddyfile_test.sh (gdy Docker jest dostępny).
#
# Co pilnuje (audyt bezpieczeństwa z 1.10.2026, komentarz nad blokiem w deploy/Caddyfile):
# - API MinIO spod `/minio/*` (admin, metryki, kworum) odpowiada 404 z proxy i NIE dochodzi do MinIO
#   – także z wielkimi literami, `%2F`, podwójnym ukośnikiem i `/./`; sondy życia
#   `/minio/health/live|ready` (monitor nr 4, deploy/monitoring/README.md) przechodzą;
# - zwykłe ścieżki bucketów (presigned GET/PUT, części wgrywania `?partNumber=&uploadId=`, `Range`)
#   przechodzą bez zmian, łącznie z bucketem o nazwie zaczynającej się od „minio”;
# - nagłówki bezpieczeństwa: `nosniff`, HSTS i CSP `sandbox` – każdy DOKŁADNIE raz i w naszym
#   brzmieniu, mimo że atrapa MinIO wysyła własne (jak prawdziwy MinIO: HSTS z includeSubDomains,
#   CSP block-all-mixed-content); PDF bez CSP `sandbox`; CORS i ETag od MinIO nietknięte; 206 dla `Range`.
#
# Jak: jeden kontener z obrazem usługi `proxy` (caddy z docker-compose.yml), w nim Caddy z plikiem
# wygenerowanym przez scripts/render_caddyfile.sh (bez przełączników = deploy/Caddyfile co do bajtu)
# plus `local_certs` (bez ACME i sieci) i atrapa MinIO – drugi proces Caddy'ego na 127.0.0.4:9000
# (`--add-host minio:127.0.0.4`). S3 pod `<domena>:9000`, czyli tak jak na produkcji.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RENDER="$ROOT/scripts/render_caddyfile.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/s3-proxy-test.XXXXXX")"
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

DOM=olimpiada.test
S3="https://$DOM:9000"

# id | ścieżka | nagłówek żądania (albo -) | oczekiwane
#   upstream   – odpowiedziała atrapa MinIO (treść `upstream=minio …`), z nagłówkami bezpieczeństwa i CSP
#   file       – plik z atrapy (200), z CSP;  pdf – plik PDF (200), BEZ CSP;  range – 206 + Content-Range
#   404        – odpowiedź proxy, do MinIO nic nie doszło
ROWS=(
  "obj-svg|/public-media/original_images/logo.svg|-|file"
  "obj-html|/public-media/documents/strona.html|-|file"
  "obj-pdf|/public-media/documents/regulamin.PDF|-|pdf"
  "obj-range|/public-media/film.mp4|RANGE|range"
  "presigned-get|/submissions/1/2/abc/def.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=00|-|upstream"
  "part-upload|/submissions/warsztaty/k.mp4?partNumber=3&uploadId=xyz&X-Amz-Signature=00|-|upstream"
  "multipart-init|/submissions/warsztaty/k.mp4?uploads|-|upstream"
  "bucket-miniox|/miniox/obiekt.txt|-|upstream"
  "bucket-root|/|-|upstream"
  "health-live|/minio/health/live|-|upstream"
  "health-ready|/minio/health/ready|-|upstream"
  "health-cluster|/minio/health/cluster|-|404"
  "admin|/minio/admin/v3/info|-|404"
  "admin-upper|/MINIO/admin/v3/info|-|404"
  "admin-enc|/minio%2Fadmin/v3/info|-|404"
  "admin-dslash|//minio/admin/v3/info|-|404"
  "admin-dot|/./minio/admin/v3/info|-|404"
  "metrics-v2|/minio/v2/metrics/cluster|-|404"
  "metrics-v3|/minio/metrics/v3/cluster/health|-|404"
  "prometheus|/minio/prometheus/metrics|-|404"
  "kms|/minio/kms/v1/key/list|-|404"
  "bare|/minio|-|404"
  # Atrapa dostaje ścieżkę surową (sprawdzone: `//miniox/./a.txt` dochodzi do niej co do znaku), więc
  # 404 dla wariantów z `//` i `/./` wyżej to dopasowanie Caddy'ego, a nie normalizacja klienta.
  "raw-path|//miniox/./a.txt|-|upstream"
)

cat >"$WORK/inside.sh" <<'INSIDE'
set -u
mkdir -p /srv/s3/public-media/original_images /srv/s3/public-media/documents /srv/maintenance
echo '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>' > /srv/s3/public-media/original_images/logo.svg
echo '<script>alert(1)</script>' > /srv/s3/public-media/documents/strona.html
echo '%PDF-1.4' > /srv/s3/public-media/documents/regulamin.PDF
printf 'ABCDEFGHIJ' > /srv/s3/public-media/film.mp4
# Atrapa MinIO: własne nagłówki „bezpieczeństwa” jak prawdziwy MinIO (addCustomHeadersMiddleware)
# oraz CORS i ETag, których przeglądarka koordynatora potrzebuje przy wgrywaniu.
cat > /tmp/stub-minio <<'EOF'
{
	admin off
	auto_https off
	persist_config off
}
:9000 {
	bind 127.0.0.4
	header {
		X-Content-Type-Options nosniff
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		Content-Security-Policy block-all-mixed-content
		Access-Control-Allow-Origin "https://olimpiada.test"
		Access-Control-Expose-Headers ETag
		Vary Origin
	}
	handle /public-media/* {
		root * /srv/s3
		file_server
	}
	handle {
		header ETag "\"stub-etag\""
		respond "upstream=minio uri={uri}" 200
	}
}
EOF
caddy run --config /tmp/stub-minio --adapter caddyfile >/tmp/stub.log 2>&1 &
caddy run --config /tmp/Caddyfile --adapter caddyfile >/tmp/caddy.log 2>&1 &
for _ in $(seq 1 40); do wget -qO- http://127.0.0.1:2019/config/ >/dev/null 2>&1 && break; sleep 0.25; done
sleep 1
while IFS='|' read -r id path hdr; do
  echo "== $id"
  if [ "$hdr" = "RANGE" ]; then
    # Busybox wget traktuje 206 na zwykłe żądanie z `Range` jako błąd i nie pokazuje nagłówków;
    # `-c` (dokończenie pobierania) wysyła `Range: bytes=<rozmiar pliku>-` sam i 206 przyjmuje.
    printf 'XXXX' > /tmp/part
    wget -S -c -T 15 --no-check-certificate -O /tmp/part "https://olimpiada.test:9000$path" 2>&1
    echo
    echo "content=$(cat /tmp/part)"
    continue
  fi
  set -- -S -O - -T 15 --no-check-certificate
  [ "$hdr" != "-" ] && set -- "$@" --header "$hdr"
  wget "$@" "https://olimpiada.test:9000$path" 2>&1
  echo
done < /tmp/requests
echo "== caddy.log"
grep -iE '"level":"(error|warn)"' /tmp/caddy.log | tail -n 20
INSIDE

CADDYFILE_OUT="$WORK/s3.caddy" EXTRA_DOMAINS="" PLATFORM_SUBDOMAINS="" DJCMS_ENABLED="" DJCMS_PRIMARY="" \
  bash "$RENDER" >/dev/null 2>"$WORK/render.err"
check "render_caddyfile.sh bez przełączników" $?
awk '{ print } $0 == "    email {$ACME_EMAIL}" { print "    local_certs"; print "    skip_install_trust" }' \
  "$WORK/s3.caddy" >"$WORK/s3.test.caddy"
{
  printf "cat > /tmp/Caddyfile <<'__CADDYFILE__'\n"; cat "$WORK/s3.test.caddy"; printf '__CADDYFILE__\n'
  printf "cat > /tmp/requests <<'__REQUESTS__'\n"
  for row in "${ROWS[@]}"; do
    IFS='|' read -r id path hdr _ <<<"$row"
    printf '%s|%s|%s\n' "$id" "$path" "$hdr"
  done
  printf '__REQUESTS__\n'
  cat "$WORK/inside.sh"
} | MSYS_NO_PATHCONV=1 docker run --rm -i \
    --add-host $DOM:127.0.0.1 --add-host minio:127.0.0.4 --add-host web:127.0.0.2 \
    -e SITE_DOMAIN=$DOM -e S3_PUBLIC_ADDRESS=$DOM:9000 -e ACME_EMAIL=ops@example.org \
    -e MAX_UPLOAD_MB=25 "$CADDY_IMAGE" sh >"$WORK/out" 2>&1

grep -q '^== caddy.log$' "$WORK/out"
check "kontener z Caddym przeszedł całą listę żądań" $?

section() { awk -v h="== $1" '$0 == h {on=1; next} /^== / {on=0} on' "$WORK/out"; }
count() { printf '%s\n' "$1" | grep -ciE "^  $2: " || true; }

verify() {  # verify <id> <oczekiwane> <ścieżka> – kod 0, gdy się zgadza; opis różnicy na stdout
  local out status exp="$2"
  out="$(section "$1")"
  status="$(printf '%s\n' "$out" | grep -m1 -E '^  HTTP/' | awk '{print $2}')"
  case "$exp" in
    404)
      [ "$status" = 404 ] || { echo "status $status"; return 1; }
      ! printf '%s\n' "$out" | grep -q 'upstream=minio' || { echo "żądanie doszło do MinIO"; return 1; }
      return 0 ;;
    upstream)
      # Ścieżka z zapytaniem (podpis, uploadId, partNumber) ma dojść do MinIO co do znaku.
      [ "$status" = 200 ] && printf '%s\n' "$out" | grep -qF "upstream=minio uri=$3" \
        || { echo "status $status, brak odpowiedzi atrapy MinIO albo zmieniona ścieżka"; return 1; } ;;
    file|pdf)
      [ "$status" = 200 ] || { echo "status $status"; return 1; } ;;
    range)
      [ "$status" = 206 ] || { echo "status $status (oczekiwano 206)"; return 1; }
      printf '%s\n' "$out" | grep -qiE '^  Content-Range: bytes 4-9/10' || { echo "brak Content-Range"; return 1; }
      printf '%s\n' "$out" | grep -qx 'content=XXXXEFGHIJ' || { echo "dociągnięta reszta pliku inna niż bajty 4–9"; return 1; } ;;
  esac
  [ "$(count "$out" 'X-Content-Type-Options')" = 1 ] || { echo "X-Content-Type-Options nie dokładnie raz"; return 1; }
  [ "$(count "$out" 'Strict-Transport-Security')" = 1 ] && printf '%s\n' "$out" | grep -qE '^  Strict-Transport-Security: max-age=31536000.?$' \
    || { echo "HSTS nie raz albo nie w brzmieniu bloku domeny głównej (includeSubDomains od MinIO?)"; return 1; }
  [ "$(count "$out" 'Access-Control-Allow-Origin')" = 1 ] && [ "$(count "$out" 'Access-Control-Expose-Headers')" = 1 ] \
    || { echo "CORS od MinIO zgubiony albo zdublowany"; return 1; }
  [ "$(count "$out" 'ETag')" = 1 ] || { echo "ETag od MinIO zgubiony albo zdublowany"; return 1; }
  if [ "$exp" = pdf ]; then
    # Bez naszego CSP; własne `block-all-mixed-content` MinIO (nieszkodliwe) może zostać.
    ! printf '%s\n' "$out" | grep -qiE '^  Content-Security-Policy: .*sandbox' || { echo "PDF z CSP sandbox"; return 1; }
  else
    [ "$(count "$out" 'Content-Security-Policy')" = 1 ] &&
      printf '%s\n' "$out" | grep -qF "  Content-Security-Policy: default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; sandbox" \
      || { echo "CSP nie dokładnie raz albo nie nasze (block-all-mixed-content od MinIO?)"; return 1; }
  fi
  return 0
}

for row in "${ROWS[@]}"; do
  IFS='|' read -r id path hdr exp <<<"$row"
  why="$(verify "$id" "$exp" "$path")"
  rc=$?
  check "S3 $id: $path${hdr:+ [$hdr]} → $exp${why:+ – $why}" $rc
  [ $rc -eq 0 ] || section "$id" | sed 's/^/     /' | head -n 25
done
if section caddy.log | grep -q .; then
  printf '     --- ostrzeżenia i błędy Caddy'"'"'ego:\n'
  section caddy.log | sed 's/^/     /'
fi

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło (KEEP_WORK=1 zostawia %s).\n' "$failures" "$WORK"
  exit 1
fi
printf '\nWszystkie testy bloku S3 na żywym Caddym przeszły.\n'
