#!/usr/bin/env bash
# Konfiguracja proxy (Caddy) na serwerze: render → caddy validate → instalacja → caddy reload.
#
# Uruchamiany NA SERWERZE, w katalogu instalacji (/opt/olimpiada); dwa pierwsze woła scripts/deploy.sh:
#   bash scripts/proxy_config.sh render   # krok 4/8: plik z deploy/Caddyfile + .env, walidacja, instalacja
#   bash scripts/proxy_config.sh apply    # krok 4c/8: działające proxy ładuje zainstalowany plik
#   bash scripts/proxy_config.sh update   # render + apply – ręcznie, np. po zmianie EXTRA_DOMAINS w .env
#   bash scripts/proxy_config.sh status   # .env, zainstalowany plik, co widzi kontener
#
# Dlaczego (docs/OPERACJE.md § 23): krok 2/8 wdrożenia kasuje katalog deploy/ i rozpakowuje go od
# nowa. Proxy montowało dawniej deploy/Caddyfile.generated jako POJEDYNCZY plik, czyli i-węzeł z
# chwili startu kontenera – po wdrożeniu Caddy widział skasowaną, starą treść, a `docker compose up -d`
# nie odtwarzał kontenera, bo jego konfiguracja compose'a się nie zmieniła. Nowe nagłówki, trasy
# i domeny nie docierały na produkcję aż do ręcznego `up -d --force-recreate proxy`.
#
# Teraz proxy montuje KATALOG stanu `caddy/` (CADDY_CONFIG_DIR=./caddy w .env → /etc/caddy), który
# krok 2/8 omija – tak jak `maintenance/`. Plik zapisany w tym katalogu kontener widzi od razu, więc
# nową konfigurację wystarczy przeładować (`caddy reload`: bez restartu kontenera, bez zrywania
# połączeń, bez chwili bez HTTPS). Odtworzenie kontenera zostaje tylko drogą awaryjną – gdy proxy
# nie działa albo widzi inną treść (montaż sprzed tej zmiany; jednorazowo przy pierwszym wdrożeniu
# robi to już `up -d` w kroku 4b, bo zmienia się montaż).
#
# `render` nie podmienia działającego pliku na taki, którego Caddy nie przyjmie: nowa treść powstaje
# obok (caddy/Caddyfile.next), przechodzi `caddy validate` w działającym kontenerze proxy i dopiero
# wtedy trafia do caddy/Caddyfile. Odrzucona = kod 1, zainstalowany plik i proxy bez zmian (a przy
# wdrożeniu: nic nie jest jeszcze zbudowane ani zatrzymane). Walidacja idzie w obrazie i środowisku
# DZIAŁAJĄCEGO kontenera – wdrożenie, które zmienia wersję obrazu caddy albo dokłada zmienną
# środowiskową proxy, sprawdza nowy plik starszym Caddym (zob. OPERACJE § 23.3).
#
# Punkty powrotu: pierwszy `render` na serwerze kopiuje najpierw do caddy/Caddyfile to, co widzi
# działające proxy (katalog montowany po zmianie .env nigdy nie jest pusty); każda instalacja nowej
# treści zostawia poprzednią w caddy/Caddyfile.prev. `apply`, którego `caddy reload` odrzuci nową
# treść albo po którego odtworzeniu Caddy nie wstanie, przywraca caddy/Caddyfile z .prev (przy
# odtworzeniu – i startuje proxy z niej) i kończy się kodem 1.
set -euo pipefail
# Git Bash (lokalne próby): bez tego ścieżki kontenera w argumentach `docker compose exec` zamieniają
# się w ścieżki Windows (jak w scripts/maintenance.sh). Na Linuksie zmienna nic nie robi.
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACTION="${1:-}"
case "$ACTION" in
  render|apply|update|status) ;;
  *) echo "użycie: bash scripts/proxy_config.sh render|apply|update|status" >&2; exit 2 ;;
esac

CONF_DIR="caddy"
LIVE="$CONF_DIR/Caddyfile"
NEXT="$CONF_DIR/Caddyfile.next"
PREV="$CONF_DIR/Caddyfile.prev"   # poprzednia treść $LIVE – punkt powrotu, gdy proxy nowej nie przyjmie
BOX_FILE="/etc/caddy/Caddyfile"

say() { printf 'proxy: %s\n' "$*"; }
die() { printf 'proxy_config: BŁĄD: %s\n' "$*" >&2; exit 1; }

