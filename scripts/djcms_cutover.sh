#!/usr/bin/env bash
# Przełączenie serwisu publicznego z Wagtaila na django CMS jednym poleceniem – docs/tasks/DJ-02.md
# § 10.1 (decyzje D8, D9), runbook: docs/OPERACJE.md § 22.9–22.10.
#
# Uruchamiany NA SERWERZE, w katalogu instalacji (/opt/olimpiada). NA PRODUKCJI WYŁĄCZNIE PO ZGODZIE
# ORGANIZATORA (DJ-02 § 12 p. 7).
#
#   bash scripts/djcms_cutover.sh --check                  # same kontrole – nic nie zmienia
#   bash scripts/djcms_cutover.sh --dry-run [--skip SLUG]  # kontrole + plan z dokładnymi poleceniami
#   bash scripts/djcms_cutover.sh [--skip SLUG…] [--yes]   # pełny przebieg (pyta o potwierdzenie)
#   bash scripts/djcms_cutover.sh --rollback [--unfreeze]  # powrót do Wagtaila (i odmrożenie edycji)
#
# Opcje:
#   --skip SLUG        (powtarzalne) konkurs, którego redakcja pracuje już w djcms – jego treść djcms
#                      NIE jest zastępowana importem z Wagtaila (D8); weryfikacja tego konkursu jest
#                      wtedy tylko ostrzeżeniem (liczba stron z definicji różni się od Wagtaila)
#   --yes              bez pytania o potwierdzenie (automatyzacja); bez terminala i bez --yes – odmowa
#   --force-reimport   ponowne przełączenie PO wycofaniu (w .env jest DJCMS_CUTOVER_DONE): import
#                      z Wagtaila kasuje zmiany zrobione w djcms od pierwszego przełączenia
#   --no-backup-verify przy --check/--dry-run: bez testu odtwarzania ostatniej kopii (szybka próba)
#   --unfreeze         przy --rollback: także `cms_freeze off` (edycja stron w /cms/ znów otwarta)
#
# Pełny przebieg (każdy krok loguje; błąd = kod ≠ 0, komunikat o stanie serwisu i BRAK przełączenia):
#   0. kontrole (te same co --check, bez testu odtwarzania – ten idzie po świeżej kopii w kroku 1):
#      DJCMS_ENABLED=1, proxy montuje deploy/Caddyfile.generated i widzi jego bieżącą treść, web
#      i djcms healthy, `cms_freeze status` odpowiada, `djcms_routes --check` w web i kontrakt tras na
#      hoście = kontrakt z obrazu web, brak aliasów językowych aktywnych konkursów (D12), API v2
#      i rejestr (`sync_competitions --dry-run`), `import_cms_bundle --all --replace --dry-run`,
#      wolne miejsce na dysku;
#   1. kopia: scripts/backup.sh (baza główna + baza i pliki djcms) i scripts/backup_verify.sh na niej;
#   2. `cms_freeze on --wait` w web – od tej chwili strony w /cms/ tylko do odczytu (wyjątki D9:
#      warsztaty, partnerzy); `--wait` czeka, aż zamrożenie zobaczą wszystkie workery web (pamięć
#      stanu 10 s) – zapis przyjęty tuż przed nim minąłby końcowy import; odwiedzający nic nie
#      widzą – strony publiczne dalej podaje Wagtail;
#   3. `sync_competitions` w djcms – witryny wszystkich aktywnych konkursów;
#   4. `import_cms_bundle --from-api --all --replace [--skip …]` – końcowy import (treść z chwili
#      zamrożenia), każdy konkurs we własnej transakcji;
#   5. `verify_cutover` – każda witryna: liczba stron, adresy 200, strona główna, S5, S16, sitemap,
#      robots, przekierowania;
#   6. `bash scripts/djcms_switch.sh on` – przełącznik (validate, reload, kontrola dymna; przy porażce
#      sam wraca do DJCMS_PRIMARY=0);
#   7. znacznik DJCMS_CUTOVER_DONE w .env i podsumowanie (strony i przekierowania per konkurs, czas).
#
# Przerwanie – co zostaje (skrypt wypisuje to sam przy każdym błędzie):
#   krok 0–1: nic nie zmienione (serwis na Wagtailu, edycja otwarta);
#   krok 2–5: Wagtail ZAMROŻONY, serwis publiczny dalej na Wagtailu, djcms częściowo/całkowicie
#             zaimportowany (niepubliczny). Ponowienie jest bezpieczne (import --replace daje ten sam
#             stan); rezygnacja: --rollback --unfreeze. Edycji nie odmrażamy sami (§ 10.1 p. 5).
#   krok 6:   przełącznik wrócił do Wagtaila sam (DJCMS_PRIMARY=0) – jak wyżej.
#
# Powtórne uruchomienie po udanym przełączeniu (DJCMS_PRIMARY=1) niczego nie robi (kod 0) – ponowny
# import skasowałby redakcję djcms. Powrót na djcms po wycofaniu BEZ utraty zmian: djcms_switch.sh on.
#
# Dziennik: całe wyjście idzie też do DJCMS_CUTOVER_LOG_DIR (domyślnie /var/log)
# /olimpiada-djcms-cutover-<data>.log. Zmienne pomocnicze: BACKUP_DIR (jak scripts/backup.sh),
# DJCMS_CUTOVER_MIN_FREE_MB (próg wolnego miejsca, domyślnie 2048).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'; }
bad_usage() { printf 'djcms_cutover: %s (bash scripts/djcms_cutover.sh --help)\n' "$*" >&2; exit 2; }

