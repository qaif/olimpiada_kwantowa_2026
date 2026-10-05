#!/usr/bin/env bash
# Wycofanie wdrożenia do poprzednich OBRAZÓW aplikacji (docs/tasks/OPS-04.md § 2, docs/OPERACJE.md § 48).
#
# Uruchamiany NA SERWERZE, w katalogu instalacji (/opt/olimpiada):
#   bash scripts/rollback.sh status                  # migawka, ostatnie udane wdrożenie, ostatnie wycofanie
#   bash scripts/rollback.sh decide                  # czy wolno wycofać automatycznie (kod 0/3/4)
#   bash scripts/rollback.sh run [--yes] [--allow-migrations]   # wycofanie ręczne
# Woła je scripts/deploy.sh:
#   bash scripts/rollback.sh snapshot                # krok 2a/8: obrazy działających kontenerów + migracje
#   bash scripts/rollback.sh record-success [--git-commit SHA]  # po udanej kontroli dymnej (5b/8)
#   bash scripts/rollback.sh auto [--failed-version V]          # po NIEUDANEJ kontroli dymnej (5b/8)
#
# Czego ten skrypt NIGDY nie robi: nie dotyka bazy (wyłącznie SELECT z django_migrations), wolumenów,
# usług danych (db, redis, minio), proxy ani strony „Prace techniczne”. Wraca wyłącznie obraz
# kontenerów web, worker, beat (i djcms, gdy działał przed wdrożeniem): `docker compose up -d
# --no-deps --no-build …`. W .env zmienia tylko APP_VERSION, WEB_IMAGE i DJCMS_IMAGE (kopia całego
# pliku obok, w deploy-state/env.before-rollback). Entrypoint web poprzedniego obrazu robi `migrate`
# (no-op – jego migracje są zastosowane, inaczej decyzja nie przepuściłaby wycofania) i
# `collectstatic --clear` (pliki statyczne poprzedniej wersji – wolumen static_files to pochodna obrazu).
#
# Decyzja (`decide`):
#   0 auto       – migawka jest, obraz :previous jest, od migawki nie przybyła ŻADNA migracja
#                  (baza główna i baza djcms) – stary kod pasuje do schematu bazy;
#   3 manual     – przybyły migracje albo stan migracji jest nieznany – automat nie wraca, decyduje
#                  człowiek (`run --allow-migrations`, naprawa do przodu albo odtworzenie kopii);
#   4 impossible – brak migawki albo obrazu :previous (np. pierwsza instalacja).
# `auto` kończy się kodem 10 (wycofano, kontrola po wycofaniu przeszła), 12 (wycofano, kontrola
# nadal nie przechodzi) albo 11 (nie wycofano); zawsze z listem do ALERT_EMAILS.
#
# Migracje czytamy z tabeli django_migrations (psql w kontenerze db), a nie `showmigrations --plan`
# w kontenerze web: to te same wiersze, ale po nieudanym wdrożeniu nowy web zwykle nie działa –
# a decyzja jest potrzebna właśnie wtedy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${DEPLOY_STATE_DIR:-$ROOT/deploy-state}"
WEB_PREV_TAG="olimpiada/web:previous"
DJCMS_PREV_TAG="olimpiada/djcms:previous"
ROLLBACK_WAIT_SECONDS="${ROLLBACK_WAIT_SECONDS:-300}"

log() { printf '==> %s\n' "$*"; }
warn() { printf 'rollback: UWAGA: %s\n' "$*" >&2; }
die() { local rc="${2:-1}"; printf 'rollback: BŁĄD: %s\n' "$1" >&2; exit "$rc"; }

env_value() {  # env_value <NAZWA> – jak scripts/render_caddyfile.sh: ostatnie wystąpienie, bez cudzysłowów i CR
  sed -n "s/^$1=//p" "$ROOT/.env" 2>/dev/null | tail -n 1 | tr -d '\r\042\047'
}
state_value() {  # state_value <plik w deploy-state> <NAZWA> – odczyt bez `source` (plik to dane, nie kod)
  # Brak pliku = pusta wartość, nie błąd (`x="$(state_value …)"` pod `set -e` zakończyłoby skrypt).
  { sed -n "s/^$2=//p" "$STATE/$1" 2>/dev/null || true; } | tail -n 1
}
flag_on() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in 1|true|yes|on) return 0 ;; esac
  return 1
}
set_env() {  # set_env <NAZWA> <wartość> – zamiana linijki albo dopisanie (prawa pliku zostają)
  if grep -qE "^$1=" "$ROOT/.env"; then
    sed -i "s|^$1=.*|$1=$2|" "$ROOT/.env"
  else
    printf '%s=%s\n' "$1" "$2" >>"$ROOT/.env"
  fi
}
del_env() { sed -i "/^$1=/d" "$ROOT/.env"; }

