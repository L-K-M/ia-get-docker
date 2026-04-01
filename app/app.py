from __future__ import annotations

import datetime as dt
import os
import re
import signal
import subprocess
import threading
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
    cancel_requested: bool = False
    message: str = ""
    logs: list[str] = field(default_factory=list)
    process: Optional[subprocess.Popen[str]] = field(default=None, repr=False)


app = Flask(__name__)

jobs: dict[str, Job] = {}
active_job_id: Optional[str] = None
jobs_lock = threading.Lock()


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


def serialize_job(job: Job) -> dict[str, object]:
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
        "auth_enabled": bool(job.auth_username),
        "auth_username": job.auth_username,
        "cancel_requested": job.cancel_requested,
        "message": job.message,
        "progress_percent": percent,
        "log_count": len(job.logs),
    }


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


def run_job(job_id: str) -> None:
    global active_job_id

    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = now_iso()
        job.message = "Launching ia-get..."

    target_dir = Path(job.output_path)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - depends on host FS permissions
        with jobs_lock:
            failed = jobs.get(job_id)
            if failed is None:
                return
            failed.status = "failed"
            failed.finished_at = now_iso()
            failed.return_code = -1
            failed.message = f"Cannot create target directory: {exc}"
            if active_job_id == job_id:
                active_job_id = None
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
        with jobs_lock:
            failed = jobs.get(job_id)
            if failed is None:
                return
            failed.status = "failed"
            failed.finished_at = now_iso()
            failed.message = f"Failed to start ia-get: {exc}"
            failed.return_code = -1
            if active_job_id == job_id:
                active_job_id = None
        return

    with jobs_lock:
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

    if process.stdin is not None:
        if job.auth_username:
            try:
                process.stdin.write(f"{job.auth_password or ''}\n")
                process.stdin.flush()
            except Exception as exc:
                process.stdin.close()
                process.terminate()
                with jobs_lock:
                    failed = jobs.get(job_id)
                    if failed is None:
                        return
                    failed.status = "failed"
                    failed.finished_at = now_iso()
                    failed.message = f"Failed to pass authentication secret to ia-get: {exc}"
                    failed.return_code = -1
                    failed.auth_password = None
                    if active_job_id == job_id:
                        active_job_id = None
                return
        process.stdin.close()

    with jobs_lock:
        running_job = jobs.get(job_id)
        if running_job is not None:
            running_job.auth_password = None

    if process.stdout is not None:
        for raw_line in iter(process.stdout.readline, ""):
            for segment in raw_line.replace("\r", "\n").splitlines():
                clean_line = sanitize_log_line(segment)
                if not clean_line:
                    continue

                with jobs_lock:
                    line_job = jobs.get(job_id)
                    if line_job is None:
                        continue
                    append_log_line_locked(line_job, clean_line)
                    update_progress_locked(line_job, clean_line)

    return_code = process.wait()

    with jobs_lock:
        final_job = jobs.get(job_id)
        if final_job is None:
            return

        final_job.return_code = return_code
        final_job.finished_at = now_iso()
        final_job.process = None

        if final_job.cancel_requested:
            final_job.status = "cancelled"
            final_job.message = "Download cancelled"
        elif return_code == 0:
            final_job.status = "completed"
            if final_job.total_files > 0:
                final_job.completed_files = final_job.total_files
            final_job.message = "Download complete"
        else:
            final_job.status = "failed"
            final_job.message = f"ia-get exited with code {return_code}"

        append_log_line_locked(final_job, f"Job finished with status: {final_job.status}")

        if active_job_id == job_id:
            active_job_id = None
        prune_history_locked()


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
    with jobs_lock:
        ordered_jobs = sorted(jobs.values(), key=lambda item: item.created_at, reverse=True)
        payload = {
            "active_job_id": active_job_id,
            "jobs": [serialize_job(job) for job in ordered_jobs],
        }
    return payload, 200


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str) -> tuple[dict[str, object], int]:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return {"error": "Job not found"}, 404
        return {"job": serialize_job(job)}, 200


@app.get("/api/jobs/<job_id>/logs")
def get_job_logs(job_id: str) -> tuple[dict[str, object], int]:
    raw_offset = request.args.get("offset", "0")
    try:
        offset = max(0, int(raw_offset))
    except ValueError:
        return {"error": "offset must be an integer"}, 400

    with jobs_lock:
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
    global active_job_id

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
        output_subdir, output_path = resolve_target_path(subdir, identifier)
    except ValueError as exc:
        return {"error": str(exc)}, 400

    with jobs_lock:
        if active_job_id is not None:
            active = jobs.get(active_job_id)
            if active is not None and active.status == "running":
                return {
                    "error": "A download is already in progress",
                    "active_job_id": active_job_id,
                }, 409

        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            url=url,
            identifier=identifier,
            output_subdir=output_subdir,
            output_path=str(output_path),
            auth_username=auth_username,
            auth_password=auth_password,
            message="Queued",
        )
        jobs[job_id] = job
        active_job_id = job_id

    worker = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    worker.start()

    return {"job": serialize_job(job)}, 201


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id: str) -> tuple[dict[str, object], int]:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return {"error": "Job not found"}, 404

        if job.status != "running":
            return {"error": f"Job is {job.status} and cannot be cancelled"}, 409

        job.cancel_requested = True
        process = job.process
        append_log_line_locked(job, "Cancellation requested")

    if process is not None and process.poll() is None:
        try:
            process.send_signal(signal.SIGINT)
        except Exception:
            process.terminate()

    return {"ok": True}, 200


def main() -> None:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    app.run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()