# --- Argumenty ------------------------------------------------------------------------------------
# Parsowane przed czymkolwiek innym (także przed założeniem dziennika) – zły argument to kod 2 bez
# śladu na serwerze. Proces potomny (niżej: `tee` do dziennika) parsuje je jeszcze raz, tak samo.
MODE=run
YES=0
FORCE_REIMPORT=0
BACKUP_VERIFY=1
UNFREEZE=0
SKIPS=()
ORIG_ARGS=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --dry-run) MODE=dry-run ;;
    --rollback) MODE=rollback ;;
    --yes) YES=1 ;;
    --force-reimport) FORCE_REIMPORT=1 ;;
    --no-backup-verify) BACKUP_VERIFY=0 ;;
    --unfreeze) UNFREEZE=1 ;;
    --skip) [ $# -ge 2 ] || bad_usage "--skip wymaga sluga konkursu"; SKIPS+=("$2"); shift ;;
    --skip=*) SKIPS+=("${1#--skip=}") ;;
    -h|--help) usage; exit 0 ;;
    *) bad_usage "nieznany argument: $1" ;;
  esac
  shift
done
# Slug jak w API v2 (`^[a-z0-9-]{1,50}$`): trafia do argumentów komend w kontenerach i do wzorców
# awk niżej – nic spoza tego kształtu.
for s in "${SKIPS[@]+"${SKIPS[@]}"}"; do
  printf '%s' "$s" | grep -Eq '^[a-z0-9-]{1,50}$' || bad_usage "--skip „$s” – slug konkursu to [a-z0-9-], do 50 znaków"
done
[ "$UNFREEZE" = 0 ] || [ "$MODE" = rollback ] || bad_usage "--unfreeze działa wyłącznie z --rollback"
[ "${#SKIPS[@]}" -eq 0 ] || [ "$MODE" != rollback ] || bad_usage "--skip nie dotyczy --rollback"
[ "$BACKUP_VERIFY" = 1 ] || [ "$MODE" = check ] || [ "$MODE" = dry-run ] \
  || bad_usage "--no-backup-verify wyłącznie przy --check/--dry-run (pełny przebieg zawsze sprawdza świeżą kopię)"

# --- Dziennik -------------------------------------------------------------------------------------
# Skrypt uruchamia sam siebie jeszcze raz z wyjściem przez `tee` – synchroniczny potok zamiast
# `exec > >(tee …)`: kod wyjścia przechodzi wprost (PIPESTATUS), a dziennik jest kompletny w chwili
# zakończenia (proces w podstawieniu mógłby dopisywać jeszcze po powrocie do powłoki). Wejście
# zostaje terminalem operatora, więc pytanie o potwierdzenie działa.
STAMP="$(date +%Y%m%d-%H%M%S)"
if [ -z "${DJCMS_CUTOVER_CHILD:-}" ]; then
  LOG_DIR="${DJCMS_CUTOVER_LOG_DIR:-/var/log}"
  LOG_FILE="$LOG_DIR/olimpiada-djcms-cutover-$STAMP.log"
  if ! { mkdir -p "$LOG_DIR" && ( umask 077 && : >>"$LOG_FILE" ); } 2>/dev/null; then
    LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/olimpiada-djcms-cutover-$STAMP.XXXXXX")"
    printf 'UWAGA: nie mogę pisać do %s – dziennik: %s\n' "$LOG_DIR" "$LOG_FILE" >&2
  fi
  set +e
  DJCMS_CUTOVER_CHILD=1 DJCMS_CUTOVER_LOG="$LOG_FILE" \
    bash "${BASH_SOURCE[0]}" "${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}" 2>&1 | tee -a "$LOG_FILE"
  exit "${PIPESTATUS[0]}"
fi
LOG_FILE="${DJCMS_CUTOVER_LOG:-(brak)}"

