#!/usr/bin/env bash
# Test zestawów usług compose'a i źródła obrazu aplikacji.
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu):
#   scripts/tests/compose_profiles_test.sh
#
# Odpowiednik scripts/tests/render_caddyfile_test.sh dla drugiego pliku, którego dotyczy punkt 11
# z docs/UNIWERSALNY-ETAP-2.md § 0.2 („kroki wdrożenia dla Konkursu #1 bez zmian”). Pilnuje trzech
# rzeczy naraz:
#
#   1. `docker compose -f docker-compose.yml` startuje **dokładnie te same dziesięć usług**, co
#      przed etapem 2 — lista jest wpisana niżej wprost, żeby test mówił, co uznaje za „dzisiaj”;
#   2. nakładka `docker-compose.operator.yml` odejmuje z tego zestawu **tylko** `clamav` i `mail`,
#      a z profilem `full` oddaje go z powrotem **na równość** (warunek z § 1.7.3) i nie rusza przy
#      okazji żadnej innej linijki konfiguracji;
#   3. bez zmiennej `WEB_IMAGE` obraz `web`/`worker`/`beat` jest ten, co był
#      (`olimpiada/web:$APP_VERSION`), a z nią — ten z rejestru;
#   4. wersja porównawcza dj. (docs/tasks/DJ-01.md): profil `djcms` dokłada jedną usługę, a nakładka
#      `docker-compose.djcms.yml` (włączana przez COMPOSE_FILE w .env) – wyłącznie montaż mediów
#      w `proxy`; bez nich konfiguracja jest ta sama co przed DJ-01;
#   5. audyt z 1.10.2026 (przypadki 12–17): Redis wyłącznie w sieci `cache` z web/worker/beat,
#      ClamAV z wyjściem przez osobną `clamav_egress`, żadna z nich w TRUSTED_PROXY_IPS ani
#      `mynetworks`; hasło Redisa opcjonalne (bez niego – konfiguracja jak dotąd); porty nakładki
#      deweloperskiej tylko na 127.0.0.1; nakładka E2E na podsieciach rozłącznych z dev;
#   6. monitoring błędów i dostępności (OPS-02, przypadek 18): profil `monitoring` dokłada GlitchTipa,
#      jego bazę w izolowanej sieci `errors` i `uptime`, bez sekretów platformy; GlitchTip poza
#      `edge`/`internal`, w sieciach errors_front/ingest/egress; relay z jednym nadawcą dla niego.
#
# Wszystko przez `docker compose config`, czyli bez demona, bez sieci i bez budowania czegokolwiek:
# sprawdzamy złożenie plików, a nie działającą instalację. Zmienne bierzemy z `.env.example`,
# żeby wynik nie zależał od tego, co ma u siebie w `.env` osoba uruchamiająca test.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE="$ROOT/docker-compose.yml"
OPERATOR="$ROOT/docker-compose.operator.yml"
ENV_FILE="$ROOT/.env.example"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/compose-profiles-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# Zestaw usług zwykłego (produkcyjnego) uruchomienia — stan sprzed etapu 2, posortowany.
DZISIAJ="beat clamav db mail minio minio-init proxy redis web worker"
# Zestaw „świeżego operatora”: to samo bez skanera i bez własnego MTA.
OPERATORSKI="beat db minio minio-init proxy redis web worker"

if ! docker compose version >/dev/null 2>&1; then
  printf 'NIE WYKONANO: brak `docker compose` w PATH – ten test niczego nie sprawdził.\n' >&2
  exit 2
fi

failures=0
check() {
  # check "opis" <kod-wyniku>
  if [ "$2" -eq 0 ]; then
    printf 'ok   %s\n' "$1"
  else
    printf 'FAIL %s\n' "$1"
    failures=$((failures + 1))
  fi
}

# uslugi <plik-wyjściowy> [argumenty compose...] – lista usług w jednej linii, alfabetycznie.
uslugi() {
  docker compose --env-file "$ENV_FILE" "$@" config --services 2>"$WORK/stderr" | sort | tr '\n' ' ' | sed 's/ $//'
}

# 1. Zestaw domyślny pliku podstawowego = to, co startuje na produkcji Olimpiady Kwantowej.
got="$(uslugi -f "$BASE")"
[ "$got" = "$DZISIAJ" ]
check "docker compose (bez nakładek) daje dzisiejsze dziesięć usług [$got]" $?

