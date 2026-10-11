#!/usr/bin/env bash
# Test obciążenia na lokalnym, jednorazowym stosie (PERF-01, docs/OPERACJE.md § 42).
#
#   scripts/loadtest/run.sh up                      # stos olimpiada-loadtest + migracje
#   scripts/loadtest/run.sh seed [--students 3000]  # loadtest_seed + manifest do runs/loadtest/
#   scripts/loadtest/run.sh run <nazwa> [argumenty loadgen.py…]
#   scripts/loadtest/run.sh down                    # zatrzymanie i skasowanie wolumenów stosu
#
# `run` zapisuje w runs/loadtest/<nazwa>/: raport generatora (summary.md/csv, timeline.csv,
# requests.csv), próbki `docker stats` co 5 s (stats.csv), ranking zapytań z pg_stat_statements
# (pg_top.txt) i liczbę połączeń z bazą w szczycie (pg_conn.txt). Liczby workerów/wątków/CPU stosu
# ustawia się zmiennymi przy `up` (np. `WEB_WORKERS=6 WEB_THREADS=4 LOADTEST_WEB_CPUS=4 run.sh up`).
#
# Ten skrypt NIE dotyka produkcji ani stosu deweloperskiego: projekt compose `olimpiada-loadtest`,
# własne wolumeny i sieć bez wyjścia na świat. Generator i tak odmawia hostów spoza listy lokalnej.
set -euo pipefail

# `pwd -W` (Git Bash na Windows) daje ścieżkę C:/…, którą docker rozumie także przy MSYS_NO_PATHCONV.
HERE="$(cd "$(dirname "$0")" && (pwd -W 2>/dev/null || pwd))"
ROOT="$(cd "$HERE/../.." && (pwd -W 2>/dev/null || pwd))"
OUT="$ROOT/runs/loadtest"
COMPOSE=(docker compose -f "$HERE/docker-compose.loadtest.yml")
export MSYS_NO_PATHCONV=1   # Git Bash na Windows: nie przepisywać /tmp, /out na ścieżki hosta
mkdir -p "$OUT"

psql() { "${COMPOSE[@]}" exec -T db psql -U loadtest -d olimpiada_loadtest -At "$@"; }

cmd="${1:-}"; shift || true
case "$cmd" in
  up)
    # --wait wyłącznie na usługach stałych: jednorazowy minio-init kończy się kodem 0, a --wait
    # na całym projekcie uznaje każdy zakończony kontener za porażkę.
    "${COMPOSE[@]}" up -d && "${COMPOSE[@]}" up -d --wait db redis minio clamav web worker proxy
    # Katalogi tłumaczeń jak w backend/Dockerfile (pliki .mo nie są w repozytorium).
    "${COMPOSE[@]}" exec -T web sh -c 'for f in $(find /app -name django.po); do msgfmt -o "${f%.po}.mo" "$f"; done' || true
    "${COMPOSE[@]}" restart web >/dev/null && "${COMPOSE[@]}" up -d --wait web proxy
    psql -c "create extension if not exists pg_stat_statements" >/dev/null
    echo "Stos olimpiada-loadtest gotowy: http://127.0.0.1:8088/"
    ;;
  seed)
    "${COMPOSE[@]}" exec -T web python manage.py loadtest_seed --i-know-this-is-not-prod \
      --manifest /tmp/manifest.json "$@"
    "${COMPOSE[@]}" cp web:/tmp/manifest.json "$OUT/manifest.json" >/dev/null
    echo "Manifest: $OUT/manifest.json"
    ;;
  run)
    name="${1:?nazwa przebiegu}"; shift
    dir="$OUT/$name"; mkdir -p "$dir"
    psql -c "select pg_stat_statements_reset()" >/dev/null
    # Próbki zasobów co 5 s w tle – zatrzymywane pułapką także przy przerwaniu generatora.
    (
      echo "t,container,cpu_percent,mem" > "$dir/stats.csv"
      while true; do
        ts=$(date +%s)
        docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' \
          | grep olimpiada-loadtest | sed "s/^/$ts,/" >> "$dir/stats.csv" || true
        conn=$(psql -c "select count(*) from pg_stat_activity where datname='olimpiada_loadtest'" 2>/dev/null || echo "")
        echo "$ts,$conn" >> "$dir/pg_conn.txt"
        sleep 5
      done
    ) &
    sampler=$!
    trap 'kill $sampler 2>/dev/null || true' EXIT
    set +e
    # LOADTEST_SHARDS procesów generatora naraz (jeden proces Pythona = jeden rdzeń; od ok. 1000
    # uczniów jeden proces sam staje się wąskim gardłem – § 42.2). Wspólny start za 15 s, żeby
    # wszystkie kontenery zdążyły wstać przed T0; potem raport łączny (--merge).
    shards="${LOADTEST_SHARDS:-1}"
    if [ "$shards" -le 1 ]; then
      "${COMPOSE[@]}" --profile loadgen run --rm loadgen --manifest /out/manifest.json --out "/out/$name" "$@"
      status=$?
    else
      start_at=$(( $(date +%s) + 15 ))
      pids=()
      for ((k = 0; k < shards; k++)); do
        "${COMPOSE[@]}" --profile loadgen run --rm loadgen --manifest /out/manifest.json --out "/out/$name" \
          --shards "$shards" --shard "$k" --start-at "$start_at" "$@" > "$dir/loadgen-$k.log" 2>&1 &
        pids+=($!)
      done
      tail -f "$dir/loadgen-0.log" --pid "${pids[0]}" 2>/dev/null | grep --line-buffered -E "^\[|PRZERW" &
      status=0
      for pid in "${pids[@]}"; do wait "$pid" || status=$?; done
      "${COMPOSE[@]}" --profile loadgen run --rm loadgen --merge "/out/$name" "$@" > "$dir/merge.log" 2>&1 || status=$?
      cat "$dir/summary.md"
    fi
    set -e
    kill $sampler 2>/dev/null || true
    psql -c "select round(total_exec_time)::int as total_ms, calls, round(mean_exec_time::numeric,2) as mean_ms,
                    rows, left(regexp_replace(query, '\s+', ' ', 'g'), 220)
             from pg_stat_statements where dbid = (select oid from pg_database where datname='olimpiada_loadtest')
             order by total_exec_time desc limit 30" > "$dir/pg_top.txt" || true
    "${COMPOSE[@]}" logs --no-color --since 30m web 2>/dev/null | grep -E "WORKER TIMEOUT|Error|Traceback|PoolTimeout" \
      | sort | uniq -c | sort -rn | head -40 > "$dir/web_errors.txt" || true
    echo "Wyniki: $dir (kod generatora: $status)"
    exit $status
    ;;
  down)
    "${COMPOSE[@]}" --profile loadgen down -v
    ;;
  *)
    sed -n '2,15p' "$0"; exit 64
    ;;
esac
