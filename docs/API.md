# HTTP API

Base URL: `http://<host>:14637`

Request body limit: `16 KiB` by default (`MAX_REQUEST_BODY_BYTES`). Oversized
JSON payloads return `413` with:

```json
{ "error": "Request body too large (max 16384 bytes)." }
```

## Authentication

API endpoints under `/api/*` can be protected with a static API key. Set the
`API_KEY` environment variable to enable this. When enabled, every request to
`/api/*` (except `/api/auth-status`) must include an `X-API-Key` header whose
value matches the configured key.

```
X-API-Key: your-secret-key
```

Requests with a missing or incorrect key receive a `401` response:

```json
{ "error": "Invalid or missing API key." }
```

When `API_KEY` is not set (the default), all endpoints are accessible without
authentication. This is suitable for trusted LAN deployments.

### Auth-exempt routes

The following routes never require an API key:

- `GET /` , `GET /index.html` , `GET /assets/*` (static UI)
- `GET /healthz` (health check)
- `GET /api/auth-status` (tells clients whether auth is enabled)

### Auth status

- `GET /api/auth-status`
  - Returns `{ "auth_required": true }` when `API_KEY` is set, `false` otherwise.
  - Always accessible without a key.

## Health

- `GET /healthz`
  - Returns `{ "status": "ok" }`

## Configuration

- `GET /api/config`
  - Returns runtime config summary:
    - `download_dir`
    - `active_job_id`
    - `default_username`
    - `has_default_password`

## Jobs

- `GET /api/jobs`
  - Returns recent jobs, current active job ID, and queue statistics.
  - Each job includes `queue_position` when status is `queued`.
  - Structured timestamps and log-line prefixes are in UTC (`...Z`).
  - Includes `queue_stats`:
    - `total_jobs`
    - `queued_jobs` (ready-to-run queue only)
    - `retry_wait_jobs`
    - `pending_jobs` (`queued_jobs + retry_wait_jobs`)
    - `running_jobs`
    - `completed_jobs`
    - `failed_jobs`
    - `cancelled_jobs`
    - `terminal_jobs`
    - `progress_percent`

- `POST /api/jobs`
  - Enqueues a new download job.
  - The scheduler runs one queued job at a time.
  - Body:

```json
{
  "url": "https://archive.org/details/Something",
  "subdir": "optional/custom/subfolder",
  "username": "optional archive.org username",
  "password": "optional archive.org password",
  "retry_delay_minutes": 10,
  "max_retry_attempts": 3
}
```

  - `url` must match `https://archive.org/details/<identifier>`.
  - `subdir` is optional and must stay inside `/downloads`.
- `username` and `password` are optional, but must be supplied together.
- If omitted, server-side defaults (`IA_USERNAME`, `IA_PASSWORD`) are used when configured.
- `retry_delay_minutes` controls automatic requeue on failure (`0` disables auto-retry).
- `max_retry_attempts` sets how many automatic retries are allowed (`0` means unlimited retries).

- `GET /api/jobs/<job_id>`
  - Returns one job status.

- `GET /api/jobs/<job_id>/logs?offset=0`
  - Returns incremental logs:
    - `lines`: new log lines from the offset
    - `next_offset`: pass this value into your next request

- `POST /api/jobs/<job_id>/cancel`
  - Cancels a queued job immediately, or sends cancellation signal (`SIGINT`) to a running job.

- `POST /api/jobs/<job_id>/restart`
  - Resets a `failed` or `cancelled` job and re-queues it (reuses the same job ID).
  - Optional body:

```json
{
  "username": "override archive.org username",
  "password": "override archive.org password",
  "retry_delay_minutes": 10,
  "max_retry_attempts": 3
}
```

  - If the job originally used authentication and no password is supplied, container defaults (`IA_USERNAME`/`IA_PASSWORD`) are tried. Returns `400` if they don't match.
  - `retry_delay_minutes` and `max_retry_attempts` default to the job's existing values when omitted.

- `POST /api/jobs/clear-finished`
  - Removes inactive history rows (`completed`, `failed`, `cancelled`).
  - Keeps active rows (`queued`, `running`, `retry_wait`).
  - In the web UI, this is wired to the **Clear Inactive** action.

## Job status values

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `retry_wait` (failed and waiting for delayed retry)
