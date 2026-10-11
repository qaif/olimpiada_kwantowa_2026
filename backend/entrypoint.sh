#!/bin/sh
set -eu

# Czeka na bazę (healthcheck compose już to gwarantuje, ale entrypoint jest samowystarczalny).
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

# Migracje uruchamia tylko proces web (worker/beat mają MIGRATE=0), żeby nie ścigać się o locki.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
fi
if [ "${RUN_COLLECTSTATIC:-1}" = "1" ]; then
    python manage.py collectstatic --noinput --clear >/dev/null
    # Notatniki kwantowe (QC-01): JupyterLite zbudowane w obrazie (etap `notebook-lab`) obok plików
    # statycznych – podaje je Caddy spod /static/notebook-lab/<BUILD_ID>/ z własną polityką CSP.
    # Kopia, a nie collectstatic: manifest WhiteNoise przepisywałby adresy w CSS JupyterLab i dublował
    # ~40 MB plików wersjami z odciskiem, których nikt nie użyje. `--clear` wyżej czyści starą wersję.
    if [ -f /app/notebook_lab_dist/current.json ]; then
        mkdir -p /app/staticfiles/notebook-lab
        cp -R /app/notebook_lab_dist/. /app/staticfiles/notebook-lab/
    fi
fi

exec "$@"
