# syntax=docker/dockerfile:1.7

FROM rust:bookworm AS ia-get-builder

ARG IA_GET_REPO=https://github.com/L-K-M/ia-get.git
ARG IA_GET_REF=main

# python3 runs the notices generator below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src/ia-get

RUN git clone --depth 1 --branch "${IA_GET_REF}" "${IA_GET_REPO}" .
# --locked builds exactly the crates in ia-get's Cargo.lock, which the
# notices below list.
RUN cargo build --release --locked && strip target/release/ia-get

# Notices for the Rust crates linked into ia-get and for the Rust standard
# library (Rust-Licenses.html), from the tree just built. Fails when a
# crate publishes no license text.
RUN --mount=type=bind,source=scripts/licenses/rust-notices.py,target=/tmp/rust-notices.py \
    python3 /tmp/rust-notices.py --target host --title ia-get \
      --out /notices/Rust-Notices.txt --rust-std /notices/Rust-Licenses.html


FROM node:22-bookworm-slim AS web-builder

WORKDIR /src/ui

COPY ui/package.json /src/ui/package.json
COPY ui/package-lock.json /src/ui/package-lock.json
RUN npm ci

COPY ui /src/ui
RUN npm run build


FROM python:3.14-slim-bookworm AS base

FROM base AS runtime

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

# Every Debian package in the image with its license file and exact source
# version (snapshot.debian.org), right after the last package installation.
RUN --mount=type=bind,source=scripts/licenses/os-packages.sh,target=/tmp/os-packages.sh \
    --mount=type=bind,from=base,source=/var/lib/dpkg/status,target=/tmp/base-db \
    sh /tmp/os-packages.sh --image ia-get-docker --base-db /tmp/base-db \
      /usr/share/doc/ia-get-docker/OS-Packages.txt

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY --from=web-builder /src/ui/dist /app/web
COPY --from=ia-get-builder /src/ia-get/target/release/ia-get /usr/local/bin/ia-get

# License notices in /usr/share/doc/ia-get-docker: this project's LICENSE,
# the hand-written THIRD_PARTY_NOTICES.md, Rust-Licenses.html, and one
# Third-Party-Notices.txt with ia-get's crates, then the Python packages and
# interpreter, then the web UI's npm packages and fonts. The Python part is
# generated here, from what pip installed; it fails on a package without
# license text.
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/ia-get-docker/
COPY --from=ia-get-builder /notices/Rust-Licenses.html /usr/share/doc/ia-get-docker/
RUN --mount=type=bind,source=scripts/licenses/python-notices.py,target=/tmp/notices/python-notices.py \
    --mount=type=bind,source=licenses,target=/tmp/notices/licenses \
    --mount=type=bind,from=ia-get-builder,source=/notices/Rust-Notices.txt,target=/tmp/notices/Rust-Notices.txt \
    python3 -I /tmp/notices/python-notices.py \
      --path "$(python3 -c 'import sysconfig; print(sysconfig.get_path("purelib"))')" \
      --cpython --overrides /tmp/notices/licenses/python-notices.toml \
      --title "The ia-get-docker image bundles the Python software below." \
      --out /tmp/Python-Notices.txt \
    && cat /tmp/notices/Rust-Notices.txt /tmp/Python-Notices.txt /app/web/Third-Party-Notices.txt \
      > /usr/share/doc/ia-get-docker/Third-Party-Notices.txt \
    && rm /tmp/Python-Notices.txt \
    && cd /usr/share/doc/ia-get-docker \
    && test -s LICENSE && test -s THIRD_PARTY_NOTICES.md && test -s Third-Party-Notices.txt \
    && test -s Rust-Licenses.html && test -s OS-Packages.txt \
    && test -s /app/web/Third-Party-Notices.txt

RUN chmod -R a+rX /app \
    && chmod 755 /usr/local/bin/ia-get \
    && mkdir -p /downloads \
    && chmod 775 /downloads

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/app/app.py"]
