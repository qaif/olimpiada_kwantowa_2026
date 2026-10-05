#!/usr/bin/env bash
# Test deploy/mail/docker-init.d/50-bounces.sh na atrapach postconf/postmap (MAIL-01 § 5).
#
# Uruchomienie: scripts/tests/mail_bounces_test.sh
# Sprawdza: domyślne adresy (noreply@ każdej domeny + adresy systemowe myhostname), `discard`
# (transport) i adres operatora (alias), zła wartość celu → `discard` z ostrzeżeniem, jawna lista
# adresów, cel = jeden z adresów (bez pętli aliasu), kod wyjścia 0 także przy awarii postmap.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/deploy/mail/docker-init.d/50-bounces.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/mail-bounces-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}

cat >"$WORK/postconf" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$(dirname "$0")/postconf.log"
STUB
cat >"$WORK/postmap" <<'STUB'
#!/usr/bin/env bash
[ -f "$(dirname "$0")/postmap_fails" ] && exit 1
printf '%s\n' "$*" >>"$(dirname "$0")/postmap.log"
STUB
chmod +x "$WORK/postconf" "$WORK/postmap"

run() {
  rm -f "$WORK"/*.log "$WORK"/olimpiada_bounce_*
  env -i PATH="$PATH" POSTCONF="$WORK/postconf" POSTMAP="$WORK/postmap" MAIL_BOUNCE_CONF_DIR="$WORK" \
    POSTFIX_myhostname=mail.platforma.test ALLOWED_SENDER_DOMAINS="platforma.test iqo.test" "$@" bash "$SCRIPT"
}

out="$(run 2>&1)"; rc=$?
check "domyślnie – kod 0" "$rc"
expected=$'double-bounce@mail.platforma.test\tdiscard:olimpiada-bounce
mailer-daemon@mail.platforma.test\tdiscard:olimpiada-bounce
noreply@iqo.test\tdiscard:olimpiada-bounce
noreply@platforma.test\tdiscard:olimpiada-bounce
postmaster@mail.platforma.test\tdiscard:olimpiada-bounce'
[ "$(cat "$WORK/olimpiada_bounce_transport")" = "$expected" ] && [ ! -s "$WORK/olimpiada_bounce_alias" ]
check "discard: noreply@ każdej domeny + postmaster/MAILER-DAEMON/double-bounce myhostname" $?
grep -qxF -- "-e transport_maps=lmdb:$WORK/olimpiada_bounce_transport" "$WORK/postconf.log" \
  && grep -qxF -- "-e virtual_alias_maps=lmdb:$WORK/olimpiada_bounce_alias" "$WORK/postconf.log" \
  && grep -qx "lmdb:$WORK/olimpiada_bounce_transport" "$WORK/postmap.log"
check "discard: postmap obu tablic i transport_maps/virtual_alias_maps" $?

run MAIL_BOUNCE_EXTRA_ADDRESSES='glitchtip@platforma.test uptime@platforma.test' MAIL_BOUNCE_ADDRESSES=x@iqo.test >/dev/null 2>&1
grep -q '^glitchtip@platforma.test' "$WORK/olimpiada_bounce_transport" && grep -q '^uptime@platforma.test' "$WORK/olimpiada_bounce_transport" \
  && grep -q '^x@iqo.test' "$WORK/olimpiada_bounce_transport"
check "nadawcy monitoringu (MAIL_BOUNCE_EXTRA_ADDRESSES) dopisani zawsze, także przy jawnej liście" $?
docker_compose_extra="$(sed -n 's/^ *MAIL_BOUNCE_EXTRA_ADDRESSES: //p' "$ROOT/docker-compose.yml")"
printf '%s' "$docker_compose_extra" | grep -q 'glitchtip@' && printf '%s' "$docker_compose_extra" | grep -q 'uptime@'
check "compose: odbicia GlitchTipa i monitora dostępności objęte (OPS-02)" $?

run MAIL_BOUNCE_TARGET=ops@qaif.test >/dev/null 2>&1
[ ! -s "$WORK/olimpiada_bounce_transport" ] && grep -qx $'noreply@iqo.test\tops@qaif.test' "$WORK/olimpiada_bounce_alias" \
  && grep -qx $'postmaster@mail.platforma.test\tops@qaif.test' "$WORK/olimpiada_bounce_alias"
check "adres operatora: przekierowanie aliasem, pusta tablica transportu" $?

out="$(run MAIL_BOUNCE_TARGET='ops@qaif.test; rm -rf /' 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'OSTRZEŻENIE' && grep -q 'discard:' "$WORK/olimpiada_bounce_transport"
check "zła wartość celu: ostrzeżenie i discard, kod 0" $?

run MAIL_BOUNCE_ADDRESSES='bounces@iqo.test,NoReply@Platforma.test' >/dev/null 2>&1
grep -qx $'bounces@iqo.test\tdiscard:olimpiada-bounce' "$WORK/olimpiada_bounce_transport" \
  && grep -qx $'noreply@platforma.test\tdiscard:olimpiada-bounce' "$WORK/olimpiada_bounce_transport" \
  && ! grep -q '^noreply@iqo.test' "$WORK/olimpiada_bounce_transport"
check "jawna lista adresów (przecinki, małe litery) zastępuje domyślną" $?

# MAIL-02: `capture` – noreply@ do Maildira agentem virtual(8), adresy systemowe dalej na discard.
out="$(run MAIL_BOUNCE_TARGET=capture MAIL_BOUNCE_MAILDIR_BASE="$WORK/maildir" \
  MAIL_BOUNCE_EXTRA_ADDRESSES='glitchtip@platforma.test' 2>&1)"; rc=$?
[ "$rc" -eq 0 ] \
  && grep -qx $'noreply@platforma.test\tvirtual:' "$WORK/olimpiada_bounce_transport" \
  && grep -qx $'noreply@iqo.test\tvirtual:' "$WORK/olimpiada_bounce_transport" \
  && grep -qx $'noreply@iqo.test\tbounces/' "$WORK/olimpiada_bounce_mailbox" \
  && grep -qx $'glitchtip@platforma.test\tdiscard:olimpiada-bounce' "$WORK/olimpiada_bounce_transport" \
  && grep -qx $'postmaster@mail.platforma.test\tdiscard:olimpiada-bounce' "$WORK/olimpiada_bounce_transport" \
  && ! grep -q '^postmaster@' "$WORK/olimpiada_bounce_mailbox" \
  && [ ! -s "$WORK/olimpiada_bounce_alias" ] && [ -d "$WORK/maildir" ]
check "capture: noreply@ → virtual: + Maildir bounces/, adresy systemowe → discard" $?
grep -qx -- "-e virtual_mailbox_base=$WORK/maildir virtual_mailbox_maps=lmdb:$WORK/olimpiada_bounce_mailbox virtual_uid_maps=static:1000 virtual_gid_maps=static:1000 virtual_mailbox_domains= virtual_mailbox_limit=0" "$WORK/postconf.log" \
  && grep -qx "lmdb:$WORK/olimpiada_bounce_mailbox" "$WORK/postmap.log"
check "capture: postconf virtual_mailbox_* (UID/GID 1000, limit 0) i postmap tablicy skrzynek" $?
printf '%s' "$out" | grep -q 'poczta zwrotna -> capture'
check "capture: linia w logu startu" $?

out="$(run MAIL_BOUNCE_TARGET=capture MAIL_BOUNCE_MAILDIR_BASE="$WORK/maildir" MAIL_BOUNCE_UID=0 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'UID ≥ 100' && grep -q 'virtual_uid_maps=static:1000' "$WORK/postconf.log"
check "capture: UID < 100 (virtual_minimum_uid) – ostrzeżenie i 1000" $?
compose_target="$(sed -n 's/^ *MAIL_BOUNCE_TARGET: //p' "$ROOT/docker-compose.yml")"
[ "$compose_target" = '${MAIL_BOUNCE_TARGET:-capture}' ]
check "compose: domyślny cel to capture (MAIL-02)" $?

run MAIL_BOUNCE_TARGET=noreply@platforma.test >/dev/null 2>&1
! grep -q '^noreply@platforma.test' "$WORK/olimpiada_bounce_alias" && grep -q '^noreply@iqo.test' "$WORK/olimpiada_bounce_alias"
check "cel będący jednym z adresów – bez aliasu na samego siebie" $?

# Obraz WŁĄCZA skrypt z bitem +x (`. plik` w run.sh z `set -e`) – po nim run.sh musi iść dalej,
# bez `set -f`/`set -u` i bez zmiennych skryptu (pierwszy e2e: `exit` zatrzymał kontener).
rm -f "$WORK"/*.log
out="$(env -i PATH="$PATH" POSTCONF="$WORK/postconf" POSTMAP="$WORK/postmap" MAIL_BOUNCE_CONF_DIR="$WORK" \
  POSTFIX_myhostname=mail.platforma.test ALLOWED_SENDER_DOMAINS="platforma.test" \
  bash -c 'set -e; . "$0"; case $- in *f*|*u*) echo WYCIEK-OPCJI;; esac; [ -z "${target:-}" ] || echo WYCIEK-ZMIENNYCH; echo DALEJ' "$SCRIPT" 2>&1)"
printf '%s' "$out" | grep -q '^DALEJ$' && ! printf '%s' "$out" | grep -q WYCIEK && grep -q 'transport_maps' "$WORK/postconf.log"
check "włączony przez \`. plik\` (set -e): run.sh idzie dalej, bez wycieku opcji i zmiennych" $?

touch "$WORK/postmap_fails"
out="$(run 2>&1)"; rc=$?
rm -f "$WORK/postmap_fails"
[ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'postmap się nie powiódł' && [ ! -s "$WORK/postconf.log" ]
check "awaria postmap: kod 0 (kontener startuje), bez zmiany konfiguracji" $?

echo
if [ "$failures" -eq 0 ]; then echo "mail_bounces: wszystko OK"; else echo "mail_bounces: $failures błędów"; exit 1; fi
