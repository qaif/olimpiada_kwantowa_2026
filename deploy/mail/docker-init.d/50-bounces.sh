#!/usr/bin/env bash
# Poczta zwrotna relaya `mail` (MAIL-01 § 5, docs/OPERACJE.md § 49.6).
#
# Uruchamiany przez obraz boky/postfix przy każdym starcie (`execute_post_init_scripts`, katalog
# /docker-init.d/ montowany z deploy/mail/docker-init.d) – po konfiguracji Postfiksa z env,
# przed startem supervisord.
#
# Problem: relay nie doręcza lokalnie (`mydestination=` pusty), więc zawiadomienie o niedoręczeniu
# (bounce) idzie na adres koperty – `noreply@<domena>` – przez MX/A tej domeny. Dla
# olimpiadakwantowa.pl i iqo-official.org rekord A wskazuje ten sam serwer, więc Postfix kończy
# na „mail for … loops back to myself”, a list wisi w kolejce i wraca jako podwójne odbicie do
# `postmaster@<myhostname>` – z tym samym skutkiem. Ten skrypt kieruje te adresy tam, gdzie
# operator chce:
#   MAIL_BOUNCE_TARGET=discard (domyślnie) – wyrzucenie (transport `discard:`); ślad zostaje
#       w logu: pierwotny list ma `status=bounced`, odbicie `status=sent (… discard …)`,
#   MAIL_BOUNCE_TARGET=<adres e-mail> – przekierowanie (virtual_alias_maps) na skrzynkę operatora.
# Adresy: MAIL_BOUNCE_ADDRESSES (spacje/przecinki; puste = noreply@ każdej domeny
# z ALLOWED_SENDER_DOMAINS) plus zawsze postmaster, MAILER-DAEMON i double-bounce w domenie
# myhostname – na nie Postfix wysyła podwójne odbicia.
#
# Skrypt nigdy nie kończy się błędem: relay bez tej poprawki nadal wysyła listy, a błąd w
# /docker-init.d/ zatrzymałby start całego kontenera (run.sh ma `set -e`). Zła wartość celu =
# ostrzeżenie w logu i `discard`.
#
# Obraz WŁĄCZA (`. plik`) skrypt z bitem wykonywalnym, a bez niego uruchamia `bash plik` – zależnie
# od systemu plików montowanego katalogu. Całość jest więc w funkcji z ciałem w podpowłoce:
# `exit`, `set -u`/`set -f` i zmienne nie wyciekają do run.sh obrazu (włączony `exit` zakończyłby
# run.sh i kontener stanąłby przed startem Postfiksa – tak wyglądał pierwszy przebieg e2e).
#
# Atrapy w teście (scripts/tests/mail_bounces_test.sh): POSTCONF, POSTMAP, MAIL_BOUNCE_CONF_DIR.

olimpiada_bounces() (
  set -u
  set -f  # ALLOWED_SENDER_DOMAINS='*' (wariant B) nie może się rozwinąć w nazwy plików

  POSTCONF="${POSTCONF:-postconf}"
  POSTMAP="${POSTMAP:-postmap}"
  CONF_DIR="${MAIL_BOUNCE_CONF_DIR:-/etc/postfix}"
  TRANSPORT_FILE="$CONF_DIR/olimpiada_bounce_transport"
  ALIAS_FILE="$CONF_DIR/olimpiada_bounce_alias"

  say() { printf '       [olimpiada-bounces] %s\n' "$*"; }

  target="$(printf '%s' "${MAIL_BOUNCE_TARGET:-discard}" | tr -d '[:space:]')"
  if [ "$target" != "discard" ] && ! printf '%s' "$target" | grep -Eq '^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$'; then
    say "OSTRZEŻENIE: MAIL_BOUNCE_TARGET='${MAIL_BOUNCE_TARGET}' to ani 'discard', ani adres e-mail – używam 'discard'."
    target="discard"
  fi

  host="${POSTFIX_myhostname:-$(hostname -f 2>/dev/null || hostname)}"

  addresses="$(printf '%s' "${MAIL_BOUNCE_ADDRESSES:-}" | tr ',' ' ')"
  if [ -z "${addresses// /}" ]; then
    addresses=""
    for domain in ${ALLOWED_SENDER_DOMAINS:-}; do
      [ "$domain" = "*" ] && continue
      addresses="$addresses noreply@$domain"
    done
  fi
  addresses="$addresses postmaster@$host MAILER-DAEMON@$host double-bounce@$host"

  # Jeden adres raz, małymi literami (postmap i tak składa klucze do małych liter), tylko adresy.
  unique="$(for address in $addresses; do printf '%s\n' "$address"; done \
    | tr '[:upper:]' '[:lower:]' | grep -E '^[^@[:space:]]+@[^@[:space:]]+$' | sort -u)"

  : >"$TRANSPORT_FILE" 2>/dev/null || { say "OSTRZEŻENIE: nie da się zapisać $TRANSPORT_FILE – bez zmian."; exit 0; }
  : >"$ALIAS_FILE"
  if [ "$target" = "discard" ]; then
    for address in $unique; do printf '%s\tdiscard:olimpiada-bounce\n' "$address" >>"$TRANSPORT_FILE"; done
  else
    lowered_target="$(printf '%s' "$target" | tr '[:upper:]' '[:lower:]')"
    for address in $unique; do
      # Przekierowanie na samego siebie zrobiłoby pętlę aliasów – taki adres zostaje bez zmiany.
      [ "$address" = "$lowered_target" ] && continue
      printf '%s\t%s\n' "$address" "$target" >>"$ALIAS_FILE"
    done
  fi

  if ! "$POSTMAP" "lmdb:$TRANSPORT_FILE" || ! "$POSTMAP" "lmdb:$ALIAS_FILE"; then
    say "OSTRZEŻENIE: postmap się nie powiódł – poczta zwrotna bez zmian."
    exit 0
  fi
  "$POSTCONF" -e "transport_maps=lmdb:$TRANSPORT_FILE" || say "OSTRZEŻENIE: postconf transport_maps"
  "$POSTCONF" -e "virtual_alias_maps=lmdb:$ALIAS_FILE" || say "OSTRZEŻENIE: postconf virtual_alias_maps"

  say "poczta zwrotna -> $target dla: $(printf '%s' "$unique" | tr '\n' ' ')"
  exit 0
)
olimpiada_bounces || true
unset -f olimpiada_bounces
