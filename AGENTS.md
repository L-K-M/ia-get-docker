## Web UI behavior and limitations

- Main view is a download table with columns: Name, Progress, Files, and Actions.
- Add new downloads with **Add Download...**; each job is queued.
- The wrapper supports a multi-job queue, with one active running job at a time.
- Queue state is persisted on disk, so queued/active entries remain visible after restart.
- Use **Clear Inactive** to remove completed/failed/cancelled history rows.
- Select a row to view full metadata and live logs in the right-side details pane.
- Footer shows overall queue progress and queue state counts.
- Username/password can be provided per-job in the UI when container auth defaults are not fully configured. When both container defaults are configured, new-job auth fields are hidden unless a restart flow requires explicit credentials.
- Container-level defaults (`IA_USERNAME`, `IA_PASSWORD`) are also supported.
- A System 7 style settings dialog lets you tune UI polling interval, recent-job list length, detail log line cap, retry defaults, and form defaults.
- Log auto-scroll is controlled in the details pane via an **Auto-scroll logs** checkbox.
- Progress is estimated from `ia-get` output (`current file / total files`).
- Full transfer-byte percentages are not exposed by upstream `ia-get`, so this UI shows file-count progress plus live logs.
- Downloads are resumable because `ia-get` itself supports resume and hash validation.
- By default the API is unauthenticated and intended for trusted local networks. Set `API_KEY` to require an `X-API-Key` header on all `/api/*` endpoints.

## Frontend development (optional)

The production container serves a prebuilt Svelte UI from `ui/dist`. For local UI work:

```bash
cd ui
npm install
npm run dev
```

Vite runs on `http://localhost:4173` and proxies `/api` requests to the Flask backend at `http://localhost:8080`.

## Troubleshooting

- `PermissionError: /app/app/__init__.py` at startup:
    - Rebuild with the latest Dockerfile, which forces read/execute permissions on app files and starts via script mode.
    - Run:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

- If your TrueNAS app definition adds extra mounts, make sure nothing is mounted over `/app`.

- `Frontend bundle not found. Build the Svelte UI first.`:
    - Rebuild the image to compile the frontend bundle:

```bash
docker compose build --no-cache
docker compose up -d
```

## Issue tracking

- Exhaustive issue analysis and recommendations are maintained in `ISSUES.md`.
