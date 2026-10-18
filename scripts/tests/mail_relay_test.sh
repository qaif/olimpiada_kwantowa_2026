#!/usr/bin/env bash
# Test deploy/mail/docker-init.d/70-relay.sh na atrapach postconf/postmap (MAIL-04).
#
# Uruchomienie: scripts/tests/mail_relay_test.sh
# Sprawdza: brak MAIL_RELAY_HOST = zero wywołań postconf; domyślne domeny Microsoftu → smtp:[host]:port;
# tablica dopisana do transport_maps z 50-bounces.sh (nie zastępuje), transport_maps ustawiane na końcu;
# TLS `encrypt` do relaya; SASL w pliku 600, hasło nigdy w wyjściu ani w argumentach postconf; bez SASL;
# `*` z wyjątkiem adresu przekierowania MAIL-03; normalizacja hosta i domen; odmowy bez zmian
# w konfiguracji; `secure` z plikiem CA i bez; idempotencja list tablic; włączenie kropką; compose.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/deploy/mail/docker-init.d/70-relay.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/mail-relay-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}

PASSWORD='Tajne:Haslo%s$1'

# postconf: zapisuje wywołania. `-h transport_maps` oddaje tablicę 50-bounces.sh, `-h <parametr>`
# – zawartość pliku `h_<parametr>` (symulacja ponownego startu w tym samym kontenerze), plik
# `fail_<parametr>` – awarię `-e <parametr>=…`.
cat >"$WORK/postconf" <<'STUB'
#!/usr/bin/env bash
dir="$(dirname "$0")"
printf '%s\n' "$*" >>"$dir/postconf.log"
if [ "$1" = "-h" ]; then
  if [ -f "$dir/h_$2" ]; then cat "$dir/h_$2"; exit 0; fi
  [ "$2" = "transport_maps" ] && printf 'lmdb:%s/olimpiada_bounce_transport\n' "$dir"
  exit 0
fi
if [ "$1" = "-e" ]; then
  name="${2%%=*}"
  [ -f "$dir/fail_$name" ] && exit 1
fi
exit 0
STUB
# postmap: zapisuje wywołania i tworzy plik .lmdb; plik `postmap_fails` – awaria.
cat >"$WORK/postmap" <<'STUB'
#!/usr/bin/env bash
dir="$(dirname "$0")"
printf '%s\n' "$*" >>"$dir/postmap.log"
[ -f "$dir/postmap_fails" ] && exit 1
touch "${1#lmdb:}.lmdb"
STUB
chmod +x "$WORK/postconf" "$WORK/postmap"
printf 'CA\n' >"$WORK/ca.crt"

