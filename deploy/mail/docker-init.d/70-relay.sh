#!/usr/bin/env bash
# Poczta do domen Microsoftu przez zewnętrzny serwer pośredniczący (MAIL-04, docs/OPERACJE.md § 49.10).
#
# Uruchamiany przez obraz boky/postfix przy każdym starcie, **po** 50-bounces.sh i 60-inbound.sh
# (kolejność alfabetyczna w /docker-init.d/), więc dopisuje się do tablic, które tamte właśnie zbudowały.
#
# Po co: Outlook.com/Hotmail odrzucają listy z adresu serwera błędem S3150 (blokada całego zakresu
# Contabo), a Microsoft odmówił odblokowania („Not qualified for mitigation”). Listy do domen Microsoftu
# idą więc przez serwer SMTP dostawcy z dobrą reputacją (Google Workspace smtp-relay, Brevo, Mailgun,
# Amazon SES – skrypt nie zależy od dostawcy); reszta poczty – jak dotąd, wprost do MX-ów odbiorców.
#
# Zmienne (docker-compose.yml, usługa `mail`):
#   MAIL_RELAY_HOST     – `[host]:port`, `host:port` albo `host` (→ `[host]:587`). Pusty = wyłączone,
#                         skrypt niczego nie rusza. Port 465/2465 (TLS od pierwszego bajtu) – odmowa:
#                         transport `smtp:` rozmawia przez STARTTLS.
#   MAIL_RELAY_DOMAINS  – domeny odbiorców przez relay (spacje/przecinki); pusty = domeny konsumenckie
#                         Microsoftu (DEFAULT_DOMAINS niżej); `*` = cała poczta.
#   MAIL_RELAY_USER / MAIL_RELAY_PASSWORD – SASL; oba puste = bez SASL (uwierzytelnienie adresem IP,
#                         np. Google „Only accept mail from the specified IP addresses”).
#   MAIL_RELAY_TLS_LEVEL – `encrypt` (domyślnie: TLS wymagany) albo `secure` (TLS + certyfikat z nazwą
#                         hosta relaya; plik CA z obrazu).
#
# Co robi:
#   1. tablica olimpiada_relay_tls (`[host]:port encrypt`) dopisana do smtp_tls_policy_maps – do relaya
#      nigdy bez TLS (hasło SASL i treść listów); globalne smtp_tls_security_level=may bez zmian,
#   2. tablica olimpiada_relay_sasl (plik i .lmdb z prawami 600) dopisana do smtp_sasl_password_maps;
#      klucz = cel z transportu, więc login i hasło dostaje wyłącznie relay, nie MX-y odbiorców,
#   3. tablica olimpiada_relay_transport (`<domena> smtp:[host]:port`) DOPISANA do transport_maps
#      z 50-bounces.sh: `transport_maps = lmdb:…bounce_transport lmdb:…relay_transport`. Adresy
#      capture/discard mają pierwszeństwo bez względu na kolejność tablic: Postfix szuka najpierw
#      pełnego adresu we wszystkich tablicach, potem domeny, na końcu `*` (transport(5)).
#   Kolejność 1 → 2 → 3 celowa: gdy TLS albo SASL się nie ustawi, poczta nie zostaje skierowana do relaya.
#
# Hasło nigdy nie trafia do logu ani do argumentów postconf (te widać w `ps`) – wyłącznie do pliku 600.
# Procesy Postfiksa i tak go nie widzą (czyszczą środowisko do `import_environment`); gdy obraz włącza
# skrypt kropką (bit wykonywalny), `unset` na końcu zabiera je też supervisordowi – jak obraz robi
# z RELAYHOST_PASSWORD. Zmienna zostaje w `docker inspect` – jak każda inna z .env.
#
# Skrypt nigdy nie kończy się błędem (umowa jak w 50-bounces.sh): błąd w /docker-init.d/ zatrzymałby
# start relaya, a bez relaya pośredniczącego listy do pozostałych domen nadal mają wychodzić.
# Zła wartość = ostrzeżenie w logu i poczta bez zmian (wprost do MX-ów, także do Microsoftu).
#
# Atrapy w teście (scripts/tests/mail_relay_test.sh): POSTCONF, POSTMAP, MAIL_BOUNCE_CONF_DIR,
# MAIL_RELAY_CA_FILE.

