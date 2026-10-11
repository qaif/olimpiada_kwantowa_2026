#!/usr/bin/env bash
# Druga (trzecia…) domena nadawcy dla relaya `mail`: klucz DKIM generowany NA SERWERZE, wpis
# w ALLOWED_SENDER_DOMAINS i dokładne rekordy DNS do wklejenia (MAIL-01, docs/OPERACJE.md § 49).
#
# Użycie (na serwerze, w katalogu instalacji – /opt/olimpiada):
#   scripts/mail_add_domain.sh iqo-official.org               # dodaj domenę, wypisz rekordy DNS
#   scripts/mail_add_domain.sh --no-restart iqo-official.org  # j.w., bez odtworzenia usługi `mail`
#   scripts/mail_add_domain.sh --print iqo-official.org       # tylko rekordy (niczego nie zmienia)
#   scripts/mail_add_domain.sh --check iqo-official.org       # czy rekordy są już w DNS-ie
#
# Idempotentny: drugi przebieg niczego nie zmienia (domena już na liście, klucz już jest – klucza
# NIGDY nie nadpisuje, bo nowy klucz unieważnia rekord wklejony w DNS-ie) i wypisuje te same rekordy.
#
# Co robi (tryb domyślny):
#   1. dopisuje domenę do ALLOWED_SENDER_DOMAINS w .env (kopia .env.bak-mail-<czas> przed zmianą),
#   2. generuje klucz w kontenerze `mail` tym samym poleceniem, co obraz przy starcie
#      (opendkim-genkey, RSA $DKIM_BITS, selektor z DKIM_SELECTOR) – tylko gdy go nie ma,
#   3. odtwarza `mail` (OpenDKIM czyta KeyTable/SigningTable przy starcie) – NIE web/worker/beat:
#      ich restart operator robi świadomie, przed ustawieniem nadawcy konkursu (§ 49, krok 5),
#   4. wypisuje rekordy i zapisuje je do mail-dns-<domena>.txt: SPF scalony z obecnym (sugestia
#      z `manage.py check_mail_dns --suggest` w `web`; nigdy drugi rekord v=spf1), DKIM, DMARC
#      (istniejący zostaje; brak = p=none z planem przejścia na quarantine) i „MX bez zmian”.
#
# Zmienne: COMPOSE (domyślnie `docker compose`; atrapa w scripts/tests/mail_add_domain_test.sh),
# REMOTE_DIR (katalog z .env; domyślnie katalog nad scripts/), MAIL_PUBLIC_IP (IPv4 do SPF;
# domyślnie pierwszy publiczny adres hosta – tak jak krok 7/8 wdrożenia), DMARC_RUA (adres raportów
# DMARC, domyślnie contact@qaif.org), DKIM_BITS (2048; 1024 wyłącznie dla panelu DNS, który nie
# przyjmie wartości TXT dłuższej niż 255 znaków).
set -euo pipefail
set -f  # bez rozwijania gwiazdki: ALLOWED_SENDER_DOMAINS=* (wariant B) iterujemy jako napis

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_DIR="${REMOTE_DIR:-$ROOT}"
COMPOSE="${COMPOSE:-docker compose}"
DMARC_RUA_FROM_ENV="${DMARC_RUA:-}"
DMARC_RUA="${DMARC_RUA:-contact@qaif.org}"
DKIM_BITS="${DKIM_BITS:-2048}"

usage() { sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() { printf 'BŁĄD: %s\n' "$*" >&2; exit 1; }

MODE=add
RESTART=1
DOMAIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-restart) RESTART=0 ;;
    --print) MODE=print ;;
    --check) MODE=check ;;
    -h|--help) usage; exit 0 ;;
    -*) die "nieznana opcja: $1" ;;
    *) [ -z "$DOMAIN" ] || die "podaj jedną domenę"; DOMAIN="$1" ;;
  esac
  shift
done
[ -n "$DOMAIN" ] || { usage >&2; exit 2; }
DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]' | sed 's/\.$//')"
# Nazwa trafia do poleceń w kontenerze i do .env – tylko etykiety LDH, bez @, spacji i cudzysłowów.
printf '%s' "$DOMAIN" | grep -Eq '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$' \
  || die "to nie jest nazwa domeny: '$DOMAIN' (bez @ – np. iqo-official.org)"
case "$DKIM_BITS" in 1024|2048) ;; *) die "DKIM_BITS: 1024 albo 2048" ;; esac

