from __future__ import annotations

import datetime as dt
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from flask import Flask, request, send_from_directory

ARCHIVE_URL_RE = re.compile(r"^https://archive\.org/details/[A-Za-z0-9._@-]+/?$")
COUNT_RE = re.compile(r"Count\s+#\s*(\d+)\s+of\s+(\d+)")
READY_RE = re.compile(r"Ready\s+to\s+download\s+(\d+)\s+files", re.IGNORECASE)
ANSI_RE = re.compile(r"\x1B\[[0-9;?]*[ -/]*[@-~]")

TERMINAL_STATES = {"completed", "failed", "cancelled"}

DOWNLOAD_ROOT = Path(os.environ.get("DOWNLOAD_DIR", "/downloads")).resolve()
IA_GET_BIN = os.environ.get("IA_GET_BIN", "/usr/local/bin/ia-get")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
MAX_LOG_LINES = int(os.environ.get("MAX_LOG_LINES", "5000"))
MAX_JOBS = int(os.environ.get("MAX_JOBS", "25"))
DEFAULT_IA_USERNAME = os.environ.get("IA_USERNAME", "").strip()
DEFAULT_IA_PASSWORD = os.environ.get("IA_PASSWORD", "")
APP_DIR = Path(__file__).resolve().parent
WEB_ROOT = Path(
    os.environ.get("WEB_ROOT", str(APP_DIR.parent / "web"))
).resolve()
STATE_FILE = Path(
    os.environ.get("STATE_FILE", str(DOWNLOAD_ROOT / ".ia-get-web-state.json"))
).resolve()
STATE_LOG_LINES = int(os.environ.get("STATE_LOG_LINES", "200"))


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Job:
    id: str
    url: str
    identifier: str
    output_subdir: str
    output_path: str
    status: str = "queued"
    created_at: str = field(default_factory=now_iso)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    return_code: Optional[int] = None
    total_files: int = 0
    current_file: int = 0
    completed_files: int = 0
    auth_username: Optional[str] = None
    auth_password: Optional[str] = field(default=None, repr=False)
    retry_delay_minutes: int = 0
    retry_count: int = 0
    next_retry_at: Optional[str] = None
    cancel_requested: bool = False
    message: str = ""
    logs: list[str] = field(default_factory=list)
    process: Optional[subprocess.Popen[str]] = field(default=None, repr=False)


app = Flask(__name__)

jobs: dict[str, Job] = {}
queued_job_ids: list[str] = []
active_job_id: Optional[str] = None
jobs_lock = threading.Lock()
jobs_cv = threading.Condition(jobs_lock)
scheduler_thread: Optional[threading.Thread] = None


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def sanitize_log_line(text: str) -> str:
    cleaned = strip_ansi(text)
    cleaned = cleaned.replace("\x00", "")
    return cleaned.strip()


def extract_identifier(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def resolve_target_path(subdir: str, identifier: str) -> tuple[str, Path]:
    raw = subdir.strip() if subdir else identifier
    if not raw:
        raw = identifier

    normalized = raw.replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized).strip("/")

    if normalized in {"", "."}:
        normalized = identifier

    parts = Path(normalized).parts
    if any(part in {"..", ""} for part in parts):
        raise ValueError("Subdirectory contains invalid path segments")

    target = (DOWNLOAD_ROOT / normalized).resolve()
    if target != DOWNLOAD_ROOT and DOWNLOAD_ROOT not in target.parents:
        raise ValueError("Subdirectory resolves outside the download root")

    return normalized, target


def serialize_job(job: Job, queue_position: Optional[int] = None) -> dict[str, object]:
    if job.total_files > 0:
        percent = round((job.completed_files / job.total_files) * 100, 1)
    elif job.status == "completed":
        percent = 100.0
    else:
        percent = 0.0

    return {
        "id": job.id,
        "url": job.url,
        "identifier": job.identifier,
        "output_subdir": job.output_subdir,
        "output_path": job.output_path,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "return_code": job.return_code,
        "total_files": job.total_files,
        "current_file": job.current_file,
        "completed_files": job.completed_files,
        "queue_position": queue_position,
        "auth_enabled": bool(job.auth_username),
        "auth_username": job.auth_username,
        "retry_delay_minutes": job.retry_delay_minutes,
        "retry_count": job.retry_count,
        "next_retry_at": job.next_retry_at,
        "cancel_requested": job.cancel_requested,
        "message": job.message,
        "progress_percent": percent,
        "log_count": len(job.logs),
    }


