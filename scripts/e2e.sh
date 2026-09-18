#!/usr/bin/env bash
#
# Scenariusz E2E na czystym środowisku (T-10).
#
# Kolejność jest istotna: najpierw kasujemy dane (baza, MinIO, Redis), potem stawiamy usługi,
# potem sypiemy dane demonstracyjne, a dopiero na końcu puszczamy Playwrighta.
#
# Wolumen `clamav_db` NIE jest kasowany celowo: pierwsze pobranie sygnatur wirusów trwa kilka
# minut, a scenariusz czeka na prawdziwy werdykt ClamAV. Skasowanie tego wolumenu zamienia
# 3-minutowy przebieg w kwadrans oczekiwania na freshclam.
#
# Użycie (Git Bash / WSL / Linux, z katalogu głównego repozytorium):
#   ./scripts/e2e.sh              # pełny cykl: reset + seed + testy + etap 2
#   ./scripts/e2e.sh --no-reset   # bez kasowania danych i bez seedu
#   ./scripts/e2e.sh --stage2     # wyłącznie krok etapu 2 (dwa konkursy): bez resetu i bez pełnego cyklu
#
# Uwaga do `--no-reset`: scenariusz zamyka etap eliminacyjny i publikuje jego wyniki, więc drugi
# przebieg na tym samym stanie **nie przejdzie**. Ten wariant jest dla środowiska, w którym etap
# jeszcze nie został przetworzony (np. po awarii w połowie poprzedniego przebiegu).
#
# Krok „etap 2” (T43) jest osobny i **idempotentny**: zakłada drugi konkurs `e2e-druga` w trybie
# prefiksu ścieżki (`--skip-existing`, więc powtórzenie niczego nie nadpisuje), zapala mu flagi
# etapu 2 i puszcza dwie kontrole (`e2e/check_stage2_screens.py`, `e2e/check_stage2_isolation.py`).
# Dlatego wolno go uruchomić samego, także po nieudanym przebiegu głównym — w przeciwieństwie do
# scenariusza pełnego cyklu, który wymaga czystego stanu.
#
set -euo pipefail

# Ścieżki kontenerowe w argumentach `docker compose` nie mogą zostać przetłumaczone przez MSYS.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)
RESET=1
MAIN=1
case "${1:-}" in
  --no-reset) RESET=0 ;;
  # Sam krok etapu 2: zastany stan środowiska, żadnego kasowania i żaden scenariusz pełnego cyklu.
  --stage2) RESET=0; MAIN=0 ;;
esac

# --- krok etapu 2: drugi konkurs i jego kontrole ------------------------------------------------
#
# Identyfikator, domena i prefiks są tu stałymi, bo **te same** wartości zna `e2e/stage2.py`
# i moduł `backend/apps/web/tests/test_e2e_two_competitions.py`. Konkurs stoi w trybie prefiksu
# ścieżki, więc nie potrzebuje ani wpisu w DNS-ie, ani pozycji w `DJANGO_ALLOWED_HOSTS`.
SECOND_SLUG=e2e-druga
SECOND_DOMAIN=e2e-druga.localhost
SECOND_PREFIX=druga

# Flagi zapalane konkursowi drugiemu (docs/UNIWERSALNY-ETAP-2.md § 0.6, zadanie T43). Zestaw jest
# **jawną listą**, a nie „wszystko z katalogu”: flagi bez własnego ekranu (marka w poczcie,
# zakresowane uprawnienia /cms/, tłumaczenia treści) zmieniają zachowanie, którego ten przebieg
# nie dotyka, a zapalone milczkiem utrudniałyby odczytanie przyczyny usterki.
SECOND_FLAGS='{"per_competition_consents": true, "document_templates": true, "custom_regions": true,
 "categories": true, "process_editor": true, "weighted_scoring": true, "reviewer_roles": true,
 "team_entries": true, "fees": true, "onsite_logistics": true, "institution_types": true,
 "custom_school_directory": true}'