# 2. Nakładka operatorska odejmuje wyłącznie clamav i mail.
got="$(uslugi -f "$BASE" -f "$OPERATOR")"
[ "$got" = "$OPERATORSKI" ]
check "nakładka operatorska daje osiem usług bez clamav i mail [$got]" $?

# 3. Warunek z § 1.7.3: profil `full` wraca do zestawu dzisiejszego **na równość**.
got="$(uslugi -f "$BASE" -f "$OPERATOR" --profile full)"
[ "$got" = "$DZISIAJ" ]
check "nakładka operatorska z --profile full = zestaw dzisiejszy, co do usługi [$got]" $?

# 4. …i nie zmienia przy okazji niczego innego: jedyna różnica w złożonej konfiguracji to wpisy
#    profilu oraz `required` przy zależności workera od skanera.
docker compose --env-file "$ENV_FILE" -f "$BASE" config >"$WORK/base.yml" 2>/dev/null
docker compose --env-file "$ENV_FILE" -f "$BASE" -f "$OPERATOR" --profile full config >"$WORK/full.yml" 2>/dev/null
diff "$WORK/base.yml" "$WORK/full.yml" | grep -E '^[<>]' \
  | grep -vE '^[<>][[:space:]]+(profiles:|- full|required: (true|false))$' >"$WORK/inne.txt"
[ ! -s "$WORK/inne.txt" ]
check "nakładka operatorska nie zmienia żadnej innej linijki konfiguracji" $?
[ -s "$WORK/inne.txt" ] && sed 's/^/     /' "$WORK/inne.txt"

# 5. Bez WEB_IMAGE obraz jest ten, co dotąd: olimpiada/web:$APP_VERSION dla web, worker i beat.
#    (`.env.example` ma APP_VERSION=dev; cztery wystąpienia to trzy usługi plus kotwica x-app-base.)
docker compose --env-file "$ENV_FILE" -f "$BASE" config 2>/dev/null \
  | grep -E '^    image: ' | sed 's/^[[:space:]]*//' | sort | uniq -c >"$WORK/obrazy.txt"
grep -qE '^ *3 image: olimpiada/web:dev$' "$WORK/obrazy.txt"
check "bez WEB_IMAGE web, worker i beat stoją na olimpiada/web:\$APP_VERSION" $?

# 6. Z WEB_IMAGE – ten sam obraz z rejestru w tych samych trzech usługach i ani jednej lokalnej nazwy.
WEB_IMAGE="ghcr.io/qaif/olimpiada-web:v0.24.0" docker compose --env-file "$ENV_FILE" -f "$BASE" config 2>/dev/null \
  | grep -E '^    image: ' | sed 's/^[[:space:]]*//' | sort | uniq -c >"$WORK/obrazy-ghcr.txt"
grep -qE '^ *3 image: ghcr\.io/qaif/olimpiada-web:v0\.24\.0$' "$WORK/obrazy-ghcr.txt" &&
  ! grep -qE 'image: olimpiada/web:' "$WORK/obrazy-ghcr.txt"
check "WEB_IMAGE podmienia obraz web, worker i beat naraz" $?

# 7. Nakładka deweloperska dalej się składa (mail w profilu `never`, e2e w `e2e`) – gdyby nowa
#    nakładka rozjechała się z nią o nazwy usług, wyszłoby dopiero przy `scripts/e2e.sh`.
docker compose --env-file "$ENV_FILE" -f "$BASE" -f "$ROOT/docker-compose.dev.yml" config -q 2>"$WORK/stderr"
check "złożenie z docker-compose.dev.yml pozostaje poprawne" $?

# 8. Wersja porównawcza django CMS (docs/tasks/DJ-01.md § 8.7): profil `djcms` dokłada **dokładnie**
#    jedną usługę do zestawu dzisiejszego. Przypadek 1 wyżej pilnuje drugiej połowy kontraktu –
#    bez profilu zestaw jest ten sam, co przed DJ-01.
got="$(uslugi -f "$BASE" --profile djcms)"
[ "$got" = "$(printf '%s\n' $DZISIAJ djcms | sort | tr '\n' ' ' | sed 's/ $//')" ]
check "--profile djcms = zestaw dzisiejszy + djcms [$got]" $?

