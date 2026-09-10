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

# When started via sudo, run git as the invoking user so the working tree
# keeps its ownership and git's dubious-ownership checks stay satisfied.
run_git() {
  if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    sudo -u "$SUDO_USER" -H git "$@"
    return
  fi
  git "$@"
}

if [ ! -f .env ]; then
  echo "error: .env not found — run 'cp .env.example .env' and adjust it first" >&2
  exit 1
fi

run_git fetch origin main
run_git pull --ff-only origin main

docker compose down
docker compose build --no-cache
docker compose up -d

echo "Updated and restarted. UI: http://<your-host-ip>:${WEB_PORT:-14637}"