compose() { (cd "$ROOT" && docker compose "$@"); }

# --- Obrazy i migracje -----------------------------------------------------------------------------

running_image_id() {  # running_image_id <usługa> – ID obrazu działającego kontenera (pusto, gdy nie działa)
  local cid
  cid="$(compose ps -q "$1" </dev/null 2>/dev/null | head -n 1 | tr -d '\r' || true)"
  [ -n "$cid" ] || return 0
  docker inspect --format '{{.Image}}' "$cid" </dev/null 2>/dev/null | tr -d '\r' || true
}
image_id() {  # image_id <ref> – ID obrazu (pusto, gdy go nie ma)
  docker image inspect --format '{{.Id}}' "$1" </dev/null 2>/dev/null | tr -d '\r' || true
}
pg_user() { env_value POSTGRES_USER; }
applied_migrations() {  # applied_migrations <baza> – `app.nazwa` zastosowanych migracji; kod ≠ 0 = nie wiadomo
  compose exec -T db psql -X -U "$(pg_user)" -d "$1" -Atc \
    "SELECT app || '.' || name FROM django_migrations ORDER BY 1" </dev/null | tr -d '\r' | sed '/^$/d'
}
djcms_db_state() {  # djcms_db_state – „present”, „absent” albo „unknown”
  local out
  out="$(compose exec -T db psql -X -U "$(pg_user)" -d "$(env_value POSTGRES_DB)" -Atc \
    "SELECT 1 FROM pg_database WHERE datname = 'olimpiada_djcms'" </dev/null 2>/dev/null | tr -d '\r')" || { echo unknown; return 0; }
  if [ "$out" = 1 ]; then echo present; else echo absent; fi
}
# djcms liczy się wyłącznie przy DJCMS_ENABLED=1: przy wyłączonym wycofanie go nie dotyka, a jego
# baza nie ma znaczenia dla starego kodu web – i żadne polecenie tego skryptu nie wspomina wtedy
# djcms (ta sama zasada co w deploy.sh: przy wyłączonym dj. przebieg jest jak przed DJ-01).
djcms_on() { flag_on "$(env_value DJCMS_ENABLED)"; }
# Migracje zastosowane TERAZ do pliku; kod 1 = odczyt nieudany (stan nieznany).
migrations_now() {  # migrations_now <plik główny> <plik djcms>
  applied_migrations "$(env_value POSTGRES_DB)" >"$1" 2>/dev/null || return 1
  if ! djcms_on; then : >"$2"; return 0; fi
  case "$(djcms_db_state)" in
    present) applied_migrations olimpiada_djcms >"$2" 2>/dev/null || return 1 ;;
    absent) : >"$2" ;;
    *) return 1 ;;
  esac
}
new_migrations() {  # new_migrations <przed> <teraz> – migracje zastosowane od migawki (posortowane)
  comm -13 <(LC_ALL=C sort -u "$1") <(LC_ALL=C sort -u "$2")
}

# --- snapshot --------------------------------------------------------------------------------------

