#!/usr/bin/env bash
# Poczta przychodząca relaya `mail` na porcie 25 (MAIL-03, docs/OPERACJE.md § 49.9).
#
# Uruchamiany przez obraz boky/postfix przy każdym starcie, **po** 50-bounces.sh (kolejność
# alfabetyczna w /docker-init.d/), więc dopisuje się do tablic, które tamten skrypt właśnie zbudował.
#
# Po co: do 9.10.2026 relay był wyłącznie wychodzący – port 25 nie był publikowany, a MX domeny
# (mail.<domena>) wskazywał w próżnię. Microsoft (SNDS/JMRP, odblokowanie adresu po błędzie S3150)
# potwierdza IP listem na postmaster@ albo abuse@ domeny z rekordu PTR, a zdalne serwery odsyłają
# odbicia na noreply@ – żaden z tych listów nie miał dokąd dojść.
#
# Co robi:
#   1. ZAWSZE zaostrza usługę `smtp` (port 25) w master.cf: `mynetworks` tylko 127.0.0.0/8 i
#      `reject_unauth_destination` bez `permit_mynetworks`. Globalne `smtpd_relay_restrictions=permit`
#      obrazu jest bezpieczne tylko na 587 osiągalnym z sieci compose; port 25 po publikacji widzi
#      świat, a przy proxy użytkownika Dockera adres klienta bywa adresem bramki 172.30.x (czyli
#      „naszej” sieci) – bez tego nadpisania relay byłby otwarty dla każdego.
#   2. Gdy MAIL_INBOUND_FORWARD jest adresem e-mail: `relay_domains` = domeny z ALLOWED_SENDER_DOMAINS,
#      `relay_recipient_maps` = wyłącznie postmaster@ i abuse@ tych domen (MAIL_INBOUND_LOCALPARTS)
#      oraz adresy, które 50-bounces.sh kieruje do skrzynki odbić (`virtual:` – tryb capture);
#      postmaster@/abuse@ przekierowane aliasem na MAIL_INBOUND_FORWARD. Każdy inny adresat dostaje
#      550 „User unknown in relay recipient table” jeszcze w rozmowie SMTP – nic nie trafia do kolejki.
#   Pusty albo błędny MAIL_INBOUND_FORWARD = poczta przychodząca wyłączona (port 25 odrzuca wszystko
#   z punktu 1, bo relay_domains zostaje puste).
#
# Skrypt nigdy nie kończy się błędem (ta sama umowa, co 50-bounces.sh): błąd w /docker-init.d/
# zatrzymałby start relaya, a wysyłka jest ważniejsza od poczty przychodzącej.
#
# Atrapy w teście (scripts/tests/mail_inbound_test.sh): POSTCONF, POSTMAP, MAIL_BOUNCE_CONF_DIR.

