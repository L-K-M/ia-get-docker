# syntax=docker/dockerfile:1.7

FROM rust:bookworm AS ia-get-builder

ARG IA_GET_REPO=https://github.com/L-K-M/ia-get.git
ARG IA_GET_REF=main

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src/ia-get

RUN git clone --depth 1 --branch "${IA_GET_REF}" "${IA_GET_REPO}" .
RUN cargo build --release && strip target/release/ia-get


FROM node:22-bookworm-slim AS web-builder

WORKDIR /src/ui

COPY ui/package.json /src/ui/package.json
COPY ui/package-lock.json /src/ui/package-lock.json
RUN npm ci

COPY ui /src/ui
RUN npm run build


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_DIR=/downloads \
    HOST=0.0.0.0 \
    PORT=8080 \
    WEB_ROOT=/app/web \
    IA_GET_BIN=/usr/local/bin/ia-get

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY --from=web-builder /src/ui/dist /app/web
COPY --from=ia-get-builder /src/ia-get/target/release/ia-get /usr/local/bin/ia-get

RUN chmod -R a+rX /app \
    && chmod 755 /usr/local/bin/ia-get \
    && mkdir -p /downloads \
    && chmod 775 /downloads

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/app/app.py"]
