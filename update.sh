#!/usr/bin/env bash
# update.sh — pull the latest wrapper code, rebuild the image, and restart.
#
# The Dockerfile clones upstream ia-get from the IA_GET_REF *branch* at build
# time, so a plain `docker compose build` would reuse the cached layer and keep
# the old upstream build. `--no-cache` is therefore required on every update.
#
# Usage: ./update.sh
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "error: .env not found — run 'cp .env.example .env' and adjust it first" >&2
  exit 1
fi

git fetch origin main
git pull --ff-only origin main

docker compose down
docker compose build --no-cache
docker compose up -d

echo "Updated and restarted. UI: http://<your-host-ip>:${WEB_PORT:-14637}"
