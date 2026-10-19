# Sandboxed TeX Live image used by webTEX to compile projects (COMPILE_BACKEND=docker).
# Each compile runs in a fresh container: `docker run --rm --init --network none ...`
#
# Default: the full, newest TeX Live (scheme-full, without documentation and sources) from the
# official TeX Live images (https://hub.docker.com/r/texlive/texlive), ~2.8 GB compressed.
# Since TeX Live 2026 openin_any no longer restricts reads; here that is harmless because each
# container sees only its own project directory.
# Smaller or pinned variants:
#   docker build --build-arg TEXLIVE_IMAGE=texlive/texlive:latest-medium -t webtex-texlive docker/texlive
#   docker build --build-arg TEXLIVE_IMAGE=texlive/texlive:TL2025-historic -t webtex-texlive docker/texlive
ARG TEXLIVE_IMAGE=texlive/texlive:latest-full
FROM ${TEXLIVE_IMAGE}

# Compile as uid 1000 (the official images already have it as user "texlive").
RUN getent passwd 1000 >/dev/null || useradd --create-home --uid 1000 tex

# biber is a self-extracting (PAR) binary of ~30 MB that unpacks ~120 MB on its first run. Unpack it
# once here, for uid 1000, and make the cache read-only, so compiles neither repeat it nor can alter it.
ENV PAR_GLOBAL_TMPDIR=/opt/webtex-biber
RUN mkdir -p /opt/webtex-biber && chown 1000:1000 /opt/webtex-biber
USER 1000
RUN HOME=/tmp biber --version
USER root
RUN chown -R root:root /opt/webtex-biber && chmod -R a+rX,go-w /opt/webtex-biber
USER 1000

# Long log lines make the log parser much more reliable.
ENV HOME=/tmp \
    TEXMFVAR=/tmp/texmf-var \
    max_print_line=10000 \
    error_line=254 \
    half_error_line=238

WORKDIR /compile