olimpiada_relay() (
  set -u
  set -f  # MAIL_RELAY_DOMAINS='*' nie może się rozwinąć w nazwy plików

  POSTCONF="${POSTCONF:-postconf}"
  POSTMAP="${POSTMAP:-postmap}"
  CONF_DIR="${MAIL_BOUNCE_CONF_DIR:-/etc/postfix}"
  TRANSPORT_FILE="$CONF_DIR/olimpiada_relay_transport"
  SASL_FILE="$CONF_DIR/olimpiada_relay_sasl"
  TLS_FILE="$CONF_DIR/olimpiada_relay_tls"
  # Ten sam plik, który obraz wpisuje przy RELAYHOST (postfix_setup_relayhost w /scripts/functions.sh).
  CA_FILE="${MAIL_RELAY_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}"
  # Domeny konsumenckie Microsoftu (Outlook.com) – te odrzucają S3150. Domeny szkół w Microsoft 365
  # (własna domena, MX *.mail.protection.outlook.com) tu nie pasują: transport_maps patrzy na adres, nie
  # na MX – takie dopisuje się do MAIL_RELAY_DOMAINS (docs/tasks/MAIL-04.md § 0).
  DEFAULT_DOMAINS="outlook.com hotmail.com live.com msn.com outlook.pl hotmail.pl live.pl passport.com"

  say() { printf '       [olimpiada-relay] %s\n' "$*"; }

  raw_host="$(printf '%s' "${MAIL_RELAY_HOST:-}" | tr -d '[:space:]')"
  if [ -z "$raw_host" ]; then
    say "wyłączony (MAIL_RELAY_HOST pusty) – cała poczta wprost do MX-ów odbiorców."
    exit 0
  fi

  # --- host i port ------------------------------------------------------------------------------
  case "$raw_host" in
    \[*\]:*) name="${raw_host#\[}"; name="${name%%\]*}"; port="${raw_host##*\]:}" ;;
    \[*\]) name="${raw_host#\[}"; name="${name%\]}"; port="" ;;
    *:*) name="${raw_host%%:*}"; port="${raw_host#*:}" ;;
    *) name="$raw_host"; port="" ;;
  esac
  name="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
  if ! printf '%s' "$name" | grep -Eqx '[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+'; then
    say "OSTRZEŻENIE: MAIL_RELAY_HOST='${MAIL_RELAY_HOST}' – to nie nazwa hosta ([host]:port) – relay wyłączony."
    exit 0
  fi
  if [ -z "$port" ]; then
    port=587
    say "MAIL_RELAY_HOST bez portu – używam 587 (STARTTLS)."
  fi
  if ! printf '%s' "$port" | grep -Eqx '[0-9]{1,5}' || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    say "OSTRZEŻENIE: MAIL_RELAY_HOST='${MAIL_RELAY_HOST}' – zły port – relay wyłączony."
    exit 0
  fi
  if [ "$port" = "465" ] || [ "$port" = "2465" ]; then
    say "OSTRZEŻENIE: port $port to TLS od pierwszego bajtu (smtps), a transport smtp: mówi STARTTLS – użyj 587; relay wyłączony."
    exit 0
  fi
  nexthop="[$name]:$port"

  # --- domeny -----------------------------------------------------------------------------------
  domains_raw="$(printf '%s' "${MAIL_RELAY_DOMAINS:-}" | tr ',' ' ' | tr '[:upper:]' '[:lower:]')"
  [ -z "${domains_raw// /}" ] && domains_raw="$DEFAULT_DOMAINS"
  all=0
  domains=""
  for domain in $domains_raw; do
    if [ "$domain" = "*" ]; then
      all=1
      continue
    fi
    if ! printf '%s' "$domain" | grep -Eqx '[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+'; then
      say "OSTRZEŻENIE: '$domain' w MAIL_RELAY_DOMAINS to nie domena – pomijam."
      continue
    fi
    case " $domains " in *" $domain "*) continue ;; esac
    domains="$domains $domain"
  done
  domains="${domains# }"
  if [ "$all" -eq 0 ] && [ -z "$domains" ]; then
    say "OSTRZEŻENIE: brak poprawnej domeny w MAIL_RELAY_DOMAINS – relay wyłączony."
    exit 0
  fi

  # --- SASL ---------------------------------------------------------------------------------------
  user="${MAIL_RELAY_USER:-}"
  password="${MAIL_RELAY_PASSWORD:-}"
  if { [ -n "$user" ] && [ -z "$password" ]; } || { [ -z "$user" ] && [ -n "$password" ]; }; then
    say "OSTRZEŻENIE: ustawiono tylko jedno z MAIL_RELAY_USER / MAIL_RELAY_PASSWORD – relay wyłączony."
    exit 0
  fi
  if [ -n "$user" ]; then
    # Format tablicy: `klucz<biały znak>login:hasło` – login z dwukropkiem albo spacją i jakakolwiek
    # nowa linia rozjechałyby wiersz (hasło może mieć dwukropek: Postfix dzieli na pierwszym).
    bad=0
    case "$user" in *[[:space:]:]*) bad=1 ;; esac
    case "$password" in *$'\n'* | *$'\r'*) bad=1 ;; esac
    if [ "$bad" -eq 1 ]; then
      say "OSTRZEŻENIE: MAIL_RELAY_USER ze spacją/dwukropkiem albo MAIL_RELAY_PASSWORD z nową linią – relay wyłączony."
      exit 0
    fi
  fi

  # --- poziom TLS ---------------------------------------------------------------------------------
  tls_level="$(printf '%s' "${MAIL_RELAY_TLS_LEVEL:-encrypt}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  if [ "$tls_level" != "encrypt" ] && [ "$tls_level" != "secure" ]; then
    say "OSTRZEŻENIE: MAIL_RELAY_TLS_LEVEL='${MAIL_RELAY_TLS_LEVEL}' – dozwolone encrypt/secure; używam encrypt."
    tls_level="encrypt"
  fi
  set_ca=0
  if [ "$tls_level" = "secure" ] && [ -z "$("$POSTCONF" -h smtp_tls_CAfile 2>/dev/null)" ]; then
    if [ -r "$CA_FILE" ]; then
      set_ca=1
    else
      say "OSTRZEŻENIE: brak pliku CA $CA_FILE – certyfikatu relaya nie da się sprawdzić; używam encrypt."
      tls_level="encrypt"
    fi
  fi

  # --- pliki ----------------------------------------------------------------------------------------
  : >"$TRANSPORT_FILE" 2>/dev/null || { say "OSTRZEŻENIE: nie da się zapisać $TRANSPORT_FILE – relay wyłączony."; exit 0; }
  for domain in $domains; do
    printf '%s\tsmtp:%s\n' "$domain" "$nexthop" >>"$TRANSPORT_FILE"
  done
  if [ "$all" -eq 1 ]; then
    # Przekierowana poczta przychodząca (60-inbound.sh, postmaster@/abuse@ → MAIL_INBOUND_FORWARD) ma
    # cudzą kopertę (np. @microsoft.com) – relay dostawcy przyjmuje tylko nadawców z naszych domen.
    # Pełny adres wygrywa z `*`, więc ten jeden adresat idzie wprost, jak przed MAIL-04.
    forward="$(printf '%s' "${MAIL_INBOUND_FORWARD:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
    if printf '%s' "$forward" | grep -Eqx '[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+'; then
      printf '%s\tsmtp:\n' "$forward" >>"$TRANSPORT_FILE"
    fi
    printf '*\tsmtp:%s\n' "$nexthop" >>"$TRANSPORT_FILE"
  fi
  printf '%s\t%s\n' "$nexthop" "$tls_level" >"$TLS_FILE"
  if [ -n "$user" ]; then
    # umask w podpowłoce: plik od pierwszej chwili 600 (chmod po zapisie zostawiłby okno 644).
    (umask 077 && rm -f "$SASL_FILE" && printf '%s\t%s:%s\n' "$nexthop" "$user" "$password" >"$SASL_FILE")
    chmod 0600 "$SASL_FILE" 2>/dev/null || true
  else
    rm -f "$SASL_FILE" "$SASL_FILE.lmdb"
  fi

  if ! "$POSTMAP" "lmdb:$TRANSPORT_FILE" || ! "$POSTMAP" "lmdb:$TLS_FILE" \
    || { [ -n "$user" ] && ! "$POSTMAP" "lmdb:$SASL_FILE"; }; then
    say "OSTRZEŻENIE: postmap się nie powiódł – relay wyłączony."
    exit 0
  fi
  [ -n "$user" ] && { chmod 0600 "$SASL_FILE.lmdb" 2>/dev/null || true; }

  # Bieżąca wartość listy tablic bez naszej (ponowny start w tym samym kontenerze nie zdubluje wpisu).
  without() {
    "$POSTCONF" -h "$1" 2>/dev/null | sed "s#lmdb:$2##g" | tr -s ' ,' ' ' | sed 's/^ //; s/ $//'
  }

  # 1. TLS do relaya.
  tls_maps="$(without smtp_tls_policy_maps "$TLS_FILE")"
  if [ "$set_ca" -eq 1 ]; then
    "$POSTCONF" -e "smtp_tls_CAfile=$CA_FILE" || { say "OSTRZEŻENIE: postconf smtp_tls_CAfile – relay wyłączony."; exit 0; }
  fi
  "$POSTCONF" -e "smtp_tls_policy_maps=${tls_maps:+$tls_maps }lmdb:$TLS_FILE" \
    || { say "OSTRZEŻENIE: postconf smtp_tls_policy_maps – relay wyłączony."; exit 0; }

  # 2. SASL do relaya.
  if [ -n "$user" ]; then
    sasl_maps="$(without smtp_sasl_password_maps "$SASL_FILE")"
    "$POSTCONF" -e "smtp_sasl_auth_enable=yes" "smtp_sasl_password_maps=${sasl_maps:+$sasl_maps }lmdb:$SASL_FILE" \
      "smtp_sasl_security_options=noanonymous" "smtp_sasl_tls_security_options=noanonymous" \
      || { say "OSTRZEŻENIE: postconf smtp_sasl_* – relay wyłączony."; exit 0; }
    auth="SASL jako $user"
  else
    auth="bez SASL – dostawca musi uwierzytelniać adres IP serwera"
  fi

  # 3. Na końcu kierowanie poczty.
  transport_maps="$(without transport_maps "$TRANSPORT_FILE")"
  "$POSTCONF" -e "transport_maps=${transport_maps:+$transport_maps }lmdb:$TRANSPORT_FILE" \
    || { say "OSTRZEŻENIE: postconf transport_maps – relay wyłączony."; exit 0; }

  if [ "$all" -eq 1 ]; then
    target="CAŁA poczta (*)${domains:+, w tym: $domains}"
  else
    target="$domains"
  fi
  say "relay $nexthop (TLS $tls_level, $auth) dla: $target"
  exit 0
)
olimpiada_relay || true
unset -f olimpiada_relay
unset MAIL_RELAY_PASSWORD