[ -f .env ] || die "brak .env w $ROOT – uruchom w katalogu instalacji (/opt/olimpiada)"

env_value() {  # env_value <NAZWA> – jak scripts/render_caddyfile.sh: ostatnie wystąpienie, bez cudzysłowów i CR
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r\042\047'
}

# Proxy ma montować właśnie ten katalog. Inna wartość (albo jej brak) = compose montuje coś innego,
# a plik zainstalowany tutaj nie miałby żadnego skutku – odmowa zamiast cichego „gotowe”.
conf_dir_ok() {
  case "$(env_value CADDY_CONFIG_DIR)" in
    ./caddy|caddy|./caddy/|caddy/) return 0 ;;
    *) return 1 ;;
  esac
}

proxy_running() { [ -n "$(docker compose ps -q --status running proxy </dev/null 2>/dev/null)" ]; }

# Obraz, który postawi compose (docker-compose.yml + .env), różny od obrazu DZIAŁAJĄCEGO proxy –
# wdrożenie podbijające Caddy'ego (DEP-02: caddy 2.8 → 2.10). Wtedy `render` waliduje nowy plik
# w jednorazowym kontenerze NOWEGO obrazu: stary Caddy odrzuca składnię, której nowy wymaga
# (`tls force_automate` nie istnieje w 2.8, a bez niej 2.10 nie wystawia certyfikatów nazwom pod
# `*.`), i zatrzymałby wdrożenie, choć kontener i tak zostanie odtworzony w kroku 4b. Każdy błąd
# odczytu (np. atrapa `docker` w testach) = „bez zmiany” – droga dotychczasowa (`exec`).
#
# Jednorazowy kontener to zwykłe `docker run --network none`, NIE `docker compose run proxy`:
# usługa proxy ma w sieciach compose'a stałe `ipv4_address` (np. DJCMS_PROXY_IP), zajęte przez
# działające proxy – `compose run` kończył się „Address already in use” (wdrożenie v0.48.5,
# 5.10.2026). `caddy validate` nie potrzebuje sieci, portów ani wolumenów – wyłącznie pliku
# i zmiennych, do których odwołuje się Caddyfile (`{$SITE_DOMAIN}` itd.; `proxy_env` niżej).
proxy_image_changed() {
  local want have
  want="$(docker compose config </dev/null 2>/dev/null | awk '/^[^ ]/ { s = ($0 == "services:"); next }
    s && /^  [^ ]/ { svc = $1 } s && svc == "proxy:" && /^    image: / { print $2; exit }')" || return 1
  have="$(docker compose ps --status running --format '{{.Image}}' proxy </dev/null 2>/dev/null | head -n 1 | tr -d '\r')" || return 1
  [ -n "$want" ] && [ -n "$have" ] && [ "$want" != "$have" ] || return 1
  PROXY_IMAGE_WANT="$want" PROXY_IMAGE_HAVE="$have"
}

proxy_env() {
  # Zmienne środowiskowe usługi proxy dokładnie tak, jak compose je poda (`.env` + wartości domyślne
  # z docker-compose.yml), jako KLUCZ=wartość dla `docker run --env-file`. Bez nich `{$SITE_DOMAIN}`
  # itp. byłyby puste i walidacja sprawdzałaby inną konfigurację niż ta, którą proxy załaduje.
  # `docker compose config` wypisuje mapę `environment:` (wartość w cudzysłowie, gdy YAML tego wymaga).
  docker compose config </dev/null 2>/dev/null | awk '
    /^[^ ]/ { s = ($0 == "services:"); next }
    s && /^  [^ ]/ { svc = $1; env = 0; next }
    s && svc == "proxy:" && /^    [^ ]/ { env = ($0 == "    environment:"); next }
    s && svc == "proxy:" && env && /^      [^ ]/ {
      line = substr($0, 7); i = index(line, ": ")
      if (i == 0) { k = line; sub(/:$/, "", k); v = "" } else { k = substr(line, 1, i - 1); v = substr(line, i + 2) }
      if (v ~ /^".*"$/) { v = substr(v, 2, length(v) - 2); gsub(/\\"/, "\"", v); gsub(/\\\\/, "\\", v) }
      else if (v ~ /^\047.*\047$/) { v = substr(v, 2, length(v) - 2); gsub(/\047\047/, "\047", v) }
      print k "=" v
    }'
}

