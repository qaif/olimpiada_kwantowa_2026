#!/usr/bin/env bash
# Test scripts/mail_add_domain.sh na atrapie compose'a (MAIL-01 § 2) – bez Dockera i bez sieci.
#
# Uruchomienie: scripts/tests/mail_add_domain_test.sh
#
# Atrapa (`COMPOSE`) udaje kontener `mail` katalogiem z kluczami, `web` – gotową odpowiedzią
# `check_mail_dns --suggest`, i zapisuje każde wywołanie do dziennika. Sprawdza:
#   1. zła nazwa domeny / domena instalacji / wariant '*' – odmowa ZANIM cokolwiek się zmieni,
#   2. pierwsze uruchomienie: domena dopisana do ALLOWED_SENDER_DOMAINS (w cudzysłowie, kopia .env),
#      klucz wygenerowany z selektorem i rozmiarem, `up -d mail` (i NIC z web/worker/beat),
#   3. rekordy: SPF z sugestii aplikacji, DKIM sklejony z fragmentów BIND, istniejący DMARC
#      „nie dodawaj drugiego”, „MX nie ruszaj”, DKIM_P do porównania, plik chmod 600,
#   4. drugie uruchomienie: .env bez zmian, klucz nie generowany, bez restartu – te same rekordy,
#   5. brak wpisu ALLOWED_SENDER_DOMAINS w .env → SITE_DOMAIN + nowa domena; --no-restart,
#   6. web niedostępny → bazowy SPF z instrukcją scalenia; brak DMARC → p=none z planem,
#   7. --check woła opendkim-testkey i check_mail_dns z kluczem; kod wyjścia z wyniku.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/scripts/mail_add_domain.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/mail-add-domain-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}

cat >"$WORK/compose" <<'STUB'
#!/usr/bin/env bash
# Atrapa `docker compose`: stan w $STUB_DIR (keys/, log, odpowiedzi).
D="$STUB_DIR"
printf '%s\n' "$*" >>"$D/log"
[ "${1:-}" = exec ] || exit 0
shift; [ "${1:-}" = -T ] && shift
service="$1"; shift
case "$service:$*" in
  mail:true) exit 0 ;;
  "mail:test -f "*) name="$(basename "$3")"; [ -f "$D/keys/$name" ] ;;
  "mail:sh -c "*)
    # sh -c '<skrypt>' sh <selektor> <domena> <bity>
    sel="$5"; dom="$6"; bits="$7"
    printf '%s %s %s\n' "$sel" "$dom" "$bits" >"$D/genkey"
    : >"$D/keys/$dom.private"
    printf '%s._domainkey\tIN\tTXT\t( "v=DKIM1; h=sha256; k=rsa; s=email; "\n\t  "p=MIIBIjAN%s"\n\t  "AQAB" )  ; ----- DKIM key\n' \
      "$sel" "$(printf '%s' "$dom" | tr -d .)" >"$D/keys/$dom.txt"
    ;;
  "mail:cat "*) cat "$D/keys/$(basename "$2")" 2>/dev/null ;;
  "mail:opendkim-testkey "*) echo "opendkim-testkey: key OK"; exit "${STUB_TESTKEY_RC:-0}" ;;
  web:*--suggest*)
    [ -f "$D/web_down" ] && exit 1
    cat "$D/suggest" ;;
  web:*check_mail_dns*) echo "wynik: ZWERYFIKOWANA"; exit "${STUB_CHECK_RC:-0}" ;;
  *) echo "atrapa: nieznane wywołanie $service $*" >&2; exit 99 ;;
esac
STUB
chmod +x "$WORK/compose"

setup() {  # setup <katalog> <treść .env>
  mkdir -p "$1/stub/keys"
  printf '%s\n' "$2" >"$1/.env"
  chmod 600 "$1/.env"
  : >"$1/stub/log"
  printf '%s\n' \
    "spf=v=spf1 ip4:203.0.113.7 -all" \
    "spf_note=Domena ma rekord „nie wysyłam poczty” (v=spf1 -all). ZMIEŃ go na wartość wyżej." \
    "dmarc=v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s" \
    "mx=" >"$1/stub/suggest"
}
run() {  # run <katalog> <argumenty…>
  local dir="$1"; shift
  REMOTE_DIR="$dir" STUB_DIR="$dir/stub" COMPOSE="$WORK/compose" MAIL_PUBLIC_IP=203.0.113.7 \
    DMARC_RUA=ops@qaif.test bash "$SCRIPT" "$@"
}