# --- Narzędzia ------------------------------------------------------------------------------------
T0="$(date +%s)"
BACKUP_DIR="${BACKUP_DIR:-/opt/olimpiada-backups}"
MIN_FREE_MB="${DJCMS_CUTOVER_MIN_FREE_MB:-2048}"
GENERATED="deploy/Caddyfile.generated"
CONTRACT_ENV="backend/djcms_contract/app_routes.env"
# Komunikat banera w /cms/ (DJ-02 § 10.1 p. 3); `--by` zapisuje w bazie, kto zamroził.
FREEZE_MESSAGE="Edycja treści przeniesiona do django CMS"
FREEZE_BY="scripts/djcms_cutover.sh ($(id -un 2>/dev/null || echo operator))"

log() { printf '\n==> %s\n' "$*"; }
say() { printf '    %s\n' "$*"; }
indent() { sed 's/^/        /'; }
die() { printf '\ndjcms_cutover: BŁĄD: %s\n' "$*" >&2; exit 1; }

[ -f .env ] || die "brak .env w $ROOT – uruchom w katalogu instalacji (/opt/olimpiada)"

env_value() {  # env_value <NAZWA> – jak scripts/djcms_switch.sh: ostatnie wystąpienie, bez cudzysłowów i CR
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r\042\047'
}
flag_on() {  # flag_on <wartość> – 0 dla 1/true/yes/on
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

# Każde polecenie w kontenerze: -T i </dev/null (bez tego `exec` czytałby wejście skryptu – przy
# pytaniu o potwierdzenie byłby to terminal operatora).
dc() { docker compose exec -T "$@" </dev/null; }

# Argumenty --skip dla import_cms_bundle (tablica, żeby dało się ją też wypisać w planie).
SKIP_ARGS=()
for s in "${SKIPS[@]+"${SKIPS[@]}"}"; do SKIP_ARGS+=(--skip "$s"); done
IMPORT_CMD=(djcms python manage.py import_cms_bundle --from-api --all --replace "${SKIP_ARGS[@]+"${SKIP_ARGS[@]}"}")
FREEZE_CMD=(web python manage.py cms_freeze on --message "$FREEZE_MESSAGE" --by "$FREEZE_BY" --wait)
SKIP_LIST="${SKIPS[*]+"${SKIPS[*]}"}"

freeze_state() {  # kod: 0 zamrożone, 1 otwarte, 2 nie wiadomo (web nie odpowiada); opis na stdout
  local out rc=0
  # `cms_freeze status` kończy się kodem 1, gdy edycja jest OTWARTA – to nie błąd (set -e).
  out="$(dc web python manage.py cms_freeze status 2>&1)" || rc=$?
  printf '%s\n' "$out" | tr -d '\r' | sed '/^$/d'
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) return 2 ;; esac
}

# Czy `proxy` widzi TĘ treść deploy/Caddyfile.generated (montaż pojedynczego pliku – po wdrożeniu bez
# odtworzenia proxy kontener czyta stary i-węzeł i `djcms_switch.sh on` odmówi dopiero w kroku 6, już
# po zamrożeniu i imporcie). Ta sama kontrola co w djcms_switch.sh.
proxy_sees_generated() {
  local host_sum box_sum
  [ -f "$GENERATED" ] || return 1
  host_sum="$(sha256sum "$GENERATED" | cut -d' ' -f1)"
  box_sum="$(dc proxy sha256sum /etc/caddy/Caddyfile 2>/dev/null | tr -d '\r' | cut -d' ' -f1 || true)"
  [ -n "$box_sum" ] && [ "$host_sum" = "$box_sum" ]
}

set_env_line() {  # set_env_line <NAZWA> <wartość> <komentarz> – jedna linijka w .env (dopisana, gdy jej nie ma)
  if grep -qE "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    { echo; echo "# $3"; echo "$1=$2"; } >>.env
  fi
  chmod 600 .env
}

ENABLED=0; flag_on "$(env_value DJCMS_ENABLED)" && ENABLED=1
PRIMARY_NOW=0; flag_on "$(env_value DJCMS_PRIMARY)" && PRIMARY_NOW=1
CUTOVER_DONE="$(env_value DJCMS_CUTOVER_DONE)"

printf 'djcms_cutover (%s) – %s, katalog %s, dziennik %s\n' "$MODE" "$(date -Iseconds)" "$ROOT" "$LOG_FILE"
[ -z "$SKIP_LIST" ] || printf 'Pominięte w imporcie (--skip): %s\n' "$SKIP_LIST"