box_sum() {  # suma pliku, który widzi kontener; pusta, gdy proxy nie działa
  docker compose exec -T proxy sha256sum "$BOX_FILE" </dev/null 2>/dev/null | tr -d '\r' | cut -d' ' -f1 || true
}

host_sum() { sha256sum "$LIVE" | cut -d' ' -f1; }

# Jedna zmiana konfiguracji proxy naraz: wdrożenie i scripts/djcms_switch.sh piszą ten sam plik.
LOCKED=0
# Tę samą blokadę trzyma wdrożenie (od kroku 2/8 do końca – scripts/deploy.sh) i przekazuje ją
# swoim krokom przez OLIMPIADA_PROXY_LOCK=held; własny flock czekałby wtedy sam na siebie.
lock() {
  [ "$LOCKED" = 1 ] && return 0
  LOCKED=1
  mkdir -p "$CONF_DIR"
  [ "${OLIMPIADA_PROXY_LOCK:-}" = held ] && return 0
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$CONF_DIR/.lock"
    flock -w 120 9 || die "konfigurację proxy od 2 min zmienia inny proces (djcms_switch.sh, djcms_cutover.sh albo wdrożenie) – ponów"
  fi
}

# Pierwsze uruchomienie na serwerze (brak $LIVE), a proxy działa: najpierw kopia konfiguracji, którą
# kontener WIDZI teraz (przy migracji z CADDYFILE_PATH – stary plik z pojedynczego montażu). Od
# chwili, w której .env ma CADDY_CONFIG_DIR=./caddy, każde odtworzenie proxy (`up -d`, restart po
# awarii, ręczne polecenie po przerwanym wdrożeniu) montuje ten katalog – pusty dałby proxy, które
# nie wstaje. Z kopią: odrzucona nowa konfiguracja zostawia tu działającą, a udana ma punkt powrotu.
seed_live() {
  [ -f "$LIVE" ] && return 0
  [ -s "$BOX_NOW" ] || return 0
  cp "$BOX_NOW" "$LIVE"
  say "$LIVE: kopia konfiguracji, którą widzi działające proxy (pierwsze uruchomienie – punkt powrotu)"
}

