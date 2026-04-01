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
  - Returns recent jobs and current active job ID.

- `POST /api/jobs`
  - Starts a new job (only one running job allowed).
  - Body:

```json
{
  "url": "https://archive.org/details/En-ROMs",
  "subdir": "optional/custom/subfolder",
  "username": "optional archive.org username",
  "password": "optional archive.org password"
}
```

  - `url` must match `https://archive.org/details/<identifier>`.
  - `subdir` is optional and must stay inside `/downloads`.
  - `username` and `password` are optional, but must be supplied together.
  - If omitted, server-side defaults (`IA_USERNAME`, `IA_PASSWORD`) are used when configured.

- `GET /api/jobs/<job_id>`
  - Returns one job status.

- `GET /api/jobs/<job_id>/logs?offset=0`
  - Returns incremental logs:
    - `lines`: new log lines from the offset
    - `next_offset`: pass this value into your next request

- `POST /api/jobs/<job_id>/cancel`
  - Sends cancellation signal (`SIGINT`) to running `ia-get`.

## Job status values

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
