# syntax=docker/dockerfile:1.7
# Obraz djcms – wersja porównawcza serwisu na django CMS (docs/tasks/DJ-01.md § 8.7).
#
# Kontekst budowania = KORZEŃ repozytorium (nie ``djcms/``): obraz kopiuje ``backend/static``
# do ``/opt/shared_static`` – te same arkusze, skrypty i fonty co aplikacja główna, bez kopii
# w repozytorium, która mogłaby się rozjechać. Co wchodzi do kontekstu, mówi
# ``djcms/Dockerfile.dockerignore`` (BuildKit bierze plik ``<Dockerfile>.dockerignore`` zamiast
# ``.dockerignore`` z korzenia).

# --- Etap 1: zależności (uv, plik blokady) ---------------------------------------------------
FROM python:3.14-slim-trixie AS builder
# Ta sama wersja uv, którą powstał ``djcms/uv.lock`` – starsza mogłaby nie znać formatu blokady.
COPY --from=ghcr.io/astral-sh/uv:0.11.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY djcms/pyproject.toml djcms/uv.lock ./
# Sekret ``extra_ca`` (opcjonalny): dodatkowy certyfikat głównego urzędu dla TLS do PyPI.
# Po co: antywirus na maszynie dewelopera (Norton) przechwytuje HTTPS i podpisuje ruch własnym
# CA – bez niego ``uv sync`` kończy się ``UnknownIssuer``. Certyfikat:
#   - przychodzi jako sekret BuildKit (``--secret id=extra_ca,src=…`` albo ``secrets:`` compose'a),
#     więc nie ma go w żadnej warstwie, w historii obrazu ani w cache'u,
#   - trafia do ``/tmp/ca.pem`` i do ``SSL_CERT_FILE`` **wyłącznie na czas tego jednego RUN**,
#   - pusty plik (domyślny ``djcms/docker/no-extra-ca.pem``) albo brak sekretu = zwykły magazyn CA
#     obrazu. Na produkcji sekretu się nie podaje.
# Etap ``runtime`` kopiuje z tego etapu wyłącznie ``/opt/venv``.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=extra_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/extra_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem; \
        export SSL_CERT_FILE=/tmp/ca.pem; \
    fi; \
    uv sync --frozen --no-install-project --extra dev; \
    rm -f /tmp/ca.pem

# --- Etap 2: runtime (nie-root, bez kompilatorów i bez certyfikatu z etapu 1) ----------------
FROM python:3.14-slim-trixie AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" \
    DJCMS_SHARED_STATIC_DIR=/opt/shared_static
RUN groupadd -g 1000 app && useradd -u 1000 -g app -m -s /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY backend/static/ /opt/shared_static/
COPY --chown=app:app djcms/ /app/
# ``/app/media`` należy do ``app``: nazwany wolumen ``djcms_media`` przy pierwszym montażu przejmuje
# właściciela z obrazu – inaczej byłby rootowy i filer nie zapisałby ani jednego pliku.
RUN set -eu; \
    mkdir -p /app/media /app/staticfiles; \
    chown app:app /app/media; \
    chmod +x /app/entrypoint.sh; \
    DJCMS_BUILD=1 DJANGO_SETTINGS_MODULE=config.settings.production \
        python manage.py collectstatic --noinput >/dev/null
USER app
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
# To samo polecenie co ``command:`` usługi ``djcms`` w docker-compose.yml – ``docker run`` obrazu
# bez compose'a (debugowanie) ma uruchamiać dokładnie ten sam model współbieżności.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--worker-class", "gthread", "--timeout", "60", "--graceful-timeout", "20", "--access-logfile", "-"]