# Jeden przebieg naraz (dwa równoległe zamrażałyby i importowały na przemian). Tylko tryby, które
# coś zmieniają; bez `flock` (Git Bash) – bez blokady.
take_lock() {
  if command -v flock >/dev/null 2>&1; then
    mkdir -p deploy
    exec 9>"deploy/.djcms_cutover.lock"
    flock -n 9 || die "inny djcms_cutover.sh właśnie działa"
  fi
}

# ================================================================================================
# Wycofanie: `--rollback [--unfreeze]` (DJ-02 § 10.4)
# ================================================================================================
if [ "$MODE" = rollback ]; then
  take_lock
  log "Wycofanie: strony publiczne z powrotem z Wagtaila"
  if [ "$ENABLED" != 1 ]; then
    say "DJCMS_ENABLED nie jest 1 – trasy djcms nie istnieją, strony publiczne i tak podaje Wagtail."
  elif [ "$PRIMARY_NOW" = 1 ] || grep -q 'header_up X-Djcms-Mode primary' "$GENERATED" 2>/dev/null; then
    # Przełącznik jest drogą ratunkową: nie wymaga zdrowego djcms, proxy ze starą treścią odtwarza sam.
    bash scripts/djcms_switch.sh off </dev/null || die "djcms_switch.sh off nie powiódł się – polecenia ręczne wypisał wyżej; stan: bash scripts/djcms_switch.sh status"
  else
    say "DJCMS_PRIMARY=0 i konfiguracja proxy w trybie preview – strony publiczne już podaje Wagtail."
  fi
  if [ "$UNFREEZE" = 1 ]; then
    log "Odmrożenie edycji stron w Wagtailu (cms_freeze off)"
    dc web python manage.py cms_freeze off --by "$FREEZE_BY" || die "cms_freeze off nie powiódł się (web działa? docker compose ps web)"
  else
    rc=0; desc="$(freeze_state)" || rc=$?
    case "$rc" in
      0) say "Wagtail: edycja stron nadal ZAMROŻONA – $desc"
         say "Odmrożenie (po decyzji): bash scripts/djcms_cutover.sh --rollback --unfreeze"
         say "  (= docker compose exec -T web python manage.py cms_freeze off)" ;;
      1) say "Wagtail: edycja stron otwarta." ;;
      *) say "Wagtail: stan zamrożenia nieznany (web nie odpowiada)." ;;
    esac
  fi
  log "Wycofano w $(( $(date +%s) - T0 )) s."
  say "Wagtail pokazuje treść z chwili zamrożenia – zmiany zrobione w djcms do Wagtaila NIE wracają (D8)."
  say "Powrót na djcms bez utraty tych zmian: bash scripts/djcms_switch.sh on (bez ponownego importu)."
  exit 0
fi

# ================================================================================================
# Stan wyjściowy: już przełączone? przełączone i wycofane?
# ================================================================================================
if [ "$PRIMARY_NOW" = 1 ]; then
  # Idempotencja: drugi przebieg po udanym przełączeniu to no-op, a nie ponowny import – od chwili
  # przełączenia źródłem prawdy jest djcms (D8) i `--replace` skasowałby pracę redakcji.
  log "Serwis publiczny jest już na django CMS (DJCMS_PRIMARY=1${CUTOVER_DONE:+, przełączenie $CUTOVER_DONE}) – nic do zrobienia."
  say "Stan: bash scripts/djcms_switch.sh status. Wycofanie: bash scripts/djcms_cutover.sh --rollback."
  exit 0
fi
if [ -n "$CUTOVER_DONE" ]; then
  say ""
  say "UWAGA: przełączenie było już wykonane ($CUTOVER_DONE) i wycofane. djcms mógł dostać od tamtej"
  say "       chwili zmiany redakcji – ponowny import z Wagtaila (--replace) je SKASUJE."
  say "       Powrót na djcms BEZ utraty zmian: bash scripts/djcms_switch.sh on"
  say "       Świadomy ponowny import: --force-reimport (konkursy do zachowania: --skip SLUG)."
  if [ "$MODE" = run ] && [ "$FORCE_REIMPORT" != 1 ]; then
    die "odmawiam ponownego importu po wycofaniu bez --force-reimport (patrz wyżej)"
  fi
fi

# ================================================================================================
# Kontrole (--check, --dry-run, krok 0 pełnego przebiegu) – wyłącznie odczyty
# ================================================================================================
CHECK_FAIL=0
ok() { printf '   ok   %s\n' "$*"; }
bad() { printf '   FAIL %s\n' "$*"; CHECK_FAIL=1; }

