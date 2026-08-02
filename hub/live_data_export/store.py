"""SQLite persistence for Live Data Export jobs, presets, and history."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from hub.settings import ROOT_DIR

TERMINAL = frozenset({"ready", "failed", "cancelled", "expired"})
ACTIVE = frozenset({"queued", "reading", "writing"})


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class LiveExportStore:
    def __init__(self, db_path: Path | None = None, *, artifacts_root: Path | None = None) -> None:
        self.db_path = db_path or (ROOT_DIR / "data" / "live_data_export.db")
        self.artifacts_root = artifacts_root or (ROOT_DIR / "data" / "live_exports")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS export_jobs (
                    id TEXT PRIMARY KEY,
                    source_key TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    format TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    filters_json TEXT NOT NULL,
                    columns_json TEXT NOT NULL,
                    estimated_rows INTEGER,
                    exported_rows INTEGER,
                    file_size INTEGER,
                    file_path TEXT,
                    download_token TEXT,
                    expires_at TEXT,
                    error TEXT,
                    progress_pct INTEGER DEFAULT 0,
                    message TEXT,
                    fingerprint TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_export_jobs_status ON export_jobs(status);
                CREATE INDEX IF NOT EXISTS idx_export_jobs_fp ON export_jobs(fingerprint, status);
                CREATE INDEX IF NOT EXISTS idx_export_jobs_token ON export_jobs(download_token);

                CREATE TABLE IF NOT EXISTS export_presets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    filters_json TEXT NOT NULL,
                    columns_json TEXT NOT NULL,
                    format TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS export_history (
                    id TEXT PRIMARY KEY,
                    job_id TEXT,
                    event TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def create_job(
        self,
        *,
        source_key: str,
        environment: str,
        format: str,
        filters: dict[str, Any],
        columns: list[str],
        actor: str,
        fingerprint: str,
        estimated_rows: int | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        job_id = f"lex_{uuid.uuid4().hex[:12]}"
        token = uuid.uuid4().hex
        now = utcnow()
        art_dir = self.artifacts_root / job_id
        art_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO export_jobs (
                    id, source_key, environment, format, status, actor,
                    filters_json, columns_json, estimated_rows, exported_rows,
                    file_size, file_path, download_token, expires_at, error,
                    progress_pct, message, fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL,
                          0, 'Queued', ?, ?, ?)
                """,
                (
                    job_id,
                    source_key,
                    environment,
                    format,
                    actor,
                    json.dumps(filters, ensure_ascii=True, sort_keys=True),
                    json.dumps(columns, ensure_ascii=True),
                    estimated_rows,
                    str(art_dir),
                    token,
                    expires_at,
                    fingerprint,
                    now,
                    now,
                ),
            )
        job = self.get_job(job_id)
        assert job is not None
        return job

    def find_active_duplicate(self, fingerprint: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM export_jobs
                WHERE fingerprint = ? AND status IN ('queued', 'reading', 'writing')
                ORDER BY created_at DESC LIMIT 1
                """,
                (fingerprint,),
            ).fetchone()
        return self._job_row(row) if row else None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM export_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_row(row) if row else None

    def get_job_by_token(self, token: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM export_jobs WHERE download_token = ?", (token,)
            ).fetchone()
        return self._job_row(row) if row else None

    def list_jobs(self, *, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM export_jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM export_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._job_row(r) for r in rows]

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "status",
            "estimated_rows",
            "exported_rows",
            "file_size",
            "file_path",
            "expires_at",
            "error",
            "progress_pct",
            "message",
            "started_at",
            "finished_at",
        }
        sets = ["updated_at = ?"]
        values: list[Any] = [utcnow()]
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k} = ?")
            values.append(v)
        values.append(job_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE export_jobs SET {', '.join(sets)} WHERE id = ?", values)
        return self.get_job(job_id)

    def request_cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if not job:
            return None
        if job["status"] in TERMINAL:
            return job
        return self.update_job(
            job_id,
            status="cancelled",
            message="Cancelled",
            error="Cancelled by user",
            finished_at=utcnow(),
            progress_pct=job.get("progress_pct") or 0,
        )

    def expire_old(self) -> int:
        now = utcnow()
        expired = 0
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, file_path FROM export_jobs
                WHERE status = 'ready' AND expires_at IS NOT NULL AND expires_at < ?
                """,
                (now,),
            ).fetchall()
            for row in rows:
                path = Path(row["file_path"] or "")
                if path.is_file():
                    try:
                        path.unlink()
                    except OSError:
                        pass
                elif path.is_dir():
                    for child in path.glob("*"):
                        try:
                            child.unlink()
                        except OSError:
                            pass
                conn.execute(
                    """
                    UPDATE export_jobs
                    SET status = 'expired', message = 'Expired', updated_at = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (now, now, row["id"]),
                )
                expired += 1
        return expired

    def add_history(
        self,
        *,
        event: str,
        actor: str,
        detail: dict[str, Any],
        job_id: str | None = None,
    ) -> None:
        hid = f"leh_{uuid.uuid4().hex[:12]}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO export_history (id, job_id, event, actor, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    hid,
                    job_id,
                    event,
                    actor,
                    json.dumps(detail, ensure_ascii=True, sort_keys=True),
                    utcnow(),
                ),
            )

    def list_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM export_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "job_id": r["job_id"],
                    "event": r["event"],
                    "actor": r["actor"],
                    "detail": json.loads(r["detail_json"] or "{}"),
                    "created_at": r["created_at"],
                }
            )
        return out

    def save_preset(
        self,
        *,
        name: str,
        source_key: str,
        environment: str,
        filters: dict[str, Any],
        columns: list[str],
        format: str,
        actor: str,
    ) -> dict[str, Any]:
        pid = f"lep_{uuid.uuid4().hex[:10]}"
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO export_presets (
                    id, name, source_key, environment, filters_json, columns_json,
                    format, actor, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    name[:120],
                    source_key,
                    environment,
                    json.dumps(filters, ensure_ascii=True, sort_keys=True),
                    json.dumps(columns, ensure_ascii=True),
                    format,
                    actor,
                    now,
                    now,
                ),
            )
        return self.get_preset(pid)  # type: ignore[return-value]

    def get_preset(self, preset_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM export_presets WHERE id = ?", (preset_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "source_key": row["source_key"],
            "environment": row["environment"],
            "filters": json.loads(row["filters_json"] or "{}"),
            "columns": json.loads(row["columns_json"] or "[]"),
            "format": row["format"],
            "actor": row["actor"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_presets(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM export_presets ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self.get_preset(r["id"]) for r in rows]  # type: ignore[misc]

    def delete_preset(self, preset_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM export_presets WHERE id = ?", (preset_id,))
            return cur.rowcount > 0

    @staticmethod
    def _job_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "source_key": row["source_key"],
            "environment": row["environment"],
            "format": row["format"],
            "status": row["status"],
            "actor": row["actor"],
            "filters": json.loads(row["filters_json"] or "{}"),
            "columns": json.loads(row["columns_json"] or "[]"),
            "estimated_rows": row["estimated_rows"],
            "exported_rows": row["exported_rows"],
            "file_size": row["file_size"],
            "file_path": row["file_path"],
            "download_token": row["download_token"],
            "expires_at": row["expires_at"],
            "error": row["error"],
            "progress_pct": row["progress_pct"],
            "message": row["message"],
            "fingerprint": row["fingerprint"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def default_expiry(self, ttl_seconds: int) -> str:
        dt = datetime.now(timezone.utc) + timedelta(seconds=int(ttl_seconds))
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