cmd_snapshot() {
  mkdir -p "$STATE"
  chmod 700 "$STATE"
  # Najpierw stara migawka znika: nieudany odczyt niżej nie może zostawić migawki POPRZEDNIEGO
  # wdrożenia, z którą automat wycofałby do wersji sprzed dwóch wydań i porównał migracje ze złym stanem.
  rm -f "$STATE/previous.env" "$STATE/migrations-before.txt" "$STATE/djcms-migrations-before.txt" \
        "$STATE/last-smoke.txt" "$STATE/rollback-smoke.txt"
  local web_id dj_id="" known=1 prev_ver dj_state
  prev_ver="$(env_value APP_VERSION)"
  # Obraz DZIAŁAJĄCEGO kontenera – to on obsługuje dziś ruch; obraz z tagu wersji bywa już inny
  # (wdrożenie tej samej wersji przebudowuje tag). Kontener nie działa – obraz, który compose by wziął.
  web_id="$(running_image_id web)"
  if [ -z "$web_id" ]; then
    local ref; ref="$(env_value WEB_IMAGE)"; [ -n "$ref" ] || ref="olimpiada/web:${prev_ver:-dev}"
    web_id="$(image_id "$ref")"
  fi
  if [ -z "$web_id" ]; then
    warn "nie ma obrazu web sprzed wdrożenia (pierwsza instalacja?) – wycofanie automatyczne w tym wdrożeniu niemożliwe"
    return 0
  fi
  # Tag po ID: przebudowa obrazu tej samej wersji odbiera mu tag wersji, a `docker image prune`
  # kasuje obrazy bez tagu – :previous trzyma go przy życiu do następnego wdrożenia.
  docker tag "$web_id" "$WEB_PREV_TAG" </dev/null
  if djcms_on; then dj_id="$(running_image_id djcms)"; fi
  [ -n "$dj_id" ] && docker tag "$dj_id" "$DJCMS_PREV_TAG" </dev/null
  if applied_migrations "$(env_value POSTGRES_DB)" >"$STATE/migrations-before.txt.tmp" 2>/dev/null; then
    mv "$STATE/migrations-before.txt.tmp" "$STATE/migrations-before.txt"
  else
    rm -f "$STATE/migrations-before.txt.tmp"; known=0
  fi
  dj_state=absent
  if djcms_on; then dj_state="$(djcms_db_state)"; fi
  case "$dj_state" in
    present)
      if applied_migrations olimpiada_djcms >"$STATE/djcms-migrations-before.txt.tmp" 2>/dev/null; then
        mv "$STATE/djcms-migrations-before.txt.tmp" "$STATE/djcms-migrations-before.txt"
      else
        rm -f "$STATE/djcms-migrations-before.txt.tmp"; known=0
      fi ;;
    absent) : >"$STATE/djcms-migrations-before.txt" ;;
    *) known=0 ;;
  esac
  local prev_git=""
  [ "$(state_value deployed.env APP_VERSION)" = "$prev_ver" ] && prev_git="$(state_value deployed.env GIT_COMMIT)"
  # Czy obraz, do którego ewentualnie wrócimy, przeszedł kiedyś kontrolę dymną: deployed.env pisze
  # wyłącznie `record-success`. Inny obraz (wdrożenie z DEPLOY_SMOKE=0, ręczna przebudowa, `up`
  # z innym tagiem) to wciąż najlepszy kandydat – ale operator ma wiedzieć, że nie sprawdzony.
  local verified deployed_id
  deployed_id="$(state_value deployed.env WEB_IMAGE_ID)"
  if [ -z "$deployed_id" ]; then
    verified=unknown
  elif [ "$deployed_id" = "$web_id" ]; then
    verified=1
  else
    verified=0
    warn "obraz web sprzed wdrożenia ($web_id) to NIE ten, który ostatnio przeszedł kontrolę dymną ($deployed_id, deploy-state/deployed.env) – wycofanie wróci do niesprawdzonego obrazu"
  fi
  {
    echo "# Migawka przed wdrożeniem – scripts/rollback.sh snapshot (krok 2a/8 scripts/deploy.sh)."
    echo "SNAPSHOT_AT=$(date -Iseconds)"
    echo "PREV_APP_VERSION=$prev_ver"
    echo "PREV_GIT_COMMIT=$prev_git"
    echo "PREV_WEB_IMAGE=$(env_value WEB_IMAGE)"
    echo "PREV_DJCMS_IMAGE=$(env_value DJCMS_IMAGE)"
    echo "WEB_IMAGE_ID=$web_id"
    echo "DJCMS_IMAGE_ID=$dj_id"
    echo "MIGRATIONS_KNOWN=$known"
    echo "PREV_SMOKE_VERIFIED=$verified"
  } >"$STATE/previous.env.tmp"
  chmod 600 "$STATE/previous.env.tmp"
  mv "$STATE/previous.env.tmp" "$STATE/previous.env"
  echo "migawka: web ${prev_ver:-?} ($web_id) -> $WEB_PREV_TAG${dj_id:+, djcms ($dj_id) -> $DJCMS_PREV_TAG}"
  if [ "$known" = 1 ]; then
    echo "migawka: migracje zastosowane – $(wc -l <"$STATE/migrations-before.txt" | tr -d ' ') (baza główna), $(wc -l <"$STATE/djcms-migrations-before.txt" | tr -d ' ') (djcms)"
  else
    warn "stan migracji nieznany (baza nie odpowiada?) – po nieudanej kontroli dymnej wycofanie tylko ręczne"
  fi
}