run_checks() {  # run_checks <1|0: test odtwarzania ostatniej kopii>
  local out rc desc ps_out avail dir seen_dirs=" "
  log "Kontrole przed przełączeniem (nic nie zmieniają)"

  # Konfiguracja z .env – bez niej żadna dalsza kontrola nie ma sensu.
  if [ "$ENABLED" = 1 ]; then ok "DJCMS_ENABLED=1"; else bad "DJCMS_ENABLED w .env nie jest 1 – bez djcms nie ma na co przełączać (docs/OPERACJE.md § 22.2)"; return; fi
  case "$(env_value CADDYFILE_PATH)" in
    ./deploy/Caddyfile.generated|deploy/Caddyfile.generated) ok "proxy montuje $GENERATED (CADDYFILE_PATH)" ;;
    *) bad "CADDYFILE_PATH=$(env_value CADDYFILE_PATH) – proxy nie montuje $GENERATED, przełącznik nie miałby skutku" ;;
  esac
  for f in scripts/djcms_switch.sh scripts/backup.sh scripts/backup_verify.sh "$CONTRACT_ENV"; do
    [ -f "$f" ] || bad "brak pliku $f w katalogu instalacji"
  done

  # Usługi.
  ps_out="$(docker compose ps --format '{{.Service}}={{.Health}}' </dev/null 2>/dev/null | tr -d '\r' || true)"
  for svc in web djcms; do
    if printf '%s\n' "$ps_out" | grep -qx "$svc=healthy"; then ok "$svc healthy"; else bad "$svc nie jest healthy (docker compose ps $svc; docker compose logs $svc)"; fi
  done
  if proxy_sees_generated; then
    ok "proxy widzi bieżącą treść $GENERATED"
  else
    bad "proxy nie działa albo widzi inną treść /etc/caddy/Caddyfile niż $GENERATED – docker compose up -d --force-recreate --no-deps proxy (kilka sekund przerwy) i ponów"
  fi

  # Zamrożenie – wyłącznie, czy komenda odpowiada (0/1); stan wyjściowy tylko informacyjnie.
  rc=0; desc="$(freeze_state)" || rc=$?
  case "$rc" in
    0) ok "cms_freeze status: edycja stron już ZAMROŻONA (ponowny przebieg po przerwaniu?) – $desc" ;;
    1) ok "cms_freeze status: edycja stron otwarta (zamrożenie w kroku 2)" ;;
    *) bad "cms_freeze status nie odpowiada (web, migracja cms.EditingFreeze?): $desc" ;;
  esac

  # Kontrakt tras: plik w obrazie web odpowiada urlconfowi, a plik na hoście (z niego generator robi
  # Caddyfile) – plikowi z obrazu. Rozjazd = adres aplikacji, który po przełączeniu trafiłby do djcms
  # (np. obraz z rejestru, WEB_IMAGE, w innej wersji niż kod w /opt/olimpiada).
  rc=0; out="$(dc web python manage.py djcms_routes --check 2>&1)" || rc=$?
  if [ "$rc" = 0 ]; then ok "djcms_routes --check w web"; else bad "djcms_routes --check w web (kod $rc)"; printf '%s\n' "$out" | tr -d '\r' | tail -n 20 | indent; fi
  rc=0; out="$(dc web python manage.py djcms_routes --format env 2>/dev/null | tr -d '\r')" || rc=$?
  if [ "$rc" = 0 ] && [ -f "$CONTRACT_ENV" ] && [ "$out" = "$(tr -d '\r' <"$CONTRACT_ENV")" ]; then
    ok "$CONTRACT_ENV na hoście = kontrakt tras obrazu web"
  else
    bad "$CONTRACT_ENV na hoście różni się od kontraktu tras obrazu web – wdróż kod i obraz tej samej wersji (scripts/deploy.sh)"
  fi

  # D12: aliasy językowe aktywnych konkursów (drugie drzewa Wagtaila) są poza zakresem DJ-02.
  # Zapytanie przez ORM (`manage.py shell -c`), a nie SQL – nazwy tabel należą do Django.
  rc=0; out="$(dc web python manage.py shell -c "$(cat <<'PY'
from apps.tenancy.aliases import CompetitionSiteAlias, content_translations_enabled
slugs = sorted(set(CompetitionSiteAlias.objects.filter(competition__is_active=True).values_list("competition__slug", flat=True))) if content_translations_enabled() else []
print("DJCMS_CUTOVER_ALIASES=" + " ".join(slugs))
PY
)" 2>&1 | tr -d '\r')" || rc=$?
  local aliases
  if [ "$rc" != 0 ] || ! printf '%s\n' "$out" | grep -q '^DJCMS_CUTOVER_ALIASES='; then
    bad "nie udało się sprawdzić aliasów językowych (manage.py shell w web, kod $rc)"; printf '%s\n' "$out" | tail -n 10 | indent
  else
    aliases="$(printf '%s\n' "$out" | sed -n 's/^DJCMS_CUTOVER_ALIASES=//p' | tail -n 1)"
    if [ -z "$aliases" ]; then ok "brak aliasów językowych aktywnych konkursów (D12)"
    else bad "aliasy językowe (WAGTAIL_I18N_ENABLED) w konkursach: $aliases – poza zakresem DJ-02 (D12), przełączenie niemożliwe"; fi
  fi

  # API v2 i rejestr witryn djcms – próbne uzgodnienie (bez zapisu).
  rc=0; out="$(dc djcms python manage.py sync_competitions --dry-run 2>&1 | tr -d '\r')" || rc=$?
  if [ "$rc" = 0 ]; then ok "API v2 odpowiada, rejestr konkursów (sync_competitions --dry-run):"; else bad "sync_competitions --dry-run w djcms (kod $rc) – API v2 albo token (DJCMS_INTERNAL_TOKEN)?"; fi
  printf '%s\n' "$out" | tail -n 20 | indent

  # Import próbny: każda paczka pobrana, zwalidowana i zaimportowana w wycofanej transakcji.
  rc=0; out="$(dc "${IMPORT_CMD[@]}" --dry-run 2>&1 | tr -d '\r')" || rc=$?
  if [ "$rc" = 0 ]; then ok "import_cms_bundle --all --replace --dry-run${SKIP_LIST:+ (--skip $SKIP_LIST)}"
  else bad "import_cms_bundle --all --replace --dry-run (kod $rc):"; printf '%s\n' "$out" | tail -n 30 | indent; fi

  # Miejsce: kopia (krok 1) ląduje w BACKUP_DIR, import (krok 4) na wolumenach Dockera, zwykle na tym
  # samym dysku co katalog instalacji. Pełny dysk zatrzymałby zapis WAL-u, czyli cały serwis.
  for dir in "$ROOT" "$BACKUP_DIR"; do
    [ -d "$dir" ] || dir="$(dirname "$dir")"
    case "$seen_dirs" in *" $dir "*) continue ;; esac
    seen_dirs="$seen_dirs$dir "
    avail="$(df -Pm "$dir" 2>/dev/null | awk 'NR == 2 { print $4 }')"
    if [ -n "$avail" ] && [ "$avail" -ge "$MIN_FREE_MB" ]; then ok "wolne miejsce $dir: $avail MB (próg $MIN_FREE_MB MB)"
    else bad "wolne miejsce $dir: ${avail:-?} MB < $MIN_FREE_MB MB (DJCMS_CUTOVER_MIN_FREE_MB)"; fi
  done

  if [ "$1" = 1 ]; then
    log "Test odtwarzania ostatniej kopii (scripts/backup_verify.sh)"
    if bash scripts/backup_verify.sh </dev/null; then ok "backup_verify ostatniej kopii"; else bad "backup_verify ostatniej kopii nie przeszedł"; fi
  fi
}