# 9. Rozdział sekretów i utwardzenie usługi djcms (reguła 11 z § 7): bez `env_file` (żadnego
#    sekretu aplikacji głównej z .env), system plików tylko do odczytu, bez uprawnień jądra,
#    wyłącznie sieć `internal` (bez wyjścia do internetu).
docker compose --env-file "$ENV_FILE" -f "$BASE" --profile djcms config 2>"$WORK/stderr" \
  | awk '/^  djcms:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' >"$WORK/djcms.yml"
# Blok usługi wycięty z YAML-a awk-iem, bez pythona i jq – skrypt biegnie też na serwerze
# i w Git Bash. Wcięcia są kontraktem wyjścia `docker compose config`: klucze usługi 4 spacje,
# elementy list i klucze map zagnieżdżonych 6.
problemy=""
grep -qE '^    env_file:' "$WORK/djcms.yml" && problemy="$problemy env_file;"
grep -qE '^    read_only: true$' "$WORK/djcms.yml" || problemy="$problemy read_only;"
awk '/^    cap_drop:$/ {on=1; next} on && /^    [^ ]/ {on=0} on' "$WORK/djcms.yml" | tr -d ' ' \
  | grep -qxE -- '-ALL' || problemy="$problemy cap_drop;"
sieci="$(awk '/^    networks:$/ {on=1; next} on && /^    [^ ]/ {on=0} on && /^      [^ ]/' "$WORK/djcms.yml" \
  | sed 's/^ *//; s/:.*//' | sort | tr '\n' ' ' | sed 's/ $//')"
[ "$sieci" = "internal" ] || problemy="$problemy sieci=[$sieci];"
grep -qE '^      (POSTGRES_|MINIO_|S3_|DJANGO_SECRET|REDIS|CELERY)' "$WORK/djcms.yml" \
  && problemy="$problemy zmienne aplikacji głównej;"
[ -s "$WORK/djcms.yml" ] && [ -z "$problemy" ]
check "djcms: bez env_file, read_only, cap_drop [ALL], tylko sieć internal, bez sekretów backendu [${problemy:-ok}]" $?

# 10. Nakładka docker-compose.djcms.yml (DJ-01h, DJ-02h): dokłada do konfiguracji WYŁĄCZNIE montaż
#     wolumenu `djcms_media` do `proxy` (tylko do odczytu) – pliki redaktorów pod /djcms/media/ – i stały
#     adres `proxy` w sieci `internal` (jedyne proxy, któremu ufa djcms). Bez niej (dj. wyłączone)
#     `proxy` jest ten sam co przed DJ-01; przypadek 1 i ten niżej razem to kontrakt.
DJ_OVERLAY="$ROOT/docker-compose.djcms.yml"
docker compose --env-file "$ENV_FILE" -f "$BASE" --profile djcms config >"$WORK/dj-bez.yml" 2>/dev/null
docker compose --env-file "$ENV_FILE" -f "$BASE" -f "$DJ_OVERLAY" --profile djcms config >"$WORK/dj-z.yml" 2>"$WORK/stderr"
diff "$WORK/dj-bez.yml" "$WORK/dj-z.yml" | grep -E '^[<>]' >"$WORK/dj-diff.txt"
[ "$(sed 's/^\([<>]\) */\1/' "$WORK/dj-diff.txt" | tr '\n' '|')" = "<internal: null|>internal:|>ipv4_address: 172.30.2.250|>- type: volume|>source: djcms_media|>target: /srv/djcms-media|>read_only: true|>volume: {}|" ]
rc=$?
check "nakładka djcms dokłada tylko montaż djcms_media:/srv/djcms-media:ro i adres proxy w sieci internal" $rc
[ $rc -eq 0 ] || sed 's/^/     /' "$WORK/dj-diff.txt"
awk '/^  proxy:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/dj-z.yml" | grep -q 'target: /srv/djcms-media'
check "montaż djcms_media trafia do usługi proxy" $?
got="$(uslugi -f "$BASE" -f "$DJ_OVERLAY")"
[ "$got" = "$DZISIAJ" ]
check "sama nakładka (bez profilu) nie dokłada usług [$got]" $?