# --- decide ----------------------------------------------------------------------------------------

DECISION_REASON=""
decide() {  # decide – kod 0 auto / 3 manual / 4 impossible; uzasadnienie w DECISION_REASON
  DECISION_REASON=""
  if [ ! -f "$STATE/previous.env" ]; then
    DECISION_REASON="brak migawki sprzed wdrożenia ($STATE/previous.env) – pierwsza instalacja albo krok 2a/8 nie zdążył"
    return 4
  fi
  local web_id tagged dj_id
  web_id="$(state_value previous.env WEB_IMAGE_ID)"
  tagged="$(image_id "$WEB_PREV_TAG")"
  if [ -z "$web_id" ] || [ "$tagged" != "$web_id" ]; then
    DECISION_REASON="obraz $WEB_PREV_TAG nie istnieje albo nie jest obrazem z migawki (${tagged:-brak} ≠ ${web_id:-brak})"
    return 4
  fi
  dj_id="$(state_value previous.env DJCMS_IMAGE_ID)"
  if [ -n "$dj_id" ] && [ "$(image_id "$DJCMS_PREV_TAG")" != "$dj_id" ]; then
    DECISION_REASON="obraz $DJCMS_PREV_TAG nie istnieje albo nie jest obrazem z migawki"
    return 4
  fi
  if [ "$(state_value previous.env MIGRATIONS_KNOWN)" != 1 ] || [ ! -f "$STATE/migrations-before.txt" ] \
     || [ ! -f "$STATE/djcms-migrations-before.txt" ]; then
    DECISION_REASON="stan migracji sprzed wdrożenia nieznany – nie da się wykluczyć, że schemat bazy jest nowszy niż stary kod"
    return 3
  fi
  local tmp; tmp="$(mktemp -d)"
  if ! migrations_now "$tmp/main" "$tmp/djcms"; then
    rm -rf "$tmp"
    DECISION_REASON="nie udało się odczytać migracji z bazy (db nie odpowiada?) – stan nieznany"
    return 3
  fi
  local added
  added="$( { new_migrations "$STATE/migrations-before.txt" "$tmp/main"
              new_migrations "$STATE/djcms-migrations-before.txt" "$tmp/djcms" | sed 's/^/djcms: /'; } )"
  rm -rf "$tmp"
  if [ -n "$added" ]; then
    DECISION_REASON="wdrożenie zastosowało migracje ($(printf '%s\n' "$added" | wc -l | tr -d ' ')): $(printf '%s\n' "$added" | head -n 20 | tr '\n' ' ')"
    return 3
  fi
  DECISION_REASON="bez nowych migracji od migawki – poprzednie obrazy pasują do schematu bazy"
  if [ "$(state_value previous.env PREV_SMOKE_VERIFIED)" = 0 ]; then
    DECISION_REASON="$DECISION_REASON (UWAGA: obraz :previous nie jest tym, który ostatnio przeszedł kontrolę dymną)"
  fi
  return 0
}

cmd_decide() {
  local rc=0
  decide || rc=$?
  case "$rc" in
    0) echo "auto: $DECISION_REASON" ;;
    3) echo "manual: $DECISION_REASON" ;;
    *) echo "impossible: $DECISION_REASON" ;;
  esac
  return "$rc"
}

# --- run -------------------------------------------------------------------------------------------

take_lock() {
  # Ta sama blokada co wdrożenie i przełączniki djcms (caddy/.lock): wycofanie w środku wdrożenia
  # ścigałoby się z jego `sed -i .env` i `up -d`. Wdrożenie trzyma ją samo (OLIMPIADA_PROXY_LOCK=held).
  [ "${OLIMPIADA_PROXY_LOCK:-}" = held ] && return 0
  command -v flock >/dev/null 2>&1 || return 0
  mkdir -p "$ROOT/caddy"
  exec 9>"$ROOT/caddy/.lock"
  flock -w 120 9 || die "od 2 min serwis zmienia inny proces (wdrożenie, djcms_switch.sh, djcms_cutover.sh) – ponów później"
}

