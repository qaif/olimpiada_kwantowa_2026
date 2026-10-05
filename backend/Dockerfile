# syntax=docker/dockerfile:1.7
# Etap 1: instalacja zależności z uv (szybka, powtarzalna; warstwa cache'owana po pyproject)
FROM python:3.14-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && uv pip install --python /opt/venv/bin/python -r pyproject.toml --extra dev

# Etap 1b: JupyterLite dla notatników kwantowych (QC-01, docs/tasks/QC-01.md § 3.1). Osobny etap, bo
# narzędzia budowy (jupyterlite-core, doit…) i pobrane archiwa Pyodide nie mają czego szukać w obrazie
# runtime – przechodzi wyłącznie gotowy katalog statyczny. Wszystko z przypiętym skrótem: narzędzia
# `--require-hashes`, rdzeń Pyodide i koła pakietów – SHA-256 z pyodide.json i z pyodide-lock.json.
# Cache BuildKit trzyma pobrane archiwa między budowami (zmiana kodu qclab nie pobiera Pyodide od nowa).
FROM python:3.14-slim-trixie AS notebook-lab
WORKDIR /build
COPY apps/notebooks/labbuild/requirements.txt apps/notebooks/labbuild/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --root-user-action=ignore --require-hashes --no-deps -r apps/notebooks/labbuild/requirements.txt
COPY apps/__init__.py apps/__init__.py
COPY apps/notebooks/__init__.py apps/notebooks/__init__.py
COPY apps/notebooks/labbuild apps/notebooks/labbuild
COPY qclab qclab
RUN --mount=type=cache,target=/root/.cache/notebook-lab \
    python -m apps.notebooks.labbuild --out /opt/notebook-lab --cache /root/.cache/notebook-lab

# Etap 2: runtime (nie-root, minimalny)
# ``gettext`` dochodzi do warstwy apt, bo Django czyta wyłącznie **skompilowane** katalogi
# tłumaczeń (``.mo``), a w repozytorium trzymamy źródła (``.po``) – tylko one dają się czytać
# i recenzować w diffie. Kompilacja jest krokiem budowania obrazu (niżej), więc plik binarny
# nigdy nie rozjedzie się ze źródłem: nie da się wydać obrazu z nieaktualnym tłumaczeniem.
#
# Dwie rzeczy z bramki Trivy (SEC-02, docs/OPERACJE.md § 47.2):
# - ``apt-get upgrade``: łatki z ``trixie-security`` trafiają do obrazu ``python:*-slim`` z opóźnieniem
#   (np. libpcre2 10.46-1~deb13u3, CVE-2026-103111) – bez tego obraz dziedziczy podatność, na którą
#   Debian ma już poprawkę. Warstwa odświeża się przy każdej zmianie obrazu bazowego,
# - systemowy ``pip`` z obrazu bazowego wypada: aplikacja żyje w /opt/venv (bez pipa, uv), a pip 26.2.1
#   niesie zwendorowane urllib3 2.7.0 / msgpack 1.1.2 / setuptools 70.3.0 ze znanymi podatnościami
#   (poprawionego wydania pipa jeszcze nie ma). Gdy pip jest potrzebny doraźnie w kontenerze:
#   ``docker compose exec -u root web python -m ensurepip``.
FROM python:3.14-slim-trixie AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends libpq5 curl procps gettext \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y --root-user-action=ignore pip \
    && groupadd -g 1000 app && useradd -u 1000 -g app -m -s /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . .
# Zbudowane JupyterLite (etap ``notebook-lab``); entrypoint procesu web kopiuje je do STATIC_ROOT.
COPY --from=notebook-lab --chown=app:app /opt/notebook-lab /app/notebook_lab_dist
RUN mkdir -p /app/staticfiles && chown app:app /app/staticfiles && chmod +x /app/entrypoint.sh
# Katalog wymiany z piaskownicą notatników (QC-01, wolumen `notebook_spool`). Pusty wolumen nazwany
# przejmuje właściciela i tryb z obrazu: worker (app) pisze, nadzorca piaskownicy wchodzi przez grupę
# (`group_add`), a dzieci piaskownicy (inne UID, bez grup) – nie mają wstępu (2770, setgid).
RUN mkdir -p /spool && chown app:app /spool && chmod 2770 /spool
# Katalogi tłumaczeń: ``msgfmt`` wprost, a nie ``manage.py compilemessages``. Powody są dwa:
# komenda Django ładuje ustawienia (czyli wymagałaby kompletu zmiennych środowiskowych już na
# etapie budowania obrazu) i przechodzi po **całym** drzewie, łącznie z katalogami tłumaczeń
# bibliotek w wirtualnym środowisku. Tutaj kompilujemy dokładnie to, co jest nasze: katalog główny
# i katalogi aplikacji (``apps/<nazwa>/locale`` – Django scala je z głównym przy tłumaczeniu).
RUN set -eu; for po in locale/*/LC_MESSAGES/django.po apps/*/locale/*/LC_MESSAGES/django.po; do \
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
# i compose rozjeżdżałyby się cichaczem co do modelu współbieżności. `--no-control-socket`: patrz
# komentarz przy gunicornie w pyproject.toml.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--threads", "4", "--worker-class", "gthread", "--no-control-socket"]
