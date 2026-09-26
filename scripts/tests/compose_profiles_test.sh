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
#      w `proxy`; bez nich konfiguracja jest ta sama co przed DJ-01.
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

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy zestawów compose przeszły.\n'