flag_on() {  # flag_on <wartość> – 0 dla 1/true/yes/on (jak render_caddyfile.sh)
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

# Tryb serwisu publicznego (DJCMS_PRIMARY) zmienia WYŁĄCZNIE scripts/djcms_switch.sh on|off – z
# kontrolą dymną i powrotem przy porażce. .env w innym trybie niż konfiguracja w działającym proxy
# to przerwane przełączenie (albo ręczna edycja .env): render dokończyłby je po cichu, bez kontroli.
mode_guard() {
  local running want
  flag_on "$(env_value DJCMS_ENABLED)" || return 0
  [ -s "$BOX_NOW" ] || return 0
  running="$(grep -m1 -oE 'header_up X-Djcms-Mode (preview|primary)' "$BOX_NOW" | awk '{ print $3 }' || true)"
  [ -n "$running" ] || return 0
  if flag_on "$(env_value DJCMS_PRIMARY)"; then want=primary; else want=preview; fi
  [ "$running" = "$want" ] && return 0
  die "DJCMS_PRIMARY w .env oznacza tryb $want, a działające proxy ma tryb $running – przerwane przełączenie albo ręczna zmiana .env. Tryb zmienia wyłącznie: bash scripts/djcms_switch.sh $([ "$want" = primary ] && echo on || echo off) (albo przywróć DJCMS_PRIMARY w .env), potem ponów"
}

render() {
  conf_dir_ok || die "CADDY_CONFIG_DIR w .env to „$(env_value CADDY_CONFIG_DIR)”, a nie ./caddy – proxy nie widziałoby tego pliku (wdrożenie dopisuje tę linijkę samo)"
  lock
  BOX_NOW="$(mktemp)"
  trap 'rm -f "$NEXT" "$BOX_NOW" "${VALIDATE_OUT:-}" "${VALIDATE_ENV:-}"' EXIT
  RUNNING=0
  proxy_running && RUNNING=1
  # Treść, którą widzi działające proxy – potrzebna do kopii przy pierwszym renderze i do kontroli
  # trybu djcms; bez tych dwóch powodów (zwykłe wdrożenie bez dj.) nie pytamy kontenera.
  if [ "$RUNNING" = 1 ] && { [ ! -f "$LIVE" ] || flag_on "$(env_value DJCMS_ENABLED)"; }; then
    docker compose exec -T proxy cat "$BOX_FILE" </dev/null >"$BOX_NOW" 2>/dev/null || : >"$BOX_NOW"
  fi
  mode_guard
  seed_live
  # Generator czyta WYŁĄCZNIE .env – zmienna z powłoki operatora nie może po cichu wygrać z plikiem
  # (ta sama zasada co w scripts/djcms_switch.sh). Zła wartość w .env = kod ≠ 0 generatora.
  env -u EXTRA_DOMAINS -u PLATFORM_SUBDOMAINS -u DJCMS_ENABLED -u DJCMS_PRIMARY -u DJCMS_ROUTES_ENV -u NOTEBOOK_LAB_HOST \
    -u CADDYFILE_SRC CADDYFILE_OUT="$NEXT" bash scripts/render_caddyfile.sh
  if [ "$RUNNING" = 1 ]; then
    VALIDATE_OUT="$(mktemp)"
    local where="działającym proxy" rc=0
    if proxy_image_changed; then
      # Plik tylko do odczytu w tym samym miejscu co w proxy, zmienne z compose'a (plik 600 – jest
      # w nich przepustka prac technicznych), bez sieci; brakujący obraz `docker run` pobierze.
      where="nowym obrazie $PROXY_IMAGE_WANT (działa $PROXY_IMAGE_HAVE)"
      VALIDATE_ENV="$(mktemp)"
      chmod 600 "$VALIDATE_ENV"
      proxy_env >"$VALIDATE_ENV" || true
      docker run --rm --network none --env-file "$VALIDATE_ENV" -v "$(pwd -P)/$NEXT:$BOX_FILE:ro" \
        "$PROXY_IMAGE_WANT" caddy validate --config "$BOX_FILE" --adapter caddyfile \
        </dev/null >"$VALIDATE_OUT" 2>&1 || rc=$?
    else
      docker compose exec -T proxy sh -c 'cat > /tmp/Caddyfile.next && caddy validate --config /tmp/Caddyfile.next --adapter caddyfile' \
        <"$NEXT" >"$VALIDATE_OUT" 2>&1 || rc=$?
    fi
    if [ "$rc" != 0 ]; then
      # 125–127 = Docker nie uruchomił polecenia (kontener nie wstał, obraz nie do pobrania, brak
      # `caddy`/`sh`) – to NIE jest ocena konfiguracji. Inny kod = odpowiedź samego `caddy validate`.
      grep -vE '"level":"(info|warn)"' "$VALIDATE_OUT" | tail -n 20 >&2 || true
      case "$rc" in
        125|126|127) die "walidacja nie wystartowała ($where, kod $rc – błąd Dockera wyżej, nie konfiguracji) – $LIVE i działające proxy bez zmian" ;;
        *) die "caddy validate odrzucił nową konfigurację ($where, kod $rc – błąd Caddy'ego wyżej) – $LIVE i działające proxy bez zmian" ;;
      esac
    fi
    say "nowa konfiguracja przechodzi caddy validate ($where)"
  else
    say "kontener proxy nie działa – walidacja pominięta (błąd pokaże start proxy)"
  fi
  if [ -f "$LIVE" ] && cmp -s "$NEXT" "$LIVE"; then
    say "$LIVE bez zmian"
  else
    # W miejscu (`cat >`, ten sam i-węzeł), a nie `mv`: montaż katalogu widzi jedno i drugie, ale
    # kontener sprzed tej zmiany (montaż pojedynczego pliku) – tylko zapis w miejscu.
    [ -f "$LIVE" ] && cat "$LIVE" >"$PREV"
    cat "$NEXT" >"$LIVE"
    say "zainstalowano nową konfigurację w $LIVE (działa po caddy reload – scripts/proxy_config.sh apply)"
  fi
}

# Powrót pliku do poprzedniej treści, gdy proxy nowej nie przyjęło: plik na dysku ma odpowiadać
# temu, na czym proxy działa – inaczej najbliższy restart kontenera (awaria, `up -d`) wczytałby
# konfigurację, której Caddy właśnie odmówił. Zapis w miejscu, jak w render. Kod 0 = przywrócono.
restore_prev() {
  [ -f "$PREV" ] && ! cmp -s "$PREV" "$LIVE" || return 1
  cat "$PREV" >"$LIVE"
  say "$LIVE przywrócony do poprzedniej treści ($PREV)"
}