# --- 1. odmowy przed zmianą ----------------------------------------------------------------------
A="$WORK/a"
setup "$A" $'SITE_DOMAIN=platforma.test\nALLOWED_SENDER_DOMAINS=platforma.test'
cp "$A/.env" "$WORK/env.orig"
for bad in 'noreply@iqo.test' 'iqo' 'iqo.test;rm' '-iqo.test' 'a b.test'; do
  out="$(run "$A" "$bad" 2>&1)"; rc=$?
  [ "$rc" -ne 0 ] && cmp -s "$A/.env" "$WORK/env.orig" && [ ! -s "$A/stub/log" ]
  check "odmowa dla '$bad' – bez zmian i bez compose'a" $?
done
out="$(run "$A" platforma.test 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'domena instalacji' && cmp -s "$A/.env" "$WORK/env.orig"
check "domena instalacji – odmowa z odesłaniem do kroku 7/8" $?
W="$WORK/w"
setup "$W" $'SITE_DOMAIN=platforma.test\nALLOWED_SENDER_DOMAINS=*'
out="$(run "$W" iqo.test 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'wariant B'
check "ALLOWED_SENDER_DOMAINS='*' – odmowa (wariant B)" $?

# --- 2. pierwsze uruchomienie -------------------------------------------------------------------
out="$(run "$A" IQO.test 2>&1)"; rc=$?
check "pierwsze uruchomienie – kod 0" "$rc"
grep -qx 'ALLOWED_SENDER_DOMAINS="platforma.test iqo.test"' "$A/.env" && grep -qx 'SITE_DOMAIN=platforma.test' "$A/.env"
check ".env: domena dopisana (małe litery, w cudzysłowie), reszta pliku nietknięta" $?
ls "$A"/.env.bak-mail-* >/dev/null 2>&1 && cmp -s "$(ls "$A"/.env.bak-mail-* | head -n 1)" "$WORK/env.orig"
check "kopia .env sprzed zmiany" $?
[ "$(stat -c %a "$A/.env" 2>/dev/null || echo 600)" = 600 ]
check ".env zostaje chmod 600" $?
[ "$(cat "$A/stub/genkey")" = "olimpiada iqo.test 2048" ]
check "klucz: selektor olimpiada, domena, 2048 bitów" $?
grep -qx 'up -d mail' "$A/stub/log" && ! grep -Eq '(up|restart).*(web|worker|beat)' "$A/stub/log"
check "odtworzony wyłącznie mail (web/worker/beat – decyzja operatora)" $?
F="$A/mail-dns-iqo.test.txt"
grep -qx '   v=spf1 ip4:203.0.113.7 -all' "$F"
check "SPF z sugestii aplikacji" $?
grep -qx '   v=DKIM1; h=sha256; k=rsa; s=email; p=MIIBIjANiqotestAQAB' "$F"
check "DKIM sklejony z fragmentów BIND w jedną wartość" $?
grep -q 'host: olimpiada._domainkey' "$F" && grep -q 'Domena MA już DMARC: v=DMARC1; p=reject' "$F" && grep -q 'NIE dodawaj drugiego rekordu _dmarc' "$F"
check "DKIM pod selektorem; istniejący DMARC – bez drugiego rekordu" $?
grep -q 'MX i wszystkie pozostałe rekordy – NIE RUSZAJ' "$F" && grep -qx 'DKIM_P=MIIBIjANiqotestAQAB' "$F"
check "MX bez zmian; DKIM_P do porównania przy wdrożeniu" $?
grep -q '_report._dmarc.qaif.test' "$F"
check "raporty DMARC w obcej domenie – rekord zgody tamtej domeny" $?
[ "$(stat -c %a "$F" 2>/dev/null || echo 600)" = 600 ] && printf '%s' "$out" | grep -q 'Zapisano: '
check "plik rekordów chmod 600, wypisany na ekran" $?