reset() {
  rm -f "$WORK"/*.log "$WORK"/olimpiada_relay_* "$WORK"/h_* "$WORK"/fail_* "$WORK/postmap_fails"
}
run() {
  env -i PATH="$PATH" POSTCONF="$WORK/postconf" POSTMAP="$WORK/postmap" MAIL_BOUNCE_CONF_DIR="$WORK" \
    MAIL_RELAY_CA_FILE="$WORK/ca.crt" "$@" bash "$SCRIPT"
}
# Brak jakiejkolwiek zmiany konfiguracji: ani jednego `postconf -e`.
no_changes() { ! grep -q -- '^-e ' "$WORK/postconf.log" 2>/dev/null; }

# 1. Wyłączony.
reset
out="$(run 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && [ ! -e "$WORK/postconf.log" ] && [ ! -e "$WORK/postmap.log" ] \
  && ! ls "$WORK"/olimpiada_relay_* >/dev/null 2>&1 && printf '%s' "$out" | grep -q 'wyłączony'
check "bez MAIL_RELAY_HOST: zero wywołań postconf/postmap, żadnych plików, kod 0" $?

# 2. Google Workspace z SASL, domyślne domeny.
reset
out="$(run MAIL_RELAY_HOST='[smtp-relay.gmail.com]:587' MAIL_RELAY_USER=relay@platforma.test \
  MAIL_RELAY_PASSWORD="$PASSWORD" 2>&1)"; rc=$?
expected_transport=""
for d in outlook.com hotmail.com live.com msn.com outlook.pl hotmail.pl live.pl passport.com; do
  expected_transport="$expected_transport$d	smtp:[smtp-relay.gmail.com]:587"$'\n'
done
[ "$rc" -eq 0 ] && [ "$(cat "$WORK/olimpiada_relay_transport")"$'\n' = "$expected_transport" ]
check "domyślne domeny Microsoftu (8) → smtp:[smtp-relay.gmail.com]:587, nic więcej" $?
grep -qxF -- "-e transport_maps=lmdb:$WORK/olimpiada_bounce_transport lmdb:$WORK/olimpiada_relay_transport" "$WORK/postconf.log"
check "transport_maps: tablica 50-bounces.sh zachowana jako pierwsza, relay dopisany" $?
[ "$(grep -- '^-e ' "$WORK/postconf.log" | tail -n1)" = "-e transport_maps=lmdb:$WORK/olimpiada_bounce_transport lmdb:$WORK/olimpiada_relay_transport" ]
check "transport_maps ustawiane na końcu (po TLS i SASL)" $?
[ "$(cat "$WORK/olimpiada_relay_tls")" = $'[smtp-relay.gmail.com]:587\tencrypt' ] \
  && grep -qxF -- "-e smtp_tls_policy_maps=lmdb:$WORK/olimpiada_relay_tls" "$WORK/postconf.log" \
  && ! grep -q 'smtp_tls_security_level' "$WORK/postconf.log"
check "TLS do relaya: encrypt w smtp_tls_policy_maps, globalny smtp_tls_security_level nieruszony" $?
[ "$(cat "$WORK/olimpiada_relay_sasl")" = "[smtp-relay.gmail.com]:587	relay@platforma.test:$PASSWORD" ] \
  && grep -qxF -- "-e smtp_sasl_auth_enable=yes smtp_sasl_password_maps=lmdb:$WORK/olimpiada_relay_sasl smtp_sasl_security_options=noanonymous smtp_sasl_tls_security_options=noanonymous" "$WORK/postconf.log" \
  && grep -qxF "lmdb:$WORK/olimpiada_relay_sasl" "$WORK/postmap.log"
check "SASL: wiersz [host]:port login:hasło, postmap, smtp_sasl_* ustawione" $?
! printf '%s' "$out" | grep -qF -- "$PASSWORD" && ! grep -qF -- "$PASSWORD" "$WORK/postconf.log" \
  && ! grep -qF -- "$PASSWORD" "$WORK/postmap.log" && printf '%s' "$out" | grep -q 'SASL jako relay@platforma.test'
check "hasło nigdy w wyjściu skryptu ani w argumentach postconf/postmap" $?
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) printf 'pomiń prawa 600 (system plików Windows)\n' ;;
  *)
    [ "$(stat -c %a "$WORK/olimpiada_relay_sasl")" = "600" ] && [ "$(stat -c %a "$WORK/olimpiada_relay_sasl.lmdb")" = "600" ]
    check "plik SASL i jego .lmdb z prawami 600" $?
    ;;
esac

# 3. Ponowny start w tym samym kontenerze: listy tablic bez duplikatów, cudze tablice zostają.
reset
printf 'lmdb:/etc/postfix/inna_tls lmdb:%s/olimpiada_relay_tls\n' "$WORK" >"$WORK/h_smtp_tls_policy_maps"
printf 'lmdb:%s/olimpiada_relay_sasl\n' "$WORK" >"$WORK/h_smtp_sasl_password_maps"
printf 'lmdb:%s/olimpiada_bounce_transport lmdb:%s/olimpiada_relay_transport\n' "$WORK" "$WORK" >"$WORK/h_transport_maps"
run MAIL_RELAY_HOST='[smtp-relay.gmail.com]:587' MAIL_RELAY_USER=u MAIL_RELAY_PASSWORD=p >/dev/null 2>&1
grep -qxF -- "-e smtp_tls_policy_maps=lmdb:/etc/postfix/inna_tls lmdb:$WORK/olimpiada_relay_tls" "$WORK/postconf.log" \
  && grep -qF -- "smtp_sasl_password_maps=lmdb:$WORK/olimpiada_relay_sasl smtp_sasl_security" "$WORK/postconf.log" \
  && grep -qxF -- "-e transport_maps=lmdb:$WORK/olimpiada_bounce_transport lmdb:$WORK/olimpiada_relay_transport" "$WORK/postconf.log"
check "ponowny start: własne tablice bez duplikatów, cudza tablica TLS zachowana" $?

# 4. Bez SASL (Google: uwierzytelnienie adresem IP), host bez nawiasów i portu.
reset
out="$(run MAIL_RELAY_HOST=SMTP-Relay.Gmail.com 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && grep -qxF $'outlook.com\tsmtp:[smtp-relay.gmail.com]:587' "$WORK/olimpiada_relay_transport" \
  && [ ! -e "$WORK/olimpiada_relay_sasl" ] && ! grep -q 'smtp_sasl' "$WORK/postconf.log" \
  && printf '%s' "$out" | grep -q 'bez SASL' && printf '%s' "$out" | grep -q '587'
check "bez loginu i hasła: bez SASL; host bez portu → [host]:587, małe litery" $?

reset
run MAIL_RELAY_HOST='smtp.eu.mailgun.org:2525' MAIL_RELAY_USER=u MAIL_RELAY_PASSWORD=p >/dev/null 2>&1
grep -qxF $'hotmail.com\tsmtp:[smtp.eu.mailgun.org]:2525' "$WORK/olimpiada_relay_transport" \
  && grep -qxF $'[smtp.eu.mailgun.org]:2525\tencrypt' "$WORK/olimpiada_relay_tls"
check "host:port bez nawiasów → [host]:port (bez szukania MX hosta relaya)" $?

# 5. Własna lista domen: przecinki, wielkie litery, duplikaty, śmieci.
reset
out="$(run MAIL_RELAY_HOST='[smtp-relay.brevo.com]:587' MAIL_RELAY_DOMAINS='Szkola.EDU.pl, outlook.com outlook.com zla_domena! x' 2>&1)"
[ "$(cut -f1 "$WORK/olimpiada_relay_transport" | tr '\n' ' ')" = "szkola.edu.pl outlook.com " ] \
  && printf '%s' "$out" | grep -q "OSTRZEŻENIE: 'zla_domena!'" && printf '%s' "$out" | grep -q "OSTRZEŻENIE: 'x'"
check "MAIL_RELAY_DOMAINS: małe litery, bez duplikatów, złe nazwy pominięte z ostrzeżeniem" $?

# 6. `*` – cała poczta; adres przekierowania MAIL-03 wprost.
reset
run MAIL_RELAY_HOST='[smtp-relay.brevo.com]:587' MAIL_RELAY_DOMAINS='*' MAIL_INBOUND_FORWARD=Ops@Qaif.test >/dev/null 2>&1
[ "$(cat "$WORK/olimpiada_relay_transport")" = $'ops@qaif.test\tsmtp:\n*\tsmtp:[smtp-relay.brevo.com]:587' ]
check "'*': wildcard przez relay, adres MAIL_INBOUND_FORWARD wprost (smtp: bez celu)" $?
[ ! -e "$WORK/fake-glob-file" ] && touch "$WORK/fake-glob-file" && reset \
  && (cd "$WORK" && run MAIL_RELAY_HOST='[r.test]:587' MAIL_RELAY_DOMAINS='*' >/dev/null 2>&1) \
  && [ "$(cat "$WORK/olimpiada_relay_transport")" = $'*\tsmtp:[r.test]:587' ]
check "'*' nie rozwija się w nazwy plików katalogu bieżącego" $?
rm -f "$WORK/fake-glob-file"

# 7. Odmowy: konfiguracja bez zmian, kod 0, ostrzeżenie.
refuse() {
  local label="$1"; shift
  reset
  out="$(run "$@" 2>&1)"; rc=$?
  [ "$rc" -eq 0 ] && no_changes && printf '%s' "$out" | grep -q 'OSTRZEŻENIE' \
    && ! grep -q '^lmdb:.*olimpiada_relay_transport' "$WORK/postmap.log" 2>/dev/null
  check "odmowa – $label: bez zmian w konfiguracji, ostrzeżenie, kod 0" $?
}
refuse "port 465 (smtps)" MAIL_RELAY_HOST='[smtp-relay.gmail.com]:465'
refuse "zły host" MAIL_RELAY_HOST='smtp.gmail.com;rm -rf /'
refuse "zły port" MAIL_RELAY_HOST='[smtp.test]:99999'
refuse "tylko login" MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_USER=u
refuse "tylko hasło" MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_PASSWORD=p
refuse "login z dwukropkiem" MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_USER='a:b' MAIL_RELAY_PASSWORD=p
refuse "hasło z nową linią" MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_USER=u MAIL_RELAY_PASSWORD=$'p\nx'
refuse "same złe domeny" MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_DOMAINS='zła!'

reset; touch "$WORK/postmap_fails"
out="$(run MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_USER=u MAIL_RELAY_PASSWORD=p 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && no_changes && printf '%s' "$out" | grep -q 'postmap się nie powiódł'
check "awaria postmap: bez zmian w konfiguracji, kod 0" $?

reset; touch "$WORK/fail_smtp_sasl_auth_enable"
out="$(run MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_USER=u MAIL_RELAY_PASSWORD=p 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && ! grep -q -- '^-e transport_maps' "$WORK/postconf.log"
check "awaria postconf SASL: poczta NIE kierowana do relaya (transport_maps nieruszone)" $?

reset; touch "$WORK/fail_smtp_tls_policy_maps"
run MAIL_RELAY_HOST='[smtp.test]:587' >/dev/null 2>&1
! grep -q -- '^-e transport_maps' "$WORK/postconf.log"
check "awaria postconf TLS: poczta NIE kierowana do relaya" $?

# 8. Poziom TLS.
reset
run MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_TLS_LEVEL=Secure >/dev/null 2>&1
grep -qxF $'[smtp.test]:587\tsecure' "$WORK/olimpiada_relay_tls" && grep -qxF -- "-e smtp_tls_CAfile=$WORK/ca.crt" "$WORK/postconf.log"
check "MAIL_RELAY_TLS_LEVEL=secure: polityka secure i smtp_tls_CAfile z pliku CA" $?
reset
out="$(env -i PATH="$PATH" POSTCONF="$WORK/postconf" POSTMAP="$WORK/postmap" MAIL_BOUNCE_CONF_DIR="$WORK" \
  MAIL_RELAY_CA_FILE="$WORK/brak.crt" MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_TLS_LEVEL=secure bash "$SCRIPT" 2>&1)"
grep -qxF $'[smtp.test]:587\tencrypt' "$WORK/olimpiada_relay_tls" && ! grep -q 'smtp_tls_CAfile=' "$WORK/postconf.log" \
  && printf '%s' "$out" | grep -q 'brak pliku CA'
check "secure bez pliku CA: encrypt z ostrzeżeniem (nigdy bez TLS)" $?
reset
out="$(run MAIL_RELAY_HOST='[smtp.test]:587' MAIL_RELAY_TLS_LEVEL=none 2>&1)"
grep -qxF $'[smtp.test]:587\tencrypt' "$WORK/olimpiada_relay_tls" && printf '%s' "$out" | grep -q 'OSTRZEŻENIE'
check "zły MAIL_RELAY_TLS_LEVEL: encrypt z ostrzeżeniem" $?

# 9. Włączenie kropką (run.sh obrazu przy bicie wykonywalnym).
reset
out="$(
  POSTCONF="$WORK/postconf" POSTMAP="$WORK/postmap" MAIL_BOUNCE_CONF_DIR="$WORK" \
    MAIL_RELAY_HOST='[smtp.test]:465' MAIL_RELAY_PASSWORD=p
  export POSTCONF POSTMAP MAIL_BOUNCE_CONF_DIR MAIL_RELAY_HOST MAIL_RELAY_PASSWORD
  . "$SCRIPT" >/dev/null 2>&1
  echo "po-include:${MAIL_RELAY_PASSWORD-usuniete}"
)"
[ "$out" = "po-include:usuniete" ]
check "włączenie kropką: powłoka wywołująca żyje dalej, MAIL_RELAY_PASSWORD usunięte ze środowiska" $?

# 10. Compose: zmienne usługi `mail`.
mail_block="$(awk '/^  mail:$/{f=1; next} f && /^  [a-z]/{f=0} f' "$ROOT/docker-compose.yml")"
missing=0
for var in MAIL_RELAY_HOST MAIL_RELAY_DOMAINS MAIL_RELAY_USER MAIL_RELAY_PASSWORD MAIL_RELAY_TLS_LEVEL; do
  printf '%s\n' "$mail_block" | grep -Eq "^      $var: \\$\\{$var:-[^}]*\\}\$" || { echo "brak $var"; missing=1; }
done
[ "$missing" -eq 0 ]
check "compose: usługa mail dostaje MAIL_RELAY_* z .env (domyślnie puste)" $?

[ "$failures" -eq 0 ] && echo "wszystko ok" || echo "błędów: $failures"
exit "$failures"