# 10a. Zaufane proxy djcms (DJ-02 D10): djcms ufa `X-Real-IP` i `X-Djcms-Mode` WYŁĄCZNIE od adresu
#      `proxy` przypiętego przez nakładkę – nie od podsieci compose'a, w której stoją też web, worker,
#      minio, clamav i poczta. `TRUSTED_PROXY_IPS` djcms = ten jeden adres, równy `ipv4_address`
#      proxy w sieci `internal` i leżący w jej podsieci – także przy innej wartości DJCMS_PROXY_IP
#      (ta sama zmienna w obu plikach). Aplikacja główna (`web`) ufa jak dotąd.
dj_trust() {  # dj_trust <plik config> – "TRUSTED_PROXY_IPS djcms|ipv4 proxy w internal|podsieć internal"
  local cfg="$1" trusted proxy_ip subnet
  trusted="$(awk '/^  djcms:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$cfg" | sed -n 's/^      TRUSTED_PROXY_IPS: //p' | tr -d '"')"
  proxy_ip="$(awk '/^  proxy:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$cfg" \
    | awk '/^      internal:$/ {on=1; next} on && /^        ipv4_address: / {print $2; exit} /^      [^ ]/ {on=0}')"
  subnet="$(awk '/^networks:$/ {n=1} n && /^  internal:$/ {on=1; next} on && /subnet: / {print $NF; exit}' "$cfg")"
  printf '%s|%s|%s' "$trusted" "$proxy_ip" "$subnet"
}
got="$(dj_trust "$WORK/dj-z.yml")"
[ "$got" = "172.30.2.250|172.30.2.250|172.30.2.0/24" ]
check "djcms ufa wyłącznie adresowi proxy przypiętemu w sieci internal [$got]" $?
DJCMS_PROXY_IP=172.30.2.240 docker compose --env-file "$ENV_FILE" -f "$BASE" -f "$DJ_OVERLAY" --profile djcms config >"$WORK/dj-ip.yml" 2>/dev/null
got="$(dj_trust "$WORK/dj-ip.yml")"
[ "$got" = "172.30.2.240|172.30.2.240|172.30.2.0/24" ]
check "DJCMS_PROXY_IP zmienia przypięcie i zaufanie razem [$got]" $?
! awk '/^  proxy:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/dj-bez.yml" | grep -q 'ipv4_address'
check "bez nakładki proxy nie ma stałego adresu (konfiguracja sprzed DJ-01)" $?
awk '/^  web:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/dj-z.yml" | grep -qF 'TRUSTED_PROXY_IPS: 172.30.1.0/24,172.30.2.0/24'
check "aplikacja główna (web) ufa proxy jak dotąd (podsieci z .env)" $?

# 11. Włączenie przez .env – dokładnie te linijki, które dopisuje scripts/deploy.sh przy
#     DJCMS_ENABLED=1 (wycięte z deploy.sh, nie przepisane): docker compose czyta COMPOSE_FILE
#     i COMPOSE_PROFILES z .env sam, więc gołe `docker compose` w katalogu instalacji widzi djcms
#     i montaż w proxy. Separator ścieżek ustawiony na `:` jak na serwerze (Windows domyślnie `;`).
SANDBOX="$WORK/instalacja"
mkdir -p "$SANDBOX"
cp "$BASE" "$DJ_OVERLAY" "$SANDBOX/"
{
  cat "$ENV_FILE"
  grep -oE '"(COMPOSE_FILE|COMPOSE_PROFILES)=[^"]+"' "$ROOT/scripts/deploy.sh" | tr -d '"' | sort -u
} >"$SANDBOX/.env"
[ "$(grep -cE '^COMPOSE_(FILE|PROFILES)=' "$SANDBOX/.env")" = "2" ]
check "deploy.sh zawiera linijki COMPOSE_FILE i COMPOSE_PROFILES dla dj." $?
got="$(cd "$SANDBOX" && env -u COMPOSE_FILE -u COMPOSE_PROFILES COMPOSE_PATH_SEPARATOR=: docker compose config --services 2>"$WORK/stderr" | sort | tr '\n' ' ' | sed 's/ $//')"
[ "$got" = "$(printf '%s\n' $DZISIAJ djcms | sort | tr '\n' ' ' | sed 's/ $//')" ]
check "gołe docker compose z .env po włączeniu dj. = zestaw dzisiejszy + djcms [$got]" $?
(cd "$SANDBOX" && env -u COMPOSE_FILE -u COMPOSE_PROFILES COMPOSE_PATH_SEPARATOR=: docker compose config 2>/dev/null) \
  | awk '/^  proxy:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' | grep -q 'target: /srv/djcms-media'
