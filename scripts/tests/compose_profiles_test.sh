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
#      (`olimpiada/web:$APP_VERSION`), a z nią — ten z rejestru.
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

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy zestawów compose przeszły.\n'
