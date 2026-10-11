#!/usr/bin/env bash
# Test kroku 7/8 wdrożenia (`scripts/deploy.sh`) – rekordy poczty i pilnowanie kluczy DKIM (MAIL-01 § 3).
#
# Uruchomienie: scripts/tests/deploy_mail_dns_test.sh
#
# Krok 7/8 to skrypt wysyłany przez SSH here-documentem – test wycina jego treść z deploy.sh (nie
# kopiuje) i uruchamia w katalogu tymczasowym na atrapie `docker`. Sprawdza:
#   1. instalacja z jedną domeną: mail-dns.txt jak dotąd, bez sekcji dodatkowych domen i ostrzeżeń,
#   2. drugie wdrożenie z tym samym kluczem: bez ostrzeżenia,
#   3. nowy klucz domeny głównej (wolumen odtworzony) → głośne „UWAGA … INNY”,
#   4. domena dodana mail_add_domain.sh: klucz zgodny z mail-dns-<d>.txt → „bez zmian”;
#      inny → „UWAGA”; brak pliku → podpowiedź --print; brak klucza → „UWAGA brak klucza”,
#   5. ALLOWED_SENDER_DOMAINS='*' – bez pętli po gwiazdce, krok kończy się kodem 0.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY="$ROOT/scripts/deploy.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/deploy-mail-dns-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
SRV="$WORK/srv"
mkdir -p "$SRV" "$WORK/bin" "$WORK/keys"

failures=0
check() {
  if [ "$2" -eq 0 ]; then printf 'ok   %s\n' "$1"; else printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); fi
}

awk '/log "7\/8 DNS dla poczty/ { seen = 1 }
     seen && /bash -s <<.REMOTE.$/ { inside = 1; next }
     inside && /^REMOTE$/ { exit }
     inside { print }' "$DEPLOY" >"$WORK/krok7.sh"
[ -s "$WORK/krok7.sh" ] && grep -q 'mail-dns.txt' "$WORK/krok7.sh"
check "udało się wyciąć zdalny skrypt kroku 7/8 z deploy.sh" $?

# Atrapa `docker compose exec -T mail cat /etc/opendkim/keys/<d>.txt` – klucze z $WORK/keys.
cat >"$WORK/bin/docker" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  "compose exec -T mail cat /etc/opendkim/keys/"*)
    f="$KEYS/$(basename "${@: -1}")"; [ -f "$f" ] && cat "$f" ;;
  *) exit 0 ;;
esac
STUB
chmod +x "$WORK/bin/docker"

key() {  # key <domena> <p>
  printf 'olimpiada._domainkey\tIN\tTXT\t( "v=DKIM1; h=sha256; k=rsa; s=email; "\n\t  "p=%s" )\n' "$2" >"$WORK/keys/$1.txt"
}
step7() {
  (cd "$SRV" && PATH="$WORK/bin:$PATH" KEYS="$WORK/keys" REMOTE_DIR="$SRV" DMARC_RUA=ops@qaif.test \
    MAIL_PUBLIC_IP=203.0.113.7 bash "$WORK/krok7.sh" 2>&1)
}

printf 'SITE_DOMAIN=platforma.test\n' >"$SRV/.env"
key platforma.test AAAA1111
out="$(step7)"; rc=$?
[ "$rc" -eq 0 ] && grep -q 'p=AAAA1111' "$SRV/mail-dns.txt" && ! grep -q 'Dodatkowe domeny' "$SRV/mail-dns.txt" \
  && ! printf '%s' "$out" | grep -q 'UWAGA'
check "jedna domena: mail-dns.txt jak dotąd, bez ostrzeżeń" $?

out="$(step7)"; rc=$?
[ "$rc" -eq 0 ] && ! printf '%s' "$out" | grep -q 'UWAGA'
check "drugie wdrożenie z tym samym kluczem – bez ostrzeżenia" $?

key platforma.test BBBB2222
out="$(step7)"; rc=$?
[ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'UWAGA: klucz DKIM domeny platforma.test jest INNY'
check "nowy klucz domeny głównej – głośne ostrzeżenie" $?

printf 'SITE_DOMAIN=platforma.test\nALLOWED_SENDER_DOMAINS="platforma.test iqo.test brakpliku.test bezklucza.test"\n' >"$SRV/.env"
key iqo.test CCCC3333
key brakpliku.test DDDD4444
printf 'DKIM_P=CCCC3333\n' >"$SRV/mail-dns-iqo.test.txt"
out="$(step7)"; rc=$?
[ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'iqo.test: klucz DKIM bez zmian' \
  && printf '%s' "$out" | grep -q 'brak mail-dns-brakpliku.test.txt' \
  && printf '%s' "$out" | grep -q 'brak klucza DKIM domeny bezklucza.test' \
  && grep -q 'Dodatkowe domeny nadawców (ALLOWED_SENDER_DOMAINS): iqo.test brakpliku.test bezklucza.test' "$SRV/mail-dns.txt"
check "dodatkowe domeny: zgodny klucz, brak pliku, brak klucza; lista w mail-dns.txt" $?

key iqo.test EEEE5555
out="$(step7)"; rc=$?
[ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'UWAGA: klucz DKIM domeny iqo.test jest INNY niż w mail-dns-iqo.test.txt'
check "inny klucz dodatkowej domeny – głośne ostrzeżenie z poleceniem" $?

printf 'SITE_DOMAIN=platforma.test\nALLOWED_SENDER_DOMAINS=*\n' >"$SRV/.env"
touch "$SRV/plik-do-rozwiniecia"
out="$(step7)"; rc=$?
[ "$rc" -eq 0 ] && ! printf '%s' "$out" | grep -q 'plik-do-rozwiniecia' && ! grep -q 'Dodatkowe domeny' "$SRV/mail-dns.txt"
check "ALLOWED_SENDER_DOMAINS='*' – gwiazdka nie rozwija się w pliki, kod 0" $?

echo
if [ "$failures" -eq 0 ]; then echo "deploy_mail_dns: wszystko OK"; else echo "deploy_mail_dns: $failures błędów"; exit 1; fi