cd "$REMOTE_DIR"
[ -f .env ] || die "brak $REMOTE_DIR/.env – uruchom w katalogu instalacji (REMOTE_DIR)"

env_get() {
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

SITE_DOMAIN="$(env_get SITE_DOMAIN)"
# DMARC_RUA: zmienna środowiska > wpis w .env > contact@qaif.org (ten sam, co krok 7/8 wdrożenia).
[ -n "${DMARC_RUA_FROM_ENV:-}" ] || { RUA_ENV="$(env_get DMARC_RUA)"; [ -z "$RUA_ENV" ] || DMARC_RUA="$RUA_ENV"; }
SELECTOR="$(env_get DKIM_SELECTOR)"; SELECTOR="${SELECTOR:-olimpiada}"
printf '%s' "$SELECTOR" | grep -Eq '^[a-z0-9][a-z0-9-]*$' || die "DKIM_SELECTOR ma niedozwolone znaki"
CURRENT="$(env_get ALLOWED_SENDER_DOMAINS | tr ',' ' ')"
CURRENT="${CURRENT:-$SITE_DOMAIN}"
KEY="/etc/opendkim/keys/${DOMAIN}.private"

c() {  # docker compose ze standardowym wejściem odciętym (skrypt bywa wołany z heredoca przez ssh)
  # shellcheck disable=SC2086
  $COMPOSE "$@" </dev/null
}

in_list() { for item in $CURRENT; do [ "$item" = "$1" ] && return 0; done; return 1; }

if [ "$MODE" = add ]; then
  [ "$DOMAIN" != "$SITE_DOMAIN" ] || die "$DOMAIN to domena instalacji – jej rekordy wypisuje krok 7/8 wdrożenia (mail-dns.txt)"
  in_list '*' && die "ALLOWED_SENDER_DOMAINS='*' (wariant B, zewnętrzny dostawca) – relay platformy nie wysyła, nie ma czego dodawać"
fi

c exec -T mail true >/dev/null 2>&1 || die "usługa 'mail' nie działa – najpierw: $COMPOSE up -d mail"

# --- 1. ALLOWED_SENDER_DOMAINS ------------------------------------------------------------------
ENV_CHANGED=0
if [ "$MODE" = add ] && ! in_list "$DOMAIN"; then
  NEW_LIST="$(printf '%s %s' "$CURRENT" "$DOMAIN" | xargs)"
  cp -p .env ".env.bak-mail-$(date +%Y%m%d-%H%M%S)"
  tmp="$(mktemp .env.tmp.XXXXXX)"
  if grep -q '^ALLOWED_SENDER_DOMAINS=' .env; then
    awk -v v="ALLOWED_SENDER_DOMAINS=\"$NEW_LIST\"" '/^ALLOWED_SENDER_DOMAINS=/ { print v; next } { print }' .env >"$tmp"
  else
    cat .env >"$tmp"
    {
      echo
      echo "# Domeny nadawców relaya \`mail\` (podpis DKIM każdej) – scripts/mail_add_domain.sh, docs/OPERACJE.md § 49."
      echo "ALLOWED_SENDER_DOMAINS=\"$NEW_LIST\""
    } >>"$tmp"
  fi
  chmod --reference=.env "$tmp" 2>/dev/null || chmod 600 "$tmp"
  mv "$tmp" .env
  CURRENT="$NEW_LIST"
  ENV_CHANGED=1
  echo "ALLOWED_SENDER_DOMAINS=\"$NEW_LIST\" (zapisane w .env)"
elif [ "$MODE" = add ]; then
  echo "ALLOWED_SENDER_DOMAINS zawiera już $DOMAIN – bez zmian."
fi

# --- 2. klucz DKIM ------------------------------------------------------------------------------
KEY_CREATED=0
if c exec -T mail test -f "$KEY" >/dev/null 2>&1; then
  if [ "$MODE" = add ]; then
    echo "Klucz DKIM $KEY już istnieje – NIE nadpisuję (rekord w DNS-ie zostaje ważny)."
  fi
elif [ "$MODE" = add ]; then
  # To samo polecenie, co obraz (/scripts/functions.sh, postfix_setup_dkim) – z tym samym
  # poprawieniem h=rsa-sha256 → h=sha256 w pliku rekordu i z tymi samymi uprawnieniami.
  # Drugi bezpiecznik w kontenerze: gdyby `test -f` wyżej zawiódł z innego powodu niż brak pliku,
  # istniejący klucz i tak nie zostanie nadpisany (kod 3 = przerwanie skryptu).
  c exec -T mail sh -c '
    set -e
    [ ! -e "/etc/opendkim/keys/$2.private" ] || { echo "klucz $2 już istnieje – nie nadpisuję" >&2; exit 3; }
    cd /tmp
    opendkim-genkey -b "$3" -h rsa-sha256 -r -v --subdomains -s "$1" -d "$2"
    sed -i "s/h=rsa-sha256/h=sha256/" "$1.txt"
    mv "$1.private" "/etc/opendkim/keys/$2.private"
    mv "$1.txt" "/etc/opendkim/keys/$2.txt"
    chown opendkim:opendkim "/etc/opendkim/keys/$2.private" "/etc/opendkim/keys/$2.txt"
    chmod 400 "/etc/opendkim/keys/$2.private"
    chmod 644 "/etc/opendkim/keys/$2.txt"
  ' sh "$SELECTOR" "$DOMAIN" "$DKIM_BITS" >/dev/null
  KEY_CREATED=1
  echo "Wygenerowano klucz DKIM ($DKIM_BITS bitów, selektor $SELECTOR): $KEY"
else
  die "brak klucza DKIM dla $DOMAIN – najpierw: scripts/mail_add_domain.sh $DOMAIN"
fi

# --- 3. odtworzenie `mail` ----------------------------------------------------------------------
if [ "$MODE" = add ] && [ "$RESTART" = 1 ]; then
  if [ "$ENV_CHANGED" = 1 ]; then
    c up -d mail
  elif [ "$KEY_CREATED" = 1 ]; then
    c restart mail
  fi
elif [ "$MODE" = add ] && { [ "$ENV_CHANGED" = 1 ] || [ "$KEY_CREATED" = 1 ]; }; then
  echo "--no-restart: OpenDKIM podpisze $DOMAIN dopiero po: $COMPOSE up -d mail"
fi

# --- 4. rekordy ---------------------------------------------------------------------------------
DKIM_RAW="$(c exec -T mail cat "/etc/opendkim/keys/${DOMAIN}.txt" 2>/dev/null || true)"
DKIM_VALUE="$(printf '%s' "$DKIM_RAW" | tr -d '\r\n\t' | grep -oE '"[^"]*"' | tr -d '"' | tr -d '\n' || true)"
DKIM_P="$(printf '%s' "$DKIM_VALUE" | tr -d ' ' | grep -oE 'p=[A-Za-z0-9+/=]+' | head -n 1 | cut -c3- || true)"
[ -n "$DKIM_P" ] || die "nie da się odczytać klucza publicznego z /etc/opendkim/keys/${DOMAIN}.txt"

MAIL_PUBLIC_IP="${MAIL_PUBLIC_IP:-$(env_get MAIL_PUBLIC_IP)}"
IP="${MAIL_PUBLIC_IP:-$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | grep -vE '^(127|10|172\.(1[6-9]|2[0-9]|3[01])|192\.168)\.' | head -n 1 || true)}"
printf '%s' "$IP" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' || die "nie znam publicznego IPv4 serwera – ustaw MAIL_PUBLIC_IP"

if [ "$MODE" = check ]; then
  rc=0
  echo "== opendkim-testkey (klucz w DNS-ie = klucz prywatny relaya) =="
  c exec -T mail opendkim-testkey -d "$DOMAIN" -s "$SELECTOR" -k "$KEY" -vvv || rc=1
  echo "== manage.py check_mail_dns =="
  c exec -T web python manage.py check_mail_dns "$DOMAIN" --ip "$IP" --selector "$SELECTOR" \
    --dkim-public-key "$DKIM_P" --dmarc-rua "$DMARC_RUA" || rc=1
  if [ "$rc" = 0 ]; then
    echo "OK: $DOMAIN zweryfikowana. Dalej: docs/OPERACJE.md § 49, kroki 5–6."
  else
    echo "NIE: popraw rekordy według mail-dns-${DOMAIN}.txt i powtórz --check (propagacja DNS: minuty do godzin)." >&2
  fi
  exit "$rc"
fi

# Sugestia SPF i obecny DMARC z aplikacji (web ma wyjście na świat, a skrypt nie musi mieć `dig`).
SPF="" SPF_NOTE="" DMARC_NOW="" MX=""
if SUGGEST="$(c exec -T web python manage.py check_mail_dns "$DOMAIN" --ip "$IP" --selector "$SELECTOR" \
      --dmarc-rua "$DMARC_RUA" --suggest 2>/dev/null)"; then
  SPF="$(printf '%s\n' "$SUGGEST" | sed -n 's/^spf=//p' | tr -d '\r' | head -n 1)"
  SPF_NOTE="$(printf '%s\n' "$SUGGEST" | sed -n 's/^spf_note=//p' | tr -d '\r' | head -n 1)"
  DMARC_NOW="$(printf '%s\n' "$SUGGEST" | sed -n 's/^dmarc=//p' | tr -d '\r' | head -n 1)"
  MX="$(printf '%s\n' "$SUGGEST" | sed -n 's/^mx=//p' | tr -d '\r' | head -n 1)"
fi
if [ -z "$SPF" ]; then
  SPF="v=spf1 ip4:${IP} -all"
  SPF_NOTE="Nie udało się odczytać obecnego SPF (usługa web?). JEŚLI domena ma już rekord v=spf1, NIE dodawaj drugiego – dopisz do istniejącego mechanizm ip4:${IP} zaraz po v=spf1 (np. 'v=spf1 ip4:${IP} include:_spf.google.com ~all')."
fi
RUA_DOMAIN="${DMARC_RUA#*@}"

OUT="mail-dns-${DOMAIN}.txt"
{
  echo "# Rekordy DNS poczty – ${DOMAIN} (listy konkursu przez relay platformy)"
  echo "# Wygenerowane przez scripts/mail_add_domain.sh, $(date -Iseconds). Dodaj je w panelu DNS"
  echo "# domeny (Squarespace: Domains → ${DOMAIN} → DNS → Custom records). docs/OPERACJE.md § 49."
  echo
  echo "1) SPF   – TXT, host: @  (korzeń domeny)"
  echo "   ${SPF}"
  echo "   ${SPF_NOTE}"
  echo
  echo "2) DKIM  – TXT, host: ${SELECTOR}._domainkey"
  echo "   ${DKIM_VALUE}"
  echo "   Wklej w całości, jako jedną wartość (panel może ją sam podzielić na kawałki po 255 znaków)."
  echo
  echo "3) DMARC – TXT, host: _dmarc"
  if [ -n "$DMARC_NOW" ]; then
    echo "   Domena MA już DMARC: ${DMARC_NOW}"
    echo "   NIE dodawaj drugiego rekordu _dmarc. Polityka (także p=reject z adkim=s/aspf=s) przepuści"
    echo "   listy relaya po dodaniu SPF i DKIM: podpis d=${DOMAIN} i koperta noreply@${DOMAIN} są w tej"
    echo "   samej domenie. Opcjonalnie dopisz do niego: rua=mailto:${DMARC_RUA}"
  else
    echo "   v=DMARC1; p=none; rua=mailto:${DMARC_RUA}; adkim=r; aspf=r; fo=1"
    echo "   Plan: p=none przez ok. 2 tygodnie (raporty na ${DMARC_RUA}); gdy w raportach nie ma obcych"
    echo "   nadawców i błędów – zmień na p=quarantine (ten sam rekord, tylko p=)."
  fi
  if [ "$RUA_DOMAIN" != "$DOMAIN" ]; then
    echo "   Raporty na adres w innej domenie (${RUA_DOMAIN}) wymagają zgody tamtej domeny – rekord"
    echo "   TXT ${DOMAIN}._report._dmarc.${RUA_DOMAIN} = \"v=DMARC1\" (w strefie ${RUA_DOMAIN})."
  fi
  echo
  echo "4) MX i wszystkie pozostałe rekordy – NIE RUSZAJ. (MX teraz: ${MX:-brak})"
  echo "   Relay platformy tylko wysyła; poczta przychodząca do ${DOMAIN} zostaje tam, gdzie jest."
  echo
  echo "Dalej (docs/OPERACJE.md § 49):"
  echo "  a) po dodaniu rekordów i propagacji: scripts/mail_add_domain.sh --check ${DOMAIN}"
  echo "  b) $COMPOSE up -d web worker beat   (aplikacja wczyta ALLOWED_SENDER_DOMAINS)"
  echo "  c) DOPIERO po a) i b): nadawca konkursu = noreply@${DOMAIN} (panel „Ustawienia konkursu”)"
  echo
  echo "# Klucz publiczny do porównania przy wdrożeniu (scripts/deploy.sh, krok 7/8) – nie wklejać:"
  echo "DKIM_P=${DKIM_P}"
} >"$OUT"
chmod 600 "$OUT"
cat "$OUT"
echo
echo "Zapisano: ${REMOTE_DIR}/${OUT}"
