# TrueNAS Deployment Guide

This wrapper is designed to fit NAS workflows where installing `ia-get` directly is not convenient.

## 1) Prepare storage

Create a dataset/folder for downloads, for example:

```text
/mnt/<pool>/apps/ia-get/downloads
```

Use that path as `DOWNLOADS_PATH` in `.env`.

## 2) Set runtime UID/GID

Pick user/group IDs that should own downloaded files on your NAS.

- In many TrueNAS setups, `568:568` works (`apps:apps`).
- If needed, use IDs from your own service user.

Set:

```dotenv
PUID=568
PGID=568
```

## 3) Configure and launch

From this project directory:

```bash
cp .env.example .env
```

Edit `.env` values for your system, then:

```bash
docker compose up -d --build
```

## 4) Open the UI

Browse to:

```text
http://<truenas-ip>:14637
```

## 5) Verify output

Run one test download and confirm files appear in your mounted dataset.

---

## Optional: TrueNAS Custom App YAML style

If you prefer TrueNAS custom app config instead of CLI compose, translate these settings:

- Image: built image from this repo (`ia-get-web:latest`) or your own registry tag.
- Port mapping: host `14637` -> container `8080`.
- Volume mount: host dataset -> container `/downloads`.
- Do not mount anything over `/app` (it contains the bundled web wrapper code).
- Environment:
  - `DOWNLOAD_DIR=/downloads`
  - `HOST=0.0.0.0`
  - `PORT=8080`
  - optional auth defaults: `IA_USERNAME`, `IA_PASSWORD`
  - optional: `MAX_LOG_LINES`, `MAX_JOBS`, `STATE_FILE`, `STATE_LOG_LINES`, `TZ`
- Security context / user: run as your desired UID/GID.

## Updating upstream ia-get

Update `.env`:

```dotenv
IA_GET_REPO=https://github.com/L-K-M/ia-get.git
IA_GET_REF=<branch-or-tag>
```

Then rebuild:

```bash
docker compose build --no-cache
docker compose up -d
```
