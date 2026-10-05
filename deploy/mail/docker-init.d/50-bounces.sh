#!/usr/bin/env bash
# Poczta zwrotna relaya `mail` (MAIL-01 § 5, MAIL-02 § 2.1, docs/OPERACJE.md § 49.7 i § 52).
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
#   MAIL_BOUNCE_TARGET=discard (domyślnie w skrypcie) – wyrzucenie (transport `discard:`); ślad
#       zostaje w logu: pierwotny list ma `status=bounced`, odbicie `status=sent (… discard …)`,
#   MAIL_BOUNCE_TARGET=capture (MAIL-02, domyślnie w compose) – zawiadomienia (DSN) na noreply@
#       trafiają natywnym agentem `virtual(8)` do skrzynki Maildir `bounces/` w katalogu
#       MAIL_BOUNCE_MAILDIR_BASE (wolumen `mail_bounces`), skąd czyta je worker aplikacji
#       (apps.email_delivery.tasks.process_bounce_mailbox). Pliki należą do MAIL_BOUNCE_UID/GID
#       (1000:1000 = użytkownik `app` obrazu aplikacji), więc worker kasuje przetworzone bez
#       dodatkowych uprawnień. Adresy systemowe (niżej) – nadal `discard`: ich odbicia nie dotyczą
#       żadnego konta,
#   MAIL_BOUNCE_TARGET=<adres e-mail> – przekierowanie (virtual_alias_maps) na skrzynkę operatora.
# Adresy: MAIL_BOUNCE_ADDRESSES (spacje/przecinki; puste = noreply@ każdej domeny
# z ALLOWED_SENDER_DOMAINS) plus zawsze adresy systemowe: MAIL_BOUNCE_EXTRA_ADDRESSES (compose:
# nadawcy GlitchTipa i monitora dostępności, OPS-02) oraz postmaster, MAILER-DAEMON i double-bounce
# w domenie myhostname – na nie Postfix wysyła podwójne odbicia.
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
# Atrapy w teście (scripts/tests/mail_bounces_test.sh): POSTCONF, POSTMAP, MAIL_BOUNCE_CONF_DIR,
# MAIL_BOUNCE_MAILDIR_BASE.