do_rollback() {  # właściwe wycofanie (warunki sprawdzone wcześniej); kod 0 = usługi healthy
  local prev_ver prev_web prev_dj dj_id from_ver target services="web worker beat" wait_dj=0
  prev_ver="$(state_value previous.env PREV_APP_VERSION)"
  prev_web="$(state_value previous.env PREV_WEB_IMAGE)"
  prev_dj="$(state_value previous.env PREV_DJCMS_IMAGE)"
  dj_id="$(state_value previous.env DJCMS_IMAGE_ID)"
  from_ver="$(env_value APP_VERSION)"
  cp "$ROOT/.env" "$STATE/env.before-rollback"
  chmod 600 "$STATE/env.before-rollback"

  # Obraz dostaje z powrotem tag, pod którym compose go znajdzie przy KAŻDYM późniejszym poleceniu
  # (restart, scripts/backup.sh, ręczne `up -d`) – a nie tylko w tym jednym `up` niżej.
  if [ -n "$prev_web" ]; then target="$prev_web"
  elif [ -n "$prev_ver" ]; then target="olimpiada/web:$prev_ver"
  else target="$WEB_PREV_TAG"; fi
  [ "$target" = "$WEB_PREV_TAG" ] || docker tag "$WEB_PREV_TAG" "$target" </dev/null
  [ -n "$prev_ver" ] && set_env APP_VERSION "$prev_ver"
  if [ -n "$prev_web" ]; then set_env WEB_IMAGE "$prev_web"
  elif [ -z "$prev_ver" ]; then set_env WEB_IMAGE "$WEB_PREV_TAG"
  else del_env WEB_IMAGE; fi

  if [ -n "$dj_id" ] && flag_on "$(env_value DJCMS_ENABLED)"; then
    if [ -n "$prev_dj" ]; then target="$prev_dj"
    elif [ -n "$prev_ver" ]; then target="olimpiada/djcms:$prev_ver"
    else target="$DJCMS_PREV_TAG"; fi
    [ "$target" = "$DJCMS_PREV_TAG" ] || docker tag "$DJCMS_PREV_TAG" "$target" </dev/null
    if [ -n "$prev_dj" ]; then set_env DJCMS_IMAGE "$prev_dj"
    elif [ -z "$prev_ver" ]; then set_env DJCMS_IMAGE "$DJCMS_PREV_TAG"
    else del_env DJCMS_IMAGE; fi
    services="$services djcms"; wait_dj=1
  fi
  chmod 600 "$ROOT/.env"

  # Pozostałe usługi na obrazie aplikacji (docker-compose.yml: image WEB_IMAGE/olimpiada/web), w
  # profilach – wracają razem z web, ale tylko te, które DZIAŁAJĄ (profil włączony na serwerze):
  # inaczej notatniki albo `uptime` zostałyby na kodzie nieudanego wydania, a wycofanie włączałoby
  # usługi, których operator nie uruchomił.
  local pair profile svc profiles=()
  for pair in notebooks:notebook-worker notebooks:notebook-runner monitoring:uptime; do
    profile="${pair%%:*}"; svc="${pair#*:}"
    if [ -n "$(compose --profile "$profile" ps -q "$svc" </dev/null 2>/dev/null | head -n 1 | tr -d '\r' || true)" ]; then
      services="$services $svc"
      profiles+=(--profile "$profile")
    fi
  done

  log "Wycofanie: ${from_ver:-?} -> ${prev_ver:-poprzedni obraz} ($services)"
  # --no-deps: db, redis, minio, proxy zostają nietknięte; --no-build: wyłącznie obraz lokalny.
  # shellcheck disable=SC2086 # services: stała lista nazw usług
  compose "${profiles[@]+"${profiles[@]}"}" up -d --no-deps --no-build $services </dev/null

  local healthy=0 ps_out i
  for i in $(seq 1 $((ROLLBACK_WAIT_SECONDS / 5 + 1))); do
    ps_out="$(compose ps --format '{{.Service}}={{.Health}}' </dev/null 2>/dev/null || true)"
    if grep -qx 'web=healthy' <<<"$ps_out" && { [ "$wait_dj" = 0 ] || grep -qx 'djcms=healthy' <<<"$ps_out"; }; then
      healthy=1; break
    fi
    [ "$i" -gt $((ROLLBACK_WAIT_SECONDS / 5)) ] || sleep 5
  done
  {
    echo "# Ostatnie wycofanie – scripts/rollback.sh."
    echo "ROLLED_BACK_AT=$(date -Iseconds)"
    echo "FROM_APP_VERSION=$from_ver"
    echo "TO_APP_VERSION=$prev_ver"
    echo "SERVICES=$services"
    echo "HEALTHY=$healthy"
  } >"$STATE/last-rollback.env"
  chmod 600 "$STATE/last-rollback.env"
  if [ "$healthy" = 1 ]; then
    # Bufor całych stron gościa: wersja z wycofania ma własną przestrzeń kluczy (APP_VERSION), ale
    # wpisy z niej mogą jeszcze pamiętać chwilę sprzed wdrożenia – sprzątanie, nie warunek.
    compose exec -T web python manage.py page_cache_clear </dev/null >/dev/null 2>&1 || true
    echo "wycofanie: usługi healthy na wersji ${prev_ver:-poprzedniej}"
    return 0
  fi
  warn "po ${ROLLBACK_WAIT_SECONDS} s $([ "$wait_dj" = 1 ] && echo 'web/djcms' || echo web) nie jest healthy (docker compose logs web)"
  return 1
}