def parse_iso_datetime(value: object) -> Optional[dt.datetime]:
    if not isinstance(value, str) or not value:
        return None

    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def job_to_state_dict(job: Job) -> dict[str, object]:
    return {
        "id": job.id,
        "url": job.url,
        "identifier": job.identifier,
        "output_subdir": job.output_subdir,
        "output_path": job.output_path,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "return_code": job.return_code,
        "total_files": job.total_files,
        "current_file": job.current_file,
        "completed_files": job.completed_files,
        "auth_username": job.auth_username,
        "retry_delay_minutes": job.retry_delay_minutes,
        "retry_count": job.retry_count,
        "next_retry_at": job.next_retry_at,
        "cancel_requested": job.cancel_requested,
        "message": job.message,
        "logs": job.logs[-STATE_LOG_LINES:] if STATE_LOG_LINES > 0 else [],
    }


def state_dict_to_job(data: object) -> Optional[Job]:
    if not isinstance(data, dict):
        return None

    required_fields = ["id", "url", "identifier", "output_subdir", "output_path"]
    if any(field not in data for field in required_fields):
        return None

    job = Job(
        id=str(data.get("id", "")).strip(),
        url=str(data.get("url", "")).strip(),
        identifier=str(data.get("identifier", "")).strip(),
        output_subdir=str(data.get("output_subdir", "")).strip(),
        output_path=str(data.get("output_path", "")).strip(),
    )

    if not job.id or not job.url:
        return None

    job.status = str(data.get("status", "queued"))
    job.created_at = str(data.get("created_at", job.created_at))
    job.started_at = data.get("started_at") if isinstance(data.get("started_at"), str) else None
    job.finished_at = data.get("finished_at") if isinstance(data.get("finished_at"), str) else None

    return_code = data.get("return_code")
    job.return_code = int(return_code) if isinstance(return_code, int) else None

    for numeric_field in ["total_files", "current_file", "completed_files", "retry_delay_minutes", "retry_count"]:
        raw_value = data.get(numeric_field)
        if isinstance(raw_value, int):
            setattr(job, numeric_field, raw_value)

    if job.retry_delay_minutes < 0:
        job.retry_delay_minutes = 0

    if job.retry_count < 0:
        job.retry_count = 0

    job.auth_username = data.get("auth_username") if isinstance(data.get("auth_username"), str) else None
    job.next_retry_at = data.get("next_retry_at") if isinstance(data.get("next_retry_at"), str) else None
    job.cancel_requested = bool(data.get("cancel_requested", False))
    job.message = str(data.get("message", ""))

    logs = data.get("logs")
    if isinstance(logs, list):
        job.logs = [str(item) for item in logs][-MAX_LOG_LINES:]

    # Never restore secrets or process handles from disk.
    job.auth_password = None
    job.process = None

    return job


def persist_state_locked() -> None:
    try:
        payload = {
            "version": 1,
            "saved_at": now_iso(),
            "queued_job_ids": list(queued_job_ids),
            "active_job_id": active_job_id,
            "jobs": [job_to_state_dict(job) for job in jobs.values()],
        }

        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
        temp_file.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        temp_file.replace(STATE_FILE)
    except Exception as exc:  # pragma: no cover - filesystem errors are environment-specific
        app.logger.warning("Failed to persist state to %s: %s", STATE_FILE, exc)