olimpiada_bounces() (
  set -u
  set -f  # ALLOWED_SENDER_DOMAINS='*' (wariant B) nie może się rozwinąć w nazwy plików

  POSTCONF="${POSTCONF:-postconf}"
  POSTMAP="${POSTMAP:-postmap}"
  CONF_DIR="${MAIL_BOUNCE_CONF_DIR:-/etc/postfix}"
  TRANSPORT_FILE="$CONF_DIR/olimpiada_bounce_transport"
  ALIAS_FILE="$CONF_DIR/olimpiada_bounce_alias"
  MAILBOX_FILE="$CONF_DIR/olimpiada_bounce_mailbox"
  MAILDIR_BASE="${MAIL_BOUNCE_MAILDIR_BASE:-/var/mail/olimpiada}"

  say() { printf '       [olimpiada-bounces] %s\n' "$*"; }

  target="$(printf '%s' "${MAIL_BOUNCE_TARGET:-discard}" | tr -d '[:space:]')"
  if [ "$target" != "discard" ] && [ "$target" != "capture" ] \
    && ! printf '%s' "$target" | grep -Eq '^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$'; then
    say "OSTRZEŻENIE: MAIL_BOUNCE_TARGET='${MAIL_BOUNCE_TARGET}' to ani 'discard'/'capture', ani adres e-mail – używam 'discard'."
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
  system_addresses="$(printf '%s' "${MAIL_BOUNCE_EXTRA_ADDRESSES:-}" | tr ',' ' ')"
  system_addresses="$system_addresses postmaster@$host MAILER-DAEMON@$host double-bounce@$host"

  # Jeden adres raz, małymi literami (postmap i tak składa klucze do małych liter), tylko adresy.
  normalize() {
    for address in "$@"; do printf '%s\n' "$address"; done \
      | tr '[:upper:]' '[:lower:]' | grep -E '^[^@[:space:]]+@[^@[:space:]]+$' | sort -u
  }
  # shellcheck disable=SC2086 # dzielenie listy na słowa jest celowe (globbing wyłącza set -f)
  unique="$(normalize $addresses $system_addresses)"
  # shellcheck disable=SC2086
  system_unique="$(normalize $system_addresses)"

  : >"$TRANSPORT_FILE" 2>/dev/null || { say "OSTRZEŻENIE: nie da się zapisać $TRANSPORT_FILE – bez zmian."; exit 0; }
  : >"$ALIAS_FILE"
  : >"$MAILBOX_FILE"
  uid="${MAIL_BOUNCE_UID:-1000}"
  gid="${MAIL_BOUNCE_GID:-1000}"
  if [ "$target" = "discard" ]; then
    for address in $unique; do printf '%s\tdiscard:olimpiada-bounce\n' "$address" >>"$TRANSPORT_FILE"; done
  elif [ "$target" = "capture" ]; then
    # virtual(8) odmawia doręczenia jako UID < virtual_minimum_uid (100) – lepiej powiedzieć to teraz,
    # niż szukać później „bounce or trace service failure” w logu.
    if ! printf '%s:%s' "$uid" "$gid" | grep -Eq '^[0-9]+:[0-9]+$' || [ "$uid" -lt 100 ]; then
      say "OSTRZEŻENIE: MAIL_BOUNCE_UID/GID='$uid:$gid' – potrzebne liczby i UID ≥ 100; używam 1000:1000."
      uid=1000
      gid=1000
    fi
    mkdir -p "$MAILDIR_BASE" 2>/dev/null || say "OSTRZEŻENIE: nie da się utworzyć $MAILDIR_BASE."
    chown "$uid:$gid" "$MAILDIR_BASE" 2>/dev/null || say "OSTRZEŻENIE: chown $uid:$gid $MAILDIR_BASE się nie powiódł."
    chmod 0750 "$MAILDIR_BASE" 2>/dev/null || true
    for address in $unique; do
      if printf '%s\n' "$system_unique" | grep -qxF -- "$address"; then
        printf '%s\tdiscard:olimpiada-bounce\n' "$address" >>"$TRANSPORT_FILE"
      else
        # `virtual:` bez celu + wiersz w virtual_mailbox_maps: wszystkie adresy do jednego Maildira
        # (ukośnik na końcu = format Maildir, nie mbox).
        printf '%s\tvirtual:\n' "$address" >>"$TRANSPORT_FILE"
        printf '%s\tbounces/\n' "$address" >>"$MAILBOX_FILE"
      fi
    done
  else
    lowered_target="$(printf '%s' "$target" | tr '[:upper:]' '[:lower:]')"
    for address in $unique; do
      # Przekierowanie na samego siebie zrobiłoby pętlę aliasów – taki adres zostaje bez zmiany.
      [ "$address" = "$lowered_target" ] && continue
      printf '%s\t%s\n' "$address" "$target" >>"$ALIAS_FILE"
    done
  fi

  if ! "$POSTMAP" "lmdb:$TRANSPORT_FILE" || ! "$POSTMAP" "lmdb:$ALIAS_FILE" || ! "$POSTMAP" "lmdb:$MAILBOX_FILE"; then
    say "OSTRZEŻENIE: postmap się nie powiódł – poczta zwrotna bez zmian."
    exit 0
  fi
  "$POSTCONF" -e "transport_maps=lmdb:$TRANSPORT_FILE" || say "OSTRZEŻENIE: postconf transport_maps"
  "$POSTCONF" -e "virtual_alias_maps=lmdb:$ALIAS_FILE" || say "OSTRZEŻENIE: postconf virtual_alias_maps"
  if [ "$target" = "capture" ]; then
    # virtual_mailbox_domains pusty jawnie: domyślne `$virtual_mailbox_maps` pytałoby tablicę także
    # o same domeny – dziś bez trafień, ale klasa adresu ma zależeć wyłącznie od transport_maps.
    # virtual_mailbox_limit=0: limit dotyczy wyłącznie plików mbox, a przy message_size_limit=0
    # (bez limitu) agent virtual kończy się „fatal: virtual_mailbox_limit is limited but
    # message_size_limit is unlimited” – sprawdzone na boky/postfix:v5.1.0-alpine 5.10.2026.
    "$POSTCONF" -e "virtual_mailbox_base=$MAILDIR_BASE" "virtual_mailbox_maps=lmdb:$MAILBOX_FILE" \
      "virtual_uid_maps=static:$uid" "virtual_gid_maps=static:$gid" "virtual_mailbox_domains=" \
      "virtual_mailbox_limit=0" \
      || say "OSTRZEŻENIE: postconf virtual_mailbox_*"
    say "poczta zwrotna -> capture ($MAILDIR_BASE/bounces/, $uid:$gid) dla: $(cut -f1 "$MAILBOX_FILE" | tr '\n' ' ')"
    say "poczta zwrotna -> discard dla: $(printf '%s' "$system_unique" | tr '\n' ' ')"
  else
    say "poczta zwrotna -> $target dla: $(printf '%s' "$unique" | tr '\n' ' ')"
  fi
  exit 0
)
olimpiada_bounces || true
unset -f olimpiada_bounces