check "…i proxy z montażem djcms_media" $?

# --- Audyt bezpieczeństwa z 1.10.2026: sieci, Redis z hasłem, porty deweloperskie ----------------

# sieci_uslug <plik config> – „usługa:sieć” dla każdej usługi (blok `networks:` usługi), posortowane.
sieci_uslug() {
  awk '/^services:/ {s=1; next} /^[a-z]/ {s=0} s && /^  [a-z]/ {svc=$1; sub(":", "", svc)}
       s && /^    networks:/ {n=1; next} n && /^    [^ ]/ {n=0}
       n && /^      [^ ]/ {net=$1; sub(":", "", net); print svc ":" net}' "$1" | sort
}
# siec <plik config> <nazwa> – blok sieci z sekcji `networks:` najwyższego poziomu.
siec() { awk -v want="  $2:" '/^networks:/ {n=1; next} /^[a-z]/ {n=0} n && $0 == want {on=1; next} n && /^  [^ ]/ {on=0} on' "$1"; }

# 12. Redis: wyłącznie sieć `cache`, a w niej wyłącznie redis, web, worker i beat – także przy
#     profilach djcms i monitoring (djcms ma własny cache LocMem, monitor sprawdza po HTTP).
docker compose --env-file "$ENV_FILE" -f "$BASE" --profile djcms --profile monitoring config >"$WORK/pelny.yml" 2>/dev/null
sieci_uslug "$WORK/pelny.yml" >"$WORK/sieci.txt"
got="$(grep ':cache$' "$WORK/sieci.txt" | cut -d: -f1 | tr '\n' ' ' | sed 's/ $//')"
[ "$got" = "beat redis web worker" ]
check "sieć cache: wyłącznie beat, redis, web, worker [$got]" $?
got="$(grep '^redis:' "$WORK/sieci.txt" | cut -d: -f2 | tr '\n' ' ' | sed 's/ $//')"
[ "$got" = "cache" ]
check "redis wyłącznie w sieci cache (nie w internal) [$got]" $?
siec "$WORK/pelny.yml" cache >"$WORK/cache.yml"
grep -qx '    internal: true' "$WORK/cache.yml" && grep -qx '        - subnet: 172.30.3.0/24' "$WORK/cache.yml"
check "sieć cache: internal: true, podsieć 172.30.3.0/24" $?

# 13. ClamAV: `internal` (web/worker) + `clamav_egress` (freshclam), w której nie ma nikogo innego.
got="$(grep '^clamav:' "$WORK/sieci.txt" | cut -d: -f2 | tr '\n' ' ' | sed 's/ $//')"
[ "$got" = "clamav_egress internal" ]
check "clamav w sieciach clamav_egress i internal [$got]" $?
got="$(grep ':clamav_egress$' "$WORK/sieci.txt" | cut -d: -f1 | tr '\n' ' ' | sed 's/ $//')"
[ "$got" = "clamav" ]
check "sieć clamav_egress: wyłącznie clamav [$got]" $?
siec "$WORK/pelny.yml" clamav_egress >"$WORK/egress.yml"
! grep -q 'internal: true' "$WORK/egress.yml" && grep -qx '        - subnet: 172.30.4.0/24' "$WORK/egress.yml"
check "sieć clamav_egress: z wyjściem do internetu (bez internal: true), podsieć 172.30.4.0/24" $?
awk '/^  clamav:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/pelny.yml" | grep -qE '^    ports:'
[ $? -ne 0 ]
check "clamav bez publikowanych portów" $?

