"""Job CRUD, progress payload, and cooperative cancel/pause flags."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from hub.jobs.db import JobDatabase, row_to_job, utcnow
from hub.settings import ROOT_DIR

TERMINAL = frozenset({"completed", "failed", "cancelled"})
ACTIVE = frozenset({"queued", "running", "paused"})


class JobStore:
    def __init__(self, db: JobDatabase | None = None, *, data_root: Path | None = None) -> None:
        self.db = db or JobDatabase()
        self.data_root = data_root or (ROOT_DIR / "data")

    def create(
        self,
        *,
        repository_id: str,
        capability_id: str,
        dry_run: bool = True,
        confirmed: bool = False,
        actor: str = "owner",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        now = utcnow()
        job_dir = self.data_root / "jobs" / job_id
        uploads = self.data_root / "uploads" / job_id
        results = self.data_root / "results" / job_id
        for path in (job_dir, uploads, results):
            path.mkdir(parents=True, exist_ok=True)
        log_path = job_dir / "job.log"
        log_path.write_text("", encoding="utf-8")

        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, repository_id, capability_id, status, dry_run, confirmed,
                    phase, message, percent, actor, input_path, result_path, log_path,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, 'queued', ?, ?, 'queued', ?, 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    repository_id,
                    capability_id,
                    1 if dry_run else 0,
                    1 if confirmed else 0,
                    "Queued",
                    actor,
                    str(uploads),
                    str(results),
                    str(log_path),
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=True),
                ),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row_to_job(row)

    def list_recent(self, *, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self.db.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [row_to_job(row) for row in rows if row is not None]  # type: ignore[misc]

    def count_active(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued', 'running', 'paused')"
            ).fetchone()
        return int(row["n"] if row else 0)

    def update(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "status",
            "phase",
            "message",
            "percent",
            "resumable",
            "cancel_requested",
            "pause_requested",
            "error",
            "started_at",
            "finished_at",
            "checkpoint",
            "metadata",
            "input_path",
            "result_path",
        }
        sets: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "checkpoint":
                sets.append("checkpoint_json = ?")
                values.append(json.dumps(value or {}, ensure_ascii=True))
            elif key == "metadata":
                sets.append("metadata_json = ?")
                values.append(json.dumps(value or {}, ensure_ascii=True))
            elif key in {"resumable", "cancel_requested", "pause_requested"}:
                sets.append(f"{key} = ?")
                values.append(1 if value else 0)
            else:
                sets.append(f"{key} = ?")
                values.append(value)
        if not sets:
            return self.get(job_id)
        sets.append("updated_at = ?")
        values.append(utcnow())
        values.append(job_id)
        with self.db.connect() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", values)
        return self.get(job_id)

    def append_log(self, job_id: str, line: str) -> None:
        job = self.get(job_id)
        if not job or not job.get("log_path"):
            return
        path = Path(job["log_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{utcnow()} {line.rstrip()}\n")

    def request_cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if not job:
            return None
        if job["status"] in TERMINAL:
            return job
        if job["status"] == "queued":
            return self.update(
                job_id,
                status="cancelled",
                phase="cancelled",
                message="Cancelled before start",
                percent=100,
                finished_at=utcnow(),
                cancel_requested=True,
            )
        return self.update(job_id, cancel_requested=True, message="Cancel requested")

    def request_pause(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if not job or job["status"] not in {"queued", "running"}:
            return job
        return self.update(job_id, pause_requested=True, message="Pause requested")

    def resume(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if not job:
            return None
        if job["status"] in {"paused", "failed"} and (job["status"] == "paused" or job.get("resumable")):
            return self.update(
                job_id,
                status="queued",
                phase="queued",
                message="Resumed — waiting for worker",
                pause_requested=False,
                cancel_requested=False,
                resumable=True,
                error=None,
                finished_at=None,
                percent=0,
            )
        return self.update(job_id, pause_requested=False)

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            job_id = row["id"]
            now = utcnow()
            conn.execute(
                """
                UPDATE jobs
                SET status = 'running', phase = 'running', message = 'Running',
                    started_at = COALESCE(started_at, ?), updated_at = ?, pause_requested = 0
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, job_id),
            )
        return self.get(job_id)

    def refresh_orphans(self) -> int:
        """Mark running jobs without a live worker as failed/resumable after restart."""
        # Best-effort: if process restarted, in-memory workers are gone.
        # Caller should invoke once at startup.
        count = 0
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status IN ('running', 'paused')"
            ).fetchall()
            now = utcnow()
            for row in rows:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed', phase = 'failed',
                        message = 'Worker stopped before completion; job may be resumed or resubmitted.',
                        resumable = 1, error = 'orphan_worker', finished_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, row["id"]),
                )
                count += 1
        return count


def progress_payload(job: dict[str, Any]) -> dict[str, Any]:
    """Stable poll DTO adapted from Live Processing job chrome (generic phases only)."""
    return {
        "id": job.get("id"),
        "repository_id": job.get("repository_id"),
        "capability_id": job.get("capability_id"),
        "status": job.get("status"),
        "phase": job.get("phase") or job.get("status"),
        "message": job.get("message") or "",
        "percent": float(job.get("percent") or 0),
        "dry_run": bool(job.get("dry_run")),
        "confirmed": bool(job.get("confirmed")),
        "resumable": bool(job.get("resumable")),
        "cancel_requested": bool(job.get("cancel_requested")),
        "pause_requested": bool(job.get("pause_requested")),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "updated_at": job.get("updated_at"),
        "log_path": job.get("log_path"),
        "input_path": job.get("input_path"),
        "result_path": job.get("result_path"),
        "actor": job.get("actor"),
        "metadata": job.get("metadata") or {},
    }
