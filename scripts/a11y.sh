#!/usr/bin/env bash
#
# Testy dostępności (A11Y-01): axe-core (WCAG 2.1 A/AA) + kontrole klawiatury, kontrastu, RTL i reflow.
#
# Przebieg jest SAMOWYSTARCZALNY – nie dotyka stosu compose dewelopera ani jego bazy:
#   1. własna sieć Dockera i własny Postgres (dane w tmpfs, znikają z kontenerem),
#   2. kontener obrazu `web` z ustawieniami `config.settings.a11y` (bez Redisa, MinIO, ClamAV,
#      workera – docstring modułu) wykonuje e2e/a11y/serve.sh: migracje, dane audytu, runserver,
#   3. kontener Playwrighta (ten sam obraz, co scenariusz E2E – e2e/requirements.txt) puszcza
#      `pytest e2e/a11y` przeciw http://web:8000,
#   4. sprzątanie (także po błędzie).
#
# Użycie (Git Bash / Linux, z katalogu głównego repozytorium):
#   ./scripts/a11y.sh                      # lokalnie: obraz olimpiada/web:dev + kod z hosta
#   ./scripts/a11y.sh -k iqo               # dodatkowe argumenty idą do pytest
#   A11Y_WEB_IMAGE=olimpiada-web:ci A11Y_MOUNT_BACKEND=0 ./scripts/a11y.sh   # CI: kod z obrazu
#   A11Y_KEEP=1 ./scripts/a11y.sh          # nie sprzątaj (serwer zostaje do oglądania w przeglądarce)
#   A11Y_SERVE_ONLY=1 ./scripts/a11y.sh    # tylko serwer (zostaje); potem testy bez restartu:
#   A11Y_REUSE=1 ./scripts/a11y.sh -k home # … na serwerze z poprzedniego kroku
#
# Wynik: kod wyjścia pytest; raport (JSON + zrzuty) w e2e/artifacts/a11y/. Nowe naruszenie
# „critical”/„serious” spoza e2e/a11y/baseline.json przewraca przebieg (docs/OPERACJE.md § 50).
set -euo pipefail
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."
ROOT="$(pwd -W 2>/dev/null || pwd)"

WEB_IMAGE="${A11Y_WEB_IMAGE:-olimpiada/web:dev}"
PW_IMAGE="${A11Y_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright/python:v1.49.1-jammy}"
PG_IMAGE="${A11Y_PG_IMAGE:-postgres:18-alpine}"
MOUNT_BACKEND="${A11Y_MOUNT_BACKEND:-1}"
P="${A11Y_PROJECT:-olimpiada-a11y}"
NET="${P}-net"

cleanup() {
  if [[ "${A11Y_KEEP:-0}" == "1" || "${A11Y_SERVE_ONLY:-0}" == "1" || "${A11Y_REUSE:-0}" == "1" ]]; then
    echo "==> Serwer zostaje: http://web:8000 w sieci ${NET} (sprzątanie: docker rm -f ${P}-web ${P}-db; docker network rm ${NET})"
    return
  fi
  docker rm -f "${P}-web" "${P}-db" "${P}-pw" >/dev/null 2>&1 || true
  docker network rm "${NET}" >/dev/null 2>&1 || true
}

start_server() {
  # Pozostałości po przerwanym przebiegu – inaczej `docker run --name` kończy się konfliktem nazw.
  docker rm -f "${P}-web" "${P}-db" "${P}-pw" >/dev/null 2>&1 || true
  docker network rm "${NET}" >/dev/null 2>&1 || true

  mkdir -p e2e/a11y/state e2e/artifacts/a11y
  rm -f e2e/a11y/state/*.json
  # Kontener `web` pisze tu plik z danymi audytu jako inny użytkownik niż właściciel checkoutu (CI).
  chmod 777 e2e/a11y/state e2e/artifacts/a11y 2>/dev/null || true

  echo "==> Sieć i baza (${PG_IMAGE}, dane w tmpfs)"
  docker network create "${NET}" >/dev/null
  docker run -d --name "${P}-db" --network "${NET}" --network-alias db \
    -e POSTGRES_DB=olimpiada -e POSTGRES_USER=olimpiada -e POSTGRES_PASSWORD=olimpiada \
    --tmpfs /var/lib/postgresql:rw,size=1g \
    "${PG_IMAGE}" -c fsync=off -c synchronous_commit=off -c full_page_writes=off \
    -c max_locks_per_transaction=256 >/dev/null
  for _ in $(seq 1 60); do
    docker exec "${P}-db" pg_isready -U olimpiada -d olimpiada >/dev/null 2>&1 && break
    sleep 1
  done

  echo "==> Serwer audytu (${WEB_IMAGE}, config.settings.a11y)"
  local args=(
    -d --name "${P}-web" --network "${NET}" --network-alias web --user root
    -e DJANGO_SETTINGS_MODULE=config.settings.a11y
    -e DATABASE_URL=postgres://olimpiada:olimpiada@db:5432/olimpiada
    -e DJANGO_SECRET_KEY=a11y-tylko-do-testow-dostepnosci-nie-uzywac-nigdzie-indziej-0123456789
    -e DJANGO_ALLOWED_HOSTS=web,localhost,127.0.0.1
    -e DJANGO_MEDIA_ROOT=/tmp/a11y-media
    -e A11Y_COMPILE_MESSAGES="${MOUNT_BACKEND}"
    -v "${ROOT}/e2e:/e2e"
    -v "${ROOT}/themes:/themes:ro"
    --entrypoint sh
  )
  if [[ "${MOUNT_BACKEND}" == "1" ]]; then
    args+=(-v "${ROOT}/backend:/app")
  fi
  docker run "${args[@]}" "${WEB_IMAGE}" /e2e/a11y/serve.sh >/dev/null

  echo "==> Czekam na migracje, dane audytu i start serwera"
  local ready=0
  for _ in $(seq 1 180); do
    if ! docker ps -q --filter "name=^${P}-web$" | grep -q .; then
      echo "Kontener ${P}-web zakończył pracę:" >&2
      docker logs --tail 80 "${P}-web" >&2 || true
      return 1
    fi
    if docker exec "${P}-web" python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=5).status == 200 else 1)" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 3
  done
  if [[ $ready -ne 1 ]]; then
    echo "Serwer audytu nie wstał w 9 minut:" >&2
    docker logs --tail 80 "${P}-web" >&2 || true
    return 1
  fi
  docker logs "${P}-web" 2>&1 | grep -E '^a11y-seed' || true
}

trap cleanup EXIT
if [[ "${A11Y_REUSE:-0}" != "1" ]]; then
  start_server
fi
if [[ "${A11Y_SERVE_ONLY:-0}" == "1" ]]; then
  exit 0
fi

echo "==> Playwright: pytest e2e/a11y $*"
set +e
docker run --rm --network "${NET}" -v "${ROOT}/e2e:/e2e" -w /e2e \
  -e E2E_BASE_URL=http://web:8000 -e A11Y_UPDATE_BASELINE="${A11Y_UPDATE_BASELINE:-0}" \
  "${PW_IMAGE}" sh -c "pip install -q --disable-pip-version-check --root-user-action=ignore -r requirements.txt && python -m pytest -c a11y/pytest.ini a11y --browser chromium $*"
STATUS=$?
set -e
if [[ $STATUS -ne 0 ]]; then
  echo "==> Ostatnie wiersze logu serwera:" >&2
  docker logs --tail 40 "${P}-web" >&2 || true
fi
echo "==> Status ${STATUS}; raport: e2e/artifacts/a11y/"
exit $STATUS
