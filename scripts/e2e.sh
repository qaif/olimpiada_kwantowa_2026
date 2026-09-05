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
#   ./scripts/e2e.sh              # pełny cykl: reset + seed + testy
#   ./scripts/e2e.sh --no-reset   # bez kasowania danych i bez seedu
#
# Uwaga do `--no-reset`: scenariusz zamyka etap eliminacyjny i publikuje jego wyniki, więc drugi
# przebieg na tym samym stanie **nie przejdzie**. Ten wariant jest dla środowiska, w którym etap
# jeszcze nie został przetworzony (np. po awarii w połowie poprzedniego przebiegu).
#
set -euo pipefail

# Ścieżki kontenerowe w argumentach `docker compose` nie mogą zostać przetłumaczone przez MSYS.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)
RESET=1
[[ "${1:-}" == "--no-reset" ]] && RESET=0

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
fi

echo "==> Scenariusz E2E (Playwright)"
START=$(date +%s)
set +e
"${COMPOSE[@]}" --profile e2e run --rm e2e
STATUS=$?
set -e
echo "==> Czas przebiegu: $(( $(date +%s) - START )) s (status $STATUS)"
echo "    artefakty: e2e/artifacts/"
exit $STATUS