# 14. Nowe sieci NIE są zaufanymi proxy aplikacji ani klientami relaya poczty (ta sama lista).
awk '/^  web:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/pelny.yml" | sed -n 's/^      TRUSTED_PROXY_IPS: //p' >"$WORK/trusted.txt"
awk '/^  mail:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/pelny.yml" | sed -n 's/^      POSTFIX_mynetworks: //p' >>"$WORK/trusted.txt"
[ "$(wc -l <"$WORK/trusted.txt")" -eq 2 ] && ! grep -qE '172\.30\.(3|4)\.' "$WORK/trusted.txt"
check "TRUSTED_PROXY_IPS (web) i mynetworks (mail) bez 172.30.3.0/24 i 172.30.4.0/24" $?
# Podsieci czterech sieci – rozłączne (każda inna /24 z 172.30.x).
for f in "$WORK/pelny.yml"; do
  awk '/^networks:/ {n=1} n && /subnet: / {print $NF}' "$f" | sort >"$WORK/podsieci.txt"
done
[ "$(sort -u "$WORK/podsieci.txt" | wc -l)" -eq 4 ] && [ "$(wc -l <"$WORK/podsieci.txt")" -eq 4 ]
check "cztery sieci, cztery różne podsieci [$(tr '\n' ' ' <"$WORK/podsieci.txt")]" $?

# 15. Hasło Redisa: bez REDIS_PASSWORD (serwer sprzed zmiany, .env.example) adresy są dotychczasowe
#     i Redis bez `requirepass`; z hasłem – `:hasło@` w obu adresach, `requirepass` i healthcheck
#     z REDISCLI_AUTH, który sprawdza PONG (a nie sam kod wyjścia redis-cli).
redis_cfg() {  # redis_cfg <plik> – "REDIS_URL|CELERY_BROKER_URL|requirepass" usługi worker/redis
  local url broker pass
  url="$(awk '/^  worker:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$1" | sed -n 's/^      REDIS_URL: //p')"
  broker="$(awk '/^  worker:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$1" | sed -n 's/^      CELERY_BROKER_URL: //p')"
  pass="$(awk '/^  redis:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$1" | awk '/- --requirepass$/ {getline; sub(/^ *- /, ""); print; exit}')"
  printf '%s|%s|%s' "$url" "$broker" "$pass"
}
got="$(redis_cfg "$WORK/pelny.yml")"
[ "$got" = 'redis://redis:6379/0|redis://redis:6379/1|""' ]
check "bez REDIS_PASSWORD: adresy bez hasła, requirepass pusty (Redis bez hasła, jak dotąd) [$got]" $?
REDIS_PASSWORD=Abc123def456GHI789 docker compose --env-file "$ENV_FILE" -f "$BASE" config >"$WORK/redis-pw.yml" 2>/dev/null
got="$(redis_cfg "$WORK/redis-pw.yml")"
[ "$got" = 'redis://:Abc123def456GHI789@redis:6379/0|redis://:Abc123def456GHI789@redis:6379/1|Abc123def456GHI789' ]
check "z REDIS_PASSWORD: :hasło@ w REDIS_URL i CELERY_BROKER_URL, requirepass = hasło [$got]" $?
awk '/^  redis:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/redis-pw.yml" | grep -q 'REDISCLI_AUTH' &&
  awk '/^  redis:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/redis-pw.yml" | grep -q 'grep -qx PONG'
check "healthcheck redisa uwierzytelnia się (REDISCLI_AUTH) i sprawdza odpowiedź PONG" $?
# Bez ostrzeżeń „variable is not set” przy braku REDIS_PASSWORD (serwer sprzed zmiany ma czysty log).
! docker compose --env-file "$ENV_FILE" -f "$BASE" config -q 2>&1 | grep -q 'REDIS_PASSWORD'
check "brak REDIS_PASSWORD nie daje ostrzeżeń compose'a" $?