manual_help() {
  local domain; domain="$(env_value SITE_DOMAIN)"
  cat <<EOF
Procedura ręczna (docs/OPERACJE.md § 48.5) – na serwerze, cd $ROOT:
  1. Co nie działa:      cat $STATE/last-smoke.txt; docker compose logs --tail 200 web
  2. Nowe migracje:      bash scripts/rollback.sh decide
  3a. Migracje wstecznie zgodne (stary kod je toleruje – np. same nowe tabele/kolumny z domyślną wartością):
                         bash scripts/rollback.sh run --allow-migrations
  3b. Inaczej – naprawa do przodu (poprawka i kolejne wdrożenie) ALBO odtworzenie bazy z kopii przed
      migracjami (\$BACKUP_DIR/pre-deploy-*.dump, OPERACJE § 2) przy włączonej stronie prac technicznych
      (bash scripts/maintenance.sh on), potem bash scripts/rollback.sh run --allow-migrations.
  Kontrola:              bash scripts/smoke.sh --server $ROOT
  Wycofanie nie włącza ani nie wyłącza strony prac technicznych (https://${domain:-<domena>}/):
                         bash scripts/maintenance.sh status
EOF
}

cmd_run() {
  local yes=0 allow=0 rc=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes|-y) yes=1 ;;
      --allow-migrations) allow=1 ;;
      *) die "nieznana opcja $1 (run [--yes] [--allow-migrations])" 2 ;;
    esac
    shift
  done
  take_lock
  decide || rc=$?
  case "$rc" in
    0) echo "decyzja: auto – $DECISION_REASON" ;;
    3) if [ "$allow" = 1 ]; then
         warn "$DECISION_REASON"
         warn "--allow-migrations: stary kod ruszy na NOWSZYM schemacie bazy – tylko gdy migracje są wstecznie zgodne"
       else
         echo "decyzja: NIE – $DECISION_REASON"
         manual_help
         exit 3
       fi ;;
    *) die "$DECISION_REASON" 4 ;;
  esac
  if [ "$yes" != 1 ]; then
    [ -t 0 ] || die "bez terminala potwierdź flagą --yes" 2
    local answer
    read -r -p "Wycofać do $(state_value previous.env PREV_APP_VERSION) (web, worker, beat)? Wpisz TAK: " answer
    [ "$answer" = TAK ] || die "przerwane – nic nie zmieniono" 2
  fi
  do_rollback
}

# --- auto (wdrożenie po nieudanej kontroli dymnej) -------------------------------------------------