stage_two() {
  echo "==> Etap 2: drugi konkurs „${SECOND_SLUG}” pod prefiksem /${SECOND_PREFIX}/"
  # `--skip-existing` zamiast sprawdzania w bashu: pytanie „czy konkurs już jest” ma jedną
  # odpowiedź i zna ją komenda, a nie skrypt.
  "${COMPOSE[@]}" exec -T web python manage.py create_competition \
    --slug "$SECOND_SLUG" --name "Olimpiada Druga" --domain "$SECOND_DOMAIN" \
    --from-template przedmiotowa --path-prefix "$SECOND_PREFIX" \
    --coordinator-email koordynator@example.com --skip-existing

  # Flag nie da się podać komendzie (i słusznie: zapalenie funkcji jest decyzją organizatora,
  # a nie skutkiem ubocznym zakładania konkursu — docs/OPERACJE.md § 6.4). Przebieg E2E robi
  # więc to, co operator robi w /admin/: wpisuje różnice wobec wartości domyślnych.
  "${COMPOSE[@]}" exec -T web python manage.py shell -c "
import json
from apps.tenancy.models import Competition
row = Competition.objects.get(slug='${SECOND_SLUG}')
row.feature_flags = json.loads('''${SECOND_FLAGS}''')
row.save(update_fields=['feature_flags'])
print('flagi konkursu', row.slug, '=', sorted(row.feature_flags))
"

  local status=0
  for check in check_stage2_screens.py check_stage2_isolation.py; do
    echo "==> Etap 2: ${check}"
    "${COMPOSE[@]}" --profile e2e run --rm -T \
      -e E2E_SECOND_PREFIX="$SECOND_PREFIX" e2e \
      sh -c "pip install -q -r requirements.txt && python ${check}" || status=$?
  done
  return $status
}

if [[ ! -f .env ]]; then
  echo "Brak pliku .env – skopiuj .env.example i uzupełnij wartości." >&2
  exit 1
fi

if [[ $RESET -eq 1 ]]; then
  echo "==> Zatrzymuję środowisko"
  "${COMPOSE[@]}" down --remove-orphans

  # Nazwa projektu compose: zmienna środowiskowa albo – jak u compose – nazwa katalogu
  # sprowadzona do [a-z0-9_-] („olimpiada clade” → „olimpiadaclade”).
  PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')}"

  # Kasujemy po etykietach compose, a nie po sklejonej nazwie: filtr projektu chroni wolumeny
  # innych projektów, które mają wolumen o tej samej nazwie logicznej.
  echo "==> Kasuję dane projektu ${PROJECT} (pg_data, minio_data, redis_data). clamav_db zostaje."
  for volume in pg_data minio_data redis_data; do
    for name in $(docker volume ls -q \
        --filter "label=com.docker.compose.project=${PROJECT}" \
        --filter "label=com.docker.compose.volume=${volume}"); do
      docker volume rm "$name" >/dev/null 2>&1 || true
    done
  done
fi

# `docker compose up -d` wraca, gdy kontener wystartował – nie gdy aplikacja jest gotowa.
# Migracje idą z entrypointu `web`, więc `seed_demo` bez tego czekania trafia na pustą bazę
# („relation competitions_edition does not exist”).
wait_healthy() {
  local service="$1" attempts="$2" pause="$3" state="unknown" id
  for _ in $(seq 1 "$attempts"); do
    id="$("${COMPOSE[@]}" ps -q "$service" 2>/dev/null || true)"
    if [[ -n "$id" ]]; then
      state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo unknown)"
      [[ "$state" == "healthy" ]] && { echo "    ${service}: healthy"; return 0; }
    fi
    sleep "$pause"
  done
  echo "    ${service}: ${state} – nie osiągnął stanu healthy" >&2
  return 1
}

echo "==> Uruchamiam usługi"
"${COMPOSE[@]}" up -d web worker beat minio-init clamav

echo "==> Czekam na gotowość usług (ClamAV pobiera sygnatury przy pierwszym starcie)"
wait_healthy clamav 90 10
wait_healthy web 60 5
wait_healthy worker 60 5

if [[ $RESET -eq 1 ]]; then
  echo "==> Dane demonstracyjne"
  "${COMPOSE[@]}" exec -T web python manage.py seed_demo
  "${COMPOSE[@]}" exec -T web python manage.py seed_cms
  # Słownik szkół: scenariusz rejestruje uczestnika przez wyszukiwarkę szkół, więc bez tego
  # kroku podpowiedzi byłyby puste i krok 1 nie miałby czego kliknąć.
  "${COMPOSE[@]}" exec -T web python manage.py seed_schools
fi

START=$(date +%s)
STATUS=0

if [[ $MAIN -eq 1 ]]; then
  echo "==> Scenariusz E2E (Playwright)"
  set +e
  "${COMPOSE[@]}" --profile e2e run --rm e2e
  STATUS=$?
  set -e
fi

# Krok etapu 2 idzie **po** scenariuszu pełnego cyklu i nawet wtedy, gdy tamten padł: kontrole
# dwóch konkursów nie zależą od zamkniętego etapu eliminacyjnego, a wynik obu przebiegów naraz
# jest tym, po co się je uruchamia. Status wyjścia niesie pierwszą usterkę, którą widać.
set +e
stage_two
STAGE2_STATUS=$?
set -e
[[ $STATUS -eq 0 ]] && STATUS=$STAGE2_STATUS

echo "==> Czas przebiegu: $(( $(date +%s) - START )) s (status $STATUS, etap 2: $STAGE2_STATUS)"
echo "    artefakty: e2e/artifacts/"
exit $STATUS