# 16. Nakładka deweloperska: każdy publikowany port wyłącznie na 127.0.0.1 (także proxy, djcms,
#     mailpit z profilu dev). W pliku podstawowym publiczne zostają WYŁĄCZNIE porty proxy.
docker compose --env-file "$ENV_FILE" -f "$BASE" -f "$ROOT/docker-compose.dev.yml" --profile dev --profile djcms config >"$WORK/dev.yml" 2>/dev/null
porty() {  # porty <plik> – „usługa host_ip” dla każdego publikowanego portu (host_ip pusty = 0.0.0.0)
  # Wpis wypisywany przy następnym wpisie albo końcu bloku `ports:` – ZANIM reguła niżej przestawi
  # `svc` na następną usługę (`docker compose config` sortuje usługi, a `ports:` bywa ostatnim kluczem).
  awk 'function flush() { if (n) print svc, ip; n = 0 }
       p && /^ {0,4}[^ ]/ { flush(); p = 0 }
       /^services:/ {s=1; next} /^[a-z]/ {s=0} s && /^  [a-z]/ {svc=$1; sub(":", "", svc)}
       s && /^    ports:/ {p=1; next}
       p && /^      - / {flush(); n=1; ip="0.0.0.0"} p && /^        host_ip: / {ip=$2}
       END {flush()}' "$1" | sort -u
}
porty "$WORK/dev.yml" >"$WORK/dev-porty.txt"
[ -s "$WORK/dev-porty.txt" ] && ! grep -v ' 127\.0\.0\.1$' "$WORK/dev-porty.txt" | grep -q .
check "nakładka dev: wszystkie porty na 127.0.0.1 [$(tr '\n' ',' <"$WORK/dev-porty.txt")]" $?
docker compose --env-file "$ENV_FILE" -f "$BASE" --profile dev config >"$WORK/base-dev.yml" 2>/dev/null
got="$(porty "$WORK/base-dev.yml" | tr '\n' ',')"
[ "$got" = "mailpit 127.0.0.1,proxy 0.0.0.0," ]
check "plik podstawowy: publicznie wyłącznie proxy, mailpit na 127.0.0.1 [$got]" $?

# 17. Nakładka E2E djcms (projekt obok deweloperskiego): wszystkie cztery sieci przesunięte z 172.30.x,
#     inaczej Docker nie założy sieci na podsieci, którą ma już projekt dev.
E2E_REPO_DIR="$ROOT" docker compose --env-file "$ENV_FILE" -f "$BASE" -f "$DJ_OVERLAY" -f "$ROOT/docker-compose.e2e-djcms.yml" \
  --profile djcms config >"$WORK/e2e.yml" 2>/dev/null
awk '/^networks:/ {n=1} n && /subnet: / {print $NF}' "$WORK/e2e.yml" | sort >"$WORK/e2e-podsieci.txt"
[ "$(wc -l <"$WORK/e2e-podsieci.txt")" -eq 4 ] && ! grep -q '^172\.30\.' "$WORK/e2e-podsieci.txt" &&
  [ -z "$(comm -12 "$WORK/podsieci.txt" "$WORK/e2e-podsieci.txt")" ]
check "nakładka E2E: cztery sieci, żadna na podsieci projektu dev [$(tr '\n' ' ' <"$WORK/e2e-podsieci.txt")]" $?

# 18. Monitoring błędów i dostępności (OPS-02): profil `monitoring` dokłada do zestawu dzisiejszego
#     dokładnie monitor (Kuma), glitchtip, glitchtip-db i uptime; baza GlitchTipa wyłącznie w sieci
#     `errors` (internal: true), w której poza nią stoi tylko glitchtip; obraz GlitchTipa przypięty
#     skrótem; uptime i glitchtip bez `env_file` (sekrety platformy nie wyjeżdżają).
got="$(uslugi -f "$BASE" --profile monitoring)"
[ "$got" = "beat clamav db glitchtip glitchtip-db mail minio minio-init monitor proxy redis uptime web worker" ]
check "profil monitoring dokłada monitor, glitchtip, glitchtip-db i uptime [$got]" $?
got="$(grep -E '^glitchtip-db:' "$WORK/sieci.txt" | cut -d: -f2 | tr '\n' ' ' | sed 's/ $//')"
[ "$got" = "errors" ]
check "glitchtip-db wyłącznie w sieci errors [$got]" $?
got="$(grep ':errors$' "$WORK/sieci.txt" | cut -d: -f1 | sort | tr '\n' ' ' | sed 's/ $//')"
[ "$got" = "glitchtip glitchtip-db" ]
check "sieć errors: wyłącznie glitchtip i glitchtip-db [$got]" $?
siec "$WORK/pelny.yml" errors | grep -qx '    internal: true'
check "sieć errors: internal: true" $?
grep -qE '^    image: glitchtip/glitchtip:[0-9.]+@sha256:[0-9a-f]{64}$' "$WORK/pelny.yml"
check "obraz GlitchTipa przypięty tagiem i skrótem" $?
for svc in glitchtip glitchtip-db uptime; do
  awk -v s="  $svc:" '$0 == s {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/pelny.yml" >"$WORK/svc.yml"
  # `env_file: .env` compose rozwija w `environment`, więc jego ślad to klucze z .env.example.
  [ -s "$WORK/svc.yml" ] && ! grep -qE 'DJANGO_SECRET_KEY|MINIO_ROOT_PASSWORD|S3_PRIVATE_SECRET_KEY|REDIS_PASSWORD' "$WORK/svc.yml"
  check "$svc bez sekretów platformy (bez env_file .env)" $?
