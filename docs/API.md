# HTTP API

Base URL: `http://<host>:8080`

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
  - Includes `queue_stats`:
    - `total_jobs`
    - `queued_jobs`
    - `retry_wait_jobs`
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
  "url": "https://archive.org/details/En-ROMs",
  "subdir": "optional/custom/subfolder",
  "username": "optional archive.org username",
  "password": "optional archive.org password",
  "retry_delay_minutes": 10
}
```

  - `url` must match `https://archive.org/details/<identifier>`.
  - `subdir` is optional and must stay inside `/downloads`.
  - `username` and `password` are optional, but must be supplied together.
  - If omitted, server-side defaults (`IA_USERNAME`, `IA_PASSWORD`) are used when configured.
  - `retry_delay_minutes` controls automatic requeue on failure (`0` disables auto-retry).

- `GET /api/jobs/<job_id>`
  - Returns one job status.

- `GET /api/jobs/<job_id>/logs?offset=0`
  - Returns incremental logs:
    - `lines`: new log lines from the offset
    - `next_offset`: pass this value into your next request

- `POST /api/jobs/<job_id>/cancel`
  - Cancels a queued job immediately, or sends cancellation signal (`SIGINT`) to a running job.

## Job status values

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `retry_wait` (failed and waiting for delayed retry)