def restore_state_locked() -> None:
    if not STATE_FILE.exists():
        return

    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - malformed state is environment-specific
        app.logger.warning("Failed to read state file %s: %s", STATE_FILE, exc)
        return

    job_entries = payload.get("jobs")
    if not isinstance(job_entries, list):
        return

    restored_jobs: dict[str, Job] = {}
    for entry in job_entries:
        restored = state_dict_to_job(entry)
        if restored is not None:
            restored_jobs[restored.id] = restored

    jobs.clear()
    jobs.update(restored_jobs)

    restored_queue = payload.get("queued_job_ids")
    ordered_queue: list[str] = []
    if isinstance(restored_queue, list):
        for item in restored_queue:
            job_id = str(item)
            job = jobs.get(job_id)
            if job is not None and job.status == "queued" and job_id not in ordered_queue:
                ordered_queue.append(job_id)

    interrupted_running_ids: list[str] = []
    for job in jobs.values():
        job.process = None
        job.auth_password = None

        if job.status == "running":
            job.status = "queued"
            job.started_at = None
            job.finished_at = None
            job.return_code = None
            job.next_retry_at = None
            job.cancel_requested = False
            job.message = "Interrupted by restart; queued to resume"
            append_log_line_locked(job, "Previous run interrupted by restart; queued to resume")
            if job.id not in interrupted_running_ids:
                interrupted_running_ids.append(job.id)

    for job_id in interrupted_running_ids:
        if job_id not in ordered_queue:
            ordered_queue.insert(0, job_id)

    for job in jobs.values():
        if job.status == "queued" and job.id not in ordered_queue:
            ordered_queue.append(job.id)

    queued_job_ids.clear()
    queued_job_ids.extend(ordered_queue)

    for job in jobs.values():
        if job.status != "retry_wait":
            continue

        retry_at = parse_iso_datetime(job.next_retry_at)
        if retry_at is None:
            job.status = "queued"
            job.next_retry_at = None
            if job.id not in queued_job_ids:
                queued_job_ids.append(job.id)
            continue

        remaining_seconds = int((retry_at - dt.datetime.now(dt.UTC)).total_seconds())
        if remaining_seconds <= 0:
            job.status = "queued"
            job.next_retry_at = None
            if job.id not in queued_job_ids:
                queued_job_ids.append(job.id)
        else:
            schedule_retry_after_delay(job.id, remaining_seconds)

    global active_job_id
    active_job_id = None

    persist_state_locked()


def append_log_line_locked(job: Job, line: str) -> None:
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    job.logs.append(f"[{timestamp}] {line}")
    if len(job.logs) > MAX_LOG_LINES:
        job.logs = job.logs[-MAX_LOG_LINES:]


def update_progress_locked(job: Job, line: str) -> None:
    ready_match = READY_RE.search(line)
    if ready_match:
        job.total_files = max(job.total_files, int(ready_match.group(1)))

    count_match = COUNT_RE.search(line)
    if count_match:
        job.current_file = int(count_match.group(1))
        job.total_files = max(job.total_files, int(count_match.group(2)))

    if "Downloaded" in line:
        if job.current_file > 0:
            job.completed_files = max(job.completed_files, job.current_file)
        else:
            job.completed_files += 1

        if job.total_files > 0:
            job.completed_files = min(job.completed_files, job.total_files)


def prune_history_locked() -> None:
    if len(jobs) <= MAX_JOBS:
        return

    removable = [job for job in jobs.values() if job.status in TERMINAL_STATES and job.id != active_job_id]
    removable.sort(key=lambda item: item.created_at)

    while len(jobs) > MAX_JOBS and removable:
        stale = removable.pop(0)
        jobs.pop(stale.id, None)


def build_queue_positions_locked() -> dict[str, int]:
    return {job_id: index + 1 for index, job_id in enumerate(queued_job_ids)}


def build_queue_stats_locked() -> dict[str, object]:
    total = len(jobs)
    queued = 0
    retry_wait = 0
    running = 0
    completed = 0
    failed = 0
    cancelled = 0

    for job in jobs.values():
        if job.status == "queued":
            queued += 1
        elif job.status == "retry_wait":
            queued += 1
            retry_wait += 1
        elif job.status == "running":
            running += 1
        elif job.status == "completed":
            completed += 1
        elif job.status == "failed":
            failed += 1
        elif job.status == "cancelled":
            cancelled += 1

    terminal = completed + failed + cancelled

    progress_points = 0.0
    for job in jobs.values():
        if job.status in {"completed", "failed", "cancelled"}:
            progress_points += 100.0
        elif job.status == "running":
            running_percent = 0.0
            if job.total_files > 0:
                running_percent = (job.completed_files / job.total_files) * 100.0
            progress_points += max(0.0, min(100.0, running_percent))

    if total > 0:
        progress_percent = round(progress_points / total, 1)
    else:
        progress_percent = 0.0

    return {
        "total_jobs": total,
        "queued_jobs": queued,
        "retry_wait_jobs": retry_wait,
        "running_jobs": running,
        "completed_jobs": completed,
        "failed_jobs": failed,
        "cancelled_jobs": cancelled,
        "terminal_jobs": terminal,
        "progress_percent": progress_percent,
    }


