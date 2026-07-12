# Issues and Recommendations

Review date: 2026-04-03
Scope: backend (`app/app.py`), frontend (`ui/src/App.svelte`, `ui/src/app.css`), Docker/runtime config, and project docs.

Severity guide used in this file:

- **High**: material security/reliability risk in typical deployment.
- **Medium**: user-facing reliability or operability issue with realistic impact.
- **Low**: correctness/UX/documentation clarity issue with limited blast radius.

---

## Open issues

### I-007 - Flask development server used as runtime server (Medium)

- Evidence: process starts with `app.run(...)` (`app/app.py:1226`).
- Impact: reduced production hardening/observability compared to a dedicated WSGI server.
- Recommendation:
  - For long-lived deployments, run under `gunicorn` or `waitress` (single worker/thread is still fine for this app).

### I-009 - Per-job auth override is hidden when container defaults are set (Low) -- INTENTIONAL BEHAVIOR

- Evidence: auth inputs are conditionally hidden when defaults exist (`ui/src/App.svelte:873`).
- Impact: users cannot intentionally override container defaults for a new job without using restart flow.
- Recommendation:
  - Add explicit "Override container auth" toggle in Add dialog.

### I-019 - Flash message system only renders errors (Low) -- INTENTIONAL BEHAVIOR

- Evidence: `setFlash(message, isError)` supports `isError=false`, but the template at `ui/src/App.svelte:713-714` only renders the flash when `flashError` is true. Success/info messages are silently discarded.
- Impact: no way to show transient success messages to the user (e.g., "Download queued successfully").
- Recommendation:
  - Add a visual flash component for non-error messages (e.g., a success banner).

### I-021 - No confirmation for destructive actions (Low) -- INTENTIONAL BEHAVIOR

- Evidence: "Clear Inactive" and "Cancel" (for running jobs) execute immediately without confirmation. A misclick on "Clear Inactive" permanently removes all job history.
- Impact: accidental data loss for job history.
- Recommendation:
  - Add a simple confirmation dialog for "Clear Inactive" and for cancelling a running download.

### I-022 - Empty `app/templates/` and `app/static/` directories (Low)

- Evidence: Flask's default `templates/` and `static/` directories exist under `app/` but are empty. The app serves its UI from `WEB_ROOT` (`/app/web`) instead.
- Impact: minor confusion for contributors; unused directories.
- Recommendation:
  - Remove both empty directories, or add a `.gitkeep` with a brief comment if they serve a structural purpose.

---

## Resolved baseline notes for future runs

- Keep the following as expected baseline behavior (re-open only if regressions are found):
  - Optional API key auth is implemented (`API_KEY` / `X-API-Key`) with frontend prompt flow and docs coverage.
  - Backend enforces request body limits via `MAX_CONTENT_LENGTH` and returns JSON `413` responses.
  - Queue locking fixes are in place for running-job cancel response serialization and `/api/config` reads.
  - Polling/loading hardening is in place (`pollInFlight` guard, initial load always clears after first poll attempt).
  - Job table accessibility and visibility improvements are implemented (keyboard-operable rows, hidden-row notice).
  - Timestamp and queue-counter semantics are clarified (UTC timestamps, disjoint queue buckets with explicit pending total).
  - Runtime lifecycle hardening is implemented (graceful SIGTERM shutdown, cancel watchdog with SIGKILL fallback).
  - UI operability improvements are implemented (detail log cap setting, pending-state button labels for cancel/retry/restart).
  - Duplicate active downloads are rejected with HTTP `409`.
  - Prior docs consistency gaps around `API_KEY` and AGENTS Web UI behavior were fixed.

---

## Documentation issues verified correct in this pass

- **README.md:44** - "Clear Inactive" label matches UI (`ui/src/App.svelte:770`).
- **README.md:50-61** - All `.env` key descriptions match implementation defaults.
- **AGENTS.md:3** - Table columns (Name, Progress, Files, Actions) match `ui/src/App.svelte:91-95`.
- **AGENTS.md:13** - Auto-scroll checkbox is in details pane (`ui/src/App.svelte:878`).
- **AGENTS.md:10** - Auth field visibility logic matches `ui/src/App.svelte:930`.
- **docs/API.md** - All endpoints, parameters, and status values verified against `app/app.py` routes and logic.
- **docs/TRUENAS.md** - Port mapping, volume mount, and env vars match `docker-compose.yml`.

---

## Feature ideas

### F-001 - WebSocket/SSE for real-time log streaming

- Currently, logs are fetched via polling (`GET /api/jobs/<id>/logs?offset=N`).
- Server-Sent Events or WebSocket would reduce latency and HTTP overhead for active logs, especially for fast-producing downloads.
- The polling approach is simple and works well at the default 1.2s interval, but a streaming option would improve the UX for users watching active downloads.

### F-002 - Disk space monitoring

- Show available disk space in the downloads directory in the footer or details pane.
- Implementation: `os.statvfs` on `DOWNLOAD_ROOT`, exposed via `/api/config` or a new endpoint.
- Could warn before queueing if space is critically low.

### F-003 - Bulk job operations

- Allow selecting multiple jobs (shift-click, checkbox) and performing batch cancel or clear.
- Useful when many jobs are queued and the user wants to cancel several at once.

### F-004 - Job queue reordering (drag to reprioritize)

- Currently, queued jobs run in FIFO order. Allow users to drag rows in the table to change priority.
- Implementation: new API endpoint `POST /api/jobs/<id>/reorder` accepting a new position, updating `queued_job_ids`.

### F-005 - Export/download full job logs

- Add a "Download Logs" button in the details pane that exports the full log as a text file.
- Could use a new API endpoint `GET /api/jobs/<id>/logs/export` returning the full log as `text/plain`.

### F-006 - Notification support

- Browser notifications (via the Notification API) or a simple sound alert when:
  - A running job completes or fails.
  - The queue finishes (all jobs in terminal state).
- Useful when the user has the UI in a background tab.

### F-007 - Pause queue

- Add a "Pause Queue" button that stops the scheduler from picking up new jobs without cancelling existing ones.
- Implementation: a `queue_paused` flag checked in `scheduler_loop`.

### F-008 - Duplicate/clone job configuration

- Right-click or action button on a completed/failed/cancelled job to clone its settings (URL, subdir, auth, retry config) into a new Add Download dialog.
- Saves re-entering configuration for similar downloads.

### F-009 - API rate limiting

- Even with `API_KEY`, there is no rate limiting. A malicious client with a valid key (or an attacker on a trusted LAN without API key) could flood the API.
- Consider lightweight rate limiting (e.g., Flask-Limiter) to protect against accidental or intentional API abuse.

### F-010 - Job search/filter in table

- For users with many jobs (near the `MAX_JOBS` limit), add a simple text filter or status filter above the table.
- Allows quickly finding a specific download in a busy queue.
