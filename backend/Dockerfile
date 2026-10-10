# syntax=docker/dockerfile:1.7
# Obrazy bazowe przypięte skrótem (`@sha256:`), a nie samym tagiem (audyt bezpieczeństwa 10.10.2026,
# S21): tag `3.14-slim-trixie` jest pływający – ten sam plik budowany w dwóch różnych dniach dawał
# dwa różne systemy bazowe, a podmieniony w rejestrze obraz wszedłby na produkcję przy najbliższym
# buildzie bez śladu w repozytorium. Skrót = indeks wieloarchitekturowy z 10.10.2026 (`docker buildx
# imagetools inspect python:3.14-slim-trixie`); obraz uv (`COPY --from`) – tak samo. Aktualizacja (łatki
# Debiana/CPythona) jest więc **świadomą** zmianą tych linijek i tych w djcms/Dockerfile – nowy skrót
# z tego samego polecenia.
ARG PYTHON_IMAGE=python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

# Etap 1: zależności z pliku blokady (`backend/uv.lock`, `uv lock` po każdej zmianie pyproject.toml).
# Do 10.10.2026 było tu `uv pip install -r pyproject.toml --extra dev`: bez blokady każdy build brał
# najnowsze wydania w zakresach (także zależności przechodnie, bez żadnych granic), a obraz
# produkcyjny niósł pytest, factory-boy, ruff i resztę narzędzi deweloperskich. Teraz:
# - `--frozen` – dokładnie wersje i skróty z `uv.lock`; pyproject bez nowej blokady nie zmienia
#   niczego (zgodność blokady z pyproject sprawdza CI: `uv lock --check`, zadanie `lint`),
# - `--no-dev --no-install-project` – bez ekstry `dev` (i ewentualnej grupy `dev`) i bez samego
#   projektu (to nie jest pakiet, kod wchodzi niżej zwykłym `COPY`).
# Wersja uv ta sama, którą powstała blokada (0.11.23, jak w djcms/Dockerfile) – 0.4, która stała tu
# wcześniej, nie zna obecnego formatu `uv.lock`. Sekret `extra_ca` (opcjonalny) – dodatkowe CA dla
# TLS do PyPI na maszynie z antywirusem przechwytującym HTTPS; opis w djcms/Dockerfile, na
# produkcji go nie ma.
FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.23@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=extra_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/extra_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem; \
        export SSL_CERT_FILE=/tmp/ca.pem; \
    fi; \
    uv sync --frozen --no-dev --no-install-project; \
    rm -f /tmp/ca.pem

# Etap 1b: to samo środowisko z ekstrą `dev` (pytest, factory-boy, ruff…) – WYŁĄCZNIE dla etapu
# `dev` niżej (docker-compose.dev.yml: testy uruchamia się w kontenerze `web`). Obraz `runtime`
# z tego etapu nie bierze niczego.
FROM builder AS builder-dev
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=extra_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/extra_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem; \
        export SSL_CERT_FILE=/tmp/ca.pem; \
    fi; \
    uv sync --frozen --no-install-project --extra dev; \
    rm -f /tmp/ca.pem

# Etap 2: runtime (nie-root, minimalny)
# ``gettext`` dochodzi do warstwy apt, bo Django czyta wyłącznie **skompilowane** katalogi
# tłumaczeń (``.mo``), a w repozytorium trzymamy źródła (``.po``) – tylko one dają się czytać
# i recenzować w diffie. Kompilacja jest krokiem budowania obrazu (niżej), więc plik binarny
# nigdy nie rozjedzie się ze źródłem: nie da się wydać obrazu z nieaktualnym tłumaczeniem.
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl procps gettext \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 app && useradd -u 1000 -g app -m -s /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . .
# `/var/tmp/packages` – punkt montowania wolumenu `package_tmp` (PACKAGE_TMP_DIR, docker-compose.yml).
# Musi istnieć w obrazie i należeć do `app`: nazwany wolumen przy pierwszym montażu przejmuje
# właściciela katalogu z obrazu, a bez niego byłby rootowy i aplikacja nie zapisałaby paczki.
RUN mkdir -p /app/staticfiles /var/tmp/packages \
    && chown app:app /app/staticfiles /var/tmp/packages && chmod +x /app/entrypoint.sh
# Katalogi tłumaczeń: ``msgfmt`` wprost, a nie ``manage.py compilemessages``. Powody są dwa:
# komenda Django ładuje ustawienia (czyli wymagałaby kompletu zmiennych środowiskowych już na
# etapie budowania obrazu) i przechodzi po **całym** drzewie, łącznie z katalogami tłumaczeń
# bibliotek w wirtualnym środowisku. Tutaj kompilujemy dokładnie to, co jest nasze.
RUN set -eu; for po in locale/*/LC_MESSAGES/django.po; do \
        [ -e "$po" ] || continue; \
        msgfmt -o "${po%.po}.mo" "$po"; \
        chown app:app "${po%.po}.mo"; \
    done
USER app
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
# WSGI + gthread, nie ASGI: aplikacja jest wyłącznie synchroniczna – patrz komentarz przy
# `command:` usługi `web` w docker-compose.yml. To jest ta sama binarka, więc `docker run` bez
# compose (np. przy debugowaniu obrazu) musi wywoływać dokładnie to samo polecenie, inaczej obraz
# i compose rozjeżdżałyby się cichaczem co do modelu współbieżności.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--threads", "4", "--worker-class", "gthread"]

# Etap `dev` – obraz środowiska deweloperskiego (docker-compose.dev.yml, `target: dev`): `runtime`
# plus narzędzia z ekstry `dev`, bo `docker compose exec web pytest …` biegnie w tym kontenerze.
# Środowisko z `builder-dev` jest nadzbiorem produkcyjnego, więc kopia na to samo miejsce tylko
# dokłada pakiety. Produkcja, CI (`target: runtime`) i nakładka E2E budują `runtime`.
FROM runtime AS dev
COPY --from=builder-dev /opt/venv /opt/venv

# Ostatni etap = cel domyślny `docker build` bez `--target`. Celowo znowu `runtime`, a nie `dev`:
# ktoś, kto zbuduje obraz „gołym” poleceniem, ma dostać obraz produkcyjny, a nie ten z pytestem.
FROM runtime AS default
