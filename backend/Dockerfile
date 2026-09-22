# syntax=docker/dockerfile:1.7
# Etap 1: instalacja zależności z uv (szybka, powtarzalna; warstwa cache'owana po pyproject)
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && uv pip install --python /opt/venv/bin/python -r pyproject.toml --extra dev

# Etap 2: runtime (nie-root, minimalny)
# ``gettext`` dochodzi do warstwy apt, bo Django czyta wyłącznie **skompilowane** katalogi
# tłumaczeń (``.mo``), a w repozytorium trzymamy źródła (``.po``) – tylko one dają się czytać
# i recenzować w diffie. Kompilacja jest krokiem budowania obrazu (niżej), więc plik binarny
# nigdy nie rozjedzie się ze źródłem: nie da się wydać obrazu z nieaktualnym tłumaczeniem.
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl procps gettext \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 app && useradd -u 1000 -g app -m -s /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . .
RUN mkdir -p /app/staticfiles && chown app:app /app/staticfiles && chmod +x /app/entrypoint.sh
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