print_plan() {
  log "Plan pełnego przebiegu (--dry-run – nic z tego nie zostało wykonane)"
  say "1. bash scripts/backup.sh && bash scripts/backup_verify.sh"
  say "2. docker compose exec -T web python manage.py cms_freeze on --message \"$FREEZE_MESSAGE\" --by \"$FREEZE_BY\" --wait"
  say "3. docker compose exec -T djcms python manage.py sync_competitions"
  say "4. docker compose exec -T ${IMPORT_CMD[*]}"
  say "5. docker compose exec -T djcms python manage.py verify_cutover${SKIP_LIST:+   (porażka konkursów z --skip: $SKIP_LIST – tylko ostrzeżenie)}"
  say "6. bash scripts/djcms_switch.sh on"
  say "7. DJCMS_CUTOVER_DONE=<czas> w .env, podsumowanie"
  say "Wykonanie: bash scripts/djcms_cutover.sh${SKIP_LIST:+ $(printf -- '--skip %s ' "${SKIPS[@]}" | sed 's/ $//')}"
}

if [ "$MODE" = check ] || [ "$MODE" = dry-run ]; then
  run_checks "$BACKUP_VERIFY"
  [ "$MODE" = dry-run ] && print_plan
  if [ "$CHECK_FAIL" != 0 ]; then
    log "Kontrole NIE przeszły – przełączenie teraz by się nie udało (nic nie zostało zmienione)."
    exit 1
  fi
  log "Kontrole przeszły w $(( $(date +%s) - T0 )) s – nic nie zostało zmienione."
  exit 0
fi