# --- 4. idempotencja ----------------------------------------------------------------------------
cp "$A/.env" "$WORK/env.after1"; cp "$F" "$WORK/dns.after1"; : >"$A/stub/log"; rm -f "$A/stub/genkey"
out="$(run "$A" iqo.test 2>&1)"; rc=$?
check "drugie uruchomienie – kod 0" "$rc"
cmp -s "$A/.env" "$WORK/env.after1" && [ "$(ls "$A"/.env.bak-mail-* | wc -l)" -eq 1 ]
check "drugie uruchomienie: .env bez zmian, bez nowej kopii" $?
[ ! -e "$A/stub/genkey" ] && ! grep -Eq '^(up|restart)' "$A/stub/log" && printf '%s' "$out" | grep -q 'NIE nadpisuję'
check "drugie uruchomienie: klucz nie generowany, bez restartu" $?
diff <(grep -v '^# Wygenerowane' "$F") <(grep -v '^# Wygenerowane' "$WORK/dns.after1") >/dev/null
check "drugie uruchomienie: te same rekordy" $?

# --- 5. brak wpisu w .env, --no-restart ---------------------------------------------------------
B="$WORK/b"
setup "$B" $'SITE_DOMAIN=platforma.test\nEMAIL_URL=smtp://mail:587'
out="$(run "$B" --no-restart iqo.test 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && grep -qx 'ALLOWED_SENDER_DOMAINS="platforma.test iqo.test"' "$B/.env" && grep -qx 'EMAIL_URL=smtp://mail:587' "$B/.env"
check "bez wpisu w .env: SITE_DOMAIN + nowa domena dopisane na końcu" $?
! grep -Eq '^(up|restart)' "$B/stub/log" && printf '%s' "$out" | grep -q 'up -d mail'
check "--no-restart: bez restartu, z poleceniem do wykonania" $?

# --- 6. web niedostępny, brak DMARC ----------------------------------------------------------------
C="$WORK/c"
setup "$C" $'SITE_DOMAIN=platforma.test'
touch "$C/stub/web_down"
run "$C" iqo.test >/dev/null 2>&1
grep -qx '   v=spf1 ip4:203.0.113.7 -all' "$C/mail-dns-iqo.test.txt" && grep -q 'NIE dodawaj drugiego – dopisz do istniejącego' "$C/mail-dns-iqo.test.txt"
check "web niedostępny: bazowy SPF i instrukcja scalenia" $?
grep -qx '   v=DMARC1; p=none; rua=mailto:ops@qaif.test; adkim=r; aspf=r; fo=1' "$C/mail-dns-iqo.test.txt" && grep -q 'p=quarantine' "$C/mail-dns-iqo.test.txt"
check "brak DMARC: p=none z planem przejścia na quarantine" $?

D="$WORK/d"
setup "$D" $'SITE_DOMAIN=platforma.test\nMAIL_PUBLIC_IP=198.51.100.9\nDMARC_RUA=raporty@iqo.test'
touch "$D/stub/web_down"
REMOTE_DIR="$D" STUB_DIR="$D/stub" COMPOSE="$WORK/compose" MAIL_PUBLIC_IP= DMARC_RUA= bash "$SCRIPT" iqo.test >/dev/null 2>&1
grep -qx '   v=spf1 ip4:198.51.100.9 -all' "$D/mail-dns-iqo.test.txt" && grep -q 'rua=mailto:raporty@iqo.test' "$D/mail-dns-iqo.test.txt" \
  && ! grep -q '_report._dmarc' "$D/mail-dns-iqo.test.txt"
check "MAIL_PUBLIC_IP i DMARC_RUA z .env; raporty w tej samej domenie – bez rekordu zgody" $?

# --- 7. --check ---------------------------------------------------------------------------------
: >"$A/stub/log"
run "$A" --check iqo.test >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && grep -q '^exec -T mail opendkim-testkey -d iqo.test -s olimpiada -k /etc/opendkim/keys/iqo.test.private' "$A/stub/log" \
  && grep -q 'check_mail_dns iqo.test --ip 203.0.113.7 --selector olimpiada --dkim-public-key MIIBIjANiqotestAQAB' "$A/stub/log"
check "--check: opendkim-testkey i check_mail_dns z kluczem relaya, kod 0" $?
cmp -s "$A/.env" "$WORK/env.after1"
check "--check niczego nie zmienia w .env" $?
STUB_CHECK_RC=1 run "$A" --check iqo.test >/dev/null 2>&1; rc=$?
[ "$rc" -ne 0 ]
check "--check: niezweryfikowana domena = kod różny od 0" $?
out="$(run "$B" --check inna.test 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'brak klucza'
check "--check bez klucza – odmowa z poleceniem dodania" $?

echo
if [ "$failures" -eq 0 ]; then echo "mail_add_domain: wszystko OK"; else echo "mail_add_domain: $failures błędów"; exit 1; fi
