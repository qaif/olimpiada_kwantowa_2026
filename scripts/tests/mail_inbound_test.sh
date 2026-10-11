#!/usr/bin/env bash
# Test deploy/mail/docker-init.d/60-inbound.sh na atrapach postconf/postmap (MAIL-03).
#
# Uruchomienie: scripts/tests/mail_inbound_test.sh
# Sprawdza: port 25 zaostrzony zawsze (także przy wyłączonej poczcie przychodzącej), wyłączenie usługi
# portu 25, gdy zaostrzenie się nie uda, postmaster@/abuse@ każdej domeny jako jedyni adresaci
# (+ noreply@ ze skrzynki odbić), alias na adres operatora dopisany do virtual_alias_maps z 50-bounces.sh,
# zły adres celu → wyłączone z ostrzeżeniem, kod wyjścia 0 zawsze.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/deploy/mail/docker-init.d/60-inbound.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/mail-inbound-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}

# postconf: zapisuje wywołania; `-h virtual_alias_maps` oddaje to, co ustawił 50-bounces.sh;
# plik `postconf_P_fails` symuluje awarię `-P`.
cat >"$WORK/postconf" <<'STUB'
#!/usr/bin/env bash
dir="$(dirname "$0")"
printf '%s\n' "$*" >>"$dir/postconf.log"
if [ "$1" = "-h" ] && [ "$2" = "virtual_alias_maps" ]; then
  printf 'lmdb:%s/olimpiada_bounce_alias\n' "$dir"; exit 0
fi
if [ "$1" = "-P" ] && [ -f "$dir/postconf_P_fails" ]; then exit 1; fi
exit 0
STUB
cat >"$WORK/postmap" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$(dirname "$0")/postmap.log"
STUB
chmod +x "$WORK/postconf" "$WORK/postmap"

run() {
  rm -f "$WORK"/*.log "$WORK"/olimpiada_inbound_* "$WORK/postconf_P_fails"
  env -i PATH="$PATH" POSTCONF="$WORK/postconf" POSTMAP="$WORK/postmap" MAIL_BOUNCE_CONF_DIR="$WORK" \
    ALLOWED_SENDER_DOMAINS="platforma.test IQO.test" "$@" bash "$SCRIPT"
}

# Skrzynka odbić z 50-bounces.sh (tryb capture): noreply@ → virtual:, adres systemowy → discard.
printf 'noreply@platforma.test\tvirtual:\nnoreply@iqo.test\tvirtual:\npostmaster@mail.platforma.test\tdiscard:olimpiada-bounce\n' \
  >"$WORK/olimpiada_bounce_transport"

out="$(run 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && grep -q -- '^-P smtp/inet/mynetworks=127.0.0.0/8 ' "$WORK/postconf.log" \
  && grep -q 'smtp/inet/smtpd_relay_restrictions=reject_unauth_destination' "$WORK/postconf.log" \
  && ! grep -q 'relay_domains' "$WORK/postconf.log" && printf '%s' "$out" | grep -q 'wyłączona'
check "bez MAIL_INBOUND_FORWARD: port 25 zaostrzony, relay_domains nieruszone, kod 0" $?
! grep -q 'permit_mynetworks' "$WORK/postconf.log"
check "port 25: żadnego permit_mynetworks" $?

out="$(run MAIL_INBOUND_FORWARD=Ops@Qaif.test 2>&1)"; rc=$?
expected_recipients=$'postmaster@platforma.test\tOK
abuse@platforma.test\tOK
postmaster@iqo.test\tOK
abuse@iqo.test\tOK
noreply@platforma.test\tOK
noreply@iqo.test\tOK'
[ "$rc" -eq 0 ] && [ "$(cat "$WORK/olimpiada_inbound_recipients")" = "$expected_recipients" ]
check "adresaci: postmaster@/abuse@ każdej domeny (małe litery) + noreply@ ze skrzynki odbić, bez adresów discard" $?
grep -qx $'postmaster@platforma.test\tops@qaif.test' "$WORK/olimpiada_inbound_alias" \
  && grep -qx $'abuse@iqo.test\tops@qaif.test' "$WORK/olimpiada_inbound_alias" \
  && ! grep -q '^noreply@' "$WORK/olimpiada_inbound_alias"
check "alias: postmaster@/abuse@ → adres operatora, noreply@ bez aliasu" $?
grep -qxF -- "-e virtual_alias_maps=lmdb:$WORK/olimpiada_bounce_alias lmdb:$WORK/olimpiada_inbound_alias relay_domains=platforma.test iqo.test relay_recipient_maps=lmdb:$WORK/olimpiada_inbound_recipients" "$WORK/postconf.log" \
  && grep -qx "lmdb:$WORK/olimpiada_inbound_recipients" "$WORK/postmap.log" \
  && grep -qx "lmdb:$WORK/olimpiada_inbound_alias" "$WORK/postmap.log"
check "postconf: tablica aliasów 50-bounces.sh zachowana, relay_domains i relay_recipient_maps ustawione" $?

run MAIL_INBOUND_FORWARD=postmaster@platforma.test >/dev/null 2>&1
! grep -q '^postmaster@platforma.test' "$WORK/olimpiada_inbound_alias" \
  && grep -qx $'abuse@platforma.test\tpostmaster@platforma.test' "$WORK/olimpiada_inbound_alias"
check "cel = jeden z adresów: bez pętli aliasu" $?

out="$(run MAIL_INBOUND_FORWARD='ops@qaif.test; rm -rf /' 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'OSTRZEŻENIE' && ! grep -q 'relay_domains' "$WORK/postconf.log"
check "zły adres celu: ostrzeżenie, poczta przychodząca wyłączona, kod 0" $?

rm -f "$WORK"/*.log; touch "$WORK/postconf_P_fails"
out="$(env -i PATH="$PATH" POSTCONF="$WORK/postconf" POSTMAP="$WORK/postmap" MAIL_BOUNCE_CONF_DIR="$WORK" \
  ALLOWED_SENDER_DOMAINS="platforma.test" MAIL_INBOUND_FORWARD=ops@qaif.test bash "$SCRIPT" 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && grep -qxF -- '-e master_service_disable=smtp/inet' "$WORK/postconf.log" \
  && ! grep -q 'relay_domains' "$WORK/postconf.log"
check "awaria zaostrzenia portu 25: usługa smtp/inet wyłączona, relay_domains nieruszone" $?

out="$(. "$SCRIPT" 2>&1 <<<'' ; echo "po-include")"
printf '%s' "$out" | grep -q 'po-include'
check "włączenie skryptu kropką (jak run.sh obrazu) nie kończy powłoki wywołującej" $?

compose_bind="$(grep -E '^\s*- "\$\{MAIL_INBOUND_BIND:-127\.0\.0\.1\}:25:25"' "$ROOT/docker-compose.yml")"
[ -n "$compose_bind" ]
check "compose: port 25 domyślnie tylko na 127.0.0.1" $?

[ "$failures" -eq 0 ] && echo "wszystko ok" || echo "błędów: $failures"
exit "$failures"