done
# Sieci GlitchTipa (krytyk OPS-02, H3/H4): GlitchTip NIE w `edge` ani `internal` (zaufane proxy
# aplikacji, klienci relaya), tylko w czterech wąskich sieciach; każda z dokładnie tymi członkami.
member_of() {  # member_of <sieć> – usługi w sieci, alfabetycznie
  grep ":$1\$" "$WORK/sieci.txt" | cut -d: -f1 | sort | tr '\n' ' ' | sed 's/ $//'
}
got="$(grep -E '^glitchtip:' "$WORK/sieci.txt" | cut -d: -f2 | sort | tr '\n' ' ' | sed 's/ $//')"
[ "$got" = "errors errors_egress errors_front errors_ingest" ]
check "glitchtip wyłącznie w errors, errors_egress, errors_front, errors_ingest (bez edge/internal) [$got]" $?
got="$(member_of errors_front)"; [ "$got" = "glitchtip proxy" ]
check "sieć errors_front: wyłącznie glitchtip i proxy [$got]" $?
got="$(member_of errors_ingest)"; [ "$got" = "beat glitchtip web worker" ]
check "sieć errors_ingest: web, worker, beat i glitchtip – zgłoszenia także z workera i beat [$got]" $?
got="$(member_of errors_egress)"; [ "$got" = "glitchtip mail" ]
check "sieć errors_egress: wyłącznie glitchtip i mail [$got]" $?
siec "$WORK/pelny.yml" errors_ingest | grep -qx '    internal: true' && siec "$WORK/pelny.yml" errors_front | grep -qx '    internal: true' \
  && ! siec "$WORK/pelny.yml" errors_egress | grep -q 'internal: true'
check "errors_ingest i errors_front bez wyjścia do internetu, errors_egress z wyjściem" $?
! grep -qE '172\.30\.[6-9]\.' "$WORK/trusted.txt" || {
  # mynetworks (druga linia) MA podsieć errors_egress – z jednym nadawcą; TRUSTED_PROXY_IPS (pierwsza) – nie.
  ! head -1 "$WORK/trusted.txt" | grep -qE '172\.30\.[6-9]\.' && sed -n 2p "$WORK/trusted.txt" | grep -q '172\.30\.9\.0/24' \
    && ! sed -n 2p "$WORK/trusted.txt" | grep -qE '172\.30\.[678]\.'
}
check "TRUSTED_PROXY_IPS bez sieci GlitchTipa; mynetworks relaya wyłącznie z errors_egress" $?
mail_cfg="$(awk '/^  mail:$/ {on=1; next} on && /^  [^ ]/ {on=0} on' "$WORK/pelny.yml")"
printf '%s\n' "$mail_cfg" | grep -qF 'POSTFIX_smtpd_sender_restrictions: check_client_access cidr:{ { 172.30.9.0/24 errors_sender_only } }, permit_mynetworks, reject' \
  && printf '%s\n' "$mail_cfg" | grep -qE 'POSTFIX_errors_sender_only: check_sender_access inline:\{ glitchtip@[^ ]+=OK \}, reject$'
check "relay: z podsieci GlitchTipa wyłącznie nadawca glitchtip@<domena>" $?
# Bez profilu – żadnej usługi, bazy ani wolumenu GlitchTipa/uptime (sieci errors_* istnieją, bo należą do
# nich web/worker/beat, proxy i mail – puste poza nimi; ta sama zasada co livekit_signal).
docker compose --env-file "$ENV_FILE" -f "$BASE" config >"$WORK/zwykly.yml" 2>/dev/null
! grep -qE '^  errors:$|^  glitchtip|glitchtip_pg|glitchtip_uploads|uptime_state' "$WORK/zwykly.yml"
check "bez profilu monitoring: ani usług, ani sieci bazy, ani wolumenów GlitchTipa/uptime" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy zestawów compose przeszły.\n'