def scheduler_loop() -> None:
    global active_job_id

    while True:
        next_job_id: Optional[str] = None

        with jobs_cv:
            while next_job_id is None:
                if active_job_id is None:
                    while queued_job_ids:
                        candidate_id = queued_job_ids.pop(0)
                        candidate = jobs.get(candidate_id)
                        if candidate is not None and candidate.status == "queued":
                            next_job_id = candidate_id
                            active_job_id = candidate_id
                            break

                if next_job_id is None:
                    jobs_cv.wait()

        run_job(next_job_id)


def ensure_scheduler_started() -> None:
    global scheduler_thread

    with jobs_lock:
        if scheduler_thread is not None and scheduler_thread.is_alive():
            return

        scheduler_thread = threading.Thread(
            target=scheduler_loop,
            daemon=True,
            name="job-scheduler",
        )
        scheduler_thread.start()


def resolve_auth_credentials(payload: dict[str, object]) -> tuple[Optional[str], Optional[str]]:
    username_input = str(payload.get("username", "")).strip()
    password_input = payload.get("password")
    password_text = str(password_input) if password_input is not None else ""

    has_username_input = bool(username_input)
    has_password_input = bool(password_text)

    if has_username_input or password_input is not None:
        if has_username_input != has_password_input:
            raise ValueError("Username and password must be provided together")

        if not has_username_input:
            return None, None

        return username_input, password_text

    has_default_username = bool(DEFAULT_IA_USERNAME)
    has_default_password = bool(DEFAULT_IA_PASSWORD)

    if has_default_username != has_default_password:
        raise ValueError(
            "Container defaults are incomplete. Set both IA_USERNAME and IA_PASSWORD, or clear both"
        )

    if not has_default_username:
        return None, None

    return DEFAULT_IA_USERNAME, DEFAULT_IA_PASSWORD


def resolve_retry_delay_minutes(payload: dict[str, object]) -> int:
    raw_value = payload.get("retry_delay_minutes", 0)
    if raw_value is None or raw_value == "":
        return 0

    try:
        retry_minutes = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError("retry_delay_minutes must be an integer") from None

    if retry_minutes < 0:
        raise ValueError("retry_delay_minutes cannot be negative")

    if retry_minutes > 1440:
        raise ValueError("retry_delay_minutes cannot exceed 1440")

    return retry_minutes


def schedule_retry_after_delay(job_id: str, delay_seconds: int) -> None:
    def retry_worker() -> None:
        deadline = time.monotonic() + max(0, delay_seconds)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            time.sleep(min(remaining, 5.0))

            with jobs_cv:
                job = jobs.get(job_id)
                if job is None or job.status != "retry_wait" or job.cancel_requested:
                    return

        with jobs_cv:
            job = jobs.get(job_id)
            if job is None or job.status != "retry_wait" or job.cancel_requested:
                return

            job.status = "queued"
            job.next_retry_at = None
            if job.id not in queued_job_ids:
                queued_job_ids.append(job.id)
            queue_position = len(queued_job_ids)
            job.message = f"Queued for retry (position {queue_position})"
            append_log_line_locked(job, "Retry delay elapsed; job re-queued")
            persist_state_locked()
            jobs_cv.notify_all()

    threading.Thread(
        target=retry_worker,
        daemon=True,
        name=f"retry-{job_id}",
    ).start()