# ================================================================================================
# Pełny przebieg
# ================================================================================================
# PHASE mówi pułapce EXIT, w jakim stanie zostaje serwis, gdy coś się nie uda (§ „Przerwanie”
# w nagłówku). Zmieniana tuż PRZED krokiem, więc błąd w kroku opisuje stan „w trakcie tego kroku”.
PHASE=checks
FROZEN_BEFORE=unknown
on_exit() {
  local rc=$?
  [ "$rc" -ne 0 ] || return 0
  printf '\n!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n' >&2
  printf '!!! Przełączenie PRZERWANE (kod %s, krok: %s).' "$rc" "$PHASE" >&2
  [ "$PHASE" = done ] || printf ' Serwis publiczny: Wagtail (bez zmian dla odwiedzających).' >&2
  printf '\n' >&2
  case "$PHASE" in
    checks|confirm)
      printf '!!! Nic nie zostało zmienione: edycja w /cms/ otwarta, djcms bez zmian.\n' >&2 ;;
    backup)
      printf '!!! Nic w serwisie nie zostało zmienione (kopia niepełna albo niesprawdzona – %s).\n' "$BACKUP_DIR" >&2
      printf '!!! Popraw przyczynę (log scripts/backup.sh wyżej) i uruchom skrypt ponownie.\n' >&2 ;;
    freeze)
      printf '!!! Zamrożenie edycji Wagtaila: stan niepewny – sprawdź: bash scripts/djcms_switch.sh status\n' >&2
      printf '!!! Ponowienie: bash scripts/djcms_cutover.sh … ; rezygnacja: bash scripts/djcms_cutover.sh --rollback --unfreeze\n' >&2 ;;
    sync|import|verify|switch)
      printf '!!! Wagtail: edycja stron ZAMROŻONA (skrypt jej nie odmraża – DJ-02 § 10.1 p. 5).\n' >&2
      [ "$PHASE" = switch ] && printf '!!! djcms_switch.sh on nie przeszedł – przełącznik sam wrócił do DJCMS_PRIMARY=0 (sprawdź: bash scripts/djcms_switch.sh status).\n' >&2
      printf '!!! djcms: treść częściowo albo całkowicie zaimportowana, niepubliczna.\n' >&2
      printf '!!! Dalej – jedno z dwóch:\n' >&2
      printf '!!!   poprawka i ponowienie (bezpieczne, import --replace daje ten sam stan): bash scripts/djcms_cutover.sh%s\n' \
        "${SKIP_LIST:+ $(printf -- '--skip %s ' "${SKIPS[@]}" | sed 's/ $//')}" >&2
      printf '!!!   rezygnacja (edycja w /cms/ znów otwarta):  bash scripts/djcms_cutover.sh --rollback --unfreeze\n' >&2 ;;
    done)
      printf '!!! Przełączenie WYKONANE (strony publiczne z djcms), błąd dopiero po nim – sprawdź: bash scripts/djcms_switch.sh status\n' >&2 ;;
  esac
  printf '!!! Dziennik: %s\n' "$LOG_FILE" >&2
  printf '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n' >&2
}
trap on_exit EXIT
take_lock

# --- 0. Kontrole (test odtwarzania dopiero na świeżej kopii w kroku 1) ------------------------------
run_checks 0
[ "$CHECK_FAIL" = 0 ] || die "kontrole nie przeszły – nic nie zostało zmienione"

# --- Potwierdzenie ----------------------------------------------------------------------------------
PHASE=confirm
log "Za chwilę: kopia → zamrożenie edycji stron w /cms/ → końcowy import z Wagtaila (--replace) → weryfikacja → przełączenie"
say "Treść djcms wszystkich aktywnych konkursów zostanie ZASTĄPIONA importem z Wagtaila${SKIP_LIST:+, poza: $SKIP_LIST}."
say "Wycofanie po przełączeniu: bash scripts/djcms_cutover.sh --rollback (~2 s; zmiany z djcms nie wracają do Wagtaila)."
if [ "$YES" != 1 ]; then
  [ -t 0 ] || { printf 'djcms_cutover: bez terminala potwierdź flagą --yes\n' >&2; exit 2; }
  read -r -p "    Wpisz PRZEŁĄCZ, aby kontynuować: " answer
  [ "$answer" = "PRZEŁĄCZ" ] || die "przerwane przez operatora"
fi

# --- 1. Kopia ---------------------------------------------------------------------------------------
PHASE=backup
log "1/7 Kopia: scripts/backup.sh (baza główna, baza i pliki djcms) i test odtwarzania"
bash scripts/backup.sh </dev/null || die "scripts/backup.sh nie powiódł się – bez kopii nie przełączam"
bash scripts/backup_verify.sh </dev/null || die "test odtwarzania świeżej kopii nie przeszedł – nie przełączam"