olimpiada_inbound() (
  set -u
  set -f

  POSTCONF="${POSTCONF:-postconf}"
  POSTMAP="${POSTMAP:-postmap}"
  CONF_DIR="${MAIL_BOUNCE_CONF_DIR:-/etc/postfix}"
  RECIPIENTS_FILE="$CONF_DIR/olimpiada_inbound_recipients"
  ALIAS_FILE="$CONF_DIR/olimpiada_inbound_alias"
  BOUNCE_TRANSPORT_FILE="$CONF_DIR/olimpiada_bounce_transport"

  say() { printf '       [olimpiada-inbound] %s\n' "$*"; }

  # 1. Port 25 – zawsze zaostrzony, niezależnie od tego, czy poczta przychodząca jest włączona.
  if ! "$POSTCONF" -P \
    "smtp/inet/mynetworks=127.0.0.0/8" \
    "smtp/inet/smtpd_client_restrictions=" \
    "smtp/inet/smtpd_helo_restrictions=" \
    "smtp/inet/smtpd_sender_restrictions=" \
    "smtp/inet/smtpd_relay_restrictions=reject_unauth_destination" \
    "smtp/inet/smtpd_recipient_restrictions=reject_non_fqdn_recipient,reject_unlisted_recipient,reject_unauth_destination,permit"; then
    # Bez zaostrzenia port 25 nie może słuchać w ogóle – wyłączamy usługę (wysyłka idzie przez 587).
    "$POSTCONF" -e "master_service_disable=smtp/inet" || true
    say "OSTRZEŻENIE: postconf -P smtp/inet się nie powiódł – usługa portu 25 wyłączona."
    exit 0
  fi

  forward="$(printf '%s' "${MAIL_INBOUND_FORWARD:-}" | tr -d '[:space:]')"
  if [ -z "$forward" ]; then
    say "poczta przychodząca wyłączona (MAIL_INBOUND_FORWARD pusty) – port 25 odrzuca wszystkich adresatów."
    exit 0
  fi
  if ! printf '%s' "$forward" | grep -Eq '^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$'; then
    say "OSTRZEŻENIE: MAIL_INBOUND_FORWARD='${MAIL_INBOUND_FORWARD}' to nie adres e-mail – poczta przychodząca wyłączona."
    exit 0
  fi
  forward="$(printf '%s' "$forward" | tr '[:upper:]' '[:lower:]')"

  domains=""
  for domain in ${ALLOWED_SENDER_DOMAINS:-}; do
    [ "$domain" = "*" ] && continue
    domains="$domains $(printf '%s' "$domain" | tr '[:upper:]' '[:lower:]')"
  done
  if [ -z "${domains// /}" ]; then
    say "OSTRZEŻENIE: brak domen w ALLOWED_SENDER_DOMAINS – poczta przychodząca wyłączona."
    exit 0
  fi
  localparts="$(printf '%s' "${MAIL_INBOUND_LOCALPARTS:-postmaster abuse}" | tr ',' ' ' | tr '[:upper:]' '[:lower:]')"

  : >"$RECIPIENTS_FILE" 2>/dev/null || { say "OSTRZEŻENIE: nie da się zapisać $RECIPIENTS_FILE – bez zmian."; exit 0; }
  : >"$ALIAS_FILE"
  for domain in $domains; do
    for local in $localparts; do
      address="$local@$domain"
      # Przekierowanie na samego siebie zrobiłoby pętlę aliasów.
      [ "$address" = "$forward" ] && continue
      printf '%s\tOK\n' "$address" >>"$RECIPIENTS_FILE"
      printf '%s\t%s\n' "$address" "$forward" >>"$ALIAS_FILE"
    done
  done
  # Skrzynka odbić (50-bounces.sh, tryb capture): zdalne zawiadomienia o niedoręczeniu na noreply@
  # też mają wejść – trafiają tam, gdzie lokalne, czyli do Maildira czytanego przez worker.
  if [ -f "$BOUNCE_TRANSPORT_FILE" ]; then
    awk -F '\t' '$2 == "virtual:" { print $1 "\tOK" }' "$BOUNCE_TRANSPORT_FILE" >>"$RECIPIENTS_FILE"
  fi

  if ! "$POSTMAP" "lmdb:$RECIPIENTS_FILE" || ! "$POSTMAP" "lmdb:$ALIAS_FILE"; then
    say "OSTRZEŻENIE: postmap się nie powiódł – poczta przychodząca wyłączona."
    exit 0
  fi
  # virtual_alias_maps ustawił przed chwilą 50-bounces.sh – dopisujemy drugą tablicę, nie zastępujemy.
  current="$("$POSTCONF" -h virtual_alias_maps 2>/dev/null | sed "s#lmdb:$ALIAS_FILE##g" | tr -s ' ,' ' ' | sed 's/^ //; s/ $//')"
  # shellcheck disable=SC2086 # lista domen to słowa
  relay_domains="$(printf '%s ' $domains | sed 's/ $//')"
  "$POSTCONF" -e \
    "virtual_alias_maps=${current:+$current }lmdb:$ALIAS_FILE" \
    "relay_domains=$relay_domains" \
    "relay_recipient_maps=lmdb:$RECIPIENTS_FILE" \
    || { say "OSTRZEŻENIE: postconf relay_domains/relay_recipient_maps"; exit 0; }
  say "poczta przychodząca na porcie 25 dla: $(cut -f1 "$RECIPIENTS_FILE" | tr '\n' ' ')(postmaster/abuse -> $forward)"
  exit 0
)
olimpiada_inbound || true
unset -f olimpiada_inbound
