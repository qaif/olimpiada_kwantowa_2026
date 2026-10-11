#!/bin/sh
# Serwer audytu dostępności – uruchamiany WEWNĄTRZ kontenera obrazu `web` przez scripts/a11y.sh.
#
# Kolejność: (opcjonalnie) katalogi tłumaczeń → migracje → dane audytu (e2e/a11y/seed.py) →
# `runserver`. Dane powstają przed startem serwera, więc pierwsze żądanie testu widzi stan pełny,
# a plik `state/fixture.json` (adresy z kodami, sekret TOTP) jest gotowy, zanim Playwright ruszy.
set -eu
cd /app

if [ "${A11Y_COMPILE_MESSAGES:-0}" = "1" ]; then
  # Kod z hosta (lokalnie) nie ma skompilowanych `.mo` – obraz kompiluje je przy budowaniu, ale
  # zamontowany katalog przykrywa wynik. Ten sam krok, co w backend/Dockerfile.
  find . -path '*/LC_MESSAGES/django.po' | while read -r po; do
    msgfmt -o "${po%.po}.mo" "$po"
  done
fi

if [ -n "${A11Y_EXTRA_PIP:-}" ]; then
  # Obraz deweloperski bywa starszy od pyproject.toml (nowa zależność w gałęzi) – lokalnie można
  # doinstalować brakujące pakiety na czas przebiegu. W CI obraz jest budowany z commita.
  # Venv obrazu nie ma własnego pipa – pip systemowy instaluje do niego przez --python.
  pip --python "$(command -v python)" install -q --disable-pip-version-check --root-user-action=ignore ${A11Y_EXTRA_PIP}
fi

python manage.py migrate --noinput -v 0
python manage.py shell -c "exec(open('/e2e/a11y/seed.py', encoding='utf-8').read())"
exec python manage.py runserver 0.0.0.0:8000 --noreload
