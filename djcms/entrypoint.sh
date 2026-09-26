#!/bin/sh
# Entrypoint djcms: czeka na bazę, stosuje migracje (RUN_MIGRATIONS=1, domyślnie), uruchamia polecenie.
# Wzorzec backend/entrypoint.sh. Bez collectstatic – statyki są zebrane w czasie budowania obrazu,
# a system plików kontenera jest tylko do odczytu (read_only: true).
set -eu

# Healthcheck compose'a już to gwarantuje (depends_on: db healthy), ale entrypoint jest samowystarczalny
# (``docker run`` obrazu bez compose'a). ``DATABASE_URL`` nie trafia do logu – tylko nazwa wyjątku.
python - <<'PY'
import os, sys, time
import psycopg
url = os.environ["DATABASE_URL"]
for i in range(30):
    try:
        psycopg.connect(url, connect_timeout=3).close()
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"db not ready ({exc.__class__.__name__}), retry {i+1}/30", flush=True)
        time.sleep(2)
sys.exit(1)
PY

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
