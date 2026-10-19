# webTEX all-in-one image: Node.js server + built client + TeX Live (COMPILE_BACKEND=local).
#   docker build -t webtex .
#   docker run -p 3000:3000 -v webtex-data:/data webtex
#
# Default TeX Live: the full, newest release (scheme-full, without documentation and sources) from
# the official images (https://hub.docker.com/r/texlive/texlive), same as docker/texlive.
# TeX runs inside this container, next to the database, and since TeX Live 2026 openin_any no longer
# restricts reads, so every compile runs under server/tex/sandbox.pl: Linux Landlock confines it to
# its project directory. That needs a host kernel >= 5.13 with Landlock enabled (the server logs
# "local compiles sandboxed with Landlock" at the first compile); in integration mode compiling is
# refused otherwise. Other variants: docker build --build-arg TEXLIVE_IMAGE=texlive/texlive:latest-medium -t webtex .
ARG TEXLIVE_IMAGE=texlive/texlive:latest-full

# ---------- build the client, production dependencies ----------
FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build && npm prune --omit=dev

# ---------- runtime: TeX Live + Node.js ----------
FROM ${TEXLIVE_IMAGE}

# The server's dependencies are plain JavaScript, so the Node binary is all we need from the Node image.
COPY --from=build /usr/local/bin/node /usr/local/bin/node
# Run as uid 1000 (the official images already have it as user "texlive").
RUN node --version \
 && (getent passwd 1000 >/dev/null || useradd --create-home --uid 1000 webtex) \
 && mkdir -p /data && chown 1000:1000 /data

# biber is a self-extracting (PAR) binary of ~30 MB that unpacks ~120 MB on its first run. Unpack it
# once here, for uid 1000, read-only: sandboxed compiles can use it but neither repeat nor alter it.
ENV PAR_GLOBAL_TMPDIR=/opt/webtex-biber
RUN mkdir -p /opt/webtex-biber && chown 1000:1000 /opt/webtex-biber
USER 1000
RUN HOME=/tmp biber --version
USER root
RUN chown -R root:root /opt/webtex-biber && chmod -R a+rX,go-w /opt/webtex-biber

WORKDIR /app
COPY --from=build /app/package.json ./
COPY --from=build /app/node_modules ./node_modules
COPY server ./server
COPY --from=build /app/client/dist ./client/dist

USER 1000

ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0 \
    DATA_DIR=/data \
    COMPILE_BACKEND=local \
    HOME=/tmp \
    NODE_NO_WARNINGS=1

VOLUME ["/data"]
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server/index.js"]