# --- 2. Zamrożenie Wagtaila --------------------------------------------------------------------------
PHASE=freeze
rc=0; freeze_state >/dev/null || rc=$?
FROZEN_BEFORE="$([ "$rc" = 0 ] && echo tak || echo nie)"
log "2/7 Zamrożenie edycji stron w Wagtailu (cms_freeze on)$([ "$FROZEN_BEFORE" = tak ] && echo ' – było już zamrożone, odświeżam komunikat')"
dc "${FREEZE_CMD[@]}" || die "cms_freeze on nie powiódł się"
freeze_state >/dev/null || die "po cms_freeze on status nie pokazuje zamrożenia"

# --- 3. Rejestr witryn ------------------------------------------------------------------------------
PHASE=sync
log "3/7 Rejestr konkursów djcms (sync_competitions)"
dc djcms python manage.py sync_competitions || die "sync_competitions nie powiódł się"

# --- 4. Końcowy import ------------------------------------------------------------------------------
PHASE=import
log "4/7 Końcowy import treści z Wagtaila (import_cms_bundle --all --replace${SKIP_LIST:+ --skip $SKIP_LIST})"
dc "${IMPORT_CMD[@]}" || die "import_cms_bundle nie powiódł się (konkursy z błędem – wyżej; pozostałe zaimportowane)"

# --- 5. Weryfikacja ---------------------------------------------------------------------------------
PHASE=verify
log "5/7 Weryfikacja witryn (verify_cutover)"
rc=0; VERIFY_OUT="$(dc djcms python manage.py verify_cutover 2>&1 | tr -d '\r')" || rc=$?
printf '%s\n' "$VERIFY_OUT"
if [ "$rc" != 0 ]; then
  # Wiersze tabeli verify_cutover: `<slug> <strony a/b> <adresy a/b> <przekier. a/b> OK|BŁĄD`.
  # Konkurs z --skip ma z definicji inną liczbę stron niż paczka Wagtaila – jego porażka to
  # ostrzeżenie; porażka każdego innego (albo brak tabeli: komenda padła) zatrzymuje przełączenie.
  failed="$(printf '%s\n' "$VERIFY_OUT" | awk '$NF == "BŁĄD" && $2 ~ /^[0-9]+\/[0-9]+$/ { print $1 }')"
  [ -n "$failed" ] || die "verify_cutover nie powiódł się (kod $rc) – nie przełączam"
  blocking=""
  for slug in $failed; do
    case " $SKIP_LIST " in *" $slug "*) ;; *) blocking="$blocking $slug" ;; esac
  done
  [ -z "$blocking" ] || die "verify_cutover: nie przeszły witryny:$blocking – nie przełączam"
  say "UWAGA: weryfikacja nie przeszła dla konkursów z --skip:$(printf ' %s' $failed) – ich treść jest z djcms,"
  say "       nie z Wagtaila, więc liczba stron różni się od paczki. Pozostałe powody (✗ wyżej) sprawdź"
  say "       po przełączeniu – przełączam."
fi

# --- 6. Przełączenie --------------------------------------------------------------------------------
PHASE=switch
log "6/7 Przełączenie (scripts/djcms_switch.sh on)"
bash scripts/djcms_switch.sh on </dev/null || die "djcms_switch.sh on nie powiódł się"

# --- 7. Znacznik i podsumowanie ---------------------------------------------------------------------
PHASE=done
log "7/7 Znacznik przełączenia w .env i podsumowanie"
set_env_line DJCMS_CUTOVER_DONE "$(date -Iseconds)" \
  "Przełączenie stron publicznych na django CMS (scripts/djcms_cutover.sh). Ponowny przebieg po wycofaniu wymaga --force-reimport."
trap - EXIT
log "Gotowe w $(( $(date +%s) - T0 )) s: strony publiczne wszystkich konkursów podaje django CMS."
say "Strony i przekierowania per konkurs (djcms/paczka Wagtaila):"
printf '%s\n' "$VERIFY_OUT" | awk '/^konkurs / || /^-+$/ || ($2 ~ /^[0-9]+\/[0-9]+$/)' | indent
[ -z "$SKIP_LIST" ] || say "Bez importu (treść djcms zachowana): $SKIP_LIST"
say "Wagtail: edycja stron zamrożona$([ "$FROZEN_BEFORE" = tak ] && echo ' (była już przed przebiegiem)'); wyjątki: warsztaty, partnerzy, ustawienia, dokumenty, obrazy."
say "Wycofanie (~2 s): bash scripts/djcms_cutover.sh --rollback   (= bash scripts/djcms_switch.sh off)"
say "  Wagtail pokaże stan z chwili zamrożenia; zmiany zrobione w djcms do Wagtaila NIE wracają."
say "Dziennik: $LOG_FILE"