def run_job(job_id: str) -> None:
    global active_job_id

    with jobs_cv:
        job = jobs.get(job_id)
        if job is None:
            if active_job_id == job_id:
                active_job_id = None
                jobs_cv.notify_all()
            return

        job.status = "running"
        job.started_at = now_iso()
        job.finished_at = None
        job.return_code = None
        job.next_retry_at = None
        job.message = "Launching ia-get..."
        persist_state_locked()

        if job.cancel_requested:
            job.status = "cancelled"
            job.finished_at = now_iso()
            job.message = "Download cancelled before start"
            append_log_line_locked(job, "Job cancelled before launch")
            if active_job_id == job_id:
                active_job_id = None
            prune_history_locked()
            persist_state_locked()
            jobs_cv.notify_all()
            return

        if job.auth_username and not job.auth_password:
            if DEFAULT_IA_USERNAME == job.auth_username and DEFAULT_IA_PASSWORD:
                job.auth_password = DEFAULT_IA_PASSWORD
                append_log_line_locked(job, "Using container default password for retry")
            else:
                job.status = "failed"
                job.finished_at = now_iso()
                job.return_code = -1
                job.next_retry_at = None
                job.retry_delay_minutes = 0
                job.message = (
                    "Missing password for authenticated retry. "
                    "Queue manually with password."
                )
                append_log_line_locked(
                    job,
                    "Cannot continue authenticated retry without password",
                )
                if active_job_id == job_id:
                    active_job_id = None
                prune_history_locked()
                persist_state_locked()
                jobs_cv.notify_all()
                return

    target_dir = Path(job.output_path)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - depends on host FS permissions
        with jobs_cv:
            failed = jobs.get(job_id)
            if failed is None:
                return
            failed.status = "failed"
            failed.finished_at = now_iso()
            failed.return_code = -1
            failed.message = f"Cannot create target directory: {exc}"
            append_log_line_locked(failed, f"Cannot create target directory: {exc}")
            if active_job_id == job_id:
                active_job_id = None
            prune_history_locked()
            persist_state_locked()
            jobs_cv.notify_all()
        return

    command = [IA_GET_BIN]
    if job.auth_username:
        command.extend(["--username", job.auth_username, "--password-stdin"])
    command.append(job.url)
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["CLICOLOR"] = "0"
    env["TERM"] = "dumb"

    try:
        process = subprocess.Popen(
            command,
            cwd=str(target_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
    except Exception as exc:  # pragma: no cover - process spawn failure is environment specific
        with jobs_cv:
            failed = jobs.get(job_id)
            if failed is None:
                return
            failed.status = "failed"
            failed.finished_at = now_iso()
            failed.message = f"Failed to start ia-get: {exc}"
            failed.return_code = -1
            append_log_line_locked(failed, f"Failed to start ia-get: {exc}")
            if active_job_id == job_id:
                active_job_id = None
            prune_history_locked()
            persist_state_locked()
            jobs_cv.notify_all()
        return

    with jobs_cv:
        running_job = jobs.get(job_id)
        if running_job is None:
            process.terminate()
            return
        running_job.process = process
        append_log_line_locked(running_job, f"Running: {' '.join(command)}")
        append_log_line_locked(running_job, f"Target directory: {target_dir}")
        if running_job.auth_username:
            append_log_line_locked(running_job, f"Authenticated as: {running_job.auth_username}")
        running_job.message = "ia-get is running"
        persist_state_locked()

    if process.stdin is not None:
        if job.auth_username:
            try:
                process.stdin.write(f"{job.auth_password or ''}\n")
                process.stdin.flush()
            except Exception as exc:
                process.stdin.close()
                process.terminate()
                with jobs_cv:
                    failed = jobs.get(job_id)
                    if failed is None:
                        return
                    failed.status = "failed"
                    failed.finished_at = now_iso()
                    failed.message = f"Failed to pass authentication secret to ia-get: {exc}"
                    failed.return_code = -1
                    failed.auth_password = None
                    append_log_line_locked(
                        failed,
                        f"Failed to pass authentication secret to ia-get: {exc}",
                    )
                    if active_job_id == job_id:
                        active_job_id = None
                    prune_history_locked()
                    persist_state_locked()
                    jobs_cv.notify_all()
                return
        process.stdin.close()

    with jobs_cv:
        running_job = jobs.get(job_id)
        if running_job is not None:
            running_job.auth_password = None

    if process.stdout is not None:
        for raw_line in iter(process.stdout.readline, ""):
            for segment in raw_line.replace("\r", "\n").splitlines():
                clean_line = sanitize_log_line(segment)
                if not clean_line:
                    continue

                with jobs_cv:
                    line_job = jobs.get(job_id)
                    if line_job is None:
                        continue
                    append_log_line_locked(line_job, clean_line)
                    update_progress_locked(line_job, clean_line)

    return_code = process.wait()

    retry_delay_seconds = 0

    with jobs_cv:
        final_job = jobs.get(job_id)
        if final_job is None:
            return

        final_job.return_code = return_code
        final_job.finished_at = now_iso()
        final_job.process = None

        if final_job.cancel_requested:
            final_job.status = "cancelled"
            final_job.message = "Download cancelled"
            final_job.next_retry_at = None
        elif return_code == 0:
            final_job.status = "completed"
            if final_job.total_files > 0:
                final_job.completed_files = final_job.total_files
            final_job.message = "Download complete"
            final_job.next_retry_at = None
        else:
            if final_job.retry_delay_minutes > 0:
                final_job.status = "retry_wait"
                final_job.retry_count += 1
                retry_delay_seconds = final_job.retry_delay_minutes * 60
                retry_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=retry_delay_seconds)
                final_job.next_retry_at = (
                    retry_at.isoformat(timespec="seconds").replace("+00:00", "Z")
                )
                final_job.message = (
                    f"ia-get exited with code {return_code}; "
                    f"retry #{final_job.retry_count} in {final_job.retry_delay_minutes} minute(s)"
                )
            else:
                final_job.status = "failed"
                final_job.message = f"ia-get exited with code {return_code}"
                final_job.next_retry_at = None

        append_log_line_locked(final_job, f"Job finished with status: {final_job.status}")
        if final_job.status == "retry_wait" and final_job.next_retry_at:
            append_log_line_locked(final_job, f"Next retry at {final_job.next_retry_at}")

        if active_job_id == job_id:
            active_job_id = None
        prune_history_locked()
        persist_state_locked()
        jobs_cv.notify_all()

    if retry_delay_seconds > 0:
        schedule_retry_after_delay(job_id, retry_delay_seconds)


@app.get("/")
def index() -> tuple[dict[str, str], int] | object:
    index_file = WEB_ROOT / "index.html"
    if not index_file.exists():
        return {"error": "Frontend bundle not found. Build the Svelte UI first."}, 503
    return send_from_directory(str(WEB_ROOT), "index.html")


@app.get("/assets/<path:asset_path>")
def ui_assets(asset_path: str) -> object:
    assets_dir = WEB_ROOT / "assets"
    return send_from_directory(str(assets_dir), asset_path)


@app.get("/index.html")
def index_alias() -> tuple[dict[str, str], int] | object:
    return index()


@app.get("/healthz")
def healthz() -> tuple[dict[str, str], int]:
    return {"status": "ok"}, 200


@app.get("/api/config")
def get_config() -> tuple[dict[str, object], int]:
    return {
        "download_dir": str(DOWNLOAD_ROOT),
        "active_job_id": active_job_id,
        "default_username": DEFAULT_IA_USERNAME,
        "has_default_password": bool(DEFAULT_IA_PASSWORD),
    }, 200


@app.get("/api/jobs")
def list_jobs() -> tuple[dict[str, object], int]:
    with jobs_cv:
        ordered_jobs = sorted(jobs.values(), key=lambda item: item.created_at, reverse=True)
        queue_positions = build_queue_positions_locked()
        payload = {
            "active_job_id": active_job_id,
            "jobs": [serialize_job(job, queue_positions.get(job.id)) for job in ordered_jobs],
            "queue_stats": build_queue_stats_locked(),
        }
    return payload, 200


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str) -> tuple[dict[str, object], int]:
    with jobs_cv:
        job = jobs.get(job_id)
        if job is None:
            return {"error": "Job not found"}, 404
        queue_position = build_queue_positions_locked().get(job.id)
        return {"job": serialize_job(job, queue_position)}, 200