proxy_admin_up() {  # czekanie (do ~30 s) na API administracyjne Caddy'ego po starcie kontenera
  for _ in $(seq 1 30); do
    docker compose exec -T proxy wget -q -O /dev/null http://127.0.0.1:2019/config/ </dev/null >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

recreate_proxy() {
  docker compose up -d --force-recreate --no-deps proxy </dev/null
  proxy_admin_up
}

apply() {
  [ -f "$LIVE" ] || die "brak $LIVE – bash scripts/proxy_config.sh render (albo wdrożenie)"
  lock
  local want seen
  want="$(host_sum)"
  seen="$(box_sum)"
  if [ -n "$seen" ] && [ "$seen" = "$want" ]; then
    # Caddy sam pomija przeładowanie, gdy konfiguracja się nie zmieniła; odrzucona zostawia poprzednią.
    if ! docker compose exec -T proxy caddy reload --config "$BOX_FILE" --adapter caddyfile </dev/null; then
      restore_prev || true
      die "caddy reload odrzucił nową konfigurację – proxy działa dalej na POPRZEDNIEJ (bash scripts/proxy_config.sh status)"
    fi
    say "konfiguracja załadowana przez caddy reload (bez restartu kontenera)"
    return 0
  fi
  if [ -z "$seen" ]; then
    say "kontener proxy nie działa – uruchamiam go od nowa"
  else
    say "kontener widzi inną treść $BOX_FILE niż $LIVE (montaż sprzed CADDY_CONFIG_DIR) – odtwarzam proxy (kilka sekund bez HTTPS)"
  fi
  if ! recreate_proxy; then
    # Caddy nie wstał z nową konfiguracją (walidacja w starszym obrazie/środowisku przepuściła coś,
    # czego nowy start nie przyjął) – powrót do poprzedniej i drugi start, żeby serwis nie leżał.
    if restore_prev; then
      say "proxy nie wstało z nową konfiguracją – odtwarzam je z poprzedniej"
      recreate_proxy || die "proxy nie wstaje ani z nową, ani z poprzednią konfiguracją – docker compose logs proxy"
      die "nowa konfiguracja proxy nie wystartowała – proxy działa na POPRZEDNIEJ (docker compose logs proxy)"
    fi
    die "proxy nie odpowiada po odtworzeniu – docker compose logs proxy"
  fi
  seen="$(box_sum)"
  [ -n "$seen" ] && [ "$seen" = "$want" ]     || die "po odtworzeniu proxy wciąż nie widzi $LIVE – sprawdź CADDY_CONFIG_DIR w .env i docker compose config proxy"
  say "proxy odtworzone z $LIVE"
}

status() {
  echo "CADDY_CONFIG_DIR=$(env_value CADDY_CONFIG_DIR) w .env$(conf_dir_ok || echo ' – NIE ./caddy: proxy nie montuje katalogu z tym plikiem')"
  if [ ! -f "$LIVE" ]; then
    echo "$LIVE: brak (tworzy go wdrożenie albo bash scripts/proxy_config.sh render)"
    return 0
  fi
  local tmp seen
  tmp="$(mktemp)"
  if env -u EXTRA_DOMAINS -u PLATFORM_SUBDOMAINS -u DJCMS_ENABLED -u DJCMS_PRIMARY -u DJCMS_ROUTES_ENV -u NOTEBOOK_LAB_HOST \
      -u CADDYFILE_SRC CADDYFILE_OUT="$tmp" bash scripts/render_caddyfile.sh >/dev/null 2>&1 && cmp -s "$tmp" "$LIVE"; then
    echo "$LIVE: zgodny z deploy/Caddyfile i .env"
  else
    echo "$LIVE: NIEZGODNY z deploy/Caddyfile i .env – bash scripts/proxy_config.sh update"
  fi
  rm -f "$tmp"
  seen="$(box_sum)"
  if [ -z "$seen" ]; then
    echo "kontener proxy: nie działa"
  elif [ "$seen" = "$(host_sum)" ]; then
    echo "kontener proxy: widzi $LIVE (po zmianie pliku: bash scripts/proxy_config.sh apply)"
  else
    echo "kontener proxy: widzi INNĄ treść $BOX_FILE – bash scripts/proxy_config.sh apply (odtworzy kontener)"
  fi
}

case "$ACTION" in
  render) render ;;
  apply) apply ;;
  update) render; apply ;;
  status) status ;;
esac