send_alert() {  # send_alert <temat> <treść> – list do ALERT_EMAILS przez Django (bez sekretów w treści)
  local code
  # Kod przez -c, treść przez środowisko (`-e NAZWA` bez wartości – compose bierze ją z naszego
  # środowiska, więc nie ma jej w argumentach procesu). ALERT_EMAILS czyta Django z .env (env_file).
  code='import os
from django.conf import settings
from django.core.mail import send_mail
to = [a.strip() for a in (getattr(settings, "ALERT_EMAILS", None) or []) if a.strip()]
if not to:
    print("ALERT_EMAILS puste – list nie wysłany")
    raise SystemExit(3)
send_mail(os.environ["DEPLOY_ALERT_SUBJECT"], os.environ["DEPLOY_ALERT_BODY"], None, to)
print(f"list alarmowy wysłany ({len(to)} adres(y))")'
  export DEPLOY_ALERT_SUBJECT="$1" DEPLOY_ALERT_BODY="$2"
  local rc=0
  compose exec -T -e DEPLOY_ALERT_SUBJECT -e DEPLOY_ALERT_BODY web python manage.py shell -c "$code" </dev/null || rc=$?
  [ "$rc" = 0 ] && return 0
  [ "$rc" = 3 ] && { warn "ALERT_EMAILS puste w .env – nikt nie dostał listu"; return 0; }
  # web nie działa (najczęstszy przypadek tutaj) – jednorazowy kontener tego samego obrazu, bez
  # migracji i bez collectstatic (nic nie pisze do bazy ani do wolumenu plików statycznych).
  rc=0
  compose run --rm --no-deps -T -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 \
    -e DEPLOY_ALERT_SUBJECT -e DEPLOY_ALERT_BODY web python manage.py shell -c "$code" </dev/null || rc=$?
  [ "$rc" = 0 ] || warn "listu alarmowego nie udało się wysłać (kod $rc) – zadzwoń do dyżurnego"
  return 0
}

banner() {
  printf '\n!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
  printf '!!! %s\n' "$@"
  printf '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n\n'
}

cmd_auto() {
  local failed="" rc=0 domain prev smoke_report body reason="" maint
  while [ $# -gt 0 ]; do
    case "$1" in
      --failed-version) failed="${2-}"; shift 2 ;;
      # Dodatkowy powód porażki od wdrożenia (np. nieudane `docker compose up -d` w kroku 4b/8).
      --reason) reason="${2-}"; shift 2 ;;
      *) die "nieznana opcja $1" 2 ;;
    esac
  done
  [ -n "$failed" ] || failed="$(env_value APP_VERSION)"
  domain="$(env_value SITE_DOMAIN)"
  prev="$(state_value previous.env PREV_APP_VERSION)"
  smoke_report="$(cat "$STATE/last-smoke.txt" 2>/dev/null | tail -n 60 || true)"
  if [ -n "$reason" ]; then
    echo "rollback: powód porażki wdrożenia: $reason"
    smoke_report="$reason
$smoke_report"
  fi
  # Wycofanie strony prac technicznych nie rusza; wdrożenie z --maintenance, które padło przed 5a,
  # zostawia ją włączoną – list ma mówić prawdę o tym, co widzą uczestnicy.
  if [ -f "$ROOT/maintenance/on" ]; then
    maint="Strona „Prace techniczne” jest WŁĄCZONA (wdrożenie z --maintenance) – wyłącz ją po sprawdzeniu: bash scripts/maintenance.sh off"
  else
    maint="Strona „Prace techniczne” nie została włączona – serwis odpowiada tym, co działa."
  fi

  decide || rc=$?

  if [ "$rc" = 0 ]; then
    banner "Kontrola dymna wersji $failed NIE przeszła – wycofuję do ${prev:-poprzedniego obrazu} (bez migracji, baza nietknięta)"
    local rb_ok=0 smoke_ok=0
    do_rollback && rb_ok=1
    # Kontrola po wycofaniu – czy serwis znów odpowiada (oczekiwana poprzednia wersja).
    bash "$ROOT/scripts/smoke.sh" --server "$ROOT" ${prev:+--expect-version "$prev"} \
      --report "$STATE/rollback-smoke.txt" </dev/null && smoke_ok=1
    body="Host: ${domain:-?}
Wdrożona wersja: $failed – kontrola dymna NIE przeszła.
Wycofano obrazy web/worker/beat do: ${prev:-poprzedniego obrazu} (baza i wolumeny nietknięte; $DECISION_REASON).
Usługi po wycofaniu healthy: $([ "$rb_ok" = 1 ] && echo tak || echo NIE). Kontrola po wycofaniu: $([ "$smoke_ok" = 1 ] && echo przeszła || echo NIE PRZESZŁA).
Kod i konfiguracja proxy w $ROOT zostały w wersji $failed – pełny powrót: wdrożenie poprzedniego tagu.
$maint