@app.get("/api/jobs/<job_id>/logs")
def get_job_logs(job_id: str) -> tuple[dict[str, object], int]:
    raw_offset = request.args.get("offset", "0")
    try:
        offset = max(0, int(raw_offset))
    except ValueError:
        return {"error": "offset must be an integer"}, 400

    with jobs_cv:
        job = jobs.get(job_id)
        if job is None:
            return {"error": "Job not found"}, 404

        total = len(job.logs)
        if offset > total:
            offset = total

        lines = job.logs[offset:]

    return {
        "job_id": job_id,
        "offset": offset,
        "next_offset": total,
        "lines": lines,
    }, 200


@app.post("/api/jobs")
def create_job() -> tuple[dict[str, object], int]:
    ensure_scheduler_started()

    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url", "")).strip()
    subdir = str(payload.get("subdir", "")).strip()

    if not ARCHIVE_URL_RE.match(url):
        return {
            "error": "URL must match https://archive.org/details/<identifier>"
        }, 400

    identifier = extract_identifier(url)

    try:
        auth_username, auth_password = resolve_auth_credentials(payload)
    except ValueError as exc:
        return {"error": str(exc)}, 400

    try:
        retry_delay_minutes = resolve_retry_delay_minutes(payload)
    except ValueError as exc:
        return {"error": str(exc)}, 400

    try:
        output_subdir, output_path = resolve_target_path(subdir, identifier)
    except ValueError as exc:
        return {"error": str(exc)}, 400

    with jobs_cv:
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            url=url,
            identifier=identifier,
            output_subdir=output_subdir,
            output_path=str(output_path),
            auth_username=auth_username,
            auth_password=auth_password,
            retry_delay_minutes=retry_delay_minutes,
            message="Queued",
        )
        jobs[job_id] = job
        queued_job_ids.append(job_id)
        queue_position = len(queued_job_ids)
        job.message = f"Queued (position {queue_position})"
        persist_state_locked()
        jobs_cv.notify_all()

        response_payload = {
            "job": serialize_job(job, queue_position),
            "queue_stats": build_queue_stats_locked(),
        }

    return response_payload, 201


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id: str) -> tuple[dict[str, object], int]:
    with jobs_cv:
        job = jobs.get(job_id)
        if job is None:
            return {"error": "Job not found"}, 404

        if job.status in {"queued", "retry_wait"}:
            job.cancel_requested = True
            if job_id in queued_job_ids:
                queued_job_ids.remove(job_id)
                job.status = "cancelled"
                job.finished_at = now_iso()
                job.next_retry_at = None
                job.message = "Download cancelled"
                append_log_line_locked(job, "Cancelled before starting")
                prune_history_locked()
                persist_state_locked()
            elif job.status == "retry_wait":
                job.status = "cancelled"
                job.finished_at = now_iso()
                job.next_retry_at = None
                job.message = "Download cancelled"
                append_log_line_locked(job, "Cancelled while waiting for retry")
                prune_history_locked()
                persist_state_locked()
            else:
                job.message = "Cancellation requested"
                append_log_line_locked(job, "Cancellation requested before start")
                persist_state_locked()

            jobs_cv.notify_all()
            queue_position = build_queue_positions_locked().get(job.id)
            return {
                "ok": True,
                "job": serialize_job(job, queue_position),
                "queue_stats": build_queue_stats_locked(),
            }, 200

        if job.status != "running":
            return {"error": f"Job is {job.status} and cannot be cancelled"}, 409

        job.cancel_requested = True
        process = job.process
        append_log_line_locked(job, "Cancellation requested")
        queue_position = build_queue_positions_locked().get(job.id)
        persist_state_locked()
        jobs_cv.notify_all()

    if process is not None and process.poll() is None:
        try:
            process.send_signal(signal.SIGINT)
        except Exception:
            process.terminate()

    with jobs_cv:
        queue_stats = build_queue_stats_locked()

    return {
        "ok": True,
        "job": serialize_job(job, queue_position),
        "queue_stats": queue_stats,
    }, 200


@app.post("/api/jobs/clear-finished")
def clear_finished_jobs() -> tuple[dict[str, object], int]:
    with jobs_cv:
        removable_ids = [
            job_id
            for job_id, job in jobs.items()
            if job.status in {"completed", "failed", "cancelled"}
        ]

        for job_id in removable_ids:
            jobs.pop(job_id, None)
            if job_id in queued_job_ids:
                queued_job_ids.remove(job_id)

        if removable_ids:
            persist_state_locked()

        payload = {
            "removed": len(removable_ids),
            "queue_stats": build_queue_stats_locked(),
        }

    return payload, 200


def main() -> None:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    with jobs_cv:
        restore_state_locked()
    ensure_scheduler_started()
    app.run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()