Kontrola dymna wersji $failed:
$smoke_report"
    if [ "$smoke_ok" = 1 ] && [ "$rb_ok" = 1 ]; then
      send_alert "[Olimpiada] ${domain:-?}: wdrożenie $failed WYCOFANE do ${prev:-poprzedniej wersji}" "$body"
      banner "Wycofano do ${prev:-poprzedniego obrazu}; kontrola po wycofaniu przeszła. Wdrożenie $failed – NIEUDANE." \
             "Szczegóły: $STATE/last-smoke.txt, docker compose logs web. Stan: bash scripts/rollback.sh status"
      return 10
    fi
    send_alert "[Olimpiada] ${domain:-?}: wdrożenie $failed wycofane, ale serwis NADAL nie działa" "$body"
    banner "Wycofano do ${prev:-poprzedniego obrazu}, ale serwis NADAL nie przechodzi kontroli – incydent (OPERACJE § 7)." \
           "Kontrola po wycofaniu: $STATE/rollback-smoke.txt"
    return 12
  fi

  body="Host: ${domain:-?}
Wdrożona wersja: $failed – kontrola dymna NIE przeszła.
NIE wycofano automatycznie: $DECISION_REASON
$maint

$(manual_help)

Kontrola dymna wersji $failed:
$smoke_report"
  send_alert "[Olimpiada] ${domain:-?}: wdrożenie $failed NIE PRZESZŁO kontroli – potrzebna decyzja" "$body"
  banner "Kontrola dymna wersji $failed NIE przeszła i wycofanie automatyczne NIE jest bezpieczne:" \
         "$DECISION_REASON" \
         "$maint" "Decyzja należy do dyżurnego:"
  manual_help
  return 11
}

# --- record-success / status -----------------------------------------------------------------------

cmd_record_success() {
  local git=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --git-commit) git="${2-}"; shift 2 ;;
      *) die "nieznana opcja $1" 2 ;;
    esac
  done
  mkdir -p "$STATE"
  chmod 700 "$STATE"
  {
    echo "# Ostatnie udane wdrożenie (kontrola dymna przeszła) – scripts/rollback.sh record-success."
    echo "DEPLOYED_AT=$(date -Iseconds)"
    echo "APP_VERSION=$(env_value APP_VERSION)"
    echo "GIT_COMMIT=$git"
    echo "WEB_IMAGE_ID=$(running_image_id web)"
    echo "DJCMS_IMAGE_ID=$(if djcms_on; then running_image_id djcms; fi)"
  } >"$STATE/deployed.env"
  chmod 600 "$STATE/deployed.env"
  echo "zapisano: wersja $(env_value APP_VERSION)${git:+ ($git)} – $STATE/deployed.env"
}

cmd_status() {
  local f
  for f in deployed.env previous.env last-rollback.env; do
    echo "--- $STATE/$f"
    if [ -f "$STATE/$f" ]; then grep -v '^#' "$STATE/$f"; else echo "(brak)"; fi
  done
  echo "--- obrazy :previous"
  echo "$WEB_PREV_TAG: $(image_id "$WEB_PREV_TAG" | cut -c1-19)${DJCMS_PREV_TAG:+, $DJCMS_PREV_TAG: $(image_id "$DJCMS_PREV_TAG" | cut -c1-19)}"
  echo "--- decyzja"
  cmd_decide || true
}

main() {
  local cmd="${1:-}"
  [ $# -gt 0 ] && shift
  if [ ! -f "$ROOT/.env" ]; then
    # Pierwsze wdrożenie: krok 2a/8 biegnie przed utworzeniem .env (krok 3/8) – nie ma czego zapamiętać.
    [ "$cmd" = snapshot ] && { echo "migawka: brak .env (pierwsze wdrożenie) – nie ma wersji, do której można by wrócić"; exit 0; }
    die "brak .env w $ROOT – uruchom w katalogu instalacji (/opt/olimpiada)" 2
  fi
  case "$cmd" in
    snapshot) cmd_snapshot "$@" ;;
    record-success) cmd_record_success "$@" ;;
    decide) cmd_decide "$@" ;;
    run) cmd_run "$@" ;;
    auto) cmd_auto "$@" ;;
    status) cmd_status "$@" ;;
    *) echo "użycie: bash scripts/rollback.sh status|decide|run [--yes] [--allow-migrations]|snapshot|record-success|auto" >&2; exit 2 ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
